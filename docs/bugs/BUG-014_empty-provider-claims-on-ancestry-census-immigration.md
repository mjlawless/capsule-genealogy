# BUG-014 — `provider_claims` come back empty on Ancestry census / immigration records

**Status:** FIXED — **E2E VERIFIED 2026-07-16** · **Severity:** 🟢 LOW-MEDIUM — degrades layered-citation quality

> ## ✅ E2E VERIFIED 2026-07-16 (Genealogy session)
> Fresh capture `20260716-ancestrycom-e9f28b27` (1920 census, record 54221383):
> - `manifest.json` → `provider_claims` block present and **fully populated** — `source_citation`
>   ("…Roll: T625_1236; Page: 6B; Enumeration District: 401"), **`original_data`** ("…NARA microfilm
>   publication T625, 2076 rolls…"), source_information, description, repository. ✅
> - `citation.json` reference_note now carries the layered **`; citing Fourteenth Census … NARA
>   microfilm publication T625 …`** — names the microfilm, not just the Ancestry database. ✅
> The Genealogy side no longer hand-transcribes the roll/ED/NARA citation — it comes from the capture.

**Found:** 2026-07-15, Genealogy research session

## Corrected diagnosis + fix (2026-07-16)

The title is a misdiagnosis. Inspecting the two cited captures on disk, the extraction was
**never empty** — `extraction.json` holds the values in full, and `ProviderClaim.verbatim_value`
has a non-blank validator, so an empty one cannot even be stored:
- `ce2ca4d3` (census) → `original_data` = *"Tenth Census of the United States, 1880. (NARA
  microfilm publication T9, 1,454 rolls)…"* — verbatim the value the report called `''`.
- `c8cf6403` (immigration) → `source_citation` = *"…Microfilm Serial or NAID: M237; RG Title:
  Records of the U.S. Customs Service; RG: 36"*.

The real gap: the **consumer reads `manifest.json` and `citation.json`, not the raw
extraction**, and neither surfaced the provider's blocks — so from the Gramps/Genealogy seat
the microfilm "was not in the capture" and got hand-transcribed. (It likely read as empty at
filing time because BUG-011, `RawProviderClaim` rejecting `annotation` → 422, was fixed the
same day; those captures post-date the fix.)

**Fix — surface them where the consumer reads (both):**
1. **`manifest.json`** now carries an additive `provider_claims: [{key, value}]` block
   (`capsule/capture/manifest.py`) — R4.3, the folder can name the original without the DB.
2. **The citation** now folds the provider's `original_data` (else `source_citation`) into the
   reference note as a `; citing …` layer (`capsule/pipeline/citation.py` `_provider_citing`,
   threaded via `build.py`), so it names the NARA microfilm — a *layered* citation, not a
   derivative one. An external-ref citing layer still wins when present.

Both additive (omitted when there are no provider blocks → existing captures byte-identical).
Regressions: `tests/test_provider_claims.py::test_BUG_014_*`.

## Symptom

For Ancestry census and passenger-list records, the extraction's `provider_claims` are present as keys
but **valued empty**, so the record's own "Source Citation" and "Original data" text (NARA roll,
Enumeration District, T-number, microfilm) is not captured.

Examples 2026-07-15:
- capture `c8cf6403` (1871 SS Rhein manifest): `provider_claims` = source_citation `''`,
  source_information `''`, description `''`, repository `''`.
- capture `ce2ca4d3` (1880 US census): source_citation `''`, source_information `''`,
  original_data `''`, description `''`, repository `''`.

Yet the record pages plainly show, e.g.:
> Source Citation — Year: 1880; Census Place: New York City …; Roll: 879; Page: 463b; Enumeration
> District: 263
> Original data — Tenth Census of the United States, 1880. (NARA microfilm publication T9 …)

## Why it matters

The **`original_data` line is the whole point of provider_claims** (per the ProviderClaim docstring): it
lets a citation name the **microfilm / NARA publication** rather than the Ancestry database — the
difference between a derivative citation and a layered one. Empty values mean the Genealogy side must
hand-transcribe the roll/ED/T-number from the page instead of getting it from the capture.

## Root cause hint

The extractor emits the provider_claim keys but isn't pulling the values from the "Source Citation" /
"Source Information" / "Original data" blocks on Ancestry record pages (they're consistently present and
labelled). Possibly these blocks are collapsed behind a "Show more" / "Source" accordion the extractor
doesn't expand before reading.

## Suggested fix

Populate provider_claim values from the record page's Source section (expand the "Source" accordion if
needed). At least `source_citation` (roll/page/ED) and `original_data` (NARA publication) should be
captured verbatim.

## Repro

Capture any Ancestry US-census or passenger-list record; inspect `extraction.json` → `provider_claims`:
keys present, values empty, while the page's Source section has the roll/ED/NARA text.
