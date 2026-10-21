# REQ-001 — Ancestry record support

**Priority: P1.** Capsule cannot currently capture an Ancestry record usefully. Ancestry is the
single largest source in the owner's tree, reachable via the `_APID` pointers that survived the
GEDCOM import, and moving that research into Gramps is blocked on this.

**Status:** ✅ **DELIVERED** (status corrected 2026-09-28). Full-resolution image capture was
confirmed 2026-07-14 (3960×3928 via `ancestry:download`), the one-call flow is REQ-002 (done),
and the bugs it spawned are fixed (BUG-003, 004, 005, 009, 010, 012, 014), except BUG-013, which
is open but low. Open follow-ons: REQ-009 (a record's second image) and BUG-017 (ledger
state after a refused duplicate).
Originally written 2026-07-14 as requirements from the research side, after a failed pilot on
1900 US Federal Census record `7602:56510272`.

**Requirements author's standing constraints** (the owner, 2026-07-14):
> 1. Every piece of media in **full resolution with full metadata**.
> 2. Each source **drained of genealogical value** — all assertions, not just the one fact cited.
> 3. If it is an **index**, mark a follow-up for the true source — unless the true source is built
>    in (the US census has both index and image).
> 4. **Assume everything will be walled off or inaccessible later. We must not have to return.**
> 5. Apply good genealogical process — prove, or disprove, and correct the tree.

---

## 1. THE BLOCKING PROBLEM — the image is on a `<canvas>`

Measured on the record page and image viewer for `7602:56510272`:

| what we looked for | what is actually there |
|---|---|
| `<img>` of the record scan | **NONE.** Only site logos and 1×1 tracking pixels. |
| Embedded PDF | **NONE.** |
| The scan itself | Rendered into a **`<canvas>`, 1370×1092** (i.e. *screen* resolution). |
| Thumbnail on the record page | `…/api/media/retrieval/v2/thumbnail/namespaces/7602/media/4114737_00288.jpg` → **600×595**; fetched directly it returns **96×95**. Useless for a census sheet. |
| Direct fetch of the media API | `…/api/media/retrieval/v2/image/namespaces/…` → **403 Forbidden**. |
| Sanctioned full-resolution route | **Image viewer → "Tool menu" → "Download"** (Ancestry's own subscriber feature). |

**Consequence: Capsule's existing media grabber finds nothing to grab.** It looks for embedded
scans/PDFs; on Ancestry there are none. And the existing `render:page-pdf` path would preserve the
canvas **at screen resolution only** — which is precisely the "we will have to come back for it
later" outcome that constraint 4 forbids. For a census page, screen-res is the difference between
reading a column and guessing at it.

### 1.1 Terms-of-service boundary — please respect it in the design
`CLAUDE.md` §7: *"Respect each site's terms; do not bulk-scrape."*
- ✅ **Use Ancestry's own Download feature.** It is the function they provide to subscribers. This is
  the route we want.
- ❌ **Do NOT reverse-engineer the private media/tile API.** It 403s by design. Rebuilding originals
  from intercepted tiles is fragile and is not a road we should take.
- Volume: ordinary research use — records already cited in the researcher's own tree, captured
  one at a time. **Please build in a pacing/rate limit** so the tool cannot be pointed at the
  whole site.

---

## 2. REQUIREMENTS

### R1 — Acquire the record image at full resolution *(P1, blocking)*
**R1.1** Capsule MUST obtain the full-resolution record image, not a screen-resolution rendering.

**R1.2** Preferred mechanism: **drive Ancestry's own "Download"** (image viewer → Tool menu →
Download) and **ingest the resulting file** into the capture folder as a media object.
- The file lands via Chrome's download pipeline; the extension will need `chrome.downloads`
  (`onDeterminingFilename` / `onChanged`) to intercept it and hand the bytes to the backend, rather
  than leaving it loose in the user's Downloads folder.
