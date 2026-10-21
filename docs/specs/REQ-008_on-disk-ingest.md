# REQ-008 — `capsule ingest` — turn a file already on disk into a capture

**Status:** ✅ **DONE 2026-09-24** — all five §4 acceptance criteria covered by
`tests/test_req_008_ingest.py` (17 tests, 731 green). Two deviations from the text below,
both recorded in §6. Filed 2026-08-21 from the Genealogy workspace's
requests to Capsule, Round 3 §R3-2.
**Priority:** 🟠 HIGH — it is what makes REQ-007's Archion export, and every other
already-downloaded evidence file, addressable as a capture.
**Relates to:** [`capture-folder-v2`](./capture-folder-v2.md) (document-primary captures — the
on-disk shape this writes), [REQ-004](./REQ-004_atomic-capture-dedup-and-integration-contract.md)
(dedup), [REQ-007](./REQ-007_tile-viewer-source-profile.md) (the export that produces the file),
and the "Local-file document capture" backlog item.

> **The ask in one line:** `capsule ingest <file> --url <permalink>` — every evidence file the
> researcher already holds gets a capture id, a hash, and a citation, without a browser.

## Why

The research engine's own audit (2026-08-21) found the gap: an agent that has just driven
Archion's official export, or downloaded a PDF from a records office, or been handed a JPEG by a
county archive, **has no supported way to put that file into the collection**. Today there are
exactly two routes, and neither fits:

1. **The popup's "Capture this PDF"** — requires the file to be open in a Chrome tab as a
   `file://` URL, and requires the one-time *Allow access to file URLs* toggle. It is a
   human-in-the-browser flow, not something a CLI or an agent step can call.
2. **`POST /capture {document: {...}}`** — the primitive exists (`DocumentIn`,
   `_decode_document` in `capsule/backend/app.py`) and is what route 1 uses under the hood, but
   it is undocumented as a public surface, takes base64 in a JSON body, and its behaviour for a
   non-Ancestry URL is unverified.

The consequence is that files sit in `~/Downloads` unhashed and uncited while the tree's
citation for them points at a viewer URL that resolves to a different page every time. That is
precisely the "not just a link to a web source" failure the whole project exists to prevent.

## 1. The command

```
capsule ingest <file> --url <permalink> [--gating public|gated] [--note "..."]
                      [--record-type <type>] [--source-quality original|derivative]
                      [--recapture]
```

- **`<file>`** — a path on disk. Image or PDF, subject to the same allow-list and 25 MiB cap
  `_decode_document` already applies (`MAX_MEDIA_ITEM_BYTES`).
- **`--url`** — **required.** The URL this file came from, and the URL the citation will name.
  For a tile viewer that is the REQ-007 §1 permalink; for a records-office download it is the
  page the download came from. There is no such thing as an ingested file with no provenance:
  a hash of an anonymous file is not evidence.
- **`--gating`** — defaults to inferring nothing and recording `public`; a paywalled export must
  be passed `gated`, which carries the copyright flag and suppresses Wayback submission exactly
  as it does for a browser capture.
- **`--note`** — the researcher's why, stored in the hash-excluded note layer (capture-folder-v2
  §2), never as evidence.

**Output:** the same JSON shape `capsule query` prints — capture id, folder path, `verify_ok`,
`dedup` — so an agent can chain it.

## 2. What it writes

A **document-primary capture**, `capture-folder/v2` §1, unchanged: `document.<ext>` as the
primary artifact, an empty `extraction.json` (zero assertions — reading a scan is M4 vision, and
REQ-006 is the standing ask for it), `citation.json`, `manifest.json`, hashes for all of it.

Two fields matter for this path:

- **`media[].provenance`** — `"site:official-download"` when the file came from the source's own
  export control (REQ-007 §2), `"user:file"` when the researcher supplied it by hand. The
  distinction is evidentiary: one is the provider's own artifact, the other is a file of
  unstated origin that happens to be in the researcher's possession.
- **`archive.kind: "document"`** and `archive.media_type` — already specified by v2.

## 3. Dedup keyed on `(url, sha256)`

REQ-004's identity function (`capsule/pipeline/dedup.py:record_identity`) resolves Ancestry ids
first, then falls back to the normalized URL. For ingest that is not enough on its own: **two
different pages of the same register can share one viewer URL** (the REQ-007 §1 problem), so a
URL-only key would false-block the second page — and a false block loses evidence.

