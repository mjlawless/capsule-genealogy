"""Tests for capsule/capture/manifest.py — the capture-folder writer + verify.

Written FIRST (RED) — capsule/capture/manifest.py does not exist yet when this
file is first run. Exercises the on-disk shape in docs/specs/capture-folder-v1.md.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from capsule.contracts.evidence import (
    ArchiveRecord,
    Assertion,
    Citation,
    EvidenceType,
    GatingStatus,
    Persona,
    Snippet,
    SnippetAnchor,
    Source,
    SourceQuality,
)

_DT = datetime(2026, 7, 7, 14, 32, 0, tzinfo=timezone.utc)


def _extraction():
    from capsule.capture.manifest import Extraction

    personas = (Persona(id="p1", source_id="src-1", role="deceased", label="John Murphy"),)
    snippets = (
        Snippet(
            id="s1",
            source_id="src-1",
            verbatim_text="b. 1900 d. 1965",
            anchor=SnippetAnchor(kind="text", start=0, end=15),
        ),
    )
    assertions = (
        Assertion(
            id="a1",
            source_id="src-1",
            persona_id="p1",
            snippet_id="s1",
            field="birth_year",
            verbatim_value="1900",
            evidence_type=EvidenceType.STATED,
            created_at=_DT,
        ),
    )
    return Extraction(personas=personas, snippets=snippets, assertions=assertions)


def _citation():
    return Citation(
        id="c1",
        source_id="src-1",
        reference_note="John Murphy memorial, Find a Grave.",
        short_note="Murphy memorial.",
        source_list_entry="Find a Grave. Digital images.",
        access_date=date(2026, 7, 7),
        archived_url="https://web.archive.org/web/2026/https://findagrave.com/1",
    )


# The assertion key sequence `extraction.json` carried BEFORE `normalized_field`
# existed. `source.content_hash` is the sha256 of those bytes, and every capture
# already on disk declares one, so this order is a frozen wire format, not a
# formatting preference. Adding a key to `Assertion` must not perturb it.
_LEGACY_ASSERTION_KEYS = [
    "id",
    "source_id",
    "persona_id",
    "snippet_id",
    "field",
    "verbatim_value",
    "normalized_value",
    "annotation",
    "evidence_type",
    "confidence",
    "supersedes_id",
    "created_at",
]


def _write_snapshot(tmp_path: Path, snapshot_html: str, **kwargs):
    """Write a capture whose snapshot is `snapshot_html`, with a matching hash.

    `_write` bakes `_SNAPSHOT`'s digest into the source, so a different snapshot
    needs its own archive record or `write_capture` rejects it — which is exactly
    the invariant we want left intact while testing redactions.
    """
    from capsule.capture.manifest import sha256_hex, write_capture

    source, citation, extraction, _snapshot, _media = _bundle()
    archive = source.archive.model_copy(
        update={"sha256": sha256_hex(snapshot_html.encode("utf-8"))}
    )
    source = source.model_copy(update={"archive": archive})
    return write_capture(
        tmp_path,
        source=source,
        citation=citation,
        extraction=extraction,
        snapshot_html=snapshot_html,
        created_at=_DT,
        **kwargs,
    )


class TestRedactionsArray:
    """`redactions[]` is how a capture admits its snapshot is not byte-true.

    `archive.sha256` attests whatever bytes were written. If those bytes were
    altered and the manifest says nothing, the hash faithfully attests a document
    that silently is not the page — the capture lies about its own provenance.
    """

    def test_no_redactions_means_no_key_in_the_manifest(self, tmp_path):
        folder = _write_snapshot(tmp_path, _SNAPSHOT)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert "redactions" not in manifest

    def test_redactions_are_declared_with_reason_and_href(self, tmp_path):
        from capsule.capture.manifest import REDACTION_MARKER, Redaction, verify_capture

        html = f'<html><a href="/myaccount.php">{REDACTION_MARKER}</a></html>'
        folder = _write_snapshot(
            tmp_path,
            html,
            redactions=(
                Redaction(
                    reason="account-chrome", location="https://example.org/myaccount.php"
                ),
            ),
        )
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["redactions"] == [
            {"reason": "account-chrome", "location": "https://example.org/myaccount.php"}
        ]
        assert verify_capture(folder).ok

    def test_BUG_a_redacted_snapshot_with_no_declared_redactions_fails_verify(self, tmp_path):
        """The dangerous direction: page text altered, manifest claims a true copy."""
        from capsule.capture.manifest import REDACTION_MARKER, verify_capture

        html = f'<html><a href="/myaccount.php">{REDACTION_MARKER}</a></html>'
        folder = _write_snapshot(tmp_path, html)  # redactions deliberately omitted
        result = verify_capture(folder)
        assert not result.ok
        assert any("redactions" in entry for entry in result.mismatched)


class TestNormalizedFieldSerialization:
    """`normalized_field` is additive, exactly as `external_refs` is.

    A new key emitted as `null` on assertions with no canonical synonym would
    silently change the bytes for identical evidence, and with them the
    `content_hash` that `verify_capture` and the capsule export depend on.
    """

    def test_absent_normalized_field_is_omitted_not_null(self):
        from capsule.capture.manifest import serialize_extraction

        payload = json.loads(serialize_extraction(_extraction()))
        (assertion,) = payload["assertions"]
        assert list(assertion) == _LEGACY_ASSERTION_KEYS
        # The two older optionals still emit as null — suppressing them would
        # break exactly the hashes this omission exists to protect.
        assert assertion["normalized_value"] is None
        assert assertion["annotation"] is None

    def test_present_normalized_field_is_serialized_after_field(self):
        from capsule.capture.manifest import Extraction, serialize_extraction

        base = _extraction()
        assertion = base.assertions[0].model_copy(
            update={"field": "type", "normalized_field": "record_type"}
        )
        extraction = Extraction(
            personas=base.personas, snippets=base.snippets, assertions=(assertion,)
        )
        (payload,) = json.loads(serialize_extraction(extraction))["assertions"]
        assert payload["normalized_field"] == "record_type"
        expected = list(_LEGACY_ASSERTION_KEYS)
        expected.insert(expected.index("field") + 1, "normalized_field")
        assert list(payload) == expected

    def test_default_retracted_is_omitted(self):
        """A model-extracted assertion is never retracted; the default-False key
        must not appear, or every existing capture's content_hash would change.

        (The stronger byte-order guard is `test_absent_normalized_field_is_omitted_
        not_null`, which asserts the full key list equals `_LEGACY_ASSERTION_KEYS`.)
        """
        from capsule.capture.manifest import serialize_extraction

        (assertion,) = json.loads(serialize_extraction(_extraction()))["assertions"]
        assert "retracted" not in assertion

    def test_true_retracted_is_serialized_before_created_at(self):
        from capsule.capture.manifest import Extraction, serialize_extraction

        base = _extraction()
        assertion = base.assertions[0].model_copy(update={"retracted": True})
        extraction = Extraction(
            personas=base.personas, snippets=base.snippets, assertions=(assertion,)
        )
        (payload,) = json.loads(serialize_extraction(extraction))["assertions"]
        assert payload["retracted"] is True
        expected = list(_LEGACY_ASSERTION_KEYS)
        expected.insert(expected.index("created_at"), "retracted")
        assert list(payload) == expected


_SNAPSHOT = "<html><body>John Murphy b. 1900 d. 1965</body></html>"


def _bundle(**source_overrides):
    """Build a self-consistent (source, citation, extraction, snapshot, media) bundle."""
    from capsule.capture.manifest import MediaItem, sha256_hex, serialize_extraction

    extraction = _extraction()
    content_hash = sha256_hex(serialize_extraction(extraction))
    snapshot_hash = sha256_hex(_SNAPSHOT.encode("utf-8"))
    archive = ArchiveRecord(
        local_snapshot="snapshot.html",
        sha256=snapshot_hash,
        captured_at=_DT,
        wayback_url="https://web.archive.org/web/2026/https://findagrave.com/1",
    )
    source = Source(
        id="src-1",
        url="https://www.findagrave.com/memorial/1",
        title="John Murphy",
        site_name="Find a Grave",
        gating_status=GatingStatus.PUBLIC,
        copyright_flag=False,
        source_quality=SourceQuality.DERIVATIVE,
        archive=archive,
        content_hash=content_hash,
    )
    if source_overrides:
        source = source.model_copy(update=source_overrides)
    media = (MediaItem(slug="headstone", ext="jpg", data=b"\xff\xd8\xff-fake-jpeg"),)
    return source, _citation(), extraction, _SNAPSHOT, media


def _write(tmp_path: Path, **kwargs):
    from capsule.capture.manifest import write_capture

    source, citation, extraction, snapshot, media = _bundle()
    params: dict[str, Any] = dict(
        source=source,
        citation=citation,
        extraction=extraction,
        snapshot_html=snapshot,
        media=media,
        created_at=_DT,
    )
    params.update(kwargs)
    return write_capture(tmp_path, **params)  # pyright: ignore[reportArgumentType]


# -- capture_id derivation -------------------------------------------------


def test_derive_capture_id_format():
    from capsule.capture.manifest import derive_capture_id

    source, *_ = _bundle()
    assert derive_capture_id(source) == "20260707-findagrave-src1"


def test_derive_capture_id_falls_back_to_domain():
    from capsule.capture.manifest import derive_capture_id

    source, *_ = _bundle(site_name=None)
    assert derive_capture_id(source) == "20260707-findagrave-src1"


# -- writer ----------------------------------------------------------------


def test_write_creates_expected_layout(tmp_path):
    folder = _write(tmp_path)
    assert folder == tmp_path / "20260707-findagrave-src1"
    assert (folder / "manifest.json").is_file()
    assert (folder / "snapshot.html").is_file()
    assert (folder / "extraction.json").is_file()
    assert (folder / "citation.json").is_file()
    assert (folder / "media" / "0-headstone.jpg").is_file()


def test_manifest_top_level_fields(tmp_path):
    folder = _write(tmp_path)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["spec_version"] == "capture-folder/v1"
    assert manifest["capture_id"] == "20260707-findagrave-src1"
    assert manifest["created_at"] == "2026-07-07T14:32:00Z"
    assert manifest["source"]["id"] == "src-1"
    assert manifest["source"]["gating_status"] == "public"
    assert manifest["archive"]["captured_at"] == "2026-07-07T14:32:00Z"
    assert manifest["archive"]["local_snapshot"] == "snapshot.html"
    assert manifest["hashes"]["algo"] == "sha256"


def test_manifest_hashes_match_disk_bytes(tmp_path):
    from capsule.capture.manifest import sha256_hex

    folder = _write(tmp_path)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for rel, expected in manifest["hashes"]["files"].items():
        on_disk = sha256_hex((folder / rel).read_bytes())
        assert on_disk == expected, rel


def test_hash_cross_field_invariants(tmp_path):
    folder = _write(tmp_path)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["hashes"]["files"]
    assert files["snapshot.html"] == manifest["archive"]["sha256"]
    assert files["extraction.json"] == manifest["source"]["content_hash"]


def test_writer_rejects_stale_content_hash(tmp_path):
    from capsule.capture.manifest import write_capture

    source, citation, extraction, snapshot, media = _bundle(content_hash="d" * 64)
    with pytest.raises(ValueError, match="content_hash"):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html=snapshot,
            media=media,
            created_at=_DT,
        )


def test_writer_rejects_stale_snapshot_hash(tmp_path):
    from capsule.capture.manifest import write_capture

    source, citation, extraction, _snapshot, media = _bundle()
    with pytest.raises(ValueError, match="sha256"):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html="TAMPERED PAGE",
            media=media,
            created_at=_DT,
        )


def test_writer_rejects_cross_source_leak(tmp_path):
    from capsule.capture.manifest import Extraction, write_capture

    source, citation, extraction, snapshot, media = _bundle()
    leaked = Extraction(
        personas=(Persona(id="p9", source_id="OTHER", role="x", label="y"),),
        snippets=extraction.snippets,
        assertions=extraction.assertions,
    )
    # A persona bound to a different source_id must be rejected before anything
    # is written — assert it raises about source_id, not about hashing.
    with pytest.raises(ValueError, match="source_id"):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=leaked,
            snapshot_html=snapshot,
            media=media,
            created_at=_DT,
        )


def test_writer_never_clobbers_existing_capture(tmp_path):
    from capsule.capture.manifest import write_capture

    _write(tmp_path)
    source, citation, extraction, snapshot, media = _bundle()
    with pytest.raises(FileExistsError):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html=snapshot,
            media=media,
            created_at=_DT,
        )


# -- verify ----------------------------------------------------------------


def test_verify_passes_on_fresh_capture(tmp_path):
    from capsule.capture.manifest import verify_capture

    folder = _write(tmp_path)
    result = verify_capture(folder)
    assert result.ok is True
    assert result.missing == ()
    assert result.mismatched == ()
    assert result.extra == ()


def test_verify_fails_on_tampered_file(tmp_path):
    from capsule.capture.manifest import verify_capture

    folder = _write(tmp_path)
    (folder / "snapshot.html").write_text("TAMPERED", encoding="utf-8")
    result = verify_capture(folder)
    assert result.ok is False
    assert "snapshot.html" in result.mismatched


def test_verify_fails_on_missing_file(tmp_path):
    from capsule.capture.manifest import verify_capture

    folder = _write(tmp_path)
    (folder / "citation.json").unlink()
    result = verify_capture(folder)
    assert result.ok is False
    assert "citation.json" in result.missing


def test_verify_fails_on_extra_unlisted_file(tmp_path):
    from capsule.capture.manifest import verify_capture

    folder = _write(tmp_path)
    (folder / "media" / "9-sneaky.jpg").write_bytes(b"unlisted")
    result = verify_capture(folder)
    assert result.ok is False
    assert "media/9-sneaky.jpg" in result.extra


def test_verify_ignores_agent_log_additions(tmp_path):
    from capsule.capture.manifest import verify_capture

    folder = _write(tmp_path)
    log_dir = folder / "agent_log"
    log_dir.mkdir(exist_ok=True)
    (log_dir / "20260707-extractor.json").write_text("{}", encoding="utf-8")
    result = verify_capture(folder)
    assert result.ok is True


def test_verify_raises_when_manifest_absent(tmp_path):
    from capsule.capture.manifest import verify_capture

    bare = tmp_path / "empty"
    bare.mkdir()
    with pytest.raises(FileNotFoundError):
        verify_capture(bare)


def test_multiple_media_are_zero_padded_for_stable_sort(tmp_path):
    from capsule.capture.manifest import MediaItem

    media = tuple(
        MediaItem(slug=f"scan{i}", ext="jpg", data=f"img-{i}".encode()) for i in range(11)
    )
    folder = _write(tmp_path, media=media)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    media_keys = [k for k in manifest["hashes"]["files"] if k.startswith("media/")]
    # 11 items -> max index 10 -> width 2 -> "00".."10", lexically sorted.
    assert media_keys[0] == "media/00-scan0.jpg"
    assert "media/10-scan10.jpg" in media_keys


# -- media provenance (Increment B slice 1) --------------------------------


def test_media_provenance_recorded_in_manifest(tmp_path):
    """A media item's origin URL + MIME are recorded alongside its on-disk path."""
    from capsule.capture.manifest import MediaItem

    media = (
        MediaItem(
            slug="certificate",
            ext="pdf",
            data=b"%PDF-1.4 fake",
            source_url="https://blob.example/scan.pdf?sig=abc",
            mime="application/pdf",
        ),
    )
    folder = _write(tmp_path, media=media)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["media"] == [
        {
            "path": "media/0-certificate.pdf",
            "source_url": "https://blob.example/scan.pdf?sig=abc",
            "mime": "application/pdf",
        }
    ]
    # The bytes are still hashed in the authoritative integrity manifest.
    assert "media/0-certificate.pdf" in manifest["hashes"]["files"]


