"""BUG-007 — a render of the page is NOT the record.

The NYC birth certificate of a person in the tree — an original source with primary
information, which DISPROVES a `Proven` claim in the tree — was "captured" as a 977x1082
screenshot inside a page render. The toast said:

    Captured birth — 6 assertion(s), verify OK, PDF ✓

Everything green. Only opening the artifact and measuring it revealed that we were holding a
picture of a screen showing the most important record on the line.

The mechanism: the certificate is a SAS-signed Azure blob behind Chrome's native PDF plugin
— no <img>, no link, and the plugin's Download button is not in the accessibility tree. The
popup has been able to grab it since July; the agent-trigger path never could, because ALL
the media-grabbing logic lived in popup.js. When the research moved to an agent, a working
capability silently stopped working.

The fix has two halves, and the second matters more:
  1. the agent path now grabs embedded record files too;
  2. a file the page HELD and we did not get is reported LOUDLY — a capture that quietly
     substitutes a render for the record is exactly what this project exists to prevent.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

_NYC_URL = "https://a860-historicalvitalrecords.nyc.gov/view/197790"
_PAGE = (
    "<html><head><title>B-K-1887-0011687</title></head>"
    "<body><p>Name: James Doyle</p><p>Born: 2 Jan 1887</p></body></html>"
)
_RAW = {
    "record_type": "birth",
    "personas": [{"id": "p1", "label": "James Doyle", "role": "subject"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Born",
            "value": "2 Jan 1887",
            "evidence_type": "stated",
            "snippet": "Born: 2 Jan 1887",
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


def test_BUG_007_a_missed_record_file_makes_the_capture_report_INCOMPLETE(tmp_path):
    """Regression: the capture must not read as healthy when the record is missing.

    The archive still keeps what it got — snapshot, extraction and render are real evidence.
    But it must SAY, loudly, that what is missing is the record itself. Silence is how a
    screenshot of a birth certificate came to sit in the archive looking like a completed
    capture.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "missed_media": [
                    "B-K-1887-0011687.pdf — Capsule has no access to "
                    "https://dorisarchive.blob.core.windows.net"
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # The evidence we DID get still lands.
        assert body["verify_ok"] is True
        assert body["assertions"]

        # ...and the capture says, in as many words, that it is not the record.
        (warning,) = body["media_warnings"]
        assert "RECORD FILE NOT CAPTURED" in warning
        assert "B-K-1887-0011687.pdf" in warning
        assert "page render" in warning
    finally:
        backend.app.dependency_overrides.clear()


def test_the_embedded_certificate_is_archived_with_its_signed_source_url(tmp_path):
    """When we DO get the file, the signed URL is recorded — it can never be rebuilt.

    The SAS signature is time-limited and per-session, so the URL is not reconstructable
    afterwards. It is the only record of where this document actually came from.
    """
    import base64
    from pathlib import Path

    signed = (
        "https://dorisarchive.blob.core.windows.net/reference/REC0051_VitalRecords/"
        "Births/Kings/1887/B-K-1887-0011687.pdf?se=2026-07-14&sp=r&sig=abc123"
    )
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "B-K-1887-0011687.pdf",
                        "mime": "application/pdf",
                        "source_url": signed,
                        "provenance": "page:embedded",
                        "data_base64": base64.b64encode(b"%PDF-1.4 the certificate").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["media_count"] == 1
        assert body["media_warnings"] == []  # we have the record; nothing to warn about

        manifest = json.loads(
            (Path(body["capture_folder"]) / "manifest.json").read_text(encoding="utf-8")
        )
        entry = manifest["media"][0]
        assert entry["path"] == "media/0-b-k-1887-0011687.pdf"
        assert entry["source_url"] == signed  # the un-reconstructable provenance
        assert entry["provenance"] == "page:embedded"
    finally:
        backend.app.dependency_overrides.clear()


def test_a_page_with_no_embedded_record_raises_no_warning(tmp_path):
    """No crying wolf: most pages hold no record file, and that is not a failure."""
    client, backend = _client(tmp_path)
    try:
        resp = client.post("/capture", json={"url": _NYC_URL, "html": _PAGE}, headers=_AUTH)
        assert resp.status_code == 200, resp.text
        assert resp.json()["media_warnings"] == []
    finally:
        backend.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# BUG-008 — the bytes decide what a file IS
# ---------------------------------------------------------------------------


def test_BUG_008_a_javascript_file_is_never_stored_as_a_pdf(tmp_path):
    """Regression: `pdfobject.min.js` was fetched, renamed `.pdf`, declared application/pdf.

    It would have passed every hash check and every "the file exists" check forever, while
    being neither a PDF nor evidence. CLAUDE.md 12.4: a checksum proves INTEGRITY, not
    IDENTITY — an evidence image that is not an image is not evidence.

    A filename and a Content-Type are hints from an untrusted page. The BYTES are the file.
    """
    import base64
    from pathlib import Path

    javascript = b"!function(e,t){/* pdfobject.min.js */}(this);"

    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        # Exactly what the grabber sent: a .js URL, called a PDF.
                        "filename": "pdfobject.min.js",
                        "mime": "application/pdf",
                        "source_url": (
                            "https://a860-historicalvitalrecords.nyc.gov/static/js/"
                            "plugins/pdfobject/pdfobject.min.js"
                        ),
                        "provenance": "page:embedded",
                        "data_base64": base64.b64encode(javascript).decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        manifest = json.loads(
            (Path(body["capture_folder"]) / "manifest.json").read_text(encoding="utf-8")
        )
        entry = manifest["media"][0]
        # Stored INERT, under what it actually is — never as a PDF.
        assert entry["mime"] != "application/pdf"
        assert entry["path"].endswith(".bin")
        assert not entry["path"].endswith(".pdf")

        # ...and the contradiction is said out loud, not silently corrected.
        assert any("integrity, not identity" in w for w in body["media_warnings"])
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_008_a_real_pdf_mislabelled_as_an_image_is_stored_as_a_pdf(tmp_path):
    """The bytes decide in BOTH directions — a real record is not lost to a bad label."""
    import base64
    from pathlib import Path

    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "certificate.jpg",  # wrong
                        "mime": "image/jpeg",  # wrong
                        "provenance": "page:embedded",
                        "data_base64": base64.b64encode(b"%PDF-1.4 the real thing").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        manifest = json.loads(
            (Path(resp.json()["capture_folder"]) / "manifest.json").read_text(encoding="utf-8")
        )
        entry = manifest["media"][0]
        assert entry["mime"] == "application/pdf"
        assert entry["path"].endswith(".pdf")
    finally:
        backend.app.dependency_overrides.clear()


def test_a_correctly_typed_file_raises_no_warning(tmp_path):
    """No crying wolf when the browser told the truth."""
    import base64

    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "B-K-1887-0011687.pdf",
                        "mime": "application/pdf",
                        "provenance": "page:embedded",
                        "data_base64": base64.b64encode(b"%PDF-1.4 certificate").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["media_warnings"] == []
    finally:
        backend.app.dependency_overrides.clear()
