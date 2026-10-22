"""Score the current extractor against hand-labeled gold on real captured pages.

A *fixture* is one real capture: `snapshot.html` (the page), `expected.json` (the
extraction a correct run must produce, hand-labeled) and `meta.json` (provenance).
`run_eval` makes the SAME single extraction call the pipeline makes on the page,
then `score` compares the model's output to the gold field-by-field — so a prompt
or model change that regresses on the owner's real records is caught, not silently
shipped.

`expected.json` / `meta.json` are committed; `snapshot.html` stays local
(gitignored — captured pages may be gated/copyright/PII), so evals run where the
owner holds the captures, not in CI. `score` is a pure function and is unit-tested
offline with synthetic fixtures; only `run_eval` (the live model call) costs money.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from capsule.agent.base import Agent
from capsule.contracts.evidence import GatingStatus
from capsule.pipeline.extract import (
    RawAssertion,
    RawExtraction,
    RawPersona,
    build_extraction_prompt,
    parse_extraction,
)
from capsule.pipeline.fetch import ProvidedContentFetcher
from capsule.pipeline.fieldname import snake_case

_SNAPSHOT = "snapshot.html"
_EXPECTED = "expected.json"
_META = "meta.json"


class SnapshotMissing(Exception):
    """A fixture has no local `snapshot.html` (gitignored) — skip, don't fail.

    Distinct from the ValueErrors `load_fixture` raises for genuinely corrupt
    gold, so `capsule eval` can skip an unseeded fixture but treat bad gold as a
    loud error rather than a silent pass.
    """


@dataclass(frozen=True)
class EvalFixture:
    """One hand-labeled fixture loaded from a fixture directory."""

    name: str
    url: str
    gating: GatingStatus
    snapshot_html: str
    expected: RawExtraction


def load_fixture(fixture_dir: Path) -> EvalFixture:
    """Load `snapshot.html` + `expected.json` + `meta.json` from `fixture_dir`.

    Raises `SnapshotMissing` when only the (gitignored, local) snapshot is
    absent; raises `ValueError` (with the fixture name) for any missing/invalid
    committed file, so corrupt gold is a loud failure, never a silent skip.
    """
    name = fixture_dir.name
    snapshot_path = fixture_dir / _SNAPSHOT
    if not snapshot_path.is_file():
        raise SnapshotMissing(
            f"fixture {name!r} has no {_SNAPSHOT} — captured pages stay local "
            f"(gitignored); re-capture this page to seed it."
        )
    meta = _read_json(fixture_dir / _META, name)
    try:
        url = meta["url"]
        gating = GatingStatus(meta.get("gating", "public"))
    except (KeyError, ValueError) as exc:
        raise ValueError(f"fixture {name!r} has an invalid {_META}: {exc}") from exc
    expected = _load_expected(fixture_dir / _EXPECTED, name)
    return EvalFixture(
        name=name,
        url=url,
        gating=gating,
        snapshot_html=snapshot_path.read_text(encoding="utf-8"),
        expected=expected,
    )


def _read_json(path: Path, name: str) -> dict:
    if not path.is_file():
        raise ValueError(f"fixture {name!r} is missing {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"fixture {name!r} has invalid {path.name}: {exc}") from exc


def _load_expected(path: Path, name: str) -> RawExtraction:
    data = _read_json(path, name)
    try:
        return RawExtraction.model_validate(data)
    except Exception as exc:  # pydantic ValidationError — surface with fixture name
        raise ValueError(f"fixture {name!r} has an invalid {_EXPECTED}: {exc}") from exc


@dataclass(frozen=True)
class FieldDiff:
    """One assertion-level difference between expected and actual extraction.

    Holds structured values (not pre-formatted strings) so later consumers — a
    JSON report, snippet scoring — can read them without re-parsing; `summary`
    does the formatting.
    """

    key: str  # "<persona>::<field>"
    kind: str  # matched | missing | extra | mismatched | duplicate
    expected_value: Optional[str] = None
    expected_type: Optional[str] = None
    actual_value: Optional[str] = None
    actual_type: Optional[str] = None


@dataclass(frozen=True)
class EvalReport:
    """The result of scoring one fixture: what matched, and what drifted.

    Carries TWO scores, because they answer different questions and a single
    number hid a real regression. The field-level diffs ask "did we name each
    fact as the gold names it?"; `values_missing` asks "did we get the values off
    the page at all, whatever they are called?" A run that relabelled `Borough`
    as `death_place` and one that silently dropped `soundex` both scored 6/7
    field-level — but only the second lost evidence. See docs/design/field-naming.md.
    """

    name: str
    record_type_expected: str
    record_type_actual: str
    personas_missing: tuple[str, ...]
    personas_extra: tuple[str, ...]
    personas_duplicated: tuple[str, ...]  # labels the model emitted more than once
    role_mismatches: tuple[str, ...]  # labels present on both sides with a different role
    diffs: tuple[FieldDiff, ...]
    values_missing: tuple[str, ...] = ()  # "<persona>::<value>" gold values absent
    values_total: int = 0  # distinct (persona, value) pairs in the gold
    # Calculated/estimated facts the gold does not pin (a birth year worked out from an age).
    # Reported for a person to check, never a failure: gold pins what the page STATES, and
    # Opus 5.5 derives facts the page implies, labelled as derived, as the evidence model
    # intends (2026-10-22 model test). Its key may repeat a stated field ("Age").
    derived: tuple[FieldDiff, ...] = ()

    @property
    def record_type_ok(self) -> bool:
        return self.record_type_expected == self.record_type_actual

    @property
    def values_captured(self) -> int:
        """Gold values the model produced somewhere, under any field name."""
        return self.values_total - len(self.values_missing)

    def _count(self, kind: str) -> int:
        return sum(1 for d in self.diffs if d.kind == kind)

    @property
    def matched(self) -> int:
        return self._count("matched")

    @property
    def missing(self) -> int:
        return self._count("missing")

    @property
    def extra(self) -> int:
        return self._count("extra")

    @property
    def mismatched(self) -> int:
        return self._count("mismatched")

    @property
    def duplicated(self) -> int:
        return self._count("duplicate")

    @property
    def ok(self) -> bool:
        """True only when the extraction matches the gold exactly."""
        return (
            self.record_type_ok
            and not self.personas_missing
            and not self.personas_extra
            and not self.personas_duplicated
            and not self.role_mismatches
            and self.missing == 0
            and self.extra == 0
            and self.mismatched == 0
            and self.duplicated == 0
        )

    def summary(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        lines = [
            f"[{status}] {self.name}  "
            f"matched={self.matched} missing={self.missing} extra={self.extra} "
            f"mismatched={self.mismatched} duplicate={self.duplicated}  "
            f"values={self.values_captured}/{self.values_total}"
        ]
        for key in self.values_missing:
            lines.append(f"  VALUE LOST  {key}: on the page, absent from the extraction")
        if not self.record_type_ok:
            lines.append(
                f"  record_type: expected {self.record_type_expected!r}, "
                f"got {self.record_type_actual!r}"
            )
        for label in self.personas_missing:
            lines.append(f"  persona MISSING:    {label!r}")
        for label in self.personas_extra:
            lines.append(f"  persona EXTRA:      {label!r}")
        for label in self.personas_duplicated:
            lines.append(f"  persona DUPLICATED: {label!r}")
        for label in self.role_mismatches:
            lines.append(f"  persona ROLE DRIFT: {label!r}")
        for diff in self.diffs:
            if diff.kind == "matched":
                continue
            exp = _repr_side(diff.expected_value, diff.expected_type)
            act = _repr_side(diff.actual_value, diff.actual_type)
            if diff.kind == "missing":
                lines.append(f"  MISSING    {diff.key}: expected {exp}")
            elif diff.kind == "extra":
                lines.append(f"  EXTRA      {diff.key}: got {act}")
            elif diff.kind == "duplicate":
                lines.append(f"  DUPLICATE  {diff.key}: model emitted this field more than once")
            else:  # mismatched
                lines.append(f"  MISMATCH   {diff.key}: expected {exp}, got {act}")
        for diff in self.derived:
            act = _repr_side(diff.actual_value, diff.actual_type)
            lines.append(f"  DERIVED    {diff.key}: got {act} (not in the gold; check it)")
        return "\n".join(lines)


def _assertion_key(persona: str, field: str) -> str:
    """`<persona>::<snake_cased field>` — the key gold and model output share.

    Folded through the same `snake_case` the capture pipeline applies, so a gold
    label of `borough` matches a model that reported `Borough`. Scoring must
    measure the evidence, not the typography. Falls back to the raw label when a
    field holds no alphanumerics (`build_capture_models` rejects that outright,
    but `score` is a pure function and must never crash on bad model output).
    """
    return f"{persona}::{snake_case(field) or field}"


def _repr_side(value: Optional[str], evidence_type: Optional[str]) -> str:
    return "—" if value is None else f"{value!r} [{evidence_type}]"


def _persona_slots(
    personas: list[RawPersona],
) -> tuple[dict[str, str], Callable[[RawAssertion], str]]:
    """Assign each persona a unique SLOT; return (slot -> role, assertion resolver).

    Two DISTINCT people can share a label — a father and son both "John B." on a
    lineage page. To compare gold and actual without merging them, each persona
    gets a slot: its plain label when that label is unique, else `label#N` by order
    of appearance. Gold and actual are then aligned slot-by-slot. Occurrence order
    is the only alignment signal available, so a model that lists same-named
    personas in a different order than the gold surfaces as a role/persona
    mismatch — the honest result, not a silent merge.

    The returned resolver maps an assertion to its persona's slot, by `persona_id`
    (preferred) or, legacy, a UNIQUE `persona` label; an unresolved reference falls
    back to the raw ref/label so the pure scorer never crashes on bad output.
    """
    counts = Counter(p.label for p in personas)
    seen: dict[str, int] = defaultdict(int)
    slot_role: dict[str, str] = {}
    ref_to_slot: dict[str, str] = {}
    unique_label_slot: dict[str, str] = {}
    for p in personas:
        occurrence = seen[p.label]
        seen[p.label] += 1
        slot = p.label if counts[p.label] == 1 else f"{p.label}#{occurrence}"
        slot_role[slot] = p.role
        if p.id is not None:
            ref_to_slot[p.id] = slot
        if counts[p.label] == 1:
            unique_label_slot[p.label] = slot

    def resolve(assertion: RawAssertion) -> str:
        if assertion.persona_id is not None:
            return ref_to_slot.get(assertion.persona_id, assertion.persona_id)
        label = assertion.persona or ""
        return unique_label_slot.get(label, label)

    return slot_role, resolve


def _index_assertions(
    assertions: list[RawAssertion],
    resolve: Callable[[RawAssertion], str],
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Map (persona-slot::field) -> (value, evidence_type); collect duplicated keys.

    Duplicates are NOT collapsed silently — the first wins in the map and the
    key is reported, so two conflicting assertions for one field surface.
    """
    index: dict[str, tuple[str, str]] = {}
    duplicates: list[str] = []
    for a in assertions:
        key = _assertion_key(resolve(a), a.field)
        if key in index:
            duplicates.append(key)
        else:
            index[key] = (a.value, a.evidence_type.value)
    return index, duplicates


