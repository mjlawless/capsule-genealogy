"""REQ-003 — household member record IDs, parsed from the page's OWN Household table.

The contract under test (spec v2 + the corner-case review, as implemented):

- **Scope**: members come ONLY from the `data-is-household="true"` section — never the
  "Suggested records" rail, never "Neighbors / View others on page" (#1). Attaching a
  census image to an unrelated suggested record is the defect this scoping prevents.
- **IDs are parsed from hrefs, never invented** (#4, #10): `{collection_id, record_id}`
  is the key; both Ancestry URL shapes are readable (#5); name-only members carry null
  IDs explicitly (#7).
- **The principal is marked** (#3): on the real DOM the page itself distinguishes the
  principal — every OTHER member's row links to their record; the subject's row does
  not (a record does not link to itself). Ambiguity (a name-only member alongside)
  resolves via the page's own "Provide alternate for <name>" marker, else NO row is
  marked — a guessed principal is worse than an unmarked one.
- **image_shared is honest** (#2): the live household table shows NO sheet/line
  columns, so a member's presence on the captured image is UNKNOWN (null), not true.
  The principal's own image is the captured one by identity.

Fixture markup is modeled byte-for-byte on a real capture
(20260716-ancestrycom-e9f28b27, 1920 census): section[data-is-household] > table with
thead (Name/Age/Relationship), every cell of a linked row wrapped in the same
`contentLink` anchor, and the principal's row as plain `td.record` text.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from capsule.contracts.evidence import GatingStatus

_DT = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)

_PAGE_URL = "https://www.ancestry.com/search/collections/6061/records/54221383"


def _linked_row(name: str, age: str, rel: str, href: str) -> str:
    cell = f'<a href="{href}" title="View record" class="contentLink">{{v}}</a>'
    return (
        "<tr>"
        f'<td class="">{cell.format(v=name)}</td>'
        f'<td class="">{cell.format(v=age)}</td>'
        f'<td class="">{cell.format(v=rel)}</td>'
        "</tr>"
    )


def _plain_row(name: str, age: str, rel: str) -> str:
    return (
        "<tr>"
        f'<td class="record">{name}</td>'
        f'<td class="record">{age}</td>'
        f'<td class="record">{rel}</td>'
        "</tr>"
    )


def _household_section(rows: str, headers=("Household Members (Name)", "Age", "Relationship")) -> str:
    ths = "".join(f"<th>{h}</th>" for h in headers)
    return (
        '<section class="tableContainer conSecond" data-table-id="_T1" data-is-household="true">'
        '<table id="_T1" class="table embed">'
        f"<thead><tr>{ths}</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</section>"
    )


_REAL_ROWS = (
    _linked_row("George Kessler", "45", "Head",
                "http://www.ancestry.com/search/collections/6061/records/54221379")
    + _linked_row("Henrietta Kessler", "47", "Wife",
                  "http://www.ancestry.com/search/collections/6061/records/54221380")
    + _linked_row("Edna Kessler", "21", "Daughter",
                  "http://www.ancestry.com/search/collections/6061/records/54221381")
    + _linked_row("Mildred Kessler", "12", "Daughter",
                  "http://www.ancestry.com/search/collections/6061/records/54221382")
    + _plain_row("Caroline Kessler", "1", "Daughter")
)

#: Record-shaped links OUTSIDE the household section — the exact false-positive
#: surface the corner-case review ranked #1. A naive href sweep pulls all of these.
_DECOYS = (
    '<div class="suggestedRecords">'
    '<a href="http://www.ancestry.com/search/collections/6224/records/12345">'
    "1930 United States Federal Census</a>"
    '<a href="http://www.ancestry.com/discoveryui-content/view/67890:60525">'
    "U.S., Find a Grave Index</a>"
    "</div>"
    "<tr><th>Neighbors</th><td>"
    '<button type="button" title="View others on page" class="link neighborsLink" '
    'data-image-gid="4442062_00979:6061">View others on page</button>'
    "</td></tr>"
)


def _page(section: str, marker_name: str | None = "Caroline Kessler") -> str:
    marker = (
        f'<label for="oremAlternateFor" class="required">'
        f"Provide alternate for {marker_name}</label>"
        if marker_name
        else ""
    )
    return (
        "<html><head><title>1920 United States Federal Census - Ancestry.com</title>"
        f"</head><body>{marker}{_DECOYS}{section}"
        "<div>Household snippet: George Kessler 45 Head</div>"
        "</body></html>"
    )


def _parse(html: str, url: str = _PAGE_URL):
    from capsule.pipeline.household import parse_household

    return parse_household(html, page_url=url)


# -- parsing ------------------------------------------------------------------


def test_members_carry_ids_age_relationship_verbatim():
    members = _parse(_page(_household_section(_REAL_ROWS)))
    by_name = {m.name: m for m in members}
    assert set(by_name) == {
        "George Kessler", "Henrietta Kessler", "Edna Kessler", "Mildred Kessler",
        "Caroline Kessler",
    }
    george = by_name["George Kessler"]
    assert (george.collection_id, george.record_id) == ("6061", "54221379")
    assert george.age == "45"
    assert george.relationship == "Head"
    assert george.is_principal is False


def test_suggested_records_and_neighbors_never_become_household():  # corner-case #1
    members = _parse(_page(_household_section(_REAL_ROWS)))
    ids = {(m.collection_id, m.record_id) for m in members}
    assert ("6224", "12345") not in ids  # suggested 1930 census
    assert ("60525", "67890") not in ids  # suggested Find a Grave (discovery shape)


def test_exactly_one_principal_the_unlinked_row_with_page_ids_echoed():
    members = _parse(_page(_household_section(_REAL_ROWS)))
    principals = [m for m in members if m.is_principal]
    assert len(principals) == 1
    principal = principals[0]
    assert principal.name == "Caroline Kessler"
    # The page's own record IDs are echoed onto the principal (#3) — the row itself
    # carries no link, but the captured URL says exactly who this record is.
    assert (principal.collection_id, principal.record_id) == ("6061", "54221383")
    # The captured image is the principal's own record image, by identity.
    assert principal.image_shared is True


def test_members_image_shared_is_unknown_not_true():  # corner-case #2
    """The real table shows no sheet/line, so 'on the captured image' is UNKNOWN.

    A household can spill to the next census sheet; claiming image_shared=True
    without sheet evidence is exactly the wrong-image defect the review blocked.
    """
    members = _parse(_page(_household_section(_REAL_ROWS)))
    for m in members:
        if not m.is_principal:
            assert m.image_shared is None


def test_discoveryui_href_shape_is_read_with_the_id_swap():  # corner-case #5
    rows = _linked_row(
        "Jacob Kessler", "57", "Head",
        "https://www.ancestry.com/discoveryui-content/view/48834840:7602",
    ) + _plain_row("Caroline Kessler", "1", "Daughter")
    members = _parse(_page(_household_section(rows)))
    jacob = next(m for m in members if m.name == "Jacob Kessler")
    # The discovery URL is <rec>:<db> — a naive read binds the IDs backwards.
    assert (jacob.collection_id, jacob.record_id) == ("7602", "48834840")


def test_double_listed_member_is_emitted_once():  # corner-case #8
    href = "http://www.ancestry.com/search/collections/6061/records/54221379"
    rows = (
        _linked_row("George Kessler", "45", "Head", href)
        + _linked_row("George Kessler", "45", "Head", href)
        + _plain_row("Caroline Kessler", "1", "Daughter")
    )
    members = _parse(_page(_household_section(rows)))
    assert sum(1 for m in members if m.record_id == "54221379") == 1


def test_page_without_household_table_yields_nothing():
    members = _parse(_page(""))  # decoys present, no data-is-household section
    assert members == ()


def test_non_ancestry_hosted_href_is_not_an_id_source():
    rows = _linked_row(
        "Someone Else", "30", "Boarder",
        "https://www.evil.example/search/collections/1111/records/2222",
    ) + _plain_row("Caroline Kessler", "1", "Daughter")
    members = _parse(_page(_household_section(rows)))
    someone = next(m for m in members if m.name == "Someone Else")
    assert someone.collection_id is None and someone.record_id is None


# -- review findings (2026-07-17): scoping cannot be defeated -----------------


#: A record-shaped decoy placed AFTER the household section — the surface an
#: over-captured slice would swallow (review finding #1).
_AFTER_DECOY = (
    '<div class="suggestedRecords"><table><tbody>'
    '<tr><td><a href="http://www.ancestry.com/search/collections/9999/records/8888">'
    "Unrelated Suggested Record</a></td></tr>"
    "</tbody></table></div>"
)


def test_script_blob_inside_the_section_cannot_leak_a_following_decoy():
    """Review CRITICAL #1: a hydration blob containing the literal '<section'
    substring must not throw the depth counter and swallow the suggested rail."""
    blob = (
        '<script type="application/json">'
        '{"cached": "<section class=\\"x\\"><table><tr><td>fragment</td></tr></table>"}'
        "</script>"
    )
    section = (
        '<section class="tableContainer" data-table-id="_T1" data-is-household="true">'
        '<table id="_T1" class="table embed">'
        "<thead><tr><th>Household Members (Name)</th><th>Age</th><th>Relationship</th></tr></thead>"
        f"<tbody>{_REAL_ROWS}</tbody></table>{blob}</section>"
    )
    members = _parse(_page(section) + _AFTER_DECOY)
    ids = {(m.collection_id, m.record_id) for m in members}
    assert ("9999", "8888") not in ids
    assert len(members) == 5  # the real household still parses in full


def test_html_comment_inside_the_section_cannot_leak_a_following_decoy():
    section = (
        '<section data-is-household="true"><table>'
        "<thead><tr><th>Household Members (Name)</th><th>Age</th><th>Relationship</th></tr></thead>"
        f"<tbody>{_REAL_ROWS}</tbody></table>"
        "<!-- injected here via JS: <section> -->"
        "</section>"
    )
    members = _parse(_page(section) + _AFTER_DECOY)
    assert ("9999", "8888") not in {(m.collection_id, m.record_id) for m in members}
    assert len(members) == 5


def test_unterminated_section_is_refused_not_extended_to_the_page():
    """Review HIGH #3: no close tag -> NO block. Everything after the household
    table is exactly the link surface scoping exists to exclude."""
    section = (
        '<section data-is-household="true"><table>'
        "<thead><tr><th>Household Members (Name)</th><th>Age</th><th>Relationship</th></tr></thead>"
        f"<tbody>{_REAL_ROWS}</tbody></table>"
        # no </section> — truncated page
    )
    members = _parse(_page(section) + _AFTER_DECOY)
    assert members == ()


def test_protocol_relative_foreign_href_never_mints_ids():
    """Review CRITICAL #2: '//evil.example/...' has no '://' but names a host."""
    rows = _linked_row(
        "Someone Else", "30", "Boarder",
        "//www.evil.example/search/collections/1111/records/2222",
    ) + _plain_row("Caroline Kessler", "1", "Daughter")
    members = _parse(_page(_household_section(rows)))
    someone = next(m for m in members if m.name == "Someone Else")
    assert someone.collection_id is None and someone.record_id is None


