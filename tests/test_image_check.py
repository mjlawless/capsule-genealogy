"""Tests for the record-image gate (capsule/pipeline/image_check.py) — REQ-001 R1.4.

The BUG-001 lesson, applied to images: "media written" is not "media usable". A capture
that archives Ancestry's 96x95 thumbnail and reports success is worse than one that
fails, because nobody re-runs a success — and the entire point of REQ-001 is that we must
never have to go back (constraint 4: assume Ancestry will be walled off).

So the gate DECODES the bytes. Magic bytes and a Content-Type are not evidence of a
usable scan; pixels are.

Written FIRST (RED): capsule/pipeline/image_check.py does not exist yet.
"""
from __future__ import annotations

import io

import pytest


def _jpeg(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 110, 100)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _png(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


# The three real cases from the pilot on 7602:56510272.
THUMBNAIL = _jpeg(96, 95)  # what the media API actually returns — useless
CANVAS = _jpeg(1370, 1092)  # the viewer canvas — SCREEN resolution
FULL_SCAN = _jpeg(3000, 2400)  # what Download should give us


class TestMeasure:
    def test_reports_real_pixel_dimensions(self):
        from capsule.pipeline.image_check import measure_image

        result = measure_image(FULL_SCAN)
        assert result is not None
        assert (result.width, result.height) == (3000, 2400)
        assert result.format == "JPEG"

    def test_undecodable_bytes_are_None_never_an_exception(self):
        """A gate that can crash a capture is worse than the bug it guards."""
        from capsule.pipeline.image_check import measure_image

        assert measure_image(b"not an image") is None
        assert measure_image(b"") is None

    def test_a_truncated_image_does_not_pass(self):
        """A half-finished download must not read as a complete scan."""
        from capsule.pipeline.image_check import measure_image

        assert measure_image(FULL_SCAN[: len(FULL_SCAN) // 3]) is None


DOWNLOAD = "ancestry:download"
CANVAS_FALLBACK = "viewer:canvas-fallback"

# The record that exposed BUG-006: a WWII draft card, downloaded at Ancestry's own full
# resolution. A physically small document — 1815px IS the complete archival scan of it.
SMALL_DOCUMENT = _jpeg(1815, 1200)
# The record-page thumbnail: not a record, whatever it claims to be.
PAGE_THUMBNAIL = _jpeg(600, 595)


class TestClassify:
    def test_BUG_the_96x95_thumbnail_is_a_hard_FAIL(self):
        """Regression (REQ-001 R1.4): the thumbnail must never pass as a record image.

        Ancestry's media API returns 96x95 when fetched directly. It has valid JPEG magic
        bytes, a valid MIME, a plausible size and a correct SHA-256 — every check we had
        before this one. It is also useless as evidence of a census sheet.
        """
        from capsule.pipeline.image_check import ImageStatus, classify_image

        assert classify_image(THUMBNAIL, provenance=DOWNLOAD) is ImageStatus.FAILED

    def test_BUG_006_a_small_document_downloaded_at_full_resolution_is_full_not_degraded(self):
        """Regression: a 1815x1200 `ancestry:download` (a WWII draft card) is FULL.

        It was classified `degraded-canvas` — wrong twice: it is not a canvas, and it is not
        degraded. It is the full resolution Ancestry holds. The old rule called anything
        under 2000px degraded, and a bare pixel count cannot tell "small document, fully
        scanned" from "big document, downscaled" — different facts, and pixels do not carry
        the difference. Provenance decides; pixels are only a floor.

        This is the MIRROR of the ledger's invariant: we DO hold the record, in full, and
        the tool said we did not — jamming a completed record on the queue forever.
        """
        from capsule.pipeline.image_check import ImageStatus, classify_image

        assert classify_image(SMALL_DOCUMENT, provenance=DOWNLOAD) is ImageStatus.FULL

    def test_the_canvas_fallback_is_DEGRADED_however_big_it_is(self):
        """The ONLY genuinely degraded source: screen-resolution by construction."""
        from capsule.pipeline.image_check import ImageStatus, classify_image

        assert classify_image(CANVAS, provenance=CANVAS_FALLBACK) is ImageStatus.DEGRADED
        # Even a large canvas is still a screen rendering, not the provider's own scan.
        big_canvas = classify_image(_jpeg(3000, 2400), provenance=CANVAS_FALLBACK)
        assert big_canvas is ImageStatus.DEGRADED

    def test_a_real_scan_is_FULL(self):
        from capsule.pipeline.image_check import ImageStatus, classify_image

        assert classify_image(FULL_SCAN, provenance=DOWNLOAD) is ImageStatus.FULL

    def test_the_record_page_thumbnail_is_below_the_floor(self):
        """600px is not a record, whatever its provenance claims."""
        from capsule.pipeline.image_check import ImageStatus, classify_image

        assert classify_image(PAGE_THUMBNAIL, provenance=DOWNLOAD) is ImageStatus.FAILED

    def test_garbage_is_FAILED(self):
        from capsule.pipeline.image_check import ImageStatus, classify_image

        assert classify_image(b"<html>403 Forbidden</html>") is ImageStatus.FAILED

    def test_png_is_measured_too(self):
        from capsule.pipeline.image_check import ImageStatus, classify_image

        assert classify_image(_png(2400, 3000), provenance=DOWNLOAD) is ImageStatus.FULL


class TestWarnings:
    def test_a_failed_image_explains_itself_with_its_real_size(self):
        from capsule.pipeline.image_check import image_warning

        warning = image_warning(THUMBNAIL, name="record.jpg")
        assert warning is not None
        assert "96x95" in warning.replace("×", "x")
        assert "record.jpg" in warning

    def test_a_canvas_capture_says_it_is_a_screen_rendering(self):
        from capsule.pipeline.image_check import image_warning

        warning = image_warning(CANVAS, name="record.jpg", provenance=CANVAS_FALLBACK)
        assert warning is not None
        assert "1370x1092" in warning.replace("×", "x")
        assert "CANVAS" in warning

    def test_a_full_scan_raises_no_warning(self):
        """The gate must not cry wolf on the very thing it was built to accept."""
        from capsule.pipeline.image_check import image_warning

        assert image_warning(FULL_SCAN, name="record.jpg", provenance=DOWNLOAD) is None

    def test_BUG_006_a_small_downloaded_document_raises_no_warning(self):
        """The draft card is complete evidence. Warning about it was the bug."""
        from capsule.pipeline.image_check import image_warning

        assert image_warning(SMALL_DOCUMENT, name="card.jpg", provenance=DOWNLOAD) is None


@pytest.mark.parametrize("data", [b"", b"\xff\xd8\xff", b"%PDF-1.4 not an image"])
def test_no_input_ever_raises(data):
    from capsule.pipeline.image_check import classify_image, image_warning, measure_image

    measure_image(data)
    classify_image(data)
    image_warning(data, name="x.jpg")
