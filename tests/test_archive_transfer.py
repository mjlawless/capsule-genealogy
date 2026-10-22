"""`capsule export` / `capsule import`: move an archive between computers by merging.

Real captures, made by the real pipeline with a fake model, in temporary archives. The
promises under test: a merge adds what is missing and touches nothing else; it can be run
again; trash stays trash; a failure leaves the target as it was; an unsafe or newer archive
is refused before anything is written.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from capsule.collection.transfer import (
    STAGING_PREFIX,
    TransferError,
    capture_folders,
    export_archive,
    import_archive,
)
from tests.test_req_004_dedup import _PAGE, _RAW

_DT = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)


def _capture(root: Path, url: str) -> str:
    """One real capture into the archive at `root`; returns its folder name."""
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.contracts.evidence import GatingStatus
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    fetcher = FakeFetcher(
        PageSnapshot(url=url, html=_PAGE, gating=GatingStatus.PUBLIC, fetched_at=_DT, title="Record")
    )
    agent = Agent(
        name="extractor", model="claude-opus-4-8", transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT, new_id=lambda: uuid.uuid4().hex,
    )
    repo = Repository(root / "capsule.db")
    try:
        outcome = run_capture(
            url, fetcher=fetcher, agent=agent, repository=repo, captures_root=root / "captures",
            now=lambda: _DT, new_id=lambda: uuid.uuid4().hex,
        )
    finally:
        repo.close()
    return outcome.capture_folder.name


def _archive(root: Path, n: int, tag: str) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    return [_capture(root, f"https://records.example/{tag}?id={i}") for i in range(n)]


def _counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def _import(package: Path, target: Path, confirm=lambda plan: True):
    return import_archive(
        package, db_path=target / "capsule.db", captures_root=target / "captures", confirm=confirm
    )


def _names(root: Path) -> set[str]:
    return {f.relative for f in capture_folders(root / "captures")}


def test_export_then_import_into_an_empty_archive(tmp_path):
    mac = tmp_path / "mac"
    _archive(mac, 3, "a")
    package = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "archive.zip").path
    win = tmp_path / "win"

    result = _import(package, win)

    assert result.added_folders == 3 and not result.not_verified
    assert _names(win) == _names(mac)
    assert _counts(win / "capsule.db") == _counts(mac / "capsule.db")
    conn = sqlite3.connect(str(win / "capsule.db"))
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
    assert not list(tmp_path.glob("win/.capsule-import-*")), "the extraction folder is removed"


def test_a_merge_keeps_the_targets_own_captures_and_can_run_again(tmp_path):
    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 2, "mac")
    _archive(win, 1, "win")
    before = _names(win)
    package = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip").path

    _import(package, win)
    assert _names(win) == before | _names(mac)
    counts = _counts(win / "capsule.db")
    assert counts["source"] == 3

    again = _import(package, win)
    assert again.added_folders == 0 and not again.plan.new_rows and again.plan.present == 2
    assert _counts(win / "capsule.db") == counts


def test_a_folder_lost_after_the_commit_is_restored_by_the_next_run(tmp_path):
    """The repair path: rows committed, folder never renamed into place."""
    import shutil

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 2, "a")
    package = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip").path
    _import(package, win)
    shutil.rmtree(win / "captures" / names[0])

    result = _import(package, win)
    assert result.added_folders == 1 and not result.plan.new_rows
    assert (win / "captures" / names[0] / "manifest.json").is_file()


def test_folders_committed_by_an_interrupted_import_are_kept_and_put_in_place(tmp_path, monkeypatch):
    """Codex review: after the commit, staged folders hold the only copy of that evidence.
    The next import, even of a different package, must publish them, not clear them."""
    import os as _os

    import capsule.collection.transfer as transfer
    from capsule.pipeline.trash import trash_capture

    mac, other, win = tmp_path / "mac", tmp_path / "other", tmp_path / "win"
    names = _archive(mac, 2, "a")
    trash_capture(mac / "captures", names[1])
    _archive(other, 1, "o")
    real_replace = _os.replace

    def stop_before_publishing(src, dst):
        if Path(src).name.startswith(STAGING_PREFIX):
            raise KeyboardInterrupt  # the process dies after the commit
        return real_replace(src, dst)

    monkeypatch.setattr(transfer.os, "replace", stop_before_publishing)
    with pytest.raises(KeyboardInterrupt):
        _import(mac, win)
    monkeypatch.setattr(transfer.os, "replace", real_replace)
    assert _counts(win / "capsule.db")["source"] == 2 and not _names(win)

    result = _import(other, win)
    assert result.recovered == 2
    assert {names[0], f"_trash/{names[1]}"} <= _names(win)
    assert not [p for p in (win / "captures").iterdir() if p.name.startswith(STAGING_PREFIX)]


def test_an_interruption_just_after_the_commit_keeps_the_folders(tmp_path, monkeypatch):
    """Codex review: cleanup must not assume a rollback; the rows may have committed."""
    import capsule.collection.transfer as transfer

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 2, "a")
    real_insert = transfer._insert_rows

    def commit_then_die(*args, **kwargs):
        real_insert(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(transfer, "_insert_rows", commit_then_die)
    with pytest.raises(KeyboardInterrupt):
        _import(mac, win)
    assert set(names) <= _names(win), "committed and copied whole: put in place, not deleted"
    assert not [p for p in (win / "captures").iterdir() if p.name.startswith(STAGING_PREFIX)]


def test_a_failed_import_restoring_a_folder_never_shows_its_rolled_back_runs(tmp_path, monkeypatch):
    """Codex review: the restored folder's source row predates the import, so it does not
    prove the import's rows committed; a rolled-back transcription run must not appear."""
    import shutil

    import capsule.collection.transfer as transfer

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    _import(mac, win)
    shutil.rmtree(win / "captures" / names[0])  # rows here, folder lost
    _record_run(mac / "capsule.db", "run1")
    run = mac / "captures" / names[0] / "transcript" / "run1"
    run.mkdir(parents=True)
    (run / "transcript.json").write_text("{}", encoding="utf-8")

    def rolled_back(*_a, **_k):
        raise TransferError("merge refused")

    monkeypatch.setattr(transfer, "_insert_rows", rolled_back)
    with pytest.raises(TransferError):
        _import(mac, win)
    assert not (win / "captures" / names[0] / "transcript" / "run1").exists()


