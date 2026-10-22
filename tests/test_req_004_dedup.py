"""REQ-004 §1 — atomic capture dedup: block only what is verifiably already held.

The contract under test:
- Identity is the RECORD, not the URL string: a declared `_APID`, an apid-less
  Ancestry record URL (either shape), or — fallback — the URL *including its query*
  (RootsIreland's record id lives there; query-stripping would false-block every
  record on the site behind the first one captured, and a false block loses
  evidence, while a false "new" merely re-captures).
- Only a VERIFIED-COMPLETE capture blocks (verify passes; image terminal-good or
  legitimately absent). A tampered/partial capture yields `dedup: "repaired"` and
  the capture proceeds.
- `remine=true` is the explicit human override.
- Two dispatches of the same identity cannot interleave (in-flight guard).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from capsule.contracts.evidence import GatingStatus

_DT = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)

_RI_URL = "https://www.rootsireland.ie/ifhf/view_detail.php?recordid=277156"
_RI_URL_2 = "https://www.rootsireland.ie/ifhf/view_detail.php?recordid=277157"
_ANC_SEARCH = "https://www.ancestry.com/search/collections/6061/records/54221383"
_ANC_DISCOVERY = "https://www.ancestry.com/discoveryui-content/view/54221383:6061"

_PAGE = (
    "<html><head><title>Marriage record</title></head><body>"
    "<div>Pat Doyle married 7 Jan 1845</div></body></html>"
)
_RAW = {
    "record_type": "marriage",
    "personas": [{"id": "p1", "label": "Pat Doyle", "role": "groom"}],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Marriage Date",
            "value": "7 Jan 1845",
            "evidence_type": "stated",
            "snippet": "Pat Doyle married 7 Jan 1845",
        }
    ],
}


def _capture_into(tmp_path, url: str):
    """Write one real capture (fake fetch + fake model) and return its outcome."""
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    n = 0

    def _ids() -> str:
        nonlocal n
        n += 1
        return f"id-{n}"

    fetcher = FakeFetcher(
        PageSnapshot(
            url=url, html=_PAGE, gating=GatingStatus.GATED, fetched_at=_DT,
            title="Marriage record",
        )
    )
    agent = Agent(
        name="extractor", model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT, new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    try:
        return run_capture(
            url, fetcher=fetcher, agent=agent, repository=repo,
            captures_root=tmp_path / "captures", now=lambda: _DT, new_id=_ids,
        )
    finally:
        repo.close()


def _find(tmp_path, url, apid=None):
    from capsule.pipeline.dedup import find_capture

    return find_capture(
        url, apid, db_path=tmp_path / "capsule.db", captures_root=tmp_path / "captures"
    )


# -- identity -----------------------------------------------------------------


def test_identity_query_string_is_kept_for_url_fallback():
    """RootsIreland's record id lives in the query — two records, two identities."""
    from capsule.pipeline.dedup import record_identity

    assert record_identity(_RI_URL) != record_identity(_RI_URL_2)
    assert record_identity(_RI_URL) == record_identity(_RI_URL + "#anchor")


def test_identity_unifies_apid_and_both_ancestry_url_shapes():
    from capsule.pipeline.dedup import record_identity

    by_apid = record_identity("https://elsewhere.example/x", "1,6061::54221383")
    assert by_apid == record_identity(_ANC_SEARCH)
    assert by_apid == record_identity(_ANC_DISCOVERY)


# -- find_capture -------------------------------------------------------------


def test_verified_complete_capture_is_found_and_blockable(tmp_path):
    outcome = _capture_into(tmp_path, _RI_URL)
    existing = _find(tmp_path, _RI_URL)
    assert existing is not None
    assert existing.capture_id == outcome.capture_folder.name
    assert existing.complete is True


def test_a_different_record_on_the_same_site_is_not_a_duplicate(tmp_path):
    _capture_into(tmp_path, _RI_URL)
    assert _find(tmp_path, _RI_URL_2) is None


def test_tampered_capture_is_found_but_not_blockable(tmp_path):
    """Verify-failing prior capture = repairable, never a block (BUG-007 shape)."""
    outcome = _capture_into(tmp_path, _RI_URL)
    (outcome.capture_folder / "snapshot.html").unlink()
    existing = _find(tmp_path, _RI_URL)
    assert existing is not None
    assert existing.complete is False


