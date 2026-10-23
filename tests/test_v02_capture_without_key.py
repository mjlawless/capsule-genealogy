"""v0.2 — capture works without an Anthropic API key (evaluation track, item 1).

An evaluator should see Capsule work before entering any key. With no key the page, media and
citation are archived now, and the extraction is marked PENDING in the manifest:
- it is not "read and found nothing", so verify's no-text floor waits for the reading;
- it is not complete, so it never blocks capturing the page again once a key exists;
- the index, the popup and the on-page toast say "extraction pending".
"""
from __future__ import annotations

import json
from pathlib import Path

from capsule.pipeline.fetch import ProvidedContentFetcher

_PAGE = (
    "<html><head><title>Baptisms 1850</title></head>"
    "<body><p>Name: John Doe</p><p>Baptised: 1850</p></body></html>"
)
_EXT = Path(__file__).resolve().parent.parent / "extension"


def _archive(tmp_path, url):
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    repo = Repository(tmp_path / "capsule.db")
    try:
        return run_capture(
            url,
            fetcher=ProvidedContentFetcher(_PAGE),
            agent=None,
            repository=repo,
            captures_root=tmp_path / "captures",
        )
    finally:
        repo.close()


def test_no_key_archives_with_the_extraction_pending(tmp_path):
    outcome = _archive(tmp_path, "https://example.org/baptisms/1850")
    folder = outcome.capture_folder
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["extraction"] == "pending"
    assert outcome.agent_call is None and not (folder / "agent_log").exists()
    assert outcome.extraction.assertions == ()
    assert (folder / "snapshot.html").is_file() and (folder / "citation.json").is_file()
    assert outcome.verify.ok
    assert any("extraction pending" in w for w in outcome.verify.warnings)


def test_the_no_text_floor_waits_for_the_reading_on_an_ordinary_page(tmp_path):
    """An unread text page must not fail verify as "read no record text": it was not read."""
    outcome = _archive(tmp_path, "https://example.org/baptisms/1850")
    assert outcome.verify.ok and outcome.verify.insufficient == ()


def test_on_a_tile_viewer_a_pending_capture_still_needs_its_image(tmp_path):
    """Codex review: on a tile viewer the image IS the record. Reading the snapshot later
    cannot recover pixels that were never archived, so pending does not excuse it."""
    outcome = _archive(tmp_path, "https://www.archion.de/de/viewer/churchRegister/1")
    assert not outcome.verify.ok
    assert outcome.verify.insufficient


def test_a_pending_capture_never_blocks_a_recapture(tmp_path):
    from capsule.pipeline.dedup import find_capture

    url = "https://example.org/baptisms/1850"
    _archive(tmp_path, url)
    held = find_capture(url, captures_root=tmp_path / "captures", db_path=tmp_path / "capsule.db")
    assert held is not None and held.complete is False
    assert "extraction pending" in held.reason


def test_the_index_shows_the_pending_state(tmp_path):
    from capsule.collection.index import write_index

    _archive(tmp_path, "https://example.org/baptisms/1850")
    page = write_index(tmp_path / "captures").read_text(encoding="utf-8")
    assert "not read yet" in page


def test_the_extension_says_extraction_pending():
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    content = (_EXT / "content.js").read_text(encoding="utf-8")
    popup = (_EXT / "popup.js").read_text(encoding="utf-8")
    assert "extraction_pending: !!body.extraction_pending," in background
    assert "res.extraction_pending" in content and "add your Anthropic API key" in content
    assert "body.extraction_pending" in popup


# -- capsule extract: read a pending capture from its archived snapshot --------------------

_RAW = {
    "record_type": "baptism",
    "personas": [{"label": "John Doe", "role": "child"}],
    "assertions": [
        {
            "persona": "John Doe",
            "field": "baptism_year",
            "value": "1850",
            "evidence_type": "stated",
            "snippet": "Baptised: 1850",
        }
    ],
}
_GATED_PAGE = (
    "<html><head><title>Baptisms 1850</title></head><body>"
    '<a href="https://example.org/ifhf/myaccount.php">Welcome, Jane Researcher</a>'
    '<a href="https://example.org/ifhf/logout.php">Log out</a>'
    "<p>Name: John Doe</p><p>Baptised: 1850</p></body></html>"
)


def _agent():
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport

    return Agent(name="extractor", model="m", transport=FakeTransport(json.dumps(_RAW)))


