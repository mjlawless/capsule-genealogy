# Changelog

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
