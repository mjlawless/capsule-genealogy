"""REQ-007 §2 — the site's own export, on any site, not just Ancestry.

`download-intercept.js` already knew how to arm a window, correlate the file that lands,
refuse anything it did not arm for, and ingest the bytes. All of it was hard-wired to one
host at `const ANCESTRY_HOST = /(^|\\.)ancestry(cdn)?\\.[a-z.]+$/i` — so on Archion, where
the register is a tiled canvas and the site's own export is the ONLY full-resolution
route, the whole mechanism was unreachable and the capture fell back to a page render.

Two halves are tested here, because they fail in different ways:

  * the CORRELATION rule, in `node` — a pure function, and the one whose failure mode is
    silent: claim the wrong download and a scan of record A is hashed, cited and filed
    under record B. It is checked against over-matching (`evil-archion.de`) as hard as
    against under-matching.
  * the WIRE contract, through the backend — a `site:official-download` must be treated as
    a record image: classified, pixel-gated, and counted toward §3.3's floor. Left out of
    `_RECORD_IMAGE_PROVENANCE` it would be measured and then judged by nothing, which is
    the same silence §3.1 was filed to end.

What is NOT tested here, and cannot be: the live run. Driving Archion's export control
needs a subscriber session behind a login. That E2E is the owner's, as REQ-002's was.
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

_DT = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}
_INTERCEPT = Path(__file__).resolve().parent.parent / "extension" / "download-intercept.js"

_ARCHION_URL = "https://www.archion.de/de/viewer/churchRegister/239254?cHash=0ba8f"
_ARCHION_PAGE = (
    "<html><head><title>Taufregister 1798-1824 | Guntersblum</title></head>"
    "<body><div id='viewer'><canvas></canvas></div></body></html>"
)

_RAW = {
    "record_type": "image",
    "source_quality": "original",
    "personas": [],
    "assertions": [],
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
    return TestClient(app), backend, settings


def _pdf_of(width: int, height: int) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format="PDF")
    return base64.b64encode(buffer.getvalue()).decode()


def _jpeg(width: int, height: int) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


# -- the correlation rule ----------------------------------------------------

#: (download host, armed page host, profile, must be accepted)
_CORRELATION = [
    # The ordinary case: a site serving its own export from its own domain.
    ("www.archion.de", "www.archion.de", None, True),
    ("archion.de", "www.archion.de", None, True),
    ("download.archion.de", "archion.de", None, True),
    # Ancestry serves exports from a SIBLING domain, which is why the profile exists.
    ("www.ancestry.com", "www.ancestry.com", "ancestry", True),
    ("mediasvc.ancestrycdn.com", "www.ancestry.com", "ancestry", True),
    # ...and without the profile that sibling is correctly a stranger.
    ("mediasvc.ancestrycdn.com", "www.ancestry.com", None, False),
    # Over-matching is the dangerous direction: these must all be refused.
    ("evil-archion.de", "archion.de", None, False),
    ("archion.de.evil.com", "archion.de", None, False),
    ("notarchion.de", "archion.de", None, False),
    ("example.com", "www.archion.de", None, False),
    # No host on either side is never a pass.
    ("", "www.archion.de", None, False),
    ("www.archion.de", "", None, False),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_REQ_007_the_download_correlation_rule():
    """Which downloads may be claimed for an armed record — checked in the real engine.

    Run through `node` against the shipped file rather than reimplemented in Python: a
    Python restatement of this table would pass happily while the JS that actually runs in
    Chrome said something else, which is worse than no test at all.
    """
    script = """
      const {hostBelongsToArmedSite} = require(process.argv[1]);
      const cases = JSON.parse(process.argv[2]);
      const failures = cases.filter(([h, a, p, want]) =>
        hostBelongsToArmedSite(h, a, p === null ? undefined : p) !== want);
      console.log(JSON.stringify(failures));
    """
    proc = subprocess.run(
        ["node", "-e", script, str(_INTERCEPT), json.dumps(_CORRELATION)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    failures = json.loads(proc.stdout.strip())
    assert failures == [], f"correlation rule disagrees on: {failures}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_REQ_007_every_extension_script_still_parses():
    """A syntax error in the service worker is a silently dead extension, not a crash."""
    for name in (
        "background.js",
        "content.js",
        "download-intercept.js",
        "media-detect.js",
        "popup.js",
        "render-pdf.js",
    ):
        path = _INTERCEPT.parent / name
        proc = subprocess.run(
            ["node", "--check", str(path)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, f"{name} does not parse:\n{proc.stderr}"


def test_REQ_007_the_arm_host_is_never_taken_from_the_page_event():
    """The correlation host comes from `location.hostname`, never from `e.detail`.

    This listener fires on an event any page JS can dispatch. A page that could name its
    own allow-list could arm Capsule to swallow the next download from anywhere — so the
    one field the page must not control is checked here, in the source, because there is
    no runtime harness that would notice it drifting.
    """
    content = (_INTERCEPT.parent / "content.js").read_text(encoding="utf-8")
    assert "host: location.hostname," in content
    assert "host: detail." not in content


# -- the wire contract -------------------------------------------------------


def test_REQ_007_an_official_download_is_treated_as_a_record_image(tmp_path):
    """The §2 acceptance: a native 2704x2171 export reports `full` and verifies.

    `site:official-download` has to sit in `_RECORD_IMAGE_PROVENANCE` for this. It is the
    provider's own export — the thing the capture claims IS the record — so it is
    classified and gated exactly as an `ancestry:download` is.
    """
    client, backend, _ = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "Bildausschnitt.pdf",
                        "mime": "application/pdf",
                        "provenance": "site:official-download",
                        "data_base64": _pdf_of(2704, 2171),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "full"
        assert body["media_warnings"] == []
        assert body["verify_ok"] is True
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_zoomed_export_passes_the_old_floor_and_is_caught_by_the_new_one(tmp_path):
    """The two layers, and why BOTH are needed. This is a real 2026-07-24 observation.

    Archion's export control exports the current VIEWPORT, so exporting while zoomed in
    produced **703x896** — genuinely the site's own download, and genuinely not the
    register page.

    `image_status` says `full`, and that is CORRECT under BUG-006: 896 clears
    `MIN_USABLE_PX` (800), and a provider download is the full resolution whatever its
    pixel count — a WWII draft card really is 1815px. No pixel test at that threshold can
    tell a small document scanned completely from a big one exported at the wrong zoom.

    What catches it is §3.3, and only because it asks a narrower question of a narrower
    class: this capture is an ORIGINAL that yielded no text, so the image is the entire
    payload, and 896 is under the 1500px floor. `verify_ok: false`.

    Pinned as a pair deliberately. Someone reading `image_status: full` here will be
    tempted to "fix" it by raising `MIN_USABLE_PX` — which is precisely the BUG-006
    regression, and would false-FAIL the draft card again.
    """
    client, backend, _ = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "zoomed.jpg",
                        "mime": "image/jpeg",
                        "provenance": "site:official-download",
                        "data_base64": _jpeg(703, 896),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Layer 1 is satisfied, and rightly so — see the docstring.
        assert body["image_status"] == "full"
        assert body["media_warnings"] == []
        # Layer 2 is not: it archives no readable register page.
        assert body["verify_ok"] is False
    finally:
        backend.app.dependency_overrides.clear()


# -- findings from the 2026-08-22 external review ----------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_REQ_007_a_referrer_cannot_override_a_mismatching_download_host():
    """The referrer is a FALLBACK for hostless URLs, never an override. [review P1]

    It used to be `[url, finalUrl, referrer].some(...)`, so during the armed window any
    image or PDF started from the armed page was claimed whatever host served it. Under the
    old Ancestry-only regex that was nearly harmless; generalized to every site it is not,
    because `cleanUpDownload` then DELETES the file from the researcher's Downloads folder.
    Claiming a download we did not arm for is bad; erasing it is the part that cannot be
    undone.

    The fallback has to stay for `blob:`/`data:`, where there is no host at all — which is
    exactly how a viewer that builds its export client-side hands it over.
    """
    script = """
      const {fromArmedSite} = require(process.argv[1]);
      const cases = JSON.parse(process.argv[2]);
      const failures = cases.filter(([item, armed, want]) =>
        fromArmedSite(item, armed) !== want);
      console.log(JSON.stringify(failures));
    """
    armed = {"host": "www.archion.de"}
    cases = [
        [{"url": "https://www.archion.de/export.pdf"}, armed, True],
        # The finding: a stranger host must NOT be rescued by a matching referrer.
        [
            {"url": "https://cdn.evil.com/x.pdf", "referrer": "https://www.archion.de/v"},
            armed,
            False,
        ],
        # ...but a hostless blob: has nothing else to go on, so the referrer decides.
        [
            {
                "url": "blob:https://www.archion.de/uuid",
                "referrer": "https://www.archion.de/v",
            },
            armed,
            True,
        ],
        [
            {"url": "blob:https://www.archion.de/uuid", "referrer": "https://evil.com/x"},
            armed,
            False,
        ],
        [
            {"url": "https://evil.com/x.pdf", "finalUrl": "https://www.archion.de/e.pdf"},
            armed,
            True,
        ],
        [{"url": "", "referrer": ""}, armed, False],
    ]
    proc = subprocess.run(
        ["node", "-e", script, str(_INTERCEPT), json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == [], proc.stdout


def test_REQ_007_a_degraded_canvas_never_satisfies_the_floor(tmp_path):
    """A big viewer canvas must not verify an image-only original. [review P1]

    It was allowed through on the reasoning that a large canvas is at least a real image of
    the record. That was wrong, and every other gate already said so: `_COMPLETE_IMAGE` and
    `_TERMINAL_GOOD` both exclude `degraded-canvas`, and the warning tells the operator in
    as many words that the record is NOT mined. `verify_ok: true` over the top of that is a
    contradiction — and on a high-DPI display a viewer canvas clears 1500px routinely, so
    the exemption quietly rebuilt the outcome this gate exists to prevent.
    """
    client, backend, _ = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "record-canvas.jpg",
                        "mime": "image/jpeg",
                        "provenance": "viewer:canvas-fallback",
                        "data_base64": _jpeg(2400, 1800),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "degraded-canvas"
        assert body["verify_ok"] is False
        assert body["verify_shortfall"], body
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_provider_download_implies_a_gated_source(tmp_path):
    """Undeclared gating + a subscriber export => GATED, not public. [review P1]

    `detect_gating` looks for ENGLISH authenticated-session markers ("log out", "my
    subscription") and its own docstring records the limit: a page showing none of them
    auto-detects PUBLIC. Archion says "Abmelden". So the entire tile-viewer class §2 just
    opened up is the class that inference is worst at — and a PUBLIC verdict clears
    `copyright_flag` and makes the capture eligible for Wayback, against the CLAUDE.md
    hard rule.

    Nobody reaches a subscriber-only export control without a subscriber session, so the
    staged download is itself the evidence the inference cannot see.
    """
    client, backend, _ = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                # No `gating` declared, and a German page with no English auth marker.
                "url": _ARCHION_URL,
                "html": "<html><body>Angemeldet als Forscher. "
                "<a href='/logout'>Abmelden</a></body></html>",
                "media": [
                    {
                        "filename": "export.pdf",
                        "mime": "application/pdf",
                        "provenance": "site:official-download",
                        "data_base64": _pdf_of(2704, 2171),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["gating"] == "gated"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_declared_gating_still_wins_over_the_inference(tmp_path):
    """The floor never overrides the operator: they are the one looking at the page.

    plan §11, "the user drives". Inferring GATED from a download is a floor for the
    UNDECLARED case, not a new authority.
    """
    client, backend, _ = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": "https://registers.nli.ie/registers/1234",
                "html": _ARCHION_PAGE,
                "gating": "public",
                "media": [
                    {
                        "filename": "leaf.jpg",
                        "mime": "image/jpeg",
                        "provenance": "site:official-download",
                        "data_base64": _jpeg(2704, 2171),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["gating"] == "public"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_canvas_grab_alone_does_not_imply_gating():
    """Anyone can screenshot a public viewer. Only an EXPORT proves a subscriber session."""
    from capsule.backend.app import _is_provider_download
    from capsule.capture.manifest import MediaItem

    def item(provenance):
        return MediaItem(slug="x", ext="jpg", data=b"", provenance=provenance)

    assert _is_provider_download(item("site:official-download")) is True
    assert _is_provider_download(item("ancestry:download")) is True
    assert _is_provider_download(item("viewer:canvas-fallback")) is False
    assert _is_provider_download(item("render:page-pdf")) is False
    assert _is_provider_download(item(None)) is False


def test_REQ_007_the_shortfall_reason_reaches_the_operator(tmp_path):
    """`verify_ok: false` beside `IMAGE full` is contradictory without it. [review P2]

    Checked in the API response AND in `result.json`, because the driving agent reads the
    sidecar rather than the HTTP body.
    """
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "zoomed.jpg",
                        "mime": "image/jpeg",
                        "provenance": "site:official-download",
                        "data_base64": _jpeg(703, 896),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "full" and body["verify_ok"] is False
        assert len(body["verify_shortfall"]) == 1
        assert "896px" in body["verify_shortfall"][0]
        result = json.loads(
            (settings.captures_root / body["capture_folder"] / "result.json").read_text(
                encoding="utf-8"
            )
        )
        assert result["verify_shortfall"] == body["verify_shortfall"]
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_passing_capture_omits_the_shortfall_key_entirely(tmp_path):
    """Additive: a healthy `result.json` stays byte-identical to before the field existed."""
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "export.pdf",
                        "mime": "application/pdf",
                        "provenance": "site:official-download",
                        "data_base64": _pdf_of(2704, 2171),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verify_ok"] is True and body["verify_shortfall"] == []
        result = json.loads(
            (settings.captures_root / body["capture_folder"] / "result.json").read_text(
                encoding="utf-8"
            )
        )
        assert "verify_shortfall" not in result
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_the_toast_shows_the_shortfall():
    """The operator sees WHY, not merely that verify failed.

    Both halves, because only checking the first passed vacuously for a whole commit: the
    toast reads `res.verify_shortfall`, but `res` is the CURATED object `postAndShape`
    builds — and it was not forwarding the field, so the value never arrived.
    """
    content = (_INTERCEPT.parent / "content.js").read_text(encoding="utf-8")
    assert "res.verify_shortfall" in content
    assert "THIS CAPTURE DOES NOT HOLD THE RECORD" in content
    background = (_INTERCEPT.parent / "background.js").read_text(encoding="utf-8")
    assert "verify_shortfall: Array.isArray(body.verify_shortfall)" in background
