"""Adversarial-review regression tests (Codex GPT-5.x pass, 2026-07-07).

Each test pins a hardening finding from the cross-model adversarial review:
extra-field rejection (protects the no-tree-field guarantee), model_copy
invariant-bypass caught at the write boundary, non-UTC datetime rejection,
correction-fork rejection, cross-source assertion rejection, and full
append-only trigger coverage across all six evidence tables.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from capsule.contracts.evidence import (
    ArchiveRecord,
    Assertion,
    Citation,
    EvidenceType,
    GatingStatus,
    Persona,
    Snippet,
    SnippetAnchor,
    Source,
    SourceQuality,
)
from capsule.store.repository import Repository

UTC = timezone.utc


def _archive(**kw):
    d = dict(local_snapshot="snapshot.html", sha256="a" * 64,
             captured_at=datetime(2026, 7, 7, 12, tzinfo=UTC), wayback_url=None)
    d.update(kw)
    return ArchiveRecord(**d)


def _source(**kw):
    d = dict(id="src-1", url="https://x/1", title="t", site_name="s",
             gating_status=GatingStatus.PUBLIC, copyright_flag=False,
             source_quality=SourceQuality.DERIVATIVE, archive=_archive(),
             content_hash="b" * 64)
    d.update(kw)
    return Source(**d)


def _persona(**kw):
    d = dict(id="persona-1", source_id="src-1", role="deceased", label="John")
    d.update(kw)
    return Persona(**d)


def _snippet(**kw):
    d = dict(id="snippet-1", source_id="src-1", verbatim_text="b. 1900",
             anchor=SnippetAnchor(kind="text", start=0, end=7))
    d.update(kw)
    return Snippet(**d)


def _assertion(**kw):
    d = dict(id="assertion-1", source_id="src-1", persona_id="persona-1",
             snippet_id="snippet-1", field="birth_year", verbatim_value="1900",
             normalized_value="1900", annotation=None,
             evidence_type=EvidenceType.STATED, confidence=0.9,
             supersedes_id=None, created_at=datetime(2026, 7, 7, tzinfo=UTC))
    d.update(kw)
    return Assertion(**d)


def _citation(**kw):
    d = dict(id="citation-1", source_id="src-1", reference_note="r",
             short_note="s", source_list_entry="e", access_date=date(2026, 7, 7),
             archived_url="https://web.archive.org/1")
    d.update(kw)
    return Citation(**d)


@pytest.fixture()
def repo(tmp_path):
    r = Repository(tmp_path / "capsule.db")
    yield r
    r.close()


def _populate(repo):
    repo.add_source(_source())
    repo.add_persona(_persona())
    repo.add_snippet(_snippet())
    repo.add_assertion(_assertion())


# --- extra="forbid" protects the no-tree-field guarantee -------------------

def test_BUG_persona_rejects_extra_tree_person_id():
    with pytest.raises(ValidationError):
        Persona(id="p", source_id="s", role="child", label="X", tree_person_id="T1")


def test_BUG_source_rejects_unknown_field():
    with pytest.raises(ValidationError):
        _source(unexpected="x")


# --- model_copy invariant bypass caught at the repository write boundary ----

def test_BUG_model_copy_gating_bypass_rejected_on_write(repo):
    # gated but copyright_flag still False -> invariant violated; model_copy
    # skips validation, so the repository must revalidate and reject.
    bad = _source().model_copy(update={"gating_status": GatingStatus.GATED})
    with pytest.raises(ValidationError):
        repo.add_source(bad)


# --- non-UTC datetimes rejected --------------------------------------------

def test_BUG_created_at_non_utc_offset_rejected():
    with pytest.raises(ValidationError):
        _assertion(created_at=datetime(2026, 7, 7, tzinfo=timezone(timedelta(hours=5))))


def test_BUG_captured_at_non_utc_offset_rejected():
    with pytest.raises(ValidationError):
        _archive(captured_at=datetime(2026, 7, 7, tzinfo=timezone(timedelta(hours=-5))))


# --- correction fork rejected (one correction per superseded row) ----------

def test_BUG_correction_fork_is_rejected(repo):
    _populate(repo)
    repo.add_correction(_assertion(id="assertion-2", verbatim_value="1901"),
                        supersedes_id="assertion-1")
    with pytest.raises(sqlite3.DatabaseError):
        repo.add_correction(_assertion(id="assertion-3", verbatim_value="1902"),
                            supersedes_id="assertion-1")


# --- cross-source assertion rejected (composite FK) ------------------------

def test_BUG_assertion_persona_from_other_source_rejected(repo):
    repo.add_source(_source())                    # src-1
    repo.add_source(_source(id="src-2"))          # src-2
    repo.add_snippet(_snippet())                  # snippet-1 on src-1
    repo.add_persona(_persona(id="persona-2", source_id="src-2"))
    bad = _assertion(persona_id="persona-2")      # claims src-1, persona is src-2
    with pytest.raises(sqlite3.DatabaseError):
        repo.add_assertion(bad)


# --- append-only triggers cover all six evidence tables --------------------

_UPDATE_STMTS = {
    "source": "UPDATE source SET url='x'",
    "archive_record": "UPDATE archive_record SET local_snapshot='x'",
    "snippet": "UPDATE snippet SET verbatim_text='x'",
    "persona": "UPDATE persona SET label='x'",
    "assertion": "UPDATE assertion SET verbatim_value='x'",
    "citation": "UPDATE citation SET short_note='x'",
}


@pytest.mark.parametrize("table", list(_UPDATE_STMTS))
def test_BUG_all_evidence_tables_reject_update(repo, table):
    _populate(repo)
    repo.add_citation(_citation())
    with pytest.raises(sqlite3.DatabaseError):
        repo.connection.execute(_UPDATE_STMTS[table])


@pytest.mark.parametrize(
    "table", ["source", "archive_record", "snippet", "persona", "assertion", "citation"]
)
def test_BUG_all_evidence_tables_reject_delete(repo, table):
    _populate(repo)
    repo.add_citation(_citation())
    with pytest.raises(sqlite3.DatabaseError):
        repo.connection.execute(f"DELETE FROM {table}")