- **No extra manual step per record.** The capture runs in the user's own logged-in browser
  session, started by the user (or by an assistant the user is directing in that session), one
  record at a time. Getting the image should not need a separate manual click on top of that.

**R1.3** Acceptable fallback **only if R1.2 proves unworkable**: `canvas.toBlob()` on the viewer
canvas. **Must be labelled as degraded** in the manifest (see R4.3) — it is ~1370 px and does **not**
satisfy constraint 4. Do not let a degraded capture masquerade as a full one.

**R1.4** MUST verify what it actually got: real image magic bytes, MIME, **and pixel dimensions**,
recorded in the manifest. A 96×95 thumbnail must be a hard **FAIL**, not a silent success.
*(This is the BUG-001 lesson: "media written" is not "media usable". Check pixels.)*

### R2 — Capture the image metadata, not just the pixels
Constraint 1 says *full metadata*. From the record page and viewer, Capsule MUST record:

| field | example (this record) |
|---|---|
| collection / database id | `7602` |
| record id | `56510272` |
| **image id** | `4114737_00288` |
| `_APID` (the tree's pointer) | `1,7602::56510272` |
| Ancestry **Source Citation** (verbatim) | `Year: 1900; Census Place: Queens Ward 5, Queens, New York; Roll: 1150; Page: 14; Enumeration District: 0687` |
| Ancestry **Source Information** (verbatim) | `Ancestry.com. 1900 United States Federal Census [database on-line]. Lehi, UT…` |
| **Original data** (the true source) | `United States of America, Bureau of the Census. Twelfth Census… NARA T623, 1854 rolls.` |
| repository / provenance | `National Archives and Records Administration` |
| image sequence | `28 of 32` |
| viewer breadcrumb | `New York > Queens > Queens Ward 05 > District 0687` |

These are what make the capture stand alone when Ancestry is gone. **The `Original data` line is
what lets us cite the NARA microfilm rather than the database** — that is the difference between a
derivative citation and a layered one (`Evidence Explained`).

### R3 — Drain the record of assertions *(constraint 2)*
Today a typical Ancestry citation in the tree supports **one fact**. The page carries far more. For
`7602:56510272` the extraction MUST yield, at minimum:

*(Names below are synthetic stand-ins; the shape of the real household is kept.)*

- **Subject:** James Doyal *(indexed surname is mangled — see R3.3)*, age 12, b. New York, son,
  single, white, male; occupation "At School"; attended school 9 months; can read / write / speak
  English.
- **Place:** Queens Ward 5, Queens, NY; street and house number; sheet, dwelling and family numbers.
- **Parents named on the record:** Thomas Doyal (b. Ireland), Mary K Doyal (b. Ireland).
- **Every household member, with age and relationship, as distinct personas:**
  Thomas 50 Head · Mary K 44 Wife · Mary L 17 Daughter · **James 12 Son** · Katie 10 Daughter ·
  John Weber 39 **Boarder** · Lena Weber 40 Boarder · Lillie T Moore 13 Boarder ·
  Annie Brown 35 **Servant**.

**R3.1** Household members MUST be separate personas with roles, **not** flattened into the subject.
*(Capsule already does persona separation correctly on RootsIreland death records — deceased vs
informant. Same requirement here.)*

**R3.2** The record's own **relationship** field (`Son`, `Wife`, `Boarder`, `Servant`) MUST be
carried as an assertion. It is the evidence for the family structure.

**R3.3** **Name variants MUST be preserved verbatim.** Ancestry indexes this household as
**"Doyal"**, with `[James Doyel]`, `[James Doyle]`, `[Doyle]` as listed variants. The
verbatim indexed form is what a future searcher needs; do not silently normalise it away.

### R4 — Index vs. image *(constraint 3)*
**R4.1** Capsule MUST classify each Ancestry record as **image-backed** or **index-only**.

**R4.2** For **index-only** records (no image behind the viewer), the capture MUST emit a
**follow-up item naming the true source** — taken from the `Original data` line. Worked examples
from one person's 23 records:

| Ancestry record | true source to chase |
|---|---|
| NY, New York, Birth Index 1878–1909 | the **birth certificate** (NYC Municipal Archives) |
| NY, New York, Index to Birth Certificates | ditto |
| NY, New York, Marriage Index 1866–1937 | the **marriage certificate** |
| NY State Death Index 1957–1969 | the **death certificate** |
| U.S. Social Security Death Index | the **SS-5 application** |
| Newspapers.com Obituary Index | the **obituary page image** |
| U.S. Find A Grave Index | the **headstone photo / sexton record** |

**R4.3** The manifest MUST record image status explicitly, e.g.
`image: full | degraded-canvas | index-only-no-image | failed`. **Never leave it implied.**

### R5 — Identity guard *(safety, non-negotiable)*
**R5.1** Before capturing, Capsule MUST assert the landed page IDs match the requested record:
`/search/collections/<db>/records/<rec>` → `<db>:<rec>` **must equal** the requested `_APID` pair.
On mismatch: **abort the capture and report.** Do not capture.

*Why:* during the pilot I read a page I believed was the 1900 census and it was a School Yearbook
entry for a different person. (Cause was benign — the researcher was using the same browser window —
but a wrong page captured and cited onto a direct ancestor is exactly the class of silent corruption
this project exists to eliminate.) Cheap check; catastrophic failure mode without it.

### R6 — Idempotency / ledger *(so we never revisit — constraint 4)*
**R6.1** Capsule MUST be able to answer *"has this `_APID` been mined, and completely?"*
**R6.2** Ledger row per record: `_APID`, capture_id, date, assertion count, **image status (R4.3)**,
follow-up-required flag.
**R6.3** Re-running a mined record MUST be a no-op (or an explicit re-mine), not a duplicate.

### R7 — Working through the user's own list
**R7.1** Accept a **list of `_APID`s** (records already cited in the user's tree) and hand them out
one at a time, in the user's own session: navigate → guard (R5) → capture → verify → ledger.
**R7.2** **Pace it.** Human-rate, with a configurable delay. Never hammer Ancestry.
**R7.3** A failure on one record must not abort the run; log it and continue.

---

## 3. ACCEPTANCE CRITERIA — the pilot record

Capsule passes R1–R6 when, for `7602:56510272` (1900 census), a single
capture produces:

1. ✅ A media file whose **pixel dimensions are the full scan**, not ~1370 px, verified by
   *decoding the image*, not by trusting the download.
2. ✅ A manifest carrying **every field in R2** — especially `image id 4114737_00288`, the
   Source Citation string, and the **NARA `Original data` line**.
3. ✅ An extraction with **≥ 9 personas** (the whole household, with roles), each with its own
   assertions, and the subject's ~15 fields.
4. ✅ `image: full` in the manifest, and **no follow-up flag** (the census has its image built in).
5. ✅ Identity guard demonstrably firing: point it at the wrong `_APID` and it must **refuse**.
6. ✅ A ledger row, and a second run that is a no-op.

**And the negative control:** run it on an **index-only** record — e.g.
`3693:35684453` (U.S. Social Security Death Index) — and it must produce `image: index-only-no-image`
plus a **follow-up naming the SS-5 application**, without pretending it captured a source image.

---

## 4. NOTES FOR THE BUILDER

- **We are the first, best and only tester** (the owner, 2026-07-14). I will exercise this hard and
  report precisely. Two bugs found today (BUG-001 blank PDF, BUG-002 capture 422) were both found by
  actually *looking at the artifact*, not by trusting a green toast — please keep the **visual/pixel
  gate** habit for images too.
- The `render:page-pdf` path is **still wanted** for Ancestry — the *record page* (the transcription,
  the source citation, the household table) is worth archiving as a PDF **in addition to** the scan.
  They are two different media objects with two different jobs.
- Ancestry pages are **login-walled**, so the capture must come from the researcher's own
  authenticated browser — which is Capsule's whole reason for existing.
