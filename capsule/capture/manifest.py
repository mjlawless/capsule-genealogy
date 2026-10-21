"""The capture-folder writer and `verify` — spec: `capture-folder/v1`.

This is the deterministic, on-disk half of a capture: given the in-process
evidence models (Source, Citation, and an Extraction bundle of
Persona/Snippet/Assertion), plus the raw page snapshot and any downloaded
media, `write_capture` lays the folder out exactly as
`docs/specs/capture-folder-v1.md` prescribes and writes an authoritative
SHA-256 hash manifest. `verify_capture` re-establishes trust in that folder
after the fact by re-hashing every listed file and refusing any unlisted one.

No Claude API calls happen here — extraction/citation are produced upstream
(the M0 pipeline increment). This module only serializes, hashes, and checks.

Design notes:
- This module is the filesystem trust boundary. In M0+ media slugs are derived
  from *scraped* page content (untrusted), so every string that becomes a path
  component — media slug/ext, agent-log names, the capture_id, and the
  short-uuid derived from source.id — is validated against a strict allow-list
  before any `write`, and the resolved target is asserted to stay inside the
  capture folder. Nothing scraped can escape the capture directory.
- The writer is the single authority on serialization AND hashing. It refuses
  to write a capture whose model-declared hashes disagree with the bytes it is
  about to put on disk (`source.content_hash` MUST equal the digest of
  `extraction.json`; `archive.sha256` MUST equal the digest of
  `snapshot.html`) — an evidence-grade capture never ships a manifest that
  lies about its own contents. `verify_capture` re-checks these same
  MUST-equal invariants, so post-write tampering of those fields is caught too.
- Writes are atomic: the capture is built in a staging directory and renamed
  into place only once `manifest.json` is finalized. A crash or mid-write
  error leaves either nothing or a complete, verifiable capture — a capture_id
  is never half-written and bricked.
- Captures are append-only: the writer never overwrites an existing capture
  folder (raises `FileExistsError`).
- `agent_log/` is process provenance, excluded from the hash manifest and the
  verify extra-file scan (spec §5.1) — but only as a *directory*; a same-named
  file is not exempt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence
from urllib.parse import urlparse

from capsule.contracts.evidence import (
    ArchiveKind,
    Assertion,
    CaptureNote,
    Citation,
    CoverageClaim,
    ExternalRef,
    ProviderClaim,
    Persona,
    Snippet,
    Source,
    _require_utc,
)
from capsule.pipeline.household import HouseholdMember
from capsule.pipeline.render_check import (
    EVIDENCE_IMAGE_STATUSES,
    RECORD_IMAGE_PROVENANCE,
    blank_render_warning,
    looks_blank,
)
from capsule.pipeline.sufficiency import (
    is_tile_viewer_source,
    longest_raster_edge,
    record_media_shortfall,
    yielded_no_text_evidence,
)
from capsule.version import build_version

_logger = logging.getLogger(__name__)

SPEC_VERSION = "capture-folder/v1"
#: Document-primary captures (a PDF the page *is*, no HTML snapshot). Only these
#: carry `spec_version: capture-folder/v2`; an HTML capture stays v1 and
#: byte-identical to a pre-v2 one. See docs/specs/capture-folder-v2.md.
SPEC_VERSION_V2 = "capture-folder/v2"
HASH_ALGO = "sha256"
_HASH_CHUNK = 1 << 20  # 1 MiB streaming read for verify

#: What replaces text removed from `snapshot.html` before it is written. Chosen to
#: be improbable as page text — a record transcription may legitimately contain
#: "[redacted]", but not this. `verify_capture` looks for it, so it is part of the
#: on-disk contract, not a cosmetic string.
REDACTION_MARKER = "[capsule:redacted account-chrome]"

# Allow-lists for every string that becomes a filesystem path component.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")  # media slug
_EXT_RE = re.compile(r"^[a-z0-9]+$")  # media extension, no leading dot
_LOG_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")  # agent-log filename
# Spec §2 folder form. `\A...\Z` (not `^...$`): `$` also matches just before a
# trailing newline, so `^...$` would admit `"<id>\n"` — a real gap for an id that
# arrives from an untrusted caller (CLI arg / HTTP body) and is echoed back.
_CAPTURE_ID_RE = re.compile(r"\A[0-9]{8}-[a-z0-9]+-[a-z0-9]+\Z")

_SNAPSHOT_NAME = "snapshot.html"
#: A document capture's primary artifact: `document.<ext>` (ext lower-alnum). The
#: writer is the filesystem trust boundary, so it re-validates this name even
#: though the backend already sanitized the extension.
_DOCUMENT_NAME_RE = re.compile(r"^document\.[a-z0-9]+$")
#: The researcher's note file. Mutable and EXCLUDED from the hash manifest and the
#: verify extra-file scan (spec §5.1), exactly like `agent_log/` — notes accrue
#: over a capture's life and must never disturb its cryptographic integrity.
NOTES_NAME = "notes.json"

#: A machine-readable outcome summary for the capture folder (live-run finding). Like
#: `notes.json` it is an EXCLUDED sidecar — a convenience projection of the outcome, not
#: hashed evidence — so a consumer (or the driving agent) can read what a capture got
#: without re-deriving it from the manifest and measuring the image. Written after verify,
#: so it can carry `verify_ok`; never part of `archive.sha256`.
RESULT_NAME = "result.json"

#: REQ-006: machine transcripts of the capture's scanned media, one sub-folder per
#: transcriber run (`transcript/<transcription_id>/`). A derivative reading aid, NOT
#: evidence — so, like `notes.json`, it is EXCLUDED from the hash manifest and may be
#: added to a sealed capture at any time without invalidating it (capture-folder-v2 §4).
TRANSCRIPT_DIR = "transcript"

#: REQ-011: the capture's lifecycle (superseded by another capture, or reinstated), a
#: projection of the append-only `capture_lifecycle_event` store. Like `notes.json` it is an
#: EXCLUDED sidecar: a capture is superseded long after it was sealed, and saying so must not
#: invalidate its hashes. Its presence lets the folder-sourced index show the state.
LIFECYCLE_NAME = "lifecycle.json"


@dataclass(frozen=True)
class Extraction:
    """The structured extraction payload written to `extraction.json`.

    A frozen bundle of already-validated evidence models. Serialized in the
    fixed order personas -> snippets -> assertions [-> external_refs] for
    deterministic bytes (and thus a deterministic `content_hash`).

    `external_refs` and `coverage` are emitted only when non-empty, so a capture
    without them serializes byte-identically to before those fields existed and its
    `content_hash` is unchanged (spec `capture-folder/v1` §4b addendum).
    """

    personas: tuple[Persona, ...] = field(default_factory=tuple)
    snippets: tuple[Snippet, ...] = field(default_factory=tuple)
    assertions: tuple[Assertion, ...] = field(default_factory=tuple)
    external_refs: tuple[ExternalRef, ...] = field(default_factory=tuple)
    # What the page declares the INDEX itself covers (a holdings table). Empty for an
    # ordinary record page. See `CoverageClaim` in capsule.contracts.evidence.
    coverage: tuple[CoverageClaim, ...] = field(default_factory=tuple)
    # What the PROVIDER says about the record — Ancestry's `Source Citation`, `Source
    # Information`, and the `Original data` line naming the microfilm the database was
    # made from. See `ProviderClaim` in capsule.contracts.evidence.
    provider_claims: tuple[ProviderClaim, ...] = field(default_factory=tuple)
    # The MODEL's note about the page — most often WHY there are no assertions ("the
    # record is a scanned image; no transcribable text"). Provenance, not evidence, and
    # distinct from a researcher's `CaptureNote`: this is the extractor's own voice.
    # Without it, a zero-assertion capture is indistinguishable from a failed one.
    notes: Optional[str] = None


@dataclass(frozen=True)
class MediaItem:
    """One downloaded media file, written to `media/<n>-<slug>.<ext>`.

    `source_url` and `mime` are evidence-grade provenance — where the byte
    stream came from and what it claims to be — recorded in the manifest's
    `media[]` array. Both are optional metadata (not path components), stored
    verbatim like `source.url`; they are not fetched or trusted by this module.

    `provenance` records *how the URL was arrived at* when it was not simply read
    off the page. A tiled book viewer exposes no fetchable full-page image, so the
    capturer asks the viewer for the leaf in view and synthesizes a whole-image
    URL. That URL is not something a reader could have found in the DOM, and an
    evidence store must not pretend otherwise.
    """

    slug: str
    ext: str  # extension without the leading dot, e.g. "jpg"
    data: bytes
    source_url: Optional[str] = None
    mime: Optional[str] = None
    provenance: Optional[str] = None  # e.g. "viewer:bookreader-iip leaf=25"
    # The image's REAL, decoded dimensions (REQ-001 R1.4). Recorded because "a file was
    # written" is not "a human can read it": 96x95 has perfect magic bytes and is a
    # thumbnail, not a census sheet. Present only for images we actually measured.
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass(frozen=True)
class Redaction:
    """A record of text removed from `snapshot.html` before it was written.

    A capture is evidence, and evidence that was altered must say so. `snapshot.html`
    is otherwise a byte-true copy of the page; when the capturer removes the
    subscriber's account chrome (see `capsule/pipeline/redact.py`), the manifest
    declares every removal so `archive.sha256` attests a document whose provenance
    is fully stated rather than a quietly edited one.

    `location` is the href of the element whose text was removed. **The removed text
    is never recorded** — it is the personal data the redaction exists to contain.
    """

    reason: str  # e.g. "account-chrome"
    location: str  # the href whose link text was replaced, credential-stripped


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of `verify_capture` — PASS iff all four tuples are empty.

    The first three are INTEGRITY: are these the bytes we recorded? `insufficient` is a
    different question that integrity provably cannot answer — is the payload the RECORD?
    Two Archion captures hashed perfectly while archiving a browser print of site chrome
    and a 689x545 viewer preview (REQ-007 §3.3). Both questions gate `ok`, because
    `verify_ok` is what a consumer reads to decide whether it holds the evidence, and a
    green light on an empty capture is the failure this whole tool exists to prevent.
    """

    ok: bool
    missing: tuple[str, ...]
    mismatched: tuple[str, ...]
    extra: tuple[str, ...]
    insufficient: tuple[str, ...] = ()
    # BUG-016 — problems with the media that do NOT invalidate the capture, re-derived
    # from the bytes on disk. Deliberately the ONE field here that does not gate `ok`:
    # a blank page render is a real problem, but the snapshot, the extraction and the
    # hashes are still real evidence (BUG-001: "the capture itself still succeeds; it is
    # the SUCCESS REPORT that was lying"). Folding it into `ok` would retroactively fail
    # captures that legitimately hold their record.
    #
    # It is recomputed rather than read from `media_warnings` in the manifest, which is
    # what makes it RETROACTIVE — the same move that let REQ-007 §3.3's floor re-ask its
    # question of captures banked before the rule existed. A capture written before this
    # field carries no `media_warnings` key and still reports its blank render here.
    warnings: tuple[str, ...] = ()


