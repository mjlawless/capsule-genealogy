"""Page images at native resolution, field crops, and rotation (REQ-006 T2).

§4 of REQ-006 makes resolution a correctness requirement: at a downscaled
whole-page render a mother's surname read as "Sarah [Tann-?]"; from a 400 dpi
crop of the same cell it is plainly "Sarah. Lawless". So a transcriber reads
crops cut from the scan's OWN pixels.

A scanned-certificate PDF (NYC Municipal Archives) wraps one 300 dpi JPEG per
page. That image is EXTRACTED, not rendered — rendering resamples. A page that
does not hold one upright, full-page image (a born-digital PDF, a composite, a
flipped placement) is rendered at 300 dpi instead, and `origin` says so, so a
consumer can tell a native crop from a resampled one.

Coordinates everywhere are the page image's native pixels, origin top-left.
Rotation is degrees CLOCKWISE applied after cropping — pre-1910 NYC forms write
their tabular columns sideways, and the wrong direction yields upside-down text.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from PIL import Image

from capsule.agent.base import PromptImage

RENDER_DPI = 300
# An embedded image must cover this share of the page to count as "the scan".
_FULL_PAGE_COVERAGE = 0.9
_TRANSPOSE = {
    90: Image.Transpose.ROTATE_270,  # PIL's constants are counter-clockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}

Origin = Literal["embedded", "rendered", "file"]


@dataclass(frozen=True)
class PageImage:
    """One page of a source document, as pixels, and how those pixels were obtained."""

    media_filename: str
    page: Optional[int]  # 0-based for PDFs; None for a single image file
    image: Image.Image
    origin: Origin


def load_pages(path: Path) -> list[PageImage]:
    """Every page of `path` as a PageImage — a PDF page by page, an image as one page."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return _pdf_pages(path)
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    return [PageImage(media_filename=path.name, page=None, image=image, origin="file")]


def _pdf_pages(path: Path) -> list[PageImage]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        pages: list[PageImage] = []
        for index in range(len(document)):
            page = document[index]
            try:
                scan = _full_page_scan(page, pdfium)
                if scan is not None:
                    pages.append(PageImage(path.name, index, scan, "embedded"))
                else:
                    bitmap = page.render(scale=RENDER_DPI / 72)
                    try:
                        rendered = bitmap.to_pil().convert("RGB")  # convert() copies the pixels
                    finally:
                        bitmap.close()  # native memory: don't wait for GC in a batch run
                    pages.append(PageImage(path.name, index, rendered, "rendered"))
            finally:
                page.close()
        return pages
    finally:
        document.close()


def _full_page_scan(page, pdfium) -> Optional[Image.Image]:
    """The page's scan at native pixels, or None if the page is not one upright image."""
    width, height = page.get_size()
    page_area = width * height
    objects = list(page.get_objects())
    try:
        images = [o for o in objects if o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE]
        if len(images) != 1:
            return None
        (obj,) = images
        a, b, c, d, _e, _f = obj.get_matrix().get()
        if b != 0 or c != 0 or a <= 0 or d <= 0:
            return None  # rotated or flipped placement: let the renderer apply it
        left, bottom, right, top = obj.get_bounds()
        if page_area <= 0 or (right - left) * (top - bottom) / page_area < _FULL_PAGE_COVERAGE:
            return None
        bitmap = obj.get_bitmap(render=False)
        try:
            return bitmap.to_pil().convert("RGB")  # convert() copies the pixels
        finally:
            bitmap.close()  # native memory: don't wait for GC in a batch run
    finally:
        for obj in objects:
            obj.close()


def crop(
    image: Image.Image, bbox: tuple[int, int, int, int], *, rotation: int = 0
) -> Image.Image:
    """Cut `bbox` (x0, y0, x1, y1) from `image`, then rotate it `rotation`° clockwise."""
    x0, y0, x1, y1 = bbox
    width, height = image.size
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
        raise ValueError(f"bbox {bbox} is empty or outside the {width}x{height} page")
    region = image.crop(bbox)
    if rotation == 0:
        return region
    if rotation not in _TRANSPOSE:
        raise ValueError(f"rotation must be 0/90/180/270, got {rotation}")
    return region.transpose(_TRANSPOSE[rotation])


def downscale(image: Image.Image, *, max_edge: int) -> tuple[Image.Image, float]:
    """Shrink so the longer edge is at most `max_edge`; never enlarge. Returns (image, scale)."""
    longest = max(image.size)
    if longest <= max_edge:
        return image, 1.0
    scale = max_edge / longest
    size = (round(image.width * scale), round(image.height * scale))
    return image.resize(size, Image.Resampling.LANCZOS), scale


def to_prompt_image(
    page: PageImage,
    *,
    bbox: Optional[tuple[int, int, int, int]] = None,
    rotation: int = 0,
    max_edge: Optional[int] = None,
    label: Optional[str] = None,
) -> PromptImage:
    """Encode a crop (or the whole page) as PNG, with an ImageRef naming exactly what was sent.

    A field crop is sent at native resolution — pass `max_edge` only for a whole-page
    overview, where `scale` then records the shrink so boxes can be mapped back.
    """
    full = (0, 0, page.image.width, page.image.height)
    region = crop(page.image, bbox or full, rotation=rotation)
    scale = 1.0
    if max_edge is not None:
        region, scale = downscale(region, max_edge=max_edge)
    buffer = io.BytesIO()
    region.save(buffer, format="PNG")
    return PromptImage.from_bytes(
        buffer.getvalue(),
        media_type="image/png",
        label=label,
        media_filename=page.media_filename,
        page=page.page,
        bbox=bbox,
        rotation=rotation,
        scale=scale,
        width=region.width,
        height=region.height,
    )
