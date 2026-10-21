# Spec: `capture-folder/v1`

Status: **frozen for M0**. This is the versioned, on-disk spec for a single
Capsule capture. Changing the shape below is a breaking change and requires a
new `spec_version` (see §6, Capsule superset).

This document defines *vocabulary* only — folder layout, file purposes, and
field names. It intentionally does not describe the extraction pipeline,
agent orchestration, or CLI (those land with the M0 pipeline increment, not
this contract-layer increment).

## 1. Folder layout

```
captures/<capture_id>/
├── manifest.json
├── snapshot.html
├── extraction.json
├── citation.json
├── media/<n>-<slug>.<ext>
└── agent_log/<ts>-<agent>.json
```

`<capture_id>` is the **folder-name form** of the capture identifier — see
§2 for its relationship to the canonical `source.id`.

## 2. Capture ID (PM decision D6)

Two distinct identifiers exist for a capture, deliberately:

- **`source.id` (canonical entity ID).** A full **UUIDv7** (time-ordered,
  128-bit). This is the ID stored in `manifest.json`, referenced by every
  child record (`Snippet.source_id`, `Persona.source_id`,
  `Assertion.source_id`, `Citation.source_id`), and used as the primary key
  in the SQLite `source` table. It is globally unique, sortable by creation
  time, and never appears truncated.

- **`<capture_id>` (folder-name form).** The **hybrid** form used ONLY as the
  on-disk directory name:

  ```
  <YYYYMMDD>-<site-slug>-<short-uuid>
  ```

  - `YYYYMMDD` — the capture date (UTC), for human browsability
    (`ls captures/` sorts chronologically and is skimmable).
  - `site-slug` — a short, filesystem-safe slug derived from the source's
    site/domain (e.g. `findagrave`, `rootsireland`), for at-a-glance
    context.
  - `short-uuid` — the first 8 hex characters of the canonical `source.id`
    (its UUIDv7, stripped of hyphens), for collision-freedom without
    repeating the full 36-character UUID in every path component.

  Example: `20260707-findagrave-1a2b3c4d`.

**Relationship.** The folder-name form is *derived from* the canonical ID
(same UUID, truncated) plus two human-friendly prefixes; it is not a
separate identity. Given a `capture_id` folder name, the full `source.id` is
recoverable only via the `manifest.json` inside it (the truncated UUID
fragment is not guaranteed unique in isolation — always resolve identity
through the manifest, never by parsing the folder name). The database and
all cross-references always use the full UUIDv7 `source.id`; the hybrid form
exists solely for the filesystem.

## 3. File purposes

| File | Purpose |
|---|---|
| `manifest.json` | The capture's own metadata: identity, source description, archive record, and the file-integrity hash manifest. Written last, once all other files are finalized, so its `hashes.files` map is authoritative. |
| `snapshot.html` | The full single-file HTML snapshot of the page as captured (verbatim; not the source of truth for extracted text — `extraction.json` snippets are). |
| `extraction.json` | The structured extraction output: `Persona[]`, `Snippet[]`, `Assertion[]` (see `capsule/contracts/evidence.py`). One JSON document, one Claude API call's worth of output (post-validation). |
| `citation.json` | The `Citation` record — layered Evidence Explained citation (reference note, short note, source-list entry) plus access date and archived URL. |
| `media/<n>-<slug>.<ext>` | Downloaded media (images, scans) at original resolution. `<n>` is a zero-padded sequence number for stable ordering; `<slug>` is a short human-readable label. |
| `agent_log/<ts>-<agent>.json` | One file per Agent-abstraction call: prompt, response, model, timestamp. **Excluded from the hash manifest** (see §5) — it is provenance-of-process, not part of the capture's evidentiary content, and grows/could be appended to without invalidating the capture. |

## 4. `manifest.json` field list

