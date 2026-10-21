"""REQ-011 — mark a capture as superseded by another (a lifecycle event, owner ruling 2026-10-21).

The live case: an armed Newspapers.com capture missed its image (BUG-022) and left a
page-render-only source; the same page was then ingested, and that ingest is what the tree
cites. Capsule is append-only, so the orphan is not edited or removed: it gains a
`superseded` event naming its replacement, projected into its hash-excluded `lifecycle.json`
so the folder-sourced index can show it. `reinstated` withdraws it, again as a new event.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tests.test_backend as tb

_URL_A = "https://a860-historicalvitalrecords.nyc.gov/view/1"
_URL_B = "https://a860-historicalvitalrecords.nyc.gov/view/2"
_URL_C = "https://a860-historicalvitalrecords.nyc.gov/view/3"


@pytest.fixture()
def three(tmp_path):
    """Three real captures in a fresh collection; returns (tmp_path, [capture ids])."""
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    ids = []
    try:
        for url in (_URL_A, _URL_B, _URL_C):
            resp = client.post("/capture", json={"url": url, "html": tb._PAGE}, headers=tb._AUTH)
            assert resp.status_code == 200, resp.text
            ids.append(Path(resp.json()["capture_folder"]).name)
    finally:
        backend.app.dependency_overrides.clear()
    return tmp_path, ids


def _repo(tmp_path):
    from capsule.store.repository import Repository

    return Repository(tmp_path / "capsule.db")


def _supersede(tmp_path, old, new, **kw):
    from capsule.pipeline.supersede import supersede_capture

    repo = _repo(tmp_path)
    try:
        return supersede_capture(
            repository=repo, captures_root=tmp_path / "captures", capture_id=old,
            by_capture_id=new, **kw,
        )
    finally:
        repo.close()


def _reinstate(tmp_path, capture_id, **kw):
    from capsule.pipeline.supersede import reinstate_capture

    repo = _repo(tmp_path)
    try:
        return reinstate_capture(
            repository=repo, captures_root=tmp_path / "captures", capture_id=capture_id, **kw
        )
    finally:
        repo.close()


def _record(tmp_path, capture_id):
    from capsule.collection.read import read_capture_record

    return read_capture_record(tmp_path / "captures" / capture_id)


def test_REQ_011_supersede_records_the_event_the_sidecar_and_the_index(three):
    from capsule.capture.manifest import verify_capture

    tmp_path, (old, new, _) = three
    _supersede(tmp_path, old, new, reason="the ingest holds the full-resolution image")

    repo = _repo(tmp_path)
    try:
        new_source = json.loads(
            (tmp_path / "captures" / new / "manifest.json").read_text(encoding="utf-8")
        )["source"]["id"]
        old_source = json.loads(
            (tmp_path / "captures" / old / "manifest.json").read_text(encoding="utf-8")
        )["source"]["id"]
        assert repo.superseded_by(old_source) == new_source
        assert repo.superseded_by(new_source) is None
    finally:
        repo.close()

    sidecar = json.loads((tmp_path / "captures" / old / "lifecycle.json").read_text("utf-8"))
    assert [e["event"] for e in sidecar] == ["superseded"]
    assert sidecar[0]["superseded_by_capture_id"] == new
    assert sidecar[0]["reason"] == "the ingest holds the full-resolution image"
    # The old capture is untouched as evidence: still verifies, sidecar and all.
    assert verify_capture(tmp_path / "captures" / old).ok
    assert _record(tmp_path, old).superseded_by == new
    assert _record(tmp_path, new).superseded_by is None
    index = (tmp_path / "captures" / "index.html").read_text(encoding="utf-8")
    assert f'href="#cap-{new}" title="superseded — cite the replacement">superseded</a>' in index
    assert "Superseded by" in index


def test_REQ_011_reinstate_withdraws_it_as_a_new_event(three):
    tmp_path, (old, new, _) = three
    _supersede(tmp_path, old, new)
    _reinstate(tmp_path, old, reason="mistake")
    sidecar = json.loads((tmp_path / "captures" / old / "lifecycle.json").read_text("utf-8"))
    assert [e["event"] for e in sidecar] == ["superseded", "reinstated"]
    assert sidecar[0]["superseded_by_capture_id"] == new  # history keeps its names
    assert _record(tmp_path, old).superseded_by is None


def test_REQ_011_refusals(three):
    tmp_path, (a, b, c) = three
    with pytest.raises(ValueError, match="itself"):
        _supersede(tmp_path, a, a)
    _supersede(tmp_path, a, b)
    with pytest.raises(ValueError, match="already superseded"):
        _supersede(tmp_path, a, b)
    # B -> A would close a loop in which neither is current; so would C -> A via B.
    with pytest.raises(ValueError, match="chain leading back"):
        _supersede(tmp_path, b, a)
    _supersede(tmp_path, b, c)
    with pytest.raises(ValueError, match="chain leading back"):
        _supersede(tmp_path, c, a)
    with pytest.raises(ValueError, match="not superseded"):
        _reinstate(tmp_path, c)
    with pytest.raises(FileNotFoundError):
        _supersede(tmp_path, a, "20990101-nowhere-deadbeef")


def test_REQ_011_a_chain_of_two_is_allowed_and_each_names_its_own_replacement(three):
    tmp_path, (a, b, c) = three
    _supersede(tmp_path, a, b)
    _supersede(tmp_path, b, c)
    assert _record(tmp_path, a).superseded_by == b
    assert _record(tmp_path, b).superseded_by == c
    assert _record(tmp_path, c).superseded_by is None


def test_REQ_011_the_store_is_append_only(three):
    tmp_path, (a, b, _) = three
    _supersede(tmp_path, a, b)
    conn = sqlite3.connect(tmp_path / "capsule.db")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE capture_lifecycle_event SET reason = 'x'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM capture_lifecycle_event")
        # The CHECKs hold even for a hand-written row.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO capture_lifecycle_event VALUES "
                "('x', (SELECT id FROM source LIMIT 1), 'superseded', NULL, NULL, 'me', 'now')"
            )
    finally:
        conn.close()


def test_REQ_011_a_tampered_sidecar_cannot_shape_a_link(three):
    """lifecycle.json is a hash-excluded file: the reader only accepts a capture-id shape."""
    tmp_path, (a, _, _) = three
    (tmp_path / "captures" / a / "lifecycle.json").write_text(
        json.dumps([{"event": "superseded", "superseded_by_capture_id": '"><script>x</script>'}]),
        encoding="utf-8",
    )
    record = _record(tmp_path, a)
    assert record.superseded and record.superseded_by is None


def test_REQ_011_the_cli(three):
    from typer.testing import CliRunner

    from capsule.cli.app import app

    tmp_path, (a, b, _) = three
    common = ["--db", str(tmp_path / "capsule.db"), "--captures", str(tmp_path / "captures")]
    runner = CliRunner()
    done = runner.invoke(app, ["supersede", a, "--by", b, "--reason", "better copy", *common])
    assert done.exit_code == 0, done.output
    assert f"{a} is superseded by {b}" in done.output
    again = runner.invoke(app, ["supersede", a, "--by", b, *common])
    assert again.exit_code == 1 and "already superseded" in again.output
    both = runner.invoke(app, ["supersede", a, "--by", b, "--reinstate", *common])
    assert both.exit_code == 2
    back = runner.invoke(app, ["supersede", a, "--reinstate", *common])
    assert back.exit_code == 0, back.output


def test_REQ_011_found_a_failed_sidecar_write_rolls_the_event_back(three, monkeypatch):
    """Codex review: the event used to commit before lifecycle.json was written, so a failed
    write left the store and the folder disagreeing, and a retry was refused as "already".
    Now the write happens inside the transaction: a failure rolls the event back, and the
    retry simply succeeds."""
    import capsule.pipeline.supersede as sup

    tmp_path, (a, b, _) = three
    real = sup.write_lifecycle_file

    def disk_full(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(sup, "write_lifecycle_file", disk_full)
    with pytest.raises(OSError):
        _supersede(tmp_path, a, b)
    conn = sqlite3.connect(tmp_path / "capsule.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM capture_lifecycle_event").fetchone() == (0,)
    finally:
        conn.close()
    monkeypatch.setattr(sup, "write_lifecycle_file", real)
    _supersede(tmp_path, a, b)
    assert _record(tmp_path, a).superseded_by == b

    monkeypatch.setattr(sup, "write_lifecycle_file", disk_full)
    with pytest.raises(OSError):
        _reinstate(tmp_path, a)
    monkeypatch.setattr(sup, "write_lifecycle_file", real)
    assert _record(tmp_path, a).superseded_by == b  # the reinstatement never happened
    _reinstate(tmp_path, a)
    assert _record(tmp_path, a).superseded_by is None


def test_REQ_011_found_another_process_cannot_slip_a_loop_past_the_check(three):
    """Codex review: two processes recording A->B and B->A could both pass the loop check.
    A second connection holds the write lock and records B->A; our A->B waits for it, then
    sees the loop and refuses."""
    import threading
    import time

    tmp_path, (a, b, _) = three
    source = {
        cid: json.loads((tmp_path / "captures" / cid / "manifest.json").read_text("utf-8"))[
            "source"
        ]["id"]
        for cid in (a, b)
    }
    holding = threading.Event()

    def other_process():
        conn = sqlite3.connect(tmp_path / "capsule.db", isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            holding.set()
            time.sleep(0.5)
            conn.execute(
                "INSERT INTO capture_lifecycle_event VALUES (?, ?, 'superseded', ?, NULL, "
                "'other', '2026-10-21T00:00:00+00:00')",
                ("other-1", source[b], source[a]),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

    worker = threading.Thread(target=other_process)
    worker.start()
    assert holding.wait(5)
    try:
        with pytest.raises(ValueError, match="chain leading back"):
            _supersede(tmp_path, a, b)
    finally:
        worker.join()


def test_REQ_011_found_append_order_wins_over_a_clock_that_stepped_back(three):
    """Codex review: a reinstatement stamped EARLIER than the supersession is still the latest."""
    from datetime import datetime, timezone

    tmp_path, (a, b, _) = three
    _supersede(tmp_path, a, b, now=lambda: datetime(2026, 10, 21, 12, tzinfo=timezone.utc))
    _reinstate(tmp_path, a, now=lambda: datetime(2026, 10, 21, 11, tzinfo=timezone.utc))
    assert _record(tmp_path, a).superseded_by is None


def test_REQ_011_found_a_commit_that_fails_after_the_sidecar_leaves_no_phantom(three):
    """Codex review: the sidecar is replaced before SQLite commits. If the COMMIT then fails,
    the folder must not keep claiming an event the store never kept. The real `write_lock`
    runs; only the connection's first COMMIT is made to fail, as a busy database would."""
    from capsule.pipeline.supersede import supersede_capture
    from capsule.store.repository import Repository

    tmp_path, (a, b, _) = three

    class FirstCommitFails:
        def __init__(self, conn):
            self._conn, self._failed = conn, False

        def commit(self):
            if not self._failed:
                self._failed = True
                raise sqlite3.OperationalError("database is locked")
            return self._conn.commit()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    repo = Repository(tmp_path / "capsule.db")
    repo.connection = FirstCommitFails(repo.connection)
    try:
        with pytest.raises(sqlite3.OperationalError):
            supersede_capture(
                repository=repo, captures_root=tmp_path / "captures", capture_id=a,
                by_capture_id=b,
            )
    finally:
        repo.close()
    assert not (tmp_path / "captures" / a / "lifecycle.json").exists()
    assert _record(tmp_path, a).superseded_by is None
    conn = sqlite3.connect(tmp_path / "capsule.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM capture_lifecycle_event").fetchone() == (0,)
    finally:
        conn.close()


