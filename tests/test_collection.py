"""Offline tests for the Captures Index (spec: captures-index/v2).

Real capture folders are built with `write_capture` in `tmp_path` (so the tests
exercise the actual on-disk shape the reader parses), then read, rendered, and
written. Nothing here touches the network or spends tokens.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from capsule.capture.manifest import (
    Extraction,
    MediaItem,
    serialize_extraction,
    sha256_hex,
    write_capture,
)
from capsule.collection.escape import esc, href
from capsule.collection.index import (
    INDEX_FILENAME,
    LEGACY_INDEX_FILENAME,
    render_index,
    write_index,
)
from capsule.collection.read import read_all, read_capture_record
from capsule.contracts.evidence import (
    ArchiveKind,
    ArchiveRecord,
    Assertion,
    CaptureNote,
    Citation,
    EvidenceType,
    ExternalRef,
    GatingStatus,
    Persona,
    Snippet,
    SnippetAnchor,
    Source,
    SourceQuality,
)

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)


def _make_capture(
    dest_root: Path,
    *,
    source_id: str,
    url: str,
    site_name: str,
    record_type: str,
    persona_label: str,
    fields: Sequence[tuple[str, str]],
    gating: GatingStatus = GatingStatus.PUBLIC,
    media: Sequence[MediaItem] = (),
    captured_at: datetime = _DT,
    external_refs: Sequence[ExternalRef] = (),
) -> Path:
    persona = Persona(id="p1", source_id=source_id, role="deceased", label=persona_label)
    snippet = Snippet(
        id="s1",
        source_id=source_id,
        verbatim_text="verbatim",
        anchor=SnippetAnchor(kind="text", start=0, end=8),
    )
    assertions = tuple(
        Assertion(
            id=f"a{i}",
            source_id=source_id,
            persona_id="p1",
            snippet_id="s1",
            field=field,
            verbatim_value=value,
            evidence_type=EvidenceType.STATED,
            created_at=captured_at,
        )
        for i, (field, value) in enumerate(fields)
    )
    extraction = Extraction(
        personas=(persona,),
        snippets=(snippet,),
        assertions=assertions,
        external_refs=tuple(external_refs),
    )
    content_hash = sha256_hex(serialize_extraction(extraction))

    snapshot_html = f"<html><body>{persona_label}</body></html>"
    snap_digest = sha256_hex(snapshot_html.encode("utf-8"))
    archive = ArchiveRecord(
        local_snapshot="snapshot.html", sha256=snap_digest, captured_at=captured_at
    )
    source = Source(
        id=source_id,
        url=url,
        site_name=site_name,
        record_type=record_type,
        gating_status=gating,
        copyright_flag=(gating == GatingStatus.GATED),
        source_quality=SourceQuality.ORIGINAL,
        archive=archive,
        content_hash=content_hash,
    )
    citation = Citation(
        id="c1",
        source_id=source_id,
        reference_note="Full reference note.",
        short_note=f"{persona_label}, {record_type} record.",
        source_list_entry="Source list entry.",
        access_date=captured_at.date(),
        archived_url=url,
    )
    return write_capture(
        dest_root,
        source=source,
        citation=citation,
        extraction=extraction,
        snapshot_html=snapshot_html,
        media=media,
        created_at=captured_at,
    )


def _thin_manifest_only(root: Path, name: str, manifest: dict) -> Path:
    """A folder with nothing but a manifest.json — the tolerant-read edge case."""
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _make_document_capture(
    dest_root: Path,
    *,
    source_id: str,
    url: str,
    title: str,
    site_name: str,
    notes: Sequence[CaptureNote] = (),
    captured_at: datetime = _DT,
) -> Path:
    """A `capture-folder/v2` document capture (a PDF the page IS) — no snapshot,
    empty extraction, optional researcher notes."""
    doc_bytes = b"%PDF-1.4 fake document bytes\n%%EOF"
    extraction = Extraction()  # zero-assertion: archive-only, no model call
    content_hash = sha256_hex(serialize_extraction(extraction))
    archive = ArchiveRecord(
        local_snapshot="document.pdf",
        sha256=sha256_hex(doc_bytes),
        captured_at=captured_at,
        kind=ArchiveKind.DOCUMENT,
        media_type="application/pdf",
    )
    source = Source(
        id=source_id,
        url=url,
        title=title,
        site_name=site_name,
        record_type=None,
        gating_status=GatingStatus.PUBLIC,
        copyright_flag=False,
        source_quality=SourceQuality.ORIGINAL,
        archive=archive,
        content_hash=content_hash,
        spec_version="capture-folder/v2",
    )
    citation = Citation(
        id="c1",
        source_id=source_id,
        reference_note="Full reference note.",
        short_note=f"{title}, archived document.",
        source_list_entry="Source list entry.",
        access_date=captured_at.date(),
        archived_url=url,
    )
    return write_capture(
        dest_root,
        source=source,
        citation=citation,
        extraction=extraction,
        primary_bytes=doc_bytes,
        notes=notes,
        created_at=captured_at,
    )


def _note(source_id: str, text: str, *, author: str = "owner") -> CaptureNote:
    return CaptureNote(id=f"n-{text[:6]}", source_id=source_id, text=text, author=author, created_at=_DT)


# --------------------------------------------------------------------------
# Escaping primitives (spec §3.3)
# --------------------------------------------------------------------------


def test_esc_escapes_markup_in_every_context():
    assert esc('<script>alert("x")</script>') == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"


def test_esc_always_escapes_quotes_so_a_value_cannot_break_out_of_an_attribute():
    """One always-quote-safe escaper, so no call site can pick the wrong one."""
    assert esc('" onmouseover="evil()') == "&quot; onmouseover=&quot;evil()"
    assert esc("' onload='evil()") == "&#x27; onload=&#x27;evil()"


def test_href_allows_http_and_https():
    assert href("https://example.test/a?b=1&c=2") == "https://example.test/a?b=1&amp;c=2"
    assert href("http://example.test/") == "http://example.test/"


def test_href_rejects_dangerous_schemes():
    """Only http(s) may become a link; everything else is not linkable."""
    assert href("javascript:alert(1)") is None
    assert href("data:text/html;base64,PHNjcmlwdD4=") is None
    assert href("vbscript:msgbox") is None
    assert href("file:///etc/passwd") is None
    assert href("") is None


def test_href_rejects_control_char_obfuscated_scheme():
    """Browsers strip control chars when parsing a scheme; so must we, first."""
    assert href("java\nscript:alert(1)") is None
    assert href("java\tscript:alert(1)") is None
    assert href("  JaVaScRiPt:alert(1)") is None
    assert href("\x00javascript:alert(1)") is None


# --------------------------------------------------------------------------
# Reading the folders (spec §2)
# --------------------------------------------------------------------------


def test_read_capture_record_projects_manifest_citation_extraction(tmp_path):
    folder = _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/death/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle"), ("age", "46 y")],
    )
    record = read_capture_record(folder)
    assert record.record_type == "death"
    assert record.primary_persona == "Mary Doyle"
    assert record.short_note == "Mary Doyle, death record."
    assert record.url == "https://example.test/death/1"
    assert record.file_types == ("html",)
    assert {(a.field, a.value) for a in record.assertions} == {
        ("name", "Mary Doyle"),
        ("age", "46 y"),
    }
    assert record.assertions[0].persona_label == "Mary Doyle"


def test_read_all_orders_newest_first_and_skips_non_captures(tmp_path):
    _make_capture(
        tmp_path,
        source_id="018f0000older",
        url="https://example.test/1",
        site_name="alpha",
        record_type="birth",
        persona_label="Old One",
        fields=[("name", "Old One")],
        captured_at=datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc),
    )
    _make_capture(
        tmp_path,
        source_id="018f0000newer",
        url="https://example.test/2",
        site_name="beta",
        record_type="death",
        persona_label="New One",
        fields=[("name", "New One")],
        captured_at=datetime(2026, 7, 8, 9, 0, tzinfo=timezone.utc),
    )
    # Noise that must be ignored: a hidden dir, an underscore dir, a stray file.
    (tmp_path / "_failed").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")

    records = read_all(tmp_path)
    assert [r.primary_persona for r in records] == ["New One", "Old One"]


def test_read_is_tolerant_of_missing_extraction_and_citation(tmp_path):
    """A folder with only a manifest.json still appears (thin), never raises."""
    folder = _thin_manifest_only(
        tmp_path,
        "20260708-thin-018f0000",
        {
            "capture_id": "20260708-thin-018f0000",
            "source": {"url": "https://thin.test/x", "record_type": "census"},
            "archive": {"captured_at": "2026-07-08T00:00:00Z"},
            "hashes": {"algo": "sha256", "files": {"snapshot.html": "abc"}},
        },
    )
    record = read_capture_record(folder)
    assert record.record_type == "census"
    assert record.assertions == ()
    assert record.short_note is None
    # And it renders without error, showing the no-assertions note.
    html = render_index([record], generated_at=_DT)
    assert "No facts were read from this page." in html


def test_local_link_targets_the_real_folder_not_the_manifest_capture_id(tmp_path):
    """spec §2: the link resolves. A manifest cannot redirect it off-collection."""
    folder = _thin_manifest_only(
        tmp_path,
        "20260708-real-018f0000",
        {
            "capture_id": "../../../../etc/passwd",
            "source": {"url": "https://thin.test/x", "record_type": "census"},
            "archive": {"captured_at": "2026-07-08T00:00:00Z"},
            "hashes": {"algo": "sha256", "files": {"snapshot.html": "abc"}},
        },
    )
    record = read_capture_record(folder)
    assert record.folder_name == "20260708-real-018f0000"

    html = render_index([record], generated_at=_DT)
    assert 'href="./20260708-real-018f0000/"' in html
    assert "../../../../etc/passwd" not in html


# --------------------------------------------------------------------------
# The status spine (spec §4)
# --------------------------------------------------------------------------


def test_status_is_extracted_when_assertions_exist_else_captured(tmp_path):
    with_assertions = read_capture_record(
        _make_capture(
            tmp_path,
            source_id="018f0000aaaa",
            url="https://example.test/1",
            site_name="example",
            record_type="death",
            persona_label="Mary Doyle",
            fields=[("name", "Mary Doyle")],
        )
    )
    assert with_assertions.status == "extracted"

    without = read_capture_record(
        _thin_manifest_only(
            tmp_path,
            "20260708-thin-018f0000",
            {
                "capture_id": "20260708-thin-018f0000",
                "source": {"url": "https://thin.test/x", "record_type": "census"},
                "archive": {"captured_at": "2026-07-08T00:00:00Z"},
                "hashes": {"algo": "sha256", "files": {"snapshot.html": "abc"}},
            },
        )
    )
    assert without.status == "captured"


def test_only_lifecycle_stages_with_data_are_shown(tmp_path):
    """Reviewed (correction log) and Transcribed (REQ-006) have data; matched and attached
    have none yet, so they are not shown (owner, 2026-10-23: placeholders confused)."""
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    for label in ("<dt>Reviewed</dt><dd>—</dd>", "<dt>Transcribed</dt><dd>—</dd>"):
        assert label in html
    for gone in ("Matched", "Attached", "OCR'd"):
        assert f"<dt>{gone}</dt>" not in html


# --------------------------------------------------------------------------
# Rendering (spec §3, §4)
# --------------------------------------------------------------------------


def test_write_index_renders_a_row_and_detail_with_links(tmp_path):
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/death/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle"), ("death_date", "1903-01-19")],
    )
    path = write_index(tmp_path, now=lambda: _DT)
    assert path.name == INDEX_FILENAME == "index.html"
    html = path.read_text(encoding="utf-8")

    assert html.startswith("<!doctype html>")
    assert "<table" in html
    # One <tbody> per capture, carrying the filter/sort keys as data-*.
    # Filed under its common category; the reader's exact type is in the detail.
    assert 'data-type="Death &amp; burial"' in html
    assert "<dt>Record type</dt><dd>death (Death &amp; burial)</dd>" in html
    assert 'data-site="example"' in html
    assert 'data-status="extracted"' in html
    # Bibliographic reference, source link, local folder link.
    assert "Mary Doyle, death record." in html
    assert 'href="https://example.test/death/1"' in html
    assert 'href="./20260708-example-018f0000/"' in html
    # Learned details table.
    assert "<td>death_date</td>" in html
    assert "<td>1903-01-19</td>" in html


def test_index_is_self_contained_and_works_without_javascript(tmp_path):
    """spec §3.1 + §3.2: no external requests; details readable with JS off."""
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/death/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")

    # Self-contained: styles and script are inline, nothing is fetched.
    assert "<style>" in html and "<script>" in html
    assert "src=" not in html
    assert 'rel="stylesheet"' not in html
    assert "https://cdn" not in html and "//unpkg" not in html
    assert "@import" not in html

    # Progressive enhancement: the detail is in the served HTML, and it is the
    # *script* that hides it — so JS-off leaves a complete, readable document.
    assert "Mary Doyle, death record." in html
    assert 'class="no-js"' in html
    assert ".js .detail" in html  # the collapse rule is gated on JS being on


def test_index_escapes_hostile_extraction_values(tmp_path):
    """Persona labels and values are LLM output over attacker-controlled pages."""
    _make_capture(
        tmp_path,
        source_id="018f0000cccc",
        url="https://example.test/x",
        site_name='ex"><script>alert(1)</script>',
        record_type="death",
        persona_label="<img src=x onerror=alert(1)>",
        fields=[("name", '" onmouseover="alert(1)')],
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert 'onmouseover="alert(1)' not in html
    # ...and the escaped forms are what landed.
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&quot;" in html


def test_index_does_not_link_a_dangerous_source_url(tmp_path):
    """`CaptureRequest.url` is unvalidated upstream; the renderer must not trust it."""
    _thin_manifest_only(
        tmp_path,
        "20260708-evil-018f0000",
        {
            "capture_id": "20260708-evil-018f0000",
            "source": {
                "url": "javascript:alert(document.domain)",
                "record_type": "death",
            },
            "archive": {"captured_at": "2026-07-08T00:00:00Z"},
            "hashes": {"algo": "sha256", "files": {"snapshot.html": "abc"}},
        },
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")

    assert 'href="javascript:' not in html
    # It is still *shown* — inert, escaped, so the researcher sees what was captured.
    assert "javascript:alert(document.domain)" in html


def test_index_flags_gated_and_media_file_types(tmp_path):
    _make_capture(
        tmp_path,
        source_id="018f0000bbbb",
        url="https://gated.test/cert/9",
        site_name="gatedsite",
        record_type="death",
        persona_label="John Doe",
        fields=[("name", "John Doe")],
        gating=GatingStatus.GATED,
        media=[MediaItem(slug="certificate", ext="pdf", data=b"%PDF-1.4 fake pdf bytes")],
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    assert 'data-gating="gated"' in html
    assert ">pdf<" in html  # file-type chip
    assert "🔒" in html


def test_render_index_empty_collection(tmp_path):
    assert not read_all(tmp_path)
    html = render_index([], generated_at=_DT)
    assert "No captures yet" in html
    assert "0 sources" in html
    assert html.startswith("<!doctype html>")


def test_BUG_same_day_captures_order_by_time_not_by_site_slug(tmp_path):
    """Regression: capture_id carries only YYYYMMDD, so same-day captures tied
    and fell back to alphabetical site order — visibly wrong once the index
    started displaying the capture *time*."""
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="rootsireland",  # sorts after "registers" alphabetically...
        record_type="marriage",
        persona_label="Earlier",
        fields=[("name", "Earlier")],
        captured_at=datetime(2026, 7, 9, 9, 14, 5, tzinfo=timezone.utc),
    )
    _make_capture(
        tmp_path,
        source_id="018f0000bbbb",
        url="https://example.test/2",
        site_name="registers",  # ...but was captured later, so must come first
        record_type="register_page",
        persona_label="Later",
        fields=[("name", "Later")],
        captured_at=datetime(2026, 7, 9, 9, 31, 5, tzinfo=timezone.utc),
    )
    assert [r.primary_persona for r in read_all(tmp_path)] == ["Later", "Earlier"]


def test_read_all_puts_a_capture_with_no_timestamp_last(tmp_path):
    """An unknown instant is not a recent one."""
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Timestamped",
        fields=[("name", "Timestamped")],
    )
    _thin_manifest_only(
        tmp_path,
        "20990101-undated-018f0000",  # a far-future id that would sort first
        {
            "capture_id": "20990101-undated-018f0000",
            "source": {"url": "https://undated.test/x", "record_type": "census"},
            "hashes": {"algo": "sha256", "files": {"snapshot.html": "abc"}},
        },
    )
    records = read_all(tmp_path)
    assert records[-1].capture_id == "20990101-undated-018f0000"


def test_index_shows_the_captured_time_not_just_the_date(tmp_path):
    """Access time is part of the citation; the folder id carries only the date."""
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    assert "2026-07-08 14:32 UTC" in html
    # ...and the sort key keeps full ISO precision, so two captures made on one
    # day order correctly rather than tying.
    assert 'data-date="2026-07-08T14:32:05Z"' in html


def test_index_renders_a_reference_to_the_linked_source_document(tmp_path):
    """A derivative names its original; the index surfaces that pointer."""
    folder = _make_capture(
        tmp_path,
        source_id="018f0000eeee",
        url="https://rootsireland.ie/ifhf/viewdetailmarriage.php",
        site_name="rootsireland",
        record_type="marriage",
        persona_label="James Kilcoin",
        fields=[("marriage_date", "29-Apr-1852")],
        external_refs=[
            ExternalRef(
                authority="nli-registers",
                identifier="vtls000632043",
                locator=None,
                url="https://registers.nli.ie/registers/vtls000632043",
                label="View Microfilm of Parish Register",
            )
        ],
    )
    record = read_capture_record(folder)
    assert record.external_refs[0].citation == "nli-registers vtls000632043"

    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    assert "References source document" in html
    assert "View Microfilm of Parish Register" in html
    assert 'href="https://registers.nli.ie/registers/vtls000632043"' in html
    assert 'data-refs="yes"' in html
    assert "↗ source" in html
    # The register is named; the leaf is not. The index must say so rather than
    # let a reader assume the pointer resolves to a page.
    assert "(document, not page)" in html


def test_index_capture_without_refs_says_nothing_about_references(tmp_path):
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    assert "References source document" not in html
    assert 'data-refs="no"' in html


def test_read_is_tolerant_of_captures_written_before_external_refs_existed(tmp_path):
    """extraction.json without the key is normal, not corrupt."""
    folder = _thin_manifest_only(
        tmp_path,
        "20260708-old-018f0000",
        {
            "capture_id": "20260708-old-018f0000",
            "source": {"url": "https://old.test/x", "record_type": "census"},
            "archive": {"captured_at": "2026-07-08T00:00:00Z"},
            "hashes": {"algo": "sha256", "files": {"snapshot.html": "abc"}},
        },
    )
    (folder / "extraction.json").write_text(
        json.dumps({"personas": [], "snippets": [], "assertions": []}), encoding="utf-8"
    )
    assert read_capture_record(folder).external_refs == ()


def test_read_drops_a_reference_that_identifies_nothing(tmp_path):
    """An unidentifiable reference points at nothing and must not render."""
    folder = _thin_manifest_only(
        tmp_path,
        "20260708-bad-018f0000",
        {
            "capture_id": "20260708-bad-018f0000",
            "source": {"url": "https://bad.test/x", "record_type": "census"},
            "archive": {"captured_at": "2026-07-08T00:00:00Z"},
            "hashes": {"algo": "sha256", "files": {"snapshot.html": "abc"}},
        },
    )
    (folder / "extraction.json").write_text(
        json.dumps(
            {
                "personas": [],
                "snippets": [],
                "assertions": [],
                "external_refs": [
                    {"authority": "", "identifier": "", "url": "https://x.test/"},
                    "not even an object",
                ],
            }
        ),
        encoding="utf-8",
    )
    assert read_capture_record(folder).external_refs == ()


def test_write_index_removes_the_superseded_markdown_index(tmp_path):
    """spec §0: a stale evidence index is worse than none."""
    legacy = tmp_path / LEGACY_INDEX_FILENAME
    legacy.write_text("# stale v1 index", encoding="utf-8")
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    write_index(tmp_path, now=lambda: _DT)
    assert not legacy.exists()
    assert (tmp_path / INDEX_FILENAME).is_file()


# --------------------------------------------------------------------------
# Document captures + notes (capture-folder/v2)
# --------------------------------------------------------------------------


def test_document_capture_reads_and_renders_a_direct_pdf_link(tmp_path):
    """A v2 document capture shows a `pdf` file-type and a direct document link,
    and — with no assertions — sits at the `captured` lifecycle stage."""
    folder = _make_document_capture(
        tmp_path,
        source_id="018f0000dddd",
        url="https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf",
        title="Spring 2025 Newsletter",
        site_name="isleauhauthistory.com",
    )
    record = read_capture_record(folder)
    assert record.is_document is True
    assert record.primary_artifact == "document.pdf"
    assert record.file_types == ("pdf",)
    assert record.status == "captured"  # archive-only, no assertions yet

    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    assert ">pdf<" in html  # file-type chip
    assert f'href="./{record.folder_name}/document.pdf"' in html  # direct link
    assert "Spring 2025 Newsletter" in html  # title used when no persona


def test_notes_render_and_are_searchable(tmp_path):
    folder = _make_document_capture(
        tmp_path,
        source_id="018f0000eeee",
        url="https://isleauhauthistory.com/documents/Spring_2025%20Newsletter.pdf",
        title="Spring 2025 Newsletter",
        site_name="isleauhauthistory.com",
        notes=(
            _note("018f0000eeee", "Names the Doyle family."),
            _note("018f0000eeee", "Revisit for the 1890 keeper list.", author="mj"),
        ),
    )
    record = read_capture_record(folder)
    assert [n.text for n in record.notes] == [
        "Names the Doyle family.",
        "Revisit for the 1890 keeper list.",
    ]

    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    assert "<h3>Notes</h3>" in html
    assert "Names the Doyle family." in html
    assert "Revisit for the 1890 keeper list." in html
    assert "— mj" in html  # note author surfaced
    # Notes join the client-side filter haystack.
    assert "names the doyle family." in html.lower()
    assert 'data-search="' in html


def test_notes_values_are_escaped(tmp_path):
    """notes.json text is user input; it must never become live markup."""
    folder = _make_document_capture(
        tmp_path,
        source_id="018f0000ffff",
        url="https://ex.test/doc.pdf",
        title="Doc",
        site_name="ex.test",
        notes=(_note("018f0000ffff", '<img src=x onerror=alert(1)>'),),
    )
    html = render_index([read_capture_record(folder)], generated_at=_DT)
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_read_is_tolerant_of_a_malformed_notes_file(tmp_path):
    """A corrupt notes.json degrades to no notes, never blanks the index."""
    folder = _make_document_capture(
        tmp_path,
        source_id="018f00001111",
        url="https://ex.test/doc.pdf",
        title="Doc",
        site_name="ex.test",
    )
    (folder / "notes.json").write_text("{ not json at all", encoding="utf-8")
    record = read_capture_record(folder)
    assert record.notes == ()
    # And it still renders.
    assert "Doc" in render_index([record], generated_at=_DT)


def test_html_capture_has_no_document_link(tmp_path):
    """An ordinary page snapshot is reached via its folder, not a direct link."""
    folder = _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    record = read_capture_record(folder)
    assert record.is_document is False
    html = render_index([record], generated_at=_DT)
    assert "document.pdf" not in html
    assert "<dt>Document</dt>" not in html


def test_BUG_document_link_refuses_a_path_walking_primary_artifact(tmp_path):
    """A hostile manifest `local_snapshot` must not build a path-walking link."""
    _thin_manifest_only(
        tmp_path,
        "20260708-evil-018f0000",
        {
            "capture_id": "20260708-evil-018f0000",
            "source": {"url": "https://evil.test/x", "record_type": "census"},
            "archive": {
                "captured_at": "2026-07-08T00:00:00Z",
                "kind": "document",
                "local_snapshot": "../../../../etc/passwd",
                "sha256": "abc",
            },
            "hashes": {"algo": "sha256", "files": {"../../../../etc/passwd": "abc"}},
        },
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    assert "etc/passwd" not in html  # neither linked nor as a file-type
    assert "<dt>Document</dt>" not in html


# --------------------------------------------------------------------------
# Interactive (backend-served) index: delete trashcan
# --------------------------------------------------------------------------


def test_static_index_has_no_trashcan_or_token(tmp_path):
    """The file written to disk is non-interactive — no button that can't work."""
    _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    html = write_index(tmp_path, now=lambda: _DT).read_text(encoding="utf-8")
    assert 'class="trash"' not in html
    assert "__CAPSULE_TOKEN__" not in html


