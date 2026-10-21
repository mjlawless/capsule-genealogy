"""The Ancestry work ledger and its paced queue (REQ-001 R6, R7).

Answers one question — **"has this record been mined, and completely?"** — and hands out
the next record on the user's own list, one at a time.

**Why "completely" is strict.** Constraint 4 of the requirements: *assume everything will
be walled off or inaccessible later; we must not have to return.* The failure that
violates it is not a failed capture — a failure is loud and gets retried. It is a capture
we BELIEVE succeeded when it did not: a 96x95 thumbnail filed as the record image, and a
ledger row saying "mined" that stops us ever looking again. So a degraded or failed image
means NOT mined, and the record stays on the queue.

**Why the pacing lives here.** The compliance rule is "no bulk crawling" (CLAUDE.md), and
the requirements ask for pacing so the tool "cannot be pointed at the whole site". A delay
the caller is asked to observe is a good intention; a queue that refuses to answer early
is a mechanism. The agent cannot learn what to capture next without asking, and this will
not tell it early. That is the difference between a policy and a guarantee.

Events are append-only, like every other table in this store: the history of a record's
mining is itself evidence, not something to overwrite. Current state is the latest event
whose `page_label` is NULL — the record's default image (REQ-009). An event with a label is
about one EXTRA image of the record (the facing page of a register opening, say) and has its
own history under (apid, page_label); it never speaks for the record as a whole.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from capsule.pipeline.ancestry import parse_apid

_logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# How long the queue waits between handing out records: human research pace, one record
# at a time, in the user's own logged-in session.
DEFAULT_PACE_SECONDS = 45
# A hard ceiling for a single day, so even a stuck loop cannot become a crawl.
DEFAULT_DAILY_CAP = 300

_COLUMNS = (
    "id, apid, collection_id, record_id, event, capture_id, image_status, "
    "assertion_count, persona_count, verify_ok, follow_up, error, created_at, page_label"
)

# Events that restate a capture we HOLD, and so may close a record. `dedup-blocked`
# (BUG-017) is written when a duplicate is refused; it copies the held capture's facts, so
# the same strict predicate judges it.
_HOLDING_EVENTS = {"captured", "dedup-blocked"}

# The ONLY two image outcomes that close a record. Everything else — degraded, failed, or
# never classified at all — leaves it on the queue.
#
# The allow-list is deliberate (BUG-004). This used to be a deny-list, so a capture with NO
# image status (None) fell straight through and counted as MINED: the Social Security Death
# Index closed as "done", and the knowledge that the SS-5 application is the ACTUAL source
# went with it. That is this ledger's own invariant, displaced from images to true sources —
# we believed we held the record when what we held was an index OF it.
#
# Unclassified is not "fine". It is unclassified.
_COMPLETE_IMAGE = {"full", "index-only-no-image"}
# An image outcome that means we still have to go back for this record. `none` (REQ-007
# §3.2) belongs here and NOT in `_COMPLETE_IMAGE`: "this capture holds no record image" is
# work outstanding, and the whole point of splitting it from `index-only-no-image` is that
# only the latter is allowed to close a record.
_INCOMPLETE_IMAGE = {"degraded-canvas", "failed", "none"}


@dataclass(frozen=True)
class LedgerRow:
    apid: str
    event: str
    capture_id: Optional[str] = None
    image_status: Optional[str] = None
    assertion_count: Optional[int] = None
    persona_count: Optional[int] = None
    verify_ok: Optional[bool] = None
    follow_up: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    #: REQ-009 — None is the record's default image; a label names one extra image.
    page_label: Optional[str] = None

    @property
    def mined(self) -> bool:
        """Is this record DONE — genuinely, not merely attempted?

        Every clause is a way we have already been burned:
          - the last thing that happened was a successful capture (not a failure), or a
            refused duplicate restating one (`dedup-blocked`, BUG-017),
          - it verified (its hashes match what is on disk),
          - it actually yielded assertions (an empty extraction is not a mined record),
          - its image is FULL or explicitly, classifiably ABSENT — never merely unknown,
          - and if it is an INDEX, we have named the real source it points at.

        A degraded (screen-res) or failed image is not mined: that stops a thumbnail from
        quietly closing a record forever. An UNCLASSIFIED image is not mined either — that
        was BUG-004, where a capture with no image status closed the Social Security Death
        Index as "done" and lost the SS-5 with it. And an index without a follow-up is not
        mined, because taking the index is not taking the record.
        """
        if self.event not in _HOLDING_EVENTS:
            return False
        if not self.verify_ok:
            return False
        if not self.assertion_count:
            return False
        # Allow-list, not deny-list: an unclassified image (None) is NOT a pass (BUG-004).
        if self.image_status not in _COMPLETE_IMAGE:
            return False
        # An index-only record is complete only once we have said what the REAL source is.
        # Taking the index is not taking the record: closing the SSDI without naming the
        # SS-5 loses the only thing that tells us where the record actually lives.
        if self.image_status == "index-only-no-image" and not self.follow_up:
            return False
        return True

    @property
    def stalled(self) -> bool:
        """A capture that BEGAN and never reported back — tried, and vanished (BUG-005).

        Distinct from `seeded` (never attempted) and from `failed` (attempted and told us
        why). This is the silent one: the tab navigated away, the browser died, the page
        was closed. The record is not lost — it is still unmined and still on the queue —
        but an operator deserves to know it was tried, rather than discovering it because
        someone happened to see a toast flash.
        """
        return self.event == "started"

    @property
    def status(self) -> str:
        """One word for what is going on with this record, for the operator."""
        if self.mined:
            return "mined"
        if self.stalled:
            return "STALLED (started, never finished)"
        if self.event == "failed":
            return f"FAILED: {self.error or 'unknown'}"
        if self.image_status in _INCOMPLETE_IMAGE:
            return f"unmined ({self.image_status})"
        return "pending"


def _migrate_events(connection: sqlite3.Connection) -> None:
    """Widen the ledger's CHECK constraints when the vocabulary has grown.

    `CREATE TABLE IF NOT EXISTS` does NOT alter an existing table, so a database created
    before a value was legal would reject it — the row that records what happened would be
    the row that fails. SQLite cannot alter a CHECK in place either, so the table is
    rebuilt: new table, copy the rows, drop, rename.

    Three widenings so far, and one sentinel serves them all: `started` (BUG-005 — a
    capture that began and vanished), `none` (REQ-007 §3.2 — this capture holds no record
    image), and `dedup-blocked` (BUG-017 — a refused duplicate restating the held capture),
    which arrived together with the new trailing `page_label` column (REQ-009). Testing for
    the NEWEST value alone is sufficient and stays correct as the list grows, because the
    rebuild always writes today's full schema whatever the table started as. Rows copied
    from an older table get `page_label` NULL: every one of them is about the record's
    default image, which is exactly what NULL means.

    The append-only triggers guard UPDATE and DELETE on rows; this is DDL, and it copies
    every row rather than changing any, so no history is lost or rewritten.
    """
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ancestry_ledger_event'"
    ).fetchone()
    if not row or not row[0] or "'dedup-blocked'" in row[0]:
        return  # brand new (already correct), or already migrated

    with connection:
        connection.executescript(
            """
            DROP TRIGGER IF EXISTS ancestry_ledger_no_update;
            DROP TRIGGER IF EXISTS ancestry_ledger_no_delete;

            CREATE TABLE ancestry_ledger_event_new (
                id              TEXT PRIMARY KEY,
                apid            TEXT NOT NULL,
                collection_id   TEXT NOT NULL,
                record_id       TEXT NOT NULL,
                event           TEXT NOT NULL CHECK (event IN
                                    ('seeded', 'issued', 'started', 'captured', 'failed',
                                     'remine-requested', 'dedup-blocked')),
                capture_id      TEXT,
                image_status    TEXT CHECK (image_status IS NULL OR image_status IN
                                    ('full', 'degraded-canvas', 'index-only-no-image',
                                     'failed', 'none')),
                assertion_count INTEGER,
                persona_count   INTEGER,
                verify_ok       INTEGER CHECK (verify_ok IS NULL OR verify_ok IN (0, 1)),
                follow_up       TEXT,
                error           TEXT,
                created_at      TEXT NOT NULL,
                page_label      TEXT
            );

            INSERT INTO ancestry_ledger_event_new
                (id, apid, collection_id, record_id, event, capture_id, image_status,
                 assertion_count, persona_count, verify_ok, follow_up, error, created_at)
                SELECT id, apid, collection_id, record_id, event, capture_id, image_status,
                       assertion_count, persona_count, verify_ok, follow_up, error, created_at
                FROM ancestry_ledger_event;

            DROP TABLE ancestry_ledger_event;
            ALTER TABLE ancestry_ledger_event_new RENAME TO ancestry_ledger_event;

            CREATE INDEX IF NOT EXISTS idx_ancestry_ledger_apid
                ON ancestry_ledger_event (apid);

            CREATE TRIGGER ancestry_ledger_no_update
            BEFORE UPDATE ON ancestry_ledger_event
            BEGIN
                SELECT RAISE(ABORT, 'append-only: ledger events cannot be updated');
            END;

            CREATE TRIGGER ancestry_ledger_no_delete
            BEFORE DELETE ON ancestry_ledger_event
            BEGIN
                SELECT RAISE(ABORT, 'append-only: ledger events cannot be deleted');
            END;
            """
        )
    _logger.info("rebuilt the Ancestry ledger to widen its CHECK vocabulary")


class TooSoon(Exception):
    """The queue refuses to issue another record yet. Carries the wait, in seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"pacing: try again in {retry_after}s")
        self.retry_after = retry_after


