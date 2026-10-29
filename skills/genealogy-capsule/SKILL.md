---
name: genealogy-capsule
description: Drive the Genealogy Capsule Chrome extension from a browser agent to archive the genealogy record page in view as a cited, hashed local source. Covers the one-time setup the human must do, the capture events, reading the machine-readable outcome, Ancestry's one-call image capture, keeping a site's own download, the completeness check, and the limits. Use when a task asks to capture, save, archive or cite a record page with Capsule, or when a page shows the "⬇ Capsule" button. For Capsule 0.2.7 or later.
---

# Genealogy Capsule: capturing records from a browser agent

Capsule is a Chrome extension plus a local app (`127.0.0.1:8765`) that saves the record page
in view as a genealogy source. It keeps a PDF of the page, the page itself, the record image
when there is one, and a citation, all hashed so the archive can be checked later. You
capture by dispatching a DOM event on the page. The extension does the rest, then writes a
verdict on the page for you to read.

Event and field names below are exact. Do not change them.

## Rules (read first)

- **The human drives.** Capture only the page they asked for, on their instruction, at a
  human pace. Never crawl, never prefetch the next record, never batch through a site.
- **Only the record in view.** Capsule reads the page already open in the human's own
  logged-in tab. Beyond that page it fetches only what belongs to that one record: a record
  file the page itself embeds, and on Ancestry the record's own image viewer, opened to use
  Ancestry's Download. It handles no credentials. Never ask for, type or store a password for
  a record site.
- **Gated pages stay local.** A page from a subscription site is archived on the human's
  computer only. Do not copy its contents anywhere else.
- **A green toast is not proof.** Every serious failure this tool has had looked like
  success: a thumbnail, a screenshot of the record, or the wrong page stored under a healthy
  message. Decide from the outcome object (below), every time. When in doubt, report the
  record as **not captured** and say why. Believing you hold a record you do not hold is
  the one unacceptable result.

## One-time setup (the human does this; you cannot)

1. **Install both parts.** The Genealogy Capsule extension from the Chrome Web Store, and the
   Capsule app from the project's GitHub releases. The app must be running.
2. **Connect them.** Click the Capsule icon in Chrome's toolbar once while the app is running.
   It pairs by itself.
3. **For site downloads, first** turn on **"Allow access to file URLs"** for Genealogy
   Capsule at `chrome://extensions` (Details). Needed to keep a file a site's own Download
   button saves, including every Ancestry image.
4. **Then** in the Capsule popup, open **Settings**, tick
   **"Capsule button on every page, and keep site downloads"**, and click **Allow** in Chrome's
   question. Without this, the
   page events below do not exist. If step 3 was done after this one, the box shows
   unticked again: tick it again.
5. Reload any tab that was open before step 4.

**Check before every run:** the page has a `⬇ Capsule` button in its bottom-right corner
(`document.getElementById("capsule-capture-btn")`). If it is missing, setup step 4 is not
done or the tab predates it: ask the human, or reload the tab. Do not click that button on
an Ancestry record that has an image (see below).

## Capture any record page

Run this as one call, with the record in view:

```js
(() => {
  const started = (window.__capsuleStarted = Date.now());  // the verdict is newer than this
  document.dispatchEvent(new CustomEvent("capsule:capture", {
    detail: { note: "why this record was kept (optional)" }
  }));
  const de = document.documentElement;
  const raw = de.getAttribute("data-capsule-last-outcome");
  const outcome = raw ? JSON.parse(raw) : null;
  return {
    busy: de.getAttribute("data-capsule-capturing") === "true",
    outcome: outcome && Date.parse(outcome.at) >= started ? outcome : null,
  };
})();
```

- `busy: true`: the capture has started. Read the verdict (next section).
- `outcome` set: Capsule refused at once (for example, no image viewer on the page). That
  object is the verdict.
- Neither: the event was not heard. The `⬇ Capsule` button is missing (setup step 4) or the
  extension is not on this page.

Use the same wrapper for every event below that captures. Works on any `http(s)` record page: archives, church
and civil registers, newspapers, cemetery sites, vital-records portals.

## Read the verdict

While a capture runs, `<html>` has `data-capsule-capturing="true"`. When it ends, that
attribute is removed and `data-capsule-last-outcome` holds the result as JSON. The result
stays until the next capture. A page capture takes a few seconds; an image capture 20–40 s.