def test_media_provenance_optional_fields_default_null(tmp_path):
    """source_url/mime are optional; absent -> null, path is always present."""
    from capsule.capture.manifest import MediaItem

    media = (MediaItem(slug="headstone", ext="jpg", data=b"\xff\xd8\xff"),)
    folder = _write(tmp_path, media=media)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["media"] == [
        {"path": "media/0-headstone.jpg", "source_url": None, "mime": None}
    ]


def test_no_media_omits_media_key(tmp_path):
    """A capture with no media has no `media` key — no-media manifests are unchanged."""
    folder = _write(tmp_path, media=())
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert "media" not in manifest


def test_verify_flags_media_entry_missing_from_hashes(tmp_path):
    """A media[] path that is not in hashes.files is a manifest inconsistency."""
    from capsule.capture.manifest import MediaItem, verify_capture

    media = (MediaItem(slug="certificate", ext="pdf", data=b"%PDF-1.4"),)
    folder = _write(tmp_path, media=media)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["media"].append({"path": "media/9-ghost.pdf", "source_url": None, "mime": None})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = verify_capture(folder)
    assert result.ok is False
    assert "media/9-ghost.pdf" in result.mismatched


def test_BUG_verify_flags_stripped_media_provenance(tmp_path):
    """Regression: deleting a media[] entry while its hashed bytes remain fails verify.

    Provenance (source_url/mime) is evidentiary content; a capture whose media
    file is still present and hashed but whose provenance record was removed
    must not verify as OK.
    """
    from capsule.capture.manifest import MediaItem, verify_capture

    media = (MediaItem(slug="certificate", ext="pdf", data=b"%PDF-1.4"),)
    folder = _write(tmp_path, media=media)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["media"]  # strip provenance, leave the bytes + hash intact
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = verify_capture(folder)
    assert result.ok is False
    assert "media/0-certificate.pdf" in result.mismatched


