"""REQ-001 — the Ancestry identity guard and the record-image gate, over POST /capture.

Two safety properties, both learned the hard way:

1. **Identity (R5).** In the pilot the researcher read a page they believed was the 1900
   census; it was a School Yearbook entry for a different person. A wrong record, hashed
   and cited onto a direct ancestor, is silent corruption nobody re-checks — precisely
   because it SUCCEEDED. So a mismatch aborts and writes nothing.

2. **Pixels (R1.4).** Ancestry's media API returns a 96x95 thumbnail with valid magic
   bytes, valid MIME, plausible size and a correct SHA-256. Every check we had before
   BUG-001 passes on it. Only decoding catches it.

Offline: the model call is faked, no network.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

_ANCESTRY_URL = "https://www.ancestry.com/search/collections/7602/records/56510272"
_OTHER_RECORD_URL = "https://www.ancestry.com/search/collections/60525/records/118449"
_APID = "1,7602::56510272"

# An IMAGE-BACKED record: it links to its own scan. That link is how the backend knows an
# image exists at all (BUG-004) — without it a record is index-only by definition, and a
# missing image is the honest answer rather than a failure.
_PAGE = (
    "<html><head><title>1900 United States Federal Census</title></head>"
    "<body><p>Name: James Doyal</p><p>Age: 12</p>"
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
    return TestClient(app), backend


def _image_b64(width: int, height: int) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


def _ancestry_media(width: int, height: int, provenance: str = "ancestry:download") -> dict:
    return {
        "filename": "record.jpg",
        "mime": "image/jpeg",
        "provenance": provenance,
        "data_base64": _image_b64(width, height),
    }


# -- R5: the identity guard --------------------------------------------------


def test_BUG_the_yearbook_incident_a_mismatched_apid_is_409_and_writes_nothing(tmp_path):
    """Regression: capturing the WRONG record must be impossible, not merely discouraged."""
    client, backend = _client(tmp_path)
    captures_root = tmp_path / "captures"
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _APID,  # we asked for the census...
                "url": _OTHER_RECORD_URL,  # ...the browser is on a different record
                "html": _PAGE,
            },
            headers=_AUTH,
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert "7602:56510272" in detail  # what was asked for
        assert "60525:118449" in detail  # what was actually there

        written = list(captures_root.glob("*")) if captures_root.exists() else []
        assert written == [], f"a refused capture wrote something: {written}"
    finally:
        backend.app.dependency_overrides.clear()


def test_a_matching_apid_proceeds(tmp_path):
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={"expected_apid": _APID, "url": _ANCESTRY_URL, "html": _PAGE},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["verify_ok"] is True
    finally:
        backend.app.dependency_overrides.clear()


def test_an_unparseable_url_with_an_expectation_is_refused(tmp_path):
    """Cannot-verify is NOT verified — an unknown page shape is never waved through."""
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={"expected_apid": _APID, "url": "https://example.test/x", "html": _PAGE},
            headers=_AUTH,
        )
        assert resp.status_code == 409, resp.text
    finally:
        backend.app.dependency_overrides.clear()


def test_a_capture_with_no_expectation_is_unaffected(tmp_path):
    """Every existing (non-Ancestry) caller keeps working: no expectation, no guard.

    The reported value moved from `None` to `"none"` (REQ-007 §3.2) and the distinction is
    the point: `None` meant three things at once — not applicable, not looked at, and
    nothing found — which is how an Archion capture holding no register page read as clean.
    `"none"` says only the third, about THIS capture's own bytes, and claims nothing about
    what the record holds. It is not a fault: `dedup._assess` deliberately falls through it.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture", json={"url": "https://x.example/1", "html": _PAGE}, headers=_AUTH
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["image_status"] == "none"
    finally:
        backend.app.dependency_overrides.clear()


# -- R1.4 / R4.3: the record-image gate --------------------------------------


