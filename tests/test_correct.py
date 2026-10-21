"""C3: apply_correction — edit / add / retract against a sealed capture.

Runs fully offline: a FakeFetcher + FakeTransport produce one real HTML capture
(folder on disk + indexed evidence), then apply_correction edits, adds, and
retracts facts. The load-bearing invariant is that a correction NEVER touches the
sealed capture — it lands only in the DB correction layer — while
current_assertions reflects the human's view and every change is logged.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from capsule.contracts.evidence import CorrectionAction, EvidenceType
from capsule.pipeline.correct import CorrectionOutcome, CorrectionRequest, apply_correction

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_CORR_DT = datetime(2026, 7, 12, 9, 0, 0, tzinfo=timezone.utc)

_HTML = (
    "<html><head><title>Mary Kelly (1810-1881)</title></head>"
    "<body><h1>Mary Kelly</h1><p>Died 3 March 1881.</p>"
    "<p>Buried at Glasnevin Cemetery, Dublin.</p></body></html>"
)

_RAW = {
    "record_type": "burial",
    "personas": [{"label": "Mary Kelly", "role": "deceased"}],
    "assertions": [
        {
            "persona": "Mary Kelly",
            "field": "death_date",
            "value": "3 March 1881",
            "evidence_type": "stated",
            "snippet": "Died 3 March 1881.",
        },
        {
            "persona": "Mary Kelly",
            "field": "burial_place",
            "value": "Glasnevin Cemetery, Dublin",
            "evidence_type": "stated",
            "snippet": "Buried at Glasnevin Cemetery, Dublin.",
        },
    ],
}


def _ids(prefix: str):
    n = 0

    def _next() -> str:
        nonlocal n
        n += 1
        return f"{prefix}-{n}"

    return _next


def _run(tmp_path):
    """One real HTML capture: returns (outcome, repo, captures_root)."""
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.contracts.evidence import GatingStatus
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    snapshot = PageSnapshot(
        url="https://www.findagrave.com/memorial/1",
        html=_HTML,
        gating=GatingStatus.PUBLIC,
        fetched_at=_DT,
        title="Mary Kelly (1810-1881)",
        site_name="Find a Grave",
    )
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    captures_root = tmp_path / "captures"
    repo = Repository(tmp_path / "capsule.db")
    outcome = run_capture(
        "https://www.findagrave.com/memorial/1",
        fetcher=FakeFetcher(snapshot),
        agent=agent,
        repository=repo,
        captures_root=captures_root,
        now=lambda: _DT,
        new_id=_ids("id"),
    )
    return outcome, repo, captures_root


def _by_field(repo, source_id):
    return {a.field: a for a in repo.current_assertions(source_id)}


# ---------------------------------------------------------------------------
# CorrectionRequest input validation
# ---------------------------------------------------------------------------


class TestCorrectionRequest:
    def test_edit_requires_target_and_value(self):
        with pytest.raises(ValueError):
            CorrectionRequest(capture_id="c", action=CorrectionAction.EDIT, new_value="x")
        with pytest.raises(ValueError):
            CorrectionRequest(capture_id="c", action=CorrectionAction.EDIT, assertion_id="a")

    def test_retract_takes_no_value(self):
        with pytest.raises(ValueError):
            CorrectionRequest(
                capture_id="c", action=CorrectionAction.RETRACT, assertion_id="a", new_value="x"
            )

    def test_add_requires_the_full_new_fact(self):
        with pytest.raises(ValueError):
            CorrectionRequest(
                capture_id="c", action=CorrectionAction.ADD, field="mother", new_value="Bridget"
            )  # missing persona_id, evidence_type, snippet_text

    def test_add_rejects_a_target(self):
        with pytest.raises(ValueError):
            CorrectionRequest(
                capture_id="c",
                action=CorrectionAction.ADD,
                assertion_id="a",
                persona_id="p",
                field="mother",
                new_value="Bridget",
                evidence_type=EvidenceType.STATED,
                snippet_text="Mother: Bridget",
            )


# ---------------------------------------------------------------------------
# apply_correction — the three actions
# ---------------------------------------------------------------------------


class TestApplyCorrection:
    def test_edit_supersedes_with_the_human_value(self, tmp_path):
        outcome, repo, captures = _run(tmp_path)
        sid, cid = outcome.source.id, outcome.capture_folder.name
        death = _by_field(repo, sid)["death_date"]

        req = CorrectionRequest(
            capture_id=cid,
            action=CorrectionAction.EDIT,
            assertion_id=death.id,
            new_value="13 March 1881",
            rationale="model misread the day",
        )
        result = apply_correction(
            req, repository=repo, captures_root=captures, now=lambda: _CORR_DT, new_id=_ids("corr")
        )
        assert isinstance(result, CorrectionOutcome)

        current = _by_field(repo, sid)
        assert current["death_date"].verbatim_value == "13 March 1881"
        assert current["death_date"].supersedes_id == death.id
        assert current["death_date"].id != death.id
        # The event records the before/after and the reason.
        (event,) = [e for e in repo.corrections_for_source(sid) if e.action == CorrectionAction.EDIT]
        assert event.previous_value == "3 March 1881"
        assert event.new_value == "13 March 1881"
        assert event.rationale == "model misread the day"
        assert event.assertion_id == current["death_date"].id

    def test_retract_removes_the_fact(self, tmp_path):
        outcome, repo, captures = _run(tmp_path)
        sid, cid = outcome.source.id, outcome.capture_folder.name
        burial = _by_field(repo, sid)["burial_place"]

        req = CorrectionRequest(
            capture_id=cid, action=CorrectionAction.RETRACT, assertion_id=burial.id
        )
        apply_correction(
            req, repository=repo, captures_root=captures, now=lambda: _CORR_DT, new_id=_ids("corr")
        )

        assert "burial_place" not in _by_field(repo, sid)  # gone entirely
        assert "death_date" in _by_field(repo, sid)  # the other fact is untouched
        (event,) = [e for e in repo.corrections_for_source(sid) if e.action == CorrectionAction.RETRACT]
        assert event.previous_value == "Glasnevin Cemetery, Dublin" and event.new_value is None

    def test_add_introduces_a_missed_fact(self, tmp_path):
        outcome, repo, captures = _run(tmp_path)
        sid, cid = outcome.source.id, outcome.capture_folder.name
        persona_id = _by_field(repo, sid)["death_date"].persona_id

        req = CorrectionRequest(
            capture_id=cid,
            action=CorrectionAction.ADD,
            persona_id=persona_id,
            field="mother_name",
            new_value="Bridget Kelly",
            evidence_type=EvidenceType.STATED,
            snippet_text="Daughter of Bridget Kelly",
        )
        result = apply_correction(
            req, repository=repo, captures_root=captures, now=lambda: _CORR_DT, new_id=_ids("corr")
        )

        current = _by_field(repo, sid)
        assert current["mother_name"].verbatim_value == "Bridget Kelly"
        assert current["mother_name"].persona_id == persona_id
        assert current["mother_name"].supersedes_id is None  # a brand-new fact
        assert result.assertion.id == current["mother_name"].id
        (event,) = [e for e in repo.corrections_for_source(sid) if e.action == CorrectionAction.ADD]
        assert event.previous_value is None and event.new_value == "Bridget Kelly"

    def test_correction_never_touches_the_sealed_capture(self, tmp_path):
        """The whole design: a correction is a separate DB layer. The capture's
        extraction.json (and thus its content_hash) must be byte-identical after,
        and the capture must still verify."""
        from capsule.capture.manifest import verify_capture

        outcome, repo, captures = _run(tmp_path)
        sid, cid = outcome.source.id, outcome.capture_folder.name
        extraction_before = (outcome.capture_folder / "extraction.json").read_bytes()
        death = _by_field(repo, sid)["death_date"]

        apply_correction(
            CorrectionRequest(
                capture_id=cid,
                action=CorrectionAction.EDIT,
                assertion_id=death.id,
                new_value="13 March 1881",
            ),
            repository=repo,
            captures_root=captures,
            now=lambda: _CORR_DT,
            new_id=_ids("corr"),
        )

        assert (outcome.capture_folder / "extraction.json").read_bytes() == extraction_before
        assert verify_capture(outcome.capture_folder).ok

    def test_edit_unknown_assertion_is_rejected(self, tmp_path):
        outcome, repo, captures = _run(tmp_path)
        with pytest.raises(ValueError):
            apply_correction(
                CorrectionRequest(
                    capture_id=outcome.capture_folder.name,
                    action=CorrectionAction.EDIT,
                    assertion_id="not-a-real-assertion",
                    new_value="x",
                ),
                repository=repo,
                captures_root=captures,
                new_id=_ids("corr"),
            )

    def test_cannot_correct_an_already_superseded_row(self, tmp_path):
        """Correcting the same original twice must fail: after the first edit the
        original is no longer current, so it can't be targeted again (this also
        respects the anti-fork unique index)."""
        outcome, repo, captures = _run(tmp_path)
        sid, cid = outcome.source.id, outcome.capture_folder.name
        death = _by_field(repo, sid)["death_date"]

        apply_correction(
            CorrectionRequest(
                capture_id=cid, action=CorrectionAction.EDIT, assertion_id=death.id, new_value="4 March 1881"
            ),
            repository=repo,
            captures_root=captures,
            now=lambda: _CORR_DT,
            new_id=_ids("corr1"),
        )
        with pytest.raises(ValueError):
            apply_correction(
                CorrectionRequest(
                    capture_id=cid, action=CorrectionAction.EDIT, assertion_id=death.id, new_value="5 March 1881"
                ),
                repository=repo,
                captures_root=captures,
                now=lambda: _CORR_DT,
                new_id=_ids("corr2"),
            )

    def test_unknown_capture_raises_file_not_found(self, tmp_path):
        outcome, repo, captures = _run(tmp_path)
        with pytest.raises(FileNotFoundError):
            apply_correction(
                CorrectionRequest(
                    capture_id="20260708-findagravecom-deadbeef",
                    action=CorrectionAction.RETRACT,
                    assertion_id="whatever",
                ),
                repository=repo,
                captures_root=captures,
            )

    def test_malformed_capture_id_raises_value_error(self, tmp_path):
        outcome, repo, captures = _run(tmp_path)
        with pytest.raises(ValueError):
            apply_correction(
                CorrectionRequest(
                    capture_id="../evil",
                    action=CorrectionAction.RETRACT,
                    assertion_id="whatever",
                ),
                repository=repo,
                captures_root=captures,
            )