# -- hardening regressions (found by review of the first cut) --------------


@pytest.mark.parametrize("bad_slug", ["../../etc/passwd", "a/b", "..", "UP", "with space", ""])
def test_BUG_media_slug_path_traversal_rejected(tmp_path, bad_slug):
    """Regression: an untrusted media slug must never become a path component."""
    from capsule.capture.manifest import MediaItem, write_capture

    source, citation, extraction, snapshot, _media = _bundle()
    media = (MediaItem(slug=bad_slug, ext="jpg", data=b"x"),)
    with pytest.raises(ValueError, match="slug"):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html=snapshot,
            media=media,
            created_at=_DT,
        )
    # And nothing escaped onto disk.
    assert not (tmp_path.parent / "passwd").exists()


@pytest.mark.parametrize("bad_ext", ["", ".jpg", "j/g", "../x"])
def test_BUG_media_ext_rejected(tmp_path, bad_ext):
    from capsule.capture.manifest import MediaItem, write_capture

    source, citation, extraction, snapshot, _media = _bundle()
    media = (MediaItem(slug="headstone", ext=bad_ext, data=b"x"),)
    with pytest.raises(ValueError, match="ext"):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html=snapshot,
            media=media,
            created_at=_DT,
        )


def test_BUG_capture_id_override_traversal_rejected(tmp_path):
    from capsule.capture.manifest import write_capture

    source, citation, extraction, snapshot, media = _bundle()
    with pytest.raises(ValueError, match="capture_id"):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html=snapshot,
            media=media,
            created_at=_DT,
            capture_id="../ESCAPED",
        )
    assert not (tmp_path.parent / "ESCAPED").exists()


