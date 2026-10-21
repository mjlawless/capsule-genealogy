"""v0.2 — the user enters their OWN Anthropic key in the extension, no terminal (item 2).

The popup sends the key to the paired local backend, which checks it with Anthropic, keeps it
in the OS keyring and uses it from the next capture on, with no restart. The extension never
stores it. Captures archived before the key existed can then be read in the background.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

import tests.test_backend as tb

_EXT = Path(__file__).resolve().parent.parent / "extension"


@pytest.fixture()
def keyring_store(monkeypatch):
    import keyring

    import capsule.credentials as credentials

    store: dict = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(keyring, "set_password", lambda s, u, v: store.__setitem__((s, u), v))
    monkeypatch.setattr(
        credentials,
        "validate_api_key",
        lambda key: None if key.startswith("sk-ant-good") else "Anthropic rejected this key.",
    )
    return store


@pytest.fixture()
def client(tmp_path, keyring_store):
    from fastapi.testclient import TestClient

    from capsule.backend.app import Settings, app, get_settings

    settings = Settings(
        db_path=tmp_path / "capsule.db", captures_root=tmp_path / "captures", token=tb._TOKEN
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_a_good_key_is_stored_and_used_without_a_restart(client, keyring_store):
    before = client.get("/settings/status", headers=tb._AUTH).json()
    assert before["api_key"] == "missing"
    resp = client.post(
        "/settings/api-key", json={"api_key": "sk-ant-good-123"}, headers=tb._AUTH
    )
    assert resp.status_code == 200, resp.text
    assert "sk-ant-good-123" not in resp.text  # never echoed
    assert keyring_store[("capsule", "anthropic_api_key")] == "sk-ant-good-123"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-good-123"
    after = client.get("/settings/status", headers=tb._AUTH).json()
    assert after["api_key"] == "set" and after["api_key_source"] == "keyring"


def test_a_rejected_key_is_not_stored(client, keyring_store):
    resp = client.post("/settings/api-key", json={"api_key": "sk-ant-bad"}, headers=tb._AUTH)
    assert resp.status_code == 400
    assert "rejected" in resp.json()["detail"]
    assert keyring_store == {} and "ANTHROPIC_API_KEY" not in os.environ


def test_only_the_paired_extension_can_set_a_key(client, keyring_store):
    resp = client.post("/settings/api-key", json={"api_key": "sk-ant-good-1"})
    assert resp.status_code == 401
    assert keyring_store == {}
    assert client.get("/settings/status").status_code == 401


def test_pending_captures_are_read_in_the_background_once_a_key_exists(client):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import app, provide_agent

    archived = client.post(
        "/capture", json={"url": tb._NYC_URL, "html": tb._PAGE}, headers=tb._AUTH
    )
    assert archived.json()["extraction_pending"] is True
    assert client.get("/settings/status", headers=tb._AUTH).json()["pending"] == 1
    refused = client.post("/settings/read-pending", headers=tb._AUTH)
    assert refused.status_code == 400  # no key yet

    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor", model="m", transport=FakeTransport(json.dumps(tb._RAW))
    )
    started = client.post("/settings/read-pending", headers=tb._AUTH)
    assert started.status_code == 200 and started.json()["total"] == 1
    for _ in range(100):
        status = client.get("/settings/status", headers=tb._AUTH).json()
        if not status["reading"]["running"]:
            break
        time.sleep(0.05)
    assert status["reading"]["done"] == 1 and status["reading"]["failed"] == 0
    assert status["pending"] == 0


def test_the_popup_sends_the_key_to_the_backend_and_never_keeps_it():
    popup = (_EXT / "popup.js").read_text(encoding="utf-8")
    html = (_EXT / "popup.html").read_text(encoding="utf-8")
    assert '"/settings/api-key"' in popup and '"/settings/status"' in popup
    assert 'id="api-key"' in html
    assert 'input.value = ""; // never keep the key in the popup' in popup
    assert not re.search(r"chrome\.storage[^;]*api", popup, re.IGNORECASE)


def test_found_a_database_that_will_not_open_does_not_wedge_background_reading(tmp_path, monkeypatch):
    """Codex review: the worker opened the database outside its guard, so a failure left
    `running` set forever and every later attempt was refused."""
    import capsule.pipeline.reextract as rx
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport

    monkeypatch.setattr(rx, "pending_captures", lambda root: ["20260101-example-aaaa1111"])

    class Broken:
        def __init__(self, *_a, **_k):
            raise OSError("disk unavailable")

    monkeypatch.setattr(rx, "Repository", Broken)
    agent = Agent(name="x", model="m", transport=FakeTransport("{}"))
    rx.start_background_reading(db_path=tmp_path / "c.db", captures_root=tmp_path, agent=agent)
    for _ in range(100):
        state = rx.background_status()
        if not state.running:
            break
        time.sleep(0.02)
    assert not state.running
    assert "could not open the database" in (state.last_error or "")


def test_found_the_popup_reports_a_failed_reading_as_a_failure():
    """Codex review: a run whose captures all failed must not read "Finished reading"."""
    import subprocess

    popup = (_EXT / "popup.js").read_text(encoding="utf-8")
    start = popup.index("function readingSummary(")
    body = popup[start : popup.index("\n}\n", start) + 3]
    script = body + """
      console.log(JSON.stringify([
        readingSummary({done: 2, total: 2, failed: 0, last_error: null}),
        readingSummary({done: 0, total: 2, failed: 2, last_error: "x: Anthropic rejected"}),
      ]));
    """
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    ok, bad = json.loads(proc.stdout)
    assert ok.startswith("Finished reading 2")
    assert "could not be read" in bad and "Anthropic rejected" in bad
    assert "Finished" not in bad
