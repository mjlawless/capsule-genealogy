# BUG-023 — `source.content_hash` is the same constant for every capture with no extraction

**Status:** ✅ **FIXED 2026-10-21 (option 1, documentation).** Filed 2026-10-03 as a question
(triaged 2026-10-21 from `docs/inbox/`). · **Severity:** 🟢 **LOW**, latent.

> ## ✅ FIXED — 2026-10-21
>
> Option 1, as recommended: `capture-folder-v1` (the hash-mapping note), `capture-folder-v2`
> and `capsule-db-v1` now say `content_hash` is the extraction digest, shared by every
> capture with an empty extraction, and never a document identity, and point at
> `archive.sha256`. No data changed. `tests/test_bug_023_content_hash_is_not_identity.py`
> pins both facts: the constant, and two different documents sharing it while their
> document hashes differ.

**Found by:** the Genealogy research workspace, 2026-10-03. They saw two
different Newspapers.com ingests (`4aa5ff8c…`, image sha256 `3aa61263…`; `e2c897eb…`, image sha256
`4303eb37…`) carry the same `content_hash` `8f144456e95bd74b…`, and asked whether it is computed
over something constant.

## Answer

Yes. `content_hash` is `sha256_hex(serialize_extraction(extraction))` (`capsule/pipeline/build.py`,
both the capture and the ingest builders), the digest of `extraction.json`. When the extraction is
empty, which is what `capsule ingest` and any no-text capture produces, the bytes are identical, so
the digest is `8f144456e95bd74b81c2daa8160bad6f16220c6d9f74fc264f4a6f20ec4d4840` every time.

**41 of the 501 sources in `data/capsule.db` share it** (461 distinct values): NLI scanned
registers, irishgenealogy.ie returns, Ancestry image viewers, Newspapers.com, and others. They are
unrelated documents.

## Does anything depend on it?

- **Dedup: no.** Outside the manifest writer and verifier, `content_hash` appears in `capsule/`
  only where it is computed (`pipeline/build.py`) and stored or read back
  (`store/repository.py`); no lookup, join or comparison uses it as an identity. Dedup keys on URL, apid and `page_label` (REQ-004, REQ-009).
- **Verification: yes, but only as a self-check.** `verify_capture` requires
  `source.content_hash == hashes.files["extraction.json"]` (`capsule/capture/manifest.py`), so the
  field is correct as defined: the hash of the extraction.
- **External consumers: yes, exposed.** `content_hash` is published in each manifest's `source`
  block (`capture-folder`) and in the `capsule-db-v1` `source` table. A consumer who reads it as "the hash of
  this capture" will see 41 collisions and may treat them as duplicates.

So nothing is broken. The field is mis-named for what a reader expects, and it is a trap for any
future feature or external tool that joins on it.

## Options

1. **Document only (cheapest).** State in `capture-folder-v1.md` (the hash-mapping note),
   `capture-folder-v2.md` (which already notes the empty `extraction.json` keeps the invariant) and
   `capsule-db-v1.md` that `content_hash` is the extraction digest and is **not unique across
   captures**; point consumers at `archive.sha256` / `hashes.files` for the document bytes. No
   data change.
2. **Fold the document into the hash for extraction-less sources.** Changes `content_hash` for new
   captures only, and breaks the `content_hash == hashes.files["extraction.json"]` invariant that
   `verify_capture` enforces. Needs a spec version note. Not recommended: the invariant is useful.
3. **Add a separate field** (e.g. `document_sha256` on `source`) as an additive contract change.
   The archive record already carries `sha256` for the document, so this may be unnecessary.

**Recommended:** option 1 now, and revisit 3 only if a consumer asks. Past captures are not
rewritten (append-only).

## Definition of fixed

The contract docs above state that `content_hash` is not a document identity, with the 8f144456…
constant named as the known collision, and a test pins that two ingests of different files may
share a `content_hash` while their document hashes differ.
