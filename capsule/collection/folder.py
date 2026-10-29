"""Readable pages for one capture folder, served by the backend (REQ-015).

Three views over what is on disk. None of them changes a byte of it: the files are sealed
evidence (their hashes are in the manifest), so everything here is a way of *reading*
them.

- The folder page (`/<capture>/`): the record as the index's detail pane shows it, then
  everything the folder holds: media shown in place, the full page, the page text, what the manifest
  records about the capture, and every file with a readable and a raw link.
- A readable view of any JSON file (`<file>.json?view`): tables for lists of records,
  labelled fields for objects, long text folded away. The raw file stays one click off.
- A cleaned view of the page text (`snapshot.html?view`): the page's scripts, ads,
  pop-up dialogs, link prefetches and Capsule's own on-page UI hidden, with a bar saying
  so. The backend serves it in the same script-free, network-free sandbox as the raw
  file, so pictures that lived on the site do not load: the archive never calls the site.

Every value read from a capture (the site's text, the model's output, a note) is
escaped with `esc`; links go through `href`, which refuses anything but http(s).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from capsule.collection.assets import INDEX_COPY_JS, INDEX_CSS
from capsule.collection.escape import esc, href
from capsule.collection.index import detail_body
from capsule.collection.mhtml import ascii_lower
from capsule.collection.read import CaptureRecord

_logger = logging.getLogger(__name__)

# What each file in a capture folder is, for someone who has never seen one.
_FILE_LABELS = {
    "citation.json": "Citation",
    "extraction.json": "Facts read from the page",
    "manifest.json": "Capture record (manifest)",
    "result.json": "Capture result",
    "notes.json": "Your notes",
    "lifecycle.json": "History of this capture",
    "snapshot.html": "Page text (HTML)",
    "document.pdf": "Document",
}
_DIR_LABELS = {
    "agent_log": "AI reading log",
    "media": "Media",
    "transcript": "Handwriting transcription",
}
# How a media file was arrived at (manifest `media[].provenance`), in plain words.
_PROVENANCE = {
    "render:page-pdf": "PDF printed from the page as you saw it",
    "render:page-mhtml": "Full page: the complete copy, as Chrome saves it",
    "page:embedded": "Embedded in the page",
    "viewer:canvas": "Read from the site's image viewer",
    "ancestry:download": "Ancestry's own download",
    "site:official-download": "The site's own download",
}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif"}
_VIEWABLE = {".json", ".html", ".htm", ".mhtml"}

FOLDER_CSS = """\
body{max-width:72rem;margin:0 auto}
header .crumb{margin:0 0 .3rem;font-size:.82rem}
header .crumb a{text-decoration:none}
.folder{display:grid;gap:1.25rem;padding:1.25rem}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:1rem 1.15rem}
.card>h2{font-size:.72rem;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);
  margin:0 0 .7rem;font-weight:600}
