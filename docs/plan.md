# Project Plan: Genealogy Source Capture Extension
*Working name: "Capsule" — a Chrome extension that turns any web page into an archived, cited, tree-ready genealogy source.*

---

## 1. Vision

Genealogists lose enormous time (and evidentiary rigor) in the gap between *finding* a record online and *properly attaching* it to an ancestor. The page gets bookmarked, the citation never gets written, the obituary image never gets saved, and the URL dies within a few years.

This extension closes that gap in one click: capture the page in view, archive it permanently, extract the genealogical facts with an agentic AI pipeline, generate an Evidence Explained–quality citation, match the people on the page to ancestors in the user's tree, and package everything into a "capsule" that can be attached to Ancestry (or any GEDCOM-compatible tool) with minimal friction.

The guiding standard is the **Genealogical Proof Standard**: every fact traceable to a source, every source citable, every citation reproducible even after the original page disappears.

## 2. Users & Core Loop

**Primary persona — the serious hobbyist.** Maintains a tree on Ancestry, researches on FindAGrave, newspapers, FamilySearch, county archives, and obituary sites. Cares about accuracy but hates writing citations.

**Secondary persona — the professional genealogist.** Bills clients, must meet GPS standards, needs research logs and airtight provenance.

**The core loop (must take under 60 seconds):**
Browse a page → click the extension → review what the agent extracted (people, events, relationships, media) → confirm the match to a tree person → save. Archive, citation, and capsule generation happen automatically in the background.

## 3. What Gets Captured (the Genealogy Layer)

Every capture produces a **Source Record** containing:

**Archival evidence.** A local full-page snapshot (single-file HTML + full-page screenshot), a SHA-256 hash of the capture for integrity, the capture timestamp, and — where the page is public — a Wayback Machine snapshot triggered via the Save Page Now API. Paywalled or login-gated pages (e.g., newspapers.com) are archived locally only, with a copyright note.

**Citation.** Generated in layered form following *Evidence Explained* conventions: full reference note, short note, and source-list entry — always including access date and archived URL. Site-specific templates for high-traffic sources (FindAGrave, FamilySearch, newspapers.com, Chronicling America, obituary aggregators) with a generic web-page template as fallback.

**Extracted assertions.** Structured facts — names, dates (including ranges and approximations), places (with historical jurisdiction awareness), events (birth, marriage, death, burial, census, immigration, military), and relationships. Every assertion links back to the exact text snippet on the page that supports it, so the user can verify provenance at a glance. Source quality is tagged (original vs. derivative; primary vs. secondary information).

**Media.** Photos, gravestone images, document scans, and newspaper clippings on the page, saved at original resolution with per-item attribution.

## 4. Architecture (the Engineering Layer)

**Chrome extension (Manifest V3, TypeScript/React).** A content script captures the rendered DOM, media, and screenshot; a side-panel UI handles review and confirmation; a service worker coordinates. The extension is deliberately thin — capture and UI only.

**Python backend (FastAPI).** All intelligence lives here, matching your Python preference. It receives the raw capture and runs the agentic pipeline, stores records (SQLite locally to start, Postgres later), manages media files, and generates exports. Can run as a local desktop service first (no cloud costs, no privacy concerns) with a hosted option later.

**Agentic pipeline (Claude API with tool use).** A small crew of specialized agents rather than one mega-prompt:

- **Extractor** — parses the capture into structured people/events/relationships, with a per-site adapter registry (FindAGrave pages get a tuned extraction schema; unknown sites get the generic path).
- **Citation agent** — builds the layered Evidence Explained citation from page metadata and the archive record.
- **Archivist** — orchestrates Wayback submission, local snapshot, hashing, and media download.
- **Matcher** — compares extracted people against the user's imported tree using fuzzy name/date/place matching, and returns ranked candidates with confidence scores and reasoning.
- **Reviewer** — a QA pass that flags low-confidence extractions and contradictions with facts already in the tree (e.g., a death date before a marriage date), so conflicts surface instead of silently merging.

Nothing is written to the tree without human confirmation. The agents propose; the user disposes. This is the primary hallucination defense, alongside snippet-level provenance.

**Data model.** Source-centric and evidence-based: `Source → Citation → Assertion → (link) → Person/Event`, plus `Media` and a `ResearchLog` that auto-records every capture session. Persons are never created from thin air — they exist in the imported tree or are created explicitly by the user from assertions.

## 5. The Ancestry Problem (honest constraint)

Ancestry has **no public write API**. Nothing can be pushed into an Ancestry tree programmatically through supported channels, and scraping/automating their logged-in pages violates their ToS and risks account bans. The plan works with this reality rather than around it:

