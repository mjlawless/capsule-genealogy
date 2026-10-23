"""Pre-debut index changes (2026-10-23): plain lifecycle lines, incomplete captures,
the "not read yet" wording, Wayback dropped, and the Help page."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from capsule.collection.index import render_index, write_index
from capsule.collection.read import CaptureRecord, read_capture_record

_DT = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def _record(**kw) -> CaptureRecord:
    base = CaptureRecord(
        capture_id="20261023-site-abc", folder_name="20261023-site-abc",
        url="https://example.org/r/1", title=None, site_name="example.org",
        record_type="death", gating="public", copyright_flag=False,
        captured_at="2026-10-23T12:00:00+00:00", wayback_url=None, short_note=None,
        source_list_entry=None, access_date=None, archived_url=None, personas=(),
        assertions=(), external_refs=(), media_files=(), file_types=("html",))
    return replace(base, **kw)


def test_an_incomplete_capture_is_marked_and_says_why():
    why = "it holds no measurable image at all"
    html = render_index([_record(verify_ok=False, verify_shortfall=(why,))], generated_at=_DT)
    assert f'class="chip incomplete" title="{why}">incomplete</span>' in html
    assert f"<dt>Incomplete</dt><dd>{why}</dd>" in html
    for ok in (True, None):
        assert "chip incomplete" not in render_index([_record(verify_ok=ok)], generated_at=_DT)


def test_verify_and_transcript_are_read_from_the_folder(tmp_path):
    folder = tmp_path / "20261023-site-abc"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"source": {"id": "s1"}, "archive": {}}))
    (folder / "result.json").write_text(json.dumps(
        {"verify_ok": False, "verify_shortfall": ["no image"]}))
    for run, when in (("a", "2026-09-28T10:00:00+00:00"), ("b", "2026-09-29T10:00:00+00:00")):
        (folder / "transcript" / run).mkdir(parents=True)
        (folder / "transcript" / run / "transcript.json").write_text(
            json.dumps({"transcription": {"created_at": when}}))
    record = read_capture_record(folder)
    assert record.verify_ok is False and record.verify_shortfall == ("no image",)
    assert record.transcribed_at == "2026-09-29T10:00:00+00:00"  # the newest run


def test_transcribed_links_to_the_reading_only_on_the_served_page():
    record = _record(transcribed_at="2026-09-29T10:00:00+00:00")
    served = render_index([record], generated_at=_DT, delete_token="t")
    static = render_index([record], generated_at=_DT)
    assert '<a href="/review/20261023-site-abc">2026-09-29 10:00 UTC · review the reading</a>' in served
    assert "<dt>Transcribed</dt><dd>2026-09-29 10:00 UTC</dd>" in static and "/review/" not in static


def test_not_read_yet_says_what_to_do():
    html = render_index([_record(extraction_pending=True)], generated_at=_DT)
    assert ">not read yet</span>" in html and "“Read them now”" in html
    assert "extraction pending" not in html and "awaiting OCR" not in html


def test_wayback_is_shown_only_if_a_capture_has_one():
    assert "<dt>Archived</dt>" not in render_index([_record()], generated_at=_DT)
    assert "<dt>Wayback</dt>" not in render_index([_record()], generated_at=_DT)
    html = render_index([_record(wayback_url="https://web.archive.org/web/1/x")], generated_at=_DT)
    assert "<dt>Wayback</dt>" in html


def test_the_help_page_is_linked_written_and_served(tmp_path):
    from fastapi.testclient import TestClient

    from capsule.backend.app import app

    (tmp_path / "20261023-site-abc").mkdir()
    (tmp_path / "20261023-site-abc" / "manifest.json").write_text(
        json.dumps({"source": {"id": "s1"}, "archive": {}}))
    index = write_index(tmp_path, now=lambda: _DT)
    assert 'href="help.html">Help</a>' in index.read_text(encoding="utf-8")
    help_file = Path(tmp_path) / "help.html"
    assert "How to use it" in help_file.read_text(encoding="utf-8")
    assert 'href="index.html"' in help_file.read_text(encoding="utf-8")  # back, offline

    client = TestClient(app)
    resp = client.get("/help.html")
    assert resp.status_code == 200 and "Correcting what was read" in resp.text
    back = client.get("/index.html", follow_redirects=False)
    assert back.status_code == 307 and back.headers["location"] == "/"


# -- record categories -----------------------------------------------------------------

@pytest.mark.parametrize(("record_type", "category"), [
    ("baptism", "Birth & baptism"),
    ("search results — Baptismal/Birth Records index (Church Baptism)", "Birth & baptism"),
    ("Church Marriage Record", "Marriage"),
    ("marriage license index", "Marriage"),
    ("Find a Grave index (burial/memorial)", "Death & burial"),
    ("obituary index", "Death & burial"),
    ("house interrogation records (husförhörslängd)", "Census & household"),
    ("church register (baptisms, marriages, burials, confirmations)", "Church registers"),
    ("probate (Administration Records Without Wills)", "Probate & wills"),
    ("property valuation (Griffith's Valuation)", "Land & property"),
    ("pension application (Revolutionary War pension/bounty-land)", "Military"),
    ("draft registration", "Military"),
    ("Naturalization Oath of Allegiance", "Immigration & naturalization"),
    ("passenger list (arriving passenger and crew list)", "Immigration & naturalization"),
    ("city directory", "Directories & books"),
    ("Social Security Applications and Claims Index", "Public records"),
    ("coverage/holdings table", "Reference lists"),
    ("unknown", "Unclassified"),
    (None, "Unclassified"),
])
def test_record_types_group_into_common_categories(record_type, category):
    from capsule.collection.record_types import record_category

    assert record_category(record_type) == category


def test_the_index_files_rows_by_category_and_keeps_the_exact_type():
    html = render_index([_record(record_type="Church Baptism Record")], generated_at=_DT)
    assert 'data-type="Birth &amp; baptism"' in html
    assert '<span title="Church Baptism Record">Birth &amp; baptism</span>' in html
    assert "<dt>Record type</dt><dd>Church Baptism Record (Birth &amp; baptism)</dd>" in html


def test_record_and_site_filters_take_several_choices():
    html = render_index([_record()], generated_at=_DT)
    for facet in ("f-type", "f-site"):
        assert f'<details class="ms" id="{facet}"' in html
    assert '<select id="f-status"' in html  # status stays a single choice


# -- open the folder by hand -----------------------------------------------------------

@pytest.fixture
def reveal_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from capsule.backend import app as backend
    from capsule.backend.app import Settings, app, get_settings

    root = tmp_path / "captures"
    (root / "20261023-site-abc").mkdir(parents=True)
    (root / "20261023-site-abc" / "manifest.json").write_text(
        json.dumps({"source": {"id": "s1"}, "archive": {}}))
    opened: list[Path] = []
    monkeypatch.setattr(backend, "_open_in_file_manager", opened.append)
    app.dependency_overrides[get_settings] = lambda: Settings(
        db_path=tmp_path / "capsule.db", captures_root=root, token="t")
    try:
        yield TestClient(app), root, opened
    finally:
        app.dependency_overrides.clear()


def test_open_folder_opens_only_the_archive_or_a_capture_in_it(reveal_client):
    client, root, opened = reveal_client
    auth = {"X-Capsule-Token": "t"}
    assert client.post("/reveal", json={"capture_id": "20261023-site-abc"}, headers=auth).status_code == 200
    assert client.post("/reveal", json={}, headers=auth).status_code == 200
    assert opened == [(root / "20261023-site-abc").resolve(), root.resolve()]
    assert client.post("/reveal", json={"capture_id": "../etc"}, headers=auth).status_code == 422
    assert client.post("/reveal", json={"capture_id": "20261023-site-zzz"}, headers=auth).status_code == 404
    assert client.post("/reveal", json={}).status_code == 401  # token-gated like every action
    assert len(opened) == 2


def test_the_served_index_shows_the_archive_and_open_buttons(reveal_client):
    client, root, _opened = reveal_client
    page = client.get("/").text
    assert f"Your archive: <code>{root.resolve()}</code>" in page
    assert '<button type="button" class="reveal">Open archive folder</button>' in page
    assert 'class="reveal" data-capture-id="20261023-site-abc">Open folder</button>' in page
    static = render_index([_record()], generated_at=_DT)
    assert 'class="reveal"' not in static and "Your archive:" not in static


# -- carrying a capture into a tree program ----------------------------------------------

def test_the_citation_is_shown_with_copy_buttons_in_both_index_files():
    record = _record(reference_note="Mary Kelly burial; <b>findagrave.com</b> (accessed 1 Oct 2026).",
                     source_list_entry="findagrave.com. \"Mary Kelly.\"")
    for html in (render_index([record], generated_at=_DT),
                 render_index([record], generated_at=_DT, delete_token="t")):
        assert '<div class="d-cite"><h3>Citation</h3>' in html
        assert ('<span class="cite-label">Reference note</span><span class="cite-text">'
                "Mary Kelly burial; &lt;b&gt;findagrave.com&lt;/b&gt; (accessed 1 Oct 2026).</span>"
                '<button type="button" class="copy">Copy</button>') in html
        assert '<span class="cite-label">Source list entry</span>' in html
        assert 'class="copy-ai"' in html and "function captureAsText" in html


def test_the_reference_note_is_read_from_citation_json(tmp_path):
    folder = tmp_path / "20261023-site-abc"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"source": {"id": "s1"}, "archive": {}}))
    (folder / "citation.json").write_text(json.dumps(
        {"reference_note": "Full note.", "source_list_entry": "Entry."}))
    record = read_capture_record(folder)
    assert record.reference_note == "Full note." and record.source_list_entry == "Entry."


def test_the_static_index_keeps_the_records_words_for_copy_for_ai():
    """Copy for AI reads each fact's supporting words from the row, on either page."""
    from capsule.collection.read import AssertionView

    fact = AssertionView("Mary Kelly", "death_date", "3 March 1881", "stated",
                         id="a" * 32, snippet='Died "3 March" 1881.')
    html = render_index([_record(assertions=(fact,))], generated_at=_DT)
    assert 'data-snippet="Died &quot;3 March&quot; 1881."' in html
    assert "data-assertion-id" not in html  # still no correction target in the static file
