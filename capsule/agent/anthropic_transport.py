"""The real Transport — a thin adapter over the `anthropic` SDK Messages API.

Kept deliberately small: it builds one `messages.create` request and maps the
response to a `TransportResult`. All logging, retries-of-record, and the eval
flywheel live in the Agent layer, not here. The client is injectable so the
adapter is unit-tested offline against a stub (no network, no API key); in
production a real `anthropic.Anthropic()` is constructed lazily on first use.

Note: sampling params (`temperature`/`top_p`/`top_k`) are intentionally never
sent — Claude Opus 4.8 rejects them with a 400. Behavior is steered by the
prompt/system instead.

`effort` (optional) sends adaptive thinking at that effort level. Unset, the request
is byte-for-byte what it always was and the model's own default applies. The
transcriber sets it (REQ-006 model trial, 2026-09-28); the page extractor does not.
"""
from __future__ import annotations

import base64
from typing import Any, Optional, Sequence

from capsule.agent.base import PromptImage, TransportResult

# Cost-relevant usage fields to carry through when the SDK reports them.
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _user_content(prompt: str, images: Sequence[PromptImage]) -> Any:
    """A bare string for text calls (the pre-REQ-006 shape, byte for byte); for a
    vision call, each image preceded by its caption, then the instruction LAST —
    so the model reads every labelled crop before it is told what to do with them.
    """
    if not images:
        return prompt
    content: list[dict[str, Any]] = []
    for index, image in enumerate(images, start=1):
        caption = f"Image {index}" + (f": {image.ref.label}" if image.ref.label else "")
        content.append({"type": "text", "text": caption})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": base64.standard_b64encode(image.data).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": prompt})
    return content


class AnthropicTransport:
    """Sends prompts via the anthropic SDK; maps the SDK response to our shape."""

    def __init__(self, client: Optional[Any] = None, *, effort: Optional[str] = None) -> None:
        self._client = client
        self._effort = effort

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # imported lazily so tests/offline paths need no SDK/key

            self._client = anthropic.Anthropic()
        return self._client

    def send(
        self,
        *,
        model: str,
        system: Optional[str],
        prompt: str,
        max_tokens: int,
        images: Sequence[PromptImage] = (),
    ) -> TransportResult:
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": _user_content(prompt, images)}],
        }
        if system is not None:
            request["system"] = system
        if self._effort is not None:
            request["thinking"] = {"type": "adaptive"}
            request["output_config"] = {"effort": self._effort}

        response = self._get_client().messages.create(**request)

        text = "".join(
            block.text
            for block in (response.content or [])
            if getattr(block, "type", None) == "text"
        )
        usage = None
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            usage = {
                field: getattr(raw_usage, field)
                for field in _USAGE_FIELDS
                if getattr(raw_usage, field, None) is not None
            }
        return TransportResult(
            text=text,
            model=getattr(response, "model", model) or model,
            usage=usage,
            stop_reason=getattr(response, "stop_reason", None),
            request_id=getattr(response, "_request_id", None),
        )
