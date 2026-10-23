# Spec: `captures-index/v2`

Status: **active**. Supersedes `captures-index/v1` (`docs/specs/captures-index-v1.md`,
now historical). This is the versioned spec for the human-readable index of a
capture *collection* — the `index.html` file at the root of a `captures/`
directory that lets a person browse every captured source without opening the
SQLite DB or reading raw JSON.

It complements, and does not change, `capture-folder/v1` (the on-disk shape of a
*single* capture). The index is a **generated projection** of the capture
folders — never hand-edited, regenerated on every capture and via
`capsule index` — so it can never drift from what is actually on disk.

## 0. What changed from v1, and why

v1 was `Captures Index.md`: one Markdown file, one `<details>` accordion per
capture. Markdown was the wrong ceiling. The index is becoming the surface where
a researcher *evaluates* a collection and, from M2/M3, *pushes it into a tree* —
that needs filtering, sorting, and eventually actions, none of which Markdown
can express. It also has to be readable by people who do not use Obsidian.

v2 is therefore a **format change** (Markdown → HTML), a **filename change**
(`Captures Index.md` → `index.html`), and a **layout change** (accordion list →
sortable table + detail pane). The Markdown index is removed, not kept alongside:
two renderers over one `CaptureRecord` would make every new field a two-place
edit, and the generated file's git diff is noise regardless — it is rewritten
wholesale on every capture.

`write_index` deletes a stale `Captures Index.md` if it finds one. A wrong
evidence index is worse than no evidence index.

## 1. Location and identity

```
captures/
├── index.html               ← this file (generated)
├── 20260708-nyc-3f2a1b9c/   ← a capture folder (capture-folder/v1)
│   ├── manifest.json
│   ├── citation.json
│   ├── extraction.json
│   └── ...
└── 20260707-findagrave-1a2b3c4d/
    └── ...
```

- The filename is exactly `index.html`, at the collection root, a sibling of the
  capture folders. No space in the name, so the `file://` URL is clean; and it is
  the default document if the backend ever serves `captures/` statically (§6).
- It is a plain file, so `verify_capture` (which operates *inside* a single
  capture folder) ignores it, and `read_all` (which enumerates capture folders)
  skips it — a top-level file is never mistaken for a capture.
- It is **regenerated, not merged.** Every write replaces the whole file from
  the current folder contents. No state lives only in the index.

## 2. Data source: the folders, not the DB

Unchanged from v1. The index is rendered by walking `captures/` and reading each
capture folder's `manifest.json`, `citation.json`, and `extraction.json`. It does
**not** read the SQLite evidence DB.

- The capture folder is the evidence of record; the DB is an index over it. An
  index that reads the folders is faithful to what a person actually sees on
  disk.
- It works with no DB present (a fresh clone of a `captures/` directory), which
  matches the append-only, self-describing intent of `capture-folder/v1`.
- Reading is **tolerant**: a folder without a `manifest.json` is not a capture
  and is skipped; a capture whose `citation.json`/`extraction.json` is missing or
  malformed still appears, rendered from what is readable. One bad capture never
  blanks the whole index.
- **The local link targets the real folder name on disk**, not the `capture_id`
  the manifest claims. These normally agree; when they do not, the directory
  wins, because §1's promise is that the link resolves.

## 3. Three properties the file must hold

These are requirements, not implementation notes. An index that violates them is
not `captures-index/v2`.

### 3.1 Self-contained

Inline CSS and inline JavaScript. **Zero** external requests: no CDN, no web
fonts, no `<img src>` outside the collection, no build step. The file travels
inside the capsule export zip and must open correctly, offline, from a `file://`
URL, on a machine that has never run Capsule — including in twenty years.

### 3.2 Progressive enhancement

Every capture is rendered into real HTML at generation time. JavaScript only
*adds* filtering, sorting, and the detail pane. **With JavaScript disabled the
file is still a complete, readable index**: the table lists every capture and
every capture's details are visible inline.

This is not a nicety. For a project whose premise is durable evidence, the index
must survive a dead JavaScript engine. It also means there is exactly one copy of
the data in the file — the DOM — which the enhancement script reads. There is no
embedded JSON payload (§5).

### 3.3 Escaping and link safety

The format change raises the escaping bar, because nearly every value rendered
originates in attacker-controlled page content (`title`, `site_name`, persona
labels, and assertion verbatim values are LLM extractions of a captured page;
`CaptureRequest.url` is unvalidated at the backend today).

- **One escaper for every value**, whether it lands in a text node or a quoted
  attribute (including the `data-*` keys the filter reads): `&`, `<`, `>`, `"`,
  `'`. A text node does not strictly need quote-escaping, but a single
  always-quote-safe escaper removes the "wrong escaper for this context" bug
  class, and `&quot;` renders as `"` regardless, so nothing is lost on screen.
