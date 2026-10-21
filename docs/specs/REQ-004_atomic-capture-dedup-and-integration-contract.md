# REQ-004 — Atomic capture dedup + an integration contract for external consumers

**Status:** ✅ **DONE — VERIFIED 2026-08-24; last gap closed on live evidence 2026-09-28.**
The live check that closed it also found [BUG-017](../bugs/BUG-017_dedup-block-leaves-ledger-stalled.md).

| § | state | evidence |
|---|---|---|
| §1 dedup (non-Ancestry URL identity) | ✅ **LIVE-VERIFIED 2026-08-22/23** | REQ-007 §1 live run: the choke point issued a real `HTTP 409: already mined`, the false block was diagnosed as the register-leaf defect, and after `page_label` joined the identity the second leaf recorded `dedup: new`. Dedup was exercised in both directions on live traffic and **found a genuine evidence-losing bug** — a stronger check than the planned E2E. |
| §1 dedup (Ancestry `{collection,record}` identity) | ✅ **LIVE-VERIFIED 2026-09-28** | The REQ-009 incident was exactly the owed duplicate attempt: a second one-call capture of `1,62073::223848` returned `HTTP 409`, `dedup: "blocked"`, `duplicate_of: 20260928-ancestrycom-366c3ee9`, and wrote nothing. **Two findings.** (1) The refusal came from the **manifest-scan** leg, not the ledger leg. The extension always writes `started` before it POSTs, and `find_capture` trusts a ledger row only when it has a `capture_id`, so on the extension path the ledger check never fires. (2) The refused attempt left the record reading **STALLED** in the ledger. Filed as [BUG-017](../bugs/BUG-017_dedup-block-leaves-ledger-stalled.md). |
| §2 `capsule-db-v1` contract | ✅ **VERIFIED 2026-08-24** | Live `data/capsule.db` (266 sources) audited against `docs/specs/capsule-db-v1.md` §1: every contract column present on `source` and `ancestry_ledger_event`, none renamed or dropped. |
| §3 Capsule never writes to the Gramps API | ✅ **RE-AUDITED 2026-08-24** | Zero `gramps` references in `capsule/`, `extension/`, `tests/`; zero mutating outbound HTTP (`post`/`put`/`patch`/`delete`/`urlopen`) anywhere in `capsule/`. |
| §4 `capsule query` | ✅ **VERIFIED 2026-08-24** | Exercised on the real corpus, three paths correct: Ancestry apid → `{"captured": true, "complete": true, "reason": "ledger: mined"}`; non-Ancestry URL (Archion register 239254) → resolves to `20260822-kirchenbcheronlinemitarchion-73eb7db7`, `"verified complete"`; never-captured control → `{"captured": false, "reason": "no matching capture"}`. |

*Note on §2: the contract doc has been kept current through REQ-007 — it already documents
`image_status: none` (§3.2) and the stricter `verify_ok` (§3.3). Consumers reading v1 are not
stale.*
**Priority:** 🟠 HIGH for §1 (dedup); 🟡 MEDIUM for §2–§4

> ## Implementation notes (2026-07-17)
> - **§1** — `capsule/pipeline/dedup.py` + the backend choke point (`_capture` in
>   `backend/app.py`): identity = Ancestry `{collection,record}` (declared apid OR either
>   record-URL shape, so apid-less captures dedup too), else the URL **including its query
>   string** — a deliberate deviation from this spec's "query stripped" suggestion:
>   RootsIreland/IrishGenealogy record ids LIVE in the query, and stripping it would
>   false-block every record on those sites behind the first (a false block loses evidence;
>   a false "new" merely re-captures). Fragment-only stripping. Blocks ONLY verified-complete
>   (ledger `mined`, or manifest image terminal-good + `verify_capture` passes); partial/
>   failed priors proceed as `dedup:"repaired"` with `duplicate_of`; `remine=true` overrides.
>   Atomicity: per-identity in-flight guard (module lock) around check-and-create. The 409
>   body carries `dedup:"blocked"` + `duplicate_of` beside a plain-string `detail`.
>   Extension v0.0.16 forwards `dedup`/`duplicate_of` into `data-capsule-last-outcome`.
> - **§2/§3** — `docs/specs/capsule-db-v1.md`: contract tables (`source`,
>   `ancestry_ledger_event` — completeness fields were already persisted per event), the
>   supported check order, and the §3 no-Gramps-writes confirmation in writing.
> - **§4** — `capsule query --apid/--url` ships (reads ledger + manifest scan, no folders
>   opened by the consumer). CLI `capture` also pre-checks, with `--recapture` override.
> - Tests: `tests/test_req_004_dedup.py` (11) — suite 632 green.
**Filed:** 2026-07-17, Genealogy research session (rule-enforcement work in the research
workspace)

