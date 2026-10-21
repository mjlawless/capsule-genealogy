# Design note: linking a RootsIreland index entry to its NLI original

Status: **design, not built.** Written 2026-07-09 from a real RootsIreland church
marriage record (James Kilcoin & Bridget Brady, Islandeady RC parish, Co. Mayo,
29-Apr-1852) and the NLI register it cites. The cheap half lands now; the
`SourceRelation` machinery is M2 work, next to the matcher.

## The two sources

| | RootsIreland | NLI registers |
|---|---|---|
| What it is | transcribed index entry | scan of the register page |
| Source quality | **derivative** | **original** |
| Yields | assertions (`status: extracted`) | an image, zero assertions (`status: captured`) |
| Gating | gated, copyright-flagged, never republished | public, Wayback-eligible |
| Text | a real HTML table | ~300 chars of viewer chrome |

Each is useless in the way the other is useful. RootsIreland gives machine-readable
facts you cannot verify; NLI gives evidence you cannot read. The relation between
them is what makes the NLI capture worth taking *before* M4 HTR exists.

They must never be merged. When HTR eventually reads the scan, its assertions may
contradict the centre's transcription of the same marriage. Because assertions
stay source-linked, that surfaces as an inspectable conflict between an original
and a derivative — with the original winning by rule — rather than as silent
corruption. This is the append-only rule paying for itself.

## The link exists, and is machine-extractable

```html
<!-- MICROFILM_LINK -->
<div id="bottom-buttons">
  <a href="https://registers.nli.ie/registers/vtls000632043" target="micro-link"
     alt="Find Microfilm at National Libary of Ireland Catholic registers online">
     View Microfilm of Parish Register </a>
</div>
```

## …but it resolves the register, not the leaf

The href carries `vtls000632043` and **no `#page/NN` fragment**. RootsIreland
tells you *which register*; it never tells you *which page*. The only page-level
locator on the record is:

```html
<tr><td></td><td colspan="5">Notes:</td></tr>
<tr><td></td><td colspan="5" class="record-detail1"><pre>1/1/6</pre></td></tr>
```

`1/1/6` is almost certainly a volume/page/entry reference in the Mayo centre's own
encoding. We do not know that encoding. Decoding it by pattern-matching would be
exactly the inference this system exists to prevent.

So the linkage decomposes into two claims of very different strength:

| Claim | Evidence type |
|---|---|
| This entry derives from NLI register `vtls000632043` | `stated` — the page says so |
| It derives from **leaf 26** of that register | `estimated` at best |

## Consequence: `SourceRelation` carries an evidence type

Not architectural symmetry for its own sake — it is the only way to record "the
page states the register" and "the researcher believes it is page 26" as the
different things they are.

```
SourceRelation
  from_source_id   the RootsIreland capture
  to               a local Source id, or an unresolved ExternalRef
  relation_type    derived_from
  evidence_type    stated | user_asserted | calculated | estimated
  created_at
```

```
ExternalRef
  authority   "nli-registers"
  identifier  "vtls000632043"
  locator     null          # register-level; the leaf is a separate, weaker claim
```

Append-only, like everything else. A RootsIreland capture usually exists *before*
the NLI capture — you find the index entry, then go look at the scan — so the
target is a normalized reference that binds to a `Source` later, or never.
Dangling references are legal. Binding appends a row; nothing is rewritten.

Register-level binding is automatic and safe. **Leaf-level binding is a
hypothesis**, per the hard rule: ambiguity parks, it does not resolve itself. It
is confirmed by a human, or by HTR reading "James Kilcoin" and "29 Apr 1852" off
the leaf and corroborating the derivative. The derivative supplies the search key
that lets the original verify the derivative.

Discovery is LLM extraction (an `external_refs` field on the reply), not a CSS
selector — consistent with the extraction rule, and it survives layout drift.

## We record the link. We never follow it.

A RootsIreland capture that auto-fetched the NLI scan would be a background
request to a provider the user did not drive — precisely what plan §11 forbids.
The user captures RootsIreland, sees "references NLI vtls000632043", and clicks
through to capture the scan themselves. **The reference is data, not an
instruction to fetch.**

Related: the capsule export must be able to carry the NLI original plus a
*citation* of the RootsIreland entry without carrying RootsIreland's gated
snapshot. The relation crosses a gating boundary.

## Four findings from the real page

1. **The snapshot archives the user's identity.** `Welcome, <name>` and
   `Subscription until <date>` are in the markup, so every gated
   RootsIreland capture writes the account holder's name and subscription status
   into `snapshot.html`, hashes it, ships it to the Anthropic API, and would put
   it in the capsule zip. "Verbatim transcription is sacred" governs the *record*,
   not the site's account chrome. The spec does not yet distinguish them.

2. **Gating auto-detection would call this page PUBLIC.** ✅ Fixed 2026-07-09.
   `_LOGIN_MARKERS` looked for `login`; this page has no login link. It has
   `logout.php`, `mysubscription.php`, `myaccount.php`, and the heritage centre's
   `© <year> Copyright …` footer. The inversion is the useful signal: *a login link means
   you are not authenticated; a logout link means you are.* `detect_gating` now
   inspects the body for authenticated-session markers, and
   `ProvidedContentFetcher` infers rather than assuming PUBLIC.