def test_REQ_011_asking_again_clears_a_phantom_sidecar(three):
    """A sidecar with no history in the store (a crash between write and commit) is removed
    by the next request about that capture."""
    tmp_path, (a, b, _) = three
    (tmp_path / "captures" / a / "lifecycle.json").write_text(
        json.dumps([{"event": "superseded", "superseded_by_capture_id": b}]), encoding="utf-8"
    )
    assert _record(tmp_path, a).superseded_by == b  # the phantom, as the index would see it
    with pytest.raises(ValueError, match="not superseded"):
        _reinstate(tmp_path, a)
    assert not (tmp_path / "captures" / a / "lifecycle.json").exists()


def test_REQ_011_found_write_lock_rolls_back_a_failed_commit(tmp_path):
    """Codex review: the COMMIT ran outside the guard, so a failed one left the transaction
    open and a later read saw rows that were about to vanish."""
    from capsule.store.repository import Repository

    repo = Repository(tmp_path / "capsule.db")
    try:
        with pytest.raises(RuntimeError):
            with repo.write_lock():
                repo.connection.execute("CREATE TABLE scratch (x)")
                raise RuntimeError("boom")
        assert not repo.connection.in_transaction
        assert repo.connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'scratch'"
        ).fetchone() == (0,)
    finally:
        repo.close()