# -- hashing / serialization (public: the pipeline uses these to build models) --


def sha256_hex(data: bytes) -> str:
    """SHA-256 hex digest of `data`."""
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj: object) -> bytes:
    """Deterministic UTF-8 JSON encoding used for every JSON file in a capture."""
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _assertion_payload(assertion: Assertion) -> dict:
    """Serialize one assertion, omitting `normalized_field` and default `retracted`.

    Additive, exactly as `external_refs` is: an assertion whose printed label has
    no canonical synonym encodes byte-for-byte as it did before the field existed,
    so every capture already on disk keeps its `content_hash`. `normalized_value`
    and `annotation` still emit as `null` — they predate this rule, and suppressing
    them would break the very hashes this is protecting.
    """
    data = assertion.model_dump(mode="json")
    if data.get("normalized_field") is None:
        del data["normalized_field"]
    # `retracted` is additive exactly like `normalized_field`: a model-extracted
    # assertion is never retracted, so omitting the default-False key keeps every
    # capture already on disk byte-for-byte identical and preserves its content_hash.
    if data.get("retracted") is False:
        del data["retracted"]
    return data


def serialize_extraction(extraction: Extraction) -> bytes:
    """Canonical byte encoding of `extraction.json` (drives `content_hash`)."""
    payload = {
        "personas": [p.model_dump(mode="json") for p in extraction.personas],
        "snippets": [s.model_dump(mode="json") for s in extraction.snippets],
        "assertions": [_assertion_payload(a) for a in extraction.assertions],
    }
    # Additive: omitted when empty, so every pre-existing capture's content_hash
    # is untouched and `spec_version` stays capture-folder/v1.
    if extraction.external_refs:
        payload["external_refs"] = [r.model_dump(mode="json") for r in extraction.external_refs]
    if extraction.coverage:
        payload["coverage"] = [c.model_dump(mode="json") for c in extraction.coverage]
    if extraction.provider_claims:
        payload["provider_claims"] = [
            p.model_dump(mode="json") for p in extraction.provider_claims
        ]
    if extraction.notes:
        payload["notes"] = extraction.notes
    return _json_bytes(payload)


