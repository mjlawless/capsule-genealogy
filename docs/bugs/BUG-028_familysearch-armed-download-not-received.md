# BUG-028 — FamilySearch: an armed capture never receives the viewer's own image download

**Status:** 🔎 **OPEN** (filed 2026-10-28 from `docs/inbox/`, reported 2026-10-26; cause not yet
confirmed). · **Severity:** 🟠 **MEDIUM**: no evidence is lost (the JPG lands in Downloads and
`capsule ingest` banks it), but every FamilySearch record image needs that manual step.

**Found by:** the research session, on FamilySearch image-viewer pages
(`www.familysearch.org/ark:/61903/3:1:…`). Extension and backend 0.2.6. 3 of 3 armed captures.

## What happens

Flow: `capsule:arm-download` → click the viewer's **Download** → choose **JPG Only** → click
**Download** (all by accessibility ref, not coordinates) → `capsule:capture` with
`image_intent: "expected"`. The page capture completes but reports that no download arrived
within 20 s, and `image_status` is not `full`. The JPG **does** land in the Downloads folder
(632 KB and 1.48 MB in two of the cases). Each was recovered with
`capsule ingest <file> --url <viewer url> --gating gated --provenance site:official-download`.

The macOS provenance of the downloaded files (`kMDItemWhereFroms`) holds one entry,
`https://www.familysearch.org/`, and no file URL: what Chrome records for a `blob:` or `data:`
download. Newspapers.com files (BUG-022) carried the image host instead.

## Analysis so far

The reporter's hypothesis was that the armed profile rejects a `blob:` URL. **The code already
handles that case:** `fromArmedSite` (`extension/download-intercept.js`) falls back to the
download's `referrer` when neither `finalUrl` nor `url` has a host, and the recorded referrer is
the armed host itself. So the hypothesis does not fit as stated. Candidates, in order:

1. **`DownloadItem.referrer` is empty** for a download started from a `blob:` anchor, even though
   the OS file metadata shows a referrer. Then no host matches, and the download is refused.
2. The download is refused for another reason (for example a MIME such as
   `application/octet-stream`), and the refusal message is lost or not surfaced.
3. The arm is not live when the download starts (a different tab, or the arm expired).

## Next step

Arm one FamilySearch capture with the service worker's console open: the refused-download log
added for BUG-018 names the reason. Fix accordingly. If it is candidate 1, find a correlation signal
the extension can actually check (`DownloadItem` has no tab id), such as the content script
watching the viewer's own blob creation. Do not widen to "any hostless download during an arm",
because `cleanUpDownload` deletes the claimed file. Add a regression
test.
