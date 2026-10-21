# BUG-012 — Floating Capsule button silently captures the INDEX + a thumbnail on image-backed records

**Status:** FIXED — **E2E VERIFIED 2026-07-16** · **Severity:** 🟠 HIGH — a "believing you hold a record you do not hold" footgun

> ## ✅ E2E VERIFIED 2026-07-16 (Genealogy session)
> Fired a plain `capsule:capture {apid:"1,6061::24407336", image_intent:"expected"}` (no arm-download)
> on an image-backed 1920-census record page — the floating-button footgun scenario.
> - Toast: **`Captured census — 24 assertion(s), verify OK, PDF ✓, IMAGE ✗ failed — NOT mined`** ✅
> - On disk (`20260716-ancestrycom-6faa0e7f`): media = **only `0-record.pdf`**; **no thumbnail jpg
>   written** ✅ (media-detect skipped the `/thumbnail/` URL).
> Both layers confirmed: the backend `image_status` floor reddens the toast, and the thumbnail is
> never banked. The silent-thumbnail path is closed.

**Found:** 2026-07-15, Genealogy research session
**Class:** same lesson as BUG-001/the runbook's cardinal rule — *a green toast is not proof; a thumbnail is not the record.*

## Fix (2026-07-15)

Two independent layers, so a thumbnail can neither be banked nor pass as success:

1. **Backend (authoritative) — `capsule/backend/app.py`.** `image_status` was derived only
   when the caller supplied an `expected_apid`; the floating button carries none, so the
   check was skipped entirely. It now derives for **any Ancestry record page** (detected from
   the URL via `record_ids_from_url`), so an image-backed record with no full-resolution
   download measures `failed` — the toast reddens and reads `IMAGE ✗ failed — NOT mined`
   instead of a clean success. Non-Ancestry captures still yield `None` (unchanged).
2. **Content — `extension/media-detect.js`.** The in-page media sweep now skips any URL whose
   path contains a `/thumbnail(s)/` segment (Ancestry's `.../retrieval/v2/thumbnail/...`
   endpoint), so the ~600px preview is never written to disk as `page:embedded` record media.

Regression tests: `tests/test_bug_012.py` (image-backed → `failed`; index-only →
`index-only-no-image`; non-Ancestry → `None`). REQ-002 supplies the *ideal* fix (option (a):
run the full arm→download flow automatically) on top of this floor.

## Symptom

Clicking the on-page floating **`⬇ Capsule`** button while on an image-backed Ancestry **record page**
produces a capture that *looks* successful but whose image is only the **~525×600 thumbnail**, not the
full-resolution record scan.

Observed 2026-07-15, capture `20260715-ancestrycom-032620c0` (SS Rhein passenger manifest,
record 15429093):
- `media/0-record.pdf` — the record-page render (fine)
- `media/1-nym237-351-0325.jpg` — **525×600**, `provenance: page:embedded`, `source_url:
  .../api/media/retrieval/v2/**thumbnail**/namespaces/7488/media/NYM237_351-0325.jpg`

The URL literally contains `/thumbnail/`. The full image (1636×1868) was only obtained afterward via
the arm-download → Ancestry-Download → capture-from-record-page flow (capture `c8cf6403`).

## Why it matters

The button path is the *obvious* thing a user or agent clicks. On an image-backed record it silently
banks a thumbnail and reports success — exactly the failure the runbook warns about ("what was actually
stored was a screenshot, or a thumbnail"). Over a long research run this would quietly fill the tree with
unreadable thumbnails while every toast said OK.

## Root cause hint

The floating-button capture appears to take the index-only path (grabs the page-embedded media URL,
which for Ancestry image records is the `/thumbnail/` endpoint) rather than requiring the full
arm-download flow. It does not distinguish "this record has a full scan behind a Download button" from
"this is the image."

## Suggested fix

When the button is clicked on a page classified as an **image-backed Ancestry record**, either:
- (a) run the full arm → Ancestry-Download → capture flow automatically (ideal; see REQ-002), or
- (b) refuse to treat the thumbnail as the image: mark it `IMAGE ✗ thumbnail-only — NOT mined` and
  toast a clear instruction to use the download flow.

Never store a `/thumbnail/` image under a provenance that reads like the record. A size/endpoint check
(`/thumbnail/` in the URL, or dimensions far below the page's declared image) should hard-fail the
`IMAGE ✓ full` gate.

## Repro

1. Log in to Ancestry; open an image-backed record page (e.g. record 15429093, collection 7488).
2. Click the floating `⬇ Capsule` button.
3. Inspect the capture: `media/1-*.jpg` is ~525×600 and its `source_url` contains `/thumbnail/`.
4. Toast reported success with no thumbnail warning.
