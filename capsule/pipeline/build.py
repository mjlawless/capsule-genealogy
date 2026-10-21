"""Turn a validated raw extraction + page snapshot into evidence models.

This is where the loosely-structured model reply becomes the strict,
source-linked evidence graph the contract demands: every Assertion gets a
generated id and is wired to exactly one Persona and one Snippet under one
Source. The Source's `content_hash` is the digest of the serialized
Extraction and `archive.sha256` is the digest of the snapshot bytes, so the
hashes the writer later re-checks are computed here from the same functions —
they cannot disagree.

Compliance is enforced structurally: `wayback_url` is always None in M0
(submission is deferred and gated pages are never submitted at all), and a
gated page is flagged `copyright_flag=True`, which the Source model requires.
"""
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

from capsule.capture.manifest import (
    SPEC_VERSION_V2,
    Extraction,
    serialize_extraction,
    sha256_hex,
)
from capsule.contracts.evidence import (
    ArchiveKind,
    ArchiveRecord,
    Assertion,
    Citation,
    CoverageClaim,
    GatingStatus,
    Persona,
    ProviderClaim,
    Snippet,
    SnippetAnchor,
    Source,
    SourceQuality,
)
from capsule.pipeline.citation import build_citation
from capsule.pipeline.extract import RawAssertion, RawExtraction, source_quality_of
from capsule.pipeline.page_identity import (
    ancestry_browse_path_from,
    archive_hierarchy_from,
    page_label_from,
)
from capsule.pipeline.external_ref import canonicalize_all
from capsule.pipeline.fetch import PageSnapshot
from capsule.pipeline.fieldname import snake_case

_SNAPSHOT_NAME = "snapshot.html"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[0-9a-z]+")  # content tokens, after tag-strip + casefold
_UNVERIFIED = "[unverified: snippet not located in source]"


def _normalize_for_match(text: str) -> str:
    """Loosely normalize page/snippet text for a location check.

    Strips HTML tags, unescapes entities, casefolds, and drops ALL whitespace so
    a "verbatim" snippet can be located despite markup and layout. Whitespace is
    removed (not just collapsed) because a real page renders a field as
    ``<b>Name</b>: Mary Lawless`` — stripping the tag leaves ``Name : ...`` with
    a space before the colon, which a snippet ``"Name: Mary Lawless"`` would not
    contain. Comparing only the visible non-space characters avoids that whole
    class of false "not on page" flags. This is a soft integrity check, not a
    security boundary — the authoritative control is human accept/correct in M1.
    """
    unescaped = html_lib.unescape(_TAG_RE.sub(" ", text))
    return _WS_RE.sub("", unescaped).casefold()


def _content_tokens(text: str) -> list[str]:
    """The alphanumeric tokens (≥2 chars) of `text`, tag-stripped and casefolded."""
    plain = html_lib.unescape(_TAG_RE.sub(" ", text)).casefold()
    return [t for t in _TOKEN_RE.findall(plain) if len(t) >= 2]


def _locate_snippet(snippet: str, page_text: str) -> bool:
    """True when `snippet` can be anchored in `page_text` (already normalized).

    Anchors PER CELL, not per joined value. A table-rendered transcript prints a
    single fact across several cells: a marriage record puts a bride's given name
    and surname in two adjacent ``<td>``s, under a ``Name:`` label cell two columns
    to the LEFT of the husband's name — so the model's verbatim snippet
    ``"Name: Sera McShane"`` is never one contiguous run of page text, even though
    every word of it is on the page. The old contiguous-substring test flagged all
    such snippets ``[unverified]`` — on a marriage record that is every name but the
    first-column groom's, i.e. the evidence itself. Requiring instead that every
    content token (``name``, ``sera``, ``mcshane``) appear anchors the snippet
    cell-by-cell. This only ever RELAXES the check — a contiguous match implies all
    its tokens match — so nothing that verified before is newly flagged; and a
    fabricated snippet still fails, because at least one invented token is absent.
    """
    tokens = _content_tokens(snippet)
    if not tokens:  # degenerate (e.g. a single-digit value): fall back to contiguous
        return _normalize_for_match(snippet) in page_text
    return all(t in page_text for t in tokens)


