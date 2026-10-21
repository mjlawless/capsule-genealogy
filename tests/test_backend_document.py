"""Slice 5 regression: POST /capture accepts a document (PDF) + a note.

A document capture is archive-only — no API key required (no model call) — and
threads the researcher note into the capture. Also pins the exactly-one-of
html/document request rule. Offline: settings overridden to tmp paths, no network.
"""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from capsule.backend.app import Settings, app, get_settings

_URL = "https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf"
_PDF = b"%PDF-1.4\n1 0 obj Isle au Haut newsletter\n%%EOF"
_TOKEN = "test-secret"


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=_TOKEN,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_capture_document_with_note(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            headers={"X-Capsule-Token": _TOKEN},
            json={
                "url": _URL,
                "document": {
                    "data_base64": _b64(_PDF),
                    "filename": "Spring_2025 Newsletter.pdf",
                    "mime": "application/pdf",
                },
                "note": "Collected because it names the Doyle family; extract later.",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verify_ok"] is True
        assert body["assertions"] == []
        assert body["media_count"] == 0
        folder = Path(body["capture_folder"])
        assert (folder / "document.pdf").read_bytes() == _PDF
        assert (folder / "notes.json").is_file()
        assert not (folder / "snapshot.html").exists()
    finally:
        app.dependency_overrides.clear()


def test_capture_rejects_both_html_and_document(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            headers={"X-Capsule-Token": _TOKEN},
            json={
                "url": _URL,
                "html": "<html></html>",
                "document": {"data_base64": _b64(_PDF)},
            },
        )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_capture_rejects_neither_html_nor_document(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            headers={"X-Capsule-Token": _TOKEN},
            json={"url": _URL},
        )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