def test_interactive_render_adds_trashcan_and_token(tmp_path):
    """With a delete_token the render is interactive: a per-capture trashcan and
    the token the page's JS uses to call the backend."""
    folder = _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    record = read_capture_record(folder)
    html = render_index([record], generated_at=_DT, delete_token="tok-abc-123")
    assert 'class="trash"' in html
    assert f'data-capture-id="{record.folder_name}"' in html
    assert "tok-abc-123" in html
    assert "Are you sure you want to delete this record" in html
    # The add-note pencil is shown even with no notes yet, so the first can be added.
    assert 'class="d-notes-add"' in html
    assert "No notes yet." in html


def test_BUG_embedded_token_cannot_break_out_of_the_script_tag(tmp_path):
    """A token containing </script> must be escaped so it can't close the tag."""
    folder = _make_capture(
        tmp_path,
        source_id="018f0000aaaa",
        url="https://example.test/1",
        site_name="example",
        record_type="death",
        persona_label="Mary Doyle",
        fields=[("name", "Mary Doyle")],
    )
    record = read_capture_record(folder)
    html = render_index([record], generated_at=_DT, delete_token="a</script><b>x")
    # Escaping `<` alone neuters the closing tag: the raw `</script>` from the
    # token never appears, and it lands as `\\u003c/script>` (only `<` escaped).
    assert "a</script><b>" not in html
    assert "\\u003c/script>\\u003cb>x" in html