**Capsule format.** Each capture (or batch) exports as a zip containing: the media files, the archived page, a `citation.txt` formatted to paste directly into Ancestry's source fields, a fact summary, and a GEDCOM 7 fragment. GEDZip packaging means the same capsule imports cleanly into RootsMagic, Family Tree Maker, Gramps, or FamilySearch-compatible tools — several of which *do* sync with Ancestry through licensed channels.

**Assisted attach.** A guided checklist in the side panel walks the user through attaching the capsule on Ancestry: it opens the right person page, puts each field's text one tap from the clipboard, and tracks which items are done. Tedium reduced ~90% without automating Ancestry's site.

**Tree context via GEDCOM import.** The user exports their tree from Ancestry once (a supported feature) and imports it into the extension so the Matcher has full context. Periodic re-import keeps it fresh.

If Ancestry ever opens partner APIs, a direct integration slots in behind the capsule abstraction without redesign.

## 6. Roadmap

**Phase 0 — Spikes (1–2 weeks).** De-risk the unknowns: MV3 full-page capture fidelity, Wayback SPN API behavior, GEDCOM 7 parsing/writing in Python (evaluate existing libs vs. minimal custom), and an extraction-quality eval of the Claude pipeline against ~20 real pages (FindAGrave, obituaries, newspaper clippings, county records) with a hand-labeled answer key. This eval set becomes the permanent regression suite for the agents.

**Phase 1 — Capture & Cite MVP.** One-click capture → archive → extract → citation → save locally → export a capsule zip. No tree matching yet. This alone is already useful and shippable to early testers.

**Phase 2 — Tree Awareness.** GEDCOM import, the Matcher agent, the review/confirm UI, conflict flagging, and attaching assertions to specific persons. Research log ships here.

**Phase 3 — Ancestry Capsule & Assisted Attach.** GEDZip export, Ancestry-formatted citation output, and the guided attach flow. Batch mode for capturing a whole research session.

**Phase 4 — Depth.** More site adapters, historical place-name normalization, GPS-style proof summaries drafted by the agents, multi-page sources (e.g., a 4-page probate file), and an optional hosted backend for cross-device sync.

## 7. Risks & Mitigations

**Site ToS and copyright.** Archiving paywalled content stays local and personal-use only; nothing is republished. Wayback submission only for public pages. Per-site adapter registry includes a "do not auto-archive" flag.

**LLM extraction errors.** Snippet-level provenance, confidence scores, the Reviewer agent, mandatory human confirmation, and the Phase 0 eval suite as a regression gate.

**Living persons / privacy.** Local-first storage by default; captures involving apparently-living people flagged; nothing leaves the user's machine unless they opt into hosted sync.

**Ancestry dependence.** The capsule is tool-agnostic (GEDCOM 7 / GEDZip), so the product survives changes on Ancestry's side and serves users on other platforms from day one.

**Scope creep.** The extension never becomes a tree editor. It captures, cites, archives, and packages. The tree of record stays wherever the user keeps it.

## 8. Success Metrics

Time from "page in view" to "saved, cited capture" (target < 60s); percentage of extractions accepted without edits (target > 85% on adapter-covered sites); citation completeness against the Evidence Explained checklist; percentage of captures with a successful permanent archive; and weekly captures per active user as the engagement north star.

## 9. Immediate Next Steps

1. Assemble the Phase 0 eval set: pick 20 representative pages across 5+ site types and hand-label the expected people/events/citations.
2. Spike the MV3 capture path (single-file HTML + screenshot from a content script).
3. Spike GEDCOM 7 read/write in Python against a real Ancestry tree export.
4. Draft the Assertion/Source/Citation schema and the capsule zip spec.
5. Build the Extractor agent against the eval set and measure baseline accuracy before writing any UI.
6. Spike Gramps Web in Docker: import a real tree, exercise the REST API for person read/write and media attach (feeds Decision D1 below).

---

## 10. Decision Log — Options Under Exploration

*Nothing in this section is committed. Each decision lists candidates, status, and what evidence closes it.*

### D1 — Living tree backend (STATUS: exploring, leaning Option A)

The base plan assumed a static GEDCOM import that goes stale. A "living tree" — one the agents can read and write continuously — changes the product significantly: the Matcher always has fresh context, and confirmed facts can be written home automatically instead of re-imported.

**Option A: Self-hosted Gramps Web (pinned front-runner).** Open-source genealogy server with a full REST API (people, events, sources, citations, media — read *and* write), built on the Gramps Python library, deployed via Docker, interoperable with Gramps Desktop. Fits the Python/agentic stack perfectly: our backend agents write confirmed assertions directly into the tree of record with no manual attach step. Caveats: AGPL-3.0 license (a consideration if this becomes a commercial product), and it adds a hosted component the user must run (Docker locally, or a small VPS).

