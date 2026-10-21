"""Ancestry identifiers, and the identity guard (REQ-001 R5).

Every identifier Capsule binds a capture to is DERIVED HERE, deterministically, from a
URL or from the tree's own `_APID` pointer. None is ever reported by the extraction
model — the same rule that governs `external_ref` identifiers: nothing an LLM invented
becomes a key we later bind two sources on.

**Why the guard exists.** During the REQ-001 pilot the researcher read a page they
believed was the 1900 census; it was a School Yearbook entry for a different person. The
cause was benign (one browser window, serial navigation), but the failure mode is not: a
wrong page captured, hashed, and cited onto a direct ancestor is precisely the silent
corruption this project exists to eliminate. The check is nearly free and its absence is
catastrophic, so it ABORTS a capture rather than warning about one.

Nothing here raises. An unparseable input is `None` — and `None`, when an expectation was
set, is a MISMATCH, not a pass: "I could not verify" must never be recorded as "I
verified" (the same principle as the blank-page gate's None-is-not-False).
"""
from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlparse

# "1,7602::56510272" — the pointer that survived the GEDCOM import. Also accepts the
# bare "7602:56510272" pair that the URLs and the ledger use.
_APID_RE = re.compile(r"^\s*(?:\d+\s*,\s*)?(\d+)\s*::?\s*(\d+)\s*$")

# https://www.ancestry.com/search/collections/<db>/records/<rec>
_SEARCH_RE = re.compile(r"/search/collections/(\d+)/records/(\d+)")
# https://www.ancestry.com/discoveryui-content/view/<rec>:<db>
_DISCOVERY_RE = re.compile(r"/discoveryui-content/view/(\d+):(\d+)")
# https://www.ancestry.com/imageviewer/collections/<db>/images/<image-id>
_IMAGE_RE = re.compile(r"/imageviewer/collections/(\d+)/images/([A-Za-z0-9_\-]+)")

# Ancestry's own national domains, NAMED. This was `ancestry\.[a-z.]+$`, which accepted any
# host that merely contains an "ancestry." label: `ancestry.com.evil.example` and
# `ancestry.evil.com` both passed, so a lookalike page could mint record ids that dedup, the
# ledger and the identity guard then trusted. Found 2026-10-21 by the BUG-020 tests; the same
# list the extension's `SIBLING_HOSTS.ancestry` has named since BUG-018.
_ANCESTRY_HOST = re.compile(
    r"(^|\.)ancestry\.(com|ca|de|fr|it|se|mx|co\.uk|com\.au)$", re.IGNORECASE
)

IdPair = Tuple[str, str]  # (collection_id, record_id)


def parse_apid(apid: Optional[str]) -> Optional[IdPair]:
    """`"1,7602::56510272"` -> `("7602", "56510272")`. None if it is not an APID."""
    if not apid or not isinstance(apid, str):
        return None
    match = _APID_RE.match(apid)
    if not match:
        return None
    return match.group(1), match.group(2)


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_ancestry_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    return bool(_ANCESTRY_HOST.search(_host_of(url)))


def _ids_from_record_path(text: str) -> Optional[IdPair]:
    """The `{collection, record}` pair a record-shaped path names, else None.

    Both URL shapes are matched explicitly because Ancestry serves both and they order
    the ids OPPOSITELY — `/collections/<db>/records/<rec>` vs `/view/<rec>:<db>`. A
    generic "find two numbers" rule would compare the right numbers in the wrong slots
    and wave a mismatched page through, which is the one thing this must never do.
    The one shared body for both public matchers, so the shapes can never drift apart.
    """
    match = _SEARCH_RE.search(text)
    if match:
        return match.group(1), match.group(2)

    match = _DISCOVERY_RE.search(text)
    if match:
        return match.group(2), match.group(1)  # the URL is <rec>:<db> — note the swap

    return None