Poll with **short, separate calls**, about every 500 ms–1 s. Do not hold one long `await`:
browser tools time out around 45 s. Reading the record with AI can make a capture take a
minute or more.

```js
// One poll. Repeat until done is true.
(() => {
  const de = document.documentElement;
  const raw = de.getAttribute("data-capsule-last-outcome");
  const outcome = raw ? JSON.parse(raw) : null;
  const busy = de.getAttribute("data-capsule-capturing") === "true";
  const fresh = !!outcome && Date.parse(outcome.at) >= (window.__capsuleStarted || 0);
  return { done: !busy && fresh, busy, outcome: fresh ? outcome : null };
})();
```

- While `busy` is true, the capture is still running: keep waiting.
- If you stop waiting before `done`, the result is **unknown**, not failed. Check the
  archive (`capsule query`, under "One record at a time") before you report it missing or
  capture it again.

The outcome carries: `ok`, `error`, `record_type`, `assertion_count`, `verify_ok`,
`verify_shortfall`, `pdf`, `pdf_skipped_reason`, `media_warnings`, `missed_media`,
`image_status`, `image_skipped`, `extraction_pending`, `url` (origin and path only) and
`at`, and on a duplicate `dedup` and `duplicate_of`. A new capture's id is not in it. The `capsule-toast` element shows the same verdict as text for a
few seconds, for the human.

### Is it complete?

A capture is **complete** only when all of these hold:

- `ok === true` and `verify_ok === true`
- `media_warnings`, `missed_media` and `verify_shortfall` are empty or absent
- `image_status` is `"full"` (the record image, at full size) or `"index-only-no-image"`
  (the record has no image), or is absent or `"none"` on an ordinary page with no separate
  record image

| Outcome | Meaning | What to do |
|---|---|---|
| `ok: false`, `error` says Capsule isn't connected | The app is not running, or never paired | Ask the human to start the app and click the Capsule icon once |
| `ok: false`, `error` says Capsule is no longer allowed on this site | The every-page setting was turned off | Ask the human; nothing was saved |
| `ok: false`, `error` contains `IDENTITY MISMATCH` or `cannot verify identity` | This page is not the record you named | Nothing was saved. Go to the right record |
| `ok: false`, `dedup: "blocked"` (`error` contains `already mined`) | This record is already in the archive, as capture `duplicate_of` | Nothing new is needed; cite that capture. Pass `remine: true` only if the human asks to capture it again |
| `verify_ok: false` | The saved files fail their own check | Not complete. Capture again; tell the human |
| `verify_shortfall` not empty | The capture does not hold the record (for example, a picture of the viewer instead of the scan) | Not complete. Read the listed reasons |
| `missed_media` not empty | The page held a record file Capsule could not fetch | Not complete: you hold only a picture of the page |
| `media_warnings` not empty | The page PDF came out blank | Usually an overlay. Capture again; on a full-screen image viewer it can be expected |
| `pdf` is not `"attached"` | No PDF; `pdf_skipped_reason` says why (often DevTools open on the tab) | The page itself still saved. Close DevTools and capture again if the PDF matters |
| `image_status` `"degraded-canvas"` or `"failed"` | The record image is missing or too small | Not complete. See "Keep a site's own download" |
| `extraction_pending: true` | Archived, but not read: no AI key is set | Complete as an archive. The human can add a key later |

## Ancestry records

Pass the record's `_APID` when you know it, written `1,<collection>::<record>`. It turns on
the identity guard: Capsule refuses to save if the open page is not that record.

**A record with an image (census, draft card, passenger list):** use the one-call flow from
the **record page** (`/search/collections/<collection>/records/<record>`, not the image
viewer). Capsule opens the viewer in a background tab, uses Ancestry's own Download, and
captures, all in one event:

```js
document.dispatchEvent(new CustomEvent("capsule:capture-ancestry-image", {
  detail: { apid: "1,<collection>::<record>" }   // optional on a record page: read from the URL
}));
```

Expect `image_status: "full"`. **Do not click the `⬇ Capsule` button or send plain
`capsule:capture` for an image-backed Ancestry record.** Without the download it can only
see the page's thumbnail, so it reports `image_status: "failed"` and the record is not done.