**Ask:** for an ingest, the identity is `(normalized url, sha256 of the file bytes)`. Same URL +
same bytes ⇒ `dedup: "blocked"` with `duplicate_of`. Same URL + different bytes ⇒ a new capture,
because it is a different page of the same volume. Once REQ-007 §1 lands and the URL is a real
per-page permalink, the two keys agree and this reduces to the ordinary case.

**`--recapture`** overrides, as `capture --recapture` does.

## 4. Acceptance

1. `capsule ingest scan.pdf --url <permalink> --gating gated` writes a v2 document capture whose
   `document.pdf` hash equals the SHA-256 of the input file, byte for byte.
2. Re-running the identical command ⇒ `dedup: "blocked"`, `duplicate_of` names the first capture,
   nothing is written.
3. Ingesting a *different* page's export under the same viewer URL ⇒ a new capture, not a block.
4. The capture appears in `captures/index.html` and answers `capsule query --url <permalink>`.
5. A gated ingest is copyright-flagged and is never submitted to Wayback.

## 5. Non-goals

- **No extraction.** Zero assertions, as capture-folder-v2 specifies. Reading the document is
  REQ-006 / F4, filed separately and still awaiting scheduling.
- **No fetching.** `ingest` reads a local path. It never downloads the URL it is given — that
  would reintroduce exactly the background traffic the compliance posture forbids.
- **No directory sweep.** One file per invocation. A bulk-import mode is a different decision
  with a different risk profile (a folder of files with one `--url` between them is a citation
  hazard), and should not be smuggled in here.

## 6. Implementation status (2026-09-24)

Shipped as specified in §1, with one added option: **`--provenance user:file |
site:official-download`** (default `user:file`). §2 names the two values but §1 gave no way
to choose; the default is the weaker claim, because "the provider's own artifact" is the
assertion that needs the researcher to make it.

```
capsule ingest scan.pdf --url <permalink> --gating gated --provenance site:official-download
```

**Deviation 1 — provenance lives on `archive`, not `media[]`.** §2 says
`media[].provenance`, but in a document capture the ingested file is the *primary artifact*
(`archive`, `document.<ext>`), not a media item; putting it in `media[]` would mean
archiving the file twice. It is recorded as **`archive.provenance`**, an additive key
written only when set ([capture-folder-v2 §1](./capture-folder-v2.md)), and mirrored into a
new nullable `archive_record.provenance` column (additive migration; every existing row is
NULL, which is correct — each came through the browser). All 373 capture folders on disk
were checked: none carry the key, so no existing manifest changes by a byte.

**Deviation 2 — an ingest never blocks a page capture.** §3 defines ingest-vs-ingest dedup
but is silent on the other direction. As built, ingested captures (marked by
`archive.provenance`) are a separate identity space: the page-capture dedup
(`find_capture`, used by the backend and `capsule capture`) skips them, because the ingest
holds a file that came *from* the page, not the page itself. Blocking the page on the
strength of the file would be a false block, and a false block loses evidence
(REQ-004). `capsule query --url` still finds them (§4.4) — it prefers a page capture, and
reports `"ingested": true` when what it found is an ingest, so `complete` keeps meaning "a
page capture would be refused".

**As specified:**
- **§3 dedup** is `find_ingested(url, sha256)` in `capsule/pipeline/dedup.py` — normalized
  URL + `archive.sha256`, complete-only blocking, `--recapture` overrides. It matches any
  capture, including a browser document capture of the same bytes at the same URL.
- **The bytes decide the type** (BUG-008): the magic-byte table moved from the backend to
  `capsule/pipeline/sniff.py` and is shared. A JPEG named `.pdf` is archived as
  `document.jpg`; bytes that are neither PDF nor image are refused (exit 2), not stored as
  `bin`. The 25 MiB cap is the same shared constant.
- **`--url` must be `http(s)`.** A `file://` URL would only restate the local path, and a
  hash of an anonymous file is not evidence (§1).
- **`--source-quality`** is recorded with the rationale *"stated by the researcher at
  ingest; the document itself was not read"*; unset, it stays `derivative` with no
  rationale, exactly as a browser document capture.
- **Exit codes:** 0 written, 3 blocked (JSON still printed, `dedup: "blocked"`), 2 refused
  input, 1 write failure.

**Not done (unchanged non-goals, §5):** no extraction, no fetch, no directory sweep.
**Follow-up worth considering:** a `--title` option. The title is derived from the URL's
last path segment, which for a tile viewer is a bare register number (`239254`).
