# `capsule-db-v1` — the integration contract for external consumers of `capsule.db`

**Status:** v1, 2026-07-17 (REQ-004 §2) · Style: same as `capture-folder-v1` — what is
contract vs what may change without notice.

The Genealogy workspace (and any future consumer) reads `capsule.db` directly for
pre-capture checks and harvest resume logic. This document states what it may rely on.

## 0. Ground rules

- **The capture FOLDER is the archive of record; the DB is an index over it.** When they
  disagree, the folder wins (`manifest.json` is the authority on a capture's identity and
  contents; `verify_capture` is the authority on its integrity).
- **The store is append-only.** Rows are never updated or deleted by Capsule; new facts
  arrive as new rows (ledger events, superseding assertions, correction events).
- **Read-only access.** External consumers must never write to `capsule.db`. Writes go
  through Capsule's backend/CLI, which enforce the invariants above.
- **Schema evolution is additive.** New tables and new nullable columns may appear at any
  time (`CREATE TABLE IF NOT EXISTS` + post-hoc column migration); contract columns below
  will not be renamed, retyped, or removed within v1. Anything NOT listed below may change
  without notice.

## 1. Contract surface

### `source` — one row per capture

| column | type | contract |
|---|---|---|
| `id` | TEXT PK | stable capture-scoped id; FK target for all child rows |
| `url` | TEXT | the captured page's URL, verbatim (query string included) |
| `title`, `site_name`, `record_type` | TEXT? | as extracted/derived at capture time |
| `gating_status` | TEXT | `public` \| `gated` |
| `copyright_flag` | INTEGER | 0/1 |
| `source_quality` | TEXT | `original` \| `derivative` |
| `content_hash` | TEXT | sha256 of `extraction.json` (MUST equal the folder's). **Not unique and not a document identity** (BUG-023): every capture with an empty extraction shares `8f144456…`. Never dedup or join on it |
| `spec_version` | TEXT | `capture-folder/v1` \| `capture-folder/v2` |

The capture folder name (`<YYYYMMDD>-<site-slug>-<short-uuid>`) is derivable but NOT a
column; resolve identity through `manifest.json`, or via the `ancestry_ledger_event.
capture_id` for Ancestry records.

### `ancestry_ledger_event` — the Ancestry work ledger (append-only event stream)

| column | type | contract |
|---|---|---|
| `apid` | TEXT | **normalized `<collection_id>:<record_id>`** — the match key |
| `collection_id`, `record_id` | TEXT | the pair, split |
| `event` | TEXT | `seeded` \| `issued` \| `started` \| `captured` \| `failed` \| `remine-requested` \| `dedup-blocked` |
| `capture_id` | TEXT? | the capture folder name, when a capture landed |
| `image_status` | TEXT? | `full` \| `degraded-canvas` \| `index-only-no-image` \| `failed` \| `none` |
| `assertion_count`, `persona_count` | INTEGER? | per-capture completeness facts |
| `verify_ok` | INTEGER? | 0/1 — folder verify at capture time |
| `follow_up` | TEXT? | the true source still to chase (index-only records) |
| `error` | TEXT? | on `failed`: what went wrong. On `captured` (REQ-010, 2026-10-21): why the capture holds **no record image** (`image_status` `failed` or `none`), e.g. *"NO RECORD IMAGE: no download was armed for this capture. This record has an image: …"*. The capture itself succeeded; NULL otherwise |
| `created_at` | TEXT | ISO-8601 UTC |
| `page_label` | TEXT? | which EXTRA image of the record the event is about (e.g. `image 62073_302022005552_0481-00154`); NULL = the record's default image. Added 2026-09-28 (REQ-009) |

`none` was added 2026-08-22 (REQ-007 §3.2) and is NOT a synonym for
`index-only-no-image`. `index-only-no-image` is a claim about the **record** — it
legitimately has no scan — and it closes the record. `none` is a report on the **capture**:
we hold no record image, whatever the record may have. A consumer must never treat `none`
as complete. (A pre-existing database is rebuilt to accept it on first open; the ledger is
append-only, and the migration copies every row rather than altering any.)

The **latest event per `apid` whose `page_label` is NULL** (by `created_at`, then rowid)
is the record's current state. "Mined" (complete) means: a `captured` **or
`dedup-blocked`** event with `verify_ok=1`, assertions > 0, and `image_status` ∈ {`full`,
`index-only-no-image`}. This is the same predicate Capsule's own dedup (REQ-004 §1)
blocks on.

**Two additive changes, 2026-09-28 — check your queries.**
- **`dedup-blocked` (BUG-017).** Written when Capsule refuses a capture of a record it
  already holds. It carries the held capture's `capture_id` and copies that capture's facts,
  so it is judged by the same predicate. Before it existed, the refused attempt's `started`
  stood as the latest event and a mined record read as in-flight. A consumer that tests
  `event = 'captured'` for "mined" must now also accept `dedup-blocked`.
- **`page_label` (REQ-009).** A record can span two images (every two-page register
  opening). An extra image gets its own capture and its own events, carrying its label;
  its latest event is its own state and never the record's. **Filter on
  `page_label IS NULL` when you want the record's state.** Every row written before
  2026-09-28 has NULL, so nothing already in the ledger changes meaning. A consumer that
  matches `apid` to find captures now also finds the extra images, which is intended.

**One additive meaning, 2026-10-21 (REQ-010).** `error` on a `captured` event is new. It
never appears beside a held record image, so it cannot change whether a record is mined (a
`failed` or `none` image is never mined). A consumer that read any non-NULL `error` as "the
capture failed" should key on `event` instead.

Note that `verify_ok` itself grew stricter on 2026-08-22 (REQ-007 §3.3): for an
`original` source that yielded no assertions, it is now 0 unless the capture holds a
raster of at least 1500px on its long edge. A consumer already keying on `verify_ok`
needs no change — the flag simply stopped being true for captures that archive no record.

### `capture_lifecycle_event` — superseded / reinstated (REQ-011, added 2026-10-21)

| column | type | contract |
|---|---|---|
| `source_id` | TEXT | the capture this event is about (FK `source.id`) |
| `event` | TEXT | `superseded` \| `reinstated` |
| `superseded_by` | TEXT? | the replacing `source.id`; set exactly when `event = 'superseded'` |
| `reason` | TEXT? | why, in the actor's words |
| `actor` | TEXT | who made the call |
| `created_at` | TEXT | ISO-8601 UTC |

The **latest event per `source_id`** is its state: `superseded` means "cite `superseded_by`
instead"; `reinstated` or no event means the capture is current. Append-only (triggers). A
superseded capture is still evidence: its folder, hashes and assertions are unchanged, and
nothing about the people in it is merged. Additive: a new table, so nothing existing changes
meaning. Capsule refuses a supersession that would leave no current capture (a loop).

### Everything else

`archive_record`, `snippet`, `persona`, `assertion`, `coverage_claim`, `provider_claim`,
`citation`, `capture_note`, `correction_event` exist and are stable in spirit, but their
columns are **not yet contract** — read them at your own risk, pin questions via a REQ if
you need a guarantee.

**`transcription` / `transcript_field` (added 2026-09-24, REQ-006)** hold machine
transcripts of scanned media — **a derivative reading aid, never evidence**. They are
deliberately separate from `assertion`: a consumer must not treat a `transcript_field`
value as a stated fact, nor let its existence raise a grade. Use them to *read* a scan
alongside its image (`crop_filename` under the capture's `transcript/<id>/` folder);
promote a value to evidence only through a human act. Not yet contract, like the rest of
this list.

## 2. The supported pre-capture check

Prefer, in order:
1. **Just capture.** The backend now enforces dedup atomically (REQ-004 §1): a
   verified-complete duplicate returns `409` with `dedup: "blocked"` and
   `duplicate_of: "<capture_id>"`. Re-asking is safe by design.
2. **`capsule query --apid <id> | --url <url>`** — returns
   `{captured, capture_id, complete, image_status, reason}` without opening folders.
3. Direct SQL against the two contract tables above.

Note on URL matching: Capsule's own dedup matches Ancestry records by
`{collection_id, record_id}` (either URL shape) and other providers by URL **including
the query string** (RootsIreland/IrishGenealogy record ids live there). If you hand-roll
a URL match, do the same — a query-stripped match will false-positive across distinct
records on those sites.

## 3. Gramps (REQ-004 §3 — the confirmation, in writing)

**Capsule does not write to the Gramps API. Its only writes are `capsule.db` and the
`captures/` folder tree (plus `data/.capsule-token`).** Attaching capture media/citations
to Gramps is done by the Genealogy workspace's own tools under its own credentials. If
Capsule ever gains a direct Gramps integration (the planned `GrampsWriteAdapter`,
deliberately unbuilt as of 2026-07-17), it will use its **own named Gramps account** —
never a shared or reader credential — so transaction attribution holds. Any change to
this paragraph is a breaking change to this contract and will bump its version.

## 4. Versioning

This contract is versioned by this file (`capsule-db-v1`). Additive changes append here;
breaking changes create `capsule-db-v2.md` and are called out in the backlog. There is no
in-DB schema version stamp yet; if you need one, say so in a REQ and it will be added as
`PRAGMA user_version` (additive).
