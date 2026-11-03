"""BUG-029 — the saved snapshot held Capsule's own button, toast and status attributes.

The snapshot was the page's outerHTML as it stood mid-capture: 457 of 478 archived snapshots
held Capsule's button, 450 its "Capturing…" toast, 365 the `data-capsule-capturing` attribute
and 16 the PREVIOUS capture's result in `data-capsule-last-outcome`; 119 held an AI assistant's
overlay. Fixed by:

1. the extension cutting exactly Capsule's markup out of the serialized page (every path), and
   leaving the snapshot untouched whenever a cut is not exact;
2. the manifest naming any known extension markup a snapshot still holds
   (`snapshot_added_markup`), so a reader knows which parts were never the site's.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

import tests.test_backend as tb
from capsule.capture.added_markup import KNOWN_ADDED_IDS, KNOWN_HTML_ATTRS, find_added_markup

_EXT = Path(__file__).resolve().parent.parent / "extension"
_CONTENT = (_EXT / "content.js").read_text(encoding="utf-8")
_POPUP = (_EXT / "popup.js").read_text(encoding="utf-8")
_RENDER = (_EXT / "render-pdf.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _snapshot_block(source: str) -> str:
    """The snapshot cut as written in `source`: its two lists and two functions, dedented."""
    start = source.index("const SNAPSHOT_UI_IDS")
    end_marker = "return html; // never let the cut break a capture"
    end = source.index("}\n", source.index("}\n", source.index(end_marker)) + 2) + 2
    line_start = source.rindex("\n", 0, start) + 1
    return textwrap.dedent(source[line_start:end])


def _cut(html: str, inner_length: int, attrs: list[str], nodes: list[str]) -> str:
    block = _snapshot_block(_CONTENT)
    fn = block[block.index("function cutCapsuleMarkup") : block.index("function pageSnapshot")]
    script = fn + (
        f"process.stdout.write(cutCapsuleMarkup({json.dumps(html)}, {inner_length}, "
        f"{json.dumps(attrs)}, {json.dumps(nodes)}));"
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


_TOAST = '<div id="capsule-toast" style="position:fixed">Capturing this page…</div>'
_BTN = '<button id="capsule-capture-btn" disabled="">Capturing…</button>'
_ATTR = ' data-capsule-capturing="true"'
_INNER = "<head></head><body><p>Mary Kelly</p></body>"
_CLEAN = f'<html lang="en">{_INNER}</html>'


def _page(start_attrs: str = "", tail: str = "") -> tuple[str, int]:
    inner = _INNER + tail
    return f'<html lang="en"{start_attrs}>{inner}</html>', len(inner)


@_needs_node
def test_BUG_029_capsule_markup_is_cut_exactly():
    html, inner = _page(_ATTR, _TOAST + _BTN)
    assert _cut(html, inner, [_ATTR], [_TOAST, _BTN]) == _CLEAN


@_needs_node
def test_BUG_029_a_page_without_capsule_markup_is_unchanged():
    assert _cut(_CLEAN, len(_INNER), [], []) == _CLEAN


@_needs_node
def test_BUG_029_attributes_are_cut_from_the_html_start_tag_only():
    """The same attribute text inside the page (here, quoted in a paragraph) is the site's."""
    quoted = f"<p>{_ATTR}</p>"
    html, inner = _page(_ATTR, quoted)
    assert _cut(html, inner, [_ATTR], []) == f'<html lang="en">{_INNER}{quoted}</html>'


@_needs_node
@pytest.mark.parametrize(
    "case",
    ["node twice", "node missing", "attribute missing", "not an html document", "empty piece"],
)
def test_BUG_029_a_cut_that_is_not_exact_leaves_the_snapshot_untouched(case):
    if case == "node twice":
        html, inner = _page("", _TOAST + _TOAST)
        assert _cut(html, inner, [], [_TOAST]) == html
    elif case == "node missing":
        assert _cut(_CLEAN, len(_INNER), [], [_TOAST]) == _CLEAN
    elif case == "attribute missing":
        assert _cut(_CLEAN, len(_INNER), [_ATTR], []) == _CLEAN
    elif case == "not an html document":
        assert _cut("<div>x</div>", 3, [], ["x"]) == "<div>x</div>"
    else:
        assert _cut(_CLEAN, len(_INNER), [""], []) == _CLEAN


def test_BUG_029_the_popup_carries_the_same_cut_as_the_content_script():
    assert _snapshot_block(_POPUP) == _snapshot_block(_CONTENT)


def test_BUG_029_every_capture_path_sends_the_cut_snapshot():
    assert "html: pageSnapshot()," in _CONTENT
    assert "recordHtml: pageSnapshot()," in _CONTENT
    assert "documentElement.outerHTML," not in _CONTENT
    grab = _POPUP[_POPUP.index("async function grabPage(") :]
    assert "html: withBytes ? pageSnapshot() :" in grab
    assert "documentElement.outerHTML," not in grab


