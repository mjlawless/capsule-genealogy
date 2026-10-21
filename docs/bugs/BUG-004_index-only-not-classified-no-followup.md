# BUG-004 — Index-only records are not classified, and no follow-up is emitted (REQ-001 R4)

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-14, `1b33609`)
> **The exact re-test this bug asked for, run 2026-08-24 against the live ledger.** Record
> `3693:35684453` shows the fix landing mid-session:
>
> | time (UTC) | capture | `image_status` | follow-up |
> |---|---|---|---|
> | 18:21:12 | `…3859c474` | `failed` | — |
> | 18:21:43 | `…a3b90ff3` | **`None`** — the dangerous NULL path | — |
> | 18:46:42 | `…c7941add` | **`index-only-no-image`** | **"Social Security Administration. Social Security Death Index, Master File."** |
>
> And `capsule ancestry followups` lists it, as this bug required. R4.2 generalizes: **20
> follow-ups are derived across the corpus**, each naming a real source read from the record's
> own `Original data` line (NPS, Washington State Archives, FamilySearch, Find a Grave, NYC
> DORIS, NY State DOH) — never a hard-coded table. · **Severity:** 🔴 **HIGH — silently closes a record whose true source was never chased**

> ## FIXED — 2026-07-14
>
> The diagnosis was exactly right, including that *both* paths were wrong and that the
> NULL-status path was the dangerous one. Implemented as prescribed:
>
> **R4.1 — the PAGE classifies, not the caller.** An image-backed record links to its scan
> (`/imageviewer/collections/<db>/images/<id>`); an index-only record does not. So "does an
> image exist" is now read from the record (`capsule/pipeline/ancestry.py:has_record_image`),
> and `image_intent` is ignored where it disagrees. A caller who says `expected` about an
> index is simply mistaken, and the record wins — so no operator can mislabel a record into
> a permanently stuck state, exactly as the report asked.
>
> **`mined` is now an ALLOW-list** (`{full, index-only-no-image}`), not a deny-list. That
> closes path B: unclassified is not "fine", it is unclassified. **And an index-only record
> is not mined until it names its true source** — taking the index is not taking the record.
>
> **R4.2 — the follow-up is DERIVED from the record's own `Original data` line**, which the
> extraction already captures verbatim as a provider claim. Never a hard-coded table: 284
> databases are in play, so a lookup would be wrong for most and silently stale for the
> rest. When a page does not state its true source, we record *that we could not read it* —
> which keeps the record open rather than closing it on a silent `None`.
>
> **R4.3 (the "Related" note) — the manifest now carries `image` and the media's MEASURED
> `width`/`height`.** The capture folder is self-describing on disk: move it, lose the
> database, open it in twenty years, and it still says whether the image inside is the
> record or a screen-resolution stand-in.
>
> Regressions: `tests/test_bug_003_004.py` (classification from the page; the follow-up
> naming the SSA; a NULL status can never be mined; an index without a follow-up is not
> mined; an image-backed record whose scan never arrived is still `failed`) plus the
> ledger-level cases. 572 tests green.
>
> **Still owed: your re-test** on `3693:35684453` — expect `index-only-no-image`, a
> follow-up naming the Social Security Administration's file, and `capsule ancestry
> followups` listing it.
**Found:** 2026-07-14, adversarial acceptance test of REQ-001 (negative control)
**Test record:** `3693:35684453` — U.S., Social Security Death Index (index-only, no image)

---

## Acceptance criterion (REQ-001 §3, negative control)

> Run it on an **index-only** record — e.g. `3693:35684453` (U.S. Social Security Death Index) — and
> it must produce `image: index-only-no-image` plus a **follow-up naming the SS-5 application**,
> without pretending it captured a source image.

**It does neither.** And both available paths are wrong.

## What actually happens

| path | `image_status` | mined? | follow-up |
|---|---|---|---|
| capture with `image_intent: "expected"` | **`failed`** | no — **UNMINED** | none |
| capture with **no** `image_intent` | **`NULL`** | **YES — MINED** | **none** |

Ledger (`data/capsule.db`):
```
('3693:35684453', 'captured', 'failed', 7, 1, None)     <- image_intent: expected
('3693:35684453', 'captured', None,     7, 1, None)     <- no image_intent  -> counts as MINED
```
```
$ capsule ancestry followups --db data/capsule.db
no outstanding follow-ups
```

### Why each path is wrong

**Path A — `failed` is a lie, and it jams the queue forever.**
`failed` means *we tried and missed a retrievable image*. There **is no image** — the SSDI is an index.
Under the (correct) mined rule — *"full-resolution or legitimately absent"* — this record can **never**
reach `mined`. It will sit `UNMINED / failed` in perpetuity, and an operator working the queue will
retry it forever.

**Path B — silently MINED, and the SS-5 is lost. This is the dangerous one.**
The record closes with **no image status and no follow-up**. We have now "mined" the Social Security
Death Index and **thrown away the knowledge that the SS-5 application is the actual source.**

That is the same failure the ledger exists to prevent, displaced from images to true sources:
> **We believe we hold the source, when what we hold is an index of it.**

It also directly breaks the requirements author's constraint 3:
> *"If it's an index, we can mark it as a followup to gain the true source, unless the true source
> is already built in."*

## Scale — this is a third of the work, not an edge case

Of the 23 Ancestry records on the first pilot person alone, **8 are
index-only**, each pointing at a different true source:

| index record | the TRUE source it points at |
|---|---|
| U.S., Social Security Death Index | the **SS-5 application** |
| U.S., Social Security Applications & Claims Index | the **SS-5 application** |
| NY, New York, Birth Index 1878–1909 | the **birth certificate** (NYC Municipal Archives) |
| NY, New York, Index to Birth Certificates 1866–1909 | ditto |
| NY, New York, Marriage Index 1866–1937 | the **marriage certificate** |
| NY State Death Index 1957–1969 | the **death certificate** |
| U.S., Newspapers.com Obituary Index ×2 | the **obituary page image** |
| U.S., Find A Grave Index | the **headstone photo / sexton record** |

Extrapolated over a run of hundreds of records, a large fraction would either jam the queue or close silently
with their true source forgotten.

## What is needed

**R4.1 — CLASSIFY from the page, don't just obey the caller.** `image_intent` is the caller's
*expectation*; whether an image exists is a *fact about the record*. Capsule must determine it —
the record page for an image-backed record carries a viewer link / image id (`4114737_00288` on the
census); an index-only record has none. Classify, then reconcile with the caller's intent:

- image exists + obtained → `full`
- image exists + not obtained → `failed` *(correctly stays UNMINED)*
- **no image exists → `index-only-no-image`** → **counts as MINED** (legitimately absent) **and MUST
  emit a follow-up**
- caller said `expected` but no image exists → **not an error** — classify `index-only-no-image` and
  say so. (Belt and braces: this also means an operator can't mislabel a record into a stuck state.)

**R4.2 — emit the follow-up, naming the true source.** The name is already on the page and Capsule is
already capturing it: `provider_claims.source_information` / the **`Original data`** line. For the
SSDI that text names the Social Security Administration's application file. **The follow-up must be
derived from the record, not from a hard-coded table** — 284 databases are in play.

**A record must not be closable as `mined` with `image_status = NULL`.** That state is what allowed
path B. Make the column non-nullable, or treat NULL as "not classified → not mined".

## Related

The manifest also does **not** record `image_status` or pixel dimensions (REQ-001 R4.3 says the
manifest MUST). Today it lives only in the ledger DB. A capture folder should be self-describing on
disk — if the DB is lost or the folder is moved, the capture must still say what its image is.


---

# ✅ RE-TESTED BY THE REPORTER — 2026-07-14. FIXED.

Re-captured `3693:35684453` **while deliberately declaring `image_intent: "expected"` — i.e. with a
caller who is WRONG.** The record won:

```
toast   : Captured death index - 7 assertion(s), verify OK, PDF ✓, index-only (no image)
ledger  : ('3693:35684453', 'index-only-no-image', 7, 1,
           'Social Security Administration. Social Security Death Index, Master File...')
