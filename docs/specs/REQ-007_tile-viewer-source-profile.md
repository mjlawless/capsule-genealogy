# REQ-007 — A `tile-viewer` source profile (page identity, official export, image floor)

**Status:** 🟡 **PARTIALLY DELIVERED — the correctness hole is closed.** Filed 2026-08-21
from the Genealogy workspace's requests to Capsule: Round 2 (Archion, 2026-07-24)
and Round 3 §R3-1 (2026-08-21). Section state, per §7:

| § | state |
|---|---|
| §1 page identity — `source.page_label` | ✅ **LIVE-VERIFIED 2026-08-22** (v0.0.19) |
| §1 page identity — permalink → `archived_url` | ⚪ open (toolbar control confirmed present) |
| §2 the site's own export as the media | ✅ **LIVE-VERIFIED 2026-08-22** (v0.0.17) |
| §3.1–3.3 image floor + `image_status` | ✅ landed 2026-08-21/22, measured on the 239-capture corpus |
| §4 layered citation (`register-image-v1`) | ✅ landed 2026-08-23 |
| §5 language normalization, §6 profile generalization | ⚪ open |

Suite green at 702 passed (2026-08-24). **Two owner rulings are owed** — see §8.
**Priority:** 🔴 HIGH — §3 is a correctness hole (a capture holding no record can report
`verify_ok: true`); §1–§2 are blocking for the Guntersblum registers the research depends on.
**Supersedes/absorbs:** the "NLI BookReader / IIPImage media capture" backlog entry
(2026-07-09) — same source class, same three failure modes.

> **The ask in one line:** for a paywalled tile viewer, capture *which page* was in view and
> *the site's own export of it* — and refuse to call a capture verified when it holds neither.

## Why now — the failure recurred, unchanged, on 2026-08-19

Round 2 was written from `data/captures/20260725-kirchenbcheronlinemitarchion-794dc3ee/`.
It was never filed here, and on **2026-08-19 a second Archion capture reproduced every fault**:

`data/captures/20260819-kirchenbcheronlinemitarchion-29b8fa24/`

| Fact | Value |
|---|---|
| `source.url` | `…/de/viewer/churchRegister/239254?cHash=…` — **identical for all N images of the register** |
| `media[0]` | `media/0-thumbnail.jpg`, **689×545** — the viewer's fit-to-window preview, banked as if it were the record |
| `media[1]` | `media/1-record.pdf`, `provenance: render:page-pdf` — a browser print of site chrome |
| `result.json` | `image_status: null, assertion_count: 0, persona_count: 0, `**`verify_ok: true`** |

Two captures on disk, 25 days apart, both **archiving nothing of the register** and both
reporting success. This is `CLAUDE.md` §12.4 exactly — a checksum that verifies against
garbage: every hash is correct, of the wrong payload.

## The class, not the site

Archion is one instance of a shape that covers a large part of the remaining corpus: an
**OpenSeadragon/IIIF/IIPImage tile viewer behind a login** (Archion, Matricula, NLI's
BookReader, many state archives). Every one of them fails the same three ways:

1. **One URL for N images** — the viewer deep-links by internal state, not by URL. §1.
2. **A page render instead of the record** — the scan is painted into tiles/canvas, so
   nothing an `<img>`/`<embed>` detector or a page-PDF render can reach is the record. §2.
3. **A green `verify_ok` on an empty capture** — because the image checks are Ancestry-gated. §3.

A per-site adapter for locating bytes is **already approved policy**: the 2026-07-14 PM ruling
(the project's internal backlog, NLI entry) held that the no-CSS-selectors rule protects *extraction* (what
the record says) from layout drift, while a viewer adapter only *locates* the bytes, and does so
by asking the viewer's own API. `probeViewer` (`extension/popup.js:217`) is the working
precedent: it reads BookReader's `br.getPageURI(br.currentIndex())`, returns `null` on any
viewer it does not understand, and stamps `provenance: "viewer:bookreader-iip leaf=N"`.
**This REQ generalizes that precedent; it does not reopen the ruling.**

## 1. 🔴 Page identity — `source.page_label` and a real `archived_url`

**Observed.** Nothing in either Archion capture says which of the register's images was in view.
A citation that cannot name the page is not a citation.

**Two sources of identity, in preference order:**

