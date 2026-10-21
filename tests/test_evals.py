"""Offline tests for the extraction eval harness.

The scorer is pure and the runner uses a FakeTransport, so nothing here spends
tokens or touches the network. Fixtures are built synthetically in `tmp_path`
(the real committed fixtures have no `snapshot.html` in CI — it stays local).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from capsule.contracts.evidence import GatingStatus
from capsule.evals.harness import SnapshotMissing, load_fixture, run_eval, score
from capsule.pipeline.extract import RawExtraction

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)

_GOLD = {
    "record_type": "death",
    "personas": [{"label": "Mary Kelly", "role": "deceased"}],
    "assertions": [
        {
            "persona": "Mary Kelly",
            "field": "name",
            "value": "Mary Kelly",
            "evidence_type": "stated",
            "snippet": "Name: Mary Kelly",
        },
        {
            "persona": "Mary Kelly",
            "field": "death_date",
            "value": "1881-03-03",
            "evidence_type": "stated",
            "snippet": "Date: 1881-03-03",
        },
    ],
}


def _raw(data: dict) -> RawExtraction:
    return RawExtraction.model_validate(data)


def test_committed_fixture_gold_is_schema_valid():
    """Every committed fixture's expected.json/meta.json must be well-formed.

    Runs in CI (needs no local snapshot.html): guards the hand-labeled gold
    against a typo silently breaking `capsule eval` when the owner runs it.
    """
    root = Path(__file__).resolve().parent.parent / "evals" / "fixtures"
    golds = sorted(root.glob("*/expected.json"))
    assert golds, "no committed eval fixtures found under evals/fixtures/"
    for gold in golds:
        RawExtraction.model_validate(json.loads(gold.read_text(encoding="utf-8")))
        meta = json.loads((gold.parent / "meta.json").read_text(encoding="utf-8"))
        assert "url" in meta, f"{gold.parent.name}/meta.json has no url"
        GatingStatus(meta.get("gating", "public"))  # raises if not public|gated


def test_score_perfect_match_is_ok():
    report = score("f", _raw(_GOLD), _raw(_GOLD))
    assert report.ok
    assert report.matched == 2
    assert report.missing == report.extra == report.mismatched == 0
    assert report.values_captured == report.values_total == 2
    assert report.values_missing == ()


def test_committed_gold_asserts_only_labels_the_page_prints():
    """Regression: the NYC gold once recorded `death_place` [stated] for `Borough`.

    The page prints "Borough: Queens"; the string `death_place` appears nowhere on
    it. Gold that renames a label into a stronger claim asserts an inference as a
    stated fact — the exact corruption this project exists to prevent, sitting in
    the only ground truth we have. So every gold `field` must appear in its own
    `snippet`, which is by definition verbatim page text.

    A guard, not a proof: a short label could coincidentally occur in a snippet it
    does not name. It catches the failure we actually made.
    """
    root = Path(__file__).resolve().parent.parent / "evals" / "fixtures"
    for gold in sorted(root.glob("*/expected.json")):
        extraction = RawExtraction.model_validate(json.loads(gold.read_text(encoding="utf-8")))
        for assertion in extraction.assertions:
            label = assertion.field.replace("_", " ")
            assert label in assertion.snippet.casefold(), (
                f"{gold.parent.name}: gold field {assertion.field!r} does not appear "
                f"in its own verbatim snippet {assertion.snippet!r} — it is a rename, "
                f"and possibly an inference"
            )


def test_BUG_field_names_are_compared_folded_so_casing_is_not_a_failure():
    """Regression: the model reported `Borough`, the gold said `borough`.

    Identical evidence, and the eval called it one MISSING plus one EXTRA. Field
    labels are typography; the scorer must fold them exactly as the capture
    pipeline does, or it measures how a page was styled rather than what it says.
    """
    actual = _raw(
        {
            **_GOLD,
            "assertions": [
                {**_GOLD["assertions"][0], "field": "Name"},
                {**_GOLD["assertions"][1], "field": "Death Date"},
            ],
        }
    )
    report = score("f", _raw(_GOLD), actual)
    assert report.ok, report.summary()
    assert report.matched == 2


def test_BUG_a_dropped_value_scores_worse_than_a_renamed_field():
    """Regression: a run that LOST `soundex` scored the same 6/7 as one that only
    renamed `Borough`. Field-level scoring conflated the two.

    Renaming a field is a mapping problem a human fixes in a minute. Never
    extracting a value is evidence lost off the page. The value-level metric is
    what tells them apart, so it must be blind to field names.
    """
    renamed = _raw(
        {
            **_GOLD,
            "assertions": [
                _GOLD["assertions"][0],
                {**_GOLD["assertions"][1], "field": "date_of_death"},
            ],
        }
    )
    dropped = _raw({**_GOLD, "assertions": [_GOLD["assertions"][0]]})

    # Both lose exactly one gold field...
    assert score("f", _raw(_GOLD), renamed).missing == 1
    assert score("f", _raw(_GOLD), dropped).missing == 1

    # ...but only one of them lost the value.
    assert score("f", _raw(_GOLD), renamed).values_captured == 2
    assert score("f", _raw(_GOLD), dropped).values_captured == 1
    assert score("f", _raw(_GOLD), dropped).values_missing == ("Mary Kelly::1881-03-03",)


# --- two distinct personas that share a label (DAR/lineage) ----------------

_TWO_JOHN = {
    "record_type": "lineage",
    "personas": [
        {"id": "p1", "label": "John B.", "role": "ancestor"},
        {"id": "p2", "label": "John B.", "role": "descendant"},
    ],
    "assertions": [
        {"persona_id": "p1", "field": "born", "value": "1750",
         "evidence_type": "stated", "snippet": "b. 1750"},
        {"persona_id": "p2", "field": "born", "value": "1789",
         "evidence_type": "stated", "snippet": "b. 1789"},
    ],
}


def test_two_same_named_personas_score_without_merging():
    """Two different people both "John B." are aligned by occurrence, not merged:
    a perfect run matches both, and no spurious duplicate is reported."""
    gold = _raw(_TWO_JOHN)
    report = score("two-john", gold, gold)
    assert report.ok, report.summary()
    assert report.matched == 2  # each person's `born` matched to the right slot
    assert report.personas_duplicated == ()  # distinct people, not a duplicate
    assert report.values_captured == report.values_total == 2


def test_gold_with_duplicate_labels_no_longer_raises():
    """Repeated names in the gold are legitimate now (they were a hard error);
    slots keep the two people distinct instead of rejecting the fixture."""
    gold = _raw(_TWO_JOHN)
    report = score("two-john", gold, gold)  # must not raise
    assert report.ok


def test_swapping_facts_between_same_named_personas_is_caught():
    """Give each John B. the OTHER's birth year: because they are distinct slots,
    both facts are now wrong — the scorer does not silently accept the swap."""
    swapped = json.loads(json.dumps(_TWO_JOHN))
    swapped["assertions"][0]["value"] = "1789"  # ancestor gets the descendant's year
    swapped["assertions"][1]["value"] = "1750"
    report = score("two-john", _raw(_TWO_JOHN), _raw(swapped))
    assert not report.ok
    assert report.mismatched == 2 and report.matched == 0


def test_same_named_personas_in_reversed_order_flag_a_mismatch():
    """Occurrence alignment is order-based (the only signal available): listing the
    two same-named people in the opposite order surfaces as a mismatch rather than
    a silent merge. Documents the known limitation."""
    reversed_order = {
        "record_type": "lineage",
        "personas": [
            {"id": "p2", "label": "John B.", "role": "descendant"},
            {"id": "p1", "label": "John B.", "role": "ancestor"},
        ],
        "assertions": [
            {"persona_id": "p2", "field": "born", "value": "1789",
             "evidence_type": "stated", "snippet": "b. 1789"},
            {"persona_id": "p1", "field": "born", "value": "1750",
             "evidence_type": "stated", "snippet": "b. 1750"},
        ],
    }
    report = score("two-john", _raw(_TWO_JOHN), _raw(reversed_order))
    assert not report.ok
    assert report.role_mismatches  # slot#0 is ancestor in gold, descendant in actual


def test_score_missing_field_is_not_ok():
    partial = json.loads(json.dumps(_GOLD))
    partial["assertions"] = partial["assertions"][:1]  # drop death_date
    report = score("f", _raw(_GOLD), _raw(partial))
    assert not report.ok
    assert report.missing == 1
    assert any(d.kind == "missing" and d.key == "Mary Kelly::death_date" for d in report.diffs)


def test_score_wrong_value_is_mismatched():
    drifted = json.loads(json.dumps(_GOLD))
    drifted["assertions"][1]["value"] = "3 March 1881"  # not verbatim to gold
    report = score("f", _raw(_GOLD), _raw(drifted))
    assert not report.ok
    assert report.mismatched == 1
    bad = next(d for d in report.diffs if d.key == "Mary Kelly::death_date")
    assert bad.kind == "mismatched"


def test_score_extra_field_is_flagged():
    extra = json.loads(json.dumps(_GOLD))
    extra["assertions"].append(
        {
            "persona": "Mary Kelly",
            "field": "age",
            "value": "46",
            "evidence_type": "stated",
            "snippet": "Age: 46",
        }
    )
    report = score("f", _raw(_GOLD), _raw(extra))
    assert not report.ok
    assert report.extra == 1


def test_score_evidence_type_drift_is_mismatched():
    drifted = json.loads(json.dumps(_GOLD))
    drifted["assertions"][1]["evidence_type"] = "calculated"  # was stated
    report = score("f", _raw(_GOLD), _raw(drifted))
    assert not report.ok
    assert report.mismatched == 1


def test_score_record_type_and_persona_drift_is_not_ok():
    drifted = json.loads(json.dumps(_GOLD))
    drifted["record_type"] = "burial"
    drifted["personas"] = [{"label": "Someone Else", "role": "deceased"}]
    report = score("f", _raw(_GOLD), _raw(drifted))
    assert not report.ok
    assert not report.record_type_ok
    assert report.personas_missing == ("Mary Kelly",)
    assert report.personas_extra == ("Someone Else",)


def test_score_persona_role_drift_is_not_ok():
    """Same label, wrong role (deceased -> informant) must fail, not pass silently."""
    drifted = json.loads(json.dumps(_GOLD))
    drifted["personas"][0]["role"] = "informant"
    report = score("f", _raw(_GOLD), _raw(drifted))
    assert not report.ok
    assert report.role_mismatches == ("Mary Kelly",)


def test_score_actual_duplicate_field_is_flagged_not_collapsed():
    """A model emitting two assertions for one persona::field must be caught."""
    dup = json.loads(json.dumps(_GOLD))
    dup["assertions"].append(
        {
            "persona": "Mary Kelly",
            "field": "name",  # duplicates the existing name assertion
            "value": "Mary Kelley",
            "evidence_type": "stated",
            "snippet": "Name: Mary Kelley",
        }
    )
    report = score("f", _raw(_GOLD), _raw(dup))
    assert not report.ok
    assert report.duplicated == 1
    assert any(d.kind == "duplicate" and d.key == "Mary Kelly::name" for d in report.diffs)


def test_score_raises_on_duplicate_gold_key():
    """Duplicate persona::field in the GOLD is a fixture authoring bug, not a pass."""
    bad_gold = json.loads(json.dumps(_GOLD))
    bad_gold["assertions"].append(bad_gold["assertions"][0])  # duplicate name
    with pytest.raises(ValueError, match="duplicate persona::field"):
        score("f", _raw(bad_gold), _raw(_GOLD))


def _write_fixture(fixture_dir, *, expected=_GOLD, meta=None, snapshot="<html>x</html>"):
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    (fixture_dir / "meta.json").write_text(
        json.dumps(meta or {"url": "https://x.example/1", "gating": "gated"}), encoding="utf-8"
    )
    (fixture_dir / "snapshot.html").write_text(snapshot, encoding="utf-8")
    return fixture_dir


def test_load_fixture_round_trips(tmp_path):
    fx = load_fixture(_write_fixture(tmp_path / "nyc-death"))
    assert fx.name == "nyc-death"
    assert fx.url == "https://x.example/1"
    assert fx.gating.value == "gated"
    assert fx.expected.record_type == "death"
    assert len(fx.expected.assertions) == 2


def test_load_fixture_missing_snapshot_raises_snapshot_missing(tmp_path):
    """A gitignored/absent snapshot is SnapshotMissing (a skip), not a hard error."""
    d = tmp_path / "no-snap"
    d.mkdir()
    (d / "expected.json").write_text(json.dumps(_GOLD), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"url": "u", "gating": "public"}), encoding="utf-8")
    with pytest.raises(SnapshotMissing, match="snapshot.html"):
        load_fixture(d)


def test_load_fixture_corrupt_gold_raises_value_error(tmp_path):
    """Corrupt committed gold is a loud ValueError, NOT a SnapshotMissing skip."""
    d = tmp_path / "bad-gold"
    d.mkdir()
    (d / "snapshot.html").write_text("<html>x</html>", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"url": "u", "gating": "public"}), encoding="utf-8")
    (d / "expected.json").write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="bad-gold"):
        load_fixture(d)


def test_run_eval_offline_scores_faked_extraction(tmp_path):
    """End-to-end offline: a fake agent returns the gold JSON → PASS report."""
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport

    fx = load_fixture(_write_fixture(tmp_path / "nyc-death"))
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_GOLD)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    report = run_eval(fx, agent=agent, now=lambda: _DT)
    assert report.ok, report.summary()
    assert report.matched == 2


def test_run_eval_offline_flags_drifted_extraction(tmp_path):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport

    drifted = json.loads(json.dumps(_GOLD))
    drifted["assertions"][0]["value"] = "Mary  Kelly"  # doubled space — not verbatim
    fx = load_fixture(_write_fixture(tmp_path / "nyc-death"))
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(drifted)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    report = run_eval(fx, agent=agent, now=lambda: _DT)
    assert not report.ok
    assert report.mismatched == 1
