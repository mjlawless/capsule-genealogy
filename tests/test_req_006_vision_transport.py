"""REQ-006 T1 — the Agent seam can carry images, and logs them by reference.

A transcript is only auditable if the log says exactly WHICH pixels the model
read: the media file, its page, the crop box, the rotation, and the sha256 of
the bytes actually sent. The bytes themselves never enter the agent log — a
capture folder would balloon, and the reference is re-derivable from the media.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from capsule.agent.base import Agent, AgentCall, ImageRef, PromptImage
from capsule.agent.fake import FakeTransport

_DT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
_PNG = b"\x89PNG\r\n\x1a\n-fake-pixels"


def _image(label: str = "father_name", **ref) -> PromptImage:
    fields = dict(
        media_filename="1-d-q-1903-0000148.pdf",
        page=0,
        bbox=(100, 200, 900, 260),
        rotation=90,
        width=60,
        height=800,
    )
    fields.update(ref)
    return PromptImage.from_bytes(_PNG, media_type="image/png", label=label, **fields)


def _agent(transport, **kw) -> Agent:
    return Agent(
        name="transcriber",
        model="claude-opus-5",
        transport=transport,
        now=lambda: _DT,
        new_id=lambda: "call1",
        **kw,
    )


def test_prompt_image_hashes_the_bytes_it_carries():
    img = _image()
    import hashlib

    assert img.ref.sha256 == hashlib.sha256(_PNG).hexdigest()
    assert img.ref.label == "father_name"


def test_agent_passes_images_to_transport_and_logs_refs_not_bytes():
    transport = FakeTransport("{}")
    call = _agent(transport).run("Transcribe.", images=[_image("a"), _image("b")])

    sent = transport.calls[0]["images"]
    assert [i.ref.label for i in sent] == ["a", "b"]

    assert [r.label for r in call.images] == ["a", "b"]
    payload = call.to_log()
    dumped = str(payload)
    assert base64.standard_b64encode(_PNG).decode() not in dumped
    assert "fake-pixels" not in dumped
    assert payload["images"][0]["media_filename"] == "1-d-q-1903-0000148.pdf"
    assert payload["images"][0]["bbox"] == [100, 200, 900, 260]


def test_text_only_call_is_unchanged():
    """No images → the transport is called exactly as before, log omits images."""
    transport = FakeTransport("ok")
    call = _agent(transport).run("hello")
    assert not transport.calls[0].get("images")
    assert call.images is None


def test_legacy_transport_without_images_param_still_works_for_text():
    class _Legacy:
        def send(self, *, model, system, prompt, max_tokens):
            from capsule.agent.base import TransportResult

            return TransportResult(text="ok", model=model)

    call = _agent(_Legacy()).run("hi")
    assert call.response == "ok"


def test_failed_vision_call_still_logs_its_image_refs():
    class _Boom:
        def send(self, **kwargs):
            raise RuntimeError("overloaded")

    logged: list[AgentCall] = []
    with pytest.raises(RuntimeError):
        _agent(_Boom(), sink=logged.append).run("t", images=[_image()])
    assert logged[0].images is not None and logged[0].images[0].label == "father_name"


def test_image_ref_rejects_bad_rotation_and_bbox():
    with pytest.raises(ValueError):
        ImageRef(media_filename="x.pdf", sha256="0" * 64, rotation=45, width=1, height=1)
    with pytest.raises(ValueError):
        ImageRef(
            media_filename="x.pdf", sha256="0" * 64, bbox=(10, 10, 5, 20), width=1, height=1
        )


def test_old_agent_log_without_images_still_rehydrates():
    AgentCall.model_validate(
        {
            "id": "c1", "agent": "extractor", "model": "claude-opus-4-8", "system": None,
            "prompt": "p", "response": "r", "max_tokens": 16000,
            "created_at": "2026-07-08T14:32:05+00:00",
        }
    )


# --- AnthropicTransport ---------------------------------------------------


class _Resp:
    def __init__(self):
        self.content = [type("B", (), {"type": "text", "text": "done"})()]
        self.model = "claude-opus-5"
        self.usage = None
        self.stop_reason = "end_turn"
        self._request_id = "req_x"


class _Client:
    def __init__(self):
        self.kwargs = None
        outer = self

        class _M:
            def create(self, **kwargs):
                outer.kwargs = kwargs
                return _Resp()

        self.messages = _M()


def test_anthropic_transport_sends_labelled_images_before_the_instruction():
    from capsule.agent.anthropic_transport import AnthropicTransport

    client = _Client()
    AnthropicTransport(client=client).send(
        model="claude-opus-5", system=None, prompt="Transcribe each crop.",
        max_tokens=4000, images=[_image("father_name"), _image("mother_name")],
    )
    content = client.kwargs["messages"][0]["content"]
    kinds = [b["type"] for b in content]
    assert kinds == ["text", "image", "text", "image", "text"]
    assert "father_name" in content[0]["text"]
    assert content[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(_PNG).decode("ascii"),
    }
    assert content[-1]["text"] == "Transcribe each crop."


def test_anthropic_transport_text_only_request_shape_unchanged():
    from capsule.agent.anthropic_transport import AnthropicTransport

    client = _Client()
    AnthropicTransport(client=client).send(
        model="claude-opus-4-8", system=None, prompt="p", max_tokens=10
    )
    assert client.kwargs["messages"] == [{"role": "user", "content": "p"}]


def test_anthropic_transport_sends_adaptive_thinking_at_the_configured_effort():
    """The transcriber's effort setting (model trial, 2026-09-28) reaches the request."""
    from capsule.agent.anthropic_transport import AnthropicTransport

    client = _Client()
    AnthropicTransport(client=client, effort="high").send(
        model="claude-sonnet-5-5", system=None, prompt="p", max_tokens=10
    )
    assert client.kwargs["thinking"] == {"type": "adaptive"}
    assert client.kwargs["output_config"] == {"effort": "high"}


def test_anthropic_transport_without_effort_sends_no_thinking_settings():
    """The page extractor sets no effort; its request must not change shape."""
    from capsule.agent.anthropic_transport import AnthropicTransport

    client = _Client()
    AnthropicTransport(client=client).send(
        model="claude-opus-4-8", system=None, prompt="p", max_tokens=10
    )
    assert "thinking" not in client.kwargs
    assert "output_config" not in client.kwargs


def test_transcribe_defaults_to_sonnet_5_5_at_high_effort():
    """Owner's call after the 2026-09-28 model trial."""
    from capsule.cli.app import TRANSCRIBE_EFFORT, TRANSCRIBE_MODEL

    assert (TRANSCRIBE_MODEL, TRANSCRIBE_EFFORT) == ("claude-sonnet-5-5", "high")
