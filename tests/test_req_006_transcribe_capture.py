"""REQ-006 T6 — `capsule transcribe <capture>`: end to end on a sealed capture.

Picks the scanned original (never Capsule's own page render), checks its bytes
against the sealed manifest before spending a call, transcribes, compares with
the page's index claims, and lands everything append-only: agent logs first (a
paid call is never lost), then the hash-excluded transcript folder, then the
store — and if the store refuses, the folder it would have described goes too.
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

import tests.test_capture_manifest as cap
import tests.test_req_006_reconcile as t5
import tests.test_req_006_transcribe as t3
from capsule.agent.base import Agent
from capsule.agent.fake import FakeTransport
from capsule.capture.manifest import MediaItem, verify_capture, write_capture
from capsule.capture.transcript import TRANSCRIPT_DIR
from capsule.pipeline.transcribe_capture import transcribe_capture
from capsule.store.repository import Repository

_DT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _pdf_bytes(pages: int = 2) -> bytes:
    imgs = [Image.new("RGB", (2000, 2400), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    imgs[0].save(buffer, format="PDF", save_all=True, append_images=imgs[1:], resolution=300)
    return buffer.getvalue()


def _capture(tmp_path: Path, media=None) -> tuple[Path, Path]:
    root = tmp_path / "captures"
    root.mkdir()
    source, citation, extraction, snapshot, _ = cap._bundle()
    media = media or (
        MediaItem(slug="record", ext="pdf", data=_pdf_bytes(1), mime="application/pdf",
                  provenance="render:page-pdf"),
        MediaItem(slug="d-k-1905-0022719", ext="pdf", data=_pdf_bytes(), mime="application/pdf",
                  provenance="page:embedded"),
    )
    folder = write_capture(root, source=source, citation=citation, extraction=extraction,
                           snapshot_html=snapshot, media=media, created_at=_DT)
    return root, folder


def _repo(tmp_path: Path, *, with_source: bool = True) -> Repository:
    repo = Repository(tmp_path / "capsule.db")
    if with_source:
        source, *_ = cap._bundle()
        repo.add_source(source)
        t5._assertion_row(repo)  # index claim: date = 1905-11-12
    return repo


def _agents():
    layout = FakeTransport(json.dumps(t3._LAYOUT))
    read = FakeTransport(json.dumps(t3._READ))
    # ids: transcription id0, fields id1..id3 (full_name, father_name, mother_name)
    recon = FakeTransport(json.dumps({"comparisons": [
        {"assertion_id": "a-date", "transcript_field_id": "id1", "verdict": "conflict",
         "explanation": "index date 1905-11-12; image field differs"},
    ]}))
    seq = iter(f"call{i}" for i in range(100))
    mk = lambda name, t: Agent(name, "claude-opus-5", t, now=lambda: _DT, new_id=lambda: next(seq))  # noqa: E731
    return mk("layout", layout), mk("transcriber", read), mk("reconciler", recon), (layout, read, recon)


def _run(repo, root, folder, *, prefix="id", **kw):
    layout, reader, reconciler, transports = _agents()
    ids = iter(f"{prefix}{i}" for i in range(100))
    outcome = transcribe_capture(
        repository=repo, captures_root=root, capture_id=folder.name,
        layout_agent=layout, read_agent=reader, reconcile_agent=reconciler,
        now=lambda: _DT, new_id=lambda: next(ids), **kw,
    )
    return outcome, transports


def test_end_to_end_lands_append_only_and_keeps_the_seal(tmp_path):
    root, folder = _capture(tmp_path)
    repo = _repo(tmp_path)
    outcome, (layout_t, _read_t, recon_t) = _run(repo, root, folder)

    # the certificate was read, not Capsule's page render
    assert {i.ref.media_filename for i in layout_t.calls[0]["images"]} == {"1-d-k-1905-0022719.pdf"}
    assert outcome.transcription.media_filename == "1-d-k-1905-0022719.pdf"

    (stored,) = repo.transcriptions_for_source("src-1")
    assert stored.status == "complete" and len(repo.fields_for_transcription(stored.id)) == 3
    (conflict,) = repo.comparisons_for_transcription(stored.id)
    assert conflict.verdict == "conflict" and conflict.index_value == "1905-11-12"

    tdir = folder / TRANSCRIPT_DIR / stored.id
    doc = json.loads((tdir / "transcript.json").read_text(encoding="utf-8"))
    assert len(doc["fields"]) == 3 and doc["comparisons"][0]["verdict"] == "conflict"
    assert len(list((tdir / "crops").iterdir())) == 3
    logs = sorted(p.name for p in (folder / "agent_log").iterdir())
    assert sum("-layout-" in n for n in logs) == 1 and sum("-reconciler-" in n for n in logs) == 1
    assert verify_capture(folder).ok

    assert [a.verbatim_value for a in repo.current_assertions("src-1")] == ["1905-11-12"]
    assert "1905-11-12" in recon_t.calls[0]["prompt"]


def test_two_scanned_originals_need_an_explicit_choice(tmp_path):
    media = (
        MediaItem(slug="front", ext="pdf", data=_pdf_bytes(1), provenance="page:embedded"),
        MediaItem(slug="back", ext="pdf", data=_pdf_bytes(1), provenance="page:embedded"),
    )
    root, folder = _capture(tmp_path, media)
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="--media"):
        _run(repo, root, folder)
    outcome, _ = _run(repo, root, folder, media="1-back.pdf")
    assert outcome.transcription.media_filename == "1-back.pdf"


def test_altered_media_is_refused_before_any_call(tmp_path):
    root, folder = _capture(tmp_path)
    repo = _repo(tmp_path)
    (folder / "media" / "1-d-k-1905-0022719.pdf").write_bytes(_pdf_bytes(1))
    with pytest.raises(ValueError, match="does not match"):
        _run(repo, root, folder)
    assert not (folder / TRANSCRIPT_DIR).exists()


def test_store_refusal_removes_the_folder_but_keeps_the_paid_calls(tmp_path):
    root, folder = _capture(tmp_path)
    repo = _repo(tmp_path, with_source=False)  # the FK will refuse
    with pytest.raises(sqlite3.IntegrityError):
        _run(repo, root, folder)
    assert not any((folder / TRANSCRIPT_DIR).glob("*/transcript.json"))
    assert len(list((folder / "agent_log").iterdir())) >= 2  # layout + read calls kept
    assert verify_capture(folder).ok


def test_a_rerun_is_a_second_transcription(tmp_path):
    root, folder = _capture(tmp_path)
    repo = _repo(tmp_path)
    _run(repo, root, folder)
    _run(repo, root, folder, prefix="second")
    assert len(repo.transcriptions_for_source("src-1")) == 2
    assert len(list((folder / TRANSCRIPT_DIR).iterdir())) == 2


def test_page_chrome_is_not_a_scan(tmp_path):
    """Live corpus, 2026-09-24: 13 older NYC captures were 'ambiguous' because the site's
    logo (doris-logo.png) and a JS plugin saved as pdfobject-min.pdf sat beside the
    certificate. A scan is something that decodes to a real page of pixels."""
    logo = io.BytesIO()
    Image.new("RGBA", (1220, 656), "navy").save(logo, format="PNG")  # the real logo's size
    media = (
        MediaItem(slug="d-k-1887-0012676", ext="pdf", data=_pdf_bytes(), mime="application/pdf"),
        MediaItem(slug="doris-logo", ext="png", data=logo.getvalue(), mime="image/png"),
        MediaItem(slug="pdfobject-min", ext="pdf", data=b"/*! PDFObject v2 */ var x=1;",
                  mime="application/pdf", provenance="page:embedded"),
    )
    root, folder = _capture(tmp_path, media)
    outcome, _ = _run(_repo(tmp_path), root, folder)
    assert outcome.transcription.media_filename == "0-d-k-1887-0012676.pdf"
