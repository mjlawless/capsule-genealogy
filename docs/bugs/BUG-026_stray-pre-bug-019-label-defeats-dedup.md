# BUG-026 — a stray page label saved before BUG-019 stops dedup matching, so a page is held twice

**Status:** ✅ **FIXED 2026-10-24** (unreleased; filed the same day). · **Severity:** 🟡 **LOW**: the failure direction is
harmless (a duplicate is kept, nothing is lost), but the archive then holds two captures of
one page and the AI connector reports both.

**Found by:** the research session's 0.2.6 connector test: `was_captured` for one NYC
Historical Vital Records page (`/view/<id>`) returned two captures, both "verified complete",
made a month apart.

## What happens

Before BUG-019 was fixed (2026-10-21), `page_label` could be read from any dropdown on the
page. The older of the two captures has `page_label: "Select Language"` (the site's
translation menu) in its manifest; the newer one, made after the fix, has none. The dedup
identity includes the page label, so the label-less request did not match the held capture,
and the page was captured again instead of being refused (or repaired).

This was noted while building REQ-012 ("BUG-019 residue") and is now seen on a real record.
`capsule query` has the same blind spot.

## Fix

`capsule/pipeline/dedup.py` `_scan_captures`: on the URL branch (every page that is not an
Ancestry record), the held manifest's label goes through `accepted_page_label` before it is
compared, the same filter BUG-019 applies to new requests, so a junk label compares as no
label. Manifests are not rewritten (append-only; the stored label stays as evidence of what
was read). `capsule query` goes through the same scan, so it is fixed too.

**Deliberately not changed: the Ancestry record branch.** A manifest does not record whether
its request named the record (`expected_apid`), and a held extra image (REQ-009) read as
label-less would block the record's default image, a false block, which REQ-004 ranks as the
worse failure. Known limit: a junk label held on an Ancestry *record* capture still splits its
identity. The archive has none today (count below).

## Verification

- `tests/test_bug_026_stray_label_dedup.py` (4 tests): a held "Select Language" capture is
  found for a label-less request and is complete; through the backend the duplicate is
  refused (409, no new folder); a held RootsIreland label and a held Ancestry extra-image
  label still keep their captures apart (the evidence-losing direction). The first two fail
  without the fix.
- **Corpus, 2026-10-24:** 24 page captures hold a label the filter now ignores ("Select
  Language" ×13 on NYC; "Kings" ×5 and "Fayette" ×2, county menus on Ancestry image-viewer
  pages whose URL already names the image; a Find a Grave notice ×3; "-" ×1 on
  IrishGenealogy). At no URL do the ignored labels differ from each other, so no two
  different records collapse into one identity. Ancestry record captures holding a label: 0.
  **Duplicate pairs this bug already made: 3**, all NYC Historical Vital Records pages. They
  stay (append-only); marking one of each pair superseded waits on REQ-011.
