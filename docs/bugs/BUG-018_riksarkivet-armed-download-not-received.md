# BUG-018 — Riksarkivet: the armed capture never receives the viewer's own download

**Status:** ✅ **FIXED 2026-09-29 (extension v0.0.22) and LIVE-VERIFIED 2026-09-29.** Filed the same
day, triaged from `docs/inbox/`. · **Severity:** 🔴 **HIGH for Swedish work** — every
`sok.riksarkivet.se/bildvisning` page hit it, and each one needed a manual `capsule ingest`.

> ## ✅ FIXED — 2026-09-29, live-verified the same day
>
> - **Cause confirmed from the page itself.** The viewer config saved in
>   `20260929-sokriksarkivetse-8bcfc652/snapshot.html` points every download URL
>   (`downloadDialogue.downloadUrls`) at `lbiiif.riksarkivet.se`. That is a sibling of the armed
>   `sok.riksarkivet.se`, which the suffix rule refuses.
> - **Fix:** a `riksarkivet` entry in `SIBLING_HOSTS` (`/(^|\.)riksarkivet\.se$/i`). The new pure
>   `armProfileFor({apid, host})` picks it from the content script's own `location.hostname`,
>   never from anything the page event supplies.
> - **Refused downloads are no longer silent, but only where no page can see.** A download from
>   another site during the armed window is logged to the **service-worker console**, with its
>   host. The page-visible timeout is identical whether or not one arrived, and it now names
>   the console. A wrong-MIME download from the armed site itself is reported to the capture
>   (`capsuleRefusedDownload`, a separate session key tied to key + armedAt, so it cannot race
>   staging or leak into a later arm).
> - **Hardening from seven Codex adversarial-review passes.** Most of this was pre-existing,
>   and the one-call flow was reached through the same arm/stage/collect path. Each item has a
>   regression test, and each test was mutation-checked (it fails when the fix is reverted):
>   1. **A page-supplied apid no longer grants the Ancestry profile.** `armProfileFor` requires
>      an Ancestry host, and `armDownload` now authorizes EVERY profile centrally against its
>      own domains. `captureAncestryImage` refuses a non-Ancestry page before any ledger write.
>      Its identity guard checks URL paths only, so `evil.example/search/collections/…` passed.
>   2. **The Ancestry sibling regex names Ancestry's real domains.** It was
>      `ancestry(cdn)?\.[a-z.]+$`, which accepted `ancestry.evil.com`.
>   3. **Only a download STARTED after the arm is claimed** (`item.startTime >= armedAt`). The
>      researcher's own earlier download used to be claimed, and then deleted by `cleanUpDownload`.
>   4. **Only the arming tab, still on the armed site, can collect.** The check uses Chrome's
>      `sender.tab.id` plus the capturing page's own host. The page-supplied key alone let
>      another tab, or the same tab after navigating to an attacker page, take the file. A host
>      binding was chosen over `documentId` so that the old arm-in-viewer, capture-from-record
>      Ancestry flow keeps working.
>   5. **A redirect is judged by its FINAL URL.** `[url, finalUrl].some(...)` let an attacker's
>      own original URL claim a provider file it redirected to.
>   6. **Arm, stage, expiry and collect are serialized through one lock.** A stage commits only
>      into the same arm generation (key + armedAt) while it is still empty, and collection is
>      atomic. Before this, two exports completing together could file the second scan under
>      the first record, and two collectors could both receive one download.
>   7. **Nothing page-visible varies with the researcher's other downloads.** A refused
>      download is named only in the service-worker console. Expiry is settled where it is
>      read, not by whichever download fired the listener. The "already armed for …" and
>      "belongs to …" messages no longer name another tab's record.
> - **Other reviews:** a JS review (4 LOW, applied) and a security review (1 MEDIUM, 3 LOW,
>   all applied or superseded by the above).
> - **Live E2E passed (reported by the Genealogy research workspace).** Two armed captures
>   on `sok.riksarkivet.se/bildvisning`, pages of two different series, both `image_status: full`
>   and `verify_ok: true`:
>   `20260929-sokriksarkivetse-c6220609` (Hanhals C I:5, birth register, 6860x5596) and
>   `20260929-sokriksarkivetse-abf158c6` (Hanhals A I:13, household roll, 7280x6224). The
>   `media/` JPG hashes match the copies the research workspace saved independently. Both captures
>   have `assertion_count 0` and `persona_count 0`, which is expected: Riksarkivet pages carry no
>   index text. The research workspace can close its item.
> - **Same failure shape, another site:** [BUG-022](BUG-022_newspapers-com-armed-download-from-img-subdomain.md)
>   (Newspapers.com serves its download from `img.newspapers.com`).