def test_interactive_render_shows_add_note_on_a_capture_with_notes(tmp_path):
    folder = _make_document_capture(
        tmp_path,
        source_id="018f0000dddd",
        url="https://ex.test/doc.pdf",
        title="Doc",
        site_name="ex.test",
        notes=(_note("018f0000dddd", "Existing note."),),
    )
    record = read_capture_record(folder)
    html = render_index([record], generated_at=_DT, delete_token="t")
    assert "Existing note." in html
    assert 'class="d-notes-add"' in html
    # Static render of the same capture shows the note but no add affordance.
    # (The `.d-notes-add` CSS rule is always in the stylesheet, so match the
    # button's `class="..."` usage, not the bare class name.)
    static = render_index([record], generated_at=_DT)
    assert "Existing note." in static
    assert 'class="d-notes-add"' not in static


# ---------------------------------------------------------------------------
# The extractor's note on a zero-assertion capture
# ---------------------------------------------------------------------------


def _capture_with_extraction_notes(root: Path, notes, source_id: str = "src-notes") -> Path:
    """A zero-assertion capture (an unindexed scan) carrying the model's explanation."""
    url = "https://registers.nli.ie/registers/vtls000632043"
    extraction = Extraction(notes=notes)
    snapshot_html = "<html><body>viewer</body></html>"
    source = Source(
        id=source_id,
        url=url,
        site_name="NLI",
        record_type="register_page",
        gating_status=GatingStatus.PUBLIC,
        copyright_flag=False,
        source_quality=SourceQuality.ORIGINAL,
        archive=ArchiveRecord(
            local_snapshot="snapshot.html",
            sha256=sha256_hex(snapshot_html.encode("utf-8")),
            captured_at=_DT,
        ),
        content_hash=sha256_hex(serialize_extraction(extraction)),
    )
    citation = Citation(
        id="c-notes",
        source_id=source_id,
        reference_note="Full reference note.",
        short_note="Parish register page.",
        source_list_entry="Source list entry.",
        access_date=_DT.date(),
        archived_url=url,
    )
    return write_capture(
        root,
        source=source,
        citation=citation,
        extraction=extraction,
        snapshot_html=snapshot_html,
        created_at=_DT,
    )


