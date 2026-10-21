"""Slice 4 regression: the archive-only document pipeline + note threading.

`run_document_capture` makes NO model call — it writes a verifiable v2 document
capture straight from provided bytes, threads a researcher note into both the
excluded notes.json and the append-only capture_note table, and refreshes the
collection index. All offline (real Repository in tmp, no agent, no network).
"""
from __future__ import annotations

from datetime import datetime, timezone

from capsule.capture.manifest import verify_capture
from capsule.contracts.evidence import ArchiveKind, GatingStatus
from capsule.pipeline.pipeline import run_document_capture
from capsule.store.repository import Repository

_URL = "https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf"
_PDF = b"%PDF-1.4\n1 0 obj Isle au Haut Historical Society newsletter\n%%EOF"


def _ids():
    seq = iter(f"018f{i:028x}" for i in range(1, 1000))
    return lambda: next(seq)


def _fixed_now():
    return datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def test_document_capture_pipeline(tmp_path) -> None:
    repo = Repository(tmp_path / "capsule.db")
    try:
        outcome = run_document_capture(
            _URL,
            document_bytes=_PDF,
            ext="pdf",
            media_type="application/pdf",
            repository=repo,
            captures_root=tmp_path / "captures",
            note="Collected because it names the Doyle family; extract later.",
            now=_fixed_now,
            new_id=_ids(),
        )
        # A verifiable v2 document capture, archive-only.
        assert outcome.verify.ok
        assert outcome.agent_call is None
        assert outcome.source.archive.kind == ArchiveKind.DOCUMENT
        assert outcome.source.archive.media_type == "application/pdf"
        assert outcome.source.spec_version == "capture-folder/v2"
        assert outcome.source.title == "Spring 2025 Newsletter"
        assert outcome.source.site_name == "isleauhauthistory.com"
        assert outcome.source.gating_status == GatingStatus.PUBLIC
        assert outcome.extraction.assertions == ()  # zero-assertion capture

        folder = outcome.capture_folder
        assert (folder / "document.pdf").read_bytes() == _PDF
        assert not (folder / "snapshot.html").exists()
        assert (folder / "notes.json").is_file()

        # The note reached the append-only store, linked to this source.
        notes = repo.notes_for_source(outcome.source.id)
        assert len(notes) == 1
        assert notes[0].text.startswith("Collected because it names the Doyle")

        # Re-verify from disk (index refresh must not disturb integrity).
        assert verify_capture(folder).ok
    finally:
        repo.close()


def test_local_file_document_is_gated_and_copyright_flagged(tmp_path) -> None:
    """A local file:// document (a purchased DAR record downloaded to disk) is not
    a public web resource: the file:// scheme forces the gated posture —
    copyright-flagged, no Wayback — even when the caller requested `public`, so it
    is never treated as republishable."""
    repo = Repository(tmp_path / "capsule.db")
    try:
        outcome = run_document_capture(
            "file:///C:/Users/you/Downloads/record-copy-0001.pdf",
            document_bytes=_PDF,
            ext="pdf",
            media_type="application/pdf",
            repository=repo,
            captures_root=tmp_path / "captures",
            gating=GatingStatus.PUBLIC,  # the file:// scheme overrides this
            now=_fixed_now,
            new_id=_ids(),
        )
        assert outcome.verify.ok
        assert outcome.source.gating_status == GatingStatus.GATED
        assert outcome.source.copyright_flag is True
        assert outcome.source.archive.wayback_url is None
        assert outcome.source.site_name is None  # file:// has no host
        assert outcome.source.title == "record-copy-0001"
        # Round-trips through the store as gated.
        assert repo.get_source(outcome.source.id).gating_status == GatingStatus.GATED
    finally:
        repo.close()


def test_document_capture_without_note(tmp_path) -> None:
    """No note ⇒ no notes.json, still a valid capture."""
    repo = Repository(tmp_path / "capsule.db")
    try:
        outcome = run_document_capture(
            _URL,
            document_bytes=_PDF,
            ext="pdf",
            media_type="application/pdf",
            repository=repo,
            captures_root=tmp_path / "captures",
            now=_fixed_now,
            new_id=_ids(),
        )
        assert outcome.verify.ok
        assert not (outcome.capture_folder / "notes.json").exists()
        assert repo.notes_for_source(outcome.source.id) == []
    finally:
        repo.close()