**Found by:** the Genealogy research workspace, 2026-09-29. Blocked its Swedish parish-register
work.

## What happened

On `https://sok.riksarkivet.se/bildvisning/C0021166_00210` (Byarum C:3, SE/VALA/00050/C/3, a
public and free register), the generic armed flow (REQ-007 §2) ran cleanly up to the hand-off:

1. `capsule:arm-download` with no apid → toast *"Armed. Click the site's own Download now."*
2. The viewer's Download menu → *"Full image 6256 x 4992px (jpg)"*.
3. Chrome saved `~/Downloads/C0021166_00210.jpg` (JPEG, 6256x4992).
4. `capsule:capture {image_intent: "expected"}` returned `image_status: "none"` and
   `image_skipped: "no download arrived within 20s of the capture — did the site's own
   Download actually start?"`

The result was capture `20260929-sokriksarkivetse-8bcfc652`, which holds only the page PDF
(1599x767 viewer chrome, 0 assertions). The researcher banked the scan by hand with
`capsule ingest <file> --url <viewer URL> --gating public --provenance site:official-download`
→ `20260929-sokriksarkivetse-49fab726`.

The image server's IIIF URLs (`lbiiif.riksarkivet.se/arkis!<id>/full/max/0/default.jpg`, `info.json`)
are not served outside the browser session. The viewer's own Download is the only route, which
suits the compliance posture anyway.

## Cause (from the code; the download URL itself is not yet confirmed)

`extension/download-intercept.js` accepts a download only when its host belongs to the armed
page's host (`hostBelongsToArmedSite`). The rule is a dotted-suffix match in **both
directions** plus the `SIBLING_HOSTS` table, which has an entry for `ancestry` only.

- Armed host: `sok.riksarkivet.se`.
- The viewer is IIIF (Universal Viewer: the page carries `#?cv=209&xywh=…` and the IIIF manifest
  `https://lbiiif.riksarkivet.se/arkis!C0021166/manifest`), so the full-size JPG almost
  certainly comes from **`lbiiif.riksarkivet.se`**.
- `lbiiif.riksarkivet.se` does not end in `.sok.riksarkivet.se`, and the reverse doesn't hold
  either. They are sibling subdomains, not parent and child, so `fromArmedSite` returns false.

**The rejection is silent.** `watchDownloads` just returns, and nothing is staged. So after 20
seconds the capture reports *"did the site's own Download actually start?"*, which is wrong:
the download did start and finished, and Capsule chose to ignore it. That misleading message
is a second defect.

**To confirm first:** when fixing, record the download's `url`, `finalUrl` and `referrer` from
`chrome.downloads.search` on a live run. If the file turns out to be a `blob:` URL, then
the referrer (`sok.riksarkivet.se/...`) would have matched and the cause is elsewhere. It could
then be a MIME the `INGESTIBLE_MIME` filter rejects, or the download finishing outside the
60-second arm window. None of these three causes shows up in the current diagnostics.

## Proposed fix

1. **Add a sibling-host profile** for Riksarkivet, e.g. `riksarkivet: /(^|\.)riksarkivet\.se$/i`,
   chosen by the **extension** from the content script's own `location.hostname` (never from
   the arm message; see the security note above `SIBLING_HOSTS`). Today the profile is set
   only when an apid is present (`profile: msg.apid ? "ancestry" : undefined`), so profile
   selection for apid-less arms needs a small host → profile lookup.
2. **Stop dropping silently.** When a download completes inside an armed window but fails the
   host, MIME or size check, stage a diagnostic, not nothing. Something like *"a download
   arrived from lbiiif.riksarkivet.se, which is not the armed site (sok.riksarkivet.se) —
   left alone"*. It must never be ingested: the correlation rule stays the safety property.
   The 20-second message then says what actually happened.
3. **Enhancement, optional:** a Riksarkivet source profile so the citation carries the image id
   (`C0021166_00210`), the volume reference (`SE/VALA/00050/C/3`) and the page label
   (*"Bild 210 / Sida 400"*), as REQ-007 does for Archion.

## Definition of fixed

One armed capture on a `bildvisning` page returns `image_status: "full"`, with no
`~/Downloads` ingest needed. Plus unit tests for `hostBelongsToArmedSite` covering
`lbiiif.riksarkivet.se` under the new profile, and a look-alike host (e.g.
`riksarkivet.se.evil.com`) that must still be rejected.
