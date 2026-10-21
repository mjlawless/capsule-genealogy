"""C2 store layer for the human-correction flywheel.

Two related mechanisms:
- `Assertion.retracted` + `current_assertions`: a retraction makes a fact vanish
  (the target is superseded and the retracting row carries no fact).
- the `correction_event` log: an append-only record of model-said-X / human-said-Y,
  FK-anchored to the source and to the assertion the correction produced.

Written test-first against capsule/store/repository.py + schema.sql.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from capsule.contracts.evidence import (
    ArchiveRecord,
    Assertion,
    CorrectionAction,
    CorrectionEvent,
    EvidenceType,
    GatingStatus,
    Persona,
    Snippet,
    SnippetAnchor,
    Source,
    SourceQuality,
)
from capsule.store.repository import Repository

_UTC = timezone.utc


def _now() -> datetime:
    return datetime(2026, 7, 12, 12, 0, 0, tzinfo=_UTC)


def _source(source_id: str = "src-1") -> Source:
    return Source(
        id=source_id,
        url="https://example.org/record",
        gating_status=GatingStatus.PUBLIC,
        copyright_flag=False,
        source_quality=SourceQuality.DERIVATIVE,
        archive=ArchiveRecord(local_snapshot="snapshot.html", sha256="a" * 64, captured_at=_now()),
        content_hash="b" * 64,
    )


def _persona() -> Persona:
    return Persona(id="persona-1", source_id="src-1", role="deceased", label="John Murphy")


def _snippet() -> Snippet:
    return Snippet(
        id="snippet-1",
        source_id="src-1",
        verbatim_text="b. 1900",
        anchor=SnippetAnchor(kind="text", start=0, end=7),
    )


def _assertion(**overrides) -> Assertion:
    defaults = dict(
        id="assertion-1",
        source_id="src-1",
        persona_id="persona-1",
        snippet_id="snippet-1",
        field="birth_year",
        verbatim_value="1900",
        evidence_type=EvidenceType.STATED,
        created_at=_now(),
    )
    defaults.update(overrides)
    return Assertion(**defaults)


def _event(**overrides) -> CorrectionEvent:
    defaults = dict(
        id="ce-1",
        source_id="src-1",
        action=CorrectionAction.EDIT,
        field="birth_year",
        assertion_id="assertion-2",
        supersedes_id="assertion-1",
        previous_value="1900",
        new_value="1901",
        created_at=_now(),
    )
    defaults.update(overrides)
    return CorrectionEvent(**defaults)


@pytest.fixture()
def repo(tmp_path):
    repository = Repository(tmp_path / "capsule.db")
    yield repository
    repository.close()


def _seed_capture(repo) -> None:
    repo.add_source(_source())
    repo.add_persona(_persona())
    repo.add_snippet(_snippet())
    repo.add_assertion(_assertion())  # assertion-1 (the original model fact)


# ---------------------------------------------------------------------------
# retracted + current_assertions
# ---------------------------------------------------------------------------


class TestRetraction:
    def test_retracted_assertion_is_excluded_from_current(self, repo):
        """A lone retracted row is not a live fact — it must never surface."""
        _seed_capture(repo)
        repo.add_assertion(
            _assertion(id="assertion-solo", field="note", verbatim_value="x", retracted=True)
        )
        current = repo.current_assertions("src-1")
        assert {a.id for a in current} == {"assertion-1"}  # the retracted row is gone

    def test_retract_makes_the_fact_disappear(self, repo):
        """A retraction supersedes its target AND carries no fact, so both drop:
        the field is removed entirely, which is retract's whole difference from edit.
        """
        _seed_capture(repo)
        retraction = _assertion(id="assertion-2", retracted=True)
        repo.add_correction(retraction, supersedes_id="assertion-1")

        assert repo.current_assertions("src-1") == []

    def test_edit_correction_still_returns_the_latest(self, repo):
        """Guard the negative space: a NON-retracted correction is still current,
        so `retracted = 0` did not accidentally hide ordinary edits.
        """
        _seed_capture(repo)
        repo.add_correction(
            _assertion(id="assertion-2", verbatim_value="1901"), supersedes_id="assertion-1"
        )
        (current,) = repo.current_assertions("src-1")
        assert current.id == "assertion-2" and current.verbatim_value == "1901"


# ---------------------------------------------------------------------------
# correction_event log
# ---------------------------------------------------------------------------


class TestCorrectionEventLog:
    def test_roundtrip_and_ordering(self, repo):
        _seed_capture(repo)
        repo.add_correction(_assertion(id="assertion-2", verbatim_value="1901"), supersedes_id="assertion-1")

        later = _event(id="ce-2", created_at=_now() + timedelta(minutes=5))
        earlier = _event(id="ce-1", created_at=_now())
        repo.add_correction_event(later)
        stored = repo.add_correction_event(earlier)

        assert stored.actor == "owner"  # default
        events = repo.corrections_for_source("src-1")
        assert [e.id for e in events] == ["ce-1", "ce-2"]  # oldest first
        assert events[0].previous_value == "1900" and events[0].new_value == "1901"
        assert events[0].action == CorrectionAction.EDIT

    def test_add_and_retract_events_roundtrip(self, repo):
        """The two non-edit shapes survive the DB (NULL supersedes/values map back)."""
        _seed_capture(repo)
        repo.add_assertion(_assertion(id="assertion-add", field="mother", verbatim_value="Mary"))
        repo.add_correction(_assertion(id="assertion-ret", retracted=True), supersedes_id="assertion-1")

        add = _event(
            id="ce-add",
            action=CorrectionAction.ADD,
            field="mother",
            assertion_id="assertion-add",
            supersedes_id=None,
            previous_value=None,
            new_value="Mary",
        )
        retract = _event(
            id="ce-ret",
            action=CorrectionAction.RETRACT,
            assertion_id="assertion-ret",
            supersedes_id="assertion-1",
            previous_value="1900",
            new_value=None,
        )
        repo.add_correction_event(add)
        repo.add_correction_event(retract)

        by_id = {e.id: e for e in repo.corrections_for_source("src-1")}
        assert by_id["ce-add"].supersedes_id is None and by_id["ce-add"].previous_value is None
        assert by_id["ce-ret"].new_value is None and by_id["ce-ret"].action == CorrectionAction.RETRACT

    def test_is_append_only(self, repo):
        _seed_capture(repo)
        repo.add_correction(_assertion(id="assertion-2", verbatim_value="1901"), supersedes_id="assertion-1")
        repo.add_correction_event(_event())

        with pytest.raises(sqlite3.DatabaseError):
            repo.connection.execute("UPDATE correction_event SET new_value = 'x' WHERE id = 'ce-1'")
        with pytest.raises(sqlite3.DatabaseError):
            repo.connection.execute("DELETE FROM correction_event WHERE id = 'ce-1'")

    def test_unknown_source_is_rejected(self, repo):
        """The log cannot dangle: an event for a source that was never captured fails."""
        _seed_capture(repo)
        with pytest.raises(sqlite3.DatabaseError):
            repo.add_correction_event(_event(source_id="ghost", assertion_id="assertion-1"))

    def test_unknown_produced_assertion_is_rejected(self, repo):
        """assertion_id must name a real produced row, or the log points at nothing."""
        _seed_capture(repo)
        with pytest.raises(sqlite3.DatabaseError):
            repo.add_correction_event(
                _event(action=CorrectionAction.ADD, assertion_id="ghost",
                       supersedes_id=None, previous_value=None, new_value="1901")
            )


# ---------------------------------------------------------------------------
# migration of an already-existing database
# ---------------------------------------------------------------------------


def _write_legacy_db_without_retracted(path) -> None:
    """A capsule.db from the release BEFORE `retracted` shipped.

    Removes only the `retracted` column line from the real schema, so the legacy
    DB carries the genuine triggers/indexes rather than a hand-rolled drift.
    """
    from capsule.store.repository import _SCHEMA_PATH

    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    legacy = sql.replace(
        "    retracted           INTEGER NOT NULL DEFAULT 0 CHECK (retracted IN (0, 1)),\n",
        "",
        1,
    )
    assert legacy != sql, "schema.sql no longer declares `retracted` as expected"
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(legacy)
        connection.commit()
    finally:
        connection.close()


def test_existing_db_gains_retracted_column(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` will not add a column to an existing DB; only
    the `_ADDED_COLUMNS` ALTER does. Without it, the next insert dies on
    `no such column: retracted`.
    """
    db_path = tmp_path / "capsule.db"
    _write_legacy_db_without_retracted(db_path)

    probe = sqlite3.connect(str(db_path))
    legacy_columns = {row[1] for row in probe.execute("PRAGMA table_info(assertion)")}
    probe.close()
    assert "retracted" not in legacy_columns  # the premise

    repo = Repository(db_path)
    try:
        columns = {row[1] for row in repo.connection.execute("PRAGMA table_info(assertion)")}
        assert "retracted" in columns
        # And a write to the new column round-trips through current_assertions.
        _seed_capture(repo)
        repo.add_correction(_assertion(id="assertion-2", retracted=True), supersedes_id="assertion-1")
        assert repo.current_assertions("src-1") == []
    finally:
        repo.close()