def test_a_copy_that_never_finished_is_not_published_and_the_next_import_redoes_it(tmp_path):
    """Codex review: a staged folder cut off mid-copy must never be put in place."""
    import shutil

    import capsule.collection.transfer as transfer

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    _import(mac, win)
    shutil.rmtree(win / "captures" / names[0])  # rows here, folder lost
    partial = win / "captures" / f"{STAGING_PREFIX}0badc0de-a-{names[0]}"
    partial.mkdir()
    shutil.copy2(mac / "captures" / names[0] / "manifest.json", partial / "manifest.json")

    assert transfer.recover_staging(win / "captures", win / "capsule.db") == 0
    assert not partial.exists() and not (win / "captures" / names[0]).exists()
    assert _import(mac, win).added_folders == 1
    from capsule.capture.manifest import verify_capture

    assert verify_capture(win / "captures" / names[0]).ok


def test_trash_stays_trash(tmp_path):
    from capsule.pipeline.trash import trash_capture

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 2, "a")
    trash_capture(mac / "captures", names[0])
    package = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip").path

    _import(package, win)
    assert (win / "captures" / "_trash" / names[0]).is_dir()
    assert not (win / "captures" / names[0]).exists()


def test_a_dry_run_changes_nothing(tmp_path):
    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 2, "a")
    _archive(win, 1, "w")
    before = (_names(win), _counts(win / "capsule.db"))
    package = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip").path

    result = _import(package, win, confirm=lambda plan: False)
    assert len(result.plan.new_folders) == 2 and result.plan.new_rows["source"] == 2
    assert (_names(win), _counts(win / "capsule.db")) == before


