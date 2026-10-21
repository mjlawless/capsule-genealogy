# BUG-001 — PDF capture renders BLANK when the page is captured from an agent-driven tab

> ## FIXED — 2026-07-14
>
> Both halves of the report were implemented, as recommended.
>
> **1. Overlay suppression** (`extension/render-pdf.js`). Before printing, an
> `@media print` stylesheet hides the listed overlays (`#claude-agent-glow-border`,
> `-inner`, `#claude-phantom-cursor`) **and Capsule's own UI** (`#capsule-capture-btn`,
> `#capsule-toast`) — so the instrument no longer photographs itself. Plus the
> recommended **heuristic**: any fixed/sticky element covering ≥90% of the viewport at
> `z-index > 1e6` is marked and hidden too, which defends against the *next* overlay we
> have never heard of. What was hidden is reported back (`pdf_hid`), never silent.
>
> Chosen over `display:none` deliberately: `printToPDF` renders with print media, so the
> effect on the PDF is identical, while the live page the researcher is reading is never
> mutated — a missed restore leaks an inert style tag instead of a page with invisible
> pieces. Restore runs before detach, and is idempotent.
>
> **2. The visual gate** (`capsule/pipeline/render_check.py`, wired into `POST /capture`).
> The render is rasterized (pypdfium2) and its near-white ratio measured at the report's
> 0.99 threshold. A blank render now returns `media_warnings`, the toast says **`PDF ✗`**
> in red instead of `PDF ✓`, and `capture-selftest.js` FAILS it. The capture itself still
> succeeds — the snapshot, extraction and hashes are intact and the bytes are archived;
> it is the *success report* that was lying, not the archive. Only `render:page-pdf`
> media is gated, so a legitimately near-white register scan is not flagged.
>
> Regression tests: `tests/test_render_check.py` (the white-fill-over-text file is
> caught), `tests/test_backend.py::test_BUG_001_blank_page_render_is_reported_but_capture_still_succeeds`,
> plus one proving the gate does not cry wolf on a page-served blank scan. 463 tests green.
>
> **Still owed:** the owner's live E2E — re-capture in an agent-driven tab and confirm the
> PDF shows the record. **The blocked re-capture of `20260714-rootsirelandie-452dd09e`
> (an 1890 civil death record) is now unblocked.**
>
> The screenshot feature, when it ships, must call the same suppression — see the warning
> below, which still stands.

**Status:** 🟡 **PARTIALLY VERIFIED 2026-08-24** — the visual gate is confirmed working; the
overlay-suppression half is not corpus-verifiable, and the sweep found a follow-on defect
(**BUG-016**)
> **What was verified 2026-08-24.** The gate was re-run over the collection using its own code
> (`capsule.pipeline.render_check`): **45 `render:page-pdf` files sampled from 208 on disk**,
> none at or above `BLANK_THRESHOLD` (0.99) except the one noted below. The measurement itself
> is sound — Ancestry record pages sit around 0.95–0.97 near-white (text-light pages), well
> clear of the gate, while content-heavy renders fall to 0.49–0.63. It neither cries wolf nor
> sleeps.
>
> **What could NOT be verified, and why it is not a failure.** The overlay-suppression half
> leaves no trace on disk by design: `pdf_hid` is reported in the API response, never persisted
> to `manifest.json` (**0 of 240 captures** carry it). A successful suppression is
> indistinguishable on disk from no overlay having been present. Confirming it needs one live
> agent-driven capture — it cannot be settled from the corpus.
>
> **What the sweep found instead.** `20260718-rootsirelandie-98854fa1` holds a page render
> measuring **`blank_ratio` 0.9914 — `looks_blank()` returns True**. The gate would have fired
> at capture time, but **`media_warnings` is never written to the capture folder**, so the
> archive carries no record that it was flagged. Filed as **BUG-016**. · **Severity:** HIGH (silently archives unreadable evidence) · **Found:** 2026-07-14
**Found by:** the owner (diagnosed the cause) + Claude (Genealogy research session)
**Component:** PDF save (`render:page-pdf`) — and, pre-emptively, the planned SCREENSHOT feature
**Capsule is not at fault for the root cause.** The bug is an interaction with a browser overlay.

---

## TL;DR

