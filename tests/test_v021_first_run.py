"""v0.2.1 — the popup's first-run screen, and the double-click installers it offers.

Most people add the extension (from the Chrome Web Store) before the Capsule app exists, and a
bare "backend not running" lost them. When the app doesn't answer, the popup now says what is
missing, offers the installer for this system as a download (no terminal), and switches to the
normal popup by itself once the app is running. These run the shipped `setupScreen` from
`popup.js` in node, and check the installers, their release names and the README agree.
"""
from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_POPUP = _ROOT / "extension" / "popup.js"
_HTML = _ROOT / "extension" / "popup.html"
_BACKGROUND = _ROOT / "extension" / "background.js"
_CMD = _ROOT / "installers" / "Install-Capsule.cmd"
_COMMAND = _ROOT / "installers" / "Install Capsule.command"
_PACKAGER = _ROOT / "installers" / "package-installers.sh"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _setup_source() -> str:
    source = _POPUP.read_text(encoding="utf-8")
    start = source.index("const REPO_URL")
    fn = source.index("function setupScreen(", start)
    return source[start : source.index("\n}\n", fn) + 3]


def _screen(os_name, seen_app):
    script = _setup_source() + (
        f"\nconsole.log(JSON.stringify(setupScreen({json.dumps(os_name)}, {json.dumps(seen_app)})));"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _readme_commands() -> tuple[str, str]:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    unix = re.search(r"^\s*(curl -LsSf \S+install\.sh \| sh)\s*$", readme, re.M).group(1)
    win = re.search(r'^\s*(powershell -ExecutionPolicy ByPass -c ".+install\.ps1 \| iex")\s*$', readme, re.M).group(1)
    return unix, win


@needs_node
@pytest.mark.parametrize(("os_name", "asset"), [("win", "Install-Capsule.cmd"), ("mac", "Install-Capsule-mac.zip")])
def test_windows_and_mac_get_a_download_not_a_terminal(os_name, asset):
    s = _screen(os_name, False)
    assert s["title"] == "One more step: install the Genealogy Capsule app"
    assert s["download"] == f"https://github.com/mjlawless/capsule-genealogy/releases/latest/download/{asset}"
    # The unsigned-installer warning is explained where the user will meet it.
    assert ("Run anyway" if os_name == "win" else "Open Anyway") in s["steps"]
    assert s["command"]  # still offered, folded away, for people who prefer it


@needs_node
def test_linux_gets_the_command():
    s = _screen("linux", False)
    assert s["download"] == "" and s["steps"] == ""
    assert s["command"] == _readme_commands()[0]


@needs_node
def test_the_commands_are_the_readmes():
    unix, win = _readme_commands()
    assert _screen("mac", False)["command"] == unix
    assert _screen("win", False)["command"] == win


@needs_node
def test_an_app_seen_before_is_stopped_not_missing():
    s = _screen("mac", True)
    assert s["title"] == "Genealogy Capsule isn't running"
    assert "keeps your archive" in s["body"] and s["download"]


@needs_node
@pytest.mark.parametrize("os_name", ["cros", "android", "", None])
def test_a_device_that_cannot_run_the_app_is_told_so(os_name):
    s = _screen(os_name, False)
    assert s["command"] == "" and s["download"] == ""
    assert "can't be installed on this device" in s["body"]


def test_the_double_click_installers_run_the_readme_install():
    unix, win = _readme_commands()
    command = _COMMAND.read_text(encoding="utf-8")
    assert unix.split()[2] in command  # the README's install.sh URL
    assert command.startswith("#!/bin/sh\n")
    assert _COMMAND.stat().st_mode & stat.S_IXUSR, "the .command must be executable in the repo"
    cmd = _CMD.read_text(encoding="utf-8")
    assert "irm https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.ps1 | iex" in cmd
    assert win.split('"')[1] in cmd  # the same irm | iex the README runs
    assert cmd.rstrip().endswith("pause"), "the window must stay open so the user can read it"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize("curl_body", ["exit 22", "exit 0"])  # an HTTP error; an empty download
def test_a_failed_download_is_not_reported_as_installed(tmp_path, curl_body):
    """Codex review: `curl … | sh` with a failed download ran nothing and still said "Done"."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "curl").write_text(f"#!/bin/sh\n{curl_body}\n", encoding="utf-8")
    (fake / "curl").chmod(0o755)
    proc = subprocess.run(
        ["sh", str(_COMMAND)],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": f"{fake}:/usr/bin:/bin"},
    )
    assert proc.returncode != 0
    assert "did not finish" in proc.stdout and "Done." not in proc.stdout


def test_release_names_match_the_popup():
    popup = _POPUP.read_text(encoding="utf-8")
    packager = _PACKAGER.read_text(encoding="utf-8")
    for name in ("Install-Capsule.cmd", "Install-Capsule-mac.zip"):
        assert f"/releases/latest/download/{name}" in popup
        assert f'"$out/{name}"' in packager


def test_cmd_files_are_checked_out_with_crlf():
    attrs = (_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.cmd text eol=crlf" in attrs


@pytest.mark.skipif(shutil.which("zip") is None, reason="zip is not installed")
def test_the_packager_builds_working_assets(tmp_path):
    subprocess.run(["bash", str(_PACKAGER), str(tmp_path)], check=True, capture_output=True, timeout=60)
    cmd_bytes = (tmp_path / "Install-Capsule.cmd").read_bytes()
    assert b"\r\n" in cmd_bytes and b"\n" not in cmd_bytes.replace(b"\r\n", b"")
    with zipfile.ZipFile(tmp_path / "Install-Capsule-mac.zip") as zf:
        info = zf.getinfo("Install Capsule.command")
        assert (info.external_attr >> 16) & 0o111, "Finder must unzip it as runnable"


def test_the_popup_checks_for_the_app_before_anything_else():
    source = _POPUP.read_text(encoding="utf-8")
    init = source[source.index("async function init()") : source.index("async function startMain(")]
    assert init.index("fetchHealth()") < init.index("showSetup()") < init.index("startMain(health)")
    html = _HTML.read_text(encoding="utf-8")
    assert re.search(r'<section id="setup" hidden>', html)
    main = html[html.index('<div id="main">') :]
    assert 'id="capture"' in main and 'id="index-link"' in main


def test_capture_errors_point_to_the_popup_not_a_terminal():
    background = _BACKGROUND.read_text(encoding="utf-8")
    assert "capsule serve" not in background.split("const NOT_CONNECTED", 1)[1].split(";", 1)[0]
    assert background.count("error: NOT_CONNECTED") == 3
    assert "Backend unreachable" not in background
