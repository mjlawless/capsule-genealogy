"""Mark a capture as superseded by another, or reinstate it (REQ-011).

Capsule is append-only, so a bad or incomplete capture is never edited or deleted. Trash
moves a folder out of the index but says nothing about what replaced it. This records the
replacement as a LIFECYCLE event on the old capture (owner ruling 2026-10-21): "cite that one
instead". It links two sources and says nothing about the people in them, so it can never
merge identities, and a mistake is undone by a later `reinstated` event, never by an edit.

The store is the authority; the old capture's hash-excluded `lifecycle.json` is its
projection, so the folder-sourced index can show the state with no database. The check, the
append and the sidecar write all run under SQLite's write lock (`Repository.write_lock`), which
serializes every process on the database (the CLI and the server), and a failed sidecar write
rolls the append back. A file and a database cannot commit together, though, so after ANY
failure the sidecar is re-synced from what the store committed, and every "already" / "not
superseded" answer re-syncs first: asking again always brings the folder back in line.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from capsule.capture.manifest import (
    LIFECYCLE_NAME,
    resolve_capture_folder,
    write_lifecycle_file,
)
from capsule.collection.index import write_index
from capsule.store.repository import Repository

_logger = logging.getLogger(__name__)

#: Serializes this process; `Repository.write_lock()` serializes every process.
_lifecycle_write_lock = threading.Lock()

#: Longest supersession chain followed when checking for a cycle. A real chain is one or two
#: links; anything near this is already a loop.
_MAX_CHAIN = 100


def _source_id(folder: Path) -> str:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    source = manifest.get("source") if isinstance(manifest, dict) else None
    source_id = source.get("id") if isinstance(source, dict) else None
    if not isinstance(source_id, str) or not source_id:
        raise ValueError(f"manifest in {folder.name} has no source.id")
    return source_id


def _capture_ids_of(captures_root: Path, wanted: set[str]) -> dict[str, str]:
    """Folder names for the given source ids, found by reading manifests."""
    found: dict[str, str] = {}
    for manifest in captures_root.glob("*/manifest.json"):
        if not wanted - set(found):
            break
        try:
            source = json.loads(manifest.read_text(encoding="utf-8")).get("source")
        except (OSError, ValueError, AttributeError):
            continue
        sid = source.get("id") if isinstance(source, dict) else None
        if sid in wanted:
            found[sid] = manifest.parent.name
    return found


class LifecycleConflict(ValueError):
    """The folder's `lifecycle.json` holds events the database does not.

    The store is the authority, but an OLDER store (a restored backup) or another collection's
    store is missing history rather than contradicting it, and overwriting the file would
    destroy that history for good. So it is kept, and nothing about the capture changes until
    a person decides which history is right (capture-folder-v2 §6).
    """


def _sidecar_ids(folder: Path) -> set[str]:
    try:
        rows = json.loads((folder / LIFECYCLE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(rows, list):
        return set()
    return {r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)}


def _project(
    repository: Repository,
    captures_root: Path,
    folder: Path,
    source_id: str,
    *,
    discard: frozenset[str] = frozenset(),
    force: bool = False,
) -> None:
    """Regenerate the capture's `lifecycle.json` from the store, naming each replacement's folder.

    Refuses (`LifecycleConflict`) when the sidecar holds an event id the store lacks, other
    than the ids in `discard` (a request's own event that just rolled back). With no events
    in the store and no conflict, a leftover sidecar is removed, but only when this store
    holds the source itself.
    """
    events = repository.lifecycle_for_source(source_id)
    foreign = _sidecar_ids(folder) - {e["id"] for e in events} - discard
    if foreign and not force:
        raise LifecycleConflict(
            f"{folder.name}/{LIFECYCLE_NAME} holds {len(foreign)} event(s) the database "
            "does not; it was kept. If the database is the current one (for example after an "
            f"interrupted command), run `capsule supersede {folder.name} --resync` to rewrite "
            "the file from it; if the database was restored from an older backup, keep the file."
        )
    if not events:
        if repository.connection.execute(
            "SELECT 1 FROM source WHERE id = ?", (source_id,)
        ).fetchone():
            (folder / LIFECYCLE_NAME).unlink(missing_ok=True)
        return
    names = _capture_ids_of(
        captures_root, {e["superseded_by"] for e in events if e["superseded_by"]}
    )
    projected = []
    for event in events:
        row = dict(event)
        if event["superseded_by"]:
            row["superseded_by_capture_id"] = names.get(event["superseded_by"])
        projected.append(row)
    write_lifecycle_file(folder, projected)


def _resync(
    repository: Repository, root: Path, folder: Path, source_id: str, own_event: str
) -> None:
    """Best-effort: put the sidecar back in line with what the store COMMITTED.

    Under both locks again, so a newer event committed by another process cannot be
    overwritten by this stale read (Codex review).
    """
    try:
        with _lifecycle_write_lock, repository.write_lock():
            _project(repository, root, folder, source_id, discard=frozenset({own_event}))
    except Exception as exc:  # noqa: BLE001 — the caller's own failure is what it reports
        _logger.warning("could not re-sync %s/%s: %s", folder.name, LIFECYCLE_NAME, exc)


def _event(source_id: str, kind: str, superseded_by: Optional[str], reason: Optional[str],
           actor: str, new_id: Callable[[], str], now: Callable[[], datetime]) -> dict:
    return {
        "event_id": new_id(),
        "source_id": source_id,
        "event": kind,
        "superseded_by": superseded_by,
        "reason": (reason or "").strip() or None,
        "actor": actor,
        "created_at": now(),
    }


def _check_supersession(
    repository: Repository, old_source: str, new_source: str, capture_id: str, by: str
) -> None:
    """Refuse a repeat, or a supersession that would close a loop (no current capture)."""
    if repository.superseded_by(old_source) == new_source:
        raise ValueError(f"{capture_id} is already superseded by {by}")
    cursor: Optional[str] = new_source
    for _ in range(_MAX_CHAIN):
        cursor = repository.superseded_by(cursor) if cursor else None
        if cursor is None:
            return
        if cursor == old_source:
            raise ValueError(
                f"{by} is itself superseded by a chain leading back to {capture_id}; "
                "that would leave no current capture"
            )
    raise ValueError(f"the supersession chain from {by} does not end")


def _append_and_project(
    repository: Repository,
    root: Path,
    folder: Path,
    source_id: str,
    event: dict,
    check: Callable[[], None],
) -> None:
    """Check, append and project under both locks; re-sync the sidecar on any failure."""
    try:
        with _lifecycle_write_lock, repository.write_lock():
            check()
            repository.add_lifecycle_event(**event)
            _project(repository, root, folder, source_id)
    except LifecycleConflict:
        raise  # nothing was written: the conflict is detected before the sidecar is touched
    except BaseException:
        _resync(repository, root, folder, source_id, event["event_id"])
        raise


def supersede_capture(
    *,
    repository: Repository,
    captures_root: Union[str, Path],
    capture_id: str,
    by_capture_id: str,
    reason: Optional[str] = None,
    actor: str = "owner",
    now: Optional[Callable[[], datetime]] = None,
    new_id: Optional[Callable[[], str]] = None,
) -> dict:
    """Record that `capture_id` is superseded by `by_capture_id`; return the event.

    Raises `ValueError` for a capture superseding itself, a supersession that would close a
    loop (B already resolves to A), or one that is already current; `FileNotFoundError` for
    an unknown capture.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    new_id = new_id or (lambda: uuid.uuid4().hex)
    root = Path(captures_root)
    old_folder = resolve_capture_folder(root, capture_id)
    new_folder = resolve_capture_folder(root, by_capture_id)
    old_source = _source_id(old_folder)
    new_source = _source_id(new_folder)
    if old_source == new_source:
        raise ValueError("a capture cannot supersede itself")

    event = _event(old_source, "superseded", new_source, reason, actor, new_id, now)
    _append_and_project(
        repository, root, old_folder, old_source, event,
        lambda: _check_supersession(repository, old_source, new_source, capture_id,
                                    by_capture_id),
    )
    _refresh_index(root, now)
    return event


def reinstate_capture(
    *,
    repository: Repository,
    captures_root: Union[str, Path],
    capture_id: str,
    reason: Optional[str] = None,
    actor: str = "owner",
    now: Optional[Callable[[], datetime]] = None,
    new_id: Optional[Callable[[], str]] = None,
) -> dict:
    """Withdraw the current supersession of `capture_id`; return the event.

    Raises `ValueError` if the capture is not currently superseded.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    new_id = new_id or (lambda: uuid.uuid4().hex)
    root = Path(captures_root)
    folder = resolve_capture_folder(root, capture_id)
    source_id = _source_id(folder)

    def check() -> None:
        if repository.superseded_by(source_id) is None:
            raise ValueError(f"{capture_id} is not superseded")

    event = _event(source_id, "reinstated", None, reason, actor, new_id, now)
    _append_and_project(repository, root, folder, source_id, event, check)
    _refresh_index(root, now)
    return event


def resync_capture(
    *,
    repository: Repository,
    captures_root: Union[str, Path],
    capture_id: str,
) -> None:
    """Rewrite one capture's `lifecycle.json` from the store, even over a history conflict.

    The explicit human resolution for `LifecycleConflict`. A sidecar left by a process killed
    between its write and the COMMIT looks exactly like history from a newer database, so
    Capsule refuses to choose; a person who knows this database is the current one runs this.
    """
    root = Path(captures_root)
    folder = resolve_capture_folder(root, capture_id)
    source_id = _source_id(folder)
    with _lifecycle_write_lock, repository.write_lock():
        _project(repository, root, folder, source_id, force=True)
    _refresh_index(root, lambda: datetime.now(timezone.utc))


def reconcile_lifecycle(repository: Repository, captures_root: Union[str, Path]) -> int:
    """Re-project `lifecycle.json` for every capture the store has events for; return the count.

    Repairs a folder that fell behind the store (a sidecar write that failed after its event
    committed, a restored folder). It NEVER deletes: a sidecar the store has no events for is
    left alone, because a store that is older than the folder, or a different store, cannot
    tell a crash phantom from history it simply never saw, and deletion is the one step that
    cannot be redone (Codex review). The remaining gap, a process killed between the sidecar
    write and the COMMIT, is cleared by the next `capsule supersede` request about that
    capture, which re-syncs from a store that holds its source.
    """
    root = Path(captures_root)
    with_events = {
        row[0]
        for row in repository.connection.execute(
            "SELECT DISTINCT source_id FROM capture_lifecycle_event"
        )
    }
    if not with_events:
        return 0
    touched = 0
    for manifest in root.glob("*/manifest.json"):
        folder = manifest.parent
        try:
            source_id = _source_id(folder)
        except (OSError, ValueError):
            continue
        if source_id in with_events:
            try:
                with _lifecycle_write_lock, repository.write_lock():
                    _project(repository, root, folder, source_id)
            except LifecycleConflict as exc:
                _logger.warning("%s", exc)
                continue
            touched += 1
    return touched


def _refresh_index(captures_root: Path, now: Callable[[], datetime]) -> None:
    try:
        write_index(captures_root, now=now)
    except Exception as exc:  # noqa: BLE001 — best-effort projection, never fatal
        _logger.warning("could not regenerate captures index: %s", exc)