.lead{font-size:.85rem;color:var(--muted);margin:.2rem 0 .7rem}
.media{display:grid;gap:1rem;grid-template-columns:repeat(auto-fill,minmax(16rem,1fr))}
.media figure{margin:0}
.media img{display:block;max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;
  background:#fff}
.media figcaption,.pdf figcaption{font-size:.75rem;color:var(--muted);margin-top:.35rem}
.pdf{margin:0 0 1rem}
.pdf iframe{width:100%;height:75vh;border:1px solid var(--line);border-radius:6px;background:#fff}
.files{width:100%;font-size:.82rem}
.files th{text-align:left;color:var(--muted);font-weight:600;font-size:.7rem;letter-spacing:.06em;
  text-transform:uppercase;border-bottom:1px solid var(--line);padding:.3rem .45rem}
.files td{border-bottom:1px solid var(--line);padding:.35rem .45rem;vertical-align:top}
.files td.size{white-space:nowrap;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.files td.open{white-space:nowrap}
.files code{font-size:.75rem;overflow-wrap:anywhere}
.btn{display:inline-block;font-size:.75rem;padding:.12rem .5rem;border:1px solid var(--line);
  border-radius:5px;text-decoration:none;margin-right:.3rem}
.btn:hover,.btn:focus-visible{border-color:var(--accent);outline:none}
.about{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.25rem .9rem;font-size:.82rem;margin:0}
.about dt{color:var(--muted)}
.about dd{margin:0;overflow-wrap:anywhere}
"""

JSON_VIEW_CSS = """\
body{max-width:72rem;margin:0 auto}
header .crumb{margin:0 0 .3rem;font-size:.82rem}
header .crumb a{text-decoration:none}
.jv-wrap{padding:1.25rem}
.jv{display:grid;grid-template-columns:minmax(8rem,auto) minmax(0,1fr);gap:.3rem 1rem;margin:0;
  font-size:.85rem}
.jv>dt{color:var(--muted);font-weight:600}
.jv>dd{margin:0;overflow-wrap:anywhere;min-width:0}
.jv-table{width:100%;font-size:.8rem;margin:.2rem 0 .4rem}
.jv-table th{text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--line);
  padding:.25rem .4rem;white-space:nowrap}
.jv-table td{border-bottom:1px solid var(--line);padding:.25rem .4rem;vertical-align:top;
  overflow-wrap:anywhere}
.jv-list{margin:0;padding-left:1.1rem}
.jv-long{white-space:pre-wrap;font-size:.8rem;margin:.3rem 0 0;padding:.5rem .65rem;
  background:var(--chip);border-radius:6px;max-height:28rem;overflow:auto}
.jv details>summary,.jv-wrap details>summary{cursor:pointer;color:var(--accent);font-size:.8rem}
.jv-none{color:var(--muted)}
.jv-num{font-variant-numeric:tabular-nums}
"""

# The cleaned view of the page text (HTML). Hidden, never removed: the markup stays as saved,
# and "Raw file" is one click away. Ads are matched by the conventions ad networks use
# (Google's div-gpt-ad slots and adsbygoogle, data-ad attributes, `ad-` classes);
# iframes go because on a record page they are ads, trackers and consent prompts, and
# under the sandbox they could not load anyway. Pop-up dialogs go because the page's
# own stylesheet, which hid them, usually lived on the site and was not saved.
_SNAPSHOT_HIDE = (
    "iframe,object,embed,noscript,"
    '[id^="div-gpt-ad"],[id*="google_ads"],ins.adsbygoogle,[data-ad],[data-ad-slot],'
    '[data-ad-unit],[data-google-query-id],[id^="ad-"],[class^="ad-"],[class*=" ad-"],'
    '[class~="ad"],[class~="ads"],[class*="advert"],[aria-label="Advertisement" i],'
    "#capsule-capture-btn,#capsule-toast,#capsule-key-prompt,"
    '.modal,[role="dialog"],[aria-modal="true"],dialog:not([open])'
    "{display:none!important}"
)
_SNAPSHOT_CSS = (
    _SNAPSHOT_HIDE
    # Zero-specificity defaults: a page that kept its own styles keeps them.
    + ":where(body){max-width:62rem;margin:0 auto;padding:0 1rem 2rem;"
    "font:16px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
    ":where(img){max-width:100%;height:auto}"
    "#capsule-view-bar{all:initial;display:block!important;position:sticky;top:0;"
    "z-index:2147483647;background:#f6efe2;color:#3d2f17;border-bottom:1px solid #d9c8a6;"
    "padding:.55rem 1rem;margin:0 -1rem 1rem;font:13px/1.45 system-ui,-apple-system,"
    "'Segoe UI',Roboto,sans-serif}"
    "#capsule-view-bar a{color:#7a5c2e;font-weight:600;margin-left:.6rem}"
)
# Resource hints fetch on their own, script or not; drop them from the cleaned view. Matched
# within one already-delimited tag, so the pattern never runs over the page.
_HINT_RE = re.compile(
    r"\brel\s*=\s*[\"']?[^\"'>]*\b(?:prefetch|preconnect|dns-prefetch|preload|"
    r"modulepreload|prerender|compression-dictionary)\b",
    re.I,
)
_TAG_END = " \t\n\r\f/>"


def _human_size(size: float) -> str:
    if size < 1024:
        return f"{int(size):,} bytes"
    size /= 1024
    if size < 1024:
        return f"{size:.1f} KB"
    return f"{size / 1024:.1f} MB"


def _label(rel: str) -> str:
    if rel in _FILE_LABELS:
        return _FILE_LABELS[rel]
    if rel.lower().endswith(".mhtml"):
        return "Full page (.mhtml)"
    return _DIR_LABELS.get(rel.split("/", 1)[0], "")


def _file_url(capture_id: str, rel: str) -> str:
    """A path from the server root: `/<capture>/media/0-record.pdf`."""
    parts = "/".join(esc(part) for part in rel.split("/"))
    return f"/{esc(capture_id)}/{parts}"


def _page(title: str, css: str, body: str, script: str = "") -> str:
    page = (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{esc(title)} · Genealogy Capsule</title>\n"
        f"<style>{INDEX_CSS}{css}</style>\n</head>\n<body>\n{body}\n"
        + (f"<script>{script}</script>\n" if script else "")
        + "</body>\n</html>\n"
    )
    # JSON may spell a lone surrogate (`"\\ud800"`), which cannot be sent as UTF-8: show
    # it as "?" rather than fail the page.
    return page.encode("utf-8", "replace").decode("utf-8")


def _title(record: CaptureRecord | None, capture_id: str) -> str:
    if record is None:
        return capture_id
    for candidate in (record.primary_persona, record.title, record.record_type):
        if _text(candidate):
            return _text(candidate)
    return capture_id


# -- the folder page ---------------------------------------------------------------------

def _media_section(capture_id: str, manifest: dict, files: list[str]) -> str:
    listed = manifest.get("media")
    described = {
        m["path"]: m
        for m in (listed if isinstance(listed, list) else [])
        if isinstance(m, dict) and isinstance(m.get("path"), str)
    }
    # The full page has its own section below.
    media = [rel for rel in files if rel.startswith("media/") and not rel.lower().endswith(".mhtml")]
    # A document capture's PDF is the record itself: show it first.
    primary = _dict(manifest.get("archive")).get("local_snapshot")
    if isinstance(primary, str) and primary.lower().endswith(".pdf") and primary in files:
        media.insert(0, primary)
    if not media:
        return ""
    pdfs, figures, others = [], [], []
    for rel in media:
        url = _file_url(capture_id, rel)
        info = described.get(rel) or {}
        bits = [esc(rel.rsplit("/", 1)[-1])]
        how = _text(info.get("provenance"))
        if how:
            bits.append(esc(_PROVENANCE.get(how, how)))
        if all(isinstance(info.get(k), int) and not isinstance(info.get(k), bool)
               for k in ("width", "height")):
            bits.append(f'{info["width"]}×{info["height"]}')
        caption = " · ".join(bits)
        ext = Path(rel).suffix.lower()
        if ext == ".pdf":
            pdfs.append(
                f'<figure class="pdf"><iframe src="{url}" title="{esc(rel)}" loading="lazy">'
                f"</iframe><figcaption>{caption} · "
                f'<a href="{url}" target="_blank" rel="noopener">Open in its own tab</a>'
                "</figcaption></figure>"
            )
        elif ext in _IMAGE_EXT:
            figures.append(
                f'<figure><a href="{url}" target="_blank" rel="noopener">'
                f'<img src="{url}" alt="{esc(rel)}" loading="lazy"></a>'
                f"<figcaption>{caption}</figcaption></figure>"
            )
        else:
            others.append(f'<li><a href="{url}">{esc(rel)}</a> · {caption}</li>')
    grid = f'<div class="media">{"".join(figures)}</div>' if figures else ""
    rest = f'<ul>{"".join(others)}</ul>' if others else ""
    return f'<section class="card"><h2>Media</h2>{"".join(pdfs)}{grid}{rest}</section>'


def _page_copies_section(capture_id: str, files: list[str]) -> str:
    """The two copies of the page besides its PDF, each under the one name used everywhere:
    the Full page (`.mhtml`, the complete copy) and the Page text (HTML) (`snapshot.html`,
    the markup Capsule reads facts from)."""
    full = next((rel for rel in files if rel.lower().endswith(".mhtml")), None)
    cards = []
    if full:
        url = _file_url(capture_id, full)
        cards.append(
            '<section class="card"><h2>Full page</h2>'
            '<p class="lead">The complete copy of the page as you saw it, with its pictures and '
            "layout, in one file. Shown here from that file alone: ads are hidden, nothing runs, "
            "and nothing is loaded from the site. The downloaded .mhtml opens in Chrome or Edge."
            f'</p><p><a class="btn" href="{url}?view">Open the full page</a>'
            f'<a class="btn" href="{url}">Download .mhtml</a></p></section>'
        )
    if "snapshot.html" in files:
        url = _file_url(capture_id, "snapshot.html")
        lead = (
            "The page's text and markup alone: what Capsule reads facts from. Its pictures and "
            "styling lived on the site, so it looks plain; the full page above is the one to look at."
            if full
            else "The page's text and markup alone: what Capsule reads facts from. Shown for "
            "reading with scripts, ads and pop-ups hidden and nothing loaded from the site, so "
            "pictures that lived on the site show as missing; a PDF under Media is the page as "
            "you saw it. The file itself is never changed."
        )
        cards.append(
            f'<section class="card"><h2>Page text (HTML)</h2><p class="lead">{lead}</p>'
            f'<p><a class="btn" href="{url}?view">Open the page text</a>'
            f'<a class="btn" href="{url}">Raw file</a></p></section>'
        )
    return "".join(cards)


def _about_section(manifest: dict, result: dict) -> str:
    source = _dict(manifest.get("source"))
    archive = _dict(manifest.get("archive"))
    pairs: list[tuple[str, str]] = []
    quality = _text(source.get("source_quality"))
    if quality:
        why = _text(source.get("source_quality_rationale"))
        pairs.append(("Kind of source", esc(quality) + (f" — {esc(why)}" if why else "")))
    if _text(source.get("page_label")):
        pairs.append(("Page says", esc(_text(source["page_label"]))))
    claims = manifest.get("provider_claims")
    for claim in claims if isinstance(claims, list) else []:
        if isinstance(claim, dict) and _text(claim.get("value")):
            key = _text(claim.get("key")).replace("_", " ").capitalize()
            pairs.append((esc(key or "Site says"), esc(_text(claim["value"]))))
    if _text(result.get("image_status")):
        pairs.append(("Record image", esc(_text(result["image_status"]))))
    if _text(archive.get("sha256")):
        primary = _text(archive.get("local_snapshot"))
        what = _label(primary) or primary or "Primary file"
        pairs.append((esc(f"{what} fingerprint"), f'<code>{esc(_text(archive["sha256"]))}</code> (SHA-256)'))
    if _text(manifest.get("spec_version")):
        made = esc(_text(manifest.get("tool_version")) or "?")
        pairs.append(("Format", f'{esc(_text(manifest["spec_version"]))} · Capsule {made}'))
    if not pairs:
        return ""
    rows = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in pairs)
    return f'<section class="card"><h2>About this capture</h2><dl class="about">{rows}</dl></section>'


def _files_section(capture_id: str, folder: Path, files: list[str]) -> str:
    rows = []
    for rel in files:
        url = _file_url(capture_id, rel)
        try:
            size = _human_size((folder / rel).stat().st_size)
        except OSError:
            size = "—"
        buttons = (
            f'<a class="btn" href="{url}?view">Readable</a><a class="btn" href="{url}">Raw</a>'
            if Path(rel).suffix.lower() in _VIEWABLE
            else f'<a class="btn" href="{url}">Open</a>'
        )
        rows.append(
            f"<tr><td>{esc(_label(rel))}</td><td><code>{esc(rel)}</code></td>"
            f'<td class="size">{size}</td><td class="open">{buttons}</td></tr>'
        )
    return (
        '<section class="card"><h2>Files</h2><table class="files"><thead><tr><th>What</th>'
        "<th>File</th><th>Size</th><th></th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table></section>'
    )


def list_files(folder: Path) -> list[str]:
    """Every file in the capture folder, as folder-relative POSIX paths, sorted."""
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def _dict(value: object) -> dict:
    """A manifest part as a dict, or empty: a damaged capture must still get its page."""
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    """A manifest value to show, as text; anything that is not text or a number is left out."""
    if isinstance(value, bool):
        return ""
    return str(value) if isinstance(value, (str, int, float)) else ""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _detail_section(record: CaptureRecord, capture_id: str) -> str:
    # The detail's own links are written for the index at the server root (`./<id>/`).
    return (
        '<section class="card" id="detail">'
        + detail_body(record, interactive=False).replace(' href="./', ' href="/')
        + '<p class="lead">To correct a fact, use ✎ on this source in the '
        f'<a href="/#cap-{esc(capture_id)}">index</a>.</p></section>'
    )


def render_folder_page(record: CaptureRecord | None, folder: Path) -> str:
    """The page for one capture folder. `record` is None when the folder cannot be read as
    a capture (a damaged manifest): the files are then listed on their own."""
    capture_id = folder.name
    files = list_files(folder)
    manifest = _read_json(folder / "manifest.json")
    result = _read_json(folder / "result.json")
    title = _title(record, capture_id)
    site = _text(record.site_name) if record else ""
    when = _text(record.captured_at) if record else ""
    meta = " · ".join(esc(x) for x in (site, f"captured {when}" if when else "") if x)
    def fenced(section, *args) -> str:
        # A capture is someone's evidence: one malformed part leaves its section out, never
        # the whole page (the file list below always renders).
        try:
            return section(*args)
        except Exception as exc:
            _logger.warning("capture %s: %s left out: %r", capture_id, section.__name__, exc)
            return ""

    detail = fenced(_detail_section, record, capture_id) if record else ""
    if not detail:
        detail = (
            '<section class="card"><p class="lead">This folder could not be read as a '
            "capture; its files are listed below.</p></section>"
        )
    body = (
        "<header>"
        f'<p class="crumb"><a href="/#cap-{esc(capture_id)}">← All sources</a></p>'
        f"<h1>{esc(title)}</h1>"
        f'<p class="meta">{meta}{" · " if meta else ""}<code>{esc(capture_id)}</code></p>'
        "</header>"
        '<main class="folder">'
        + detail
        + fenced(_media_section, capture_id, manifest, files)
        + fenced(_page_copies_section, capture_id, files)
        + fenced(_about_section, manifest, result)
        + _files_section(capture_id, folder, files)
        + "</main>"
    )
    return _page(title, FOLDER_CSS, body, INDEX_COPY_JS)


# -- the readable JSON view ----------------------------------------------------------------

_LONG = 600  # characters: longer text is folded behind its first line
# Machine keys (`id`, `source_id`, …) are 32 hex characters that mean nothing to a reader:
# shown short, the full value on hover, so a table keeps room for what the record says.
_ID_RE = re.compile(r"\A[0-9a-f]{16,}\Z")


def _key(name: object) -> str:
    text = str(name).replace("_", " ").strip()
    return esc(text[:1].upper() + text[1:])


def _scalar(value: Any, depth: int, key: str = "") -> str:
    if (key == "id" or key.endswith("_id")) and isinstance(value, str) and _ID_RE.match(value):
        return f'<code title="{esc(value)}">{esc(value[:8])}…</code>'
    if value is None or value == "":
        return '<span class="jv-none">—</span>'
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f'<span class="jv-num">{value}</span>'
    text = str(value)
    link = href(text) if text.startswith(("http://", "https://")) else None
    if link and "\n" not in text and len(text) < 2000:
        return f'<a href="{link}" rel="noreferrer">{esc(text)}</a>'
    if len(text) <= _LONG and "\n" not in text:
        return esc(text)
    # A model's raw reply is often JSON in a string: show it as structure.
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            return _value(json.loads(stripped), depth)
        except ValueError:
            pass
    first = esc(stripped.splitlines()[0][:160]) if stripped else ""
    return (
        f"<details><summary>{first}… ({len(text):,} characters)</summary>"
        f'<div class="jv-long">{esc(text)}</div></details>'
    )


_MAX_COLUMNS = 24  # a capture file's records have about a dozen fields
_MAX_CELLS = 20_000


def _columns(items: list) -> list[str] | None:
    """The columns of a list of objects that share their fields, or None when the list does
    not read as a table. Sparse lists (each row with its own fields) are refused, as are
    tables too large to show: a small file must not become an enormous page."""
    if len(items) < 2 or not all(isinstance(i, dict) and i for i in items):
        return None
    first = set(items[0])
    if not all(len(first & set(i)) * 2 >= len(i) for i in items):
        return None
    columns: list[str] = []
    seen: set[str] = set()
    for item in items:
        for key in item:
            if key not in seen:
                if len(columns) == _MAX_COLUMNS:
                    return None
                seen.add(key)
                columns.append(key)
    return columns if len(columns) * len(items) <= _MAX_CELLS else None


_MAX_DEPTH = 40  # deeper than any capture file nests; past it, the rest is shown as text


def _value(value: Any, depth: int, key: str = "") -> str:
    if depth > _MAX_DEPTH and isinstance(value, (dict, list)):
        return f'<div class="jv-long">{esc(json.dumps(value, ensure_ascii=False))}</div>'
    if isinstance(value, dict):
        if not value:
            return '<span class="jv-none">—</span>'
        rows = "".join(
            f"<dt>{_key(k)}</dt><dd>{_value(v, depth + 1, str(k))}</dd>" for k, v in value.items()
        )
        return f'<dl class="jv">{rows}</dl>'
    if isinstance(value, list):
        if not value:
            return '<span class="jv-none">none</span>'
        columns = _columns(value)
        if columns:
            head = "".join(f"<th>{_key(c)}</th>" for c in columns)
            rows = "".join(
                "<tr>"
                + "".join(f"<td>{_value(item.get(c), depth + 1, c)}</td>" for c in columns)
                + "</tr>"
                for item in value
            )
            inner = (
                f'<table class="jv-table"><thead><tr>{head}</tr></thead>'
                f"<tbody>{rows}</tbody></table>"
            )
        else:
            items = "".join(f"<li>{_value(v, depth + 1)}</li>" for v in value)
            inner = f'<ul class="jv-list">{items}</ul>'
        if depth == 0:
            return inner
        noun = "item" if len(value) == 1 else "items"
        opened = " open" if len(value) <= 12 else ""
        return f"<details{opened}><summary>{len(value)} {noun}</summary>{inner}</details>"
    return _scalar(value, depth, key)


def render_json_view(capture_id: str, rel: str, raw: str, record: CaptureRecord | None) -> str:
    """A readable page for one JSON file of a capture; unreadable JSON is shown as text."""
    try:
        content = _value(json.loads(raw), 0)
    except (ValueError, RecursionError):
        content = (
            '<p class="lead">This file is not valid JSON; its text:</p>'
            f'<div class="jv-long">{esc(raw)}</div>'
        )
    label = _label(rel) or rel
    url = _file_url(capture_id, rel)
    body = (
        "<header>"
        f'<p class="crumb"><a href="/{esc(capture_id)}/">← {esc(_title(record, capture_id))}'
        "</a></p>"
        f"<h1>{esc(label)}</h1>"
        f'<p class="meta"><code>{esc(rel)}</code> · <a href="{url}">Raw JSON</a></p>'
        "</header>"
        f'<main class="jv-wrap">{content}</main>'
    )
    return _page(f"{label} · {capture_id}", JSON_VIEW_CSS, body)


# -- the cleaned view of the page text (HTML) --------------------------------------------

def _clean(raw: str) -> tuple[str, int, int]:
    """Drop `<script>` elements and resource-hint `<link>`s, in one forward pass.

    Captured pages are hostile input, so no regex runs over the page (a backtracking pattern
    over a crafted page could stall the backend): every search moves forward from the last
    one. Returns the cleaned page and where the content after `<head>` and after `<body>`
    starts in it (-1 when there is no such tag)."""
    low = ascii_lower(raw)
    out: list[str] = []
    size = 0
    head_at = body_at = -1
    i = 0
    while True:
        start = low.find("<", i)
        if start < 0:
            out.append(raw[i:])
            break
        name_end = start + 1
        while name_end < len(low) and low[name_end] not in _TAG_END and name_end - start < 12:
            name_end += 1
        name = low[start + 1:name_end]
        close = low.find(">", start)
        if close < 0:
            out.append(raw[i:])
            break
        if name == "script":
            out.append(raw[i:start])
            size += start - i
            end = low.find("</script", close)
            if end < 0:  # an unclosed script runs to the end of the page in a browser too
                break
            after = low.find(">", end)
            i = after + 1 if after >= 0 else len(raw)
            if after < 0:
                break
            continue
        # A real <link> is short; a giant one is left alone (sandboxed, it loads nothing).
        if name == "link" and close - start < 4096 and _HINT_RE.search(raw, start, close):
            out.append(raw[i:start])
            size += start - i
            i = close + 1
            continue
        out.append(raw[i:close + 1])
        size += close + 1 - i
        if name == "head" and head_at < 0:
            head_at = size
        elif name == "body" and body_at < 0:
            body_at = size
        i = close + 1
    return "".join(out), head_at, body_at


def render_snapshot_view(capture_id: str, rel: str, raw: str) -> str:
    """The page text (HTML) with its scripts, ads, pop-ups and prefetches hidden or dropped, and
    a bar that says so. Served sandboxed like the raw file; the file is not changed."""
    html, head_at, body_at = _clean(raw)
    url = _file_url(capture_id, rel)
    style = f"<style>{_SNAPSHOT_CSS}</style>"
    bar = (
        '<div id="capsule-view-bar">Page text (HTML), shown for reading: scripts, ads and '
        "pop-ups are hidden and nothing is loaded from the site, so its pictures do not appear."
        f'<a href="/{esc(capture_id)}/">Back to the capture</a><a href="{url}">Raw file</a></div>'
    )
    if body_at < 0:
        return style + bar + html
    if head_at < 0 or head_at > body_at:
        return html[:body_at] + style + bar + html[body_at:]
    return html[:head_at] + style + html[head_at:body_at] + bar + html[body_at:]
