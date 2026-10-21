"""Slice 6 regression: later-annotation of an already-written capture.

`add_note_to_capture` resolves a capture by id and appends a researcher note
WITHOUT re-capturing: the note lands in the append-only `capture_note` table and
the hash-EXCLUDED `notes.json`, the capture still verifies, and a mis-typed or
traversing id is refused before anything is written. The store insert runs first
(FK-checked), so annotating a capture whose source was never indexed fails
before `notes.json` is touched.

All offline — a real Repository + a real capture folder in tmp, no agent, no
network. The capture under test is built with `run_document_capture` (archive-
only, no model call) purely because it is the cheapest real capture to make.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from capsule.capture.manifest import NOTES_NAME, verify_capture
from capsule.pipeline.annotate import add_note_to_capture
from capsule.pipeline.pipeline import run_document_capture
from capsule.store.repository import Repository

_URL = "https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf"
_PDF = b"%PDF-1.4\n1 0 obj Isle au Haut Historical Society newsletter\n%%EOF"


def _ids():
    seq = iter(f"018f{i:028x}" for i in range(1, 1000))
    return lambda: next(seq)


def _fixed_now():
    return datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def _make_capture(repo: Repository, captures_root, *, note: str | None = None):
    return run_document_capture(
        _URL,
        document_bytes=_PDF,
        ext="pdf",
        media_type="application/pdf",
        repository=repo,
        captures_root=captures_root,
        note=note,
        now=_fixed_now,
        new_id=_ids(),
    )


def _notes_json(folder) -> list[dict]:
    return json.loads((folder / NOTES_NAME).read_text(encoding="utf-8"))


def test_annotate_appends_note_to_existing_capture(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        captures = tmp_path / "captures"
        outcome = _make_capture(repo, captures, note="original capture-time note")
        capture_id = outcome.capture_folder.name

        added = add_note_to_capture(
            repository=repo,
            captures_root=captures,
            capture_id=capture_id,
            text="Names the Doyle family; revisit for the 1890 entry.",
            now=_fixed_now,
            new_id=lambda: "annotate-note-0001",
        )
        assert added.source_id == outcome.source.id
        assert added.author == "owner"

        # Both notes are in the append-only store, oldest first, and in notes.json.
        stored = repo.notes_for_source(outcome.source.id)
        assert [n.text for n in stored] == [
            "original capture-time note",
            "Names the Doyle family; revisit for the 1890 entry.",
        ]
        payloads = _notes_json(outcome.capture_folder)
        assert [p["text"] for p in payloads] == [n.text for n in stored]

        # Annotating a sealed capture must not disturb its cryptographic integrity.
        assert verify_capture(outcome.capture_folder).ok
    finally:
        repo.close()


def test_annotate_capture_that_had_no_prior_note(tmp_path) -> None:
    """No capture-time note ⇒ no notes.json until the first later note is added."""
    repo = Repository(tmp_path / "capsule.db")
    try:
        captures = tmp_path / "captures"
        outcome = _make_capture(repo, captures)  # no note
        assert not (outcome.capture_folder / NOTES_NAME).exists()

        add_note_to_capture(
            repository=repo,
            captures_root=captures,
            capture_id=outcome.capture_folder.name,
            text="Why I kept this.",
            now=_fixed_now,
            new_id=lambda: "annotate-note-0001",
        )
        payloads = _notes_json(outcome.capture_folder)
        assert [p["text"] for p in payloads] == ["Why I kept this."]
        assert len(repo.notes_for_source(outcome.source.id)) == 1
        assert verify_capture(outcome.capture_folder).ok
    finally:
        repo.close()


def test_second_annotation_appends_not_overwrites(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        captures = tmp_path / "captures"
        outcome = _make_capture(repo, captures)
        cid = outcome.capture_folder.name
        for i, text in enumerate(("first later note", "second later note"), start=1):
            add_note_to_capture(
                repository=repo,
                captures_root=captures,
                capture_id=cid,
                text=text,
                now=_fixed_now,
                new_id=lambda i=i: f"annotate-note-{i:04d}",
            )
        payloads = _notes_json(outcome.capture_folder)
        assert [p["text"] for p in payloads] == ["first later note", "second later note"]
        assert [p["id"] for p in payloads] == ["annotate-note-0001", "annotate-note-0002"]
    finally:
        repo.close()


def test_BUG_annotate_unknown_capture_id_raises(tmp_path) -> None:
    """A well-formed but non-existent id names no capture — FileNotFoundError."""
    repo = Repository(tmp_path / "capsule.db")
    try:
        with pytest.raises(FileNotFoundError):
            add_note_to_capture(
                repository=repo,
                captures_root=tmp_path / "captures",
                capture_id="20260711-isleauhauthistory-deadbeef",
                text="note",
            )
    finally:
        repo.close()


@pytest.mark.parametrize(
    "bad_id",
    [
        "../evil",
        "not/a/capture",
        "..",
        "bad id",
        "",
        "20260711-isleauhauthistory-abcdef\n",  # trailing newline: `\Z`, not `$`
    ],
)
def test_BUG_annotate_malformed_or_traversing_id_refused(tmp_path, bad_id) -> None:
    """A malformed/traversing id is refused before any filesystem or DB write.

    Includes a trailing-newline id: Python's `$` matches before a final `\\n`, so
    the id regex must anchor with `\\Z` or an `"<id>\\n"` slips past the gate.
    """
    repo = Repository(tmp_path / "capsule.db")
    try:
        with pytest.raises(ValueError):
            add_note_to_capture(
                repository=repo,
                captures_root=tmp_path / "captures",
                capture_id=bad_id,
                text="note",
            )
    finally:
        repo.close()


def test_BUG_annotate_unindexed_source_leaves_notes_json_untouched(tmp_path) -> None:
    """Store-first ordering: if the source isn't in the DB, the FK insert fails
    and notes.json is never modified — no note is half-written to disk."""
    captures = tmp_path / "captures"
    repo_a = Repository(tmp_path / "a.db")
    try:
        outcome = _make_capture(repo_a, captures, note="original capture-time note")
    finally:
        repo_a.close()

    before = (outcome.capture_folder / NOTES_NAME).read_bytes()
    repo_b = Repository(tmp_path / "b.db")  # a fresh index that never saw this source
    try:
        with pytest.raises(sqlite3.Error):
            add_note_to_capture(
                repository=repo_b,
                captures_root=captures,
                capture_id=outcome.capture_folder.name,
                text="should not be written",
                new_id=lambda: "annotate-note-0001",
            )
    finally:
        repo_b.close()
    # notes.json is byte-identical: the failed insert did not touch it.
    assert (outcome.capture_folder / NOTES_NAME).read_bytes() == before


def test_BUG_concurrent_annotations_lose_no_note(tmp_path) -> None:
    """Concurrent `add_note_to_capture` calls (as the threaded backend runs them)
    must not drop a note from notes.json. Each thread opens its own Repository —
    exactly as each FastAPI request does — and notes.json is a projection of the
    append-only store, so every committed note must appear in both the file and
    the store, never lost to a read-modify-write race."""
    captures = tmp_path / "captures"
    db = tmp_path / "capsule.db"
    repo = Repository(db)
    try:
        outcome = _make_capture(repo, captures)  # no capture-time note
    finally:
        repo.close()
    cid = outcome.capture_folder.name
    source_id = outcome.source.id

    n = 12
    ready = threading.Barrier(n)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        r = Repository(db)
        try:
            ready.wait()  # release all threads together to maximize contention
            add_note_to_capture(
                repository=r,
                captures_root=captures,
                capture_id=cid,
                text=f"concurrent note {i}",
                new_id=lambda i=i: f"concurrent-note-{i:04d}",
            )
        except BaseException as exc:  # noqa: BLE001 — surface any thread failure
            errors.append(exc)
        finally:
            r.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    payloads = _notes_json(outcome.capture_folder)
    assert len(payloads) == n  # no note dropped from the file
    assert {p["id"] for p in payloads} == {f"concurrent-note-{i:04d}" for i in range(n)}
    repo2 = Repository(db)
    try:
        assert len(repo2.notes_for_source(source_id)) == n  # file == store
    finally:
        repo2.close()


def test_BUG_annotate_blank_text_rejected(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        captures = tmp_path / "captures"
        outcome = _make_capture(repo, captures)
        with pytest.raises(ValueError):
            add_note_to_capture(
                repository=repo,
                captures_root=captures,
                capture_id=outcome.capture_folder.name,
                text="   ",
            )
        # Nothing recorded for a rejected note.
        assert repo.notes_for_source(outcome.source.id) == []
    finally:
        repo.close()
