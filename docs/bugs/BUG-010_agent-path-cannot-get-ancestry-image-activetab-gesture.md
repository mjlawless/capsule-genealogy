# BUG-010 — The agent-driven path cannot obtain the full-res Ancestry image (activeTab needs a toolbar gesture)

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-14, `bf42fc0`)
> **The named record proves it.** `7884:119680425` (a 1910 census record) — the exact record
> this bug was filed on — captured as `20260714-ancestrycom-8c0ccfe8` with the scan attached
> from the armed download: **`ancestry:download`, 3914×2748, 1.45 MB, `image_status: full`.**
> The race diagnosis was right, and the wait works.
>
> **Sustained corpus-wide: 58 Ancestry captures carry an `ancestry:download`, and all 58 are
> `image_status: full`** — with no host grant, exactly as the fix predicted. Option C
> (viewer-URL identity) was never needed. · **Severity:** 🔴 **HIGH — on the agent-assisted path, REQ-001 captures got the page but never the record image**

> ## FIXED — 2026-07-14. And the diagnosis was not the host grant.
>
> Your own evidence pointed the way: on the retry, the real scan arrived as **media file
> 9** — pushed AFTER the eight junk items, i.e. **the armed Download itself delivered it.**
> The armed path uses `chrome.downloads` + a `file://` read; it needs no `ancestry.com` host
> permission at all. So the host grant was only ever feeding the generic sidebar sweep —
> which BUG-009 now **skips entirely on Ancestry.** With that gone, the host grant is not
> part of the image path.
>
> **The real cause of the first-attempt failure was a race.** The agent clicks Ancestry's
> Download and then fires the capture; the download is usually still in flight at that moment.
> `takeStagedDownload` asked once, heard "not here yet", and returned `IMAGE ✗ failed` — while
> the file landed a second later. It now **WAITS up to 20s** for the armed download to stage
> before giving up (the `onChanged` listener stages it the instant it completes). A download
> that never starts still fails honestly, and says so.
>
> So the answer to your options: **not A.** The host grant was a red herring created by the
> BUG-009 junk sweep. **B, effectively** — the armed intercept works; it just needed the
> capture to wait for it. No `permissions.request` change, no viewer-URL capture.
>
> **Re-test (v0.0.11, reload) the full agent flow with NO host grant:** arm → click
> Ancestry's Download → navigate back → capture. The scan should attach from the armed
> download, and the folder should hold only `0-record.pdf` and that one scan. If it still
> fails, the armed state is not surviving the navigation and I need Option C — tell me and
> I will add viewer-URL identity so capture can fire where the download happens.
**Found:** 2026-07-14, first agent-assisted image capture of the second pilot run — a 1910 US Census record (`7884:119680425`)
**Relates to:** REQ-001 (arm-download), BUG-009 (sidebar false-positives, compounding the toast)

---

## What happened

Ran the documented agent path end-to-end, no human toolbar click:

1. On the record page: `document.dispatchEvent(new CustomEvent("capsule:arm-download", {detail:{apid:"1,7884::119680425"}}))`
   → toast **"Armed. Click the viewer's Download now."** ✅
2. Opened Ancestry's image viewer, clicked **Ancestry's own Tools → Download** (a real, trusted click on the site's UI). ✅
3. Firing `capsule:capture` **on the viewer URL** was correctly refused by the identity guard
   (`/imageviewer/...` is not a readable record URL). Reasonable — but it means capture must be
   fired from the record page while the armed bytes are held.
4. Navigated back to the record page, fired `capsule:capture` `{apid, image_intent:"expected"}`.

**Result:** `IMAGE ✗ failed — NOT mined`. The capture folder (`20260714-ancestrycom-1004e237`)
held only `0-record.pdf` (a `render:page-pdf`). No scan. Then the remote-media fallback was refused:

> "Capsule has no access to https://www.ancestry.com. Open the Capsule popup on this page and
> capture once to grant it, then re-run."

## Root cause (from `extension/manifest.json`)

Permissions are `["activeTab", "scripting", "storage", "debugger", "downloads"]` +
`optional_host_permissions: ["https://*/*"]`. **`activeTab` grants the service worker host access to
a tab ONLY after a user gesture on the extension action (the toolbar popup).** The agent's
CustomEvent trigger is not such a gesture, so:

- the service worker has **no host permission** for `ancestry.com` → it cannot fetch the image URL
  (the remote-media fallback), and
- the `arm-download` workaround (which uses `debugger`/`downloads` to intercept Ancestry's own
  Download and should sidestep host-fetch entirely) **did not attach the bytes** — it returned
  `IMAGE ✗ failed`. Either the debugger intercept also needs the gesture/grant, or the armed state
  did not survive the viewer→record navigation.

## Why this matters

REQ-001's point (the owner, 2026-07-14) is that a capture made in the user's own logged-in session
includes the record image, not just the page. Today the agent gets the assertions and a
page render, but **not the record image** — and a page render is not the record. So every
image-backed Ancestry record still silently needs a second, toolbar-click capture of the same record.

## What I need from GS (the owner's call — I will not work around it)

Pick a path:

**A. Persistent host grant, one-time.** Have the popup request `chrome.permissions.request({origins:
["https://*.ancestry.com/*","https://*.ancestrycdn.com/*"]})` so ONE manual popup click grants
ancestry.com **persistently**. After that, verify whether the agent path can (a) fetch the image URL
directly, or (b) whether Ancestry's media/tile endpoint still 403s (REQ-001 said it does by design —
in which case host permission alone is NOT enough and arm-download must be fixed).

**B. Fix arm-download so it attaches on the agent path.** Make the `debugger`/`downloads` intercept
capture Ancestry's own Download and marry those bytes into the next `capsule:capture` for the same
apid, with no toolbar gesture. Confirm the armed state survives a viewer→record-page navigation
(the capture must fire on the record page for the identity guard to pass).

**C. Allow capture from the viewer URL** for the image, if that is simpler — i.e. teach the identity
guard to read the `pId=<rec>` and `/imageviewer/collections/<db>/` ids from the viewer URL so the
capture can be fired where the download actually happens.

Also please fix **BUG-009** as part of this — right now the sidebar "Suggested records" thumbnails
are listed as "RECORD FILE NOT CAPTURED", so the toast buried the one real failure line under eight
false ones.

## Repro
- URL: `https://www.ancestry.com/search/collections/7884/records/119680425`
- Capture folder written: `data/captures/20260714-ancestrycom-1004e237/` (render only, no scan)
- apid: `1,7884::119680425`

---

## UPDATE 2026-07-14 — Option A WORKS. Agent path now gets the full-res image.

After the owner granted the host (popup "Capture this page once" on ancestry.com), I re-ran the exact
agent flow (arm-download → click Ancestry's Download → capture on the record page) for
`7884:119680425`. Result: the armed intercept delivered **`9-4449171-00576-1.jpg`, 3914×2748, 1.4 MB**
— the genuine full-res 1910 census scan (cardinal check: read "Rose, Daughter, 13, New York" off it).

So the blocker was the **activeTab host grant**, exactly as the toast said. With it granted, the
agent completes the whole capture of that one record in the user's session. Remaining questions for GS:
- Does the grant PERSIST across sessions, or must the owner re-grant per session? (If per-session, a
  persistent `optional_host_permissions` request from the popup, Option A in the original report,
  would make it a true one-time action.)
- BUG-009 must still be fixed — see that file; it now provably WRITES junk, not just warns.
