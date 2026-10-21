# BUG-022 — Newspapers.com: the armed capture never receives the site's JPG download

**Status:** 🟡 **FIXED 2026-10-21 (extension v0.0.23) — live E2E pending.** Filed 2026-10-03
(triaged 2026-10-21 from `docs/inbox/`). Cause confirmed from the download's own provenance.

> ## 🟡 FIXED — 2026-10-21, live E2E pending
>
> - A `newspapers` entry in `SIBLING_HOSTS` (`/(^|\.)newspapers\.com$/i`), host-selected in
>   `armProfileFor` like `riksarkivet`. `armDownload`'s central authorization covers it with no
>   change, and a test shows a page off newspapers.com naming the profile is refused.
> - `tests/test_bug_022_newspapers_download.py`: 6 tests on BUG-018's node harness (the reported
>   case end to end, a foreign page cannot claim the download, the profile is refused off-site,
>   correlation and profile-selection tables). Mutation-checked: dropping the profile from
>   `HOST_SELECTED_PROFILES` fails 2 tests; loosening the regex to `/newspapers\.com/i` fails 3.
> - Codex adversarial review: approve, no material findings. 886 tests green.
> - **Owed:** reload the extension (v0.0.23), then one armed capture on a Newspapers.com page image
>   returning `image_status: full`.

**Severity:** 🟡 **MEDIUM** — every Newspapers.com page image
needs a manual `capsule ingest`, and a failed armed capture leaves an orphan source behind.

**Found by:** the Genealogy research workspace, 2026-10-03. Same failure
shape as [BUG-018](BUG-018_riksarkivet-armed-download-not-received.md).

## What happens

On `https://www.newspapers.com/image/715957102/`: `capsule:arm-download` (toast "Armed") → the
site's Download → Entire Page → "Save as JPG" → `capsule:capture {image_intent: "expected"}`. The
outcome was `ok: true, image_status: "none"`, *"no download from the armed site arrived within
20s"*, though `Newsday_Suffolk_Edition__1968_08_20_46.jpg` (2874x3562) reached `~/Downloads` in the
same minute.

## Cause (confirmed 2026-10-21, not a hypothesis)

macOS records where a download came from. `mdls -name kMDItemWhereFroms` on the file in
`~/Downloads` gives a URL on **`img.newspapers.com`** (`/img/img?…&a=download&filename=…`). The
armed page is `www.newspapers.com`. Those are siblings, not parent and child, so
`hostBelongsToArmedSite` in `extension/download-intercept.js` refuses them, exactly as it refused
`lbiiif.riksarkivet.se` for BUG-018. The report gave two guesses, a different host or a `blob:`/
`data:` URL. The first was right, the second wrong.

**The extension in use was already v0.0.22.** The outcome text the report quotes, *"no download
from the armed site arrived within 20s"*, is the BUG-018 wording, so this is not a stale extension.
The service-worker console would have named `img.newspapers.com` as the refused host.

The URL carries a signed token (a JWT in the query string). It is not copied into this doc, and it
never reaches the backend: the armed path's `media` object carries the bytes, filename, MIME type and
provenance, but no `source_url`.

**Gating checked 2026-10-21:** all 19 Newspapers.com captures on disk are `gating_status: gated`,
`copyright_flag: true`, `wayback_url: null`, as a paywalled source must be.

## Fix

Add a `newspapers` entry to `SIBLING_HOSTS` (`/(^|\.)newspapers\.com$/i`) and a host-keyed profile
in `armProfileFor`, the same way `riksarkivet` is chosen: from the content script's own
`location.hostname`, never from anything the page event supplies. The profile must be authorized
centrally in `armDownload` against its own domains, as the other profiles are.

Then confirm with one live armed capture on a Newspapers.com page image: it should return
`image_status: full`. If it still fails, the service-worker console now names the host a refused
download came from (BUG-018).

## Related cleanup (not part of this fix)

The failed armed attempt left an orphan source (page-render PDF only,
`record_type "obituary index"`, 0 assertions). The same page was then ingested as
`20261003-newspaperscom-4aa5ff8c`, which the tree cites. See
[REQ-011](../specs/REQ-011_mark-a-capture-superseded.md) for marking the orphan superseded. The
two share one URL, so the dedup did not stop the ingest; that is expected for `ingest`, which
takes a file the researcher chose.

## Definition of fixed

A regression test (`tests/test_bug_022_*.py`, in the style of `test_bug_018_riksarkivet_download.py`)
shows `img.newspapers.com` is accepted for a `www.newspapers.com` arm and still refused for an
arm on any other site, and one live armed Newspapers.com capture returns `image_status: full`.
