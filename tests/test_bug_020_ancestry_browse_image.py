"""BUG-020 — Ancestry browse-image captures: a false "re-capture" alarm, and a thin citation.

Two defects on the same captures (browse images: a scan reached by browsing a collection,
with no indexed record behind it):

1. The page-PDF render of Ancestry's image viewer is blank on every attempt, because the
   viewer draws the scan in a way that does not print. The BUG-001 warning blamed an overlay
   and said "re-capture with overlays hidden", which cannot help. The full-resolution JPG
   beside it is the evidence. Now: beside a record image measured `full`/`degraded-canvas`,
   the warning names the evidence and asks for nothing; with no such image, BUG-001's alarm
   stands. Both at capture time and when `verify_capture` re-measures an old folder.
2. The citation named only the collection, which holds hundreds of thousands of images. The
   viewer prints the browse path (county > volume) and an "N of M" counter; the new
   `ancestry-browse-image-v1` template cites by them.

The HTML below is a synthetic reduction of the viewer's markup (the real snapshots are
subscriber content and stay local), keeping the exact attribute shapes the parser reads.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import date

import pytest

import tests.test_bug_016_media_warnings_persist as t16

_URL = "https://www.ancestry.com/imageviewer/collections/8802/images/005537869_00063"
_TITLE = "Ancestry.com - Pennsylvania, U.S., Wills and Probate Records, 1683-1993"


def _now():
    from datetime import datetime, timezone

    return datetime(2026, 9, 28, 21, 15, tzinfo=timezone.utc)


def _viewer(*, county="Fayette", volume="Orphans Court Records, Vol 1-3, 1783-1844",
            page="63", count="695") -> str:
    crumbs = ""
    for i, value in enumerate(v for v in (county, volume) if v is not None):
        crumbs += (
            f'<div data-browse-index="{i}" class="breadcrumbItem link">'
            f'<span class="pathText">{value}</span>'
            f'<input class="browse-input noDisplay" aria-label="Notes" name="browsePath-{i}" '
            f'type="text" data-tag="input" autocomplete="off" data-browse-index="{i}" '
            f'size="7" value="{value}"></div>'
        )
    counter = (
        f'<input aria-label="Page number" class="page-input" maxlength="3" style="width:3ch" '
        f'value="{page}"><span class="imageOfText middle">of</span>'
        f'<span class="imageCountText middle">{count}</span>'
        if page is not None
        else ""
    )
    return (
        f"<html><head><title>{_TITLE}</title></head><body>"
        f'<nav class="breadcrumbs">{crumbs}</nav><div>{counter}</div></body></html>'
    )


# -- the browse path ---------------------------------------------------------------------


def test_BUG_020_the_browse_path_is_read_off_the_viewer():
    from capsule.pipeline.page_identity import AncestryBrowsePath, ancestry_browse_path_from

    assert ancestry_browse_path_from(_viewer(), _URL) == AncestryBrowsePath(
        trail=("Fayette", "Orphans Court Records, Vol 1-3, 1783-1844"),
        image=63,
        image_count=695,
    )


@pytest.mark.parametrize(
    "url",
    [
        # Not Ancestry: a lookalike path on another host is not this breadcrumb.
        "https://evil.example/imageviewer/collections/8802/images/005537869_00063",
        "https://ancestry.com.evil.example/imageviewer/collections/8802/images/x_00063",
        # Ancestry, but a record page, not the image viewer.
        "https://www.ancestry.com/discoveryui-content/view/123:8802",
    ],
)
def test_BUG_020_only_an_ancestry_image_viewer_is_read(url):
    from capsule.pipeline.page_identity import ancestry_browse_path_from

    assert ancestry_browse_path_from(_viewer(), url) is None


def test_BUG_020_an_image_number_that_disagrees_with_the_url_is_not_cited():
    """The URL is the live leaf; a stale counter must not put the wrong image in a citation."""
    from capsule.pipeline.page_identity import ancestry_browse_path_from

    path = ancestry_browse_path_from(_viewer(page="12"), _URL)
    assert path is not None
    assert path.trail == ("Fayette", "Orphans Court Records, Vol 1-3, 1783-1844")
    assert path.image is None


def test_BUG_020_no_counter_still_cites_the_path():
    from capsule.pipeline.page_identity import ancestry_browse_path_from

    path = ancestry_browse_path_from(_viewer(page=None), _URL)
    assert path is not None and path.image is None and path.image_count is None
    assert path.trail[-1] == "Orphans Court Records, Vol 1-3, 1783-1844"


def test_BUG_020_an_image_past_the_count_is_not_cited():
    from capsule.pipeline.page_identity import ancestry_browse_path_from

    path = ancestry_browse_path_from(_viewer(count="40"), _URL)
    assert path is not None and path.image is None and path.image_count is None


def test_BUG_020_no_browse_inputs_means_no_path():
    from capsule.pipeline.page_identity import ancestry_browse_path_from

    assert ancestry_browse_path_from(_viewer(county=None, volume=None), _URL) is None


# -- the citation ------------------------------------------------------------------------


def _snapshot(html: str):
    from types import SimpleNamespace

    return SimpleNamespace(url=_URL, title=_TITLE, site_name="ancestry.com", html=html)


def test_BUG_020_a_browse_image_is_cited_by_its_path():
    from capsule.pipeline.citation import build_citation
    from capsule.pipeline.page_identity import ancestry_browse_path_from

    citation = build_citation(
        citation_id="c1",
        source_id="s1",
        snapshot=_snapshot(_viewer()),
        access_date=date(2026, 9, 28),
        archived_url=_URL,
        browse=ancestry_browse_path_from(_viewer(), _URL),
    )
    assert citation.template_id == "ancestry-browse-image-v1"
    assert citation.reference_note == (
        '"Pennsylvania, U.S., Wills and Probate Records, 1683-1993," digital images, '
        f"ancestry.com ({_URL} : accessed 28 September 2026), Fayette > "
        "Orphans Court Records, Vol 1-3, 1783-1844 > image 63 of 695."
    )
    assert citation.short_note == (
        '"Pennsylvania, U.S., Wills and Probate Records, 1683-1993," Fayette > '
        "Orphans Court Records, Vol 1-3, 1783-1844 > image 63 of 695."
    )


def test_BUG_020_a_record_with_assertions_still_cites_the_record():
    """An indexed record in the viewer cites the RECORD; the browse path is only a fallback."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.pipeline.fetch import ProvidedContentFetcher

    raw = {
        "record_type": "probate",
        "personas": [{"label": "John Doe", "role": "decedent"}],
        "assertions": [
            {
                "persona": "John Doe",
                "field": "name",
                "value": "John Doe",
                "evidence_type": "stated",
                "snippet": "Fayette",
            }
        ],
    }
    snapshot = ProvidedContentFetcher(_viewer()).fetch(_URL)
    built = build_capture_models(
        snapshot, parse_extraction(json.dumps(raw)), new_id=lambda: "id-1", now=_now
    )
    assert built.citation.template_id == "record-transcript-v1"


