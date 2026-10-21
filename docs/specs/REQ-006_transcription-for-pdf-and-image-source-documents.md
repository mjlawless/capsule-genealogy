# REQ-006 — Transcription for PDF / image source documents

**Status:** 🟡 IN PROGRESS — approved 2026-09-24; slices T1–T8 in §10. T1–T8 ✅ (§8 gates 1–6 met on the two gold certificates; T8 backfill run 2026-09-28: 31 of 34 certificates read). Open: three transcriber defects the backfill surfaced (§10, "T8 backfill"), and the 3 disputed gold values.
**Priority:** 🔴 HIGH (correctness + the primary unlock for the consuming workspace)
**Filed:** 2026-07-19, Genealogy workspace, after reading 9 original NYC certificates by hand
**Relates to:** `docs/plan.md:186` **F4 — HTR / vision extraction agent** (already pinned);
`docs/plan.md:168` UC-2; `CLAUDE.md:21` M4+. **This is not a new idea — it is the pain signal
the plan said to wait for.**

> **The ask in one line:** Capsule captures the scan, hashes it, decodes it to check its pixel
> dimensions — and never reads what it says. Make it read the document.

---

## 1. Why now — the trigger this project defined has fired

`docs/plan.md:345` sets the rule: *"**M4+ — pulled by pain, not plan.** Usage chooses: NYC certs
dominating → F4 vision path…"* — under the standing constraint `CLAUDE.md:23`, *"Do not build ahead
of the current milestone."*

**NYC certificates are now dominating.** On 2026-07-19 the consuming workspace found a repeatable,
subscription-free route to original NYC vital records (NYC Open Data → certificate number →
`a860-historicalvitalrecords.nyc.gov/browse-all` → Capsule capture) and banked **9 original
certificates in one day, with 5 more already located and the route reusable.**

This REQ files the evidence from actually reading those nine, so F4 can be specified against real
documents rather than from theory.

## 2. What the DOM path gets, and what it misses

This project's own fixture makes the case better than prose can.
`evals/fixtures/nyc-death-1903-mary-lawless/meta.json:5`:

> "…the page is an HTML index panel (Name/Soundex/Age/Type/Borough/Date/Certificate) wrapping an
> `<embed>` PDF of the scanned certificate. **This gold pins the 7 index-panel fields; the scan's
> own contents (cause of death, parents, informant) are only in the PDF and need M4 vision.**"

That fixture is certificate **Q-1903-0148**. The Genealogy workspace has now read it. Side by side:

| | Fields | Examples |
|---|---|---|
| **DOM extraction gets** (7) | index-panel labels | name, soundex `L420`, age `46 y`, type, borough `Queens`, date, certificate `148` |
| **The scan actually holds** (~20 more) | everything of genealogical consequence | **father `James Kilcoyne`**, **mother `Bridget. Kilcoyne`**, cause `Pulmonary Tuberculosis`, exact time of death `9:15 A.M.`, attending physician + dates attended, **burial place `Holy Cross Cemetery`** + burial date, undertaker + address, occupation `Housewife`, marital status, **years resident in the U.S. `35`**, precise age `46 y 6 m 26 d` |

The seven DOM fields are the *finding aid*. The twenty in the image are the *record*. Under the
consuming project's source hierarchy the difference is not incremental — an index entry and an
original certificate sit in different tiers of evidence.

**Concrete value from one day of hand-reading nine documents:** two couples newly named in the
tree, three parent-child links
corroborated in original records, one duplicate-event conflict settled, one date conflict resolved,
two new research leads (an unexpected birthplace; a shared cemetery plot), and one prior workspace
reading corrected. **None of that is reachable from the DOM.**

## 3. 🔴 The finding that should shape the design: THE INDEX WAS WRONG

Certificate **K-1905-22719** (Robert Euin sr). The NYC Open Data index — the same index the DOM
panel is built from — gives the date of death as **12 November 1905**.

**The document says 12 December 1905**, twice, in the same hand: attendance *"from December 8th to
December 12th"*, and *"died on the 12 day of December 1905."*

The tree already held 12 Dec. Had the workspace trusted the index over the image, it would have
written an error into its system of record **on the authority of a finding aid**.

Three design consequences, and they are the whole REQ:

