# BUG-019 — `page_label` is read from any dropdown on any site, and joins the dedup identity

**Status:** ✅ **FIXED 2026-10-21** (backend-side; RootsIreland's real record identity remains a
follow-up). Filed 2026-09-29 (found by the inbox/corpus check). · **Severity:** 🟠 **MEDIUM** —
wrong data in a contract field (`source.page_label`, capture-folder and `capsule-db-v1`), and in
the dedup key.

> ## ✅ FIXED — 2026-10-21
>
> - **The backend decides, at the top of `POST /capture`** (`accepted_page_label`,
>   `capsule/pipeline/page_identity.py`), before the dedup key, the ledger or the folder read
>   the label. That covers every extension already installed, so the extension was left as is.
>   A label survives only:
>   - with an `expected_apid` (the REQ-009 one-call flow naming a record's extra image);
>   - on a tile viewer or Riksarkivet, where it is the leaf in view;
>   - on RootsIreland, as a **legacy discriminator** (the trap below).
>   Everything else is dropped: NYC, Find a Grave, irishgenealogy.ie, Ancestry browse pages.
> - **Riksarkivet was NOT added to `TILE_VIEWER_HOSTS`**, against this report's suggestion.
>   The corpus showed why: on all three labelled Riksarkivet captures, `page_label_from` reads
>   the saved snapshot as **"Bild 1"** (the serialized `selected` attribute stays on the first
>   leaf, the REQ-007 §1 lesson), so making it a tile host would have mislabelled every capture
>   the extension did not label. Riksarkivet URLs already name the leaf, so only its LIVE label
>   is trusted, via a separate `LIVE_LABEL_HOSTS`.
> - **The trap is pinned, not solved.** RootsIreland keeps its search-form label because it is
>   the only thing separating records served at one search URL. A real record identity for
>   RootsIreland is still owed. No RootsIreland session was available, so this was reasoned from
>   the corpus, not a live session. A test shows two RootsIreland records at one URL are both
>   accepted.
> - **Behaviour change, intended:** the same NYC certificate captured twice is now a duplicate
>   whatever dropdown the page had (`remine` still overrides). The two NYC pairs this bug
>   noted were captured under the old rule and are untouched.
> - 18 tests in `tests/test_bug_019_page_label_scoping.py` (host table, end to end through
>   `/capture`, dedup with and without a label, the RootsIreland trap); mutation-checked.
>   Codex adversarial review: approve. Existing captures keep their labels (append-only); a
>   label on a non-tile, non-RootsIreland host written before 2026-10-21 is unreliable.

## What happens

`extension/content.js` `livePageLabel()` (REQ-007 §1) picks the page label from the **live DOM**:
the `<select>` with a `page-select` class wins, and otherwise the one with **the most options**.
Its selected option's text becomes `page_label`. The only guard is at `content.js:229`: no label
when there's an apid or an Ancestry record URL.

**It is not scoped to tile-viewer hosts.** The backend's own reader, `page_label_from`
(`capsule/pipeline/page_identity.py`), returns None unless `is_tile_viewer_source(url)`. But the
backend accepts the extension's `req.page_label` as sent, so the host scoping never applies to
it.

## Evidence in the corpus (2026-09-29)

31 of 446 captures carry a `source.page_label`. Only 2 are real page identifiers:

| Host | Captures | Label recorded |
|---|---|---|
| rootsireland.ie | 14 | the parish chosen in the search form, e.g. `BURRISCARRA RC PARISH (Roman Catholic)`, `3 years` |
| a860-historicalvitalrecords.nyc.gov | 11 | `Select Language` |
| findagrave.com | 3 | `Cemetery office has no record of this person` |
| ancestry.com (`/imageviewer/` browse pages, no apid) | 2 | `Fayette` (the county browse dropdown) |
| irishgenealogy.ie | 1 | `-` |
| sok.riksarkivet.se | 1 | `Bild 210 / Sida 400` ✅ correct |
| archion.de | 1 | `6` ✅ correct |

The Ancestry row also falsifies a claim in `record_identity`'s docstring (`capsule/pipeline/dedup.py`):
*"the extension never sends a generic page-selector label from an Ancestry page"*. That holds for
record pages but not for image-viewer browse pages, which have neither an apid nor a record URL.

## Consequences

- **Wrong data in a contract field.** `source.page_label` is meant to be *"the source's own
  identifier for the image in view, verbatim"*. Downstream consumers (the captures index,
  `capsule-db-v1`) read "Select Language" as a page identifier.
- **Dedup identity.** `record_identity` appends `#page=<label>` whenever a label is present. A
  junk label can only *split* identities, never merge them, so the failure is a false "new"
  (a re-capture), never a false block. Two same-URL pairs may have slipped through this way:
  NYC `/view/608725` (`20260710-…-4fb82c43` unlabelled, `20260925-…-02b6b397` "Select Language")
  and `/view/4009369` (`20260924-…-3d1780da`, `…-28916b90`). Not yet checked whether those
  second captures were legitimate repairs.

## ⚠️ The trap in the obvious fix

Host-scoping the extension's label to `TILE_VIEWER_HOSTS` is the natural fix, **but on
RootsIreland the junk label is currently doing real work.** Its search results are served at
one URL for every record (`rootsireland.ie/mayo/quis.php?page=0&prevStartQuery=0` holds five
captures of different records). The parish label is the only thing keeping those five
identities apart. Drop it and the next RootsIreland capture from that URL is refused as
*"already mined"*, which is the evidence-losing direction.

So a correct fix needs both halves:

1. **Scope the extension's label to tile-viewer hosts** (the same host list as the backend, or
   let the backend discard a label for a non-tile host). Also skip Ancestry `/imageviewer/`
   pages. **`sok.riksarkivet.se` is not in `TILE_VIEWER_HOSTS` today**, so scoping alone would
   drop its one correct label. Add it there, together with BUG-018's Riksarkivet profile.
2. **Give URL-less-identity sites a real identity first.** RootsIreland needs a record identity
   that is not its search URL (e.g. the record id from the result page's content). Until then,
   RootsIreland at least must keep a distinguishing key. RootsIreland access expired
   2026-09-21, so no new captures are expected there right now, which makes this less urgent
   but still worth doing right.

Existing captures keep their labels (append-only; the folder is the record). The fix affects
new captures only, plus an optional note in the capture-folder spec that labels on non-tile hosts
before this fix are unreliable.

## Definition of fixed

- New captures on NYC, Find a Grave, irishgenealogy.ie and Ancestry browse pages carry no
  `page_label`. Archion, NLI, Matricula and Riksarkivet still do.
- A RootsIreland capture of a second record from the same search URL is still accepted as new.
- Tests at both layers: the extension's label scoping, and `record_identity` for a
  non-tile host with and without a label.