1. **The site's own permalink (preferred).** Archion's viewer has a **"Permalink erstellen" /
   "Create permalink"** control (🔗 in the toolbar; the string occurs 5× in the captured
   `snapshot.html`) with a checkbox **"Zoomstufe & Bildbereich einschließen"** (include zoom
   level and image region). It mints a stable per-page — optionally per-*region* — URL. Drive it
   and store the result as `citation.archived_url`.
2. **The page selector (fallback).** The selection is already in the DOM of both captures:
   `selected="" value="0">2` — Image 2. Read the selected `<option>`'s text into
   `source.page_label` ("Image 174").

**Acceptance:** two captures of two different images of register 239254 differ in
`source.page_label`, and (permalink path) in `citation.archived_url`; neither is null.

## 2. 🔴 The media must be the site's official export, not a page render

**Cause.** The register is a tiled canvas. A page render can only ever get the on-screen
preview; the in-page media sweep can only ever find the fit-to-window thumbnail — which is
exactly what the 2026-08-19 capture banked at 689×545.

**Ask.** Drive Archion's own export control: toolbar ⬇ → *"Bildausschnitt herunterladen"* →
`Bild` + `Querformat` → **Herunterladen**. That yields a native **~2704×2171** PDF of the
two-page spread. Store it with `provenance: "site:official-download"`.

**The mechanism already exists and is most of the work.** `extension/download-intercept.js`
implements arm-window correlation, the `chrome.extension.isAllowedFileSchemeAccess()` pre-check,
a 25 MiB cap, and the "never touch a download we did not arm" safety property. It is hard-wired
to one host at line 30:

```js
const ANCESTRY_HOST = /(^|\.)ancestry(cdn)?\.[a-z.]+$/i;
```

**Generalize that to an allow-listed host set** (`ancestry`, `archion.de`, …) carried in the arm
message, so `capsule:arm-download` works for any site whose own export is the sanctioned route.
Keep the correlation rule unchanged: ingest only a download that arrives inside an armed window,
from an allow-listed host, after we armed.

**Two behaviours the implementation must encode** (learned live on 2026-07-24):

- **Export what is IN VIEW — the control exports the current viewport, not the full tile
  set, and not the full page.** Confirmed by the owner 2026-08-23 and measured three ways on
  ONE leaf of ONE register:

  | how the leaf was reached / viewed | export |
  |---|---|
  | fresh page load, whole spread in view (leaf 2) | **2644×2164**, 916 KB |
  | reached by changing the `<select>` (leaf 6) | **1432×2228**, 596 KB |
  | same leaf, view differing again | 1.1 MB |

  Size is a property of the VIEW, not of the page and not of the Portrait/Landscape radio —
  that radio was verified `checked: true` on a run that still exported portrait-shaped, so it
  is not the lever. **Zooming in crops the export (703×896 on 2026-07-24), and navigating by
  the page selector does not restore fit-zoom the way a fresh load does** — which is how the
  leaf-6 captures came out cropped. Put the whole document in view and confirm it before
  exporting.

  **A shape tell worth encoding:** a two-page spread that exports TALLER than wide has almost
  certainly been cropped by the view. `1432×2228` for a landscape spread is the signature.

  **Candidate guard (not built):** Capsule already measures every export, so it could compare
  one against previous exports OF THE SAME REGISTER and warn when it is markedly smaller —
  1432 where a sibling leaf gave 2644 is a partial view, not a smaller page. That catches what
  §3.3's absolute floor cannot: a cropped export can clear 1500px and still not be the page.
- **Re-downloading the same image is free** (Archion states this in its panel), so a retry or a
  second export of the same page does not consume the pass's quota. Quota is per unique page
  (1-month pass = 50). A retry loop is therefore safe; a sweep across pages is not, and is
  forbidden anyway by the compliance posture in plan §11.

**Do NOT scrape the tile server.** Archion's tile URLs are signed and expiring
(`/si/<hash>/thumbnail150_300.jpg?md5=…&expires=…`). Bypassing that is both fragile and a ToS
problem. The official export is the correct and compliant integration point — the same posture
as REQ-001's Ancestry download path.

**Acceptance:** an Archion capture holds a `site:official-download` PDF whose largest raster is
≥1500 px on the long edge; the 689×545 thumbnail is **not** banked as record media.