def test_BUG_020_the_pipeline_cites_a_browse_image_by_its_path():
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.pipeline.fetch import ProvidedContentFetcher

    empty = {"record_type": "unknown", "personas": [], "assertions": []}
    snapshot = ProvidedContentFetcher(_viewer()).fetch(_URL)
    built = build_capture_models(
        snapshot, parse_extraction(json.dumps(empty)), new_id=lambda: "id-1", now=_now
    )
    assert built.citation.template_id == "ancestry-browse-image-v1"
    assert "image 63 of 695" in built.citation.reference_note


# -- the warning -------------------------------------------------------------------------


def _jpeg(width: int = 3000, height: int = 2000) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 190, 170)).save(buf, "JPEG", quality=20)
    return buf.getvalue()


def test_BUG_020_the_message_depends_on_what_else_the_capture_holds():
    from capsule.pipeline.render_check import blank_render_warning

    alone = blank_render_warning("record.pdf")
    assert "NOT usable as evidence" in alone and "re-capture" in alone
    beside = blank_render_warning("record.pdf", "005537869-00063.jpg")
    assert "005537869-00063.jpg" in beside and "Nothing needs re-capturing" in beside
    assert "re-capture with overlays hidden" not in beside


def _post(tmp_path, media):
    client, backend = t16._client(tmp_path, json.dumps(t16._RAW))
    try:
        resp = client.post(
            "/capture",
            json={"url": t16._URL, "html": t16._PAGE, "gating": "gated", "media": media},
            headers=t16._AUTH,
        )
        assert resp.status_code == 200, resp.text
    finally:
        backend.app.dependency_overrides.clear()
    (manifest_path,) = (tmp_path / "captures").glob("*/manifest.json")
    return json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path.parent


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


