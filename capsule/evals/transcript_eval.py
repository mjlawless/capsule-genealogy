"""Score a stored machine transcript against hand-read gold (REQ-006 T6).

Gold lives in `evals/transcripts/<name>.json`, one file per certificate, naming
the capture it describes. Scoring reads the LATEST stored transcription of that
capture — no model call, so re-scoring is free; producing a fresh transcript is
`capsule transcribe`.

Each gold field gets one outcome, from the best-matching transcribed field:

* `exact`    — letter for letter (whitespace collapsed; `accept` lists alternatives).
* `honest`   — differs, but the reading carries an uncertainty marker (`[?]`,
  `[illegible]`, …) or is status illegible. §8.1: "an honest [?] is a pass."
* `wrong`    — differs with no marker: a confident misreading.
* `missing`  — no transcribed field matched.
* `disputed` — the gold value itself is questioned and awaiting the owner; shown,
  never scored (the eval has found the gold wrong before).

The gate: no `wrong` or `missing` on a `hard` field, and every expected
image-vs-index conflict emitted (§8.4, K-1905-22719).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from capsule.contracts.transcript import TranscriptComparison, TranscriptField, TranscriptStatus

_RANK = {"exact": 0, "honest": 1, "wrong": 2}


@dataclass(frozen=True)
class GoldField:
    label: str  # the form's label, for the report
    match: list[str]  # regexes tried against the normalised key and printed label
    value: str  # verbatim, as hand-read
    accept: list[str] = field(default_factory=list)  # other exact renderings
    hard: bool = False  # a confident miss here fails the gate
    disputed: Optional[str] = None  # why the gold is in question; not scored until settled


@dataclass(frozen=True)
class TranscriptGold:
    name: str
    capture_id: str
    fields: list[GoldField]
    expect_conflicts: list[str] = field(default_factory=list)  # index values that must conflict


@dataclass(frozen=True)
class FieldResult:
    label: str
    outcome: str
    expected: str
    got: Optional[str]
    hard: bool
    field_id: Optional[str] = None
    disputed: Optional[str] = None


@dataclass
class TranscriptScore:
    name: str
    results: list[FieldResult]
    conflicts_missing: list[str]

    @property
    def counts(self) -> dict[str, int]:
        tally = {k: 0 for k in ("exact", "honest", "wrong", "missing", "disputed")}
        for r in self.results:
            tally[r.outcome] += 1
        return tally

    @property
    def passed(self) -> bool:
        hard_miss = any(r.hard and r.outcome in ("wrong", "missing") for r in self.results)
        return not hard_miss and not self.conflicts_missing


def load_gold(path: Path) -> TranscriptGold:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return TranscriptGold(
            name=data["name"],
            capture_id=data["capture_id"],
            fields=[GoldField(**f) for f in data.get("fields", [])],
            expect_conflicts=list(data.get("expect_conflicts", [])),
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unusable transcript gold {path}: {exc}") from exc


def score_transcript(
    gold: TranscriptGold,
    fields: Sequence[TranscriptField],
    *,
    comparisons: Sequence[TranscriptComparison],
) -> TranscriptScore:
    results = [_score_field(g, fields) for g in gold.fields]
    conflicted = {c.index_value for c in comparisons if c.verdict == "conflict"}
    missing = [v for v in gold.expect_conflicts if v not in conflicted]
    return TranscriptScore(gold.name, results, missing)


def _score_field(gold: GoldField, fields: Sequence[TranscriptField]) -> FieldResult:
    patterns = [re.compile(p) for p in gold.match]
    candidates = [
        f for f in fields
        if any(p.search(_norm_key(f.field_key)) or p.search(_norm_label(f.form_label)) for p in patterns)
    ]
    graded = sorted(((_grade(gold, f), f) for f in candidates), key=lambda pair: _RANK[pair[0]])
    best = graded[0] if graded else None
    got = best[1].verbatim_value if best else None
    field_id = best[1].id if best else None
    if gold.disputed:
        outcome = "disputed"
    elif best is None:
        outcome = "missing"
    else:
        outcome = best[0]
    return FieldResult(gold.label, outcome, gold.value, got, gold.hard, field_id, gold.disputed)


def _grade(gold: GoldField, reading: TranscriptField) -> str:
    got = _collapse(reading.verbatim_value or "")
    if got in {_collapse(v) for v in [gold.value, *gold.accept]}:
        return "exact"
    if reading.status is TranscriptStatus.ILLEGIBLE or "[" in got:
        return "honest"
    return "wrong"


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _norm_label(label: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (label or "").lower())
