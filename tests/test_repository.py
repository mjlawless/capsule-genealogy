"""Tests for capsule/store/repository.py (thin repository over schema.sql).

Written FIRST (RED) — capsule/store/repository.py and capsule/store/schema.sql
do not exist yet when this file is first run.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest


def _archive_record(**overrides):
    from capsule.contracts.evidence import ArchiveRecord

    defaults = dict(
        local_snapshot="snapshot.html",
        sha256="a" * 64,
        captured_at=datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc),
        wayback_url=None,
    )
    defaults.update(overrides)
    return ArchiveRecord(**defaults)


def _source(**overrides):
    from capsule.contracts.evidence import GatingStatus, Source, SourceQuality

    defaults = dict(
        id="src-1",
        url="https://www.findagrave.com/memorial/1",
        title="John Murphy",
        site_name="Find a Grave",
        gating_status=GatingStatus.PUBLIC,
        copyright_flag=False,
        source_quality=SourceQuality.DERIVATIVE,
        archive=_archive_record(),
        content_hash="b" * 64,
    )
    defaults.update(overrides)
    return Source(**defaults)


def _persona(**overrides):
    from capsule.contracts.evidence import Persona

    defaults = dict(id="persona-1", source_id="src-1", role="deceased", label="John Murphy")
    defaults.update(overrides)
    return Persona(**defaults)


def _snippet(**overrides):
    from capsule.contracts.evidence import Snippet, SnippetAnchor

    defaults = dict(
        id="snippet-1",
        source_id="src-1",
        verbatim_text="b. 1900 d. 1965",
        anchor=SnippetAnchor(kind="text", start=0, end=15, media_filename=None, bbox=None),
    )
    defaults.update(overrides)
    return Snippet(**defaults)


def _assertion(**overrides):
    from capsule.contracts.evidence import Assertion, EvidenceType

    defaults = dict(
        id="assertion-1",
        source_id="src-1",
        persona_id="persona-1",
        snippet_id="snippet-1",
        field="birth_year",
        verbatim_value="1900",
        normalized_value="1900",
        annotation=None,
        evidence_type=EvidenceType.STATED,
        confidence=0.9,
        supersedes_id=None,
        created_at=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Assertion(**defaults)


def _citation(**overrides):
    from capsule.contracts.evidence import Citation

    defaults = dict(
        id="citation-1",
        source_id="src-1",
        reference_note="Full reference note.",
        short_note="Short note.",
        source_list_entry="Source list entry.",
        access_date=date(2026, 7, 7),
        archived_url="https://web.archive.org/web/1/x",
    )
    defaults.update(overrides)
    return Citation(**defaults)


@pytest.fixture()
def repo(tmp_path):
    from capsule.store.repository import Repository

    repository = Repository(tmp_path / "capsule.db")
    yield repository
    repository.close()


def _write_legacy_db(path) -> None:
    """Create a `capsule.db` as the release BEFORE `normalized_field` shipped.

    Uses the real schema.sql with that one column line removed, so the legacy DB
    carries the genuine triggers, indexes, and composite FKs rather than a
    hand-rolled approximation that could drift from the shipped schema.
    """
    from capsule.store.repository import _SCHEMA_PATH

    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    legacy = sql.replace("    normalized_field    TEXT,\n", "", 1)
    assert legacy != sql, "schema.sql no longer declares normalized_field as expected"
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(legacy)
        connection.commit()
    finally:
        connection.close()


class TestSchemaInitialization:
    def test_schema_creates_all_tables(self, repo):
        cursor = repo.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        expected = {"source", "archive_record", "snippet", "persona", "assertion", "citation"}
        assert expected.issubset(tables)

    def test_foreign_keys_pragma_is_on(self, repo):
        cursor = repo.connection.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1

    def test_BUG_existing_db_gains_normalized_field_column(self, tmp_path):
        """Regression: `CREATE TABLE IF NOT EXISTS` will NOT alter an existing DB.

        A column added to schema.sql reaches a fresh database and is silently
        skipped for one that already exists — SQLite sees the table and moves on.
        The column never appears and the next capture dies on `no such column`.
        Only the guarded `ALTER TABLE ... ADD COLUMN` in `_migrate` closes that gap.
        """
        from capsule.store.repository import Repository

        db_path = tmp_path / "capsule.db"
        _write_legacy_db(db_path)

        probe = sqlite3.connect(str(db_path))
        legacy_columns = {row[1] for row in probe.execute("PRAGMA table_info(assertion)")}
        probe.close()
        assert "normalized_field" not in legacy_columns  # the premise of this test

        repository = Repository(db_path)
        try:
            cursor = repository.connection.execute("PRAGMA table_info(assertion)")
            assert "normalized_field" in {row[1] for row in cursor.fetchall()}
            # And the migrated DB actually accepts a write to the new column.
            repository.add_source(_source())
            repository.add_persona(_persona())
            repository.add_snippet(_snippet())
            repository.add_assertion(_assertion(normalized_field="record_type"))
            (stored,) = repository.current_assertions("src-1")
            assert stored.normalized_field == "record_type"
        finally:
            repository.close()

    def test_migrating_an_already_migrated_db_is_a_no_op(self, tmp_path):
        """`_migrate` runs on every open, so it must be idempotent."""
        from capsule.store.repository import Repository

        db_path = tmp_path / "capsule.db"
        for _ in range(3):
            Repository(db_path).close()
        repository = Repository(db_path)
        try:
            cursor = repository.connection.execute("PRAGMA table_info(assertion)")
            columns = [row[1] for row in cursor.fetchall()]
            assert columns.count("normalized_field") == 1
        finally:
            repository.close()

    def test_migration_preserves_rows_written_before_the_column_existed(self, tmp_path):
        """ADD COLUMN must not rewrite evidence — the append-only triggers forbid it.

        A row written by the old release must survive the migration untouched and
        read back with `normalized_field` absent, not blank.
        """
        from capsule.store.repository import Repository

        db_path = tmp_path / "capsule.db"
        _write_legacy_db(db_path)

        source, persona, snippet = _source(), _persona(), _snippet()
        legacy = sqlite3.connect(str(db_path))
        try:
            legacy.execute("PRAGMA foreign_keys = ON")
            legacy.execute(
                "INSERT INTO source (id, url, gating_status, copyright_flag, "
                "source_quality, content_hash, spec_version) VALUES (?,?,?,?,?,?,?)",
                (source.id, source.url, "public", 0, "derivative",
                 source.content_hash, "capture-folder/v1"),
            )
            legacy.execute(
                "INSERT INTO archive_record (source_id, local_snapshot, sha256, captured_at) "
                "VALUES (?,?,?,?)",
                (source.id, "snapshot.html", source.archive.sha256,
                 "2026-07-07T12:00:00+00:00"),
            )
            legacy.execute(
                "INSERT INTO persona (id, source_id, role, label) VALUES (?,?,?,?)",
                (persona.id, persona.source_id, persona.role, persona.label),
            )
            legacy.execute(
                "INSERT INTO snippet (id, source_id, verbatim_text, anchor_kind) VALUES (?,?,?,?)",
                (snippet.id, snippet.source_id, snippet.verbatim_text, "text"),
            )
            legacy.execute(
                "INSERT INTO assertion (id, source_id, persona_id, snippet_id, field, "
                "verbatim_value, evidence_type, created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("assertion-legacy", "src-1", "persona-1", "snippet-1", "birth_year",
                 "1900", "stated", "2026-07-07T00:00:00+00:00"),
            )
            legacy.commit()
        finally:
            legacy.close()

        repository = Repository(db_path)
        try:
            (stored,) = repository.current_assertions("src-1")
            assert stored.id == "assertion-legacy"
            assert stored.verbatim_value == "1900"
            assert stored.normalized_field is None
        finally:
            repository.close()


class TestRoundTrip:
    """MEDIUM/HIGH-3 code review finding: these must read back from the DB
    (get_*), not just assert identity against the object the add_* method
    happened to return -- that was tautological and never proved
    column/param ordering or bbox/date serialization actually round-trip.
    """

    def test_source_persona_snippet_assertion_round_trip(self, repo):
        source = _source()
        repo.add_source(source)

        persona = _persona()
        repo.add_persona(persona)

        snippet = _snippet()
        repo.add_snippet(snippet)

        assertion = _assertion()
        repo.add_assertion(assertion)

        current = repo.current_assertions("src-1")
        assert len(current) == 1
        assert current[0] == assertion

    def test_BUG_get_source_reads_from_db_not_identity(self, repo):
        source = _source()
        repo.add_source(source)

        fetched = repo.get_source("src-1")
        assert fetched == source
        assert fetched is not source

    def test_BUG_get_persona_reads_from_db_not_identity(self, repo):
        repo.add_source(_source())
        persona = _persona()
        repo.add_persona(persona)

        fetched = repo.get_persona("persona-1")
        assert fetched == persona
        assert fetched is not persona

    def test_BUG_get_snippet_reads_from_db_not_identity(self, repo):
        repo.add_source(_source())
        snippet = _snippet(
            anchor=_snippet().anchor.model_copy(update={"bbox": (1, 2, 3, 4)})
        )
        repo.add_snippet(snippet)

        fetched = repo.get_snippet("snippet-1")
        assert fetched == snippet
        assert fetched is not snippet
        assert fetched.anchor.bbox == (1, 2, 3, 4)

    def test_BUG_get_citation_reads_from_db_not_identity(self, repo):
        repo.add_source(_source())
        citation = _citation()
        repo.add_citation(citation)

        fetched = repo.get_citation("citation-1")
        assert fetched == citation
        assert fetched is not citation

    def test_get_source_raises_lookup_error_when_missing(self, repo):
        with pytest.raises(LookupError):
            repo.get_source("does-not-exist")


class TestAppendOnly:
    def test_update_on_assertion_is_rejected(self, repo):
        repo.add_source(_source())
        repo.add_persona(_persona())
        repo.add_snippet(_snippet())
        repo.add_assertion(_assertion())

        with pytest.raises(sqlite3.DatabaseError):
            repo.connection.execute(
                "UPDATE assertion SET verbatim_value = 'changed' WHERE id = 'assertion-1'"
            )

    def test_delete_on_assertion_is_rejected(self, repo):
        repo.add_source(_source())
        repo.add_persona(_persona())
        repo.add_snippet(_snippet())
        repo.add_assertion(_assertion())

        with pytest.raises(sqlite3.DatabaseError):
            repo.connection.execute("DELETE FROM assertion WHERE id = 'assertion-1'")

    def test_update_on_source_is_rejected(self, repo):
        repo.add_source(_source())

        with pytest.raises(sqlite3.DatabaseError):
            repo.connection.execute("UPDATE source SET url = 'https://example.com' WHERE id = 'src-1'")


class TestForeignKeyEnforcement:
    def test_assertion_with_nonexistent_persona_is_rejected(self, repo):
        repo.add_source(_source())
        repo.add_snippet(_snippet())

        orphan = _assertion(persona_id="does-not-exist")
        with pytest.raises(sqlite3.DatabaseError):
            repo.add_assertion(orphan)


class TestCorrections:
    def test_correction_supersedes_original_and_current_assertions_returns_latest(self, repo):
        repo.add_source(_source())
        repo.add_persona(_persona())
        repo.add_snippet(_snippet())

        original = _assertion()
        repo.add_assertion(original)

        correction = _assertion(
            id="assertion-2",
            verbatim_value="1901",
            normalized_value="1901",
        )
        repo.add_correction(correction, supersedes_id="assertion-1")

        current = repo.current_assertions("src-1")
        assert len(current) == 1
        assert current[0].id == "assertion-2"
        assert current[0].supersedes_id == "assertion-1"
        assert current[0].verbatim_value == "1901"

    def test_BUG_correction_chain_returns_only_final_row(self, repo):
        """A >1-deep chain (original -> correction -> correction) must
        collapse to just the last, un-superseded row."""
        repo.add_source(_source())
        repo.add_persona(_persona())
        repo.add_snippet(_snippet())

        repo.add_assertion(_assertion())  # assertion-1 (original)
        repo.add_correction(
            _assertion(id="assertion-2", verbatim_value="1901", normalized_value="1901"),
            supersedes_id="assertion-1",
        )
        repo.add_correction(
            _assertion(id="assertion-3", verbatim_value="1902", normalized_value="1902"),
            supersedes_id="assertion-2",
        )

        current = repo.current_assertions("src-1")
        assert len(current) == 1
        assert current[0].id == "assertion-3"
        assert current[0].verbatim_value == "1902"

    def test_BUG_add_correction_does_not_mutate_caller(self, repo):
        """add_correction must build a copy, never mutate the caller's
        Assertion in place (models are frozen now, but this proves the
        repository's own contract too, independent of that enforcement)."""
        repo.add_source(_source())
        repo.add_persona(_persona())
        repo.add_snippet(_snippet())
        repo.add_assertion(_assertion())

        caller_assertion = _assertion(id="assertion-2", verbatim_value="1901")
        assert caller_assertion.supersedes_id is None

        repo.add_correction(caller_assertion, supersedes_id="assertion-1")

        assert caller_assertion.supersedes_id is None


class TestConnectionLifecycle:
    def test_BUG_schema_init_failure_closes_connection(self, tmp_path, monkeypatch):
        """MEDIUM-5 code review finding: a schema-init failure must not
        leak an open sqlite3 connection."""
        from capsule.store import repository as repository_module

        bad_schema = tmp_path / "bad_schema.sql"
        bad_schema.write_text("THIS IS NOT VALID SQL AT ALL;", encoding="utf-8")
        monkeypatch.setattr(repository_module, "_SCHEMA_PATH", bad_schema)

        opened_connections: list[sqlite3.Connection] = []
        original_connect = sqlite3.connect

        def spying_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            opened_connections.append(connection)
            return connection

        monkeypatch.setattr(repository_module.sqlite3, "connect", spying_connect)

        with pytest.raises(sqlite3.DatabaseError):
            repository_module.Repository(tmp_path / "broken.db")

        assert len(opened_connections) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            opened_connections[0].execute("SELECT 1")


class TestTransactionalWrites:
    def test_BUG_add_source_partial_failure_rolls_back(self, repo):
        """MEDIUM-6 code review finding: add_source performs two INSERTs
        (source, archive_record); a failure on the second must not leave
        the first dangling in an uncommitted/partial state."""
        # Pre-seed archive_record with a colliding source_id (FK disabled
        # only for this seed insert) so add_source's *second* INSERT fails
        # while its *first* INSERT (into `source`) would otherwise succeed.
        repo.connection.execute("PRAGMA foreign_keys = OFF")
        repo.connection.execute(
            """
            INSERT INTO archive_record
                (source_id, local_snapshot, sha256, captured_at, wayback_url)
            VALUES ('rollback-1', 'x.html', ?, '2026-01-01T00:00:00+00:00', NULL)
            """,
            ("x" * 64,),
        )
        repo.connection.commit()
        repo.connection.execute("PRAGMA foreign_keys = ON")

        source = _source(id="rollback-1")
        with pytest.raises(sqlite3.DatabaseError):
            repo.add_source(source)

        cursor = repo.connection.execute(
            "SELECT COUNT(*) FROM source WHERE id = ?", ("rollback-1",)
        )
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# coverage_claim — what an index says it holds (P2)
# ---------------------------------------------------------------------------


def _coverage(**overrides):
    from capsule.contracts.evidence import CoverageClaim

    defaults = dict(
        id="cov-1",
        source_id="src-1",
        snippet_id="snippet-1",
        place="Kilmore",
        record_type="R.C. Baptisms",
        year_start=1845,
        year_end=1880,
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return CoverageClaim(**defaults)


def _seed_source(repo):
    repo.add_source(_source())
    repo.add_snippet(_snippet())


def test_coverage_claim_round_trips(repo):
    _seed_source(repo)
    repo.add_coverage_claim(_coverage())

    (claim,) = repo.coverage_claims_for_source("src-1")
    assert claim.place == "Kilmore"
    assert claim.record_type == "R.C. Baptisms"
    assert (claim.year_start, claim.year_end) == (1845, 1880)
    assert claim.snippet_id == "snippet-1"  # the verbatim text is always one hop away


def test_coverage_for_answers_was_this_year_covered(repo):
    """The question P2 exists to answer, and the reason a negative search means anything."""
    _seed_source(repo)
    repo.add_coverage_claim(_coverage(id="cov-1", year_start=1845, year_end=1880))

    assert repo.coverage_for(place="kilmore", record_type="baptism", year=1852)
    # Searched 1901, index stops at 1880: the absence proves NOTHING.
    assert repo.coverage_for(place="kilmore", record_type="baptism", year=1901) == []


def test_coverage_for_matches_the_indexs_own_vocabulary_case_insensitively(repo):
    """A researcher types "kilmore"; the index printed "Co. Cavan / Kilmore"."""
    _seed_source(repo)
    repo.add_coverage_claim(_coverage(place="Co. Cavan / Kilmore", record_type="R.C. Baptisms"))

    assert repo.coverage_for(place="kilmore", year=1852)
    assert repo.coverage_for(record_type="baptism", year=1852)


def test_BUG_an_unparseable_range_never_reports_coverage(repo):
    """Regression: a claim whose years we could not read must not answer "covered".

    Answering confidently and wrongly is the whole bug class — an absence read as
    meaningful when the index never covered those years. Better to surface nothing
    for a year query than to surface a claim that cannot support it.
    """
    _seed_source(repo)
    repo.add_coverage_claim(_coverage(year_start=None, year_end=None))

    assert repo.coverage_for(place="kilmore", year=1852) == []
    # ...but it is still ON RECORD, and a year-less query surfaces it for a human.
    assert len(repo.coverage_for(place="kilmore")) == 1


def test_coverage_claims_are_indexed_atomically_with_the_capture(repo):
    """A half-indexed coverage table would answer "was 1852 covered?" with a wrong No."""
    _seed_source(repo)
    import sqlite3

    import pytest as _pytest

    good = _coverage(id="cov-a")
    orphan = _coverage(id="cov-b", snippet_id="no-such-snippet")  # violates the composite FK

    with _pytest.raises(sqlite3.IntegrityError):
        repo.add_capture(
            source=_source(id="src-2", url="https://x.example/2"),
            citation=_citation(id="citation-2", source_id="src-2"),
            personas=[],
            snippets=[],
            assertions=[],
            coverage=[good, orphan],
        )
    # Neither claim landed: all rows or none.
    assert repo.coverage_for(place="kilmore") == []
