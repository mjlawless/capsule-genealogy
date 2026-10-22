# Changelog

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