```jsonc
{
  "spec_version": "capture-folder/v1",
  "capture_id": "20260707-findagrave-1a2b3c4d",   // folder-name form, §2
  "tool_version": "0.0.0",                         // capsule package version at capture time
  "created_at": "2026-07-07T14:32:00Z",            // ISO-8601 UTC

  "source": {
    "id": "018f...uuid7...",                       // canonical UUIDv7, §2
    "url": "https://...",
    "title": "...",
    "site_name": "...",
    "record_type": "burial | baptism | marriage | census | ... | null",
    "gating_status": "public | gated",
    "copyright_flag": false,
    "source_quality": "original | derivative",
    "content_hash": "<hex digest>"                 // see "Hash mapping" note below
  },

  "archive": {
    "local_snapshot": "snapshot.html",
    "sha256": "<hex digest>",                       // hash of local_snapshot; see note below
    "captured_at": "2026-07-07T14:31:55Z",
    "wayback_url": "https://web.archive.org/... | null"
  },

  "hashes": {
    "algo": "sha256",
    "files": {
      "snapshot.html": "<hex digest>",              // MUST equal archive.sha256
      "extraction.json": "<hex digest>",            // MUST equal source.content_hash
      "citation.json": "<hex digest>",
      "media/0-headstone.jpg": "<hex digest>"
      // ... every hashed file, path relative to the capture folder root
    }
  }
}
```

Notes:
- `source.*` and `archive.*` mirror the `Source` and `ArchiveRecord` pydantic
  models in `capsule/contracts/evidence.py` field-for-field, INCLUDING
  `captured_at`, which lives under `archive` (not `source`) in both the
  manifest and the model — this was previously stated inconsistently in an
  earlier draft of this spec and has been corrected here to match the
  models. Field names are kept identical between the on-disk manifest and
  the in-process models on purpose, to avoid a translation layer.
- **Hash mapping (`content_hash` vs. `archive.sha256` vs. `hashes.files`).**
  Three hash-shaped fields exist and each has one job:
  - `archive.sha256` is `ArchiveRecord.sha256` — specifically the digest of
    `snapshot.html`, the raw captured page. It is duplicated into
    `hashes.files["snapshot.html"]` so `verify` (§5) can treat every hashed
    file uniformly; the two values MUST be identical, and `verify` failing
    on `snapshot.html` implies `archive.sha256` is also stale.
  - `source.content_hash` is `Source.content_hash` — the digest of the
    capture's primary *derived* evidentiary artifact, `extraction.json`
    (the structured Persona/Snippet/Assertion output), as distinct from the
    raw snapshot. It is duplicated into `hashes.files["extraction.json"]`
    for the same uniformity reason, and the two values MUST match.
    **It is NOT an identity for the capture or its document (BUG-023).** Every capture
    with an empty extraction (`capsule ingest`, a document capture, a page with no
    record text) has the same `content_hash`, `8f144456e95bd74b81c2daa8160bad6f16220c6d9f74fc264f4a6f20ec4d4840`
    (41 of 501 sources on 2026-10-21). Use `archive.sha256` / `hashes.files` for the
    document's bytes, and never dedup or join on `content_hash`.
  - `hashes.files` is the complete, authoritative manifest for `verify`
    (§5) — every hashed file in the capture, not just the two above (e.g.
    `citation.json`, `media/*`). It is not merely derived from the other
    two fields; it is the source of truth for integrity checking.

  **Interpretation flag:** the pydantic models (`Source.content_hash`,
  `ArchiveRecord.sha256`) don't state what `content_hash` hashes over — this
  mapping (content_hash → extraction.json) is the smallest reasonable
  choice made to close that ambiguity for the contract layer, not something
  asserted elsewhere in the models or enforced by code in this increment.
- `hashes.files` keys are paths relative to the capture folder root, using
  forward slashes regardless of OS.
- The `created_at` / `captured_at` examples above show `Z`-suffixed
  UTC timestamps for readability. `datetime.isoformat()` (used by the
  pydantic models and the repository) emits `+00:00` instead of `Z` for
  UTC — both are valid ISO-8601 and mean the same instant. Whichever
  manifest JSON serializer is eventually built (out of scope here) should
  emit `Z` to match the examples in this spec and avoid divergence; this is
  a documentation note only, not a constraint enforced by any code in this
  increment.

## 4b. `extraction.json` → `external_refs[]` (added 2026-07-09, backward-compatible)

A derivative source often names the original it was made from: a RootsIreland
index entry links the NLI parish register it transcribes. `extraction.json`
therefore carries an optional top-level `external_refs` array:

```jsonc
"external_refs": [
  {
    "authority":  "nli-registers",   // known authority, else the host
    "identifier": "vtls000632043",   // the referenced document
    "locator":    null,              // the place *inside* it — usually absent
    "url":        "https://registers.nli.ie/registers/vtls000632043",
    "label":      "View Microfilm of Parish Register"   // verbatim link text, or null
  }
]
```

- **Optional and additive**, exactly as `media[]` (§4a). Written only when the
  page names a source document, so an extraction without refs serializes
  byte-identically and its `content_hash` is unchanged. `spec_version` stays
  `capture-folder/v1`; a reader that predates this addendum ignores the key.