**Option B: FamilySearch API.** The only major cloud platform with a genuine read/write API (GEDCOM X–based). Now includes "user trees" — personal, private trees with API access — though that capability was in invite-only beta and the platform requires solution-provider approval before production access. Free, credible, zero hosting burden, but our roadmap would sit behind their approval and beta gates.

**Option C: Static GEDCOM import + capsule only (original baseline).** No living tree; lowest complexity; remains the fallback and the universal export path regardless of A/B.

**How this decision closes:** Phase 0 spike results — Gramps Web API ergonomics for our write patterns (assertion + citation + media in one flow), plus a FamilySearch developer-program application to measure the approval friction in parallel. Decide end of Phase 0. Whatever wins, the tree backend sits behind a `TreeAdapter` interface so the choice is swappable.

### D2 — AI knowledge layer: agentic RAG (STATUS: pinned as the working design)

The assistant component needs retrieval because source pages arrive in wildly varying layouts and quality, and because research questions span both structured facts and unstructured captures.

**Corpora indexed (three):** the living tree itself (people, events, notes — via the tree adapter); every capture's full archived text, extracted assertions, and citations; and OCR text from saved media (clippings, gravestone photos, document scans). Chunks carry structured metadata — surnames, date ranges, places, source IDs — enabling *hybrid retrieval*: filter structurally first ("Smith, Ohio, 1840–1880"), rank semantically second. Candidate stores: pgvector (if Postgres lands anyway) or Chroma (lighter, local-first).

**Agentic retrieval, not a fixed pipeline.** Retrieval is a *tool* the agents choose to call, not a preprocessing step. Per question, the assistant decides between hitting the vector store, querying the tree API directly, or both — structured questions ("when did great-grandpa immigrate?") route to the tree; recall questions ("have I seen this obituary before?") route to semantic search over captures.

**Extraction-quality flywheel.** Every human correction to an extraction is stored as a (page → corrected output) pair and indexed. When the Extractor meets a new messy layout, it retrieves the most similar past examples as few-shot guidance — so accuracy on ugly pages improves with use, instead of accumulating hand-coded site adapters forever.

**Surface in the side panel:** duplicate/conflict alerts at capture time ("you captured this person 8 months ago from a different obituary — dates conflict"), and a free-form "ask about anyone in your tree" assistant, always answering with citations back to sources.

**Open sub-questions:** embedding model choice (local vs. API), OCR engine for media, and how correction pairs are privacy-scoped if a hosted tier ever exists.

**Roadmap impact if adopted:** indexing pipeline lands in Phase 2 alongside tree awareness; the conversational assistant and flywheel land in Phase 4. If D1 resolves to Option A, "GEDCOM import" in Phase 2 becomes "Gramps Web adapter," and periodic re-import disappears.

---

## 11. Use Cases (exploration — driving feature discovery)

*Each use case is a concrete research scenario walked end-to-end. Requirements discovered here feed the Decision Log and roadmap. More UCs to be added as we explore.*

### UC-1 — RootsIreland transcript → original register image (STATUS: pinned)

**Scenario.** Researcher finds an ancestor's baptism on rootsireland.ie. The hit is a *transcription index entry* created by county heritage centres — a derivative source. The original parish register exists elsewhere as a microfilm scan (Catholic registers free at registers.nli.ie; civil records at IrishGenealogy.ie; some parishes on FamilySearch film). One find is therefore a two-step research act: cite the transcript now, pursue the original image next. The site is also login-gated (paid credits), and Irish records are dense with same-name individuals — every parish has ten John Murphys.

**Capture flow.**
1. Researcher views the transcript result page; clicks capture.
2. Extractor pulls the full record: child, baptism date, parish, townland, father, mother (maiden name), sponsors. Layout is tabular and terse — a good test of extraction on index-style pages.
3. Archivist saves a local-only snapshot (login-gated → no Wayback; copyright flag set; personal evidence use).
4. Citation agent produces the transcript citation *explicitly marked derivative* (transcribing centre, database, access date), with an empty pre-built citation slot for the original register.
5. Source-chain agent proposes where the original lives, from parish + record type + date, and creates a "pursue original" research task with candidate repositories.
6. Matcher proposes attachments for *multiple* tree persons from the one record — child, both parents — weighting parish/townland and parents' names heavily, not just name + year. Sponsors are flagged as potential kin (FAN-club leads), not auto-attached.
7. Researcher confirms; assertions, citation, and the open chain-task are written to the tree of record.
8. Later, when the original register image is found and captured, it links into the same chain: derivative + original travel together in the capsule, and the proof upgrades.

