"""C6 slice 1: the served index shows corrections (read-only), the static file does not.

Offline, through the backend: capture one page, read each fact's id off the served
index (`GET /`), correct it with `POST /correction`, and read the page again. The
id the page offers must always be the CURRENT one, so a fact can be corrected twice.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}
_URL = "https://www.findagrave.com/memorial/1"
_PAGE = (
    "<html><head><title>Mary Kelly (1810-1881)</title></head>"
    "<body><h1>Mary Kelly</h1><p>Died 3 March 1881.</p>"
    "<p>Buried at Glasnevin Cemetery, Dublin.</p></body></html>"
)
_RAW = {
    "record_type": "burial",
    "personas": [{"label": "Mary Kelly", "role": "deceased"}],
    "assertions": [
        {"persona": "Mary Kelly", "field": "death_date", "value": "3 March 1881",
         "evidence_type": "stated", "snippet": "Died 3 March 1881."},
        {"persona": "Mary Kelly", "field": "burial_place", "value": "Glasnevin Cemetery, Dublin",
         "evidence_type": "stated", "snippet": "Buried at Glasnevin Cemetery, Dublin."},
    ],
}


@pytest.fixture
def served(tmp_path):
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=_TOKEN,
        fixtures_root=tmp_path / "fixtures",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor",
        model="claude-opus-5-5",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    client = TestClient(app)
    resp = client.post("/capture", json={"url": _URL, "html": _PAGE, "gating": "public"}, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    capture_id = Path(resp.json()["capture_folder"]).name
    try:
        yield client, settings, capture_id, resp.json()
    finally:
        app.dependency_overrides.clear()


def _page(client) -> str:
    resp = client.get("/")
    assert resp.status_code == 200, resp.text
    return resp.text


def _row(page: str, field: str) -> str:
    """The served page's `<tr>` for one fact (the detail block, not the summary row)."""
    # re.S: a correction tag's tooltip holds one line per correction.
    rows = re.findall(r"<tr[^>]*><td>[^<]*</td><td>" + re.escape(field) + r"</td>.*?</tr>", page,
                      re.S)
    assert len(rows) == 1, rows
    return rows[0]


def _id(row: str) -> str | None:
    m = re.search(r'data-assertion-id="([0-9a-f]+)"', row)
    return m.group(1) if m else None


def _history(row: str) -> list[str]:
    """The fact's correction history, as the ✎ form receives it (`data-history`)."""
    m = re.search(r'data-history="([^"]*)"', row)
    return json.loads(html.unescape(m.group(1))) if m else []


def _tag(row: str) -> tuple[str, str] | None:
    """(word, tooltip) of the row's correction tag."""
    m = re.search(r'<span class="tag" title="([^"]*)">([^<]*)</span>', row)
    return (m.group(2), html.unescape(m.group(1))) if m else None


