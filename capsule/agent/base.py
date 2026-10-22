"""The Agent abstraction — the single seam every Claude API call goes through.

Per the plan (docs/plan.md §347) and CLAUDE.md: every model call is wrapped by
one `Agent`, which records prompt + response + model + timestamp as an
`AgentCall`. That record IS the `agent_log/<ts>-<agent>.json` payload written
into a capture folder (see docs/specs/capture-folder-v1.md §5.1), and a human
correction to an extraction later becomes an eval fixture from the same record.
Agent *upgrades* (prompt/model changes) never touch pipeline code because the
pipeline only ever sees this interface.

The actual HTTP call is delegated to a `Transport` so the pipeline and tests are
fully decoupled from the anthropic SDK: `FakeTransport` (offline tests) and
`AnthropicTransport` (the real client) both satisfy it.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from capsule.contracts.evidence import _require_utc

_logger = logging.getLogger(__name__)

# Agent role names become part of the on-disk agent-log filename, so they must
# satisfy the manifest's agent-log allow-list (lowercase, digits, _-).
_AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Chosen by measurement, not by name (2026-10-22): on 31 of the owner's pages Opus 5.5 left
# out the fewest facts, quoted every value word for word, labelled derived birth years
# "calculated", and cost the same as Opus 4.8 before it. Change it the same way: run the
# comparison, review the differences, then switch in a release.
DEFAULT_MODEL = "claude-opus-5-5"
# The model's reasoning counts toward this. A 30-result search page needed 14,008 tokens;
# 21,000 leaves room for longer ones and stays under the SDK's non-streaming limit (~21,333).
DEFAULT_MAX_TOKENS = 21000


class TransportResult(BaseModel):
    """The normalized result of one model call, independent of the SDK shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    model: str
    usage: Optional[dict[str, int]] = None
    stop_reason: Optional[str] = None
    request_id: Optional[str] = None


class ImageRef(BaseModel):
    """WHICH pixels a vision call read — the logged stand-in for image bytes (REQ-006).

    A transcript is auditable only if its provenance names the exact region the
    model saw: the capture's media file, the page within it, the crop box in that
    page's NATIVE pixel space, the rotation applied, and the sha256 of the bytes
    actually sent. The bytes themselves are never logged — they are re-derivable
    from the archived media, and embedding them would balloon every capture folder.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_filename: str
    page: Optional[int] = None  # 0-based page for PDFs; None for single images
    bbox: Optional[tuple[int, int, int, int]] = None  # (x0, y0, x1, y1); None = whole page
    rotation: int = 0  # degrees clockwise applied AFTER cropping
    scale: float = 1.0  # sent pixels / native pixels (<1 when a page was downscaled)
    width: int  # dimensions of the image as SENT
    height: int
    sha256: str  # of the bytes as sent
    label: Optional[str] = None  # the caption the model saw beside this image

    @field_validator("rotation")
    @classmethod
    def _right_angles_only(cls, value: int) -> int:
        if value not in (0, 90, 180, 270):
            raise ValueError(f"rotation must be 0/90/180/270, got {value}")
        return value

    @field_validator("bbox")
    @classmethod
    def _bbox_is_well_formed(
        cls, value: Optional[tuple[int, int, int, int]]
    ) -> Optional[tuple[int, int, int, int]]:
        if value is not None:
            x0, y0, x1, y1 = value
            if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
                raise ValueError(f"bbox must be (x0, y0, x1, y1) with x1>x0, y1>y0: {value}")
        return value


class PromptImage(BaseModel):
    """An image to send with a prompt: its bytes plus the ref that gets logged."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data: bytes
    media_type: str  # "image/png" | "image/jpeg" | ...
    ref: ImageRef

    @model_validator(mode="after")
    def _ref_hash_matches_bytes(self) -> "PromptImage":
        if hashlib.sha256(self.data).hexdigest() != self.ref.sha256:
            raise ValueError("PromptImage.ref.sha256 does not match its bytes")
        return self

    @classmethod
    def from_bytes(
        cls, data: bytes, *, media_type: str, label: Optional[str] = None, **ref_fields
    ) -> "PromptImage":
        ref = ImageRef(sha256=hashlib.sha256(data).hexdigest(), label=label, **ref_fields)
        return cls(data=data, media_type=media_type, ref=ref)


class Transport(Protocol):
    """The one method the Agent needs: send a single prompt, get a result.

    `images` is passed ONLY when a call carries images, so a text-only transport
    written before REQ-006 keeps working unchanged for text calls.
    """

    def send(
        self,
        *,
        model: str,
        system: Optional[str],
        prompt: str,
        max_tokens: int,
        images: Sequence[PromptImage] = (),
    ) -> TransportResult: ...