def test_BUG_source_id_yielding_empty_short_is_rejected(tmp_path):
    from capsule.capture.manifest import derive_capture_id

    source, *_ = _bundle(id="///")
    with pytest.raises(ValueError):
        derive_capture_id(source)


def test_BUG_source_id_traversal_sanitized_to_safe_component():
    """A hostile source.id must be sanitized to a single safe folder component."""
    from capsule.capture.manifest import _CAPTURE_ID_RE, derive_capture_id

    source, *_ = _bundle(id="../../evil")
    capture_id = derive_capture_id(source)
    assert capture_id == "20260707-findagrave-evil"
    assert _CAPTURE_ID_RE.match(capture_id)
    assert "/" not in capture_id and ".." not in capture_id


def test_BUG_agent_log_naive_created_at_rejected(tmp_path):
    from datetime import datetime as _dt

    from capsule.capture.manifest import write_capture

    source, citation, extraction, snapshot, media = _bundle()
    with pytest.raises(ValueError, match="UTC"):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html=snapshot,
            media=media,
            created_at=_dt(2026, 7, 7, 14, 32, 0),  # naive
        )


def test_BUG_local_snapshot_must_match_on_disk_name(tmp_path):
    from capsule.capture.manifest import write_capture

    source, citation, extraction, snapshot, media = _bundle(
        archive=ArchiveRecord(
            local_snapshot="page.html",  # writer always writes snapshot.html
            sha256=_bundle()[0].archive.sha256,
            captured_at=_DT,
            wayback_url="https://web.archive.org/web/2026/https://findagrave.com/1",
        )
    )
    with pytest.raises(ValueError, match="local_snapshot"):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html=snapshot,
            media=media,
            created_at=_DT,
        )


