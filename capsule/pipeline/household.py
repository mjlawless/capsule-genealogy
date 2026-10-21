"""REQ-003 — the record's household members, read from the page's OWN table.

A census page names a whole household, and each member's row in Ancestry's
"Household Members" table links to that member's own record. Those links are the
member record IDs a downstream consumer needs to complete a document in a single
pass — capture the image once, cite it onto every member.

Everything here is DETERMINISTIC parsing of the captured HTML — the same rule as
`ancestry.py`: an identifier we later bind records on is never something a model
reported. And it is strictly scoped: the ONLY subtree read is the element carrying
Ancestry's own `data-is-household="true"` marker. A record page carries two other
clusters of record-shaped links — the "Suggested records" rail and "Neighbors" —
and sweeping those would attach this capture's census image to unrelated records,
the corner-case review's top-ranked defect.

What the real DOM taught us (capture 20260716-ancestrycom-e9f28b27):
- The table shows Name / Age / Relationship only — NO sheet or line numbers. So
  whether a member appears on the captured image is UNKNOWN (a household can spill
  onto the next census sheet), and `image_shared` is None, never a guess. Sheet
  comparison is implemented for table variants that do print the columns.
- The principal's own row is the one WITHOUT a link — a record does not link to
  itself. A name-only member (indexing gap) is also unlinked, so ambiguity is
  resolved by the page's own "Provide alternate for <name>" marker; if it cannot
  be, NO row is marked principal. A guessed principal is a mis-link waiting to
  happen; an unmarked one just keeps the consumer conservative.

Never raises on page input: an unreadable table yields `()`, and a capture must
never fail because a sidecar convenience could not be parsed.
"""
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from capsule.pipeline.ancestry import IdPair, record_ids_from_href, record_ids_from_url

_ANCHOR_RE = re.compile(r'<(?P<tag>[a-z][a-z0-9]*)\b[^>]*\bdata-is-household="true"', re.I)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.I | re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_THEAD_SPLIT_RE = re.compile(r"</thead\s*>", re.I)
#: Content that is OPAQUE to the tag structure — a script/JSON hydration blob can
#: contain the literal substring `<section` inside a string value, which would throw
#: the depth counter in `_balanced_slice` and swallow a sibling "Suggested records"
#: rail into the household block (review finding #1 — reproduced). Stripped, along
#: with styles and comments, before ANY structural scan.
_SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b.*?</style\s*>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
#: The record page names its own subject in the correction UI ("Provide alternate
#: for Caroline Kessler"). The page <title> does NOT carry the name, so this is the
#: one deterministic subject-name source the DOM offers.
_MARKER_RE = re.compile(r"Provide alternate for\s+([^<]{1,120})<", re.I)


@dataclass(frozen=True)
class HouseholdMember:
    """One row of the Household Members table, IDs verbatim from its href.

    `age` is the verbatim cell text ("45", but also "9/12" for infant months) —
    never coerced to a number. `collection_id`/`record_id` are None for a
    name-only member: explicitly unlinkable by ID, so a consumer knows to fall
    back to name+age+relationship instead of assuming completeness.
    """

    name: str
    age: Optional[str]
    relationship: Optional[str]
    collection_id: Optional[str]
    record_id: Optional[str]
    sheet: Optional[str] = None
    line: Optional[str] = None
    is_principal: bool = False
    #: Is this member on the captured record image? True for the principal (the
    #: image, when one exists, is their record's by identity); True/False when the
    #: table prints sheet numbers to compare; None = unknown — never assumed.
    image_shared: Optional[bool] = None