def _correct(client, capture_id: str, **body) -> dict:
    resp = client.post("/correction", json={"capture_id": capture_id, **body}, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_uncorrected_capture_offers_its_fact_ids_and_is_not_reviewed(served):
    client, _settings, _cid, body = served
    page = _page(client)
    ids = {a["field"]: a["id"] for a in body["assertions"]}
    assert _id(_row(page, "death_date")) == ids["death_date"]
    assert _id(_row(page, "burial_place")) == ids["burial_place"]
    assert "pill s-extracted" in page and "pill s-reviewed" not in page
    assert "<dt>Reviewed</dt><dd>—</dd>" in page


def test_a_fact_can_be_corrected_twice_from_the_ids_the_page_offers(served):
    client, settings, cid, _body = served
    first = _correct(client, cid, action="edit", assertion_id=_id(_row(_page(client), "death_date")),
                     new_value="13 March 1881")
    page = _page(client)
    row = _row(page, "death_date")
    assert '<span class="v">13 March 1881</span>' in row and _tag(row)[0] == "edited"
    assert "“3 March 1881”" not in re.sub(r'data-history="[^"]*"|title="[^"]*"', "", row)
    (line,) = _history(row)  # the history is for the ✎ form, not the table
    assert line.startswith("“3 March 1881” → “13 March 1881” · owner · ")
    assert _id(row) == first["assertion_id"]  # the CURRENT row, not the superseded one
    assert "pill s-reviewed" in page and "1 correction · latest" in page

    second = _correct(client, cid, action="edit", assertion_id=_id(row), new_value="18 March 1881")
    row = _row(_page(client), "death_date")
    assert _id(row) == second["assertion_id"]
    assert "18 March 1881" in row
    assert [h.split(" · ")[0] for h in _history(row)] == [  # oldest first
        "“3 March 1881” → “13 March 1881”", "“13 March 1881” → “18 March 1881”"]
    assert _tag(row)[0] == "edited"  # one tag, however long the history

    # The static file is the sealed reading: no ids, no corrections, never "reviewed" —
    # including when it is regenerated AFTER the corrections exist.
    from capsule.collection.index import write_index

    static = write_index(settings.captures_root).read_text(encoding="utf-8")
    assert "data-assertion-id" not in static
    assert "pill s-reviewed" not in static
    assert "3 March 1881" in static and "18 March 1881" not in static


def test_a_retracted_fact_stays_visible_struck_and_cannot_be_targeted(served):
    client, _settings, cid, _body = served
    _correct(client, cid, action="retract", assertion_id=_id(_row(_page(client), "burial_place")))
    row = _row(_page(client), "burial_place")
    assert 'class="retracted"' in row
    assert "<s>Glasnevin Cemetery, Dublin</s>" in row
    word, tooltip = _tag(row)  # no ✎ form on a retracted fact: its history is the tooltip
    assert word == "retracted" and tooltip.startswith("retracted “Glasnevin Cemetery, Dublin” · owner")
    assert _id(row) is None


def test_an_added_fact_is_listed_last_with_its_persona(served):
    client, settings, cid, _body = served
    from capsule.store.repository import Repository

    repo = Repository(settings.db_path)
    try:
        (sid,) = repo.connection.execute("SELECT id FROM source").fetchone()
        persona_id = repo.personas_for_source(sid)[0].id
    finally:
        repo.close()
    added = _correct(client, cid, action="add", persona_id=persona_id, field="age_at_death",
                     new_value="71", evidence_type="stated", snippet_text="aged 71")
    page = _page(client)
    row = _row(page, "age_at_death")
    assert row.startswith("<tr") and "<td>Mary Kelly</td>" in row
    assert _tag(row)[0] == "added" and _id(row) == added["assertion_id"]
    assert _history(row)[0].startswith("added “71” · owner")
    assert page.index("burial_place") < page.index("age_at_death")


def test_a_folder_fact_missing_from_the_database_gets_no_id(served):
    """Drift between folder and database must never yield a button the backend refuses."""
    from dataclasses import replace

    from capsule.collection.corrections import overlay_corrections
    from capsule.collection.read import AssertionView, read_all
    from capsule.store.repository import Repository

    client, settings, cid, _body = served
    _correct(client, cid, action="edit", assertion_id=_id(_row(_page(client), "death_date")),
             new_value="13 March 1881")
    (record,) = read_all(settings.captures_root)
    stray = AssertionView("Mary Kelly", "occupation", "widow", "stated", id="f" * 32)
    record = replace(record, assertions=(*record.assertions, stray))
    repo = Repository(settings.db_path)
    try:
        (overlaid,) = overlay_corrections([record], repo)
    finally:
        repo.close()
    by_field = {a.field: a for a in overlaid.assertions}
    assert by_field["occupation"].id is None and by_field["occupation"].value == "widow"
    assert by_field["death_date"].value == "13 March 1881"


def test_no_corrections_means_the_records_are_returned_untouched(served):
    from capsule.collection.corrections import overlay_corrections
    from capsule.collection.read import read_all
    from capsule.store.repository import Repository

    _client, settings, _cid, _body = served
    records = read_all(settings.captures_root)
    repo = Repository(settings.db_path)
    try:
        assert overlay_corrections(records, repo) == records
    finally:
        repo.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_index_scripts_parse():
    from capsule.collection.assets import INDEX_CORRECT_JS, INDEX_DELETE_JS, INDEX_JS

    for source in (INDEX_JS, INDEX_DELETE_JS, INDEX_CORRECT_JS):
        proc = subprocess.run(
            ["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"],
            input=source, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