def test_REQ_011_found_capsule_index_repairs_a_folder_that_fell_behind_the_store(three):
    """`capsule index` re-projects every capture the store has events for."""
    from typer.testing import CliRunner

    from capsule.cli.app import app

    tmp_path, (a, b, c) = three
    _supersede(tmp_path, b, c)
    (tmp_path / "captures" / b / "lifecycle.json").unlink()  # the folder fell behind
    result = CliRunner().invoke(
        app,
        ["index", "--captures", str(tmp_path / "captures"), "--db", str(tmp_path / "capsule.db")],
    )
    assert result.exit_code == 0, result.output
    assert _record(tmp_path, b).superseded_by == c


def test_REQ_011_reindexing_never_deletes_a_sidecar_the_store_has_no_events_for(three):
    """Deletion cannot be redone, and a store older than the folder cannot tell a crash
    phantom from history it never saw (Codex review). Reindexing leaves it; the next request
    about THAT capture is what clears a real phantom."""
    from typer.testing import CliRunner

    from capsule.cli.app import app

    tmp_path, (a, b, _) = three
    sidecar = tmp_path / "captures" / a / "lifecycle.json"
    sidecar.write_text(
        json.dumps([{"event": "superseded", "superseded_by_capture_id": b}]), encoding="utf-8"
    )
    result = CliRunner().invoke(
        app,
        ["index", "--captures", str(tmp_path / "captures"), "--db", str(tmp_path / "capsule.db")],
    )
    assert result.exit_code == 0, result.output
    assert sidecar.exists()
    with pytest.raises(ValueError, match="not superseded"):
        _reinstate(tmp_path, a)
    assert not sidecar.exists()


