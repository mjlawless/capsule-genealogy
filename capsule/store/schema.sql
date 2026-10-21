-- Capsule M0 evidence schema (capture-folder/v1 contract layer).
--
-- Append-only by design: corrections are new `assertion` rows carrying
-- `supersedes_id`, never in-place edits. Every evidence table below is
-- protected by BEFORE UPDATE / BEFORE DELETE triggers that ABORT — this is
-- the storage-level enforcement of "Never merge person identities" and
-- "no destructive merges, no orphaned assertions, ever" (CLAUDE.md).
--
-- Datetimes are stored as ISO-8601 UTC TEXT (e.g. "2026-07-07T12:00:00+00:00").
-- Dates are stored as ISO-8601 TEXT (e.g. "2026-07-07").
-- Booleans are stored as INTEGER CHECK(x IN (0,1)).
-- SnippetAnchor.bbox is stored as a JSON TEXT array "[x0, y0, x1, y1]" or NULL.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- source
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source (
    id              TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    title           TEXT,
    site_name       TEXT,
    record_type     TEXT,
    gating_status   TEXT NOT NULL CHECK (gating_status IN ('public', 'gated')),
    copyright_flag  INTEGER NOT NULL CHECK (copyright_flag IN (0, 1)),
    source_quality  TEXT NOT NULL CHECK (source_quality IN ('original', 'derivative')),
    -- WHY it was judged original or derivative (the model's words), so the call is
    -- auditable rather than a bare enum. NULL = nobody judged, which is stored as
    -- 'derivative' (the weaker claim) — ORIGINAL is earned, never defaulted.
    -- NOTE: post-hoc column — also listed in `_ADDED_COLUMNS` in repository.py.
    source_quality_rationale TEXT,
    content_hash    TEXT NOT NULL,
    spec_version    TEXT NOT NULL DEFAULT 'capture-folder/v1'
);

CREATE TRIGGER IF NOT EXISTS source_no_update
BEFORE UPDATE ON source
BEGIN
    SELECT RAISE(ABORT, 'append-only: source rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS source_no_delete
BEFORE DELETE ON source
BEGIN
    SELECT RAISE(ABORT, 'append-only: source rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- archive_record (1:1 with source)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS archive_record (
    source_id       TEXT PRIMARY KEY REFERENCES source (id),
    local_snapshot  TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    wayback_url     TEXT,
    -- `kind` records what the primary archived artifact IS: an HTML page
    -- ('snapshot', the M0 default) or a fetched document like a PDF ('document').
    -- `media_type` is the document's declared MIME. NOTE: adding a column here
    -- does NOT alter an existing capsule.db (CREATE TABLE is IF NOT EXISTS), so
    -- each also needs an entry in `_ADDED_COLUMNS` in repository.py.
    kind            TEXT NOT NULL DEFAULT 'snapshot' CHECK (kind IN ('snapshot', 'document')),
    media_type      TEXT,
    -- REQ-008: how an INGESTED file reached Capsule ('site:official-download' |
    -- 'user:file'); NULL for every browser capture.
    provenance      TEXT
);

CREATE TRIGGER IF NOT EXISTS archive_record_no_update
BEFORE UPDATE ON archive_record
BEGIN
    SELECT RAISE(ABORT, 'append-only: archive_record rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS archive_record_no_delete
BEFORE DELETE ON archive_record
BEGIN
    SELECT RAISE(ABORT, 'append-only: archive_record rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- snippet
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS snippet (
    id                      TEXT PRIMARY KEY,
    source_id               TEXT NOT NULL REFERENCES source (id),
    verbatim_text           TEXT NOT NULL,
    anchor_kind             TEXT NOT NULL CHECK (anchor_kind IN ('text', 'image')),
    anchor_start            INTEGER,
    anchor_end              INTEGER,
    anchor_media_filename   TEXT,
    anchor_bbox             TEXT,
    UNIQUE (id, source_id)  -- composite-FK target for assertion (same-source guard)
);

CREATE INDEX IF NOT EXISTS idx_snippet_source_id ON snippet (source_id);

CREATE TRIGGER IF NOT EXISTS snippet_no_update
BEFORE UPDATE ON snippet
BEGIN
    SELECT RAISE(ABORT, 'append-only: snippet rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS snippet_no_delete
BEFORE DELETE ON snippet
BEGIN
    SELECT RAISE(ABORT, 'append-only: snippet rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- persona (NOTE: no tree_person_id — see capsule/contracts/evidence.py)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS persona (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES source (id),
    role        TEXT NOT NULL,
    label       TEXT NOT NULL,
    UNIQUE (id, source_id)  -- composite-FK target for assertion (same-source guard)
);

CREATE INDEX IF NOT EXISTS idx_persona_source_id ON persona (source_id);

CREATE TRIGGER IF NOT EXISTS persona_no_update
BEFORE UPDATE ON persona
BEGIN
    SELECT RAISE(ABORT, 'append-only: persona rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS persona_no_delete
BEFORE DELETE ON persona
BEGIN
    SELECT RAISE(ABORT, 'append-only: persona rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- assertion
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS assertion (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES source (id),
    persona_id          TEXT NOT NULL,
    snippet_id          TEXT NOT NULL,
    -- `field` is the label the record prints (snake_cased); `normalized_field` is
    -- our canonical term for it, and ONLY when that term is a synonym rather than
    -- an inference. See capsule/contracts/evidence.py, docs/design/field-naming.md.
    -- NOTE: adding a column here does NOT alter an existing capsule.db — the
    -- CREATE TABLE above is IF NOT EXISTS, so SQLite skips it entirely. Any new
    -- column also needs an entry in `_ADDED_COLUMNS` in repository.py.
    field               TEXT NOT NULL,
    normalized_field    TEXT,
    verbatim_value      TEXT NOT NULL,
    normalized_value    TEXT,
    annotation          TEXT,
    evidence_type       TEXT NOT NULL CHECK (evidence_type IN ('stated', 'calculated', 'estimated')),
    confidence          REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    supersedes_id       TEXT REFERENCES assertion (id),
    -- A human retraction. A superseding row flagged `retracted` means "this fact
    -- is not on the source", so current_assertions excludes it (and its target is
    -- already excluded as superseded). A model-extracted row is always 0, which is
    -- why the JSON serializer omits the default and content_hash is unchanged.
    -- Post-hoc column — also listed in `_ADDED_COLUMNS` in repository.py.
    retracted           INTEGER NOT NULL DEFAULT 0 CHECK (retracted IN (0, 1)),
    created_at          TEXT NOT NULL,
    -- Composite FKs: an assertion's persona and snippet must belong to the
    -- SAME source as the assertion itself (defends against cross-source
    -- evidence mixups where current_assertions(A) would surface B's persona).
    FOREIGN KEY (persona_id, source_id) REFERENCES persona (id, source_id),
    FOREIGN KEY (snippet_id, source_id) REFERENCES snippet (id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_assertion_source_id ON assertion (source_id);
CREATE INDEX IF NOT EXISTS idx_assertion_persona_id ON assertion (persona_id);
-- One correction per superseded assertion: prevents a "fork" (two rows both
-- superseding the same assertion), which would make current_assertions return
-- multiple current rows. Partial unique index (NULLs = originals, unconstrained).
CREATE UNIQUE INDEX IF NOT EXISTS ux_assertion_supersedes_id
    ON assertion (supersedes_id) WHERE supersedes_id IS NOT NULL;

-- Corrections are new rows with supersedes_id set — never in-place edits.
CREATE TRIGGER IF NOT EXISTS assertion_no_update
BEFORE UPDATE ON assertion
BEGIN
    SELECT RAISE(ABORT, 'append-only: assertion rows cannot be updated; insert a correction with supersedes_id instead');
END;

CREATE TRIGGER IF NOT EXISTS assertion_no_delete
BEFORE DELETE ON assertion
BEGIN
    SELECT RAISE(ABORT, 'append-only: assertion rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- citation
-- ---------------------------------------------------------------------------

-- What an INDEX declares it holds: "Kilmore, R.C. Baptisms, 1845-1880".
--
-- A claim about the SOURCE, not about a person — hence no persona_id, unlike
-- `assertion`. It is what makes a negative search usable: an absence only means
-- something if the index covered the years you searched. See `CoverageClaim` in
-- capsule/contracts/evidence.py.
--
-- year_start/year_end are NULLable on purpose: a page may declare an open-ended
-- range ("1845-present") or one we could not parse. An unread range must never be
-- coerced into a number, because a confident wrong answer about coverage IS the bug
-- this table exists to kill.
CREATE TABLE IF NOT EXISTS coverage_claim (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES source (id),
    snippet_id          TEXT NOT NULL,
    -- Verbatim, as the index prints them. Its own vocabulary is evidence.
    place               TEXT NOT NULL,
    record_type         TEXT NOT NULL,
    year_start          INTEGER,
    year_end            INTEGER,
    evidence_type       TEXT NOT NULL CHECK (evidence_type IN ('stated', 'calculated', 'estimated')),
    annotation          TEXT,
    created_at          TEXT NOT NULL,
    CHECK (year_start IS NULL OR year_end IS NULL OR year_end >= year_start),
    -- Composite FK: the backing snippet must belong to the SAME source, so a
    -- coverage claim can never cite another source's text.
    FOREIGN KEY (snippet_id, source_id) REFERENCES snippet (id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_coverage_claim_source_id ON coverage_claim (source_id);
-- The query this table exists for: "is <place>/<record_type> covered for <year>?"
CREATE INDEX IF NOT EXISTS idx_coverage_claim_lookup
    ON coverage_claim (place, record_type);

-- What the PROVIDER says about the record: Ancestry's `Source Citation`, `Source
-- Information`, and above all `Original data` — the line naming the NARA microfilm the
-- database was made from. That line is what lets a citation name the ORIGINAL rather
-- than the database.
--
-- A claim about the SOURCE, so no persona_id (see `ProviderClaim` in
-- capsule/contracts/evidence.py). Verbatim: once Ancestry is gone, this text is all that
-- remains of what they claimed.
CREATE TABLE IF NOT EXISTS provider_claim (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES source (id),
    snippet_id          TEXT NOT NULL,
    key                 TEXT NOT NULL,
    verbatim_value      TEXT NOT NULL,
    evidence_type       TEXT NOT NULL CHECK (evidence_type IN ('stated', 'calculated', 'estimated')),
    annotation          TEXT,
    created_at          TEXT NOT NULL,
    -- Composite FK: the backing snippet must belong to the SAME source.
    FOREIGN KEY (snippet_id, source_id) REFERENCES snippet (id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_claim_source_id ON provider_claim (source_id);
CREATE INDEX IF NOT EXISTS idx_provider_claim_key ON provider_claim (key);

CREATE TRIGGER IF NOT EXISTS provider_claim_no_update
BEFORE UPDATE ON provider_claim
BEGIN
    SELECT RAISE(ABORT, 'append-only: provider_claim rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS provider_claim_no_delete
BEFORE DELETE ON provider_claim
BEGIN
    SELECT RAISE(ABORT, 'append-only: provider_claim rows cannot be deleted');
END;

-- The Ancestry work ledger (REQ-001 R6/R7): has this record been mined, and completely?
--
-- Append-only EVENTS, not a mutable status column — consistent with the rest of the
-- store, and it means the history of a record's mining (issued, failed, re-mined) is
-- itself evidence rather than something overwritten. The current state is the LATEST
-- event per _APID whose `page_label` is NULL (REQ-009: a labelled event is about one extra
-- image of the record, and never speaks for the record as a whole).
--
-- "Mined" is deliberately strict (see ledger.py): a degraded or failed image means NOT
-- mined, so the record stays on the queue. Constraint 4 — assume access to the record may lapse
-- later — means the one unacceptable outcome is BELIEVING we hold a record we do not hold.
CREATE TABLE IF NOT EXISTS ancestry_ledger_event (
    id              TEXT PRIMARY KEY,
    apid            TEXT NOT NULL,          -- normalized "<collection>:<record>"
    collection_id   TEXT NOT NULL,
    record_id       TEXT NOT NULL,
    -- `started` is written BEFORE the work, so a capture that dies in flight (the tab
    -- navigated away, the browser crashed) still leaves a trace. Without it, an aborted
    -- capture is indistinguishable from one never attempted — BUG-005. A `started` with
    -- no terminal event after it means: tried, and vanished.
    -- NOTE: a CHECK constraint canNOT be added to an EXISTING table by CREATE TABLE IF
    -- NOT EXISTS. See `_migrate_events` in ledger.py, which rebuilds the table when it
    -- predates a value listed here.
    -- `dedup-blocked` (BUG-017): a capture of a record we already hold was refused. It
    -- restates the held capture's facts, so the refused attempt's own `started` is never
    -- left as the record's latest word (which read a mined record as STALLED).
    event           TEXT NOT NULL CHECK (event IN
                        ('seeded', 'issued', 'started', 'captured', 'failed',
                         'remine-requested', 'dedup-blocked')),
    capture_id      TEXT,
    -- `none` (REQ-007 §3.2) is a fact about the CAPTURE — we looked at what we banked and
    -- there is no record image in it. Distinct from `index-only-no-image`, which is a claim
    -- about the RECORD (it legitimately has no scan) and is terminal-good. NULL now means
    -- only "not applicable / not looked at"; it used to mean all three, which is how an
    -- empty capture read as a clean one.
    image_status    TEXT CHECK (image_status IS NULL OR image_status IN
                        ('full', 'degraded-canvas', 'index-only-no-image', 'failed',
                         'none')),
    assertion_count INTEGER,
    persona_count   INTEGER,
    verify_ok       INTEGER CHECK (verify_ok IS NULL OR verify_ok IN (0, 1)),
    follow_up       TEXT,                   -- the true source still to chase (R4.2)
    error           TEXT,
    created_at      TEXT NOT NULL,
    -- REQ-009: which image of the record this event is about, e.g. "image 00154". NULL is
    -- the record's DEFAULT image (the one its record page links to), and only NULL-label
    -- events carry the record's mined state. Last column, so it is purely additive.
    page_label      TEXT
);

CREATE INDEX IF NOT EXISTS idx_ancestry_ledger_apid ON ancestry_ledger_event (apid);

CREATE TRIGGER IF NOT EXISTS ancestry_ledger_no_update
BEFORE UPDATE ON ancestry_ledger_event
BEGIN
    SELECT RAISE(ABORT, 'append-only: ledger events cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS ancestry_ledger_no_delete
BEFORE DELETE ON ancestry_ledger_event
BEGIN
    SELECT RAISE(ABORT, 'append-only: ledger events cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS citation (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES source (id),
    reference_note      TEXT NOT NULL,
    short_note          TEXT NOT NULL,
    source_list_entry   TEXT NOT NULL,
    access_date         TEXT NOT NULL,
    archived_url        TEXT NOT NULL,
    template_id         TEXT NOT NULL DEFAULT 'generic-web-v1'
);

CREATE INDEX IF NOT EXISTS idx_citation_source_id ON citation (source_id);

CREATE TRIGGER IF NOT EXISTS citation_no_update
BEFORE UPDATE ON citation
BEGIN
    SELECT RAISE(ABORT, 'append-only: citation rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS citation_no_delete
BEFORE DELETE ON citation
BEGIN
    SELECT RAISE(ABORT, 'append-only: citation rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- capture_note — the researcher's own annotation layer (NOT evidence)
-- ---------------------------------------------------------------------------
--
-- A note is the USER'S free-text commentary about a capture (why it was
-- collected, what to revisit), authored by the researcher — never the source.
-- It is deliberately NOT an assertion: no snippet, no evidence type, and it is
-- excluded from the capture's hash manifest (`notes.json` is verify-excluded,
-- like `agent_log/`). Notes still obey the store's append-only rule: revising a
-- note is a new row, so the same BEFORE UPDATE/DELETE guards apply. A note can
-- be attached long after capture and works for any capture kind (page or PDF).

CREATE TABLE IF NOT EXISTS capture_note (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES source (id),
    text        TEXT NOT NULL,
    author      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capture_note_source_id ON capture_note (source_id);

CREATE TRIGGER IF NOT EXISTS capture_note_no_update
BEFORE UPDATE ON capture_note
BEGIN
    SELECT RAISE(ABORT, 'append-only: capture_note rows cannot be updated; add a new note instead');
END;

CREATE TRIGGER IF NOT EXISTS capture_note_no_delete
BEFORE DELETE ON capture_note
BEGIN
    SELECT RAISE(ABORT, 'append-only: capture_note rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- correction_event — the human-correction flywheel log (NOT evidence)
-- ---------------------------------------------------------------------------
--
-- The evidence OF a correction is the assertion graph itself (a superseding row
-- for edit/retract, a new row for add). This table is the flywheel log on top of
-- it: it remembers what the model said (`previous_value`) versus what the human
-- said (`new_value`) so a corrected capture can become an eval fixture
-- automatically (CLAUDE.md: "corrected outputs become eval fixtures"). Like
-- capture_note it is the researcher's layer — verify-excluded, never part of a
-- sealed content_hash — and append-only under the same BEFORE UPDATE/DELETE
-- guards. `assertion_id` names the row this correction produced. Per-action
-- invariants (add has no supersedes_id/previous_value; retract has no new_value)
-- are enforced by the CorrectionEvent model, not restated as CHECKs here.

CREATE TABLE IF NOT EXISTS correction_event (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source (id),
    action          TEXT NOT NULL CHECK (action IN ('edit', 'add', 'retract')),
    field           TEXT NOT NULL,
    assertion_id    TEXT NOT NULL REFERENCES assertion (id),
    supersedes_id   TEXT REFERENCES assertion (id),
    previous_value  TEXT,
    new_value       TEXT,
    actor           TEXT NOT NULL,
    rationale       TEXT,
    agent_call_id   TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_correction_event_source_id ON correction_event (source_id);

CREATE TRIGGER IF NOT EXISTS correction_event_no_update
BEFORE UPDATE ON correction_event
BEGIN
    SELECT RAISE(ABORT, 'append-only: correction_event rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS correction_event_no_delete
BEFORE DELETE ON correction_event
BEGIN
    SELECT RAISE(ABORT, 'append-only: correction_event rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- Reserved tables (documentation only — NOT created in this increment)
-- ---------------------------------------------------------------------------
--
-- M2 — persona_tree_link: links a persona to a person in an external tree
-- adapter (Contract 2, capsule/contracts/tree_adapter.py) once the Matcher
-- agent exists. Never created before M2; a persona with no link row is an
-- unresolved/unmatched persona, which is the expected M0/M1 state.
--
-- CREATE TABLE persona_tree_link (
--     persona_id          TEXT NOT NULL REFERENCES persona (id),
--     external_person_id  TEXT NOT NULL,
--     adapter             TEXT NOT NULL,
--     linked_at           TEXT NOT NULL,
--     confidence          REAL,
--     linked_by           TEXT NOT NULL
-- );
--
-- M3 — disposition_event: records what happened to a source/persona pair
-- during the guided Ancestry attach flow (capsule export, §5 of docs/plan.md).
--
-- CREATE TABLE disposition_event (
--     source_id    TEXT NOT NULL REFERENCES source (id),
--     persona_id   TEXT NOT NULL REFERENCES persona (id),
--     state        TEXT NOT NULL CHECK (state IN ('local-only', 'in-research-tree', 'promoted-to-primary')),
--     occurred_at  TEXT NOT NULL,
--     note         TEXT
-- );

-- ---------------------------------------------------------------------------
-- transcription / transcript_field — the machine-transcript layer (REQ-006; NOT evidence)
-- ---------------------------------------------------------------------------
--
-- What a vision transcriber READ from a capture's scanned media. Owner ruling R1
-- (2026-09-24): a machine transcript is a derivative reading aid beside the image
-- and never the cited evidence, so it lives in these tables and NEVER in
-- `assertion`; nothing here can raise a grade. A transcribed value enters the
-- evidence graph only when a human accepts it (correction loop), which is when an
-- image-anchored `snippet` is created — hence each field carries its crop
-- reference directly. Re-running the transcriber is a NEW transcription row; like
-- every table here, both are append-only. The on-disk projection is the capture's
-- hash-excluded `transcript/<transcription_id>/` folder (capture-folder-v2 §4).

CREATE TABLE IF NOT EXISTS transcription (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source (id),
    media_filename  TEXT NOT NULL,
    media_sha256    TEXT NOT NULL,
    model           TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'failed')),
    problems        TEXT NOT NULL,  -- JSON array of the guard's findings, verbatim
    layout_notes    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (id, source_id)  -- composite-FK target for transcript_field (same-source guard)
);

CREATE INDEX IF NOT EXISTS idx_transcription_source_id ON transcription (source_id);

CREATE TRIGGER IF NOT EXISTS transcription_no_update
BEFORE UPDATE ON transcription
BEGIN
    SELECT RAISE(ABORT, 'append-only: transcription rows cannot be updated; run the transcriber again');
END;

CREATE TRIGGER IF NOT EXISTS transcription_no_delete
BEFORE DELETE ON transcription
BEGIN
    SELECT RAISE(ABORT, 'append-only: transcription rows cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS transcript_field (
    id                TEXT PRIMARY KEY,
    transcription_id  TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    page              INTEGER,
    field_key         TEXT NOT NULL,
    form_label        TEXT,
    status            TEXT NOT NULL CHECK (status IN ('value', 'unknown', 'blank', 'illegible')),
    verbatim_value    TEXT,  -- exactly as written; NULL only for 'blank'
    annotation        TEXT,  -- normalisation lives here, never in verbatim_value
    confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    needs_review      INTEGER NOT NULL CHECK (needs_review IN (0, 1)),
    note              TEXT,
    attempts          INTEGER NOT NULL CHECK (attempts >= 1),
    crop_filename     TEXT NOT NULL,
    crop_sha256       TEXT NOT NULL,
    crop_bbox         TEXT NOT NULL,  -- JSON "[x0, y0, x1, y1]", native page pixels
    crop_rotation     INTEGER NOT NULL CHECK (crop_rotation IN (0, 90, 180, 270)),
    agent_call_id     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (transcription_id, source_id) REFERENCES transcription (id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_transcript_field_transcription_id ON transcript_field (transcription_id);

CREATE TRIGGER IF NOT EXISTS transcript_field_no_update
BEFORE UPDATE ON transcript_field
BEGIN
    SELECT RAISE(ABORT, 'append-only: transcript_field rows cannot be updated; run the transcriber again');
END;

CREATE TRIGGER IF NOT EXISTS transcript_field_no_delete
BEFORE DELETE ON transcript_field
BEGIN
    SELECT RAISE(ABORT, 'append-only: transcript_field rows cannot be deleted');
END;

-- transcript_comparison — image vs. index, side by side (REQ-006 §3; NOT evidence).
-- When a transcribed field and an index assertion record the same fact, the pair is
-- recorded here with BOTH values. A 'conflict' is a finding, never a resolution: the
-- assertion is not demoted and the transcript is not promoted. 'agree' is
-- information only and never raises a grade (§5.2).
CREATE TABLE IF NOT EXISTS transcript_comparison (
    id                   TEXT PRIMARY KEY,
    source_id            TEXT NOT NULL,
    transcription_id     TEXT NOT NULL,
    assertion_id         TEXT NOT NULL REFERENCES assertion (id),
    transcript_field_id  TEXT NOT NULL REFERENCES transcript_field (id),
    verdict              TEXT NOT NULL CHECK (verdict IN ('agree', 'conflict', 'unclear')),
    index_value          TEXT NOT NULL,
    transcript_value     TEXT,
    explanation          TEXT,
    agent_call_id        TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    FOREIGN KEY (transcription_id, source_id) REFERENCES transcription (id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_transcript_comparison_transcription_id
    ON transcript_comparison (transcription_id);

CREATE TRIGGER IF NOT EXISTS transcript_comparison_no_update
BEFORE UPDATE ON transcript_comparison
BEGIN
    SELECT RAISE(ABORT, 'append-only: transcript_comparison rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS transcript_comparison_no_delete
BEFORE DELETE ON transcript_comparison
BEGIN
    SELECT RAISE(ABORT, 'append-only: transcript_comparison rows cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- capture_lifecycle_event — "this capture was replaced by that one" (REQ-011)
-- ---------------------------------------------------------------------------
--
-- A LIFECYCLE fact about a capture, not an evidence relation between two documents (owner
-- ruling 2026-10-21): it says which capture to cite, and nothing about the people in either.
-- `superseded` names the replacement; `reinstated` withdraws the latest supersession. The
-- current state is the latest event per source. Append-only like everything here: the old
-- capture, its folder and its assertions are never touched, and a mistake is undone by a
-- later event, never by an edit. Projected into the OLD capture's hash-excluded
-- `lifecycle.json`, so the folder-sourced index can show it with no database (captures-index
-- §2).

CREATE TABLE IF NOT EXISTS capture_lifecycle_event (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source (id),
    event           TEXT NOT NULL CHECK (event IN ('superseded', 'reinstated')),
    superseded_by   TEXT REFERENCES source (id),
    reason          TEXT,
    actor           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    CHECK ((event = 'superseded') = (superseded_by IS NOT NULL)),
    CHECK (superseded_by IS NULL OR superseded_by != source_id)
);

CREATE INDEX IF NOT EXISTS idx_capture_lifecycle_source_id
    ON capture_lifecycle_event (source_id);

CREATE TRIGGER IF NOT EXISTS capture_lifecycle_event_no_update
BEFORE UPDATE ON capture_lifecycle_event
BEGIN
    SELECT RAISE(ABORT, 'append-only: capture_lifecycle_event rows cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS capture_lifecycle_event_no_delete
BEFORE DELETE ON capture_lifecycle_event
BEGIN
    SELECT RAISE(ABORT, 'append-only: capture_lifecycle_event rows cannot be deleted');
END;
