"""Write a machine transcript into a capture folder (REQ-006 T4).

Layout, per transcriber run (capture-folder-v2 §4):

    <capture>/transcript/<transcription_id>/
        transcript.json         the run and its fields, flagged as a derivative
        crops/<page>-<key>.png  the exact pixels each field was read from

The whole `transcript/` tree is excluded from the hash manifest (like
`notes.json`), so it can be added to a sealed capture without invalidating it.
Each run gets its own folder and a folder is never overwritten — a re-run is a
new transcription, mirroring the append-only tables it projects.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from capsule.capture.manifest import TRANSCRIPT_DIR, _json_bytes
from capsule.contracts.transcript import TranscriptComparison, Transcription, TranscriptField

TRANSCRIPT_NAME = "transcript.json"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_CROP_RE = re.compile(r"^crops/[A-Za-z0-9_.-]+$")

#: Stamped on every transcript.json so a consumer reading the file alone cannot
#: mistake it for the record (REQ-006 §5.1, §8.3).
CAUTION = (
    "A machine reading of the scanned image — a reading aid, not the record. "
    "The image is the evidence; check every value against its crop, especially "
    "any with needs_review true or an uncertainty marker such as [?]."
)

__all__ = ["CAUTION", "TRANSCRIPT_DIR", "TRANSCRIPT_NAME", "write_transcript_folder"]


def write_transcript_folder(
    capture_folder: str | Path,
    transcription: Transcription,
    fields: Sequence[TranscriptField],
    crops: Mapping[str, bytes],
    *,
    comparisons: Sequence[TranscriptComparison] = (),
) -> Path:
    """Write `transcript/<transcription.id>/` atomically; return its path.

    Raises ValueError for an unsafe id or crop path (checked before anything is
    written) and FileExistsError if this transcription's folder already exists.
    """
    if not _SAFE_ID_RE.match(transcription.id):
        raise ValueError(f"unsafe transcription id {transcription.id!r}")
    for name in crops:
        if not _SAFE_CROP_RE.match(name) or ".." in name:
            raise ValueError(f"crop path {name!r} must be crops/<name> with no separators")

    parent = Path(capture_folder) / TRANSCRIPT_DIR
    target = parent / transcription.id
    if target.exists():
        raise FileExistsError(f"transcript {transcription.id} already written: {target}")
    parent.mkdir(exist_ok=True)

    document = {
        "kind": "machine-transcript",
        "derivative": True,
        "caution": CAUTION,
        "transcription": transcription.model_dump(mode="json"),
        "fields": [f.model_dump(mode="json") for f in fields],
        # image vs. index, both values kept — a conflict is a finding, never a resolution
        "comparisons": [c.model_dump(mode="json") for c in comparisons],
    }
    staging = Path(tempfile.mkdtemp(prefix=f".{transcription.id}.", dir=parent))
    try:
        (staging / "crops").mkdir()
        for name, data in crops.items():
            (staging / name).write_bytes(data)
        (staging / TRANSCRIPT_NAME).write_bytes(_json_bytes(document))
        os.rename(staging, target)  # atomic on one filesystem; fails if target appeared
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target
