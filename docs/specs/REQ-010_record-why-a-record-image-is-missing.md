# REQ-010 — record WHY a capture holds no record image

**Status:** ✅ **DONE 2026-10-21.** Filed 2026-09-29 (found by the corpus check). Approved and
the ledger question decided on 2026-10-21 under the owner's blanket deference (reversible).

> ## ✅ DONE — 2026-10-21
>
> - **Wire:** `image_skipped` (optional, ≤500 chars) on `/capture`, sent by `postAndShape` and
>   droppable for an older backend (`DROPPABLE_WIRE_FIELDS`), like `page_label`.
> - **When it is recorded** (`_missing_image_reason`, `capsule/backend/app.py`): when the
>   image is `failed` (the record has one and we did not get it), or `none` with a reason other
>   than "nothing was armed". Off Ancestry every image-less page derives `none`, and "no
>   download was armed" is true of all of them, so recording it there would be noise on every
>   ordinary capture. The backend keys on the prefix "no download was armed", which begins both
>   extension spellings (`background.js`'s default and `takeStagedDownload`'s shorter one, which
>   an ordinary capture actually gets); a test pins the prefix to every extension string.
> - **Manifest:** a `media_warnings` entry, `NO RECORD IMAGE: <reason>.` For `failed` with
>   nothing armed, it adds the remedy: *"This record has an image: capture it with the
>   one-call Ancestry image flow to bank the scan."* (Proposal point 4.)
> - **Ledger, the owner ruling:** the existing `error` column on the `captured` event (no
>   schema change), rather than a new `note` column. `error` already means "what went wrong",
>   a missing image is the part of the capture that went wrong, and such an event is never
>   mined, so no consumer's "mined" predicate changes. Documented in `capsule-db-v1`.
> - Control characters are flattened and the text capped, since it is extension-supplied.
> - 18 tests in `tests/test_req_010_missing_image_reason.py`, including the 57e4eb68
>   acceptance scenario end to end (manifest + ledger). Existing captures are not rewritten.
> - **Codex adversarial review caught two real defects before commit, both fixed with
>   regression tests:** `image_reason` was unset on the document path (a document capture
>   naming an apid would have raised after its folder was written, leaving no ledger event),
>   and the backend matched only the long "not armed" spelling, so ordinary captures would have
>   carried a false warning. Third pass: approve.
**Priority:** 🟡 LOW–MEDIUM — observability. Nothing is lost, but every image failure is currently
unexplained after the fact.
**Relates to:** [BUG-004](../bugs/BUG-004_index-only-not-classified-no-followup.md) (the image
status is derived from the record), [BUG-005](../bugs/BUG-005_aborted-capture-leaves-no-trace.md),
[BUG-016](../bugs/BUG-016_media-warnings-never-reach-the-capture-folder.md) (the same problem for
blank renders), [BUG-018](../bugs/BUG-018_riksarkivet-armed-download-not-received.md) (whose silent
rejection is one of the reasons this would have shown).

## The gap

When a capture ends up without its record image, the extension knows why. `takeStagedDownload`
returns a `skipped` reason, e.g.:

- *"no download was armed for this capture"* (a plain page capture of an image-backed record)
- *"no download arrived within 20s of the capture — did the site's own Download actually start?"*
- *"the downloaded file is too large to archive"*, *"could not read the downloaded file: …"*

That reason goes back to the caller as `image_skipped` (`extension/background.js`
`postAndShape`) and is shown once in a toast. **It is never sent to the backend**, so it reaches
neither the capture folder nor the ledger. The ledger's `captured` event gets
`image_status: failed` with `error` NULL, and the manifest carries no warning.

**Live case, 2026-09-29:** `20260929-ancestrycom-57e4eb68` (a California Death Index record).
The record links to a scan, but it was captured through the plain page
capture, so nothing was armed. The backend correctly derived `failed` (BUG-004's rule: an image
exists and we don't have it). The researcher's note reads *"index only"*, the reverse of what
Ancestry holds. With the reason recorded, the capture would have said *"no download was armed —
this record has an image; use the one-call flow"*.

## Proposal

1. The extension sends `image_skipped` (and `missed_media`) on the `/capture` wire, as an
   optional field. The version handshake (2026-08-23) already covers new wire fields.
2. The backend writes it into the manifest's `media_warnings` whenever the derived
   `image_status` is `failed` or `none`. That's additive, like BUG-016's `media_warnings`
   change.
3. The ledger's `captured` event carries it in `error` (or a new nullable `note` column, if
   `error` should stay reserved for failures, since `captured` is not one). **Needs an owner
   ruling:** it touches `capsule-db-v1`.
4. When the reason is *"no download was armed"* on an Ancestry record that has an image, the
   warning should point to the one-call flow, because that is the action that gets the scan.

## Acceptance

Re-running `57e4eb68`'s scenario (a plain capture of an image-backed Ancestry record) produces a
manifest warning and a ledger entry that both say no download was armed, and that name the
one-call flow as the remedy.
