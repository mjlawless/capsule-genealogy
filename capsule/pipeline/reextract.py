"""Read a capture that was archived without an API key (v0.2, `capsule extract`).

A capture made with no Anthropic key holds the page, its media and its citation, with the
extraction marked `pending`. Once a key exists this reads it: the ARCHIVED snapshot (never the
live site, which may have changed or be behind a lapsed subscription) goes through the normal
pipeline into a NEW capture, and the pending one is marked superseded by it (REQ-011).

Nothing about the original is edited. The new capture carries the original's media bytes,
image status, page label, gating and redaction record, and its citation keeps the ORIGINAL
access date, because that is when the page was seen; only the reading is new.
"""
from __future__ import annotations

import json
import logging
import re
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Sequence, Union

from capsule.agent.base import Agent
from capsule.capture.manifest import (
    LIFECYCLE_NAME,
    Extraction,
    MediaItem,
    Redaction,
    resolve_capture_folder,
    serialize_extraction,
    sha256_hex,
    verify_capture,
    write_result_json,
)
from capsule.contracts.evidence import (
    ArchiveRecord,
    Assertion,
    Citation,
    CoverageClaim,
    ExternalRef,
    GatingStatus,
    Persona,
    ProviderClaim,
    Snippet,
    Source,
)
from capsule.pipeline.ancestry import record_ids_from_url
from capsule.pipeline.fetch import ProvidedContentFetcher
from capsule.pipeline.pipeline import CaptureOutcome, run_capture
from capsule.pipeline.supersede import supersede_capture
from capsule.pipeline.trash import trash_capture
from capsule.store.ledger import AncestryLedger
from capsule.store.repository import Repository

#: A claim older than this is a crashed run's, not a live one.
_logger = logging.getLogger(__name__)

_CLAIM_TTL_S = 3600

#: `media/<n>-<slug>.<ext>` as the writer names it.
_MEDIA_NAME = re.compile(r"\Amedia/\d+-(?P<slug>.+)\.(?P<ext>[a-z0-9]+)\Z")


@dataclass(frozen=True)
class Extracted:
    """The capture that now stands for the pending one.

    `outcome` is None when an interrupted earlier run had already written the replacement and
    this run only finished the supersession (`resumed`): nothing was read twice.
    """

    replaced: str
    replacement: Path
    outcome: Optional[CaptureOutcome]
    resumed: bool = False


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _media_of(folder: Path, manifest: dict) -> list[MediaItem]:
    items: list[MediaItem] = []
    for entry in manifest.get("media") or []:
        match = _MEDIA_NAME.match(str(entry.get("path") or ""))
        if not match:
            raise ValueError(f"{folder.name}: unexpected media path {entry.get('path')!r}")
        items.append(
            MediaItem(
                slug=match["slug"],
                ext=match["ext"],
                data=(folder / entry["path"]).read_bytes(),
                mime=entry.get("mime"),
                provenance=entry.get("provenance"),
                source_url=entry.get("source_url"),
            )
        )
    return items


