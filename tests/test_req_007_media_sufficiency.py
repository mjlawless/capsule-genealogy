"""REQ-007 §3.3 — `verify_ok` must stop passing on a capture that archives no record.

Two Archion captures, 25 days apart, reported `verify_ok: true` while holding a browser
print of site chrome plus a 689x545 viewer preview, naming no page of the register, with
`assertion_count: 0`. Every hash in both was correct. They were the hashes of the wrong
payload — `CLAUDE.md` §12.4, a checksum that verifies against garbage.

`verify_capture` could not have caught it: it asks *are these the bytes we recorded?*, and
they were. So it now also asks *is the payload the record?* — but only of the one class
where a pixel count is a safe thing to fail on:

    no assertions AND no provider claims
    AND (no coverage claims  OR  the source is a known tile viewer)

i.e. no record text could be read as evidence, so the image is the entire payload.

The class originally also required `source_quality == "original"`. It no longer does: the
live Archion run on 2026-08-22 showed that label moving between two captures of the SAME
register, so a gate scoped on it was absent exactly when the model happened to say
"derivative". See `sufficiency` for the full argument, including why coverage claims exempt
a capture everywhere EXCEPT on a tile-viewer host.

Everything else is out of class, and the tests below spend as much effort pinning what must
still PASS as what must now fail — because BUG-006 was a false-FAIL that jammed a finished
record on the queue, and re-creating it a size at a time would be worse than the bug this
fixes.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone

_DT = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

_ARCHION_URL = "https://www.archion.de/de/viewer/churchRegister/239254?cHash=0ba8f"
_ARCHION_PAGE = (
    "<html><head><title>Taufregister 1798-1824 | Guntersblum</title></head>"
    "<body><div id='viewer'><canvas></canvas></div></body></html>"
)

#: What the model says about a register scan it cannot read: this page IS the record, and
#: there is no text on it to extract. The exact shape of both Archion captures on disk.
_IMAGE_ONLY_ORIGINAL = {
    "record_type": "image",
    "source_quality": "original",
    "source_quality_rationale": "a scan of the register page itself",
    "personas": [],
    "assertions": [],
    "provider_claims": [],
}

#: An index page: text IS the evidence, and it legitimately holds no image at all.
_DERIVATIVE_WITH_TEXT = {
    "record_type": "marriage",
    "source_quality": "derivative",
    "personas": [{"id": "p1", "label": "Pat Doyle", "role": "subject"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "marriage_date",
            "value": "7 Jan 1845",
            "evidence_type": "stated",
            "snippet": "7 Jan 1845",
        }
    ],
    "provider_claims": [],
}

#: An ORIGINAL that also yielded text — a certificate whose fields the DOM exposes. It has
#: a text layer, so a small image is a missing enhancement, never an empty capture.
_ORIGINAL_WITH_TEXT = {**_DERIVATIVE_WITH_TEXT, "source_quality": "original"}


def _client(tmp_path, raw: dict):
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
        transport=FakeTransport(json.dumps(raw)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    return TestClient(app), backend, settings


def _jpeg(width: int, height: int) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


def _pdf_of(width: int, height: int) -> str:
    """A PDF wrapping one raster of the given PIXEL size — the shape of a site export."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format="PDF")
    return base64.b64encode(buffer.getvalue()).decode()


def _capture(client, media=None):
    return client.post(
        "/capture",
        json={
            "url": _ARCHION_URL,
            "html": _ARCHION_PAGE,
            "gating": "gated",
            **({"media": media} if media else {}),
        },
        headers=_AUTH,
    )


# -- the failure this REQ exists to stop -------------------------------------


