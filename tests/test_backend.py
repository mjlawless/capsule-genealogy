"""Tests for the browser-capture path — ProvidedContentFetcher (+ POST /capture).

The real Capsule capture is the page in view from the user's own browser (in the
user's own session), posted to a localhost backend that runs the existing pipeline.
These tests exercise that path fully OFFLINE: content is *provided* (no network
fetch) and the model call is faked.
"""
from __future__ import annotations

from datetime import datetime, timezone

from capsule.contracts.evidence import GatingStatus

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_PAGE = (
    "<html><head><title>Death Certificate 4017734</title></head>"
    "<body><p>Name: Mary Kelly</p><p>Died: 3 March 1881</p></body></html>"
)


def test_provided_content_fetcher_wraps_given_html_without_network():
    from capsule.pipeline.fetch import ProvidedContentFetcher

    fetcher = ProvidedContentFetcher(_PAGE, gating=GatingStatus.GATED, now=lambda: _DT)
    url = "https://a860-historicalvitalrecords.nyc.gov/view/4017734"
    snap = fetcher.fetch(url)

    assert snap.url == url
    assert snap.html == _PAGE
    assert snap.gating is GatingStatus.GATED  # the browser/user declares gating
    assert snap.title == "Death Certificate 4017734"
    assert snap.fetched_at == _DT


def test_provided_content_fetcher_defaults_to_public():
    from capsule.pipeline.fetch import ProvidedContentFetcher

    snap = ProvidedContentFetcher(_PAGE, now=lambda: _DT).fetch("https://x.example/1")
    assert snap.gating is GatingStatus.PUBLIC


# -- POST /capture (offline: injected fake Agent, no network, no tokens) ---

import json  # noqa: E402

_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}
_NYC_URL = "https://a860-historicalvitalrecords.nyc.gov/view/4017734"
_RAW = {
    "record_type": "death",
    "personas": [{"label": "Mary Kelly", "role": "deceased"}],
    "assertions": [
        {
            "persona": "Mary Kelly",
            "field": "death_date",
            "value": "3 March 1881",
            "evidence_type": "stated",
            "snippet": "Died: 3 March 1881",
        }
    ],
}


def _client(tmp_path, response_text, token=_TOKEN):
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend import app as backend
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=token,
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


def test_health():
    from fastapi.testclient import TestClient

    from capsule.backend.app import app

    assert TestClient(app).get("/health").json()["status"] == "ok"


def test_post_capture_captures_provided_page(tmp_path):
    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={"url": _NYC_URL, "html": _PAGE, "gating": "gated"},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verify_ok"] is True
        assert body["url"] == _NYC_URL
        assert body["gating"] == "gated"  # the browser declared it
        assert body["record_type"] == "death"
        (out,) = body["assertions"]
        # `id` is a generated assertion id — stable shape, not a fixed value — and
        # `persona_label` is resolved from the persona the assertion points at, so
        # the accept/correct UI (C5/C6) can target one fact and group by person.
        assert isinstance(out.pop("id"), str) and out["persona_label"] == "Mary Kelly"
        out.pop("persona_label")
        assert out == {
            "field": "death_date",
            "verbatim_value": "3 March 1881",
            "evidence_type": "stated",
            "unverified": False,  # snippet is on the provided page
        }
        assert (tmp_path / "captures").is_dir()
    finally:
        backend.app.dependency_overrides.clear()


