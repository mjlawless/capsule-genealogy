"""REQ-017 — the site-terms note appears where people first meet Capsule.

Capsule works on subscription record sites, some of which limit what a user may save. The
README always said so; the welcome tab, the Help page and the store listing now say the same,
so a careful researcher sees it before the first capture.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule.collection.help import render_help

_ROOT = Path(__file__).resolve().parent.parent
_PHRASE = "within the terms of the sites you use"


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("relpath", ["README.md", "extension/welcome.html"])
def test_shipped_files_carry_the_terms_note(relpath):
    assert _PHRASE in _flat((_ROOT / relpath).read_text(encoding="utf-8"))


def test_help_page_carries_the_terms_note():
    assert _PHRASE in _flat(render_help())


def test_store_listing_carries_the_terms_note():
    # The listing is a development-repo document; the public export leaves it out.
    listing = _ROOT / "docs" / "web-store" / "listing.md"
    if not listing.exists():
        pytest.skip("store listing is not in this tree")
    text = listing.read_text(encoding="utf-8")
    private = text[text.index("WHAT STAYS PRIVATE") :]
    assert _PHRASE in _flat(private[: private.index("```")])