def record_ids_from_url(url: Optional[str]) -> Optional[IdPair]:
    """`(collection_id, record_id)` for an Ancestry RECORD url, else None."""
    if not url or not isinstance(url, str):
        return None
    if not is_ancestry_url(url):
        return None
    return _ids_from_record_path(url)


def record_ids_from_href(href: Optional[str]) -> Optional[IdPair]:
    """`(collection_id, record_id)` for an IN-PAGE record href, else None.

    Unlike `record_ids_from_url` — which vets a full URL the caller navigated to —
    this reads hrefs found inside an already-captured Ancestry page (REQ-003's
    Household Members links), which may be site-relative. Any href that NAMES A
    HOST must name an Ancestry host: a foreign host that mimics the path shape
    must never mint an ID we later bind records on. Host presence is decided by
    `urlparse` — not a `"://"` substring test, which a protocol-relative
    `//evil.example/...` href slips straight past (review finding #2), and which
    misfires on a relative path whose query merely contains a URL (#7).
    """
    if not href or not isinstance(href, str):
        return None
    try:
        has_host = bool(urlparse(href).netloc)
    except ValueError:
        return None  # cannot even parse it — refuse, never guess
    if has_host and not is_ancestry_url(href):
        return None
    return _ids_from_record_path(href)


def image_id_from_url(url: Optional[str]) -> Optional[str]:
    """The image id (e.g. `4114737_00288`) from an image-viewer URL, else None.

    This identifier names the physical scan — the one thing that lets a future
    researcher find this exact page again once Ancestry is gone.
    """
    if not url or not isinstance(url, str):
        return None
    if not is_ancestry_url(url):
        return None
    match = _IMAGE_RE.search(url)
    return match.group(2) if match else None


def image_id_from_html(html: Optional[str]) -> Optional[str]:
    """The record's image id, if the PAGE ITSELF says it has one (REQ-001 R4.1).

    An image-backed record links to its scan — `/imageviewer/collections/7602/images/
    4114737_00288`. An index-only record (the Social Security Death Index, a birth index)
    carries no such link, because there is no image behind it.

    That makes "does this record have an image?" a FACT about the record, discoverable from
    the page — not the caller's expectation. BUG-004: we trusted the caller's
    `image_intent`, so an index-only record could be filed as `failed` (jamming the queue
    forever, chasing a scan that does not exist) or — worse — closed as mined with no image
    status at all, losing the knowledge that the SS-5 is the real source.

    Derived by pattern, never by the model: this id is bound into the manifest, and no
    identifier we bind on may be something an LLM invented.
    """
    if not html or not isinstance(html, str):
        return None
    match = _IMAGE_RE.search(html)
    return match.group(2) if match else None


def has_record_image(html: Optional[str]) -> bool:
    """Does the record page itself offer a scan? See `image_id_from_html`."""
    return image_id_from_html(html) is not None


def identity_mismatch(*, expected_apid: Optional[str], url: Optional[str]) -> Optional[str]:
    """None when `url` really holds `expected_apid`'s record; a REASON when it does not.

    A returned reason means: DO NOT CAPTURE. Not "capture with a warning" — a warned
    capture still lands, and a wrong record on disk, hashed and cited to an ancestor, is
    worse than no record at all, because nobody re-checks a capture that succeeded.

    An unparseable URL or a malformed expectation is a MISMATCH: we cannot confirm the
    page holds the record that was asked for, and an unconfirmed identity is exactly what
    this exists to refuse.
    """
    if expected_apid is None:
        return None  # no expectation declared — nothing to guard

    want = parse_apid(expected_apid)
    if want is None:
        return f"cannot verify identity: {expected_apid!r} is not a readable _APID"

    got = record_ids_from_url(url)
    if got is None:
        return (
            f"cannot verify identity: expected {want[0]}:{want[1]}, but "
            f"{url!r} is not a readable Ancestry record URL"
        )

    if got != want:
        return (
            f"IDENTITY MISMATCH: expected {want[0]}:{want[1]}, "
            f"but this page is {got[0]}:{got[1]}"
        )
    return None