status  : 2 mined / 3 known          <- legitimately absent image => MINED
followups: 3693:35684453 - Social Security Administration. Social Security Death Index, Master File.
```

| criterion | result |
|---|---|
| page classifies, not the caller (R4.1) | ✅ caller said `expected`; the record said index-only, and **the record won** |
| `index-only-no-image` | ✅ |
| counts as MINED (legitimately absent) | ✅ |
| follow-up naming the true source (R4.2) | ✅ **derived from the record's own `Original data` line** |
| `followups` lists it | ✅ |
| an image-backed record with no scan is still `failed` and UNMINED | ✅ `7884:19775160` unchanged |
| identity guard still refuses | ✅ regression re-run: `IDENTITY MISMATCH … NOTHING was captured` |

## R4.3 — the manifest is now self-describing. Verified against the file, not the claim.

Re-mined the census (`7602:56510272`) after the fix:

```
manifest   image: full
           media[1] media/1-4114737-00288.jpg (ancestry:download)  width=3960  height=3928
decoded    1-4114737-00288.jpg: 3960 x 3928        <- the file agrees with the manifest
```

Move the folder, lose the database, open it in twenty years — it still says what its image is.

## One deliberate note in the builder's favour

My spec said the follow-up must "name the **SS-5 application**". It doesn't — it names the
**SSA Master File**, because *that is what the record's `Original data` line actually says*.
**The tool reported the page rather than my assumption, and that is correct.** R4.2 explicitly
required derivation from the record, never a hard-coded table. Getting from "SSA Master File" to
"order the SS-5" is a genealogist's judgement, not the tool's. **Leave it as it is.**