def test_a_failure_before_the_commit_leaves_the_target_as_it_was(tmp_path, monkeypatch):
    import capsule.collection.transfer as transfer

    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 2, "a")
    _archive(win, 1, "w")
    before = (_names(win), _counts(win / "capsule.db"))
    package = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip").path

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(transfer, "_insert_rows", boom)
    with pytest.raises(RuntimeError):
        _import(package, win)
    assert (_names(win), _counts(win / "capsule.db")) == before
    assert not [p for p in (win / "captures").iterdir() if p.name.startswith(STAGING_PREFIX)]


def test_an_archive_from_a_newer_capsule_is_refused(tmp_path):
    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 1, "a")
    _archive(win, 1, "w")
    conn = sqlite3.connect(str(mac / "capsule.db"))
    conn.execute("ALTER TABLE source ADD COLUMN from_the_future TEXT")
    conn.commit()
    conn.close()
    before = (_names(win), _counts(win / "capsule.db"))

    with pytest.raises(TransferError, match="update Capsule first"):
        _import(mac, win)  # an archive folder works as well as a zip
    assert (_names(win), _counts(win / "capsule.db")) == before


def test_importing_an_archive_into_itself_is_refused(tmp_path):
    mac = tmp_path / "mac"
    _archive(mac, 1, "a")
    with pytest.raises(TransferError, match="this archive"):
        _import(mac, mac)


@pytest.mark.parametrize(
    "evil", ["../outside.txt", "captures/../../outside.txt", "/etc/passwd", "C:/x.txt", "notes.txt"]
)
def test_unsafe_zip_entries_are_refused_before_anything_is_written(tmp_path, evil):
    package = tmp_path / "evil.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("capsule.db", b"")
        zf.writestr(evil, b"x")
    win = tmp_path / "win"
    with pytest.raises(TransferError, match="unsafe"):
        _import(package, win)
    assert not (tmp_path / "outside.txt").exists()


def test_on_windows_a_path_too_long_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    import capsule.collection.transfer as transfer

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    (mac / "captures" / names[0] / ("x" * 240 + ".json")).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(transfer, "_ON_WINDOWS", True)
    with pytest.raises(TransferError, match="too long for Windows"):
        _import(mac, win)
    assert not _names(win)


def test_a_second_import_waits_its_turn(tmp_path):
    """Codex review: a concurrent import must not clear the other's staged folders."""
    import capsule.collection.transfer as transfer

    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 1, "a")
    _archive(win, 1, "w")
    before = (_names(win), _counts(win / "capsule.db"))
    with transfer.import_lock(win / "captures"):
        with pytest.raises(TransferError, match="another import"):
            _import(mac, win)
    assert (_names(win), _counts(win / "capsule.db")) == before
    assert _import(mac, win).added_folders == 1  # free again once the first is done


def test_a_note_added_after_an_earlier_import_reaches_the_capture(tmp_path):
    """Codex review: notes.json is a projection of the store; a merged note must refresh it."""
    from capsule.pipeline.annotate import add_note_to_capture
    from capsule.store.repository import Repository

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    _import(mac, win)
    repo = Repository(mac / "capsule.db")
    try:
        add_note_to_capture(
            repository=repo, captures_root=mac / "captures", capture_id=names[0],
            text="found the baptism too",
        )
    finally:
        repo.close()

    result = _import(mac, win)
    assert result.added_folders == 0 and result.plan.new_rows == {"capture_note": 1}
    assert result.notes_refreshed == 1
    notes = (win / "captures" / names[0] / "notes.json").read_text(encoding="utf-8")
    assert "found the baptism too" in notes


