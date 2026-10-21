"""REQ-006 T2 — native-resolution page images, crops, and rotation.

§4: transcription must read per-field crops at the scan's own resolution. The NYC
certificate PDFs wrap a 300 dpi JPEG per page, so the page image is EXTRACTED —
never re-rendered, which would resample. A page with no single full-page image
(a born-digital PDF, a composite) falls back to a 300 dpi render, and says so.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from capsule.pipeline.raster import (
    crop,
    downscale,
    load_pages,
    to_prompt_image,
)

_CORPUS_Q1903 = Path(
    "data/captures/20260719-a860historicalvitalrecordsnycgov-603f8316/media/1-d-q-1903-0000148.pdf"
)


def _scan(size=(600, 800)) -> Image.Image:
    """A synthetic 'scan': white page, a black block top-left, a grey block bottom-right."""
    img = Image.new("RGB", size, "white")
    img.paste((0, 0, 0), (10, 10, 110, 60))
    img.paste((128, 128, 128), (size[0] - 110, size[1] - 60, size[0] - 10, size[1] - 10))
    return img


def _pdf_of(images, tmp_path: Path, resolution: float = 300.0) -> Path:
    path = tmp_path / "cert.pdf"
    images[0].save(path, save_all=True, append_images=images[1:], resolution=resolution)
    return path


def test_pdf_pages_are_extracted_at_native_pixels(tmp_path):
    path = _pdf_of([_scan(), _scan((500, 700))], tmp_path)
    pages = load_pages(path)
    assert [p.image.size for p in pages] == [(600, 800), (500, 700)]
    assert [p.page for p in pages] == [0, 1]
    assert all(p.origin == "embedded" for p in pages)
    assert pages[0].media_filename == "cert.pdf"


def test_plain_image_file_is_one_page(tmp_path):
    path = tmp_path / "scan.jpg"
    _scan().save(path)
    (page,) = load_pages(path)
    assert page.page is None and page.origin == "file" and page.image.size == (600, 800)


def test_page_without_an_embedded_scan_is_rendered_and_labelled(tmp_path):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(72 * 2, 72 * 3)  # 2in x 3in, no objects
    path = tmp_path / "blank.pdf"
    pdf.save(path)
    (page,) = load_pages(path)
    assert page.origin == "rendered"
    width, height = page.image.size  # 300 dpi; pdfium may round an edge up a pixel
    assert abs(width - 600) <= 1 and abs(height - 900) <= 1


def test_crop_uses_native_coordinates():
    region = crop(_scan(), (10, 10, 110, 60))
    assert region.size == (100, 50)
    assert region.getpixel((50, 25)) == (0, 0, 0)


@pytest.mark.parametrize(
    "rotation, size, dark_corner",
    [
        (0, (100, 40), (0, 0)),
        (90, (40, 100), (39, 0)),  # clockwise: top-left goes to top-right
        (180, (100, 40), (99, 39)),
        (270, (40, 100), (0, 99)),
    ],
)
def test_crop_rotates_clockwise(rotation, size, dark_corner):
    img = Image.new("RGB", (100, 40), "white")
    img.paste((0, 0, 0), (0, 0, 10, 10))
    out = crop(img, (0, 0, 100, 40), rotation=rotation)
    assert out.size == size
    assert out.getpixel(dark_corner) == (0, 0, 0)


def test_crop_rejects_a_box_outside_the_page():
    with pytest.raises(ValueError, match="outside"):
        crop(_scan(), (500, 700, 700, 900))


def test_downscale_reports_its_scale_and_never_upscales():
    small, scale = downscale(_scan((2000, 2400)), max_edge=1200)
    assert max(small.size) == 1200 and scale == pytest.approx(0.5)
    same, one = downscale(_scan((300, 400)), max_edge=1200)
    assert same.size == (300, 400) and one == 1.0


def test_to_prompt_image_records_exactly_what_was_sent(tmp_path):
    (page,) = load_pages(_pdf_of([_scan()], tmp_path))
    img = to_prompt_image(page, bbox=(10, 10, 110, 60), rotation=90, label="father_name")
    ref = img.ref
    assert (ref.media_filename, ref.page, ref.bbox, ref.rotation) == ("cert.pdf", 0, (10, 10, 110, 60), 90)
    assert (ref.width, ref.height, ref.scale) == (50, 100, 1.0)
    decoded = Image.open(io.BytesIO(img.data))
    assert decoded.size == (50, 100) and img.media_type == "image/png"


def test_to_prompt_image_whole_page_downscaled(tmp_path):
    (page,) = load_pages(_pdf_of([_scan((2000, 2400))], tmp_path))
    img = to_prompt_image(page, max_edge=1200, label="page 1")
    assert img.ref.bbox is None and img.ref.scale == pytest.approx(0.5)
    assert (img.ref.width, img.ref.height) == (1000, 1200)


@pytest.mark.skipif(not _CORPUS_Q1903.exists(), reason="corpus not present")
def test_corpus_q1903_is_two_native_300dpi_pages():
    pages = load_pages(_CORPUS_Q1903)
    assert [p.image.size for p in pages] == [(2072, 2317), (2072, 2317)]
    assert all(p.origin == "embedded" for p in pages)
