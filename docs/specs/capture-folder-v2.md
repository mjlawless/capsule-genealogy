# Spec: `capture-folder/v2`

Status: **active**. This extends [`capture-folder/v1`](./capture-folder-v1.md) with
one new capture *shape* — a **document-primary capture** — plus a universal,
backward-compatible **notes** layer. Everything in v1 (folder layout, capture-id
derivation, hash mapping, `verify`, the §4a–§4d addenda) is inherited unchanged
and is not restated here. Read v1 first.

A capture is written as **v2 only when it is a document capture**. An HTML page
capture is still written as `capture-folder/v1` and is **byte-identical** to one
written before this spec existed. `spec_version` names the shape, and both
coexist on disk; a reader must handle either.

## 1. Document-primary captures (`archive.kind = "document"`)

Some sources are not a web *page* but a document the page *is* — a PDF newsletter,
a scanned certificate served at its own URL. There is no HTML to snapshot, and at
M0 there is **no extraction** (reading a document is vision/HTR, deferred to M4).
It is a first-class **zero-assertion** capture, exactly like an unindexed parish
register scan: archived, hashed, and cited now; facts added later.

### Folder layout

```
captures/<capture_id>/
├── manifest.json
├── document.<ext>        ← the primary artifact (replaces snapshot.html)
├── extraction.json       ← present but EMPTY (no personas/snippets/assertions)
├── citation.json
├── notes.json            ← optional; §2
└── media/…               ← rare for a document; allowed
```

- **`document.<ext>`** is the primary archived artifact. `<ext>` is lower-alnum
  (`document.pdf`). `archive.sha256` is the digest of exactly these bytes, and it
  is duplicated into `hashes.files["document.<ext>"]` — the same MUST-equal
  invariant v1 §4 states for `snapshot.html`, now keyed to whatever
  `archive.local_snapshot` names.
- **`extraction.json` is still written**, empty. This keeps `source.content_hash`
  (the digest of `extraction.json`) and every downstream reader (SQLite index,
  collection index) working with no special case. An empty extraction has a fixed,
  known digest (`8f144456e95bd74b…`), so **`content_hash` is shared by every such capture and
  is not a document identity** (BUG-023); `archive.sha256` names the document.
- **No `agent_log/`** is required — a document capture makes no model call.

### `manifest.json` — the `archive` block

The `archive` block gains two keys, written **only** for a document capture (an
HTML capture omits both, preserving byte-identity):

```jsonc
"archive": {
  "local_snapshot": "document.pdf",   // the primary artifact (historical field name)
  "sha256": "<digest of document.pdf>",
  "captured_at": "2026-07-11T12:00:00Z",
  "wayback_url": null,
  "kind": "document",                 // NEW: "snapshot" | "document"
  "media_type": "application/pdf"     // NEW: MIME of the primary artifact, or null
}
```

**`archive.provenance`** (added 2026-09-24, REQ-008, additive) is written **only** for a
file ingested from disk by `capsule ingest`: `"site:official-download"` (the source's own
export control) or `"user:file"` (a file the researcher supplied by hand). It is absent on
every browser capture, so their manifests are unchanged. Readers MUST tolerate its absence.
An ingested capture never blocks a browser capture of the same URL (REQ-008 §6).

`kind` mirrors `ArchiveRecord.kind` (default `"snapshot"`, omitted from the
manifest in that case). `local_snapshot` keeps its historical name across both
kinds so the manifest and the pydantic model stay one-to-one.

### `verify`

Unchanged in structure. The §4 MUST-equal invariant is keyed to
`archive.local_snapshot` (whichever artifact it names) rather than hard-coded to
`snapshot.html`. The §4d redaction-marker check applies only when a
`snapshot.html` is present, so it is naturally inert for a document capture.

## 2. `notes.json` — the researcher's annotation layer (backward-compatible)

A capture may carry the collector's own free-text notes: *why* it was collected,
what to revisit, how it connects to a person. This is **analysis, not evidence** —
authored by the user, not the source — so it is deliberately **not** an assertion
(no snippet, no evidence type) and it is **excluded from the hash manifest**.

```jsonc
// notes.json — a JSON array, oldest first
[
  {
    "id": "note-1",
    "text": "Collected: Spring 2025 issue, mentions the Doyle family.",
    "author": "owner",
    "created_at": "2026-07-11T12:00:00Z"
  }
]
```

- **Universal and additive.** Notes work on **any** capture — v1 HTML or v2
  document. `notes.json` is written **only when notes exist**, so a note-free
  capture is byte-identical to one without this file. Adding notes does not change
  `spec_version`.
- **Excluded from `verify`** — from `hashes.files` and from the extra-file scan
  (v1 §5.1), exactly like `agent_log/`. This is what lets notes accrue *after* a
  capture is sealed without ever invalidating its cryptographic integrity, and why
  a note can be attached long after capture.
- **Append-only, like all Capsule storage.** Revising a note is a new note; the
  latest is shown, history is retained. Mirrored in the SQLite `capture_note`
  table (append-only triggers) for query; `notes.json` is the portable copy that
  travels with a capsule export.
- **`source_id` is implied by the folder** and omitted from each note object.

## 3. Relationship to v1 and the M3 capsule

- A v1 HTML capture is unchanged and remains valid; v2 is a strict superset that
  adds the `document` kind and the notes file.