- **The model finds; the code normalizes.** The extraction reply reports only the
  URL and its verbatim link text. `authority`, `identifier`, and `locator` are
  derived deterministically from the URL by `capsule/pipeline/external_ref.py`, so
  no identifier we later bind two sources on was invented by an LLM.
- **`locator` is normally `null`,** and that is the point. The RootsIreland link
  carries no `#page/NN`: it resolves the *register*, not the leaf. Recording a
  locator that the page did not state would promote an inference to a stated
  fact. Leaf-level binding is a hypothesis, resolved by a human or by HTR.
- **A reference is recorded, never followed.** Fetching it would be a background
  request to a provider the user did not drive (plan §11). This array is data.
- It is **not** a link between two local captures. Whether the referenced document
  is one we hold is an evidence-typed judgment belonging to `SourceRelation` at
  M2. See `docs/design/rootsireland-nli-linkage.md`.

## 4d. `manifest.json` → `redactions[]` (added 2026-07-10, backward-compatible)

`snapshot.html` is the page as the browser saw it, with **exactly one** permitted
alteration: the subscriber's account chrome is removed before the file is written.
A gated record page is rendered for a logged-in person and says so — RootsIreland
prints `Welcome, <name>` and `Subscription until <date>` in its header. That is not
the record. Left alone it is hashed into `archive.sha256`, transmitted to the
extraction model, and would ship inside a capsule export.

Evidence that was altered must say so, so the manifest declares every removal:

```jsonc
"redactions": [
  {"reason": "account-chrome", "location": "https://example.org/ifhf/myaccount.php"}
]
```

- **Optional and additive**, like `media[]` (§4a) and `external_refs[]` (§4b).
  Omitted entirely when nothing was removed, so a public capture's manifest is
  byte-identical to a pre-§4d one. `spec_version` stays `capture-folder/v1`.
- The removed text is **replaced in place** by the literal marker
  `[capsule:redacted account-chrome]`. `archive.sha256` attests the redacted bytes —
  the file on disk and the hash always agree.
- **`location` is the href whose link text was removed, never the text itself.** The
  manifest must not become the place the personal data ends up instead.
- **`verify` FAILS** a capture whose `snapshot.html` contains the marker but whose
  manifest declares no `redactions[]`. That is the dangerous direction: a hash
  faithfully attesting a document that silently is not the page.
- **"Verbatim transcription is sacred" governs the record, not the site's account
  chrome.** Names, dates, and places transcribed into assertions are untouched.
- **A logout link is never redacted.** `detect_gating` proves a page was paywalled by
  finding "Log Out" in its body; removing it would leave the archived snapshot unable
  to evidence its own gating, and a re-read would call a copyrighted page public.
  See `capsule/pipeline/redact.py`.

## 4c. `extraction.json` → `assertions[].normalized_field` (added 2026-07-09, backward-compatible)

An assertion's `field` is **the label the record itself prints**, folded to
snake_case by `capsule/pipeline/fieldname.py`. `normalized_field` is our canonical
genealogical term for that label, and is optional:

```jsonc
{
  "field":            "borough",      // what the page prints: "Borough: Queens"
  "normalized_field": null,           // omitted entirely when absent
  "verbatim_value":   "Queens",
  "normalized_value": null,
  "evidence_type":    "stated"
}
```

- **Optional and additive**, exactly as `external_refs[]` (§4b) and `media[]` (§4a).
  The key is omitted — not written as `null` — when there is no canonical synonym,
  so an assertion serializes byte-identically to a pre-addendum capture and its
  `content_hash` is unchanged. `spec_version` stays `capture-folder/v1`.
  (`normalized_value` and `annotation` still emit as `null`; they predate this rule
  and suppressing them would break the hashes this omission protects.)
- **`normalized_field` is for synonyms, not inferences.** `Type` → `record_type`
  names the same fact in different words. `Borough` → `death_place` does not: a New
  York death certificate states the borough it was *filed* in, and calling that the
  place of death asserts something the page never says. If the canonical term would
  claim more than the printed label, leave `normalized_field` absent and make the
  claim a separate assertion typed `calculated`, with an annotation explaining the
  derivation.
- **The model copies; the code folds.** The model reports the label verbatim and
  may propose a synonym. Only casing is normalized by code. Nothing here is
  invented by the pipeline.
