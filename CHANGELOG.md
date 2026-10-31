# Changelog

## 0.2.8 — security updates, and sample captures to look at

- **Security update: the image library.** Capsule opens the record images it saves to check
  that each one is a full, readable picture, using the Pillow library. This release moves to
  Pillow 13, which fixes flaws in how images are read, among them a critical one in its JPEG
  reader. A website's image is outside your control, so **please update.**
- **Updated Anthropic library.** Pages are read through version 2 of Anthropic's library,
  which uses Anthropic's current API version. Nothing changes in how you use Capsule or in what
  a reading costs.
- **Sample captures.** `samples/` holds six real captures of public records a century or more
  old (NYC Municipal Archives, IrishGenealogy.ie, the National Library of Ireland): what
  Capsule records about a page, its facts and its citation, as JSON files you can open.
- **For contributors:** a code of conduct, a citation file (`CITATION.cff`) for citing
  Capsule in your own work, a pull-request template, and notes on how the repository is
  published and how pull requests are taken.

## 0.2.7 — an easier install, a full copy of every page, and a page for each capture

- **Security fix: open saved pages safely.** A saved page keeps the scripts the site had
  (ads, trackers). Until now, opening one from the index let those scripts run as part of
  Capsule's own pages, where they could reach the rest of your archive and contact the site
  and its advertisers. Saved files now open in a locked-down view where nothing runs and
  nothing is loaded from the internet. **Every earlier version has this problem; please
  update.**
- **A full copy of every page.** A web-page capture now also keeps the **full page**
  (`page.mhtml`): the page as you saw it, with its pictures and layout, in one file, made from
  what Chrome had already loaded (nothing is fetched again). Open it from the index, or
  download it and open it in Chrome or Edge. **Ads are hidden** from it and from the page PDF.
  Like the PDF, it is made only when Chrome allows (not while DevTools is open on the tab) and
  is left out of very large captures; the capture message says when it was.
- **A page for each capture.** A capture's folder link now opens a readable page: the record
  and its citation, its pictures and PDF shown in place, the full page, what the capture
  records about itself, and every file. JSON files open as readable tables.
- **Clear names for the copies of a page:** the **full page** (`page.mhtml`, the complete
  copy), the **page PDF**, and the **page text** (`snapshot.html`, what Capsule reads facts
  from).
- **An easier install.** Adding the extension opens a welcome tab that walks through the two
  parts and says when both are in place. The installers point to the Chrome Web Store. Chrome
  asks for fewer permissions at install: downloads, and access to every site, are asked for
  only when you turn on **Capsule button on every page, and keep site downloads** in Settings,
  or when a capture needs a site. The extension warns when it and the app are out of step.
- **Use Capsule from an AI assistant.** `skills/genealogy-capsule` teaches an AI browser agent
  (such as Claude Code with Claude in Chrome) to capture with Capsule correctly: the one-time
  setup, how to read a capture's result, and when a record really is saved. See "Using Capsule
  with an AI assistant" in the README.
- **The index's detail pane** always shows each fact's ✎ and ✕, and a divider between the list
  and the details can be dragged (or moved with the arrow keys) to give either side more room.
- **Fixes:** the AI connector's people and coverage lists page through long results like its
  search does; an old label no longer hides a page from duplicate checks; a paid reading whose
  results were not stored is recorded rather than paid for again; a page that is itself the
  record image is graded as one, and a capture that holds only a print of it says why.

## 0.2.6 — choose which pages to read, and ask an AI about your archive

- **Choose which saved pages to read.** Adding an API key no longer offers to read every
  page saved without one. On the index, pages not read yet have a tick box: tick some (or
  filter the list) and click **Read with AI**, which shows the pages and a rough cost before
  it starts. A page is read once: one that was already read is not read again, here or with
  `capsule extract`. A new **Read or not** filter shows what has been read. The popup's "Read them now" is replaced by a link
  to those pages in the index.

- **Ask an AI about your archive.** `capsule mcp` lets Claude Desktop or Claude Code read
  your archive and answer from it, citing the captures it used; it can change nothing.
  `capsule mcp --print-config` prints the setup to paste in. See the Help page.

## 0.2.5 — correct what was read, and a clearer index

- **Correct what Capsule read, on the index page.** Opened from Capsule
  (`http://127.0.0.1:8765/`), every fact has ✎ to change its value (the form quotes the words
  on the page it came from) and ✕ to retract it, and **+ Add a fact** adds something the
  reading missed about a person it found (adding a person it missed is not possible yet).
  Corrections are kept beside the original reading, never over
  it: a corrected fact shows a small *edited*, *added* or *retracted* tag, and its history is
  in the ✎ form. The capture's status becomes *reviewed*.
- After a capture, the popup's footer link opens that capture in the index.
- **Carry a capture into your family tree program.** Each capture shows its full citation
  (reference note and source-list entry) with Copy buttons, and **Copy for AI** copies the
  whole capture as text to paste into Claude or another assistant, which can then tell you
  what to enter in Gramps, RootsMagic or another program. The Help page walks through both
  ways. There is no direct connection to a tree program yet.
- **Help:** a short guide to using Capsule, linked from the top of the index.
- **Record types are grouped** into common categories (Birth & baptism, Marriage, Death &
  burial, Census & household, and so on); the exact type the reader gave is in each
  capture's detail. The record and site filters now take several choices at once.
- **Open folder** buttons show a capture's folder, or the whole archive, in Finder or File
  Explorer, so you can look at the files yourself.
- **A capture that does not hold the record is marked "incomplete"**, with the reason. A page
  saved without an API key says "not read yet" and what to do.
