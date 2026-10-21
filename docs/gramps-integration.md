# Gramps integration — connecting Capsule captures to the Gramps tree

**Status:** Planned (decisions locked 2026-07-13). Supersedes the Ancestry/GEDCOM-export
write path in `docs/plan.md` (Ancestry stays a read-only research surface).

This document describes how the Capsule Chrome-extension capture pipeline connects to the
user's Gramps tree so that captured, cited evidence can be written into the family tree.
It spans two repositories:

- **Capsule** — `<capsule repo>` (this repo). The capture side.
- **Gramps** — `<genealogy workspace>`, a separate private workspace. Runs **Gramps Web** in docker
  (API at `http://localhost:5555/api`), with a working REST client and a migration plan whose
  Phase 3 is *"write externally captured evidence into Gramps via the API."*

## Why this fits (the two halves were built to meet)

| Capsule provides | Gramps provides |
|---|---|
| `TreeAdapter` empty Protocol seam (`capsule/contracts/tree_adapter.py`) awaiting its first concrete impl | Gramps Web REST API (people/sources/citations/notes/media/tags), full CRUD |
| Reserved `persona_tree_link` table (`capsule/store/schema.sql:301`) for persona↔tree-person mapping | the workspace's zero-dep REST client — auth+refresh, pagination, transaction logging |
| `Repository.current_assertions()` — correction-resolved live facts | the workspace's tree-architecture decision: Gramps Web named as *the* write target; Ancestry read-only |
| Append-only evidence, never-merge personas | Phase 3 pilot + Phase 7 `gramps_apply.py` ledger executor (Proven direct / Probable+note / never Possible) |

## Locked decisions

1. **Topology — in-process adapter in Capsule.** Implement `GrampsWriteAdapter` inside
   this repo as the first concrete `TreeAdapter`. It holds the Gramps base URL + credentials
   (via env, not hardcoded), calls the REST API directly via a port of the workspace's REST client,
   reads Capsule's own evidence store, and records the mapping in `persona_tree_link`.