def test_a_dry_run_on_an_older_archive_leaves_its_database_byte_for_byte(tmp_path):
    """Codex review: planning must not migrate the target; it plans on a migrated copy."""
    import hashlib

    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 1, "a")
    _archive(win, 1, "w")
    conn = sqlite3.connect(str(win / "capsule.db"))
    conn.execute("ALTER TABLE source DROP COLUMN source_quality_rationale")  # an older build
    conn.commit()
    conn.close()
    digest = hashlib.sha256((win / "capsule.db").read_bytes()).hexdigest()
    files = sorted(p.name for p in win.rglob("*"))

    result = _import(mac, win, confirm=lambda plan: False)
    assert result.plan.new_rows["source"] == 1 and not result.applied
    assert hashlib.sha256((win / "capsule.db").read_bytes()).hexdigest() == digest
    assert sorted(p.name for p in win.rglob("*")) == files


def test_a_bad_package_creates_nothing(tmp_path):
    win = tmp_path / "win"
    with pytest.raises(TransferError):
        _import(tmp_path / "missing.zip", win)
    assert not win.exists()


def test_a_rerun_restores_notes_lost_after_the_commit(tmp_path):
    from typer.testing import CliRunner

    from capsule.cli.app import app
    from capsule.pipeline.annotate import add_note_to_capture
    from capsule.store.repository import Repository

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    repo = Repository(mac / "capsule.db")
    try:
        add_note_to_capture(
            repository=repo, captures_root=mac / "captures", capture_id=names[0], text="a note"
        )
    finally:
        repo.close()
    args = ["import", str(mac), "--db", str(win / "capsule.db"), "--captures", str(win / "captures"), "--yes"]
    runner = CliRunner()
    assert runner.invoke(app, args).exit_code == 0
    notes = win / "captures" / names[0] / "notes.json"
    notes.unlink()  # as if the import stopped between its commit and the note projection

    res = runner.invoke(app, args)
    assert "Nothing new to import" in res.output
    assert "a note" in notes.read_text(encoding="utf-8")


def test_a_transcription_made_after_an_earlier_import_arrives(tmp_path):
    """Codex review: a held folder still gains new transcript runs and logs, never overwrites."""
    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    _import(mac, win)
    _record_run(mac / "capsule.db", "run1")
    run = mac / "captures" / names[0] / "transcript" / "run1"
    run.mkdir(parents=True)
    (run / "transcript.json").write_text('{"text": "Patrick"}', encoding="utf-8")
    logs = sorted((mac / "captures" / names[0] / "agent_log").iterdir())
    (win / "captures" / names[0] / "agent_log" / logs[0].name).write_text("kept", encoding="utf-8")

    result = _import(mac, win)
    assert result.added_files == 1
    assert (win / "captures" / names[0] / "transcript" / "run1" / "transcript.json").read_text(
        encoding="utf-8"
    ) == '{"text": "Patrick"}'
    held = win / "captures" / names[0] / "agent_log" / logs[0].name
    assert held.read_text(encoding="utf-8") == "kept", "an existing file is never overwritten"
    assert _import(mac, win).plan.nothing_new


def test_a_capture_trashed_during_an_import_is_not_recreated_in_its_old_place(tmp_path, monkeypatch):
    """Codex review: a new file for a held capture must follow it, not resurrect its path."""
    import capsule.collection.transfer as transfer
    from capsule.pipeline.trash import trash_capture

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    _import(mac, win)
    _record_run(mac / "capsule.db", "run1")
    run = mac / "captures" / names[0] / "transcript" / "run1"
    run.mkdir(parents=True)
    (run / "transcript.json").write_text("{}", encoding="utf-8")
    real_insert = transfer._insert_rows

    def trash_meanwhile(*args, **kwargs):
        trash_capture(win / "captures", names[0])
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(transfer, "_insert_rows", trash_meanwhile)
    _import(mac, win)
    monkeypatch.setattr(transfer, "_insert_rows", real_insert)
    assert not (win / "captures" / names[0]).exists(), "the old place is not recreated"

    assert _import(mac, win).added_files == 1
    assert (win / "captures" / "_trash" / names[0] / "transcript" / "run1" / "transcript.json").exists()


