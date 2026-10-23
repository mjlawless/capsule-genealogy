"""Overlay the correction log onto `CaptureRecord`s, for the served index (M1).

A capture folder is sealed: `extraction.json` is what the model read at capture
time and is never rewritten. A human correction lives only in the database, as a
superseding (edit, retract) or new (add) assertion plus a `correction_event`. So
the folder-built records show the model's reading, and the page served by the
backend, the one place corrections are made, lays the current state over them.

Only captures with at least one correction are touched: every other record is
returned exactly as `read_all` built it, so a capture nobody corrected renders the
same on the served page as in the static `captures/index.html`. The static file
never gets this overlay (spec `captures-index/v2` §6: static for reading).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Callable, Optional, Sequence

from capsule.collection.read import AssertionView, CaptureRecord, CorrectionView
from capsule.contracts.evidence import Assertion, CorrectionAction, CorrectionEvent
from capsule.store.repository import Repository


def overlay_corrections(
    records: Sequence[CaptureRecord], repository: Repository
) -> list[CaptureRecord]:
    """`records` with each corrected capture's facts replaced by their current state."""
    events_by_source = repository.correction_events_by_source()
    if not events_by_source:
        return list(records)
    out: list[CaptureRecord] = []
    for record in records:
        events = events_by_source.get(record.source_id or "")
        out.append(_overlay(record, events, repository) if events else record)
    return out


def _stamp(moment: object) -> Optional[str]:
    if isinstance(moment, datetime):
        return moment.isoformat()
    return str(moment) if moment else None


def _view(event: CorrectionEvent) -> CorrectionView:
    return CorrectionView(
        action=event.action.value,
        previous_value=event.previous_value,
        new_value=event.new_value,
        actor=event.actor,
        created_at=_stamp(event.created_at),
        rationale=event.rationale,
    )


def _overlay(
    record: CaptureRecord, events: list[CorrectionEvent], repository: Repository
) -> CaptureRecord:
    source_id = record.source_id or ""
    rows = {a.id: a for a in repository.assertions_for_source(source_id)}
    # The append-only schema allows one successor per row (`ux_assertion_supersedes_id`),
    # so each fact is a simple chain: original -> edit -> edit -> ... (-> retract).
    successor = {a.supersedes_id: a.id for a in rows.values() if a.supersedes_id}
    produced_by = {e.assertion_id: e for e in events}
    labels = {p.id: p.label for p in repository.personas_for_source(source_id)}

    def words(snippet_id: str) -> Optional[str]:
        try:
            return repository.get_snippet(snippet_id).verbatim_text
        except LookupError:
            return None

    def follow(start: str) -> tuple[Optional[Assertion], tuple[CorrectionView, ...]]:
        chain: list[CorrectionView] = []
        current, seen = start, {start}
        while current in successor and successor[current] not in seen:
            current = successor[current]
            seen.add(current)
            event = produced_by.get(current)
            if event is not None:
                chain.append(_view(event))
        return rows.get(current), tuple(chain)

    views: list[AssertionView] = []
    for folder_view in record.assertions:
        if folder_view.id is None or folder_view.id not in rows:
            # Not in the database (drift): show the model's reading, with no id, so the
            # page offers no correction that the backend would refuse.
            views.append(replace(folder_view, id=None))
            continue
        final, chain = follow(folder_view.id)
        views.append(_project(final, chain, labels, folder_view.persona_label, words))
    for event in events:
        if event.action is not CorrectionAction.ADD:
            continue
        final, chain = follow(event.assertion_id)
        views.append(_project(final, (_view(event), *chain), labels, "?", words))

    return replace(
        record,
        assertions=tuple(views),
        reviewed_at=_stamp(events[-1].created_at),
        correction_count=len(events),
    )


def _project(
    final: Optional[Assertion],
    chain: tuple[CorrectionView, ...],
    labels: dict[str, str],
    fallback_label: str,
    words: Callable[[str], Optional[str]],
) -> AssertionView:
    """The display row for the end of one fact's chain."""
    if final is None:  # unreachable with an intact graph; never raise inside the index
        return AssertionView(fallback_label, "", "", "", corrections=chain)
    label = labels.get(final.persona_id, fallback_label)
    snippet = words(final.snippet_id)
    if final.retracted:
        # The retracting row carries no fact of its own: show the value it removed.
        removed = chain[-1].previous_value if chain else final.verbatim_value
        return AssertionView(
            persona_label=label,
            field=final.field,
            value=removed or "",
            evidence_type=final.evidence_type.value,
            retracted=True,
            corrections=chain,
            snippet=snippet,
        )
    return AssertionView(
        persona_label=label,
        field=final.field,
        value=final.verbatim_value,
        evidence_type=final.evidence_type.value,
        id=final.id,
        corrections=chain,
        snippet=snippet,
    )