def _archive_page(tmp_path, url, html, when):
    from datetime import datetime, timezone

    from capsule.contracts.evidence import GatingStatus
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    repo = Repository(tmp_path / "capsule.db")
    try:
        return run_capture(
            url,
            fetcher=ProvidedContentFetcher(
                html, gating=GatingStatus.GATED, now=lambda: datetime(*when, tzinfo=timezone.utc)
            ),
            agent=None,
            repository=repo,
            captures_root=tmp_path / "captures",
        )
    finally:
        repo.close()


def test_extract_reads_the_archived_page_into_a_capture_that_supersedes_it(tmp_path):
    from capsule.capture.manifest import verify_capture
    from capsule.collection.read import read_capture_record
    from capsule.pipeline.reextract import extract_capture
    from capsule.store.repository import Repository

    pending = _archive_page(tmp_path, "https://example.org/b/1", _GATED_PAGE, (2026, 3, 1, 9))
    assert json.loads((pending.capture_folder / "manifest.json").read_text())["redactions"]
    repo = Repository(tmp_path / "capsule.db")
    try:
        done = extract_capture(
            repository=repo,
            captures_root=tmp_path / "captures",
            capture_id=pending.capture_folder.name,
            agent=_agent(),
        )
    finally:
        repo.close()
    new = done.outcome
    assert len(new.extraction.assertions) == 1
    assert new.verify.ok, new.verify  # the carried-over redaction record still matches
    manifest = json.loads((new.capture_folder / "manifest.json").read_text())
    assert "extraction" not in manifest
    assert manifest["redactions"] and manifest["source"]["gating_status"] == "gated"
    # The citation is dated when the page was SEEN, not when it was read.
    assert new.citation.access_date.isoformat() == "2026-03-01"
    assert manifest["archive"]["captured_at"].startswith("2026-03-01")
    # The pending capture is untouched and superseded by the new one.
    assert verify_capture(pending.capture_folder).ok
    assert read_capture_record(pending.capture_folder).superseded_by == new.capture_folder.name


def test_extract_refuses_a_capture_that_is_not_pending(tmp_path):
    import pytest

    from capsule.pipeline.reextract import extract_capture
    from capsule.store.repository import Repository

    pending = _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))
    repo = Repository(tmp_path / "capsule.db")
    try:
        done = extract_capture(
            repository=repo, captures_root=tmp_path / "captures",
            capture_id=pending.capture_folder.name, agent=_agent(),
        )
        with pytest.raises(ValueError, match="not waiting"):
            extract_capture(
                repository=repo, captures_root=tmp_path / "captures",
                capture_id=done.outcome.capture_folder.name, agent=_agent(),
            )
    finally:
        repo.close()


def test_capsule_extract_all_reads_every_pending_capture_once(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import capsule.cli.app as cli

    _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))
    _archive_page(tmp_path, "https://example.org/b/2", _PAGE, (2026, 3, 2, 9))
    monkeypatch.setattr(cli, "_build_agent", lambda captures_root: _agent())
    common = ["--db", str(tmp_path / "capsule.db"), "--captures", str(tmp_path / "captures")]
    first = CliRunner().invoke(cli.app, ["extract", "--all", *common])
    assert first.exit_code == 0, first.output
    assert first.output.count("assertion(s), verify OK") == 2
    again = CliRunner().invoke(cli.app, ["extract", "--all", *common])
    assert again.exit_code == 0 and "nothing is waiting" in again.output


def _extract(tmp_path, capture_id):
    from capsule.pipeline.reextract import extract_capture
    from capsule.store.repository import Repository

    repo = Repository(tmp_path / "capsule.db")
    try:
        return extract_capture(
            repository=repo, captures_root=tmp_path / "captures", capture_id=capture_id,
            agent=_agent(),
        )
    finally:
        repo.close()