def test_protocol_relative_and_query_bearing_relative_ancestry_hrefs_parse():
    rows = (
        _linked_row("George Kessler", "45", "Head",
                    "//www.ancestry.com/search/collections/6061/records/54221379")
        # A relative href whose QUERY contains a URL is still a same-site link (#7).
        + _linked_row("Henrietta Kessler", "47", "Wife",
                      "/search/collections/6061/records/54221380?ref=http://x")
        + _plain_row("Caroline Kessler", "1", "Daughter")
    )
    members = _parse(_page(_household_section(rows)))
    by_name = {m.name: m for m in members}
    assert (by_name["George Kessler"].collection_id,
            by_name["George Kessler"].record_id) == ("6061", "54221379")
    assert (by_name["Henrietta Kessler"].collection_id,
            by_name["Henrietta Kessler"].record_id) == ("6061", "54221380")


def test_marriage_header_is_not_the_age_column():
    """Review MEDIUM #4: 'Marriage' contains 'age' — substring binding replaces
    every member's real age with marital status."""
    href = "http://www.ancestry.com/search/collections/6061/records/54221379"
    cell = f'<a href="{href}" class="contentLink">{{v}}</a>'
    rows = (
        "<tr>"
        f"<td>{cell.format(v='George Kessler')}</td>"
        f"<td>{cell.format(v='Married')}</td>"
        f"<td>{cell.format(v='45')}</td>"
        f"<td>{cell.format(v='Head')}</td>"
        "</tr>"
        '<tr><td class="record">Caroline Kessler</td><td class="record">Single</td>'
        '<td class="record">1</td><td class="record">Daughter</td></tr>'
    )
    section = _household_section(
        rows, headers=("Household Members (Name)", "Marriage", "Age", "Relationship")
    )
    members = _parse(_page(section))
    george = next(m for m in members if m.name == "George Kessler")
    assert george.age == "45"
    assert george.relationship == "Head"


