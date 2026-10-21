"""BUG-005 — an aborted capture must not vanish without trace.

Found on the first production run. The agent dispatched a capture and navigated to the next
record while it was still in flight, which killed it. Result: no capture folder, no ledger
row, no error. The record stayed `seeded` — indistinguishable from one nobody had tried.

**The safety invariant held**, and that is the important half: the aborted capture wrote
nothing, so the record stayed UNMINED and on the queue. We did not come to believe we held
a record we did not hold.

But the ledger had an `error` column from its first commit and never once wrote to it. Over
a run of hundreds of records, "tried and died" and "not yet attempted" are very different things, and a
silent abort was only discoverable by someone happening to see a toast flash.

Note WHY a `started` marker is needed and backend error-logging alone is not enough: in the
abort case the POST never arrives. The backend cannot record a failure for a capture it
never learned about.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

_APID = "1,60901::626630211"  # the record that vanished: SS Applications & Claims Index
_URL = "https://www.ancestry.com/search/collections/60901/records/626630211"
_PAGE = "<html><body><p>Name: James T Doyle</p></body></html>"
_RAW = {
    "record_type": "index",
    "personas": [{"id": "p1", "label": "James T Doyle", "role": "subject"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Name",
            "value": "James T Doyle",
            "evidence_type": "stated",
            "snippet": "Name: James T Doyle",
        }
    ],
}


def _client(tmp_path, response_text=json.dumps(_RAW)):
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend import app as backend
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=_TOKEN,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(response_text),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    return TestClient(app, raise_server_exceptions=False), backend


def test_BUG_005_a_capture_that_died_in_flight_is_visible_as_STALLED(tmp_path):
    """The exact scenario: marked started, then the tab navigated away. Nothing else.

    The POST never arrives, so the backend can never log a failure for it — the `started`
    marker is the ONLY evidence the capture ever happened.
    """
    client, backend = _client(tmp_path)
    try:
        client.post("/ancestry/seed", json={"apids": [_APID]}, headers=_AUTH)
        # The extension marks it in flight... and then the page navigates away. No capture
        # POST ever follows.
        started = client.post("/ancestry/started", json={"apid": _APID}, headers=_AUTH)
        assert started.status_code == 200, started.text

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        assert ledger["mined"] == 0
        assert ledger["stalled"] == [{"apid": "60901:626630211"}]
        assert "STALLED" in ledger["unmined"][0]["status"]
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_005_a_stalled_record_is_distinguishable_from_one_never_attempted(tmp_path):
    """The whole point: `seeded` and "tried and died" must not look the same."""
    client, backend = _client(tmp_path)
    try:
        client.post(
            "/ancestry/seed", json={"apids": [_APID, "1,7602::56510272"]}, headers=_AUTH
        )
        client.post("/ancestry/started", json={"apid": _APID}, headers=_AUTH)

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        by_apid = {row["apid"]: row for row in ledger["unmined"]}

        assert "STALLED" in by_apid["60901:626630211"]["status"]  # tried, and vanished
        assert by_apid["7602:56510272"]["status"] == "pending"  # simply not done yet
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_005_a_client_side_failure_is_recorded_with_its_reason(tmp_path):
    """An identity mismatch or a dead render is invisible to the backend. Report it."""
    client, backend = _client(tmp_path)
    try:
        client.post(
            "/ancestry/failed",
            json={"apid": _APID, "error": "IDENTITY MISMATCH: this page is 60525:118449"},
            headers=_AUTH,
        )
        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        row = ledger["unmined"][0]
        assert "IDENTITY MISMATCH" in row["error"]
        assert "FAILED" in row["status"]
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_005_a_capture_that_reaches_the_backend_and_blows_up_leaves_an_error_row(tmp_path):
    """The other silence: a 5xx. The ledger's `error` column had never once been written."""
    client, backend = _client(tmp_path, response_text="this is not JSON at all")
    try:
        # The fake model returns garbage, so the extraction raises inside the pipeline.
        resp = client.post(
            "/capture",
            json={"expected_apid": _APID, "url": _URL, "html": _PAGE},
            headers=_AUTH,
        )
        assert resp.status_code >= 400

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        row = ledger["unmined"][0]
        assert row["error"], "a failed capture must leave its reason in the ledger"
        assert ledger["mined"] == 0
    finally:
        backend.app.dependency_overrides.clear()


def test_a_stalled_record_is_still_re_issued_by_the_queue(tmp_path):
    """It is not lost — merely visible. The queue still hands it back."""
    client, backend = _client(tmp_path)
    try:
        client.post("/ancestry/seed", json={"apids": [_APID]}, headers=_AUTH)
        client.post("/ancestry/started", json={"apid": _APID}, headers=_AUTH)

        nxt = client.get("/ancestry/next", headers=_AUTH).json()
        assert nxt["apid"] == "60901:626630211"
    finally:
        backend.app.dependency_overrides.clear()


def test_the_ledger_migrates_a_database_that_predates_started(tmp_path):
    """A live DB was written before `started` was a legal event.

    Its CHECK constraint would REJECT the very row that records "a capture began" — and
    `CREATE TABLE IF NOT EXISTS` never alters an existing table. So the table is rebuilt,
    copying every row: no history rewritten, none lost.
    """
    import sqlite3

    db = tmp_path / "capsule.db"
    old = sqlite3.connect(str(db))
    old.executescript(
        """
        CREATE TABLE ancestry_ledger_event (
            id TEXT PRIMARY KEY, apid TEXT NOT NULL, collection_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            event TEXT NOT NULL CHECK (event IN
                ('seeded','issued','captured','failed','remine-requested')),
            capture_id TEXT, image_status TEXT, assertion_count INTEGER,
            persona_count INTEGER, verify_ok INTEGER, follow_up TEXT, error TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO ancestry_ledger_event
            (id, apid, collection_id, record_id, event, created_at)
        VALUES ('e1', '7602:56510272', '7602', '56510272', 'seeded',
                '2026-07-14T00:00:00+00:00');
        """
    )
    old.commit()
    old.close()

    from capsule.store.ledger import AncestryLedger

    ledger = AncestryLedger(db)
    try:
        # The old row survived the rebuild...
        assert [r.apid for r in ledger.all_latest()] == ["7602:56510272"]
        # ...and the new event is accepted, where before it would have been rejected.
        ledger.record_started("1,7602::56510272")
        assert ledger.latest("1,7602::56510272").stalled
    finally:
        ledger.close()
