"""Read a `captures/` directory into light display views (`CaptureRecord`).

This is the read side of the Captures Index (spec: `captures-index/v1`). It
walks the collection and parses each capture folder's `manifest.json` +
`citation.json` + `extraction.json` into a `CaptureRecord` — everything the
renderer needs and nothing more.

Parsing is deliberately **tolerant**, not strict-pydantic: the index is a
human browse surface, so a folder with a missing or malformed `citation.json`
or `extraction.json` should still appear (rendered from what is readable)
rather than raise. A folder without a `manifest.json` is not a capture and is
skipped. This keeps one bad capture from blanking the whole index.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from capsule.collection.record_types import record_category

_logger = logging.getLogger(__name__)

_MANIFEST = "manifest.json"
_CITATION = "citation.json"
_EXTRACTION = "extraction.json"
_NOTES = "notes.json"
_RESULT = "result.json"
_TRANSCRIPTS = "transcript"

_IMAGE_EXTS = ("jpg", "jpeg", "png", "gif", "webp", "tif", "tiff")


def _ext_type(rel: str) -> str:
    """Display file-type for an artifact path: its extension, images folded to `image`."""
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else "file"
    return "image" if ext in _IMAGE_EXTS else ext


def _safe_rel(rel: str) -> bool:
    """A hash key that names a real file inside the folder (no traversal).

    hash-manifest keys are untrusted (a hand-forged manifest can put anything
    there). Mirrors `manifest._is_safe_relkey`, so a key like `../../etc/passwd`
    is neither turned into a file-type chip nor treated as a browsable artifact.
    """
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    return not any(part in ("", ".", "..") for part in rel.split("/"))


@dataclass(frozen=True)
class PersonaView:
    """A person as they appear on the source, for display."""

    label: str
    role: str
    #: The persona's id, so the served index can add a fact to this person.
    id: str | None = None


@dataclass(frozen=True)
class CorrectionView:
    """One human correction to a fact, for display (M1, the served index only)."""

    action: str
    previous_value: str | None
    new_value: str | None
    actor: str
    created_at: str | None
    #: The researcher's reason, when they gave one.
    rationale: str | None = None


@dataclass(frozen=True)
class AssertionView:
    """One learned fact, flattened for the index table."""

    persona_label: str
    field: str
    value: str
    evidence_type: str
    #: The assertion's id, so the served index can target it with a correction. From
    #: `extraction.json`, or, once corrected, the CURRENT row's id in the database.
    id: str | None = None
    #: The fact was retracted by a correction; shown struck through, never hidden.
    retracted: bool = False
    #: Corrections that produced this value, oldest first. Empty on the static file.
    corrections: tuple[CorrectionView, ...] = ()
    #: The words on the page that support the fact (its snippet), shown when correcting it.
    snippet: str | None = None


@dataclass(frozen=True)
class ExternalRefView:
    """A pointer this page gives to the original it indexes, for display."""

    authority: str
    identifier: str
    locator: str | None
    url: str
    label: str | None

    @property
    def citation(self) -> str:
        """`nli-registers vtls000632043 · page/26` — what the page actually says."""
        base = f"{self.authority} {self.identifier}"
        return f"{base} · {self.locator}" if self.locator else base


@dataclass(frozen=True)
class NoteView:
    """One researcher note, for display — the user's own words, not evidence."""

    text: str
    author: str
    created_at: str | None


