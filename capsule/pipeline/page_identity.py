"""What a tile-viewer page STATES about itself — which leaf, and where it sits.

REQ-007 §1 (page identity) and §4 (the archival trail behind the citation). Both are
structural facts the page publishes about the document, not readings of the record, so
both are parsed deterministically and never asked of a model.

== §1: WHICH page of the register is this?

A tile viewer deep-links by internal state, not by URL: every one of the 173 images of
Archion register 239254 is served at the same address. Three captures on disk prove the
cost — all three name the same URL, none says which leaf it holds, and two of them hold no
leaf at all. **A citation that cannot name the page is not a citation.**

The page selector is the answer and it was in the DOM the whole time:

    <select class="page-select form-select">
      <option selected="" value="0">2</option>   <-- the image in view
      <option value="1">3</option>
      ...

This is a DETERMINISTIC parser, never model output — the same call REQ-003 made for the
household block. Which page was on screen is a structural fact about the document, not a
reading of the record, so there is nothing for a model to judge and everything for it to
get subtly wrong. The no-CSS-selectors rule protects EXTRACTION from layout drift; the
2026-07-14 PM ruling already allows a viewer adapter to locate things structurally.

Scoped to `TILE_VIEWER_HOSTS`, because a `<select>` on an arbitrary page is a form control,
not a page selector. Off those hosts this returns None rather than guessing, and an
unrecognized viewer falls through exactly as `probeViewer` does.

WHAT THE REAL SNAPSHOTS TAUGHT, and neither was in the spec:

  * The option text is the bare number ("2"), NOT the "Image 174" the spec described. The
    site renders "Image 2" in its thumbnail rail and "2 / 173" beside the select; the
    element itself holds only the digits. Stored verbatim, because that is what the source
    says — formatting it into "Image 2" is the citation layer's business (§4), and a parser
    that invents a word the page never showed is the wrong place to be creative.
  * The option's text can be WRAPPED IN MARKUP. The 2026-08-19 snapshot has it inside two
    nested `<font dir="auto" style="vertical-align: inherit;">` tags — Google Translate
    rewriting the DOM in the researcher's browser before the snapshot was taken. Anything
    reading `>([^<]*)<` would have come back empty on that capture. Tags are stripped, so a
    translated page still identifies its own leaf.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from capsule.pipeline.ancestry import is_ancestry_url
from capsule.pipeline.sufficiency import TILE_VIEWER_HOSTS, is_tile_viewer_source

_logger = logging.getLogger(__name__)

#: Longest label worth keeping. A page identifier is a handful of characters; anything
#: longer means we matched the wrong control, and a paragraph in `source.page_label` is
#: worse than no label at all.
MAX_LABEL_CHARS = 60

_SELECT_RE = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.IGNORECASE | re.DOTALL)
_OPTION_RE = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.IGNORECASE | re.DOTALL)
_SELECTED_RE = re.compile(r"\bselected\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
#: The viewer's own class hook. A hint that ranks candidates, never a requirement — if
#: Archion renames it, the "most options wins" fallback still finds the page selector.
_PAGE_SELECT_HINT = "page-select"


def _text_of(fragment: str) -> str:
    """Visible text of an HTML fragment: tags stripped, entities resolved, space collapsed."""
    return " ".join(html_lib.unescape(_TAG_RE.sub("", fragment)).split())


def _selected_option_text(options_html: str) -> Optional[str]:
    """The text of the option carrying `selected`, or the first option as the DOM default."""
    options = _OPTION_RE.findall(options_html)
    if not options:
        return None
    for attrs, inner in options:
        if _SELECTED_RE.search(attrs):
            return _text_of(inner)
    # No explicit `selected`: a browser shows the first option, so that IS the page in view.
    return _text_of(options[0][1])


def page_label_from(html: Optional[str], url: Optional[str]) -> Optional[str]:
    """The source's own identifier for the image in view, verbatim. None if unknowable.

    None is a real answer and the common one — it means "this source has no page selector
    we recognize", which must never be confused with "this capture is page 1".
    """
    if not html or not is_tile_viewer_source(url):
        return None

    best: Optional[str] = None
    best_rank: tuple[int, int] = (0, 0)
    for attrs, body in _SELECT_RE.findall(html):
        options = _OPTION_RE.findall(body)
        if len(options) < 2:
            continue  # a one-option select identifies nothing
        # Rank: the viewer's own class hook first, then the largest option list. A register
        # page selector has 172 entries; a language or sort dropdown has a handful.
        rank = (1 if _PAGE_SELECT_HINT in attrs.lower() else 0, len(options))
        if rank <= best_rank:
            continue
        label = _selected_option_text(body)
        if label and len(label) <= MAX_LABEL_CHARS:
            best, best_rank = label, rank

    if best is None:
        _logger.debug("no page selector recognized for %s", url)
    return best


# -- §4: where this document sits, in the source's own words -----------------


@dataclass(frozen=True)
class ArchiveHierarchy:
    """The archival trail a viewer page publishes above the document it is showing.

    Archion prints it as a breadcrumb, and it decomposes exactly as a citation needs:

        All Archives / Hessen / Zentralarchiv der Evangelischen Kirche in Hessen und
        Nassau / Dekanat Oppenheim / Guntersblum / <volume title> / Back

    `trail` is every step VERBATIM with the navigation chrome removed. The named parts are
    read FROM THE END, which is the stable direction: an archive that adds a level above
    (a country, a collection) shifts every position counted from the front and none counted
    from the back. A trail too short to support a name yields None for it rather than a
    guess — a citation that names the wrong custodian is worse than one that names none.
    """

    trail: tuple[str, ...]

    @property
    def volume(self) -> Optional[str]:
        """The document itself — the register volume."""
        return self.trail[-1] if len(self.trail) >= 1 else None

    @property
    def parish(self) -> Optional[str]:
        """Whose records these are: the congregation that kept the volume."""
        return self.trail[-2] if len(self.trail) >= 2 else None

    @property
    def district(self) -> Optional[str]:
        """The deanery/district, which disambiguates a common parish name."""
        return self.trail[-3] if len(self.trail) >= 3 else None

    @property
    def custodian(self) -> Optional[str]:
        """WHO HOLDS THE ORIGINAL — the archive, not the website.

        This is the whole point of §4. Archion is the access path; the Darmstadt archive is
        where the register physically is, and a citation that stops at the website tells a
        future researcher nothing about where to go when the website is gone.
        """
        return self.trail[-4] if len(self.trail) >= 4 else None


_BREADCRUMB_RE = re.compile(
    r"<(?P<tag>nav|ol|ul|div)(?=[ >])[^>]*(?:breadcrumb|crumb)[^>]*>"
    r"(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_RE = re.compile(r"<a(?=[ >])[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
#: Navigation, not archival structure. "All Archives" is the site root and "Back" is a
#: control that happens to live in the same element — neither describes the document.
_NAV_CRUMBS = frozenset({"all archives", "alle archive", "back", "zurück", "home", "start"})
#: A breadcrumb step longer than this is a sentence, not a place — we matched the wrong
#: element. Volume titles run long, so this is generous.
MAX_CRUMB_CHARS = 300


def archive_hierarchy_from(
    html: Optional[str], url: Optional[str]
) -> Optional[ArchiveHierarchy]:
    """The archival trail this page publishes, or None if it publishes none.

    Host-scoped like `page_label_from`: a `<div class="breadcrumb">` on an arbitrary site is
    site navigation, and reading it as provenance would put "Home > Search > Results" into a
    citation.
    """
    if not html or not is_tile_viewer_source(url):
        return None
    match = _BREADCRUMB_RE.search(html)
    if not match:
        return None
    steps = []
    for anchor in _ANCHOR_RE.findall(match.group("body")):
        text = _text_of(anchor)
        if not text or len(text) > MAX_CRUMB_CHARS:
            continue
        if text.strip().lower() in _NAV_CRUMBS:
            continue
        steps.append(text)
    return ArchiveHierarchy(trail=tuple(steps)) if steps else None


# -- BUG-020: an Ancestry BROWSE image — the path the researcher browsed to reach it ------


@dataclass(frozen=True)
class AncestryBrowsePath:
    """Where an Ancestry browse image sits: the collection and the path down to the leaf.

    A browse image is a scan reached by browsing a collection (county → volume → image), with
    no indexed record behind it, so there are no persons to cite from. Ancestry's viewer
    prints the path as a breadcrumb of `browsePath-N` inputs and a "63 of 695" counter, and
    that path is exactly what Evidence Explained cites a browse image by:

        "Pennsylvania, U.S., Wills and Probate Records, 1683-1993," digital images,
        Ancestry (… : accessed …), Fayette > Orphans Court Records, Vol 1-3, 1783-1844 >
        image 63 of 695.

    Every part is verbatim from the page; `image`/`image_count` are None rather than a guess.
    """

    trail: tuple[str, ...]
    image: Optional[int] = None
    image_count: Optional[int] = None


#: An image-viewer URL: `/imageviewer/collections/<db>/images/<image id>`.
_ANCESTRY_IMAGE_PATH_RE = re.compile(r"^/imageviewer/collections/\d+/images/([^/?#]+)")
_INPUT_RE = re.compile(r"<input(?=[\s/>])[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_BROWSE_NAME_RE = re.compile(r"^browsePath-(\d{1,2})$")
_IMAGE_COUNT_RE = re.compile(
    r'class="[^"]*\bimageCountText\b[^"]*"[^>]*>\s*(\d{1,6})\s*<', re.IGNORECASE
)
#: The image number an image id ends with: `005537869_00063` -> 63.
_TRAILING_NUMBER_RE = re.compile(r"[_-](\d{1,6})$")


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): html_lib.unescape(v) for k, v in _ATTR_RE.findall(tag)}


def ancestry_browse_path_from(
    html: Optional[str], url: Optional[str]
) -> Optional[AncestryBrowsePath]:
    """The browse path an Ancestry image-viewer page publishes, or None.

    Ancestry-scoped and image-viewer-scoped: a `browsePath` input anywhere else is not this
    breadcrumb. The image number is taken from the viewer's page counter only when it agrees
    with the number the URL's image id ends in. The URL is the live identity of the leaf (the
    viewer updates it on every navigation), while a serialized `value` attribute can lag
    behind the screen (REQ-007 §1 learned that on Archion), and a citation that names the
    wrong image is worse than one that names none.
    """
    if not html or not url or not is_ancestry_url(url):
        return None
    match = _ANCESTRY_IMAGE_PATH_RE.match(urlparse(url).path)
    if not match:
        return None

    steps: dict[int, str] = {}
    page_input: Optional[str] = None
    for tag in _INPUT_RE.findall(html):
        attrs = _attrs(tag)
        name = _BROWSE_NAME_RE.match(attrs.get("name", ""))
        if name:
            value = " ".join(attrs.get("value", "").split())
            if value and len(value) <= MAX_CRUMB_CHARS:
                steps.setdefault(int(name.group(1)), value)
        elif "page-input" in attrs.get("class", "").split():
            page_input = attrs.get("value", "").strip()
    if not steps:
        return None
    # Only an unbroken run from level 0: a gap means we did not read the path we think.
    trail: list[str] = []
    for level in range(len(steps)):
        if level not in steps:
            break
        trail.append(steps[level])

    count_match = _IMAGE_COUNT_RE.search(html)
    image_count = int(count_match.group(1)) if count_match else None
    url_number = _TRAILING_NUMBER_RE.search(match.group(1))
    image: Optional[int] = None
    if page_input and page_input.isdigit() and url_number:
        if int(page_input) == int(url_number.group(1)):
            image = int(page_input)
    if image is not None and image_count is not None and image > image_count:
        image = image_count = None
    return AncestryBrowsePath(trail=tuple(trail), image=image, image_count=image_count)


# -- BUG-019: whose LIVE page label is trusted ------------------------------------------

#: Hosts where the extension's live page label is the viewer's own page selector. The tile
#: viewers, plus Riksarkivet's `bildvisning`, whose selector reads "Bild 40 / Sida 63".
#: Riksarkivet is deliberately NOT a `TILE_VIEWER_HOSTS` member: its saved snapshot keeps the
#: FIRST leaf selected, so `page_label_from` would label every capture "Bild 1", and its URLs
#: already name the leaf. Only the label read live from the screen is trusted there.
LIVE_LABEL_HOSTS = TILE_VIEWER_HOSTS | frozenset({"sok.riksarkivet.se"})

#: Hosts whose label is not a page identifier but is, for now, the only thing telling apart
#: different records served at ONE URL. RootsIreland's search results all live at
#: `…/quis.php?page=0&…`; the search-form parish the extension picks up is what keeps five
#: different records there from being refused as one. Dropping it would turn a false "new"
#: into a false block, the evidence-losing direction. Kept until RootsIreland has a real
#: record identity (BUG-019; the subscription expired 2026-09-21, so this is corpus-reasoned).
LEGACY_DISCRIMINATOR_HOSTS = frozenset({"rootsireland.ie"})


def _on(url: Optional[str], hosts: frozenset[str]) -> bool:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in hosts)


def accepted_page_label(
    url: Optional[str], label: Optional[str], *, expected_apid: Optional[str] = None
) -> Optional[str]:
    """The page label a capture request may keep, or None (BUG-019).

    The extension reads a label from the biggest `<select>` on ANY page, and the backend used
    to trust it, so "Select Language" (NYC), "Cemetery office has no record of this person"
    (Find a Grave) and "Fayette" (an Ancestry county dropdown) landed in `source.page_label`
    and in the dedup key. The backend is the trust boundary and decides here, which also
    covers every extension already installed:

    - an Ancestry request naming its record (`expected_apid`) keeps its label: that is the
      one-call flow naming a record's extra image (REQ-009);
    - a tile viewer or Riksarkivet keeps it: there it IS the leaf in view;
    - RootsIreland keeps it as a legacy discriminator (see `LEGACY_DISCRIMINATOR_HOSTS`);
    - everything else drops it.
    """
    if not label:
        return None
    if expected_apid:
        return label
    if _on(url, LIVE_LABEL_HOSTS) or _on(url, LEGACY_DISCRIMINATOR_HOSTS):
        return label
    return None
