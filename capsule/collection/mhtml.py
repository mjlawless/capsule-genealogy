"""The full page (`page.mhtml`) as one readable page (REQ-015 §D).

Since 0.2.7 every capture of a web page also keeps the page as Chrome saves it: an MHTML
file (`render:page-mhtml`) holding the page with its stylesheets, pictures and fonts, made
from what the tab had already loaded. Chrome opens such a file from disk, but will not
display one served over http, so the backend unpacks it here into a single HTML page: every
stylesheet, picture and font the page used is inlined as a `data:` URI, scripts are dropped,
and ads and Capsule's own UI are hidden. The page is served in the same sandbox as any
capture file, so it can load nothing from anywhere: what shows is what the file holds.

The file is hostile input (a site wrote most of it), so the parser here is written for it:
every scan moves forward only (no backtracking pattern ever runs over the document), the
number of parts and the size of the page made are capped, a stylesheet's `@import` chain is
depth-limited and cycle-checked, and text is decoded with replacement, never trusted. The
file on disk is only read.
"""
from __future__ import annotations

import base64
import binascii
import re
import string
from dataclasses import dataclass
from html import unescape
from urllib.parse import urldefrag, urljoin

from capsule.collection.escape import esc

MAX_PARTS = 3_000
# References followed in one page (`src`, `url(...)`, `@import`). A real page has a few
# hundred; a crafted one could branch imports to make the work grow as a power of its size.
MAX_REFERENCES = 50_000
MAX_OUTPUT_BYTES = 80 * 1024 * 1024  # data: URIs grow a page by a third; past this, stop inlining
_MAX_IMPORT_DEPTH = 4
_MAX_TAG = 64 * 1024  # a tag longer than this is copied through untouched
_MAX_HEADERS = 16_384

_TAG_END = " \t\n\r\f/>"
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def ascii_lower(text: str) -> str:
    """Lower-case A-Z only. The scans find offsets in a lower-cased copy and slice the
    original, so the copy must keep every character's length: `str.lower` turns "İ" into two
    characters, which shifted every tag after a Turkish name and broke the page."""
    return text.translate(_ASCII_LOWER)
# One attribute inside a delimited tag. Each alternative starts on a different character
# class, and a quoted value ends at its first matching quote, so this is linear in the tag.
_ATTR_RE = re.compile(r"""([^\s"'>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+))?""")
_HINT_RE = re.compile(
    r"\b(?:prefetch|preconnect|dns-prefetch|preload|modulepreload|prerender|"
    r"compression-dictionary)\b",
    re.I,
)
_CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]{1,40})", re.I)
_MIME_RE = re.compile(r"[a-z0-9.+-]{1,40}/[a-z0-9.+-]{1,80}")

# Hidden, never removed, in the page as shown. Unlike the page text (HTML) view (folder.py), this
# one has its own stylesheets, so pop-ups are already hidden by the page itself and are not
# touched here. Frames cannot show (they are `cid:` parts) and are hidden; the record files a
# page embeds were captured as media in their own right.
_VIEW_CSS = (
    "iframe,object,embed,"
    '[id^="div-gpt-ad"],[id*="google_ads"],ins.adsbygoogle,[data-ad],[data-ad-slot],'
    '[data-ad-unit],[data-google-query-id],[id^="ad-"],[class^="ad-"],[class*=" ad-"],'
    '[class~="ad"],[class~="ads"],[class*="advert"],[aria-label="Advertisement" i],'
    "#capsule-capture-btn,#capsule-toast,#capsule-key-prompt{display:none!important}"
    "#capsule-view-bar{all:initial;display:block!important;position:sticky;top:0;"
    "z-index:2147483647;background:#f6efe2;color:#3d2f17;border-bottom:1px solid #d9c8a6;"
    "padding:.55rem 1rem;font:13px/1.45 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
    "#capsule-view-bar a{color:#7a5c2e;font-weight:600;margin-left:.6rem}"
)


@dataclass(frozen=True)
class _Part:
    mime: str
    location: str
    content_id: str
    data: bytes
    charset: str


def _headers(block: str) -> dict[str, str]:
    """MIME headers, unfolded, names lower-cased. `block` is already bounded."""
    out: dict[str, str] = {}
    name = ""
    for line in block.splitlines():
        if line[:1] in (" ", "\t") and name:
            out[name] += " " + line.strip()
            continue
        key, sep, value = line.partition(":")
        if sep:
            name = key.strip().lower()
            out[name] = value.strip()
    return out


def _param(value: str, key: str) -> str:
    for piece in value.split(";")[1:]:
        k, _, v = piece.partition("=")
        if k.strip().lower() == key:
            return v.strip().strip('"')
    return ""