@dataclass(frozen=True)
class CaptureRecord:
    """A single capture folder, projected to what the index shows.

    Built by `read_capture_record`; `capture_id` is the folder name (and the
    relative link target). Fields that a later milestone will populate
    (in-tree, OCR'd) are intentionally absent here — the renderer emits their
    placeholders — so this view only carries what M0 can actually source.
    """

    capture_id: str
    folder_name: str
    url: str
    title: str | None
    site_name: str | None
    record_type: str | None
    gating: str
    copyright_flag: bool
    captured_at: str | None
    wayback_url: str | None
    short_note: str | None
    source_list_entry: str | None
    access_date: str | None
    archived_url: str | None
    personas: tuple[PersonaView, ...]
    assertions: tuple[AssertionView, ...]
    external_refs: tuple[ExternalRefView, ...]
    media_files: tuple[str, ...]
    file_types: tuple[str, ...]
    notes: tuple[NoteView, ...] = ()
    #: The MODEL's note about the page — usually WHY there are no assertions ("the
    #: record is a scanned image; no transcribable text"). Deliberately NOT `notes`,
    #: which is the RESEARCHER's voice: this is the extractor's, and conflating the two
    #: would let tool output masquerade as the owner's own words.
    extraction_notes: str | None = None
    #: The primary archived artifact's filename (`snapshot.html` or `document.pdf`),
    #: read from `archive.local_snapshot` — the direct link target for a document.
    primary_artifact: str | None = None
    #: True when the primary artifact is a document (a PDF the page IS), not a
    #: page snapshot — drives the direct-document link in the index.
    is_document: bool = False
    #: REQ-011 — True when the hash-excluded `lifecycle.json` ends in `superseded`. Kept
    #: apart from `superseded_by`: the state holds even when the replacement's folder is not
    #: in the collection (trashed, not yet copied), and must not silently read as current.
    superseded: bool = False
    #: v0.2 — archived without an API key; the record has not been read yet.
    extraction_pending: bool = False
    #: The replacement's folder name, when it is known; None if this one is current or the
    #: replacement's folder is unavailable.
    superseded_by: str | None = None
    #: The source's id from `manifest.json`: the key into the database, where corrections live.
    source_id: str | None = None
    #: M1 — set only by `corrections.overlay_corrections` (the served index), from the
    #: correction log: when the latest correction was made, or None if there is none.
    reviewed_at: str | None = None
    #: How many corrections the log holds for this capture (0 on the static file).
    correction_count: int = 0
    #: The capture's own verdict, from `result.json`: False when it does not hold the
    #: evidence (e.g. no record image and no text); None when the capture predates it.
    verify_ok: bool | None = None
    #: Why `verify_ok` is False, in the capture's own words.
    verify_shortfall: tuple[str, ...] = ()
    #: REQ-006: when the newest machine transcript of the capture's scan was made.
    transcribed_at: str | None = None
    #: The full Evidence Explained first-reference note from `citation.json`: the text a
    #: researcher pastes into a tree program's citation.
    reference_note: str | None = None

    @property
    def primary_persona(self) -> str | None:
        return self.personas[0].label if self.personas else None

    @property
    def record_category(self) -> str:
        """The common category the index shows and filters by (`record_types`)."""
        return record_category(self.record_type)

    @property
    def status(self) -> str:
        """Where this capture sits on the lifecycle (spec `captures-index/v2` §4).

        `captured -> extracted -> reviewed -> matched -> attached`. The first two
        are derivable from a capture folder: a capture with assertions has been
        extracted, one without is a bare snapshot (typically a scan awaiting OCR).
        `reviewed` comes from the correction log, which lives in the database, so
        only the served index (which overlays it) can show it. The matcher verdict
        (M2) and the persona-tree link (M3) do not exist yet.
        """
        if self.correction_count:
            return "reviewed"
        return "extracted" if self.assertions else "captured"


def _load_json(path: Path) -> dict:
    """Parse a JSON object, or `{}` if absent/unreadable (tolerant by design)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("captures index: could not read %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _load_json_array(path: Path) -> list:
    """Parse a JSON array, or `[]` if absent/unreadable/not-an-array (tolerant)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("captures index: could not read %s: %s", path, exc)
        return []
    return data if isinstance(data, list) else []


def _file_types(hash_files: dict, primary_name: str | None) -> tuple[str, ...]:
    """Derive the artifact file types present from the manifest's hash keys.

    The primary artifact is either the `html` snapshot or a document
    (`document.pdf` → `pdf`, a scan → `image`); `media/<n>-<slug>.<ext>` entries
    add their own extension. The two structured JSON files are not artifact types
    and are excluded. `primary_name` (`archive.local_snapshot`) is what tells a
    top-level `document.pdf` apart from noise — without it a document capture
    would show no file type at all.
    """
    types: set[str] = set()
    for rel in hash_files:
        if not _safe_rel(rel):
            continue  # a forged, path-walking key is not a browsable artifact
        if primary_name is not None and rel == primary_name:
            types.add("html" if rel.endswith(".html") else _ext_type(rel))
        elif rel.startswith("media/"):
            types.add(_ext_type(rel))
        elif rel.endswith(".html"):
            types.add("html")
    return tuple(sorted(types))