def note_payload(note: CaptureNote) -> dict:
    """One note as it appears in `notes.json` (source_id is implied by the folder)."""
    return {
        "id": note.id,
        "text": note.text,
        "author": note.author,
        "created_at": _iso_z(note.created_at),
    }


def serialize_notes(notes: Sequence[CaptureNote]) -> bytes:
    """Canonical byte encoding of `notes.json` — a JSON array, oldest first.

    Excluded from the hash manifest, so this is not integrity-critical, but a
    stable encoding keeps the file diff-friendly as notes are appended over time.
    """
    return _json_bytes([note_payload(n) for n in notes])


def write_result_json(
    capture_folder: str | Path,
    *,
    record_type: str | None,
    image_status: str | None,
    assertion_count: int,
    persona_count: int,
    verify_ok: bool,
    verify_shortfall: Sequence[str] = (),
) -> Path:
    """Write the excluded `result.json` outcome summary into a sealed capture folder.

    A convenience projection the driving agent (or any later tool) can read to know what a
    capture GOT without re-deriving it from the manifest and measuring the image (live-run
    finding). Excluded from the hash manifest (`_is_excluded`), so writing it never invalidates
    `verify_capture` — like `notes.json`, it may be written after the folder is sealed. The
    `capture_id` is the folder's own name, so it is self-identifying if copied elsewhere.
    """
    folder = Path(capture_folder)
    payload: dict[str, object] = {
        "capture_id": folder.name,
        "record_type": record_type,
        "image_status": image_status,
        "assertion_count": assertion_count,
        "persona_count": persona_count,
        "verify_ok": verify_ok,
    }
    # WHY verify failed, when it failed for want of a record rather than a bad hash
    # (REQ-007 §3.3). Additive and omitted when empty, so a passing capture's result.json is
    # byte-identical to before. An agent that reads only `verify_ok` keeps working; one that
    # wants to know what to DO about it no longer has to re-run `verify_capture` to find out.
    if verify_shortfall:
        payload["verify_shortfall"] = list(verify_shortfall)
    path = folder / RESULT_NAME
    path.write_bytes(_json_bytes(payload))
    return path


def write_notes_file(capture_folder: str | Path, notes: Sequence[CaptureNote]) -> Path:
    """Write `notes.json` as the full canonical projection of `notes`; return the path.

    `notes.json` is a pure projection of the append-only `capture_note` store —
    exactly as `index.html` is a projection of the capture folders. The
    later-annotation path rewrites the whole file from the store after each
    insert (rather than read-modify-writing the file itself), so `notes.json` can
    never drift from, or lose a note relative to, the store. The swap is atomic
    (same-dir temp + `os.replace`), so a crash never leaves it half-written;
    callers serialize concurrent writers to the same folder (see `annotate.py`).

    Excluded from the hash manifest, so writing it never affects `verify_capture`.
    """
    folder = Path(capture_folder)
    path = folder / NOTES_NAME
    data = serialize_notes(notes)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{NOTES_NAME}.", dir=folder)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        _replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def write_lifecycle_file(capture_folder: str | Path, events: Sequence[Mapping[str, object]]) -> Path:
    """Write `lifecycle.json` as the full projection of a capture's lifecycle events (REQ-011).

    Same discipline as `write_notes_file`: regenerated whole from the append-only store, never
    read-modify-written, and swapped in atomically. Excluded from the hash manifest.
    """
    folder = Path(capture_folder)
    path = folder / LIFECYCLE_NAME
    fd, tmp_name = tempfile.mkstemp(prefix=f".{LIFECYCLE_NAME}.", dir=folder)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes([dict(e) for e in events]))
        _replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def _replace_with_retry(src: Path, dst: Path, *, attempts: int = 10, delay: float = 0.02) -> None:
    """`os.replace(src, dst)`, retried on a transient Windows `PermissionError`.

    On Windows a rename onto `dst` fails with `PermissionError` while another
    handle has `dst` open — e.g. a concurrent `write_index` reading this same
    `notes.json`, or an antivirus/indexer scan. The overlap is momentary, so a
    short bounded retry resolves it; the final attempt re-raises so a genuine
    permission problem still surfaces. POSIX `os.replace` is atomic and never hits
    this path.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


# -- capture-id derivation --------------------------------------------------


def _site_slug(source: Source) -> str:
    """Filesystem-safe slug from the source's site name, else its URL domain."""
    if source.site_name:
        raw = source.site_name
    else:
        netloc = urlparse(source.url).netloc.lower()
        netloc = netloc[4:] if netloc.startswith("www.") else netloc
        parts = [p for p in netloc.split(".") if p]
        # drop the TLD: rootsireland.ie -> rootsireland, a.co.uk -> co (best effort)
        raw = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
    slug = re.sub(r"[^a-z0-9]+", "", raw.lower())
    return slug or "source"


