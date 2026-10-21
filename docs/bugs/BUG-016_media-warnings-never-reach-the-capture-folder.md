# BUG-016 — `media_warnings` never reach the capture folder: a blank render sits on disk looking clean

**Status:** ✅ **FIXED 2026-08-24** — both halves landed, full corpus swept, 711 tests green ·
**Severity:** 🟠 **HIGH — the archive does not record what the system knew about it**

> ## ✅ FIXED — 2026-08-24
>
> **1. Going forward: the manifest records it.** `media_warnings` is threaded
> `backend/app.py` → `run_capture` → `write_capture` and written into `manifest.json`,
> additive and omitted when empty — so the 240 existing captures serialize byte-for-byte as
> before. It is the same pass-through `image_status` and `page_label` already use.
>
> **2. Retroactively: `verify_capture` re-measures.** `VerifyResult` gained a `warnings`
> tuple, re-derived from the bytes on disk rather than read back from the manifest. That is
> what makes it retroactive — a capture banked before the field existed still reports its
> blank render. Confirmed on the real capture:
>
> ```
> verify_capture("20260718-rootsirelandie-98854fa1").warnings
>   → ("media/0-record.pdf renders BLANK — something was composited over the page…",)
> manifest has media_warnings key? False   # pre-fix capture, left untouched
> ```
>
> **Nothing archived was rewritten.** Backfilling 240 manifests would have pre-empted the
> owner ruling still open at REQ-007 §8 (whether a capture may annotate its own archived
> evidence). Re-measuring answers the same question without touching a byte.
>
> **`warnings` deliberately does NOT gate `verify_ok`.** It is the only field on
> `VerifyResult` that does not. A blank render is a real problem, but the snapshot, the
> extraction and the hashes remain real evidence — BUG-001 settled this ("the capture itself
> still succeeds; it is the SUCCESS REPORT that was lying"). Folding it into `ok` would
> retroactively fail captures that hold their record perfectly well: the BUG-006 false-FAIL
> mistake in a new costume.
>
> **3. The full sweep — the open question is closed.** All **208** `render:page-pdf` files
> measured (not the 45 originally sampled), **0 unjudgeable**. **Exactly one is blank**, the
> one this bug was filed about. The threshold is not marginal: the next-closest render sits
> at **0.9659**, a wide gap below the 0.99 gate, so there is no borderline cluster waiting to
> flip.
>
> **A nuance found while verifying, worth recording.** That capture is *no longer* silently
> clean: REQ-007 §3.3 (which landed 2026-08-22, five weeks after the capture) now fails it
> for holding no measurable image. But §3.3 says *"it holds no measurable image at all"* —
> not *"this render is blank"* — and it only applies to the no-text-evidence class. **A
> record with 20 assertions and a blank page render would still sail past §3.3 with the
> blank render unrecorded.** That residual gap is what this fix closes, and it is why the
> fix is a warning rather than another sufficiency rule.
>
> Regression tests: `tests/test_bug_016_media_warnings_persist.py` — 9 cases covering the
> manifest write, the omitted-when-empty shape, the retroactive re-derivation from a
> pre-fix manifest, that warnings never gate `ok` or get confused with `insufficient`, that
> a near-white scan the *site* served is never warned about (no crying wolf), and an
> end-to-end `POST /capture` round-trip that pins the **arrival** of the value on disk —
> the `postAndShape` lesson, since the vacuous version of that test is what let this bug
> exist. **711 tests green.**
**Found:** 2026-08-24, verifying BUG-001 against the collection
**Component:** `capsule/backend/app.py` (`media_warnings`) → `manifest.json`
**Class:** the same shape as REQ-007's `postAndShape` lesson — a fact computed at capture time
that never reaches durable storage.

## Symptom

`data/captures/20260718-rootsirelandie-98854fa1/media/0-record.pdf` is a **blank page render**:

```
blank_ratio  = 0.9914247835039914
BLANK_THRESHOLD = 0.99
looks_blank()   = True
```

BUG-001's visual gate is working — it measures this file correctly today. At capture time it
would have returned a `media_warnings` entry and the toast would have read **`PDF ✗`**.

**But the capture folder carries no trace of that.** Its `manifest.json` keys are:

```
spec_version, capture_id, tool_version, created_at, source, archive, media, redactions, hashes
```

There is no `media_warnings` key — **in this capture or in any of the other 239**. The warning
existed only in the HTTP response and in a toast that has long since disappeared.

## Why it matters

This is the failure mode `CLAUDE.md` §12.4 and BUG-001 itself are about: *the archive looks
clean while the system knew it was not.* Concretely:

- Open this capture in a year and nothing says the render is blank. The hashes verify, the
  manifest is well-formed, `verify_ok` was true at the time.
- It directly contradicts the principle BUG-004 R4.3 established and this project keeps
  restating: **the capture folder must be self-describing on disk** — "move it, lose the
  database, open it in twenty years, and it still says whether the image inside is the record
  or a screen-resolution stand-in." Measured `width`/`height` were persisted for exactly this
  reason. The warnings were not.
- It makes BUG-001's gate **unauditable retroactively**. The only reason this was caught is
  that the gate's own code could be re-run over the corpus. A warning that only ever existed
  in a toast cannot be swept for.

## Root cause

`media_warnings` is assembled in `_capture` (`capsule/backend/app.py:1219–1258`) from
`_check_rendered_media`, `_mistyped_media` and the image probes, then passed to
`CaptureResponse` (`:1347`) and to the outcome shaper (`:1456–1464`). **It is never written
into the manifest.** Nothing persists it.

Note the deliberate design point this must NOT disturb: REQ-007 §3.3 records that
`media_warnings` is documented as *problems that do not invalidate the capture*, which is why
`verify_shortfall` was kept separate from it. Persisting warnings does not change that
contract — it only makes the warning durable.

## Suggested fix

1. **Write `media_warnings` into `manifest.json`** as an additive, omitted-when-empty key —
   the same shape `verify_shortfall` uses on `result.json`. Additive keeps `capture-folder/v2`
   readers working.
2. **Decide the retroactive question.** `capsule verify` already re-asks integrity questions of
   old captures (that is what made REQ-007 §3.3's floor retroactive). The blank-render check
   could be re-run the same way, so a capture banked before this fix still reports the problem
   rather than needing the original toast. *Recommended — it is what turned §3.3 from a
   going-forward rule into a corpus-wide one.*
3. **Then sweep the corpus** for other renders at or above the threshold. Only 45 of 208 page
   renders were sampled on 2026-08-24; a full pass may find more.

## Acceptance

- A capture whose page render is blank carries the warning **in `manifest.json`**, not only in
  the response.
- `20260718-rootsirelandie-98854fa1` — or a re-verify of it — reports the blank render from
  disk alone, with no reference to the original session.
- The full set of 208+ `render:page-pdf` files is measured and any others at ≥0.99 are named.
- `media_warnings` stays distinct from `verify_shortfall` (REQ-007 §3.3): warnings do not
  invalidate a capture; shortfalls do.

## Evidence

Measured 2026-08-24 with `capsule.pipeline.render_check` over the live collection:

| | value |
|---|---|
| `render:page-pdf` files on disk | 208 |
| sampled | 45 |
| at or above the 0.99 gate | **1** — `20260718-rootsirelandie-98854fa1` (0.9914) |
| captures carrying `media_warnings` in the manifest | **0 of 240** |
| typical Ancestry record page | 0.95–0.97 (text-light, correctly *not* flagged) |
| content-heavy renders | 0.49–0.63 |

Related: **BUG-001** (the gate that produces the warning), **REQ-007 §3.3** (the
`verify_shortfall` field that got the persistence right), **BUG-004 R4.3** (the
self-describing-folder principle).
