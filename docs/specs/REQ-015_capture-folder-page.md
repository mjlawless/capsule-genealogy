# REQ-015 — a readable page for each capture, and safe viewing of what a capture saved

**Status:** 🟡 §A–§B **BUILT 2026-10-29, for 0.2.7** (unreleased) · §C–§D **BUILT 2026-10-29 (owner rulings C1, D1, D2), live check pending** · Owner request 2026-10-29: the capture's own page (`/<capture>/`) was a
bare file list; it should be "a pretty version of the citation view … enriched with … the
extraction, manifest, citation, and all media files", JSON should be readable "by default",
ads should be stripped from the saved page and the PDF, and the saved `.html` is "a pretty
thin version of the real thing … formatting is broken, and images are broken".

## Names (owner, 2026-10-29)

"Saved page" had come to mean two different files. Each copy now has one name, used in the
index, the capture's page, the views, the popup, the help page and the AI connector:

| File | Name | What it is |
|---|---|---|
| `media/<n>-page.mhtml` | **Full page** | the complete copy, with pictures and layout (§D) |
| `snapshot.html` | **Page text (HTML)** | the page's text and markup, what Capsule reads facts from |
| `media/0-record.pdf` | **Page PDF** | the page printed as you saw it |

The outcome fields are `full_page` / `full_page_skipped_reason`; the extension helper is
`appendFullPage`.

## §B — archived pages ran their own scripts on Capsule's address (security, BUILT)

Found while looking at the page text (`snapshot.html`) for this request. `snapshot.html` is the site's own
markup with every `<script>` it had. The backend served it from its own origin
(`http://127.0.0.1:8765`), so opening a saved Find a Grave memorial ran about 70 requests: ad
networks (doubleclick, amazon-adsystem), Google Tag Manager, a FullStory session recorder,
ancestry.com and findagrave.com. A script there could also read Capsule's own pages. Verified
harmlessly: `fetch('/')` from an archived page returned the index, which holds the API token, so a
hostile ad script could have read gated files from the archive and called the correction and
delete endpoints.

**Fix (217812a):** every capture file except a PDF is served with
`Content-Security-Policy: sandbox; default-src 'none'` (images, media, styles and fonts from the
archive only) and `nosniff`. A sandboxed page has an opaque origin, runs no script and loads
nothing from the network. PDFs get `nosniff` only, because Chrome's PDF viewer will not load
under a sandbox and a PDF runs no page script. Verified in Chrome: the archived page's origin is
`null`, no tracker globals exist, its outside requests stop; the record PDF still opens; review
page images still load. **Every released version (≤ 0.2.6) has this hole; 0.2.7 closes it.**

