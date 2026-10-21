"""Contract 1 — the evidence data model: Source -> Citation -> Assertion.

Every assertion carries a link back to the source snippet that supports it
and an evidence type (stated | calculated | estimated). Storage built on top
of these models is append-only: corrections are new Assertion rows with
`supersedes_id` set, never in-place edits. See docs/plan.md §13 and
docs/specs/capture-folder-v1.md for the on-disk shape these models mirror.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def _require_utc(value: datetime) -> datetime:
    """Shared guard: capture timestamps must be timezone-aware AND in UTC.

    The schema stores datetimes as ISO-8601 UTC TEXT, so a non-UTC offset
    (e.g. +05:00) would silently violate that contract. Reject it here.
    """
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (UTC)")
    if value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be in UTC (offset must be +00:00)")
    return value


class EvidenceType(str, Enum):
    """F6 — every fact carries its evidential character."""

    STATED = "stated"
    CALCULATED = "calculated"
    ESTIMATED = "estimated"


class GatingStatus(str, Enum):
    """Whether the source page was freely accessible or login/paywall gated."""

    PUBLIC = "public"
    GATED = "gated"


class SourceQuality(str, Enum):
    """Original (primary record) vs. derivative (transcription/index)."""

    ORIGINAL = "original"
    DERIVATIVE = "derivative"


class CorrectionAction(str, Enum):
    """What a human did to a model-extracted fact when reviewing a capture.

    `edit` — the model read the fact but got the value wrong (a superseding
    assertion carries the corrected value). `add` — the model missed a fact
    entirely (a brand-new assertion, backed by a human-supplied snippet). `retract`
    — the model asserted a fact that is not actually on the source (a superseding
    assertion flagged `retracted`). Together these are the whole vocabulary of the
    accept/correct loop; "accept" logs nothing because it changes nothing.
    """

    EDIT = "edit"
    ADD = "add"
    RETRACT = "retract"


class ExternalRef(BaseModel):
    """A pointer a page gives to the source document it indexes or transcribes.

    A derivative source often names its original: a RootsIreland index entry links
    the NLI parish register it was transcribed from. Capturing that pointer costs
    nothing at capture time and cannot be reconstructed later, so we record it —
    but we only ever *record* it. Following it would be a background request to a
    provider the user did not drive (plan §11).

    `identifier` names the referenced document; `locator` names the place inside it
    (a leaf, a page) and is **usually absent**, because most index entries resolve
    the register and not the page. That asymmetry is the whole reason binding this
    to a local `Source` is a separate, evidence-typed decision at M2 rather than
    something the capture pipeline may assume. See
    `docs/design/rootsireland-nli-linkage.md`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority: str  # e.g. "nli-registers"; the host when unrecognized
    identifier: str  # e.g. "vtls000632043"
    locator: Optional[str] = None  # e.g. "page/26"; None = document-level only
    url: str  # verbatim, as it appeared on the page
    label: Optional[str] = None  # verbatim link text, if any

    @field_validator("authority", "identifier")
    @classmethod
    def _must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("authority and identifier must be non-empty")
        return value


class SnippetAnchor(BaseModel):
    """Where a Snippet's verbatim text/image lives within the capture."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["text", "image"]
    start: Optional[int] = None
    end: Optional[int] = None
    media_filename: Optional[str] = None
    bbox: Optional[tuple[int, int, int, int]] = None


class ArchiveKind(str, Enum):
    """What the capture's primary archived artifact IS.

    `snapshot` — an HTML page in view (`snapshot.html`), the original M0 shape.
    `document` — a fetched document the page *is*, e.g. a PDF (`document.pdf`),
    with no HTML page. A document capture carries no extraction at capture time
    (reading a PDF is vision/HTR, deferred to M4); it is a first-class
    zero-assertion capture, exactly like an unindexed register scan.
    """

    SNAPSHOT = "snapshot"
    DOCUMENT = "document"


class ArchiveRecord(BaseModel):
    """The archival evidence for a captured page or document.

    `local_snapshot` names the primary archived artifact — `snapshot.html` for a
    page, `document.<ext>` for a document — and `sha256` is the digest of exactly
    those bytes. The field keeps its historical name across both kinds so the
    on-disk manifest and this model stay one-to-one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    local_snapshot: str
    sha256: str
    captured_at: datetime
    wayback_url: Optional[str] = None
    kind: ArchiveKind = ArchiveKind.SNAPSHOT
    # The declared media type of the primary artifact (e.g. "application/pdf").
    # Meaningful for `document` captures; left None for HTML snapshots.
    media_type: Optional[str] = None
    # How the primary artifact reached Capsule when it was NOT read off the page in view
    # (REQ-008): "site:official-download" (the source's own export control) or
    # "user:file" (a file the researcher supplied by hand). None for every browser
    # capture. The distinction is evidentiary: one is the provider's own artifact, the
    # other a file of unstated origin that happens to be in the researcher's possession.
    provenance: Optional[str] = None

    @field_validator("captured_at")
    @classmethod
    def _captured_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _require_utc(value)