The captured PDF has a **perfect text layer** and a **blank visual layer**. `pypdf` extracts every
field; a human opening the file sees a **white page**. Cause: **Claude-in-Chrome injects a
full-viewport `position:fixed` overlay at z-index 2147483646**; Chrome's print pipeline flattens that
top-most layer into a **full-page opaque white fill painted after all text**.

**Capsule must defensively hide known overlays (including its own UI) before rendering.**

---

## Repro

1. Open a RootsIreland record page in a Claude-in-Chrome **agent-driven** tab, e.g.
   `https://www.rootsireland.ie/<county>/view_detail.php?recordid=<id>&type=<type>&recordCentre=<centre>`
2. Fire a capture: `document.dispatchEvent(new CustomEvent('capsule:capture', {detail:{}}))`
3. Toast reports success: `Captured Civil Marriage — 22 assertion(s), verify OK, PDF ✓`
4. Open `data/captures/<id>/media/0-record.pdf` **and LOOK AT IT.**

**Expected:** the record, rendered.
**Actual:** a blank white page with a faint pink border.

> **The same page prints correctly from a normal (non-agent) browser window.** That is the tell.

Affected capture (kept for reference): `20260714-rootsirelandie-d8618305`

---

## Root cause — PROVEN, not inferred

The tail of the PDF page content stream:

```
... Tj  ET              <- last text is drawn
1 1 1 RG 1 1 1 rg       <- set colour to WHITE
0 0 740 1056 re  f      <- fill a FULL-PAGE rectangle ON TOP OF EVERYTHING
Q
```

DOM inspection of the agent tab:

```
#claude-agent-glow-border         position:fixed   1866x876 (FULL VIEWPORT)   z-index: 2147483646
#claude-agent-glow-border-inner   opacity: 0.60
#claude-phantom-cursor
```

Claude-in-Chrome paints a full-viewport "an agent is driving this tab" glow. Chrome's print pipeline
composites that top-most full-viewport layer into an opaque full-page background fill, emitted
**after** the text — so it occludes the entire record.

Corroborating detail: the blank render shows a **faint pink border** — that *is* the glow.

### Proof: hide the overlay, re-capture the identical page

Capture `20260714-rootsirelandie-f432b981` (same URL, same Capsule build, overlays hidden):

| check | before | after |
|---|---|---|
| full-page fill emitted after last text | **YES** | **NO** |
| blank-page detector (% near-white pixels) | **~100%** | **61.7%** |
| rendered page | **BLANK** (white page, faint pink border) | **complete record, full colour** |

Text extraction was **identical and fully correct in both cases** — which is exactly why the existing
verification passed. **Text extraction cannot detect this class of failure.**

---

## Secondary defect (exposed by the fixed render) — Capsule photographs itself

The clean PDF contains **Capsule's own UI**, bottom-right: the green *"Capturing this page…"* toast
and the purple *"Capturing…"* pill. (The render screenshots are not published: they show a gated record.)

An archival record should not contain the instrument that made it. Same fix resolves it.

---

## Proposed fix

Hide known overlays immediately before rendering, restore immediately after. Cover **both** the PDF
render and the **planned screenshot**.

```js
const CAPSULE_HIDE_FOR_RENDER = [
  // Claude-in-Chrome agent chrome (full-viewport, z-index 2147483646)
  '#claude-agent-glow-border',
  '#claude-agent-glow-border-inner',
  '#claude-phantom-cursor',
  // Capsule's own UI — do not archive the instrument
  '#capsule-capture-btn',
  '#capsule-toast',
];

function withOverlaysHidden(fn) {
  const prev = [];
  for (const sel of CAPSULE_HIDE_FOR_RENDER) {
    for (const el of document.querySelectorAll(sel)) {
      prev.push([el, el.style.getPropertyValue('display'), el.style.getPropertyPriority('display')]);
      el.style.setProperty('display', 'none', 'important');
    }
  }
  try { return fn(); }
  finally { for (const [el, v, p] of prev) el.style.setProperty('display', v, p); }
}
```

Equivalently, inject a print stylesheet:
`@media print { #claude-agent-glow-border, #claude-agent-glow-border-inner, #claude-phantom-cursor, #capsule-capture-btn, #capsule-toast { display: none !important; } }`