def test_BUG_a_thumbnail_download_is_image_failed_but_the_capture_is_archived(tmp_path):
    """Regression: a 96x95 thumbnail must never read as a captured record.

    It IS archived — the bytes are evidence of what Ancestry served that day — but
    `image: failed`, a loud warning, and the record is not mined, so it stays on the
    queue. Constraint 4: we must never BELIEVE we hold a record we do not hold.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _APID,
                "url": _ANCESTRY_URL,
                "html": _PAGE,
                "image_intent": "expected",
                "media": [_ancestry_media(96, 95)],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "failed"
        assert any("THUMBNAIL" in w for w in body["media_warnings"])
        # The evidence of the attempt still lands, hashed and verified.
        assert body["verify_ok"] is True
        assert body["media_count"] == 1
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_006_a_small_downloaded_document_is_mined_not_stuck_on_the_queue(tmp_path):
    """Regression: the WWII draft card (1815x1200, ancestry:download) is FULL.

    It was rejected as `degraded-canvas` — not a canvas, and not degraded: it is the full
    resolution Ancestry holds for that collection. The record was COMPLETE and the tool said
    it was not, so it sat on the queue forever, re-mined to no effect. That is the mirror of
    the ledger's invariant — and small documents (draft cards, index cards, headstone photos,
    obituary clippings) are a large share of a real tree.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _APID,
                "url": _ANCESTRY_URL,
                "html": _PAGE,
                "image_intent": "expected",
                "media": [_ancestry_media(1815, 1200)],  # provenance: ancestry:download
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "full"
        assert body["media_warnings"] == []  # complete evidence — do not cry wolf over it

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        assert ledger["mined"] == 1  # and it LEAVES the queue, which was the whole problem
    finally:
        backend.app.dependency_overrides.clear()


def test_the_canvas_fallback_is_degraded_not_full(tmp_path):
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _APID,
                "url": _ANCESTRY_URL,
                "html": _PAGE,
                "image_intent": "expected",
                "media": [_ancestry_media(1370, 1092, provenance="viewer:canvas-fallback")],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "degraded-canvas"
        # Named for what it IS — a canvas capture — not for its pixel count. A download of
        # the same size would be the full record (BUG-006); the canvas never is.
        assert any("CANVAS" in w for w in body["media_warnings"])
    finally:
        backend.app.dependency_overrides.clear()


def test_a_full_scan_is_image_full_with_no_warning(tmp_path):
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _APID,
                "url": _ANCESTRY_URL,
                "html": _PAGE,
                "image_intent": "expected",
                "media": [_ancestry_media(3000, 2400)],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "full"
        assert body["media_warnings"] == []
    finally:
        backend.app.dependency_overrides.clear()


def test_an_index_only_record_is_not_a_failure(tmp_path):
    """The SSDI control: no image is the HONEST answer, not a broken capture.

    Conflating "this record has no image" with "the image failed" would either send us
    back for a scan that never existed, or let a broken download hide as an index entry.
    Note the page: an index-only record carries NO image-viewer link, and that — not the
    caller's say-so — is what makes it index-only (BUG-004).
    """
    index_only_page = (
        "<html><head><title>U.S., Social Security Death Index</title></head>"
        "<body><p>Name: James T Doyle</p></body></html>"
    )
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": "1,3693::35684453",
                "url": "https://www.ancestry.com/search/collections/3693/records/35684453",
                "html": index_only_page,
                "image_intent": "none",
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "index-only-no-image"
        assert body["media_warnings"] == []
    finally:
        backend.app.dependency_overrides.clear()


def test_an_expected_image_that_never_arrived_is_failed(tmp_path):
    """The download broke. That is a FAILURE — not an index-only record."""
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _APID,
                "url": _ANCESTRY_URL,
                "html": _PAGE,
                "image_intent": "expected",
                "media": [],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["image_status"] == "failed"
    finally:
        backend.app.dependency_overrides.clear()


def test_a_page_served_image_is_not_pixel_gated(tmp_path):
    """No crying wolf: a small image the PAGE served is not a failed record download."""
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": "https://x.example/1",
                "html": _PAGE,
                "media": [
                    {
                        "filename": "seal.jpg",
                        "mime": "image/jpeg",
                        "source_url": "https://example.org/seal.jpg",
                        "data_base64": _image_b64(96, 95),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["media_warnings"] == []
        # Still no warning and still no record image — a page-served seal was never
        # claimed to be one. `"none"` reports that we hold none; it is not a failure.
        assert body["image_status"] == "none"
    finally:
        backend.app.dependency_overrides.clear()