2. **Write target — Gramps primary.** Gramps Web is Capsule's canonical tree write target.
   The Ancestry/GEDCOM-export M3 goal is shelved; Ancestry remains a read-only research
   surface (matches the workspace's accepted tree-architecture decision).

## Architecture

```
CAPSULE  (this repo)                                 GRAMPS  (Genealogy)
──────────────────────────────                       ────────────────────────
Chrome ext ─POST /capture→ backend                   Gramps Web  (docker :5555)
      │                                               REST /api/{people,sources,
   capture folder  (evidence of record, sha256)          citations,notes,media,tags}
      │                                                        ▲
   Source→Citation→Assertion→Persona                  GrampsClient (port of gramps_api.py)
   (SQLite, append-only)                                       ▲
      │                                                        │
   current_assertions() ──┐                                    │
                          │                                    │
             ┌────────────▼──────── HUMAN GPS GATE ────────────┴─────────┐
             │  GrampsWriteAdapter(TreeAdapter)  — the join              │
             │  • maps evidence → Gramps objects                         │
             │  • writes only approved (Proven direct / Probable+note)   │
             │  • records persona_tree_link (never merges personas)      │
             │  • logs every Gramps transaction id for undo              │
             └───────────────────────────────────────────────────────────┘
```

## Object mapping (Capsule → Gramps)

| Capsule | Gramps | Notes |
|---|---|---|
| `Source` (url, title, site_name, record_type, archive.sha256, archived_url) | `Source` (title, author/pubinfo, `attribute_list` = EE SrcAttributes) | provider → a Gramps `Repository`; sha256 + capture_id + archived_url recorded as SrcAttributes/provenance note |
| `Citation` (source_list_entry, access_date, archived_url) | `Citation` (`source_handle`, `page`, `date`, `confidence` 0–4) | `page` = the "where within"; **GPS label never goes in `confidence`** — see guardrails |
| `Snippet.verbatim_text` | `Note` (type `Transcript`) on the citation | verbatim transcription is sacred; `[?]` for illegible preserved |
| `Persona` + its `current_assertions()` | `Person` (create) **or** attach `citation_list` to an existing handle | human confirms the match; **never auto-merge**; birth/death/etc assertions → `Event`s with citation |
| — (link record) | `persona_tree_link` row (`adapter="gramps"`, `external_person_id`=handle) | the immutable mapping; personas stay source-scoped |
| GPS conclusion label | Gramps `Tag` `GPS-*` + proof-pointer `Note` | note points to `citations/<file>` proof argument |
| capture folder snapshot/document + sha256 | `Media` (path, checksum) in `gramps-web/media/` | checksum verified; optional, later rung |

## Guardrails (union of both projects' hard rules — never violate)

- **Human GPS gate.** Only `Proven` (write directly) and `Probable` (write with a
  qualifying note) reach the tree. `Possible` / `Unverified` are **blocked**.
- **Never merge personas.** A persona *links* to a Gramps person; it is never fused.
  Ambiguity parks as an unlinked persona (Capsule rule) — no destructive merges.
- **Append-only on the Capsule side.** The adapter only *reads* evidence; corrections
  still flow through `apply_correction`. Writing to Gramps never mutates Capsule rows.
- **Transaction logging.** Every write batch logs Gramps transaction ids to
  a transaction log so `POST /api/transactions/history/{id}/undo` is always
  available (Gramps project rule).
- **Gated/copyright sources.** Set Gramps `private=True` and a copyright note; the local
  private Gramps instance may hold the transcription, but nothing is republished and no
  Wayback submission is made for gated material.
- **Evidence gate reconciliation.** Capsule's capture folder (with sha256) *is* the
  evidence of record. The adapter writes a provenance note referencing the Capsule
  `capture_id` + sha256; syncing media into the Gramps `evidence/` workspace + manifest is
  a later rung (open sub-decision, see below).

## Phased path (each rung dogfoodable before the next)

- **G0 — Read-only spike.** From Capsule, authenticate to Gramps Web and read `counts()`.
  Proves connectivity + auth across the seam. No writes. Reuses `gramps_api.py` logic.
- **G1 — Source + Citation + transcription Note.** `capsule attach <capture_id>` maps one
  capture into a Gramps Source + Citation + Transcript note. Transaction-logged. This *is*
  the Gramps Phase 3 pilot, driven from Capsule.
- **G2 — Persona → Person (gated).** Create a Person or attach a citation to an existing
  handle after human confirmation; write `persona_tree_link`; enforce the GPS gate.
- **G3 — Read-back for the Matcher.** `TreeAdapter` read methods search Gramps people for
  candidate matches → feeds the M2 verdict card. Pulls the Mayflower lineage goal forward.
- **G4 — Extension "Attach to tree" button** drives the gated flow end-to-end.

## Config the adapter needs

- `GRAMPS_BASE_URL` (e.g. `http://localhost:5555`), `GRAMPS_USER`, `GRAMPS_PASS`
  (or a pre-minted token) — read from env / a Capsule `.env`, never hardcoded.
- The Gramps docker stack must be running; a dedicated edit-capable account.

## Open sub-decisions (defer to build time)

- **Media sync depth (G-later):** provenance note only, vs. also pushing the snapshot/PDF
  into `gramps-web/media/` with checksum against `evidence/MANIFEST.csv`.
- **In-process vs. HTTP driver for the extension button (G4):** call the adapter through
  a new Capsule backend endpoint, or a CLI the side panel shells to.

## Milestone-ladder reconciliation (`CLAUDE.md`)

This work is Capsule **M2 (read) + M3 (write) pulled forward**, redirected from
Ancestry/GEDCOM to Gramps. `CLAUDE.md` §"Current state" ("no code exists yet / M0") is
stale and its M2/M3 descriptions name GEDCOM/Ancestry — both need updating to name Gramps
Web as the tree target. Flagged; the edit is a follow-up, not part of G0.
