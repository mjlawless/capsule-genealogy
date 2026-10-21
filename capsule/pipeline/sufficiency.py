"""Does this capture actually HOLD the record it claims to? (REQ-007 §3.3)

`verify_capture` answers a different question — *are these the bytes we recorded?* — and it
answered YES twice on Archion captures that archived a browser print of site chrome plus a
689x545 viewer preview, named no page of the register, and carried `assertion_count: 0`.
Every hash was correct. They were the hashes of the wrong payload. `CLAUDE.md` §12.4: a
checksum that verifies against garbage.

Integrity cannot see that, and no amount of hashing ever will. So there is a second question:
**is the payload the record?**

It is asked of exactly ONE class of capture, and the scoping IS the design:

    no assertions AND no provider claims
    AND (no coverage claims  OR  the source is a known tile viewer)

Read the first line as: nothing about a PERSON could be turned into structured evidence, so
whatever the capture is worth has to be in the image. There is no text layer to fall back
on, and if the image is a preview then the capture holds nothing whatsoever.

The second line is the part the corpus forced, and it is worth the words. Coverage claims are
text evidence — a RootsIreland holdings page yields 140 of them, no assertions and no image,
and is a perfectly good capture; failing it would be a serious false FAIL. But an Archion
register page ALSO yields a handful (4), parsed out of the volume title, while holding no
record at all — so letting coverage exempt a capture outright let both captures this REQ was
filed about walk straight past the gate. Measured, not guessed: sweeping the corpus with
coverage exempting showed the 2026-07-25 and 2026-08-19 Archion captures passing again.

So for a source whose record IS an image by construction, coverage does not exempt. That is
decided by HOST (`TILE_VIEWER_HOSTS`), which is a fact about the site and cannot drift.

THIS USED TO REQUIRE `source_quality == "original"`, and the live Archion run on
2026-08-22 is why it no longer does. The same register captured on 2026-08-19 was labelled
`original` and on 2026-08-22 `derivative` — the extractor's judgment moved between two
captures of one source. Scoping a safety gate on a signal that moves means the gate is
absent exactly when the model happens to say "derivative", which is the case it was built
for. The class is now decided by what the extraction PRODUCED, which is a fact about this
capture and cannot drift.

Why not a one-line `assertion_count == 0`: that RootsIreland holdings page is the whole
reason. Provider claims are the provider's own description of the source and count as text
evidence unconditionally; coverage counts except on a tile-viewer host, per above.

And BUG-006 still stands: a bare pixel count cannot tell "small document, fully scanned"
from "big document, downscaled". Inside this class it does not have to. A capture that
produced no evidence and holds no readable image is empty on any reading of it — and
`ledger.mined` already refuses to close such a record, so this only makes the folder-level
verdict agree with the ledger rather than contradict it.

FAILING CLOSED IS DELIBERATE. A raster this cannot measure does not satisfy the floor.
"Cannot judge" is never "is fine" — that is the BUG-001 lesson, and here the cost of a false
PASS is a register page nobody ever goes back for, because the tool said it was banked.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence
from urllib.parse import urlsplit

from capsule.pipeline.image_check import RECORD_IMAGE_FLOOR_PX, measure_record_file

_logger = logging.getLogger(__name__)

# Two artifacts that can never BE the record for this class, at any pixel count, because
# of what they are rather than how big they are:
#
#   `render:page-pdf`  — a browser print. The scan lives in a tiled canvas, so the print
#     gets the on-screen preview and the site chrome around it. REQ-007 §2 exists precisely
#     because that artifact keeps being mistaken for the evidence.
#   `viewer:canvas`    — a screen-resolution grab, degraded BY CONSTRUCTION (BUG-006). It
#     was originally allowed through this floor on the reasoning that a big canvas is at
#     least a real image of the record. That was wrong, and every other gate in the system
#     already said so: `ledger._COMPLETE_IMAGE` and `dedup._TERMINAL_GOOD` both exclude
#     `degraded-canvas`, and `image_warning` tells the operator in as many words that the
#     record is NOT mined and to go get the real download. `verify_ok: true` over the top of
#     that is a contradiction, and on a high-DPI display a viewer canvas clears 1500px
#     routinely — so the exemption would have quietly rebuilt the very outcome this gate
#     exists to prevent.
#
# Excluding by PROVENANCE rather than by size is the BUG-006 call: what the bytes ARE is a
# stronger signal than how many of them there are.
#
# This costs nothing in recall. When a record page really does show its scan inline at full
# resolution, the in-page media sweep banks that <img> as its own item, and THAT item is
# measured here — so the floor is satisfied by the image itself, never by the print of it.
_NOT_THE_RECORD = ("render:page-pdf", "viewer:canvas")

def longest_raster_edge(
    data: bytes, *, ext: str = "", mime: str = ""
) -> Optional[int]:
    """Longest edge in pixels of the largest raster in `data`, whether image or PDF.

    One measurement path, shared with the record-image classifier: this gate and
    `image_status` must never be able to disagree about how big the same file is.

    None when the bytes cannot be measured at all. Callers must treat None as "unknown",
    and unknown must never satisfy a floor.
    """
    facts = measure_record_file(data, ext=ext, mime=mime)
    return facts.long_side if facts else None


# Sources whose record IS an image by construction: a tiled viewer behind a login, where the
# page text is site chrome and the evidence is only ever in the scan. REQ-007 §6 calls this a
# source profile; this is its smallest useful form — a host set, not a selector, so it cannot
# drift the way a layout does and cannot move the way a model judgment does.
#
# Membership is a claim about the SITE, not about any capture, and it only ever makes the
# gate stricter: it stops coverage claims parsed out of a volume title from exempting a
# capture that holds no register page. Adding a host here is safe; a site whose records are
# text must NOT be added, because that would fail its perfectly good text captures.
TILE_VIEWER_HOSTS = frozenset(
    {
        "archion.de",
        "registers.nli.ie",
        "matricula-online.eu",
        "data.matricula-online.eu",
    }
)


def is_tile_viewer_source(url: Optional[str]) -> bool:
    """Is this URL on a source whose record is an image? Suffix match, never over-matching."""
    try:
        host = urlsplit(url or "").hostname or ""
    except ValueError:
        return False
    host = host.lower()
    return any(host == h or host.endswith("." + h) for h in TILE_VIEWER_HOSTS)


def yielded_no_text_evidence(
    *,
    assertion_count: int,
    coverage_count: int = 0,
    provider_claim_count: int = 0,
    url: Optional[str] = None,
) -> bool:
    """The class this gate applies to — see the module docstring for the full argument."""
    if assertion_count != 0 or provider_claim_count != 0:
        return False
    return coverage_count == 0 or is_tile_viewer_source(url)


def record_media_shortfall(
    *,
    assertion_count: int,
    coverage_count: int = 0,
    provider_claim_count: int = 0,
    url: Optional[str] = None,
    media: Sequence[tuple[str, Optional[str], Optional[int]]],
    floor_px: int = RECORD_IMAGE_FLOOR_PX,
) -> Optional[str]:
    """Why this capture does not hold its record, or None when it does (or is out of class).

    `media` is `(path, provenance, longest_raster_edge_px)` per item, already measured by
    the caller — this function does no I/O so it can be reasoned about and tested directly.
    """
    if not yielded_no_text_evidence(
        assertion_count=assertion_count,
        coverage_count=coverage_count,
        provider_claim_count=provider_claim_count,
        url=url,
    ):
        return None

    best_path, best_px = None, 0
    for path, provenance, longest in media:
        if any((provenance or "").startswith(p) for p in _NOT_THE_RECORD):
            continue
        if longest is not None and longest > best_px:
            best_path, best_px = path, longest
    if best_px >= floor_px:
        return None

    # Say what was actually held, not merely that something failed: "no record image at all"
    # and "a 689x545 preview" call for different work by the researcher, and a verdict that
    # cannot tell them apart gets read as noise and then ignored.
    held = (
        f"the largest is {best_path} at {best_px}px on the long edge"
        if best_path
        else "it holds no measurable image at all"
    )
    return (
        f"this capture read no record text at all, so the image IS the evidence — but "
        f"{held}, under the {floor_px}px floor. It archives no record. (REQ-007 §3.3)"
    )