def test_a_dry_run_with_nothing_new_writes_nothing(tmp_path):
    """Codex review: the re-sync after an import must not run on a dry run."""
    import hashlib

    from typer.testing import CliRunner

    from capsule.cli.app import app

    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 1, "a")
    _import(mac, win)
    (win / "captures" / "index.html").unlink(missing_ok=True)
    digest = hashlib.sha256((win / "capsule.db").read_bytes()).hexdigest()

    args = ["import", str(mac), "--db", str(win / "capsule.db"), "--captures", str(win / "captures")]
    res = CliRunner().invoke(app, args + ["--dry-run"])
    assert res.exit_code == 0 and "Nothing new to import" in res.output
    assert not (win / "captures" / "index.html").exists()
    assert hashlib.sha256((win / "capsule.db").read_bytes()).hexdigest() == digest


def test_names_that_would_collide_on_extraction_are_refused(tmp_path, monkeypatch):
    """Codex review: Windows renames some names and ignores case, so two entries could
    land on one file; refuse before extracting anything."""
    import capsule.collection.transfer as transfer

    def package(*entries):
        path = tmp_path / f"p{len(list(tmp_path.glob('p*.zip')))}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("capsule.db", b"")
            for entry in entries:
                zf.writestr(entry, b"x")
        return path

    win = tmp_path / "win"
    with pytest.raises(TransferError, match="differ only in case"):
        _import(package("captures/c/media/a.jpg", "captures/c/media/A.jpg"), win)
    with pytest.raises(TransferError, match="differ only in case"):
        _import(package("captures/c/media/a.jpg", "captures/c/MEDIA/b.jpg"), win)
    monkeypatch.setattr(transfer, "_ON_WINDOWS", True)
    with pytest.raises(TransferError, match="Windows cannot store"):
        _import(package("captures/c/what?.json"), win)
    assert not win.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="creating links needs extra rights on Windows")
def test_an_archive_folder_holding_a_link_is_refused(tmp_path):
    """Codex review: a link inside an archive folder could import files from elsewhere."""
    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "keys.txt").write_text("private", encoding="utf-8")
    (mac / "captures" / names[0] / "agent_log" / "elsewhere").symlink_to(secret)

    with pytest.raises(TransferError, match="link to somewhere else"):
        _import(mac, win)
    assert not win.exists() or not _names(win)


@pytest.mark.skipif(sys.platform == "win32", reason="creating links needs extra rights on Windows")
def test_a_link_in_a_capture_both_archives_hold_is_refused_too(tmp_path):
    import shutil

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    _import(mac, win)
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "keys.txt").write_text("private", encoding="utf-8")
    log = mac / "captures" / names[0] / "agent_log"
    shutil.rmtree(log)
    log.symlink_to(secret)  # the whole growing folder is now a link

    with pytest.raises(TransferError, match="link to somewhere else"):
        _import(mac, win)
    assert not (win / "captures" / names[0] / "agent_log" / "keys.txt").exists()


def test_export_leaves_out_a_folder_with_no_database_row(tmp_path):
    mac = tmp_path / "mac"
    _archive(mac, 1, "a")
    stray = mac / "captures" / "20261022-stray-00000000"
    stray.mkdir()
    (stray / "manifest.json").write_text(json.dumps({"source": {"id": "not-in-db"}}), encoding="utf-8")

    result = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip")
    assert result.folders == 1 and result.skipped == ["20261022-stray-00000000"]


def _record_run(db: Path, run_id: str) -> None:
    """The committed rows of a transcription run, as `capsule transcribe` writes them."""
    conn = sqlite3.connect(str(db))
    sid = conn.execute("SELECT id FROM source ORDER BY id LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO transcription (id, source_id, media_filename, media_sha256, model, status, "
        "problems, created_at) VALUES (?, ?, 'm.jpg', 'x', 'm', 'complete', '[]', 't')",
        (run_id, sid),
    )
    conn.commit()
    conn.close()


