"""Tests for the rendered-PDF visual gate (capsule/pipeline/render_check.py).

BUG-001: a full-viewport agent overlay is flattened by Chrome's print pipeline into an
opaque full-page fill painted AFTER the text. The PDF keeps a perfect text layer and
shows a human a white page. Magic bytes, size, %%EOF, SHA-256 and text extraction ALL
passed on that file. Only pixels catch it — which is why this module exists.

Written FIRST (RED): capsule/pipeline/render_check.py does not exist yet.
"""
from __future__ import annotations

import pytest


def _pdf(content_stream: bytes) -> bytes:
    """A minimal, valid one-page PDF (US Letter) drawing `content_stream`.

    Hand-built rather than generated, so the test owns exactly what is on the page: an
    empty stream is a genuinely blank page, and a full-page `re f` fill is the shape of
    the actual bug.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << >> >>",
        b"<< /Length "
        + str(len(content_stream)).encode()
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_at).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


BLANK = _pdf(b"")
# The bug's own shape: an opaque white full-page fill over everything.
WHITE_FILL_OVER_ALL = _pdf(b"1 1 1 rg\n0 0 612 792 re f\n")
# Ink on the page: a big black box. A human sees something.
INKED = _pdf(b"0 0 0 rg\n50 50 500 700 re f\n")


class TestBlankRatio:
    def test_an_empty_page_is_all_white(self):
        from capsule.pipeline.render_check import blank_ratio

        assert blank_ratio(BLANK) == pytest.approx(1.0, abs=0.01)

    def test_a_page_with_ink_is_not(self):
        from capsule.pipeline.render_check import blank_ratio

        ratio = blank_ratio(INKED)
        assert ratio is not None
        assert ratio < 0.9

    def test_unreadable_bytes_return_None_rather_than_raising(self):
        """A capture must never die because the gate could not read the file.

        None means "cannot judge" — distinct from "judged blank". The caller reports
        that; it does not fail the capture.
        """
        from capsule.pipeline.render_check import blank_ratio

        assert blank_ratio(b"not a pdf at all") is None
        assert blank_ratio(b"") is None


class TestLooksBlank:
    def test_BUG_001_white_fill_over_text_is_caught(self):
        """Regression: THE bug. An opaque full-page fill is blank to a human.

        This is the shape of the file that shipped as `PDF ✓`. Every byte-level check
        passed on it. The gate exists so that never happens twice.
        """
        from capsule.pipeline.render_check import looks_blank

        assert looks_blank(WHITE_FILL_OVER_ALL) is True

    def test_an_inked_page_passes(self):
        from capsule.pipeline.render_check import looks_blank

        assert looks_blank(INKED) is False

    def test_unjudgeable_is_None_not_False(self):
        """Never let "I could not check" masquerade as "I checked and it is fine"."""
        from capsule.pipeline.render_check import looks_blank

        assert looks_blank(b"not a pdf") is None
