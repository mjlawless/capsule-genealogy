# BUG-017 — A refused duplicate leaves the ledger saying a mined record is STALLED

**Status:** ✅ **FIXED 2026-09-28** (owner-approved design, option 1) · live repair of
`62073:223848` applied · **Severity:** 🟠 **HIGH — the ledger's current state was wrong, and
the external `capsule-db-v1` contract handed that wrong state to consumers**

> ## ✅ FIXED — 2026-09-28
>
> - **New event `dedup-blocked`.** When `/capture` refuses a duplicate of an Ancestry record,
>   `_record_dedup_blocked` (`capsule/backend/app.py`) appends it. It carries the held
>   capture's `capture_id` and **copies that capture's own `captured` facts**
>   (`AncestryLedger.record_dedup_blocked`), so the strict `mined` predicate judges it, and
>   an unmined capture stays unmined. With no `captured` row to copy (an apid-less capture
>   that never entered the ledger), the facts are read from the capture folder. It is
>   written only for an (apid, page_label) the ledger already tracks, so a refusal never
>   puts an unseeded record on the queue. Best-effort: it can never turn the 409 into a 500.
> - **An identity-refusal 409 now writes `failed`.** `postAndShape` skips the failure row
>   only for a dedup block (`body.dedup === "blocked"`), so the extension's own `started` is
>   never left dangling by the other kind of 409 either.
> - **Contract:** `dedup-blocked` added to `capsule-db-v1`'s event list, and to its "mined"
>   definition. Additive; the table is rebuilt once on first open (every row copied).
> - **BUG-005 does not regress:** a capture that dies mid-flight still leaves `started` as its
>   latest event (`test_BUG_017_a_capture_that_dies_mid_flight_still_reads_stalled`).
> - **Tests:** `tests/test_bug_017_req_009_ledger.py`. The end-to-end test was checked by
>   disabling the fix, which makes it fail.
> - **Not changed:** the ledger half of the dedup still falls through to the manifest scan
>   on the extension path, because the extension's `started` is newer than any `captured`
>   when `/capture` runs. The block itself is correct; only the ledger fast path is
>   bypassed. Left as is: the scan is the authority anyway (folder wins, capsule-db-v1 §0).
**Found by:** the 2026-09-28 queue review, while checking REQ-009's live incident against the corpus.
**Relates to:** [BUG-005](./BUG-005_aborted-capture-leaves-no-trace.md) (why `started` exists),
[REQ-004](../specs/REQ-004_atomic-capture-dedup-and-integration-contract.md) §1 (the dedup that
refuses), [REQ-009](../specs/REQ-009_one-call-ancestry-multi-image-records.md) (makes this path common).

## What happened (live, 2026-09-28)

Record `62073:223848` was captured and verified, then a second capture attempt was refused as a
duplicate (the REQ-009 incident). The ledger now reads:

```
started   |                              |   |      | 2026-09-28T04:37:53
captured  | 20260928-ancestrycom-366c3ee9| 1 | full | 2026-09-28T04:38:13
started   |                              |   |      | 2026-09-28T04:53:46   <- latest
```

and every reader of the ledger believes the latest row:

```
$ capsule ancestry status
  UNMINED 62073:223848 — STALLED (started, never finished)

>>> AncestryLedger("data/capsule.db").latest("62073:223848").mined
False
```

The record is mined: its capture holds a full image, `verify_ok` 1, 7 assertions.

## Why

1. Both extension capture paths write `started` **before** they POST — `doCapture`
   (`extension/background.js:228`) and `captureAncestryImage` (`background.js:530`). That is
   BUG-005's fix and it is right: a capture that dies mid-flight must leave a trace.
2. The backend refuses the duplicate with a 409 (`capsule/backend/app.py`, `_dedup_blocked`).
3. `postAndShape` deliberately records **nothing** on a 409 (`background.js:386`: *"A 409 is the
   system WORKING … not a failure to record"*). Correct as far as it goes — but it leaves the
   `started` it wrote with no terminal event after it.
4. `capsule-db-v1` defines *"the latest event per `apid`"* as the record's current state, so a
   refused duplicate turns a mined record into an in-flight one.

## Consequences

- **External consumers are misinformed.** The Genealogy workspace reads `capsule.db` under the
  v1 contract and will see `62073:223848` as not mined.
- **The work queue re-issues a finished record.** `unmined()` / `next()` build on `latest()`, so
  the paced queue can hand this apid out again; each retry is refused again and appends another
  `started`, so the loop never ends.
- **The ledger half of the dedup never fires for extension captures.** `find_capture` trusts the
  ledger row only when it carries a `capture_id` (`capsule/pipeline/dedup.py:125-140`). The
  extension's own `started` is always the newest row by the time `/capture` runs, so the check
  always falls through to the manifest scan. The block still happens (the scan finds the
  verified capture), which is why nothing looked broken — but the ledger check that REQ-004 §1
  describes is dead code on the extension path.

## Not affected

The other eight apids in the corpus with a `started` after a verified capture are deliberate
re-captures, and each has a later `captured` event. `62073:223848` is the only record stuck today.
The same thing happens after an identity-guard 409 from the backend, but the extension runs the
same guard first, so that case is rare.

## Fix options (owner / design call)

1. **Backend appends a terminal event on refusal.** When `/capture` refuses an Ancestry identity
   as a duplicate, it appends an event carrying the held capture's facts (`capture_id`,
   `image_status`, `verify_ok`, counts), so the latest row says "mined" again. Either reuse
   `captured` (simplest; misstates that a capture happened at that time) or add a new event
   type such as `dedup-blocked` (honest; **changes the v1 contract's event enum**, additive).
2. **Check before marking started.** The extension asks `capsule query --apid`'s endpoint (or a
   new `/dedup` probe) first and writes `started` only for a capture that will proceed. Leaves
   the ledger untouched on refusal, but adds a round trip and a check-then-act gap that the
   backend's in-flight lock already covers.
3. **Redefine current state** as "latest event, ignoring a `started` that has no terminal event
   after it when an earlier `captured` is mined". Fixes readers, not the rows, and every
   external consumer would have to re-implement the rule. Not recommended.

Recommended: **option 1 with a new `dedup-blocked` event**, documented in `capsule-db-v1`, plus a
one-off repair event for `62073:223848` (append-only; nothing is edited). Fix this before REQ-009
ships, since REQ-009 routes more captures of held records through the refusal path.

## Acceptance

- A refused duplicate of a mined record leaves `latest(apid).mined == True`.
- `capsule ancestry status` no longer lists it as STALLED.
- A capture that genuinely dies mid-flight still leaves `started` as its latest event (BUG-005
  must not regress).
- Regression test on the backend: a 409 from dedup writes the terminal event.
