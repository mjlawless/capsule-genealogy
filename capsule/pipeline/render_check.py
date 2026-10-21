"""Does the rendered PDF actually SHOW anything? The visual gate (BUG-001).

A capture once reported `PDF ✓` while archiving a white page. Chrome's print pipeline
flattens a top-most full-viewport layer — an agent's "I am driving this tab" glow —
into an opaque full-page fill emitted *after* all the text. The resulting file has a
perfect text layer underneath and shows a human nothing.

Every check we had passed on it: magic bytes, file size, `%%EOF`, the SHA-256 against
the manifest, and text extraction. None of them look at pixels. That is the lesson:
for a RENDER, "a file was written" and "a human can read it" are different claims, and
only the second one is evidence.

So rasterize page 1 and look. The threshold separates the observed cases by a mile: the
blanked capture measured ~100% near-white, a correct render of the same page 61.7%.

Never raises. A gate that can crash a capture is worse than the bug it guards. An
unreadable file yields None ("cannot judge") — deliberately NOT False, so that "I could
not check" can never be mistaken for "I checked and it is fine".
"""
from __future__ import annotations

import logging
from typing import Optional

_logger = logging.getLogger(__name__)

# A page at or above this fraction of near-white sampled pixels shows a human nothing.
# Observed: ~1.00 blank, vs 0.617 for the same page rendered correctly.
BLANK_THRESHOLD = 0.99
# Per-channel value at or above which a pixel counts as "near white" (0-255).
_NEAR_WHITE = 245
# Sample every Nth pixel. The signal is a full-page fill, not a hairline, so a coarse
# grid is ample and keeps the gate to a few milliseconds.
_STRIDE = 4
_DPI = 110


#: Media provenances that ARE the record image (not a picture of the page): the provider's own
#: download, a site's official export, or the viewer's canvas. Shared by the capture-time
#: and verify-time blank-render warnings so the two cannot disagree (BUG-020).
RECORD_IMAGE_PROVENANCE = ("ancestry:download", "site:official-download", "viewer:canvas")

#: Record-image statuses that mean the capture holds the record itself.
EVIDENCE_IMAGE_STATUSES = frozenset({"full", "degraded-canvas"})


def blank_render_warning(name: str, record_image: Optional[str] = None) -> str:
    """What to say about a page render that came out blank (BUG-001, BUG-020).

    With no record image beside it, the render was the capture's picture of the page and a
    blank one is BUG-001's failure: say it is unusable and how to re-capture. With a record
    image measured `full` or `degraded-canvas`, the render was never the evidence. Some
    viewers (Ancestry's image viewer) draw the scan in a way that does not print, so the
    render is blank on every attempt, and "re-capture with overlays hidden" sent researchers
    after a fix that cannot work. The warning stays, because the file IS blank, but it names
    the evidence and asks for nothing.
    """
    if record_image:
        return (
            f"{name} renders blank, but this capture also holds the record image "
            f"{record_image}, which is the evidence. Nothing needs re-capturing."
        )
    return (
        f"{name} renders BLANK — something was composited over the page. The capture is "
        "archived, but this PDF is NOT usable as evidence: re-capture with overlays hidden."
    )


def blank_ratio(pdf_bytes: bytes) -> Optional[float]:
    """Fraction (0.0-1.0) of page 1's sampled pixels that are near-white.

    None if the bytes cannot be rasterized at all — a corrupt file, an empty one, or a
    missing rasterizer. None means "cannot judge", never "is fine".
    """
    if not pdf_bytes:
        return None
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - the dependency is declared
        _logger.warning("cannot check the render: pypdfium2 is unavailable: %s", exc)
        return None

    try:
        document = pdfium.PdfDocument(pdf_bytes)
        try:
            if len(document) == 0:
                return None
            bitmap = document[0].render(scale=_DPI / 72)
            # Read the raw buffer rather than going through PIL: it saves a dependency,
            # and the near-white test is channel-order-independent (every channel must
            # be high), so BGRA vs RGBA does not matter here.
            width, height = bitmap.width, bitmap.height
            stride, channels = bitmap.stride, bitmap.n_channels
            buffer = bytes(bitmap.buffer)
        finally:
            document.close()
    except Exception as exc:  # pdfium raises assorted types on bad input
        _logger.warning("cannot rasterize the rendered PDF: %s", exc)
        return None

    if width == 0 or height == 0 or channels < 3:
        return None
    near_white = 0
    sampled = 0
    for y in range(0, height, _STRIDE):
        row = y * stride
        for x in range(0, width, _STRIDE):
            offset = row + x * channels
            sampled += 1
            # Alpha (channel 4, when present) is ignored on purpose: pdfium renders on
            # an opaque white backing, so a transparent pixel already reads as white —
            # exactly as a human viewing the file would see it.
            if all(buffer[offset + c] >= _NEAR_WHITE for c in range(3)):
                near_white += 1
    if sampled == 0:
        return None
    return near_white / sampled


def looks_blank(pdf_bytes: bytes, threshold: float = BLANK_THRESHOLD) -> Optional[bool]:
    """True if page 1 renders effectively blank; None if it cannot be judged.

    True means: this file is not usable as evidence, however perfect its text layer is.
    """
    ratio = blank_ratio(pdf_bytes)
    if ratio is None:
        return None
    return ratio >= threshold


def largest_raster_size(pdf_bytes: bytes) -> Optional[tuple[int, int]]:
    """(width, height) in PIXELS of the biggest image EMBEDDED in `pdf_bytes`. None if none.

    Not the page size. A PDF's page geometry is in points and says nothing about how much
    detail is in the file: the same 973x781pt page can carry a 2704px archival scan or a
    689px viewer preview, and only the second one is the failure REQ-007 §3.3 has to catch.
    So this reports the raster, and the caller compares it against a pixel floor.

    Descends into Form XObjects (pypdfium2 recurses by default), because a scan wrapped in
    one is still the scan.

    Never raises, and None means "cannot judge", never "is fine" — the same contract as
    `blank_ratio`. A caller that treats None as a pass is reintroducing BUG-001.
    """
    if not pdf_bytes:
        return None
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - the dependency is declared
        _logger.warning("cannot measure the PDF: pypdfium2 is unavailable: %s", exc)
        return None

    try:
        document = pdfium.PdfDocument(pdf_bytes)
        try:
            best = (0, 0)
            for page in document:
                try:
                    for obj in page.get_objects(filter=(pdfium.raw.FPDF_PAGEOBJ_IMAGE,)):
                        metadata = obj.get_metadata()
                        size = (int(metadata.width), int(metadata.height))
                        if max(size) > max(best):
                            best = size
                finally:
                    page.close()
        finally:
            document.close()
    except Exception as exc:  # pdfium raises a wide variety of errors on bad input
        _logger.warning("cannot measure the rasters in the PDF: %s", exc)
        return None
    return best if max(best) else None


def largest_raster_px(pdf_bytes: bytes) -> Optional[int]:
    """Longest edge in pixels of the biggest raster in `pdf_bytes`. None if unmeasurable."""
    size = largest_raster_size(pdf_bytes)
    return max(size) if size else None
