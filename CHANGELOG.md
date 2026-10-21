# Changelog

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