## Why (the incident, and why the fix can only live in Capsule)

On 2026-07-17 the research agent nearly **re-captured a NYC death record that the
collection already held** — caught by the owner, not by tooling. The Genealogy workspace now has a
binding "consult the index before capturing" rule, but four rounds of adversarial design review
(Codex) concluded the rule cannot be *enforced* from the Genealogy side: the capture entry point is
a browser `CustomEvent` dispatched from arbitrary JS, so no hook on the driving-agent side can see
every dispatch. **The check-and-create inside Capsule's backend is the only atomic choke point.**

A duplicate capture is not just wasted work: it forks the evidence trail (two capture ids for one
record; citations can end up pointing at either) and burns paced Ancestry quota.

## 1. Atomic dedup at check-and-create (the ask)

When a capture request arrives (any entry point — `capsule:capture`,
`capsule:capture-ancestry-image`, CLI), the backend:

1. **Matches on normalized record identity, not raw URL** — APID / `record_id` / ark where the
   provider has one (the ledger already keys Ancestry by apid); URL only as fallback, normalized
   (origin + path, query stripped — same normalization BUG-015 applied to the outcome `url`).
2. **Blocks only verified-complete duplicates.** An existing capture counts as "already held" only
   if `verify_ok` AND image status is terminal-good (`full`, or `index-only-no-image` for records
   with no image) AND the media hash is present. A **partial, failed, or pre-fix capture must not
   block** — recapture is then a *repair* and should proceed (BUG-007's screenshot-instead-of-PDF
   is exactly the shape that must stay repairable).
3. **Is atomic** — no gap between the index check and the capture write in which a second dispatch
   creates a sibling.
4. **Surfaces the outcome machine-readably** in `data-capsule-last-outcome` / `result.json`:
   `dedup: "new" | "blocked" | "repaired"` and, when blocked, `duplicate_of: "<capture_id>"` (+ its
   folder name), so the agent cites the existing capture instead of hunting for it.

**Acceptance:** dispatch the same apid twice → second returns `dedup: "blocked"` with
`duplicate_of` and writes nothing; corrupt/incomplete first capture (no `verify_ok`) → second
proceeds as `repaired`; two rapid dispatches of the same apid → exactly one capture exists.

## 2. Integration contract for `capsule.db` (external consumers)

The Genealogy workspace queries `capsule.db` directly (pre-capture index checks; harvest resume
logic) and its new mutation-surface inventory must currently label Capsule **UNVERIFIED external —
no stated guarantees**. `capture-folder-v1` and `captures-index-v2` spec the folder and the human
index; the **DB tables have no contract**.

**Ask:** a short `capsule-db-v1` spec (same style as capture-folder-v1) stating: which tables/
columns external consumers may rely on (`source`, `ancestry_ledger_event`, match keys), a schema
version stamp readable from the DB, and what may change without notice vs what is contract.
**Persist the per-capture completeness fields in the ledger rows** (`verify_ok`, `image_status`,
capture/extension version, media hash) if any is currently only in `result.json` — dedup (§1) and
the agent's pre-capture check should be answerable from the DB without opening capture folders.

## 3. Confirm: Capsule never writes to the Gramps API

The Gramps tree went behind **credential separation on 2026-07-17** (read-only default agent
account; writes attributed via `connection.user`). Our understanding: Capsule writes only
`capsule.db` + `captures/`, and attaching media to Gramps is done by the Genealogy workspace's own
tools. **Please confirm in writing** (one line in the contract, §2). If Capsule ever gains a
direct Gramps integration (the M2/M3 "push into a tree" direction in captures-index-v2 hints at
one), it needs its **own named Gramps account** — never the shared or reader credentials — so
transaction attribution holds.

## 4. `capsule query` — a supported pre-capture check

Today the agent's mandatory pre-capture check is hand-written SQLite. A supported
`capsule query --apid <id>` / `--url <url>` returning `{captured: bool, capture_id, folder,
verify_ok, image_status}` would make the right check the cheapest check. If §1 lands, this drops
to convenience (the backend refuses duplicates anyway) — build only if cheap; §2's contract is the
fallback.
