"""BUG-021 — every capture recorded `tool_version: "0.0.0"`, so no capture said which code made it.

Fixed by:
1. a real product version in `pyproject.toml`, shared with `extension/manifest.json`;
2. `tool_version` = that version plus, from a git checkout, the commit (`0.1.0+g1a2b3c4`);
3. the extension naming its own build on every capture (`X-Capsule-Extension-Version`),
   recorded in the manifest as the additive `extension_version`;
4. `/health` reporting the backend's version.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import capsule.version as version
import tests.test_backend as tb
import tests.test_backend_document as td

_ROOT = Path(__file__).resolve().parent.parent
_EXT = _ROOT / "extension"


@pytest.fixture(autouse=True)
def _fresh_version_cache():
    version.build_version.cache_clear()
    yield
    version.build_version.cache_clear()


def _pyproject_version() -> str:
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)


def test_BUG_021_the_product_version_is_real_and_shared_with_the_extension():
    """One product version for backend and extension, and never the 0.0.0 placeholder."""
    product = _pyproject_version()
    assert product != "0.0.0"
    assert re.fullmatch(r"\d+\.\d+\.\d+", product), product
    ext = json.loads((_EXT / "manifest.json").read_text(encoding="utf-8"))["version"]
    assert ext == product, "extension/manifest.json and pyproject.toml must carry one version"


def test_BUG_021_a_checkout_records_the_commit():
    if not (version._REPO_ROOT / ".git").exists():
        pytest.skip("not running from a git checkout")
    assert re.fullmatch(r"\d+\.\d+\.\d+\+g[0-9a-f]{7,}(\.dirty)?", version.build_version())


def test_BUG_021_a_zip_download_with_no_git_falls_back_to_the_product_version(
    tmp_path, monkeypatch
):
    """GitHub's "Download ZIP" has no .git: the plain version, never an error."""
    monkeypatch.setattr(version, "_REPO_ROOT", tmp_path)
    assert version.build_version() == version.product_version()


def test_BUG_021_no_git_binary_falls_back_to_the_product_version(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(version, "_REPO_ROOT", tmp_path)

    def no_git(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    assert version.build_version() == version.product_version()


def test_BUG_021_dirty_is_marked(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(version, "_REPO_ROOT", tmp_path)
    answers = {"rev-parse": "abc1234", "status": " M capsule/version.py"}

    def fake_git(*args):
        return answers[args[0]]

    monkeypatch.setattr(version, "_git", fake_git)
    assert version.build_version() == f"{version.product_version()}+gabc1234.dirty"


def test_BUG_021_the_manifest_records_tool_version_and_the_extension_version(tmp_path):
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        resp = client.post(
            "/capture",
            json={"url": tb._NYC_URL, "html": tb._PAGE, "gating": "gated"},
            headers={**tb._AUTH, "X-Capsule-Extension-Version": "0.1.0"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        backend.app.dependency_overrides.clear()
    (manifest_path,) = (tmp_path / "captures").glob("*/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["tool_version"] == version.build_version()
    assert not manifest["tool_version"].startswith("0.0.0")
    assert manifest["extension_version"] == "0.1.0"


def test_BUG_021_no_header_means_no_extension_version_key(tmp_path):
    """Additive: a capture not sent by the extension (or by an older one) has no key at all."""
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        resp = client.post(
            "/capture", json={"url": tb._NYC_URL, "html": tb._PAGE}, headers=tb._AUTH
        )
        assert resp.status_code == 200, resp.text
    finally:
        backend.app.dependency_overrides.clear()
    (manifest_path,) = (tmp_path / "captures").glob("*/manifest.json")
    assert "extension_version" not in json.loads(manifest_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "header", ["evil<script>", "1.2.3.4.5", "", "1..2", "v0.1.0", "1" * 6, "0.1.0\n"]
)
def test_BUG_021_a_header_that_is_not_a_version_is_not_recorded(tmp_path, header):
    """The header is page-reachable text headed for a manifest: only a version gets in."""
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        resp = client.post(
            "/capture",
            json={"url": tb._NYC_URL, "html": tb._PAGE},
            headers={**tb._AUTH, "X-Capsule-Extension-Version": header},
        )
        assert resp.status_code == 200, resp.text
    finally:
        backend.app.dependency_overrides.clear()
    (manifest_path,) = (tmp_path / "captures").glob("*/manifest.json")
    assert "extension_version" not in json.loads(manifest_path.read_text(encoding="utf-8"))


def test_BUG_021_a_document_capture_records_the_extension_version(tmp_path):
    client = td._client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            headers={"X-Capsule-Token": td._TOKEN, "X-Capsule-Extension-Version": "0.1.0"},
            json={"url": td._URL, "document": {"data_base64": td._b64(td._PDF)}},
        )
        assert resp.status_code == 200, resp.text
    finally:
        td.app.dependency_overrides.clear()
    (manifest_path,) = (tmp_path / "captures").glob("*/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["extension_version"] == "0.1.0"
    assert manifest["tool_version"] == version.build_version()


def test_BUG_021_health_reports_the_backend_version():
    from fastapi.testclient import TestClient

    from capsule.backend.app import app

    assert TestClient(app).get("/health").json() == {
        "status": "ok",
        "version": version.build_version(),
    }


def test_BUG_021_every_extension_capture_post_names_its_build():
    """All three POSTs to /capture send the header: the background flow and both popup paths."""
    header = '"X-Capsule-Extension-Version": chrome.runtime.getManifest().version'
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    popup = (_EXT / "popup.js").read_text(encoding="utf-8")
    assert background.count("fetch(BACKEND") == background.count(header) == 1
    assert popup.count("fetch(BACKEND") == popup.count(header) == 2


def test_BUG_021_the_build_identity_is_frozen_once_computed(tmp_path, monkeypatch):
    """A `git pull` after the server started must not relabel the old code as the new commit."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(version, "_REPO_ROOT", tmp_path)
    head = {"sha": "aaaaaaa"}
    monkeypatch.setattr(
        version, "_git", lambda *a: head["sha"] if a[0] == "rev-parse" else ""
    )
    started = version.build_version()
    head["sha"] = "bbbbbbb"  # the checkout moves on while the server keeps running
    assert version.build_version() == started
    assert started.endswith("+gaaaaaaa")


def test_BUG_021_the_backend_freezes_its_build_identity_at_import():
    """Not left to the first capture: the module primes the cache when the server loads it."""
    import ast

    source = (_ROOT / "capsule" / "backend" / "app.py").read_text(encoding="utf-8")
    module_level_calls = [
        node.value.func.id
        for node in ast.parse(source).body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]
    assert "build_version" in module_level_calls
