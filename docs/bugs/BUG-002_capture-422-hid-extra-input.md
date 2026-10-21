# BUG-002 — ALL CAPTURES FAIL: `HTTP 422: body.media.0.hid: Extra inputs are not permitted`

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-14, `0cffa3a`)
> **Verification:** the defect broke *every* capture, so any success is proof. **216 captures
> were banked after the fix** (2026-07-14 → 2026-08-23) across Ancestry, RootsIreland,
> IrishGenealogy, FamilySearch, NYC vital records and Archion. Zero `hid` 422s among the
> ledger's 16 recorded failures. The structural fix (separate `{media, hid}` objects) also
> holds: the only later `extra_forbidden` 422 was BUG-011 — a different producer, a different
> model. · **Severity:** 🔴 **CRITICAL — every capture was broken**

> ## FIXED — 2026-07-14
>
> Diagnosis was exactly right, including that `extra="forbid"` is the correct call and
> the extension is what needed fixing.
>
> Fixed **structurally**, not with the one-line delete: `renderPagePdf()` now returns the
> wire payload and the local metadata as **separate** objects — `{media, hid}` — where
> `media` holds exactly the four fields `MediaIn` accepts. Both callers push
> `render.media` and nothing else. The one-line `{hid, skipped, ...mediaItem}` fix would
> have worked today and left the *next* field added to the render free to break capture
> the same way; as the report itself said, "the real defect is that background.js spreads
> a local object straight onto the wire."
>
> Note the popup path was broken too, not just the on-page trigger — it spread the same
> object. Both are fixed.
>
> Regression tests (`tests/test_backend.py`): `test_BUG_002_media_payload_rejects_render_local_fields`
> (the stray key IS a 422 — pins the strict contract) and
> `test_BUG_002_the_exact_payload_the_extension_now_sends_is_accepted` (pins the shape,
> so adding a key to `render.media` fails here rather than in the field). 475 tests green.
**Found:** 2026-07-14, Genealogy research session (E2E retest after server + extension reload)
**Regression introduced by:** the BUG-001 fix (the `hid` field it added)
**Fix:** one line in `extension/background.js`

---

## Symptom

Every capture fails immediately (~260 ms). Toast:

```
Capsule: HTTP 422: body.media.0.hid: Extra inputs are not permitted
```

Nothing is written. The content script IS injected, the token IS valid, the backend IS reachable —
the request is rejected at schema validation.

## Root cause — exact

**`extension/background.js:52`**

```js
const media = render.data_base64 ? [render] : [];
```

It puts the **entire `render` object** into the `media[]` payload. `renderPagePdf()` now returns
(`extension/render-pdf.js:213-220`):

```js
return {
  data_base64: data,
  filename: RENDER_FILENAME,
  mime: RENDER_MIME,
  provenance: RENDER_PROVENANCE,
  hid: (suppressed.hid || []).concat(suppressed.heuristic || []),   // <-- NEW (BUG-001 fix)
};
```

`hid` is intended for the **response summary** (`background.js:87` → `pdf_hid: render.hid`), **not**
for the media payload sent to the backend.

The backend's model (`capsule/backend/app.py:154`) is deliberately strict:

```python
class MediaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_base64: str
    filename: Optional[str]
    mime: Optional[str]
    source_url: Optional[str]
    provenance: Optional[str]
```

No `hid` → `extra="forbid"` → **422**.

**`extra="forbid"` is the right call** (the backend is the trust boundary and should not silently
swallow unknown fields). **The extension is what needs fixing.**

## Fix — separate the wire payload from the local metadata

```js
// background.js:52
const { hid, skipped, ...mediaItem } = render;          // hid/skipped are LOCAL, not wire fields
const media = render.data_base64 ? [mediaItem] : [];
```

`render.hid` remains available for the summary at line 87 — only the outbound payload is cleaned.

*(Alternative — adding `hid` to `MediaIn` — is NOT recommended: `hid` is a fact about how the render
was made, not about the media bytes. If you do want it persisted, it belongs in the capture manifest
alongside `provenance`, not on the media item.)*

## Why this is CRITICAL, not cosmetic

It **blocks 100% of captures** — every capture attempt fails until it is fixed, and for some
pre-1848 parish records the page in view is the only image of the record the researcher can reach.

## Suggested regression test

The `hid` field arrived with a fix and broke the wire contract silently — nothing in the test suite
noticed. Add a contract test that would have:

```python
def test_BUG_002_media_payload_rejects_unknown_fields():
    """Regression: the extension must not send render-local metadata (hid/skipped) as media fields."""
    resp = client.post("/capture", headers=_AUTH, json={
        "url": "https://example.test/r", "html": "<html></html>",
        "media": [{"data_base64": _PDF_B64, "filename": "0-record.pdf",
                   "mime": "application/pdf", "provenance": "render:page-pdf",
                   "hid": ["#capsule-toast"]}],       # <-- what the extension actually sent
    })
    assert resp.status_code == 422        # documents the contract...
```

...and, better, a **JS-side unit test** asserting that the object handed to `fetch` contains only the
five `MediaIn` keys. The real defect is that **`background.js` spreads a local object straight onto
the wire** — any future field added to `renderPagePdf()`'s return will break capture again the same
way. Pin the payload shape explicitly rather than by spread.

---

## Context: this was found while E2E-testing the BUG-001 fix

Immediately before this regression appeared, the BUG-001 fix was verified working end-to-end
(capture `20260714-rootsirelandie-b565b7da`, an 1890 civil death record): the
occluding white fill was gone, the blank-page gate passed (83.4% near-white), and the record rendered
legibly. **The BUG-001 fix itself is good.** This is purely the plumbing of the new `hid` field.

**Still outstanding from BUG-001 (see that file):** Capsule's own UI (`#capsule-toast`,
`#capsule-capture-btn`) was still baked into the rendered PDF. Worth confirming that the `hid`
list now covers those selectors once capture works again.


---

# ✅ E2E CONFIRMED BY THE REPORTER — 2026-07-14

Retested after server + extension reload, on the blocked record (the 1890 civil death
record). **The agent hid nothing — Capsule's own suppression was tested end to end.**
Capture: `20260714-rootsirelandie-6a23696d`.

| check | result |
|---|---|
| capture completes (no 422) | ✅ `Captured Civil Death Record — 14 assertion(s), verify OK, PDF ✓` |
| PDF written, real file | ✅ `%PDF-`, 62,499 bytes, `provenance: render:page-pdf` |
| **blank-page visual gate** | ✅ **PASS — 84.4% near-white** (the blank case was ~100%) |
| **rendered page legible** | ✅ every field, through to the cause of death |
| **BUG-001 secondary — Capsule UI excluded** | ✅ **FIXED** — no "Capturing this page…", no "Capturing…", no "Capsule" anywhere in the render |
| persona separation | ✅ correct — the deceased and the informant (a stepson) are **distinct personas**; no identity merge |
| no data lost vs pre-fix capture | ✅ all 12 fields present, values identical |

**BUG-001 (both defects) and BUG-002 are CLOSED from the research side.** The load-bearing record
is now archived as a page-faithful,
human-readable PDF, and attached to the citation in Gramps.

**The structural fix was the right call.** Separating the wire payload from local metadata means the
*next* field added to the render cannot break capture the same way; the one-liner I proposed would
have worked today and re-broken later. The two regression tests pin both directions of the contract.

**Reminder for the SCREENSHOT feature:** it needs the same overlay hide-list **and** the same visual
gate. A screenshot cannot be trusted to be non-blank just because bytes were written — that is the
entire lesson of BUG-001.
