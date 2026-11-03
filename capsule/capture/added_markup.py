"""Markup that browser extensions had added to a page when its snapshot was taken.

`snapshot.html` is the page as the browser serialized it, so it also holds whatever an extension
had put into the page at that moment: Capsule's own button, "Capturing…" toast and status
attributes (extensions before 0.2.9 left them in; later ones cut them out), or the overlay an
AI assistant paints over a tab it is driving. The snapshot is kept exactly as received, because
it is hashed evidence; the manifest names what is in it, so a reader knows those parts were
never the site's.

An id alone does not prove an extension made an element: a site may use the same id for its
own content, and saying that content was not the site's would misdescribe the evidence. So each
known element is named only when it also has the shape the extension gives it (the tag, where
it is placed, and its fixed-position inline style), or Capsule's own `data-capsule-ui` mark.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

#: Elements, by id, that browser extensions add to a page, with the tag and the parent each is
#: given: Capsule appends its UI to <html>; Claude in Chrome adds its overlay and cursor to
#: <body>, with the overlay's inner border inside it. The ids are the ones
#: extension/render-pdf.js hides from the page PDF (a test keeps the two in step).
_SIGNATURES: dict[str, tuple[str, str]] = {
    "capsule-capture-btn": ("button", "html"),
    "capsule-toast": ("div", "html"),
    "capsule-key-prompt": ("div", "html"),
    "claude-agent-glow-border": ("div", "body"),
    "claude-agent-glow-border-inner": ("div", "#claude-agent-glow-border"),
    "claude-phantom-cursor": ("div", "body"),
}
KNOWN_ADDED_IDS = tuple(_SIGNATURES)

#: Attributes Capsule sets on the page's <html> element during and after a capture.
KNOWN_HTML_ATTRS = ("data-capsule-capturing", "data-capsule-last-outcome")

#: Set by Capsule (0.2.9 on) on every element it creates.
_CAPSULE_MARK = "data-capsule-ui"
_OVERLAY_STYLE = re.compile(r"(^|;)\s*position:\s*(fixed|absolute)\b", re.IGNORECASE)
_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

_ORDER = tuple(f"#{i}" for i in KNOWN_ADDED_IDS) + tuple(f"html[{a}]" for a in KNOWN_HTML_ATTRS)


def _is_capsule_status(name: str, value: str | None) -> bool:
    """The values Capsule writes: `"true"` while capturing; a JSON result with its time after
    (empty in some July 2026 snapshots, from an extension of that time)."""
    if name == "data-capsule-capturing":
        return value == "true"
    if not value:
        return True
    try:
        outcome = json.loads(value or "")
    except ValueError:
        return False
    return isinstance(outcome, dict) and isinstance(outcome.get("at"), str)


class _Scanner(HTMLParser):
    """Collects known added markup from start tags; text is never read as tags."""

    # Elements whose content a browser keeps as literal text, so markup inside them is the
    # site's text (an HTML example on the page), never an element. Named here rather than left
    # to html.parser, whose list differs between Python patch releases, and with <noscript>,
    # which a browser running scripts also keeps as text. <plaintext> runs to the end of the
    # page and is handled below.
    CDATA_CONTENT_ELEMENTS = ("script", "style", "xmp", "iframe", "noembed", "noframes", "noscript")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: set[str] = set()
        self._plaintext = False
        # Each open element as (tag, label): the label is `#id` for a known id, else the tag.
        self._open: list[tuple[str, str]] = []

    def _parent(self) -> str:
        return self._open[-1][1] if self._open else ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._plaintext:
            return
        if tag == "plaintext":
            self._plaintext = True
            return
        values = dict(attrs)
        if tag == "html":
            for name in KNOWN_HTML_ATTRS:
                if name in values and _is_capsule_status(name, values[name]):
                    self.found.add(f"html[{name}]")
        known = values.get("id")
        if known in _SIGNATURES and self._made_by_an_extension(tag, known, values):
            self.found.add(f"#{known}")
        if tag not in _VOID:
            self._open.append((tag, f"#{known}" if known in _SIGNATURES else tag))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._open:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        # Close back to the matching open element; an unmatched end tag changes nothing.
        for at in range(len(self._open) - 1, -1, -1):
            if self._open[at][0] == tag:
                del self._open[at:]
                return

    def _made_by_an_extension(self, tag: str, known: str, values: dict) -> bool:
        if known.startswith("capsule-") and _CAPSULE_MARK in values:
            return True
        want_tag, want_parent = _SIGNATURES[known]
        return (
            tag == want_tag
            and self._parent() == want_parent
            and bool(_OVERLAY_STYLE.search(values.get("style") or ""))
        )


def find_added_markup(html: str) -> tuple[str, ...]:
    """The known extension markup present in `html`, as CSS selectors in a fixed order.

    Empty when there is none, and also when the page cannot be scanned: this is a note about
    the snapshot, and must never fail a capture.
    """
    scanner = _Scanner()
    try:
        scanner.feed(html)
        scanner.close()
    except Exception:  # noqa: BLE001 — html.parser is lenient; a note is never worth a failure
        return ()
    return tuple(item for item in _ORDER if item in scanner.found)
