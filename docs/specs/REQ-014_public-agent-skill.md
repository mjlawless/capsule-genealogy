# REQ-014 — a public skill that teaches a browser agent to capture with Capsule

**Status:** 🟡 **BUILT 2026-10-28, for 0.2.7** (unreleased). · Owner request 2026-10-28: "make it
a public skill for 0.2.7".

## Why

Capsule's page events (`capsule:capture`, `capsule:capture-ancestry-image`,
`capsule:arm-download`) and its machine-readable verdict (`data-capsule-last-outcome`) exist so
an AI browser agent can capture for a researcher. Nothing public said how to use them. The only
agent manual was a private runbook, left out of the public export at the v0.1 release, and it
had gone stale: it told the human to paste a token (replaced by pairing in 0.2), told the agent
to read the toast (which disappears after 6 s, before an image capture finishes), and never
mentioned the one-call Ancestry flow.

## What ships

- **`skills/genealogy-capsule/SKILL.md`**, in the standard skill format (frontmatter `name`,
  `description`). Contents: the compliance rules first; the human's one-time setup for 0.2.7
  (both parts, pairing, "Allow access to file URLs" *before* the every-page setting, reload);
  capture; reading the verdict by polling `data-capsule-capturing` /
  `data-capsule-last-outcome` in short calls; the completeness test and a table of outcomes;
  Ancestry one-call, second-image and index-only captures; keeping a site's own download (arm,
  click by element reference, capture); one record at a time; the `capsule ancestry` queue;
  known limits (BUG-028 FamilySearch, with the `capsule ingest --provenance
  site:official-download` workaround).
- **README** "Using Capsule with an AI assistant": where the skill is and how to install it in
  Claude Code (copy the folder into `~/.claude/skills/`), plus `capsule mcp`.
- **`tests/test_req_014_agent_skill.py`**: every event, `detail` field, page attribute, element
  id, outcome field and quoted message the skill names is checked against `content.js`,
  `background.js`, `download-intercept.js`, `popup.html` and the backend, so the skill cannot
  drift from the code without a red test. Also: the skill is not in `release/exclude.txt`,
  matches no `release/forbidden.txt` pattern, and uses placeholder record ids only.

## Decisions

- **Path `skills/`, not `.claude/skills/`.** `.claude` is excluded from the public export.
- **Examples use placeholders** (`1,<collection>::<record>`). The old runbooks' example ids came
  from real research.
- **Self-pacing via `/ancestry/next` is left out.** It needs the pairing token, which the user no
  longer handles. The CLI queue covers long lists.
- **The private runbook stays private** and now points at the skill; the public
  `docs/runbooks/ancestry-capture.md` does too.

## Review

Codex adversarial review, 5 rounds, every finding fixed: a lost verdict is "unknown, ask the
archive" (`capsule query`), never "nothing ran"; the site-requests rule names what Capsule
does fetch (a record file the page embeds, Ancestry's own viewer); the degraded status is
`degraded-canvas` (the first draft said `degraded`, caught by pinning statuses to
`ImageStatus`); outcome fields are pinned to the keys `postAndShape` returns; `image_intent`
values, `pdf: "attached"`, and the CLI flags the skill gives are pinned to the code.

## Before release

- When BUG-028 is fixed, drop its "Known limits" entry and the workaround.
- REQ-009 (second image) is still awaiting its live test; the skill says so.
- CHANGELOG line at the 0.2.7 version bump.
