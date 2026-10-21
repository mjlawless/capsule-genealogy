"""Persist failed model calls to disk, so no call goes unrecorded.

`Agent.run` builds a faithful `AgentCall` even when the transport raises, and
emits it to its `sink` before re-raising — but a failed call never reaches a
capture folder (the pipeline aborts before `write_capture`). This sink closes
that gap for the CLI composition root: it writes failed calls to
`captures/_failed/<log_filename>.json` so the one call whose provenance matters
most is not the one with zero on-disk trace. Successful calls are ignored here
— they already live in their capture's `agent_log/`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Union

from capsule.agent.base import AgentCall

_FAILED_DIRNAME = "_failed"

_logger = logging.getLogger(__name__)


def _write_call(captures_root: Union[str, Path], call: AgentCall) -> None:
    failed_dir = Path(captures_root) / _FAILED_DIRNAME
    failed_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(call.to_log(), indent=2, ensure_ascii=False) + "\n"
    (failed_dir / call.log_filename).write_text(payload, encoding="utf-8")


def failed_call_sink(captures_root: Union[str, Path]) -> Callable[[AgentCall], None]:
    """Build an Agent `sink` that records only *failed* (transport-error) calls."""

    def _sink(call: AgentCall) -> None:
        if call.error is None:
            return  # success — recorded in the capture's own agent_log/
        _write_call(captures_root, call)

    return _sink


def record_unwritten_call(captures_root: Union[str, Path], call: AgentCall) -> None:
    """Persist a *successful* call whose capture failed downstream (build/write).

    The transport succeeded (so the Agent sink ignored it) but the capture never
    reached disk, so this paid call would otherwise have zero on-disk trace. Best
    effort: a write failure here must never mask the downstream error that
    triggered it, so it is logged and swallowed.
    """
    try:
        _write_call(captures_root, call)
    except OSError as exc:
        _logger.warning("could not record unwritten call %s: %s", call.id, exc)
