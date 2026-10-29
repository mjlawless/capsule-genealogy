# BUG-029 — the saved snapshot contains Capsule's own on-page UI (and an assistant's overlay)

**Status:** 🔎 **OPEN** (filed 2026-10-28, found by the REQ-013 §D measurement). · **Severity:** 🟡
**LOW–MEDIUM**: nothing from the source is lost, but the hashed `snapshot.html` (the "page exactly
as you saw it") also holds markup the source never served.

## What happens

The snapshot is `document.documentElement.outerHTML` (`popup.js` `grabPage`, `content.js` capture
and the Ancestry one-call flow). Everything the extension or another extension has added to the
DOM at that moment is serialized with the page. Counted in the archive on 2026-10-28:

| Injected markup | Snapshots containing it (of 478) |
|---|---|
| Capsule's on-page button (`#capsule-capture-btn`) | 457 |
| Capsule's "Capturing…" toast | 450 |
| An assistant extension's overlay (`claude-agent-glow-border`) | 121 |

A reader of the snapshot, or anything that renders it later, sees Capsule's button and toast as
if they were part of the record site's page.

## Not affected

The capture-time page PDF (`render:page-pdf`). The renderer already hides Capsule's UI before
printing (`render-pdf.js`).

## Fix (proposed)

Serialize a clone with Capsule's own nodes removed (the button, the toast, and any element the
extension marks as its own), and record in the manifest that the snapshot was taken that way.
Leave other extensions' markup alone, but **flag** known overlays in the manifest rather than
silently editing the page. Existing snapshots stay as they are (append-only, hashed).

Since REQ-013 §C the button is on fewer pages (opt-in), but the toast still appears on every
page-script capture.
