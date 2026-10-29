"""REQ-015 — readable pages for one capture folder.

The folder page shows the record as the index does plus everything the folder holds; any
JSON file and the page text (HTML) have a readable view. All of it only reads the folder: the
files are sealed evidence, so a view must never change a byte of them, and everything it
shows from a capture (the site's text, the model's output) is escaped.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from capsule.backend.app import Settings, app, get_settings

_TOKEN = "test-secret"
_PDF = b"%PDF-1.4\n1 0 obj Parish register extract\n%%EOF"


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        db_path=tmp_path / "capsule.db", captures_root=tmp_path / "captures", token=_TOKEN
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _capture(client: TestClient) -> str:
    resp = client.post(
        "/capture",
        headers={"X-Capsule-Token": _TOKEN},
        json={
            "url": "https://archive.example.org/register.pdf",
            "document": {
                "data_base64": base64.b64encode(_PDF).decode("ascii"),
                "filename": "register.pdf",
                "mime": "application/pdf",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    return Path(resp.json()["capture_folder"]).name


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_folder_page_shows_the_record_its_media_and_every_file(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture(client)
        folder = tmp_path / "captures" / capture_id
        (folder / "media").mkdir(exist_ok=True)
        (folder / "media" / "1-stone.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
        page = client.get(f"/{capture_id}/")
        assert page.status_code == 200
        html = page.text
        # The record as the index shows it: citation, Copy buttons, and the way back.
        assert 'class="d-cite"' in html and 'class="copy-ai"' in html
        assert f'href="/#cap-{capture_id}"' in html
        # The document capture's PDF is shown in place, and the image too.
        assert f'<iframe src="/{capture_id}/document.pdf"' in html
        assert f'<img src="/{capture_id}/media/1-stone.jpg"' in html
        # Every file listed; JSON files get a readable view and a raw link.
        assert f'href="/{capture_id}/manifest.json?view">Readable</a>' in html
        assert f'href="/{capture_id}/manifest.json">Raw</a>' in html
        # The page is the backend's own markup: nothing loads from outside the archive.
        csp = page.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "img-src 'self' data:" in csp
        # Unknown and malformed captures are refused, as for files.
        assert client.get("/20260711-example-deadbeef/").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_a_json_file_reads_as_tables_and_fields_and_its_text_is_escaped(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture(client)
        path = tmp_path / "captures" / capture_id / "extraction.json"
        path.write_text(json.dumps({
            "personas": [
                {"id": "a" * 32, "label": "Mary <b>Doyle</b>", "role": "deceased"},
                {"id": "b" * 32, "label": "John Doyle", "role": "spouse"},
            ],
            "notes": "<script>alert(1)</script>",
            "source_url": "javascript:alert(1)",
        }), encoding="utf-8")
        before = _digest(path)
        resp = client.get(f"/{capture_id}/extraction.json?view")
        assert resp.status_code == 200
        html = resp.text
        assert "<th>Label</th>" in html and "<td>Mary &lt;b&gt;Doyle&lt;/b&gt;</td>" in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>alert" not in html
        assert 'href="javascript:' not in html
        # A 32-character key is shown short, with the full value on hover.
        assert f'<code title="{"a" * 32}">aaaaaaaa…</code>' in html
        assert f'href="/{capture_id}/extraction.json">Raw JSON</a>' in html
        assert resp.headers["content-security-policy"].startswith("sandbox;")
        # The raw file is still the raw file, unchanged.
        assert client.get(f"/{capture_id}/extraction.json").json()["notes"].startswith("<script>")
        assert _digest(path) == before
    finally:
        app.dependency_overrides.clear()


def test_the_page_text_reads_without_scripts_ads_or_prefetches(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture(client)
        path = tmp_path / "captures" / capture_id / "snapshot.html"
        path.write_text(
            '<html><head><link rel="dns-prefetch" href="//ads.example">'
            '<link href="https://ads.example/dict" rel="compression-dictionary">'
            '<script src="https://ads.example/tag.js"></script></head>'
            '<body><div id="div-gpt-ad-1">AD</div><h1>Mary Doyle</h1>'
            "<script>track()</script></body></html>",
            encoding="utf-8",
        )
        before = _digest(path)
        resp = client.get(f"/{capture_id}/snapshot.html?view")
        assert resp.status_code == 200
        html = resp.text
        assert "<script" not in html and "ads.example" not in html
        assert "<h1>Mary Doyle</h1>" in html
        assert '[id^="div-gpt-ad"]' in html  # hidden by the view's stylesheet, not deleted
        assert 'id="capsule-view-bar"' in html and f'href="/{capture_id}/snapshot.html">Raw' in html
        assert resp.headers["content-security-policy"].startswith("sandbox;")
        # The raw file still holds everything it was saved with.
        assert "track()" in client.get(f"/{capture_id}/snapshot.html").text
        assert _digest(path) == before
    finally:
        app.dependency_overrides.clear()


def test_a_capture_with_a_damaged_manifest_still_lists_its_files(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture(client)
        manifest = tmp_path / "captures" / capture_id / "manifest.json"
        damaged = json.loads(manifest.read_text(encoding="utf-8"))
        damaged.update(source="damaged", archive=[1], media="x", provider_claims={"a": 1})
        manifest.write_text(json.dumps(damaged), encoding="utf-8")
        page = client.get(f"/{capture_id}/")
        assert page.status_code == 200
        assert "document.pdf" in page.text and "manifest.json" in page.text
        # Wrong types one level down: numbers, lists and objects where text belongs.
        damaged.update(
            source={"page_label": 42, "source_quality": ["x"], "source_quality_rationale": {}},
            archive={"sha256": [], "local_snapshot": 7},
            media=[{"path": []}, {"path": "media/a.png", "provenance": {}, "width": True}],
            provider_claims=[{"key": [], "value": {"a": 1}}],
            spec_version={"v": 3},
        )
        manifest.write_text(json.dumps(damaged), encoding="utf-8")
        (tmp_path / "captures" / capture_id / "result.json").write_text(
            json.dumps({"image_status": ["full"]}), encoding="utf-8")
        page = client.get(f"/{capture_id}/")
        assert page.status_code == 200
        assert "document.pdf" in page.text and "manifest.json" in page.text
        # And in what the page's header reads: the site and the title.
        for bad in (42, ["x"], {"a": 1}):
            damaged.update(source={"site_name": bad, "title": bad, "url": "https://x.example/"})
            manifest.write_text(json.dumps(damaged), encoding="utf-8")
            page = client.get(f"/{capture_id}/")
            assert page.status_code == 200, bad
            assert "manifest.json" in page.text
            assert client.get(f"/{capture_id}/manifest.json?view").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_deeply_nested_or_broken_json_still_reads(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture(client)
        folder = tmp_path / "captures" / capture_id
        for name, text in (
            ("deep.json", "[" * 350 + "0" + "]" * 350),
            ("deeper.json", "[" * 100_000 + "]" * 100_000),
            ("surrogate.json", '{"text": "\\ud800", "\\udc00": 1}'),
            ("broken.json", '{"a": <b>'),
        ):
            (folder / name).write_text(text, encoding="utf-8")
            resp = client.get(f"/{capture_id}/{name}?view")
            assert resp.status_code == 200, name
        assert "{&quot;a&quot;: &lt;b&gt;" in resp.text
    finally:
        app.dependency_overrides.clear()


def test_a_deeply_nested_manifest_still_lists_the_files(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        capture_id = _capture(client)
        folder = tmp_path / "captures" / capture_id
        deep = "[" * 100_000 + "]" * 100_000
        (folder / "result.json").write_text(deep, encoding="utf-8")
        (folder / "manifest.json").write_text(deep, encoding="utf-8")
        page = client.get(f"/{capture_id}/")
        assert page.status_code == 200
        assert "document.pdf" in page.text
    finally:
        app.dependency_overrides.clear()


def test_cleaning_hostile_page_text_takes_linear_time() -> None:
    """A page can hold thousands of unclosed `<script>`, `<link` or `<body` openings; a
    backtracking pattern rescanned the page at each one (15 s for 256 KB)."""
    import time

    from capsule.collection.folder import render_snapshot_view

    for chunk in ("<!--<script>-->", "<link rel=x ", "<body", "<head <body ", "<a>"):
        page = "<html><head></head><body><p>record</p>" + chunk * 40_000
        if chunk == "<a>":
            page += "<link " + "rel=a " * 40_000 + ">"
        began = time.perf_counter()
        render_snapshot_view("20260711-example-deadbeef", "snapshot.html", page)
        assert time.perf_counter() - began < 2, chunk


def test_a_sparse_list_is_not_blown_up_into_a_huge_table() -> None:
    """Rows that share one key and each add their own once made a table with a column per
    row: 60 KB of JSON became 156 MB of HTML."""
    from capsule.collection.folder import render_json_view

    rows = [{"shared": 1, f"field{n}": 1} for n in range(2_000)]
    page = render_json_view("20260711-example-deadbeef", "x.json", json.dumps(rows), None)
    assert len(page) < 2_000_000
    assert "<table" not in page
