# REQ-003 — Capture a document's family/household members (record IDs) for single-pass completeness

**Status:** ✅ **DONE — E2E VERIFIED 2026-08-24** (v3 — see implementation notes below)

The pending live verification was **already satisfied by field captures on disk** and had
simply never been recorded here. Verified 2026-08-24 by scanning every capture manifest in
`data/captures`:

- **13 captures carry a `household` block**, banked **2026-07-19 → 2026-07-29** — all *after*
  the 2026-07-17 ship, i.e. produced by real live Ancestry sessions through the extension, not
  by fixtures or a corpus replay.
- **77 household members total.** Acceptance holds on **13/13**: each block has **exactly one
  `is_principal: true`**, and **every member carries both `collection_id` and `record_id`** —
  the record IDs that make single-pass completeness possible. **Zero violations.**
- Shape confirmed on a real block (`20260723-ancestrycom-53e949db`, 7 members): `name`, `age`,
  `relationship`, `collection_id`, `record_id`, `is_principal`, `image_shared` — emitted into
  `manifest.json`, which is where the consumer reads it (the BUG-014 lesson).

No further live session is owed for this REQ.
**Priority:** 🟡 MEDIUM — enables "process by document" completeness in the Gramps migration
**Filed:** 2026-07-16, Genealogy research session · **Corner-cased 2026-07-16** (schema + acceptance updated to the review below) · **Greenlit by owner 2026-07-17** after the field session re-affirmed it

## Implementation notes (v3, 2026-07-17) — what the real DOM changed

Implemented as a **deterministic parser** (`capsule/pipeline/household.py`), never a model
output, emitting an additive `household` block in **`manifest.json`** (the consumer reads the
manifest — the BUG-014 lesson). Wired in `run_capture` for Ancestry URLs; both the CLI and
backend paths get it. Validated against all 69 Ancestry captures on disk: 44/45 census records
parse with exactly one principal; the 45th (1892 NY State Census) genuinely renders no
household table. Index/vital/passenger-list records correctly yield no block.

Deviations from the v2 schema, forced by the real DOM (capture `20260716-ancestrycom-e9f28b27`):

1. **Scoping anchor**: Ancestry marks the household table itself with
   `data-is-household="true"` — the parser reads ONLY that subtree, which satisfies #1
   (Suggested records / Neighbors excluded) more robustly than heading-text matching.