## 3. 🔴 An image floor before `verify_ok` — and `image_status` must never be `null` by omission

**Root cause, precisely.** `capsule/backend/app.py` computed the image status only for Ancestry:

```python
on_ancestry_record = record_ids_from_url(req.url) is not None
image_status = (
    _derive_image_status(html=req.html, measured=measured)
    if (req.expected_apid or on_ancestry_record)
    else None
)
```

So everything BUG-012 (never bank a thumbnail) and BUG-006 (`MIN_USABLE_PX`, provenance-aware
classification) built was **inert on every non-Ancestry site**, and `verify_ok` there degraded to
"the hashes match". That is why both Archion captures passed.

**Ask.**

1. **Ungate the measurement.** Classify captured rasters on any host, so `image_status` reports
   what the capture actually holds. *(Landed 2026-08-21 — see §7.)*
2. **Distinguish "none" from "unknown".** Emit `image_status: "none"` when we looked and there is
   no record image; reserve `null` for "not applicable / not looked at". `null` currently means
   both, which is how an empty capture reads as a clean one.
3. **Fail verification for the image-only original class.** For `source_quality: original` **with
   no text layer**, `verify_ok` must be false unless `media[]` holds an image/PDF whose largest
   raster is ≥ **1500 px** on the long edge.

**The floor must be scoped, not global.** `MIN_USABLE_PX = 800`
(`capsule/pipeline/image_check.py:47`) is deliberately low because **BUG-006** was filed when a
pixel threshold false-failed a legitimately small document — a WWII draft card is a small piece
of card, and 1815 px is a complete archival scan of it. Raising the global floor to 1500 would
reintroduce that bug. The 1500 px floor applies **only** to the image-only-original class this
REQ defines, where a sub-1500 px raster is by construction a viewer preview and never the record.

**Acceptance:** re-capturing the 2026-08-19 Archion page without the §2 export yields
`verify_ok: false` and `image_status: "none"` — it can never again report success while holding
a 689×545 preview.

## 4. 🟠 Citation shape — the breadcrumb is right there

**Observed** (`citation.json`, 2026-08-19): `reference_note` is the raw `<title>` with the site
name repeated and no page —
*"Kirchenbücher online mit ARCHION, 'Baptismal registers 1798-1824, … | Guntersblum | Deanery of
Oppenheim | Central Archive of the Evangelical Church in Hesse and Nassau | Hesse: Church records
online with ARCHION' (…?cHash=… : accessed 19 August 2026)."*

**Available and unused:** that breadcrumb decomposes cleanly into
`All Archives / Hessen / Zentralarchiv der Evangelischen Kirche in Hessen und Nassau /
Dekanat Oppenheim / Guntersblum / <volume>` — repository, region, archive, deanery, parish,
volume — plus the §1 page label.

**Target shape (Evidence Explained, layered):**

> Evangelische Kirche Guntersblum (Kr. Oppenheim), "Taufregister 1838–1858, Trauregister
> 1838–1863, Beerdigungsregister 1838–1855, Konfirmandenregister 1838–1855," p. 358, entry 11
> (Image 174); digital images, *Archion* (\<permalink\> : accessed 24 July 2026); citing
> Zentralarchiv der Evangelischen Kirche in Hessen und Nassau, Darmstadt.

The **"citing"** clause is the point: Archion is the access path, the Darmstadt archive is the
custodian. `record-transcript-v1` already layers a "; citing …" clause from external refs and
provider claims (BUG-014) — this needs a `register-image-v1` sibling fed by the breadcrumb, since
an image-only capture has no personas to name a subject from.

## 5. 🟠 Language — normalize to `/en/`, never click the toggle

Owner preference (2026-07-24): **records of the website should be in English.** Archion serves
`/de/` and `/en/`; `https://www.archion.de/en/viewer/churchRegister/239260` works, needs **no
`cHash`**, and renders chrome and page labels in English ("Image 174").

**⚠️ Site bug to route around:** clicking the in-page **DE|EN** toggle on a viewer page **loses
the document** — it rewrites `/de/viewer/churchRegister/<uid>?cHash=…` →
`/en/viewer?uid=<uid>&cHash=…` and lands on *"No document was selected."* Capsule must **rewrite
the URL directly** to `/en/viewer/churchRegister/<uid>`, never drive the toggle.

