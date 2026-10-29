# BUG-025 — `capsule mcp`: `find_people` cannot page, `check_coverage` is unbounded

**Status:** ✅ **FIXED 2026-10-24** (unreleased; filed the same day). · **Severity:** 🟠 **MEDIUM**: part of the archive
is out of the assistant's reach, with nothing to tell it so except `more: true`.

**Found by:** the research session's test of every tool in release 0.2.6 against the live
archive, 2026-10-24 (read-only; nothing was written).

## What happens

1. **`find_people` truncates and has no `offset`.** `capsule/connector/archive.py`
   `find_people` clamps `limit` to `_MAX_LIMIT` (100) and returns `found[:limit]` with
   `more: true`, but the tool takes no `offset`. A surname search reporting `total: 546`
   can only ever show the first 100. Asking for `limit=500` silently returns 100; the tool
   description does not state the maximum (`search_captures` documents "max 100").
2. **`check_coverage` with no filters returns every claim.** On the live archive that was 313
   claims, about 150,000 characters: over the client's tool-result limit, so the answer was
   written to a file and never reached the model.
3. **The `place` match is a substring of the parish name** (`place="Mayo"` finds *Mayo Abbey*,
   not the parishes of County Mayo). That is the stored data, not a defect, but the tool
   description does not say so.

## Fix

- `find_people(name, limit=50, offset=0)` and `check_coverage(..., limit=50, offset=0)`, both
  answering `total`, `offset` and `more` exactly as `search_captures` does, `limit` clamped to
  100, and "max 100" in both descriptions.
- `check_coverage`'s description says `place` matches part of the place name as the coverage
  page wrote it (usually a parish), not a county.
- `check_coverage` drops claims whose capture is not readable here (or is hidden as gated)
  BEFORE paging, so `total` and the pages agree; claims are sorted (place, record type,
  years, capture) because `coverage_for` has no `ORDER BY` and offset paging needs a stable
  order. Snippets are now read only for the claims on the page.
- Additive only: `capsule-mcp/v1` stands (new optional arguments, new answer keys).

## Verification

- `tests/test_bug_025_connector_paging.py` (6 tests): paging 130 people at 50 a page returns
  each once; `limit=500` gives 100 with `more`; an unfiltered coverage call is one page of 50;
  paging every claim returns each once, in order; with gated captures hidden, `total` counts
  only the shown claims and no page comes up short; both tools list `offset` and "max 100".
  The REQ-012 suite (incl. the byte-for-byte read-only test) still passes.
- **Live archive, 2026-10-24** (read-only): a `find_people` surname search paged at 100 returns all
  546, identical as a multiset to a direct scan of the records. (25 of them repeat the same
  name, capture and role: duplicate persona rows in 9 captures, an extraction issue parked in
  the backlog, not paging.) Unfiltered `check_coverage`: `total` 313, a page of 50, about
  24,000 characters (was all 313, about 150,000).
