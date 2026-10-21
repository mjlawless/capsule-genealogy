# BUG-020 — Ancestry browse-image captures: a false "re-capture" alarm, and a thin citation

**Status:** ✅ **FIXED 2026-10-21.** Filed 2026-09-29 (found by the corpus check). ·
**Severity:** 🟡 **LOW–MEDIUM** — the evidence was banked correctly, but the capture told the
researcher it was unusable, and the citation could not locate the page.

> ## ✅ FIXED — 2026-10-21
>
> - **Defect 1:** `blank_render_warning` (`capsule/pipeline/render_check.py`) is the one wording
>   for both the capture-time check and `verify_capture`'s re-measure. Beside a record image
>   measured `full`/`degraded-canvas` it reads *"record.pdf renders blank, but this capture
>   also holds the record image 005537869-00063.jpg, which is the evidence. Nothing needs
>   re-capturing."*; with no such image, BUG-001's alarm is unchanged. Verify reads the
>   folder's own `image` status, so both existing browse captures now re-report with the
>   corrected wording, with nothing rewritten. A folder with no measured status keeps the
>   alarm, because an unmeasured image is not trusted to be the record.
> - **Defect 2:** new `ancestry-browse-image-v1` citation, from `ancestry_browse_path_from`
>   (`capsule/pipeline/page_identity.py`), which reads the viewer's `browsePath-N` inputs and
>   its "N of M" counter. Result for `a52051e9`: *"Pennsylvania, U.S., Wills and Probate
>   Records, 1683-1993," digital images, ancestry.com (… : accessed 28 September 2026), Fayette
>   > Orphans Court Records, Vol 1-3, 1783-1844 > image 63 of 695.* The image number is
>   cited only when the counter agrees with the number the URL's image id ends in (the URL
>   is the live leaf); otherwise the path is cited without one. A page with assertions still
>   cites the record (`record-transcript-v1`). Checked against all 7 image-viewer captures on
>   disk: 7 of 7 yield a clean path. Existing citations are not rewritten (append-only); new
>   captures get the new template.
> - **Found on the way, fixed:** the backend's `is_ancestry_url` used `ancestry\.[a-z.]+$`,
>   which accepted `ancestry.com.evil.example` and `ancestry.evil.com`, so a lookalike page
>   could mint record ids that dedup, the ledger and the identity guard trusted. It now names
>   Ancestry's domains, the same list the extension has used since BUG-018.
> - 24 tests in `tests/test_bug_020_ancestry_browse_image.py`, including an end-to-end
>   `POST /capture` read back off disk; both warning paths mutation-checked. Codex adversarial
>   review: approve.
> - **Not done (noted below, unchanged):** offering `capsule transcribe` for browse images,
>   and the `site:official-download` provenance on apid-less Ancestry downloads.

## Context

The first two captures of Ancestry **browse images** in the corpus: pages opened in Ancestry's
image viewer by browsing a collection, with no indexed record behind them (so no apid). Both are
from the research workspace (Fayette Co., PA, Orphans Court Docket vol. 1):

- `20260928-ancestrycom-c60e5f1f`: `/imageviewer/collections/8802/images/005537869_00061`
- `20260928-ancestrycom-a52051e9`: `/imageviewer/collections/8802/images/005537869_00063`

What went right: the generic armed download (REQ-007 §2) caught Ancestry's own Download, a
4714x3308 JPG, measured `image_status: full`. Gating is correct (`gated`, copyright-flagged, no
Wayback submission), even though the extension's `gating: msg.apid ? "gated" : undefined` sends
nothing for an apid-less capture. The backend derived `gated` itself.

## Defect 1 — a false "NOT usable as evidence: re-capture" alarm

Both manifests carry, in `media_warnings`:

> *record.pdf renders BLANK — something was composited over the page. The capture is archived,
> but this PDF is NOT usable as evidence: re-capture with overlays hidden.*

Re-rendered 2026-09-29: both PDFs really are 100% white. But **the stated cause is wrong and the
advice is wrong.** No overlay is involved: Ancestry's viewer draws the scan in a way that doesn't
print, so a page PDF of the viewer is blank every time. Re-capturing with overlays hidden
reproduces exactly the same result. The capture's actual evidence, the full-resolution JPG, is
intact.

The warning comes from `capsule/backend/app.py` (`looks_blank` → the fixed BUG-001 text), which
knows nothing about the other media in the capture.

**Fix:** when a capture also holds a record image measured `full` (or `degraded-canvas`), a
blank page render is expected and harmless. Say so in a lower-key warning, e.g. *"record.pdf is
blank (the viewer does not print); the record image 1-….jpg is the evidence"*, or suppress it.
Keep BUG-001's alarm for the case it was built for: a blank render with no record image beside
it.

## Defect 2 — the citation cannot locate the page

`citation.json` reads, in full:

> ancestry.com, "Ancestry.com - Pennsylvania, U.S., Wills and Probate Records, 1683-1993"
> (https://www.ancestry.com/imageviewer/collections/8802/images/005537869_00063 : accessed 28 September 2026).

It has no county, no volume and no image number. Yet the extractor's own notes on `a52051e9`
say it saw all of them: *"image 63 of 695 for Fayette County, 'Orphans Court Records, Vol 1-3,
1783-1844'"*. The browse path is Ancestry's equivalent of Archion's breadcrumb, and REQ-007 §4
already turns that breadcrumb into the `citing` clause. Ancestry browse pages need the same:
collection → county → series/volume → "image N of M".

## Also noted (not defects on their own)

- **0 assertions**, which is correct: the page has no index text. Only REQ-006 transcription can
  read the scan. Consider offering `capsule transcribe` for browse-image captures, or queuing it.
- The image provenance is `site:official-download`, not `ancestry:download`, because the profile
  is chosen only when there's an apid. Harmless, but it makes these captures harder to find as
  Ancestry downloads.
- `source.page_label` is `Fayette`, the county browse dropdown. That is
  [BUG-019](BUG-019_page-label-read-from-any-dropdown-on-any-site.md).

## Definition of fixed

A browse-image capture with a `full` record image carries no "re-capture" advice, and its
citation names the county, the volume or series, and the image number.