def test_sheet_columns_drive_image_shared_when_present():
    """Review MEDIUM #5: the page-boundary logic (corner-case #2) must be pinned:
    same sheet as the principal -> shared image; different sheet -> NOT on it."""
    href = "http://www.ancestry.com/search/collections/7602/records/{rec}"
    cell = '<a href="{href}" class="contentLink">{v}</a>'

    def row(name, age, rel, sheet, rec=None):
        wrap = (lambda v: cell.format(href=href.format(rec=rec), v=v)) if rec else (lambda v: v)
        return (
            "<tr>"
            f"<td>{wrap(name)}</td><td>{wrap(age)}</td>"
            f"<td>{wrap(rel)}</td><td>{wrap(sheet)}</td>"
            "</tr>"
        )

    rows = (
        row("Jacob Kessler", "57", "Head", "12A", rec="48834840")
        + row("Benhard Kessler", "18", "Son", "12B", rec="48834842")  # spilled sheet
        + row("Caroline Kessler", "42", "Wife", "12A")  # principal, unlinked
    )
    section = _household_section(
        rows, headers=("Household Members (Name)", "Age", "Relationship", "Sheet")
    )
    members = _parse(
        _page(section),
        url="https://www.ancestry.com/search/collections/7602/records/48834841",
    )
    by_name = {m.name: m for m in members}
    assert by_name["Jacob Kessler"].image_shared is True  # same sheet 12A
    assert by_name["Benhard Kessler"].image_shared is False  # sheet 12B — wrong image
    assert by_name["Caroline Kessler"].is_principal is True


