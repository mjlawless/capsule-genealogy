"""A scripted, offline Transport for tests — never touches the network.

`FakeTransport` records every `send(...)` call and returns pre-scripted results,
so the Agent abstraction and (later) the pipeline can be tested deterministically
without an API key. A response may be given as a plain string (wrapped into a
`TransportResult`), a `TransportResult`, or a list cycled through per call.
"""
from __future__ import annotations

from typing import Optional, Sequence, Union

from capsule.agent.base import PromptImage, TransportResult

_Scripted = Union[str, TransportResult]


class FakeTransport:
    """Records calls; replays scripted responses in order (repeats the last)."""

    def __init__(self, responses: Union[_Scripted, Sequence[_Scripted]]) -> None:
        if isinstance(responses, (str, TransportResult)):
            responses = [responses]
        if not responses:
            raise ValueError("FakeTransport needs at least one scripted response")
        self._responses = list(responses)
        self._index = 0
        self.calls: list[dict] = []

    def send(
        self,
        *,
        model: str,
        system: Optional[str],
        prompt: str,
        max_tokens: int,
        images: Sequence[PromptImage] = (),
    ) -> TransportResult:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "images": list(images),
            }
        )
        # Replay in order; once exhausted, keep returning the final scripted item.
        scripted = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        if isinstance(scripted, TransportResult):
            return scripted
        return TransportResult(text=scripted, model=model)