class DailyCapReached(Exception):
    """The day's issue cap is spent. Not an error — a guardrail doing its job."""


class AncestryLedger:
    """Append-only ledger + paced queue, over the same SQLite evidence DB."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        now: Optional[Callable[[], datetime]] = None,
        new_id: Optional[Callable[[], str]] = None,
        pace_seconds: int = DEFAULT_PACE_SECONDS,
        daily_cap: int = DEFAULT_DAILY_CAP,
    ) -> None:
        self.connection = sqlite3.connect(str(db_path))
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate_events(self.connection)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._new_id = new_id or (lambda: uuid.uuid4().hex)
        self.pace_seconds = pace_seconds
        self.daily_cap = daily_cap

    def close(self) -> None:
        self.connection.close()

    # -- writing -------------------------------------------------------------

    def _append(self, apid: str, event: str, **fields) -> None:
        ids = parse_apid(apid)
        if ids is None:
            raise ValueError(f"not a readable _APID: {apid!r}")
        collection_id, record_id = ids
        verify_ok = fields.get("verify_ok")
        self.connection.execute(
            f"INSERT INTO ancestry_ledger_event ({_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._new_id(),
                f"{collection_id}:{record_id}",
                collection_id,
                record_id,
                event,
                fields.get("capture_id"),
                fields.get("image_status"),
                fields.get("assertion_count"),
                fields.get("persona_count"),
                None if verify_ok is None else int(bool(verify_ok)),
                fields.get("follow_up"),
                fields.get("error"),
                self._now().isoformat(),
                fields.get("page_label"),
            ),
        )

    def seed(self, apids: Iterable[str]) -> int:
        """Put records on the work queue. Idempotent — re-seeding adds nothing.

        Returns how many were NEWLY seeded, so a re-run honestly reports "0 new".
        """
        added = 0
        with self.connection:
            for apid in apids:
                ids = parse_apid(apid)
                if ids is None:
                    continue
                key = f"{ids[0]}:{ids[1]}"
                seen = self.connection.execute(
                    "SELECT 1 FROM ancestry_ledger_event WHERE apid = ? LIMIT 1", (key,)
                ).fetchone()
                if seen:
                    continue
                self._append(apid, "seeded")
                added += 1
        return added

    def record_capture(
        self,
        apid: str,
        *,
        capture_id: str,
        image_status: Optional[str],
        assertion_count: int,
        persona_count: int,
        verify_ok: bool,
        follow_up: Optional[str] = None,
        page_label: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """A capture landed. `error`, if given, says why its record image is missing (REQ-010)."""
        with self.connection:
            self._append(
                apid,
                "captured",
                capture_id=capture_id,
                image_status=image_status,
                assertion_count=assertion_count,
                persona_count=persona_count,
                verify_ok=verify_ok,
                follow_up=follow_up,
                page_label=page_label,
                error=error,
            )

    def record_dedup_blocked(
        self,
        apid: str,
        *,
        capture_id: str,
        page_label: Optional[str] = None,
        fallback: Optional[Callable[[], dict]] = None,
    ) -> bool:
        """A duplicate of a held capture was refused: restate the held capture (BUG-017).

        Both extension capture paths write `started` BEFORE they POST (BUG-005), and a
        refusal is not a failure, so nothing used to follow it. The refused attempt's
        `started` then stood as the record's latest event, and a captured, verified record
        read as STALLED — to `capsule ancestry status`, to the paced queue (which re-issued
        it), and to every consumer of the capsule-db-v1 contract.

        The facts are COPIED from the held capture's own `captured` event, so this row is
        judged by the same strict `mined` predicate as the original, never granted a pass
        of its own. Only when the ledger has no such event (an apid-less capture that never
        entered the ledger) is the caller's `fallback` called to supply them — lazily, so
        the common refusal never reads the capture folder.

        Written only for an (apid, page_label) the ledger already tracks: a refusal must not
        put a record on the work queue that nobody seeded. Returns whether a row was written.
        """
        key = self._key(apid)
        if key is None:
            return False
        known = self.connection.execute(
            "SELECT 1 FROM ancestry_ledger_event WHERE apid = ? AND page_label IS ? LIMIT 1",
            (key, page_label),
        ).fetchone()
        if not known:
            return False
        held = self.connection.execute(
            "SELECT image_status, assertion_count, persona_count, verify_ok, follow_up "
            "FROM ancestry_ledger_event WHERE capture_id = ? AND event = 'captured' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (capture_id,),
        ).fetchone()
        if held is not None:
            image_status, assertion_count, persona_count, verify_ok, follow_up = held
            facts = dict(
                image_status=image_status,
                assertion_count=assertion_count,
                persona_count=persona_count,
                verify_ok=None if verify_ok is None else bool(verify_ok),
                follow_up=follow_up,
            )
        else:
            facts = dict(fallback()) if fallback is not None else {}
        with self.connection:
            self._append(
                apid, "dedup-blocked", capture_id=capture_id, page_label=page_label, **facts
            )
        return True

    def record_started(self, apid: str, *, page_label: Optional[str] = None) -> None:
        """Mark a capture as IN FLIGHT, before the work begins (BUG-005).

        This is the only way a capture that dies mid-flight leaves any trace at all. The
        one that prompted this navigated away while the capture was still running: the POST
        never arrived, so the backend could not have logged a failure — it never learned
        the capture existed. The record simply stayed `seeded`, indistinguishable from one
        nobody had tried yet.

        A `started` with no terminal event after it means: TRIED, AND VANISHED. That is a
        different thing from "not done yet", and over a run of hundreds of records an operator has to be able
        to tell them apart — otherwise a silent abort is only ever caught by someone
        happening to see a toast flash, which is exactly how this was found.
        """
        with self.connection:
            self._append(apid, "started", page_label=page_label)

    def record_failure(
        self, apid: str, *, error: str, page_label: Optional[str] = None
    ) -> None:
        """A failure must never abort a run (R7.3): it is recorded, and we move on.

        The `error` is the point. The ledger had this column from the start and never wrote
        to it, so every failure looked like silence.
        """
        with self.connection:
            self._append(apid, "failed", error=error, page_label=page_label)

    def request_remine(self, apid: str) -> None:
        with self.connection:
            self._append(apid, "remine-requested")

    # -- reading -------------------------------------------------------------

    @staticmethod
    def _key(apid: str) -> Optional[str]:
        ids = parse_apid(apid)
        return None if ids is None else f"{ids[0]}:{ids[1]}"

    def latest(self, apid: str, page_label: Optional[str] = None) -> Optional[LedgerRow]:
        """The latest event for one image of a record. No label = the record's default
        image, which is the record's own state (REQ-009)."""
        key = self._key(apid)
        if key is None:
            return None
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM ancestry_ledger_event WHERE apid = ? AND page_label IS ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (key, page_label),
        ).fetchone()
        return _to_row(row) if row else None

    def images(self, apid: str) -> list[LedgerRow]:
        """The latest event for each EXTRA (labelled) image of a record, by label (REQ-009).

        So a lookup by apid can point at every image the collection holds, not only the
        default one.
        """
        key = self._key(apid)
        if key is None:
            return []
        rows = self.connection.execute(
            f"SELECT {_COLUMNS} FROM ancestry_ledger_event e "
            "WHERE e.apid = ? AND e.page_label IS NOT NULL "
            "AND e.rowid = (SELECT x.rowid FROM ancestry_ledger_event x "
            "               WHERE x.apid = e.apid AND x.page_label = e.page_label "
            "               ORDER BY x.created_at DESC, x.rowid DESC LIMIT 1) "
            "ORDER BY e.page_label",
            (key,),
        ).fetchall()
        return [_to_row(r) for r in rows]

    def is_mined(self, apid: str) -> bool:
        row = self.latest(apid)
        return bool(row and row.mined)

    def all_latest(self) -> list[LedgerRow]:
        rows = self.connection.execute(
            f"SELECT {_COLUMNS} FROM ancestry_ledger_event e "
            "WHERE e.page_label IS NULL "
            "AND e.rowid = (SELECT x.rowid FROM ancestry_ledger_event x "
            "               WHERE x.apid = e.apid AND x.page_label IS NULL "
            "               ORDER BY x.created_at DESC, x.rowid DESC LIMIT 1) "
            "ORDER BY e.apid"
        ).fetchall()
        return [_to_row(r) for r in rows]

    def unmined(self) -> list[LedgerRow]:
        return [row for row in self.all_latest() if not row.mined]

    def follow_ups(self) -> list[LedgerRow]:
        """Index-only records whose TRUE source is still to be chased (R4.2)."""
        return [row for row in self.all_latest() if row.follow_up]

    # -- the paced queue -----------------------------------------------------

    def next_record(self) -> Optional[str]:
        """The next unmined `_APID`, or None when the queue is empty.

        Raises `TooSoon` when the pace has not elapsed, and `DailyCapReached` at the cap.
        This IS the pacing mechanism: the caller cannot find out what to capture next
        without asking, and asking too often simply does not work. A rate limit the caller
        is trusted to honor is not a rate limit.
        """
        now = self._now()

        issued_today = self.connection.execute(
            "SELECT COUNT(*) FROM ancestry_ledger_event WHERE event = 'issued' "
            "AND created_at >= ?",
            ((now - timedelta(days=1)).isoformat(),),
        ).fetchone()[0]
        if issued_today >= self.daily_cap:
            raise DailyCapReached(
                f"{issued_today} records issued in the last 24h (cap {self.daily_cap})"
            )

        last = self.connection.execute(
            "SELECT created_at FROM ancestry_ledger_event WHERE event = 'issued' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if last:
            elapsed = (now - datetime.fromisoformat(last[0])).total_seconds()
            if elapsed < self.pace_seconds:
                raise TooSoon(retry_after=int(self.pace_seconds - elapsed) + 1)

        candidate = next((row.apid for row in self.unmined()), None)
        if candidate is None:
            return None

        with self.connection:
            self._append(candidate, "issued")
        return candidate


def _to_row(row: Sequence) -> LedgerRow:
    (
        _id,
        apid,
        _collection_id,
        _record_id,
        event,
        capture_id,
        image_status,
        assertion_count,
        persona_count,
        verify_ok,
        follow_up,
        error,
        created_at,
        page_label,
    ) = row
    return LedgerRow(
        apid=apid,
        event=event,
        capture_id=capture_id,
        image_status=image_status,
        assertion_count=assertion_count,
        persona_count=persona_count,
        verify_ok=None if verify_ok is None else bool(verify_ok),
        follow_up=follow_up,
        error=error,
        created_at=created_at,
        page_label=page_label,
    )
