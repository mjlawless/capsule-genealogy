"""REQ-013 §A/§F — the welcome tab opened when the extension is installed.

Most people add the extension from the Chrome Web Store and, before 0.2.7, were never told the
Capsule app is the other half. These run the shipped `welcomeView` (welcome.js, on top of
setup-shared.js) in node, and check the install hook, the page's wiring and the store ID.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from capsule.config import CAPSULE_STORE_EXTENSION_ID

_EXT = Path(__file__).resolve().parent.parent / "extension"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _view(os_name, health, version="0.2.7", from_store=True):
    shared = (_EXT / "setup-shared.js").read_text(encoding="utf-8")
    welcome = (_EXT / "welcome.js").read_text(encoding="utf-8")
    fn = welcome[welcome.index("function welcomeView(") :]
    fn = fn[: fn.index("\n}\n") + 3]
    script = shared + "\n" + fn + (
        f"\nconsole.log(JSON.stringify(welcomeView({json.dumps(os_name)}, {json.dumps(health)}, "
        f"{json.dumps(version)}, {json.dumps(from_store)})));"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_node
@pytest.mark.parametrize(("os_name", "asset"), [("win", "Install-Capsule.cmd"), ("mac", "Install-Capsule-mac.zip")])
def test_no_app_offers_this_computers_installer(os_name, asset):
    v = _view(os_name, None)
    assert v["appRunning"] is False
    assert v["setup"]["download"].endswith("/releases/latest/download/" + asset)
    # Always the first-install wording, never "isn't running": this is a brand-new install.
    assert v["setup"]["title"].startswith("One more step")


@needs_node
def test_linux_gets_the_command():
    v = _view("linux", None)
    assert v["setup"]["download"] == "" and "install.sh" in v["setup"]["command"]


@needs_node
def test_a_running_app_of_the_same_version_is_simply_done():
    v = _view("mac", {"status": "ok", "version": "0.2.7+gabc1234"})
    assert v == {"appRunning": True, "banner": None, "setup": None}


@needs_node
def test_an_older_app_says_to_rerun_the_installer():
    v = _view("win", {"status": "ok", "version": "0.2.6"})
    assert v["appRunning"] is True and "installer again" in v["banner"]


def test_the_welcome_tab_opens_on_install_only():
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    hook = background[background.index("chrome.runtime.onInstalled.addListener(") :]
    hook = hook[: hook.index("\n});\n")]
    assert 'details.reason === "install"' in hook
    assert 'chrome.runtime.getURL("welcome.html")' in hook
    # Registered at top level (not inside a function), or the install event is missed.
    assert background.index("chrome.runtime.onInstalled.addListener(") < background.index("\nfunction ")


def test_the_page_loads_the_shared_code_first_and_no_remote_script():
    html = (_EXT / "welcome.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="([^"]+)"></script>', html)
    assert scripts == ["setup-shared.js", "welcome.js"]
    assert "<script>" not in html  # MV3 extension pages refuse inline script
    for name in ("lead-next", "app-title", "app-install", "app-done", "setup-download", "setup-command", "version-banner"):
        assert f'id="{name}"' in html


def test_the_page_explains_every_install_warning():
    html = (_EXT / "welcome.html").read_text(encoding="utf-8")
    assert "two parts" in html
    for line in ("all your data on all websites", "page debugger backend", "Enhanced Safe Browsing"):
        assert line in html


def test_the_store_id_matches_the_app():
    shared = (_EXT / "setup-shared.js").read_text(encoding="utf-8")
    assert f'const STORE_EXTENSION_ID = "{CAPSULE_STORE_EXTENSION_ID}";' in shared


def test_every_install_path_ends_at_the_store():
    """REQ-013 §B: the installers name the same store URL as the app and the extension, before
    the Load unpacked fallback, and open it only on a first install outside CI."""
    from capsule.config import STORE_URL

    root = _EXT.parent
    sh = (root / "install.sh").read_text(encoding="utf-8")
    ps1 = (root / "install.ps1").read_text(encoding="utf-8")
    shared = (_EXT / "setup-shared.js").read_text(encoding="utf-8")
    assert f'STORE_URL="{STORE_URL}"' in sh
    assert f"$StoreUrl = '{STORE_URL}'" in ps1
    assert STORE_URL.endswith("/" + CAPSULE_STORE_EXTENSION_ID) and "genealogy-capsule/" in shared
    for text in (sh, ps1):
        tail = text[text.index("Last step: add Genealogy Capsule") :]
        assert "Load unpacked" in tail  # still offered, after the store
        assert "Developer mode (top right)" not in tail
    assert '[ -z "$PREVIOUS" ] && [ -z "${CI:-}" ]' in sh
    assert "-not $hadCapsule -and -not $env:CI" in ps1
