"""REQ-001 R6/R7 over HTTP — the ledger, the paced queue, and idempotency.

The queue is where compliance stops being a promise and becomes a mechanism: the agent
cannot find out what to capture next without asking, and asking too often returns 429.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

_CENSUS_APID = "1,7602::56510272"
_CENSUS_URL = "https://www.ancestry.com/search/collections/7602/records/56510272"
# Image-backed: the page links to its own scan (BUG-004 — the PAGE decides, not the caller).
_PAGE = (
    "<html><body><p>Name: James Doyal</p><p>Age: 12</p>"
    '<a href="/imageviewer/collections/7602/images/4114737_00288?pId=56510272">View</a>'
    "</body></html>"
)
_RAW = {
    "record_type": "census",
    "personas": [{"id": "p1", "label": "James Doyal", "role": "subject"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Age",
            "value": "12",
            "evidence_type": "stated",
            "snippet": "Age: 12",
        }
    ],
}


def _client(tmp_path):
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
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    return TestClient(app), backend


# An index-only record: no viewer link, so no image exists to go and get.
_INDEX_PAGE = (
    "<html><body><p>Name: James T Doyle</p>"
    "<div>Original data: Social Security Administration.</div></body></html>"
)


def _capture(client, *, html=None, image="full", intent="expected", follow_up=None, remine=False):
    """A capture of the census record. By default a COMPLETE one: image-backed page plus
    the full-resolution scan, which is the only combination that counts as mined."""
    body = {
        "expected_apid": _CENSUS_APID,
        "url": _CENSUS_URL,
        "html": html or _PAGE,
        "image_intent": intent,
        "remine": remine,
    }
    if follow_up:
        body["follow_up"] = follow_up
    if image == "full":
        body["media"] = [_jpeg(3000, 2400)]
    elif image:
        body["media"] = [image]
    return client.post("/capture", json=body, headers=_AUTH)


def _jpeg(width: int, height: int) -> dict:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (90, 90, 90)).save(buffer, format="JPEG")
    return {
        "filename": "record.jpg",
        "mime": "image/jpeg",
        "provenance": "ancestry:download",
        "data_base64": base64.b64encode(buffer.getvalue()).decode(),
    }


def test_BUG_the_ancestry_routes_are_not_swallowed_by_the_capture_catch_all(tmp_path):
    """Regression: routes match in DECLARATION order.

    `GET /{capture_id}/{file_path}` is declared before these, so an `/ancestry/next`
    registered after it would be answered as "no such capture named 'ancestry'" — the same
    trap `/favicon.ico` fell into. This proves the route is actually reachable.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.get("/ancestry/next", headers=_AUTH)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"apid": None, "record_url": None, "done": True}
    finally:
        backend.app.dependency_overrides.clear()


def test_seed_then_next_issues_the_record(tmp_path):
    client, backend = _client(tmp_path)
    try:
        seeded = client.post("/ancestry/seed", json={"apids": [_CENSUS_APID]}, headers=_AUTH)
        assert seeded.json()["seeded"] == 1
        # Idempotent: a re-run adds nothing rather than duplicating the work.
        again = client.post("/ancestry/seed", json={"apids": [_CENSUS_APID]}, headers=_AUTH)
        assert again.json()["seeded"] == 0

        nxt = client.get("/ancestry/next", headers=_AUTH).json()
        assert nxt["apid"] == "7602:56510272"
        assert nxt["record_url"] == _CENSUS_URL  # constructed, never fetched
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_asking_again_too_soon_is_429(tmp_path):
    """The pacing is a mechanism, not a request: the queue simply will not answer."""
    client, backend = _client(tmp_path)
    try:
        client.post(
            "/ancestry/seed",
            json={"apids": [_CENSUS_APID, "1,3693::35684453"]},
            headers=_AUTH,
        )
        assert client.get("/ancestry/next", headers=_AUTH).status_code == 200

        second = client.get("/ancestry/next", headers=_AUTH)
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) > 0
    finally:
        backend.app.dependency_overrides.clear()


def test_a_capture_records_itself_in_the_ledger(tmp_path):
    client, backend = _client(tmp_path)
    try:
        assert _capture(client).status_code == 200

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        assert ledger["mined"] == 1
        assert ledger["unmined"] == []
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_a_mined_record_is_a_no_op_not_a_duplicate(tmp_path):
    """R6.3. The batch loop is allowed to be dumb and re-ask; the ledger makes it safe."""
    client, backend = _client(tmp_path)
    try:
        assert _capture(client).status_code == 200

        again = _capture(client)
        assert again.status_code == 409
        assert "already mined" in again.json()["detail"]

        # ...but an explicit re-mine is honored.
        assert _capture(client, remine=True).status_code == 200
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_a_thumbnail_capture_leaves_the_record_UNMINED(tmp_path):
    """The clause that satisfies constraint 4: a bad image does not close a record.

    Without it, a 96x95 thumbnail would be filed as "mined", the record would leave the
    queue forever, and we would discover the loss only once Ancestry was gone.
    """
    client, backend = _client(tmp_path)
    try:
        resp = _capture(client, image=_jpeg(96, 95))
        assert resp.status_code == 200
        assert resp.json()["image_status"] == "failed"

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        assert ledger["mined"] == 0
        assert ledger["unmined"][0]["image"] == "failed"

        # And because it is unmined, capturing it again is NOT blocked.
        assert _capture(client).status_code == 200
        assert client.get("/ancestry/ledger", headers=_AUTH).json()["mined"] == 1
    finally:
        backend.app.dependency_overrides.clear()


def test_an_index_only_capture_carries_its_follow_up(tmp_path):
    """R4.2: capturing the SSDI is not capturing the SS-5. The ledger says so."""
    client, backend = _client(tmp_path)
    try:
        resp = _capture(
            client,
            html=_INDEX_PAGE,  # no viewer link: the RECORD says it has no image
            image=None,
            intent="none",
            follow_up="SS-5 application (Social Security Admin.)",
        )
        assert resp.status_code == 200
        assert resp.json()["image_status"] == "index-only-no-image"

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        assert ledger["mined"] == 1  # we took everything the index HAS
        assert "SS-5" in ledger["follow_ups"][0]["follow_up"]  # and still owe the original
    finally:
        backend.app.dependency_overrides.clear()
