"""BUG-026: a page label stored before BUG-019 must not stop dedup recognising the page.

Before BUG-019 the backend kept any dropdown's text as `page_label`, so an NYC capture holds
"Select Language". Today's request for the same page carries no label, so the two never
matched and the page was captured twice. The held label now faces the same filter as a new
request (`accepted_page_label`), except on Ancestry, where a held label may be a real extra
image (REQ-009) and dropping it would falsely block the record's default image.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import pytest

from capsule.contracts.evidence import GatingStatus
from capsule.pipeline.dedup import find_capture

_DT = datetime(2026, 9, 25, 0, 59, 51, tzinfo=timezone.utc)
_NYC = "https://a860-historicalvitalrecords.nyc.gov/view/1000001"
_RI = "https://www.rootsireland.ie/ifhf/quis.php?page=0"
_ANC = "https://www.ancestry.com/discoveryui-content/view/54221383:6061"
_PAGE = (
    "<html><head><title>Death record</title></head><body>"
    "<div>Pat Doyle died 7 Jan 1880</div></body></html>"
)
_RAW = {
    "record_type": "death",
    "personas": [{"id": "p1", "label": "Pat Doyle", "role": "deceased"}],
    "assertions": [{
        "persona_id": "p1", "field": "Death Date", "value": "7 Jan 1880",
        "evidence_type": "stated", "snippet": "Pat Doyle died 7 Jan 1880",
    }],
}


def _hold(tmp_path, url: str, page_label: Optional[str]) -> str:
    """Write one capture with `page_label` stored as given (as the pre-BUG-019 backend did,
    bypassing today's request filter), and return its folder name."""
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

    fetcher = FakeFetcher(PageSnapshot(
        url=url, html=_PAGE, gating=GatingStatus.PUBLIC, fetched_at=_DT, title="Death record",
    ))
    agent = Agent(
        name="extractor", model="claude-opus-5-5", transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT, new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    try:
        outcome = run_capture(
            url, fetcher=fetcher, agent=agent, repository=repo,
            captures_root=tmp_path / "captures", now=lambda: _DT, new_id=_ids,
            page_label=page_label,
        )
    finally:
        repo.close()
    manifest = json.loads((outcome.capture_folder / "manifest.json").read_text("utf-8"))
    assert manifest["source"].get("page_label") == page_label  # stored as read
    return outcome.capture_folder.name


def _find(tmp_path, url: str, page_label: Optional[str] = None):
    return find_capture(url, None, db_path=tmp_path / "capsule.db",
                        captures_root=tmp_path / "captures", page_label=page_label)


def test_a_held_junk_label_no_longer_hides_the_page(tmp_path):
    held = _hold(tmp_path, _NYC, "Select Language")
    existing = _find(tmp_path, _NYC)
    assert existing is not None and existing.capture_id == held
    assert existing.complete  # so the backend refuses the duplicate instead of making one


def test_the_backend_refuses_the_duplicate(tmp_path):
    """End to end: today's capture of the held page is refused, not repeated, even when the
    extension still sends the junk label (the backend drops it, BUG-019)."""
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    held = _hold(tmp_path, _NYC, "Select Language")
    settings = Settings(db_path=tmp_path / "capsule.db", captures_root=tmp_path / "captures",
                        token="t", fixtures_root=tmp_path / "fixtures")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor", model="claude-opus-5-5", transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT, new_id=lambda: "call-2")
    try:
        resp = TestClient(app).post(
            "/capture", headers={"X-Capsule-Token": "t"},
            json={"url": _NYC, "html": _PAGE, "gating": "public",
                  "page_label": "Select Language"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409, resp.text
    assert held in resp.text
    folders = [p.name for p in (tmp_path / "captures").iterdir() if p.is_dir()]
    assert [f for f in folders if not f.startswith(("_", "."))] == [held]


@pytest.mark.parametrize("url, label", [
    (_RI, "Kilmore"),  # RootsIreland: the label still tells records at one URL apart
    (_ANC, "image 62073_0481-00154"),  # Ancestry: a held extra image (REQ-009)
])
def test_a_label_that_still_means_something_keeps_it_apart(tmp_path, url, label):
    """The other direction, the evidence-losing one: a held label that IS an identity must
    not be read as no label, or it would block a different image or record."""
    held = _hold(tmp_path, url, label)
    assert _find(tmp_path, url) is None
    found = _find(tmp_path, url, page_label=label)
    assert found is not None and found.capture_id == held
