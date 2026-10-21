"""Slice 6 regression: the `capsule note` command.

Annotates a capture that already exists on disk + in the index. Offline: the
capture is seeded with `run_document_capture` (no agent), then the command is
driven through Typer's CliRunner against the same db/captures paths.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from typer.testing import CliRunner

from capsule.cli.app import app
from capsule.pipeline.pipeline import run_document_capture
from capsule.store.repository import Repository

runner = CliRunner()

_URL = "https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf"
_PDF = b"%PDF-1.4\n1 0 obj Isle au Haut newsletter\n%%EOF"


def _seed_capture(tmp_path) -> str:
    repo = Repository(tmp_path / "capsule.db")
    try:
        seq = iter(f"018f{i:028x}" for i in range(1, 1000))
        outcome = run_document_capture(
            _URL,
            document_bytes=_PDF,
            ext="pdf",
            media_type="application/pdf",
            repository=repo,
            captures_root=tmp_path / "captures",
            now=lambda: datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc),
            new_id=lambda: next(seq),
        )
    finally:
        repo.close()
    return outcome.capture_folder.name


def test_cli_note_appends_and_reports(tmp_path) -> None:
    capture_id = _seed_capture(tmp_path)
    result = runner.invoke(
        app,
        [
            "note",
            capture_id,
            "Names the Doyle family; revisit later.",
            "--db",
            str(tmp_path / "capsule.db"),
            "--captures",
            str(tmp_path / "captures"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"noted on {capture_id}" in result.output

    notes = json.loads((tmp_path / "captures" / capture_id / "notes.json").read_text("utf-8"))
    assert [n["text"] for n in notes] == ["Names the Doyle family; revisit later."]


def test_cli_note_unknown_capture_exits_1(tmp_path) -> None:
    # Seed so the db exists; annotate a different, non-existent id.
    _seed_capture(tmp_path)
    result = runner.invoke(
        app,
        [
            "note",
            "20260711-isleauhauthistory-deadbeef",
            "text",
            "--db",
            str(tmp_path / "capsule.db"),
            "--captures",
            str(tmp_path / "captures"),
        ],
    )
    assert result.exit_code == 1
    assert "could not add note" in result.output
