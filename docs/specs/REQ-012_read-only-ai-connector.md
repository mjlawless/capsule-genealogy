# REQ-012 — a read-only AI connector (MCP) for the archive

**Status:** 🟡 **BUILT 2026-10-23, shipped in v0.2.6 (2026-10-24); owner's live check pending.** Approved the
same day; the owner ruled on §9 (see "Rulings"), and "go ahead" accepted the name
`capsule mcp`.

> ## Implementation (2026-10-23)
>
> - `capsule/connector/archive.py` (the read-only archive), `capsule/connector/server.py`
>   (MCP server, MCP Python SDK **2.3.0, MIT**, `MCPServer`), `capsule mcp` in the CLI.
>   Contract `capsule-mcp/v1`.
> - `Repository.open_read_only`: SQLite `mode=ro` URI, 5 s busy timeout, no schema step.
>   The Ancestry ledger also writes on open, so the connector never opens it.
> - **Deviation, `was_captured`:** it lists every capture of the page (an Ancestry record
>   by its record ids, other pages by normalized URL), whichever image was saved, each
>   judged complete by the duplicate check's own read-only verdict. It does not call
>   `capsule query`, which consults the ledger and matches the saved image label: captures
>   saved before BUG-019's fix carry stray labels ("Select Language" and the like), so
>   `capsule query` misses them; the connector does not.
> - **Serving takes the archive only from `--data-dir` or `CAPSULE_DATA_DIR` as the process
>   was started**, never from a `.env` in the app's working folder (the CLI loads `./.env`
>   before every command). `--print-config`, which the user runs from their Capsule folder,
>   uses the normal lookup.
> - Facts carry Capsule's `[unverified …]` mark (`unverified: true` plus a warning), also on
>   retracted facts in history; transcript fields keep both their annotation and their
>   review note.
> - 18 tests in `tests/test_req_012_connector.py`, including a byte-for-byte check that no
>   tool changes the archive, a refused write through the connection, empty-archive refusal,
>   and a `.env` that must not pick the archive. Verified end to end over real stdio,
>   started from an unrelated folder, against the owner's archive.
> - Codex adversarial review: 4 rounds, every finding fixed with a regression test.
> - **Still owed (§10):** the owner's live check from Claude Desktop, and the 10-20 question
>   set; the Help page section is written.

## 1. The need

The owner's direction (2026-10-23): getting a capture into a family tree must work by hand,
**but the AI route is preferred**. v0.2.5 shipped the hand route (each capture's citation
with Copy buttons) and a stopgap for the AI route: **Copy for AI** copies one capture as text
to paste into an assistant. That works, but only one capture at a time, and only after the
user has found it.

A connector lets the user's own AI assistant ask the archive directly, for example:

- "What do I have on Mary Kelly of Kilmore?"
- "Which of my captures give a birth year for John Murphy, and do they agree?"
- "I searched the Kilmore baptism index for 1852 and found nothing. Does that index even
  cover 1852?"
- "Help me enter this burial record in Gramps."

