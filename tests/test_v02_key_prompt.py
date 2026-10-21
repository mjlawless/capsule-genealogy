"""v0.2 — after a capture made without an API key, a prompt says what a key adds (owner request).

Dismissable, self-dismissing after ~15 s (paused while the mouse is over it), and "Not today"
quiets it for a day so it never nags on every capture. These run the shipped `showKeyPrompt`
from `content.js` in node against a minimal fake DOM and clock.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_CONTENT = Path(__file__).resolve().parent.parent / "extension" / "content.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

_HARNESS = r"""
let now = 1_800_000_000_000;
Date.now = () => now;
const timers = [];
global.setTimeout = (fn, ms) => { const t = { at: now + ms, fn, live: true }; timers.push(t); return t; };
global.clearTimeout = (t) => { if (t) t.live = false; };
function advance(ms) {
  now += ms;
  for (const t of timers) if (t.live && t.at <= now) { t.live = false; t.fn(); }
}
const store = {};
global.chrome = { storage: { local: {
  get: async (k) => ({ [k]: store[k] }),
  set: async (o) => Object.assign(store, o),
} } };
const byId = {};
function el(tag) {
  const listeners = {};
  const node = {
    tag, children: [], style: {}, attrs: {}, textContent: "",
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(c) { this.children.push(c); return c; },
    append(...cs) { this.children.push(...cs); },
    addEventListener(ev, fn) { listeners[ev] = fn; },
    fire(ev) { return listeners[ev] && listeners[ev](); },
    remove() { if (this.id && byId[this.id] === this) delete byId[this.id]; },
  };
  Object.defineProperty(node, "id", {
    get() { return this._id; }, set(v) { this._id = v; },
  });
  return node;
}
global.document = {
  createElement: el,
  getElementById: (id) => byId[id] || null,
  documentElement: { appendChild(c) { byId[c.id] = c; return c; } },
};
function find(node, pred) {
  if (pred(node)) return node;
  for (const c of node.children || []) { const f = find(c, pred); if (f) return f; }
  return null;
}
const shown = () => byId[KEY_PROMPT_ID] || null;
"""


def _run(scenario: str) -> dict:
    source = _CONTENT.read_text(encoding="utf-8")
    consts = "\n".join(
        line.strip() for line in source.splitlines()
        if re.match(r"\s*const (KEY_PROMPT_|ANTHROPIC_KEYS_URL)", line)
    )
    start = source.index("  async function showKeyPrompt(")
    end = source.index("\n  }\n", start) + 4
    script = _HARNESS + consts + "\n" + source[start:end] + "\n(async () => {" + scenario + "})()"
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@needs_node
def test_it_shows_the_benefits_and_closes_itself_after_15_seconds():
    r = _run("""
      await showKeyPrompt();
      const card = shown();
      const text = JSON.stringify(card, (k, v) => (k === "fire" ? undefined : v));
      advance(14_000); const stillThere = !!shown();
      advance(1_500); const gone = !shown();
      console.log(JSON.stringify({ text, stillThere, gone, ms: KEY_PROMPT_MS }));
    """)
    assert r["ms"] == 15000
    assert r["stillThere"] and r["gone"]
    for benefit in ("person, date and place", "stated, calculated or estimated",
                    "handwritten scans", "already archived"):
        assert benefit in r["text"]
    assert "console.anthropic.com" in r["text"]


@needs_node
def test_it_waits_while_the_mouse_is_over_it():
    r = _run("""
      await showKeyPrompt();
      shown().fire("mouseenter");
      advance(60_000); const heldOpen = !!shown();
      shown().fire("mouseleave");
      advance(15_001); const gone = !shown();
      console.log(JSON.stringify({ heldOpen, gone }));
    """)
    assert r == {"heldOpen": True, "gone": True}


@needs_node
def test_the_close_button_dismisses_it():
    r = _run("""
      await showKeyPrompt();
      const x = find(shown(), (n) => n.attrs && n.attrs["aria-label"] === "Dismiss");
      x.fire("click");
      console.log(JSON.stringify({ gone: !shown() }));
    """)
    assert r == {"gone": True}


@needs_node
def test_not_today_quiets_it_for_a_day():
    r = _run("""
      await showKeyPrompt();
      const later = find(shown(), (n) => n.tag === "button" && n.textContent === "Not today");
      await later.fire("click");
      const gone = !shown();
      await showKeyPrompt(); const quietLater = !shown();
      now += 24 * 60 * 60 * 1000 + 1;
      await showKeyPrompt(); const backTomorrow = !!shown();
      console.log(JSON.stringify({ gone, quietLater, backTomorrow }));
    """)
    assert r == {"gone": True, "quietLater": True, "backTomorrow": True}


def test_it_appears_only_after_a_clean_capture_made_without_a_key():
    """Codex review: never beside a failed capture, where it would outlive the red toast."""
    source = _CONTENT.read_text(encoding="utf-8")
    assert 'if (res.extraction_pending && clean) showKeyPrompt(res.image_status === "full");' in (
        source
    )
    gate = source[source.index("const clean ="):source.index("if (res.extraction_pending && clean)")]
    for condition in ("res.verify_ok", "!warnings.length", "!missed.length"):
        assert condition in gate
    assert "innerHTML" not in source[source.index("async function showKeyPrompt"):][:6000]


@needs_node
def test_it_mentions_the_record_image_only_when_one_was_saved():
    r = _run("""
      await showKeyPrompt(true);
      const withImg = JSON.stringify(shown(), (k, v) => (k === "fire" ? undefined : v));
      shown().remove();
      await showKeyPrompt(false);
      const without = JSON.stringify(shown(), (k, v) => (k === "fire" ? undefined : v));
      console.log(JSON.stringify({ withImg, without }));
    """)
    assert "record image" in r["withImg"]
    assert "record image" not in r["without"]


def test_found_the_prompt_is_never_archived_with_a_record():
    """Codex review: a prompt still on screen at the next capture would be serialized into
    snapshot.html and drawn over the PDF. Every capture path drops it first, and the PDF
    renderer hides it as a second line of defence."""
    ext = _CONTENT.parent
    content = (ext / "content.js").read_text(encoding="utf-8")
    popup = (ext / "popup.js").read_text(encoding="utf-8")
    render = (ext / "render-pdf.js").read_text(encoding="utf-8")
    assert content.count("document.getElementById(KEY_PROMPT_ID)?.remove();") >= 3
    for path_start in ("async function capture(note, opts)", "recordHtml: document"):
        before = content[: content.index(path_start)] if path_start.startswith("record") else (
            content[content.index(path_start):content.index("capturing = true;",
                                                            content.index(path_start))]
        )
        assert "KEY_PROMPT_ID)?.remove()" in before[-400:]
    grab = popup[popup.index("async function grabPage("):]
    assert grab.index('getElementById("capsule-key-prompt")') < grab.index("outerHTML")
    assert '"#capsule-key-prompt"' in render