Left: Chrome still issues the browser's own favicon request, and a `<link
rel="compression-dictionary">` was attempted once on the raw file. The readable view (§A)
drops resource hints entirely.

## §A — the capture's page, readable JSON, readable page text (BUILT)

All three only *read* the folder: its files are sealed evidence, hashed in the manifest.
`capsule/collection/folder.py`; routes in `capsule/backend/app.py`.

- **`/<capture>/` — the capture's page.** The record exactly as the index's detail pane shows it
  (`index.detail_body`, one renderer, not two: spec `captures-index/v2` warns against a second
  renderer over `CaptureRecord`), with the owner's corrections overlaid as on the served index
  and working Copy / Copy for AI buttons. Then: **Media** shown in place (images inline, PDFs in
  Chrome's viewer, each captioned with how it was obtained: "PDF printed from the page as you saw
  it", "The site's own download", …); **Full page** and **Page text (HTML)**, each with a readable view and the raw file;
  **About this capture** from the manifest (kind of source and why, the site's own citation
  claims, record image status, the primary file's SHA-256, format version); **Files**, every file
  with a plain name ("Facts read from the page", "AI reading log"), size, and Readable / Raw
  links. Read-only: corrections stay on the index (✎ needs the token, which this page never
  holds). Served with a CSP that allows only this archive's images and frames.
- **`<file>.json?view` — readable JSON.** Lists of records become tables (personas, assertions,
  snippets), objects become labelled fields, 32-hex machine ids show as their first 8
  characters (full value on hover), text over 600 characters folds behind its first line, and
  a model's raw reply that is itself JSON is shown as structure. Links only for http(s). The
  raw file stays at the plain URL; the folder page links the readable view first.
- **`snapshot.html?view` — the page text (HTML), readable.** Scripts and resource hints dropped; ads
  (Google `div-gpt-ad` slots, `adsbygoogle`, `data-ad*`, `ad-` classes), iframes, pop-up
  dialogs and Capsule's own on-page UI hidden by a stylesheet, not deleted; a bar says what was
  hidden and links the raw file. Served in the same sandbox as the raw file.

**What §A cannot fix:** the page text looks plain because its stylesheets and pictures lived on
the site and were never saved: the snapshot is `outerHTML`, which holds only references to
them. Viewing must not fetch them (that is a request to the provider and breaks the
compliance rule), so an existing snapshot can never look like the real page. The PDF is the
faithful "as you saw it" record for those. Fixing this for new captures is §D.

**Review (Codex, 9 rounds, approved).** Every finding was real and has a test in
`tests/test_req_015_folder_page.py`: a damaged manifest (wrong types in containers, in values,
in the header's title and site) must still give a page that lists the files, every section but
the file list being fenced; JSON nested 350 or 100,000 deep, and a lone surrogate (`"\ud800"`),
must still render; the saved-page cleaner is a single forward pass (a backtracking regex took
15 s on a crafted 256 KB page); a sparse JSON list is not turned into a table (60 KB of JSON had
become 156 MB of HTML).

## §C — ads hidden from the PDF and the full page (BUILT)

**Owner ruling C1 (2026-10-29): "yes, hide the ads by default."**

`extension/render-pdf.js` already hid Capsule's own UI and agent overlays before printing
(inline `display:none`, marked, restored after). Ads join that step, by the conventions ad
networks use and **nothing broader**: Google `div-gpt-ad` slots, `google_ads` ids,
`adsbygoogle`, `data-ad*` and `data-google-query-id` attributes, `ad-`/`ad`/`ads`/`advert`
classes and ids, `aria-label="Advertisement"`, and iframes whose host is an ad network
(doubleclick, googlesyndication, amazon-adsystem, …). Never iframes, embeds, objects or
dialogs in general: certificate sites show the record itself in an `<embed>` or `<iframe>`,
and hiding one would erase the evidence from its own archive (a test keeps the list from
growing into those).

**Measured before shipping** on all 478 captured pages in the owner's archive (parsed, never
loaded): matches only on Find a Grave (its ad slots, e.g. `#headerspot.ad-wrapper`, Google
ad frames), Ancestry (invisible 0×0 DoubleClick tracking frames, one per page) and
Newspapers.com and one county records site (ad and tracking frames). **None held any text of a fact
read from its record.** NYC, RootsIreland, FamilySearch, NLI, Archion and the others: no
matches.

What was hidden is recorded where it lives: each hidden element keeps its
`data-capsule-render-hide` mark inside `page.mhtml` (§D), and the outcome reports
`ads_hidden`. When the full page is skipped (too large), the count survives only in the
outcome: no new field was added to the wire, because a store-updated extension meets
older backends, and an unknown field there is a 422 for the whole capture.

## §D — the whole page saved as MHTML (BUILT)

**Owner rulings D1/D2 (2026-10-29): "Add the page.mhtml file to every new capture. Go ahead
an update 0.2.7."** The owner had also said MHTML was only a suggestion if something
better existed; the comparison table below is why it is the one.

