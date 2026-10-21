"""Annotate an already-written capture with a researcher note (slice 6).

Capture-*time* notes ship with `run_capture` / `run_document_capture`; this is
the *later*-annotation path — "add a note to anything already collected", even
long after the capture was sealed. A note is the user's own words, NOT evidence
(no snippet, no evidence type), so it lands in the hash-EXCLUDED `notes.json` and
the append-only `capture_note` table, never in the manifest's hashed content.

Order is deliberate: the store insert (FK-checked, transactional) runs first, so
a typo'd or un-indexed `capture_id` fails before `notes.json` is touched; then
the note is appended to `notes.json` and the human-readable index is refreshed
(best-effort — a render failure never loses a note that is already recorded).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from capsule.capture.manifest import resolve_capture_folder, write_notes_file
from capsule.collection.index import write_index
from capsule.contracts.evidence import CaptureNote
from capsule.store.repository import Repository

_logger = logging.getLogger(__name__)

#: Serializes the store-insert -> notes.json rewrite below. FastAPI runs sync
#: endpoints in a threadpool, so two concurrent `POST /note` calls could otherwise
#: interleave a read-modify-write and drop a note from the file. A single
#: process-wide lock is ample for a localhost, single-user M0 tool where note
#: appends are rare and sub-millisecond. (A separate OS process — the CLI racing
#: the running server on the *same* capture — is out of scope for M0; the DB stays
#: correct regardless, and a later reindex would reconcile notes.json from it.)
_notes_write_lock = threading.Lock()


def _source_id_from_manifest(folder: Path) -> str:
    """The capture's `source.id`, read from its manifest — the note's FK target."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    source = manifest.get("source") if isinstance(manifest, dict) else None
    source_id = source.get("id") if isinstance(source, dict) else None
    if not isinstance(source_id, str) or not source_id:
        raise ValueError(f"manifest in {folder.name} has no source.id")
    return source_id


def add_note_to_capture(
    *,
    repository: Repository,
    captures_root: Union[str, Path],
    capture_id: str,
    text: str,
    author: str = "owner",
    now: Optional[Callable[[], datetime]] = None,
    new_id: Optional[Callable[[], str]] = None,
) -> CaptureNote:
    """Append a researcher note to an existing capture, and return the note.

    Resolves `capture_id` to its folder (trust-boundary validated), reads the
    source id from its manifest, records the note in the append-only store
    (FK-validated — an unknown or mis-indexed capture fails here, before any file
    is written), appends it to the hash-excluded `notes.json`, and refreshes the
    index. Raises `ValueError` (malformed id / blank text) or `FileNotFoundError`
    (no such capture). `text` is validated by `CaptureNote` (non-blank).
    """
    now = now or (lambda: datetime.now(timezone.utc))
    new_id = new_id or (lambda: uuid.uuid4().hex)

    folder = resolve_capture_folder(captures_root, capture_id)
    source_id = _source_id_from_manifest(folder)

    note = CaptureNote(
        id=new_id(),
        source_id=source_id,
        text=text,
        author=author,
        created_at=now(),
    )
    # Store first, then rewrite notes.json as the full projection of the store —
    # all under one lock so concurrent appends can't lose a note. The FK insert
    # proves the source exists, so a mis-typed capture_id (or a folder whose
    # source was never indexed) fails here, before notes.json is touched. Because
    # the file is regenerated from the append-only store (not read-modify-written
    # from its own prior bytes), it can never drift from or lose a note.
    with _notes_write_lock:
        repository.add_capture_note(note)
        write_notes_file(folder, repository.notes_for_source(source_id))

    try:
        write_index(captures_root, now=now)
    except Exception as exc:  # noqa: BLE001 — best-effort projection, never fatal
        _logger.warning("could not regenerate captures index: %s", exc)
    return note
