# BUG-015 — `data-capsule-last-outcome` mirrors the query-stringed record URL, blocking the driving agent's read-back

**Status:** FIXED (v0.0.15) — **E2E VERIFIED 2026-07-17** · **Severity:** 🟢 LOW — workaround existed (hand-picking fields), but it defeated the point of the machine-readable outcome

> ## ✅ E2E VERIFIED 2026-07-17 (Genealogy session)
> Confirmed live ×3 on FamilySearch captures: every `data-capsule-last-outcome` read came
> back in one pass with `url` as origin+path only (query stripped) — no harness block.

**Found:** 2026-07-17, Genealogy research session

## Symptom

The v0.0.14 machine-readable outcome works — but when the driving agent reads
`data-capsule-last-outcome` back through its browser-automation tool, the harness **blocks the
read** (`[BLOCKED: Cookie/query string data]`). The mirrored JSON contains the full record `url`
including its query string (FamilySearch `?personArk=…&groupId=…`, Ancestry search params), which
the harness's exfiltration guard correctly treats as credential-shaped data. The agent had to fall
back to re-reading individual fields one by one — exactly the awkwardness the attribute was built
to remove.

## Root cause

`postAndShape` (background.js) echoes the backend `CaptureResponse.url` — the verbatim
`outcome.source.url` — into the shaped result, and `setOutcome` (content.js) mirrored that result
whole onto the page's `<html>` attribute. Correct for *evidence* (the full URL, query and all, is
the provenance and belongs in `manifest.json` and the citation), wrong for a *status channel* read
back by an automation harness: query strings on genealogy record URLs routinely carry
identity/session-shaped parameters.

A secondary reason to sanitize: the attribute sits on the captured page's own DOM, so the **site's
own JavaScript can read it**. The mirror should carry nothing worth reading. (Audited the full
shaped result while fixing: `url` was the only field carrying page-external data — no local paths,
no token, and `missed_media` entries are filename + error text only.)

## Fix (extension-only, v0.0.15)

`setOutcome` now strips query + fragment from `url` before mirroring — `new URL(url)` →
`origin + pathname`, with a split-on-`[?#]` fallback for unparseable values; error outcomes without
a `url` simply omit the key. Origin + path still identifies *which* record the verdict belongs to
(the feedback's stated need: `at` + a clean id). Nothing else changes: the toast, the popup, the
backend response, `result.json`, `manifest.json`, and the citation are untouched, and the runbook's
documented outcome contract (`docs/runbooks/ancestry-capture.md`) never promised a query-stringed
`url`.

## Acceptance / E2E verification (next live session)

1. Capture a FamilySearch record page (URL with `?personArk=…`) via `capsule:capture`.
2. Read `data-capsule-last-outcome` through the browser-automation tool **in one read** — no
   harness block.
3. `url` in the attribute is origin + path only; `manifest.json` in the capture folder still
   records the **full** query-stringed URL (evidence unchanged).
4. Regression guard: an Ancestry one-call capture (`capsule:capture-ancestry-image`) still yields
   the outcome fields the success gate uses (`ok`, `image_status`, `verify_ok`).
