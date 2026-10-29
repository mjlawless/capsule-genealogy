"""BUG-027 — a page that IS the record image must report it, and a print-only capture must say why.

An archive's IIIF image opened directly in the tab (`…/full/max/0/default.jpg`) has no record
text and no viewer: the page is one `<img>`. With site access the extension banked the
4190x2404 scan as `page:embedded` and verify passed, but `image_status` said `none`, because
only download/canvas provenances were graded. Without access it banked only the page print, and
the §3.3 shortfall said "no measurable image at all" over a 4174px PDF, while the cause (no host
access) sat in `media_warnings`, which `result.json` does not carry.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone

_DT = datetime(2026, 10, 26, 19, 4, 58, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

_IMAGE_URL = "https://archives.example.org/iiif/pool1/reg/0806/FOND.TIF/full/max/0/default.jpg"
_IMAGE_PAGE = (
    '<html><head><title>default.jpg (4190×2404)</title></head>'
    f'<body><img src="{_IMAGE_URL}"></body></html>'
)
# What the reader returns for a page with no record text.
_RAW = {"record_type": "unknown", "personas": [], "assertions": []}

_NO_ACCESS = (
    "default.jpg — Capsule has no access to https://archives.example.org. Open the Capsule "
    "popup on this page and capture once to grant it, then re-run."
)


def _client(tmp_path):
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
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
    return TestClient(app), app


def _encoded(width: int, height: int, fmt: str) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format=fmt)
    return base64.b64encode(buffer.getvalue()).decode()


def _text_pdf() -> str:
    """A PDF with no measurable raster — a typed document opened in the tab. Graded as a
    record image, `classify_image` would call it FAILED, which is what the test guards."""
    return base64.b64encode(b"%PDF-1.4 typed certificate, no images").decode()


def _embedded(data: str, *, source_url: str, mime: str = "image/jpeg", name: str = "default.jpg"):
    return {
        "data_base64": data,
        "filename": name,
        "mime": mime,
        "source_url": source_url,
        "provenance": "page:embedded",
    }


def _print(width: int, height: int):
    return {
        "data_base64": _encoded(width, height, "PDF"),
        "filename": "record.pdf",
        "mime": "application/pdf",
        "provenance": "render:page-pdf",
    }


def _capture(client, *, url=_IMAGE_URL, html=_IMAGE_PAGE, media=(), missed=()):
    resp = client.post(
        "/capture",
        json={"url": url, "html": html, "media": list(media), "missed_media": list(missed)},
        headers=_AUTH,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_BUG_027_the_page_image_is_graded_as_the_record(tmp_path):
    """The reported case: a 4190x2404 direct image passes verify AND reports `full`."""
    client, app = _client(tmp_path)
    try:
        body = _capture(
            client,
            media=[_print(4174, 2288), _embedded(_encoded(4190, 2404, "JPEG"), source_url=_IMAGE_URL)],
        )
        assert body["verify_ok"] is True
        assert body["image_status"] == "full"
    finally:
        app.dependency_overrides.clear()


def test_BUG_027_a_url_fragment_does_not_defeat_the_match(tmp_path):
    client, app = _client(tmp_path)
    try:
        body = _capture(
            client,
            url=_IMAGE_URL + "#zoom",
            media=[_embedded(_encoded(4190, 2404, "JPEG"), source_url=_IMAGE_URL)],
        )
        assert body["image_status"] == "full"
    finally:
        app.dependency_overrides.clear()


def test_BUG_027_a_small_direct_image_fails(tmp_path):
    """Under the 800px usable floor, the page image is graded — and graded as a failure."""
    client, app = _client(tmp_path)
    try:
        body = _capture(client, media=[_embedded(_encoded(600, 344, "JPEG"), source_url=_IMAGE_URL)])
        assert body["image_status"] == "failed"
        assert body["verify_ok"] is False
    finally:
        app.dependency_overrides.clear()


def test_BUG_027_a_small_direct_image_is_not_sent_to_the_ancestry_flow(tmp_path):
    """A real capture always carries the extension's "no download was armed" reason. On an
    Ancestry record, `failed` + not armed means "use the one-call image flow"; on an
    archive's own site that remedy is wrong, and the thumbnail warning already says enough."""
    client, app = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _IMAGE_URL,
                "html": _IMAGE_PAGE,
                "media": [_embedded(_encoded(600, 344, "JPEG"), source_url=_IMAGE_URL)],
                "image_skipped": "no download was armed for this capture",
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "failed"
        assert not any("Ancestry" in w for w in body["media_warnings"])
        assert any("THUMBNAIL" in w for w in body["media_warnings"])
    finally:
        app.dependency_overrides.clear()