Volume titles stay German — correct; they are the archive's own metadata and must not be
translated. (Both captures on disk in fact carry *English* volume titles, i.e. Archion translates
them itself; capture what the site serves, verbatim, either way.)

## 6. Generalization — what a `tile-viewer` profile is

A source profile keyed on a recognized viewer, supplying three hooks and nothing else:

| Hook | Archion | NLI (already built) |
|---|---|---|
| `page_identity()` | selected `<option>` text; permalink control | `br.currentIndex()` |
| `official_export()` | ⬇ → Bildausschnitt herunterladen (armed download) | `FIF=…&CVT=jpeg` (one GET) |
| `verify_floor` | image-only original ⇒ ≥1500 px | same |

An unrecognized viewer ⇒ **return null and fall through to generic detection**, exactly as
`probeViewer` does today. The profile never guesses at a page: a capture that fails loudly is
recoverable, one that silently archives the wrong leaf is a corrupted source that survives into
the tree.

**Open debt this closes:** the NLI BookReader path still owes one live end-to-end run (grant
`iserver.nli.ie`, capture a real register leaf). NLI is the route to the pre-1848 Backs images,
which exist nowhere else. Work it with §2.
**✅ Already closed, found on disk 2026-09-28:** the run had happened before this spec was filed.
`20260713-registersnliie-804f324a` holds `media/0-vtls000632043-026.jpg`, provenance
`viewer:bookreader-iip leaf=25`, measuring **1500×1340**. That is the exact leaf and native size
from the 2026-07-09 diagnosis. `20260709-registersnliie-d6c8ab12` holds a second one (leaf 149,
1500×958).

## 7. Implementation status

- **§3.1 (ungate the measurement)** — ✅ landed 2026-08-21 (`64b9452`), committed with this
  spec. `image_status` is derived for every capture that has a record page, on any host; the
  Ancestry-only gate is gone, and every raster is measured into `manifest.media[]`.
- **§3.2 (`none` ≠ `null`)** — ✅ landed 2026-08-22. A page capture off Ancestry that holds no
  record image now reports `image_status: "none"` — a fact about the CAPTURE's own bytes,
  needing no site knowledge, terminal-BAD. `index-only-no-image` stays an Ancestry-only claim
  about the RECORD, and stays terminal-good. `null` now means only "not applicable".
  *One consumer had to be taught the difference and it is worth recording why:*
  `dedup._assess` treated any non-terminal-good status as incomplete, so emitting `none`
  across the corpus would have marked every text record (RootsIreland, IrishGenealogy,
  FamilySearch) unblockable and silently switched REQ-004 dedup off for most of the
  collection. `none` therefore falls through to verification, which owns the question of
  whether holding no image actually matters.
- **§3.3 (the image floor)** — ✅ landed 2026-08-22, with **two deliberate deviations from the
  ask above**, both tightenings:
  1. The class test for "no text layer" is **the extraction yielded zero assertions**, not a
     PDF-text probe. It is what both Archion captures actually show (`assertion_count: 0`),
     it needs no heuristics, and it is the honest reading of "the image is the whole payload".
  2. **A `render:page-pdf` cannot satisfy the floor at any pixel count.** A browser print of
     the page is the artifact §2 exists to replace; letting a large embedded raster inside one
     pass would readmit the exact failure through the size test. This costs no recall — when a
     page really does show its scan inline, the media sweep banks that `<img>` separately and
     it is measured on its own.

  Implemented in `capsule/pipeline/sufficiency.py` and enforced inside `verify_capture`, which
  grew a fourth result tuple, `insufficient`. Siting it in the folder verifier rather than at
  capture time is what makes it **retroactive**: `capsule verify` re-asks the question of
  captures banked long before the rule existed.

  **Measured against the real corpus (2026-08-22): of 238 captures on disk, exactly 2 newly
  fail — the two Archion captures this REQ was filed about.** Both now report
  `verify_ok: false`, one naming its 689px thumbnail explicitly. No other capture on any site
  is affected, which is the evidence that the scoping in §3.3 holds.