def _decode(body: str, encoding: str) -> bytes:
    raw = body.encode("latin-1", "replace")
    encoding = encoding.lower()
    try:
        if encoding == "base64":
            return binascii.a2b_base64(raw)
        if encoding == "quoted-printable":
            return binascii.a2b_qp(raw)
    except (binascii.Error, ValueError):
        return b""
    return raw


def _split_headers(text: str, start: int = 0) -> tuple[int, int]:
    """Where a header block ends and its body begins, or (-1, -1)."""
    end = text.find("\r\n\r\n", start, start + _MAX_HEADERS)
    if end >= 0:
        return end, end + 4
    end = text.find("\n\n", start, start + _MAX_HEADERS)
    return (end, end + 2) if end >= 0 else (-1, -1)


def parse(data: bytes) -> tuple[str, list[_Part]]:
    """(the page's own URL, its parts). Empty when the bytes are not an MHTML file."""
    # MHTML is 7-bit text by construction (binary parts are base64). Latin-1 maps every
    # byte to one character, so offsets stay exact and nothing can fail to decode.
    text = data.decode("latin-1")
    head_end, body_at = _split_headers(text)
    if head_end < 0:
        return "", []
    top = _headers(text[:head_end])
    ctype = top.get("content-type", "")
    boundary = _param(ctype, "boundary")
    if not boundary or not ctype.lower().startswith("multipart/related"):
        return "", []
    page_url = top.get("snapshot-content-location", "")
    marker = "--" + boundary
    parts: list[_Part] = []
    at = text.find(marker, body_at)
    while at >= 0 and len(parts) < MAX_PARTS:
        start = at + len(marker)
        if text.startswith("--", start):
            break  # the closing boundary
        nxt = text.find(marker, start)
        stop = nxt if nxt >= 0 else len(text)
        h_end, b_at = _split_headers(text, start)
        if 0 <= h_end < stop:
            h = _headers(text[start:h_end])
            ctype = h.get("content-type", "")
            body = text[b_at:stop]
            if body.endswith("\r\n"):
                body = body[:-2]
            elif body.endswith("\n"):
                body = body[:-1]
            parts.append(_Part(
                mime=ctype.split(";")[0].strip().lower(),
                location=urldefrag(h.get("content-location", ""))[0],
                content_id=h.get("content-id", "").strip("<>"),
                data=_decode(body, h.get("content-transfer-encoding", "")),
                charset=_param(ctype, "charset"),
            ))
        at = nxt
    return page_url, parts


def _text_of(part: _Part) -> str:
    charset = part.charset
    if not charset:
        found = _CHARSET_RE.search(part.data, 0, 4096)
        charset = found.group(1).decode("ascii") if found else "utf-8"
    try:
        return part.data.decode(charset, "replace")
    except LookupError:
        return part.data.decode("utf-8", "replace")


_CSS_ESCAPE_RE = re.compile(r"\\(.)", re.S)


def _unescaped(text: str, char: str, start: int) -> int:
    """Index of the next `char` at or after `start` that a CSS escape does not cover, or -1:
    the quote ending a string, or the `)` ending an unquoted url(...). One after an odd run
    of backslashes is escaped. Each backslash is counted once, for the character right after
    it, so this stays linear."""
    k = start
    while True:
        k = text.find(char, k)
        if k < 0:
            return -1
        b = k - 1
        while b >= start and text[b] == "\\":
            b -= 1
        if (k - 1 - b) % 2 == 0:
            return k
        k += 1