_BLANK_RENDER_MEDIA = {
    "data_base64": _b64(t16.BLANK_RENDER),
    "filename": "record.pdf",
    "mime": "application/pdf",
    "provenance": "render:page-pdf",
}


def test_BUG_020_beside_a_full_record_image_a_blank_render_asks_for_nothing(tmp_path):
    """End to end, read back off disk: the reported case."""
    from capsule.capture.manifest import verify_capture

    manifest, folder = _post(
        tmp_path,
        [
            _BLANK_RENDER_MEDIA,
            {
                "data_base64": _b64(_jpeg()),
                "filename": "005537869_00063.jpg",
                "mime": "image/jpeg",
                "provenance": "site:official-download",
            },
        ],
    )
    assert manifest["image"] == "full"
    (warning,) = [w for w in manifest["media_warnings"] if "blank" in w.lower()]
    assert "Nothing needs re-capturing" in warning
    assert "overlays hidden" not in warning
    # And the retroactive re-measure agrees with what was written.
    (re_measured,) = verify_capture(folder).warnings
    assert "Nothing needs re-capturing" in re_measured


def test_BUG_020_alone_a_blank_render_keeps_the_BUG_001_alarm(tmp_path):
    """No record image beside it: the render WAS the capture, and a blank one is unusable."""
    from capsule.capture.manifest import verify_capture

    manifest, folder = _post(tmp_path, [_BLANK_RENDER_MEDIA])
    (warning,) = [w for w in manifest["media_warnings"] if "BLANK" in w]
    assert "re-capture with overlays hidden" in warning
    (re_measured,) = verify_capture(folder).warnings
    assert "re-capture with overlays hidden" in re_measured


def test_BUG_020_verify_softens_an_old_capture_that_holds_its_record(tmp_path):
    """A folder banked before the fix re-reports with the corrected wording, from its bytes."""
    from capsule.capture.manifest import MediaItem, verify_capture

    folder = t16._capture_with(
        tmp_path,
        media=[
            *t16._media(t16.BLANK_RENDER),
            MediaItem(
                slug="005537869-00063",
                ext="jpg",
                data=_jpeg(),
                mime="image/jpeg",
                provenance="site:official-download",
                source_url=None,
            ),
        ],
    )
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "image" not in manifest  # written without a status, like a pre-R4.3 folder
    # Without a measured status the record image is not trusted to be the record.
    (warning,) = verify_capture(folder).warnings
    assert "overlays hidden" in warning


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://www.ancestry.com/discoveryui-content/view/1:2", True),
        ("https://www.ancestry.co.uk/search/collections/1/records/2", True),
        ("https://www.ancestry.com.au/search/collections/1/records/2", True),
        ("https://ancestry.com/x", True),
        # Lookalikes. The old rule, `ancestry\.[a-z.]+$`, accepted every one of these.
        ("https://ancestry.com.evil.example/search/collections/1/records/2", False),
        ("https://ancestry.evil.com/search/collections/1/records/2", False),
        ("https://www.ancestry.co.uk.attacker.net/x", False),
        ("https://notancestry.com/x", False),
        ("https://evil.example/www.ancestry.com/x", False),
    ],
)
def test_BUG_020_found_is_ancestry_url_names_ancestry_domains_only(url, want):
    """Found by this file's lookalike test: the backend's host rule was the pre-BUG-018 one."""
    from capsule.pipeline.ancestry import is_ancestry_url

    assert is_ancestry_url(url) is want
