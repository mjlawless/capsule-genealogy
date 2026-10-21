# Capsule roadmap

Where Capsule is heading, in rough order. Nothing here has a promised date. Items that cost
money (store fees, code signing) are marked **needs budget** and happen only when that is
decided. Suggestions are welcome in [Discussions](../../discussions).

## Now: easy to evaluate (v0.2)

The goal: someone on a clean Mac or Windows computer goes from this page to their first capture
in **10 minutes or less**, and can see it work **without an Anthropic API key**.

- **Capture without an API key.** Archive the page, PDF, record image and citation straight
  away. The AI extraction is marked *pending* and can be run later, once a key is added.
- **One-line install** for macOS and Windows: one command installs everything and runs setup.
- **Pairing without copy-paste.** The extension finds the local backend and pairs after a
  one-time confirmation, instead of a pasted token.
- **Version check.** The extension and the backend tell you in plain language when one of
  them needs updating.
- **Install tested in CI.** Every release is installed from scratch on fresh Linux, macOS and
  Windows machines before it ships.
- **Demo collection.** Optional sample captures from public-domain records, so you can explore
  the collection page before installing the extension.

## Next: evidence you can work with

- **Review and correct.** A side panel to accept or correct each extracted fact. Corrections
  are kept append-only beside the original reading, and become test cases for the extractor.
- **Better handwriting transcription** of scanned certificates and registers.
- **More sources handled natively,** starting with the National Library of Ireland's parish
  register viewer.
- **Wayback Machine submission** for public (non-paywalled) pages.
- **A larger evaluation set,** so changes to extraction can be measured, not guessed.

## Later: from evidence to a tree

- **Gramps integration.** Write accepted evidence into a Gramps Web tree, every fact still
  linked to its source, and nobody merged without your decision.
- **Lineage and proof support.** Assemble a documented chain of evidence across generations,
  in the shape lineage societies ask for.

## Later: no terminal at all

- **A packaged app** with a tray icon that starts with your computer; no Python or terminal.
- **Signed installers** for Windows, then macOS (**needs budget**: code signing).
- **Chrome Web Store listing,** so installing the extension is one click (**needs budget**:
  developer registration; the `debugger` permission needs careful review).
- **Automatic updates** and a one-click "send diagnostics" bundle for support.

## Not planned

- A hosted service. Your evidence stays on your computer; that is the point.
- Bulk downloading or crawling of any site. Capsule captures the page you are looking at.