class Source(BaseModel):
    """The record of the captured page itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    url: str
    title: Optional[str] = None
    site_name: Optional[str] = None
    record_type: Optional[str] = None
    gating_status: GatingStatus
    copyright_flag: bool
    source_quality: SourceQuality
    # WHY this source was called original or derivative, in the model's words. The
    # judgment is a claim like any other, so it must be auditable rather than a bare
    # enum a builder set. None means nobody made the judgment — which, per the rule
    # below, is stored as DERIVATIVE (the weaker claim), not as an unearned ORIGINAL.
    source_quality_rationale: Optional[str] = None
    # WHICH page of a multi-image source this capture holds, in the source's own words —
    # the text of its page selector (REQ-007 §1). A tile viewer serves all 173 images of a
    # register at ONE URL, so without this a citation cannot name the page it cites, and
    # three captures on disk prove it: same URL, same title, different leaves, nothing to
    # tell them apart. None means the source has no page selector we recognize, which is
    # most of the corpus and must never be read as "page 1".
    page_label: Optional[str] = None
    archive: ArchiveRecord
    content_hash: str
    spec_version: str = "capture-folder/v1"

    @model_validator(mode="after")
    def _gating_invariants(self) -> "Source":
        if self.gating_status == GatingStatus.GATED:
            if not self.copyright_flag:
                raise ValueError("gated sources must have copyright_flag=True")
            if self.archive.wayback_url is not None:
                raise ValueError("gated sources must not have a wayback_url")
        return self


class Snippet(BaseModel):
    """A verbatim excerpt of the source, anchored to its location."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    verbatim_text: str
    anchor: SnippetAnchor


class Persona(BaseModel):
    """A person as they appear ON THE SOURCE — not a tree person.

    Deliberately has NO `tree_person_id` field. Personas are source-scoped
    evidence; linking a persona to a tree person is a Matcher-agent (M2)
    concern that lives in a separate link table (see `persona_tree_link` in
    capsule/store/schema.sql), so a persona can be linked, re-linked, or left
    unresolved without ever mutating the evidence record itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    role: str
    label: str


class Assertion(BaseModel):
    """A single extracted fact, always source- and snippet-linked.

    Field names get the same verbatim/normalized split as values, and for the same
    reason. `field` is the label the record itself prints, mechanically folded to
    snake_case (see `capsule.pipeline.fieldname`); `normalized_field` is our
    canonical genealogical term for it, and is optional.

    **`normalized_field` is for synonyms, not inferences.** `Type` -> `record_type`
    names the same fact in different words. `Borough` -> `death_place` does not: a
    New York death certificate states the borough it was *filed* in, and calling
    that the place of death asserts something the page never says. That asymmetry
    is what makes the rule necessary — `normalized_value` is safe because `"46 y"`
    -> `"46"` adds no meaning, whereas a canonical field name *can*. If the
    canonical field would claim more than the printed label, leave
    `normalized_field` None and make the claim a separate assertion typed
    `calculated`, with an annotation explaining the derivation. See
    `docs/design/field-naming.md`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    persona_id: str
    snippet_id: str
    field: str
    normalized_field: Optional[str] = None
    verbatim_value: str
    normalized_value: Optional[str] = None
    annotation: Optional[str] = None
    evidence_type: EvidenceType
    confidence: Optional[float] = None
    supersedes_id: Optional[str] = None
    # A human retraction. A superseding assertion marked `retracted` says "this
    # fact is wrong, not merely revised"; `current_assertions` filters it (and its
    # target) out. Additive and omitted-when-False in serialization (see
    # `_assertion_payload`), so a model-extracted assertion — always False — encodes
    # byte-for-byte as it did before this field existed and keeps its content_hash.
    retracted: bool = False
    created_at: datetime

    @field_validator("field")
    @classmethod
    def _field_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be non-empty")
        return value

    @field_validator("normalized_field")
    @classmethod
    def _normalized_field_must_not_be_blank(cls, value: Optional[str]) -> Optional[str]:
        # A blank string would read as "we have a canonical name for this" while
        # carrying none. Absent is a different claim from empty; keep them distinct.
        if value is not None and not value.strip():
            raise ValueError("normalized_field must be None or non-empty")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not (0.0 <= value <= 1.0):
            raise ValueError("confidence must be within 0.0..1.0")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _require_utc(value)