- The M3 capsule (`capsule/v1`, v1 §6) inherits both additions: a capsule may
  wrap a document capture, and `notes.json` follows the same excluded-file rule as
  `agent_log/`.

## 4. `transcript/` — machine transcripts of scanned media (additive, REQ-006)

A capture whose media is a scanned original (an NYC certificate, a register page)
may carry one or more **machine transcripts**: what a vision transcriber read from
the image, field by field. A transcript is a **derivative reading aid, not the
record** — the image stays the evidence, and nothing here is an assertion or can
raise a grade (REQ-006 §5.1–5.2, owner ruling R1). It follows the notes rule:
**excluded from the hash manifest**, addable to a sealed capture at any time.

```
<capture>/transcript/<transcription_id>/
├── transcript.json          ← the run + its fields (below)
└── crops/p<page>-<key>.png  ← the exact pixels each field was read from
```

```jsonc
// transcript.json
{
  "kind": "machine-transcript",
  "derivative": true,
  "caution": "A machine reading of the scanned image — a reading aid, not the record. …",
  "transcription": {
    "id": "…", "source_id": "…",
    "media_filename": "1-d-q-1903-0000148.pdf", "media_sha256": "…",
    "model": "claude-opus-5", "status": "complete",   // | "partial" | "failed"
    "problems": [], "layout_notes": null, "created_at": "2026-09-24T12:00:00Z"
  },
  "fields": [{
    "id": "…", "page": 0, "field_key": "mothers_name", "form_label": "Mother's Name.",
    "status": "value",            // | "unknown" | "blank" | "illegible"
    "verbatim_value": "Bridget. Kilcoyne",   // exactly as written; null only for blank
    "annotation": "trailing period as written",
    "confidence": 0.8, "needs_review": true, "note": null, "attempts": 2,
    "crop_filename": "crops/p1-mothers_name.png", "crop_sha256": "…",
    "crop_bbox": [974, 1431, 1135, 2213],   // native pixels of the page image
    "crop_rotation": 270,                    // degrees clockwise applied after cropping
    "agent_call_id": "…"                     // the logged call in agent_log/
  }]
}
```

- **Verbatim and annotation are separate layers.** `verbatim_value` is exactly as
  written, uncertainty marked inline (`[?]`, `[?reading]`, `[a/b?]`, `[illegible]`);
  normalisation lives only in `annotation`. An informant's non-answer (`"Unknown"`)
  is `status: "unknown"` with the words kept, never a null.
- **Pinned to bytes.** `media_sha256` names the exact media read; each `crop_sha256`
  the exact pixels sent to the model, re-derivable from the media via
  `crop_bbox` + `crop_rotation`.
- **Append-only.** Each transcriber run is a new `<transcription_id>/` folder; a
  folder is never overwritten. Mirrored in the SQLite `transcription` /
  `transcript_field` tables (append-only triggers).
- **Excluded from `verify`**, like `notes.json` and `agent_log/`; the model calls
  themselves are logged in `agent_log/` as usual. Adding a transcript does not change
  `spec_version`.

## 5. `manifest.json` → `tool_version` and `extension_version` (BUG-021, added 2026-10-21, backward-compatible)

```jsonc
{
  "tool_version": "0.1.0+g1a2b3c4",   // product version + "+g<commit>[.dirty]" from a git checkout
  "extension_version": "0.1.0"        // OPTIONAL — the Chrome extension build that sent the capture
}
```

- **`tool_version` now means something.** It is the product version from `pyproject.toml`
  (one version for the backend and the extension, released together), plus `+g<short sha>`
  when the backend runs from a git checkout, and `.dirty` when that checkout had uncommitted
  changes. Without git (a "Download ZIP" install) it is the plain product version.
  **`"0.0.0"` in an existing folder means "written before BUG-021"** (every capture before
  2026-10-21 says so). Past captures are not rewritten.
- **`extension_version` is optional and additive**, like `media_warnings`. The extension
  sends it as the `X-Capsule-Extension-Version` request header (a header, so an older backend
  ignores it instead of rejecting the capture). Only a Chrome version string (1–4
  dot-separated integers) is recorded; anything else is dropped. Omitted for CLI captures,
  `capsule ingest`, and captures from an extension older than 0.1.0, so their manifests
  serialize exactly as before. Adding it does not change `spec_version`.

## 6. `lifecycle.json` — superseded / reinstated (REQ-011, added 2026-10-21, backward-compatible)

```jsonc
[
  { "id": "…", "source_id": "…", "event": "superseded",       // | "reinstated"
    "superseded_by": "<source id>", "superseded_by_capture_id": "20261003-newspaperscom-4aa5ff8c",
    "reason": "…", "actor": "owner", "created_at": "2026-10-21T…" }
]
```

- **A hash-EXCLUDED sidecar**, like `notes.json`: a capture is superseded long after it was
  sealed, and saying so must not invalidate it. `verify` ignores it.
- **A projection** of the append-only `capture_lifecycle_event` table, regenerated whole on
  each event, oldest first. The latest event is the state.
- Present only on a capture that has ever been superseded. Its absence means "current".
- **The database is the authority, but never over history it lacks.** A sidecar holding
  event ids the database does not have is kept, and changes to that capture are refused until
  a person runs `capsule supersede <id> --resync` ("this database is current"). This covers
  both a restored older database and the rare crash between the file write and the COMMIT,
  which Capsule cannot tell apart.
