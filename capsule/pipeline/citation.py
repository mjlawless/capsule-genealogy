"""Deterministic Evidence-Explained-style citation, no model call.

M0 keeps the whole capture to exactly one Claude call (the extraction), so the
citation is composed here from the extraction by a fixed template, not by the
model. It emits the three EE layers — a full reference note, a short note, and
a source-list entry — and records the access date and archived location, as
the plan requires. `template_id` versions the wording so a later, richer
template never silently rewrites existing citations.

Two templates:

- `record-transcript-v1` — for a page that IS a record transcript (the
  extraction has assertions). It cites the RECORD, layered as Evidence Explained
  demands: the record (who/what/when/where) -> the database that served it ->
  the original the database itself cites (from `external_refs`). Every element is
  already in the extraction, so no second model call is needed.
- `generic-web-v1` — the fallback for a page with no transcribable record (a
  document capture, an unindexed scan). It cites the page by its `<title>`,
  which is all there is to cite.

The old default was to cite every page by its `<title>`; on a database like
RootsIreland that title is the site's boilerplate ("Birth Death Marriage
Genealogy Records Ireland ...") — identical across every record and naming
neither the person nor the record. `record-transcript-v1` fixes that.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional, Sequence
from urllib.parse import urlparse

from capsule.contracts.evidence import (
    Assertion,
    Citation,
    ExternalRef,
    Persona,
    ProviderClaim,
)

_TEMPLATE_GENERIC = "generic-web-v1"
_TEMPLATE_RECORD = "record-transcript-v1"
_TEMPLATE_REGISTER = "register-image-v1"
_TEMPLATE_ANCESTRY_BROWSE = "ancestry-browse-image-v1"

#: Ancestry's `<title>` is "Ancestry.com - <collection title>"; the prefix is the site.
_ANCESTRY_TITLE_PREFIX = re.compile(r"^\s*Ancestry(?:\.[a-z.]+)?\s+-\s+", re.IGNORECASE)

#: Roles that make a persona a *relative or witness* of the record's subject, not
#: the subject. Everything else (child, husband, wife, deceased, grantor, ...) is a
#: principal — the person(s) the record is ABOUT and whom the citation names.
_RELATIVE_ROLE_RE = re.compile(
    r"father|mother|parent|sponsor|informant|witness|bondsman|deponent",
    re.IGNORECASE,
)

#: Friendly names for the authorities an external ref can name, so the "citing"
#: layer reads as a citation and not a slug. Unknown authorities pass through.
_AUTHORITY_NAMES = {
    "nli-registers": "National Library of Ireland, Catholic parish register microfilm",
}


def _site_of(snapshot: "object") -> str:
    site = getattr(snapshot, "site_name", None)
    if site:
        return site
    netloc = urlparse(getattr(snapshot, "url", "")).netloc
    return netloc[4:] if netloc.startswith("www.") else netloc or "Unknown source"


def _title_of(snapshot: "object") -> str:
    return getattr(snapshot, "title", None) or getattr(snapshot, "url", "") or "Untitled"


def _access(access_date: date) -> str:
    """EE-style access date, e.g. `8 July 2026` (day is unpadded)."""
    return f"{access_date.day} {access_date:%B %Y}"


def _principals(personas: Sequence[Persona]) -> list[Persona]:
    """The persona(s) the record is ABOUT — subjects, not relatives/witnesses."""
    principals = [p for p in personas if not _RELATIVE_ROLE_RE.search(p.role or "")]
    return principals or list(personas)  # roles unclear -> name everyone


def _field_value(assertions: Sequence[Assertion], *keywords: str) -> Optional[str]:
    """The verbatim value of the first assertion whose field mentions a keyword.

    Matches against both the record's own label (`field`) and our synonym
    (`normalized_field`), so "date_of_marriage" and a normalized "marriage_date"
    both answer to `"date"`. Returns the value as printed on the record.
    """
    for a in assertions:
        haystack = f"{a.field} {a.normalized_field or ''}".lower()
        if any(k in haystack for k in keywords):
            return a.verbatim_value
    return None


def _record_subject(
    record_type: str,
    personas: Sequence[Persona],
    assertions: Sequence[Assertion],
) -> str:
    """The record layer: `<type> of <principals>, <date>, <place>` (as available)."""
    subject = " and ".join(p.label for p in _principals(personas))
    parts = [f"{record_type} of {subject}" if subject else record_type]
    when = _field_value(assertions, "date")
    if when:
        parts.append(when)
    parish = _field_value(assertions, "parish", "district", "church", "congregation")
    county = _field_value(assertions, "county")
    place = ", ".join(x for x in (parish, county) if x)
    if place:
        parts.append(place)
    return ", ".join(parts)


def _citing_layer(external_refs: Sequence[ExternalRef]) -> str:
    """The 'what the database itself cites' layer, from the first external ref."""
    if not external_refs:
        return ""
    ref = external_refs[0]
    authority = _AUTHORITY_NAMES.get(ref.authority, ref.authority)
    locator = f", {ref.locator}" if ref.locator else ""
    return f"; citing {authority}, {ref.identifier}{locator} ({ref.url})"


#: The provider's own description of the ORIGINAL, in order of preference. `original_data`
#: names the microfilm the database was made from ("...NARA microfilm T9, 1,454 rolls");
#: `source_citation` gives the roll/page/ED. Either turns a derivative citation into a
#: layered one.
_CITING_CLAIM_KEYS = ("original_data", "source_citation")


def _provider_citing(provider_claims: Sequence[ProviderClaim]) -> str:
    """The 'citing the original' layer from the PROVIDER's own description (BUG-014).

    Ancestry extracts the NARA microfilm / roll / ED into `provider_claims`, but the
    citation used to name only the database — so the microfilm never reached the reference
    note and the researcher re-transcribed it by hand. Fold it in, verbatim (the provider's
    words are evidence, not ours to reword), preferring `original_data` (the microfilm) over
    `source_citation` (the roll/ED). Returns "" when the page carried no such block.
    """
    by_key = {p.key: p.verbatim_value for p in provider_claims}
    for key in _CITING_CLAIM_KEYS:
        value = (by_key.get(key) or "").strip()
        if value:
            # The reference note supplies the closing period; drop the block's own so a
            # value ending in "…Washington, D.C." does not read "D.C..".
            return f"; citing {value.rstrip('.')}"
    return ""


def _build_record_transcript(
    *,
    citation_id: str,
    source_id: str,
    snapshot: "object",
    access_date: date,
    archived_url: str,
    record_type: str,
    personas: Sequence[Persona],
    assertions: Sequence[Assertion],
    external_refs: Sequence[ExternalRef],
    provider_claims: Sequence[ProviderClaim] = (),
) -> Citation:
    site = _site_of(snapshot)
    url = getattr(snapshot, "url", "")
    subject = _record_subject(record_type, personas, assertions)
    # Prefer the database's OWN external citation (a resolvable link to the original); fall
    # back to the provider's `Original data`/`Source Citation` text (BUG-014) so the note
    # names the microfilm even when there is no external ref — which is the Ancestry norm.
    citing = _citing_layer(external_refs) or _provider_citing(provider_claims)

    # record -> database -> what the database cites.
    reference_note = (
        f"{subject}; database entry, {site} "
        f"({url} : accessed {_access(access_date)}){citing}."
    )
    short_note = f"{subject}, {site}."
    source_list_entry = f'{site}. "{subject}." {url}.'

    return Citation(
        id=citation_id,
        source_id=source_id,
        reference_note=reference_note,
        short_note=short_note,
        source_list_entry=source_list_entry,
        access_date=access_date,
        archived_url=archived_url,
        template_id=_TEMPLATE_RECORD,
    )


def _build_register_image(
    *,
    citation_id: str,
    source_id: str,
    snapshot: "object",
    access_date: date,
    archived_url: str,
    hierarchy: "object",
    page_label: Optional[str],
) -> Citation:
    """A page of a register held in an archive and shown through a viewer (REQ-007 §4).

    `generic-web-v1` cited these as a web page, which on Archion produced the raw `<title>`
    with the site name repeated twice and no page at all:

        Kirchenbücher online mit ARCHION, "Baptismal registers 1798-1824, ... | Guntersblum
        | Deanery of Oppenheim | Central Archive ... | Hessen: Church records online with
        ARCHION" (…?cHash=… : accessed 19 August 2026).

    Everything a real citation needs was already on the page, in the breadcrumb above the
    image, and none of it was used.

    THE `citing` CLAUSE IS THE POINT. Archion is the access path; the register itself is a
    physical book in Darmstadt. A citation that stops at the website tells a future
    researcher nothing about where to go when the website is gone — and REQ-001's constraint
    4 ("assume everything will be walled off") says to expect exactly that. This is the same
    layering `record-transcript-v1` does for Ancestry's "citing NARA microfilm…", applied to
    a source that has no persons to name a subject from.

    Every part is VERBATIM from the page. The only words this adds are structural — the
    parentheses, "digital images", "accessed", "citing", and "Image" (which is the site's own
    caption for a leaf, printed beside its thumbnails). It does not invent "Evangelische
    Kirche" or a "Kr." abbreviation that the page never showed.
    """
    site = _site_of(snapshot)
    url = getattr(snapshot, "url", "")
    parish = getattr(hierarchy, "parish", None)
    district = getattr(hierarchy, "district", None)
    volume = getattr(hierarchy, "volume", None)
    custodian = getattr(hierarchy, "custodian", None)

    # The creator: whose records these are, disambiguated by the district when the page
    # gives one. A bare "Guntersblum" is ambiguous across German states.
    creator = f"{parish} ({district})" if parish and district else (parish or site)
    # No comma: the quoted title already ends with one inside the quotes, and Evidence
    # Explained puts the page reference straight after it — `"Title," Image 6;` — not
    # `"Title,", Image 6;`.
    leaf = f" Image {page_label}" if page_label else ""
    citing = f"; citing {custodian}" if custodian else ""
    titled = f' "{volume},"' if volume else ""

    reference_note = (
        f"{creator},{titled}{leaf}; digital images, {site} "
        f"({archived_url or url} : accessed {_access(access_date)}){citing}."
    )
    short_note = f"{parish or site},{titled}{leaf}."
    source_list_entry = (
        f"{creator}." + (f' "{volume}."' if volume else "")
        + f" Digital images. {site}. {url}."
        + (f" Citing {custodian}." if custodian else "")
    )

    return Citation(
        id=citation_id,
        source_id=source_id,
        reference_note=reference_note,
        short_note=short_note,
        source_list_entry=source_list_entry,
        access_date=access_date,
        archived_url=archived_url,
        template_id=_TEMPLATE_REGISTER,
    )


def _build_ancestry_browse_image(
    *,
    citation_id: str,
    source_id: str,
    snapshot: "object",
    access_date: date,
    archived_url: str,
    browse: "object",
) -> Citation:
    """An Ancestry browse image: a scan with no indexed record, cited by its browse path (BUG-020).

    `generic-web-v1` cited these by the collection title alone, which cannot find the page
    again: "Pennsylvania, U.S., Wills and Probate Records, 1683-1993" holds hundreds of
    thousands of images. The viewer prints the path the researcher browsed (county, then
    volume) and an "N of M" counter, which is exactly how Evidence Explained cites a browse
    image. Every part is verbatim; the only words added are structural (`digital images`,
    `accessed`, `image … of …`) and the `>` path separator.
    """
    site = _site_of(snapshot)
    url = getattr(snapshot, "url", "")
    collection = _ANCESTRY_TITLE_PREFIX.sub("", _title_of(snapshot)).strip() or _title_of(snapshot)
    trail = tuple(getattr(browse, "trail", ()))
    image = getattr(browse, "image", None)
    count = getattr(browse, "image_count", None)
    leaf = ""
    if image is not None:
        leaf = f" > image {image} of {count}" if count is not None else f" > image {image}"
    path = " > ".join(trail) + leaf

    reference_note = (
        f'"{collection}," digital images, {site} '
        f"({archived_url or url} : accessed {_access(access_date)}), {path}."
    )
    short_note = f'"{collection}," {path}.'
    source_list_entry = f'"{collection}." Digital images. {site}. {url}.'

    return Citation(
        id=citation_id,
        source_id=source_id,
        reference_note=reference_note,
        short_note=short_note,
        source_list_entry=source_list_entry,
        access_date=access_date,
        archived_url=archived_url,
        template_id=_TEMPLATE_ANCESTRY_BROWSE,
    )


def _build_generic_web(
    *,
    citation_id: str,
    source_id: str,
    snapshot: "object",
    access_date: date,
    archived_url: str,
) -> Citation:
    site = _site_of(snapshot)
    title = _title_of(snapshot)
    url = getattr(snapshot, "url", "")

    reference_note = f'{site}, "{title}" ({url} : accessed {_access(access_date)}).'
    short_note = f'{site}, "{title}."'
    source_list_entry = f'{site}. "{title}." {url}.'

    return Citation(
        id=citation_id,
        source_id=source_id,
        reference_note=reference_note,
        short_note=short_note,
        source_list_entry=source_list_entry,
        access_date=access_date,
        archived_url=archived_url,
        template_id=_TEMPLATE_GENERIC,
    )


def build_citation(
    *,
    citation_id: str,
    source_id: str,
    snapshot: "object",
    access_date: date,
    archived_url: str,
    record_type: Optional[str] = None,
    personas: Sequence[Persona] = (),
    assertions: Sequence[Assertion] = (),
    external_refs: Sequence[ExternalRef] = (),
    provider_claims: Sequence[ProviderClaim] = (),
    hierarchy: "object" = None,
    page_label: Optional[str] = None,
    browse: "object" = None,
) -> Citation:
    """Compose a layered citation.

    When the extraction has a record to cite (`record_type` and at least one
    assertion), use `record-transcript-v1` — it names the person, the record, and
    the original the database cites. Otherwise fall back to `generic-web-v1`, which
    cites the page by its title (a document capture or an unindexed scan has no
    record to name). Loosely typed on `snapshot` to avoid a circular import with
    `fetch`; reads `.site_name`, `.title`, and `.url` only.
    """
    if record_type and assertions:
        return _build_record_transcript(
            citation_id=citation_id,
            source_id=source_id,
            snapshot=snapshot,
            access_date=access_date,
            archived_url=archived_url,
            record_type=record_type,
            personas=personas,
            assertions=assertions,
            external_refs=external_refs,
            provider_claims=provider_claims,
        )
    # A register page shown through a viewer: no persons to name, but a full archival
    # trail published above the image. Cited as the archive's document, not as a web page.
    if hierarchy is not None and getattr(hierarchy, "volume", None):
        return _build_register_image(
            citation_id=citation_id,
            source_id=source_id,
            snapshot=snapshot,
            access_date=access_date,
            archived_url=archived_url,
            hierarchy=hierarchy,
            page_label=page_label,
        )
    # An Ancestry browse image (BUG-020): no record, but the path browsed to reach it.
    if browse is not None and getattr(browse, "trail", ()):
        return _build_ancestry_browse_image(
            citation_id=citation_id,
            source_id=source_id,
            snapshot=snapshot,
            access_date=access_date,
            archived_url=archived_url,
            browse=browse,
        )
    return _build_generic_web(
        citation_id=citation_id,
        source_id=source_id,
        snapshot=snapshot,
        access_date=access_date,
        archived_url=archived_url,
    )