2. **No `sheet`/`line` columns exist** in the live table (Name/Age/Relationship only), so
   `image_shared` is **`null` = UNKNOWN** for every non-principal member — never `true` on
   faith (#2). The consumer must verify page membership itself before attaching the image
   (e.g. via the member's own record). Sheet comparison is implemented and will populate
   `sheet`/`image_shared` automatically on table variants that print the columns.
3. **No `apid` string is emitted** — `{collection_id, record_id}` only, per corner-case #4:
   the `"1,"` prefix is not derivable from the page, and we do not invent identifiers.
4. **Principal detection** (#3): the page itself marks the principal — every other member's
   row links to their record; the subject's row is unlinked (a record does not link to
   itself). Chain: row whose href IDs == the captured URL's IDs → single unlinked row →
   unlinked row matching the page's own "Provide alternate for &lt;name&gt;" marker → else NO
   row is marked (a guessed principal is worse than an unmarked one). The principal's row
   gets the captured record's own IDs echoed onto it and `image_shared: true` (the captured
   image is its record's by identity).

Tests: `tests/test_req_003_household.py` (14, pinning #1–#8 + both URL shapes + additivity).

## The ask

When Capsule captures a **multi-person record** (a census household, a marriage certificate, a
passenger manifest), have it surface, for each other person the record names, that person's **own
Ancestry record identity** — i.e. their `_APID` / `{collection_id, record_id}` — in the capture
output (a `household` / `co_personas` block in `manifest.json` and/or `extraction.json`).

Today the extraction lists household members by **name + age + relationship** (e.g. "Jacob Kessler, 57,
Head") but NOT their Ancestry record IDs. On the Ancestry record page each "Household Member" is a
link to that member's own record (`.../records/<their_rec>`); the ask is to extract those links.

```jsonc
// desired addition to the capture output (v2 schema — folds in the corner-case review below)
"household": [
  { "name": "Jacob Kessler",   "relationship": "Head", "age": 57,
    "collection_id": "7602", "record_id": "48834840", "apid": "1,7602::48834840",
    "sheet": "12A", "line": 41, "is_principal": true,  "image_shared": true },
  { "name": "Caroline Kessler","relationship": "Wife", "age": 42,
    "collection_id": "7602", "record_id": "48834841", "apid": "1,7602::48834841",
    "sheet": "12A", "line": 42, "is_principal": false, "image_shared": true },
  { "name": "Benhard Kessler", "relationship": "Son",  "age": 18,
    "collection_id": "7602", "record_id": "48834842", "apid": "1,7602::48834842",
    "sheet": "12B", "line": 1,  "is_principal": false, "image_shared": false },  // spilled to next sheet
  { "name": "A Boarder",      "relationship": "Boarder", "age": 30,
    "collection_id": null, "record_id": null, "apid": null,                       // name-only, unlinkable
    "sheet": "12A", "line": 43, "is_principal": false, "image_shared": true }
]
```
*(The surname in this example is a substitute for the real one; ages, roles and ids show the shape only.)*

`{collection_id, record_id}` is the canonical key (the `apid` string is a convenience — see #4).
`is_principal` marks the captured record's own person (#3). `image_shared` is `false` when the member's
`sheet` differs from the principal's, i.e. the captured image is NOT that member's page (#2). `age` and
`relationship` come straight off the table and make ID matches verifiable + name-only members matchable
(#6). Omit the whole block when the page exposes no member rows.

## Why

The Gramps migration must meet a **Definition of Done**: a document is not "done" until it is cited on
**every** person it names who is in the tree, with each member's facts mined. A census names a whole
household; a marriage cert names both parties + all four parents + witnesses.

Right now I process **by ancestor** — so a shared census page gets captured (or reused) separately when
I reach each household member's Ahnentafel number, and the document is "incomplete" in between. With the
members' record IDs in the capture I can flip to processing **by document**: capture the image **once**,
then attach that one image to every household member's own citation and mine each member's facts — a
**single pass** that completes the record. That is both more complete and avoids re-capturing the same
physical census page N times.

The image is identical across a household (same scan); only the `_APID`/citation differs per member. So
this needs no extra image work — just the members' record IDs, which are already on the page as links.

## Scope / notes

- **Parse ONLY the "Household Members" DOM subtree** — never the "Suggested records" rail or "Neighbors /
  View others on page" (both are record-shaped links to *other* collections). Scoping is mandatory, not a
  nicety (#1).
- **Census / passenger list:** each member/co-traveller row links to their own record — take `{collection,
  record}` from the href, and `age` + `relationship` + `sheet`/`line` from the row.
- **Marriage / vital cert:** the two parties (and sometimes parents) may each have linked records, possibly
  in different collections; include what's linked. Witnesses are usually unlinked — name-only is fine.
- **URL shapes:** parse both `/search/collections/<coll>/records/<rec>` and
  `/discoveryui-content/view/<rec>:<coll>` (tolerate query strings/anchors/redirects) (#5).
- Purely additive; omit the block when the page exposes no member rows. No new permissions; no media/tile
  API; **no fetching member pages** (parse the visible hrefs only — fetching would be crawling-ahead) (#10).

## Acceptance

For a census capture, `manifest.json` (or `extraction.json`) contains a `household` array per the v2
schema above, in which:
1. every row comes from the Household-Members subtree only (no Suggested/Neighbor pollution);
2. each linked member carries `{collection_id, record_id}`; name-only members carry them as `null`;
3. exactly one row has `is_principal: true` (the captured record's own person);
4. `image_shared` is `false` for any member whose `sheet` differs from the principal's;
5. `age` and `relationship` are present for every row the table shows them for.

That is enough for the consumer to attach the one captured image to every **same-sheet** household member's
Gramps citation, verify each by age/relationship, skip the principal, and know which members need their own
image — a safe single pass. (The old one-line acceptance — "give each member's apid" — was necessary but
not sufficient; see the corner-case review.)

---

## Corner-case review (consumer-side, added 2026-07-16 — the request is directionally right but NOT yet fully corner-cased)

Written by the Genealogy research agent, which is the consumer. The base ask is good; these are the
edge cases that decide whether the block is *safe to act on unattended*. Ranked by risk.

### 🔴 Must-fix (correctness / would cause a wrong-image or false link)

1. **Scope strictly to the "Household Members" table — exclude "Suggested records" and "Neighbors".**
   An Ancestry record page has THREE clusters of record-shaped links: the household table, a **Suggested
   records** rail (links to *other* collections — 1920 census, death index, Find a Grave…), and
   **Neighbors → "View others on page"**. A naive "grab every `/records/<id>` href" pulls all three, and
   the consumer would then attach this census image to unrelated records. The block MUST come from the
   Household-Members DOM subtree only. This is the single biggest false-positive risk.

2. **A household can span two census sheets — do NOT assume one image fits every member.** The spec says
   *"the image is identical across a household (same scan); only the _APID/citation differs."* That is
   **false at a page boundary**: members enumerated on the next sheet have a **different image**. Attaching
   the captured sheet-1 image to a spillover member is a wrong-image defect (our §12). Mitigation: include
   each member's **sheet/page + line** (the page shows them), and either (a) flag `image_shared:false` when
   a member's sheet differs from the principal's, or (b) let the consumer detect the sheet change and
   capture that member's own image. At minimum, drop the "always identical" claim from the spec.

3. **Mark the principal.** The household list includes the subject (Head) whose record IS the captured
   record. Emit `is_principal: true` on that row (or echo the captured record's own `{collection,record}`)
   so the consumer doesn't re-link/re-mine the person it's already processing, and can dedup the block
   against the current capture.

### 🟡 Should-fix (robustness / matchability)

4. **Key on `{collection_id, record_id}`, not the `"1,"`-prefixed `apid` string.** The leading `1,` in
   `1,7602::48834841` is Ancestry's dbid/category and is not guaranteed constant across collections; the
   consumer's stored `_APID` may carry a different prefix. Make `{collection_id, record_id}` the PRIMARY
   key (the `apid` string is a convenience). Our matching already keys on `collection::record`.

5. **Handle both Ancestry URL shapes.** Member links appear as `/search/collections/<coll>/records/<rec>`
   **and** `/discoveryui-content/view/<rec>:<coll>` (plus query strings/anchors, and occasional redirects).
   Parse both; the example regex only covers the first.

6. **Carry `age` + `relationship` per member (this is the high-value add).** Today the extraction personas
   expose only a `label` — no age, no relationship. That's exactly why our name-only household-link guard
   is fragile and had to be made conservative (it can't tell a father from his same-named son, or a
   co-ancestor from the subject). Record IDs solve linking for *linked* members, but (a) **name-only
   members** (see #7) have no ID and still need age+relationship to be matchable at all, and (b) age+
   relationship lets the consumer **verify** an ID match instead of trusting it blind (defense in depth).
   Cheap to add — both are printed in the Household-Members table. Strongly recommended; arguably the part
   that most "improves how the agent works."

7. **Emit name-only members explicitly.** Some members aren't linked (indexing gaps). Include them with
   `apid: null` (spec already allows name-only) but make it explicit they are **unlinkable by ID** — the
   consumer then knows to fall back to name+age+relationship (#6) rather than assume completeness.

### 🟢 Minor / notes

8. **De-dup the block itself** — emit each `{collection,record}` once (rare double-listings; principal
   listed among members).
9. **Marriage / vital certs:** parties may link to records in *different* collections (a marriage index vs
   a party's own record); the shared-image assumption holds per *record image*, but multi-image certs
   (front/back, cert + license) need the consumer to know which image is which. Keep parents/witnesses
   name-only.
10. **Compliance stays clean** *only if* the record IDs come from parsing the page's own visible hrefs —
    never by fetching the member pages (that would be crawling-ahead, which the spec correctly forbids).

### Net
Ship it, but with #1 (scope), #2 (page-boundary image), and #3 (mark principal) as blocking, and #6
(age+relationship) folded in — otherwise the consumer still can't safely act on the block unattended, and
the name-matching ambiguity that motivated the request survives. With #1–#3 + #6, the consumer can retire
the conservative name-guard in its household-linking tool and link households by ID in one pass.