def parse_household(
    html: str, *, page_url: Optional[str]
) -> tuple[HouseholdMember, ...]:
    """Members of the page's Household table, `()` when it has none.

    `page_url` is the captured record's own URL; its `{collection, record}` pair
    identifies (or is echoed onto) the principal's row.
    """
    if not html or not isinstance(html, str):
        return ()
    html = _strip_opaque(html)

    members: list[HouseholdMember] = []
    seen_ids: set[IdPair] = set()
    seen_unlinked: set[tuple[str, Optional[str], Optional[str]]] = set()
    for section in _household_sections(html):
        for member in _members_in_section(section):
            if member.collection_id and member.record_id:
                key = (member.collection_id, member.record_id)
                if key in seen_ids:
                    continue  # double-listed (corner-case #8) — emit once
                seen_ids.add(key)
            else:
                bare = (member.name.casefold(), member.age, member.relationship)
                if bare in seen_unlinked:
                    continue
                seen_unlinked.add(bare)
            members.append(member)

    if not members:
        return ()
    return _mark_principal(members, html=html, page_url=page_url)


# -- section scoping ----------------------------------------------------------


def _strip_opaque(html: str) -> str:
    """Remove comments, script and style bodies before any structural scan.

    Their content is data, not markup — but it can CONTAIN markup-shaped text
    (hydration JSON with cached HTML fragments), and every scan downstream of
    here (anchor finding, tag balancing, row/href extraction) must never read a
    string literal as structure.
    """
    html = _COMMENT_RE.sub(" ", html)
    html = _SCRIPT_RE.sub(" ", html)
    return _STYLE_RE.sub(" ", html)


def _household_sections(html: str) -> list[str]:
    """Each `data-is-household="true"` element's markup, tag-balance sliced."""
    sections: list[str] = []
    for match in _ANCHOR_RE.finditer(html):
        sections.append(_balanced_slice(html, match.start(), match.group("tag")))
    return sections


def _balanced_slice(html: str, start: int, tag: str) -> str:
    """`html[start:...]` up to the close tag matching the element at `start`.

    Counts `<tag`/`</tag>` from the anchor itself (depth 1 after the first open),
    over HTML whose opaque content was already stripped (`_strip_opaque`). An
    unterminated element yields `""` — the section is REFUSED, not extended to
    the rest of the document. Over-capture is NOT harmless here: everything after
    the household table is exactly the record-shaped link surface (Suggested
    records, Neighbors) that scoping exists to exclude, and a leaked link becomes
    a wrong-record ID. No block beats a polluted block.
    """
    open_re = re.compile(rf"<{tag}\b", re.I)
    close_re = re.compile(rf"</{tag}\s*>", re.I)
    depth = 0
    pos = start
    while True:
        opened = open_re.search(html, pos)
        closed = close_re.search(html, pos)
        if closed is None:
            return ""
        if opened is not None and opened.start() < closed.start():
            depth += 1
            pos = opened.end()
            continue
        depth -= 1
        pos = closed.end()
        if depth <= 0:
            return html[start:pos]


# -- row parsing --------------------------------------------------------------