1. **The transcript must come from the IMAGE, not the DOM/index.** A "transcript" assembled from
   index fields would have propagated this error with a confident-looking label. If the vision path
   ever falls back to DOM text, that fallback must be *labelled as such*, not silently substituted.
2. **Disagreement between image and index is a FEATURE — surface it.** When the transcribed value
   contradicts the DOM/provider claim for the same field, emit a **conflict**, do not pick a winner.
   `docs/design/rootsireland-nli-linkage.md:22-23` already anticipates exactly this shape.
3. **Never let a transcript raise the confidence of anything.** See §5.

## 4. Resolution is a correctness requirement, not a quality knob

Measured while reading these nine: at whole-page render (~700 px wide) the parents'-name cells are
**~40 px tall and rotated 90°**. At that scale the consuming workspace previously transcribed a
mother's surname as **"Sarah [Tann-?]"**. Re-read from a **400 dpi crop of that single cell**, it is
plainly **"Sarah. Lawless"** — the identical letterform to the father's surname on the line above.

A wrong maiden name is not a small error: it sends research down a fabricated surname for years.

**Requirement:** transcription must operate on **per-field crops at ≥300–400 dpi**, not on a
downscaled full page. This aligns with F4 as already designed (`docs/plan.md:176-177`, "crops
field-by-field"). Note the pre-existing rotation problem: on pre-1910 NYC forms the entire tabular
section is **rotated 90°**, and column order runs right-to-left. Crops need a rotation step, and
getting the direction wrong yields upside-down text (observed).

## 5. 🔴 Non-negotiable constraints (evidential, not technical)

The consuming workspace operates to a documented genealogical standard. A transcript that violates
these is worse than no transcript, because it *reads as evidence*.

1. **A machine transcript is a DERIVATIVE. It is never the cited evidence.** The **image** remains
   the citation; the transcript is an aid to reading it. It must be stored and surfaced so it can
   never be mistaken for the record.
2. **A transcript must NEVER raise a confidence grade.** Confidence follows source class and the
   record's own content — never "we transcribed it, so we're surer."
3. **Two layers, per `docs/plan.md:177`:** a **verbatim** layer exactly as written, and any
   normalization as a separate **annotation** (`"phthisis [pulmonary tuberculosis]"`). Never
   silently modernize, expand, or correct. Do not fix spelling; `McGetrick` and `McGettrick` appear
   on two certificates for the same woman and **both must survive**.
4. **Illegible is a value.** Mark `[?]` and move on. **Never guess a name, date, or place.** An
   empty field is a finding; a plausible invention is a fabrication.
5. **Per-field confidence + crop-beside-value review** (already in the F4 design). This is the
   feature that makes the rest safe, and it should not be value-engineered out. A human verifies
   the shaky fields; the crop must be shown beside the value.
6. **"Unknown" is data.** Two of the nine certificates state the informant did **not know** the
   deceased's parents (`Mother's Maiden Name: Unknown`). That is a real, recordable finding about
   family memory — it must not be normalized to null/empty and thereby lost.
7. **Sensitive content must not be special-cased away.** One certificate records a sensitive
   cause of death. Transcribe it accurately and neutrally; the consuming workspace handles disclosure.

## 6. Where it would live (schema)

There is **no `transcript` / `full_text` / `ocr_text` column on any table** today
(`capsule/store/schema.sql`). Notes for whoever specs this:

* `snippet` already carries the **unused half of its own design**: `anchor_kind IN ('text','image')`
  plus `anchor_media_filename` and `anchor_bbox` (`schema.sql:89-93`). All three construction sites
  hardcode `kind="text"` (`build.py:203`, `251`, `286`). **Per-field HTR values fit the existing
  snippet shape** — an image-anchored snippet with a bbox is exactly what a field crop produces.
* A **whole-document** transcript has no home and would need a new field/table.
* Evidence tables are append-only via `BEFORE UPDATE`/`BEFORE DELETE` ABORT triggers
  (`schema.sql:38-48` et al.), and adding a column to an existing `capsule.db` requires an entry in
  `_ADDED_COLUMNS` in `repository.py` — `CREATE TABLE IF NOT EXISTS` will not alter it
  (`schema.sql:62-64`).
* `_locate_snippet()` (`build.py:254`, `289`) currently validates every snippet against the page
  text as a hallucination guard. **That guard cannot work for image-derived text** — the words are
  not in the HTML. A vision path needs a *different* guard (per-field confidence + human review),
  and must not simply bypass this one silently.

Also note the transport is text-only today and forecloses images by type:
`anthropic_transport.py:49-55` sends `"content": prompt` as a bare `str`; the `Transport` protocol
(`base.py:52-59`) and `AgentCall.prompt` (`base.py:78`) are both typed `str`. Adding vision means
changing all three — a deliberate interface change, not a local patch.

## 7. A ready-made eval — gold data supplied

`evals/fixtures/nyc-death-1903-mary-lawless/` currently pins 7 index-panel fields and explicitly
excludes the scan's contents. **That same certificate has now been read by hand**, so the fixture
can be extended into a real vision eval at no research cost.

Gold values for **Q-1903-0148** (Mary Lawless, Queens 1903), transcribed from a 400 dpi render:

| Field | Verbatim value |
|---|---|
| Cause of death | `Pulmonary Tuberculosis` |
| Date of death | `19` `Jan` `1903` |
| Time of death | `about 9:15 o'clock A.M.` |
| Attended from / to | `Jan 16 1903` / `Jan 19 1903` |
| Last saw alive | `19th day of Jan 1903` |
| Full name | `Mary Lawless` |
| Age | `46 yrs 6 mos 26 days` |
| Colour | `White` |
| Marital status | `Married` |
| Occupation | `Housewife` |
| Birthplace | `Ireland` |
| How long in U.S. | `35 years` |
| How long resident in City of New York | `35 years` |
| **Father's Name** | `James Kilcoyne` |
| Father's Birthplace | `Ireland` |
| **Mother's Name** | `Bridget. Kilcoyne` |
| Mother's Birthplace | `Ireland` |
| Place of death | `Rockaway Beach` |
| Place of burial | `Holy Cross Cemetery` |
| Date of burial | `Jan 21st 1903` |
| Undertaker | `Geo. R. McVeigh` |
| Undertaker residence | `744 Myrtle Ave, Bklyn N.Y.` |
| Physician signature | `Geo. Hatfield` (hand unclear — a legitimate `[?]` candidate) |

Two deliberately hard cases are included: `Geo. Hatfield` is genuinely marginal and a correct system
should flag it rather than assert it; and `Bridget. Kilcoyne` carries the trailing period actually
written on the form, which a "helpful" normalizer would strip.

**Suggested second fixture:** K-1905-22719, whose value is that the **index disagrees with the
image** (§3). It is the natural regression test for conflict emission, and no other fixture in the
repo tests that path.

## 8. Suggested acceptance gates

1. On the extended `nyc-death-1903-mary-lawless` fixture, the vision path transcribes **father's
   and mother's names exactly**, or marks them `[?]` — **never a confident wrong name.** A wrong
   name is a hard failure; an honest `[?]` is a pass.
2. Per-field confidence is emitted and the review UI shows **the crop beside the value**.
3. Transcribed values are stored so a consumer **cannot mistake them for the original record**, and
   are excluded from anything that could raise a confidence grade.
4. Where a transcribed field contradicts the DOM/provider claim for the same field, a **conflict is
   emitted** — neither value silently wins. Regression-tested on K-1905-22719.
5. `Unknown` and other explicit informant non-answers survive as values, not nulls.
6. Verbatim and normalized layers are stored separately; no silent spelling correction.

## 9. What the consuming workspace will do with it

Attach the transcript to the citation as a **reading aid alongside the image**, mine the named
facts, and grade **from the source class and the record's content** — never from the transcript's
existence. Hundreds of tree records remain to work through, of which the NYC certificate class is the
highest-value and currently the most under-extracted.

**Priority note:** speed matters far less than honesty here. A slow transcriber that says `[?]`
when unsure is strictly more useful than a fast one that guesses — because a guessed name is
indistinguishable from a read one once it is in a tree, and it will be trusted for years.

---

## 10. Implementation status

**Approved 2026-09-24.** Owner rulings:

* **R1 — separate layer.** Transcribed values live in their own append-only tables
  (`transcription`, `transcript_field`), never in `assertion`. They cannot be mistaken for
  evidence or raise a grade (§5.1–5.2); promotion to an assertion is a later, human act through
  the correction loop.
* **R2 — no F5 templates yet.** A layout pass locates fields on any form; templates wait until
  recurring layouts and corrections show where location fails.

**Corpus facts that shaped the plan (measured 2026-09-24):** every NYC certificate PDF wraps a
**native 300 dpi scan** (Q-1903-0148: 2072×2317, 2 pages — front and back), so §4's resolution
requirement costs no re-capture — the embedded image is extracted, never re-rendered. 37 NYC
captures qualify, both gold certificates among them. K-1905-22719's `extraction.json` already holds
the index claim `date | 1905-11-12 | stated` — §3's conflict is testable from the corpus.

| Slice | Scope | Status |
|---|---|---|
| T1 | Vision transport: `Agent.run(images=…)`; `AgentCall.images` logs `ImageRef`s (file, page, native bbox, rotation, scale, sha256 of sent bytes) — never the bytes | ✅ 2026-09-24 |
| T2 | Rasterize + crop: native embedded-image extraction (pypdfium2), crop, right-angle rotation | ✅ 2026-09-24 |
| T3 | Transcriber agent: layout pass (boxes in thousandths → native pixels), batched per-field read on native crops, reader-driven re-cut loop | ✅ 2026-09-24 |
| T4 | Storage: `transcription` / `transcript_field` tables (append-only) + hash-excluded `transcript/<id>/` folder (capture-folder-v2 §4). **Deviation from §6, owner-approved:** crop refs live on `transcript_field`, not `snippet` — an image-anchored snippet is created only when a human promotes a value to evidence | ✅ 2026-09-24 |
| T5 | Conflict emission: one text-only `reconciler` call pairs index assertions with transcribed fields → `agree` / `conflict` / `unclear` in append-only `transcript_comparison` (both values kept, no winner; guard drops pairs naming ids not sent) | ✅ 2026-09-24 |
| T6 | `capsule transcribe <capture>` (picks the scan, never Capsule's own render; checks it against the seal before any call; logs → folder → store, folder removed if the store refuses) + `capsule eval-transcript` scoring stored transcripts against `evals/transcripts/*.json` (exact / honest / wrong / missing / disputed; gate = no confident miss on a hard field + expected conflicts emitted) | ✅ code 2026-09-24 |
| T7 | Crop-beside-value review page: `GET /review/<capture_id>` on `capsule serve` (read-only, like `GET /`). Caution banner; image-vs-index conflicts first, both values, no winner; then every field beside its crop, least trustworthy first; markers highlighted; all model text escaped. `capsule transcribe` prints the link | ✅ 2026-09-24 |
| T8 | Backfill the NYC captures (cost estimate first) | ✅ 2026-09-28 (owner go-ahead same day) — see "T8 backfill" below |

Model: the transcriber defaults to `claude-sonnet-5-5` at effort `high` since 2026-09-28 (owner's
call after the model trial below; it was `claude-opus-5` before). Both are configurable per run
(`--model`, `--effort`). The DOM extractor is unchanged.

### Live runs on Q-1903-0148 (2026-09-24, `claude-opus-5`, scratch only — nothing persisted)

**Run 1 — the design as first built.** No confidently wrong value: every doubtful reading
carried `[?]` and low confidence (age `[?76]` at 0.12, marital `[?Widow]` at 0.22). But two
systemic defects wrecked the sideways table: the layout pass gave every column **rotation 90**
(they need 270 — the reader reported 18 crops upside down), and its boxes were **short along the
writing** (`fathers_name` ended at y≈739‰; the ink runs to ≈880‰), so both parents came back as
given names only: `[?James]`, `[?Bridget].`

**Fix — the reader drives re-cutting.** The reader reports each crop's `orientation` and
`clipped` edges; the pipeline re-cuts (rotated, and/or grown on the clipped native edge) and
re-reads, max 3 attempts, all retries batched; the reading of a bad crop is discarded, never kept.
Padding is now generous along the line of writing, modest across it.

**Run 2.** Both parents **exact** (`James Kilcoyne`, `Bridget. Kilcoyne` — period kept): **§8
gate 1 passes.** Also exact: age `46 yrs 6 mos 26 days`, occupation, marital status, colour,
all three birthplaces, both residence durations, cause, place of death, burial place and date.
Uncertainty stayed honest: `74[?4] Myrtle Ave Bklyn N.Y.`. 26 crops re-cut once, 1 twice;
0 guard problems. **~$0.57 and ~3½ min per certificate** (7 calls).

**Two disagreements with the §7 gold — gold proposed wrong, pending owner check:**
* Undertaker: gold `Geo. R. McVeigh`; the ink reads **`Geo. P. McHugh`** (M, raised c, H-u-g-h —
  no V, no ei). Transcriber: `Geo. [P/T?]. Mc Hugh`, flagged.
* Physician: gold `Geo. Hatfield`; the ink opens with an **F** and a small raised letter, not
  "Geo.". Transcriber: `F. [?D.] Hatfield` at 0.55, flagged. The gold already calls it marginal.

**Owner ruling 2026-09-24:** both corrections confirmed → `evals/transcripts/nyc-death-1903-mary-lawless.json`
carries `Geo. P. McHugh` and `F[?]. Hatfield`.

### End-to-end runs via `capsule transcribe` (2026-09-24 — persisted to the corpus)

**Q-1903-0148** — `complete`, 41 fields. `capsule eval-transcript`: **PASS** — parents exact
again, 14 exact / 5 honest / 0 wrong / 3 disputed. The undertaker now reads `Geo. [?P]. McHugh`,
agreeing with the corrected gold.

**K-1905-22719, first run** — `partial`: on page 1 the layout model answered in **pixels of the
overview** (x up to 1345), not thousandths. The guard rejected all 25 boxes — safe, no garbage
cropped — but the father and the death date were lost, so no conflict could be emitted. **Fix:**
the layout prompt now states each overview's pixel size and asks for `"units"`; a page answered in
pixels (declared, or given away by a coordinate > 1000 that fits the overview) is rescaled from
the overview's known size, and the rescale is written into `layout_notes`.

**K-1905-22719, re-run** — `complete`, 43 fields, and **§3's finding is caught:** CONFLICT, index
`1905-11-12` vs image `December` ("the image states the death month as December"). §8 gate 4
passes on the real certificate. The surname trap held honestly: `Robert [?Ennis]` (0.45),
`James [?Ennis]` (0.50), `Jane [Cain/Caw?]` (0.35) — wrong guesses, but every one marked, and
the reconciler called `Robert Euin` vs `Robert [?Ennis]` **unclear**, not agree. Hand-read gold
for this certificate would turn these into scored results.

**Reconciler defect found and fixed (prompt):** the death date is split `12` / `December` / `5`;
the reconciler called the index date *agree* with the day and year parts. The prompt now says to
judge a split fact as a whole and give every part the same verdict. Not yet re-run live.

**Gold questions — owner ruling 2026-09-24, "go with the ink":** Date of burial §7 `Jan 21st
1903` → `Jany 21st 1903`; both residence durations §7 `35 years` → `35 Years`. Q-1903-0148 now
scores **17 exact / 5 honest / 0 wrong / 0 disputed**.

**Reviews:** T1–T4 and T5–T7 each got an independent review, and both were approved with no
critical or high findings. Fixed: pdfium bitmaps and objects are now closed explicitly; HTML class
attributes are quoted; an undeclared-units layout on a ≤1000 px overview is now noted. **Accepted
residual risk:** a hard crash (process kill, power loss) between the atomic folder rename and the
database insert in `transcribe_capture` leaves a `transcript/<id>/` folder with no database row.
`/review` would show it and `eval-transcript` wouldn't. Either write order has a crash window, and
this order never loses a paid call's log. Nothing detects the orphan yet; a `verify`-style sweep
could.

### T8 backfill (2026-09-28 — every NYC certificate not already transcribed)

**Scope.** 45 NYC captures hold 36 distinct certificates. The 2 gold certificates were already
transcribed, so the run covered the **34** others, one capture each (the newest capture of each
certificate).

**Outcome** (from the `transcription` table, not the console log):

| | count |
|---|---|
| complete | 29 |
| partial | 2 |
| failed | 3 |
| fields read | 1,188 |
| index comparisons | 236: 160 agree, 48 unclear, **28 conflict** across 13 certificates |

**Cost:** 198 calls, 767k input + 525k output tokens on `claude-opus-5`. Scaled from the $0.57
measured on the gold run, that is roughly **$15–17**, under the ~$20 estimate. About 2–3
minutes per certificate.

**Findings, checked by eye where marked:**

1. ✅ **A real image-vs-index conflict at the source: 1880-0001260.** NYC's index entry says
   *Anna Grimm, 37 years*. The scan filed under that number (registrar's stamp **1260**) is
   *Sarah Ann Worthington, 1 year 5 months*, d. 11 Feb 1880, 135 Frost St, Brooklyn. Either
   the index is wrong or the site links the wrong image. Exactly the case this REQ exists for.
2. ✅ **Transcriber defect: it reads the wrong number as the certificate number** on the 1880
   Brooklyn forms. They carry a registrar's **stamp** (the certificate number, matching the
   index) and a separate **handwritten** number at the top right. The reader took the
   handwritten one, which produced false conflicts on 1880-0001260 (read "1410") and
   1880-0001303 (read "1459"). Both checked by eye. Fix: the layout prompt or a field note
   should name the registrar's stamp as the certificate number.
3. **Transcriber defect: 3 of 34 failed on a malformed layout reply** (not valid JSON twice,
   no JSON object once): 1880-0001230, 1873-000161a, 1901-0006212. The layout pass needs one
   repair/retry before it gives up. These three are un-transcribed until then.
4. **Transcriber defect: 2 partials from an inverted box** (y2 < y1, or x past 1000):
   1880-0001303 `place_of_business`, 1930-0004368 `diagnosis_pronoun`. Normalize or re-ask
   rather than drop.
5. **Index errors the images caught (not checked by eye):** 1881-0004195 index *Naggue*
   Carroll, image *Maggie* Carroll; 1930-0004368 index *Euin*, image *Ewin* (and age 65 vs 64).
6. **Year conflicts on marriage forms pre-printed "188_" (need a human):** 1881-0002738
   (image read as 1882) and 1891-0002596 (image read as 1881). A clerk may have overwritten
   the printed decade; the transcriber may have read the printed digits. Review at
   `/review/<capture>`.

Every transcript is a reading aid, never evidence (R1). Nothing here changed an assertion.

### Model trial (2026-09-28) and the switch to Sonnet 5.5

One read per setting of the gold certificate Q-1903-0148 (22 hand-checked fields), plus the
K-1905-22719 conflict check. Scratch copies only; about $11 in all.

| Setting | Exact | Hedged | Formatting only | Misread | Not found | $/cert | s/cert |
|---|---|---|---|---|---|---|---|
| Opus 5, high (the old default) | 16 | 5 | 1 | 0 | 0 | 0.61 | 242 |
| Opus 5.5, low | 18 | 2 | 1 | 1 | 0 | 0.26 | 73 |
| Opus 5.5, medium | 16 | 3 | 1 | 1 | 1 | 0.34 | 120 |
| Opus 5.5, high | 15 | 3 | 3 | 0 | 1 | 0.42 | 154 |
| Opus 5.5, xhigh | 17 | 3 | 1 | 0 | 1 | 0.61 | 252 |
| Sonnet 5.5, low | 16 | 0 | 1 | 2 | 3 | 0.09 | 40 |
| Sonnet 5.5, medium | 15 | 0 | 3 | 2 | 2 | 0.09 | 42 |
| **Sonnet 5.5, high (the new default)** | 18 | 0 | 1 | 2 | 1 | 0.10 | 42 |

- **Owner's decision: Sonnet 5.5 at high.** It is about 6× cheaper and 6× faster than the old
  default, gets the parents' names right, catches the K-1905 conflict, and passes the §8 gate.
- **What it costs in behaviour:** Sonnet 5.5 **never hedged** (0 `[?]` readings). Where Opus
  hedges, it commits to a reading: it supplied the physician's illegible initial (a different
  letter each run), and twice it put the place of death into the undertaker's address. Every
  such misread was still flagged `needs_review`, so the review page remains the safeguard. Its
  layout pass also finds fewer fields (31–37 vs Opus's ~42). "Time of death" was the most
  often missed.
- **API facts learned:** Sonnet 5.5 and Opus 5.5 both reject `thinking: disabled` (400). Sonnet
  5.5 names `thinking.type.between_tools` as its lowest setting; it was not tested. Opus 5.5's
  default effort is `medium`.
- **Caveat:** one run per setting. Two identical Opus 5 high runs differed by 3 fields, so gaps
  of 1–2 fields are noise.
- **Not recorded yet:** `AgentCall` logs the model but not the effort. Add it if effort starts
  varying between runs that need to be compared later.