def test_a_transcription_still_being_written_is_left_for_the_next_import(tmp_path):
    """Codex review: an archive folder may be live; a run whose rows are not committed yet
    must not arrive as files without rows."""
    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    run = mac / "captures" / names[0] / "transcript" / "in-flight"
    run.mkdir(parents=True)
    (run / "transcript.json").write_text("{}", encoding="utf-8")

    _import(mac, win)
    assert not (win / "captures" / names[0] / "transcript" / "in-flight").exists()
    _record_run(mac / "capsule.db", "in-flight")  # the run commits
    assert _import(mac, win).added_files == 1
    assert (win / "captures" / names[0] / "transcript" / "in-flight" / "transcript.json").exists()


def test_export_takes_only_transcription_runs_its_snapshot_holds(tmp_path):
    """Codex review: a run that finishes during an export (files written, rows not yet
    committed) must not ship without its rows; the next export brings it."""
    mac = tmp_path / "mac"
    names = _archive(mac, 1, "a")
    folder = mac / "captures" / names[0]
    _record_run(mac / "capsule.db", "committed")
    for run in ("committed", "in-flight"):
        (folder / "transcript" / run).mkdir(parents=True)
        (folder / "transcript" / run / "transcript.json").write_text("{}", encoding="utf-8")

    package = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip").path
    with zipfile.ZipFile(package) as zf:
        shipped = {n.split("/transcript/")[1].split("/")[0] for n in zf.namelist() if "/transcript/" in n}
    assert shipped == {"committed"}


def test_a_folder_moved_during_export_fails_it_rather_than_shipping_rows_alone(tmp_path, monkeypatch):
    import capsule.collection.transfer as transfer
    from capsule.pipeline.trash import trash_capture

    mac = tmp_path / "mac"
    names = _archive(mac, 2, "a")
    listed = list(capture_folders(mac / "captures"))
    trash_capture(mac / "captures", names[0])  # moves after the listing
    monkeypatch.setattr(transfer, "capture_folders", lambda root: iter(listed))

    with pytest.raises(TransferError, match="moved while exporting"):
        export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip")
    assert not (tmp_path / "a.zip").exists()
    assert not list(tmp_path.glob(".capsule-export-*"))


def test_a_folder_moved_partway_through_reading_fails_the_export(tmp_path, monkeypatch):
    """Codex review: a move after the folder's manifest was read must still be caught."""
    import shutil

    import capsule.collection.transfer as transfer

    mac = tmp_path / "mac"
    names = _archive(mac, 1, "a")
    folder = mac / "captures" / names[0]
    real = transfer._sealed_files

    def sealed_then_moved(path):
        found = real(path)
        shutil.move(str(path), str(tmp_path / "elsewhere"))  # trashed mid-export
        return found

    monkeypatch.setattr(transfer, "_sealed_files", sealed_then_moved)
    with pytest.raises(TransferError, match="moved while exporting"):
        export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip")
    assert not (tmp_path / "a.zip").exists() and not folder.exists()


def test_a_capture_missed_by_the_listing_fails_the_export(tmp_path, monkeypatch):
    """Codex review: a first trash during the listing can hide a capture from it entirely."""
    import capsule.collection.transfer as transfer

    mac = tmp_path / "mac"
    _archive(mac, 2, "a")
    real = transfer.capture_folders
    calls = {"n": 0}

    def listing_misses_one(root):
        calls["n"] += 1
        folders = list(real(root))
        return iter(folders[1:] if calls["n"] == 1 else folders)

    monkeypatch.setattr(transfer, "capture_folders", listing_misses_one)
    with pytest.raises(TransferError, match="changed while exporting"):
        export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip")
    assert not (tmp_path / "a.zip").exists()


def test_a_capture_already_missing_a_file_still_exports_as_it_is(tmp_path):
    mac = tmp_path / "mac"
    names = _archive(mac, 1, "a")
    (mac / "captures" / names[0] / "citation.json").unlink()  # fails verify, before and after
    result = export_archive(mac / "capsule.db", mac / "captures", tmp_path / "a.zip")
    assert result.folders == 1