def _js_list(source: str, name: str) -> list[str]:
    return json.loads(re.search(rf"const {name} = (\[[^\]]*\]);", source).group(1))


def test_BUG_029_the_cut_covers_every_element_and_attribute_capsule_adds():
    """A new Capsule element or <html> attribute must be added to the cut, or this fails."""
    ids = set(re.findall(r'const [A-Z_]+_ID = "(capsule-[a-z-]+)";', _CONTENT))
    assert ids and ids <= set(_js_list(_CONTENT, "SNAPSHOT_UI_IDS"))
    attrs = set(
        re.findall(r'documentElement\.setAttribute\(\s*"(data-capsule-[a-z-]+)"', _CONTENT)
    )
    assert attrs == set(_js_list(_CONTENT, "SNAPSHOT_HTML_ATTRS"))


def test_BUG_029_the_manifest_note_knows_every_id_the_pdf_hides_and_every_capsule_attribute():
    start = _RENDER.index("const HIDE_SELECTORS")
    hidden = set(re.findall(r'"#([a-z-]+)"', _RENDER[start : _RENDER.index("];", start)]))
    assert set(KNOWN_ADDED_IDS) == hidden
    assert set(KNOWN_HTML_ATTRS) == set(_js_list(_CONTENT, "SNAPSHOT_HTML_ATTRS"))
    assert set(_js_list(_CONTENT, "SNAPSHOT_UI_IDS")) <= set(KNOWN_ADDED_IDS)


_GLOW = (
    '<div id="claude-agent-glow-border" style="position: fixed; inset: 0px;">'
    '<div id="claude-agent-glow-border-inner" style="position: absolute; inset: 0px;"></div></div>'
)


def test_BUG_029_known_markup_is_found_in_tags_never_in_text_or_scripts():
    html = (
        '<html data-capsule-capturing="true"><body>'
        f"{_GLOW}"
        '<p>id="capsule-toast"</p>'
        "<script>var s = '<div id=\"capsule-capture-btn\" style=\"position:fixed\">';</script>"
        '<p data-capsule-last-outcome="x">not on html</p>'
        f"</body>{_TOAST}</html>"
    )
    assert find_added_markup(html) == (
        "#capsule-toast",
        "#claude-agent-glow-border",
        "#claude-agent-glow-border-inner",
        "html[data-capsule-capturing]",
    )
    assert find_added_markup(_CLEAN) == ()


@pytest.mark.parametrize(
    "page",
    [
        '<html><body><article id="capsule-capture-btn">Original record text</article></body></html>',
        '<html><body><div id="capsule-toast" style="position:fixed">in the body</div></body></html>',
        '<html><body><div id="claude-phantom-cursor">no fixed style</div></body></html>',
        '<html><body><div><div id="claude-agent-glow-border" style="position:fixed">nested'
        "</div></div></body></html>",
        '<html data-capsule-capturing="yes" data-capsule-last-outcome="site value"><body></body></html>',
    ],
)
def test_BUG_029_a_site_element_that_only_shares_an_id_is_not_called_extension_markup(page):
    """Codex review: an id (or attribute name) alone is not proof an extension added it."""
    assert find_added_markup(page) == ()


def test_BUG_029_capsules_mark_is_proof_wherever_the_element_is():
    page = '<html><body><div id="capsule-toast" data-capsule-ui="">x</div></body></html>'
    assert find_added_markup(page) == ("#capsule-toast",)


def _capture(tmp_path, html: str) -> dict:
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        resp = client.post("/capture", json={"url": tb._NYC_URL, "html": html}, headers=tb._AUTH)
        assert resp.status_code == 200, resp.text
    finally:
        backend.app.dependency_overrides.clear()
    (folder,) = (tmp_path / "captures").glob("*/")
    from capsule.capture.manifest import verify_capture

    assert verify_capture(folder).ok is True
    assert (folder / "snapshot.html").read_text(encoding="utf-8") == html  # kept as received
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


def test_BUG_029_the_manifest_names_extension_markup_the_snapshot_still_holds(tmp_path):
    page = tb._PAGE.replace("<html>", '<html data-capsule-capturing="true">').replace(
        "</body>", f"</body>{_TOAST}"
    )
    manifest = _capture(tmp_path, page)
    assert manifest["snapshot_added_markup"] == ["#capsule-toast", "html[data-capsule-capturing]"]
    assert list(manifest)[-1] == "hashes"


def test_BUG_029_a_clean_snapshot_has_no_note(tmp_path):
    assert "snapshot_added_markup" not in _capture(tmp_path, tb._PAGE)