3. **The site demarcates its own record.** Everything that is not the record is
   wrapped in `class="noPrint"`. That is the site's own declaration, not a
   selector we invented. Unsuitable for extraction logic (no-selectors rule), but
   a legitimate basis for trimming the API payload — which also drops
   the account greeting and cuts token cost.

4. ~~**The record URL is not an identifier.**~~ **CORRECTED 2026-07-09.** I read a
   `<form method="POST">` in the markup and concluded the page was session-bound.
   That form is the print/save control, not the navigation. A real capture shows
   the record has a stable GET URL:
   `rootsireland.ie/ifhf/view_detail.php?recordid=<id>&type=<type>&recordCentre=<centre>`.
   The `recordid` is a durable locator and `archived_url` is meaningful after all.
   (The NLI URL likewise carries the leaf in its fragment — `#page/26/mode/1up` —
   so fragments must never be stripped.)

## Sequencing

- **Now, cheap:** preserve URL fragments (done — asserted in
  `test_empty_extraction_builds_a_capture_with_no_assertions`); have extraction
  emit `external_refs`. Recording a reference costs nothing at capture time and
  is unreconstructable later.
- **M2, with the matcher:** the `SourceRelation` contract, the dangling-reference
  resolver, register-level auto-bind, leaf-level hypothesis, and surfacing the
  linked pair in `captures/index.html`.
- **Unsequenced, safety-shaped:** findings 1 and 2 above. Neither is a feature.


---

# ⚡ SOLVED 2026-07-14 — the leaf is NOT a hypothesis. NLI resolves it itself.

Tested live on the very record this note was written from: **James Kilcoin × Bridget Brady,
Islandeady, 29 Apr 1852**, NLI register `vtls000632043`.

## The NLI viewer ships a date navigator

The register page carries a **"Filter Events/Dates"** control with three comboboxes and an Apply
button:

| control | contents |
|---|---|
| **Select Event** | `Marriages` *(the event types this register actually contains)* |
| **Select Year** | `1839 … 1880` *(exactly the register's span — the film's own target card reads "Marriages: Sept. 17, 1839 — Sept. 3, 1880")* |
| **Select Month** | `January…September, November, December` for Marriages/1852 — **October is absent** |

**The month list is derived from the actual entries.** October 1852 is missing because no marriage
was recorded that month. NLI has indexed which leaves contain which event-months.

## It lands on the leaf, deterministically

`Marriages` + `1852` + `April` → **Apply** → the URL becomes:

```
https://registers.nli.ie/registers/vtls000632043#page/26/mode/1up
```

**Leaf 26** — the exact leaf this design note guessed at. And the entry is there, in the left column:

> *April 29 — **James Kilcoin to Bridget Brady** — present Michael and Bridget Gibbons — 1 1 6*

**So leaf resolution is O(1), driven by data we already hold.** RootsIreland gives us the *event
type* and the *date*; NLI turns those into the leaf. No binary search, no HTR, no page-by-page hunt,
and — crucially — **no decoding of the centre's Notes field.**

## 🔴 CORRECTION — `1/1/6` is NOT a locator. It is money.

This note said:

> ~~"`1/1/6` is almost certainly a volume/page/entry reference in the Mayo centre's own encoding.
> We do not know that encoding."~~

**It is the marriage offering: £1 1s 6d.** It is written in the register's own fee column,
immediately after the witnesses — *"present Michael and Bridget Gibbons **1 1 6**"* — and
RootsIreland transcribed it verbatim into `Notes:`. The same page shows the column throughout
(`£1`, `4 6`, `5 0`, `1 2 6`).

The note's instinct was right for the right reason — *"decoding it by pattern-matching would be
exactly the inference this system exists to prevent"* — and the discipline paid: it parked the
ambiguity instead of resolving it, and the answer turned out to be **that there was nothing to
decode.** Nobody should spend another minute on it.

## What this changes

| claim | old evidence type | **new** |
|---|---|---|
| entry derives from NLI register `vtls000632043` | `stated` | `stated` (unchanged) |
| entry derives from **leaf 26** | ~~`estimated` at best~~ | **`calculated`** — from (event, year, month) via NLI's own index |

Leaf-level binding is no longer a hypothesis awaiting a human or HTR. It is **computed by the
authority itself**. The `SourceRelation.evidence_type` machinery is still right — but the leaf link
now arrives as `calculated`, not `estimated`, and can auto-bind.

**Caveat, stated honestly:** month granularity resolves to *the leaf containing that month's
entries*. Where a month spans two leaves, the target entry may be on the next opening — a one-click
check, not a search. And the derivative supplies the name and exact day to confirm it, exactly as
this note already argued.

## Also on the NLI viewer

- A **Download** button (the images are **public, free, permanent** — no subscription, no wall).
- The fragment **is** the locator (`#page/26/mode/1up`) — as this note already warned, **never strip
  fragments**.

## Consequence for sequencing — this matters for the RootsIreland clock

The perishable half is **RootsIreland** (reachable only while the researcher holds a subscription).
The NLI half is **free and permanent**. So:

> **Capture the RootsIreland index entry while you are viewing it — it carries the date, the event
> type, and the register id. The NLI leaves can be resolved and downloaded at leisure, for years,
> for nothing.**

The reference is the thing that rots. The image is not.

**And the hard asymmetry to remember:** for **pre-1848 Backs/Knockmore** there IS no NLI film — the
RootsIreland transcript is the *only* surviving witness, and no image exists or ever can. For those
records the derivative is terminal, and the linkage above simply does not apply.
