"""The machine-transcript layer (REQ-006) — a reading aid, never evidence.

Owner ruling R1 (2026-09-24): transcribed values live here, in their own
append-only records, and NEVER in `Assertion`. A vision transcript is a
derivative of the image (REQ-006 §5.1): the image stays the cited evidence,
and nothing in this layer can raise a confidence grade (§5.2). Promoting a
transcribed value into the evidence graph is a separate, later, human act —
through the correction loop — which is when an image-anchored `Snippet` is
created for it. That is why a field carries its crop reference directly
rather than through `snippet`: the evidence tables hold only what a human or
the page itself has stated.

Two layers per field, per the plan (§5.3): `verbatim_value` exactly as written
(uncertainty marked inline — `[?]`, `[?reading]`, `[a/b?]`, `[illegible]`), and
any normalisation or explanation in `annotation`. An informant's non-answer
("Unknown") is a value, not a null (§5.6).
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from capsule.contracts.evidence import _require_utc

_MARKER_RE = re.compile(r"\[")


class TranscriptStatus(str, Enum):
    VALUE = "value"  # an entry is written; verbatim_value holds it (markers allowed)
    UNKNOWN = "unknown"  # the record states the informant did not know — words as written
    BLANK = "blank"  # the space is empty; verbatim_value is None
    ILLEGIBLE = "illegible"  # something is written, nothing readable; value is the marker


def check_reading(status: TranscriptStatus, verbatim: Optional[str], confidence: float) -> None:
    """Raise ValueError unless status, value, and confidence agree.

    Shared by the contract and by the transcriber's guard, so a reading the
    pipeline accepts is exactly a reading the store will accept.
    """
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence {confidence} outside 0..1")
    text = (verbatim or "").strip()
    if status in (TranscriptStatus.VALUE, TranscriptStatus.UNKNOWN) and not text:
        raise ValueError(f"status {status.value} needs the words as written")
    if status is TranscriptStatus.BLANK and text:
        raise ValueError("status blank must carry no value")
    if status is TranscriptStatus.ILLEGIBLE and not _MARKER_RE.search(text):
        raise ValueError("status illegible must carry a marker such as [illegible]")


class Transcription(BaseModel):
    """One transcriber run over one media file of one source. Re-running is a new
    Transcription; nothing is overwritten."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    media_filename: str  # the capture's media file that was read
    media_sha256: str  # of that file, so the transcript is pinned to exact bytes
    model: str
    status: Literal["complete", "partial", "failed"]
    problems: tuple[str, ...] = ()  # the guard's findings, verbatim
    layout_notes: Optional[str] = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class TranscriptField(BaseModel):
    """One transcribed field, pinned to the exact crop it was read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    transcription_id: str
    source_id: str
    page: Optional[int]  # 0-based PDF page; None for a single image
    field_key: str
    form_label: Optional[str]  # the printed label, as printed
    status: TranscriptStatus
    verbatim_value: Optional[str]
    annotation: Optional[str] = None
    confidence: float
    needs_review: bool
    note: Optional[str] = None
    attempts: int = 1
    crop_filename: str  # relative to the transcription's folder, e.g. "crops/p1-age.png"
    crop_sha256: str
    crop_bbox: tuple[int, int, int, int]  # native pixels of the page image
    crop_rotation: int
    agent_call_id: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("crop_rotation")
    @classmethod
    def _right_angle(cls, value: int) -> int:
        if value not in (0, 90, 180, 270):
            raise ValueError(f"crop_rotation must be 0/90/180/270, got {value}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "TranscriptField":
        check_reading(self.status, self.verbatim_value, self.confidence)
        return self


class TranscriptComparison(BaseModel):
    """One index claim set beside one transcribed field that records the same fact.

    REQ-006 §3: when the image and the index disagree, the disagreement is the
    finding — both values are kept here and NEITHER wins. `agree` is recorded as
    information only; it never raises a grade (§5.2). `unclear` covers a pair
    that cannot be judged (an illegible image value, a partial date).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    transcription_id: str
    assertion_id: str  # the index / DOM claim
    transcript_field_id: str  # what the image says
    verdict: Literal["agree", "conflict", "unclear"]
    index_value: str  # denormalised, so the record reads alone
    transcript_value: Optional[str]
    explanation: Optional[str] = None
    agent_call_id: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def _conflict_says_why(self) -> "TranscriptComparison":
        if self.verdict == "conflict" and not (self.explanation or "").strip():
            raise ValueError("a conflict must say what differs")
        return self