def test_found_extract_refuses_bytes_that_no_longer_match_their_hashes(tmp_path):
    """Codex review: an edited snapshot must not be re-hashed into a fresh capture that
    carries the original access date."""
    import pytest

    pending = _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))
    snap = pending.capture_folder / "snapshot.html"
    snap.write_text(snap.read_text(encoding="utf-8").replace("1850", "1851"), encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches its own hashes"):
        _extract(tmp_path, pending.capture_folder.name)
    assert len(list((tmp_path / "captures").glob("*/manifest.json"))) == 1


def test_found_extract_refuses_a_capture_already_read(tmp_path):
    """Codex review: the pending marker is immutable, so asking again by id must not pay for
    a second reading and create a second current capture."""
    import pytest

    pending = _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))
    _extract(tmp_path, pending.capture_folder.name)
    with pytest.raises(ValueError, match="already been read"):
        _extract(tmp_path, pending.capture_folder.name)
    assert len(list((tmp_path / "captures").glob("*/manifest.json"))) == 2


def test_found_one_reader_per_capture_across_processes(tmp_path):
    """Codex review: a claim held by another process blocks a second reader."""
    import pytest

    pending = _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))
    (tmp_path / "captures" / f".extract-{pending.capture_folder.name}.lock").write_text("")
    with pytest.raises(ValueError, match="already being read"):
        _extract(tmp_path, pending.capture_folder.name)


def test_found_an_interrupted_extract_resumes_without_reading_twice(tmp_path, monkeypatch):
    """Codex review: a crash between writing the replacement and superseding the original
    must not make the retry pay for a second reading and create a second current capture."""
    import pytest

    import capsule.pipeline.reextract as rx
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.collection.read import read_capture_record
    from capsule.store.repository import Repository

    pending = _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))
    transport = FakeTransport(json.dumps(_RAW))
    agent = Agent(name="extractor", model="m", transport=transport)
    real = rx.supersede_capture

    def crash(**_kwargs):
        raise OSError("process killed")

    monkeypatch.setattr(rx, "supersede_capture", crash)
    repo = Repository(tmp_path / "capsule.db")
    try:
        with pytest.raises(OSError):
            rx.extract_capture(repository=repo, captures_root=tmp_path / "captures",
                               capture_id=pending.capture_folder.name, agent=agent)
        monkeypatch.setattr(rx, "supersede_capture", real)
        done = rx.extract_capture(repository=repo, captures_root=tmp_path / "captures",
                                  capture_id=pending.capture_folder.name, agent=agent)
    finally:
        repo.close()
    assert done.resumed and done.outcome is None
    assert len(transport.calls) == 1  # read once, not twice
    assert len(list((tmp_path / "captures").glob("*/manifest.json"))) == 2
    assert read_capture_record(pending.capture_folder).superseded_by == done.replacement.name


def test_found_reading_an_ancestry_capture_updates_the_ledger(tmp_path):
    """Codex review: the pending capture was ledgered with 0 assertions, so the record stayed
    unmined and the queue would capture it again. The replacement must stand for it."""
    import tests.test_backend_ancestry as ta
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import provide_agent
    from capsule.pipeline.reextract import extract_capture
    from capsule.store.ledger import AncestryLedger
    from capsule.store.repository import Repository

    client, backend = ta._client(tmp_path)
    backend.app.dependency_overrides[provide_agent] = lambda: None  # no key yet
    try:
        resp = client.post(
            "/capture",
            json={"url": ta._ANCESTRY_URL, "html": ta._PAGE, "expected_apid": ta._APID},
            headers=ta._AUTH,
        )
    finally:
        backend.app.dependency_overrides.clear()
    assert resp.status_code == 200 and resp.json()["extraction_pending"], resp.text
    pending_id = Path(resp.json()["capture_folder"]).name

    repo = Repository(tmp_path / "capsule.db")
    try:
        done = extract_capture(
            repository=repo, captures_root=tmp_path / "captures", capture_id=pending_id,
            agent=Agent(name="extractor", model="m", transport=FakeTransport(json.dumps(ta._RAW))),
        )
    finally:
        repo.close()
    ledger = AncestryLedger(tmp_path / "capsule.db")
    try:
        row = ledger.latest(ta._APID)
    finally:
        ledger.close()
    assert row.capture_id == done.replacement.name
    assert row.assertion_count == 1