def _notes(folder: Path) -> tuple[NoteView, ...]:
    """Project a capture's `notes.json` (the researcher's annotation layer).

    Tolerant by design, exactly like the rest of the reader: absent on most
    captures (no note yet), and a malformed file degrades to no notes rather than
    blanking the whole index. Notes are the user's words, not evidence — they are
    shown, never hashed.
    """
    data = _load_json_array(folder / _NOTES)
    out: list[NoteView] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        out.append(
            NoteView(
                text=str(item.get("text", "")),
                author=str(item.get("author", "")) or "owner",
                created_at=item.get("created_at"),
            )
        )
    return tuple(out)


def _personas(extraction: dict) -> tuple[tuple[PersonaView, ...], dict[str, str]]:
    """Persona views plus an id→label map for resolving assertions."""
    views: list[PersonaView] = []
    by_id: dict[str, str] = {}
    for p in extraction.get("personas", []) or []:
        if not isinstance(p, dict):
            continue
        label = str(p.get("label", "")) or "(unlabeled)"
        pid = p.get("id")
        pid = pid if isinstance(pid, str) and pid else None
        views.append(PersonaView(label=label, role=str(p.get("role", "")) or "?", id=pid))
        if pid:
            by_id[pid] = label
    return tuple(views), by_id


def _snippets(extraction: dict) -> dict[str, str]:
    """snippet id -> its verbatim words, tolerant of anything malformed."""
    out: dict[str, str] = {}
    for s in extraction.get("snippets", []) or []:
        if isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(
            s.get("verbatim_text"), str
        ):
            out[s["id"]] = s["verbatim_text"]
    return out


def _assertions(extraction: dict, persona_by_id: dict[str, str]) -> tuple[AssertionView, ...]:
    out: list[AssertionView] = []
    snippets = _snippets(extraction)
    for a in extraction.get("assertions", []) or []:
        if not isinstance(a, dict):
            continue
        label = persona_by_id.get(a.get("persona_id", ""), "?")
        aid = a.get("id")
        out.append(
            AssertionView(
                persona_label=label,
                field=str(a.get("field", "")),
                value=str(a.get("verbatim_value", "")),
                evidence_type=str(a.get("evidence_type", "")),
                id=aid if isinstance(aid, str) and aid else None,
                snippet=snippets.get(sid) if isinstance(sid := a.get("snippet_id"), str) else None,
            )
        )
    return tuple(out)


def _external_refs(extraction: dict) -> tuple[ExternalRefView, ...]:
    """Project `extraction.json`'s optional `external_refs[]`, tolerantly.

    Absent on every capture written before the field existed, and on any page
    that names no source document — both are normal, not errors.
    """
    out: list[ExternalRefView] = []
    for ref in extraction.get("external_refs", []) or []:
        if not isinstance(ref, dict):
            continue
        authority = str(ref.get("authority", "")).strip()
        identifier = str(ref.get("identifier", "")).strip()
        if not authority or not identifier:
            continue  # an unidentifiable reference points at nothing
        locator = ref.get("locator")
        label = ref.get("label")
        out.append(
            ExternalRefView(
                authority=authority,
                identifier=identifier,
                locator=str(locator) if locator else None,
                url=str(ref.get("url", "")),
                label=str(label) if label else None,
            )
        )
    return tuple(out)


_LIFECYCLE = "lifecycle.json"
#: A folder-name shape check for the replacement link; the renderer escapes it anyway.
_CAPTURE_ID_SHAPE = re.compile(r"\A[0-9]{8}-[a-z0-9]+-[a-z0-9]+\Z")


def _supersession(folder: Path) -> tuple[bool, str | None]:
    """(superseded?, replacement folder name) from `lifecycle.json`, tolerant of anything."""
    events = _load_json_array(folder / _LIFECYCLE)
    latest = events[-1] if events and isinstance(events[-1], dict) else None
    if not latest or latest.get("event") != "superseded":
        return False, None
    name = latest.get("superseded_by_capture_id")
    return True, (name if isinstance(name, str) and _CAPTURE_ID_SHAPE.match(name) else None)