def test_media_provenance_records_a_url_derived_from_viewer_state(tmp_path):
    """A tiled book viewer exposes no full-page image, so the capturer synthesizes
    the URL. The manifest must say so — a reader could not have found it in the DOM.
    """
    import base64
    from pathlib import Path

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": "https://registers.nli.ie/registers/vtls000632043#page/26/mode/1up",
                "html": _PAGE,
                "media": [
                    {
                        "filename": "vtls000632043_026.jpg",
                        "mime": "image/jpeg",
                        "source_url": "https://iserver.nli.ie/fcgi-bin/iipsrv.fcgi?FIF=x&CVT=jpeg",
                        "provenance": "viewer:bookreader-iip leaf=25",
                        "data_base64": base64.b64encode(b"\xff\xd8\xff fake jpeg").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        folder = Path(resp.json()["capture_folder"])
        entry = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["media"][0]
        assert entry["path"] == "media/0-vtls000632043-026.jpg"
        assert entry["provenance"] == "viewer:bookreader-iip leaf=25"
        assert resp.json()["verify_ok"] is True
    finally:
        backend.app.dependency_overrides.clear()


def test_media_without_provenance_omits_the_key_entirely(tmp_path):
    """Additive: a URL read straight off the page keeps a byte-identical manifest."""
    import base64
    from pathlib import Path

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "cert.pdf",
                        "mime": "application/pdf",
                        "data_base64": base64.b64encode(b"%PDF-1.4 fake").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        folder = Path(resp.json()["capture_folder"])
        entry = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["media"][0]
        assert "provenance" not in entry
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_omitted_gating_is_inferred_from_the_page_not_assumed_public(tmp_path):
    """Regression: the extension's `gating` select defaulted to "public".

    An explicit declaration wins over inference, so a pre-selected "Public" made
    backend gating detection unreachable on the only path that matters — and
    wrote thirteen paywalled captures to disk flagged republishable. The popup
    now sends no `gating` unless the user overrides.
    """
    paywalled = _PAGE.replace("<body>", "<body><a href='/ifhf/logout.php'>Log Out</a>")
    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={"url": "https://rootsireland.ie/ifhf/view_detail.php", "html": paywalled},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["gating"] == "gated"  # inferred, not declared
    finally:
        backend.app.dependency_overrides.clear()


def test_omitted_gating_on_a_public_page_still_infers_public(tmp_path):
    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post("/capture", json={"url": _NYC_URL, "html": _PAGE}, headers=_AUTH)
        assert resp.status_code == 200, resp.text
        assert resp.json()["gating"] == "public"
    finally:
        backend.app.dependency_overrides.clear()


def test_post_capture_stores_media_with_provenance(tmp_path):
    """POSTed media bytes are stored and their origin URL + MIME land in the manifest."""
    import base64
    from pathlib import Path

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        pdf_bytes = b"%PDF-1.4 fake certificate"
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "certificate 4017734.pdf",  # note the space
                        "mime": "application/pdf",
                        "source_url": "https://blob.example/scan.pdf?sig=abc",
                        "data_base64": base64.b64encode(pdf_bytes).decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["media_count"] == 1
        assert body["verify_ok"] is True

        folder = Path(body["capture_folder"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["media"][0]
        # Slug sanitized server-side (space -> dash); ext from the filename.
        assert entry["path"] == "media/0-certificate-4017734.pdf"
        assert entry["source_url"] == "https://blob.example/scan.pdf?sig=abc"
        assert entry["mime"] == "application/pdf"
        assert (folder / entry["path"]).read_bytes() == pdf_bytes
    finally:
        backend.app.dependency_overrides.clear()


def test_post_capture_stores_rendered_pdf_media_contract(tmp_path):
    """R1 contract: the extension's rendered page-PDF is storable as-is.

    Pins the exact media item the extension sends for a `chrome.debugger` ->
    `Page.printToPDF` render: inline base64, no source_url (these bytes came from
    no URL — the tool synthesized them), provenance `render:page-pdf`. If a schema
    change later constrains provenance or demands source_url, this fails here and
    the extension is fixed with it, instead of the render 422-ing in the wild.
    """
    import base64
    from pathlib import Path

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        pdf_bytes = b"%PDF-1.4 rendered page"
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "record.pdf",
                        "mime": "application/pdf",
                        "provenance": "render:page-pdf",
                        "data_base64": base64.b64encode(pdf_bytes).decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["media_count"] == 1
        assert body["verify_ok"] is True

        folder = Path(body["capture_folder"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["media"][0]
        assert entry["path"] == "media/0-record.pdf"
        assert entry["provenance"] == "render:page-pdf"
        assert entry["source_url"] is None
        assert (folder / entry["path"]).read_bytes() == pdf_bytes
        # A render is evidence like any other artifact: hashed, and re-verified.
        assert manifest["hashes"]["files"]["media/0-record.pdf"]
    finally:
        backend.app.dependency_overrides.clear()


def test_post_capture_rejects_oversized_media(tmp_path, monkeypatch):
    """A media item over the per-item byte cap is a 422, not a silent store."""
    import base64

    from capsule.backend import app as backend_mod

    client, backend = _client(tmp_path, json.dumps(_RAW))
    monkeypatch.setattr(backend_mod, "MAX_MEDIA_ITEM_BYTES", 8)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {"filename": "big.bin", "data_base64": base64.b64encode(b"x" * 9).decode()}
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 422, resp.text
        assert "too large" in resp.json()["detail"]
    finally:
        backend.app.dependency_overrides.clear()


def test_favicon_is_204_not_a_redirect_and_422(tmp_path):
    """A browser's automatic /favicon.ico request must not look like a real error.

    With no route of its own it fell through to the `/{capture_id}/{file_path}`
    catch-all — 307 to `/favicon.ico/`, then 422 as the id validator rejected
    `favicon.ico` as a malformed capture id. Two alarming log lines per page view,
    and a 422 that means nothing teaches you to ignore the ones that mean something.

    `follow_redirects=False` so a reintroduced 307 fails here rather than being
    quietly followed to whatever it lands on.
    """
    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.get("/favicon.ico", follow_redirects=False)
        assert resp.status_code == 204, resp.text
        assert not resp.content
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_oversized_render_would_422_whole_capture(tmp_path, monkeypatch):
    """Regression: an oversized media item takes the WHOLE capture down with it.

    `_decode_media` raises before `run_capture` writes anything, so an over-cap item
    does not merely get dropped — the page snapshot, extraction and citation are all
    lost with it. That is precisely why the extension size-checks its rendered PDF
    client-side (extension/render-pdf.js, RENDER_MAX_BYTES) and drops the render
    rather than posting it: a page too big to render must still be captured.

    Pinned here because the guard's whole justification lives in this behavior. If a
    future backend instead skips the bad item and captures the rest, this fails —
    and the client-side guard can be reconsidered.
    """
    import base64

    from capsule.backend import app as backend_mod

    client, backend = _client(tmp_path, json.dumps(_RAW))
    monkeypatch.setattr(backend_mod, "MAX_MEDIA_ITEM_BYTES", 8)
    captures_root = tmp_path / "captures"
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "record.pdf",
                        "mime": "application/pdf",
                        "provenance": "render:page-pdf",
                        "data_base64": base64.b64encode(b"%PDF-1.4 too big").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 422, resp.text
        # The capture did not merely lose its PDF — it never happened at all.
        written = list(captures_root.glob("*")) if captures_root.exists() else []
        assert written == [], f"expected no capture folder, found {written}"
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_active_content_extension_stored_as_bin(tmp_path):
    """Regression: an active-content filename (evil.html) is stored inert as .bin.

    A page controls the filename; a captured `.html`/`.svg`/`.js` sitting in the
    evidence archive is a latent local-script risk. Only known media extensions
    survive; everything else falls back to the inert `bin`.
    """
    import base64
    from pathlib import Path

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "evil.html",
                        "mime": "text/html",
                        "data_base64": base64.b64encode(b"<script>alert(1)</script>").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        folder = Path(resp.json()["capture_folder"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["media"][0]["path"] == "media/0-evil.bin"
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_overlong_extension_falls_back_not_500(tmp_path):
    """Regression: a huge filename extension must not reach (and crash) the writer.

    An unbounded alnum extension passes a naive charset check but blows the
    filesystem name limit at write time -> uncaught OSError -> 500. The
    allow-list caps it: it falls back to the MIME map instead.
    """
    import base64
    from pathlib import Path

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "x." + "a" * 300,
                        "mime": "application/pdf",
                        "data_base64": base64.b64encode(b"%PDF-1.4").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        folder = Path(resp.json()["capture_folder"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["media"][0]["path"] == "media/0-x.pdf"
    finally:
        backend.app.dependency_overrides.clear()


def test_post_capture_rejects_invalid_base64(tmp_path):
    """Undecodable media bytes are rejected at the boundary (422), never stored."""
    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [{"filename": "x.pdf", "data_base64": "not!!valid!!base64"}],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 422, resp.text
    finally:
        backend.app.dependency_overrides.clear()


def test_post_capture_maps_bad_extraction_to_422(tmp_path):
    client, backend = _client(tmp_path, "this is not JSON")
    try:
        resp = client.post("/capture", json={"url": _NYC_URL, "html": _PAGE}, headers=_AUTH)
        assert resp.status_code == 422, resp.text
        # Centralized ValueError handler: bad extraction -> 422 "invalid request".
        assert "invalid request" in resp.json()["detail"]
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_capture_requires_token_rejects_missing(tmp_path):
    """Regression: an unauthenticated POST (any web page's JS) must be refused.

    The backend listens on localhost; without a token, any site the user visits
    could drive captures against it (local CSRF, and it spends the API budget).
    """
    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post("/capture", json={"url": _NYC_URL, "html": _PAGE})  # no header
        assert resp.status_code == 401, resp.text
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_capture_requires_token_rejects_wrong(tmp_path):
    """Regression: a POST with the wrong token must be refused (constant-time compare)."""
    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={"url": _NYC_URL, "html": _PAGE},
            headers={"X-Capsule-Token": "not-the-token"},
        )
        assert resp.status_code == 401, resp.text
    finally:
        backend.app.dependency_overrides.clear()


def test_capture_unconfigured_token_fails_closed(tmp_path):
    """A server with no CAPSULE_TOKEN must fail closed (503), never open."""
    client, backend = _client(tmp_path, json.dumps(_RAW), token=None)
    try:
        resp = client.post("/capture", json={"url": _NYC_URL, "html": _PAGE}, headers=_AUTH)
        assert resp.status_code == 503, resp.text
        assert "CAPSULE_TOKEN" in resp.json()["detail"]
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_auth_runs_before_agent_401_beats_503(tmp_path, monkeypatch):
    """Regression: auth must be checked BEFORE the agent dependency.

    Locks the security-critical ordering: with a bad token AND no API key, the
    REAL require_token + REAL provide_agent must yield 401 (auth first), never
    503 — otherwise an unauthenticated caller learns server config. Neither
    dependency is overridden here so the true resolution order is exercised.
    """
    from fastapi.testclient import TestClient

    from capsule.backend.app import Settings, app, get_settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(
        db_path=tmp_path / "capsule.db", captures_root=tmp_path / "captures", token=_TOKEN
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        resp = TestClient(app).post("/capture", json={"url": _NYC_URL, "html": _PAGE})  # no token
        assert resp.status_code == 401, resp.text
    finally:
        app.dependency_overrides.clear()


def test_cors_allows_extension_origin_not_web_page(tmp_path):
    """CORS must approve an extension origin but reject an arbitrary web page.

    A custom auth header forces a preflight; if a random site's origin is not
    approved, the browser never sends the actual POST — defense before the token.
    A denied origin must get NO Access-Control-Allow-Origin header at all (not a
    mismatched one), so a stray ``allow_origins=["*"]`` regression is caught.
    """
    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        ext_origin = "chrome-extension://" + "a" * 32
        preflight = {
            "Origin": ext_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-capsule-token",
        }
        allowed = client.options("/capture", headers=preflight)
        assert allowed.status_code == 200, allowed.text
        assert allowed.headers.get("access-control-allow-origin") == ext_origin

        preflight["Origin"] = "https://evil.example"
        denied = client.options("/capture", headers=preflight)
        assert denied.status_code == 400, denied.text
        assert "access-control-allow-origin" not in denied.headers
    finally:
        backend.app.dependency_overrides.clear()


def test_post_capture_without_api_key_archives_with_extraction_pending(tmp_path, monkeypatch):
    """v0.2 contract change (was: 503). With no key and the REAL provide_agent, the page is
    archived now and its extraction is marked PENDING, so an evaluator sees Capsule work
    before entering a key, and the capture never reads as a failed reading."""
    from fastapi.testclient import TestClient

    from capsule.backend.app import Settings, app, get_settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(
        db_path=tmp_path / "capsule.db", captures_root=tmp_path / "captures", token=_TOKEN
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        resp = client.post("/capture", json={"url": _NYC_URL, "html": _PAGE}, headers=_AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["extraction_pending"] is True
        assert body["verify_ok"] is True  # not "read and found nothing"
        assert body["assertions"] == []
        from pathlib import Path

        folder = Path(body["capture_folder"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["extraction"] == "pending"
        assert not (folder / "agent_log").exists()  # no model call was made
        # A pending capture does not block capturing the page again once a key exists.
        again = client.post("/capture", json={"url": _NYC_URL, "html": _PAGE}, headers=_AUTH)
        assert again.status_code == 200, again.text
    finally:
        app.dependency_overrides.clear()


def test_BUG_001_blank_page_render_is_reported_but_capture_still_succeeds(tmp_path):
    """Regression (BUG-001): a rendered PDF that shows a human NOTHING must say so.

    An agent's full-viewport overlay gets flattened by Chrome's print pipeline into an
    opaque fill over the text. Magic bytes, size, %%EOF, SHA-256 and text extraction
    all passed on that file, so the capture reported `PDF ✓` while archiving a white
    page — unreadable evidence that nobody re-runs, because it claimed success.

    Two things are asserted, and both matter:
      1. the blank render is REPORTED (media_warnings), and
      2. the capture still SUCCEEDS — the snapshot, extraction and hashes are intact
         and the bytes are archived. The gate tells the truth; it does not destroy
         evidence.
    """
    import base64

    from tests.test_render_check import WHITE_FILL_OVER_ALL

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "record.pdf",
                        "mime": "application/pdf",
                        "provenance": "render:page-pdf",
                        "data_base64": base64.b64encode(WHITE_FILL_OVER_ALL).decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        warnings = body["media_warnings"]
        assert len(warnings) == 1
        assert "BLANK" in warnings[0]
        assert "record.pdf" in warnings[0]

        # The capture is NOT destroyed by a bad render: it still lands, verified.
        assert body["verify_ok"] is True
        assert body["media_count"] == 1
        assert body["assertions"]
    finally:
        backend.app.dependency_overrides.clear()


def test_a_readable_render_raises_no_warning(tmp_path):
    """The gate must not cry wolf: a render with ink on it passes silently."""
    import base64

    from tests.test_render_check import INKED

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "record.pdf",
                        "mime": "application/pdf",
                        "provenance": "render:page-pdf",
                        "data_base64": base64.b64encode(INKED).decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["media_warnings"] == []
    finally:
        backend.app.dependency_overrides.clear()


def test_a_blank_page_scan_from_the_page_is_not_flagged(tmp_path):
    """Only RENDERS are gated. A scan the page served is evidence as-is.

    A parish register's mostly-blank page is legitimately near-white. Flagging it
    would cry wolf on real evidence, so the check keys on provenance, not on MIME.
    """
    import base64

    from tests.test_render_check import BLANK

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "filename": "register-scan.pdf",
                        "mime": "application/pdf",
                        "source_url": "https://example.org/scan.pdf",
                        "data_base64": base64.b64encode(BLANK).decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["media_warnings"] == []
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_002_media_payload_rejects_render_local_fields(tmp_path):
    """Regression (BUG-002): the extension must not post render-local metadata.

    `renderPagePdf()` grew a `hid` field (what it hid to get a clean print — BUG-001),
    and background.js spread the WHOLE render object into media[]. MediaIn is
    `extra="forbid"`, so every capture 422'd: `body.media.0.hid: Extra inputs are not
    permitted`. 100% of captures broken by a field that has no business on the wire.

    `extra="forbid"` is correct — the backend is the trust boundary and must not
    silently swallow unknown keys. This test pins that, so the contract stays strict
    and the extension is the side that keeps its payload clean.
    """
    import base64

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "data_base64": base64.b64encode(b"%PDF-1.4 x").decode(),
                        "filename": "record.pdf",
                        "mime": "application/pdf",
                        "provenance": "render:page-pdf",
                        "hid": ["#capsule-toast"],  # what the extension actually sent
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 422, resp.text
        assert "hid" in resp.text
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_002_the_exact_payload_the_extension_now_sends_is_accepted(tmp_path):
    """The other half: pin the payload shape, not just the rejection.

    The rejection test above documents the contract but would still pass if the
    extension were sending garbage. This one asserts that the FOUR keys renderPagePdf
    now returns in `media` — and only those — are exactly what the backend accepts.
    Keep the two in lockstep: if a key is added to `render.media`, this fails.
    """
    import base64

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _NYC_URL,
                "html": _PAGE,
                "media": [
                    {
                        "data_base64": base64.b64encode(b"%PDF-1.4 x").decode(),
                        "filename": "record.pdf",
                        "mime": "application/pdf",
                        "provenance": "render:page-pdf",
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["media_count"] == 1
    finally:
        backend.app.dependency_overrides.clear()