class _Inliner:
    """Turns references to the file's own parts into `data:` URIs, within a size budget."""

    def __init__(self, parts: list[_Part]) -> None:
        self.by_location: dict[str, _Part] = {}
        for part in parts:
            if part.location and part.location not in self.by_location:
                self.by_location[part.location] = part
        self.budget = MAX_OUTPUT_BYTES
        self.work = MAX_REFERENCES
        # Each part is turned into its data: URI once, stylesheets included (so a stylesheet
        # imported many times is expanded once), and every later use is a lookup.
        self.cache: dict[str, str] = {}
        # Parts that cannot be inlined (too big for what is left): asked again, the answer is
        # immediate rather than another conversion.
        self.refused: set[str] = set()

    def uri(
        self, url: str, base: str, depth: int = 0, seen: frozenset[str] = frozenset()
    ) -> str | None:
        self.work -= 1
        if self.work < 0 or self.budget <= 0:
            return None
        url = unescape(url.strip())
        if not url or url.startswith(("data:", "cid:", "javascript:")):
            return None
        try:
            target = urldefrag(urljoin(base, url))[0]
        except ValueError:
            return None
        part = self.by_location.get(target)
        if part is None or target in seen or target in self.refused:
            return None
        if target in self.cache:
            # Every insertion is charged, not only the first: one picture referenced a
            # thousand times would otherwise make a page a thousand pictures big.
            cached = self.cache[target]
            if len(cached) > self.budget:
                return None
            self.budget -= len(cached)
            return cached
        if not _MIME_RE.fullmatch(part.mime) or part.mime == "text/html":
            return None  # frames and pages are not inlined
        data = part.data
        # Rewriting can only grow a part, so one already too big is refused before any work.
        if (len(data) * 4) // 3 > self.budget:
            self.refused.add(target)
            return None
        if part.mime == "text/css":
            if depth >= _MAX_IMPORT_DEPTH:
                return None
            data = self.css(_text_of(part), target, depth + 1, seen | {target}).encode("utf-8")
        cost = (len(data) * 4) // 3 + 64
        if cost > self.budget:
            self.refused.add(target)
            return None
        self.budget -= cost
        # Re-encoded stylesheets are UTF-8 whatever their @charset said; a charset on the
        # data: URI outranks @charset, so the page reads them as what they now are.
        mime = "text/css;charset=utf-8" if part.mime == "text/css" else part.mime
        made = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        self.cache[target] = made
        return made

    def _quoted_imports(self, text: str, base: str, depth: int, seen: frozenset[str]) -> str:
        """`@import "theme.css";` names a stylesheet without `url(...)`. Chrome writes imports as
        `url(...)` when it saves a page, but a file from elsewhere may not: turn the quoted form
        into `url(...)` so the pass above inlines it. Forward-only, like every scan here."""
        out: list[str] = []
        low = ascii_lower(text)
        i = 0
        while True:
            at = low.find("@import", i)
            if at < 0:
                out.append(text[i:])
                break
            j = at + 7
            while j < len(text) and text[j] in " \t\r\n\f":
                j += 1
            quote = text[j:j + 1]
            end = _unescaped(text, quote, j + 1) if quote in ("'", '"') else -1
            if end < 0:
                out.append(text[i:j])
                i = j
                continue
            made = self.uri(text[j + 1:end], base, depth, seen)
            out.append(text[i:j])
            out.append(f'url("{made}")' if made else text[j:end + 1])
            i = end + 1
        return "".join(out)

    def css(
        self, text: str, base: str, depth: int = 0, seen: frozenset[str] = frozenset()
    ) -> str:
        """Rewrite every `url(...)` in a stylesheet that names one of the file's parts, in one
        forward pass. (`@import url(...)` is covered: the import becomes a data: URI.)"""
        text = self._quoted_imports(text, base, depth, seen)
        out: list[str] = []
        low = ascii_lower(text)
        i = 0
        while True:
            at = low.find("url(", i)
            if at < 0:
                out.append(text[i:])
                break
            j = at + 4
            while j < len(text) and text[j] in " \t\r\n\f":
                j += 1
            quote = text[j:j + 1]
            if quote in ("'", '"'):
                # A quoted URL may hold ")" (`scan_(1).png`): end it at its own quote.
                end = _unescaped(text, quote, j + 1)
                close = text.find(")", end + 1) if end >= 0 else -1
                raw = _CSS_ESCAPE_RE.sub(r"\1", text[j + 1:end]) if end >= 0 else ""
            else:
                # Unquoted, a ")" can still be part of the URL when escaped (`scan_\(1\).png`).
                close = _unescaped(text, ")", j)
                raw = _CSS_ESCAPE_RE.sub(r"\1", text[j:close].strip()) if close >= 0 else ""
            if close < 0:
                # Nothing later can close it, so nothing later is a URL either.
                out.append(text[i:])
                break
            made = self.uri(raw, base, depth, seen)
            out.append(text[i:at])
            out.append(f'url("{made}")' if made else text[at: close + 1])
            i = close + 1
        return "".join(out)