def test_REQ_011_found_reindexing_against_an_unrelated_database_keeps_the_sidecars(three, tmp_path_factory):
    """Codex review: a store that has never seen a source cannot call its sidecar a phantom."""
    from typer.testing import CliRunner

    from capsule.cli.app import app
    from capsule.store.repository import Repository

    tmp_path, (a, b, _) = three
    _supersede(tmp_path, a, b)
    other_db = tmp_path_factory.mktemp("other") / "capsule.db"
    Repository(other_db).close()  # an empty, unrelated store
    result = CliRunner().invoke(
        app, ["index", "--captures", str(tmp_path / "captures"), "--db", str(other_db)]
    )
    assert result.exit_code == 0, result.output
    assert _record(tmp_path, a).superseded_by == b


def test_REQ_011_found_a_trashed_replacement_does_not_make_the_old_one_current(three):
    """Codex review: superseded is a state of the OLD capture, whatever happened to the new."""
    from capsule.pipeline.trash import trash_capture
    from typer.testing import CliRunner

    from capsule.cli.app import app

    tmp_path, (a, b, _) = three
    _supersede(tmp_path, a, b)
    trash_capture(tmp_path / "captures", b)
    result = CliRunner().invoke(
        app,
        ["index", "--captures", str(tmp_path / "captures"), "--db", str(tmp_path / "capsule.db")],
    )
    assert result.exit_code == 0, result.output
    record = _record(tmp_path, a)
    assert record.superseded and record.superseded_by is None
    index = (tmp_path / "captures" / "index.html").read_text(encoding="utf-8")
    assert "the replacement is not in this collection" in index


