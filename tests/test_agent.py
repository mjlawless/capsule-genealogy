"""Tests for the Agent abstraction — capsule/agent/.

Written FIRST (RED). The Agent is the single seam every Claude API call goes
through: it logs prompt + response + model + timestamp as an AgentCall record
(which is also the agent_log/<ts>-<agent>.json payload). Tests run OFFLINE via
a FakeTransport and a stub Anthropic client — no network, no API key.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)


def _agent(**overrides):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport

    transport = overrides.pop("transport", FakeTransport("extracted-json"))
    defaults = dict(
        name="extractor",
        model="claude-opus-4-8",
        transport=transport,
        max_tokens=16000,
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    defaults.update(overrides)
    return Agent(**defaults)


# -- Agent core ------------------------------------------------------------


def test_run_returns_agentcall_with_logged_fields():
    from capsule.agent.fake import FakeTransport

    transport = FakeTransport("the extraction")
    agent = _agent(transport=transport)
    call = agent.run("Extract people from this page.", system="You are a genealogist.")

    assert call.id == "call-1"
    assert call.agent == "extractor"
    assert call.model == "claude-opus-4-8"
    assert call.system == "You are a genealogist."
    assert call.prompt == "Extract people from this page."
    assert call.response == "the extraction"
    assert call.created_at == _DT
    assert call.max_tokens == 16000


def test_run_forwards_request_to_transport():
    from capsule.agent.fake import FakeTransport

    transport = FakeTransport("ok")
    agent = _agent(transport=transport, system="default-system")
    agent.run("hello")

    assert len(transport.calls) == 1
    sent = transport.calls[0]
    assert sent["model"] == "claude-opus-4-8"
    assert sent["prompt"] == "hello"
    assert sent["system"] == "default-system"
    assert sent["max_tokens"] == 16000


def test_run_system_argument_overrides_instance_default():
    from capsule.agent.fake import FakeTransport

    transport = FakeTransport("ok")
    agent = _agent(transport=transport, system="instance-default")
    agent.run("hi", system="per-call-system")
    assert transport.calls[0]["system"] == "per-call-system"


def test_run_captures_usage_and_stop_reason():
    from capsule.agent.base import TransportResult
    from capsule.agent.fake import FakeTransport

    result = TransportResult(
        text="done",
        model="claude-opus-4-8",
        usage={"input_tokens": 1200, "output_tokens": 340},
        stop_reason="end_turn",
        request_id="req_abc",
    )
    agent = _agent(transport=FakeTransport(result))
    call = agent.run("go")
    assert call.usage == {"input_tokens": 1200, "output_tokens": 340}
    assert call.stop_reason == "end_turn"
    assert call.request_id == "req_abc"


def test_sink_receives_every_call():
    from capsule.agent.fake import FakeTransport

    logged = []
    agent = _agent(transport=FakeTransport(["a", "b"]), sink=logged.append)
    agent.run("first")
    agent.run("second")
    assert [c.response for c in logged] == ["a", "b"]


def test_agent_rejects_unsafe_name():
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport

    with pytest.raises(ValueError, match="name"):
        Agent(name="Bad Name!", model="claude-opus-4-8", transport=FakeTransport("x"))


# -- AgentCall as agent_log payload ----------------------------------------


def test_log_filename_is_capture_safe():
    from capsule.capture.manifest import _LOG_NAME_RE

    call = _agent().run("go")
    name = call.log_filename
    # Must match the manifest's agent-log name allow-list (lowercase, no T/Z).
    assert _LOG_NAME_RE.match(name), name
    assert name == "20260708-143205-000000-extractor-call1.json"


def test_BUG_log_filename_disambiguates_same_second_calls():
    """Two calls in the same second must not collide (silent agent-log loss)."""
    from capsule.agent.base import AgentCall

    def _call(call_id):
        return AgentCall(
            id=call_id, agent="extractor", model="claude-opus-4-8", system=None,
            prompt="p", response="r", max_tokens=16000, created_at=_DT,
        )

    a = _call("callabcde1")
    b = _call("callabcde2")
    assert a.log_filename != b.log_filename  # full id, not an 8-char prefix


def test_BUG_run_logs_failure_and_reraises():
    """A failed model call must still produce a faithful record (every call logged)."""

    class _Boom:
        def send(self, **kwargs):
            raise RuntimeError("rate limited")

    logged = []
    agent = _agent(transport=_Boom(), sink=logged.append)
    with pytest.raises(RuntimeError, match="rate limited"):
        agent.run("extract this", system="sys")

    assert len(logged) == 1
    call = logged[0]
    assert call.prompt == "extract this"
    assert call.system == "sys"
    assert call.error is not None and "rate limited" in call.error
    assert call.response == ""


def test_BUG_agentcall_rejects_naive_created_at():
    from capsule.agent.base import AgentCall

    with pytest.raises(ValueError, match="UTC"):
        AgentCall(
            id="c1", agent="extractor", model="claude-opus-4-8", system=None,
            prompt="p", response="r", max_tokens=16000,
            created_at=datetime(2026, 7, 8, 14, 32, 5),  # naive
        )


def test_BUG_agentcall_rejects_unsafe_agent_name():
    from capsule.agent.base import AgentCall

    with pytest.raises(ValueError, match="agent"):
        AgentCall(
            id="c1", agent="Bad Name!", model="claude-opus-4-8", system=None,
            prompt="p", response="r", max_tokens=16000, created_at=_DT,
        )


def test_agentcall_serializes_to_json_payload():
    call = _agent().run("go")
    payload = call.to_log()
    assert payload["agent"] == "extractor"
    assert payload["model"] == "claude-opus-4-8"
    assert payload["prompt"] == "go"
    assert payload["created_at"].startswith("2026-07-08T14:32:05")


def test_agentcall_flows_into_write_capture(tmp_path):
    """An AgentCall must be accepted by write_capture's agent_logs and excluded
    from the hash manifest (spec §5.1)."""
    import tests.test_capture_manifest as cap
    from capsule.capture.manifest import verify_capture, write_capture

    call = _agent().run("extract")
    source, citation, extraction, snapshot, media = cap._bundle()

    folder = write_capture(
        tmp_path,
        source=source,
        citation=citation,
        extraction=extraction,
        snapshot_html=snapshot,
        media=media,
        created_at=_DT,
        agent_logs={call.log_filename: call.to_log()},
    )
    assert (folder / "agent_log" / call.log_filename).is_file()
    assert verify_capture(folder).ok is True  # agent_log excluded from hashing


# -- AnthropicTransport (offline, stubbed client) --------------------------


class _StubUsage:
    input_tokens = 1000
    output_tokens = 250


class _StubBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _StubResponse:
    def __init__(self, text):
        self.content = [_StubBlock(text)]
        self.model = "claude-opus-4-8"
        self.usage = _StubUsage()
        self.stop_reason = "end_turn"
        self._request_id = "req_stub"


class _StubMessages:
    def __init__(self):
        self.create_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return _StubResponse("stubbed text")


class _StubClient:
    def __init__(self):
        self.messages = _StubMessages()


def test_anthropic_transport_builds_request_and_maps_response():
    from capsule.agent.anthropic_transport import AnthropicTransport
    from capsule.agent.base import TransportResult

    client = _StubClient()
    transport = AnthropicTransport(client=client)
    result = transport.send(
        model="claude-opus-4-8",
        system="sys",
        prompt="user prompt",
        max_tokens=8000,
    )
    assert isinstance(result, TransportResult)
    assert result.text == "stubbed text"
    assert result.model == "claude-opus-4-8"
    assert result.usage == {"input_tokens": 1000, "output_tokens": 250}
    assert result.stop_reason == "end_turn"
    assert result.request_id == "req_stub"

    sent = client.messages.create_kwargs
    assert sent["model"] == "claude-opus-4-8"
    assert sent["max_tokens"] == 8000
    assert sent["system"] == "sys"
    assert sent["messages"] == [{"role": "user", "content": "user prompt"}]
    # Opus 4.8 rejects sampling params — they must NOT be sent.
    assert "temperature" not in sent
    assert "top_p" not in sent


def test_anthropic_transport_omits_system_when_none():
    from capsule.agent.anthropic_transport import AnthropicTransport

    client = _StubClient()
    AnthropicTransport(client=client).send(
        model="claude-opus-4-8", system=None, prompt="p", max_tokens=100
    )
    assert "system" not in client.messages.create_kwargs


class _StubUsageWithCache:
    input_tokens = 1000
    output_tokens = 250
    cache_read_input_tokens = 800
    cache_creation_input_tokens = 40


def test_BUG_anthropic_transport_captures_cache_tokens():
    """Cache-token usage is cost-relevant provenance — don't silently drop it."""
    from capsule.agent.anthropic_transport import AnthropicTransport

    client = _StubClient()

    def _create(**kwargs):
        client.messages.create_kwargs = kwargs
        resp = _StubResponse("text")
        resp.usage = _StubUsageWithCache()
        return resp

    client.messages.create = _create
    result = AnthropicTransport(client=client).send(
        model="claude-opus-4-8", system=None, prompt="p", max_tokens=100
    )
    assert result.usage == {
        "input_tokens": 1000,
        "output_tokens": 250,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 40,
    }
