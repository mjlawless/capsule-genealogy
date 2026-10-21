"""REQ-001 R6/R7 — the work ledger and its paced queue.

The ledger exists to make constraint 4 true: *assume everything will be walled off; we
must not have to return.* The failure that breaks it is not a failed capture — a failure
is loud and gets retried. It is a capture we BELIEVE succeeded when it did not: a
thumbnail filed as the record image, and a ledger row saying "mined" that stops us ever
looking again.

The pacing exists so the tool cannot be pointed at the whole site. It is enforced by the
queue refusing to answer, not by asking the caller to be patient.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

_T0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
_CENSUS = "1,7602::56510272"
_SSDI = "1,3693::35684453"


class _Clock:
    """An injectable clock, so pacing is tested without sleeping."""

    def __init__(self, start: datetime = _T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _ledger(tmp_path, clock=None, **kwargs):
    from capsule.store.ledger import AncestryLedger

    n = [0]

    def _ids() -> str:
        n[0] += 1
        return f"ev-{n[0]}"

    return AncestryLedger(
        tmp_path / "capsule.db", now=clock or _Clock(), new_id=_ids, **kwargs
    )


def _capture(ledger, apid=_CENSUS, **overrides):
    kwargs = dict(
        capture_id="cap-1",
        image_status="full",
        assertion_count=15,
        persona_count=9,
        verify_ok=True,
    )
    kwargs.update(overrides)
    ledger.record_capture(apid, **kwargs)


class TestMinedDefinition:
    """"Mined" must mean DONE — not merely attempted."""

    def test_a_full_capture_is_mined(self, tmp_path):
        ledger = _ledger(tmp_path)
        _capture(ledger)
        assert ledger.is_mined(_CENSUS)

    def test_BUG_a_degraded_image_does_NOT_close_the_record(self, tmp_path):
        """Regression: a screen-resolution scan must not end the hunt.

        This clause is what satisfies constraint 4. If a 1370px canvas counted as mined,
        the record would leave the queue forever, and we would not discover — until
        Ancestry was gone — that we cannot read the census column.
        """
        ledger = _ledger(tmp_path)
        _capture(ledger, image_status="degraded-canvas")
        assert not ledger.is_mined(_CENSUS)
        assert ledger.unmined()[0].apid == "7602:56510272"

    def test_BUG_a_thumbnail_does_NOT_close_the_record(self, tmp_path):
        ledger = _ledger(tmp_path)
        _capture(ledger, image_status="failed")
        assert not ledger.is_mined(_CENSUS)

    def test_an_index_only_record_IS_mined_once_it_names_its_true_source(self, tmp_path):
        """No image is the honest, COMPLETE answer for an index — but only once we have
        said what the index POINTS AT.

        Closing the Social Security Death Index without naming the SS-5 application was
        BUG-004: we "mined" an index OF the record and lost the record itself.
        """
        ledger = _ledger(tmp_path)
        _capture(
            ledger,
            image_status="index-only-no-image",
            follow_up="the SS-5 application (Social Security Administration)",
        )
        assert ledger.is_mined(_CENSUS)

    def test_BUG_004_an_index_without_a_follow_up_is_NOT_mined(self, tmp_path):
        """Taking the index is not taking the record."""
        ledger = _ledger(tmp_path)
        _capture(ledger, image_status="index-only-no-image", follow_up=None)
        assert not ledger.is_mined(_CENSUS)

    def test_BUG_004_an_unclassified_image_is_NOT_mined(self, tmp_path):
        """The dangerous path: a record closing as done while never classified at all."""
        ledger = _ledger(tmp_path)
        _capture(ledger, image_status=None)
        assert not ledger.is_mined(_CENSUS)

    def test_a_capture_with_no_assertions_is_not_mined(self, tmp_path):
        """We came for the record's contents. An empty extraction did not drain it."""
        ledger = _ledger(tmp_path)
        _capture(ledger, assertion_count=0)
        assert not ledger.is_mined(_CENSUS)

    def test_a_capture_that_failed_verify_is_not_mined(self, tmp_path):
        ledger = _ledger(tmp_path)
        _capture(ledger, verify_ok=False)
        assert not ledger.is_mined(_CENSUS)

    def test_a_failure_leaves_the_record_on_the_queue(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.record_failure(_CENSUS, error="download never arrived")
        assert not ledger.is_mined(_CENSUS)
        assert ledger.latest(_CENSUS).error == "download never arrived"


class TestSeedAndQueue:
    def test_seed_is_idempotent(self, tmp_path):
        ledger = _ledger(tmp_path)
        assert ledger.seed([_CENSUS, _SSDI]) == 2
        assert ledger.seed([_CENSUS, _SSDI]) == 0  # a re-run honestly adds nothing

    def test_next_issues_an_unmined_record(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.seed([_CENSUS])
        assert ledger.next_record() == "7602:56510272"

    def test_BUG_a_mined_record_is_never_issued_again(self, tmp_path):
        """R6.3: re-running a mined record is a no-op, not a duplicate capture."""
        ledger = _ledger(tmp_path, pace_seconds=0)
        ledger.seed([_CENSUS])
        _capture(ledger)
        assert ledger.next_record() is None  # the queue is empty: it is done

    def test_a_remine_puts_it_back(self, tmp_path):
        ledger = _ledger(tmp_path, pace_seconds=0)
        ledger.seed([_CENSUS])
        _capture(ledger)
        ledger.request_remine(_CENSUS)
        assert ledger.next_record() == "7602:56510272"


class TestPacing:
    def test_BUG_the_queue_refuses_to_answer_early(self, tmp_path):
        """The pacing is a MECHANISM, not a request.

        The agent cannot learn what to capture next without asking the queue, and the
        queue will not tell it early. A rate limit the caller is trusted to honor is not a
        rate limit — and CLAUDE.md's "do not bulk-scrape" deserves a guarantee.
        """
        from capsule.store.ledger import TooSoon

        clock = _Clock()
        ledger = _ledger(tmp_path, clock, pace_seconds=45)
        ledger.seed([_CENSUS, _SSDI])

        assert ledger.next_record() is not None
        with pytest.raises(TooSoon) as exc:
            ledger.next_record()
        assert exc.value.retry_after > 0

        clock.advance(45)
        assert ledger.next_record() is not None  # patience is rewarded

    def test_the_daily_cap_stops_a_runaway_loop(self, tmp_path):
        from capsule.store.ledger import DailyCapReached

        clock = _Clock()
        ledger = _ledger(tmp_path, clock, pace_seconds=0, daily_cap=2)
        ledger.seed([_CENSUS, _SSDI, "1,1234::5678"])

        ledger.next_record()
        ledger.next_record()
        with pytest.raises(DailyCapReached):
            ledger.next_record()


class TestFollowUps:
    def test_an_index_only_record_names_the_true_source(self, tmp_path):
        """R4.2: capturing the SSDI is not capturing the SS-5. Say so, in the ledger."""
        ledger = _ledger(tmp_path)
        _capture(
            ledger,
            apid=_SSDI,
            capture_id="cap-2",
            image_status="index-only-no-image",
            assertion_count=6,
            persona_count=1,
            follow_up="SS-5 application (Social Security Administration)",
        )
        (row,) = ledger.follow_ups()
        assert "SS-5" in row.follow_up
        # It IS mined — we took everything the index holds — and still owes a follow-up.
        assert row.mined


def test_the_ledger_is_append_only(tmp_path):
    """A record's mining history is evidence, not something to overwrite."""
    import sqlite3

    ledger = _ledger(tmp_path)
    ledger.seed([_CENSUS])
    with pytest.raises(sqlite3.IntegrityError):
        with ledger.connection:
            ledger.connection.execute("UPDATE ancestry_ledger_event SET event = 'captured'")
    with pytest.raises(sqlite3.IntegrityError):
        with ledger.connection:
            ledger.connection.execute("DELETE FROM ancestry_ledger_event")