| Way | Looks like the page | Viewable from the index | New permission | Requests to the site |
|---|---|---|---|---|
| `outerHTML` (still kept) | no (no CSS, pictures by reference) | yes | none | none |
| **MHTML via `Page.captureSnapshot`** (chosen) | yes: CSS, pictures, fonts inside one file | through the backend's unpacked view | none (the `debugger` access the PDF already uses) | **none, measured** |
| MHTML via `chrome.pageCapture` | same | same | yes; a warning-bearing permission disables the extension for current users until they re-approve | none expected |
| Inline everything in the page script | yes | yes | none | yes: cross-origin CSS and pictures fetched again |
| Full-page screenshot | pixel-exact | yes | none | none, but not searchable, and the PDF covers it |

**How it works.** In the PDF's debugger session, after `Page.printToPDF` and before the page
is restored (so ads are still hidden), `Page.captureSnapshot {format: "mhtml"}` with its own
20 s timeout; a failure never costs the PDF. It travels as one more ordinary media item
(`page.mhtml`, `multipart/related`, provenance `render:page-mhtml`), so the wire format is
unchanged and an older backend stores it as an unknown file rather than failing. It is
appended **last**, by `appendSavedPage`, and left out when the capture would pass the
backend's limits (25 files, 100 MiB), so it can never displace the record. The backend
recognizes it by its bytes (`From: <Saved by Blink>`, or MIME + `multipart/related` +
`Snapshot-Content-Location`, so an ordinary e-mail is not taken for one) and never counts it
as the record image (`sufficiency._NOT_THE_RECORD`).

**Measured:** a headless Chrome loaded a local page (stylesheet with a web font, images, a
lazy image below the fold, an iframe) from a server that logged every request; during
`Page.captureSnapshot` it logged **none**. The lazy image, never loaded, is not in the file.
The output is 7-bit ASCII. (`tests/fixtures/mhtml/blink-sample.mhtml` is that file.)

**Viewing.** Chrome refuses MHTML over http, so `capsule/collection/mhtml.py` unpacks it
into one page: every stylesheet, picture and font the file holds inlined as a `data:` URI
(stylesheets re-encoded UTF-8 and labelled so), the picture that actually loaded chosen
over `srcset`, scripts and resource hints dropped, ads, frames and Capsule's UI hidden, a
bar saying so. Served in a sandbox that admits only `data:` (no script, no network). The
capture's page (and the index's details) link it as **Open the full page** and offers the file as a download (it
opens by double-click in Chrome or Edge). Written for hostile input from the start: every
scan forward-only, parts and output size capped, `@import` depth-limited and cycle-checked,
CSS inlined as base64 so `</style>` in it is inert, malformed files degrade to an
explanation (`tests/test_req_015_saved_page.py`).

**Review (Codex, 8 rounds, approved).** Each finding was real and has a test in
`tests/test_req_015_saved_page.py`: every inlined copy is charged against the size budget
(one picture referenced 1,000 times had made a page 1,000 pictures big); an unclosed
`<style>` ends the scan (repeated ones rescanned quadratically); each stylesheet is expanded
once, references are capped, and a part too big for what is left is refused once (branching
`@import`s had grown the work as N⁴); quoted `@import "x.css"` is inlined; the scans
lower-case A–Z only (`"İ".lower()` is two characters, and shifted the markup after a Turkish
name; the plain saved-page cleaner had the same flaw); CSS URLs end at their own unescaped
quote or `)` (`url("scan_(1).png")`, `url(scan_\(1\).png)`).

**Where the build differs from the proposal.** The file is `media/<n>-page.mhtml` (a media
item), not a top-level `page.mhtml`, and the capture format did **not** need a version
bump: the media list already carries any file with its provenance
(`capture-folder-v1.md` §4a notes the new provenance).

**Release gate:** the real extension path (`chrome.debugger` → `Page.captureSnapshot`) is
proven only in headless Chrome over CDP, not yet through the extension. One live capture
with the 0.2.7 build (owner) must show `full page: archived` and open correctly before
release.