The assistant answers from the archive, citing the captures it used. This is the first,
local, read-only step toward the assistant in `docs/plan.md` D2 ("ask about anyone in your
tree, always answering with citations back to sources").

## 2. What it is

A local **MCP server** (Model Context Protocol, the standard way AI apps reach a user's
own data) built into Capsule:

- **Started by the AI app**, over stdio, as `capsule mcp --data-dir <archive>`. No network
  port, nothing listening, no token: the app runs it as the user, on the user's computer.
- **Read-only.** It reads the archive's database and capture folders, and it changes
  nothing: no corrections, notes, captures or files. There is no write tool in v1.
- **Clients:** any AI app that runs a local stdio MCP server. Claude Desktop is the first
  supported and tested one (Claude Code works the same way). Which others to test and
  document is an open question (§9).

## 3. Where the archive is (must be explicit)

An AI app starts the server from a working folder the user does not choose. Capsule's
default archive lookup (`capsule.config.data_dir()`) depends on that folder: it honors
`CAPSULE_DATA_DIR`, then `./data/capsule.db` if present, then the per-user data folder, and
the CLI reads `./.env`. Started from an arbitrary folder, the connector could open an empty
archive and answer, confidently, "you have no captures" (the BUG-003 failure).

So:

1. The server takes the archive **explicitly**: `--data-dir`, or `CAPSULE_DATA_DIR` set in
   the client's server config (`env`). It does not rely on the working folder or `./.env`.
2. **`capsule mcp --print-config`** prints the exact JSON to paste into the client's config:
   the full path to `uv` (an installed copy has no `capsule` command on PATH), the `--project`
   path to the installed app, and the real archive path. The Help page links to it.
3. **An empty or missing archive is refused, not answered.** If the database has no sources,
   every tool returns an error naming the path it opened and, via `stray_databases`, any
   archive found nearby, rather than an empty result.

## 4. Reading safely while Capsule is running

`Repository()` writes on every open: it runs `schema.sql` and the column migrations, then
commits. The connector must not use it. It gets its own reader:

- opens the database **read-only** (`file:<path>?mode=ro` URI), with no schema or migration
  step;
- sets a **busy timeout** (the database uses a rollback journal, so a capture's commit
  briefly blocks readers);
- tolerates a database **older than the code** (a missing optional column reads as absent),
  and reports a database **newer than the code** instead of guessing;
- reads capture folders only through the existing tolerant readers
  (`capsule/collection/read.py`), never writes `index.html`, sidecars or anything else.

A test enforces the read-only promise: an archive's database and folders are hashed before
and after a full run of every tool, and must be byte-identical.

## 5. Tools (v1)

Every tool returns structured JSON. Page-derived text (titles, values, supporting words,
citations, notes) is always in clearly named fields (§7.3).

| Tool | Answers | Returns |
|---|---|---|
| `search_captures` | "what do I have on…" | Capture summaries matching words in names, facts, citations, notes and sites, with optional filters: record category, site, date range, gated or not. Paged (default 20). |
| `get_capture` | everything about one capture | Its citation (reference note, source-list entry, access date, URL), record type and category, site, captured date, gating, status, the persons named (each with role), every **current** fact (person, field, value exactly as written, evidence type, the record's supporting words, and whether it was corrected), the researcher's notes (marked as notes, not evidence), files in the folder, verification status, and any replacement capture. |
| `find_people` | "who named X appears where" | Every **persona** (a person as one record names them) whose name matches, each with its capture and role. Never grouped across records (§7.1). |
| `check_coverage` | "does that index cover 1852?" | What a site's index **says it covers**, from its captured coverage pages (`capsule coverage`), with the citation of the coverage page. |
| `was_captured` | "have I already saved this page?" | The pre-capture check (`capsule query`, capsule-db-v1 §2) for a URL. |
| `get_transcript` | "what does this scanned certificate say?" | The newest vision transcript of a capture's scan (REQ-006): fields, values, and those flagged for review. |
| `get_page_text` (*off by default, §9*) | "what else does that page say?" | The saved page's text, capped in length. |

Facts and records come out **the way the served index shows them**: built through
`overlay_corrections`, so a corrected value is the current one, and a retracted fact is left
out unless the tool is asked for history (then it is listed as retracted, with its
correction). Record categories come from `record_category`; citations are the same fields the
index's Citation block shows.

**An MCP prompt, `enter_in_tree`** (capture id, program name), gives the assistant the same
instructions as the Help page's AI route: list each person, then the events and facts to
enter with this citation attached, keep every value exactly as written and its evidence
type, and never assume anyone is already in the tree; ask the user.

## 6. What the assistant does with it

The assistant reads; the user acts. For tree entry, the assistant tells the user what to
enter, and the user makes the entries in Gramps or another program, checking each against
the record (the v0.2.5 Help page's AI route, without the copy and paste). Writing into the
tree is a later requirement (§8).

## 7. Rules

### 7.1 Never merge people

**What Capsule guarantees:** its data never merges people. A persona is one person as one
record names them; `find_people` returns each separately, with its record; nothing in the
connector clusters, links or deduplicates personas; every fact stays linked to its record.

**What Capsule can only ask for:** the assistant may still say two records are "the same
John Murphy". The tool descriptions and the `enter_in_tree` prompt tell it not to: to
present matches as possibilities for the user to decide, with the evidence on each side.
The spec does not claim this is enforced.

### 7.2 Every answer is citable

Every fact the connector returns carries its capture id, its record's citation, and the
record's own supporting words. Tool descriptions ask the assistant to cite the capture for
every statement it makes from the archive. Values are verbatim; evidence types (stated,
calculated, estimated) are always included.

### 7.3 Captured pages are untrusted text

A page, its title, a note or a snippet can contain text written to steer an AI ("ignore your
instructions and…"). The connector returns all page-derived text in labelled data fields,
never as instructions, and v1 has **no tool that acts** (nothing to steer it into doing).
The Help page warns users who also enable tools that act (email, files, browsing) in the
same AI app that captured pages are untrusted.

### 7.4 Pages from subscription and sign-in sites

With Copy for AI, the user chooses each paste. With a connector, **the assistant chooses
which captures to read**, and their text goes to the AI provider without the user seeing
each transfer. That is a different stance from "you drive", so the policy is the owner's
decision (§9, question 1). Whatever it is:

- A capture counts as **gated if either** its stored flag says so **or** the sites catalog
  says its site is a subscription or sign-up site. (The stored flag is known to be wrong in
  both directions on older captures.)
- Every result from a gated capture carries `gated: true` and a "personal research only"
  note.
- `get_page_text`, where the copyright exposure is largest, is off by default.

### 7.5 Other

- **Living people:** records about living people (recent records) need care when sent to an
  AI provider. v1 treats them like any other record (owner ruling, §9); a known limit.
- **Size:** results are paged and long text is capped, so one call cannot dump the archive.
- **The tool surface is a contract**, `capsule-mcp/v1`, versioned like `capsule-db-v1`: tool
  names, inputs and output fields change only additively within v1. Users' prompts and
  clients will depend on them.
- **Dependencies:** the official MCP Python SDK. Its version and license are confirmed at
  build time and recorded here then.

## 8. Not in scope (v1)

- **Writing to Gramps or any tree.** That is a later requirement under
  `docs/gramps-integration.md` (the `GrampsWriteAdapter`, its guardrails, and the user's
  confirmation of each write).
- Corrections, notes or captures made through the AI.
- A remote or hosted connector, or any network listener.
- Embeddings and semantic search (plan.md D2's index); v1 searches words.

## 9. Open questions for the owner

1. **Pages from subscription sites** (§7.4). Options:
   - **(a) include, flagged:** the assistant can read them; every result says gated.
   - **(b) summary only:** the citation and fact list, but no supporting words or page text.
   - **(c) exclude:** gated captures are invisible to the assistant.
   - **(d) per-session opt-in:** excluded until the user says, in that conversation, that
     they may be read.

   **Recommendation: (a) with a setting to choose (c).** It is the user's own research, the
   same text the extension already sends to Anthropic to read the page, and the same text
   Copy for AI hands over, and excluding it would hide most of a typical archive (Ancestry,
   RootsIreland, Newspapers.com). `get_page_text` stays off by default either way.
2. **`get_page_text`:** build it in v1 (off by default), or leave it out?
   Recommendation: build it, off.
3. **Clients:** Claude Desktop only at first, or also test and document others?
   Recommendation: Claude Desktop and Claude Code at first; document others as reported.
4. **Living people:** flag recent records (e.g. born or dated within the last 100 years) in
   results, hold them back, or neither for v1? Recommendation: flag them in v1.
5. **The name:** `capsule mcp`, shown to users as "Capsule for AI assistants"?

**Rulings (owner, 2026-10-23):**

1. Gated pages: **(a) include, flagged**, with a setting to exclude them.
2. `get_page_text`: **build it, off by default.**
3. Clients: **Claude Desktop and Claude Code**; others documented as reported.
4. Living people: **nothing special in v1.** (§7.5's note stands as a known limit; revisit
   if users ask.)
5. Name: open.

## 10. Definition of done

- `capsule mcp` serves the §5 tools and the `enter_in_tree` prompt over stdio;
  `capsule mcp --print-config` prints a working Claude Desktop config on macOS and Windows.
- The read-only test (§4) and a refusal test for an empty archive (§3) pass on all three
  operating systems in CI.
- Corrected facts match the served index for the same capture (a test compares them).
- The gated policy chosen in §9 is implemented, using the either-flag rule (§7.4).
- A question set (like the eval fixtures) of 10 to 20 real questions about a test archive,
  answered through the connector in Claude Desktop, each answer citing the right captures.
- The Help page gains "Ask an AI about your archive": setup, what it can and cannot do, the
  untrusted-text warning, and the gated policy in plain words.
- A live check by the owner on the real archive from Claude Desktop.
