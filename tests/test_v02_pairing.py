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


def test_a_store_build_id_can_be_added(client, monkeypatch):
    store_id = "ponmlkjihgfedcbaponmlkjihgfedcba"
    monkeypatch.setenv("CAPSULE_EXTENSION_IDS", store_id)
    resp = client.post("/pair", headers={"Origin": f"chrome-extension://{store_id}"})
    assert resp.status_code == 200


def test_the_extension_pairs_by_itself_when_no_token_is_saved():
    for name in ("background.js", "popup.js"):
        source = (_EXT / name).read_text(encoding="utf-8")
        assert 'BACKEND.replace(/\\/capture$/, "/pair"), { method: "POST" }' in source, name
