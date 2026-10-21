"""REQ-006 T5 — image vs. index: conflicts are emitted, never resolved.

§3: on K-1905-22719 the NYC index (the DOM panel) says 12 November 1905 and the
certificate says 12 December, twice. A system that trusted the finding aid would
have written an error into the tree. So when a transcribed field and an index
assertion record the same fact, the pair is compared and a disagreement becomes
a first-class `conflict` record carrying BOTH values — neither one wins. Agreement
is recorded too, as information only: it never raises a grade (§5.2).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import tests.test_capture_manifest as cap
import tests.test_req_006_storage as t4
from capsule.agent.base import Agent
from capsule.agent.fake import FakeTransport
from capsule.capture.transcript import TRANSCRIPT_DIR, write_transcript_folder
from capsule.contracts.transcript import TranscriptComparison, TranscriptStatus
from capsule.pipeline.reconcile import IndexClaim, reconcile

_DT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)

_CLAIMS = [
    IndexClaim(assertion_id="a-date", field="date", verbatim_value="1905-11-12"),
    IndexClaim(assertion_id="a-name", field="name", verbatim_value="Robert Euin"),
    IndexClaim(assertion_id="a-age", field="age", verbatim_value="75 y"),
]
_FIELDS = [
    t4._field(id="f-date", field_key="date_of_death", form_label="Date of Death.",
              verbatim_value="Dec 12 1905", annotation=None, needs_review=False),
    t4._field(id="f-name", field_key="full_name", form_label="Full Name.",
              verbatim_value="Robert Euin", annotation=None, needs_review=False),
    t4._field(id="f-age", field_key="age", form_label="Age.",
              verbatim_value="75 yrs", annotation=None, needs_review=False),
]

_REPLY = {"comparisons": [
    {"assertion_id": "a-date", "transcript_field_id": "f-date", "verdict": "conflict",
     "explanation": "index gives November 12; the image reads Dec 12"},
    {"assertion_id": "a-name", "transcript_field_id": "f-name", "verdict": "agree", "explanation": None},
    {"assertion_id": "a-age", "transcript_field_id": "f-age", "verdict": "agree",
     "explanation": "75 y and 75 yrs state the same age"},
]}


def _comparison(**kw) -> TranscriptComparison:
    fields = dict(
        id="c1", source_id="src-1", transcription_id="tr1", assertion_id="a-date",
        transcript_field_id="tf1", verdict="conflict", index_value="1905-11-12",
        transcript_value="Dec 12 1905", explanation="month differs", agent_call_id="rc1",
        created_at=_DT,
    )
    fields.update(kw)
    return TranscriptComparison(**fields)


def _run(reply=_REPLY, claims=_CLAIMS, fields=_FIELDS):
    transport = FakeTransport(json.dumps(reply))
    agent = Agent("reconciler", "claude-opus-5", transport, now=lambda: _DT, new_id=lambda: "rc1")
    ids = iter(f"c{i}" for i in range(100))
    result = reconcile(
        claims, fields, source_id="src-1", transcription_id="tr1",
        agent=agent, created_at=_DT, new_id=lambda: next(ids),
    )
    return result, transport


def test_the_k1905_date_conflict_is_emitted_with_both_values():
    result, _ = _run()
    (conflict,) = [c for c in result.comparisons if c.verdict == "conflict"]
    assert conflict.assertion_id == "a-date" and conflict.transcript_field_id == "f-date"
    assert conflict.index_value == "1905-11-12" and conflict.transcript_value == "Dec 12 1905"
    assert "Dec" in conflict.explanation
    assert result.problems == [] and result.call is not None and result.call.id == "rc1"


def test_agreements_are_recorded_as_information():
    result, _ = _run()
    assert sorted(c.assertion_id for c in result.comparisons if c.verdict == "agree") == ["a-age", "a-name"]


def test_the_prompt_sends_both_sides_with_ids_and_forbids_picking_a_winner():
    _, transport = _run()
    (call,) = transport.calls
    for token in ("a-date", "1905-11-12", "f-date", "Dec 12 1905", "Date of Death."):
        assert token in call["prompt"]
    assert "never decide which is right" in call["system"].lower()
    assert not call["images"]  # text-only comparison


@pytest.mark.parametrize(
    "bad, reason",
    [
        ({"assertion_id": "a-ghost", "transcript_field_id": "f-date", "verdict": "agree"}, "unknown assertion"),
        ({"assertion_id": "a-date", "transcript_field_id": "f-ghost", "verdict": "agree"}, "unknown transcript field"),
        ({"assertion_id": "a-date", "transcript_field_id": "f-date", "verdict": "prefer_index"}, "verdict"),
        ({"assertion_id": "a-date", "transcript_field_id": "f-date", "verdict": "conflict", "explanation": ""},
         "explanation"),
    ],
)
def test_guard_drops_bad_pairs_and_says_why(bad, reason):
    result, _ = _run({"comparisons": [bad]})
    assert result.comparisons == []
    assert any(reason in p for p in result.problems)


def test_duplicate_pairs_are_kept_once():
    pair = _REPLY["comparisons"][0]
    result, _ = _run({"comparisons": [pair, pair]})
    assert len(result.comparisons) == 1


def test_nothing_to_compare_makes_no_call():
    result, transport = _run(claims=[])
    assert result.comparisons == [] and result.call is None and transport.calls == []


def test_illegible_image_values_are_offered_and_may_be_unclear():
    fields = [t4._field(id="f-date", field_key="date_of_death", status=TranscriptStatus.ILLEGIBLE,
                        verbatim_value="[illegible]", annotation=None)]
    reply = {"comparisons": [{"assertion_id": "a-date", "transcript_field_id": "f-date",
                              "verdict": "unclear", "explanation": "image value illegible"}]}
    result, transport = _run(reply, fields=fields)
    assert "[illegible]" in transport.calls[0]["prompt"]
    assert result.comparisons[0].verdict == "unclear"
    assert result.comparisons[0].transcript_value == "[illegible]"


def test_comparison_contract_requires_a_reason_for_a_conflict():
    _comparison()
    with pytest.raises(ValueError):
        _comparison(explanation=None)
    with pytest.raises(ValueError):
        _comparison(verdict="index_wins")


# --- persistence --------------------------------------------------------------


def _assertion_row(repo, assertion_id="a-date"):
    """A minimal persona/snippet/assertion for src-1, so the comparison FK has a target."""
    from capsule.contracts.evidence import Assertion, EvidenceType, Persona, Snippet, SnippetAnchor

    repo.add_persona(Persona(id="p1", source_id="src-1", role="deceased", label="Robert Euin"))
    repo.add_snippet(Snippet(id="s1", source_id="src-1", verbatim_text="Date: 1905-11-12",
                             anchor=SnippetAnchor(kind="text", start=0, end=16)))
    repo.add_assertion(Assertion(id=assertion_id, source_id="src-1", persona_id="p1", snippet_id="s1",
                                 field="date", verbatim_value="1905-11-12",
                                 evidence_type=EvidenceType.STATED, created_at=_DT))


def test_comparisons_persist_append_only_and_touch_no_evidence(tmp_path):
    repo = t4._repo_with_source(tmp_path)
    _assertion_row(repo)
    comparison = _comparison()
    repo.add_transcription(t4._transcription(), [t4._field()], [comparison])
    assert repo.comparisons_for_transcription("tr1") == [comparison]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        repo.connection.execute("UPDATE transcript_comparison SET verdict = 'agree'")
    # the index claim is not demoted and the image value is not promoted
    (assertion,) = repo.current_assertions("src-1")
    assert assertion.verbatim_value == "1905-11-12"


def test_comparison_must_reference_a_real_assertion(tmp_path):
    repo = t4._repo_with_source(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_transcription(t4._transcription(), [t4._field()], [_comparison(assertion_id="nope")])
    assert repo.transcriptions_for_source("src-1") == []  # all or nothing


def test_transcript_json_carries_the_comparisons(tmp_path):
    folder = cap._write(tmp_path)
    write_transcript_folder(folder, t4._transcription(), [t4._field()], {}, comparisons=[_comparison()])
    doc = json.loads((folder / TRANSCRIPT_DIR / "tr1" / "transcript.json").read_text(encoding="utf-8"))
    (c,) = doc["comparisons"]
    assert (c["verdict"], c["index_value"], c["transcript_value"]) == ("conflict", "1905-11-12", "Dec 12 1905")


def test_the_prompt_judges_a_split_fact_as_a_whole():
    """Live K-1905-22719: the date is written as '12' / 'December' / '5'; the reconciler
    called the index date 'agree' with the day and year parts and 'conflict' only with
    the month. A partial match must not read as agreement."""
    from capsule.pipeline.reconcile import RECONCILE_SYSTEM

    system = RECONCILE_SYSTEM.lower()
    assert "whole fact" in system and "never \"agree\"" in system
