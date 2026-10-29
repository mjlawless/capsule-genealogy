"""REQ-013 §C — permissions asked for when they are used, not at install.

The 0.2.6 install dialog listed "Read and change all your data on all websites" (from the
<all_urls> content script and `debugger`) and "Manage your downloads". The page scripts now run
only on granted sites, registered by background.js, and `downloads` is optional, turned on with
the page button from the popup. `debugger` stays until the REQ-013 §D measurement decides.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_EXT = Path(__file__).resolve().parent.parent / "extension"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _manifest() -> dict:
    return json.loads((_EXT / "manifest.json").read_text(encoding="utf-8"))


def test_the_install_dialog_asks_for_no_sites_and_no_downloads():
    m = _manifest()
    assert "content_scripts" not in m, "a declared content script asks for its sites at install"
    assert "downloads" not in m["permissions"] and m["optional_permissions"] == ["downloads"]
    assert m["host_permissions"] == ["http://127.0.0.1:8765/*"]
    # file:///*: the finished site download is read back through its file:// URL (Codex review).
    assert set(m["optional_host_permissions"]) == {"https://*/*", "http://*/*", "file:///*"}
    # Kept until REQ-013 §D's measurement: debugger cannot be optional.
    assert "debugger" in m["permissions"]


def _matches(origins):
    source = (_EXT / "background.js").read_text(encoding="utf-8")
    start = source.index("function pageScriptMatches(")
    fn = source[start : source.index("\n}\n", start) + 3]
    script = fn + f"\nconsole.log(JSON.stringify(pageScriptMatches({json.dumps(origins)})));"
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_node
def test_the_page_scripts_follow_the_granted_sites_but_never_the_app():
    assert _matches(["http://127.0.0.1:8765/*"]) == []
    assert _matches([]) == [] and _matches(None) == []
    assert _matches(
        ["https://www.example.org/*", "http://127.0.0.1:8765/*", "https://*.example.net/*", "https://www.example.org/*"]
    ) == ["https://*.example.net/*", "https://www.example.org/*"]
    assert _matches(["https://*/*", "http://*/*", "http://localhost:8000/*"]) == ["http://*/*", "https://*/*"]
    # A file grant (for reading downloads back) never puts the page scripts on local files.
    assert _matches(["file:///*", "https://www.example.org/*"]) == ["https://www.example.org/*"]


def test_the_page_scripts_are_the_ones_the_manifest_used_to_declare():
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    assert 'const PAGE_SCRIPT_FILES = ["media-detect.js", "content.js"];' in background
    # Kept in step at every start and on every grant or revoke, at top level so a woken worker
    # registers its listeners before the event is delivered.
    top = background[: background.index("\nasync function ")]
    for call in ("\nsyncPageScripts();", "chrome.permissions.onAdded.addListener(", "chrome.permissions.onRemoved.addListener("):
        assert call in top
    added = background[background.index("chrome.permissions.onAdded.addListener(") :]
    assert "watchDownloads()" in added[: added.index("\n});")]


@needs_node
def test_without_the_downloads_permission_the_listener_waits_and_an_arm_says_how():
    intercept = (_EXT / "download-intercept.js").read_text(encoding="utf-8")
    start = intercept.index("let downloadsWatched")
    body = intercept[start : intercept.index("chrome.downloads.onChanged.addListener(", start)]
    # Run only the guard: with no chrome.downloads it returns false and attaches nothing.
    guard = "globalThis.chrome = {};\n" + body + "  return true; }\nconsole.log(JSON.stringify(watchDownloads()));"
    proc = subprocess.run(["node", "-e", guard], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) is False
    arm = intercept[intercept.index("async function armDownload(") :]
    assert arm.index("if (!chrome.downloads)") < arm.index("fileAccessAllowed()")
    assert "Capsule button on every page, and keep site downloads" in intercept
    popup_html = (_EXT / "popup.html").read_text(encoding="utf-8")
    assert "Capsule button on every page, and keep site downloads" in popup_html


def _setting_request(file_access):
    popup = (_EXT / "popup.js").read_text(encoding="utf-8")
    consts = popup[popup.index("const PAGE_EVERYWHERE =") : popup.index("const DOWNLOADS_FILE_HINT")]
    start = popup.index("function pageSettingRequest(")
    fn = popup[start : popup.index("\n}\n", start) + 3]
    script = consts + fn + f"\nconsole.log(JSON.stringify(pageSettingRequest({json.dumps(file_access)})));"
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_node
def test_the_file_grant_is_asked_for_only_when_chrome_allows_file_urls():
    """Codex review: Chrome refuses a whole request that names file:///* while "Allow access to
    file URLs" is off, so asking for it on a fresh install blocked the setting entirely."""
    off = _setting_request(False)
    assert off == {"permissions": ["downloads"], "origins": ["https://*/*", "http://*/*"]}
    on = _setting_request(True)
    assert on["permissions"] == ["downloads"] and "file:///*" in on["origins"]


def test_the_popup_asks_from_the_click_and_explains_file_access():
    popup = (_EXT / "popup.js").read_text(encoding="utf-8")
    handler = popup[popup.index('box.addEventListener("change", () => {') :]
    handler = handler[: handler.index("\n  });")]
    # The request is the first call in the handler: an await before it spends the gesture.
    before = handler.split("chrome.permissions.request(wanted)")[0]
    code = "\n".join(line for line in before.splitlines() if not line.lstrip().startswith("//"))
    assert "await" not in code
    init = popup[popup.index("async function initPageSetting(") :]
    assert init.index("isAllowedFileSchemeAccess()") < init.index('box.addEventListener("change"')
    assert "DOWNLOADS_FILE_HINT" in handler and "Allow access to file URLs" in popup
    # The installer download no longer assumes the downloads API exists.
    assert "if (chrome.downloads) chrome.downloads.download(" in popup


def test_a_withdrawn_site_cannot_still_capture_from_an_open_tab():
    """Codex review: Chrome stops injecting the page scripts into NEW pages when a site is
    withdrawn, but a tab already open keeps its button and event listeners. Every request from a
    page script is checked against the grant at that moment."""
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    listener = background[background.index("chrome.runtime.onMessage.addListener(") :]
    listener = listener[: listener.index("\n});")]
    assert "sender.tab && PAGE_MESSAGES.has(msg.type)" in listener
    assert listener.index("pageStillAllowed(sender)") < listener.index("handlePageMessage(msg, sender, sendResponse)")
    for kind in ("capsule:arm-download", "capsule:capture", "capsule:capture-ancestry-image"):
        assert f'"{kind}"' in background[background.index("const PAGE_MESSAGES") : background.index("const SITE_WITHDRAWN")]
    allowed = background[background.index("async function pageStillAllowed(") :]
    assert "chrome.permissions.contains({ origins: [origin + \"/*\"] })" in allowed[: allowed.index("\n}\n")]
