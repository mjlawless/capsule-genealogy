"""REQ-010 — record WHY a capture holds no record image.

The extension always knew ("no download was armed for this capture", "no download from the
armed site arrived within 20s …") and showed it once in a toast. The manifest said nothing and
the ledger's `captured` event had `image_status: failed` with `error` NULL, so a plain capture
of an image-backed Ancestry record (`57e4eb68`) was later read by the researcher as "index
only". Now the reason rides the wire (`image_skipped`), and lands in `media_warnings` and the
ledger's `error` when the capture holds no record image.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import tests.test_backend as tb
import tests.test_backend_ancestry as ta

_NOT_ARMED = "no download was armed for this capture"
_TIMED_OUT = (
    "no download from the armed site arrived within 20s of the capture — did the site's own "
    "Download actually start?"
)


@pytest.mark.parametrize(
    ("status", "skipped", "want"),
    [
        # The 57e4eb68 case: the record has an image, nothing was armed. Name the remedy.
        ("failed", _NOT_ARMED, "one-call Ancestry image flow"),
        # An armed download that never arrived (BUG-018/BUG-022): worth saying anywhere.
        ("failed", _TIMED_OUT, "arrived within 20s"),
        ("none", _TIMED_OUT, "arrived within 20s"),
        # "Nothing was armed" off Ancestry is every ordinary web page: not worth a warning.
        ("none", _NOT_ARMED, None),
        # ...in BOTH spellings: an ordinary capture asks `takeStagedDownload` with its own URL
        # as the key and gets the short one (Codex review, 2026-10-21).
        ("none", "no download was armed", None),
        ("failed", "no download was armed", "one-call Ancestry image flow"),
        # We hold the record image, or the record has none: nothing is missing.
        ("full", _TIMED_OUT, None),
        ("degraded-canvas", _TIMED_OUT, None),
        ("index-only-no-image", _NOT_ARMED, None),
        (None, _NOT_ARMED, None),
        ("failed", None, None),
        ("failed", "   ", None),
    ],
)
def test_REQ_010_when_a_reason_is_worth_recording(status, skipped, want):
    from capsule.backend.app import _missing_image_reason

    got = _missing_image_reason(status, skipped)
    if want is None:
        assert got is None
    else:
        assert got is not None and want in got and got.startswith("NO RECORD IMAGE: ")


def test_REQ_010_the_reason_is_flattened_and_capped():
    """It is extension-supplied text headed for a manifest and a ledger row."""
    from capsule.backend.app import _missing_image_reason

    got = _missing_image_reason("none", "line one\nline\ttwo\x00" + "x" * 1000)
    assert got is not None
    assert "\n" not in got and "\t" not in got and "\x00" not in got
    assert len(got) < 400


def _ledger_rows(db_path: Path):
    from capsule.store.ledger import AncestryLedger

    ledger = AncestryLedger(db_path)
    try:
        return ledger.latest(ta._APID)
    finally:
        ledger.close()


def test_REQ_010_the_57e4eb68_scenario_is_on_disk_and_in_the_ledger(tmp_path):
    """A plain capture of an image-backed Ancestry record: both records say why, and the fix."""
    client, backend = ta._client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": ta._ANCESTRY_URL,
                "html": ta._PAGE,
                "expected_apid": ta._APID,
                "image_skipped": _NOT_ARMED,
            },
            headers=ta._AUTH,
        )
        assert resp.status_code == 200, resp.text
    finally:
        backend.app.dependency_overrides.clear()
    (manifest_path,) = (tmp_path / "captures").glob("*/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["image"] == "failed"
    (warning,) = [w for w in manifest["media_warnings"] if w.startswith("NO RECORD IMAGE")]
    assert _NOT_ARMED in warning and "one-call Ancestry image flow" in warning
    row = _ledger_rows(tmp_path / "capsule.db")
    assert row.event == "captured" and row.image_status == "failed"
    assert row.error == warning
    assert not row.mined


def test_REQ_010_an_ordinary_page_with_nothing_armed_gets_no_warning(tmp_path):
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        resp = client.post(
            "/capture",
            json={"url": tb._NYC_URL, "html": tb._PAGE, "image_skipped": _NOT_ARMED},
            headers=tb._AUTH,
        )
        assert resp.status_code == 200, resp.text
    finally:
        backend.app.dependency_overrides.clear()
    (manifest_path,) = (tmp_path / "captures").glob("*/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert not any(w.startswith("NO RECORD IMAGE") for w in manifest.get("media_warnings", []))


def test_REQ_010_an_armed_download_that_never_came_is_recorded_off_ancestry(tmp_path):
    """The BUG-022 shape: armed on a site, the export never arrived. The folder says so."""
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        resp = client.post(
            "/capture",
            json={"url": tb._NYC_URL, "html": tb._PAGE, "image_skipped": _TIMED_OUT},
            headers=tb._AUTH,
        )
        assert resp.status_code == 200, resp.text
    finally:
        backend.app.dependency_overrides.clear()
    (manifest_path,) = (tmp_path / "captures").glob("*/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["image"] == "none"
    assert any("arrived within 20s" in w for w in manifest["media_warnings"])


def test_REQ_010_the_extension_sends_the_reason_and_may_drop_it_for_an_older_backend():
    background = (Path(__file__).resolve().parent.parent / "extension" / "background.js").read_text(
        encoding="utf-8"
    )
    assert "image_skipped: o.imageSkipped || undefined," in background
    assert re.search(r'DROPPABLE_WIRE_FIELDS = new Set\(\[[^\]]*"image_skipped"', background)
    # The backend's "nothing was armed" prefix must begin every such extension reason.
    from capsule.backend.app import _NOT_ARMED as prefix

    intercept = (
        Path(__file__).resolve().parent.parent / "extension" / "download-intercept.js"
    ).read_text(encoding="utf-8")
    spelled = re.findall(r'skipped: "(no download was armed[^"]*)"', background + intercept)
    assert len(spelled) == 3, spelled
    assert all(s.startswith(prefix) for s in spelled)


def test_REQ_010_found_a_document_capture_with_an_apid_still_lands_in_the_ledger(tmp_path):
    """Codex review: `image_reason` was only assigned on the html branch, so a document
    capture naming an apid raised AFTER its folder was written, leaving no ledger event."""
    import tests.test_backend_document as td

    client, backend = ta._client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": ta._ANCESTRY_URL,
                "document": {"data_base64": td._b64(td._PDF)},
                "expected_apid": ta._APID,
                "image_skipped": _NOT_ARMED,
            },
            headers=ta._AUTH,
        )
    finally:
        backend.app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    row = _ledger_rows(tmp_path / "capsule.db")
    assert row is not None and row.event == "captured" and row.error is None