def test_the_models_note_reaches_the_index_instead_of_a_generic_sentence(tmp_path):
    """Regression: the extractor's explanation was discarded.

    Every NLI register page yields zero assertions, and the model says why. The index
    used to print a generic "awaiting OCR" guess over the top of a real answer.
    """
    from capsule.collection.read import read_capture_record

    note = "The page image is a scanned document; no transcribable text is present."
    folder = _capture_with_extraction_notes(tmp_path, note)

    record = read_capture_record(folder)
    assert record.extraction_notes == note

    html = render_index([record], generated_at=_DT)
    assert note in html
    assert "Extractor:" in html  # attributed — never mistaken for the researcher's note
    assert "No facts were read" not in html  # the generic sentence yields to a reason


def test_a_capture_with_no_model_note_keeps_the_generic_sentence(tmp_path):
    from capsule.collection.read import read_capture_record

    folder = _capture_with_extraction_notes(tmp_path, None, source_id="src-plain")
    record = read_capture_record(folder)

    assert record.extraction_notes is None
    assert "No facts were read from this page." in render_index([record], generated_at=_DT)


def test_the_models_note_is_escaped(tmp_path):
    """It is LLM output over an attacker-controlled page — it is not trusted markup."""
    from capsule.collection.read import read_capture_record

    folder = _capture_with_extraction_notes(
        tmp_path, "<img src=x onerror=alert(1)>", source_id="src-xss"
    )
    html = render_index([read_capture_record(folder)], generated_at=_DT)

    assert "<img src=x onerror" not in html
    assert "&lt;img" in html
