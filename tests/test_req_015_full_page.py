"""REQ-015 §C/§D — ads hidden from the render, and the whole page saved as MHTML.

The render hides ads by the conventions ad networks use and nothing broader (a certificate
site shows the record in an <embed> or <iframe>). The full page rides as one more media
item, last, never at the cost of the record. The backend recognizes it by its bytes, never
counts it as the record image, and unpacks it into one readable page that loads nothing.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from capsule.collection import mhtml
from capsule.collection.mhtml import parse, render_mhtml_view
from capsule.pipeline.sniff import sniff

_ROOT = Path(__file__).resolve().parent.parent
_RENDER_JS = _ROOT / "extension" / "render-pdf.js"
_SAMPLE = _ROOT / "tests" / "fixtures" / "mhtml" / "blink-sample.mhtml"
_ID = "20261029-example-deadbeef"
_REL = "media/1-page.mhtml"


def _mhtml(*parts: tuple[str, str, bytes], page: str = "https://records.example/page") -> bytes:
    """A minimal MHTML file: (content type, location, body) per part, base64-encoded."""
    boundary = "----MultipartBoundary--test----"
    out = [
        "From: <Saved by Blink>",
        f"Snapshot-Content-Location: {page}",
        "MIME-Version: 1.0",
        f'Content-Type: multipart/related; type="text/html"; boundary="{boundary}"',
        "",
    ]
    for ctype, location, body in parts:
        out += [
            f"--{boundary}",
            f"Content-Type: {ctype}",
            "Content-Transfer-Encoding: base64",
            f"Content-Location: {location}",
            "",
            base64.b64encode(body).decode("ascii"),
        ]
    out.append(f"--{boundary}--")
    return "\r\n".join(out).encode("ascii")


# -- the render (extension) -------------------------------------------------------------

def _ad_selectors() -> list[str]:
    source = _RENDER_JS.read_text(encoding="utf-8")
    block = re.search(r"const AD_SELECTORS = \[(.*?)\];", source, re.S)
    assert block, "AD_SELECTORS moved"
    return re.findall(r"""'([^']+)'|"([^"]+)\"""", block.group(1))