def read_capture_record(folder: Path) -> CaptureRecord:
    """Read one capture folder into a `CaptureRecord`.

    Requires a readable `manifest.json` (its absence means the folder is not a
    capture); `citation.json`/`extraction.json` are optional and default to
    empty. Raises `FileNotFoundError` if there is no `manifest.json`, so callers
    can distinguish "not a capture" from "a capture with thin content".
    """
    manifest_path = folder / _MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no {_MANIFEST} in {folder}")
    manifest = _load_json(manifest_path)
    citation = _load_json(folder / _CITATION)
    extraction = _load_json(folder / _EXTRACTION)

    source = manifest.get("source") or {}
    archive = manifest.get("archive") or {}
    hash_files = ((manifest.get("hashes") or {}).get("files")) or {}

    personas, by_id = _personas(extraction)
    media_files = tuple(sorted(rel for rel in hash_files if rel.startswith("media/")))
    primary_artifact = archive.get("local_snapshot")
    primary_artifact = primary_artifact if isinstance(primary_artifact, str) else None
    is_document = archive.get("kind") == "document"
    superseded, superseded_by = _supersession(folder)

    return CaptureRecord(
        capture_id=str(manifest.get("capture_id") or folder.name),
        # The *link target* is the directory that actually exists, never the
        # manifest's claim about its own id. They normally agree; when they do
        # not, the directory wins, so the index's local link always resolves
        # (spec §2) and manifest-supplied text can never shape a path.
        folder_name=folder.name,
        url=str(source.get("url", "")),
        title=source.get("title"),
        site_name=source.get("site_name"),
        record_type=source.get("record_type"),
        gating=str(source.get("gating_status", "public")),
        copyright_flag=bool(source.get("copyright_flag", False)),
        captured_at=archive.get("captured_at"),
        wayback_url=archive.get("wayback_url"),
        short_note=citation.get("short_note"),
        source_list_entry=citation.get("source_list_entry"),
        reference_note=citation.get("reference_note")
        if isinstance(citation.get("reference_note"), str)
        else None,
        access_date=citation.get("access_date"),
        archived_url=citation.get("archived_url"),
        personas=personas,
        assertions=_assertions(extraction, by_id),
        external_refs=_external_refs(extraction),
        media_files=media_files,
        file_types=_file_types(hash_files, primary_artifact),
        notes=_notes(folder),
        # Tolerant like every other projection: a malformed or absent value is simply
        # no note, never an exception (a capture folder must always be readable).
        extraction_notes=(extraction.get("notes") or None)
        if isinstance(extraction.get("notes"), str)
        else None,
        primary_artifact=primary_artifact,
        is_document=is_document,
        superseded=superseded,
        superseded_by=superseded_by,
        extraction_pending=manifest.get("extraction") == "pending",
        source_id=source.get("id") if isinstance(source.get("id"), str) else None,
        **_outcome(folder),
    )


def _outcome(folder: Path) -> dict:
    """`verify_ok`/`verify_shortfall` from the `result.json` sidecar, and the newest
    transcript's date; tolerant like every other projection (absent is simply unknown)."""
    result = _load_json(folder / _RESULT)
    ok = result.get("verify_ok")
    shortfall = result.get("verify_shortfall")
    transcribed_at = None
    for path in (folder / _TRANSCRIPTS).glob("*/transcript.json"):
        created = (_load_json(path).get("transcription") or {}).get("created_at")
        if isinstance(created, str) and (transcribed_at is None or created > transcribed_at):
            transcribed_at = created
    return {
        "verify_ok": ok if isinstance(ok, bool) else None,
        "verify_shortfall": tuple(str(s) for s in shortfall) if isinstance(shortfall, list) else (),
        "transcribed_at": transcribed_at,
    }


def read_all(captures_root: Path) -> list[CaptureRecord]:
    """Read every capture folder under `captures_root`, newest first.

    Subdirectories without a `manifest.json` (and hidden/`_`-prefixed dirs such
    as `_failed/`) are skipped.

    Sorted by `archive.captured_at` descending, then `capture_id`. The id alone
    is not enough: it carries only `YYYYMMDD`, so two captures made on the same
    day would tie and fall back to alphabetical order by site slug — which the
    index then displays under a "Captured" column showing the time, visibly out
    of order. A capture with no readable timestamp sorts last rather than first,
    since an unknown instant is not a recent one.
    """
    if not captures_root.is_dir():
        return []
    records: list[CaptureRecord] = []
    for child in captures_root.iterdir():
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        if not (child / _MANIFEST).is_file():
            continue
        try:
            records.append(read_capture_record(child))
        except (FileNotFoundError, OSError) as exc:
            _logger.warning("captures index: skipping %s: %s", child, exc)
    records.sort(key=lambda r: (r.captured_at or "", r.capture_id), reverse=True)
    return records