class CoverageClaim(BaseModel):
    """What an INDEX says it covers — a claim about a source, not about a person.

    "Kilmore, baptisms, 1845-1880" is the declaration that makes a *negative* search
    usable: I searched and found nothing, AND the index says it covers those years,
    therefore the absence is real. Without it an empty search is just an empty
    search — the trap that let a false premise stand for twelve research sessions.

    Deliberately has NO `persona_id`, unlike `Assertion`. A coverage row is about the
    index's holdings; there is no person in it. Inventing a persona to carry one
    would put a non-person in the persona table and let a statement about a FINDING
    AID contaminate statements about a HUMAN — the same firewall as the rule that an
    index and the register it derives from are never merged.

    Evidence-typed like everything else: `stated` when the page prints the range,
    `calculated`/`estimated` when it was derived. Snippet-linked, so the verbatim
    text behind the claim is always one hop away and the parsed range can be audited
    against what the page actually said. The years are parsed for querying; the
    snippet stays the truth.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    snippet_id: str
    # Verbatim, as the page prints them — a coverage table's own vocabulary
    # ("Kilmore", "R.C. Baptisms") is evidence, not ours to tidy.
    place: str
    record_type: str
    # Parsed, for the "was 1852 covered?" question. Optional because a page may
    # declare an open-ended range ("1845-present") or an unparseable one, and a range
    # we could not read must NOT be coerced into a number that would answer that
    # question wrongly. None means "cannot answer" — see `covers`.
    year_start: Optional[int] = None
    year_end: Optional[int] = None
    evidence_type: EvidenceType = EvidenceType.STATED
    annotation: Optional[str] = None
    created_at: datetime

    @field_validator("place", "record_type")
    @classmethod
    def _must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("place and record_type must be non-empty")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def _years_run_forwards(self) -> "CoverageClaim":
        if (
            self.year_start is not None
            and self.year_end is not None
            and self.year_end < self.year_start
        ):
            raise ValueError(
                f"coverage range runs backwards: {self.year_start}-{self.year_end}"
            )
        return self

    def covers(self, year: int) -> bool:
        """Does this claim cover `year`? An open end is open; an unread range is not.

        A claim with NO parsed years covers NOTHING. It cannot answer the question it
        exists to answer, and a confident wrong answer here is precisely the bug P2
        kills — so it declines to answer instead of guessing.
        """
        if self.year_start is None and self.year_end is None:
            return False
        if self.year_start is not None and year < self.year_start:
            return False
        if self.year_end is not None and year > self.year_end:
            return False
        return True


class ProviderClaim(BaseModel):
    """What the PROVIDER says about this record — its own description of the source.

    Ancestry prints three blocks under every record: `Source Citation` (roll, page,
    enumeration district), `Source Information` (the database), and — the one that
    matters most — **`Original data`**, naming the actual record the database was made
    from ("...NARA microfilm T623, 1854 rolls"). That line is what lets a citation name
    the MICROFILM rather than the database, which is the difference between a derivative
    citation and a layered one.

    A claim about the SOURCE, not about a person — so, like `CoverageClaim`, it carries no
    `persona_id`. There is no human in "NARA T623, 1854 rolls", and inventing one to hold
    it would put a non-person in the persona table.

    Verbatim, because a provider's own description of its holdings is evidence and not
    ours to tidy — and because once Ancestry is gone, this text is all that remains of
    what they claimed. Snippet-linked and evidence-typed like every other claim.

    `key` is a documented vocabulary rather than an enum, so an unrecognized block from
    some other provider is KEPT rather than dropped for not fitting a closed list. Known
    keys: `source_citation`, `source_information`, `original_data`, `repository`,
    `image_sequence`, `breadcrumb`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    snippet_id: str
    key: str
    verbatim_value: str
    evidence_type: EvidenceType = EvidenceType.STATED
    annotation: Optional[str] = None
    created_at: datetime

    @field_validator("key", "verbatim_value")
    @classmethod
    def _must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("key and verbatim_value must be non-empty")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _require_utc(value)


