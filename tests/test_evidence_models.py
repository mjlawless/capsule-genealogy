"""Tests for the evidence contract models (capsule/contracts/evidence.py).

Written FIRST (RED) — capsule/contracts/evidence.py does not exist yet when
this file is first run.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _archive_record(**overrides):
    from capsule.contracts.evidence import ArchiveRecord

    defaults = dict(
        local_snapshot="snapshot.html",
        sha256="a" * 64,
        captured_at=datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc),
        wayback_url=None,
    )
    defaults.update(overrides)
    return ArchiveRecord(**defaults)


def _source(**overrides):
    from capsule.contracts.evidence import GatingStatus, Source, SourceQuality

    defaults = dict(
        id="018f0000-0000-7000-8000-000000000001",
        url="https://www.findagrave.com/memorial/1",
        title="John Murphy",
        site_name="Find a Grave",
        gating_status=GatingStatus.PUBLIC,
        copyright_flag=False,
        source_quality=SourceQuality.DERIVATIVE,
        archive=_archive_record(),
        content_hash="b" * 64,
    )
    defaults.update(overrides)
    return Source(**defaults)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_evidence_type_values(self):
        from capsule.contracts.evidence import EvidenceType

        assert EvidenceType.STATED == "stated"
        assert EvidenceType.CALCULATED == "calculated"
        assert EvidenceType.ESTIMATED == "estimated"

    def test_gating_status_values(self):
        from capsule.contracts.evidence import GatingStatus

        assert GatingStatus.PUBLIC == "public"
        assert GatingStatus.GATED == "gated"

    def test_source_quality_values(self):
        from capsule.contracts.evidence import SourceQuality

        assert SourceQuality.ORIGINAL == "original"
        assert SourceQuality.DERIVATIVE == "derivative"

    def test_evidence_type_rejects_bad_value(self):
        from capsule.contracts.evidence import Assertion

        with pytest.raises(ValidationError):
            Assertion(
                id="a1",
                source_id="s1",
                persona_id="p1",
                snippet_id="sn1",
                field="birth_date",
                verbatim_value="1900",
                evidence_type="not-a-real-type",
                created_at=datetime.now(timezone.utc),
            )

    def test_gating_status_rejects_bad_value(self):
        with pytest.raises(ValidationError):
            _source(gating_status="not-a-real-status", copyright_flag=True)

    def test_source_quality_rejects_bad_value(self):
        with pytest.raises(ValidationError):
            _source(source_quality="not-a-real-quality")


# ---------------------------------------------------------------------------
# SnippetAnchor / ArchiveRecord value objects
# ---------------------------------------------------------------------------


class TestSnippetAnchor:
    def test_text_anchor_round_trip(self):
        from capsule.contracts.evidence import SnippetAnchor

        anchor = SnippetAnchor(kind="text", start=10, end=42, media_filename=None, bbox=None)
        dumped = anchor.model_dump()
        restored = SnippetAnchor.model_validate(dumped)
        assert restored == anchor

    def test_image_anchor_round_trip_with_bbox(self):
        from capsule.contracts.evidence import SnippetAnchor

        anchor = SnippetAnchor(
            kind="image",
            start=None,
            end=None,
            media_filename="0-headstone.jpg",
            bbox=(0, 0, 100, 200),
        )
        dumped = anchor.model_dump()
        restored = SnippetAnchor.model_validate(dumped)
        assert restored == anchor

    def test_kind_rejects_invalid_literal(self):
        from capsule.contracts.evidence import SnippetAnchor

        with pytest.raises(ValidationError):
            SnippetAnchor(kind="audio", start=None, end=None, media_filename=None, bbox=None)


class TestArchiveRecord:
    def test_round_trip(self):
        record = _archive_record(wayback_url="https://web.archive.org/web/1/x")
        dumped = record.model_dump()
        from capsule.contracts.evidence import ArchiveRecord

        restored = ArchiveRecord.model_validate(dumped)
        assert restored == record

    def test_captured_at_must_be_tz_aware(self):
        from capsule.contracts.evidence import ArchiveRecord

        with pytest.raises(ValidationError):
            ArchiveRecord(
                local_snapshot="snapshot.html",
                sha256="a" * 64,
                captured_at=datetime(2026, 7, 7, 12, 0, 0),  # naive
            )


# ---------------------------------------------------------------------------
# Source invariants (gating_status <-> copyright_flag / wayback_url)
# ---------------------------------------------------------------------------


class TestSourceInvariants:
    def test_gated_requires_copyright_flag_true(self):
        with pytest.raises(ValidationError):
            _source(gating_status="gated", copyright_flag=False)

    def test_gated_with_copyright_flag_true_is_accepted(self):
        source = _source(gating_status="gated", copyright_flag=True)
        assert source.gating_status == "gated"
        assert source.copyright_flag is True

    def test_gated_rejects_wayback_url(self):
        with pytest.raises(ValidationError):
            _source(
                gating_status="gated",
                copyright_flag=True,
                archive=_archive_record(wayback_url="https://web.archive.org/web/1/x"),
            )

    def test_gated_accepts_no_wayback_url(self):
        source = _source(
            gating_status="gated",
            copyright_flag=True,
            archive=_archive_record(wayback_url=None),
        )
        assert source.archive.wayback_url is None

    def test_public_may_have_copyright_flag_false(self):
        source = _source(gating_status="public", copyright_flag=False)
        assert source.copyright_flag is False

    def test_public_may_have_copyright_flag_true(self):
        source = _source(gating_status="public", copyright_flag=True)
        assert source.copyright_flag is True

    def test_public_may_have_wayback_url(self):
        source = _source(
            gating_status="public",
            copyright_flag=False,
            archive=_archive_record(wayback_url="https://web.archive.org/web/1/x"),
        )
        assert source.archive.wayback_url == "https://web.archive.org/web/1/x"

    def test_default_spec_version(self):
        source = _source()
        assert source.spec_version == "capture-folder/v1"

    def test_source_round_trip(self):
        from capsule.contracts.evidence import Source

        source = _source()
        dumped = source.model_dump()
        restored = Source.model_validate(dumped)
        assert restored == source


# ---------------------------------------------------------------------------
# Snippet
# ---------------------------------------------------------------------------


class TestSnippet:
    def test_round_trip(self):
        from capsule.contracts.evidence import Snippet, SnippetAnchor

        snippet = Snippet(
            id="sn1",
            source_id="s1",
            verbatim_text="b. 1900 d. 1965",
            anchor=SnippetAnchor(kind="text", start=0, end=15, media_filename=None, bbox=None),
        )
        dumped = snippet.model_dump()
        restored = Snippet.model_validate(dumped)
        assert restored == snippet


# ---------------------------------------------------------------------------
# Persona (no tree_person_id — intentional)
# ---------------------------------------------------------------------------


class TestPersona:
    def test_round_trip(self):
        from capsule.contracts.evidence import Persona

        persona = Persona(id="p1", source_id="s1", role="deceased", label="John Murphy")
        dumped = persona.model_dump()
        restored = Persona.model_validate(dumped)
        assert restored == persona

    def test_persona_has_no_tree_person_id_field(self):
        from capsule.contracts.evidence import Persona

        assert "tree_person_id" not in Persona.model_fields


# ---------------------------------------------------------------------------
# Assertion
# ---------------------------------------------------------------------------


class TestFrozenModels:
    """HIGH-1 code review finding: models were mutable, so invariants only
    held at construction time. All models now carry
    `model_config = ConfigDict(frozen=True)`.
    """

    def test_BUG_source_field_assignment_raises(self):
        source = _source()
        with pytest.raises(ValidationError):
            source.copyright_flag = True

    def test_BUG_snippet_field_assignment_raises(self):
        from capsule.contracts.evidence import Snippet, SnippetAnchor

        snippet = Snippet(
            id="sn1",
            source_id="s1",
            verbatim_text="text",
            anchor=SnippetAnchor(kind="text", start=0, end=4, media_filename=None, bbox=None),
        )
        with pytest.raises(ValidationError):
            snippet.verbatim_text = "changed"

    def test_BUG_persona_field_assignment_raises(self):
        from capsule.contracts.evidence import Persona

        persona = Persona(id="p1", source_id="s1", role="deceased", label="John Murphy")
        with pytest.raises(ValidationError):
            persona.label = "changed"

    def test_BUG_assertion_field_assignment_raises(self):
        from capsule.contracts.evidence import Assertion, EvidenceType

        assertion = Assertion(
            id="a1",
            source_id="s1",
            persona_id="p1",
            snippet_id="sn1",
            field="birth_date",
            verbatim_value="1900",
            evidence_type=EvidenceType.STATED,
            created_at=datetime.now(timezone.utc),
        )
        with pytest.raises(ValidationError):
            assertion.verbatim_value = "changed"

    def test_BUG_citation_field_assignment_raises(self):
        from capsule.contracts.evidence import Citation

        citation = Citation(
            id="c1",
            source_id="s1",
            reference_note="R",
            short_note="S",
            source_list_entry="L",
            access_date=date(2026, 7, 7),
            archived_url="https://web.archive.org/web/1/x",
        )
        with pytest.raises(ValidationError):
            citation.template_id = "changed"


class TestAssertionFieldNaming:
    """`field` is the printed label; `normalized_field` is our synonym for it.

    The model cannot enforce the synonym-not-inference rule — only a human reading
    the pair can. What it CAN enforce is that both are meaningful strings, so
    "we have no canonical name" and "we have a blank one" never look alike.
    """

    @staticmethod
    def _assertion(**overrides):
        from capsule.contracts.evidence import Assertion, EvidenceType

        defaults = dict(
            id="a1",
            source_id="s1",
            persona_id="p1",
            snippet_id="sn1",
            field="borough",
            verbatim_value="Queens",
            evidence_type=EvidenceType.STATED,
            created_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return Assertion(**defaults)

    def test_normalized_field_defaults_to_none(self):
        assert self._assertion().normalized_field is None

    def test_normalized_field_accepts_a_synonym(self):
        assertion = self._assertion(field="type", normalized_field="record_type")
        assert assertion.normalized_field == "record_type"

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_normalized_field_is_rejected(self, blank):
        """Absent is a different claim from empty: a blank string would read as
        'we have a canonical name for this' while carrying none."""
        with pytest.raises(ValidationError):
            self._assertion(normalized_field=blank)

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_field_is_rejected(self, blank):
        """An assertion whose label is whitespace names no fact at all."""
        with pytest.raises(ValidationError):
            self._assertion(field=blank)

    def test_BUG_archive_record_field_assignment_raises(self):
        record = _archive_record()
        with pytest.raises(ValidationError):
            record.sha256 = "c" * 64

    def test_BUG_snippet_anchor_field_assignment_raises(self):
        from capsule.contracts.evidence import SnippetAnchor

        anchor = SnippetAnchor(kind="text", start=0, end=4, media_filename=None, bbox=None)
        with pytest.raises(ValidationError):
            anchor.start = 1

    def test_frozen_model_copy_still_produces_updated_instance(self):
        """model_copy(update=...) must keep working on frozen models --
        this is how Repository.add_correction builds a corrected Assertion.
        """
        from capsule.contracts.evidence import Assertion, EvidenceType

        original = Assertion(
            id="a1",
            source_id="s1",
            persona_id="p1",
            snippet_id="sn1",
            field="birth_date",
            verbatim_value="1900",
            evidence_type=EvidenceType.STATED,
            created_at=datetime.now(timezone.utc),
        )
        corrected = original.model_copy(update={"supersedes_id": "a0"})
        assert corrected.supersedes_id == "a0"
        assert original.supersedes_id is None  # original untouched


class TestAssertion:
    def _assertion(self, **overrides):
        from capsule.contracts.evidence import Assertion, EvidenceType

        defaults = dict(
            id="a1",
            source_id="s1",
            persona_id="p1",
            snippet_id="sn1",
            field="birth_date",
            verbatim_value="1900",
            normalized_value="1900-01-01",
            annotation=None,
            evidence_type=EvidenceType.STATED,
            confidence=0.9,
            supersedes_id=None,
            created_at=datetime(2026, 7, 7, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return Assertion(**defaults)

    def test_round_trip(self):
        from capsule.contracts.evidence import Assertion

        assertion = self._assertion()
        dumped = assertion.model_dump()
        restored = Assertion.model_validate(dumped)
        assert restored == assertion

    def test_confidence_none_is_allowed(self):
        assertion = self._assertion(confidence=None)
        assert assertion.confidence is None

    def test_confidence_at_lower_bound_accepted(self):
        assertion = self._assertion(confidence=0.0)
        assert assertion.confidence == 0.0

    def test_confidence_at_upper_bound_accepted(self):
        assertion = self._assertion(confidence=1.0)
        assert assertion.confidence == 1.0

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            self._assertion(confidence=-0.01)

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            self._assertion(confidence=1.01)

    def test_supersedes_id_defaults_to_none(self):
        assertion = self._assertion()
        assert assertion.supersedes_id is None

    def test_supersedes_id_can_be_set_for_correction(self):
        assertion = self._assertion(id="a2", supersedes_id="a1")
        assert assertion.supersedes_id == "a1"

    def test_retracted_defaults_to_false(self):
        assert self._assertion().retracted is False

    def test_retracted_can_be_set_for_a_retraction(self):
        assertion = self._assertion(id="a2", supersedes_id="a1", retracted=True)
        assert assertion.retracted is True

    def test_BUG_created_at_naive_datetime_rejected(self):
        """HIGH-2 code review finding: ArchiveRecord.captured_at had a
        tz-aware guard but Assertion.created_at did not. Same guard now
        applies to both.
        """
        with pytest.raises(ValidationError):
            self._assertion(created_at=datetime(2026, 7, 7))  # naive


# ---------------------------------------------------------------------------
# Citation
# ---------------------------------------------------------------------------


class TestCitation:
    def test_round_trip(self):
        from capsule.contracts.evidence import Citation

        citation = Citation(
            id="c1",
            source_id="s1",
            reference_note="Full reference note text.",
            short_note="Short note text.",
            source_list_entry="Source list entry text.",
            access_date=date(2026, 7, 7),
            archived_url="https://web.archive.org/web/1/x",
        )
        dumped = citation.model_dump()
        restored = Citation.model_validate(dumped)
        assert restored == citation

    def test_default_template_id(self):
        from capsule.contracts.evidence import Citation

        citation = Citation(
            id="c1",
            source_id="s1",
            reference_note="R",
            short_note="S",
            source_list_entry="L",
            access_date=date(2026, 7, 7),
            archived_url="https://web.archive.org/web/1/x",
        )
        assert citation.template_id == "generic-web-v1"


# ---------------------------------------------------------------------------
# CorrectionEvent (the human-correction flywheel log)
# ---------------------------------------------------------------------------


class TestCorrectionAction:
    def test_values(self):
        from capsule.contracts.evidence import CorrectionAction

        assert CorrectionAction.EDIT == "edit"
        assert CorrectionAction.ADD == "add"
        assert CorrectionAction.RETRACT == "retract"


class TestCorrectionEvent:
    @staticmethod
    def _event(**overrides):
        from capsule.contracts.evidence import CorrectionAction, CorrectionEvent

        defaults = dict(
            id="ce1",
            source_id="s1",
            action=CorrectionAction.EDIT,
            field="birth_year",
            assertion_id="a2",
            supersedes_id="a1",
            previous_value="1901",
            new_value="1900",
            created_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return CorrectionEvent(**defaults)

    def test_edit_round_trips(self):
        from capsule.contracts.evidence import CorrectionEvent

        event = self._event()
        assert event.actor == "owner"  # default
        assert CorrectionEvent.model_validate(event.model_dump()) == event

    def test_add_has_no_supersedes_or_previous_value(self):
        from capsule.contracts.evidence import CorrectionAction

        event = self._event(
            action=CorrectionAction.ADD,
            supersedes_id=None,
            previous_value=None,
            new_value="Queens",
        )
        assert event.supersedes_id is None
        assert event.previous_value is None

    def test_retract_has_no_new_value(self):
        from capsule.contracts.evidence import CorrectionAction

        event = self._event(
            action=CorrectionAction.RETRACT,
            previous_value="Queens",
            new_value=None,
        )
        assert event.new_value is None

    def test_edit_requires_supersedes_id(self):
        with pytest.raises(ValidationError):
            self._event(supersedes_id=None)

    def test_edit_requires_both_values(self):
        with pytest.raises(ValidationError):
            self._event(new_value=None)

    def test_add_rejects_a_supersedes_id(self):
        from capsule.contracts.evidence import CorrectionAction

        with pytest.raises(ValidationError):
            self._event(action=CorrectionAction.ADD, previous_value=None)  # supersedes_id still set

    def test_add_requires_a_new_value(self):
        from capsule.contracts.evidence import CorrectionAction

        with pytest.raises(ValidationError):
            self._event(
                action=CorrectionAction.ADD,
                supersedes_id=None,
                previous_value=None,
                new_value=None,
            )

    def test_retract_requires_supersedes_id(self):
        from capsule.contracts.evidence import CorrectionAction

        with pytest.raises(ValidationError):
            self._event(
                action=CorrectionAction.RETRACT,
                supersedes_id=None,
                previous_value="Queens",
                new_value=None,
            )

    def test_retract_rejects_a_new_value(self):
        from capsule.contracts.evidence import CorrectionAction

        with pytest.raises(ValidationError):
            self._event(action=CorrectionAction.RETRACT, previous_value="Queens", new_value="Kings")

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_field_is_rejected(self, blank):
        with pytest.raises(ValidationError):
            self._event(field=blank)

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_actor_is_rejected(self, blank):
        with pytest.raises(ValidationError):
            self._event(actor=blank)

    def test_naive_created_at_is_rejected(self):
        with pytest.raises(ValidationError):
            self._event(created_at=datetime(2026, 7, 12))  # naive

    def test_is_frozen(self):
        event = self._event()
        with pytest.raises(ValidationError):
            event.new_value = "1899"

    def test_optional_provenance_fields(self):
        event = self._event(rationale="misread the year", agent_call_id="call-7")
        assert event.rationale == "misread the year"
        assert event.agent_call_id == "call-7"


# ---------------------------------------------------------------------------
# CoverageClaim — what an index says it holds (P2)
# ---------------------------------------------------------------------------


class TestCoverageClaim:
    """A claim about a SOURCE's holdings, not about a person.

    This is what makes a negative search usable: "I found nothing, and the index
    covers those years, so the absence is real."
    """

    def _claim(self, **overrides):
        from capsule.contracts.evidence import CoverageClaim

        defaults = dict(
            id="cov-1",
            source_id="src-1",
            snippet_id="snip-1",
            place="Kilmore",
            record_type="R.C. Baptisms",
            year_start=1845,
            year_end=1880,
            created_at=datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return CoverageClaim(**defaults)

    def test_has_no_persona_id(self):
        """A coverage claim must never carry a person: there is none in it.

        Inventing one would put a non-person in the persona table and let a claim
        about a finding aid contaminate claims about a human.
        """
        from capsule.contracts.evidence import CoverageClaim

        assert "persona_id" not in CoverageClaim.model_fields
        with pytest.raises(ValidationError):  # extra="forbid"
            self._claim(persona_id="p1")

    def test_is_snippet_linked_and_evidence_typed(self):
        from capsule.contracts.evidence import EvidenceType

        claim = self._claim()
        assert claim.snippet_id == "snip-1"
        assert claim.evidence_type is EvidenceType.STATED  # default

    def test_verbatim_place_and_type_are_kept_as_printed(self):
        claim = self._claim(place="Co. Cavan / Kilmore", record_type="R.C. Baptisms")
        assert claim.place == "Co. Cavan / Kilmore"
        assert claim.record_type == "R.C. Baptisms"

    def test_covers_the_declared_range_inclusive(self):
        claim = self._claim(year_start=1845, year_end=1880)
        assert claim.covers(1845)
        assert claim.covers(1852)
        assert claim.covers(1880)
        assert not claim.covers(1844)
        assert not claim.covers(1881)

    def test_open_ended_range_is_open(self):
        """"1845-present" covers everything from 1845 on."""
        assert self._claim(year_start=1845, year_end=None).covers(2026)
        assert not self._claim(year_start=1845, year_end=None).covers(1844)
        assert self._claim(year_start=None, year_end=1880).covers(1700)

    def test_BUG_unparseable_range_covers_nothing_rather_than_everything(self):
        """Regression: a claim with no parsed years must NOT answer 'covered'.

        The bug P2 exists to kill is a confident wrong answer about coverage — an
        absence read as meaningful when the index never covered those years. A claim
        whose range we could not read cannot answer the question, so it must decline
        (False) rather than vacuously cover every year an open-ended check would.
        """
        blank = self._claim(year_start=None, year_end=None)
        assert not blank.covers(1852)
        assert not blank.covers(1)

    def test_a_backwards_range_is_refused(self):
        with pytest.raises(ValidationError):
            self._claim(year_start=1880, year_end=1845)

    def test_blank_place_or_record_type_is_refused(self):
        with pytest.raises(ValidationError):
            self._claim(place="  ")
        with pytest.raises(ValidationError):
            self._claim(record_type="")

    def test_created_at_must_be_tz_aware(self):
        with pytest.raises(ValidationError):
            self._claim(created_at=datetime(2026, 7, 14, 12, 0, 0))

    def test_is_frozen(self):
        claim = self._claim()
        with pytest.raises(ValidationError):
            claim.place = "Elsewhere"