def test_BUG_partial_write_is_atomic(tmp_path):
    """A mid-write failure must leave no folder behind, so the id is re-capturable."""
    from capsule.capture.manifest import write_capture

    source, citation, extraction, snapshot, media = _bundle()
    # A non-JSON-serializable agent-log payload raises mid-write.
    with pytest.raises(TypeError):
        write_capture(
            tmp_path,
            source=source,
            citation=citation,
            extraction=extraction,
            snapshot_html=snapshot,
            media=media,
            created_at=_DT,
            agent_logs={"extractor.json": {"bad": {1, 2, 3}}},
        )
    assert not (tmp_path / "20260707-findagrave-src1").exists()
    # The id is not bricked: a clean retry succeeds.
    folder = _write(tmp_path)
    assert folder.is_file() is False and folder.is_dir()


def test_BUG_verify_rejects_manifest_field_tamper(tmp_path):
    """verify must re-check the spec MUST-equal invariants, not just hashes.files."""
    from capsule.capture.manifest import verify_capture

    folder = _write(tmp_path)
    mpath = folder / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    manifest["archive"]["sha256"] = "0" * 64  # diverges from hashes.files
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_capture(folder).ok is False


def test_BUG_verify_rejects_unsafe_listed_key(tmp_path):
    """A listed key that points outside the folder must FAIL, never PASS."""
    from capsule.capture.manifest import sha256_hex, verify_capture

    folder = _write(tmp_path)
    outside = tmp_path / "evil.txt"
    outside.write_bytes(b"planted")
    mpath = folder / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    manifest["hashes"]["files"]["../evil.txt"] = sha256_hex(b"planted")
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_capture(folder).ok is False


def test_BUG_agent_log_as_file_is_flagged_extra(tmp_path):
    """The agent_log exclusion is for the directory, not a same-named file."""
    from capsule.capture.manifest import verify_capture

    folder = _write(tmp_path)
    (folder / "agent_log").write_bytes(b"smuggled")  # a file, not a dir
    assert verify_capture(folder).ok is False


def test_verify_result_lists_are_immutable(tmp_path):
    from capsule.capture.manifest import verify_capture

    folder = _write(tmp_path)
    result = verify_capture(folder)
    assert isinstance(result.missing, tuple)
    assert isinstance(result.mismatched, tuple)
    assert isinstance(result.extra, tuple)
