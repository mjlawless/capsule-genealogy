"""REQ-006 T6 — scoring a stored transcript against hand-read gold.

§8.1: "the vision path transcribes father's and mother's names exactly, or marks
them [?] — never a confident wrong name. A wrong name is a hard failure; an
honest [?] is a pass." So the scorer distinguishes an honest miss (marked) from
a confident one, and only the latter on a hard field fails the gate. A gold
value the owner has not yet confirmed is `disputed` — reported, never scored
against the transcriber (the eval once found the gold was wrong).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tests.test_req_006_reconcile as t5
import tests.test_req_006_storage as t4
from capsule.contracts.transcript import TranscriptStatus
from capsule.evals.transcript_eval import GoldField, TranscriptGold, load_gold, score_transcript


def _f(key, value, label=None, status=TranscriptStatus.VALUE, id=None):
    return t4._field(id=id or f"f-{key}", field_key=key, form_label=label, status=status,
                     verbatim_value=value, annotation=None)


_GOLD = TranscriptGold(
    name="q1903",
    capture_id="cap",
    fields=[
        GoldField(label="Father's Name", match=[r"^fathers?_name$"], value="James Kilcoyne", hard=True),
        GoldField(label="Mother's Name", match=[r"^mothers?_name$"], value="Bridget. Kilcoyne", hard=True),
        GoldField(label="Age", match=[r"^age$"], value="46 yrs 6 mos 26 days"),
        GoldField(label="Date of Burial", match=[r"burial.*date|date_of_burial"], value="Jan 21st 1903",
                  disputed="image shows 'Jany'; owner to confirm"),
    ],
)


def _outcomes(score):
    return {r.label: r.outcome for r in score.results}


def test_exact_values_pass_letter_for_letter():
    score = score_transcript(_GOLD, [_f("fathers_name", "James Kilcoyne"), _f("mothers_name", "Bridget. Kilcoyne"),
                                     _f("age", "46 yrs 6 mos 26 days")], comparisons=[])
    o = _outcomes(score)
    assert o["Father's Name"] == o["Mother's Name"] == o["Age"] == "exact"
    assert score.passed


def test_dropping_the_period_is_not_exact():
    score = score_transcript(_GOLD, [_f("fathers_name", "James Kilcoyne"),
                                     _f("mothers_name", "Bridget Kilcoyne")], comparisons=[])
    assert _outcomes(score)["Mother's Name"] == "wrong" and not score.passed


def test_an_honest_marker_is_a_pass_even_on_a_hard_field():
    score = score_transcript(_GOLD, [_f("fathers_name", "James Kil[?coyne]"),
                                     _f("mothers_name", "[illegible]", status=TranscriptStatus.ILLEGIBLE)],
                             comparisons=[])
    o = _outcomes(score)
    assert o["Father's Name"] == o["Mother's Name"] == "honest"
    assert score.passed


def test_a_confident_wrong_soft_field_is_reported_but_does_not_fail_the_gate():
    score = score_transcript(_GOLD, [_f("fathers_name", "James Kilcoyne"), _f("mothers_name", "Bridget. Kilcoyne"),
                                     _f("age", "76 yrs")], comparisons=[])
    assert _outcomes(score)["Age"] == "wrong"
    assert score.passed and score.counts["wrong"] == 1


def test_best_candidate_wins_when_several_fields_match():
    fields = [_f("fathers_name", "James Kilcoyne"), _f("mothers_name", "Bridget. Kilcoyne"),
              _f("age", "[?76]", id="f1"), _f("age", "46 yrs 6 mos 26 days", id="f2")]
    assert _outcomes(score_transcript(_GOLD, fields, comparisons=[]))["Age"] == "exact"


def test_matching_also_uses_the_printed_label():
    gold = TranscriptGold(name="x", capture_id="c", fields=[
        GoldField(label="Father's Name", match=[r"fathersname"], value="James Kilcoyne", hard=True)])
    score = score_transcript(gold, [_f("column_14", "James Kilcoyne", label="Father's Name.")], comparisons=[])
    assert score.results[0].outcome == "exact"


def test_missing_hard_field_fails():
    score = score_transcript(_GOLD, [_f("mothers_name", "Bridget. Kilcoyne")], comparisons=[])
    assert _outcomes(score)["Father's Name"] == "missing" and not score.passed


def test_disputed_gold_is_reported_never_scored():
    score = score_transcript(_GOLD, [_f("fathers_name", "James Kilcoyne"), _f("mothers_name", "Bridget. Kilcoyne"),
                                     _f("date_of_burial", "Jany 21 st 1903")], comparisons=[])
    (burial,) = [r for r in score.results if r.label == "Date of Burial"]
    assert burial.outcome == "disputed" and burial.got == "Jany 21 st 1903"
    assert score.passed


def test_accepted_alternatives_count_as_exact():
    gold = TranscriptGold(name="x", capture_id="c", fields=[
        GoldField(label="Hour", match=[r"hour|time"], value="9:15", accept=["9.15"])])
    assert score_transcript(gold, [_f("death_hour", "9.15")], comparisons=[]).results[0].outcome == "exact"


def test_expected_conflict_must_be_emitted():
    gold = TranscriptGold(name="k1905", capture_id="c", fields=[], expect_conflicts=["1905-11-12"])
    missing = score_transcript(gold, [], comparisons=[])
    assert not missing.passed and missing.conflicts_missing == ["1905-11-12"]
    found = score_transcript(gold, [], comparisons=[t5._comparison()])  # index 1905-11-12, conflict
    assert found.passed and found.conflicts_missing == []
    agreed = score_transcript(gold, [], comparisons=[t5._comparison(verdict="agree", explanation=None)])
    assert not agreed.passed


def test_gold_files_in_the_repo_load(tmp_path):
    golds = [load_gold(p) for p in sorted(Path("evals/transcripts").glob("*.json"))]
    assert {g.name for g in golds} >= {"nyc-death-1903-mary-lawless", "nyc-death-1905-robert-euin"}
    mary = next(g for g in golds if g.name == "nyc-death-1903-mary-lawless")
    assert {f.label for f in mary.fields if f.hard} == {"Father's Name", "Mother's Name"}
    undertaker = next(f for f in mary.fields if f.label == "Undertaker")
    assert undertaker.value == "Geo. P. McHugh"  # owner-confirmed correction, 2026-09-24
    with pytest.raises(ValueError):
        load_gold(tmp_path / "missing.json")
