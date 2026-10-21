# Spec: `captures-index/v1`

> **Status: SUPERSEDED by [`captures-index/v2`](./captures-index-v2.md)**
> (2026-07-09). v1 rendered `Captures Index.md`; v2 renders `index.html` — a
> sortable table with a detail pane, a status spine, and a hard self-contained /
> no-JS-required / scheme-allowlisted-links contract. `write_index` deletes a
> stale `Captures Index.md` on sight. Kept for history; do not implement against
> this file. See v2 §0 for why the format changed.

This was the versioned spec for the human-readable
index of a capture *collection* — the `Captures Index.md` file that sits at the
root of a `captures/` directory and lets a person browse every captured source
without opening the SQLite DB or reading raw JSON.

It complements, and does not change, `capture-folder/v1` (the on-disk shape of a
*single* capture). The index is a **generated projection** of the capture
folders — never hand-edited, regenerated on every capture and via
`capsule index` — so it can never drift from what is actually on disk.

## 1. Location and identity

```
captures/
├── Captures Index.md        ← this file (generated)
├── 20260708-nyc-3f2a1b9c/   ← a capture folder (capture-folder/v1)
│   ├── manifest.json
│   ├── citation.json
│   ├── extraction.json
│   └── ...
└── 20260707-findagrave-1a2b3c4d/
    └── ...
```

- The filename is exactly `Captures Index.md` (with the space), at the
  collection root, a sibling of the capture folders.
- It is a plain file, so `verify_capture` (which operates *inside* a single
  capture folder) ignores it, and `read_all` (which enumerates capture folders)
  skips it — a top-level file is never mistaken for a capture.
- It is **regenerated, not merged.** Every write replaces the whole file from
  the current folder contents. No state lives only in the index.

## 2. Data source: the folders, not the DB

The index is rendered by walking `captures/` and reading each capture folder's
three JSON files — `manifest.json`, `citation.json`, `extraction.json`. It does
**not** read the SQLite evidence DB. Rationale:

- The capture folder is the evidence of record; the DB is an index over it. An
  index that reads the folders is faithful to what a person actually sees on
  disk, and its "link to local" always resolves to a real directory.
- It works with no DB present (a fresh clone of a `captures/` directory), which
  matches the append-only, self-describing intent of `capture-folder/v1`.
- Reading is **tolerant**: a folder without a `manifest.json` is not a capture
  and is skipped; a capture whose `citation.json`/`extraction.json` is missing or
  malformed still appears, rendered from what is readable. One bad capture never
  blanks the whole index.

## 3. What each entry shows (thin by default)

The index "should appear thin, either linking to the details, or hiding them in
an accordion." So each capture is one HTML `<details>` block (GitHub renders
these as collapsible accordions inside Markdown):

- **Summary line** (always visible, one line): record type · primary persona ·
  site slug · gating (🔒 for gated) · file types present.
- **Body** (collapsed until expanded):
  - a **basic bibliographic reference** — the citation short note;
  - **link to the source** — the original URL;
  - **link to the local** capture folder;
  - captured-at / access-date / archived-location;
  - the **summarized learned details** — a table of assertions
    (persona · field · verbatim value · evidence type);
  - **metadata placeholders** (see §4).

"Type of file it is" is derived from the capture's `manifest.json`
`hashes.files` keys: every capture has `html` (the snapshot); a capture with
`media/*.pdf` also shows `pdf`, `media/*.jpg` shows `image`, etc.

## 4. Growth path (metadata that lights up later)

The requirement is explicitly expandable: "As we make the capture more robust,
this could become more important." The index reserves metadata fields that are
rendered as `—` (unknown / not yet) at M0 and become real as later milestones
land them:

| Field | Rendered at M0 | Becomes real at |
|---|---|---|
| In Ancestry tree? | `—` | M3 (guided attach — a persona↔tree link exists) |
| OCR'd / transcribed? | `—` | M4 (vision/HTR over a captured scan) |
| Media captured | file-type chips from `hashes.files` | Increment B (embedded media/PDF capture) |

Adding a real column later is a backward-compatible change to the renderer, not
a spec break. A breaking change to the *entry shape* (e.g. dropping a field a
downstream tool parses) would require a `captures-index/v2`.

## 5. Non-goals (M0)

- Not machine-parseable. This is a human browse surface; the SQLite DB and the
  per-capture JSON remain the machine contracts.
- No per-assertion source-snippet links yet (the data exists — `Snippet` +
  `SnippetAnchor` — but rendering anchored provenance is deferred until it earns
  its keep).
- No pagination/search. Revisit if a real collection grows large enough to need
  it.
