# REQ-002 — One-call Ancestry image capture (collapse the arm→download→capture choreography)

**Status:** ✅ **DONE — E2E VERIFIED 2026-07-16 (v0.0.13)** · **Priority:** 🟠 HIGH — the slowest step of capturing an Ancestry record image

> ## ✅ E2E VERIFIED 2026-07-16 (Genealogy session) — the `_findToolMenu()` fix works
> Re-fired `capsule:capture-ancestry-image { apid: "1,6061::24407336" }` on the record page (single
> event, no manual choreography).
> - Toast: **`Captured census — 24 assertion(s), verify OK, PDF ✓, IMAGE ✓ full`** ✅
> - On disk (`20260716-ancestrycom-40fbf344`): `1-4300663-00025.jpg` = **4296×3072** full-res scan +
>   `0-record.pdf` ✅ — real record image, not a thumbnail.
> The ~8-step arm→download→capture choreography is now **one event**. Used immediately on a real
> 1920 census record.


## E2E test 2026-07-16 (Genealogy session) — FAILED, precise fix identified

Fired `capsule:capture-ancestry-image { apid: "1,6061::24407336" }` on the record page.
Toast: **`Capsule: Ancestry's Download was not triggered: could not find Ancestry's Tools menu`**.

Root cause — confirmed against the live viewer DOM (`document.querySelectorAll('button,[role=button]')`):
the tool-menu trigger is **`title="Tool menu"`, `textContent="Tool menu"`** (a wrench ICON), class
`calloutTriggerNoArrow singleFloatButton`. There is **NO** control with visible text **"Tools"**
(verified: nothing matches `/^\s*tools\s*$/i` on textContent — `visible_text_Tools_button: false`).
So the `clickAncestryDownload()` finder that matches the menu by the word **"Tools"** never matches.

**One-line fix:** match the tool-menu trigger by **`title`/`textContent` === "Tool menu"** (or the
class `singleFloatButton` / `calloutTriggerNoArrow`). Once the menu opens, the **"Download"** item IS
visible text, so that half of the finder is fine — only the menu-open step fails. The rest of the flow
(apid-from-URL, arm, background-tab viewer open, capture-from-record-page) ran without error; the plain
`capsule:capture` path and BUG-012 floor both work. So this is the single remaining blocker.

**FIX APPLIED (2026-07-16, v0.0.13):** `content.js` now has `_findToolMenu()`, which matches the
trigger by label `/tool\s*menu/i` (the wrench icon's "Tool menu") — keeping `/^\s*tools?\s*$/i` as a
fallback for other variants — and, failing that, by class `.singleFloatButton, .calloutTriggerNoArrow`.
The Download half is unchanged (`/download/i` on the opened menu). Reload the extension and re-fire
`capsule:capture-ancestry-image {apid}` to confirm `IMAGE ✓ full`.


**Filed:** 2026-07-15, Genealogy research session (capturing Ancestry records already cited in the tree, for Gramps)

## Implementation (2026-07-15)

Fire ONE event on the **record page**:

```js
document.dispatchEvent(new CustomEvent("capsule:capture-ancestry-image",
  { detail: { apid: "1,7488::15429093" } }));   // apid optional — derived from the URL if omitted
// detail.show_viewer:true opens the viewer visibly (debug a selector drift); onResult(res) gets the raw result
```

Flow, all internal to the service worker (which survives the tab work a content script cannot):

1. **content.js** (record page) derives the apid from the URL if not given, finds the record's
   image-viewer link, and hands the worker the record page's **own** url + html (captured up
   front — it carries the fields the extractor needs; the viewer is only a canvas) + the viewer url.
2. **background.js** (`captureAncestryImage`) runs the identity guard, renders the record page to
   PDF, **arms** the download, opens the viewer in a **background tab**, and messages that tab.
3. **content.js** (viewer half, `clickAncestryDownload`) clicks Ancestry's own **Tool menu → Download**,
   matched by visible **label** ("Tool menu", "Download") with a class fallback — see the fix note above.
4. The worker waits for the armed download, closes the viewer tab, and POSTs the **record** html +
   the full image through the existing capture path → the usual `verify OK, PDF ✓, IMAGE ✓ full` toast.

**Design note (posture).** This is the one line REQ-002 consciously relaxes: Capsule now clicks
Ancestry's own Download control for the operator (previously the human/agent did that click). It
stays foreground and user-triggered, uses the provider's own subscriber download, and touches no
media/tile API — the rest of the compliance posture is unchanged.

**Owner E2E to confirm:** the Tools→Download text finder against the live viewer DOM (if Ancestry
labels differ, or a size/format dialog intervenes, the toast will say *"could not find Ancestry's
Tools menu / no Download item"* and nothing is banked — tighten the finder from there). No new
extension permissions were needed.

## The ask

Provide a **single agent action** that captures a full-resolution image from an image-backed Ancestry
record, doing the whole dance internally. Something like:

```js
document.dispatchEvent(new CustomEvent("capsule:capture-ancestry-image", {
  detail: { apid: "1,6061::20844342", record_url: ".../records/20844342" }
}));
// resolves to the usual toast: "...verify OK, PDF ✓, IMAGE ✓ full"
```

Internally it should: ensure/open the image viewer for the APID → arm the download → **programmatically
trigger Ancestry's own Tools→Download** → capture from the record page → return the toast. (Or expose
it CLI/queue-driven — `capsule ancestry seed --file apids.txt` already exists; wire the image-full flow
into `capsule ancestry next` so the agent just navigates + fires one call per record.)

## Why

The proven working flow today is **~8 agent steps per record**:
1. navigate to the image viewer URL
2. `capsule:arm-download {apid}` (dispatch + read toast)
3. find the Tool menu button
4. click it
5. find the Download menu item
6. click it
7. navigate back to the record page
8. `capsule:capture {apid, image_intent:"expected"}` (dispatch + read toast)

That's fragile (DOM lookups for the tool menu each time) and slow. Repeated for every record a
researcher works through, this choreography is the single biggest cost.
The sequence itself is reliable (dozens captured cleanly on 2026-07-15 once BUG-011 was fixed); the ask
is only to **encapsulate** it so the agent fires one event instead of eight.

## Constraints / notes

- Must keep respecting pacing/ToS — the download comes from Ancestry's **own** Download button, no
  media-API scraping (unchanged). If queue-driven, keep the `Retry-After` pacing.
- Would also obviate BUG-013 (capture-from-viewer identity-guard gotcha).
- The identity guard (`apid`) must still hold — refuse if the page isn't that record.
- Success must still require `IMAGE ✓ full` (a thumbnail is not success — see BUG-012).

## Acceptance

One dispatched event (or one `capsule ancestry next` + navigate) yields a verified `IMAGE ✓ full`
capture on disk, with no per-record DOM choreography by the agent.
