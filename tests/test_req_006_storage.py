"""REQ-006 T4 — the transcript layer: its own tables, its own folder, never evidence.

Owner ruling R1 (2026-09-24): transcribed values live in `transcription` /
`transcript_field`, NEVER in `assertion` — a machine transcript is a derivative
reading aid (§5.1) and must not be mistakable for the record or able to raise a
grade (§5.2). On disk it is `transcript/<id>/`, excluded from the hash manifest
like `notes.json`, so it can be added to a sealed capture without invalidating it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tests.test_capture_manifest as cap
import tests.test_req_006_transcribe as t3
from capsule.capture.manifest import verify_capture
from capsule.capture.transcript import TRANSCRIPT_DIR, write_transcript_folder
from capsule.contracts.transcript import Transcription, TranscriptField, TranscriptStatus
from capsule.pipeline.transcribe import records_from_draft, transcribe_document
from capsule.store.repository import Repository

_DT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _transcription(**kw) -> Transcription:
    fields = dict(
        id="tr1", source_id="src-1", media_filename="1-d-q-1903-0000148.pdf",
        media_sha256="a" * 64, model="claude-opus-5", status="complete",
        problems=(), layout_notes=None, created_at=_DT,
    )
    fields.update(kw)
    return Transcription(**fields)


def _field(**kw) -> TranscriptField:
    fields = dict(
        id="tf1", transcription_id="tr1", source_id="src-1", page=0,
        field_key="mothers_name", form_label="Mother's Name.",
        status=TranscriptStatus.VALUE, verbatim_value="Bridget. Kilcoyne",
        annotation="trailing period as written", confidence=0.8, needs_review=True,
        note=None, attempts=2, crop_filename="crops/p1-mothers_name.png",
        crop_sha256="b" * 64, crop_bbox=(10, 20, 110, 900), crop_rotation=270,
        agent_call_id="call7", created_at=_DT,
    )
    fields.update(kw)
    return TranscriptField(**fields)


def _repo_with_source(tmp_path: Path) -> Repository:
    repo = Repository(tmp_path / "capsule.db")
    source, *_ = cap._bundle()
    repo.add_source(source)
    return repo


# --- contract ---------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        dict(status=TranscriptStatus.VALUE, verbatim_value=""),
        dict(status=TranscriptStatus.UNKNOWN, verbatim_value=None),
        dict(status=TranscriptStatus.BLANK, verbatim_value="x"),
        dict(status=TranscriptStatus.ILLEGIBLE, verbatim_value="Mary"),
        dict(confidence=1.2),
        dict(crop_rotation=45),
    ],
)
def test_field_contract_rejects_inconsistent_values(overrides):
    with pytest.raises(ValueError):
        _field(**overrides)


def test_unknown_survives_as_a_value_and_blank_as_null():
    assert _field(status=TranscriptStatus.UNKNOWN, verbatim_value="Unknown").verbatim_value == "Unknown"
    assert _field(status=TranscriptStatus.BLANK, verbatim_value=None).verbatim_value is None


# --- repository -------------------------------------------------------------


def test_round_trip_preserves_every_character(tmp_path):
    repo = _repo_with_source(tmp_path)
    fields = [
        _field(),
        _field(id="tf2", field_key="fathers_name", status=TranscriptStatus.UNKNOWN,
               verbatim_value="Unknown", annotation=None, needs_review=False, attempts=1),
        _field(id="tf3", field_key="occupation", status=TranscriptStatus.BLANK,
               verbatim_value=None, annotation=None),
    ]
    repo.add_transcription(_transcription(problems=("read: no reading returned for 'x'",)), fields)

    (stored,) = repo.transcriptions_for_source("src-1")
    assert stored == _transcription(problems=("read: no reading returned for 'x'",))
    assert repo.fields_for_transcription("tr1") == fields


def test_transcripts_are_append_only(tmp_path):
    repo = _repo_with_source(tmp_path)
    repo.add_transcription(_transcription(), [_field()])
    for sql in (
        "UPDATE transcription SET status = 'failed'",
        "DELETE FROM transcription",
        "UPDATE transcript_field SET verbatim_value = 'Bridget Kilcoyne'",
        "DELETE FROM transcript_field",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            repo.connection.execute(sql)


def test_a_rerun_is_a_new_transcription_not_an_overwrite(tmp_path):
    repo = _repo_with_source(tmp_path)
    repo.add_transcription(_transcription(), [_field()])
    later = datetime(2026, 9, 25, tzinfo=timezone.utc)
    repo.add_transcription(
        _transcription(id="tr2", created_at=later),
        [_field(id="tf9", transcription_id="tr2", created_at=later)],
    )
    assert [t.id for t in repo.transcriptions_for_source("src-1")] == ["tr1", "tr2"]


def test_field_cannot_point_at_another_sources_transcription(tmp_path):
    repo = _repo_with_source(tmp_path)
    other, *_ = cap._bundle(id="src-2")
    repo.add_source(other)
    repo.add_transcription(_transcription(), [_field()])
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_transcription(
            _transcription(id="tr3", source_id="src-2"),
            [_field(id="tf4", transcription_id="tr1", source_id="src-2")],
        )


def test_transcription_and_fields_land_atomically(tmp_path):
    repo = _repo_with_source(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_transcription(_transcription(), [_field(), _field()])  # duplicate field id
    assert repo.transcriptions_for_source("src-1") == []


def test_storing_a_transcript_writes_no_evidence(tmp_path):
    """§8.3 — a transcript can never be mistaken for, or promote, an assertion."""
    repo = _repo_with_source(tmp_path)
    count = lambda t: repo.connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
    before = {t: count(t) for t in ("assertion", "snippet", "persona")}
    repo.add_transcription(_transcription(), [_field()])
    assert {t: count(t) for t in before} == before


# --- capture folder ---------------------------------------------------------


def test_transcript_folder_is_written_and_the_seal_still_verifies(tmp_path):
    folder = cap._write(tmp_path)
    assert verify_capture(folder).ok
    crop = b"\x89PNG\r\n\x1a\n-crop"
    write_transcript_folder(folder, _transcription(), [_field()], {"crops/p1-mothers_name.png": crop})

    root = folder / TRANSCRIPT_DIR / "tr1"
    doc = json.loads((root / "transcript.json").read_text(encoding="utf-8"))
    assert doc["derivative"] is True and "not the record" in doc["caution"]
    assert doc["fields"][0]["verbatim_value"] == "Bridget. Kilcoyne"
    assert (root / "crops" / "p1-mothers_name.png").read_bytes() == crop
    assert verify_capture(folder).ok  # hash-excluded, like notes.json


def test_transcript_folder_is_never_clobbered(tmp_path):
    folder = cap._write(tmp_path)
    write_transcript_folder(folder, _transcription(), [_field()], {"crops/p1-mothers_name.png": b"x"})
    with pytest.raises(FileExistsError):
        write_transcript_folder(folder, _transcription(), [_field()], {"crops/p1-mothers_name.png": b"y"})


@pytest.mark.parametrize("bad", ["../../evil.png", "crops/../../x.png", "/abs.png", "crops\\x.png"])
def test_crop_paths_are_confined_to_the_transcript_folder(tmp_path, bad):
    folder = cap._write(tmp_path)
    with pytest.raises(ValueError):
        write_transcript_folder(folder, _transcription(), [], {bad: b"x"})
    assert not (folder / TRANSCRIPT_DIR / "tr1").exists()  # nothing half-written


def test_other_unlisted_files_still_fail_verify(tmp_path):
    """The exclusion is exactly transcript/ — it must not open a hole for anything else."""
    folder = cap._write(tmp_path)
    (folder / "transcripts.json").write_text("{}")
    assert not verify_capture(folder).ok


# --- draft → records ----------------------------------------------------------


def test_records_from_draft_names_crops_and_keeps_provenance(tmp_path):
    layout, reader, *_ = t3._agents(t3._LAYOUT, [t3._READ])
    draft = transcribe_document(t3._pdf(tmp_path), layout_agent=layout, read_agent=reader)
    ids = iter(f"id{i}" for i in range(100))
    transcription, fields, crops = records_from_draft(
        draft, source_id="src-1", media_sha256="c" * 64, model="claude-opus-5",
        created_at=_DT, new_id=lambda: next(ids),
    )
    assert transcription.status == "complete" and transcription.source_id == "src-1"
    assert [f.field_key for f in fields] == ["full_name", "father_name", "mother_name"]
    assert all(f.transcription_id == transcription.id for f in fields)
    assert set(crops) == {f.crop_filename for f in fields}
    father = next(f for f in fields if f.field_key == "father_name")
    assert father.crop_filename == "crops/p1-father_name.png"
    assert father.crop_rotation == 270 and father.agent_call_id == draft.calls[1].id
    assert hashlib.sha256(crops[father.crop_filename]).hexdigest() == father.crop_sha256


def test_records_from_draft_status_reflects_problems(tmp_path):
    read = {"fields": [t3._READ["fields"][0]]}
    layout, reader, *_ = t3._agents(t3._LAYOUT, [read])
    draft = transcribe_document(t3._pdf(tmp_path), layout_agent=layout, read_agent=reader)
    ids = iter(f"id{i}" for i in range(100))
    transcription, fields, _ = records_from_draft(
        draft, source_id="s", media_sha256="c" * 64, model="m", created_at=_DT, new_id=lambda: next(ids)
    )
    assert transcription.status == "partial" and len(transcription.problems) == 2
    assert len(fields) == 1