class AgentCall(BaseModel):
    """One logged model call — provenance of process, and a future eval fixture.

    Self-validating: `agent` and `created_at` are re-checked here (not just in
    Agent.__init__), so a record rehydrated for the eval-fixture flywheel or
    built directly can never produce a capture-folder name write_capture rejects
    or a timestamp that disagrees with its own filename. `error` is set only when
    the model call failed — the record still exists, honoring "every call logged".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    agent: str
    model: str
    system: Optional[str]
    prompt: str
    response: str
    max_tokens: int
    usage: Optional[dict[str, int]] = None
    stop_reason: Optional[str] = None
    request_id: Optional[str] = None
    error: Optional[str] = None
    # REQ-006: refs to the images sent with `prompt`, in order. None for text calls,
    # so every pre-vision agent log rehydrates unchanged.
    images: Optional[tuple[ImageRef, ...]] = None
    created_at: datetime

    @field_validator("agent")
    @classmethod
    def _agent_name_is_capture_safe(cls, value: str) -> str:
        if not _AGENT_NAME_RE.match(value):
            raise ValueError(
                f"invalid agent name {value!r}: must match {_AGENT_NAME_RE.pattern}"
            )
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @property
    def log_filename(self) -> str:
        """`<YYYYMMDD>-<HHMMSS>-<microseconds>-<agent>-<id>.json` (capture-safe).

        Microsecond resolution plus the full sanitized id (a 32-hex uuid in
        production) makes two calls colliding on the same filename effectively
        impossible — important because callers key `agent_logs` by this name, so
        a collision would silently drop a logged call. No `T`/`Z`, so it stays
        within the manifest's agent-log name allow-list.
        """
        ts = self.created_at.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        ident = re.sub(r"[^a-z0-9]", "", self.id.lower()) or "0"
        return f"{ts}-{self.agent}-{ident}.json"

    def to_log(self) -> dict:
        """The JSON-serializable agent-log payload (for write_capture agent_logs).

        `images` is omitted when None so text-call logs keep their exact shape.
        """
        exclude = {"images"} if self.images is None else set()
        return self.model_dump(mode="json", exclude=exclude)


class Agent:
    """Wraps a Transport with a fixed role name + model, logging every call.

    `now` / `new_id` are injected so tests are deterministic; production uses
    UTC-now and a UUID. `sink`, if given, is called with each AgentCall after the
    call completes — the hook the eval-fixture flywheel (M1+) will attach to.
    """

    def __init__(
        self,
        name: str,
        model: str,
        transport: Transport,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        system: Optional[str] = None,
        now: Optional[Callable[[], datetime]] = None,
        new_id: Optional[Callable[[], str]] = None,
        sink: Optional[Callable[[AgentCall], None]] = None,
    ) -> None:
        if not _AGENT_NAME_RE.match(name):
            raise ValueError(
                f"invalid agent name {name!r}: must match {_AGENT_NAME_RE.pattern}"
            )
        self.name = name
        self.model = model
        self._transport = transport
        self._max_tokens = max_tokens
        self._system = system
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._new_id = new_id or (lambda: uuid.uuid4().hex)
        self._sink = sink

    def run(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        images: Sequence[PromptImage] = (),
    ) -> AgentCall:
        """Send `prompt` (plus any `images`), log the exchange, return the AgentCall.

        If the transport raises (timeout, 5xx, dropped connection), a failure
        AgentCall is still built and sent to `sink` before the exception is
        re-raised — the call that failed is exactly the one whose provenance
        matters most, so it must never go unlogged.
        """
        resolved_system = system if system is not None else self._system
        refs = tuple(image.ref for image in images) or None
        extra: dict = {"images": tuple(images)} if images else {}
        try:
            result = self._transport.send(
                model=self.model,
                system=resolved_system,
                prompt=prompt,
                max_tokens=self._max_tokens,
                **extra,
            )
        except Exception as exc:
            call = AgentCall(
                id=self._new_id(),
                agent=self.name,
                model=self.model,
                system=resolved_system,
                prompt=prompt,
                response="",
                max_tokens=self._max_tokens,
                request_id=getattr(exc, "request_id", None),
                error=f"{type(exc).__name__}: {exc}",
                images=refs,
                created_at=self._now(),
            )
            self._emit(call)
            raise
        call = AgentCall(
            id=self._new_id(),
            agent=self.name,
            model=result.model or self.model,
            system=resolved_system,
            prompt=prompt,
            response=result.text,
            max_tokens=self._max_tokens,
            usage=result.usage,
            stop_reason=result.stop_reason,
            request_id=result.request_id,
            images=refs,
            created_at=self._now(),
        )
        self._emit(call)
        return call

    def _emit(self, call: AgentCall) -> None:
        """Fire the sink, but never let a sink failure supersede the real result.

        On the failure path especially, the sink raising would replace the
        transport exception the caller needs to see — so a sink error is logged
        and swallowed here rather than propagated.
        """
        if self._sink is None:
            return
        try:
            self._sink(call)
        except Exception as sink_exc:  # noqa: BLE001 — must not mask the call's own error
            _logger.warning("agent sink failed for call %s: %s", call.id, sink_exc)