- **Find a Grave memorials are no longer saved as gated** when you are signed out (a hidden
  "Sign out successful" notice was read as a signed-in session). Pages from subscription and
  sign-up sites are now recognized from the list of known sites, not from English words on
  the page, so a German Archion page is recognized too.
- The index no longer shows placeholder lines (Matched, Attached, OCR'd); it shows when a
  scan was transcribed. Wayback Machine submission, planned but never built, is dropped:
  the copy on your computer is the archive.
- The installer is now tested on computers with no Python of their own.

## 0.2.4 — records read by Claude Opus 5.5

- **Records are now read by Claude Opus 5.5** (was Opus 4.8), chosen by testing on 31 real
  record pages across six sites. It left out the fewest facts of the newer models, quoted
  every value word for word, and works out a birth year from an age at death, marking it
  "calculated" rather than "stated". It costs about the same per page.
- Long pages, such as a search with many results, no longer risk running out of room.
- The extraction eval reports derived facts (calculated or estimated) for review instead of
  counting them as errors; facts the page states are still checked exactly.
- The Windows test run passes again (two test-only fixes).

## 0.2.3 — move your archive between computers, and fixes from a test pass

- **Move your archive to another computer.** `capsule export archive.zip` packages the whole
  archive, safely while Capsule runs; `capsule import archive.zip` merges it into another
  install. It adds only what is missing, never overwrites or deletes, and can be re-run later
  to bring newer captures across. `--dry-run` shows what it would add.
- **A list of the sites Capsule works with**, in [SITES.md](SITES.md) and the README, from a
  test pass on the top free genealogy sites.
- **A complete capture of an ordinary page now shows a green confirmation.** It said "IMAGE ✗
  none — NOT mined" in red on every page without a separate record image.
- **Irish census records are no longer mistaken for one another.** The census site keeps the
  record number after a `#` in the address, which Capsule ignored when checking for duplicates.
- **The popup no longer shows an empty yellow bar** under its title.
- **Ad, tracking and consent frames are no longer saved as files** with a warning each, and
  Capture no longer asks for access to ad sites.
- The popup says when it is an unofficial copy of the extension, and how to fix that.

## 0.2.2 — Windows install fixes

Found on the first real Windows install of 0.2.1:

- **The Windows installer no longer fails while setting Capsule to start at logon** ("Cannot
  find path …\app\.env"). A leftover check for an API key in a settings file broke every
  first install that set up the logon task. Capsule doesn't need a key to run.
- **A start-at-login item left by a deleted copy of Capsule is replaced** instead of stopping
  the install. If another copy really exists, the message now says where it is and gives the
  exact command to retire it.
- The install window no longer shows setup's by-hand "run `capsule serve`" steps and pairing
  token partway through, which contradicted the installer's own ending.
- CI now runs the Windows upgrade with the logon task, the path that failed.

## 0.2.1 — no terminal needed

- **New name: Genealogy Capsule.** The extension, popup, collection page, installers and docs
  now use the full name; "Capsule" remains the short form. Nothing you have changes: the
  `capsule` command, your archive folder and your settings stay where they are.
- **Double-click installers:** `Install-Capsule.cmd` for Windows and `Install-Capsule-mac.zip`
  for macOS, attached to each release. They run the same install as the one-line command, in a
  window you can watch. They are not code-signed yet, so Windows and macOS warn once; the README
  and the extension say exactly what to click.
- **A first-run screen in the extension.** If the Capsule app isn't installed (or isn't
  running), the popup says so, offers the installer for your system, and switches to the normal
  popup by itself as soon as Capsule starts. Ready for installing the extension from the Chrome
  Web Store before the app.
- **Chrome Web Store ready:** the app pairs with the Web Store copy of the extension by itself,
  as it does with the unpacked one.
- Capture errors now point to the popup instead of a terminal command, and the popup says
  plainly when an older Capsule app doesn't recognize the extension.
- The installers' last message no longer asks Web Store users to load the extension again.
- CI: the Windows install test now runs its checks in the same step as the installer.

## 0.2.0 — easy to evaluate

- **One-line install** for macOS/Linux and Windows. It installs everything, starts Capsule at
  login, and upgrades safely: the previous version is put back automatically if anything fails,
  and your archive is never touched. Tested from scratch on fresh machines in CI.
- **Try it without an AI key.** Pages are archived (page, PDF, record image, citation) and
  marked "not read yet"; a short, dismissable prompt explains what a key adds.
- **Enter your own Anthropic key in the extension**, no terminal; it is checked and kept in your
  computer's password store. **Read them now** reads everything archived before the key.
  `capsule extract` does the same from the command line, from the archived page.
- **No more token pasting:** the extension pairs with Capsule by itself, and re-pairs after a
  reinstall. Web pages still cannot drive captures.
- **Version check:** the popup says in plain words when the extension or the app needs updating.

## 0.1.0 — first public release (early access)

Capsule had been in daily private use before this release. Highlights of what ships:

- **Capture** any record page from Chrome into a hashed, self-describing capture folder: page
  snapshot, page PDF, the record image from the site's own download, an AI extraction with
  every fact linked to its source text, and an Evidence Explained-style citation.
- **Record images** from Ancestry (one-click), tile viewers (Archion, NLI, Matricula),
  Riksarkivet and Newspapers.com, through the site's own Download button.
- **`capsule ingest`** for PDFs and images you already downloaded.
- **`capsule transcribe`**: an AI reading of handwritten scans, kept beside the image as a
  reading aid, never as evidence.
- **`capsule supersede`**: mark a capture as replaced by a better one, append-only.
- **Install from source without environment variables:** `capsule setup` stores your
  Anthropic key in the OS keyring; the archive lives in your per-user data folder.
- Every capture records which code made it (`tool_version`, `extension_version`).