def _flag_unverified(annotation: Optional[str], located: bool) -> Optional[str]:
    """Append the unverified marker to `annotation` when the snippet wasn't found."""
    if located:
        return annotation
    return f"{annotation} {_UNVERIFIED}".strip() if annotation else _UNVERIFIED


def _field_name(label: str, *, kind: str) -> str:
    """Fold a model-reported field label to snake_case, or fail loudly.

    A label with no alphanumerics ("---", "?") is not a field name; storing it
    would produce an assertion no query could ever find and no human could read.
    Fail the capture instead — the raw reply survives in `agent_log/` either way.
    """
    folded = snake_case(label)
    if not folded:
        raise ValueError(f"{kind} {label!r} has no alphanumeric characters")
    return folded


@dataclass(frozen=True)
class BuiltCapture:
    """The three top-level pieces a capture is written and indexed from."""

    source: Source
    citation: Citation
    extraction: Extraction


def build_capture_models(
    snapshot: PageSnapshot,
    raw: RawExtraction,
    *,
    new_id: Callable[[], str],
    now: Callable[[], datetime],
    page_label: Optional[str] = None,
) -> BuiltCapture:
    """Assemble source-linked evidence models from `raw`; ids come from `new_id`.

    `page_label` (REQ-007 §1) is the LIVE reading from the browser when there is one, and
    it WINS over parsing the snapshot. That is not a preference, it is a correctness
    requirement — see `page_identity.page_label_from` for why the archived HTML cannot be
    trusted on this one point.
    """
    moment = now()
    source_id = new_id()

    personas: list[Persona] = []
    ref_to_id: dict[str, str] = {}  # RawPersona.id (model ref) -> generated persona id
    label_to_id: dict[str, str] = {}  # unique label -> generated id (legacy reference)
    ambiguous_labels: set[str] = set()  # labels shared by >1 persona on this page
    for rp in raw.personas:
        persona_id = new_id()
        personas.append(
            Persona(id=persona_id, source_id=source_id, role=rp.role, label=rp.label)
        )
        # Repeated LABELS are allowed — two distinct people can share a name — but a
        # repeated model-assigned id is a genuine ambiguity in the reply.
        if rp.id is not None:
            if rp.id in ref_to_id:
                raise ValueError(f"duplicate persona id {rp.id!r}: persona ids must be unique")
            ref_to_id[rp.id] = persona_id
        if rp.label in label_to_id:
            ambiguous_labels.add(rp.label)
        else:
            label_to_id[rp.label] = persona_id

    def _resolve_persona(ra: RawAssertion) -> str:
        """The generated persona id a raw assertion refers to.

        Prefers `persona_id` (the model's per-reply ref), which is unambiguous even
        when names repeat. A legacy `persona` label reference is honored only when
        the label is unique; a label shared by two personas is refused with a clear
        instruction to reference by id, rather than silently binding to the first.
        """
        if ra.persona_id is not None:
            resolved = ref_to_id.get(ra.persona_id)
            if resolved is None:
                raise ValueError(
                    f"assertion references unknown persona id {ra.persona_id!r}; "
                    f"known ids: {sorted(ref_to_id)}"
                )
            return resolved
        if ra.persona in ambiguous_labels:
            raise ValueError(
                f"assertion references persona by the non-unique label {ra.persona!r}; "
                "give each persona an id and reference it by persona_id"
            )
        resolved = label_to_id.get(ra.persona) if ra.persona is not None else None
        if resolved is None:
            raise ValueError(
                f"assertion references unknown persona {ra.persona!r}; "
                f"known personas: {sorted(label_to_id)}"
            )
        return resolved

    page_text = _normalize_for_match(snapshot.html)
    snippets: list[Snippet] = []
    assertions: list[Assertion] = []
    for ra in raw.assertions:
        resolved_persona_id = _resolve_persona(ra)
        snippet_id = new_id()
        snippets.append(
            Snippet(
                id=snippet_id,
                source_id=source_id,
                verbatim_text=ra.snippet,
                anchor=SnippetAnchor(kind="text"),
            )
        )
        # Soft integrity check: a "verbatim" snippet the model claims came from
        # the page but that cannot be located in it is flagged for the human
        # reviewer (guards hallucination / prompt injection) without dropping it.
        # Anchored per cell (see `_locate_snippet`): a name split across table
        # cells is real evidence, not an unlocated snippet.
        located = _locate_snippet(ra.snippet, page_text)
        # The model copies the record's printed label; folding it to snake_case is
        # a deterministic transform, so code owns it. `normalized_field` is the
        # model's judgment and passes through untouched — a human must be able to
        # see, and disagree with, exactly the synonym it chose.
        normalized_field = (
            _field_name(ra.normalized_field, kind="normalized_field")
            if ra.normalized_field
            else None
        )
        assertions.append(
            Assertion(
                id=new_id(),
                source_id=source_id,
                persona_id=resolved_persona_id,
                snippet_id=snippet_id,
                field=_field_name(ra.field, kind="field"),
                normalized_field=normalized_field,
                verbatim_value=ra.value,
                normalized_value=ra.normalized_value,
                annotation=_flag_unverified(ra.annotation, located),
                evidence_type=ra.evidence_type,
                confidence=ra.confidence,
                created_at=moment,
            )
        )

    # What the INDEX says it holds. Snippet-linked like any other claim — the verbatim
    # text declaring the range stays one hop away, so the parsed years can be audited
    # against what the page actually printed. NO persona: a coverage row is a statement
    # about the source, and inventing a person to carry it would let a claim about a
    # finding aid contaminate claims about a human.
    coverage: list[CoverageClaim] = []
    for rc in raw.coverage:
        snippet_id = new_id()
        snippets.append(
            Snippet(
                id=snippet_id,
                source_id=source_id,
                verbatim_text=rc.snippet,
                anchor=SnippetAnchor(kind="text"),
            )
        )
        located = _locate_snippet(rc.snippet, page_text)
        coverage.append(
            CoverageClaim(
                id=new_id(),
                source_id=source_id,
                snippet_id=snippet_id,
                place=rc.place,
                record_type=rc.record_type,
                year_start=rc.year_start,
                year_end=rc.year_end,
                evidence_type=rc.evidence_type,
                # The same hallucination guard an assertion gets: a coverage range the
                # model says it read but which is not on the page is FLAGGED, not
                # trusted. A fabricated range is the worst outcome here — it would make
                # an absence look meaningful when the index never covered those years.
                annotation=_flag_unverified(rc.annotation, located),
                created_at=moment,
            )
        )

    # What the PROVIDER says about the record — Ancestry's Source Citation / Source
    # Information / Original data. Snippet-linked and hallucination-guarded like every
    # other claim: a fabricated "Original data" line would send a researcher to a
    # microfilm that does not exist, which is worse than having no citation at all.
    provider_claims: list[ProviderClaim] = []
    for rp in raw.provider_claims:
        snippet_id = new_id()
        snippets.append(
            Snippet(
                id=snippet_id,
                source_id=source_id,
                verbatim_text=rp.snippet,
                anchor=SnippetAnchor(kind="text"),
            )
        )
        located = _locate_snippet(rp.snippet, page_text)
        provider_claims.append(
            ProviderClaim(
                id=new_id(),
                source_id=source_id,
                snippet_id=snippet_id,
                key=_field_name(rp.key, kind="field"),
                verbatim_value=rp.value,
                annotation=_flag_unverified(rp.annotation, located),
                created_at=moment,
            )
        )

    # The model reported the links it saw; every identifier is derived here, so
    # nothing an LLM invented becomes a key we later bind two sources on.
    extraction = Extraction(
        personas=tuple(personas),
        snippets=tuple(snippets),
        assertions=tuple(assertions),
        external_refs=canonicalize_all([(r.url, r.label) for r in raw.external_refs]),
        coverage=tuple(coverage),
        provider_claims=tuple(provider_claims),
        # Why this page yielded what it yielded, in the model's own words. For an
        # unindexed register scan ("no transcribable text is present") that sentence IS
        # the finding, and it used to survive only in agent_log/.
        notes=raw.notes,
    )
    content_hash = sha256_hex(serialize_extraction(extraction))
    snapshot_digest = sha256_hex(snapshot.html.encode("utf-8"))

    gating = snapshot.gating
    archive = ArchiveRecord(
        local_snapshot=_SNAPSHOT_NAME,
        sha256=snapshot_digest,
        captured_at=snapshot.fetched_at,
        wayback_url=None,  # M0: submission deferred; gated pages never submitted
    )
    # REQ-007 §1/§4 — what the page STATES about itself: which leaf, and the archival
    # trail above it. Structural, deterministic, never asked of the model.
    resolved_page_label = page_label or page_label_from(snapshot.html, snapshot.url)
    hierarchy = archive_hierarchy_from(snapshot.html, snapshot.url)
    # BUG-020 — an Ancestry browse image's path (county > volume > image N of M).
    browse = ancestry_browse_path_from(snapshot.html, snapshot.url)

    source = Source(
        id=source_id,
        url=snapshot.url,
        title=snapshot.title,
        site_name=snapshot.site_name,
        record_type=raw.record_type,
        gating_status=gating,
        copyright_flag=(gating == GatingStatus.GATED),
        # The model's judgment, with ORIGINAL earned rather than assumed: silence or a
        # hedge lands on DERIVATIVE (see `source_quality_of`). This was hardcoded to
        # DERIVATIVE for every capture, which quietly filed the NLI register scan — an
        # ORIGINAL — as a copy. Nothing read it yet, but the M2 matcher prefers
        # originals, and "the original lost to the derivative because a builder set a
        # default" is the silent-corruption class this project exists to prevent.
        source_quality=source_quality_of(raw),
        source_quality_rationale=raw.source_quality_rationale,
        # REQ-007 §1 — read STRUCTURALLY off the snapshot, never asked of the model: which
        # leaf was on screen is a fact about the document, not a reading of the record.
        page_label=resolved_page_label,
        archive=archive,
        content_hash=content_hash,
    )
    # A record transcript cites the RECORD (record -> database -> what the database
    # cites), composed from the extraction we just built; a page with no record
    # falls back to a title-only web citation inside build_citation.
    citation = build_citation(
        citation_id=new_id(),
        source_id=source_id,
        snapshot=snapshot,
        access_date=snapshot.fetched_at.astimezone(timezone.utc).date(),
        archived_url=snapshot.url,
        record_type=raw.record_type,
        personas=personas,
        assertions=assertions,
        external_refs=extraction.external_refs,
        # BUG-014: fold the provider's Original data / Source Citation (the NARA microfilm,
        # roll, ED) into the reference note so it names the ORIGINAL, not just the database.
        provider_claims=extraction.provider_claims,
        # REQ-007 §4 — the archive that HOLDS the register, and which leaf this is.
        # Drives `register-image-v1` for a viewer page with no persons to cite from.
        hierarchy=hierarchy,
        page_label=resolved_page_label,
        browse=browse,
    )
    return BuiltCapture(source=source, citation=citation, extraction=extraction)