def _page_snapshot(html: str, inner: str, attrs: dict, elements: dict) -> str:
    """Run the shipped pageSnapshot against a minimal DOM: `elements` maps an id to
    (outerHTML, marked by Capsule, placed directly under <html>)."""
    block = _snapshot_block(_CONTENT)
    fake = f"""
      const ATTRS = {json.dumps(attrs)};
      const ELEMENTS = {json.dumps(elements)};
      const root = {{
        outerHTML: {json.dumps(html)}, innerHTML: {json.dumps(inner)},
        hasAttribute: (n) => n in ATTRS, getAttribute: (n) => ATTRS[n],
      }};
      const escapeAttr = (v) => v.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
      globalThis.document = {{
        documentElement: root,
        getElementById: (id) => {{
          const e = ELEMENTS[id];
          if (!e) return null;
          return {{ outerHTML: e[0], parentNode: e[2] ? root : {{}},
                   hasAttribute: (n) => n === "data-capsule-ui" && e[1] }};
        }},
        createElement: () => ({{
          setAttribute(n, v) {{ this.n = n; this.v = v; }},
          get outerHTML() {{ return `<span ${{this.n}}="${{escapeAttr(this.v)}}"></span>`; }},
        }}),
      }};
    """
    script = fake + block + "process.stdout.write(pageSnapshot());"
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


_OWN_TOAST = '<div id="capsule-toast" data-capsule-ui="">Capturing this page…</div>'
_SITE_BTN = '<article id="capsule-capture-btn">Original record text</article>'


@_needs_node
def test_BUG_029_only_capsules_own_marked_elements_are_cut():
    inner = _INNER + _OWN_TOAST
    html = f'<html lang="en"{_ATTR}>{inner}</html>'
    out = _page_snapshot(
        html, inner, {"data-capsule-capturing": "true"}, {"capsule-toast": [_OWN_TOAST, True, True]}
    )
    assert out == _CLEAN


@_needs_node
@pytest.mark.parametrize("marked, under_html", [(False, True), (True, False)])
def test_BUG_029_a_page_element_sharing_capsules_id_stays(marked, under_html):
    """Codex review: an id match is not proof Capsule made the element. A site's own element
    with that id (Capsule then never creates its own) must survive in the snapshot."""
    inner = _INNER + _SITE_BTN
    html = f'<html lang="en">{inner}</html>'
    out = _page_snapshot(html, inner, {}, {"capsule-capture-btn": [_SITE_BTN, marked, under_html]})
    assert out == html and "Original record text" in out


def test_BUG_029_every_element_capsule_creates_is_marked():
    for creation in ("el.id = TOAST_ID;", "card.id = KEY_PROMPT_ID;", "btn.id = BTN_ID;"):
        at = _CONTENT.index(creation)
        assert 'setAttribute(SNAPSHOT_UI_MARK, "");' in _CONTENT[at : at + 120], creation


def test_BUG_029_a_capture_of_a_page_using_capsules_id_gets_no_note(tmp_path):
    page = tb._PAGE.replace(
        "<body>", '<body><article id="capsule-capture-btn">Original record text</article>'
    )
    assert "snapshot_added_markup" not in _capture(tmp_path, page)


@_needs_node
@pytest.mark.parametrize(
    "name, value",
    [
        ("data-capsule-last-outcome", "site value"),
        ("data-capsule-last-outcome", "[1, 2]"),
        ("data-capsule-last-outcome", '{"ok": true}'),
        ("data-capsule-capturing", "yes"),
    ],
)
def test_BUG_029_a_site_value_on_a_capsule_attribute_name_stays(name, value):
    """Codex review: only the values Capsule writes are cut; anything else is the site's."""
    escaped = value.replace("&", "&amp;").replace('"', "&quot;")
    html = f'<html lang="en" {name}="{escaped}">{_INNER}</html>'
    assert _page_snapshot(html, _INNER, {name: value}, {}) == html


@_needs_node
@pytest.mark.parametrize(
    "name, value",
    [
        ("data-capsule-capturing", "true"),
        ("data-capsule-last-outcome", ""),
        ("data-capsule-last-outcome", '{"ok":true,"note":"say \\"hi\\" <b> & x","at":"2026-10-30"}'),
    ],
)
def test_BUG_029_capsules_own_attribute_values_are_cut(name, value):
    escaped = value.replace("&", "&amp;").replace('"', "&quot;")
    html = f'<html lang="en" {name}="{escaped}">{_INNER}</html>'
    assert _page_snapshot(html, _INNER, {name: value}, {}) == _CLEAN


@pytest.mark.parametrize(
    "element", ["xmp", "noscript", "iframe", "noembed", "noframes", "plaintext", "script", "style"]
)
def test_BUG_029_markup_inside_a_browsers_raw_text_element_is_the_sites_text(element):
    """Codex review: a browser keeps these elements' content as literal text (an HTML example
    shown on the page, or <noscript> on a page running scripts), so it is never an element."""
    example = (
        '<html data-capsule-capturing="true"><body>'
        '<div id="claude-phantom-cursor" style="position:fixed"></div></body></html>'
    )
    page = f"<html><body><{element}>{example}</{element}></body></html>"
    assert find_added_markup(page) == ()
