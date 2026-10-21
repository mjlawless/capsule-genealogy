"""v0.2 — a stale saved token heals itself (follow-up to automatic pairing).

After a reinstall or a moved archive the backend has a new token, while the extension still holds
the old one, so every capture would 401 until someone pasted the new one. Now a 401 makes the
extension forget its token, pair again by itself (the backend checks the browser-set Origin) and,
in the background flow, send the capture once more. The backend refuses a bad token before doing
anything, so the retry cannot duplicate work.
"""
from __future__ import annotations

from pathlib import Path

_EXT = Path(__file__).resolve().parent.parent / "extension"


def test_the_background_flow_re_pairs_and_retries_once_on_401():
    source = (_EXT / "background.js").read_text(encoding="utf-8")
    flow = source[source.index("async function postAndShape(o)"):]
    flow = flow[: flow.index("let body = await resp.json()")]
    assert '"X-Capsule-Token": token,' in flow and "let token = o.token;" in flow
    retry = flow[flow.index("if (resp.status === 401)"):]
    assert retry.index("chrome.storage.local.remove(TOKEN_KEY)") < retry.index(
        "pairWithBackend()"
    ) < retry.index("resp = await send(payload)")
    assert retry.count("resp = await send(payload)") == 1  # once, never a loop
    assert "fresh !== token" in retry  # the same stale token is never re-sent


def test_both_popup_capture_paths_re_pair_on_401():
    source = (_EXT / "popup.js").read_text(encoding="utf-8")
    assert source.count("if (resp.status === 401 && (await repairAfter401()))") == 2
    helper = source[source.index("async function repairAfter401()"):]
    helper = helper[: helper.index("\n}\n")]
    assert helper.index("chrome.storage.local.remove(TOKEN_KEY)") < helper.index("loadToken()")