- `href` values: **scheme allowlist**, not an escape — escaping
  `javascript:alert(1)` yields a working exploit. Only `http://` and `https://`
  may become a link; anything else (notably `javascript:`, `data:`, `vbscript:`,
  `file:`) renders as inert escaped text, so the researcher still sees what was
  captured. ASCII control characters and whitespace are stripped before the
  scheme is tested, because browsers ignore them when parsing a scheme
  (`java\nscript:` is `javascript:`).
- The local folder link is built from the on-disk folder name (§2), never from
  manifest-supplied text.

## 4. Layout, and the status spine

A sortable table of captures, plus a detail pane for the selected row.

**Columns:** status · record type · primary persona · site · captured moment ·
flags and file types. Above the table: a free-text filter and facet selects
(record type, site, status). Clicking a row shows that capture's detail —
bibliographic reference, source link, local folder link, captured/access/archived
dates, any referenced source document (§4a), the learned-detail assertion table,
and the metadata flags.

The captured column shows **date and time** (`2026-07-08 14:32 UTC`). Access time
is part of the citation, and two captures of one URL on the same day can differ.
The row's `data-date` sort key carries the full ISO timestamp, and `read_all`
orders by `archive.captured_at` — not by `capture_id`, whose `YYYYMMDD` prefix
ties on same-day captures and silently falls back to alphabetical site order.

### 4a. Referenced source documents

When a capture's `extraction.json` carries `external_refs[]`
(`capture-folder/v1` §4b), the row shows an `↗ source` chip and the detail lists
each reference: its verbatim link text, its canonical `authority identifier`, and
a link to the URL as it appeared on the page.

A reference whose `locator` is null is rendered **"(document, not page)"**. This
is not decoration. A RootsIreland entry links the NLI *register*, never the leaf;
a reader who assumed the pointer resolved to the exact page would be reading a
claim the source never made. The index states the scope of the pointer it found.

The index draws no line between two captures. Whether a referenced document is one
we hold — and whether it is *this leaf* of it — is an evidence-typed judgment that
belongs to `SourceRelation` at M2, not to a renderer. See
`docs/design/rootsireland-nli-linkage.md`.

The **status** column is the spine of the document, and the reason this is a
dashboard rather than a listing. A capture moves along a lifecycle:

```
captured → extracted → reviewed → matched → attached
```

| Stage | Means | Sourced from | Live at |
|---|---|---|---|
| `captured` | folder written, hashed | always true | M0 |
| `extracted` | assertions exist | `extraction.json` | M0 |
| `reviewed` | a human accepted/corrected the extraction | correction log | M1 |
| `matched` | the matcher proposed a tree person | matcher verdict | M2 |
| `attached` | a persona is linked to a tree person | persona↔tree link | M3 |

Only the first two are derivable at M0, so `status` is `extracted` when a capture
has assertions and `captured` when it does not. The served page also shows
`reviewed`, from the correction log (§4c). The later stages render as `—`
until the data behind them exists. The question this column will be asked, once
phase 2 lands, is *"what have I captured but not yet pushed into the tree?"* —
so it is drawn now, at the front, even mostly empty.

The same reasoning governs two other reserved fields, both `—` today:

| Field | Becomes real at |
|---|---|
| OCR'd / transcribed? | M4 (vision/HTR over a captured scan) |
| Eval fixture / extraction agreement | when `evals/` has labels for a capture |

Adding a real column later is a backward-compatible renderer change, not a spec
break. A breaking change to the *entry shape* would require `captures-index/v3`.

"Type of file" is derived from the capture's `manifest.json` `hashes.files` keys:
every capture has `html` (the snapshot); a capture with `media/*.pdf` also shows
`pdf`, `media/*.jpg` shows `image`, etc.

### 4b. Superseded captures (REQ-011, added 2026-10-21)

A capture whose `lifecycle.json` ends in `superseded` stays listed, because nothing is ever
hidden or deleted, but its row carries a **superseded** chip linking to the replacement, and its
detail block gains a "Superseded by" line. The link target is accepted only if it has the
capture-id shape, since the sidecar is hash-excluded.

### 4c. Corrections on the served page (M1, added 2026-10-23)

A human correction (edit, add, retract) lives only in the database, never in the sealed
folder. So the two delivery modes (§6) now differ for a corrected capture:

