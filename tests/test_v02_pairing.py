"""v0.2 — the extension pairs with the local backend by itself, no token pasting (item 3).

`POST /pair` hands the token only to a request whose browser-set `Origin` is Capsule's own
extension ID. A web page (or another extension) cannot forge that header, so a page still
cannot drive captures. The unpacked extension's ID is fixed by the public `key` in its
manifest; a test pins that key to the ID the backend trusts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import shutil
from pathlib import Path

import pytest

import tests.test_backend as tb

_EXT = Path(__file__).resolve().parent.parent / "extension"


@pytest.fixture()
def client(tmp_path):
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


def _extension_id_from_manifest() -> str:
    key = json.loads((_EXT / "manifest.json").read_text(encoding="utf-8"))["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join("abcdefghijklmnop"[int(c, 16)] for c in digest)


def test_the_manifest_key_gives_the_id_the_backend_trusts():
    from capsule.config import CAPSULE_EXTENSION_ID

    assert _extension_id_from_manifest() == CAPSULE_EXTENSION_ID


def test_capsules_own_extension_is_handed_the_token(client):
    from capsule.config import CAPSULE_EXTENSION_ID

    resp = client.post("/pair", headers={"Origin": f"chrome-extension://{CAPSULE_EXTENSION_ID}"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"token": tb._TOKEN}


@pytest.mark.parametrize(
    "origin",
    [
        "chrome-extension://abcdefghijklmnopabcdefghijklmnop",  # another extension
        "https://evil.example",  # a web page
        "http://127.0.0.1:8765",  # the backend's own pages
        "null",
        "",
    ],
)
def test_anyone_else_is_refused(client, origin):
    headers = {"Origin": origin} if origin else {}
    resp = client.post("/pair", headers=headers)
    assert resp.status_code == 403
    assert tb._TOKEN not in resp.text


def test_the_chrome_web_store_copy_pairs_by_itself(client, monkeypatch):
    """v0.2.1: the store assigned its own ID; a store install must pair with no setting."""
    from capsule.config import CAPSULE_STORE_EXTENSION_ID

    monkeypatch.delenv("CAPSULE_EXTENSION_IDS", raising=False)
    assert re.fullmatch(r"[a-p]{32}", CAPSULE_STORE_EXTENSION_ID)
    resp = client.post("/pair", headers={"Origin": f"chrome-extension://{CAPSULE_STORE_EXTENSION_ID}"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"token": tb._TOKEN}


def test_the_popup_knows_the_same_official_ids_as_the_app():
    from capsule.config import CAPSULE_EXTENSION_ID, CAPSULE_STORE_EXTENSION_ID

    source = (_EXT / "popup.js").read_text(encoding="utf-8")
    listed = re.search(r"const OFFICIAL_EXTENSION_IDS = \[([^\]]*)\]", source).group(1)
    assert sorted(re.findall(r'"([a-p]{32})"', listed)) == sorted(
        [CAPSULE_EXTENSION_ID, CAPSULE_STORE_EXTENSION_ID]
    )


def _refused_advice(extension_id: str) -> str:
    source = (_EXT / "popup.js").read_text(encoding="utf-8")
    ids = source[source.index("const OFFICIAL_EXTENSION_IDS") :]
    ids = ids[: ids.index(";") + 1]
    start = source.index("function pairRefusedAdvice(")
    fn = source[start : source.index("\n}\n", start) + 3]
    script = ids + "\n" + fn + f"\nconsole.log(JSON.stringify(pairRefusedAdvice({json.dumps(extension_id)})));"
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_an_unofficial_copy_is_told_so_not_to_update_the_app():
    """The first real Windows install: a copy loaded from a folder without the manifest `key`
    got a path-derived ID, and the popup wrongly said to update the app."""
    advice = _refused_advice("bgmnjfaogjkeicjkffcbdljhlncloijn")
    assert "isn't an official one" in advice and "Load unpacked" in advice
    assert "installer again" not in advice
    official = _refused_advice("jalijanpiopkphjagoiofcofclmmmaan")
    assert "installer again" in official


def test_a_refused_pairing_is_logged_with_the_origin(client, caplog):
    with caplog.at_level("WARNING"):
        resp = client.post("/pair", headers={"Origin": "chrome-extension://bgmnjfaogjkeicjkffcbdljhlncloijn"})
    assert resp.status_code == 403
    assert "bgmnjfaogjkeicjkffcbdljhlncloijn" in caplog.text and "refused to pair" in caplog.text


def test_a_store_build_id_can_be_added(client, monkeypatch):
    store_id = "ponmlkjihgfedcbaponmlkjihgfedcba"
    monkeypatch.setenv("CAPSULE_EXTENSION_IDS", store_id)
    resp = client.post("/pair", headers={"Origin": f"chrome-extension://{store_id}"})
    assert resp.status_code == 200


def test_the_extension_pairs_by_itself_when_no_token_is_saved():
    for name in ("background.js", "popup.js"):
        source = (_EXT / name).read_text(encoding="utf-8")
        assert 'BACKEND.replace(/\\/capture$/, "/pair"), { method: "POST" }' in source, name