**A second image of the same record** (the facing page, a multi-page record): add
`record_viewer_url` (that image's viewer URL) and, if you like, `page_label`. Built, but not
yet tested live; check the outcome closely. If its verdict is lost, check with
`capsule query --apid <_APID>` and look for that image under `other_images`: the record's own
`complete` is about its first image, not this one.

**An index-only record (death index, birth index, an obituary index):** no image, no
download:

```js
document.dispatchEvent(new CustomEvent("capsule:capture", {
  detail: { apid: "1,<collection>::<record>", image_intent: "none" }
}));
```

Expect `image_status: "index-only-no-image"`. Capsule records a follow-up naming the
original the index points to (from the record's own "Original data" line).

## Keep a site's own download

When a site offers its own Download for the record image (an image viewer's download, a
"save image" button), Capsule can keep that exact file. Capsule never clicks on the site; you
operate the site's control, as the human would.

1. Open the image and wait until it has fully loaded (download buttons are often disabled
   until then).
2. Arm: `document.dispatchEvent(new CustomEvent("capsule:arm-download"))`. On Ancestry, add
   `{ detail: { apid } }`. The toast says `Armed. Click the site's own Download now.`, or
   `Capsule could not arm: <reason>`. Act on the reason: usually setup step 3 or 4.
3. Click the site's Download, and every option in its dialog, **by element reference, not by
   screen coordinates.** Download dialogs change size when an option is picked.
4. On the same page, without navigating: `capsule:capture` with
   `detail: { image_intent: "expected" }` (plus `apid` on Ancestry). Read the verdict.
   Expect `image_status: "full"`.

The arm belongs to the page you armed on. If the tab navigates before step 4, the download
is not filed, by design.

## One record at a time

Wait for each verdict before you navigate. The verdict is written on the page, so leaving
mid-capture loses it, and the capture itself may or may not finish. Never guess either way:
ask the archive. `capsule query --url <record URL>` (or `--apid <_APID>`) prints whether the
record is held and `complete`. Run it if you have a shell; otherwise ask the human to run it,
or to look for the record on the Capsule index page (`http://127.0.0.1:8765/`). Check before
you report the record as missing or capture it again. The loop is: open record, (arm, download),
capture, **read the verdict**, then the next record.

After an image capture, if you can, open the saved image and read one field off it. Looking
at the saved file has caught every serious bug this tool has had.

## Long Ancestry lists (optional)

The human can keep a queue in the Capsule app instead of you tracking state:

```
capsule ancestry seed --file apids.txt   # one _APID per line
capsule ancestry status                  # what is done, and what is not (with why)
capsule ancestry followups               # index records whose original is still to find
```

A record counts as done only when it was captured, verified, read, and its image is full
size or legitimately absent.

## Known limits

- **FamilySearch site downloads are not claimed** (Capsule bug BUG-028). The file lands in
  the Downloads folder, but the armed capture never receives it. Until that is fixed, capture
  the page as usual, then the human (or you, with a shell) files the image:
  `capsule ingest <file> --url <the image's page URL> --provenance site:official-download`.
- A tile viewer whose image is drawn in pieces cannot be saved from the page. Use the site's
  own download.
- If an Ancestry download fails after setup, check "Allow access to file URLs" first.

## Quick reference

```js
// Any page
document.dispatchEvent(new CustomEvent("capsule:capture", { detail: { note: "…" } }));
// Ancestry, record with an image (from the record page)
document.dispatchEvent(new CustomEvent("capsule:capture-ancestry-image", { detail: { apid } }));
// Ancestry, index-only
document.dispatchEvent(new CustomEvent("capsule:capture", { detail: { apid, image_intent: "none" } }));
// A site's own download: arm, click the site's Download, then capture
document.dispatchEvent(new CustomEvent("capsule:arm-download"));
document.dispatchEvent(new CustomEvent("capsule:capture", { detail: { image_intent: "expected" } }));
// Each capture: set window.__capsuleStarted = Date.now() first, in the same call.
// Verdict: poll <html> until data-capsule-capturing is gone and
// data-capsule-last-outcome's `at` is at or after window.__capsuleStarted.
```

**Complete = `ok` and `verify_ok`, no warnings, no missed media, no shortfall, and the image
full or legitimately absent. Anything else: say so, and come back to it.**