**What UC-1 teaches us (acceptance criteria).** Extraction must handle index/tabular layouts, not just prose pages; citations must carry a source-quality tag (original/derivative) that survives into the capsule; a capture must be able to spawn a research task; one capture must attach to several persons; and matching must be tunable for high-ambiguity name pools.

### Features pinned from UC-1 (under exploration)

**F1 — Source chains.** A capture is never a dead end. Derivative sources link forward to their originals via an agent that infers likely repositories and pre-builds the citation slot. Chains surface as open research tasks in the side panel and resolve when the original is captured. Aligns the product with the Genealogical Proof Standard's preference for original sources — a differentiator no mainstream tool offers.

**F2 — Cluster-aware capture.** One record proposes assertions for multiple tree persons at once (child + parents from a baptism; whole households from a census, which UC for census to follow). Non-family participants (sponsors, witnesses, neighbors) are logged as FAN-club leads attached to the source, searchable later via the RAG layer.

**F3 — Ambiguity-weighted matching.** Matcher confidence scoring must incorporate locality (parish/townland) and relationship corroboration (parents' names) with higher weight in dense-name populations. UC-1's Irish parish data becomes part of the Phase 0 eval set to benchmark this.

**Gated-site archival policy (reaffirmed).** Login/paywalled sources: local snapshot + hash only, copyright-flagged, never republished — the citation still records exactly where and when the transcript was seen.

### UC-2 — NYC vital records: handwritten original certificates (STATUS: pinned)

**Scenario.** NYC Municipal Archives' Historical Vital Records site offers free, full-color, downloadable scans of death certificates (1862–1948, all five boroughs). This is the mirror image of UC-1: the researcher is looking at the *original* — top-tier evidence — but the record is a handwritten form. Extraction can't scrape a DOM; it must read an image with often-difficult handwriting. The site's own name search doesn't handle spelling variants, so researchers typically arrive via an index (site index, German Genealogy Group, FamilySearch).

**Capture flow.**
1. Researcher locates the certificate (see F7 — the full index to digitized death certs is openly published and can live locally in our backend for fuzzy name → certificate number → scan resolution better than the site's own search).
2. Capture saves the full-resolution color image as the media object — never a viewer screenshot. The download filename encodes year and certificate number, seeding the citation automatically.
3. Form-template registry (F5) identifies the certificate era/layout, so the agent knows where each field lives: name, age, cause, parents, birthplace, burial.
4. HTR agent (F4) crops field-by-field, transcribes with a vision model, and attaches per-field confidence. Review UI shows each image crop beside its transcribed value — the human verifies only the shaky fields.
5. Transcription follows genealogy rules: verbatim layer exactly as written, normalization layer as annotation ("phthisis [pulmonary tuberculosis]"), illegible marked [?]. Corrections feed the D2 flywheel as handwriting few-shot examples.
6. Cluster capture (F2): assertions proposed for deceased, father, and mother (maiden name). Age at death in years/months/days yields a birth date tagged **calculated** (F6), never presented as stated.
7. Forward chains (F1): cemetery + burial date → FindAGrave / burial-record task; implied birth year → birth-certificate search task; residence address → census follow-up lead.
8. Public free site → Wayback-eligible; Evidence Explained citation autofilled from borough + certificate number + year + repository.

**What UC-2 teaches us (acceptance criteria).** The Extractor needs a first-class vision path, not just DOM parsing; per-field confidence with targeted human review; a verbatim-plus-annotation transcription model; stated/calculated/estimated evidence typing on dates; original-resolution media capture; and handwritten certificates must join the Phase 0 eval set.

### Features pinned from UC-2 (under exploration)

**F4 — HTR / vision extraction agent.** Field-cropped handwriting transcription with per-field confidence scores and a crop-beside-value review UI. The vision path sits alongside the DOM path as an equal citizen in the Extractor.

**F5 — Form-template registry.** Era- and jurisdiction-aware field maps for standardized record forms (vital certificates, census schedules, passenger manifests). Extends the per-site adapter idea to per-form; templates are data, not code.

**F6 — Evidence typing for facts.** Every date/fact carries its evidential character: *stated* (written on the record), *calculated* (derived, e.g., birth date from age at death), or *estimated*. This survives into citations, the tree write, and the capsule.

**F7 — Local reference indexes.** Openly published index datasets (e.g., NYC's 3.7M-row death-certificate index on NYC Open Data) are ingested into the backend as agent tools, enabling fuzzy identity resolution and direct record location that beats the source site's own search.

### UC-3 — Identity resolution: same names, same families (STATUS: pinned)

**Scenario.** Irish research is the worst case of genealogy's deepest problem. The traditional Irish naming convention (first son after the father's father, first daughter after the mother's mother, and so on) makes *cousin families mirror each other*: two Murphy brothers in one parish each produce a John, a Robert, a Mary, and a Rose in overlapping years, each John married to a Mary. Family-shape similarity — normally a confidence booster — becomes actively dangerous. The worst failure mode is **conflation**: merging two real people into one and attaching sources to the wrong person, an error that propagates through hint systems and is brutal to unpick.

**Required behavior.**
1. When Matcher candidates are near-tied, the capture is **never coin-flip attached**. It parks in an unresolved evidence pool, linked to all candidates with probabilities (F8).
2. The review UI auto-generates an **identity table**: every candidate side by side with every known fact — the professional's tool, built automatically.
3. A **discriminator-seeking agent** (F9) reasons about what fact would separate the candidates ("these two differ by townland — a civil marriage record or Griffith's Valuation entry would resolve this") and spawns a targeted research task via the F1 chain machinery. It hunts distinguishing evidence rather than waiting for it.
4. Discriminators are weighted by real diagnostic power: townland over parish, occupation, spouse's maiden name, and above all **FAN-network overlap** — mirrored families have *different* sponsors, witnesses, and neighbors (builds on the F2 FAN log). In naming-convention cultures, matching children's names is explicitly *downweighted* (refines F3).
5. **Negative evidence** counts: candidate John A's 1888 death excludes him from an 1890 baptism. A consistency engine runs timeline checks continuously (children after death, impossible maternal ages, marriage before birth).
6. New captures automatically re-score all open hypotheses; a hypothesis resolves when one candidate's probability clears a threshold *and* the human confirms.
7. Hard rule: **the system never merges persons.** And because every assertion remains linked to its source, a later *split* can cleanly re-deal the evidence to the right identities.

**What UC-3 teaches us (acceptance criteria).** Matching is hypothesis management, not one-shot scoring; unresolved evidence is a first-class state visible in the UI; the agents must generate discriminating research tasks, not just scores; and no automated action may ever be destructive to identity.

### Features pinned from UC-3 (under exploration)

**F8 — Identity hypothesis pool.** Ambiguous captures attach to N candidates with live probabilities instead of forcing a match. Hypotheses re-score as evidence accumulates and surface as open questions in the side panel.

**F9 — Discriminator-seeking agent.** Given near-tied candidates, the agent identifies which obtainable fact has the highest expected power to separate them and creates the corresponding research task. The most genuinely agentic component in the system.

**F10 — Consistency engine & no-merge safeguard.** Continuous timeline/logic validation, negative-evidence support, FAN-overlap scoring as a first-class matching feature, and a split-safe data model in which identity operations are always reversible because assertions never detach from their sources.

**Roadmap impact.** F4/F5 join Phase 1 (capture core, with handwritten certificates in the Phase 0 eval set); F6–F8 and F10 land with Phase 2 tree awareness; F9 lands Phase 2–3 once the chain machinery exists. UC-3's mirrored-family fixtures (synthetic Irish parish clusters) join the eval suite to benchmark conflation resistance — the metric is *zero false merges*, even at the cost of more unresolved hypotheses.

### UC-4 — Ancestry search & discovery support (STATUS: pinned)

**Scenario.** Ancestry is the researcher's primary data provider, but has no API. The browser-extension form factor is precisely what makes it workable anyway: the extension assists a logged-in subscriber with the pages *they* are already viewing, at human speed, user-initiated, for personal research. What it must never do is act like a crawler.

**Compliance posture (governs everything below).**
- The user drives; the extension reads only the page in view.
- No background fetching of Ancestry URLs, no auto-clicking through results, no bulk crawling, no credential handling by our backend.
- Subscriber-licensed record images archive **local-only** under the gated-site policy (same as newspapers.com); nothing republished, no Wayback submission.
- All assistance is advisory overlay + pre-filled navigation the user chooses to click. This posture is documented in-product so users understand the boundary too.

**Assist flow.**
1. **Search planning (F11).** Ancestry search is URL-parameterized, so the search-planner agent takes a target person (from the living tree plus the F8 hypothesis pool) and generates a *ranked search plan* — record collections to try, spelling variants, date windows — each step rendered as a pre-filled, click-to-open search link. The user clicks; the agent strategized. The same planner emits FamilySearch and Findmypast variants, making it provider-neutral.
2. **In-view triage (F12).** On a results or hints page the user has opened, the content script reads the visible entries and the agent scores each against the target identity — overlay badges: *supports* / *conflicts* / *already captured* / *ambiguous (see hypotheses)*.
3. **Hint conflation guard (F13).** Every Ancestry hint is run through the F8 identity hypotheses and F10 consistency engine *before* acceptance — "this hint's John Murphy died 1888; yours had a child in 1890." Hints are the main vector spreading conflated families through the ecosystem; intercepting them is likely the product's single highest-value moment.
4. **Record capture.** Ancestry record pages carry structured transcriptions (clean DOM extraction; often derivative — F6 tagging applies, F1 chains to the original image). Capture works exactly as elsewhere; the citation records the Ancestry collection, the underlying original, and access date.
5. **Attach loop closes.** Captured evidence flows back out through the capsule + assisted-attach flow (§5) to the user's Ancestry tree.

**What UC-4 teaches us (acceptance criteria).** Assistance must be strictly in-view and user-initiated (auditable: the extension makes zero Ancestry requests itself); search plans must be explainable (why this collection, why this spelling); triage must run within a couple of seconds of page view to be useful; and extraction must tolerate constant DOM drift — the LLM extractor plus D2 correction flywheel is the mitigation, not brittle CSS selectors.

### Features pinned from UC-4 (under exploration)

**F11 — Search-planner agent.** Given a target identity and current evidence gaps (fed by F9's discriminators), generates ranked, pre-filled search links across Ancestry, FamilySearch, and Findmypast. Turns "where do I look next?" into a click list.

**F12 — In-view triage overlay.** Real-time scoring of visible search results/hints against the target, rendered as non-intrusive badges with reasoning available on tap.

**F13 — Hint conflation guard.** Hypothesis-aware and consistency-checked evaluation of Ancestry hints before the user accepts them, surfacing conflicts and same-name-different-person risk. Direct application of F8/F10 at the ecosystem's most error-prone moment.

**Roadmap impact.** F11 can ship early (Phase 1–2: it needs only the tree and URL templates); F12–F13 land Phase 2–3 since they depend on the Matcher, hypothesis pool, and consistency engine. Ancestry DOM-drift fixtures join the eval suite.

### Sidekick interaction — one-tap verdict (STATUS: pinned)

**Concept.** The extension's primary interaction on any research page: one easy button. Tap it → the extension reads the page in view (UC-4 posture) → the Matcher scores the page's people against the tree *and* against the exclusion dataset (F15), with the consistency engine checking for conflicts → a verdict card with reasoning, never a bare score: *"Likely your John Murphy (87%) — townland and mother's maiden name align"* or *"Probably the false family — matches the Ballyragget Murphys on 3 points."*

**Three actions on the card:**
- **Add** — launches the full capture pipeline: extract → cite → archive → propose attachments → confirm.
- **Ignore** — logged as reviewed-not-relevant so the page is never re-triaged; quietly accumulates into the research log (negative searches are part of the Genealogical Proof Standard's "reasonably exhaustive search").
- **Not mine** — a *full capture too*, but the assertions enrich a doppelgänger profile instead of the tree. Sourced and cited like everything else, so exclusion reasoning is defensible later.

### Features pinned from the sidekick (under exploration)

**F14 — One-tap verdict sidekick.** The button + verdict card + three-action flow above. This becomes the extension's front door; the side panel is the depth view behind it. Verdict must render within a couple of seconds; reasoning always available on tap.

**F15 — Doppelgänger / exclusion dataset.** Cumulative modeling of real-but-not-mine families. Each "Not mine" builds an anti-identity — fully sourced people, events, and relationships filed against the exclusion set rather than the tree. Every future page is scored against both identity sets, so red herrings get *easier* to spot with use ("matches known red herring, 3 points"). This is exclusion research made cumulative — professionally standard reasoning that mainstream tools throw away — and it compounds:
- Doppelgänger facts become discriminators for F9 ("your John can't be this one; the false John died in 1888").
- F13's hint guard checks hints against the exclusion set before the user accepts them.
- F8 hypotheses can resolve *negatively* — a candidate migrates to the exclusion set with its evidence intact.
- The RAG layer (D2) indexes exclusion profiles alongside the tree, so "have I ruled this family out before?" is answerable.

**Roadmap impact.** F14's verdict card is the Phase 1 UI centerpiece (initially scoring against the tree only); F15 lands Phase 2 with the hypothesis pool, since exclusion profiles reuse the same person/assertion/source data model — just flagged with a different identity scope. Eval suite gains "known red herring" fixtures: the metric is how quickly the system re-recognizes a previously excluded family.

### Research navigator — always-on "what next?" guidance (STATUS: pinned)

**Concept.** The layer above the sidekick. F14 answers "what is this page?"; the navigator answers "what should I do next?" at every moment, in one of four modes chosen by a director agent:

- **Resolve** — what's in view has open work: capture it, verify low-confidence HTR fields, settle an F8 hypothesis it bears on.
- **Deepen** — what's present points further: unpursued F1 chains, uncaptured cluster members, FAN leads worth logging.
- **Continue** — the next step on the current objective, drawing on F9's discriminators and F11's search plans as the ready-made queue.
- **Terminal** — declare the line's end honestly, then re-prioritize.

**Terminals are conclusions, not surrender.** Professionals *prove* a line is exhausted; amateurs just stop. Genealogy terminals are often concretely knowable — most Irish pre-1858 wills and vast record classes burned in the 1922 Four Courts fire; a parish's registers simply start in 1835, making an 1810 baptism there unfindable; the 1890 US census is essentially gone. Terminal flavors:
- **Exhausted** — the needed records never existed or were destroyed; closed with the coverage evidence cited (this *is* the GPS "reasonably exhaustive search," documented).
- **Blocked** — records exist but are offline ("PRONI holds this estate's rent rolls") → becomes a deferred visit-the-archive task, not a dead end.
- **Proven / disproven** — the objective resolved on the evidence.

On any terminal, a prioritizer proposes the next objective, weighing generation gaps, pending-evidence density, and record-richness of each open line.

### Features pinned from the navigator (under exploration)

**F16 — Research navigator agent.** The four-mode director above; per page-view and per objective, always able to say what the highest-value next action is and why.

**F17 — Record-coverage knowledge base.** Jurisdiction × record type × date range: what exists, what was destroyed, what survives where (and at which repository). Powers honest "exhausted" terminals, feeds F11's planner (don't search for what can't exist), and F19's economics. Grows as data, not code — seeded from published record-loss and coverage references, extended by the community over time.

**F18 — Research objectives & priority queue.** GPS-style research questions become first-class entities with state (open / blocked / exhausted / proven / disproven), owning their evidence, chains, hypotheses, and pending tasks. The unit the navigator steers, the prioritizer ranks, and the access ledger values against.

### Paywall economics — queue smart, use access well, exhaust visibly (STATUS: pinned)

**Concept.** Research options constantly dead-end at paywalls. Instead of stalling, the system treats access as a resource-allocation problem — helping the researcher decide, with actual data, when a short subscription is worth buying.

**The flow.**
1. **Ledger accrues.** Any step that hits a paywall — a discriminator needing a Findmypast register, a chain pointing at a newspapers.com obituary — logs a pending-access item against that provider: which objective it serves and its expected evidentiary value (a hypothesis-resolving discriminator scores far above a nice-to-have).
2. **Buy signal fires.** When a provider's accumulated value crosses threshold, the agent recommends a *short access window* with the case made explicit: "23 pending items at Findmypast across 5 objectives — one month now likely resolves 2 identity hypotheses." Credit-based providers (RootsIreland) get batch economics — "14 lookups queued; the 25-credit tier fits." Free routes are always checked first: Ancestry Library Edition at public libraries; Findmypast/Fold3/MyHeritage free at FamilySearch affiliate libraries.
3. **Focus mode.** On unlock, the sidekick flips into a focused working session: the ledger becomes a prioritized checklist, F11 pre-fills every search, capture is streamlined, and progress + time remaining stay visible ("11 of 23 done, 26 days left"). Still human-speed and user-driven per the UC-4 posture — the agent makes every click count rather than clicking for you.
4. **Tapped out.** When all ledger items resolve and F17 confirms the provider's relevant collections are covered, the provider is marked **exhausted for current objectives, as of [date]** — visible, so you never re-subscribe on vague hope. Dated because it re-arms honestly: new objectives or newly added collections reopen it.

### Features pinned from paywall economics (under exploration)

**F19 — Access ledger.** Per-provider queue of pending paywalled items, valued by objective impact, with subscription/credit/free-route recommendation logic.

**F20 — Focus mode.** Time-boxed, value-ordered research-session UI for an unlocked provider, built on F11 pre-fill and the streamlined capture pipeline.

**F21 — Provider exhaustion state.** Dated, evidence-backed "tapped out" marking per provider, re-armed by new objectives or collection additions tracked via F17.

**Roadmap impact.** F18 objectives should land *early* in Phase 2 — they're the spine the navigator, prioritizer, and ledger all hang from. F16 starts as a simple mode-chooser in Phase 2 and matures through Phase 4. F17 is a data effort that can begin immediately (seed with Ireland + NYC to serve UC-1–UC-3) and grow forever. F19–F21 land Phase 3–4, after chains, discriminators, and the planner exist to feed the ledger.

---

## 12. Competitive Landscape (as of mid-2026)

*Several tools own pieces of this design; none owns the system. The evidence layer — cross-site, source-grade, identity-safe — is unclaimed.*

**Goldie May — closest relative (ACTION: deep study before designing F17/F18).** Browser extension + cloud service, shipping since 2021, freemium, solo-founder built, repeatedly named among Family Tree Magazine's best genealogy websites. Overlaps directly with our plan: research *projects with objectives and research plans* (≈F18), automatic research logging of every site visited with helpful/not-helpful marking (≈F14's Ignore + research log), screenshot capture to the log, auto-citations for FamilySearch and Ancestry records, census auto-search (≈slice of F11), and a locality-knowledge feature that knows record availability facts like courthouse fires and late record-keeping starts (≈lightweight F17). Gaps that define our differentiation: no AI/agentic anything, no extraction of assertions or attachment to tree persons, screenshots rather than hashed archival captures + Wayback, no identity hypotheses or exclusion memory, no capsule export, and its deepest features require working from the FamilySearch tree. Validation takeaway: researchers pay for objectives + logs; the evidence engine on top is open ground.

**Platform giants — walled-garden AI, moving fast.** FamilySearch now fields AI-indexed records, corpus-scale full-text handwriting search, an AI chatbot, and (new) an AI research assistant that proposes tree-extending discoveries at sign-in. Ancestry's Hints/ThruLines do AI discovery across tens of billions of records; Family Tree Maker is adding AI integration; MyHeritage leads on photo AI and AI record finding. Two strategic reads: (a) FamilySearch's full-text HTR *eclipses F4 at corpus scale for their collections* — so F4's value concentrates on everything not already text-indexed (NYC certificates, small archives, arbitrary pages); (b) all platform AI optimizes discovery *inside the wall* and none of it does source-quality rigor, identity hypotheses, or exclusion memory — Ancestry hints remain the ecosystem's main conflation vector, which is exactly the problem F13 monetizes into trust.

**Prompt-layer assistants.** Steve Little's Genealogical Research Assistant — a free structured prompt for ChatGPT/Claude/Gemini from the National Genealogical Society's AI Program Director — produces research plans and GPS proof summaries and is widely respected. It validates demand for GPS-grade AI reasoning but has no page access, no tree access, no capture, no memory. Ours is that reasoning *embedded* where research happens.

**Open source.** Gramps Web (already D1's front-runner) ships an AI chat over the tree — validating D2's RAG direction — but has no capture, extraction, or research-guidance ambitions. Complementary, not competitive.

**Unclaimed whitespace (our moat candidates):** cumulative exclusion research (F15 appears genuinely novel anywhere), hypothesis-based no-merge identity handling (F8/F10), hint conflation guarding (F13), evidence-grade dual archival with integrity hashes, paywall economics (F19–F21), and the portable capsule. Common thread: features that protect *correctness* and *provenance*, which engagement-optimized platforms are structurally disincentivized to build.

---

## 13. Build Strategy — tracer bullet, contracts first

**Principle.** Build the thinnest end-to-end slice through every layer, then thicken whichever layer real usage complains about. The foundation is not components — it's **three contracts** that are expensive to get wrong later. Nail these in week one and everything above them is swappable:

1. **Evidence data model.** `Source → Citation → Assertion` with every assertion carrying its source link and evidence type (F6: stated/calculated/estimated), append-only — nothing destructively merged, ever (F10 falls out for free).
2. **`TreeAdapter` interface.** A dumb local store implements it first; Gramps Web or FamilySearch slot in later without rework (keeps D1 open without blocking).
3. **Capture-folder & capsule spec.** The on-disk shape of a capture (snapshot, media, extraction JSON, citation, hashes) and the export zip.

**Milestone ladder — each rung independently useful on real research (dogfood from day one):**

- **M0 — Pipeline CLI, no extension (a weekend).** Pure Python: `capture <url>` → capture folder + SQLite row, extraction via one Claude call, Evidence Explained citation. Usable on FindAGrave immediately. Extension deferred = zero TS friction while the value core stabilizes.
- **M1 — The button (~week 2).** Minimal MV3 extension (~100 lines): reads the page in view, POSTs to the localhost backend; side panel shows extraction for accept/correct. Every correction is logged from day one — D2 flywheel data collection starts before RAG exists.
- **M2 — Tree awareness.** Ancestry GEDCOM imported read-only; Matcher agent; verdict card v1 (match / no / unsure, with reasoning). The F14 sidekick becomes real; matching pain points arrive as field data.
- **M3 — Capsule out.** Export zip + paste-ready Ancestry citation + guided attach checklist. The loop closes to the real tree; the tool is worth keeping even if development stopped here.
- **M4+ — pulled by pain, not plan.** Usage chooses: NYC certs dominating → F4 vision path; Irish ambiguity biting → F8 hypotheses; "what next?" itching → F18 objectives. Each lands as a new agent on the same spine.

**Guardrails.** Every agent call goes through a single interface with prompts and responses logged, so corrected captures automatically become the eval set and agent upgrades never touch pipeline code. Backend runs as an always-on local service (M4 Mac mini). UC-4 compliance posture and the gated-site archival policy apply from M0.
