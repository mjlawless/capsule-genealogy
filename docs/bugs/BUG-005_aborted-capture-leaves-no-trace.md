# BUG-005 — An aborted capture leaves no trace: the `error` column is never populated

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-14, `8fcad7a`)
> **Verified 2026-08-24 against the live ledger.** All three mechanisms are demonstrably live:
> 1. **`started` before the work** — **105 `started` events across 90 apids**. The in-flight
>    marker the fix called load-bearing is being written.
> 2. **`failed` carries its reason** — **16 `failed` events**, reasons recorded verbatim
>    (`cannot verify identity: expected 7884:119680425…`, `ValueError: extraction reply does
>    not match schema…`). No more silent raises.
> 3. **The extension reports what the backend cannot see** — those identity-mismatch rows can
>    only have arrived via `POST /ancestry/failed`; the backend never saw those captures.
>
> **Currently 0 stalled** (no `started` left without a terminal event) — the healthy state, with
> the machinery proven present by the 105 markers that did resolve. · **Severity:** 🟡 **MEDIUM — safe, but silent. You cannot tell "tried and died" from "not yet attempted".**

> ## FIXED — 2026-07-14
>
> All three items, and the diagnosis was right that **safety was already correct; visibility
> was not.**
>
> The important subtlety: **backend error-logging alone could not have fixed the case you
> hit.** The POST never arrived, so the backend never learned the capture existed — there
> was nothing for it to log. Your item 3 (the in-flight marker) was not optional, it was
> load-bearing. So:
>
> 1. **`started` is written BEFORE the work**, from the service worker — which survives the
>    page navigating away. A `started` with no terminal event means *tried, and vanished*.
>    That row is the only possible evidence of a capture killed in flight.
> 2. **A capture that reaches the backend and raises now records `failed` with its reason**
>    rather than raising into silence. A 409 (already-mined, identity refusal) is the system
>    *working* and is deliberately NOT recorded as a failure.
> 3. **The extension reports what the backend cannot see** — an identity mismatch, a dead
>    render — to a new `POST /ancestry/failed`.
>
> **`capsule ancestry status` now separates STALLED (tried and vanished, in red) from
> pending (never attempted)** — the distinction you actually need across hundreds of records.
>
> Schema note worth knowing, since it touches your live DB: `event` gained a value, and a
> CHECK constraint canNOT be added to an existing table by `CREATE TABLE IF NOT EXISTS` —
> your database would have **rejected the very row that records "a capture began"**. The
> ledger therefore rebuilds the table on first open, copying every row: no history rewritten,
> none lost. Pinned by a test that constructs an old-schema DB and migrates it.
>
> A stalled record is still re-issued by the queue — it was never lost, only invisible.
>
> Regressions: `tests/test_bug_005.py`. 578 tests green.
>
> **Re-test:** kill a capture mid-flight on purpose (navigate away) and confirm it shows as
> `STALLED` rather than `pending`.
**Found:** 2026-07-14, during the first production run
**Spotted by:** the owner — *"I saw an error flash through the Capsule previously. Did anything fail?"*

---

## What happened

Capturing `60901:626630211` (U.S. Social Security Applications and Claims Index). The agent
dispatched the capture and then **navigated to the next record while it was still in flight**. The
capture runs in the content script of that page, so the navigation killed it.

**Result: nothing at all.** No capture folder. No ledger row. No error.

```
$ select count(*) from ancestry_ledger_event where error is not null;
0                                  <-- across the ENTIRE ledger
```

The record simply remained `seeded`, indistinguishable from one never attempted.

## The good part — the invariant HELD

The aborted capture wrote **nothing**, so the record stayed **UNMINED** and stayed on the queue. It
was **not** falsely closed. That is exactly the designed behaviour, and it is the important half:
*we did not come to believe we held a record we did not hold.* The retry mined it cleanly.

## The gap

The ledger **has** an `error` column. It is never written to. So:

| what happened | what the ledger says |
|---|---|
| never attempted | `seeded` |
| **attempted, aborted, died** | **`seeded`** |

Over a long run these are very different things, and an operator needs to tell them apart. A
silent abort currently looks identical to "not done yet" — it is only discovered if a human happens
to see a toast flash, which is exactly how this one was found.

## What is needed

1. **Write the error row.** When a capture fails — aborted, network, backend non-2xx, extension
   disconnect — append an `event='error'` (or `event='attempted'` with `error` set) row. Safety is
   already right; **visibility is not.**
2. **Surface it in `ancestry status`**, e.g. `UNMINED 60901:626630211 — error: aborted (navigation)`,
   so the queue distinguishes *stalled* from *pending*.
3. **Consider a heartbeat / in-flight marker** so a capture killed by navigation can be detected at
   all. The content script could mark `event='started'` before the POST; a `started` with no
   terminal event is a died-in-flight record.

## Note on the trigger (agent-side, already fixed in the caller)

The proximate cause was the agent pipelining — dispatching a capture and navigating on before it
finished. **The run procedure now awaits completion before navigating.** But the tool should not
depend on a well-behaved caller to keep an honest ledger: a browser crash, a tab close, or a user
clicking a link would produce the same silent hole.
