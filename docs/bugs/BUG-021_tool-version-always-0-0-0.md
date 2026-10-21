# BUG-021 — every capture records `tool_version: "0.0.0"`, so no capture says which code made it

**Status:** ✅ **FIXED 2026-10-21** (product version 0.1.0). Filed 2026-09-29 (found by the corpus
check). · **Severity:** 🟡 **LOW**, rising: provenance for every past capture was already lost.

> ## ✅ FIXED — 2026-10-21
>
> - **One product version**, `0.1.0`, in `pyproject.toml` and `extension/manifest.json`; a test
>   fails if they ever differ.
> - **`tool_version` = product version + the commit** from a git checkout
>   (`0.1.0+g1a2b3c4`, `.dirty` for uncommitted changes), computed once per process in
>   `capsule/version.py`. A ZIP download or a machine without `git` falls back to `0.1.0`.
> - **The extension names its build on every capture** via the `X-Capsule-Extension-Version`
>   header (all three `/capture` POSTs: the background flow and both popup paths). The backend
>   records it as the additive manifest field `extension_version` after checking it is a
>   Chrome version string; anything else is dropped. A header rather than a body field
>   because `CaptureRequest` is `extra="forbid"`: a new body field would 422 every capture
>   against an older backend.
> - **`/health` reports `version`.**
> - Spec: `capture-folder-v2.md` §5. 17 tests in `tests/test_bug_021_versioning.py`; the
>   header-validation test caught a real bug on its first run (`re.match` with `$` accepted a
>   trailing newline; now `fullmatch`).
> - **Correction to the original report below:** it said the extension version was "already on
>   the wire for the handshake". It was not. The 2026-08-23 "handshake" is the drop-and-retry
>   fallback for named unknown body fields (`DROPPABLE_WIRE_FIELDS`); no version was ever
>   exchanged until this fix.

## What happens

`manifest.json` has `tool_version`, documented in `docs/specs/capture-folder-v1.md` as the
*"capsule package version at capture time"*. It comes from `_tool_version()`
(`capsule/capture/manifest.py`), which returns `importlib.metadata.version("capsule")`. The
package version in `pyproject.toml` has never moved off `0.0.0`.

**All 446 capture folders on disk say `0.0.0`**, across two months and dozens of behavior
changes (BUG-001 to BUG-017, REQ-001 to REQ-009). The extension does version itself (now
`0.0.21`, with a handshake since 2026-08-23), but its version is not recorded in the manifest
at all.

## Why it matters

The question comes up all the time in triage: *"was this capture made before or after the
fix?"* Today it can only be answered by comparing timestamps against `git log` and against when
`capsule serve` was restarted. The 2026-09-29 inbox check had to do exactly that for
`20260928-ancestrycom-ee204dc9` (refused before REQ-009 landed?). A capture folder is meant to
explain itself without the database or the repo (R4.3).

## Proposed fix

1. **Make the recorded version mean something.** Either bump `pyproject.toml`'s version as part
   of each shipped change, or (less ceremony) derive it at build or start time from git, e.g.
   `0.0.0+g<short-sha>[.dirty]`. The git form identifies the exact code with no discipline
   required. `capsule serve` runs from the working tree under launchd, so a start-time lookup
   works.
2. **Also record the extension version** that sent the capture (it is already on the wire for
   the handshake), as an additive manifest field. Additive, so it is still `capture-folder` v2
   and not a version bump, but the spec needs a line for it.

Past captures are not rewritten (append-only). `0.0.0` in an existing folder should be read as
*"before BUG-021"*.

## Definition of fixed

A new capture's manifest identifies the backend code (a version or commit) and the extension
version that made it, and a test pins `_tool_version()` to something other than `0.0.0` in a
checkout.
