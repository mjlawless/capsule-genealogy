"""Slice 9 regression: recoverable trash (delete a capture).

`trash_capture` MOVES a capture folder to `captures/_trash/` so it leaves the
index, while the append-only DB evidence rows are retained (they cannot be
deleted by design). A malformed/traversing id is refused; a re-trash of the same
id is suffixed, never overwriting an earlier trashed copy. All offline.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from capsule.collection.index import INDEX_FILENAME
from capsule.pipeline.pipeline import run_document_capture
from capsule.pipeline.trash import TRASH_DIRNAME, trash_capture
from capsule.store.repository import Repository

_URL = "https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf"
_PDF = b"%PDF-1.4 fake\n%%EOF"


def _fixed_now():
    return datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ids():
    seq = iter(f"018f{i:028x}" for i in range(1, 1000))
    return lambda: next(seq)


def _make(repo: Repository, captures):
    return run_document_capture(
        _URL,
        document_bytes=_PDF,
        ext="pdf",
        media_type="application/pdf",
        repository=repo,
        captures_root=captures,
        now=_fixed_now,
        new_id=_ids(),
    )


def test_trash_moves_folder_out_of_collection_and_retains_db(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        captures = tmp_path / "captures"
        outcome = _make(repo, captures)
        cid = outcome.capture_folder.name
        source_id = outcome.source.id

        dest = trash_capture(captures, cid, now=_fixed_now)

        # The folder is moved into _trash/, contents intact.
        assert not outcome.capture_folder.exists()
        assert dest == captures / TRASH_DIRNAME / cid
        assert (dest / "manifest.json").is_file()
        assert (dest / "document.pdf").read_bytes() == _PDF

        # It is gone from the regenerated index.
        index_html = (captures / INDEX_FILENAME).read_text(encoding="utf-8")
        assert cid not in index_html

        # Append-only: the evidence rows are retained (delete never touches the DB).
        assert repo.get_source(source_id).id == source_id
    finally:
        repo.close()


def test_trash_unknown_id_raises_filenotfound(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        trash_capture(tmp_path / "captures", "20260711-isleauhauthistory-deadbeef")


@pytest.mark.parametrize("bad_id", ["../evil", "..", "not/a/capture", "bad id", ""])
def test_trash_rejects_malformed_id(tmp_path, bad_id) -> None:
    (tmp_path / "captures").mkdir()
    with pytest.raises(ValueError):
        trash_capture(tmp_path / "captures", bad_id)


def test_trash_twice_suffixes_and_never_overwrites(tmp_path) -> None:
    captures = tmp_path / "captures"
    # First capture + trash.
    repo_a = Repository(tmp_path / "a.db")
    try:
        cid = _make(repo_a, captures).capture_folder.name
    finally:
        repo_a.close()
    first = trash_capture(captures, cid, now=_fixed_now)
    assert first == captures / TRASH_DIRNAME / cid

    # Re-capture the SAME id (fresh index db so the source PK is free), trash again.
    repo_b = Repository(tmp_path / "b.db")
    try:
        assert _make(repo_b, captures).capture_folder.name == cid  # same id
    finally:
        repo_b.close()
    second = trash_capture(captures, cid, now=_fixed_now)

    # The earlier trashed copy is preserved; the second lands beside it, suffixed.
    assert second != first
    assert first.is_dir() and second.is_dir()
    assert second.name.startswith(cid + ".")
