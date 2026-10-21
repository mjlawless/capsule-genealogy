"""Apply one human correction to an already-sealed capture (slice C3).

A capture folder is immutable once sealed — it is exactly what the model
extracted at capture time. A correction therefore never rewrites `extraction.json`
or its hash; it lands in the SEPARATE, append-only correction layer built in C1/C2:

- **edit** — the model read a fact but got the value wrong: a superseding
  assertion carries the corrected value.
- **add** — the model missed a fact: a brand-new assertion about an existing
  persona, backed by a human-supplied snippet (the words on the source).
- **retract** — the model asserted a fact that is not on the source: a superseding
  assertion flagged `retracted`, so `current_assertions` drops the fact entirely.

Every correction also writes a `correction_event` (model-said-X / human-said-Y),
atomically with the assertion it produces. Auto-generating an eval fixture from the
corrected capture is a separate concern (slice C4); this service stops at the store.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from pydantic import BaseModel, ConfigDict, model_validator

from capsule.capture.manifest import resolve_capture_folder
from capsule.contracts.evidence import (
    Assertion,
    CorrectionAction,
    CorrectionEvent,
    EvidenceType,
    Snippet,
    SnippetAnchor,
)
from capsule.store.repository import Repository


def _source_id_from_manifest(folder: Path) -> str:
    """The capture's `source.id`, read from its manifest — the correction's anchor.

    Re-declared here (not imported from `annotate`) to keep the correction service
    independent of the note service; it is a three-line manifest read.
    """
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    source = manifest.get("source") if isinstance(manifest, dict) else None
    source_id = source.get("id") if isinstance(source, dict) else None
    if not isinstance(source_id, str) or not source_id:
        raise ValueError(f"manifest in {folder.name} has no source.id")
    return source_id


class CorrectionRequest(BaseModel):
    """The inputs to one correction, validated per action at the boundary.

    Distinct from `CorrectionEvent` (the stored log): this is what a caller
    supplies, and its invariants are about *inputs* — edit needs a target and a
    value, add needs the person/field/value/snippet to build a fresh assertion,
    retract needs only a target. Fail-fast here so a malformed request never
    reaches the store.
    """

    model_config = ConfigDict(extra="forbid")

    capture_id: str
    action: CorrectionAction
    rationale: Optional[str] = None
    actor: str = "owner"

    # edit / retract: the current assertion being corrected.
    assertion_id: Optional[str] = None
    # edit / add: the human's verbatim value.
    new_value: Optional[str] = None
    # add: the new fact's person, label, evidence character, and supporting words.
    persona_id: Optional[str] = None
    field: Optional[str] = None
    evidence_type: Optional[EvidenceType] = None
    snippet_text: Optional[str] = None

    @model_validator(mode="after")
    def _inputs_by_action(self) -> "CorrectionRequest":
        if self.action == CorrectionAction.EDIT:
            if not self.assertion_id:
                raise ValueError("an 'edit' correction requires assertion_id")
            if self.new_value is None:
                raise ValueError("an 'edit' correction requires new_value")
        elif self.action == CorrectionAction.RETRACT:
            if not self.assertion_id:
                raise ValueError("a 'retract' correction requires assertion_id")
            if self.new_value is not None:
                raise ValueError("a 'retract' correction takes no new_value")
        elif self.action == CorrectionAction.ADD:
            if self.assertion_id is not None:
                raise ValueError("an 'add' correction has no target assertion_id")
            missing = [
                name
                for name, value in (
                    ("persona_id", self.persona_id),
                    ("field", self.field),
                    ("new_value", self.new_value),
                    ("evidence_type", self.evidence_type),
                    ("snippet_text", self.snippet_text),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"an 'add' correction requires: {', '.join(missing)}")
        return self


class CorrectionOutcome(BaseModel):
    """What one applied correction produced."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    assertion: Assertion  # the superseding (edit/retract) or new (add) row
    event: CorrectionEvent


def apply_correction(
    request: CorrectionRequest,
    *,
    repository: Repository,
    captures_root: Union[str, Path],
    now: Optional[Callable[[], datetime]] = None,
    new_id: Optional[Callable[[], str]] = None,
) -> CorrectionOutcome:
    """Apply `request` to the capture it names, returning the produced correction.

    Resolves the capture folder (trust-boundary validated), reads its source id,
    and — for edit/retract — looks the target up among the capture's CURRENT
    assertions, so you can only correct a live fact (never an already-superseded
    row; that also respects the anti-fork unique index). Persists the produced
    assertion and its `correction_event` atomically. Raises `ValueError` (unknown
    target, malformed request) or `FileNotFoundError` (no such capture).
    """
    now = now or (lambda: datetime.now(timezone.utc))
    new_id = new_id or (lambda: uuid.uuid4().hex)

    folder = resolve_capture_folder(captures_root, request.capture_id)
    source_id = _source_id_from_manifest(folder)

    new_snippet: Snippet | None = None
    if request.action == CorrectionAction.ADD:
        assertion, event, new_snippet = _build_add(request, source_id, now, new_id)
    else:
        target = _resolve_target(repository, source_id, request.assertion_id)
        if request.action == CorrectionAction.EDIT:
            assertion, event = _build_edit(request, target, now, new_id)
        else:
            assertion, event = _build_retract(request, target, now, new_id)

    stored_assertion, stored_event = repository.record_correction(
        assertion=assertion, event=event, new_snippet=new_snippet
    )
    return CorrectionOutcome(source_id=source_id, assertion=stored_assertion, event=stored_event)


