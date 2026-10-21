"""The Ancestry work queue is off unless deliberately enabled (owner ruling, 2026-10-21).

`/ancestry/seed`, `/ancestry/next` and `capsule ancestry seed` pace an agent through a list of
records in the researcher's logged-in session. A public install does not offer that until
`CAPSULE_ENABLE_WORK_QUEUE=1` is set. Capturing the page in view never depends on it.
"""
from __future__ import annotations

import json

import tests.test_backend_ancestry as ta


def test_the_queue_endpoints_refuse_when_the_queue_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPSULE_ENABLE_WORK_QUEUE")
    client, backend = ta._client(tmp_path)
    try:
        seeded = client.post("/ancestry/seed", json={"apids": [ta._APID]}, headers=ta._AUTH)
        nxt = client.get("/ancestry/next", headers=ta._AUTH)
        # Capturing the page in view is unaffected.
        captured = client.post(
            "/capture",
            json={"url": ta._ANCESTRY_URL, "html": ta._PAGE, "expected_apid": ta._APID},
            headers=ta._AUTH,
        )
    finally:
        backend.app.dependency_overrides.clear()
    assert seeded.status_code == 403 and "CAPSULE_ENABLE_WORK_QUEUE" in seeded.json()["detail"]
    assert nxt.status_code == 403
    assert captured.status_code == 200, captured.text


def test_the_queue_works_when_enabled(tmp_path):
    client, backend = ta._client(tmp_path)
    try:
        seeded = client.post("/ancestry/seed", json={"apids": [ta._APID]}, headers=ta._AUTH)
    finally:
        backend.app.dependency_overrides.clear()
    assert seeded.status_code == 200, seeded.text
    assert json.loads(seeded.text)["seeded"] == 1


def test_the_cli_seed_refuses_when_the_queue_is_off(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from capsule.cli.app import app

    monkeypatch.delenv("CAPSULE_ENABLE_WORK_QUEUE")
    apids = tmp_path / "apids.txt"
    apids.write_text(ta._APID + "\n", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["ancestry", "seed", "--file", str(apids), "--db", str(tmp_path / "c.db")]
    )
    assert result.exit_code == 1
    assert "CAPSULE_ENABLE_WORK_QUEUE" in result.output
    status = CliRunner().invoke(app, ["ancestry", "status", "--db", str(tmp_path / "c.db")])
    assert status.exit_code == 0, status.output  # reporting stays available