def test_headerless_td_table_consumes_its_header_row_not_a_phantom_member():
    """Review MEDIUM #6: a table variant with the header as a plain <td> row must
    not mint a member named 'Household Members' nor drop the real columns."""
    rows = (
        "<tr><td>Household Members</td><td>Age</td><td>Relationship</td></tr>"
        + _linked_row("George Kessler", "45", "Head",
                      "http://www.ancestry.com/search/collections/6061/records/54221379")
        + _plain_row("Caroline Kessler", "1", "Daughter")
    )
    section = (
        '<section data-is-household="true"><table>'
        f"<tbody>{rows}</tbody></table></section>"
    )
    members = _parse(_page(section))
    names = {m.name for m in members}
    assert "Household Members" not in names
    george = next(m for m in members if m.name == "George Kessler")
    assert george.age == "45" and george.relationship == "Head"


# -- name-only members and principal ambiguity (#3, #7) -----------------------


def test_name_only_member_has_explicit_null_ids_and_marker_names_principal():
    rows = (
        _linked_row("George Kessler", "45", "Head",
                    "http://www.ancestry.com/search/collections/6061/records/54221379")
        + _plain_row("A Boarder", "30", "Boarder")  # indexing gap: no link
        + _plain_row("Caroline Kessler", "1", "Daughter")  # the principal, no link
    )
    members = _parse(_page(_household_section(rows), marker_name="Caroline Kessler"))
    by_name = {m.name: m for m in members}
    boarder = by_name["A Boarder"]
    assert boarder.collection_id is None and boarder.record_id is None
    assert boarder.is_principal is False
    assert by_name["Caroline Kessler"].is_principal is True


