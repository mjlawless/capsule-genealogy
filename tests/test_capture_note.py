"""Slice 2 regressions: the researcher note layer + document ArchiveRecord.

Notes are the user's annotation layer — append-only, source-linked, and NOT
evidence (no snippet, no evidence type). These tests pin: the model guards, the
repository round-trip and ordering, the append-only DB triggers, threading notes
through `add_capture`, and that a `document` ArchiveRecord (kind + media_type)
survives a store round-trip.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from capsule.contracts.evidence import (
    ArchiveKind,
    ArchiveRecord,
    CaptureNote,
    Citation,
    GatingStatus,
    Source,
    SourceQuality,
)
from capsule.store.repository import Repository

_UTC = timezone.utc


def _now() -> datetime:
    return datetime(2026, 7, 11, 12, 0, 0, tzinfo=_UTC)


def _source(
    source_id: str = "018f0000000000000000000000000001", *, kind=ArchiveKind.SNAPSHOT
) -> Source:
    local = "snapshot.html" if kind == ArchiveKind.SNAPSHOT else "document.pdf"
    media_type = None if kind == ArchiveKind.SNAPSHOT else "application/pdf"
    return Source(
        id=source_id,
        url="https://example.org/thing",
        gating_status=GatingStatus.PUBLIC,
        copyright_flag=False,
        source_quality=SourceQuality.DERIVATIVE,
        archive=ArchiveRecord(
            local_snapshot=local,
            sha256="a" * 64,
            captured_at=_now(),
            kind=kind,
            media_type=media_type,
        ),
        content_hash="b" * 64,
    )


def _citation(source_id: str) -> Citation:
    return Citation(
        id=source_id[:-1] + "c",
        source_id=source_id,
        reference_note="ref",
        short_note="short",
        source_list_entry="list",
        access_date=_now().date(),
        archived_url="https://example.org/thing",
    )


def _note(source_id: str, text: str, *, note_id: str, at: datetime) -> CaptureNote:
    return CaptureNote(id=note_id, source_id=source_id, text=text, created_at=at)


def test_capture_note_rejects_blank_text() -> None:
    with pytest.raises(ValueError):
        CaptureNote(id="n1", source_id="s1", text="   ", created_at=_now())


def test_capture_note_requires_utc_created_at() -> None:
    with pytest.raises(ValueError):
        CaptureNote(
            id="n1",
            source_id="s1",
            text="why I kept this",
            created_at=datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone(timedelta(hours=5))),
        )


def test_note_roundtrip_and_ordering(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        src = _source()
        repo.add_source(src)
        later = _note(src.id, "second", note_id="n2", at=_now() + timedelta(minutes=5))
        earlier = _note(src.id, "first", note_id="n1", at=_now())
        repo.add_capture_note(later)
        repo.add_capture_note(earlier)
        notes = repo.notes_for_source(src.id)
        assert [n.text for n in notes] == ["first", "second"]  # oldest first
        assert notes[0].author == "owner"
    finally:
        repo.close()


def test_capture_note_is_append_only(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        src = _source()
        repo.add_source(src)
        repo.add_capture_note(_note(src.id, "keep", note_id="n1", at=_now()))
        with pytest.raises(Exception):
            repo.connection.execute("UPDATE capture_note SET text = 'x' WHERE id = 'n1'")
        with pytest.raises(Exception):
            repo.connection.execute("DELETE FROM capture_note WHERE id = 'n1'")
    finally:
        repo.close()


def test_add_capture_threads_notes(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        src = _source()
        note = _note(src.id, "collected because it names Mary", note_id="n1", at=_now())
        repo.add_capture(
            source=src,
            citation=_citation(src.id),
            personas=[],
            snippets=[],
            assertions=[],
            notes=[note],
        )
        assert [n.text for n in repo.notes_for_source(src.id)] == [
            "collected because it names Mary"
        ]
    finally:
        repo.close()


def test_document_archive_roundtrip(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        src = _source(kind=ArchiveKind.DOCUMENT)
        repo.add_source(src)
        got = repo.get_source(src.id)
        assert got.archive.kind == ArchiveKind.DOCUMENT
        assert got.archive.media_type == "application/pdf"
        assert got.archive.local_snapshot == "document.pdf"
    finally:
        repo.close()


def test_snapshot_archive_defaults_roundtrip(tmp_path) -> None:
    """An HTML capture round-trips with kind=snapshot and no media_type."""
    repo = Repository(tmp_path / "capsule.db")
    try:
        src = _source()
        repo.add_source(src)
        got = repo.get_source(src.id)
        assert got.archive.kind == ArchiveKind.SNAPSHOT
        assert got.archive.media_type is None
    finally:
        repo.close()
