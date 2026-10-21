"""HTML escaping for the Captures Index (spec `captures-index/v2` §3.3).

Nearly every value the index renders originates in attacker-controlled page
content: `title`, `site_name`, persona labels and assertion verbatim values are
LLM extractions of a captured page, and `CaptureRequest.url` is unvalidated at
the backend. Markdown made most of that inert; HTML does not.

Two functions, because there are exactly two kinds of interpolation:

- `esc()` — every value, whether it lands in a text node or a quoted attribute.
  It always escapes quotes. A text node does not *need* that, but a single
  always-quote-safe escaper removes the "wrong escaper for this context" bug
  class, and `&quot;` renders as `"` regardless, so nothing is lost on screen.
- `href()` — anything that becomes a link. A **scheme allowlist**, not an
  escape, because escaping `javascript:alert(1)` yields a working exploit.
"""
from __future__ import annotations

from html import escape as _escape

#: The only schemes that may become an `href`. Everything else renders inert.
SAFE_SCHEMES = ("http://", "https://")


def esc(value: str) -> str:
    """Escape a value for any HTML context: `&`, `<`, `>`, `"`, `'`."""
    return _escape(value, quote=True)


def _strip_control(url: str) -> str:
    """Drop ASCII control characters and whitespace, as a browser does.

    A browser ignores these when parsing a URL's scheme, so `java\\nscript:` is
    `javascript:`. Stripping before the allowlist test closes that bypass.
    """
    return "".join(ch for ch in url if ord(ch) > 0x20 and ord(ch) != 0x7F)


def href(url: str) -> str | None:
    """Return an attribute-safe href, or `None` if `url` is not linkable.

    `None` means the caller must render the URL as inert escaped text rather
    than as an `<a href>`. Callers never fall back to emitting the raw value.
    """
    cleaned = _strip_control(url)
    if not cleaned.lower().startswith(SAFE_SCHEMES):
        return None
    return esc(cleaned)
