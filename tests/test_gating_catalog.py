"""Gating inference: the catalog decides for sites it knows; page markers for the rest.

From the free-sites test pass (2026-10-22): every Find a Grave memorial was saved as gated
because of a hidden "Sign out successful" dialog, and Trove (signed out) was gated too. On
the owner's archive the new rule changes only those (8 Find a Grave pages, gated -> public)
in the unsafe direction; everything else it changes moves toward gated.
"""
from __future__ import annotations

import pytest

from capsule.contracts.evidence import GatingStatus
from capsule.pipeline import fetch
from capsule.pipeline.fetch import detect_gating

GATED, PUBLIC = GatingStatus.GATED, GatingStatus.PUBLIC
_SCRIPT_ONLY = (
    "<html><body><h1>Article</h1><script>window.i18n={\"menu.logout\":\"Log out\","
    "\"sub\":\"Cancel my subscription\"}</script></body></html>"
)


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        # Find a Grave: the hidden "you have signed out" dialog is not a session...
        ("https://www.findagrave.com/memorial/1/x",
         '<div class="modal" hidden><h1>Sign out successful</h1></div><p>Born 1810</p>', PUBLIC),
        # ...but a visible sign-out link is.
        ("https://www.findagrave.com/memorial/1/x",
         '<nav><a href="/logout">Sign out</a></nav><p>Born 1810</p>', GATED),
        # A free site's script bundle naming its logout route is not the page.
        ("https://trove.nla.gov.au/newspaper/article/1", _SCRIPT_ONLY, PUBLIC),
        ("https://trove.nla.gov.au/newspaper/article/1",
         "<!-- <a>Log out</a> --><style>.logout{}</style><p>text</p>", PUBLIC),
        # An unknown site keeps the cautious rule: script text still counts.
        ("https://example.org/record/1", _SCRIPT_ONLY, GATED),
        ("https://example.org/record/1", "<p>Sign out successful</p>", PUBLIC),
        ("https://example.org/record/1", "<p>No session here</p>", PUBLIC),
        # Subscription and sign-up sites are gated without any English marker.
        ("https://www.newspapers.com/image/123/", "<p>page image</p>", GATED),
        ("https://www.archion.de/de/viewer/?no_cache=1", "<a>Abmelden</a>", GATED),
        ("https://www.familysearch.org/ark:/61903/1:1:XXXX", "<p>record</p>", GATED),
        ("https://search.ancestry.co.uk/cgi-bin/sse.dll?x=1", "<p>record</p>", GATED),
        ("https://rootsireland.ie/data/record.php", "<p>record</p>", GATED),
        # A login URL is gated wherever it is.
        ("https://trove.nla.gov.au/login?next=/x", "<p>text</p>", GATED),
    ],
)
def test_gating(url, html, expected):
    assert detect_gating(url, html) is expected


def test_without_a_catalog_every_site_keeps_the_cautious_rule(monkeypatch):
    """An unreadable catalog never fails a capture; it just knows no site."""
    monkeypatch.setattr(fetch, "_catalog", lambda: ())
    assert detect_gating("https://trove.nla.gov.au/newspaper/article/1", _SCRIPT_ONLY) is GATED
    assert detect_gating("https://www.newspapers.com/image/123/", "<p>x</p>") is PUBLIC
