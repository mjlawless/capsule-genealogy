"""Auto-write an eval fixture from a corrected capture (slice C4).

This is the payoff the correction loop exists for: "corrected outputs become eval
fixtures automatically" (CLAUDE.md). After a human has edited/added/retracted facts
on a capture, its CURRENT assertions are the gold — the extraction a correct run
*should* have produced. `write_fixture` projects that corrected truth back into the
eval fixture shape (`expected.json` + `meta.json` + a local `snapshot.html`) so a
later prompt/model change is scored against a real page the model previously got
wrong.

Fixtures round-trip through `harness.load_fixture`: the `expected.json` written here
is a `RawExtraction`, and scoring the reconstructed gold against itself passes.

HTML (snapshot) captures only — a document capture has no `snapshot.html` and no
assertions to be gold, so it cannot seed a fixture.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from capsule.capture.manifest import resolve_capture_folder
from capsule.pipeline.extract import RawAssertion, RawExtraction, RawPersona
from capsule.store.repository import Repository

_SNAPSHOT = "snapshot.html"
_EXPECTED = "expected.json"
_META = "meta.json"

#: A fixture directory name is used as a path segment, so keep it to a safe slug.
_SAFE_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _dump_json(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _source_id_from_manifest(folder: Path) -> str:
    """The capture's `source.id`, read from its manifest — the fixture's anchor."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    source = manifest.get("source") if isinstance(manifest, dict) else None
    source_id = source.get("id") if isinstance(source, dict) else None
    if not isinstance(source_id, str) or not source_id:
        raise ValueError(f"manifest in {folder.name} has no source.id")
    return source_id


def write_fixture(
    *,
    repository: Repository,
    captures_root: Union[str, Path],
    capture_id: str,
    fixtures_root: Union[str, Path],
    name: Optional[str] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> Path:
    """Write an eval fixture from the CORRECTED state of `capture_id`.

    Rebuilds a `RawExtraction` gold from the capture's current assertions (post
    any edit/add/retract), copies its `snapshot.html` as the page under test, and
    writes `expected.json` + `meta.json`. Returns the fixture directory.

    Raises `ValueError` for a document capture (no snapshot / no assertions) or a
    malformed id/name, `FileNotFoundError` for no such capture.
    """
    now = now or (lambda: datetime.now(timezone.utc))

    fixture_name = name or capture_id
    if not _SAFE_NAME_RE.match(fixture_name):
        raise ValueError(f"unsafe fixture name: {fixture_name!r}")

    folder = resolve_capture_folder(captures_root, capture_id)
    snapshot_path = folder / _SNAPSHOT
    if not snapshot_path.is_file():
        raise ValueError(
            f"capture {capture_id!r} has no {_SNAPSHOT}: only HTML captures can seed a fixture "
            "(a document capture carries no transcribable assertions)"
        )

    source_id = _source_id_from_manifest(folder)
    source = repository.get_source(source_id)
    personas = repository.personas_for_source(source_id)
    current = repository.current_assertions(source_id)

    expected = RawExtraction(
        # record_type is required on RawExtraction; a capture always had one (it
        # was a required field of the model's reply), stored on the source.
        record_type=source.record_type or "",
        # Emit the id-based shape: each persona keeps its (durable) id, and every
        # assertion references its persona by that id — so a capture that names two
        # different people with the same label round-trips into a fixture correctly.
        personas=[RawPersona(id=p.id, label=p.label, role=p.role) for p in personas],
        assertions=[
            RawAssertion(
                persona_id=a.persona_id,
                field=a.field,
                value=a.verbatim_value,
                evidence_type=a.evidence_type,
                snippet=repository.get_snippet(a.snippet_id).verbatim_text,
                normalized_field=a.normalized_field,
                normalized_value=a.normalized_value,
                annotation=a.annotation,
                confidence=a.confidence,
            )
            for a in current
        ],
    )

    meta = {
        "url": source.url,
        "gating": source.gating_status.value,
        "record_type": source.record_type,
        # Provenance: this gold was derived from a corrected capture, not typed by
        # hand — so a later reader knows the labels came from the human review pass.
        "source": "auto-generated from corrected capture",
        "capture_id": capture_id,
        "generated_at": now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    dest = Path(fixtures_root) / fixture_name
    dest.mkdir(parents=True, exist_ok=True)
    (dest / _EXPECTED).write_text(_dump_json(expected.model_dump(mode="json")), encoding="utf-8")
    (dest / _META).write_text(_dump_json(meta), encoding="utf-8")
    # The page under test stays local (the harness gitignores snapshot.html);
    # copy the exact bytes so the fixture reproduces the capture faithfully.
    (dest / _SNAPSHOT).write_bytes(snapshot_path.read_bytes())
    return dest
