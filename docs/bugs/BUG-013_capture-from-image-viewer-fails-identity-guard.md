# BUG-013 — `capsule:capture` from the image-viewer URL fails the identity guard

**Status:** 🟢 **OPEN, but the cost is gone** (re-assessed 2026-08-24) · **Severity:** 🟢 **LOW**
— was 🟡 MEDIUM
> **The underlying defect stands; the pain it described does not.** The identity guard still
> requires a readable record page (`extension/background.js:88`) and still does not recognise
> the image-viewer URL as the same record — unchanged.
>
> But **REQ-002 (✅ E2E VERIFIED 2026-07-16, v0.0.13) removed the navigate-back from the
> operator's hands entirely.** `capsule:capture-ancestry-image { apid }` is fired *on the record
> page* and the extension drives the viewer itself in a background tab
> (`clickAncestryDownload()` guards on `/imageviewer/collections/` and runs there). The ~8-step
> choreography this bug called "the awkward part" is one event.
>
> **So the remaining question is whether to fix the guard at all.** Teaching it to read
> `pId=<rec>` from the viewer URL is still correct-in-principle and would make a stray manual
> dispatch work — but nothing in the current flow pays the cost any more. **Owner's call:**
> close as superseded by REQ-002, or keep as a low-priority correctness tidy-up.
> *Recommendation: keep open at LOW — it is a real inconsistency, and REQ-007 §6's source
> profiles are where per-site URL identity belongs.*
**Found:** 2026-07-15, Genealogy research session

## Symptom

Dispatching `capsule:capture { apid, image_intent:"expected" }` while the tab is on the **image-viewer**
URL (`/imageviewer/collections/<db>/images/<img>?...pId=<rec>`) fails with:

```
Capsule: cannot verify identity: expected 7488:15429093, but this is not a readable Ancestry record page
```

The capture only succeeds when dispatched from the **record page**
(`/search/collections/<db>/records/<rec>`). So the working flow forces: open viewer → arm → click
Ancestry Download (in the viewer) → **navigate back to the record page** → capture. The navigate-back is
the awkward part.

## Why it matters

The Download button lives in the **viewer**, but capture must run on the **record page** — two different
URLs for the same record. Every image capture pays a navigate-back and it's easy to get wrong (I hit
this on the first real capture 2026-07-15). It also makes REQ-002 (one-call capture) trickier than it
needs to be.

## Root cause hint

The identity guard reads the record-page DOM to confirm the APID and does not recognise the image-viewer
page as "the same record" — even though the viewer URL carries `pId=<rec>` and the collection id.

## Suggested fix

Let the identity guard resolve the APID from the **image-viewer** page too (parse `pId` + collection
from the URL, or the viewer's DOM), so `capsule:capture` can run from either the record page or the
viewer for the same APID. At minimum, make the error message actionable: "navigate to the record page
(`/search/collections/<db>/records/<rec>`) to capture."

(Largely obviated if REQ-002 encapsulates the whole flow.)

## Repro

1. Navigate to `/imageviewer/collections/7488/images/NYM237_351-0325?pId=15429093`.
2. `capsule:arm-download {apid:"1,7488::15429093"}` → "Armed."
3. Click Ancestry Tools→Download.
4. Without leaving the viewer, `capsule:capture {apid:"1,7488::15429093", image_intent:"expected"}`.
5. → "cannot verify identity … not a readable Ancestry record page".
