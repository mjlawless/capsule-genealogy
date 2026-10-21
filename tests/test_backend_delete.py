"""Slice 9 regression: GET / serves the interactive index; DELETE trashes.

The backend serves the captures index live (with a trashcan wired to this
backend, carrying the token), and DELETE /capture/{id} moves the capture to
_trash/. Offline: settings overridden to tmp paths, a capture is first created
via POST /capture with a document (no API key needed).
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


def test_index_is_served_interactive_and_delete_trashes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture_a_document(client)

        # GET / renders the capture, a trashcan, and the injected token.
        page = client.get("/")
        assert page.status_code == 200, page.text
        assert "text/html" in page.headers["content-type"]
        assert capture_id in page.text
        assert 'class="trash"' in page.text
        assert "__CAPSULE_TOKEN__" in page.text
        # Defense-in-depth: nosniff so the token page can't be MIME-confused.
        assert page.headers.get("x-content-type-options") == "nosniff"

        # DELETE moves it to _trash/ and reports where.
        resp = client.delete(f"/capture/{capture_id}", headers={"X-Capsule-Token": _TOKEN})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        assert (tmp_path / "captures" / "_trash" / capture_id).is_dir()
        assert not (tmp_path / "captures" / capture_id).exists()

        # It is gone from a fresh render.
        assert capture_id not in client.get("/").text
    finally:
        app.dependency_overrides.clear()


def test_served_index_local_links_resolve_to_capture_files(tmp_path: Path) -> None:
    """The served index's relative Local/Document links must resolve: GET on a
    capture file returns its bytes, and GET on the folder returns a listing."""
    client = _client(tmp_path)
    try:
        capture_id = _capture_a_document(client)

        # The Document link -> the archived PDF bytes.
        doc = client.get(f"/{capture_id}/document.pdf")
        assert doc.status_code == 200, doc.text
        assert doc.content == _PDF
        assert "application/pdf" in doc.headers["content-type"]

        # The Local link (folder, trailing slash) -> a listing that links the files.
        listing = client.get(f"/{capture_id}/")
        assert listing.status_code == 200
        assert "document.pdf" in listing.text
        assert "manifest.json" in listing.text
    finally:
        app.dependency_overrides.clear()


def test_capture_file_rejects_traversal_and_unknown(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture_a_document(client)
        # A traversing path can't escape the capture folder.
        assert client.get(f"/{capture_id}/..%2f..%2f..%2fetc").status_code in (404, 422)
        # A well-formed but unknown capture id -> 404; a file that isn't there -> 404.
        assert client.get("/20260711-isleauhauthistory-deadbeef/x.pdf").status_code == 404
        assert client.get(f"/{capture_id}/nope.pdf").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_delete_requires_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture_a_document(client)
        resp = client.delete(f"/capture/{capture_id}")  # no token header
        assert resp.status_code == 401, resp.text
        # ...and the capture is untouched.
        assert (tmp_path / "captures" / capture_id).is_dir()
    finally:
        app.dependency_overrides.clear()


def test_delete_unknown_capture_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        resp = client.delete(
            "/capture/20260711-isleauhauthistory-deadbeef",
            headers={"X-Capsule-Token": _TOKEN},
        )
        assert resp.status_code == 404, resp.text
    finally:
        app.dependency_overrides.clear()


def test_delete_malformed_id_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        # A traversing id must never delete outside the root. The decoded
        # multi-segment path matches no DELETE route — 404 (unmatched), 422 (bad
        # single-segment id), or 405 (the GET file route owns that path shape) —
        # never a delete.
        resp = client.delete(
            "/capture/..%2F..%2Fetc",
            headers={"X-Capsule-Token": _TOKEN},
        )
        assert resp.status_code in (404, 405, 422), resp.text
    finally:
        app.dependency_overrides.clear()
