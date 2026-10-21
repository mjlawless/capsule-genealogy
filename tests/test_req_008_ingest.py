"""REQ-008 — `capsule ingest <file> --url <permalink>`: a file on disk becomes a capture.

Offline: no fetch and no model call exist on this path at all, so nothing is faked. Each
acceptance criterion (spec §4) has a test, plus the two boundaries the spec leaves to the
implementation: an ingest never blocks a PAGE capture of its URL, and the bytes (not the
file name) decide what is accepted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capsule.cli import app as cli_app
from capsule.pipeline.dedup import find_capture, find_ingested
from capsule.store.repository import Repository

runner = CliRunner()

_VIEWER = "https://www.archion.de/en/viewer/churchRegister/239254"
_PDF_PAGE_2 = b"%PDF-1.7\n% page 2 of the register\n%%EOF\n"
_PDF_PAGE_6 = b"%PDF-1.7\n% page 6 of the register\n%%EOF\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # never touch the real data/ or .env
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


def _write(tmp: Path, name: str, data: bytes) -> Path:
    path = tmp / name
    path.write_bytes(data)
    return path


def _ingest(env: Path, file: Path, *extra: str, url: str = _VIEWER):
    return runner.invoke(
        cli_app.app,
        [
            "ingest",
            str(file),
            "--url",
            url,
            "--db",
            str(env / "capsule.db"),
            "--captures",
            str(env / "captures"),
            *extra,
        ],
    )


def _out(result) -> dict:
    return json.loads(result.stdout.strip().splitlines()[-1])


def _manifest(env: Path, capture_id: str) -> dict:
    return json.loads((env / "captures" / capture_id / "manifest.json").read_text("utf-8"))


# -- §4.1 — a v2 document capture whose document hash IS the input file's -----------


def test_ingest_writes_a_v2_document_capture_hashed_byte_for_byte(env):
    scan = _write(env, "scan.pdf", _PDF_PAGE_2)
    result = _ingest(env, scan, "--gating", "gated")
    assert result.exit_code == 0, result.output
    out = _out(result)
    assert out["dedup"] == "new" and out["verify_ok"] is True

    folder = env / "captures" / out["capture_id"]
    expected = hashlib.sha256(_PDF_PAGE_2).hexdigest()
    assert (folder / "document.pdf").read_bytes() == _PDF_PAGE_2
    manifest = _manifest(env, out["capture_id"])
    assert manifest["spec_version"] == "capture-folder/v2"
    assert manifest["archive"]["kind"] == "document"
    assert manifest["archive"]["sha256"] == expected == out["sha256"]
    assert manifest["archive"]["provenance"] == "user:file"
    assert json.loads((folder / "extraction.json").read_text("utf-8"))["assertions"] == []


def test_official_download_provenance_is_recorded_in_manifest_and_index(env):
    scan = _write(env, "export.pdf", _PDF_PAGE_2)
    result = _ingest(env, scan, "--provenance", "site:official-download")
    out = _out(result)
    assert _manifest(env, out["capture_id"])["archive"]["provenance"] == "site:official-download"

    repo = Repository(env / "capsule.db")
    try:
        row = repo.connection.execute(
            "SELECT provenance FROM archive_record WHERE sha256 = ?", (out["sha256"],)
        ).fetchone()
    finally:
        repo.close()
    assert row == ("site:official-download",)


# -- §4.2 / §4.3 — dedup keyed on (url, sha256) --------------------------------------


def test_reingesting_the_same_bytes_from_the_same_url_is_blocked_and_writes_nothing(env):
    scan = _write(env, "scan.pdf", _PDF_PAGE_2)
    first = _out(_ingest(env, scan))
    before = sorted(p.name for p in (env / "captures").iterdir())

    again = _ingest(env, scan)
    assert again.exit_code == 3
    out = _out(again)
    assert out["dedup"] == "blocked"
    assert out["duplicate_of"] == first["capture_id"]
    assert sorted(p.name for p in (env / "captures").iterdir()) == before


def test_recapture_overrides_the_block(env):
    scan = _write(env, "scan.pdf", _PDF_PAGE_2)
    first = _out(_ingest(env, scan))
    again = _ingest(env, scan, "--recapture")
    assert again.exit_code == 0, again.output
    assert _out(again)["capture_id"] != first["capture_id"]


def test_a_different_page_under_the_same_viewer_url_is_a_new_capture(env):
    first = _out(_ingest(env, _write(env, "p2.pdf", _PDF_PAGE_2)))
    second = _ingest(env, _write(env, "p6.pdf", _PDF_PAGE_6))
    assert second.exit_code == 0, second.output
    out = _out(second)
    assert out["dedup"] == "new"
    assert out["capture_id"] != first["capture_id"]


def test_same_bytes_from_a_different_url_is_a_new_capture(env):
    scan = _write(env, "scan.pdf", _PDF_PAGE_2)
    _ingest(env, scan)
    other = _ingest(env, scan, url="https://www.archion.de/en/viewer/churchRegister/239260")
    assert other.exit_code == 0 and _out(other)["dedup"] == "new"


# -- §4.4 — it is findable: collection index + `capsule query --url` -----------------


def test_ingest_appears_in_the_index_and_answers_query(env):
    out = _out(_ingest(env, _write(env, "scan.pdf", _PDF_PAGE_2)))
    index = (env / "captures" / "index.html").read_text("utf-8")
    assert out["capture_id"] in index

    q = runner.invoke(
        cli_app.app,
        ["query", "--url", _VIEWER, "--db", str(env / "capsule.db"), "--captures", str(env / "captures")],
    )
    assert q.exit_code == 0, q.output
    answer = json.loads(q.stdout)
    assert answer["captured"] is True
    assert answer["capture_id"] == out["capture_id"]
    assert answer["ingested"] is True


# -- §4.5 — gated means copyright-flagged, never Wayback -----------------------------


def test_gated_ingest_is_copyright_flagged_and_never_submitted_to_wayback(env):
    out = _out(_ingest(env, _write(env, "scan.pdf", _PDF_PAGE_2), "--gating", "gated"))
    assert out["gating"] == "gated"
    manifest = _manifest(env, out["capture_id"])
    assert manifest["source"]["copyright_flag"] is True
    assert manifest["archive"]["wayback_url"] is None


# -- the boundary the spec leaves open: a FILE from a page is not the page ------------


def test_an_ingest_never_blocks_a_page_capture_of_its_url(env):
    _ingest(env, _write(env, "scan.pdf", _PDF_PAGE_2))
    # What the backend and `capsule capture` ask before capturing the page itself:
    assert find_capture(_VIEWER, db_path=env / "capsule.db", captures_root=env / "captures") is None
    held = find_capture(
        _VIEWER, db_path=env / "capsule.db", captures_root=env / "captures", include_ingested=True
    )
    assert held is not None and held.ingested and held.complete


def test_find_ingested_matches_url_and_bytes_only(env):
    out = _out(_ingest(env, _write(env, "scan.pdf", _PDF_PAGE_2)))
    digest = out["sha256"]
    hit = find_ingested(_VIEWER, digest, captures_root=env / "captures")
    assert hit is not None and hit.capture_id == out["capture_id"] and hit.complete
    assert find_ingested(_VIEWER, "0" * 64, captures_root=env / "captures") is None
    assert find_ingested(_VIEWER + "?x=1", digest, captures_root=env / "captures") is None


# -- input boundaries ----------------------------------------------------------------


def test_the_bytes_decide_the_type_not_the_file_name(env):
    # A JPEG saved with a .pdf name is archived as what it is (BUG-008).
    jpeg = _write(env, "misnamed.pdf", b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    out = _out(_ingest(env, jpeg))
    folder = env / "captures" / out["capture_id"]
    assert (folder / "document.jpg").is_file()
    assert out["media_type"] == "image/jpeg"


def test_unrecognized_bytes_are_refused(env):
    script = _write(env, "record.pdf", b"console.log('not a record')")
    result = _ingest(env, script)
    assert result.exit_code == 2
    assert "not a recognized PDF or image" in result.output
    assert not (env / "captures").exists()


@pytest.mark.parametrize("url", ["file:///C:/Users/me/Downloads/scan.pdf", "not a url"])
def test_url_must_be_the_http_page_the_file_came_from(env, url):
    result = _ingest(env, _write(env, "scan.pdf", _PDF_PAGE_2), url=url)
    assert result.exit_code == 2
    assert "--url must be the http(s) page" in result.output


def test_oversized_file_is_refused_before_reading(env, monkeypatch):
    monkeypatch.setattr(cli_app, "MAX_ARTIFACT_BYTES", 8)
    result = _ingest(env, _write(env, "scan.pdf", _PDF_PAGE_2))
    assert result.exit_code == 2
    assert "the limit is 8" in result.output


# -- researcher-stated fields ---------------------------------------------------------


def test_stated_source_quality_and_record_type_carry_an_honest_rationale(env):
    out = _out(
        _ingest(
            env,
            _write(env, "scan.pdf", _PDF_PAGE_2),
            "--source-quality",
            "original",
            "--record-type",
            "baptism",
            "--note",
            "Image 6, the 1848 baptisms",
        )
    )
    manifest = _manifest(env, out["capture_id"])
    assert manifest["source"]["source_quality"] == "original"
    assert manifest["source"]["record_type"] == "baptism"
    notes = json.loads((env / "captures" / out["capture_id"] / "notes.json").read_text("utf-8"))
    assert notes[0]["text"] == "Image 6, the 1848 baptisms"

    repo = Repository(env / "capsule.db")
    try:
        rationale = repo.connection.execute(
            "SELECT source_quality_rationale FROM source WHERE url = ?", (_VIEWER,)
        ).fetchone()[0]
    finally:
        repo.close()
    assert "stated by the researcher" in rationale


def test_default_source_quality_stays_derivative_with_no_rationale(env):
    out = _out(_ingest(env, _write(env, "scan.pdf", _PDF_PAGE_2)))
    assert _manifest(env, out["capture_id"])["source"]["source_quality"] == "derivative"
    repo = Repository(env / "capsule.db")
    try:
        rationale = repo.connection.execute(
            "SELECT source_quality_rationale FROM source WHERE url = ?", (_VIEWER,)
        ).fetchone()[0]
    finally:
        repo.close()
    assert rationale is None
