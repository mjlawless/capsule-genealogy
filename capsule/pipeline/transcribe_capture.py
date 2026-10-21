"""Transcribe a sealed capture's scanned original, end to end (REQ-006 T6).

The orchestration behind `capsule transcribe <capture_id>`:

1. **Choose the scan.** The capture's own document (an ingested PDF) or a media
   file that is a scanned original — never Capsule's own page render
   (`render:*` provenance). More than one candidate needs an explicit `media=`.
2. **Check its bytes against the seal** before spending a call: a transcript is
   pinned to `media_sha256`, so the file must be the one the manifest hashed.
3. **Transcribe** (T3), then **compare with the page's index claims** (T5).
4. **Land it, append-only, in an order that loses nothing paid for:** agent logs
   first (every call is kept even if a later step fails), then the hash-excluded
   `transcript/<id>/` folder (atomic), then the store. If the store refuses, the
   folder it would have described is removed, so disk and store never disagree.

Nothing here writes an assertion or touches a grade (REQ-006 §5.1–5.2).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from capsule.agent.base import Agent, AgentCall
from capsule.capture.manifest import resolve_capture_folder
from capsule.capture.transcript import write_transcript_folder
from capsule.collection.index import write_index
from capsule.contracts.transcript import TranscriptComparison, Transcription, TranscriptField
from capsule.pipeline.annotate import _source_id_from_manifest
from capsule.pipeline.reconcile import IndexClaim, reconcile
from capsule.pipeline.transcribe import records_from_draft, transcribe_document
from capsule.store.repository import Repository

_logger = logging.getLogger(__name__)

_SCAN_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")


@dataclass(frozen=True)
class TranscribeOutcome:
    capture_folder: Path
    transcript_folder: Path
    transcription: Transcription
    fields: list[TranscriptField]
    comparisons: list[TranscriptComparison]


def transcribe_capture(
    *,
    repository: Repository,
    captures_root: Union[str, Path],
    capture_id: str,
    layout_agent: Agent,
    read_agent: Agent,
    reconcile_agent: Agent,
    media: Optional[str] = None,
    now: Optional[Callable[[], datetime]] = None,
    new_id: Optional[Callable[[], str]] = None,
) -> TranscribeOutcome:
    """Transcribe one capture's scan and record it. Raises ValueError for a capture
    with no (or an ambiguous, or an altered) scan — before any model call."""
    now = now or (lambda: datetime.now(timezone.utc))
    new_id = new_id or (lambda: uuid.uuid4().hex)

    folder = resolve_capture_folder(captures_root, capture_id)
    source_id = _source_id_from_manifest(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    rel = _choose_scan(manifest, media, folder)
    path = folder / rel
    media_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    sealed = (manifest.get("hashes") or {}).get("files", {}).get(rel)
    if sealed != media_sha256:
        raise ValueError(
            f"{rel} does not match the capture's sealed hash — refusing to transcribe altered media"
        )

    calls: list[AgentCall] = []
    try:
        draft = transcribe_document(path, layout_agent=layout_agent, read_agent=read_agent)
        calls += draft.calls
        created_at = now()
        transcription, fields, crops = records_from_draft(
            draft,
            source_id=source_id,
            media_sha256=media_sha256,
            model=read_agent.model,
            created_at=created_at,
            new_id=new_id,
        )
        claims = [
            IndexClaim(assertion_id=a.id, field=a.field, verbatim_value=a.verbatim_value)
            for a in repository.current_assertions(source_id)
        ]
        compared = reconcile(
            claims, fields, source_id=source_id, transcription_id=transcription.id,
            agent=reconcile_agent, created_at=created_at, new_id=new_id,
        )
        if compared.call is not None:
            calls.append(compared.call)
    finally:
        _write_agent_logs(folder, calls)  # paid calls are kept whatever happens next

    if compared.problems:
        problems = transcription.problems + tuple(compared.problems)
        status = "partial" if transcription.status == "complete" else transcription.status
        transcription = Transcription.model_validate(
            {**transcription.model_dump(), "problems": problems, "status": status}
        )

    target = write_transcript_folder(
        folder, transcription, fields, crops, comparisons=compared.comparisons
    )
    try:
        repository.add_transcription(transcription, fields, compared.comparisons)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)  # never leave a folder the store denies
        raise

    try:
        write_index(captures_root, now=now)
    except Exception as exc:  # noqa: BLE001 — best-effort projection, never fatal
        _logger.warning("could not regenerate captures index: %s", exc)
    return TranscribeOutcome(folder, target, transcription, fields, compared.comparisons)


def _choose_scan(manifest: dict, media: Optional[str], folder: Path) -> str:
    """The relative path of the scan to read (see module docstring, step 1).

    A media file is a candidate only if it is not Capsule's own render AND it decodes
    to a real page of pixels — the NYC pages also save the site logo and a JS plugin
    named `pdfobject-min.pdf`, which made 13 captures falsely "ambiguous". The
    capture's own document (an ingest) is trusted as-is.
    """
    capture_id = folder.name
    candidates: list[str] = []
    archive = manifest.get("archive") or {}
    if archive.get("kind") == "document" and _is_scan(archive.get("local_snapshot", "")):
        candidates.append(archive["local_snapshot"])
    for item in manifest.get("media") or []:
        rel = item.get("path", "")
        provenance = item.get("provenance") or ""
        if _is_scan(rel) and not provenance.startswith("render:") and _is_page_of_pixels(folder / rel):
            candidates.append(rel)

    if media is not None:
        chosen = [c for c in candidates if c == media or Path(c).name == media]
        if not chosen:
            raise ValueError(
                f"{media!r} is not a scanned original in {capture_id}; candidates: {candidates or 'none'}"
            )
        return chosen[0]
    if not candidates:
        raise ValueError(f"{capture_id} holds no scanned original to transcribe")
    if len(candidates) > 1:
        names = ", ".join(Path(c).name for c in candidates)
        raise ValueError(f"{capture_id} holds several scans ({names}); choose one with --media")
    return candidates[0]


def _is_scan(rel: str) -> bool:
    return rel.lower().endswith(_SCAN_SUFFIXES)


# A scan is a page of pixels in BOTH directions: an NYC certificate is 2072x2317, a
# 4x6in card at 300 dpi is 1200x1800. The DORIS site logo (1220x656) is long but thin.
_MIN_SCAN_LONG_EDGE = 1000
_MIN_SCAN_SHORT_EDGE = 800


def _is_page_of_pixels(path: Path) -> bool:
    """Does `path` decode to a page image large enough to be a scan? A logo, a
    thumbnail, or a mislabelled script does not."""
    try:
        if path.suffix.lower() == ".pdf":
            import pypdfium2 as pdfium

            from capsule.pipeline.raster import _full_page_scan

            document = pdfium.PdfDocument(str(path))
            try:
                page = document[0]
                try:
                    scan = _full_page_scan(page, pdfium)
                finally:
                    page.close()
            finally:
                document.close()
            return scan is not None and _big_enough(scan.size)
        from PIL import Image

        with Image.open(path) as image:
            return _big_enough(image.size)
    except Exception:  # noqa: BLE001 — anything that won't decode is not a scan
        return False


def _big_enough(size: tuple[int, int]) -> bool:
    return max(size) >= _MIN_SCAN_LONG_EDGE and min(size) >= _MIN_SCAN_SHORT_EDGE


def _write_agent_logs(folder: Path, calls: list[AgentCall]) -> None:
    """Append each call's log to the capture's (hash-excluded) agent_log/; never overwrite."""
    if not calls:
        return
    log_dir = folder / "agent_log"
    log_dir.mkdir(exist_ok=True)
    for call in calls:
        target = log_dir / call.log_filename
        if target.exists():
            _logger.warning("agent log %s already exists; not overwritten", target.name)
            continue
        target.write_text(
            json.dumps(call.to_log(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