def score(name: str, expected: RawExtraction, actual: RawExtraction) -> EvalReport:
    """Compare `actual` to the gold `expected` field-by-field (pure, no I/O).

    Assertions are matched by (persona slot, field); a match requires the same
    verbatim `value` AND `evidence_type` (verbatim transcription is sacred, so
    value comparison is exact). Personas are compared by SLOT and role — a slot is
    a label, or `label#N` when a name repeats, so two distinct people who share a
    name are aligned by occurrence order rather than merged. A duplicate slot::field
    in the gold is a fixture bug and raises ValueError; the same in `actual` is
    reported, not collapsed.
    """
    exp_slot_role, exp_resolve = _persona_slots(expected.personas)
    act_slot_role, act_resolve = _persona_slots(actual.personas)
    exp_map, exp_adup = _index_assertions(expected.assertions, exp_resolve)
    if exp_adup:
        raise ValueError(
            f"gold fixture {name!r} has duplicate persona::field keys: {sorted(set(exp_adup))}"
        )

    # A derived fact is set aside for review (see EvalReport.derived) when the gold does not
    # pin that field at all, or when it rides beside a STATED fact of the same field (a birth
    # year worked out from "Age"). A derived fact standing in for a field the gold states is
    # NOT set aside: a stated fact downgraded to "calculated" is evidence-type drift.
    exp_keys = {_assertion_key(exp_resolve(a), a.field) for a in expected.assertions}
    stated_keys = {
        _assertion_key(act_resolve(a), a.field)
        for a in actual.assertions
        if a.evidence_type.value == "stated"
    }
    scored, derived = [], []
    for a in actual.assertions:
        key = _assertion_key(act_resolve(a), a.field)
        # Only about a person the reply declares: a dangling reference fails the capture
        # build, so it must stay scored and fail here too (Codex review).
        known = act_resolve(a) in act_slot_role
        if a.evidence_type.value != "stated" and known and (key not in exp_keys or key in stated_keys):
            derived.append(FieldDiff(key, "derived", None, None, a.value, a.evidence_type.value))
        else:
            scored.append(a)
    act_map, act_adup = _index_assertions(scored, act_resolve)

    exp_slots, act_slots = set(exp_slot_role), set(act_slot_role)
    personas_missing = tuple(sorted(exp_slots - act_slots))
    personas_extra = tuple(sorted(act_slots - exp_slots))
    # Slots are unique by construction; two same-named people are now distinct slots,
    # not a "duplicate". A spurious repeat surfaces instead as a slot missing/extra.
    personas_duplicated: tuple[str, ...] = ()
    role_mismatches = tuple(
        sorted(s for s in exp_slots & act_slots if exp_slot_role[s] != act_slot_role[s])
    )

    diffs: list[FieldDiff] = []
    for key in sorted(exp_map):
        exp_value, exp_type = exp_map[key]
        if key not in act_map:
            diffs.append(FieldDiff(key, "missing", exp_value, exp_type, None, None))
            continue
        act_value, act_type = act_map[key]
        kind = "matched" if (exp_value, exp_type) == (act_value, act_type) else "mismatched"
        diffs.append(FieldDiff(key, kind, exp_value, exp_type, act_value, act_type))
    for key in sorted(act_map):
        if key not in exp_map:
            act_value, act_type = act_map[key]
            diffs.append(FieldDiff(key, "extra", None, None, act_value, act_type))
    for key in sorted(set(act_adup)):
        act_value, act_type = act_map[key]
        diffs.append(FieldDiff(key, "duplicate", None, None, act_value, act_type))

    # Value-level score, deliberately blind to field names: did the extraction
    # pick up each value the page carries, under whatever label it chose? Field
    # naming is a mapping problem a human can fix; a value never extracted is
    # evidence lost, and the two must not be reported as the same failure.
    exp_values = {(exp_resolve(a), a.value) for a in expected.assertions}
    act_values = {(act_resolve(a), a.value) for a in actual.assertions}
    values_missing = tuple(sorted(f"{p}::{v}" for p, v in exp_values - act_values))

    return EvalReport(
        name=name,
        record_type_expected=expected.record_type,
        record_type_actual=actual.record_type,
        personas_missing=personas_missing,
        personas_extra=personas_extra,
        personas_duplicated=personas_duplicated,
        role_mismatches=role_mismatches,
        diffs=tuple(diffs),
        values_missing=values_missing,
        values_total=len(exp_values),
        derived=tuple(derived),
    )


def run_eval(
    fixture: EvalFixture,
    *,
    agent: Agent,
    now: Optional[Callable[[], datetime]] = None,
) -> EvalReport:
    """Run the single extraction call on the fixture page and score it vs gold.

    Uses `ProvidedContentFetcher`, so no network request is made — the page is
    the fixture's stored snapshot. The one cost is the extraction model call
    `agent.run` makes; inject a fake agent to exercise this offline.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    snapshot = ProvidedContentFetcher(
        fixture.snapshot_html, gating=fixture.gating, now=now
    ).fetch(fixture.url)
    prompt = build_extraction_prompt(snapshot)
    call = agent.run(prompt.user, system=prompt.system)
    actual = parse_extraction(call.response)
    return score(fixture.name, fixture.expected, actual)
