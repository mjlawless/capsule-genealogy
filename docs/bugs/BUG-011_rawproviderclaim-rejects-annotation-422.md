# BUG-011 — Ancestry image capture fails: `HTTP 422: provider_claims.N.annotation: Extra inputs are not permitted`

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-15)
> **The named record recovered, in the same session.** `7488:15429093` (an
> SS Rhein 1871 passenger manifest) — the exact record this 422 blocked — shows four failures
> and then a clean capture:
>
> | time (UTC) | event | result |
> |---|---|---|
> | 21:27–21:29 | `failed` ×4 | `extraction reply does not match schema` — this bug |
> | 21:41:47 | `started` | |
> | **21:42:04** | **`captured`** | `20260715-ancestrycom-c8cf6403` — **`image_status: full`, 11 assertions, `verify_ok: 1`** |
>
> That is the "re-capture the SS Rhein manifest cleanly to replace the workaround" this bug
> asked for. The annotation path it restored is live in the store: a provider claim carrying a
> non-empty `annotation` is persisted (`"Original data block links to
> '/search/collections/7488/moreinfo'…"`) — the field that used to 422 now round-trips to disk. · **Severity:** 🟠 **HIGH — blocks full-image capture on any record whose "Original data" block the extractor annotates**

> **Fix landed:** `RawProviderClaim` now carries `annotation: Optional[str] = None`
> (`capsule/pipeline/extract.py`), and `build.py` threads it through
> `_flag_unverified(rp.annotation, located)` — matching the RawAssertion / RawCoverage
> paths, so the note is preserved AND the unverified marker still applies (the report's
> suggested `annotation=raw.annotation` would have dropped that marker). Four regression
> tests in `tests/test_provider_claims.py` (the exact 422 shape, the raw-model unit, a
> through-build survival check, and a contract test that the three raw claim models keep
> the same optional-`annotation` shape). Full suite green (100 passed). Re-capture the SS
> Rhein manifest cleanly to replace the workaround.
**Found:** 2026-07-15, Genealogy research session (an SS Rhein 1871 passenger manifest, Ancestry coll. 7488 record 15429093)
**Class:** same family as BUG-002 — a strict `extra="forbid"` schema rejecting a field a producer legitimately emits. Different producer (the LLM **extractor**, not the extension) and different model (`RawProviderClaim`).

---

## Symptom

The canonical Ancestry image flow (`capsule:arm-download` → click Ancestry's Download → `capsule:capture {apid, image_intent:"expected"}`) fails at the capture step. Toast:

```
Capsule: HTTP 422: invalid request: extraction reply does not match schema:
1 validation error for RawExtraction
provider_claims.2.annotation
  Extra inputs are not permitted [type=extra_forbidden,
   input_value="The Original data block ... is spelled out inline.", input_type=str]
```

Reproduced **twice, deterministically** on the same record (arm OK → "Armed. Click the viewer's Download now."; Ancestry Download succeeded; capture 422s). Every attempt's extractor reply landed in `data/captures/_failed/*-extractor-*.json`. Net effect: **no full-resolution image can be captured for this record** — the only artifact obtainable was an index-only capture with a 525×600 **thumbnail** (via the floating-button path, which apparently took a code path that didn't emit `annotation` that run — so the bug is intermittent-by-LLM, not path-specific).

## Root cause — exact

The extractor (`agent: extractor`, `claude-opus-4-8`) returned `provider_claims` where one entry carries an `annotation` key:

```
pc[0] keys: ['key', 'value', 'snippet']
pc[1] keys: ['key', 'value', 'snippet']
pc[2] keys: ['key', 'value', 'snippet', 'annotation']   <-- "The Original data block links to
                                                             '/search/collections/7488/moreinfo'
                                                             captioned 'View Sources' … inline."
pc[3] keys: ['key', 'value', 'snippet']
pc[4] keys: ['key', 'value', 'snippet']
```

`annotation` is a **first-class field everywhere except on the raw provider-claim model**:

- `capsule/pipeline/extract.py` — `RawAssertion.annotation` (line 80) ✓ and `RawCoverage.annotation` (line 126) ✓ both exist…
- …but **`RawProviderClaim` (line 129) has only `key`/`value`/`snippet`** and `model_config = ConfigDict(extra="forbid")` (line 140). **No `annotation`.**
- `capsule/contracts/evidence.py` — the canonical **`ProviderClaim` DOES have `annotation: Optional[str] = None`** (≈ line 397).
- `capsule/cli/app.py:238-239` — the CLI **prints** `claim.annotation` for provider claims.
- `capsule/capture/manifest.py:209` — notes that `annotation` fields "still emit as `null`" for provider claims.

So the extractor's prompt teaches `annotation` as a general normalization/commentary field (extract.py docstring lines 230/233; JSON examples 310/329), the canonical model and the CLI expect it on provider claims, but the **raw ingestion model silently lacks it and `extra="forbid"` turns the mismatch into a hard 422.** The LLM adds `annotation` to a provider claim whenever the "Original data" block invites commentary (e.g. "the source is spelled out inline / links to /moreinfo") — which is common — so this will recur across records, non-deterministically.

## Fix — add the field to the raw model (and thread it through)

`extra="forbid"` is the right trust boundary (per BUG-002). The producer side is already "correct" — the extractor is emitting a field that is valid on the canonical `ProviderClaim`. The raw model is the outlier:

```python
# capsule/pipeline/extract.py — RawProviderClaim (≈ line 142)
class RawProviderClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    value: str
    snippet: str
    annotation: Optional[str] = None          # <-- ADD, matching RawAssertion / RawCoverage
```

Then thread it into the canonical build so the annotation is not dropped:

```python
# capsule/pipeline/build.py ≈ line 291
ProviderClaim(
    ...,
    annotation=raw.annotation,                # <-- pass it through (canonical ProviderClaim already has the field)
)
```

(Confirm the `ProviderClaim(...)` call at build.py:291 currently omits `annotation` — if so it defaults to `None` and the extractor's note is lost even once ingestion accepts it.)

## Why this matters

It blocks banking **full-resolution** record images — the actual evidence — while leaving a green-ish path (index-only + thumbnail) that *looks* like a capture. That is precisely the "believing you hold a record you do not hold" failure the runbook warns about. In this session it blocked the load-bearing immigration manifest that **reverses a committed genealogical conclusion** (an immigrant's place of origin), so the evidence integrity here is not cosmetic.

## Suggested regression test

```python
def test_BUG_011_raw_provider_claim_accepts_annotation():
    """Regression: the extractor may annotate a provider claim (e.g. the 'Original data'
    block); RawProviderClaim must accept `annotation` like RawAssertion/RawCoverage do."""
    rc = RawProviderClaim(key="original_data", value="NARA M237, roll 351",
                          snippet="Original data: …", annotation="spelled out inline")
    assert rc.annotation == "spelled out inline"

def test_BUG_011_extraction_reply_with_annotated_provider_claim_is_accepted():
    """The exact shape that 422'd: a provider_claims entry carrying `annotation`."""
    RawExtraction.model_validate({
        "personas": [], "assertions": [], "coverage": [], "external_refs": [],
        "provider_claims": [{"key": "original_data", "value": "…", "snippet": "…",
                             "annotation": "the source is spelled out inline"}],
    })  # must not raise
```

Better still, a contract test asserting `RawAssertion`, `RawCoverage`, and `RawProviderClaim` share the same optional-`annotation` shape, so the three raw claim models don't drift again.

## Repro

1. Log in to Ancestry in the browser; navigate to record `1,7488::15429093` (SS Rhein passenger list).
2. Open the image viewer; `capsule:arm-download {apid}` → "Armed."; Tools → Download; back on the record page `capsule:capture {apid, image_intent:"expected", remine:true}`.
3. Toast → `HTTP 422 … provider_claims.2.annotation … Extra inputs are not permitted`.
4. Offending reply: `data/captures/_failed/20260715-2128*-extractor-*.json` (response `provider_claims[2]` has `annotation`).

## Interim workaround used by the research session

The full-resolution NARA scan was obtained via Ancestry's own **Download** (saved as `NYM237_351-0325.jpg`, 1636×1868) and the **record page** was archived by Capsule (`data/captures/20260715-ancestrycom-032620c0`, record PDF + thumbnail). The genealogy side can hash the downloaded full image into its own evidence store, but this is a **workaround**, not an `IMAGE ✓ full` Capsule capture — re-capture cleanly once this is fixed.