def test_found_a_replacement_whose_rows_never_landed_is_trashed_and_re_read(tmp_path, monkeypatch):
    """Codex review: the folder is written before its database rows. If the process dies in
    between, reusing that folder would break supersession forever (its source is unknown to
    the store). The retry moves it to the trash and reads again."""
    import pytest

    import capsule.pipeline.pipeline as pl
    from capsule.collection.read import read_capture_record

    pending = _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))
    real = pl._persist

    def dies(*_a, **_k):
        raise OSError("process killed before the rows landed")

    monkeypatch.setattr(pl, "_persist", dies)
    with pytest.raises(OSError):
        _extract(tmp_path, pending.capture_folder.name)
    monkeypatch.setattr(pl, "_persist", real)
    done = _extract(tmp_path, pending.capture_folder.name)
    assert not done.resumed and done.outcome is not None
    assert read_capture_record(pending.capture_folder).superseded_by == done.replacement.name
    assert len(list((tmp_path / "captures" / "_trash").glob("*/manifest.json"))) == 1


def test_found_a_reinstated_capture_is_read_afresh_not_given_back_the_rejected_reading(tmp_path):
    """Codex review: extract -> reinstate (the person rejects the reading) -> extract must not
    silently re-apply the rejected reading; and --all must find the capture pending again."""
    from capsule.collection.read import read_capture_record
    from capsule.pipeline.reextract import pending_captures
    from capsule.pipeline.supersede import reinstate_capture
    from capsule.store.repository import Repository

    pending = _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))
    first = _extract(tmp_path, pending.capture_folder.name)
    repo = Repository(tmp_path / "capsule.db")
    try:
        reinstate_capture(repository=repo, captures_root=tmp_path / "captures",
                          capture_id=pending.capture_folder.name, reason="wrong reading")
    finally:
        repo.close()
    assert pending_captures(tmp_path / "captures") == [pending.capture_folder.name]
    second = _extract(tmp_path, pending.capture_folder.name)
    assert not second.resumed and second.replacement != first.replacement
    assert read_capture_record(pending.capture_folder).superseded_by == second.replacement.name


def test_found_a_damaged_interrupted_reading_is_never_applied(tmp_path, monkeypatch):
    """Codex review: resume must verify the replacement before superseding an intact original."""
    import pytest

    import capsule.pipeline.reextract as rx
    from capsule.collection.read import read_capture_record

    pending = _archive_page(tmp_path, "https://example.org/b/1", _PAGE, (2026, 3, 1, 9))

    def crash(**_kwargs):
        raise OSError("process killed")

    real = rx.supersede_capture
    monkeypatch.setattr(rx, "supersede_capture", crash)
    with pytest.raises(OSError):
        _extract(tmp_path, pending.capture_folder.name)
    monkeypatch.setattr(rx, "supersede_capture", real)
    (replacement,) = [
        p.parent for p in (tmp_path / "captures").glob("*/manifest.json")
        if p.parent != pending.capture_folder
    ]
    (replacement / "snapshot.html").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches its own hashes"):
        _extract(tmp_path, pending.capture_folder.name)
    assert read_capture_record(pending.capture_folder).superseded is False


def test_found_the_ledger_follows_a_re_reading_after_a_reinstatement(tmp_path):
    """Codex review: extract A->B, reinstate A (B rejected), extract A->C: the ledger must name
    C, not the rejected B."""
    import tests.test_backend_ancestry as ta
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import provide_agent
    from capsule.pipeline.reextract import extract_capture
    from capsule.pipeline.supersede import reinstate_capture
    from capsule.store.ledger import AncestryLedger
    from capsule.store.repository import Repository

    client, backend = ta._client(tmp_path)
    backend.app.dependency_overrides[provide_agent] = lambda: None
    try:
        resp = client.post(
            "/capture",
            json={"url": ta._ANCESTRY_URL, "html": ta._PAGE, "expected_apid": ta._APID},
            headers=ta._AUTH,
        )
    finally:
        backend.app.dependency_overrides.clear()
    original = Path(resp.json()["capture_folder"]).name

    def read():
        repo = Repository(tmp_path / "capsule.db")
        try:
            return extract_capture(
                repository=repo, captures_root=tmp_path / "captures", capture_id=original,
                agent=Agent(name="x", model="m", transport=FakeTransport(json.dumps(ta._RAW))),
            )
        finally:
            repo.close()

    first = read()
    repo = Repository(tmp_path / "capsule.db")
    try:
        reinstate_capture(repository=repo, captures_root=tmp_path / "captures",
                          capture_id=original)
    finally:
        repo.close()
    second = read()
    ledger = AncestryLedger(tmp_path / "capsule.db")
    try:
        row = ledger.latest(ta._APID)
    finally:
        ledger.close()
    assert row.capture_id == second.replacement.name != first.replacement.name
