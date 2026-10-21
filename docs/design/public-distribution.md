# Design note: what a publicly distributed Chrome extension would require

Status: **analysis only. Nothing here is scheduled.** Written 2026-07-09 in answer
to "how does the project to date map to the idea of a publicly available Chrome
extension?" Recorded so the constraints are not rediscovered later; explicitly
**not** a plan to build, and downstream of the M0–M4 dogfooding that would tell us
whether the tool is worth distributing at all.

## The short version

Architecturally we are already there. Legally and operationally we are not, and the
gap is about **who the actor is**.

What exists is the Zotero Connector shape: a thin MV3 extension that reads the page
in view and POSTs to a local companion app on `127.0.0.1`. Zotero Connector is
public, passes Chrome Web Store review, and talks to `localhost:23119`. That is a
working precedent for exactly this topology — which is a slightly funny outcome,
given Zotero was rejected as a *library* early on (an early
project decision) and its *distribution model* turns out to be the
right one.

So the question is not "can the extension be public." It is **"what does the
backend become?"**

## Three paths, not equivalent

| Path | Verdict |
|---|---|
| **Local companion app** — extension in the Web Store, user installs a signed native app or container, extension talks to localhost | The only path that preserves the compliance posture. The Zotero model. |
| **Hosted backend** — extension POSTs to a server we operate | Fatal. See below. |
| **BYO-key, no backend** — extension calls the Anthropic API directly | Kills the pipeline, evidence store, hashes, citations. That is a bookmarklet, not this project. |

### Why the hosted backend is fatal

Today, capturing a RootsIreland record means *the subscriber* makes a personal copy
of a record they paid to see. If that snapshot goes to a server we operate, **we**
become the party systematically copying a paywalled genealogical database on behalf
of many users. Ancestry's and RootsIreland's terms forbid precisely that, and "the
user clicked the button" does not change who holds the copy.

The compliance posture in `docs/plan.md` §11 is not decoration — it is what makes
this lawful, and it depends on the archive being **local**. That is the one design
assumption distribution would put under pressure, and the answer is: the backend
stays local. Not because localhost is elegant, but because moving it converts a
researcher archiving their own sources into a company copying someone's database.

## What already maps well

The compliance rules read like they were written for a Web Store review, though they
were not:

- `activeTab` + a user gesture: we read the page in view, on click. Nothing in the
  background.
- Zero background requests to providers. No crawling, no rate-limit evasion.
- No credential handling. (Caveat: an Azure SAS media URL *is* a self-authorizing
  credential, and we fetch it. We now refuse to persist or transmit it —
  `capsule/pipeline/urlsafe.py` — but the claim needs that footnote.)
- Gated content: local snapshot, copyright-flagged, never republished, no Wayback.
- The user's evidence lives in a folder they own, portable and hashed.

Most capture extensions cannot say any of that. It is a strong privacy disclosure
and a strong story to a provider's counsel.

## What breaks, ranked

1. ~~**Account PII in gated snapshots.**~~ ✅ **Solved 2026-07-10.**
   `capsule/pipeline/redact.py` removes account-chrome link text before the snapshot
   is written, hashed, or sent to the model, and the manifest declares each removal
   (spec §4d). Detection is pattern-based rather than per-site, so Ancestry and
   FindMyPast need no new code — which is what made this tractable for distribution
   at all. Residual for a public build: captures written by earlier versions still
   carry the PII, so an importer/upgrade path would need to say so rather than
   silently re-hash them.
2. **Version skew is the default state, not an edge case.** Chrome silently
   auto-updates extensions; a user's companion app does not update itself. Every
   request-model change lands on some users as a hard 422. This already happened on
   2026-07-09: the extension gained `media[].provenance` while the running backend's
   `MediaIn` still had `extra="forbid"`, and the popup rendered the failure as
   `[object Object]`. **A version handshake is a prerequisite** — extension sends its
   version, `/health` returns the minimum backend version it supports, popup tells
   the user to upgrade.
3. **The permission triple.** `optional_host_permissions: ["https://*/*",
   "http://*/*"]`, a `world: "MAIN"` script injection, and a `localhost` host
   permission. All three are justifiable here; all three will need justifying, and
   the localhost pattern in particular is a known malware shape that draws reviewer
   attention.
4. **The API key.** Extraction costs roughly $0.05 per capture on Opus 4.8. Public
   means either every user supplies their own Anthropic key — niche, and it would
   mean prompting for a secret in a popup — or we pay, which drags us back to the
   hosted backend and its legal problem.
5. **The extension has no tests.** A few hundred lines of hand-verified JavaScript
   that shipped two bugs in one day: a `gating` default that silenced the backend's
   detection, and an error handler that ate the error. Tolerable for a personal
   tool; not for the surface a thousand strangers touch.

## Pre-distribution checklist, in order

1. Strip account chrome from gated snapshots.
2. Add the version handshake between extension and backend.
3. Give the extension a test harness.
4. Narrow or justify the host permissions.
5. Decide the key question: companion app, or nothing.

## What this does not change

Nothing about M0–M3. The three contracts — evidence model, `TreeAdapter`,
capture-folder spec — are unaffected by distribution. That is the point of having
contracts. Distribution is M5+, and downstream of finding out whether the tool
earns its keep on real Irish research first.