def test_REQ_007_the_2026_08_19_archion_shape_can_no_longer_report_success(tmp_path):
    """THE acceptance criterion: a page render + a viewer preview is not a capture.

    Exactly what is on disk — `render:page-pdf` of site chrome, plus the fit-to-window
    689x545 thumbnail the in-page sweep banked. Hashes fine, holds nothing.
    """
    client, backend, _ = _client(tmp_path, _IMAGE_ONLY_ORIGINAL)
    try:
        resp = _capture(
            client,
            media=[
                {
                    "filename": "thumbnail.jpg",
                    "mime": "image/jpeg",
                    "data_base64": _jpeg(689, 545),
                },
                {
                    "filename": "record.pdf",
                    "mime": "application/pdf",
                    "provenance": "render:page-pdf",
                    "data_base64": _pdf_of(1024, 768),
                },
            ],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verify_ok"] is False
        assert body["image_status"] == "none"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_an_image_only_original_holding_nothing_at_all_fails(tmp_path):
    """No media whatsoever. The emptiest possible capture must be the loudest failure."""
    client, backend, settings = _client(tmp_path, _IMAGE_ONLY_ORIGINAL)
    try:
        resp = _capture(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["verify_ok"] is False
        folder = settings.captures_root / resp.json()["capture_folder"]
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        assert result["verify_ok"] is False
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_the_reason_names_what_was_actually_held(tmp_path):
    """A verdict that cannot tell "no image" from "a 689px preview" gets read as noise."""
    from capsule.capture.manifest import verify_capture

    client, backend, settings = _client(tmp_path, _IMAGE_ONLY_ORIGINAL)
    try:
        resp = _capture(
            client,
            media=[
                {
                    "filename": "thumbnail.jpg",
                    "mime": "image/jpeg",
                    "data_base64": _jpeg(689, 545),
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        result = verify_capture(settings.captures_root / resp.json()["capture_folder"])
        assert result.ok is False
        assert len(result.insufficient) == 1
        reason = result.insufficient[0]
        assert "689px" in reason and "1500px" in reason
        # Integrity is untouched: the bytes ARE what we recorded. Only sufficiency failed.
        assert result.missing == () and result.mismatched == () and result.extra == ()
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_page_render_never_satisfies_the_floor_however_big(tmp_path):
    """Provenance beats pixel count: a browser print of the page is not the register.

    A 2704px raster inside a `render:page-pdf` is a print of whatever was on screen — the
    same artifact §2 exists to replace. Accepting it would let the exact failure back in
    through the size test.
    """
    client, backend, _ = _client(tmp_path, _IMAGE_ONLY_ORIGINAL)
    try:
        resp = _capture(
            client,
            media=[
                {
                    "filename": "record.pdf",
                    "mime": "application/pdf",
                    "provenance": "render:page-pdf",
                    "data_base64": _pdf_of(2704, 2171),
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["verify_ok"] is False
    finally:
        backend.app.dependency_overrides.clear()


# -- and what must still pass ------------------------------------------------


def test_REQ_007_the_official_export_satisfies_the_floor(tmp_path):
    """The §2 Archion export — a native 2704x2171 PDF — is the record, and verifies."""
    client, backend, _ = _client(tmp_path, _IMAGE_ONLY_ORIGINAL)
    try:
        resp = _capture(
            client,
            media=[
                {
                    "filename": "export.pdf",
                    "mime": "application/pdf",
                    "provenance": "site:official-download",
                    "data_base64": _pdf_of(2704, 2171),
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["verify_ok"] is True
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_full_resolution_raster_satisfies_the_floor(tmp_path):
    """Not only PDFs: a downloaded JPEG of the leaf is the record just as much."""
    client, backend, _ = _client(tmp_path, _IMAGE_ONLY_ORIGINAL)
    try:
        resp = _capture(
            client,
            media=[
                {
                    "filename": "leaf.jpg",
                    "mime": "image/jpeg",
                    "provenance": "viewer:bookreader-iip leaf=174",
                    "data_base64": _jpeg(2704, 2171),
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["verify_ok"] is True
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_derivative_with_no_image_still_verifies(tmp_path):
    """The corpus guard. RootsIreland, IrishGenealogy, FamilySearch: text IS the evidence.

    Most of the collection holds no image and never should. If the floor reached these,
    it would fail nearly everything and switch REQ-004 dedup off with it.
    """
    client, backend, _ = _client(tmp_path, _DERIVATIVE_WITH_TEXT)
    try:
        resp = _capture(client)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verify_ok"] is True
        assert body["image_status"] == "none"  # reported, and not a fault
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_an_original_that_yielded_text_is_out_of_class(tmp_path):
    """BUG-006 non-regression: an original WITH a text layer is not failed on pixels.

    The NYC certificate shape — the DOM exposes the index panel, so there are assertions.
    A small or missing image there is a missing enhancement (BUG-007's own subject), which
    `image_status` reports; it is not an empty capture, and must not fail verification.
    """
    client, backend, _ = _client(tmp_path, _ORIGINAL_WITH_TEXT)
    try:
        resp = _capture(
            client,
            media=[
                {
                    "filename": "cert.jpg",
                    "mime": "image/jpeg",
                    "data_base64": _jpeg(977, 1082),
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["verify_ok"] is True
    finally:
        backend.app.dependency_overrides.clear()


# -- the measuring machinery -------------------------------------------------


def test_REQ_007_a_pdf_is_measured_by_its_raster_not_its_page_geometry():
    """973x781 POINTS carrying a 2704px scan is a full-resolution file, and vice versa."""
    from capsule.pipeline.sufficiency import longest_raster_edge

    big = base64.b64decode(_pdf_of(2704, 2171))
    small = base64.b64decode(_pdf_of(689, 545))
    assert longest_raster_edge(big, mime="application/pdf") == 2704
    assert longest_raster_edge(small, mime="application/pdf") == 689
    # By extension, not only by MIME — a file the page declared nothing for.
    assert longest_raster_edge(big, ext="pdf") == 2704


def test_REQ_007_unmeasurable_bytes_never_satisfy_the_floor():
    """"Cannot judge" is never "is fine" — the BUG-001 lesson, applied to the floor."""
    from capsule.pipeline.sufficiency import longest_raster_edge, record_media_shortfall

    assert longest_raster_edge(b"not a pdf at all", mime="application/pdf") is None
    assert longest_raster_edge(b"", ext="jpg") is None
    shortfall = record_media_shortfall(
        assertion_count=0,
        media=[("media/0-mystery.bin", "site:official-download", None)],
    )
    assert shortfall is not None
    assert "no measurable image" in shortfall


def test_REQ_007_an_archive_only_document_capture_is_exempt(tmp_path):
    """A document capture is out of class, and not as a convenience.

    The gate reasons "the extraction produced nothing, therefore the image is the whole
    payload". But `run_document_capture` makes no model call at all, so "produced nothing"
    is trivially true of EVERY document capture and carries no information. Applying it
    would fail a text PDF — a transcript, a will, a typed certificate — for holding no big
    raster, when its evidence is exactly the text.

    Known limit, to revisit with REQ-006: a document capture holding a 689px scan is
    therefore not caught. Once vision/HTR actually extracts from these they stop being
    archive-only and the premise becomes true again.
    """
    import json as _json

    from capsule.capture.manifest import verify_capture

    client, backend, settings = _client(tmp_path, _IMAGE_ONLY_ORIGINAL)
    try:
        resp = _capture(
            client,
            media=[
                {
                    "filename": "export.pdf",
                    "mime": "application/pdf",
                    "provenance": "site:official-download",
                    "data_base64": _pdf_of(2704, 2171),
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        folder = settings.captures_root / resp.json()["capture_folder"]
        assert verify_capture(folder).ok is True
    finally:
        backend.app.dependency_overrides.clear()

    # Re-shape the manifest into the document form: the one full-resolution file is the
    # PRIMARY artifact and `media[]` is empty. The hashes still describe the same bytes.
    manifest_path = folder / "manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    media_path = manifest["media"][0]["path"]
    manifest["media"] = []
    manifest["archive"]["kind"] = "document"
    manifest["archive"]["media_type"] = "application/pdf"
    manifest["archive"]["local_snapshot"] = media_path
    manifest["archive"]["sha256"] = manifest["hashes"]["files"][media_path]
    # `media[]` no longer claims the file, so drop the entry that would fail integrity.
    del manifest["hashes"]["files"]["snapshot.html"]
    (folder / "snapshot.html").unlink()
    manifest_path.write_text(_json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_capture(folder)
    assert result.insufficient == (), result.insufficient


# -- the widened class (2026-08-22, after the live Archion run) ---------------


def test_REQ_007_the_class_no_longer_depends_on_source_quality():
    """The gate must bite whatever the extractor called the source.

    The live run recorded `derivative` for the same Archion register that an earlier
    capture recorded `original`. A safety gate scoped on that label is absent precisely
    when the label drifts the wrong way.
    """
    from capsule.pipeline.sufficiency import record_media_shortfall

    for _ in ("original", "derivative"):
        assert record_media_shortfall(
            assertion_count=0, media=[("m/0.jpg", "site:official-download", 689)]
        ) is not None


def test_REQ_007_coverage_claims_are_text_evidence_except_on_a_tile_viewer():
    """The RootsIreland holdings page must never fail; Archion must never escape.

    A holdings table yields 140 coverage claims, no assertions and no image, and is a
    perfectly good capture — the one that exposed a false premise standing 12 sessions.
    But an Archion register page also yields a handful, parsed out of the volume title,
    while holding no register at all. Measured on the corpus: letting coverage exempt
    outright let BOTH captures this REQ was filed about walk past the gate.
    """
    from capsule.pipeline.sufficiency import record_media_shortfall

    holdings = record_media_shortfall(
        assertion_count=0,
        coverage_count=140,
        url="https://www.rootsireland.ie/coverage/",
        media=[("media/0-record.pdf", "render:page-pdf", 900)],
    )
    assert holdings is None, "a holdings page carries its evidence as coverage claims"

    register = record_media_shortfall(
        assertion_count=0,
        coverage_count=4,
        url="https://www.archion.de/en/viewer/churchRegister/239254",
        media=[("media/0-thumbnail.jpg", None, 689)],
    )
    assert register is not None, "coverage parsed off a volume title is not the record"


def test_REQ_007_the_tile_viewer_host_test_never_over_matches():
    """A host set is a claim about a SITE. It must not spill onto lookalike domains."""
    from capsule.pipeline.sufficiency import is_tile_viewer_source

    assert is_tile_viewer_source("https://www.archion.de/en/viewer/x") is True
    assert is_tile_viewer_source("https://archion.de/x") is True
    assert is_tile_viewer_source("https://registers.nli.ie/registers/1") is True
    assert is_tile_viewer_source("https://evil-archion.de/x") is False
    assert is_tile_viewer_source("https://archion.de.evil.com/x") is False
    assert is_tile_viewer_source("https://www.rootsireland.ie/x") is False
    assert is_tile_viewer_source(None) is False


def test_REQ_007_provider_claims_always_count_as_text_evidence():
    """Unlike coverage, the provider naming the source is text evidence on any host."""
    from capsule.pipeline.sufficiency import record_media_shortfall

    assert record_media_shortfall(
        assertion_count=0,
        provider_claim_count=2,
        url="https://www.archion.de/en/viewer/churchRegister/239254",
        media=[("media/0-thumbnail.jpg", None, 689)],
    ) is None
