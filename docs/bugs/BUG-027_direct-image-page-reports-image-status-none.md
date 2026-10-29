# BUG-027 — a page that IS the record image reports `image_status: "none"`, and the verifier calls a 4174px print "no measurable image"

**Status:** ✅ **FIXED 2026-10-26** (unreleased; filed the same day, from `docs/inbox/`). · **Severity:** 🟠 **MEDIUM**: no
evidence is lost (the image is banked and hashed), but a downstream reader cannot tell a good
capture from a failed one by `image_status`, and the failed one's message hides its fix.

**Found by:** the research session, capturing pages of a French departmental archive's IIIF
image server opened directly in the tab (`…/iiif/…/full/max/0/default.jpg`: a page whose only
content is the `<img>`, titled `default.jpg (4190×2404)`). Public, no login. Extension and
backend 0.2.6.

## What happens

**Without site access** (six captures, e.g. `20261026-archives57com-cc93db96`): only
`media/0-record.pdf` (`render:page-pdf`, manifest 4174×2288) is saved. The PDF embeds the full
raster, but a print is excluded from the §3.3 floor by provenance (`sufficiency._NOT_THE_RECORD`,
correctly). The shortfall then says *"it holds no measurable image at all"*, which is false on
the capture's own manifest, and it does not point at the real cause: the extension had no host
access, so it could not fetch the image.

**With site access** (five captures, e.g. `20261026-archives57com-448dadc4`): the image is
banked as `media/1-default.jpg` (`page:embedded`, 4190×2404, SHA-256-identical to the server's
file), and `verify_ok: true`. **But `image_status` is still `"none"`.**

## Cause

The two gates disagree about which media can be the record image:

- `record_media_shortfall` (the floor behind `verify_ok`) counts every measured item except
  `render:page-pdf` and `viewer:canvas`, so the `page:embedded` JPEG satisfies it.
- `_check_record_images` (the grade behind `image_status`) grades only
  `RECORD_IMAGE_PROVENANCE` (`ancestry:download`, `site:official-download`, `viewer:canvas`).
  `page:embedded` is measured but never graded, so off Ancestry `_derive_image_status` returns
  `measured or "none"`, which is `"none"`.

Grading every `page:embedded` image is wrong (the code says why: a page-served thumbnail is
legitimately a thumbnail). The case here is narrower: **the page IS the image**.

## Fix

1. **The page image is graded as the record** (`backend/app.py`, `_is_the_page_itself`). A
   media item whose `source_url` equals the capture's URL (fragment ignored) is classified
   and pixel-gated like the download/canvas provenances, so a 4190×2404 file reports `full`
   and one under 800px `failed`. Keyed on the URL, not the provenance: one of the reported
   captures came through a path that set no provenance at all. **Rasters only**: a PDF
   opened in the tab also has its own URL, and a text PDF graded as an image would flip from
   `none` to `failed`. A thumbnail on an ordinary page never matches.
2. **The shortfall says what was held and why** (`pipeline/sufficiency.py`). When the only
   measured artifact is a print or canvas grab, it is named ("it holds only
   `media/0-record.pdf` (4174px), a page render or screen grab, which is never the record
   image itself"). When the extension reported a record file it could not fetch, the
   folder-sourced verify reads that reason back from `manifest.media_warnings` and appends
   it ("The record file was not captured: default.jpg — Capsule has no access to …"). The
   warning prefix is one constant, `MISSED_RECORD_FILE_PREFIX`, written by the backend and
   read by the verify. "No measurable image at all" now means exactly that.

**Decided: a sized IIIF request** (`…/full/1000,/0/default.jpg`, 1000px) reports
`image_status: "full"` (it clears the 800px usable floor, as a provider download would) and
`verify_ok: false` (an image-only capture needs 1500px to archive the record), with the
shortfall saying which to act on. That is the existing split for a zoomed Archion export.

**Not changed: the existing captures.** Their manifests are hashed and append-only, so the
five verified captures keep `image: "none"` on disk; a new capture of the same page gets the
new status. `capsule supersede` (REQ-011) marks each failed capture by its verified one.

## Also reported, not part of this fix

- **`source_quality: "derivative"`** for a scan of the original register. Extraction reads
  only the HTML, which here is one `<img>` tag, and the prompt makes `derivative` the safe
  default when the record cannot be seen (`extract.py`, `source_quality_of`). That is the
  intended behaviour for a binary field; an `unknown` value would be a contract change
  (`capsule/contracts/evidence.py`). Parked.
- **The failed and the verified capture of each page are both listed.** REQ-011 already covers
  this: `capsule supersede <failed> --by <verified>` marks the failed one, and the index shows
  the link.

## Verification

- `tests/test_bug_027_direct_image_page.py` (9 tests): the direct image reports `full` and
  passes verify; a URL fragment does not defeat the match; under 800px it is `failed`; 1000px
  is `full` + verify fails; a thumbnail on an ordinary page stays `none`; a PDF opened in the
  tab stays `none`; a print-only capture names the print and the access cause; the wording by
  case; the prefix the verify reads is the one the backend writes. 7 fail without the fix (the
  two guards pass either way, as they must); removing the raster-only check fails the PDF guard.
- Every capture in the archive (494) re-verified before and after: **no `verify_ok`
  changed**. Nine shortfall messages changed, all print-only captures: the six reported here
  now name the print and the missing site access, and three older ones (a tile-viewer page, an
  Irish civil-records page, a newspaper page) now name their print.
- Follow-up: off Ancestry, a "no download was armed" reason no longer adds the "one-call Ancestry image flow" remedy to a `failed` image (a 600px direct image was told to use it). Test added; fails without the fix.
- Full suite 1,272 passed; ruff clean; Codex adversarial review on both commits: approve, no findings.
