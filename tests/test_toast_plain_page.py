"""The on-page toast: an ordinary page with no record image is a success, not a failure.

Found in the free-sites test pass (2026-10-22): every capture on Chronicling America, Trove,
IrishGenealogy and Find a Grave toasted "IMAGE ✗ none — NOT mined" in red, though the
capture was complete. `none` with nothing armed is how the backend describes any page that
has no separate record image, and it raises no follow-up for it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_CONTENT = Path(__file__).resolve().parent.parent / "extension" / "content.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _toast(res: dict) -> dict:
    source = _CONTENT.read_text(encoding="utf-8")
    start = source.index("  function toastFromResult(res) {")
    fn = source[start : source.index("\n  }\n", start) + 4]
    script = (
        "let shown = null, prompted = false;"
        "const setOutcome = () => {};"
        "const toast = (text, ok) => { shown = { text, ok }; };"
        "const showKeyPrompt = () => { prompted = true; };"
        + fn
        + f"toastFromResult({json.dumps(res)});"
        "console.log(JSON.stringify({ ...shown, prompted }));"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


_PLAIN = {
    "ok": True,
    "verify_ok": True,
    "pdf": "attached",
    "media_warnings": [],
    "missed_media": [],
    "image_status": "none",
    "image_skipped": "no download was armed",
    "extraction_pending": True,
}


def test_a_plain_page_capture_is_green():
    shown = _toast(_PLAIN)
    assert shown["ok"] is True
    assert "NOT mined" not in shown["text"] and "IMAGE" not in shown["text"]
    assert shown["prompted"] is True  # the clean no-key capture still offers the key


def test_an_armed_download_that_never_came_stays_red():
    shown = _toast({**_PLAIN, "image_skipped": "the armed download did not arrive in time"})
    assert shown["ok"] is False
    assert "IMAGE ✗ none — NOT mined" in shown["text"]


def test_a_failed_image_or_failed_verify_stays_red():
    assert _toast({**_PLAIN, "image_status": "failed", "image_skipped": None})["ok"] is False
    assert _toast({**_PLAIN, "verify_ok": False, "verify_shortfall": ["no image"]})["ok"] is False
    assert _toast({**_PLAIN, "missed_media": ["scan.jpg — no access"]})["ok"] is False
