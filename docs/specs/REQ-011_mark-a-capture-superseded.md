# REQ-011 — mark a capture as superseded by another

**Status:** ✅ **DONE 2026-10-21.** Filed 2026-10-03 (triaged 2026-10-21 from `docs/inbox/`).
Approved as a lifecycle event; the premise was corrected the same day (both options needed a new
table), and the owner's blanket deference the same day re-confirmed the recommendation.

> ## ✅ DONE — 2026-10-21
>
> - **Store:** `capture_lifecycle_event` (`superseded` | `reinstated`), append-only with
>   triggers; CHECKs require `superseded_by` exactly for `superseded` and forbid
>   self-supersession. Documented in `capsule-db-v1`.
> - **Folder:** the old capture's hash-excluded `lifecycle.json` (capture-folder-v2 §6),
>   regenerated from the store, so the folder-sourced index can show it with no database.
> - **Index:** a "superseded" chip linking to the replacement, plus a "Superseded by" line
>   (captures-index-v2 §4b). Superseded captures stay listed.
> - **CLI:** `capsule supersede <old> --by <new> [--reason …]`, and
>   `capsule supersede <old> --reinstate`. It refuses self-supersession, a repeat, and any
>   supersession that would close a loop (no current capture).
> - **Consistency between the store and the folder** (eight Codex adversarial-review
>   rounds, each finding fixed with a regression test):
>   - the check, the append and the sidecar write run under SQLite's write lock
>     (`BEGIN IMMEDIATE`), which serializes the CLI and the server, so two processes cannot
>     both pass the loop check;
>   - a failed sidecar write, or a failed COMMIT, rolls the event back and re-syncs the file;
>   - order is append order (rowid), never the clock;
>   - `capsule index` re-projects every capture the store has events for, and never deletes;
>   - **history conflicts are refused, not guessed:** a sidecar holding event ids the
>     database lacks (a restored older backup, or a crash between the file write and the
>     COMMIT) is kept, and changes to that capture raise `LifecycleConflict`, naming the
>     remedy. **`capsule supersede <id> --resync`** is the explicit human resolution:
>     "this database is the current one, rewrite the file from it."
>   - a capture stays superseded when its replacement's folder is unavailable (trashed), and
>     shows that in the index.
> - **Deferred, with reason:** `index.html` publication is not serialized across processes,
>   so two concurrent commands can leave a stale index. This is true of every index writer
>   (captures, notes, trash), it heals on the next write or `capsule index`, and it is
>   tracked in the backlog rather than fixed inside REQ-011.
> - **Not done:** dedup still treats a superseded capture like any other. Ingest has its own
>   identity space, so the live case is unaffected.
> - 19 tests in `tests/test_req_011_supersede.py`.

**Priority:** 🟢 LOW — hygiene. Nothing is lost; a stale capture just keeps looking current.
**Relates to:** [BUG-022](../bugs/BUG-022_newspapers-com-armed-download-from-img-subdomain.md)
(the failed armed capture that produced the orphan),
[BUG-017](../bugs/BUG-017_dedup-block-leaves-ledger-stalled.md) (the append-only event pattern),
[`capsule-db-v1`](capsule-db-v1.md).

## The gap

Capsule is append-only, so a bad or incomplete capture cannot be edited or removed from the DB.
Delete exists, but it only moves the folder to `_trash/` and retains the rows. There is no way to
say *"this capture was replaced by that one"*, so a consumer reading the DB still sees both as
live.

**Live case (Genealogy research workspace, 2026-10-03):** an orphan source is a
page-render PDF only (`record_type "obituary index"`, 0 assertions), left by an armed capture that
missed its image (BUG-022). The same Newspapers.com page was then ingested as
`20261003-newspaperscom-4aa5ff8c` (`verify_ok`), and that ingest is the one the tree cites. The
Genealogy side asked for the orphan to be marked superseded "if Capsule has a way to do that".
It does not.

## Proposal

A `capsule supersede <old> --by <new> [--reason …]` command that appends a **supersession event**
linking the old source to the new one. (Not `capsule capture supersede`: `capture` is one command
whose positional argument is a URL, so that spelling would try to capture the URL "supersede".)

- Append-only: a new row in a **new** append-only table, never an update of the old row. No
  existing table can hold it (see Ruling). The old capture, its folder and its assertions stay
  exactly as they are.
- Read side: the captures index and `capsule query` show `superseded_by: <source id>` on the old
  capture, and by default list the superseding one as current. A consumer opts in to seeing
  superseded captures.
- Reversible by a later event (a superseding event can itself be superseded or withdrawn), as the
  correction loop's superseding assertions are.
- Never merges identities: this links two **sources**, and says nothing about the people in them.
  The hard rule against merging person identities is untouched.

## Ruling (2026-10-21)

The question was whether "superseded" is a **source-level relation** (evidence-typed, like the
RootsIreland ↔ NLI relation in the project notes) or a **lifecycle event on the capture** (like the
ledger's `dedup-blocked`). **The owner chose the lifecycle event.**

**Correction (2026-10-21, same day, from a review of the schema).** The comparison the owner ruled
on said the lifecycle option was "simpler and needs no new relation table", and that the
relation option "survives a trash/restore". Neither holds:

- **No existing table can carry a supersession.** `source` has a `BEFORE UPDATE` trigger, so a
  `superseded_by` column there could never be set after insert. `ancestry_ledger_event` has
  `apid NOT NULL`, so it cannot hold a Newspapers.com (or any non-Ancestry) capture.
  `correction_event` is assertion-scoped (`assertion_id NOT NULL`). `capture_note` is
  source-scoped but free text, so a supersession written there would not be queryable.
- **So both options are one new append-only table**, and `superseded_by` is a field **derived in
  the index** from that table, not a column on any existing table.
- **Trash keeps DB rows**, so a row of either kind survives a trash/restore. That is not a
  difference either.

What is left is meaning, not cost: a **lifecycle event** says "this capture is no longer the
current one" (a state of the old capture, reversible by a later event), while an **evidence-typed
relation** says "these two sources stand in relation R" (and could later carry other kinds, such as
derivative-of). **Recommendation unchanged: lifecycle event**, because supersession is about which
capture to cite, not about how two documents relate as evidence. **Owed: owner re-confirmation on
the corrected comparison.**

## Not in scope

Automatic detection (the same URL captured twice). Ingest legitimately captures a page already
captured, so a human or the Genealogy workspace decides.

## Definition of done

The command exists with tests (append-only, reversible, index shows the link), the contract docs
carry the additive field, and the orphan source is marked superseded by `4aa5ff8c…` with one repair
event.