def derive_capture_id(source: Source) -> str:
    """Folder-name form `<YYYYMMDD>-<site-slug>-<short-uuid>` (spec §2).

    The short-uuid is the first 8 alphanumerics of the canonical `source.id`
    (lowercased, hyphens stripped); the date is the UTC capture date from
    `archive.captured_at`. Raises `ValueError` if `source.id` yields no
    alphanumerics (a capture folder must have a non-empty, safe short-uuid).
    Identity is only ever resolved via `manifest.json`, never by parsing this.
    """
    day = source.archive.captured_at.astimezone(timezone.utc).strftime("%Y%m%d")
    short = re.sub(r"[^a-z0-9]", "", source.id.lower())[:8]
    if not short:
        raise ValueError(
            f"cannot derive capture_id: source.id {source.id!r} has no alphanumerics"
        )
    return f"{day}-{_site_slug(source)}-{short}"


def resolve_capture_folder(captures_root: str | Path, capture_id: str) -> Path:
    """Resolve a (user-supplied) `capture_id` to its folder, safely.

    Validates that `capture_id` is a single spec §2-shaped path component,
    asserts the resolved folder stays inside `captures_root`, and requires a
    `manifest.json` inside it (its absence means the id names no capture). Raises
    `ValueError` for a malformed id (including any traversal attempt) and
    `FileNotFoundError` for an unknown one. Identity is never inferred by parsing
    the id — the manifest inside the folder is the authority.
    """
    if not _CAPTURE_ID_RE.match(capture_id):
        raise ValueError(
            f"invalid capture_id {capture_id!r}: must match {_CAPTURE_ID_RE.pattern}"
        )
    root = Path(captures_root)
    folder = root / capture_id
    # Defense in depth: the id already matched a single safe component, but assert
    # the resolved target stays inside captures_root before trusting it.
    if root.resolve() not in folder.resolve().parents:
        raise ValueError(f"capture_id escapes captures_root: {capture_id!r}")
    if not (folder / "manifest.json").is_file():
        raise FileNotFoundError(f"no capture with id {capture_id!r} under {root}")
    return folder


# -- writer -----------------------------------------------------------------


