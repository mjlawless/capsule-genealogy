# REQ-009 — one-call Ancestry capture of a record's SECOND image (facing page, multi-image record)

**Status:** 🟡 **BUILT 2026-09-28 (extension v0.0.21) — live E2E pending** on 62073:223848
image 153. Ledger ruling given by the owner 2026-09-28 (the proposal below, as written).
Shipped with [BUG-017](../bugs/BUG-017_dedup-block-leaves-ledger-stalled.md).
Filed 2026-09-28 from the Genealogy workspace,
at the owner's instruction: *"Capsule should be perfectly suited to Ancestry downloads, that is one
of its main purposes. If there is a reason that it is deficient, file an enhancement request."*
**Priority:** 🟠 HIGH — Ancestry image capture is Capsule's primary job, and a record that spans
two images is common (every two-page register opening; multi-page probate and pension files).
**Relates to:** [REQ-002](./REQ-002_one-call-ancestry-image-capture.md) (the one-call flow),
[REQ-004](./REQ-004_atomic-capture-dedup-and-integration-contract.md) (dedup),
[REQ-007](./REQ-007_tile-viewer-source-profile.md) §1 (`page_label` in the dedup identity — shipped
for tile viewers only, **not** for Ancestry; see the correction below),
[REQ-008](./REQ-008_on-disk-ingest.md) (today's workaround).

> ## ⚠️ Correction, 2026-09-28 (queue review) — the backend is NOT ready
>
> As filed, this spec says the backend already includes `page_label` in the dedup identity, so
> the fix is extension-only. **That holds for tile viewers, not for Ancestry.** When a request
> resolves to an Ancestry `{collection, record}`, every dedup layer ignores the label:
>
> | Layer | Code | What it does with `page_label` for an Ancestry id |
> |---|---|---|
> | identity / in-flight lock | `capsule/pipeline/dedup.py:81-83` `record_identity` | returns `ancestry:{c}:{r}` — the label is never appended (*"An Ancestry record … has no leaves"*) |
> | ledger lookup | `dedup.py:125-140` `find_capture` → `AncestryLedger.latest(apid)` | keyed on the apid alone |
> | manifest scan | `dedup.py:239-240` `_scan_captures` | matches `record_ids_from_url(m_url) == ids`; the label comparison runs only in the non-Ancestry branch |
>
> So forwarding `page_label` from the extension alone would still get image 153 refused as
> "already mined". The change is **backend + ledger + contract**:
>
> - `ancestry_ledger_event` has no image/page column, and `capsule-db-v1` defines *"the latest
>   event per `apid`"* as the record's current state. The Genealogy workspace reads that
>   contract. Giving a record a second image changes what "current state" means, so this is a
>   contract change and needs an owner ruling (it is also the open question in item 4 below).
> - **Proposed ruling:** a ledger event with `page_label` NULL is the record's **default image**
>   and alone carries the record's "mined" state, so existing consumers read exactly what they do
>   today. Extra images get their own events under `(apid, page_label)`. The ledger gains one
>   nullable `page_label` column — an additive v1 change, rebuilt the way the `none` migration
>   was (the ledger is append-only; rows are copied, never altered).
> - **Prerequisite:** [BUG-017](../bugs/BUG-017_dedup-block-leaves-ledger-stalled.md). The
>   REQ-009 incident itself left `62073:223848` reading **STALLED** in the ledger, because a
>   refused duplicate leaves the extension's `started` event as the latest row. REQ-009 sends
>   more captures of already-held records through that refusal path, so fix it first.

> **The ask in one line:** `capsule:capture-ancestry-image {apid, record_viewer_url, page_label}`
> captures a *different image of an already-captured record* as its own capture, instead of
> refusing it as "already mined".

## What happened (live, 2026-09-28)

Record: *New Jersey, U.S., Episcopal Diocese of New Jersey, Church Records, 1700-1970*, Ancestry
collection 62073, record 223848 (All Saints, Scotch Plains, burials 1890, entry 4). The register is
a two-page opening: **page 122** (image 152: day, date, name, age, residence) and **page 123**
(image 153: date of death, cause, place of burial, clergyman). The record page's viewer link opens
image 152 only.

1. `capsule:capture-ancestry-image {apid: "1,62073::223848"}` → `20260928-ancestrycom-366c3ee9`,
   IMAGE full, verify_ok, 7 assertions. Page 122. Correct.
2. Same event plus `record_viewer_url: …/62073_302022005552_0481-00154?pId=223848` (image 153)
   → `ok:false, dedup:"blocked"`, HTTP 409 *"already mined: 1,62073::223848 was captured as
   20260928-ancestrycom-366c3ee9 … Send remine true to capture it again."*

Page 123 carries the **death date, cause and burial place** — the half of the entry the tree's new
Burial event and the corrected death date rest on. It could not be banked through the one-call flow.

## Why (read from the code, 2026-09-28)

| Layer | What it does | Gap |
|---|---|---|
| `capsule/backend/app.py` `/capture` dedup | `find_capture(url, expected_apid, …, page_label=req.page_label)` — REQ-007 §1 makes the leaf part of the identity | ~~none — the backend is ready~~ **Corrected 2026-09-28:** for an Ancestry id, `dedup.py` drops the label in the identity key, the ledger lookup and the manifest scan (see the correction above) |
| `extension/content.js` `captureAncestryImage(detail)` | honours `detail.record_viewer_url` (line ~377); builds the message with `apid, recordUrl, recordHtml, viewerUrl, note, showViewer` | **drops `detail.remine` and any page label** — so even the documented override "send remine true" cannot be sent through this event |
| `extension/background.js` `captureAncestryImage(msg)` → `postAndShape` | posts `expected_apid`, `image_intent`, `gating`, media | **passes no `remine`, no `pageLabel`** |
| `armDownload({key: msg.apid})` | correlates the viewer download by apid | two images of one apid share a key — harmless serially, ambiguous if ever concurrent |

So the 409's own advice ("Send remine true") is unreachable from the one-call entry point, and even
if it were reachable it would be the wrong tool: `remine` re-captures *the same identity*, and would
leave `ancestry_ledger_event`'s latest row for the apid pointing at page 123, so an index lookup by
apid would stop finding page 122.

## The change

1. **content.js** — forward `detail.page_label` and `detail.remine` in the
   `capsule:capture-ancestry-image` message. When `detail.record_viewer_url` differs from the
   record page's own viewer link and no label is given, **derive one** from the image id
   (`62073_302022005552_0481-00154` → `image 00154`), so a second image is never label-less.
2. **background.js** — pass `pageLabel` and `remine` through to `postAndShape`; arm the download
   with `key: apid + "#" + (pageLabel || "")`.
3. **Dedup identity (backend work, not yet done)** — (apid, page_label) for Ancestry, as REQ-007
   §1 does for tile viewers. This means changing all three layers in `capsule/pipeline/dedup.py`:
   `record_identity`, the ledger lookup in `find_capture`, and the Ancestry branch of
   `_scan_captures`. A label-less capture keeps today's behaviour (the record's default image).