**Consider a defensive sweep as well:** any `position:fixed` element whose rect covers ≥90% of the
viewport with `z-index > 1e6` is almost certainly agent/extension chrome, not content. Hiding those
would make Capsule robust against *future* overlays it has never heard of (other extensions, other
agents), not just this one. Recommend: explicit ID list **plus** the heuristic, logged when it fires.

### ⚠️ THE SCREENSHOT FEATURE WILL HIT THIS SAME BUG
`captureScreenshot` composites the same overlay stack. Without this fix, the screenshot will
faithfully archive a pink glow and a "Capturing…" pill instead of the record. **Apply the fix to the
screenshot path before shipping it.**

---

## Required: add a VISUAL verification gate

The existing checks all passed on a blank page:
magic bytes ✅ · size ✅ · `%%EOF` ✅ · SHA-256 vs manifest ✅ · text extraction ✅ → **"PDF ✓"**

None of them look at pixels. Add a **blank-page detector** to capture verification:

```python
# render page 1, sample pixels; fail if the page is effectively empty
pix = fitz.open(pdf)[0].get_pixmap(dpi=110)
near_white = sum(1 for y in range(0, pix.height, 4) for x in range(0, pix.width, 4)
                 if all(c > 245 for c in pix.pixel(x, y)[:3]))
total = len(range(0, pix.height, 4)) * len(range(0, pix.width, 4))
assert near_white / total < 0.99, "PDF renders blank — capture is not usable as evidence"
```

Observed: **~100% before the fix, 61.7% after.** A 0.99 threshold separates them cleanly and would
have caught this in one second.

**Principle:** *"PDF ✓" must mean "a human can read it,"* not "a file was written."

---

## Why this matters here (context from the research side)

Capsule's whole promise is *"the archived snapshot IS the evidence."* This bug silently produces
**unreadable evidence that reports success.** For some pre-1848 parish records, the page in view
is the only image of the record the researcher can reach. A capture that says `PDF ✓` and archives a
blank page is worse than a failed capture, because nobody re-runs it.

**Blocked on this fix:** re-capture of `20260714-rootsirelandie-452dd09e` — an 1890 civil death
record that was load-bearing evidence in the research. It was captured before PDF shipped and has no
PDF at all.


---

# E2E VERIFICATION BY THE REPORTER — 2026-07-14 (Genealogy research session)

Re-captured the **blocked** record (the 1890 civil death record) from
an agent-driven tab, **deliberately WITHOUT hiding the overlay myself**, so this tests the shipped fix
end to end. New capture: `20260714-rootsirelandie-b565b7da`.

## ✅ PRIMARY DEFECT — FIXED. Confirmed.

| check | result |
|---|---|
| toast | `Captured Civil Death Record — 15 assertion(s), verify OK, PDF ✓` |
| **blank-page visual gate** | **PASS — 83.4% near-white** (blank case was ~100%) |
| **rendered page** | **the complete record, legible** (screenshot not published: gated record) |
| content probes | name ✅ · townland ✅ · informant + relationship ✅ · death date ✅ · occupation ✅ · marital status ✅ · cause of death ✅ |

**The occluding full-page white fill is gone.** The load-bearing record is now archived
as a readable PDF. **Thank you — this unblocks the research.**

## 🟡 SECONDARY DEFECT — **NOT FIXED.** Capsule is still photographing itself.

The rendered PDF **still contains Capsule's own UI**, bottom-right:
- the green **"Capturing this page…"** toast
- the purple **"Capturing…"** pill

The agent chrome (`#claude-agent-glow-border` et al.) is correctly excluded,
but **`#capsule-toast` and `#capsule-capture-btn` are not.** They are baked into the archived
evidence.

**Please add Capsule's own elements to the hide-list** (they were in the original proposed fix):

```js
'#capsule-capture-btn',
'#capsule-toast',
```

Low severity — it does not obscure the record — but an archival record should not contain the
instrument that made it, and these captures are permanent.

**Suggest: keep this bug OPEN until the Capsule-UI elements are excluded too.**

## Reminder — the SCREENSHOT path
Whenever the screenshot ships, it needs the **same** hide-list (agent chrome **and** Capsule UI) and
the **same** visual gate. Please do not ship it relying on the PDF fix alone.


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
