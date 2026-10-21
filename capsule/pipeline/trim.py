"""Shrink the HTML sent to the extraction model, without losing any of the record.

A captured page is mostly not the record. The NYC death certificate page is 35.7 KB,
of which the seven indexed fields are a few hundred bytes; the rest is scripts, SVG
sprites, a page footer, and per-element `class`/`onclick` noise. We pay input tokens
for all of it on every capture.

This is a **payload** transform, not an evidence one: nothing here changes what is
written to disk or hashed. `snapshot.html` and its SHA-256 are produced upstream from
the page as the browser saw it — minus the subscriber's account chrome, which
`capsule/pipeline/redact.py` removes before the write and which the manifest declares
in `redactions[]`. That is the *only* alteration a snapshot ever carries.

What it removes is defined by what the model cannot use:

- `<script>`, `<style>`, `<noscript>`, `<svg>`, `<template>`, `<link>`, `<meta>` —
  no record text lives in any of them. `<title>` is kept.
- HTML comments.
- Every attribute except a small allow-list.

What it keeps, deliberately:

- **Every text node.** `visible_text(trimmed) == visible_text(original)` is the
  invariant this module exists to hold, and the tests assert it.
- **Every `href`.** The extractor reads these to report `external_refs` — a
  RootsIreland entry names its NLI register in an `<a href>`, and a text-only trim
  would silently destroy that link.
- **Table structure**, including `colspan`/`rowspan`. On a marriage record the
  husband and wife columns are positional; collapsing cells would merge two people
  into one.

`src` is dropped on purpose. The model never needs it, and a media URL can be a
signed, self-authorizing blob URL (the NYC certificate is an Azure SAS link) — no
reason to disclose one to a third party. Media provenance is recorded in the
manifest from the browser, not from this payload.

It never inspects site-specific class names, so it cannot delete a record on a site
that names things differently. (RootsIreland wraps its microfilm link in
`class="noPrint"` — a `noPrint` filter would have thrown away the very link the
`external_refs` feature depends on.)
"""
from __future__ import annotations

import re

from capsule.pipeline.urlsafe import strip_credential_params

#: Elements whose *contents* are never record text. Dropped entirely.
_DROP_WITH_CONTENT = ("script", "style", "noscript", "svg", "template")

#: Void/metadata elements dropped as single tags.
_DROP_TAGS = ("link", "meta", "base")

#: Attributes the model can actually use. Everything else goes.
#: `href` is load-bearing for `external_refs`; the table attrs for column layout.
_KEEP_ATTRS = frozenset(
    {"href", "alt", "title", "colspan", "rowspan", "value", "datetime", "label"}
)

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<([a-zA-Z][\w:-]*)((?:\s[^>]*?)?)/?>", re.DOTALL)
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_WS_RE = re.compile(r"\s+")


def _drop_elements(html: str) -> str:
    for name in _DROP_WITH_CONTENT:
        html = re.sub(
            rf"<{name}\b[^>]*>.*?</{name}\s*>", " ", html, flags=re.IGNORECASE | re.DOTALL
        )
        # An unclosed <script> (or one terminated by EOF) would otherwise survive.
        html = re.sub(rf"<{name}\b[^>]*>.*", " ", html, flags=re.IGNORECASE | re.DOTALL)
    for name in _DROP_TAGS:
        html = re.sub(rf"<{name}\b[^>]*?/?>", " ", html, flags=re.IGNORECASE)
    return html


def _strip_attrs(match: re.Match[str]) -> str:
    tag = match.group(1)
    kept: list[str] = []
    for name, double, single in _ATTR_RE.findall(match.group(2) or ""):
        lowered = name.lower()
        if lowered not in _KEEP_ATTRS:
            continue
        value = double or single
        if lowered == "href":
            # `href` is kept because external_refs depend on it, but a link can be
            # pre-signed (the NYC certificate's Azure SAS URL). Never transmit the
            # credential, even though we do transmit the link.
            value = strip_credential_params(value)
        kept.append(f'{lowered}="{value}"')
    return f"<{tag}{' ' + ' '.join(kept) if kept else ''}>"


def trim_for_extraction(html: str) -> str:
    """Return `html` with everything the extractor cannot read removed.

    Lossless with respect to visible text, links, and table structure; lossy with
    respect to styling, scripting, and layout attributes.
    """
    stripped = _drop_elements(html)
    stripped = _COMMENT_RE.sub(" ", stripped)
    stripped = _TAG_RE.sub(_strip_attrs, stripped)
    return _WS_RE.sub(" ", stripped).strip()


def visible_text(html: str) -> str:
    """The page's text with all markup gone — the invariant `trim` must preserve.

    The tests use it to prove a trim dropped no record text. It is also how a
    caller can tell whether a page has anything to transcribe at all.
    """
    stripped = _drop_elements(html)
    stripped = _COMMENT_RE.sub(" ", stripped)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    return _WS_RE.sub(" ", stripped).strip()
