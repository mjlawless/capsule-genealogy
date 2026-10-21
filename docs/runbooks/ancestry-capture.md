# Runbook — capturing an Ancestry record (REQ-001)

For the researcher, and for a browser assistant the researcher directs in their own logged-in tab. Implements
`docs/specs/REQ-001_ancestry-record-support.md`.

## What Capsule does and does not do

**Capsule never touches Ancestry.** It does not fetch the image, does not call the media
API (which 403s by design), and does not reverse-engineer the tile API. It holds **no
Ancestry CSS selectors**.

The full-resolution scan comes from **Ancestry's own subscriber feature** — image viewer →
Tool menu → **Download**. Capsule *arms*, *intercepts the resulting file*, *verifies its
pixels*, and *ingests it*.

Who clicks Download: since **REQ-002** the one-call flow clicks it for you (matched by the
visible **"Tool menu" → "Download"** labels, with a class fallback) — the one line REQ-002
relaxes. The manual flow below, where the agent does the click, still works and is the fallback.

## One-time setup

1. `chrome://extensions` → **Reload** Capsule. It is **v0.0.9** and asks for a new
   **`downloads`** permission — approve it.
2. On Capsule's card, enable **"Allow access to file URLs"**.
   **This is load-bearing.** `chrome.downloads` can tell us a file landed but *cannot hand
   us its bytes* — the only way to read them is a `file://` fetch, and that toggle is what
   permits it. Without it, arming fails immediately and says so.
   *(If you enabled this earlier for a local PDF capture, check it survived the reload.)*
3. `capsule serve` running, token pasted into the popup once.
4. Seed the work queue with records already cited in your own tree:
   ```
   capsule ancestry seed --file apids.txt      # one _APID per line, e.g. 1,7602::56510272
   capsule ancestry status
   ```

## The per-record loop (one record at a time, in your own session)

### Preferred — one call (REQ-002)

For an **image-backed** record, fire a single event on the **record page**. Capsule does the
whole dance (arm → open viewer → click Ancestry's Tools→Download → capture) internally:

```js
const next = await fetch("http://127.0.0.1:8765/ancestry/next",
                         {headers: {"X-Capsule-Token": TOKEN}}).then(r => r.json());
if (next.done) return;
// Navigate to next.record_url (the /search/collections/<db>/records/<rec> page), then:
document.dispatchEvent(new CustomEvent("capsule:capture-ancestry-image",
  { detail: { apid: next.apid } }));       // show_viewer:true to watch the viewer for debugging
```

No per-record DOM choreography, no navigate-back. Read the toast (table below). If it says
*"could not find Ancestry's Tools menu / no Download item"*, the viewer DOM drifted — fall
back to the manual flow and report the labels you see.

### Fallback — manual click (still supported)

```js
// index-only records, or if the one-call finder can't locate Download:
document.dispatchEvent(new CustomEvent("capsule:arm-download",
  { detail: { apid: next.apid } }));
//    ...now click: viewer → Tool menu → Download...
// Back on the RECORD page:
document.dispatchEvent(new CustomEvent("capsule:capture", { detail: {
  apid: next.apid,
  image_intent: "expected",                // "none" for an index-only record
  follow_up: "the SS-5 application",       // index-only ONLY: the true source to chase
}}));
```

### Reading the verdict without the toast (v0.0.14)

The toast self-removes after 6 s, and an image capture takes 20–40 s — so an agent should
**not** rely on the toast. Two machine-readable channels carry the same verdict:

- **On the page** (for the driving agent): while a capture runs, `document.documentElement`
  has `data-capsule-capturing="true"`; when it finishes, that attribute is removed and
  `data-capsule-last-outcome` holds the full JSON result (`{ok, record_type, image_status,
  assertion_count, verify_ok, media_warnings, missed_media, url, error, at}`), persisting until
  the next capture. Poll: wait while `data-capsule-capturing` is present, then read the outcome.
  The `url` field is query-stripped (origin + path only, BUG-015) so reading the attribute back
  never trips a harness's cookie/query-string guard; the full URL stays in `manifest.json`.
- **On disk** (durable): each capture folder has `result.json`
  (`{capture_id, record_type, image_status, assertion_count, persona_count, verify_ok}`) — an
  excluded sidecar, so it never affects `capsule verify`.

The toast still shows the same thing for a human:

| toast | meaning |
|---|---|
| `verify OK, PDF ✓, IMAGE ✓ full` | mined. Move on. |
| `IMAGE ✗ failed — NOT mined` | the image is a thumbnail / unreadable. **Still on the queue.** |
| `IMAGE ✗ degraded-canvas — NOT mined` | screen-res only. **Still on the queue.** |
| `index-only (no image)` | correct for an index record. Mined, with a follow-up owed. |
| `IDENTITY MISMATCH…` | **the page is not the record you asked for. Nothing was captured.** |

A failure never aborts the run (R7.3): record it and ask `/ancestry/next` again.

## Why a bad image does not close a record

A degraded or failed image leaves the record **unmined**, so the queue hands it back. That
is the whole point of the ledger. The one outcome we cannot afford is *believing we hold a
record we do not hold* — access may lapse later, and a 96×95 thumbnail filed as
"done" is a record we would never return for.

## UAT checklist (the acceptance criteria)

Pilot record **`7602:56510272`** (1900 census):

1. **Open the archived image and read a census column.** Not the toast — the file. This is
   how both previous bugs were caught.
2. `manifest.json` carries the image id, the Source Citation, and the **NARA `Original
   data` line** (`provider_claims`).
3. **≥ 9 personas**, each with their role — Head, Wife, Son, Boarder, Servant — and the
   subject's ~15 fields.
4. `image: full`, and no follow-up (the census has its image built in).
5. **Point it at the wrong `_APID` and confirm it REFUSES** (`IDENTITY MISMATCH`, nothing
   written).
6. `capsule ancestry status` shows it mined; capturing it again is a no-op.

**Negative control** — `3693:35684453` (Social Security Death Index): must report
`index-only-no-image` with a follow-up naming the **SS-5 application**, and must not
pretend it captured a source image.

## Known limits

- **Chrome may block automatic downloads** in some profiles. If the download never lands,
  the arm window times out after 60s and says so. Allow automatic downloads for
  ancestry.com if it bites.
- **The canvas fallback (`canvas.toBlob`) is deliberately not automatic.** A screen-res
  image must never quietly stand in for the real scan.
- **The pixel thresholds are provisional** (`capsule/pipeline/image_check.py`: <500px =
  thumbnail, <2000px = screen-res). The first real download calibrates them — tell me its
  actual dimensions.