- Why the asymmetry needs a rule: `normalized_value` is safe because `"46 y"` →
  `"46"` adds no meaning, whereas a canonical *field name* can. See
  `docs/design/field-naming.md`.

## 4a. `media[]` provenance array (added 2026-07-08, backward-compatible)

Evidence-grade capture must record *where each media file came from*, not just
that its bytes hash to a value. The manifest therefore carries an optional
top-level `media` array, one entry per `media/<n>-<slug>.<ext>` file, in the
same order:

```jsonc
"media": [
  {
    "path": "media/0-certificate.pdf",   // relative path; MUST be a key in hashes.files
    "source_url": "https://.../scan.pdf?sig=...",  // where the bytes were fetched, or null
    "mime": "application/pdf"             // declared content type, or null
  }
]
```

- **Optional and additive.** The array is written **only when the capture has
  media**. A media-free capture's `manifest.json` is byte-identical to one
  written before this addendum, so no existing capture changes shape and the
  `spec_version` stays `capture-folder/v1`. (No capture with media existed on
  disk when this was added, so nothing is retroactively reshaped.) A reader
  that predates this addendum simply ignores the unknown key.
- **`path`** duplicates the `hashes.files` key for that media file; the bytes'
  integrity is still attested solely by `hashes.files`. `verify` (§5) checks
  that every `media[].path` is present in `hashes.files` — a provenance record
  for bytes that aren't hashed (or aren't there) is a manifest inconsistency
  and fails verify.
- **`source_url` / `mime`** are recorded verbatim, like `source.url`; they are
  not fetched or trusted by the writer. They carry the same (non-cryptographic)
  trust level as every other descriptive manifest field outside the two §4
  MUST-equal hash pairs — the manifest is the verifier, not a self-hashed file.

## 5. The `verify` operation

`verify(capture_folder)` re-establishes trust in a capture after the fact
(e.g. before export, or on periodic integrity audit). It is a pure function
of the folder's contents plus its own `manifest.json`:

1. Read `manifest.json`; take `hashes.algo` (`sha256`) and `hashes.files`.
2. For every `path -> expected_hex` entry in `hashes.files`: the file must
   exist at `capture_folder/path`; compute its digest with `hashes.algo`;
   compare to `expected_hex`.
3. Walk the capture folder for files **not** excluded by §5.1 below. Any
   file present on disk that is not a key in `hashes.files` is an **EXTRA**
   file and is a verify failure (unlisted content must not silently exist
   inside a capture that claims integrity).
4. Result is:
   - **FAIL** if any listed file is missing, any digest mismatches, or any
     extra/unlisted file is found.
   - **PASS** otherwise.

### 5.1 Exclusions

`agent_log/` is **excluded** from both the hash manifest and the
extra-file scan. This exclusion is stated explicitly here so that `verify`
is deterministic and reproducible: agent logs are process provenance that
can legitimately be added after the fact (e.g. a later re-run's log) without
invalidating a previously verified capture. `manifest.json` itself is also
excluded from the extra-file scan (it is the verifier, not a verified file)
but its own presence and parseability are a precondition for running
`verify` at all.

No other exclusions exist. Every other file under the capture folder root
must be listed in `hashes.files` and match.

## 6. Relationship to the M3 capsule (`capsule/v1`)

An M3 **capsule** is a superset of a capture folder:

```
<capsule_id>/
├── manifest.json     (spec_version: "capsule/v1")
├── snapshot.html
├── extraction.json
├── citation.json
├── media/...
├── agent_log/...
└── attach/           (NEW at M3: guided-attach artifacts —
                        Ancestry-formatted citation text, fact summary,
                        GEDCOM 7 fragment; see docs/plan.md §5, §13 M3)
```

- `spec_version` bumps from `"capture-folder/v1"` to `"capsule/v1"`.
- All field names under `source`, `archive`, and `hashes` are **reused
  unchanged** from this spec — a capsule's `manifest.json` is a strict
  superset of a capture's, adding only capsule-specific top-level keys
  (e.g. `attach`) and, if attach-generated files are hashed, additional
  entries in `hashes.files`.
- `verify` extends the same way: `attach/` files, if hashed, follow the same
  listed-and-matching rule as any other non-excluded file; if `attach/` is
  itself excluded from hashing (open question, decided at M3 implementation
  time), that exclusion will be documented here in the same style as §5.1.
- This section records the *relationship*, not the M3 implementation — no
  `attach/` code or schema exists yet (out of scope for this increment).