def _text(fragment: str) -> str:
    return " ".join(html_lib.unescape(_TAG_RE.sub(" ", fragment)).split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _column(headers: Sequence[str], *needles: str) -> Optional[int]:
    """The index of the header a needle names, by TOKEN prefix — not substring.

    Substring matching mis-binds columns: "Marriage", "Language" and "Percentage"
    all contain "age" (review finding #4), and a wrongly-bound age column silently
    replaces every member's real age with other data. Token-prefix keeps the
    intended looseness ("Members" ~ member, "Relationship" ~ relation) without it.
    """
    for i, header in enumerate(headers):
        header_tokens = _tokens(header)
        for needle in needles:
            if any(token.startswith(needle) for token in header_tokens):
                return i
    return None


_HEADER_NEEDLES = ("name", "member", "age", "relation", "sheet", "line")


def _looks_like_header_row(cells: Sequence[str]) -> bool:
    tokens = [t for cell in cells for t in _tokens(cell)]
    return any(t.startswith(n) for t in tokens for n in _HEADER_NEEDLES)


def _row_ids(row: str) -> Optional[IdPair]:
    for href in _HREF_RE.findall(row):
        ids = record_ids_from_href(html_lib.unescape(href))
        if ids:
            return ids
    return None


def _members_in_section(section: str) -> list[HouseholdMember]:
    # Headers come from the <thead> ONLY when one exists (review finding #9 — a
    # stray <th> inside a body row must not become a trailing header).
    split = _THEAD_SPLIT_RE.split(section, maxsplit=1)
    if len(split) == 2:
        head, body = split
        headers = [_text(th) for th in _TH_RE.findall(head)]
    else:
        body = section
        headers = [_text(th) for th in _TH_RE.findall(section)]
    rows = _TR_RE.findall(body)

    # A table variant with no <th> at all renders its header as a plain <td> row
    # (review finding #6). Without this, that row becomes a phantom "member" named
    # "Household Members" and every real member loses its column mapping. Only a
    # linkless first row that reads like headers is consumed as one.
    if not headers and rows:
        first_cells = [_text(td) for td in _TD_RE.findall(rows[0])]
        if first_cells and _row_ids(rows[0]) is None and _looks_like_header_row(first_cells):
            headers = first_cells
            rows = rows[1:]

    name_col = _column(headers, "name", "member")
    age_col = _column(headers, "age")
    rel_col = _column(headers, "relation")
    sheet_col = _column(headers, "sheet")
    line_col = _column(headers, "line")

    members: list[HouseholdMember] = []
    for row in rows:
        cells = [_text(td) for td in _TD_RE.findall(row)]
        if not cells:
            continue

        def cell(index: Optional[int]) -> Optional[str]:
            if index is None or index >= len(cells):
                return None
            return cells[index] or None

        name = cell(name_col if name_col is not None else 0)
        if not name:
            continue

        ids = _row_ids(row)
        members.append(
            HouseholdMember(
                name=name,
                age=cell(age_col),
                relationship=cell(rel_col),
                collection_id=ids[0] if ids else None,
                record_id=ids[1] if ids else None,
                sheet=cell(sheet_col),
                line=cell(line_col),
            )
        )
    return members


# -- principal + image_shared -------------------------------------------------


def _mark_principal(
    members: list[HouseholdMember], *, html: str, page_url: Optional[str]
) -> tuple[HouseholdMember, ...]:
    page_ids = record_ids_from_url(page_url)
    index = _principal_index(members, html=html, page_ids=page_ids)
    if index is None:
        return tuple(members)

    principal = members[index]
    principal_sheet = principal.sheet
    marked: list[HouseholdMember] = []
    for i, member in enumerate(members):
        if i == index:
            marked.append(
                replace(
                    member,
                    is_principal=True,
                    # Echo the captured record's own IDs onto its unlinked row (#3).
                    collection_id=member.collection_id or (page_ids[0] if page_ids else None),
                    record_id=member.record_id or (page_ids[1] if page_ids else None),
                    image_shared=True,
                )
            )
            continue
        shared: Optional[bool] = None
        if principal_sheet and member.sheet:
            shared = member.sheet == principal_sheet
        marked.append(replace(member, image_shared=shared))
    return tuple(marked)


def _principal_index(
    members: list[HouseholdMember], *, html: str, page_ids: Optional[IdPair]
) -> Optional[int]:
    # Strongest signal first: a row whose parsed IDs ARE the captured record's.
    if page_ids:
        for i, member in enumerate(members):
            if (member.collection_id, member.record_id) == page_ids:
                return i

    unlinked = [i for i, m in enumerate(members) if m.record_id is None]
    if len(unlinked) == 1:
        return unlinked[0]

    if len(unlinked) > 1:
        marker = _MARKER_RE.search(html)
        if marker:
            want = " ".join(html_lib.unescape(marker.group(1)).split()).casefold()
            hits = [i for i in unlinked if members[i].name.casefold() == want]
            if len(hits) == 1:
                return hits[0]
    return None
