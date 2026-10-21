"""Slice 3 regressions: document-primary captures + the excluded notes.json.

Pins the capture-folder/v2 shape the writer emits for a PDF (no snapshot.html;
`document.pdf` is the hashed primary artifact; `archive.kind`/`media_type` set),
that `verify` treats the declared primary artifact generically, and that
`notes.json` is written yet EXCLUDED from the hash manifest and the extra-file
scan — for both document and HTML captures.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from capsule.capture.manifest import (
    Extraction,
    sha256_hex,
    serialize_extraction,
    verify_capture,
    write_capture,
)
from capsule.contracts.evidence import (
    ArchiveKind,
    ArchiveRecord,
    CaptureNote,
    Citation,
    GatingStatus,
    Source,
    SourceQuality,
)

_UTC = timezone.utc
_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=_UTC)
_SID = "018f0000000000000000000000000abc"
_PDF = b"%PDF-1.4\n1 0 obj fake newsletter\n%%EOF"


def _empty_extraction() -> Extraction:
    return Extraction()


def _document_source() -> Source:
    ext = _empty_extraction()
    return Source(
        id=_SID,
        url="https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf",
        title="Spring 2025 Newsletter",
        site_name="isleauhauthistory.com",
        gating_status=GatingStatus.PUBLIC,
        copyright_flag=False,
        source_quality=SourceQuality.DERIVATIVE,
        archive=ArchiveRecord(
            local_snapshot="document.pdf",
            sha256=sha256_hex(_PDF),
            captured_at=_NOW,
            kind=ArchiveKind.DOCUMENT,
            media_type="application/pdf",
        ),
        content_hash=sha256_hex(serialize_extraction(ext)),
        spec_version="capture-folder/v2",
    )


def _citation() -> Citation:
    return Citation(
        id=_SID[:-1] + "d",
        source_id=_SID,
        reference_note="ref",
        short_note="short",
        source_list_entry="list",
        access_date=_NOW.date(),
        archived_url="https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf",
    )


def _note(text: str = "Collected: Spring 2025 issue, mentions the Doyle family.") -> CaptureNote:
    return CaptureNote(id="note-1", source_id=_SID, text=text, created_at=_NOW)


def test_document_capture_writes_v2_and_verifies(tmp_path) -> None:
    folder = write_capture(
        tmp_path,
        source=_document_source(),
        citation=_citation(),
        extraction=_empty_extraction(),
        primary_bytes=_PDF,
    )
    assert verify_capture(folder).ok

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["spec_version"] == "capture-folder/v2"
    assert manifest["archive"]["kind"] == "document"
    assert manifest["archive"]["media_type"] == "application/pdf"
    assert manifest["archive"]["local_snapshot"] == "document.pdf"
    assert "document.pdf" in manifest["hashes"]["files"]
    assert "snapshot.html" not in manifest["hashes"]["files"]
    assert (folder / "document.pdf").read_bytes() == _PDF
    assert not (folder / "snapshot.html").exists()


def test_document_capture_requires_primary_bytes(tmp_path) -> None:
    with pytest.raises(ValueError, match="requires primary_bytes"):
        write_capture(
            tmp_path,
            source=_document_source(),
            citation=_citation(),
            extraction=_empty_extraction(),
            snapshot_html="<html>not a document</html>",
        )


def test_document_capture_rejects_tampered_bytes(tmp_path) -> None:
    """archive.sha256 must attest the exact primary bytes."""
    src = _document_source()
    with pytest.raises(ValueError, match="archive.sha256 does not match"):
        write_capture(
            tmp_path,
            source=src,
            citation=_citation(),
            extraction=_empty_extraction(),
            primary_bytes=_PDF + b"tampered",
        )


def test_notes_json_written_but_excluded_from_hashes(tmp_path) -> None:
    folder = write_capture(
        tmp_path,
        source=_document_source(),
        citation=_citation(),
        extraction=_empty_extraction(),
        primary_bytes=_PDF,
        notes=[_note()],
    )
    assert (folder / "notes.json").is_file()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert "notes.json" not in manifest["hashes"]["files"]  # excluded
    notes = json.loads((folder / "notes.json").read_text(encoding="utf-8"))
    assert notes[0]["text"].startswith("Collected: Spring 2025")
    # A note present on disk must NOT make verify flag an extra/unlisted file.
    assert verify_capture(folder).ok


def test_deleting_primary_document_fails_verify(tmp_path) -> None:
    folder = write_capture(
        tmp_path,
        source=_document_source(),
        citation=_citation(),
        extraction=_empty_extraction(),
        primary_bytes=_PDF,
    )
    (folder / "document.pdf").unlink()
    result = verify_capture(folder)
    assert not result.ok
    assert "document.pdf" in result.missing