def test_records_with_no_folder_are_reported_not_hidden(tmp_path):
    """Codex review: a database-only package must not look like a clean import."""
    import shutil

    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 2, "a")
    shutil.rmtree(mac / "captures")
    result = _import(mac, win, confirm=lambda plan: False)
    assert result.plan.records_without_folder == 2 and not result.plan.new_folders


def test_export_never_overwrites(tmp_path):
    mac = tmp_path / "mac"
    _archive(mac, 1, "a")
    out = tmp_path / "a.zip"
    out.write_bytes(b"precious")
    with pytest.raises(TransferError, match="already exists"):
        export_archive(mac / "capsule.db", mac / "captures", out)
    assert out.read_bytes() == b"precious"

    # Nor a file that happens to share the old fixed temporary name (Codex review).
    (tmp_path / "b.zip.partial").write_bytes(b"also precious")
    export_archive(mac / "capsule.db", mac / "captures", tmp_path / "b.zip")
    assert (tmp_path / "b.zip.partial").read_bytes() == b"also precious"
    assert not list(tmp_path.glob(".capsule-export-*")), "its own temporary file is removed"


def _correct(db: Path, new_id: str) -> None:
    """A superseding assertion over the archive's first one, as a correction would write."""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM assertion ORDER BY id LIMIT 1").fetchone())
    row.update(id=new_id, supersedes_id=row["id"])
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO assertion ({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    conn.commit()
    conn.close()


def test_rival_corrections_stop_the_import_cleanly(tmp_path):
    """Codex review: the same fact corrected differently on each computer cannot merge (one
    assertion is superseded once); refuse in plain words, and change nothing."""
    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 1, "a")
    _import(mac, win)
    _correct(mac / "capsule.db", "fix-on-mac")
    _correct(win / "capsule.db", "fix-on-windows")
    _capture(mac, "https://records.example/unrelated?id=9")
    before = (_names(win), _counts(win / "capsule.db"))

    with pytest.raises(TransferError, match="corrected differently"):
        _import(mac, win)
    assert (_names(win), _counts(win / "capsule.db")) == before


def test_on_a_disk_without_hard_links_export_still_never_replaces(tmp_path, monkeypatch):
    """Codex review: the fallback claims the name exclusively; a second export loses cleanly."""
    import os as _os

    mac = tmp_path / "mac"
    _archive(mac, 1, "a")

    def no_links(*_a, **_k):
        raise OSError("operation not supported")

    monkeypatch.setattr(_os, "link", no_links)
    out = tmp_path / "a.zip"
    export_archive(mac / "capsule.db", mac / "captures", out)
    first = out.read_bytes()
    assert zipfile.is_zipfile(out)
    with pytest.raises(TransferError, match="already exists"):
        export_archive(mac / "capsule.db", mac / "captures", out)
    assert out.read_bytes() == first


def test_rival_supersessions_that_would_loop_stop_the_import_cleanly(tmp_path):
    """Codex review: A-replaced-by-B on one computer and B-replaced-by-A on the other are
    each valid, but merged they leave no current capture."""
    from capsule.pipeline.supersede import supersede_capture
    from capsule.store.repository import Repository

    mac, win = tmp_path / "mac", tmp_path / "win"
    a, b = _archive(mac, 2, "a")
    _import(mac, win)

    def supersede(root: Path, old: str, new: str) -> None:
        repo = Repository(root / "capsule.db")
        try:
            supersede_capture(
                repository=repo, captures_root=root / "captures", capture_id=old, by_capture_id=new
            )
        finally:
            repo.close()

    supersede(mac, a, b)
    supersede(win, b, a)
    before = _counts(win / "capsule.db")
    with pytest.raises(TransferError, match="replacing each other"):
        _import(mac, win)
    assert _counts(win / "capsule.db") == before


def test_divergent_replaced_histories_keep_each_computers_own_decision(tmp_path):
    """Codex review: latest-event-wins would let append order pick a computer's decision,
    and append-only events mean a refusal could never be settled. Each side keeps its own,
    and other captures still import."""
    from capsule.pipeline.supersede import reinstate_capture, supersede_capture
    from capsule.store.repository import Repository

    mac, win = tmp_path / "mac", tmp_path / "win"
    a, b, c = _archive(mac, 3, "a")
    _import(mac, win)
    for root, by, undo in ((mac, b, False), (win, c, True)):
        repo = Repository(root / "capsule.db")
        try:
            supersede_capture(repository=repo, captures_root=root / "captures", capture_id=a, by_capture_id=by)
            if undo:
                reinstate_capture(repository=repo, captures_root=root / "captures", capture_id=a)
        finally:
            repo.close()

    def current(root: Path):
        repo = Repository(root / "capsule.db")
        try:
            sid = repo.connection.execute("SELECT source_id FROM capture_lifecycle_event").fetchone()[0]
            return repo.superseded_by(sid)
        finally:
            repo.close()

    mac_view, win_view = current(mac), current(win)
    assert mac_view is not None and win_view is None
    newer = _capture(mac, "https://records.example/later?id=1")

    result = _import(mac, win)
    assert result.plan.lifecycle_kept_here and newer in _names(win)
    assert current(win) is None, "the reinstatement made here stands"
    _import(win, mac)
    assert current(mac) == mac_view, "and the supersession made there stands there"
    assert _import(mac, win).plan.lifecycle_kept_here, "reported each time, never blocking"


def test_one_sided_replaced_history_merges(tmp_path):
    from capsule.pipeline.supersede import supersede_capture
    from capsule.store.repository import Repository

    mac, win = tmp_path / "mac", tmp_path / "win"
    a, b = _archive(mac, 2, "a")
    _import(mac, win)
    repo = Repository(mac / "capsule.db")
    try:
        supersede_capture(repository=repo, captures_root=mac / "captures", capture_id=a, by_capture_id=b)
    finally:
        repo.close()
    assert _import(mac, win).plan.new_rows == {"capture_lifecycle_event": 1}


def test_two_captures_sharing_a_folder_name_stop_the_import_before_any_write(tmp_path):
    """Codex review: merging the rows while skipping the folder would strand evidence."""
    import shutil

    mac, win = tmp_path / "mac", tmp_path / "win"
    names = _archive(mac, 1, "a")
    other = _archive(win, 1, "w")
    shutil.move(str(win / "captures" / other[0]), str(win / "captures" / names[0]))
    before = (_names(win), _counts(win / "capsule.db"))

    with pytest.raises(TransferError, match="share a folder name"):
        _import(mac, win)
    assert (_names(win), _counts(win / "capsule.db")) == before


def test_the_cli_round_trip(tmp_path):
    from typer.testing import CliRunner

    from capsule.cli.app import app

    mac, win = tmp_path / "mac", tmp_path / "win"
    _archive(mac, 2, "a")
    runner = CliRunner()
    out = tmp_path / "a.zip"
    res = runner.invoke(
        app, ["export", str(out), "--db", str(mac / "capsule.db"), "--captures", str(mac / "captures")]
    )
    assert res.exit_code == 0, res.output
    assert "Keep it private" in res.output

    args = ["import", str(out), "--db", str(win / "capsule.db"), "--captures", str(win / "captures")]
    dry = runner.invoke(app, args + ["--dry-run"])
    assert dry.exit_code == 0 and "2 new" in dry.output and "nothing was changed" in dry.output
    assert not _names(win)
    assert not (win / "captures" / "index.html").exists(), "a dry run writes no index either"

    res = runner.invoke(app, args + ["--yes"])
    assert res.exit_code == 0, res.output
    assert "Imported 2 captures" in res.output
    assert (win / "captures" / "index.html").is_file()
    again = runner.invoke(app, args + ["--yes"])
    assert "Nothing new to import" in again.output