def _iso_z(value: datetime) -> str:
    """ISO-8601 UTC with a `Z` suffix (spec §4 prefers `Z` over `+00:00`)."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _tool_version() -> str:
    # BUG-021: the product version plus, from a git checkout, the commit. Every capture
    # before this said "0.0.0"; read that as "before BUG-021".
    return build_version()


def _media_rel_paths(media: Sequence[MediaItem]) -> list[str]:
    """`media/<n>-<slug>.<ext>` paths, zero-padded to keep lexical == numeric order.

    Width is the digit-count of the largest index, so a single-item capture is
    `media/0-<slug>.<ext>` (matching the spec example) while 11+ items become
    `media/00-...`..`media/10-...`. Slug/ext are pre-validated by the caller.
    """
    if not media:
        return []
    width = len(str(len(media) - 1))
    return [f"media/{i:0{width}d}-{m.slug}.{m.ext}" for i, m in enumerate(media)]


def _validate_media(media: Sequence[MediaItem]) -> None:
    for item in media:
        if not _SLUG_RE.match(item.slug):
            raise ValueError(
                f"invalid media slug {item.slug!r}: must match {_SLUG_RE.pattern}"
            )
        if not _EXT_RE.match(item.ext):
            raise ValueError(
                f"invalid media ext {item.ext!r}: must match {_EXT_RE.pattern}"
            )


def _validate_agent_log_names(agent_logs: Mapping[str, object]) -> None:
    for name in agent_logs:
        if not _LOG_NAME_RE.match(name):
            raise ValueError(
                f"invalid agent_log name {name!r}: must match {_LOG_NAME_RE.pattern}"
            )


def _assert_same_source(source_id: str, extraction: Extraction, citation: Citation) -> None:
    """Every child record must belong to this source — no cross-source leakage.

    Mirrors the composite-FK 'same source' guarantee the SQLite schema enforces;
    the filesystem layer must not be a way around it.
    """
    for kind, items in (
        ("persona", extraction.personas),
        ("snippet", extraction.snippets),
        ("assertion", extraction.assertions),
    ):
        for item in items:
            if item.source_id != source_id:
                raise ValueError(
                    f"{kind} {item.id!r} has source_id {item.source_id!r}, "
                    f"expected {source_id!r}"
                )
    if citation.source_id != source_id:
        raise ValueError(
            f"citation {citation.id!r} has source_id {citation.source_id!r}, "
            f"expected {source_id!r}"
        )


def write_capture(
    dest_root: str | Path,
    *,
    source: Source,
    citation: Citation,
    extraction: Extraction,
    snapshot_html: str | None = None,
    primary_bytes: bytes | None = None,
    media: Sequence[MediaItem] = (),
    redactions: Sequence[Redaction] = (),
    notes: Sequence[CaptureNote] = (),
    agent_logs: Mapping[str, object] | None = None,
    created_at: datetime | None = None,
    tool_version: str | None = None,
    capture_id: str | None = None,
    image_status: str | None = None,
    household: Sequence[HouseholdMember] = (),
    media_warnings: Sequence[str] = (),
    extension_version: str | None = None,
    extraction_pending: bool = False,
    extracted_from: str | None = None,
) -> Path:
    """Write a capture folder under `dest_root/` and return its path.

    `dest_root` is the `captures/` collection directory; the capture is written
    to `dest_root/<capture_id>`. Raises `ValueError` if any declared hash, any
    child `source_id`, or any path-component (media slug/ext, agent-log name,
    capture_id) is invalid, and `FileExistsError` if the target folder already
    exists (captures are append-only, never clobbered). The write is atomic:
    on any failure no partial capture is left behind.

    `snapshot_html` is written verbatim. If the caller redacted it first, it must
    pass the corresponding `redactions` so the manifest declares them (spec §4d) —
    `verify_capture` rejects a capture whose snapshot carries the redaction marker
    but whose manifest claims nothing was removed.
    """
    _assert_same_source(source.id, extraction, citation)
    _validate_media(media)
    if agent_logs:
        _validate_agent_log_names(agent_logs)

    # Resolve the primary archived artifact and validate its on-disk name by kind.
    # An HTML page is `snapshot.html` (bytes from `snapshot_html`); a document is
    # `document.<ext>` (bytes from `primary_bytes`). The writer is the filesystem
    # trust boundary, so it re-validates the name even though the caller sanitized it.
    primary_name = source.archive.local_snapshot
    if snapshot_html is not None and primary_bytes is not None:
        raise ValueError("pass exactly one of snapshot_html / primary_bytes")
    if source.archive.kind == ArchiveKind.SNAPSHOT:
        if snapshot_html is None:
            raise ValueError("a snapshot capture requires snapshot_html")
        if primary_name != _SNAPSHOT_NAME:
            raise ValueError(
                f"archive.local_snapshot must be {_SNAPSHOT_NAME!r} for a snapshot, "
                f"got {primary_name!r}"
            )
        primary_data = snapshot_html.encode("utf-8")
    else:  # ArchiveKind.DOCUMENT
        if primary_bytes is None:
            raise ValueError("a document capture requires primary_bytes")
        if not _DOCUMENT_NAME_RE.match(primary_name):
            raise ValueError(
                f"archive.local_snapshot must match {_DOCUMENT_NAME_RE.pattern} for a "
                f"document, got {primary_name!r}"
            )
        primary_data = primary_bytes

    created_at = _require_utc(created_at) if created_at is not None else datetime.now(timezone.utc)

    extraction_bytes = serialize_extraction(extraction)

    primary_digest = sha256_hex(primary_data)
    extraction_digest = sha256_hex(extraction_bytes)
    if source.archive.sha256 != primary_digest:
        raise ValueError(
            f"archive.sha256 does not match {primary_name} bytes "
            f"(declared {source.archive.sha256!r}, actual {primary_digest!r})"
        )
    if source.content_hash != extraction_digest:
        raise ValueError(
            "source.content_hash does not match extraction.json bytes "
            f"(declared {source.content_hash!r}, actual {extraction_digest!r})"
        )

    capture_id = capture_id or derive_capture_id(source)
    if not _CAPTURE_ID_RE.match(capture_id):
        raise ValueError(
            f"invalid capture_id {capture_id!r}: must match {_CAPTURE_ID_RE.pattern}"
        )

    dest = Path(dest_root)
    folder = dest / capture_id
    # Defense in depth: the validated capture_id is already a single safe
    # component, but assert the resolved target stays inside dest_root anyway.
    if dest.resolve() not in folder.resolve().parents:
        raise ValueError(f"capture_id escapes dest_root: {capture_id!r}")
    if folder.exists():
        raise FileExistsError(f"capture folder already exists: {folder}")

    citation_bytes = _json_bytes(citation.model_dump(mode="json"))
    media_paths = _media_rel_paths(media)

    dest.mkdir(parents=True, exist_ok=True)
    # Build in a staging dir and rename into place last, so a crash or a
    # mid-write error never leaves a partial capture that bricks the id.
    staging = Path(tempfile.mkdtemp(prefix=f".{capture_id}.", dir=dest))
    try:
        (staging / primary_name).write_bytes(primary_data)
        (staging / "extraction.json").write_bytes(extraction_bytes)
        (staging / "citation.json").write_bytes(citation_bytes)

        hashes: dict[str, str] = {
            primary_name: primary_digest,
            "extraction.json": extraction_digest,
            "citation.json": sha256_hex(citation_bytes),
        }
        if media:
            (staging / "media").mkdir()
            for rel, item in zip(media_paths, media):
                (staging / rel).write_bytes(item.data)
                hashes[rel] = sha256_hex(item.data)

        if agent_logs:
            log_dir = staging / "agent_log"
            log_dir.mkdir()
            for name, payload in agent_logs.items():
                # Excluded from hashes (spec §5.1).
                (log_dir / name).write_bytes(_json_bytes(payload))

        # Provenance for each media file (spec §4a): where it came from and what
        # it claims to be, keyed to the on-disk path whose bytes hashes.files
        # attests. Omitted entirely when there is no media, so a media-free
        # capture's manifest is byte-identical to a pre-provenance one.
        # `provenance` is emitted only when set, so a capture whose media URLs were
        # read straight off the page keeps a byte-identical manifest.
        media_meta = [
            {
                "path": rel,
                "source_url": item.source_url,
                "mime": item.mime,
                **({"provenance": item.provenance} if item.provenance else {}),
                # The measured pixel size. Additive: absent unless we decoded the image,
                # so no existing capture's manifest changes.
                **(
                    {"width": item.width, "height": item.height}
                    if item.width and item.height
                    else {}
                ),
            }
            for rel, item in zip(media_paths, media)
        ]

        # The researcher's notes: an EXCLUDED file (not in hashes.files), written
        # only when notes exist so a note-free capture is byte-identical (§5.1).
        if notes:
            (staging / NOTES_NAME).write_bytes(serialize_notes(notes))

        archive_block: dict[str, object] = {
            "local_snapshot": source.archive.local_snapshot,
            "sha256": source.archive.sha256,
            "captured_at": _iso_z(source.archive.captured_at),
            "wayback_url": source.archive.wayback_url,
        }
        # Additive: a document capture declares its artifact kind + media type; an
        # HTML snapshot omits both, so its manifest stays byte-identical to pre-v2.
        if source.archive.kind == ArchiveKind.DOCUMENT:
            archive_block["kind"] = source.archive.kind.value
            archive_block["media_type"] = source.archive.media_type
        # REQ-008: an ingested file says how it reached Capsule. Omitted when unset, so
        # every browser capture's manifest is byte-identical to one written before it.
        if source.archive.provenance:
            archive_block["provenance"] = source.archive.provenance

        manifest: dict[str, object] = {
            "spec_version": source.spec_version,
            "capture_id": capture_id,
            "tool_version": tool_version or _tool_version(),
            "created_at": _iso_z(created_at),
            "source": {
                "id": source.id,
                "url": source.url,
                "title": source.title,
                "site_name": source.site_name,
                "record_type": source.record_type,
                "gating_status": source.gating_status.value,
                "copyright_flag": source.copyright_flag,
                "source_quality": source.source_quality.value,
                **(
                    # Additive: omitted when absent, so a capture written before the
                    # judgment existed serializes byte-identically to before.
                    {"source_quality_rationale": source.source_quality_rationale}
                    if source.source_quality_rationale
                    else {}
                ),
                **(
                    # REQ-007 §1. Additive and omitted when absent, so every capture of a
                    # source with no page selector — most of the corpus — serializes
                    # byte-for-byte as it did before the field existed.
                    {"page_label": source.page_label} if source.page_label else {}
                ),
                "content_hash": source.content_hash,
            },
            "archive": archive_block,
            "hashes": {"algo": HASH_ALGO, "files": hashes},
        }
        if media_meta:
            # Placed before `hashes` for readability; JSON key order is cosmetic.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "media": media_meta,
                "hashes": manifest["hashes"],
            }
        if image_status:
            # REQ-001 R4.3 — say EXPLICITLY what this capture got for its record image:
            # full | degraded-canvas | index-only-no-image | failed | none. Never implied.
            #
            # The last two are not synonyms (REQ-007 §3.2). `index-only-no-image` is a claim
            # about the RECORD — it legitimately has no scan — and closes it. `none` is a
            # report on THIS CAPTURE: we hold no record image, whatever the record has.
            #
            # It lives here, in the folder, and not only in the index DB, because the
            # capture folder is the archive of record: move it, lose the database, open it
            # in twenty years — it must still be able to tell you whether the image inside
            # it is the record or a screen-resolution stand-in.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "image": image_status,
                "hashes": manifest["hashes"],
            }
        if extraction.provider_claims:
            # BUG-014 — the provider's OWN description of the source (Ancestry's Source
            # Citation / Original data: the NARA roll, ED, microfilm). It is already in
            # extraction.json, but the manifest is the folder's self-describing record
            # (R4.3), and the consumer reads THIS, not the raw extraction. `original_data` is
            # the load-bearing one — the difference between naming the microfilm and naming
            # only the database. Verbatim key+value; additive, omitted when there are none,
            # so a capture with no provider blocks serializes exactly as before.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "provider_claims": [
                    {"key": claim.key, "value": claim.verbatim_value}
                    for claim in extraction.provider_claims
                ],
                "hashes": manifest["hashes"],
            }
        if household:
            # REQ-003 — the record's household members with their OWN record IDs,
            # parsed deterministically from the page's Household table (see
            # capsule/pipeline/household.py). It lives in the manifest because the
            # manifest is what a downstream consumer reads (the BUG-014 lesson): with
            # it, one captured census image can be cited onto every member in a
            # single pass. Additive — omitted when the page shows no member rows, so
            # every existing capture's manifest serializes byte-identically.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "household": [_household_payload(m) for m in household],
                "hashes": manifest["hashes"],
            }
        if extracted_from:
            # v0.2 — this capture is the READING of a capture archived without a key. Recorded
            # in the replacement itself, so an interrupted `capsule extract` finds it on retry
            # and finishes the supersession instead of paying for a second reading.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "extracted_from": extracted_from,
                "hashes": manifest["hashes"],
            }
        if extraction_pending:
            # v0.2 — archived with no model call (no API key yet). The empty extraction is
            # NOT a reading that found nothing, and must never be mistaken for one: verify
            # skips the no-text floor for it, and dedup does not let it block a re-capture.
            # Additive and omitted otherwise, so every other manifest is unchanged.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "extraction": "pending",
                "hashes": manifest["hashes"],
            }
        if extension_version:
            # BUG-021 — which build of the extension sent this capture. `tool_version`
            # names the backend; the two are released together but do not upgrade in
            # lockstep (Chrome reloads the extension, launchd restarts the backend), so
            # triage needs both. Additive and omitted when absent: a CLI capture and every
            # capture written before the field existed serialize exactly as they did.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "extension_version": extension_version,
                "hashes": manifest["hashes"],
            }
        if media_warnings:
            # BUG-016 — what the capture KNEW about its own media, written where the
            # capture can still say it. These were computed at capture time and then
            # existed only in the HTTP response and a toast: a page render measuring 99%
            # near-white sat in the collection looking clean, because the one artifact
            # that recorded the problem was a notification that had long since gone.
            #
            # This is the same half-a-change as REQ-007's `postAndShape` lesson —
            # computing a fact is not the same as delivering it — and the same principle
            # R4.3 settled for `image`/`width`/`height`: the folder is the archive of
            # record, so it must carry what we knew about it.
            #
            # NOT the same thing as `verify_shortfall`, and deliberately kept apart: a
            # shortfall says the capture does not hold the record and INVALIDATES it
            # (`verify_ok: false`). A warning says something about the media is wrong
            # while the snapshot, the extraction and the hashes remain real evidence
            # (BUG-001: "the capture itself still succeeds; it is the SUCCESS REPORT that
            # was lying"). Warnings never gate `ok`.
            #
            # Additive and omitted when empty, so every clean capture's manifest
            # serializes byte-for-byte as it did before the field existed.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "media_warnings": list(media_warnings),
                "hashes": manifest["hashes"],
            }
        if redactions:
            # Additive (spec §4d): omitted entirely when nothing was removed, so an
            # unredacted capture's manifest is byte-identical to a pre-§4d one.
            manifest = {
                **{k: v for k, v in manifest.items() if k != "hashes"},
                "redactions": [
                    {"reason": r.reason, "location": r.location} for r in redactions
                ],
                "hashes": manifest["hashes"],
            }
        (staging / "manifest.json").write_bytes(_json_bytes(manifest))
        os.replace(staging, folder)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return folder


def _household_payload(member: HouseholdMember) -> dict:
    """One member as it appears in the manifest's `household` block.

    `{collection_id, record_id}` is the matching key (nulls = name-only, unlinkable
    by ID); `image_shared: null` means UNKNOWN — the table showed no sheet numbers,
    so the consumer must not attach the captured image on faith. `sheet`/`line`
    appear only when the table printed them.
    """
    payload = {
        "name": member.name,
        "age": member.age,
        "relationship": member.relationship,
        "collection_id": member.collection_id,
        "record_id": member.record_id,
        "is_principal": member.is_principal,
        "image_shared": member.image_shared,
    }
    if member.sheet is not None:
        payload["sheet"] = member.sheet
    if member.line is not None:
        payload["line"] = member.line
    return payload


# -- verify -----------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_safe_relkey(rel: str) -> bool:
    """A manifest hash key must be a forward-slash relative path inside the folder."""
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    return not any(part in ("", ".", "..") for part in rel.split("/"))


def _is_excluded(rel: str, root: Path) -> bool:
    """Files the extra-file scan ignores: the manifest, notes.json, and
    agent_log/* and transcript/* (dirs only). notes.json is the researcher's
    mutable annotation layer — it can grow after the capture is sealed without
    invalidating it, exactly like agent_log/ (spec §5.1) and the machine
    transcripts under transcript/ (REQ-006)."""
    if rel in ("manifest.json", NOTES_NAME, RESULT_NAME, LIFECYCLE_NAME):
        return True
    for directory in ("agent_log", TRANSCRIPT_DIR):
        if rel.startswith(f"{directory}/") and (root / directory).is_dir():
            return True
    return False


def _record_media_shortfall(root: Path, manifest: dict) -> tuple[str, ...]:
    """REQ-007 §3.3 — the image-only-original gate, read from the folder alone.

    Deliberately answered from what is ON DISK rather than from anything the pipeline
    remembers, so `capsule verify` re-asks it of captures banked long before the rule
    existed. The two Archion captures that provoked this REQ therefore stop passing the
    moment they are re-verified, which is the whole point: a capture that archives no
    record must never be able to report success, including retroactively.

    Class membership is decided by what the extraction PRODUCED, never by the model's
    `source_quality` label. That label moved between two captures of the same Archion
    register (`original` on 2026-08-19, `derivative` on 2026-08-22), and a gate scoped on a
    signal that drifts is absent precisely when the drift goes the wrong way. See
    `sufficiency` for the full argument.

    `extraction.json` therefore has to be read on every verify. That is one small JSON in a
    routine that is already hashing every file in the folder, and the counts short-circuit
    before anything is decoded — so an ordinary capture still never pays for a
    rasterization it cannot possibly need. This runs on every verify, and `dedup` verifies
    every candidate folder it considers.

    Never raises. A gate that can crash a verify is worse than the bug it guards: an
    unreadable extraction or an unmeasurable file yields no shortfall claim here, because
    "I could not check" must not masquerade as "I checked and it is empty". (The reverse
    lean from `sufficiency`'s own floor, and correct: THIS function decides whether the
    question is even askable, and a question we cannot ask is not a failed answer.)
    """
    # An ARCHIVE-ONLY document capture is out of class, and not as a convenience. The gate
    # reasons "the extraction produced nothing, therefore the image is the whole payload" —
    # but `run_document_capture` makes no model call at all, so "produced nothing" is
    # trivially true of every document capture and carries no information whatsoever. Using
    # it would fail a text PDF (a transcript, a will, a typed certificate) for holding no
    # big raster, when its evidence is precisely the text.
    #
    # KNOWN LIMIT, to revisit with REQ-006: this also means a document capture holding a
    # 689px scan is not caught. Once vision/HTR actually extracts from these, they stop
    # being archive-only and the premise becomes true again.
    if (manifest.get("archive") or {}).get("kind") == ArchiveKind.DOCUMENT.value:
        return ()
    try:
        extraction = json.loads((root / "extraction.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        _logger.warning("cannot read extraction.json to judge media sufficiency: %s", exc)
        return ()

    def _count(key: str) -> int:
        value = extraction.get(key)
        # A malformed list is "cannot judge", not "empty": -1 keeps this capture OUT of the
        # class rather than sweeping it in on a reading we do not trust.
        return len(value) if isinstance(value, list) else -1

    counts = {k: _count(k) for k in ("assertions", "coverage", "provider_claims")}
    # `coverage` and `provider_claims` are optional keys — absent means genuinely none.
    for key in ("coverage", "provider_claims"):
        if counts[key] == -1 and key not in extraction:
            counts[key] = 0
    if any(v < 0 for v in counts.values()):
        return ()
    source_url = (manifest.get("source") or {}).get("url")
    if not yielded_no_text_evidence(
        assertion_count=counts["assertions"],
        coverage_count=counts["coverage"],
        provider_claim_count=counts["provider_claims"],
        url=source_url if isinstance(source_url, str) else None,
    ):
        return ()

    raw_media = manifest.get("media")
    entries = list(raw_media) if isinstance(raw_media, list) else []
    measured: list[tuple[str, Optional[str], Optional[int]]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not _is_safe_relkey(path):
            continue
        target = root / path
        if not target.is_file():
            continue
        provenance = entry.get("provenance")
        mime = entry.get("mime")
        measured.append(
            (
                path,
                provenance if isinstance(provenance, str) else None,
                longest_raster_edge(
                    target.read_bytes(),
                    ext=path.rsplit(".", 1)[-1] if "." in path else "",
                    mime=mime if isinstance(mime, str) else "",
                ),
            )
        )

    shortfall = record_media_shortfall(
        assertion_count=counts["assertions"],
        coverage_count=counts["coverage"],
        provider_claim_count=counts["provider_claims"],
        url=source_url if isinstance(source_url, str) else None,
        media=measured,
    )
    return (shortfall,) if shortfall else ()


def _render_warnings(root: Path, manifest: dict) -> tuple[str, ...]:
    """Re-measure this capture's page renders and report any that show nothing (BUG-016).

    Only `render:page-pdf` media are judged, for the same reason `_check_rendered_media`
    gives at capture time: a render is supposed to be a picture of the page in view, so a
    blank one means something composited over it. A scan the site served is allowed to be
    near-white — a mostly-empty register page legitimately is — and failing those would
    cry wolf on real evidence.

    Never raises, and never gates `verify_ok`. An unreadable file yields no warning here
    rather than a false one: `looks_blank` returns None for "cannot judge", which must
    never be confused with "checked and blank".
    """
    raw_media = manifest.get("media")
    entries = raw_media if isinstance(raw_media, list) else []
    # BUG-020: beside a record image the capture measured as the record (`image`, R4.3), a
    # blank render is harmless and the warning says so. Read from the folder's own
    # manifest, so a capture banked before the fix re-reports with the corrected wording.
    record_image: Optional[str] = None
    if manifest.get("image") in EVIDENCE_IMAGE_STATUSES:
        record_image = next(
            (
                e["path"]
                for e in entries
                if isinstance(e, dict)
                and isinstance(e.get("path"), str)
                and str(e.get("provenance") or "").startswith(RECORD_IMAGE_PROVENANCE)
            ),
            None,
        )
    warnings: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("provenance") != "render:page-pdf":
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not _is_safe_relkey(path):
            continue
        target = root / path
        if not target.is_file():
            continue
        try:
            verdict = looks_blank(target.read_bytes())
        except Exception:  # pragma: no cover - looks_blank is already total
            continue
        if verdict is True:
            warnings.append(blank_render_warning(path, record_image))
    return tuple(warnings)


def verify_capture(capture_folder: str | Path) -> VerifyResult:
    """Re-hash a capture and confirm integrity against its own `manifest.json`.

    PASS iff every listed file exists and matches, no unlisted (non-excluded)
    file is present, and the spec §4 MUST-equal invariants hold
    (`archive.sha256` == hashes.files[snapshot.html], `source.content_hash` ==
    hashes.files[extraction.json]). Raises `FileNotFoundError` if the manifest
    is absent and `ValueError` if it is unparseable, uses an unsupported hash
    algorithm, or has a malformed `hashes.files` — those are 'cannot verify'
    conditions, distinct from a verify FAIL.
    """
    root = Path(capture_folder)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no manifest.json in capture folder: {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest.json is not valid JSON: {exc}") from exc

    hashes = manifest.get("hashes") or {}
    if hashes.get("algo") != HASH_ALGO:
        raise ValueError(f"unsupported hash algo in manifest: {hashes.get('algo')!r}")
    listed = hashes.get("files")
    if not isinstance(listed, dict):
        raise ValueError("manifest hashes.files is missing or malformed")

    missing: list[str] = []
    mismatched: list[str] = []
    for rel, expected in listed.items():
        if not _is_safe_relkey(rel):
            # A key that points outside the folder can never be trusted.
            mismatched.append(rel)
            continue
        target = root / rel
        if not target.is_file():
            missing.append(rel)
        elif _sha256_file(target) != expected:
            mismatched.append(rel)

    # Re-check the spec §4 cross-field MUST-equal invariants, so tampering with
    # these manifest fields (outside hashes.files) is also caught.
    source_meta = manifest.get("source") or {}
    archive_meta = manifest.get("archive") or {}
    # The primary artifact is whatever `archive.local_snapshot` names —
    # `snapshot.html` for a page, `document.pdf` for a document. Its hashes.files
    # entry MUST equal archive.sha256, whichever it is.
    primary_name = archive_meta.get("local_snapshot")
    if not isinstance(primary_name, str) or listed.get(primary_name) != archive_meta.get("sha256"):
        mismatched.append("archive.sha256!=hashes.files[<primary artifact>]")
    if listed.get("extraction.json") != source_meta.get("content_hash"):
        mismatched.append("source.content_hash!=hashes.files[extraction.json]")

    # Every media[] provenance entry must point at a file the hash manifest
    # attests — a provenance record for bytes that aren't integrity-checked (or
    # aren't present) is a manifest inconsistency, not a trustworthy capture.
    raw_media = manifest.get("media")
    media_entries = raw_media if isinstance(raw_media, list) else []
    media_paths: set[str] = set()
    for entry in media_entries:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str):
            mismatched.append("media[] entry has no string path")
        else:
            media_paths.add(path)
            if path not in listed:
                mismatched.append(path)
    # Reverse direction: every hashed media file must carry a provenance entry.
    # A stripped/tampered media[] (provenance silently removed while the bytes
    # stay hashed and on disk) must fail verify — provenance is part of the
    # evidentiary content of a capture, not decoration.
    for rel in listed:
        if rel.startswith("media/") and rel not in media_paths:
            mismatched.append(rel)

    # A redacted snapshot MUST declare its redactions. The dangerous direction is a
    # capture whose page text was altered while its manifest claims a byte-true copy:
    # `archive.sha256` would faithfully attest a document that silently is not the
    # page. (The reverse — declaring a redaction that left no marker — is caught by
    # nothing here, and does not misrepresent the evidence.)
    snapshot_file = root / _SNAPSHOT_NAME
    if snapshot_file.is_file() and not manifest.get("redactions"):
        text = snapshot_file.read_text(encoding="utf-8", errors="replace")
        if REDACTION_MARKER in text:
            mismatched.append("snapshot.html is redacted but manifest declares no redactions[]")

    present = {
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    }
    extra = sorted(
        rel for rel in present if rel not in listed and not _is_excluded(rel, root)
    )

    # Integrity is settled above. Now the question hashing cannot reach: does this capture
    # hold its record at all? Scoped to the image-only-original class — see `sufficiency`.
    # v0.2: an archive-only capture has not been READ yet, so "this capture read no record
    # text" would be false. The floor waits for the reading; the warning says it is owed.
    # EXCEPT on a tile viewer, where the image IS the record whatever the text says: reading
    # the snapshot later cannot recover pixels that were never archived (Codex review).
    pending = manifest.get("extraction") == "pending"
    source_url = (manifest.get("source") or {}).get("url")
    if pending and not is_tile_viewer_source(source_url):
        insufficient: tuple[str, ...] = ()
    else:
        insufficient = _record_media_shortfall(root, manifest)

    # BUG-016 — and the question that is neither integrity nor sufficiency: is anything
    # WRONG with the media we do hold? Re-measured from the bytes, so a capture banked
    # before this existed still answers it. Pointedly absent from `ok` below.
    warnings = _render_warnings(root, manifest)
    if pending:
        warnings += (
            "extraction pending: this capture was archived without an Anthropic API key, so "
            "the record has not been read yet. Add a key, then run "
            f"`capsule extract {root.name}`.",
        )

    ok = not missing and not mismatched and not extra and not insufficient
    return VerifyResult(
        ok=ok,
        missing=tuple(sorted(missing)),
        mismatched=tuple(sorted(mismatched)),
        extra=tuple(extra),
        insufficient=insufficient,
        warnings=warnings,
    )
