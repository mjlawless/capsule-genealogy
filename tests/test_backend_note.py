"""Slice 6 regression: POST /note annotates an already-collected capture.

Adding a note needs no API key (no model call) and is archive-safe — the note is
excluded from the capture's hashes. Also pins the auth gate and the 404/422 error
mapping. Offline: settings overridden to tmp paths, no network. A capture to
annotate is first created via POST /capture with a document (also key-free).
"""
from __future__ import annotations

import base64
import json
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


def _capture_a_document(client: TestClient) -> str:
    resp = client.post(
        "/capture",
        headers={"X-Capsule-Token": _TOKEN},
        json={
            "url": _URL,
            "document": {
                "data_base64": base64.b64encode(_PDF).decode("ascii"),
                "filename": "Spring_2025 Newsletter.pdf",
                "mime": "application/pdf",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    return Path(resp.json()["capture_folder"]).name


def test_note_added_to_existing_capture(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture_a_document(client)
        resp = client.post(
            "/note",
            headers={"X-Capsule-Token": _TOKEN},
            json={
                "capture_id": capture_id,
                "text": "Names the Doyle family; extract later.",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["capture_id"] == capture_id
        assert body["text"].startswith("Names the Doyle")
        assert body["author"] == "owner"
        assert body["created_at"].endswith("Z")

        notes_path = tmp_path / "captures" / capture_id / "notes.json"
        payloads = json.loads(notes_path.read_text(encoding="utf-8"))
        assert [p["text"] for p in payloads] == ["Names the Doyle family; extract later."]
    finally:
        app.dependency_overrides.clear()


def test_note_unknown_capture_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        resp = client.post(
            "/note",
            headers={"X-Capsule-Token": _TOKEN},
            json={"capture_id": "20260711-isleauhauthistory-deadbeef", "text": "x"},
        )
        assert resp.status_code == 404, resp.text
    finally:
        app.dependency_overrides.clear()


def test_note_malformed_id_returns_422(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        resp = client.post(
            "/note",
            headers={"X-Capsule-Token": _TOKEN},
            json={"capture_id": "../evil", "text": "x"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_note_requires_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        resp = client.post(
            "/note",
            json={"capture_id": "20260711-isleauhauthistory-deadbeef", "text": "x"},
        )
        assert resp.status_code == 401, resp.text
    finally:
        app.dependency_overrides.clear()