def test_REQ_011_found_a_restored_older_database_cannot_erase_newer_folder_history(three):
    """Codex review: restore a backup holding only A->B, keep folders that also hold the later
    reinstatement, reindex. The folder's newer history must survive, and the capture must not
    be changed until someone decides which history is right."""
    import shutil

    from typer.testing import CliRunner

    from capsule.cli.app import app
    from capsule.pipeline.supersede import LifecycleConflict

    tmp_path, (a, b, _) = three
    _supersede(tmp_path, a, b)
    backup = tmp_path / "backup.db"
    shutil.copy(tmp_path / "capsule.db", backup)  # the store as of A->B
    _reinstate(tmp_path, a)
    shutil.copy(backup, tmp_path / "capsule.db")  # restore the older store
    sidecar = tmp_path / "captures" / a / "lifecycle.json"
    newer = sidecar.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["index", "--captures", str(tmp_path / "captures"), "--db", str(tmp_path / "capsule.db")],
    )
    assert result.exit_code == 0, result.output
    assert sidecar.read_text(encoding="utf-8") == newer
    assert _record(tmp_path, a).superseded is False  # the folder's newer word stands
    with pytest.raises(LifecycleConflict):
        _reinstate(tmp_path, a)
    assert sidecar.read_text(encoding="utf-8") == newer


def test_REQ_011_found_a_real_crash_leftover_is_resolved_by_an_explicit_resync(three):
    """Codex review: a crash between the sidecar write and the COMMIT leaves a sidecar with a
    real event id the store never kept. It is indistinguishable from newer history, so
    Capsule refuses to choose (`LifecycleConflict`, naming the remedy), and `--resync` is the
    person's explicit "the database is right"."""
    from typer.testing import CliRunner

    from capsule.cli.app import app
    from capsule.pipeline.supersede import LifecycleConflict

    tmp_path, (a, b, _) = three
    # Exactly what a killed `supersede A --by B` leaves: a real projection, no committed row.
    _supersede(tmp_path, a, b)
    conn = sqlite3.connect(tmp_path / "capsule.db")
    try:
        conn.execute("DROP TRIGGER capture_lifecycle_event_no_delete")
        conn.execute("DELETE FROM capture_lifecycle_event")
        conn.commit()
    finally:
        conn.close()
    sidecar = tmp_path / "captures" / a / "lifecycle.json"
    assert sidecar.exists() and _record(tmp_path, a).superseded_by == b

    with pytest.raises(LifecycleConflict, match="--resync"):
        _supersede(tmp_path, a, b)
    assert sidecar.exists()
    common = ["--db", str(tmp_path / "capsule.db"), "--captures", str(tmp_path / "captures")]
    done = CliRunner().invoke(app, ["supersede", a, "--resync", *common])
    assert done.exit_code == 0, done.output
    assert not sidecar.exists()
    assert _record(tmp_path, a).superseded is False
    # And the index a person reads says so too (Codex review).
    index = (tmp_path / "captures" / "index.html").read_text(encoding="utf-8")
    assert ">superseded</" not in index
