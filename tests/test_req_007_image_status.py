"""REQ-007 §3.1 — the image checks were Ancestry-gated, so every other site got a free pass.

Two Archion captures on disk (2026-07-25 and 2026-08-19) hold a browser print of site chrome
plus, in the second, a **689x545 viewer thumbnail** — no register image at all — and both
report `verify_ok: true` with `image_status: null`. The bytes hash perfectly; they are the
wrong bytes. `CLAUDE.md` §12.4.

The cause was one condition in the capture handler: `image_status` was derived only
`if (req.expected_apid or on_ancestry_record)`, so `classify_image`, `MIN_USABLE_PX` and the
BUG-012 thumbnail guard were all inert off Ancestry, and `verify_ok` there meant no more than
"the hashes match".

What changes here:
  1. a MEASURED record image is now reported on ANY host — it is a fact about the bytes;
  2. every raster is measured and its real dimensions stamped into `manifest.media[]`, so
     "how big is the biggest image in this capture?" is answerable without opening files;
  3. the ABSENCE inference stays Ancestry-only. `has_record_image` looks for Ancestry's
     `/imageviewer/collections/...` link; asked about Archion it would answer "this record
     has no image" about markup it cannot read, and `index-only-no-image` is terminal-good —
     a lie that would close the record AND let dedup block a repair.

§3.2 (a real "we looked, there is none" signal) and §3.3 (the 1500px floor gating verify_ok
for image-only originals) are NOT in this change; an Archion capture still reports
`verify_ok: true`. This removes the blindfold, not yet the false PASS.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone

_DT = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

# A tile-viewer register page: one URL for all N images, no Ancestry markup anywhere.
_ARCHION_URL = "https://www.archion.de/de/viewer/churchRegister/239254?cHash=0ba8f"
_ARCHION_PAGE = (
    "<html><head><title>Taufregister 1798-1824 | Guntersblum</title></head>"
    "<body><div id='viewer'><canvas></canvas></div>"
    "<select><option selected value='0'>Image 174</option></select></body></html>"
)

_RAW = {
    "record_type": "image",
    "personas": [{"id": "p1", "label": "Guntersblum register", "role": "subject"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Volume",
            "value": "Taufregister 1798-1824",
            "evidence_type": "stated",
            "snippet": "Taufregister 1798-1824",
        }
    ],
    "provider_claims": [],
}


def _client(tmp_path):
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend import app as backend
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=_TOKEN,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    return TestClient(app), backend, settings


def _image_b64(width: int, height: int) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


def _manifest(settings, folder_name: str) -> dict:
    path = settings.captures_root / folder_name / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_REQ_007_a_viewer_canvas_image_is_classified_off_ancestry(tmp_path):
    """A screen-resolution viewer grab reports `degraded-canvas` on Archion, not None.

    Before this change the measurement was computed and then thrown away for every
    non-Ancestry host — the capture reported nothing about the only image it held.
    """
    client, backend, _settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "leaf.jpg",
                        "mime": "image/jpeg",
                        "provenance": "viewer:canvas",
                        "data_base64": _image_b64(2704, 2171),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["image_status"] == "degraded-canvas"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_thumbnail_sized_record_image_fails_off_ancestry(tmp_path):
    """The 689x545 Archion preview: below MIN_USABLE_PX, so `failed` — never silence."""
    client, backend, _settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "thumbnail.jpg",
                        "mime": "image/jpeg",
                        "provenance": "viewer:canvas",
                        "data_base64": _image_b64(689, 545),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["image_status"] == "failed"
        # And it must SAY so, not merely record it: a silent status nobody reads is how a
        # preview gets filed as a register page.
        assert any("689x545" in w for w in resp.json()["media_warnings"]), resp.json()
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_page_served_media_is_measured_into_the_manifest_but_not_gated(tmp_path):
    """Measuring is not gating: dimensions land in `media[]`; no warning, no failure.

    This is what makes the 689x545 preview visible to a consumer (and to REQ-007 §3.3's
    floor) without opening every file — while a page-served thumbnail still does not get
    treated as a failed record image, because it never claimed to be one.
    """
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                # No provenance — exactly what the in-page media sweep banks.
                "media": [
                    {
                        "filename": "thumbnail.jpg",
                        "mime": "image/jpeg",
                        "data_base64": _image_b64(689, 545),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # `"none"` = we hold no RECORD image (§3.2). The seal is banked and measured, but it
        # never claimed to be the record, so it neither satisfies nor fails anything.
        assert body["image_status"] == "none"
        assert body["media_warnings"] == []
        manifest = _manifest(settings, body["capture_folder"])
        assert manifest["media"][0]["width"] == 689
        assert manifest["media"][0]["height"] == 545
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_absence_is_never_inferred_off_ancestry(tmp_path):
    """No image captured, non-Ancestry page -> `none`. NEVER `index-only-no-image`.

    The two are not synonyms and the difference decides whether a record ever gets looked
    at again. `index-only-no-image` is a claim about the RECORD — it legitimately has no
    scan — and it is terminal-good: it closes the record and lets dedup BLOCK the repair
    capture that would fix it. Making that claim requires reading Ancestry's markup.
    `none` is a report on THIS CAPTURE's bytes, needs no site knowledge, and leaves the
    work open (§3.2).
    """
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={"url": _ARCHION_URL, "html": _ARCHION_PAGE, "gating": "gated"},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "none"
        assert _manifest(settings, body["capture_folder"])["image"] == "none"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_pdf_is_not_handed_to_the_image_decoder(tmp_path):
    """A PDF carries no width/height — it is never decoded, so no bogus dimensions."""
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _ARCHION_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "page.pdf",
                        "mime": "application/pdf",
                        "provenance": "render:page-pdf",
                        "data_base64": base64.b64encode(b"%PDF-1.4 a page render").decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        entry = _manifest(settings, resp.json()["capture_folder"])["media"][0]
        assert "width" not in entry and "height" not in entry
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_the_ledger_is_rebuilt_to_accept_none(tmp_path):
    """A live ledger predates `none`, and its CHECK would REJECT the row carrying it.

    Same shape as the BUG-005 widening, and the same hazard: `CREATE TABLE IF NOT EXISTS`
    never alters an existing table, so the constraint on disk stays as it was written. A
    capture whose status the database refuses is a capture LOST at the last step, which is
    why this is a rebuild-and-copy rather than a hope that `none` never reaches the ledger.
    """
    import sqlite3

    db = tmp_path / "capsule.db"
    old = sqlite3.connect(str(db))
    old.executescript(
        """
        CREATE TABLE ancestry_ledger_event (
            id TEXT PRIMARY KEY, apid TEXT NOT NULL, collection_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            event TEXT NOT NULL CHECK (event IN
                ('seeded','issued','started','captured','failed','remine-requested')),
            capture_id TEXT,
            image_status TEXT CHECK (image_status IS NULL OR image_status IN
                ('full', 'degraded-canvas', 'index-only-no-image', 'failed')),
            assertion_count INTEGER, persona_count INTEGER, verify_ok INTEGER,
            follow_up TEXT, error TEXT, created_at TEXT NOT NULL
        );
        INSERT INTO ancestry_ledger_event
            (id, apid, collection_id, record_id, event, image_status, created_at)
        VALUES ('e1', '7602:56510272', '7602', '56510272', 'captured', 'full',
                '2026-07-14T00:00:00+00:00');
        """
    )
    old.commit()
    old.close()

    from capsule.store.ledger import AncestryLedger

    ledger = AncestryLedger(db)
    try:
        # Every pre-existing row survived the rebuild, values intact.
        assert [(r.apid, r.image_status) for r in ledger.all_latest()] == [
            ("7602:56510272", "full")
        ]
        # And the value that would have been rejected now inserts.
        ledger.connection.execute(
            "INSERT INTO ancestry_ledger_event (id, apid, collection_id, record_id, event,"
            " image_status, created_at) VALUES (?,?,?,?,?,?,?)",
            ("e2", "7602:99", "7602", "99", "captured", "none",
             "2026-08-22T00:00:00+00:00"),
        )
        ledger.connection.commit()
        # The append-only guarantee is restored, not dropped with the old table.
        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            ledger.connection.execute("UPDATE ancestry_ledger_event SET error = 'x'")
    finally:
        ledger.close()


def test_REQ_007_none_never_reads_as_a_mined_record():
    """`none` must sit with the INCOMPLETE outcomes, never with the terminal-good ones.

    The BUG-004 invariant, restated for a new value: an allow-list decides what closes a
    record, and "we hold no image" is work outstanding — not a record legitimately without
    a scan. Getting this backwards would close a register the moment we failed to grab it.
    """
    from capsule.pipeline.dedup import _TERMINAL_GOOD
    from capsule.store.ledger import _COMPLETE_IMAGE, _INCOMPLETE_IMAGE, LedgerRow

    assert "none" not in _COMPLETE_IMAGE
    assert "none" not in _TERMINAL_GOOD
    assert "none" in _INCOMPLETE_IMAGE
    row = LedgerRow(
        apid="7602:99", event="captured", verify_ok=True, assertion_count=3,
        persona_count=1, image_status="none",
    )
    assert row.mined is False
    assert row.status == "unmined (none)"