def test_the_ad_list_never_reaches_record_content() -> None:
    """Certificate sites show the record in an <embed>/<object>/<iframe>; a dialog or modal
    can hold a record too. Hiding any of those wholesale would erase evidence from the PDF
    and the full page, so the live ad list may name ad conventions only."""
    selectors = ["".join(pair) for pair in _ad_selectors()]
    assert '[id^="div-gpt-ad"]' in selectors and "ins.adsbygoogle" in selectors
    for selector in selectors:
        for broad in ("iframe", "object", "embed", "dialog", "modal", "noscript"):
            assert broad not in selector.lower(), f"{selector!r} hides {broad} wholesale"
    source = _RENDER_JS.read_text(encoding="utf-8")
    # An iframe goes only when its host is an ad network.
    assert "AD_FRAME_HOSTS.test(host)" in source and "doubleclick" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_full_page_goes_last_and_only_within_the_backends_limits() -> None:
    script = _RENDER_JS.read_text(encoding="utf-8") + """
    const page = {data_base64: "QUJD", filename: "page.mhtml", mime: "multipart/related",
                  provenance: "render:page-mhtml"};
    const out = {};
    let media = [{data_base64: "AAAA"}];
    out.fits = [appendFullPage(media, {page}), media.length, media[1] === page];
    media = Array.from({length: 25}, () => ({data_base64: "AAAA"}));
    out.full = [appendFullPage(media, {page}), media.length];
    media = [{data_base64: {length: 140 * 1024 * 1024}}];
    out.big = [appendFullPage(media, {page}), media.length];
    out.none = appendFullPage([], {page: null, page_skipped: "timed out"});
    console.log(JSON.stringify(out));
    """
    proc = subprocess.run(
        ["node", "-e", "globalThis.chrome = {};\n" + script],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["fits"] == [None, 2, True]
    assert out["full"][1] == 25 and "25 files" in out["full"][0]
    assert out["big"][1] == 1 and "100 MiB" in out["big"][0]
    assert out["none"] == "timed out"


def test_the_full_page_is_taken_after_the_pdf_while_ads_are_hidden() -> None:
    source = _RENDER_JS.read_text(encoding="utf-8")
    pdf = source.index('"Page.printToPDF"')
    snap = source.index('"Page.captureSnapshot"')
    restore = source.index("evaluateInPage(target, RESTORE_EXPRESSION)")
    assert pdf < snap < restore
    assert "provenance: PAGE_ARCHIVE_PROVENANCE" in source
    assert 'const PAGE_ARCHIVE_PROVENANCE = "render:page-mhtml"' in source
    background = (_ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "appendFullPage(o.media, o.render)" in background
    popup = (_ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
    assert "appendFullPage(media, rendered)" in popup


# -- the backend ------------------------------------------------------------------------

def test_the_bytes_say_mhtml_and_an_ordinary_email_is_not_one() -> None:
    assert sniff(_SAMPLE.read_bytes()) == ("mhtml", "multipart/related")
    email = b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\n"
    assert sniff(email) is None
    related = b"MIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=x\r\n\r\n"
    assert sniff(related) is None  # not a page snapshot without Snapshot-Content-Location


def test_a_capture_keeps_the_full_page_but_never_as_the_record(tmp_path: Path) -> None:
    from tests.test_backend import _AUTH, _NYC_URL, _PAGE, _RAW, _client

    client, backend = _client(tmp_path, json.dumps(_RAW))
    try:
        resp = client.post("/capture", json={
            "url": _NYC_URL, "html": _PAGE,
            "media": [{
                "data_base64": base64.b64encode(_SAMPLE.read_bytes()).decode(),
                "filename": "page.mhtml", "mime": "multipart/related",
                "provenance": "render:page-mhtml",
            }],
        }, headers=_AUTH)
        assert resp.status_code == 200, resp.text
        folder = Path(resp.json()["capture_folder"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        entry = next(m for m in manifest["media"] if m["provenance"] == "render:page-mhtml")
        assert entry["path"].endswith("page.mhtml") and entry["mime"] == "multipart/related"
        capture_id = folder.name
        page = client.get(f"/{capture_id}/").text
        assert f'href="/{capture_id}/{entry["path"]}?view">Open the full page' in page
        # The index's details pane links it too: open (unpacked) or download.
        index = client.get("/").text
        url = f"./{capture_id}/{entry['path']}"
        assert f'<dt>Full page</dt><dd><a href="{url}?view">Open</a>' in index
        assert f'<a href="{url}">Download .mhtml</a>' in index
        # The static index.html (opened from disk) links the file, which Chrome opens there.
        from datetime import datetime, timezone

        from capsule.collection.index import render_index
        from capsule.collection.read import read_all

        static = render_index(read_all(folder.parent), generated_at=datetime.now(timezone.utc))
        assert f'<dt>Full page</dt><dd><a href="{url}">Open the full page</a>' in static
        view = client.get(f"/{capture_id}/{entry['path']}?view")
        assert view.status_code == 200
        assert 'href="data:text/css;charset=utf-8;base64,' in view.text
        csp = view.headers["content-security-policy"]
        assert csp.startswith("sandbox;") and "default-src 'none'" in csp and "'self'" not in csp
        raw = client.get(f"/{capture_id}/{entry['path']}")
        assert raw.content == _SAMPLE.read_bytes()
        assert "attachment" in raw.headers["content-disposition"]
    finally:
        backend.app.dependency_overrides.clear()


def test_chromes_own_file_unpacks_into_one_page() -> None:
    url, parts = parse(_SAMPLE.read_bytes())
    assert url == "http://127.0.0.1:8899/index.html"
    assert {p.mime for p in parts} >= {"text/html", "text/css", "image/png", "font/woff2"}
    page = render_mhtml_view(_ID, _REL, _SAMPLE.read_bytes())
    assert 'src="data:image/png;base64,' in page
    css = re.search(r'href="data:text/css;charset=utf-8;base64,([^"]+)"', page)
    assert css and "data:font/woff2;base64," in base64.b64decode(css.group(1)).decode()
    assert "lazy.png" in page  # never loaded, so not in the file: left as a dead link
    assert 'id="capsule-view-bar"' in page and "<script" not in page


def test_scripts_hints_and_srcset_go_and_the_loaded_picture_is_shown() -> None:
    html = (
        b'<html><head><link rel="preconnect" href="https://ads.example">'
        b"<script>steal()</script><style>p{background:url(bg.png)}</style></head>"
        b'<body><img src="x.png" srcset="small.png 1x, big.png 2x">'
        b"<p>Mary Doyle</p><script src=a.js></script></body></html>"
    )
    data = _mhtml(
        ("text/html", "https://records.example/page", html),
        ("image/png", "https://records.example/big.png", b"\x89PNG big"),
        ("image/png", "https://records.example/bg.png", b"\x89PNG bg"),
    )
    page = render_mhtml_view(_ID, _REL, data)
    assert "steal()" not in page and "a.js" not in page and "preconnect" not in page
    assert "srcset" not in page
    big = base64.b64encode(b"\x89PNG big").decode()
    assert f'src="data:image/png;base64,{big}"' in page
    assert 'background:url("data:image/png;base64,' in page
    assert "<p>Mary Doyle</p>" in page


def test_a_stylesheet_cannot_break_out_of_its_place() -> None:
    """Linked CSS is inlined as base64 in a data: URI, so a `</style><script>` inside it is
    never markup."""
    data = _mhtml(
        ("text/html", "https://records.example/page",
         b'<html><head><link rel=stylesheet href="evil.css"></head><body>x</body></html>'),
        ("text/css", "https://records.example/evil.css", b"</style><script>x()</script>"),
    )
    page = render_mhtml_view(_ID, _REL, data)
    assert "<script>x()" not in page and "data:text/css;charset=utf-8;base64," in page


def test_import_cycles_and_deep_chains_end() -> None:
    a = b'@import url("b.css"); p{color:red}'
    b = b'@import url("a.css"); @import url("c0.css");'
    chain = [(f"c{n}.css", f'@import url("c{n + 1}.css");'.encode()) for n in range(20)]
    data = _mhtml(
        ("text/html", "https://records.example/page",
         b'<html><head><link rel=stylesheet href="a.css"></head><body>x</body></html>'),
        ("text/css", "https://records.example/a.css", a),
        ("text/css", "https://records.example/b.css", b),
        *[("text/css", f"https://records.example/{name}", body) for name, body in chain],
    )
    began = time.perf_counter()
    page = render_mhtml_view(_ID, _REL, data)
    assert time.perf_counter() - began < 2
    assert "data:text/css" in page


def test_a_quoted_import_is_inlined_too() -> None:
    data = _mhtml(
        ("text/html", "https://records.example/page",
         b'<html><head><style>@import "theme.css" screen; p{}</style></head><body>x</body></html>'),
        ("text/css", "https://records.example/theme.css", b"h1{color:red}"),
    )
    page = render_mhtml_view(_ID, _REL, data)
    assert '@import url("data:text/css;charset=utf-8;base64,' in page and " screen;" in page
    # Unterminated or odd imports are left alone, quickly.
    began = time.perf_counter()
    render_mhtml_view(_ID, _REL, _mhtml(("text/html", "https://records.example/page",
        b"<style>" + b'@import "' * 60_000 + b"</style>")))
    assert time.perf_counter() - began < 2


def test_a_name_whose_lowercase_is_longer_does_not_shift_the_markup() -> None:
    """`"İ".lower()` is two characters: offsets found in a lower-cased copy then sliced the
    wrong place, and the picture after a Turkish name came out as `<<mg`."""
    from capsule.collection.folder import render_snapshot_view

    body = b"<html><head><style>p{background:url(bg.png)}</style></head>"
    body += "<body><p>İsmail İnce</p><img src=\"a.png\"><script>x()</script></body></html>".encode()
    data = _mhtml(
        ("text/html; charset=utf-8", "https://records.example/page", body),
        ("image/png", "https://records.example/a.png", b"\x89PNG a"),
        ("image/png", "https://records.example/bg.png", b"\x89PNG bg"),
    )
    page = render_mhtml_view(_ID, _REL, data)
    assert "<p>İsmail İnce</p><img src=\"data:image/png;base64," in page
    assert 'url("data:image/png;base64,' in page and "x()" not in page
    plain = render_snapshot_view(_ID, "snapshot.html", body.decode())
    assert "<p>İsmail İnce</p><img src=\"a.png\"></body>" in plain and "x()" not in plain


def test_a_quoted_css_url_may_hold_parentheses_and_escapes() -> None:
    css = (b'p{background:url("scan_(1).png")} h1{background:url( \'a\\\'b.png\' )}'
           b" h2{background:url(plain.png)} h3{background:url(esc_\\(2\\).png)}")
    data = _mhtml(
        ("text/html", "https://records.example/page",
         b"<html><head><style>" + css + b"</style></head><body>x</body></html>"),
        ("image/png", "https://records.example/scan_(1).png", b"\x89PNG 1"),
        ("image/png", "https://records.example/a'b.png", b"\x89PNG 2"),
        ("image/png", "https://records.example/plain.png", b"\x89PNG 3"),
        ("image/png", "https://records.example/esc_(2).png", b"\x89PNG 4"),
    )
    page = render_mhtml_view(_ID, _REL, data)
    assert page.count('url("data:image/png;base64,') == 4, page
    began = time.perf_counter()
    for body in (b'url("' * 60_000, b"url('x')" + b"url(" * 60_000, b'url("\\' * 60_000):
        render_mhtml_view(_ID, _REL, _mhtml(("text/html", "https://records.example/page",
            b"<style>" + body + b"</style>")))
    assert time.perf_counter() - began < 3


def test_branching_imports_are_expanded_once() -> None:
    """Four stylesheets each importing the next 25 times made the work grow as N^4 from
    under 2 KB of CSS; each stylesheet is now expanded once and references are capped."""
    sheets = [
        (f"s{n}.css", (f'@import url("s{n + 1}.css");' * 25).encode() if n < 4 else b"p{}")
        for n in range(5)
    ]
    data = _mhtml(
        ("text/html", "https://records.example/page",
         b'<html><head><link rel=stylesheet href="s0.css"></head><body>x</body></html>'),
        *[("text/css", f"https://records.example/{name}", body) for name, body in sheets],
    )
    began = time.perf_counter()
    render_mhtml_view(_ID, _REL, data)
    assert time.perf_counter() - began < 1


def test_a_part_too_big_for_what_is_left_is_refused_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pictures used most of the budget; then 1,000 links to a stylesheet too big for what
    was left converted it 1,000 times. A refusal is now remembered."""
    monkeypatch.setattr(mhtml, "MAX_OUTPUT_BYTES", 2_000_000)
    css = b"p{color:red}" * 200_000  # 2.4 MB
    links = b'<link rel=stylesheet href="big.css">' * 1_000
    data = _mhtml(
        ("text/html", "https://records.example/page", b"<html><head>" + links + b"</head></html>"),
        ("text/css", "https://records.example/big.css", css),
    )
    began = time.perf_counter()
    page = render_mhtml_view(_ID, _REL, data)
    assert time.perf_counter() - began < 1
    assert "data:text/css" not in page


def test_malformed_files_degrade_to_an_explanation() -> None:
    for data in (
        b"",
        b"not mhtml at all",
        b"From: <Saved by Blink>\r\nContent-Type: multipart/related\r\n\r\n",  # no boundary
        _mhtml()[:-20],  # no closing boundary, no parts
        b"From: <Saved by Blink>\r\nContent-Type: multipart/related; boundary=b\r\n\r\n"
        b"--b\r\nContent-Type: text/html\r\nContent-Transfer-Encoding: base64\r\n\r\n!!!\r\n--b--",
    ):
        page = render_mhtml_view(_ID, _REL, data)
        assert 'id="capsule-view-bar"' in page
    # Bytes that are not valid in the page's charset are replaced, never fatal.
    data = _mhtml(
        ("text/html; charset=utf-8", "https://records.example/page", b"<p>\xed\xa0\x80\xff</p>")
    )
    render_mhtml_view(_ID, _REL, data).encode("utf-8")


def test_part_count_and_page_size_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mhtml, "MAX_OUTPUT_BYTES", 400)
    images = [
        ("image/png", f"https://records.example/{n}.png", b"\x89PNG" + bytes([n]) * 90)
        for n in range(10)
    ]
    tags = "".join(f'<img src="{n}.png">' for n in range(10)).encode()
    data = _mhtml(("text/html", "https://records.example/page", b"<body>" + tags + b"</body>"), *images)
    page = render_mhtml_view(_ID, _REL, data)
    assert 0 < page.count("data:image/png") < 10  # inlined until the budget ran out
    # A picture used again and again is charged every time, not once.
    again = b"<body>" + b'<img src="0.png">' * 1_000 + b"</body>"
    page = render_mhtml_view(_ID, _REL, _mhtml(
        ("text/html", "https://records.example/page", again), images[0]))
    assert page.count("data:image/png") <= 4
    monkeypatch.setattr(mhtml, "MAX_PARTS", 3)
    _url, parts = parse(data)
    assert len(parts) == 3


def test_unpacking_a_hostile_file_takes_linear_time() -> None:
    for body in (
        b"<a " * 60_000,
        b'<p style="background:url(' * 30_000,
        b"<style>" + b"url(" * 60_000,
        b"<img src=" + b'"' * 60_000 + b">",
        b"<script>" * 60_000,
        b"<style>" * 60_000,
    ):
        data = _mhtml(("text/html", "https://records.example/page", body))
        began = time.perf_counter()
        render_mhtml_view(_ID, _REL, data)
        assert time.perf_counter() - began < 3, body[:20]
    many = "\r\n".join(["--b\r\nContent-Type: text/plain\r\n\r\nx"] * 50_000)
    data = (
        "From: <Saved by Blink>\r\nContent-Type: multipart/related; boundary=b\r\n\r\n" + many
    ).encode()
    began = time.perf_counter()
    parse(data)
    assert time.perf_counter() - began < 3