def _rewrite_tag(tag: str, name: str, inline: _Inliner, base: str) -> str | None:
    """A start tag with its references to the file's parts inlined; None drops the tag."""
    attrs = list(_ATTR_RE.finditer(tag, 1 + len(name), len(tag) - 1))
    values = {m.group(1).lower(): m.group(2) or "" for m in attrs}

    def plain(key: str) -> str:
        value = values.get(key) or ""
        return value[1:-1] if value[:1] in "\"'" and len(value) >= 2 else value

    if name == "source":
        return None  # <picture>/<video> sources: the <img> carries what actually loaded
    if name == "link" and _HINT_RE.search(plain("rel")):
        return None
    replace: dict[str, str] = {}
    drop = {"srcset", "sizes"} if name == "img" else set()
    if name == "img":
        # The picture that loaded may be a srcset candidate rather than `src`: use whichever
        # the file holds.
        candidates = [plain("src")] + [c.strip().split(" ")[0] for c in plain("srcset").split(",")]
        for candidate in candidates:
            made = inline.uri(candidate, base) if candidate else None
            if made:
                replace["src"] = made
                break
    else:
        for key in ("src", "poster", "background"):
            made = inline.uri(plain(key), base) if plain(key) else None
            if made:
                replace[key] = made
    if name == "link" and plain("href"):
        made = inline.uri(plain("href"), base)
        if made:
            replace["href"] = made
    if "url(" in plain("style").lower():
        replace["style"] = esc(inline.css(unescape(plain("style")), base))
    if not replace and not any(k in values for k in drop):
        return tag
    pieces = [f"<{tag[1:1 + len(name)]}"]
    for m in attrs:
        key = m.group(1).lower()
        if key in drop:
            continue
        pieces.append(f' {m.group(1)}="{replace.pop(key)}"' if key in replace else " " + m.group(0))
    pieces += [f' {k}="{v}"' for k, v in replace.items()]
    return "".join(pieces) + (" />" if tag[:-1].rstrip().endswith("/") else ">")


def _rewrite_html(html: str, inline: _Inliner, base: str) -> tuple[str, int, int]:
    """One forward pass: drop scripts and resource hints, inline the file's own parts.
    Returns the page and where the content after `<head>` and after `<body>` starts."""
    low = ascii_lower(html)
    out: list[str] = []
    size = 0
    head_at = body_at = -1
    i = 0

    def emit(piece: str) -> None:
        nonlocal size
        out.append(piece)
        size += len(piece)

    while True:
        start = low.find("<", i)
        if start < 0:
            emit(html[i:])
            break
        name_end = start + 1
        while name_end < len(low) and low[name_end] not in _TAG_END and name_end - start < 12:
            name_end += 1
        name = low[start + 1:name_end]
        close = low.find(">", start)
        if close < 0:
            emit(html[i:])
            break
        emit(html[i:start])
        if name == "script":
            end = low.find("</script", close)
            after = low.find(">", end) if end >= 0 else -1
            if after < 0:
                break  # an unclosed script runs to the end of the page in a browser too
            i = after + 1
            continue
        tag = html[start:close + 1]
        if name == "style":
            end = low.find("</style", close)
            if end < 0:  # an unclosed style holds the rest of the page, as in a browser
                emit(html[start:])
                break
            emit(tag + inline.css(html[close + 1:end], base))
            i = end
            continue
        if name[:1].isalpha() and len(tag) <= _MAX_TAG:
            rewritten = _rewrite_tag(tag, name, inline, base)
            if rewritten is not None:
                emit(rewritten)
        else:
            emit(tag)
        if name == "head" and head_at < 0:
            head_at = size
        elif name == "body" and body_at < 0:
            body_at = size
        i = close + 1
    return "".join(out), head_at, body_at


def render_mhtml_view(capture_id: str, rel: str, data: bytes) -> str:
    """The full page as one self-contained HTML page, or a short explanation when the file
    cannot be read as one."""
    page_url, parts = parse(data)
    main = next((p for p in parts if p.mime == "text/html" and p.location == page_url), None)
    main = main or next((p for p in parts if p.mime == "text/html"), None)
    raw_url = f"/{esc(capture_id)}/" + "/".join(esc(p) for p in rel.split("/"))
    bar = (
        '<div id="capsule-view-bar">Full page, from the saved file alone: ads are hidden, '
        "scripts do not run, and nothing is loaded from the site."
        f'<a href="/{esc(capture_id)}/">Back to the capture</a>'
        f'<a href="{raw_url}">Download the .mhtml</a></div>'
    )
    style = f"<style>{_VIEW_CSS}</style>"
    if main is None:
        page = (
            f"<!doctype html><meta charset=utf-8><title>Full page</title>{style}{bar}"
            "<p style='font:15px system-ui;margin:2rem'>This file could not be read as a full "
            "page. The download link above gives the file itself.</p>"
        )
    else:
        inline = _Inliner(parts)
        html, head_at, body_at = _rewrite_html(_text_of(main), inline, main.location or page_url)
        if body_at < 0:
            page = style + bar + html
        elif head_at < 0 or head_at > body_at:
            page = html[:body_at] + style + bar + html[body_at:]
        else:
            page = html[:head_at] + style + html[head_at:body_at] + bar + html[body_at:]
    return page.encode("utf-8", "replace").decode("utf-8")