4. **Ledger** — the apid's ledger row must keep resolving to **all** its captures (or to the
   default-image one), not only the newest. Say in the spec which, and test it. **Awaiting owner
   ruling** — proposed answer in the correction above (NULL label = default image = the record's
   mined state), which also requires a `capsule-db-v1` update.
5. **Manifest / index** — record `page_label` and the viewer image id, so the captures index shows
   "record 223848 — image 00153 (p.122), image 00154 (p.123)".

## Implementation status (2026-09-28)

| Item | State |
|---|---|
| 1. content.js forwards `page_label` + `remine`; derives a label | ✅ `extraImageLabel()`. The label is the viewer's image id, verbatim: `image 62073_302022005552_0481-00154`, not `image 00154`. A suffix alone could collide across film rolls, and the full id also records which viewer image the capture holds (item 5). No label when the requested image IS the record page's own, or when the page has no viewer link to compare against. |
| 2. background.js passes label + remine; arm key `apid#label` | ✅ Every `started`/`failed` event of the flow carries the label, so a second image can never stall the record. |
| 3. Dedup identity (apid, page_label) | ✅ All three layers in `dedup.py`. |
| 4. Ledger | ✅ Owner ruling: a nullable `page_label` column; a NULL label is the default image and alone carries the record's state (`latest`, `all_latest`, the queue); `AncestryLedger.images()` lists the extra images. |
| 5. Manifest / index | ✅ `source.page_label` in the manifest (already threaded by REQ-007). The captures index is unchanged. |
| — Regular capture path | Changed: `livePageLabel()` is **no longer sent from an Ancestry page**. It reads the selected option of any multi-option `<select>`, and on an Ancestry record that label is now part of the dedup identity, so a stray select would turn a true duplicate into a "new". All 157 Ancestry captures on disk have no label, so nothing on disk changes meaning. |
| — `capsule query` | `--page-label`, and an `other_images` list so an apid lookup points at every image, not only the default. |

**Still owed:** one live run. Reload the extension (v0.0.21), restart `capsule serve`, then
dispatch the one-call event for `1,62073::223848` with `record_viewer_url` set to image 153.
Expect `dedup: "new"` and a manifest `page_label` naming image `…0481-00154`.

## Acceptance

- Capture of image 152 then image 153 of one apid → two captures, both `ok`, the second
  `dedup: "new"`, each manifest naming its image.
- Repeating the image-153 capture → `dedup: "blocked"`, pointing at the image-153 capture (not the
  image-152 one).
- An apid lookup (`find_capture` / the pre-capture check) returns both, or the default one plus a
  pointer to the other — never only the newest.
- `remine: true` sent in the event reaches the backend (regression test on the message shape).

## Workaround until then

REQ-008: drive Ancestry's own viewer **Tools → Download** for the extra image, then
`capsule ingest <file> --url <viewer URL of that image> --gating gated --note "…"`. It banks and
hashes the image, but it loses the record page's extraction and does not link the capture to the
apid's ledger — which is exactly why the one-call flow should do it.