class Citation(BaseModel):
    """The layered Evidence Explained citation for a source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    reference_note: str
    short_note: str
    source_list_entry: str
    access_date: date
    archived_url: str
    template_id: str = "generic-web-v1"


class CaptureNote(BaseModel):
    """A researcher's free-text note about a capture — why it was collected,
    what to revisit, how it connects to a person or line.

    Authored by the *user*, not the source, so it is deliberately NOT an
    Assertion: it carries no snippet and no evidence type, and it never enters
    the sealed, hashed evidence. Notes are the analysis layer, distinct from the
    source and its information (Evidence Explained's research-note sense).

    Notes are append-only like every other record — revising is a new note, the
    latest is shown, history is retained — but they live in a mutable, verify-
    *excluded* layer (`notes.json`, the `capture_note` table), so a capture can
    accrue notes over its whole life without disturbing its cryptographic
    integrity. This is why a note may be attached long after capture, and why it
    works identically for a `snapshot` (HTML) or a `document` (PDF) capture.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    text: str
    author: str = "owner"
    created_at: datetime

    @field_validator("text")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note text must be non-empty")
        return value

    @field_validator("author")
    @classmethod
    def _author_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note author must be non-empty")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _require_utc(value)


class CorrectionEvent(BaseModel):
    """The append-only record of one human correction to a model extraction.

    The *evidence* of a correction is the assertion graph itself — a superseding
    row (edit/retract) or a new row (add). This event is the **flywheel log** on
    top of it: it remembers what the model said versus what the human said, so a
    corrected capture can be turned into an eval fixture automatically (CLAUDE.md:
    "corrected outputs become eval fixtures automatically"). Like `CaptureNote` it
    is the researcher's layer, verify-*excluded* and never part of the sealed
    `extraction.json`; unlike a note it is structured, and every field but
    `rationale`/`agent_call_id` is required so the log is self-describing without
    joining back to the assertion table.

    Invariants by `action` (enforced below), where `previous_value` is the model's
    verbatim value and `new_value` is the human's:
    - `edit`    — `supersedes_id` set; both values present (model X -> human Y).
    - `add`     — `supersedes_id` is None; `previous_value` None (nothing existed);
                  `new_value` present.
    - `retract` — `supersedes_id` set; `previous_value` present; `new_value` None
                  (the fact is removed, not replaced).

    `assertion_id` always names the assertion this correction *produced* (the
    superseding row for edit/retract, the new row for add), so the UI and the
    fixture writer can find the resulting fact directly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_id: str
    action: CorrectionAction
    field: str  # the affected field label, denormalized so the log reads alone
    assertion_id: str  # the assertion this correction produced
    supersedes_id: Optional[str] = None  # the corrected assertion (None for `add`)
    previous_value: Optional[str] = None  # what the model said (None for `add`)
    new_value: Optional[str] = None  # what the human said (None for `retract`)
    actor: str = "owner"
    rationale: Optional[str] = None
    agent_call_id: Optional[str] = None  # the model call that produced the original
    created_at: datetime

    @field_validator("field", "actor")
    @classmethod
    def _must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field and actor must be non-empty")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def _action_invariants(self) -> "CorrectionEvent":
        if self.action == CorrectionAction.ADD:
            if self.supersedes_id is not None:
                raise ValueError("an 'add' correction has no supersedes_id")
            if self.previous_value is not None:
                raise ValueError("an 'add' correction has no previous_value")
            if self.new_value is None:
                raise ValueError("an 'add' correction must carry a new_value")
        elif self.action == CorrectionAction.EDIT:
            if self.supersedes_id is None:
                raise ValueError("an 'edit' correction must set supersedes_id")
            if self.previous_value is None or self.new_value is None:
                raise ValueError("an 'edit' correction carries both values")
        elif self.action == CorrectionAction.RETRACT:
            if self.supersedes_id is None:
                raise ValueError("a 'retract' correction must set supersedes_id")
            if self.previous_value is None:
                raise ValueError("a 'retract' correction carries the previous_value")
            if self.new_value is not None:
                raise ValueError("a 'retract' correction has no new_value")
        return self