def test_apidless_ancestry_capture_is_found_via_the_other_url_shape(tmp_path):
    """No ledger row exists (no apid was declared) — the manifest scan must still
    match the record by {collection, record}, across URL shapes."""
    _capture_into(tmp_path, _ANC_SEARCH)
    existing = _find(tmp_path, _ANC_DISCOVERY)
    assert existing is not None
    # An Ancestry record capture with no declared image status is NOT provably
    # complete — held, but repairable, never a block.
    assert existing.complete is False


# -- review findings (2026-07-17) ---------------------------------------------


def _mined_ledger_row(tmp_path, capture_id: str) -> None:
    """Write the same mined ledger row the backend writes after a real capture."""
    from capsule.store.ledger import AncestryLedger

    ledger = AncestryLedger(tmp_path / "capsule.db")
    try:
        ledger.record_capture(
            "6061:54221383",
            capture_id=capture_id,
            image_status="full",
            assertion_count=1,
            persona_count=1,
            verify_ok=True,
        )
    finally:
        ledger.close()


def test_ledger_mined_row_blocks_while_its_folder_is_held(tmp_path):
    """The ledger fast path itself (review MEDIUM: it had zero direct coverage)."""
    outcome = _capture_into(tmp_path, _ANC_SEARCH)
    _mined_ledger_row(tmp_path, outcome.capture_folder.name)
    existing = _find(tmp_path, _ANC_SEARCH)
    assert existing is not None
    assert existing.complete is True
    assert existing.reason == "ledger: mined"


def test_trashed_capture_never_blocks_a_recapture(tmp_path):
    """Review HIGH: the append-only ledger remembers folders the collection no
    longer holds. `trash_capture` moves the folder to `_trash/` and (by design)
    leaves the rows — a mined row whose folder is gone must NOT block."""
    from capsule.pipeline.trash import trash_capture

    outcome = _capture_into(tmp_path, _ANC_SEARCH)
    _mined_ledger_row(tmp_path, outcome.capture_folder.name)
    assert _find(tmp_path, _ANC_SEARCH).complete is True  # held: blockable

    trash_capture(tmp_path / "captures", outcome.capture_folder.name, now=lambda: _DT)

    existing = _find(tmp_path, _ANC_SEARCH)
    # The folder is gone from the active collection — nothing may block now.
    assert existing is None or existing.complete is False


# -- the backend choke point --------------------------------------------------

_TOKEN = "test-token"
_AUTH = {"X-Capsule-Token": _TOKEN}


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
        name="extractor", model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT, new_id=lambda: "call-1",
    )
    return TestClient(app), backend


def test_second_capture_of_same_record_is_blocked_with_duplicate_of(tmp_path):
    client, backend = _client(tmp_path)
    try:
        first = client.post(
            "/capture", json={"url": _RI_URL, "html": _PAGE, "gating": "gated"},
            headers=_AUTH,
        )
        assert first.status_code == 200, first.text
        assert first.json()["dedup"] == "new"
        capture_id = first.json()["capture_folder"].replace("\\", "/").rsplit("/", 1)[-1]

        second = client.post(
            "/capture", json={"url": _RI_URL, "html": _PAGE, "gating": "gated"},
            headers=_AUTH,
        )
        assert second.status_code == 409, second.text
        body = second.json()
        assert body["dedup"] == "blocked"
        assert body["duplicate_of"] == capture_id
        assert "remine" in body["detail"]  # the override is named, not hidden

        # And nothing was written: still exactly one capture folder.
        folders = [
            p for p in (tmp_path / "captures").iterdir()
            if p.is_dir() and not p.name.startswith((".", "_"))
        ]
        assert len(folders) == 1
    finally:
        backend.app.dependency_overrides.clear()


def test_remine_true_overrides_the_block(tmp_path):
    client, backend = _client(tmp_path)
    try:
        assert (
            client.post(
                "/capture", json={"url": _RI_URL, "html": _PAGE, "gating": "gated"},
                headers=_AUTH,
            ).status_code
            == 200
        )
        again = client.post(
            "/capture",
            json={"url": _RI_URL, "html": _PAGE, "gating": "gated", "remine": True},
            headers=_AUTH,
        )
        assert again.status_code == 200, again.text
    finally:
        backend.app.dependency_overrides.clear()


