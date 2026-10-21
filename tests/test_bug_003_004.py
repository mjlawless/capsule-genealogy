"""Regressions from the REQ-001 acceptance test (BUG-003, BUG-004).

**BUG-003** — `capsule ancestry status` read `./capsule.db` while `capsule serve` wrote
`./data/capsule.db`. Both had every table, so nothing errored: `status` simply reported
"0 mined / 0 known" for a record the backend had, seconds earlier, refused to re-capture
as *already mined*. The operator's source of truth for a long run said "nothing is
done" — always.

**BUG-004** — an index-only record (the Social Security Death Index) was classified from
the CALLER's expectation rather than from the record. Told "expected" it said `failed`,
jamming the queue forever chasing a scan that does not exist; told nothing, it closed as
MINED with no image status and no follow-up — we "mined" the INDEX and threw away the
knowledge that the SS-5 application is the actual source. That is this ledger's own
invariant, displaced from images to true sources.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}

_SSDI_APID = "1,3693::35684453"
_SSDI_URL = "https://www.ancestry.com/search/collections/3693/records/35684453"
_CENSUS_APID = "1,7602::56510272"
_CENSUS_URL = "https://www.ancestry.com/search/collections/7602/records/56510272"

# An index-only record: no image-viewer link anywhere on the page.
_SSDI_PAGE = (
    "<html><head><title>U.S., Social Security Death Index</title></head><body>"
    "<p>Name: James T Doyle</p><p>Death: Feb 1969</p>"
    "<div>Original data: Social Security Administration. Social Security Death Master "
    "File. Social Security Administration.</div>"
    "</body></html>"
)
# An image-backed record: it links to its own scan.
_CENSUS_PAGE = (
    "<html><head><title>1900 United States Federal Census</title></head><body>"
    "<p>Name: James Doyal</p>"
    '<a href="/imageviewer/collections/7602/images/4114737_00288?pId=56510272">View image</a>'
    "</body></html>"
)

_RAW = {
    "record_type": "index",
    "personas": [{"id": "p1", "label": "James T Doyle", "role": "subject"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Death",
            "value": "Feb 1969",
            "evidence_type": "stated",
            "snippet": "Death: Feb 1969",
        }
    ],
    "provider_claims": [
        {
            "key": "Original data",
            "value": (
                "Social Security Administration. Social Security Death Master File. "
                "Social Security Administration."
            ),
            "snippet": "Original data: Social Security Administration.",
        }
    ],
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
    return TestClient(app), backend


# -- BUG-003 -----------------------------------------------------------------


def test_BUG_003_cli_and_backend_agree_on_the_database_path():
    """Regression: `ancestry status` must read the same DB that `serve` writes.

    It reported "0 mined / 0 known" for a record the backend had just refused to re-capture
    as "already mined" — because the two defaulted to different files.
    """
    from capsule.backend.app import get_settings
    from capsule.config import default_captures_root, default_db_path

    settings = get_settings()
    assert settings.db_path == default_db_path()
    assert settings.captures_root == default_captures_root()
    # And that default is NOT a bare capsule.db in the cwd, which is what the CLI used.
    assert default_db_path() != Path("capsule.db")


def test_BUG_003_an_empty_ledger_beside_another_database_is_flagged(tmp_path, monkeypatch):
    """A confident, silent, wrong zero is the failure mode. Ask the question instead."""
    from capsule.config import stray_databases

    monkeypatch.chdir(tmp_path)
    (tmp_path / "capsule.db").write_bytes(b"")  # the stray one, in the cwd
    (tmp_path / "data").mkdir()
    real = tmp_path / "data" / "capsule.db"
    real.write_bytes(b"")

    assert stray_databases(real), "an empty ledger next to another capsule.db must be flagged"


# -- BUG-004 -----------------------------------------------------------------


def test_BUG_004_an_index_only_record_is_classified_from_the_PAGE_not_the_caller(tmp_path):
    """The caller said an image was expected. The record says otherwise. The record wins.

    Obeying the caller filed the SSDI as `failed`, which — under the (correct) rule that a
    failed image cannot be mined — left it stuck on the queue forever, retried for a scan
    that does not exist.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _SSDI_APID,
                "url": _SSDI_URL,
                "html": _SSDI_PAGE,
                "image_intent": "expected",  # the caller is simply wrong about this record
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["image_status"] == "index-only-no-image"  # not "failed"
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_004_an_index_only_record_emits_a_follow_up_naming_the_true_source(tmp_path):
    """Taking the index is not taking the record.

    The follow-up comes from the record's OWN `Original data` line — never a hard-coded
    table. 284 databases are in play; a lookup table would be wrong for most of them.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={"expected_apid": _SSDI_APID, "url": _SSDI_URL, "html": _SSDI_PAGE},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["image_status"] == "index-only-no-image"
        assert "Social Security Administration" in body["follow_up"]

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        assert "Social Security" in ledger["follow_ups"][0]["follow_up"]
        # It IS mined — we took everything the index holds — but the real source is on the
        # record as still to be chased.
        assert ledger["mined"] == 1
    finally:
        backend.app.dependency_overrides.clear()


def test_BUG_004_a_capture_with_no_image_status_can_NEVER_be_mined():
    """The dangerous path: a record closing as done while unclassified.

    This is what lost the SS-5. `mined` is now an ALLOW-list — unclassified is not "fine",
    it is unclassified.
    """
    from capsule.store.ledger import LedgerRow

    unclassified = LedgerRow(
        apid="3693:35684453",
        event="captured",
        image_status=None,
        assertion_count=7,
        persona_count=1,
        verify_ok=True,
    )
    assert not unclassified.mined


def test_BUG_004_an_index_without_a_follow_up_is_not_mined():
    """An index we cannot point past is not a finished job."""
    from capsule.store.ledger import LedgerRow

    without = LedgerRow(
        apid="3693:35684453",
        event="captured",
        image_status="index-only-no-image",
        assertion_count=7,
        persona_count=1,
        verify_ok=True,
        follow_up=None,
    )
    assert not without.mined

    with_source = LedgerRow(
        apid="3693:35684453",
        event="captured",
        image_status="index-only-no-image",
        assertion_count=7,
        persona_count=1,
        verify_ok=True,
        follow_up="the SS-5 application",
    )
    assert with_source.mined


def test_R4_3_the_manifest_says_what_the_image_IS_and_how_big_it_is(tmp_path):
    """The capture folder must be self-describing on disk.

    Move the folder, lose the database, open it in twenty years — it must still say
    whether the image inside it is the record or a screen-resolution stand-in. Today that
    fact lived only in the ledger DB.
    """
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (3000, 2400), (90, 90, 90)).save(buffer, format="JPEG")

    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _CENSUS_APID,
                "url": _CENSUS_URL,
                "html": _CENSUS_PAGE,
                "image_intent": "expected",
                "media": [
                    {
                        "filename": "record.jpg",
                        "mime": "image/jpeg",
                        "provenance": "ancestry:download",
                        "data_base64": base64.b64encode(buffer.getvalue()).decode(),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text

        manifest = json.loads(
            (Path(resp.json()["capture_folder"]) / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["image"] == "full"  # stated explicitly, never implied (R4.3)
        entry = next(m for m in manifest["media"] if m["path"].endswith("record.jpg"))
        assert (entry["width"], entry["height"]) == (3000, 2400)  # MEASURED, not claimed
    finally:
        backend.app.dependency_overrides.clear()


def test_an_image_backed_record_whose_image_never_arrived_is_still_failed(tmp_path):
    """The other direction must keep working: a REAL missing scan is a failure.

    The census page links to its image. If we did not get it we have to go back — and the
    record must NOT be quietly reclassified as index-only.
    """
    client, backend = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "expected_apid": _CENSUS_APID,
                "url": _CENSUS_URL,
                "html": _CENSUS_PAGE,  # the page HAS an image...
                "image_intent": "expected",
                "media": [],  # ...and we did not get it
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["image_status"] == "failed"

        ledger = client.get("/ancestry/ledger", headers=_AUTH).json()
        assert ledger["mined"] == 0  # stays on the queue, as it must
    finally:
        backend.app.dependency_overrides.clear()
