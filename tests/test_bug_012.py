"""Regression: the floating button must not bank a thumbnail as a clean success (BUG-012).

Clicking the on-page `⬇ Capsule` button (or firing a bare `capsule:capture`) on an
image-backed Ancestry **record page** carries no `expected_apid`. The backend used to
derive `image_status` only when an apid was declared, so this path skipped the check
entirely: the in-page media sweep banked the record page's ~600px thumbnail and the
capture reported `verify OK, PDF ✓` with no image status at all — the "believe you hold a
record you do not hold" footgun.

The fix keys the derivation off the *record page* (its URL names the record), not off the
caller's expectation. An image-backed record with no full-resolution download now measures
`failed` — which the toast renders as `IMAGE ✗ failed — NOT mined` and reddens — so a
thumbnail can never again pass as success.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

_DT = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

# The SS Rhein passenger manifest from the bug report: an image-backed record, so its page
# links to its own scan in the image viewer.
_IMAGE_RECORD_URL = "https://www.ancestry.com/search/collections/7488/records/15429093"
_IMAGE_RECORD_PAGE = (
    "<html><head><title>New York, Passenger Lists</title></head><body>"
    "<p>Name: Anna Maria Muller</p><p>Arrival: 1871</p>"
    '<a href="/imageviewer/collections/7488/images/NYM237_351-0325?pId=15429093">'
    "View image</a>"
    "</body></html>"
)

# An index-only Ancestry record: no image-viewer link anywhere on the page.
_INDEX_RECORD_URL = "https://www.ancestry.com/search/collections/3693/records/35684453"
_INDEX_RECORD_PAGE = (
    "<html><head><title>U.S., Social Security Death Index</title></head><body>"
    "<p>Name: James T Doyle</p><p>Death: Feb 1969</p>"
    "<div>Original data: Social Security Administration. Social Security Death Master "
    "File. Social Security Administration.</div>"
    "</body></html>"
)

# A page that is NOT an Ancestry record page: image_status must stay None, unchanged.
_NON_RECORD_URL = "https://www.nyc.gov/vital-records/certificate/12345"
_NON_RECORD_PAGE = "<html><body><p>A birth certificate viewer.</p></body></html>"

_RAW = {
    "record_type": "index",
    "personas": [{"id": "p1", "label": "Anna Maria Muller", "role": "subject"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Arrival",
            "value": "1871",
            "evidence_type": "stated",
            "snippet": "Arrival: 1871",
        }
    ],
    "provider_claims": [],
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


def test_BUG_012_image_backed_record_without_apid_is_not_a_clean_success(tmp_path):
    """No apid, an image-backed record, no full download -> `failed`, never silent success."""
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            # No expected_apid, no image_intent — exactly the floating-button path.
            json={"url": _IMAGE_RECORD_URL, "html": _IMAGE_RECORD_PAGE},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        # The old behaviour was `None` (nothing on the toast); it must now name the miss so
        # the capture reddens instead of reading as complete.
        assert resp.json()["image_status"] == "failed"
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_012_index_only_record_without_apid_is_classified_from_the_page(tmp_path):
    """An index-only record has no scan to miss: `index-only-no-image`, not `failed`."""
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={"url": _INDEX_RECORD_URL, "html": _INDEX_RECORD_PAGE},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["image_status"] == "index-only-no-image"
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_012_non_ancestry_capture_makes_no_claim_about_the_record(tmp_path):
    """The guard is precise: a non-record page gets NO Ancestry inference about it.

    It now says `"none"` rather than nothing at all (REQ-007 §3.2) — a report on the bytes
    we hold, not a verdict on the page. What must never appear here is
    `index-only-no-image`, which would assert this record legitimately has no scan; that
    inference reads Ancestry's markup and cannot be made about a page we cannot parse.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={"url": _NON_RECORD_URL, "html": _NON_RECORD_PAGE},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["image_status"] == "none"
    finally:
        backend.app.dependency_overrides.clear()
