"""The M0 capture pipeline: one URL in, one verifiable capture out.

`run_capture` wires the pieces together in a fixed order and makes exactly one
model call:

    fetch (page in view) -> extraction Agent call -> parse -> build models
      -> write_capture (folder + hashes + agent log) -> index rows in the repo

The capture folder is written before the database is touched: the on-disk
capture is the evidence of record, and the SQLite rows are only an index over
it. Determinism hooks (`now`, `new_id`) are injected so the whole pipeline is
exercised offline in tests; production supplies UTC-now and a UUID factory.

Compliance (plan §11, UC-4) is upheld by construction: exactly one
`fetcher.fetch(url)` for the page in view, no link-following, and — via the
model builder — no Wayback submission and copyright-flagging of gated pages.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from capsule.agent.base import Agent, AgentCall
from capsule.capture.manifest import (
    Extraction,
    MediaItem,
    Redaction,
    VerifyResult,
    verify_capture,
    write_capture,
    write_result_json,
)
from capsule.collection.index import write_index
from capsule.contracts.evidence import (
    CaptureNote,
    Citation,
    GatingStatus,
    Source,
    SourceQuality,
)
from capsule.pipeline.build import (
    BuiltCapture,
    build_capture_models,
    build_document_capture_models,
)
from capsule.pipeline.ancestry import is_ancestry_url
from capsule.pipeline.extract import RawExtraction, build_extraction_prompt, parse_extraction
from capsule.pipeline.failure_log import record_unwritten_call
from capsule.pipeline.fetch import Fetcher
from capsule.pipeline.household import parse_household
from capsule.pipeline.redact import redact_account_chrome
from capsule.store.repository import Repository

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureOutcome:
    """Everything a caller (CLI, side panel) needs after a capture completes."""

    capture_folder: Path
    source: Source
    citation: Citation
    extraction: Extraction
    # None for an archive-only document capture, which makes no model call.
    agent_call: Optional[AgentCall]
    verify: VerifyResult


def run_capture(
    url: str,
    *,
    fetcher: Fetcher,
    agent: Optional[Agent],
    repository: Repository,
    captures_root: Union[str, Path],
    media: Sequence[MediaItem] = (),
    note: Optional[str] = None,
    note_author: str = "owner",
    now: Optional[Callable[[], datetime]] = None,
    new_id: Optional[Callable[[], str]] = None,
    image_status: Optional[str] = None,
    page_label: Optional[str] = None,
    media_warnings: Sequence[str] = (),
    extension_version: Optional[str] = None,
    prior_redactions: Optional[Sequence[Redaction]] = None,
    extracted_from: Optional[str] = None,
) -> CaptureOutcome:
    """Capture `url` into a verifiable folder + append-only evidence rows.

    `media` are already-decoded, provenance-carrying byte bundles (e.g. an
    embedded certificate PDF the browser grabbed in-page); they are written into
    the capture folder and recorded in the manifest. The pipeline never fetches
    them — like the page snapshot, they are provided by the caller.

    `note` is an optional researcher annotation ("why I collected this"). It is
    the user's own words, not evidence — stored in the excluded `notes.json` and
    the append-only `capture_note` table, never in the hashed evidence.

    `agent=None` (v0.2, no API key yet) archives the page with NO model call: the
    snapshot, media and citation are written now and the extraction is empty and marked
    `pending` in the manifest. That is not a failed reading, and `verify_capture` does not
    treat it as one; reading it later makes a new capture that supersedes this one.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    new_id = new_id or (lambda: uuid.uuid4().hex)
    # One instant for the whole capture, so the manifest's created_at and every
    # assertion's created_at agree rather than drifting across now() calls.
    moment = now()
    frozen_now = lambda: moment  # noqa: E731 — trivial constant-time closure

    snapshot = fetcher.fetch(url)  # the page in view — the only request we make
    # Strip the subscriber's account chrome before ANYTHING downstream sees the page:
    # the prompt, `snapshot.html`, its SHA-256, and the snippet-location check must
    # all describe the same document. Order matters — `fetcher.fetch` has already run
    # gating detection against the raw HTML, and the redactor deliberately leaves the
    # logout marker in place so the written snapshot still evidences its own gating.
    if prior_redactions is None:
        redacted_html, redactions = redact_account_chrome(snapshot.html)
        snapshot = replace(snapshot, html=redacted_html)
    else:
        # `capsule extract` re-reads a snapshot that was ALREADY redacted when it was
        # archived; the original capture's declaration carries over unchanged.
        redactions = tuple(prior_redactions)

    # REQ-003: member record IDs from the page's own Household table — parsed from
    # the same redacted HTML that becomes snapshot.html, so the block is re-derivable
    # from the archived page forever. Deterministic and Ancestry-only; never a model
    # output, never fatal to the capture.
    household = parse_household(snapshot.html, page_url=url) if is_ancestry_url(url) else ()

    if agent is None:
        call = None
        raw = RawExtraction(record_type="", personas=[], assertions=[])
    else:
        prompt = build_extraction_prompt(snapshot)
        call = agent.run(prompt.user, system=prompt.system)  # the single model call
    # The call has now been paid for. If anything downstream fails, the record
    # must still land on disk (transport-failure logging is the Agent sink's job;
    # this covers a *successful* call whose capture then fails to build/write).
    try:
        if call is not None:
            raw = parse_extraction(call.response)
        built = build_capture_models(
            snapshot, raw, new_id=new_id, now=frozen_now, page_label=page_label
        )
        notes = _notes_for(built.source.id, note, note_author, moment, new_id)
        folder = write_capture(
            captures_root,
            source=built.source,
            citation=built.citation,
            extraction=built.extraction,
            snapshot_html=snapshot.html,
            media=media,
            redactions=redactions,
            notes=notes,
            agent_logs={call.log_filename: call.to_log()} if call is not None else None,
            created_at=moment,
            # R4.3 — the folder must be able to say, on its own, what its record image IS.
            image_status=image_status,
            household=household,
            # BUG-016 — and what it knew was WRONG with that media. Computed by the
            # caller (the backend measures the render and sniffs the bytes); persisted
            # here, because a warning that lives only in a toast is gone by morning.
            media_warnings=media_warnings,
            extension_version=extension_version,
            extraction_pending=call is None,
            extracted_from=extracted_from,
        )
        _persist(repository, built, notes=notes)
    except Exception:
        if call is not None:
            record_unwritten_call(captures_root, call)
        raise
    outcome = CaptureOutcome(
        capture_folder=folder,
        source=built.source,
        citation=built.citation,
        extraction=built.extraction,
        agent_call=call,
        verify=verify_capture(folder),
    )
    # A machine-readable outcome the driving agent can read without measuring pixels
    # (live-run finding). Excluded sidecar, written after verify; best-effort like the index.
    try:
        write_result_json(
            folder,
            record_type=built.source.record_type,
            image_status=image_status,
            assertion_count=len(built.extraction.assertions),
            persona_count=len(built.extraction.personas),
            verify_ok=outcome.verify.ok,
            verify_shortfall=outcome.verify.insufficient,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort projection, never fatal
        _logger.warning("could not write result.json: %s", exc)
    # Refresh the human-readable collection index. This is a convenience
    # projection over the folders, never part of the capture's integrity — a
    # render failure must not lose a capture that is already written and indexed.
    try:
        write_index(captures_root, now=frozen_now)
    except Exception as exc:  # noqa: BLE001 — best-effort projection, never fatal
        _logger.warning("could not regenerate captures index: %s", exc)
    return outcome


def _notes_for(
    source_id: str,
    note: Optional[str],
    author: str,
    moment: datetime,
    new_id: Callable[[], str],
) -> tuple[CaptureNote, ...]:
    """Build the capture-time note tuple (empty when no note was given).

    The note is linked to the source the pipeline just created and stamped with
    the one capture instant, so its id/timestamp agree with the rest of the capture.
    """
    if not note or not note.strip():
        return ()
    return (
        CaptureNote(
            id=new_id(),
            source_id=source_id,
            text=note,
            author=author,
            created_at=moment,
        ),
    )


def _persist(
    repository: Repository,
    built: BuiltCapture,
    *,
    notes: Sequence[CaptureNote] = (),
) -> None:
    """Index a written capture into the append-only store atomically.

    Delegates to `Repository.add_capture`, which runs every insert in a single
    transaction — so a mid-write failure rolls back the whole index rather than
    leaving a source with only some of its assertions.
    """
    repository.add_capture(
        source=built.source,
        citation=built.citation,
        personas=built.extraction.personas,
        snippets=built.extraction.snippets,
        assertions=built.extraction.assertions,
        notes=notes,
        # Same transaction as the rest: a half-indexed holdings table would answer
        # "was 1852 covered?" with a confident, wrong No.
        coverage=built.extraction.coverage,
        provider_claims=built.extraction.provider_claims,
    )


def run_document_capture(
    url: str,
    *,
    document_bytes: bytes,
    ext: str,
    media_type: Optional[str],
    repository: Repository,
    captures_root: Union[str, Path],
    gating: GatingStatus = GatingStatus.PUBLIC,
    note: Optional[str] = None,
    note_author: str = "owner",
    title: Optional[str] = None,
    site_name: Optional[str] = None,
    provenance: Optional[str] = None,
    record_type: Optional[str] = None,
    source_quality: Optional[SourceQuality] = None,
    now: Optional[Callable[[], datetime]] = None,
    new_id: Optional[Callable[[], str]] = None,
    extension_version: Optional[str] = None,
) -> CaptureOutcome:
    """Archive-only capture of a document the page IS (a PDF), no HTML, no model call.

    Mirrors `run_capture` but skips fetch + extraction entirely: the caller (the
    browser, via the backend) provides the bytes it already loaded, and Capsule
    writes a verifiable `capture-folder/v2` document capture — the PDF as the hashed
    primary artifact, an empty extraction, a citation, and any researcher `note`.
    Reading the document into assertions is deferred to M4.

    `capsule ingest` (REQ-008) is the other caller: the bytes come from a file on disk,
    and `provenance` records how that file reached Capsule.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    new_id = new_id or (lambda: uuid.uuid4().hex)
    moment = now()
    frozen_now = lambda: moment  # noqa: E731

    built = build_document_capture_models(
        url=url,
        document_bytes=document_bytes,
        ext=ext,
        media_type=media_type,
        gating=gating,
        fetched_at=moment,
        new_id=new_id,
        title=title,
        site_name=site_name,
        provenance=provenance,
        record_type=record_type,
        source_quality=source_quality,
    )
    notes = _notes_for(built.source.id, note, note_author, moment, new_id)
    folder = write_capture(
        captures_root,
        source=built.source,
        citation=built.citation,
        extraction=built.extraction,
        primary_bytes=document_bytes,
        notes=notes,
        created_at=moment,
        extension_version=extension_version,
    )
    _persist(repository, built, notes=notes)
    outcome = CaptureOutcome(
        capture_folder=folder,
        source=built.source,
        citation=built.citation,
        extraction=built.extraction,
        agent_call=None,  # archive-only: no model call was made
        verify=verify_capture(folder),
    )
    # Machine-readable outcome sidecar (live-run finding). A document has no record image,
    # so image_status is None; best-effort like the index.
    try:
        write_result_json(
            folder,
            record_type=built.source.record_type,
            image_status=None,
            assertion_count=len(built.extraction.assertions),
            persona_count=len(built.extraction.personas),
            verify_ok=outcome.verify.ok,
            verify_shortfall=outcome.verify.insufficient,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort projection, never fatal
        _logger.warning("could not write result.json: %s", exc)
    try:
        write_index(captures_root, now=frozen_now)
    except Exception as exc:  # noqa: BLE001 — best-effort projection, never fatal
        _logger.warning("could not regenerate captures index: %s", exc)
    return outcome