def test_broken_prior_capture_is_repaired_not_blocked(tmp_path):
    client, backend = _client(tmp_path)
    try:
        first = client.post(
            "/capture", json={"url": _RI_URL, "html": _PAGE, "gating": "gated"},
            headers=_AUTH,
        )
        assert first.status_code == 200
        first_id = first.json()["capture_folder"].replace("\\", "/").rsplit("/", 1)[-1]
        (tmp_path / "captures" / first_id / "snapshot.html").unlink()

        second = client.post(
            "/capture", json={"url": _RI_URL, "html": _PAGE, "gating": "gated"},
            headers=_AUTH,
        )
        assert second.status_code == 200, second.text
        assert second.json()["dedup"] == "repaired"
        assert second.json()["duplicate_of"] == first_id
    finally:
        backend.app.dependency_overrides.clear()


def test_expected_apid_ledger_block_then_trash_then_recapture(tmp_path):
    """The generalized R6.3 path end-to-end: mined via ledger -> 409; trashed ->
    the same request proceeds again (review HIGH, exercised through the endpoint)."""
    from capsule.pipeline.trash import trash_capture

    body = {
        "url": _ANC_SEARCH,
        "html": _PAGE,
        "gating": "gated",
        "expected_apid": "1,6061::54221383",
    }
    client, backend = _client(tmp_path)
    try:
        first = client.post("/capture", json=body, headers=_AUTH)
        assert first.status_code == 200, first.text
        first_id = first.json()["capture_folder"].replace("\\", "/").rsplit("/", 1)[-1]

        second = client.post("/capture", json=body, headers=_AUTH)
        assert second.status_code == 409, second.text
        assert second.json()["dedup"] == "blocked"
        assert second.json()["duplicate_of"] == first_id

        trash_capture(tmp_path / "captures", first_id, now=lambda: _DT)
        third = client.post("/capture", json=body, headers=_AUTH)
        assert third.status_code == 200, third.text
    finally:
        backend.app.dependency_overrides.clear()


def test_two_rapid_dispatches_yield_exactly_one_capture(tmp_path):
    """REQ-004 §1 acceptance, with GENUINE overlap: the first dispatch is held
    open inside the model call while the second arrives (review MEDIUM)."""
    import threading

    from capsule.agent.base import TransportResult
    from capsule.agent.fake import FakeTransport

    entered = threading.Event()
    release = threading.Event()

    class HeldTransport(FakeTransport):
        def send(self, **kwargs) -> TransportResult:
            entered.set()
            assert release.wait(timeout=30), "test deadlock: release never set"
            return super().send(**kwargs)

    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.backend import app as backend
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=_TOKEN,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor", model="claude-opus-4-8",
        transport=HeldTransport(json.dumps(_RAW)),
        now=lambda: _DT, new_id=lambda: "call-1",
    )
    client = TestClient(app)
    body = {"url": _RI_URL, "html": _PAGE, "gating": "gated"}
    results: dict = {}

    def _first():
        results["first"] = client.post("/capture", json=body, headers=_AUTH)

    try:
        worker = threading.Thread(target=_first)
        worker.start()
        assert entered.wait(timeout=30), "first dispatch never reached the model call"
        # The first capture is mid-flight and holds the identity: the second
        # dispatch must be refused NOW, not create a sibling.
        second = client.post("/capture", json=body, headers=_AUTH)
        assert second.status_code == 409, second.text
        assert second.json()["dedup"] == "blocked"
        assert "in flight" in second.json()["detail"]
        release.set()
        worker.join(timeout=60)
        assert results["first"].status_code == 200, results["first"].text

        folders = [
            p for p in (tmp_path / "captures").iterdir()
            if p.is_dir() and not p.name.startswith((".", "_"))
        ]
        assert len(folders) == 1  # exactly one capture exists
    finally:
        release.set()
        backend.app.dependency_overrides.clear()


def test_inflight_identity_is_released_when_the_capture_fails(tmp_path):
    """A failed capture must not leave its identity claimed forever (review): an
    unparseable model reply raises inside the guarded section; a retry must proceed.
    (This used "no API key -> 503"; since v0.2 a missing key archives instead.)"""
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
        name="extractor", model="m", transport=FakeTransport("this is not JSON")
    )
    body = {"url": _RI_URL, "html": _PAGE, "gating": "gated"}
    try:
        client = TestClient(app)
        first = client.post("/capture", json=body, headers=_AUTH)
        assert first.status_code >= 400 and first.status_code != 409, first.text
        # The identity was released on failure — the retry fails the SAME way, NOT with
        # the in-flight 409.
        retry = client.post("/capture", json=body, headers=_AUTH)
        assert retry.status_code == first.status_code, retry.text
    finally:
        backend.app.dependency_overrides.clear()


