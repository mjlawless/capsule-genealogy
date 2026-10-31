"""Reading captures saved without a key is chosen on the index page.

A user who captured many pages before adding a key may not want them all read (each costs
money). So the popup no longer reads everything: the served index offers a tick box on each
capture still waiting, an "AI reading" filter, and Read with AI for the ticked ones (or all
the waiting ones the filters show). A capture that was already read is never read again here.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import capsule.pipeline.reextract as rx
import tests.test_backend as tb
from capsule.collection.index import write_index
from tests.test_v02_key_in_extension import client, keyring_store  # noqa: F401 — fixtures

_EXT = Path(__file__).resolve().parent.parent / "extension"


def _archive(client, n):  # noqa: F811 — the fixture is passed in
    """`n` captures saved without a key; their folder names, oldest first."""
    names = []
    for i in range(n):
        body = client.post(
            "/capture",
            json={"url": f"https://example.org/record/{i}", "html": tb._PAGE},
            headers=tb._AUTH,
        ).json()
        assert body["extraction_pending"] is True
        names.append(Path(body["capture_folder"]).name)
    return names


def _add_key():
    """From here on a key exists (after `_archive`, which must save without one)."""
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import app, provide_agent

    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor", model="m", transport=FakeTransport(json.dumps(tb._RAW))
    )


def _wait(client):  # noqa: F811
    for _ in range(200):
        status = client.get("/settings/status", headers=tb._AUTH).json()
        if not status["reading"]["running"]:
            return status
        time.sleep(0.05)
    raise AssertionError("the reading never finished")


def _captures_root(client):  # noqa: F811
    from capsule.backend.app import app, get_settings

    return app.dependency_overrides[get_settings]().captures_root


def test_only_the_chosen_captures_are_read(client):  # noqa: F811
    first, second, third = _archive(client, 3)
    _add_key()
    started = client.post("/read", json={"capture_ids": [first, third]}, headers=tb._AUTH)
    assert started.status_code == 200, started.text
    assert started.json() == {"started": 2, "total": 2, "refused": []}
    status = _wait(client)
    assert status["reading"]["done"] == 2 and status["reading"]["failed"] == 0
    assert rx.pending_captures(_captures_root(client)) == [second]


def test_a_capture_already_read_is_never_read_again(client):  # noqa: F811
    (first,) = _archive(client, 1)
    _add_key()
    client.post("/read", json={"capture_ids": [first]}, headers=tb._AUTH)
    _wait(client)
    root = Path(_captures_root(client))
    replacement = next(
        p.parent.name
        for p in root.glob("*/manifest.json")
        if json.loads(p.read_text(encoding="utf-8")).get("extracted_from") == first
    )
    for chosen, reason in ((first, "a newer capture holds its reading"),
                           (replacement, "reading it again is not offered")):
        again = client.post("/read", json={"capture_ids": [chosen]}, headers=tb._AUTH)
        assert again.status_code == 422
        (refusal,) = again.json()["detail"]["refused"]
        assert refusal["capture_id"] == chosen and reason in refusal["reason"]
    assert len(list(root.glob("*/manifest.json"))) == 2  # nothing new was written


def test_unknown_and_path_walking_ids_are_refused_and_the_rest_read(client):  # noqa: F811
    (first,) = _archive(client, 1)
    _add_key()
    started = client.post(
        "/read",
        json={"capture_ids": [first, first, "20260101-nowhere-aaaa1111", "../etc"]},
        headers=tb._AUTH,
    )
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["started"] == 1  # the duplicate is read once
    assert {r["capture_id"] for r in body["refused"]} == {"20260101-nowhere-aaaa1111", "../etc"}
    _wait(client)


def test_no_key_no_token_and_a_running_reading_are_refused(client, monkeypatch):  # noqa: F811
    (first,) = _archive(client, 1)
    chosen = {"capture_ids": [first]}
    assert client.post("/read", json=chosen).status_code == 401
    assert client.post("/read", json=chosen, headers=tb._AUTH).status_code == 400  # no key
    assert client.post("/read", json={"capture_ids": []}, headers=tb._AUTH).status_code == 422

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import app, provide_agent

    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="x", model="m", transport=FakeTransport("{}")
    )
    monkeypatch.setattr(rx._background, "running", True)
    busy = client.post("/read", json=chosen, headers=tb._AUTH)
    assert busy.status_code == 409 and "already reading" in busy.json()["detail"]
    assert rx.pending_captures(_captures_root(client)) == [first]


def test_reading_everything_from_the_popup_is_retired_with_a_pointer(client):  # noqa: F811
    old = client.post("/settings/read-pending", headers=tb._AUTH)
    assert old.status_code == 410 and "index" in old.json()["detail"]
    html = (_EXT / "popup.html").read_text(encoding="utf-8")
    assert 'id="read-pending"' not in html and 'id="read-in-index"' in html
    assert "/settings/read-pending" not in (_EXT / "popup.js").read_text(encoding="utf-8")


def test_tick_boxes_only_on_captures_waiting_and_only_on_the_served_page(
    client, monkeypatch  # noqa: F811
):
    first, second = _archive(client, 2)
    _add_key()
    client.post("/read", json={"capture_ids": [first]}, headers=tb._AUTH)
    _wait(client)
    root = Path(_captures_root(client))

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    served = client.get("/").text
    # One tick box: on the capture still waiting, never on the read one or its reading.
    assert served.count('class="pick"') == 1
    assert f'class="pick" data-capture-id="{second}"' in served
    assert 'id="readbar" data-key="missing"' in served and "1 not read yet." in served
    assert served.count(">not read yet</span>") == 1  # the read original lost its chip
    assert 'id="f-read"' in served
    assert 'data-read="not read yet"' in served and 'data-read="read"' in served
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert 'id="readbar" data-key="set"' in client.get("/").text

    static = write_index(root).read_text(encoding="utf-8")
    assert 'class="pick"' not in static and 'id="readbar"' not in static
    assert '"/read"' not in static and "__CAPSULE_TOKEN__" not in static
    assert 'data-read="not read yet"' in static and 'id="f-read"' in static


def test_no_read_bar_when_nothing_is_waiting(client):  # noqa: F811
    (first,) = _archive(client, 1)
    _add_key()
    client.post("/read", json={"capture_ids": [first]}, headers=tb._AUTH)
    _wait(client)
    served = client.get("/").text
    assert 'id="readbar"' not in served and 'class="pick"' not in served


def test_found_a_reading_set_aside_is_never_paid_for_again(client):  # noqa: F811
    """Codex review: read -> reinstate (the reading is rejected) made the original readable
    again from the index, a second charge. Refused at the door, in the worker under its
    claim, and offered nowhere on the page; `capsule extract <id>` still may (explicit)."""
    import pytest

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import app, get_settings
    from capsule.pipeline.supersede import reinstate_capture
    from capsule.store.repository import Repository

    (first,) = _archive(client, 1)
    _add_key()
    client.post("/read", json={"capture_ids": [first]}, headers=tb._AUTH)
    _wait(client)
    settings = app.dependency_overrides[get_settings]()
    repo = Repository(settings.db_path)
    try:
        reinstate_capture(repository=repo, captures_root=settings.captures_root,
                          capture_id=first, reason="wrong reading")
        before = len(list(Path(settings.captures_root).glob("*/manifest.json")))

        again = client.post("/read", json={"capture_ids": [first]}, headers=tb._AUTH)
        assert again.status_code == 422
        assert "read once" in again.json()["detail"]["refused"][0]["reason"]
        assert client.get("/settings/status", headers=tb._AUTH).json()["pending"] == 0
        served = client.get("/").text
        assert 'class="pick"' not in served and 'id="readbar"' not in served
        assert "reading it again is not offered yet" in served

        calls = []

        class Counting(FakeTransport):
            def send(self, *a, **k):
                calls.append(1)
                return super().send(*a, **k)

        agent = Agent(name="x", model="m", transport=Counting(json.dumps(tb._RAW)))
        with pytest.raises(ValueError, match="read before"):
            rx.extract_capture(repository=repo, captures_root=settings.captures_root,
                               capture_id=first, agent=agent)
        assert calls == []
        assert len(list(Path(settings.captures_root).glob("*/manifest.json"))) == before
    finally:
        repo.close()


def test_found_the_page_rechecks_the_key_when_you_come_back_to_it():
    """Codex review: an index opened before the key was added stayed disabled for good."""
    from capsule.collection.assets import INDEX_READ_JS

    assert 'window.addEventListener("focus"' in INDEX_READ_JS
    assert '"visibilitychange"' in INDEX_READ_JS
    assert 'keySet = s.api_key === "set"' in INDEX_READ_JS


def test_found_a_failed_status_check_never_ends_the_watch():
    """Codex review: one failed status fetch during a reading stopped polling for good, leaving
    the page on "Reading…" after the work finished. Browser-checked with a forced failure."""
    from capsule.collection.assets import INDEX_READ_JS

    assert ".catch(lost)" in INDEX_READ_JS and "if (!s) { lost(); return; }" in INDEX_READ_JS
    assert "schedule(5000)" in INDEX_READ_JS.split("function lost()", 1)[1].split("function poll()")[0]
    # Every poll goes through the one timer, so retries and focus never run parallel chains.
    assert INDEX_READ_JS.count("setTimeout(poll") == 1


def test_found_a_reading_from_another_window_never_clears_this_pages_ticks():
    """Codex review: when a reading started in another tab ended, this page reloaded and lost
    the ticks made here. It now says so and offers a reload instead."""
    from capsule.collection.assets import INDEX_READ_JS

    branch = INDEX_READ_JS.split("if (running && watched) { finished(r); return; }", 1)[1]
    assert branch.lstrip().startswith("if (running) { elsewhereFinished(); return; }")
    body = INDEX_READ_JS.split("function elsewhereFinished()", 1)[1].split("\n  }\n", 1)[0]
    assert "reload()" not in body.split("addEventListener", 1)[0]
    assert "box.disabled = false" in body
