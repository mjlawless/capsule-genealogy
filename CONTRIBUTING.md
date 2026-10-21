# Contributing to Capsule

Thanks for helping. Capsule is a small, opinionated tool: its value is that the evidence it
keeps can be trusted years later. Contributions are welcome when they keep that true.

## Ground rules (from the design, not negotiable)

- **Evidence model:** `Source → Citation → Assertion`. Every assertion links to the words it
  came from and is typed `stated | calculated | estimated`. Storage is append-only: no
  destructive merges, no orphaned assertions.
- **Never merge person identities.** Ambiguity stays as hypotheses.
- **The user drives.** Read only the page in view, on the user's action. No background
  requests to record sites, no crawling, no credential handling.
- **Paywalled content is never republished.** Never commit captured pages, record images or
  databases (the `data/` folder is git-ignored for this reason), and never paste
  subscription-site content into issues, tests or fixtures. Test fixtures are synthetic or
  from public records.
- **Verbatim is sacred.** Normalization lives in annotations; illegible is `[?]`.
- **The on-disk formats are versioned contracts** (`docs/specs/capture-folder-v*.md`,
  `captures-index-v*.md`, `capsule-db-v1.md`). Changes are additive, documented, and never
  rewrite existing captures.
- **Every model call goes through the `Agent` abstraction**, which logs it.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check capsule tests
```

The extension's JavaScript is tested from Python by running it in `node` (install Node.js
18+). Bug fixes come with a regression test that fails without the fix. Bugs and
requirements are written up as numbered documents in `docs/bugs/` and `docs/specs/`, with the
evidence for the fix.

## Pull requests

Keep them focused, describe what changed and why, and include tests. By contributing you
agree that your contribution is licensed under the MIT license.
