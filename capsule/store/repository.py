"""Thin repository over the append-only evidence schema (schema.sql).

Accepts and returns the pydantic models from `capsule.contracts.evidence`.
All queries are parameterized — values are never string-interpolated into
SQL. This module does not know about `TreeAdapter`; tree linkage is a
separate concern (see `persona_tree_link`, reserved for M2 in schema.sql).

Each multi-statement write is wrapped in `with self.connection:` so it
commits or rolls back atomically — a mid-method failure never leaves a
partial write pending for the next call to see.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Sequence

from capsule.contracts.transcript import (
    TranscriptComparison,
    Transcription,
    TranscriptField,
)
from capsule.contracts.evidence import (
    ArchiveKind,
    ArchiveRecord,
    Assertion,
    CaptureNote,
    Citation,
    CorrectionAction,
    CorrectionEvent,
    CoverageClaim,
    EvidenceType,
    ProviderClaim,
    GatingStatus,
    Persona,
    Snippet,
    SnippetAnchor,
    Source,
    SourceQuality,
)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Listed explicitly (never SELECT *) and shared by every coverage read, so the
# column order can't drift apart from `_row_to_coverage_claim`.
_COVERAGE_COLUMNS = (
    "id, source_id, snippet_id, place, record_type, year_start, year_end, "
    "evidence_type, annotation, created_at"
)
_PROVIDER_COLUMNS = (
    "id, source_id, snippet_id, key, verbatim_value, evidence_type, annotation, created_at"
)

# Columns added to existing tables after those tables first shipped, as
# (table, column, type). `schema.sql` uses CREATE TABLE IF NOT EXISTS, so a
# column added there reaches a NEW database and is silently skipped for one that
# already exists — the column never appears, and the next insert dies with
# `no such column`. Every post-hoc column therefore needs an entry here too.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("assertion", "normalized_field", "TEXT"),
    # Human-retraction flag. ADD COLUMN with a NOT NULL DEFAULT 0 backfills every
    # existing row to 0 (never retracted) without firing the append-only triggers.
    ("assertion", "retracted", "INTEGER NOT NULL DEFAULT 0"),
    # Document-primary captures (capture-folder/v2): an existing capsule.db has an
    # archive_record with neither column. ADD COLUMN appends them nullable/defaulted
    # without touching a row, so the append-only triggers never fire. The CHECK on
    # `kind` lives only in the CREATE TABLE (new DBs); the model validates it either way.
    ("archive_record", "kind", "TEXT NOT NULL DEFAULT 'snapshot'"),
    ("archive_record", "media_type", "TEXT"),
    # REQ-008: how an ingested file reached Capsule. NULL for every row before it existed,
    # which is correct — every one of those came through the browser.
    ("archive_record", "provenance", "TEXT"),
    # Why a source was judged original vs derivative. Nullable: every row written
    # before the judgment existed simply has no rationale (and is 'derivative', which
    # is what it always was — so no capture on disk changes meaning).
    ("source", "source_quality_rationale", "TEXT"),
)


def _migrate(connection: sqlite3.Connection) -> None:
    """Apply additive column migrations to an already-existing database.

    `ALTER TABLE ... ADD COLUMN` appends a nullable column without touching a
    single row, so the append-only BEFORE UPDATE / BEFORE DELETE triggers never
    fire and no stored evidence is rewritten. That is the only kind of schema
    change this store permits: an evidence row, once written, is immutable.

    Table and column names are interpolated because SQLite cannot bind an
    identifier as a parameter. They are module constants, never caller input.
    """
    for table, column, decl in _ADDED_COLUMNS:
        cursor = connection.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


class Repository:
    """Opens/initializes a SQLite DB from schema.sql and exposes CRUD-append ops."""

    def __init__(self, db_path: str | Path) -> None:
        self.connection = sqlite3.connect(str(db_path))
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
            _migrate(self.connection)
            self.connection.commit()
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    # -- source ---------------------------------------------------------

    def add_source(self, source: Source) -> Source:
        # Revalidate at the write boundary: model_copy(update=...) can yield an
        # instance that skipped the gating invariant (pydantic does not re-run
        # validators on copy-update), so re-validate before persisting.
        source = Source.model_validate(source.model_dump())
        with self.connection:
            self._insert_source(source)
        return source

    def _insert_source(self, source: Source) -> None:
        """Emit the source + archive rows on the current transaction (no commit).

        Private so that both `add_source` (single-row) and `add_capture`
        (whole-capture, one transaction) can share the exact same SQL.
        """
        self.connection.execute(
            """
            INSERT INTO source
                (id, url, title, site_name, record_type, gating_status,
                 copyright_flag, source_quality, source_quality_rationale,
                 content_hash, spec_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.id,
                source.url,
                source.title,
                source.site_name,
                source.record_type,
                source.gating_status.value,
                int(source.copyright_flag),
                source.source_quality.value,
                source.source_quality_rationale,
                source.content_hash,
                source.spec_version,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO archive_record
                (source_id, local_snapshot, sha256, captured_at, wayback_url,
                 kind, media_type, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.id,
                source.archive.local_snapshot,
                source.archive.sha256,
                source.archive.captured_at.isoformat(),
                source.archive.wayback_url,
                source.archive.kind.value,
                source.archive.media_type,
                source.archive.provenance,
            ),
        )

    def get_source(self, source_id: str) -> Source:
        cursor = self.connection.execute(
            """
            SELECT s.id, s.url, s.title, s.site_name, s.record_type, s.gating_status,
                   s.copyright_flag, s.source_quality, s.source_quality_rationale,
                   s.content_hash, s.spec_version,
                   a.local_snapshot, a.sha256, a.captured_at, a.wayback_url,
                   a.kind, a.media_type, a.provenance
            FROM source s
            JOIN archive_record a ON a.source_id = s.id
            WHERE s.id = ?
            """,
            (source_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"source not found: {source_id}")
        return _row_to_source(row)

    # -- persona ----------------------------------------------------------

    def add_persona(self, persona: Persona) -> Persona:
        with self.connection:
            self._insert_persona(persona)
        return persona

    def _insert_persona(self, persona: Persona) -> None:
        self.connection.execute(
            "INSERT INTO persona (id, source_id, role, label) VALUES (?, ?, ?, ?)",
            (persona.id, persona.source_id, persona.role, persona.label),
        )

    def get_persona(self, persona_id: str) -> Persona:
        cursor = self.connection.execute(
            "SELECT id, source_id, role, label FROM persona WHERE id = ?",
            (persona_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"persona not found: {persona_id}")
        id_, source_id, role, label = row
        return Persona(id=id_, source_id=source_id, role=role, label=label)

    def personas_for_source(self, source_id: str) -> list[Persona]:
        """Every persona on `source_id`, in insertion order.

        Personas are append-only and never retracted (only a persona's *facts*
        are), so this is the full cast the source names — which is exactly the
        persona set an eval fixture should expect the model to find.
        """
        cursor = self.connection.execute(
            "SELECT id, source_id, role, label FROM persona WHERE source_id = ? ORDER BY rowid",
            (source_id,),
        )
        return [
            Persona(id=r[0], source_id=r[1], role=r[2], label=r[3]) for r in cursor.fetchall()
        ]

    # -- snippet ------------------------------------------------------------

    def add_snippet(self, snippet: Snippet) -> Snippet:
        with self.connection:
            self._insert_snippet(snippet)
        return snippet

    def _insert_snippet(self, snippet: Snippet) -> None:
        anchor = snippet.anchor
        bbox_json = json.dumps(list(anchor.bbox)) if anchor.bbox is not None else None
        self.connection.execute(
            """
            INSERT INTO snippet
                (id, source_id, verbatim_text, anchor_kind, anchor_start,
                 anchor_end, anchor_media_filename, anchor_bbox)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snippet.id,
                snippet.source_id,
                snippet.verbatim_text,
                anchor.kind,
                anchor.start,
                anchor.end,
                anchor.media_filename,
                bbox_json,
            ),
        )

    def get_snippet(self, snippet_id: str) -> Snippet:
        cursor = self.connection.execute(
            """
            SELECT id, source_id, verbatim_text, anchor_kind, anchor_start,
                   anchor_end, anchor_media_filename, anchor_bbox
            FROM snippet
            WHERE id = ?
            """,
            (snippet_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"snippet not found: {snippet_id}")
        return _row_to_snippet(row)

    # -- coverage claim (what an index says it holds) --------------------------

    def add_coverage_claim(self, claim: CoverageClaim) -> CoverageClaim:
        claim = CoverageClaim.model_validate(claim.model_dump())
        with self.connection:
            self._insert_coverage_claim(claim)
        return claim

    def _insert_coverage_claim(self, claim: CoverageClaim) -> None:
        self.connection.execute(
            """
            INSERT INTO coverage_claim
                (id, source_id, snippet_id, place, record_type, year_start,
                 year_end, evidence_type, annotation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim.id,
                claim.source_id,
                claim.snippet_id,
                claim.place,
                claim.record_type,
                claim.year_start,
                claim.year_end,
                claim.evidence_type.value,
                claim.annotation,
                claim.created_at.isoformat(),
            ),
        )

    def coverage_claims_for_source(self, source_id: str) -> list[CoverageClaim]:
        cursor = self.connection.execute(
            f"SELECT {_COVERAGE_COLUMNS} FROM coverage_claim WHERE source_id = ? "
            "ORDER BY place, record_type, year_start",
            (source_id,),
        )
        return [_row_to_coverage_claim(row) for row in cursor.fetchall()]

    def coverage_for(
        self,
        *,
        place: Optional[str] = None,
        record_type: Optional[str] = None,
        year: Optional[int] = None,
    ) -> list[CoverageClaim]:
        """Coverage claims matching a place / record type / year.

        The read path the whole feature exists for: before treating an empty search
        as meaningful, ask whether the index ever covered what you searched.

        `place` and `record_type` match case-insensitively on a SUBSTRING, because an
        index's own vocabulary ("Co. Cavan / Kilmore", "R.C. Baptisms") is kept
        verbatim while a researcher types "kilmore". Deliberately generous: this
        surfaces candidate claims for a human to read against their snippets, it does
        NOT adjudicate coverage. A too-narrow match would HIDE a claim and recreate
        the very false-premise bug; a too-broad one merely shows an extra row.

        `year` is filtered in Python via `CoverageClaim.covers`, not in SQL, so the
        NULL/open-ended semantics live in exactly ONE place (the model) instead of
        being duplicated in a WHERE clause that could silently disagree with it.
        """
        sql = f"SELECT {_COVERAGE_COLUMNS} FROM coverage_claim"
        conditions: list[str] = []
        params: list[object] = []
        if place:
            conditions.append("LOWER(place) LIKE ?")
            params.append(f"%{place.lower()}%")
        if record_type:
            conditions.append("LOWER(record_type) LIKE ?")
            params.append(f"%{record_type.lower()}%")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY place, record_type, year_start"

        claims = [_row_to_coverage_claim(row) for row in self.connection.execute(sql, params)]
        if year is None:
            return claims
        return [c for c in claims if c.covers(year)]

    # -- provider claim (what the provider says about the record) ---------------

    def add_provider_claim(self, claim: ProviderClaim) -> ProviderClaim:
        claim = ProviderClaim.model_validate(claim.model_dump())
        with self.connection:
            self._insert_provider_claim(claim)
        return claim

    def _insert_provider_claim(self, claim: ProviderClaim) -> None:
        self.connection.execute(
            """
            INSERT INTO provider_claim
                (id, source_id, snippet_id, key, verbatim_value, evidence_type,
                 annotation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim.id,
                claim.source_id,
                claim.snippet_id,
                claim.key,
                claim.verbatim_value,
                claim.evidence_type.value,
                claim.annotation,
                claim.created_at.isoformat(),
            ),
        )

    def provider_claims_for_source(self, source_id: str) -> list[ProviderClaim]:
        cursor = self.connection.execute(
            f"SELECT {_PROVIDER_COLUMNS} FROM provider_claim WHERE source_id = ? ORDER BY key",
            (source_id,),
        )
        return [_row_to_provider_claim(row) for row in cursor.fetchall()]

    # -- assertion ------------------------------------------------------------

    def add_assertion(self, assertion: Assertion) -> Assertion:
        # Revalidate at the write boundary (confidence range, UTC created_at),
        # closing the model_copy(update=...) validation gap for corrections too.
        assertion = Assertion.model_validate(assertion.model_dump())
        with self.connection:
            self._insert_assertion(assertion)
        return assertion

    def _insert_assertion(self, assertion: Assertion) -> None:
        self.connection.execute(
            """
            INSERT INTO assertion
                (id, source_id, persona_id, snippet_id, field, normalized_field,
                 verbatim_value, normalized_value, annotation, evidence_type,
                 confidence, supersedes_id, retracted, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assertion.id,
                assertion.source_id,
                assertion.persona_id,
                assertion.snippet_id,
                assertion.field,
                assertion.normalized_field,
                assertion.verbatim_value,
                assertion.normalized_value,
                assertion.annotation,
                assertion.evidence_type.value,
                assertion.confidence,
                assertion.supersedes_id,
                int(assertion.retracted),
                assertion.created_at.isoformat(),
            ),
        )

    def add_correction(self, assertion: Assertion, supersedes_id: str) -> Assertion:
        """Insert `assertion` as a correction of `supersedes_id`.

        Never mutates the passed-in `assertion` (frozen model, and the
        repository's own contract regardless) — a new model instance
        carrying `supersedes_id` is built via `model_copy` and that copy is
        stored/returned.
        """
        correction = assertion.model_copy(update={"supersedes_id": supersedes_id})
        return self.add_assertion(correction)

    # -- citation ------------------------------------------------------------

    def add_citation(self, citation: Citation) -> Citation:
        with self.connection:
            self._insert_citation(citation)
        return citation

    def _insert_citation(self, citation: Citation) -> None:
        self.connection.execute(
            """
            INSERT INTO citation
                (id, source_id, reference_note, short_note, source_list_entry,
                 access_date, archived_url, template_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                citation.id,
                citation.source_id,
                citation.reference_note,
                citation.short_note,
                citation.source_list_entry,
                citation.access_date.isoformat(),
                citation.archived_url,
                citation.template_id,
            ),
        )

    # -- capture_note (the researcher's annotation layer) --------------------

    def add_capture_note(self, note: CaptureNote) -> CaptureNote:
        """Append one researcher note (append-only; revising = a new note).

        A note is the user's commentary, not source evidence — see `CaptureNote`.
        The source it annotates must already exist (FK), so a note can be added
        long after capture.
        """
        note = CaptureNote.model_validate(note.model_dump())
        with self.connection:
            self._insert_capture_note(note)
        return note

    def _insert_capture_note(self, note: CaptureNote) -> None:
        self.connection.execute(
            """
            INSERT INTO capture_note (id, source_id, text, author, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (note.id, note.source_id, note.text, note.author, note.created_at.isoformat()),
        )

    def notes_for_source(self, source_id: str) -> list[CaptureNote]:
        """All notes for `source_id`, oldest first (append order)."""
        cursor = self.connection.execute(
            """
            SELECT id, source_id, text, author, created_at
            FROM capture_note
            WHERE source_id = ?
            ORDER BY created_at, id
            """,
            (source_id,),
        )
        return [
            CaptureNote(id=r[0], source_id=r[1], text=r[2], author=r[3], created_at=r[4])
            for r in cursor.fetchall()
        ]

    # -- capture lifecycle (REQ-011: superseded / reinstated) -------------------

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        """Hold SQLite's write lock (`BEGIN IMMEDIATE`) for a check-then-append sequence.

        A `threading.Lock` serializes one process; this serializes every process on the same
        database (the CLI and the running server), so a check made inside it still holds when
        the append commits. Commits on success, rolls back on any exception.
        """
        if self.connection.in_transaction:
            self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            # Inside the guard: a COMMIT that fails (busy) can leave the transaction open,
            # and anything read afterwards would see rows that are about to vanish.
            self.connection.commit()
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def add_lifecycle_event(
        self,
        *,
        event_id: str,
        source_id: str,
        event: str,
        superseded_by: Optional[str],
        reason: Optional[str],
        actor: str,
        created_at: datetime,
    ) -> None:
        """Append one lifecycle event. FK- and CHECK-validated by the store itself.

        Inside `write_lock()` it joins that transaction; otherwise it commits on its own.
        """
        joined = self.connection.in_transaction
        self.connection.execute(
            """
            INSERT INTO capture_lifecycle_event
                (id, source_id, event, superseded_by, reason, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, source_id, event, superseded_by, reason, actor, created_at.isoformat()),
        )
        if not joined:
            self.connection.commit()

    def lifecycle_for_source(self, source_id: str) -> list[dict]:
        """Every lifecycle event for `source_id`, in the order they were appended."""
        cursor = self.connection.execute(
            """
            SELECT id, source_id, event, superseded_by, reason, actor, created_at
            FROM capture_lifecycle_event
            WHERE source_id = ?
            ORDER BY rowid
            """,
            (source_id,),
        )
        # APPEND order, not `created_at`: a clock that stepped backward between a supersession
        # and its reinstatement must not resurrect the supersession (Codex review).
        keys = ("id", "source_id", "event", "superseded_by", "reason", "actor", "created_at")
        return [dict(zip(keys, row)) for row in cursor.fetchall()]

    def superseded_by(self, source_id: str) -> Optional[str]:
        """The source that currently supersedes `source_id`, or None (latest event wins)."""
        events = self.lifecycle_for_source(source_id)
        if not events or events[-1]["event"] != "superseded":
            return None
        return events[-1]["superseded_by"]

    # -- transcription (REQ-006: the machine-transcript layer, NOT evidence) ----

    def add_transcription(
        self,
        transcription: Transcription,
        fields: Sequence[TranscriptField],
        comparisons: Sequence[TranscriptComparison] = (),
    ) -> Transcription:
        """Append one transcriber run, its fields, and its image-vs-index
        comparisons, atomically (all or nothing).

        A transcript is a derivative reading aid (REQ-006 §5.1): it is written to
        its own tables only — never `assertion`, `snippet`, or `persona` — so it
        can neither be mistaken for the record nor raise a grade. Re-running the
        transcriber is a new call here, never an overwrite.
        """
        transcription = Transcription.model_validate(transcription.model_dump())
        fields = [TranscriptField.model_validate(f.model_dump()) for f in fields]
        comparisons = [TranscriptComparison.model_validate(c.model_dump()) for c in comparisons]
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO transcription
                    (id, source_id, media_filename, media_sha256, model, status,
                     problems, layout_notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transcription.id,
                    transcription.source_id,
                    transcription.media_filename,
                    transcription.media_sha256,
                    transcription.model,
                    transcription.status,
                    json.dumps(list(transcription.problems), ensure_ascii=False),
                    transcription.layout_notes,
                    transcription.created_at.isoformat(),
                ),
            )
            for field in fields:
                self.connection.execute(
                    """
                    INSERT INTO transcript_field
                        (id, transcription_id, source_id, page, field_key, form_label,
                         status, verbatim_value, annotation, confidence, needs_review,
                         note, attempts, crop_filename, crop_sha256, crop_bbox,
                         crop_rotation, agent_call_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        field.id,
                        field.transcription_id,
                        field.source_id,
                        field.page,
                        field.field_key,
                        field.form_label,
                        field.status.value,
                        field.verbatim_value,
                        field.annotation,
                        field.confidence,
                        int(field.needs_review),
                        field.note,
                        field.attempts,
                        field.crop_filename,
                        field.crop_sha256,
                        json.dumps(list(field.crop_bbox)),
                        field.crop_rotation,
                        field.agent_call_id,
                        field.created_at.isoformat(),
                    ),
                )
            for comparison in comparisons:
                self.connection.execute(
                    """
                    INSERT INTO transcript_comparison
                        (id, source_id, transcription_id, assertion_id, transcript_field_id,
                         verdict, index_value, transcript_value, explanation, agent_call_id,
                         created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comparison.id,
                        comparison.source_id,
                        comparison.transcription_id,
                        comparison.assertion_id,
                        comparison.transcript_field_id,
                        comparison.verdict,
                        comparison.index_value,
                        comparison.transcript_value,
                        comparison.explanation,
                        comparison.agent_call_id,
                        comparison.created_at.isoformat(),
                    ),
                )
        return transcription

    def comparisons_for_transcription(self, transcription_id: str) -> list[TranscriptComparison]:
        """Image-vs-index comparisons recorded with one transcriber run, in order."""
        cursor = self.connection.execute(
            """
            SELECT id, source_id, transcription_id, assertion_id, transcript_field_id, verdict,
                   index_value, transcript_value, explanation, agent_call_id, created_at
            FROM transcript_comparison
            WHERE transcription_id = ?
            ORDER BY rowid
            """,
            (transcription_id,),
        )
        return [
            TranscriptComparison(
                id=r[0], source_id=r[1], transcription_id=r[2], assertion_id=r[3],
                transcript_field_id=r[4], verdict=r[5], index_value=r[6], transcript_value=r[7],
                explanation=r[8], agent_call_id=r[9], created_at=r[10],
            )
            for r in cursor.fetchall()
        ]

    def transcriptions_for_source(self, source_id: str) -> list[Transcription]:
        """Every transcriber run for `source_id`, oldest first; the last is the latest."""
        cursor = self.connection.execute(
            """
            SELECT id, source_id, media_filename, media_sha256, model, status,
                   problems, layout_notes, created_at
            FROM transcription
            WHERE source_id = ?
            ORDER BY created_at, id
            """,
            (source_id,),
        )
        return [
            Transcription(
                id=r[0], source_id=r[1], media_filename=r[2], media_sha256=r[3], model=r[4],
                status=r[5], problems=tuple(json.loads(r[6])), layout_notes=r[7], created_at=r[8],
            )
            for r in cursor.fetchall()
        ]

    def fields_for_transcription(self, transcription_id: str) -> list[TranscriptField]:
        """The fields of one transcriber run, in the order they were read."""
        cursor = self.connection.execute(
            """
            SELECT id, transcription_id, source_id, page, field_key, form_label, status,
                   verbatim_value, annotation, confidence, needs_review, note, attempts,
                   crop_filename, crop_sha256, crop_bbox, crop_rotation, agent_call_id,
                   created_at
            FROM transcript_field
            WHERE transcription_id = ?
            ORDER BY rowid
            """,
            (transcription_id,),
        )
        return [
            TranscriptField(
                id=r[0], transcription_id=r[1], source_id=r[2], page=r[3], field_key=r[4],
                form_label=r[5], status=r[6], verbatim_value=r[7], annotation=r[8],
                confidence=r[9], needs_review=bool(r[10]), note=r[11], attempts=r[12],
                crop_filename=r[13], crop_sha256=r[14], crop_bbox=tuple(json.loads(r[15])),
                crop_rotation=r[16], agent_call_id=r[17], created_at=r[18],
            )
            for r in cursor.fetchall()
        ]

    # -- correction_event (the human-correction flywheel log) ----------------

    def add_correction_event(self, event: CorrectionEvent) -> CorrectionEvent:
        """Append one correction-event log row (append-only).

        This records what the model said vs. what the human said; the *evidence*
        of the correction is the assertion graph (add_correction / a retracted
        superseding row). Both the source and the produced `assertion_id` must
        already exist — the FKs enforce that the log can never dangle.
        """
        event = CorrectionEvent.model_validate(event.model_dump())
        with self.connection:
            self._insert_correction_event(event)
        return event

    def _insert_correction_event(self, event: CorrectionEvent) -> None:
        self.connection.execute(
            """
            INSERT INTO correction_event
                (id, source_id, action, field, assertion_id, supersedes_id,
                 previous_value, new_value, actor, rationale, agent_call_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.source_id,
                event.action.value,
                event.field,
                event.assertion_id,
                event.supersedes_id,
                event.previous_value,
                event.new_value,
                event.actor,
                event.rationale,
                event.agent_call_id,
                event.created_at.isoformat(),
            ),
        )

    def corrections_for_source(self, source_id: str) -> list[CorrectionEvent]:
        """All correction events for `source_id`, oldest first (append order)."""
        cursor = self.connection.execute(
            """
            SELECT id, source_id, action, field, assertion_id, supersedes_id,
                   previous_value, new_value, actor, rationale, agent_call_id, created_at
            FROM correction_event
            WHERE source_id = ?
            ORDER BY created_at, id
            """,
            (source_id,),
        )
        return [
            CorrectionEvent(
                id=r[0],
                source_id=r[1],
                action=CorrectionAction(r[2]),
                field=r[3],
                assertion_id=r[4],
                supersedes_id=r[5],
                previous_value=r[6],
                new_value=r[7],
                actor=r[8],
                rationale=r[9],
                agent_call_id=r[10],
                created_at=r[11],
            )
            for r in cursor.fetchall()
        ]

    def record_correction(
        self,
        *,
        assertion: Assertion,
        event: CorrectionEvent,
        new_snippet: Snippet | None = None,
    ) -> tuple[Assertion, CorrectionEvent]:
        """Persist a correction — its produced assertion + its log — atomically.

        One transaction so the flywheel log can never exist without the assertion
        it describes, nor an added assertion without its supporting snippet. The
        caller (apply_correction) has already built the models: a superseding row
        (edit/retract) or a new row plus its human-supplied `new_snippet` (add).
        Both models are re-validated at this write boundary, since a `model_copy`
        upstream can bypass pydantic validators.
        """
        assertion = Assertion.model_validate(assertion.model_dump())
        event = CorrectionEvent.model_validate(event.model_dump())
        with self.connection:
            if new_snippet is not None:
                self._insert_snippet(new_snippet)
            self._insert_assertion(assertion)
            self._insert_correction_event(event)
        return assertion, event

    # -- whole-capture (atomic) ---------------------------------------------

    def add_capture(
        self,
        *,
        source: Source,
        citation: Citation,
        personas: Sequence[Persona],
        snippets: Sequence[Snippet],
        assertions: Sequence[Assertion],
        notes: Sequence[CaptureNote] = (),
        coverage: Sequence[CoverageClaim] = (),
        provider_claims: Sequence[ProviderClaim] = (),
    ) -> None:
        """Index a whole capture in ONE transaction — all rows or none.

        A capture folder is written to disk before it is indexed here, so a
        partial index (a source with only some of its assertions) would be an
        un-repairable violation of the append-only 'no orphaned assertions'
        invariant. Wrapping every insert in a single `with self.connection:`
        makes a mid-write failure roll the whole index back, leaving the DB
        clean and the on-disk capture re-indexable.
        """
        source = Source.model_validate(source.model_dump())
        validated_assertions = [
            Assertion.model_validate(a.model_dump()) for a in assertions
        ]
        validated_notes = [CaptureNote.model_validate(n.model_dump()) for n in notes]
        validated_coverage = [CoverageClaim.model_validate(c.model_dump()) for c in coverage]
        validated_provider_claims = [
            ProviderClaim.model_validate(p.model_dump()) for p in provider_claims
        ]
        with self.connection:
            self._insert_source(source)
            self._insert_citation(citation)
            for persona in personas:
                self._insert_persona(persona)
            for snippet in snippets:
                self._insert_snippet(snippet)
            for assertion in validated_assertions:
                self._insert_assertion(assertion)
            for note in validated_notes:
                self._insert_capture_note(note)
            # Coverage claims ride the same transaction: a capture that indexed the
            # index's holdings only halfway would answer "was 1852 covered?" with a
            # confident, wrong No — the exact failure this table exists to prevent.
            for claim in validated_coverage:
                self._insert_coverage_claim(claim)
            # The provider's own description of the record — including the `Original
            # data` line naming the NARA microfilm. Same transaction: a capture that
            # indexed the record but lost its provenance would cite the database when it
            # could have cited the original, and nobody would ever know it was possible.
            for provider_claim in validated_provider_claims:
                self._insert_provider_claim(provider_claim)

    def get_citation(self, citation_id: str) -> Citation:
        cursor = self.connection.execute(
            """
            SELECT id, source_id, reference_note, short_note, source_list_entry,
                   access_date, archived_url, template_id
            FROM citation
            WHERE id = ?
            """,
            (citation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"citation not found: {citation_id}")
        return _row_to_citation(row)

    # -- queries ------------------------------------------------------------

    def current_assertions(self, source_id: str) -> list[Assertion]:
        """The live facts for `source_id`.

        A row is current when it is (a) not superseded by a later correction and
        (b) not itself a retraction. A retraction supersedes its target — so the
        target drops out via the NOT IN — and the retracting row carries no fact,
        so `retracted = 0` drops it too. The net effect of a retract is that the
        fact disappears entirely, which is the whole point of retract vs edit.
        """
        cursor = self.connection.execute(
            """
            SELECT id, source_id, persona_id, snippet_id, field, normalized_field,
                   verbatim_value, normalized_value, annotation, evidence_type,
                   confidence, supersedes_id, retracted, created_at
            FROM assertion
            WHERE source_id = ?
              AND retracted = 0
              AND id NOT IN (
                  SELECT supersedes_id FROM assertion WHERE supersedes_id IS NOT NULL
              )
            """,
            (source_id,),
        )
        return [_row_to_assertion(row) for row in cursor.fetchall()]


def _row_to_source(row: tuple) -> Source:
    (
        id_,
        url,
        title,
        site_name,
        record_type,
        gating_status,
        copyright_flag,
        source_quality,
        source_quality_rationale,
        content_hash,
        spec_version,
        local_snapshot,
        sha256,
        captured_at,
        wayback_url,
        kind,
        media_type,
        provenance,
    ) = row
    archive = ArchiveRecord(
        local_snapshot=local_snapshot,
        sha256=sha256,
        captured_at=captured_at,
        wayback_url=wayback_url,
        kind=ArchiveKind(kind),
        media_type=media_type,
        provenance=provenance,
    )
    return Source(
        id=id_,
        url=url,
        title=title,
        site_name=site_name,
        record_type=record_type,
        gating_status=GatingStatus(gating_status),
        copyright_flag=bool(copyright_flag),
        source_quality=SourceQuality(source_quality),
        source_quality_rationale=source_quality_rationale,
        archive=archive,
        content_hash=content_hash,
        spec_version=spec_version,
    )


def _row_to_coverage_claim(row: tuple) -> CoverageClaim:
    (
        id_,
        source_id,
        snippet_id,
        place,
        record_type,
        year_start,
        year_end,
        evidence_type,
        annotation,
        created_at,
    ) = row
    return CoverageClaim(
        id=id_,
        source_id=source_id,
        snippet_id=snippet_id,
        place=place,
        record_type=record_type,
        year_start=year_start,
        year_end=year_end,
        evidence_type=EvidenceType(evidence_type),
        annotation=annotation,
        created_at=datetime.fromisoformat(created_at),
    )


def _row_to_provider_claim(row: tuple) -> ProviderClaim:
    (
        id_,
        source_id,
        snippet_id,
        key,
        verbatim_value,
        evidence_type,
        annotation,
        created_at,
    ) = row
    return ProviderClaim(
        id=id_,
        source_id=source_id,
        snippet_id=snippet_id,
        key=key,
        verbatim_value=verbatim_value,
        evidence_type=EvidenceType(evidence_type),
        annotation=annotation,
        created_at=datetime.fromisoformat(created_at),
    )


def _row_to_snippet(row: tuple) -> Snippet:
    (
        id_,
        source_id,
        verbatim_text,
        anchor_kind,
        anchor_start,
        anchor_end,
        anchor_media_filename,
        anchor_bbox_json,
    ) = row
    bbox = tuple(json.loads(anchor_bbox_json)) if anchor_bbox_json is not None else None
    anchor = SnippetAnchor(
        kind=anchor_kind,
        start=anchor_start,
        end=anchor_end,
        media_filename=anchor_media_filename,
        bbox=bbox,
    )
    return Snippet(id=id_, source_id=source_id, verbatim_text=verbatim_text, anchor=anchor)


def _row_to_citation(row: tuple) -> Citation:
    (
        id_,
        source_id,
        reference_note,
        short_note,
        source_list_entry,
        access_date,
        archived_url,
        template_id,
    ) = row
    return Citation(
        id=id_,
        source_id=source_id,
        reference_note=reference_note,
        short_note=short_note,
        source_list_entry=source_list_entry,
        access_date=access_date,
        archived_url=archived_url,
        template_id=template_id,
    )


def _row_to_assertion(row: tuple) -> Assertion:
    (
        id_,
        source_id,
        persona_id,
        snippet_id,
        field,
        normalized_field,
        verbatim_value,
        normalized_value,
        annotation,
        evidence_type,
        confidence,
        supersedes_id,
        retracted,
        created_at,
    ) = row
    return Assertion(
        id=id_,
        source_id=source_id,
        persona_id=persona_id,
        snippet_id=snippet_id,
        field=field,
        normalized_field=normalized_field,
        verbatim_value=verbatim_value,
        normalized_value=normalized_value,
        annotation=annotation,
        evidence_type=EvidenceType(evidence_type),
        confidence=confidence,
        supersedes_id=supersedes_id,
        retracted=bool(retracted),
        created_at=created_at,
    )