def _resolve_target(repository: Repository, source_id: str, assertion_id: Optional[str]) -> Assertion:
    """The current assertion `assertion_id` names, or a ValueError.

    Scanning `current_assertions` (not a raw row fetch) is the guard: it refuses
    an id that belongs to another source, or to a row already superseded — you
    correct what is live, which is exactly what the UI showed you.
    """
    for assertion in repository.current_assertions(source_id):
        if assertion.id == assertion_id:
            return assertion
    raise ValueError(f"no current assertion {assertion_id!r} on this capture")


def _build_edit(
    request: CorrectionRequest,
    target: Assertion,
    now: Callable[[], datetime],
    new_id: Callable[[], str],
) -> tuple[Assertion, CorrectionEvent]:
    # A superseding row carrying the human's value on the SAME snippet the model
    # read. `normalized_value` is cleared (the old normalization is stale) and no
    # model annotation is carried — a human-stated value needs no caveat; the
    # reason lives in the event. The field's evidential character is unchanged.
    corrected = target.model_copy(
        update={
            "id": new_id(),
            "verbatim_value": request.new_value,
            "normalized_value": None,
            "annotation": None,
            "supersedes_id": target.id,
            "retracted": False,
            "created_at": now(),
        }
    )
    event = CorrectionEvent(
        id=new_id(),
        source_id=target.source_id,
        action=CorrectionAction.EDIT,
        field=target.field,
        assertion_id=corrected.id,
        supersedes_id=target.id,
        previous_value=target.verbatim_value,
        new_value=request.new_value,
        actor=request.actor,
        rationale=request.rationale,
        created_at=now(),
    )
    return corrected, event


def _build_retract(
    request: CorrectionRequest,
    target: Assertion,
    now: Callable[[], datetime],
    new_id: Callable[[], str],
) -> tuple[Assertion, CorrectionEvent]:
    # A superseding row identical to the target but flagged `retracted`: it records
    # what was withdrawn (its value survives on this row for the log) while
    # `current_assertions` drops both it and the superseded target.
    retraction = target.model_copy(
        update={
            "id": new_id(),
            "supersedes_id": target.id,
            "retracted": True,
            "created_at": now(),
        }
    )
    event = CorrectionEvent(
        id=new_id(),
        source_id=target.source_id,
        action=CorrectionAction.RETRACT,
        field=target.field,
        assertion_id=retraction.id,
        supersedes_id=target.id,
        previous_value=target.verbatim_value,
        new_value=None,
        actor=request.actor,
        rationale=request.rationale,
        created_at=now(),
    )
    return retraction, event


def _build_add(
    request: CorrectionRequest,
    source_id: str,
    now: Callable[[], datetime],
    new_id: Callable[[], str],
) -> tuple[Assertion, CorrectionEvent, Snippet]:
    # A missed fact: a fresh snippet holding the human's supporting words (a text
    # anchor with no page offsets — it was supplied, not located) plus a new
    # assertion about an existing persona. The composite FK requires the snippet
    # and persona to belong to THIS source, which the shared source_id guarantees.
    snippet = Snippet(
        id=new_id(),
        source_id=source_id,
        verbatim_text=request.snippet_text or "",
        anchor=SnippetAnchor(kind="text"),
    )
    assertion = Assertion(
        id=new_id(),
        source_id=source_id,
        persona_id=request.persona_id or "",
        snippet_id=snippet.id,
        field=request.field or "",
        verbatim_value=request.new_value or "",
        evidence_type=request.evidence_type or EvidenceType.STATED,
        created_at=now(),
    )
    event = CorrectionEvent(
        id=new_id(),
        source_id=source_id,
        action=CorrectionAction.ADD,
        field=assertion.field,
        assertion_id=assertion.id,
        supersedes_id=None,
        previous_value=None,
        new_value=request.new_value,
        actor=request.actor,
        rationale=request.rationale,
        created_at=now(),
    )
    return assertion, event, snippet
