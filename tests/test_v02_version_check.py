"""v0.2 — the popup says, in plain words, when the extension and the Capsule app differ (item 5).

Chrome can update the extension without the app, or the app can be reinstalled without
reloading the extension. Either way a capture can fail in confusing ways, so the popup compares
`/health`'s version with its own and says which one is behind and how to update it. These run
the shipped `versionAdvice` from `popup.js` in node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_POPUP = Path(__file__).resolve().parent.parent / "extension" / "popup.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _advice(app_version, extension_version):
    source = _POPUP.read_text(encoding="utf-8")
    start = source.index("function versionAdvice(")
    body = source[start : source.index("\n}\n", start) + 3]
    script = body + f"\nconsole.log(JSON.stringify(versionAdvice({json.dumps(app_version)}, " \
        f"{json.dumps(extension_version)})));"
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_node
@pytest.mark.parametrize(
    ("app", "ext"),
    [("0.2.0", "0.2.0"), ("0.2.0+g1a2b3c4", "0.2.0"), ("0.2.0+g1a2b3c4.dirty", "0.2.0")],
)
def test_matching_versions_say_nothing(app, ext):
    assert _advice(app, ext) is None


@needs_node
def test_an_older_app_says_to_run_the_installer_again():
    advice = _advice("0.1.0+gabc1234", "0.2.0")
    assert "Capsule app (0.1.0) is older" in advice and "installer again" in advice
    assert "archive is kept" in advice


@needs_node
def test_an_older_extension_says_to_reload_it():
    advice = _advice("0.2.1", "0.2.0")
    assert "extension (0.2.0) is older" in advice and "chrome://extensions" in advice


@needs_node
@pytest.mark.parametrize("bad", [None, "", "unknown", "v0.2"])
def test_an_unreadable_version_says_nothing(bad):
    assert _advice(bad, "0.2.0") is None


def test_the_popup_checks_on_open():
    source = _POPUP.read_text(encoding="utf-8")
    init = source[source.index("async function init()"):]
    assert init.index("checkVersions();") < init.index("loadToken()")
    html = (_POPUP.parent / "popup.html").read_text(encoding="utf-8")
    assert 'id="version-banner"' in html