def test_document_capture_dedups_like_any_other(tmp_path):
    """The dedup check sits before the html/document split (review MEDIUM) —
    prove it on the wire: same document URL twice -> blocked; remine -> proceeds."""
    import base64

    client, backend = _client(tmp_path)
    doc_body = {
        "url": "https://example.org/records/cert.pdf",
        "document": {
            "data_base64": base64.b64encode(
                b"%PDF-1.4\n1 0 obj test certificate\n%%EOF"
            ).decode("ascii"),
            "filename": "cert.pdf",
            "mime": "application/pdf",
        },
    }
    try:
        first = client.post("/capture", json=doc_body, headers=_AUTH)
        assert first.status_code == 200, first.text
        second = client.post("/capture", json=doc_body, headers=_AUTH)
        assert second.status_code == 409, second.text
        assert second.json()["dedup"] == "blocked"
        third = client.post("/capture", json={**doc_body, "remine": True}, headers=_AUTH)
        assert third.status_code == 200, third.text
    finally:
        backend.app.dependency_overrides.clear()


def test_inflight_guard_admits_one_holder_per_identity():
    from capsule.backend.app import _begin_inflight, _end_inflight

    identity = "url:https://example.test/r?id=1"
    assert _begin_inflight(identity) is True
    try:
        assert _begin_inflight(identity) is False  # second dispatch: refused
        assert _begin_inflight("url:https://example.test/r?id=2") is True  # others fine
        _end_inflight("url:https://example.test/r?id=2")
    finally:
        _end_inflight(identity)
    assert _begin_inflight(identity) is True  # released -> available again
    _end_inflight(identity)


# -- capsule query (REQ-004 §4) ----------------------------------------------


def test_capsule_query_reports_the_existing_capture(tmp_path):
    from typer.testing import CliRunner

    from capsule.cli.app import app as cli_app

    outcome = _capture_into(tmp_path, _RI_URL)
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "query", "--url", _RI_URL,
            "--db", str(tmp_path / "capsule.db"),
            "--captures", str(tmp_path / "captures"),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["captured"] is True
    assert payload["capture_id"] == outcome.capture_folder.name
    assert payload["complete"] is True

    miss = runner.invoke(
        cli_app,
        [
            "query", "--url", _RI_URL_2,
            "--db", str(tmp_path / "capsule.db"),
            "--captures", str(tmp_path / "captures"),
        ],
    )
    assert json.loads(miss.output)["captured"] is False


# -- BUG-024: the Irish census site keeps its record id in the fragment --------

_CENSUS = "https://nationalarchives.ie/collections/search-the-census/census-record/"


def test_census_records_reached_from_search_are_two_identities():
    """Search results link to `census-record/#id=…`; the page reads the id from there."""
    from capsule.pipeline.dedup import record_identity

    first = record_identity(_CENSUS + "#id=2285856&c20_year=1911")
    second = record_identity(_CENSUS + "#id=2285857&c20_year=1911")
    assert first != second
    assert first != record_identity(_CENSUS)


def test_both_forms_of_a_census_link_are_one_record():
    """The search link (`#id=`) and "Link to record" (`?id=`) name the same record."""
    from capsule.pipeline.dedup import record_identity

    by_fragment = record_identity(_CENSUS + "#id=2285856&c20_year=1911")
    assert by_fragment == record_identity(_CENSUS + "?id=2285856&c20_year=1911")
    assert by_fragment == record_identity(
        "https://www.NationalArchives.ie/collections/search-the-census/census-record/"
        "?c20_year=1911&id=2285856&utm_source=x#gf_15"
    )


def test_a_second_census_record_is_not_blocked_by_the_first(tmp_path):
    _capture_into(tmp_path, _CENSUS + "#id=2285856&c20_year=1911")
    assert _find(tmp_path, _CENSUS + "?id=2285856&c20_year=1911") is not None
    assert _find(tmp_path, _CENSUS + "#id=2285857&c20_year=1911") is None


def test_fragments_elsewhere_are_still_dropped():
    from capsule.pipeline.dedup import record_identity

    page = "https://nationalarchives.ie/collections/search-the-census/"
    assert record_identity(page + "#id=1") == record_identity(page)
