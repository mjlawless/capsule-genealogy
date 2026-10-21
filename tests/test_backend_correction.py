"""C5: POST /correction — token-gated human corrections over HTTP.

Offline: a fake Agent captures one HTML page through the backend, then
/correction edits/adds/retracts a fact and auto-writes an eval fixture. Mirrors
test_backend's dependency-override style; fixtures_root is pointed at tmp so a test
never writes into the repo's committed evals/fixtures.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}
_URL = "https://www.findagrave.com/memorial/1"
_PAGE = (
    "<html><head><title>Mary Kelly (1810-1881)</title></head>"
    "<body><h1>Mary Kelly</h1><p>Died 3 March 1881.</p>"
    "<p>Buried at Glasnevin Cemetery, Dublin.</p></body></html>"
)
_RAW = {
    "record_type": "burial",
    "personas": [{"label": "Mary Kelly", "role": "deceased"}],
    "assertions": [
        {"persona": "Mary Kelly", "field": "death_date", "value": "3 March 1881",
         "evidence_type": "stated", "snippet": "Died 3 March 1881."},
        {"persona": "Mary Kelly", "field": "burial_place", "value": "Glasnevin Cemetery, Dublin",
         "evidence_type": "stated", "snippet": "Buried at Glasnevin Cemetery, Dublin."},
    ],
}


def _client(tmp_path, *, token=_TOKEN):
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend import app as backend
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=token,
        fixtures_root=tmp_path / "fixtures",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    return TestClient(app), backend, settings


def _capture(client) -> dict:
    resp = client.post("/capture", json={"url": _URL, "html": _PAGE, "gating": "public"}, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assertion_id(body: dict, field: str) -> str:
    (a,) = [a for a in body["assertions"] if a["field"] == field]
    return a["id"]


def _persona_id(settings) -> str:
    """The single capture's persona id, read straight from the store (an `add`
    correction references a persona by id, which the capture response doesn't
    expose)."""
    from capsule.store.repository import Repository

    repo = Repository(settings.db_path)
    try:
        (sid,) = repo.connection.execute("SELECT id FROM source").fetchone()
        return next(a.persona_id for a in repo.current_assertions(sid))
    finally:
        repo.close()


def _fixture_expected(settings, capture_id: str) -> dict:
    return json.loads((settings.fixtures_root / capture_id / "expected.json").read_text(encoding="utf-8"))


def test_edit_correction_supersedes_and_writes_a_fixture(tmp_path):
    client, backend, settings = _client(tmp_path)
    try:
        body = _capture(client)
        capture_id = Path(body["capture_folder"]).name
        death_id = _assertion_id(body, "death_date")

        resp = client.post(
            "/correction",
            json={
                "capture_id": capture_id,
                "action": "edit",
                "assertion_id": death_id,
                "new_value": "13 March 1881",
                "rationale": "model misread the day",
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["action"] == "edit"
        assert out["field"] == "death_date"
        assert out["supersedes_id"] == death_id
        assert out["assertion_id"] != death_id
        assert out["fixture"] == capture_id  # a fixture was auto-generated
        values = {(a["field"], a["value"]) for a in _fixture_expected(settings, capture_id)["assertions"]}
        assert ("death_date", "13 March 1881") in values  # carrying the corrected value
    finally:
        backend.app.dependency_overrides.clear()


def test_retract_correction_removes_the_fact_from_the_fixture(tmp_path):
    client, backend, settings = _client(tmp_path)
    try:
        body = _capture(client)
        capture_id = Path(body["capture_folder"]).name
        burial_id = _assertion_id(body, "burial_place")

        resp = client.post(
            "/correction",
            json={"capture_id": capture_id, "action": "retract", "assertion_id": burial_id},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["action"] == "retract"
        expected = _fixture_expected(settings, capture_id)
        assert not any(a["field"] == "burial_place" for a in expected["assertions"])
    finally:
        backend.app.dependency_overrides.clear()


def test_add_correction_introduces_a_fact(tmp_path):
    client, backend, settings = _client(tmp_path)
    try:
        body = _capture(client)
        capture_id = Path(body["capture_folder"]).name

        resp = client.post(
            "/correction",
            json={
                "capture_id": capture_id,
                "action": "add",
                "persona_id": _persona_id(settings),
                "field": "mother_name",
                "new_value": "Bridget Kelly",
                "evidence_type": "stated",
                "snippet_text": "Daughter of Bridget Kelly",
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["action"] == "add" and out["supersedes_id"] is None
        expected = _fixture_expected(settings, capture_id)
        assert ("mother_name", "Bridget Kelly") in {
            (a["field"], a["value"]) for a in expected["assertions"]
        }
    finally:
        backend.app.dependency_overrides.clear()


def test_correction_requires_a_token(tmp_path):
    client, backend, _ = _client(tmp_path)
    try:
        body = _capture(client)
        capture_id = Path(body["capture_folder"]).name
        resp = client.post(
            "/correction",
            json={"capture_id": capture_id, "action": "retract", "assertion_id": "x"},
        )  # no token header
        assert resp.status_code == 401
    finally:
        backend.app.dependency_overrides.clear()


def test_correction_on_unknown_capture_is_404(tmp_path):
    client, backend, _ = _client(tmp_path)
    try:
        resp = client.post(
            "/correction",
            json={
                "capture_id": "20260708-findagravecom-deadbeef",
                "action": "retract",
                "assertion_id": "x",
            },
            headers=_AUTH,
        )
        assert resp.status_code == 404, resp.text
    finally:
        backend.app.dependency_overrides.clear()


def test_correction_unknown_target_is_422(tmp_path):
    client, backend, _ = _client(tmp_path)
    try:
        body = _capture(client)
        capture_id = Path(body["capture_folder"]).name
        resp = client.post(
            "/correction",
            json={"capture_id": capture_id, "action": "edit",
                  "assertion_id": "not-a-real-assertion", "new_value": "x"},
            headers=_AUTH,
        )
        assert resp.status_code == 422, resp.text
    finally:
        backend.app.dependency_overrides.clear()


def test_malformed_correction_request_is_422(tmp_path):
    client, backend, _ = _client(tmp_path)
    try:
        body = _capture(client)
        capture_id = Path(body["capture_folder"]).name
        # edit with no new_value violates CorrectionRequest's per-action invariant.
        resp = client.post(
            "/correction",
            json={"capture_id": capture_id, "action": "edit", "assertion_id": "a"},
            headers=_AUTH,
        )
        assert resp.status_code == 422, resp.text
    finally:
        backend.app.dependency_overrides.clear()
