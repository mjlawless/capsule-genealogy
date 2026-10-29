"""BUG-025: `find_people` and `check_coverage` page like `search_captures`.

`find_people` stopped at 100 with no way to the rest, and an unfiltered `check_coverage`
returned every claim at once (about 150,000 characters on the live archive).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from capsule.connector.archive import Archive, ConnectorSettings

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token"
_AUTH = {"X-Capsule-Token": _TOKEN}
_PEOPLE = 130
_PARISHES = 120


def _people_capture() -> tuple[dict, str]:
    names = [f"John Murphy {n:03d}" for n in range(_PEOPLE)]
    raw = {
        "record_type": "census",
        "personas": [{"label": name, "role": "household member"} for name in names],
        "assertions": [
            {"persona": name, "field": "residence", "value": "Kilmore",
             "evidence_type": "stated", "snippet": f"{name} of Kilmore."}
            for name in names
        ],
    }
    page = "<html><body>" + "".join(f"<p>{name} of Kilmore.</p>" for name in names)
    return raw, page + "</body></html>"


def _coverage_capture(prefix: str) -> tuple[dict, str]:
    parishes = [f"{prefix} Parish {n:03d}" for n in range(_PARISHES)]
    raw = {
        "record_type": "index-coverage",
        "personas": [],
        "assertions": [],
        "coverage": [
            {"place": parish, "record_type": "R.C. Baptisms", "year_start": 1845,
             "year_end": 1880, "snippet": f"{parish} R.C. Baptisms 1845-1880",
             "evidence_type": "stated"}
            for parish in parishes
        ],
    }
    page = "<html><body><table>" + "".join(
        f"<tr><td>{parish} R.C. Baptisms 1845-1880</td></tr>" for parish in parishes
    )
    return raw, page + "</table></body></html>"


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    """One page naming 130 people; two coverage pages of 120 parishes each, one public and
    one on a subscription site (hidden when gated captures are excluded)."""
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    tmp = tmp_path_factory.mktemp("bug025")
    data = tmp / "data"
    settings = Settings(db_path=data / "capsule.db", captures_root=data / "captures",
                        token=_TOKEN, fixtures_root=tmp / "fixtures")
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    try:
        for url, gating, (raw, page) in (
            ("https://www.findagrave.com/memorial/1/household", "public", _people_capture()),
            ("https://www.irishgenealogy.ie/coverage", "public", _coverage_capture("Open")),
            ("https://www.ancestry.com/coverage", "gated", _coverage_capture("Gated")),
        ):
            app.dependency_overrides[provide_agent] = lambda r=raw: Agent(
                name="extractor", model="claude-opus-5-5",
                transport=FakeTransport(json.dumps(r)), now=lambda: _DT,
                new_id=lambda: "call-1")
            resp = client.post("/capture", json={"url": url, "html": page, "gating": gating},
                               headers=_AUTH)
            assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()
    return data


def _all_pages(call, key: str, **kw) -> tuple[list[dict], list[dict]]:
    """Every page of a paged answer, by following `more` with `offset`."""
    pages, offset = [], 0
    while True:
        page = call(offset=offset, **kw)
        pages.append(page)
        offset += len(page[key])
        if not page["more"]:
            return pages, [item for p in pages for item in p[key]]
        assert page[key], "a page with more to come must not be empty"


def test_find_people_pages_through_everyone(data_dir):
    archive = Archive(ConnectorSettings(data_dir=data_dir))
    pages, people = _all_pages(archive.find_people, "people", name="murphy john", limit=50)
    assert [p["total"] for p in pages] == [_PEOPLE] * 3
    assert [p["offset"] for p in pages] == [0, 50, 100]
    assert len(people) == _PEOPLE
    assert len({p["person"] for p in people}) == _PEOPLE  # no repeats, none skipped


def test_find_people_limit_is_capped_at_100(data_dir):
    archive = Archive(ConnectorSettings(data_dir=data_dir))
    page = archive.find_people("murphy", limit=500)
    assert (page["total"], len(page["people"]), page["more"]) == (_PEOPLE, 100, True)
    last = archive.find_people("murphy", limit=100, offset=100)
    assert (len(last["people"]), last["more"]) == (_PEOPLE - 100, False)


def test_unfiltered_coverage_is_one_bounded_page(data_dir):
    page = Archive(ConnectorSettings(data_dir=data_dir)).check_coverage()
    assert page["total"] == 2 * _PARISHES
    assert (page["offset"], len(page["claims"]), page["more"]) == (0, 50, True)


def test_coverage_pages_cover_every_claim_once_in_order(data_dir):
    archive = Archive(ConnectorSettings(data_dir=data_dir))
    _, claims = _all_pages(archive.check_coverage, "claims", limit=100)
    places = [c["place"] for c in claims]
    assert len(places) == len(set(places)) == 2 * _PARISHES
    assert places == sorted(places, key=str.lower)


def test_coverage_total_counts_only_what_can_be_shown(data_dir):
    """Claims of a hidden (gated) capture are dropped BEFORE paging: the total and the
    pages agree, and no page comes up short."""
    archive = Archive(ConnectorSettings(data_dir=data_dir, include_gated=False))
    pages, claims = _all_pages(archive.check_coverage, "claims", limit=100)
    assert [p["total"] for p in pages] == [_PARISHES, _PARISHES]
    assert [len(p["claims"]) for p in pages] == [100, _PARISHES - 100]
    assert all(c["place"].startswith("Open ") for c in claims)


def test_the_tools_offer_offset_and_state_the_maximum(data_dir):
    from mcp import Client

    from capsule.connector.server import build_server

    async def tools():
        async with Client(build_server(ConnectorSettings(data_dir=data_dir))) as c:
            return {t.name: t for t in (await c.list_tools()).tools}

    listed = asyncio.run(tools())
    for name in ("find_people", "check_coverage"):
        assert "offset" in listed[name].input_schema["properties"]
        assert "max 100" in listed[name].description
    assert "parish" in listed["check_coverage"].description