def test_two_unlinked_rows_and_no_marker_marks_no_principal():
    """A guessed principal is a mis-link waiting to happen — mark none, honestly."""
    rows = (
        _linked_row("George Kessler", "45", "Head",
                    "http://www.ancestry.com/search/collections/6061/records/54221379")
        + _plain_row("A Boarder", "30", "Boarder")
        + _plain_row("Caroline Kessler", "1", "Daughter")
    )
    members = _parse(_page(_household_section(rows), marker_name=None))
    assert all(m.is_principal is False for m in members)


def test_missing_age_relationship_columns_yield_none_not_guesses():
    rows = (
        '<tr><td><a href="http://www.ancestry.com/search/collections/6061/records/1" '
        'class="contentLink">Only Name</a></td></tr>'
        '<tr><td class="record">Caroline Kessler</td></tr>'
    )
    section = _household_section(rows, headers=("Household Members (Name)",))
    members = _parse(_page(section))
    only = next(m for m in members if m.name == "Only Name")
    assert only.age is None and only.relationship is None


# -- the capture output: manifest.json household block ------------------------


_RAW = {
    "record_type": "census",
    "personas": [{"id": "p1", "label": "George Kessler", "role": "head"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Relation",
            "value": "Head",
            "evidence_type": "stated",
            "snippet": "George Kessler 45 Head",
        }
    ],
}


def _run_capture(tmp_path, html: str, url: str = _PAGE_URL):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    n = 0

    def _ids() -> str:
        nonlocal n
        n += 1
        return f"id-{n}"

    fetcher = FakeFetcher(
        PageSnapshot(
            url=url,
            html=html,
            gating=GatingStatus.GATED,
            fetched_at=_DT,
            title="1920 United States Federal Census",
        )
    )
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    try:
        return run_capture(
            url,
            fetcher=fetcher,
            agent=agent,
            repository=repo,
            captures_root=tmp_path / "captures",
            now=lambda: _DT,
            new_id=_ids,
        )
    finally:
        repo.close()


def test_manifest_carries_the_household_block(tmp_path):
    outcome = _run_capture(tmp_path, _page(_household_section(_REAL_ROWS)))
    manifest = json.loads((outcome.capture_folder / "manifest.json").read_text("utf-8"))
    household = manifest["household"]
    assert len(household) == 5
    principals = [m for m in household if m["is_principal"]]
    assert len(principals) == 1 and principals[0]["name"] == "Caroline Kessler"
    george = next(m for m in household if m["name"] == "George Kessler")
    assert george["collection_id"] == "6061"
    assert george["record_id"] == "54221379"
    assert george["age"] == "45"
    assert george["relationship"] == "Head"
    assert george["image_shared"] is None


def test_household_block_is_additive_absent_without_member_rows(tmp_path):
    outcome = _run_capture(tmp_path, _page(""))
    manifest = json.loads((outcome.capture_folder / "manifest.json").read_text("utf-8"))
    assert "household" not in manifest
    # And the capture itself is untouched by the feature: still verifiable.
    assert outcome.verify.ok


def test_household_capture_still_verifies(tmp_path):
    """The block is manifest metadata, not a hashed file — verify must stay green."""
    outcome = _run_capture(tmp_path, _page(_household_section(_REAL_ROWS)))
    assert outcome.verify.ok