def pending_captures(captures_root: Union[str, Path]) -> list[str]:
    """Capture ids still waiting to be read, oldest first, skipping any already superseded."""
    found: list[str] = []
    for manifest_path in sorted(Path(captures_root).glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("extraction") != "pending":
            continue
        if _currently_superseded(manifest_path.parent):
            continue  # already read (a reinstated one is pending again)
        found.append(manifest_path.parent.name)
    return found


def waiting_captures(captures_root: Union[str, Path]) -> list[str]:
    """Pending captures that were NEVER read: what the index offers to read.

    Narrower than `pending_captures`: a capture whose reading was set aside (reinstated) is
    pending again for `capsule extract`, an explicit request, but the index never offers to
    read it a second time (not offered yet).
    """
    root = Path(captures_root)
    return [c for c in pending_captures(root) if not _ever_superseded(root / c)]


def choose_readable(
    captures_root: Union[str, Path], capture_ids: Sequence[str]
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split the captures a person chose into (readable, refused with a reason).

    Readable means still waiting to be read: archived without a key and never replaced by a
    reading, even one later set aside by a reinstatement. A capture that was read (with a key,
    or later by `capsule extract`) is never read again from here.
    Duplicates are dropped; order is kept.
    """
    root = Path(captures_root)
    readable: list[str] = []
    refused: list[tuple[str, str]] = []
    seen: set[str] = set()
    for capture_id in capture_ids:
        if capture_id in seen:
            continue
        seen.add(capture_id)
        try:
            folder = resolve_capture_folder(root, capture_id)
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            refused.append((capture_id, "no such capture"))
            continue
        except (ValueError, OSError):
            refused.append((capture_id, "not a capture this archive can read"))
            continue
        if not isinstance(manifest, dict) or manifest.get("extraction") != "pending":
            refused.append((capture_id, "already read; reading it again is not offered"))
        elif _currently_superseded(folder):
            refused.append((capture_id, "already read: a newer capture holds its reading"))
        elif _ever_superseded(folder):
            refused.append(
                (capture_id, "already read once (that reading was set aside); reading it "
                 "again is not offered")
            )
        else:
            readable.append(folder.name)
    return readable, refused


def _ever_superseded(folder: Path) -> bool:
    """Was this capture ever replaced, even if it was reinstated since?"""
    try:
        events = json.loads((folder / LIFECYCLE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(events, list) and any(
        isinstance(e, dict) and e.get("event") == "superseded" for e in events
    )


def _currently_superseded(folder: Path) -> bool:
    try:
        events = json.loads((folder / LIFECYCLE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(events) and isinstance(events[-1], dict) and events[-1].get("event") == "superseded"


def extract_capture(
    *,
    repository: Repository,
    captures_root: Union[str, Path],
    capture_id: str,
    agent: Agent,
) -> Extracted:
    """Read a pending capture into a new capture that supersedes it.

    A capture is read at most once: one read before and then reinstated (the reading set
    aside) is refused too, checked under the claim, so it is never paid for twice. Reading
    again is not offered yet, on the index page or in the CLI.

    Raises `ValueError` if the capture is not pending (it was already read, or is a document
    capture, which has no page text to read) and `FileNotFoundError` if it does not exist.
    """
    root = Path(captures_root)
    folder = resolve_capture_folder(root, capture_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("extraction") != "pending":
        raise ValueError(f"{capture_id} is not waiting to be read (it has no pending extraction)")
    source = manifest["source"]
    # Already read? The pending marker is immutable, so ask the lifecycle (Codex review).
    if repository.superseded_by(source["id"]) is not None:
        raise ValueError(f"{capture_id} has already been read and replaced")
    # Only bytes that still match their hashes may be read: otherwise an edited snapshot would
    # be re-hashed into a fresh capture carrying the ORIGINAL access date (Codex review).
    # Sufficiency is a different question and does not block reading.
    check = verify_capture(folder)
    if check.missing or check.mismatched or check.extra:
        raise ValueError(
            f"{capture_id} no longer matches its own hashes (missing {list(check.missing)}, "
            f"changed {list(check.mismatched)}, unexpected {list(check.extra)}); not reading it"
        )
    with _claim(root, capture_id):
        # Re-checked under the claim: another process may have finished meanwhile.
        if repository.superseded_by(source["id"]) is not None:
            raise ValueError(f"{capture_id} has already been read and replaced")
        if any(
            e["event"] == "superseded" for e in repository.lifecycle_for_source(source["id"])
        ):
            raise ValueError(f"{capture_id} was read before; reading it again is not offered")
        return _read(repository, root, folder, manifest, capture_id, agent)


@contextmanager
def _claim(root: Path, capture_id: str) -> Iterator[None]:
    """One reader per capture, across processes: a lock file created exclusively.

    The model call is slow and costs money, so two `capsule extract` runs (or the CLI and the
    server) must not both read the same capture and both create a replacement. A claim left
    by a crashed run expires after an hour.
    """
    lock = root / f".extract-{capture_id}.lock"
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > _CLAIM_TTL_S:
            lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f"{capture_id} is already being read by another process") from exc
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _read(
    repository: Repository,
    root: Path,
    folder: Path,
    manifest: dict,
    capture_id: str,
    agent: Agent,
) -> Extracted:
    source = manifest["source"]
    archive = manifest["archive"]
    snapshot_name = archive.get("local_snapshot") or "snapshot.html"
    html = (folder / snapshot_name).read_text(encoding="utf-8")
    seen_at = _parse_time(archive["captured_at"])

    # Every step below is resumable, in this order, so an interruption anywhere is finished by
    # simply asking again (Codex review): (1) the replacement folder AND its database rows,
    # (2) the Ancestry ledger, (3) the supersession, last, because it is what marks the job
    # done. A replacement folder whose rows never landed is indexed from its own files, so a
    # reading already paid for is not paid for again; only one that cannot be is re-read.
    replacement = _unfinished_replacement(repository, root, capture_id, source["id"])
    outcome: Optional[CaptureOutcome] = None
    if replacement is None:
        outcome = run_capture(
            source["url"],
            # The page as it was archived, at the moment it was archived: the citation's access
            # date is when the page was SEEN, not when it was read.
            fetcher=ProvidedContentFetcher(
                html, gating=GatingStatus(source["gating_status"]), now=lambda: seen_at
            ),
            agent=agent,
            repository=repository,
            captures_root=root,
            media=_media_of(folder, manifest),
            image_status=manifest.get("image"),
            page_label=source.get("page_label"),
            media_warnings=tuple(manifest.get("media_warnings") or ()),
            extension_version=manifest.get("extension_version"),
            prior_redactions=tuple(
                Redaction(reason=r["reason"], location=r["location"])
                for r in manifest.get("redactions") or ()
            ),
            extracted_from=capture_id,
        )
        replacement = outcome.capture_folder
    _update_ledger(repository, source, manifest, capture_id, replacement)
    if repository.superseded_by(source["id"]) is None:
        supersede_capture(
            repository=repository,
            captures_root=root,
            capture_id=capture_id,
            by_capture_id=replacement.name,
            reason="read with `capsule extract` from the archived snapshot",
            actor="capsule extract",
        )
    return Extracted(
        replaced=capture_id, replacement=replacement, outcome=outcome, resumed=outcome is None
    )


def _indexed(repository: Repository, folder: Path) -> bool:
    """Did this capture's rows reach the database (not just its folder the disk)?"""
    try:
        source_id = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))[
            "source"
        ]["id"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return (
        repository.connection.execute("SELECT 1 FROM source WHERE id = ?", (source_id,)).fetchone()
        is not None
    )


def _unfinished_replacement(
    repository: Repository, root: Path, capture_id: str, source_id: str
) -> Optional[Path]:
    """A reading of `capture_id` that an interrupted run wrote but never applied, or None.

    A replacement that WAS applied and then reinstated away was rejected by a person, and is
    never brought back (Codex review); the next run reads afresh. A replacement whose
    database rows never landed (the model answered, then the database write failed) is
    indexed from its own files, so the reading is not paid for twice; if that write fails
    again the error propagates and the folder waits for the next retry. Only a reading that
    is damaged, or whose files cannot be turned back into exactly what was written, is moved
    to the trash and read again, as before.
    """
    applied = {
        e["superseded_by"]
        for e in repository.lifecycle_for_source(source_id)
        if e["event"] == "superseded"
    }
    for manifest_path in sorted(root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or manifest.get("extracted_from") != capture_id:
            continue
        folder = manifest_path.parent
        if (manifest.get("source") or {}).get("id") in applied:
            continue  # applied once, then rejected by a reinstatement
        check = verify_capture(folder)
        damaged = bool(check.missing or check.mismatched or check.extra)
        if not _indexed(repository, folder):
            # Verified FIRST: a damaged folder must never reach the database.
            rebuilt = None if damaged else _rebuild_from_folder(folder, manifest)
            if rebuilt is None:
                trash_capture(root, folder.name)
                continue
            # A database failure here propagates and leaves the folder for the next retry:
            # the reading is intact, so trashing it would only pay for it again (Codex review).
            repository.add_capture(**rebuilt)
            # The capture stopped before its `result.json`; write it as `run_capture` would,
            # so the index shows its verdict instead of "unknown". Best-effort, like there.
            try:
                write_result_json(
                    folder,
                    record_type=rebuilt["source"].record_type,
                    image_status=manifest.get("image"),
                    assertion_count=len(rebuilt["assertions"]),
                    persona_count=len(rebuilt["personas"]),
                    verify_ok=check.ok,
                    verify_shortfall=check.insufficient,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort projection, never fatal
                _logger.warning("could not write result.json for %s: %s", folder.name, exc)
        elif damaged:
            # Never let a damaged reading replace an intact original (Codex review).
            raise ValueError(
                f"an earlier reading of {capture_id} ({folder.name}) no longer matches its own "
                "hashes; the original stays current. Move that folder aside and run again."
            )
        return folder
    return None


def _rebuild_from_folder(folder: Path, manifest: dict) -> Optional[dict]:
    """The `add_capture` arguments for a written capture whose rows never landed, or None.

    Only when the files turn back into exactly what was written: `extraction.json` must
    re-serialize to the bytes its `content_hash` names, and `citation.json` to the same
    JSON. Anything else is None, and the caller reads the capture again rather than index a
    guess. Indexing itself is one `add_capture` transaction, as at capture time.
    """
    try:
        raw = json.loads((folder / "extraction.json").read_text(encoding="utf-8"))
        cited = json.loads((folder / "citation.json").read_text(encoding="utf-8"))
        source = Source.model_validate({
            **manifest["source"],
            "archive": ArchiveRecord.model_validate(manifest["archive"]),
            "spec_version": manifest["spec_version"],
        })
        extraction = Extraction(
            personas=tuple(Persona.model_validate(p) for p in raw.get("personas") or ()),
            snippets=tuple(Snippet.model_validate(s) for s in raw.get("snippets") or ()),
            assertions=tuple(Assertion.model_validate(a) for a in raw.get("assertions") or ()),
            external_refs=tuple(
                ExternalRef.model_validate(r) for r in raw.get("external_refs") or ()
            ),
            coverage=tuple(CoverageClaim.model_validate(c) for c in raw.get("coverage") or ()),
            provider_claims=tuple(
                ProviderClaim.model_validate(p) for p in raw.get("provider_claims") or ()
            ),
            notes=raw.get("notes"),
        )
        citation = Citation.model_validate(cited)
        if sha256_hex(serialize_extraction(extraction)) != source.content_hash:
            raise ValueError("extraction.json does not re-serialize to its content_hash")
        if citation.model_dump(mode="json") != cited:
            raise ValueError("citation.json does not round-trip")
    except Exception as exc:  # noqa: BLE001 — any doubt means "read it again", never a guess
        _logger.warning("could not rebuild %s from its files, reading again: %s", folder.name, exc)
        return None
    return {
        "source": source,
        "citation": citation,
        "personas": extraction.personas,
        "snippets": extraction.snippets,
        "assertions": extraction.assertions,
        "coverage": extraction.coverage,
        "provider_claims": extraction.provider_claims,
    }


def _readings_of(root: Path, capture_id: str) -> set[str]:
    """Every capture written as a reading of `capture_id`."""
    names: set[str] = set()
    for manifest_path in root.glob("*/manifest.json"):
        try:
            if json.loads(manifest_path.read_text(encoding="utf-8")).get("extracted_from") == (
                capture_id
            ):
                names.add(manifest_path.parent.name)
        except (OSError, ValueError, AttributeError):
            continue
    return names


def _update_ledger(
    repository: Repository, source: dict, manifest: dict, original: str, replacement: Path
) -> None:
    """Tell the Ancestry ledger the record was read, if it was tracking the pending capture.

    The pending capture was ledgered with zero assertions, so the record stayed unmined and the
    queue would capture it again. The replacement's facts now stand for it (Codex review).
    Idempotent: nothing is written if the ledger already names the replacement.
    """
    ids = record_ids_from_url(source.get("url"))
    if ids is None:
        return
    apid = f"{ids[0]}:{ids[1]}"
    page_label = source.get("page_label")
    db_path = repository.connection.execute("PRAGMA database_list").fetchone()[2]
    ledger = AncestryLedger(db_path)
    try:
        held = ledger.latest(apid, page_label)
        # The ledger may advance from the pending capture itself, or from an EARLIER reading
        # of it that a person rejected by reinstating the original (Codex review). Anything
        # else is an unrelated newer capture and is left alone.
        ours = {original} | _readings_of(replacement.parent, original)
        if held is None or held.capture_id not in ours or held.capture_id == replacement.name:
            return
        extraction = json.loads((replacement / "extraction.json").read_text(encoding="utf-8"))
        ledger.record_capture(
            apid,
            capture_id=replacement.name,
            image_status=manifest.get("image"),
            assertion_count=len(extraction.get("assertions") or []),
            persona_count=len(extraction.get("personas") or []),
            verify_ok=verify_capture(replacement).ok,
            follow_up=held.follow_up,
            page_label=page_label,
        )
    finally:
        ledger.close()


# -- reading chosen captures in the background (the index page's "Read with AI") ------------


@dataclass
class BackgroundReading:
    """Progress of the one background reading run (there is at most one at a time)."""

    running: bool = False
    total: int = 0
    done: int = 0
    failed: int = 0
    last_error: Optional[str] = None


_background = BackgroundReading()
_background_lock = threading.Lock()


def background_status() -> BackgroundReading:
    with _background_lock:
        return BackgroundReading(**vars(_background))


class ReadingBusy(RuntimeError):
    """A background reading is already going; a second one is refused, not merged."""


def start_background_reading(
    *,
    db_path: Union[str, Path],
    captures_root: Union[str, Path],
    agent: Agent,
    targets: Sequence[str],
) -> BackgroundReading:
    """Read the chosen captures on a worker thread; returns the state it started with.

    The caller chooses (the index page's ticks, filtered by `choose_readable`): nothing is
    read just because it is pending. Raises `ReadingBusy` while another
    run is going, so the page never claims to have started a reading it did not. Each capture
    is read with the same `extract_capture` as `capsule extract`, which re-checks that it is
    still waiting, so every safety it has applies here.
    """
    targets = list(dict.fromkeys(targets))
    with _background_lock:
        if _background.running:
            raise ReadingBusy("Capsule is already reading captures; wait for it to finish.")
        if not targets:
            return BackgroundReading(**vars(_background))
        _background.running, _background.total = True, len(targets)
        _background.done = _background.failed = 0
        _background.last_error = None

    def work() -> None:
        repository: Optional[Repository] = None
        try:
            # Opened INSIDE the guard: a database that will not open must still clear
            # `running`, or every later "Read them now" would be refused (Codex review).
            repository = Repository(db_path)
            for target in targets:
                try:
                    extract_capture(
                        repository=repository,
                        captures_root=captures_root,
                        capture_id=target,
                        agent=agent,
                    )
                    with _background_lock:
                        _background.done += 1
                except Exception as exc:  # noqa: BLE001 — one bad capture must not stop the rest
                    with _background_lock:
                        _background.failed += 1
                        _background.last_error = f"{target}: {exc}"
        except Exception as exc:  # noqa: BLE001 — reported in the status, never silent
            with _background_lock:
                _background.last_error = f"could not open the database: {exc}"
        finally:
            if repository is not None:
                repository.close()
            with _background_lock:
                _background.running = False

    threading.Thread(target=work, name="capsule-read-pending", daemon=True).start()
    return background_status()