def test_BUG_027_a_sized_iiif_request_reads_full_but_fails_the_record_floor(tmp_path):
    """Decided, not accidental: a 1000px `…/full/1000,/0/default.jpg` is a complete image (it
    clears the 800px usable floor, as a provider download would), but an image-only capture
    needs 1500px to count as archiving the record. Both are reported, and `verify_shortfall`
    says which one to act on — the same split the zoomed Archion export already shows."""
    client, app = _client(tmp_path)
    try:
        body = _capture(client, media=[_embedded(_encoded(1000, 574, "JPEG"), source_url=_IMAGE_URL)])
        assert body["image_status"] == "full"
        assert body["verify_ok"] is False
        assert any("1500px floor" in s for s in body["verify_shortfall"])
    finally:
        app.dependency_overrides.clear()


def test_BUG_027_a_thumbnail_on_an_ordinary_page_stays_ungraded(tmp_path):
    """The rule in `_check_record_images`: a page-served thumbnail is legitimately a
    thumbnail, and grading it would cry wolf. Its URL is not the page's, so it never matches."""
    client, app = _client(tmp_path)
    try:
        body = _capture(
            client,
            url="https://archives.example.org/viewer/register/0806",
            html="<html><body><img src='https://archives.example.org/thumb/0806.jpg'></body></html>",
            media=[
                _embedded(
                    _encoded(600, 344, "JPEG"),
                    source_url="https://archives.example.org/thumb/0806.jpg",
                )
            ],
        )
        assert body["image_status"] == "none"
    finally:
        app.dependency_overrides.clear()


def test_BUG_027_a_pdf_opened_in_the_tab_is_not_graded(tmp_path):
    """A text PDF has its own URL too, and measures as no raster. Graded, it would flip to
    `failed` — "come back for the image" — on a document that never had one."""
    pdf_url = "https://archives.example.org/files/certificate.pdf"
    client, app = _client(tmp_path)
    try:
        body = _capture(
            client,
            url=pdf_url,
            html="<html><body><embed src='certificate.pdf'></body></html>",
            media=[_embedded(_text_pdf(), source_url=pdf_url, mime="application/pdf", name="certificate.pdf")],
        )
        assert body["image_status"] == "none"
    finally:
        app.dependency_overrides.clear()


def test_BUG_027_a_print_only_capture_names_the_print_and_the_access_cause(tmp_path):
    """The pre-grant case, end to end: the shortfall must not say "no measurable image" of a
    folder holding a 4174px print, and must carry the remedy the extension reported."""
    client, app = _client(tmp_path)
    try:
        body = _capture(client, media=[_print(4174, 2288)], missed=[_NO_ACCESS])
        assert body["verify_ok"] is False
        (shortfall,) = body["verify_shortfall"]
        assert "no measurable image" not in shortfall
        assert "media/0-record.pdf (4174px)" in shortfall
        assert "has no access to https://archives.example.org" in shortfall
    finally:
        app.dependency_overrides.clear()


def test_BUG_027_shortfall_wording_by_case():
    from capsule.pipeline.sufficiency import record_media_shortfall

    print_only = record_media_shortfall(
        assertion_count=0, media=[("media/0-record.pdf", "render:page-pdf", 4174)]
    )
    assert print_only is not None
    assert "it holds only media/0-record.pdf (4174px)" in print_only
    assert "not captured" not in print_only  # nothing was reported missing

    # A real (small) image outranks the print in the message: it is the closer miss.
    small = record_media_shortfall(
        assertion_count=0,
        media=[
            ("media/0-record.pdf", "render:page-pdf", 4174),
            ("media/1-preview.jpg", "page:embedded", 689),
        ],
    )
    assert "the largest is media/1-preview.jpg at 689px" in small

    nothing = record_media_shortfall(assertion_count=0, media=[], missed=["x.jpg — timed out"])
    assert "no measurable image at all" in nothing
    assert nothing.endswith("The record file was not captured: x.jpg — timed out")


def test_BUG_027_the_missed_file_prefix_is_the_one_the_backend_writes(tmp_path):
    """Pinned like `_NOT_ARMED`: the verify reads back exactly what the capture wrote."""
    client, app = _client(tmp_path)
    try:
        body = _capture(client, media=[_print(4174, 2288)], missed=[_NO_ACCESS])
        from capsule.pipeline.sufficiency import MISSED_RECORD_FILE_PREFIX

        assert f"{MISSED_RECORD_FILE_PREFIX}{_NO_ACCESS}" in body["media_warnings"]
    finally:
        app.dependency_overrides.clear()