- **§2 (the site's own export as the media)** — ✅ **backend + extension landed
  2026-08-22; the LIVE RUN is still owed** (it needs a subscriber session behind Archion's
  login, exactly as REQ-002's Tools→Download finder did).
  - `download-intercept.js` is no longer hard-wired to `ANCESTRY_HOST`. The correlation
    rule is now `hostBelongsToArmedSite(host, armedHost, profile)`: a dotted-suffix match
    in both directions against the host of the page that armed, plus a small built-in
    `SIBLING_HOSTS` table for providers that serve exports from a different domain
    (Ancestry's `ancestrycdn.com`). Suffix matching, not eTLD+1, deliberately — an
    approximate registrable-domain comparison without a public-suffix list errs by
    WIDENING, and `evil-archion.de` does not end in `.archion.de`.
  - **The allow-list is NOT carried in the arm message**, contrary to the ask in §2 above.
    The arm event is dispatched by page JS, and a page that could name its own trusted
    hosts could arm Capsule to swallow the next download from anywhere. The page says WHICH
    RECORD; the extension decides WHICH HOSTS. `host` is read from the content script's own
    `location.hostname`, never from `e.detail` — pinned by a test.
  - The arm key generalizes from `_APID` to the record URL off Ancestry; provenance becomes
    `site:official-download`, now in the backend's `_RECORD_IMAGE_PROVENANCE` so the export
    is classified and pixel-gated like any other record image.
  - **A real gap this surfaced:** Archion's export is a **PDF**, and `classify_image` runs
    on PIL, which cannot open one — so the single most important file in the capture
    measured `failed`. Record-image measurement now understands PDFs
    (`measure_record_file` → biggest embedded raster), shared with §3.3's floor so the two
    can never disagree about the same file.
  - The canvas fallback stays Ancestry-only on purpose: off Ancestry it *is* the 689x545
    preview this REQ was filed about, and offering it here would rebuild the failure with a
    friendlier label.
  - Extension v0.0.16 → **v0.0.17**.
- **External review, 2026-08-22** (Codex, against the §2+§3 diff). Four findings, all
  verified against the code before being acted on, all real, all fixed:
  1. **A degraded canvas satisfied the §3.3 floor.** `_NOT_THE_RECORD` excluded only page
     renders, so a `viewer:canvas` grab over 1500px verified an image-only original — while
     `image_status` called the same bytes `degraded-canvas` and the warning told the
     operator the record was NOT mined. Every other gate already agreed
     (`ledger._COMPLETE_IMAGE`, `dedup._TERMINAL_GOOD` both exclude it); `verify_ok` was the
     odd one out, and on a high-DPI display a viewer canvas clears 1500px routinely. The
     original reasoning — "a big canvas is at least a real image of the record" — was wrong.
     Canvas provenance is now excluded from the floor.
  2. **The referrer could override a mismatching download host.** `fromArmedSite` was
     `[url, finalUrl, referrer].some(...)`, so inside the armed window any file started from
     the armed page was claimed whatever host served it — and `cleanUpDownload` then DELETES
     it from the researcher's Downloads. Harmless under the old Ancestry-only regex; not
     harmless generalized. The referrer is now consulted only when the download has no host
     of its own (`blob:`/`data:`, which is how a client-built export arrives).
  3. **A non-Ancestry official download left gating undeclared.** `detect_gating` looks for
     ENGLISH markers ("log out", "my subscription") and its own docstring records the limit:
     a page showing none auto-detects PUBLIC — clearing `copyright_flag` and making the
     capture Wayback-eligible, against the CLAUDE.md hard rule. Archion says "Abmelden", so
     the whole class §2 opened up is the class the inference is worst at. A staged provider
     download is now treated as evidence of a subscriber session and floors gating at GATED.
     Backend-side, not extension-side, so the CLI path is covered and a caller cannot bypass
     it; an explicitly declared gating still wins (plan §11, "the user drives").
     *Empirical note: both Archion captures on disk already recorded `gated` because the
     operator declared it, so no capture was actually mis-flagged.*
  4. **The shortfall reason never reached the operator.** `verify_ok` was a bare boolean, so
     the zoomed export read `verify FAILED` beside `IMAGE full` — two true statements that
     contradict each other with no way to tell which was actionable. `verify_shortfall` now
     rides on `CaptureResponse`, `result.json` (additive; omitted when empty), and the
     toast. Deliberately NOT folded into `media_warnings`, which is documented as problems
     that do NOT invalidate the capture — this one does.
- **§2 LIVE RUN — ✅ VERIFIED 2026-08-22** on a subscriber Archion session, extension v0.0.17,
  register 239254 (Guntersblum), Image 2. Capture
  `20260822-kirchenbcheronlinemitarchion-73eb7db7`:

  | | 2026-08-19 (before) | 2026-08-22 (after) |
  |---|---|---|
  | record media | *none* | `site:official-download`, **2644x2164** PDF, 916 KB |
  | thumbnail | 689x545, banked as the record | 661x541, banked as `page:embedded` — not record media |
  | `image_status` | `null` | **`full`** |
  | `verify_ok` | `true`, holding nothing | `true`, holding the register page |

  Confirmed along the way: the arm refusal named the record URL
  (`a download is already armed for https://www.archion.de/en/viewer/churchRegister/239254`),
  proving the key generalized past `_APID`; the v0.0.17 toast read *"Armed. Click the site's
  own Download now."*; `cleanUpDownload` left nothing behind in Downloads; gating recorded
  `gated` + `copyright_flag: true`; and the export renders the real handwritten spread
  (`blank_ratio` 0.558, nowhere near the 0.99 blank gate).

  **Two observations from the live page that were not in the spec:**
  1. **The export is a PDF, and Archion says so in the panel** ("Save your find now in a PDF
     file... Re-downloading this page is, of course, completely free for you"). The PDF
     classification fix was load-bearing, not defensive: without it this capture would have
     reported `image_status: failed` on a perfect 2644px scan.
  2. **This viewer serves `<img>` tiles, not a `<canvas>`** — `canvasCount: 0`, 194 `<img>`
     on the page. §6's table lists Archion's `page_identity()` as canvas-based; it is not.
     The English UI offers **Portrait / Landscape** (no "Bild" radio as §2 recorded), and the
     toolbar exposes a **Permalink** control, i.e. §1's preferred identity source is present.
- **§3.3 class WIDENED 2026-08-22, after the live run.** `source_quality` is out of the
  class test entirely. The class is now:

      no assertions AND no provider claims
      AND (no coverage claims  OR  the source is a known tile viewer)
      AND the capture is not archive-only

  Three things the corpus forced, none of which were in the original plan:
  1. **Coverage claims are text evidence — except on a tile-viewer host.** A RootsIreland
     holdings page yields 140 coverage claims, no assertions and no image, and is a good
     capture (it is the one that exposed a false premise standing 12 sessions). But an
     Archion register page yields 4, parsed out of the volume title, while holding no
     register. Sweeping the corpus with coverage exempting outright showed **both captures
     this REQ was filed about passing again** — the widening had removed its own target. So
     `TILE_VIEWER_HOSTS` (archion.de, registers.nli.ie, matricula-online.eu) decides, by
     HOST: a fact about the site that cannot drift like a layout or move like a model
     judgment. This is REQ-007 §6's source profile in its smallest useful form.
  2. **Archive-only document captures are exempt.** `run_document_capture` makes no model
     call, so "the extraction produced nothing" is trivially true of every one of them and
     carries no information. Applying the gate failed 8 document tests and would fail a text
     PDF — a transcript, a will, a typed certificate — for holding no big raster when its
     evidence is exactly the text. *Known limit, revisit with REQ-006: a document capture
     holding a 689px scan is not caught. Once vision/HTR extracts from these they stop being
     archive-only and the premise holds again.*
  3. **Provider claims count as text evidence unconditionally** — the provider naming the
     source is text, on any host.

  **Measured on the corpus (239 captures): 5 fail.** Both target Archion captures; the
  2026-08-22 capture passes. The other three (RootsIreland ×2, MyProVenue ×1) produced zero
  structured evidence AND hold no image — `ledger.mined` already refuses to close such a
  record, so this only makes the folder verdict agree with the ledger. The 140-coverage
  holdings page is correctly exempt.
- **~~A gap the live run exposed, NOT yet addressed.~~ RESOLVED by the widening above.** This capture recorded
  `source_quality: derivative`; the 2026-08-19 capture of the *same register* recorded
  `original`. The extractor's judgment is not stable across captures of one source — and
  §3.3's floor is scoped to `original` AND zero assertions. So a future Archion capture that
  fails to get the export could be labelled `derivative` and skate past the gate that exists
  to catch exactly that. §3.3 still did its job on both captures on disk, but its scoping
  rests on a signal that moves. Worth a decision before relying on it: either widen the class
  (e.g. any capture with zero assertions and no text layer), or make `source_quality`
  deterministic for a recognized viewer profile under §6.
- **§1 (page identity) — ✅ `source.page_label` landed 2026-08-22** (extension v0.0.18).
  The permalink half of §1 is still open; the selector half is done, and a live check
  changed its design mid-build.

  **The spec was wrong about the markup, twice, and the real snapshots said so:**
  1. The option text is the **bare number** (`<option selected="" value="0">2</option>`),
     not the "Image 174" §1 describes. The site renders "Image 2" in its thumbnail rail and
     "2 / 173" beside the select; the element holds only digits. Stored verbatim — turning
     it into "Image 2" is §4's business, and a parser that invents a word the page never
     showed is the wrong place to be creative.
  2. On 2026-08-19 that text sits inside **two nested `<font dir="auto">` tags** — Google
     Translate rewriting the DOM in the researcher's browser before the snapshot was taken.
     Anything reading `>([^<]*)<` returns empty on that capture. Tags are stripped.

  **🔴 And the thing that nearly shipped as a silent wrong-page bug.** The parser first ran
  backend-side against `snapshot.html`, which looked right and passed on all three captures
  on disk — because all three were at the default leaf. Driving the live viewer to image 6
  showed why that was luck:

  | | value |
  |---|---|
  | live `selectedIndex` (the screen) | **6** |
  | `selected` attribute in `outerHTML` (the snapshot) | **2** |

  Serializing a page writes the ATTRIBUTE, and the attribute never moves off the option the
  viewer first loaded — navigating changes only the live property. So a researcher who
  browses to image 174 and captures would have been handed `page_label: "2"`: **a
  confidently wrong page in a citation that looks complete, which is worse than no page at
  all.** The content script now reads the live DOM (`livePageLabel()`) and sends
  `page_label` on the capture; that value WINS over parsing. The parser is retained as the
  fallback for the CLI, whose fetched HTML was never navigated and whose attribute is
  therefore truthful.

  *Open evidentiary wrinkle, no ruling yet:* the archived `snapshot.html` still carries the
  as-loaded `selected` attribute, so a future auditor comparing it against `page_label` will
  see them disagree. Capsule does not rewrite the snapshot to match — mutating archived
  evidence is a bigger call than this REQ should make on its own.
- **🔴 A blocker the §1 live run exposed, and fixed: a register was ONE record to dedup.**
  Capturing image 6 of register 239254 was refused — `HTTP 409: already mined` — against the
  morning capture of image 2. Dedup keys a non-Ancestry capture on its URL, and a tile
  viewer serves all 173 images at one address, so **the collection could hold exactly one
  leaf of any register and refused every other.** For the Guntersblum registers the research
  depends on, which are the reason §1 and §2 exist, that is fatal — and it is the failure
  `dedup`'s own docstring names: a false block LOSES EVIDENCE, a false "new" merely
  re-captures. REQ-008 predicted it in the abstract ("one viewer URL serves N pages"); the
  live run turned it into a wall.

  `page_label` is now part of the identity, in both `record_identity` (the in-flight lock)
  and the manifest scan. An Ancestry record keeps its `{collection, record}` key — it has no
  leaves. A capture banked before the field existed carries `page_label: None` and so
  matches only a request that also has none: it can still be repaired, and it can never
  block a different leaf it cannot be compared against.
- **§1 LIVE-VERIFIED 2026-08-22** (v0.0.18 → v0.0.19), capture
  `20260823-kirchenbcheronlinemitarchion-99ef65fd` of register 239254 at image 6:

  | | value |
  |---|---|
  | `source.page_label` in the manifest | **`6`** — the leaf on screen |
  | `selected` attribute in the archived snapshot | **`2`** — still the as-loaded leaf |
  | `dedup` | `new` — the second leaf is no longer falsely blocked |
  | `image_status` / `verify_ok` | `none` / `false` — correct; no export was run |

  The snapshot/manifest divergence is the proof the live-DOM override is load-bearing in a
  real capture and not only in a fixture.

  **A third gap the run exposed:** `postAndShape` in the service worker builds a CURATED
  outcome object, so a new response field is not automatically visible to anyone. The
  manifest said `page_label: "6"` and the API returned it, while the outcome mirror the
  driving agent reads said `undefined`. The same gap had silently disabled
  `verify_shortfall` since it was added — the toast reads `res.verify_shortfall`, but the
  shaper never forwarded it, so §3.3's explanation never reached the operator at all. Both
  are forwarded now, and the tests that "covered" them were rewritten: the old ones asserted
  that `content.js` MENTIONS the field, which passed vacuously while the value never
  arrived. *Rule for this codebase: adding a field to `CaptureResponse` is half the change;
  `postAndShape` is the other half.*
- **§4 (layered citation) — ✅ landed 2026-08-23** as `register-image-v1`, driven by a
  deterministic breadcrumb parser (`page_identity.archive_hierarchy_from`).

  Before, on the 2026-08-19 capture:
  > Kirchenbücher online mit ARCHION, "Taufregister 1798-1824, … | Guntersblum | Dekanat
  > Oppenheim | Zentralarchiv … | Hessen: Kirchenbücher online mit ARCHION" (…?cHash=… :
  > accessed 19 August 2026).

  After:
  > Guntersblum (Dekanat Oppenheim), "Taufregister 1798-1824, Trauregister 1798-1824,
  > Beerdigungsregister 1798-1824, Konfirmandenregister 1801-1824," Image 6; digital images,
  > Kirchenbücher online mit ARCHION (…/en/viewer/churchRegister/239254 : accessed 23 August
  > 2026); **citing Zentralarchiv der Evangelischen Kirche in Hessen und Nassau.**

  The named parts are read **from the END of the trail** (volume, parish, district,
  custodian), because an archive that adds a level above shifts every position counted from
  the front and none counted from the back. A trail too short to support a name yields None
  rather than a guess — naming the wrong custodian is worse than naming none.

  **Two deviations from §4 above, both deliberate:**
  1. The creator is `Guntersblum (Dekanat Oppenheim)` — verbatim breadcrumb — not the
     spec's `Evangelische Kirche Guntersblum (Kr. Oppenheim)`. The page never prints
     "Evangelische Kirche" or "Kr.", and a citation builder is the wrong place to invent
     words the source did not use. The only words added are structural: the parentheses,
     "digital images", "accessed", "citing", and "Image" — which is the site's own caption
     for a leaf, printed beside its thumbnails.
  2. The publisher renders as the site's full name, `Kirchenbücher online mit ARCHION`,
     not the spec's tidier *Archion*. Same reason. Worth a ruling if the long form grates —
     it is a normalization, and normalization belongs somewhere it can be seen.

  Not yet included: `p. 358, entry 11`. That is a reading of the image, which is REQ-006
  (vision/HTR) — the citation is built to take it when it exists.
- **§5, §6** — open. §1's permalink half (drive "Create permalink" into
  `citation.archived_url`) also remains; the toolbar control is confirmed present.

## 8. Owner rulings owed

Both surfaced during the live runs and are recorded in §7 at the point they arose; neither
blocks the delivered sections, and neither should be decided by the implementation alone.

1. **The archived snapshot disagrees with `page_label`, on purpose.** `snapshot.html` carries
   the as-loaded `selected` attribute (`2`) while the manifest records the leaf actually on
   screen (`6`). The live-DOM read is the correct one — that is the whole point of §1 — but a
   future auditor comparing the two will see a contradiction with nothing in the capture
   explaining it. Capsule does not rewrite the snapshot: mutating archived evidence is a
   bigger call than this REQ should make. **Options:** (a) leave as-is and document the
   divergence in the spec only; (b) record the discrepancy as a `capture_note` at capture
   time, so the capture explains itself; (c) something else. *Recommendation: (b) — it
   annotates rather than mutates, which is the same posture as normalization living in
   annotations.*

2. **The publisher renders as the site's full name.** `register-image-v1` emits
   `Kirchenbücher online mit ARCHION`, verbatim, not the tidier *Archion*. Shortening it is a
   normalization, and normalization belongs somewhere it can be seen. **Ruling needed:** keep
   verbatim, or add a per-site display-name normalization with the long form retained
   underneath. *Recommendation: keep verbatim until a second tile-viewer site makes the
   pattern real — §6 is the right home for it, not a special case for one host.*
