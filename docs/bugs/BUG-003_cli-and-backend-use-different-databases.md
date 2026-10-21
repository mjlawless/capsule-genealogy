# BUG-003 — `capsule ancestry status` reads a different database than the backend writes

**Status:** ✅ **FIXED — VERIFIED 2026-08-24** (2026-07-14, `1b33609`)
> **Re-test run 2026-08-24.** The two commands this bug says disagreed now agree exactly:
> `capsule ancestry status` (default) and `capsule ancestry status --db data/capsule.db` both
> report **`108 mined / 112 known`** and the same four UNMINED apids. The single resolver in
> `capsule/config.py` is doing its job. · **Severity:** 🟠 **HIGH — the command you are meant to trust reports the opposite of the truth**

> ## FIXED — 2026-07-14
>
> One resolver now, `capsule/config.py`, and both the CLI and the backend ask it —
> `CAPSULE_DATA_DIR` moves them together or not at all. The CLI's `--db` / `--captures`
> defaults are no longer independent literals, so `status`, `seed` and `serve` cannot drift
> apart again. (Your inference about `seed` was right: it would have written to the wrong
> file too, and `/ancestry/next` would have come back empty forever.)
>
> **And the guard you asked for:** an EMPTY ledger with another `capsule.db` a directory
> away now says so, instead of reporting a confident zero. A silent, confident, wrong zero
> was the failure mode.
>
> Regressions: `tests/test_bug_003_004.py::test_BUG_003_cli_and_backend_agree_on_the_database_path`
> and `::test_BUG_003_an_empty_ledger_beside_another_database_is_flagged`. 572 tests green.
**Found:** 2026-07-14, adversarial acceptance test of REQ-001
**Fix:** one default path

---

## Symptom

Immediately after successfully mining `7602:56510272` (the backend itself confirms it, by refusing a
re-capture with `409: already mined`):

```
$ capsule ancestry status
0 mined / 0 known                       <-- WRONG

$ capsule ancestry status --db data/capsule.db
1 mined / 1 known                       <-- the truth
```

## Root cause

Two different SQLite files:

```
./capsule.db        172 KB   <- the CLI's DEFAULT (--db capsule.db).  Ledger table exists, EMPTY.
./data/capsule.db   716 KB   <- what the BACKEND actually writes.     Contains the rows.
```

The CLI's `--db` default is `capsule.db` (repo root); `capsule serve` writes to `data/capsule.db`.
Both files have the `ancestry_ledger_event` table, so nothing errors — the CLI just reads an empty
one and reports zero.

## Why this matters more than it looks

`capsule ancestry status` **is the operator's source of truth** for a long run. It is the thing
you check to answer *"what is left?"* and *"did that land?"*. Right now, with the documented default
invocation, it answers **"nothing is done"** — always.

Two concrete harms:
1. **Wasted re-mining.** An operator (or an agent) reads `0 mined`, re-runs records that are already
   captured. Each re-mine is a page load, a download, and a model call.
2. **`seed` almost certainly writes to the wrong file too.** If `capsule ancestry seed --file …` uses
   the same default, the queue is seeded into `./capsule.db` while `/ancestry/next` serves from
   `data/capsule.db` — so the paced queue would come back empty and the whole batch path silently
   does nothing. *(Not yet tested — flagged because it follows from the same default.)*

This is the benign direction of the ledger's core invariant (believing we **don't** hold a record we
**do**), rather than the catastrophic one. But it makes `status` useless as a gate, and `status` is
exactly what the design leans on to make constraint 4 true.

## Fix

Make the CLI and the backend resolve the **same** path — one shared setting (e.g.
`CAPSULE_DB` / a single `settings.db_path`), rather than two independently-defaulted literals.

**And add a guard:** if the ledger table exists but is empty *and* another `capsule.db` exists
elsewhere in the tree, say so rather than reporting a confident zero. A silent, confident, wrong
zero is the failure mode here.

## Suggested regression test

```python
def test_BUG_003_cli_and_backend_agree_on_the_ledger_path():
    """Regression: `ancestry status` must read the same DB `serve` writes.

    It reported '0 mined / 0 known' for a record the backend had just refused to
    re-capture as 'already mined'.
    """
```


---

# ✅ RE-TESTED BY THE REPORTER — 2026-07-14. FIXED.

```
$ capsule ancestry status                      # DEFAULT invocation, no --db
2 mined / 3 known
  UNMINED 7884:19775160 - failed

$ capsule ancestry status --db data/capsule.db  # explicit
2 mined / 3 known                                # AGREES
```

The default and the explicit path now report the same thing. One resolver, one truth.

**And a bonus the fix earned:** the SSDI record that BUG-004 had silently closed as `mined` with a
NULL status now shows up **UNMINED** again under the allow-list. The bad close did not survive the
fix — the ledger re-opened a record we did not really hold. That is the invariant working
retroactively, which is more than I asked for.