- **Served page** (`GET /`): for each capture with at least one `correction_event`, the
  backend lays the current facts over the folder's reading. A corrected fact shows its
  current value and one tag (`edited`, `added`, `added, edited` or `retracted`), however
  many corrections it has had; its history, oldest first, each line naming both values
  ("“Anna Grimm” → “Annie Grimm” · owner · date — reason"), is shown in its ✎ form (and in
  the tag's tooltip, the only place for a retracted fact, which has no form). A retracted
  fact stays listed, struck through; an added fact is listed after the model's. Status becomes
  `reviewed` and the "Reviewed" line gives the count and the latest date. Each live fact
  carries `data-assertion-id`, the CURRENT assertion id, the target of the next correction.
  A capture with no corrections renders exactly as the folders say.
- **Static file** (`captures/index.html`): unchanged. It shows the model's sealed reading,
  carries no assertion ids, and never shows `reviewed`.

`#cap-<folder>` in the address opens that capture on either page, and the served page keeps
the address in step with the open capture, so a reload or a link reopens it.

**Correcting (served page only).** Each live fact has ✎ (edit its value) and ✕ (retract it);
a capture that names at least one person has "+ Add a fact" (person, field, value, evidence
type, and the supporting words, which are required). The edit and retract forms quote the
fact's supporting words (`data-snippet`). Each form posts to `POST /correction` with the
page's token and reloads the page at the capture; a refused correction is explained in the
form, which cannot be dismissed while a save is in flight. A reason, when given, is part of
the fact's history line. None of these controls, ids or histories appear in the static
file. Each fact's supporting words (`data-snippet`) appear in both, since 2026-10-23, so
"Copy for AI" (§4d) carries them from either page.

### 4d. Record categories, filters, lifecycle lines, Help (added 2026-10-23)

Renderer changes only; the entry shape is unchanged, so this stays `captures-index/v2`.

- **Record column and filter use a common category** (`capsule/collection/record_types.py`:
  Birth & baptism, Marriage, Death & burial, Census & household, Church registers, Probate &
  wills, Land & property, Military, Immigration & naturalization, Directories & books,
  Public records, Reference lists, Unclassified). The reader's own record type is kept and
  shown in the detail ("Record type") and as the category's tooltip.
- **Record and site filters take several choices** (a checkbox dropdown, each choice with
  its count); status stays a single choice. Several choices within a filter are OR'ed;
  filters are AND'ed with each other and with the text box.
- **Lifecycle lines:** only stages with data are shown: *Reviewed* (correction log, served
  page) and *Transcribed* (the newest `transcript/*/transcript.json`; on the served page a
  link to `/review/<folder>`). *Matched* and *Attached* return when the Gramps work gives
  them data; *OCR'd* became *Transcribed*.
- **Incomplete captures:** a capture whose `result.json` says `verify_ok: false` carries an
  `incomplete` chip and an "Incomplete" line with its `verify_shortfall`. A capture with no
  `result.json` (older) shows neither.
- **Wayback:** the "Archived" line is gone (submission was dropped, plan.md D3); a capture
  that carries a `wayback_url` still shows it as "Wayback".
- **Citation:** each capture's detail shows its full reference note and source-list entry
  (from `citation.json`) with **Copy** buttons, and **Copy for AI**, which copies the capture
  as plain text (metadata, both citations, a gated note, and each current fact as person |
  field | value | evidence | supporting words) to paste into an assistant. Copying only
  reads the page, so these appear in the static file too: the one exception to "no actions
  in the static file", since nothing is written.
- **Help:** the header links `help.html`, a short static guide written beside `index.html`
  (and served at `/help.html`; `/index.html` redirects to `/`).
- **Served page only:** the header names the archive folder with an "Open archive folder"
  button, and each capture's "Local" line has "Open folder"; both call `POST /reveal`
  (token-gated), which opens the folder in the computer's file manager.

## 5. Non-goals

- **Not machine-parseable, and deliberately carries no embedded JSON payload.**
  The per-capture JSON and the SQLite DB remain the machine contracts;
  `CaptureRecord` is the extension point. A third serialization would be a third
  source of truth with no consumer today. When the backend serves a live
  dashboard (§6) it will project `CaptureRecord` to JSON then, against a real
  caller.
- No per-assertion source-snippet links yet. `Snippet` + `SnippetAnchor` already
  carry the provenance; rendering anchored links is deferred until it earns its
  keep.
- No pagination. Client-side filter covers a collection of realistic size;
  revisit if one grows past it.
- **No actions.** The static file reads; it cannot write. See §6.

## 6. Growth path: one renderer, two delivery modes

`render_index(records, generated_at=...) -> str` is pure. That keeps two delivery
modes open without a second renderer:

- **Static file** (`captures/index.html`) — the portable browse and archival
  surface, forever. Ships inside the capsule export zip. Read-only by nature.
- **Served page** — when phase 2 needs actions (accept a correction, attach a
  persona to a tree), the localhost FastAPI backend serves the same rendered view
  at `http://127.0.0.1:<port>/`, where the shared token is available and buttons
  can POST to the existing API.

Static for reading, served for doing, one template. Actions must never appear in
the static file, where they would be dead controls in an archive.