def _document_title_from_url(url: str) -> Optional[str]:
    """A human title from a document URL's filename: the last path segment,
    URL-decoded, extension dropped, `_`/`+` turned to spaces.

    `.../Spring_2025%20Newsletter.pdf` -> `Spring 2025 Newsletter`. This never
    reads the document's own bytes — the title is derived only from the URL the
    user handed us, so no untrusted PDF internals are parsed at M0.
    """
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    stem = name.rsplit(".", 1)[0] if "." in name else name
    title = re.sub(r"\s+", " ", stem.replace("_", " ").replace("+", " ")).strip()
    return title or None


def _site_from_url(url: str) -> Optional[str]:
    netloc = urlparse(url).netloc.lower()
    netloc = netloc[4:] if netloc.startswith("www.") else netloc
    return netloc or None


def build_document_capture_models(
    *,
    url: str,
    document_bytes: bytes,
    ext: str,
    media_type: Optional[str],
    gating: GatingStatus,
    fetched_at: datetime,
    new_id: Callable[[], str],
    title: Optional[str] = None,
    site_name: Optional[str] = None,
    provenance: Optional[str] = None,
    record_type: Optional[str] = None,
    source_quality: Optional[SourceQuality] = None,
) -> BuiltCapture:
    """Assemble the models for an archive-only document capture (capture-folder/v2).

    There is no extraction at M0 — reading a document is vision/HTR (M4) — so this
    builds an EMPTY `Extraction` (a first-class zero-assertion capture) whose digest
    is `content_hash`, and an `ArchiveRecord` of kind `document` whose `sha256`
    attests the document bytes. Title/site default from the URL (never from the
    document's own bytes). Compliance holds by construction: `wayback_url` is None
    and a gated document is `copyright_flag=True`.

    A local file (`file://`) is forced to the GATED posture regardless of the
    caller's request: it is not a public web resource (it can't be archived to
    Wayback) and is typically a purchased, copyright artifact — a DAR record
    downloaded to disk, say. Fail-safe: this only ever restricts (copyright-flagged,
    never republished), never over-shares.

    `provenance`, `record_type` and `source_quality` are set only by `capsule ingest`
    (REQ-008), where the researcher states them: nothing read the document, so a stated
    `source_quality` carries a rationale saying exactly that. Left unset, a document
    capture is what it always was — unknown record type, DERIVATIVE.
    """
    if urlparse(url).scheme == "file":
        gating = GatingStatus.GATED

    source_id = new_id()
    extraction = Extraction()  # empty: no personas/snippets/assertions
    content_hash = sha256_hex(serialize_extraction(extraction))

    resolved_title = title or _document_title_from_url(url)
    resolved_site = site_name or _site_from_url(url)

    archive = ArchiveRecord(
        local_snapshot=f"document.{ext}",
        sha256=sha256_hex(document_bytes),
        captured_at=fetched_at,
        wayback_url=None,
        kind=ArchiveKind.DOCUMENT,
        media_type=media_type,
        provenance=provenance,
    )
    source = Source(
        id=source_id,
        url=url,
        title=resolved_title,
        site_name=resolved_site,
        record_type=record_type,  # unknown until extraction (M4), unless the researcher says
        gating_status=gating,
        copyright_flag=(gating == GatingStatus.GATED),
        source_quality=source_quality or SourceQuality.DERIVATIVE,
        source_quality_rationale=(
            "stated by the researcher at ingest; the document itself was not read"
            if source_quality is not None
            else None
        ),
        archive=archive,
        content_hash=content_hash,
        spec_version=SPEC_VERSION_V2,
    )
    citation = build_citation(
        citation_id=new_id(),
        source_id=source_id,
        snapshot=SimpleNamespace(url=url, title=resolved_title, site_name=resolved_site),
        access_date=fetched_at.astimezone(timezone.utc).date(),
        archived_url=url,
    )
    return BuiltCapture(source=source, citation=citation, extraction=extraction)
