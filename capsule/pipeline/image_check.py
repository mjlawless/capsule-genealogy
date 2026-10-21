"""Is this record image actually USABLE as evidence? (REQ-001 R1.4)

The sibling of `render_check.py`, and the same hard-won lesson. BUG-001 archived a PDF
with perfect magic bytes, a perfect size, a perfect `%%EOF`, a correct SHA-256 and a
flawless text layer — and a blank page. Every check we had passed on an unreadable file.

The image path has the identical trap and worse odds. Ancestry's media API returns a
**96x95** thumbnail: valid JPEG, valid MIME, plausible size, correct hash — and useless
as evidence of a census sheet. The viewer canvas gives **~1370px**: screen resolution,
enough to look right in a toast and not enough to read a census column.

So we DECODE and measure. Pixels are the only thing that can tell a real scan from a
convincing fake — and REQ-001's constraint 4 ("assume everything will be walled off; we
must not have to return") means a silently-archived thumbnail is not a small bug. It is a
record we will never go back for, because we will believe we already have it.

Never raises: a gate that can crash a capture is worse than the bug it guards. Bytes it
cannot decode are `None`/`FAILED` — "cannot judge" is never "is fine".
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from capsule.pipeline.render_check import largest_raster_size

_logger = logging.getLogger(__name__)

# The FLOOR, on the long edge. Not a quality bar — a sanity check.
#
# BUG-006: this used to be a two-tier SIZE test, with anything under 2000px called
# "degraded". That rejected a WWII draft card downloaded at 1815x1200 — a physically small
# document, scanned in full, in colour, every field crisp. It was labelled `degraded-canvas`,
# which was wrong twice over: it is not a canvas, and it is not degraded. The record was
# complete and the tool said it was not, which jams a finished record on the queue forever.
#
# The lesson: A BARE PIXEL COUNT CANNOT DISTINGUISH "small document, fully scanned" from
# "big document, downscaled." Those are different facts, and pixels do not carry the
# difference. The measured gap between a legitimate small scan (1815) and the viewer canvas
# (1370) is 445px — there is no safe threshold in there, and the next collection would land
# inside it.
#
# So PROVENANCE decides (see `classify_image`) and the pixel test is demoted to catching a
# download that came back as a thumbnail. 800 clears the record-page thumbnail (600px) and
# the raw one (96px), and cannot false-FAIL a real scan.
MIN_USABLE_PX = 800

# The floor for the IMAGE-ONLY ORIGINAL class only (REQ-007 §3.3) — NOT a second opinion
# about `MIN_USABLE_PX`, and never applied globally.
#
# BUG-006 is the reason for the scoping. Raising the global floor to 1500 would re-reject
# that 1815x1200 draft card the moment a scan came in slightly smaller, which is the exact
# false-FAIL the 800 was lowered to prevent. This threshold answers a narrower question
# about a narrower class: when a capture's ENTIRE evidentiary payload is an image — an
# `original` from which no assertion could be read — a sub-1500px raster is by construction
# a viewer preview (Archion's fit-to-window grab measured 689x545) and never the record.
# There is no small-document ambiguity here, because there is no text to fall back on: if
# the image is a preview, the capture holds nothing at all.
RECORD_IMAGE_FLOOR_PX = 1500

# The one genuinely degraded source: the opt-in canvas fallback, which is a screen-resolution
# rendering by construction. Everything else we archive as a record image came from the
# provider's own download — which IS the full resolution, by definition. There is nothing
# better to be had, whatever its pixel count.
_CANVAS_PROVENANCE = "viewer:canvas"


class ImageStatus(str, Enum):
    """What we actually got. Recorded in the manifest, never implied (R4.3)."""

    FULL = "full"
    DEGRADED = "degraded-canvas"
    FAILED = "failed"


@dataclass(frozen=True)
class ImageFacts:
    width: int
    height: int
    format: str

    @property
    def long_side(self) -> int:
        return max(self.width, self.height)


def measure_image(data: bytes) -> Optional[ImageFacts]:
    """Decode `data` and report its REAL dimensions. None if it is not a usable image.

    Decodes fully rather than just reading the header, so a TRUNCATED download — a header
    promising 3000px followed by half a scan — fails here instead of being recorded as a
    full-resolution capture that cannot actually be opened.
    """
    if not data:
        return None
    try:
        from PIL import Image, ImageFile
    except ImportError as exc:  # pragma: no cover - the dependency is declared
        _logger.warning("cannot check the image: Pillow is unavailable: %s", exc)
        return None

    # Never accept a partial decode: a truncated file must FAIL, not quietly pass with
    # whatever pixels arrived before the bytes ran out.
    ImageFile.LOAD_TRUNCATED_IMAGES = False

    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format or "?"
            image.load()  # forces the full decode — this is what catches truncation
            width, height = image.size
    except Exception as exc:  # Pillow raises a wide variety of errors on bad input
        _logger.warning("cannot decode the record image: %s", exc)
        return None

    if width <= 0 or height <= 0:
        return None
    return ImageFacts(width=width, height=height, format=fmt)


_PDF_EXTS = frozenset({"pdf"})


def _is_pdf(ext: str, mime: str) -> bool:
    return (mime or "").lower().startswith("application/pdf") or ext.lower() in _PDF_EXTS


def measure_record_file(
    data: bytes, *, ext: str = "", mime: str = ""
) -> Optional[ImageFacts]:
    """Measure a record file whatever container it arrived in. None if unmeasurable.

    A record image is not always a raster file. Ancestry's own Download hands over a JPEG,
    but Archion's hands over a PDF wrapping the scan (REQ-007 §2) — and PIL cannot open a
    PDF at all, so routing one through `measure_image` reports FAILED on the single most
    important file in the capture. That is not a hypothetical: it is what happened the
    moment `site:official-download` was added to the backend's record-image provenances.

    For a PDF the answer is the biggest raster INSIDE it, not the page geometry: page size
    is in points and a 973x781pt page can carry either a 2704px archival scan or a 689px
    preview. Note this is a weaker check than the raster path — reading image metadata does
    not decode the pixels, so a PDF truncated after its metadata is not caught here the way
    a truncated JPEG is.
    """
    if _is_pdf(ext, mime):
        size = largest_raster_size(data)
        return ImageFacts(width=size[0], height=size[1], format="PDF") if size else None
    return measure_image(data)


def classify_image(
    data: bytes, *, provenance: Optional[str] = None, ext: str = "", mime: str = ""
) -> ImageStatus:
    """FULL / DEGRADED / FAILED — decided by WHERE the image came from, then sanity-checked.

    The BUG-001 lesson was "measurement beats the label", and applying it here was a
    mistake (BUG-006). For a blank PDF the label carried no information, so only pixels
    could tell the truth. Here the label carries the DECISIVE information:

      - an `ancestry:download` IS the full resolution — it is what the provider serves as
        the original, and there is nothing better to be had. A WWII draft card is a small
        piece of card: 1815px is a complete archival scan of it, not a downscaled census
        sheet, and no pixel count can tell those two apart.
      - a `viewer:canvas` fallback is screen-resolution BY CONSTRUCTION. It is the only
        genuinely degraded source we have.

    Pixels remain as a FLOOR — to catch a "download" that came back as a thumbnail (the
    600px record-page one, or the 96px raw one). That is the failure a size test can
    actually see.
    """
    facts = measure_record_file(data, ext=ext, mime=mime)
    if facts is None:
        return ImageStatus.FAILED
    # The floor: whatever it claims to be, this is not a readable record.
    if facts.long_side < MIN_USABLE_PX:
        return ImageStatus.FAILED
    if (provenance or "").startswith(_CANVAS_PROVENANCE):
        return ImageStatus.DEGRADED
    return ImageStatus.FULL


def image_warning(
    data: bytes,
    *,
    name: str,
    provenance: Optional[str] = None,
    ext: str = "",
    mime: str = "",
) -> Optional[str]:
    """A human-readable problem with this image, or None when it is genuinely full.

    The wording carries the real measured size, because "the image failed" tells the
    researcher nothing, while "96x95 — that is the thumbnail" tells them exactly what
    happened and what to do about it.
    """
    facts = measure_record_file(data, ext=ext, mime=mime)
    if facts is None:
        return (
            f"{name} is NOT a readable image (corrupt, truncated, or not an image at "
            "all). It is archived as evidence of what was served, but it is not usable "
            "as a record image — this record is NOT mined."
        )

    size = f"{facts.width}x{facts.height}"
    if facts.long_side < MIN_USABLE_PX:
        return (
            f"{name} is only {size} — that is a THUMBNAIL, not the record. It cannot be "
            "read as evidence. This record is NOT mined; the full-resolution image has "
            "still to be acquired."
        )
    if (provenance or "").startswith(_CANVAS_PROVENANCE):
        return (
            f"{name} is a {size} CANVAS capture — a screen-resolution rendering, not the "
            "provider's own download. Readable at a glance, but not the archival image. "
            "This record is NOT mined; get the real download."
        )
    # A download is the full resolution by definition, whatever its size: a small document
    # scanned completely is not a degraded big one (BUG-006).
    return None
