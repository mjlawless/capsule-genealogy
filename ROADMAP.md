# Genealogy Capsule roadmap

Where Capsule is heading, in rough order. Nothing here has a promised date. Items that cost
money (store fees, code signing) are marked **needs budget** and happen only when that is
decided. Suggestions are welcome in [Discussions](../../discussions).

## Done in v0.2: easy to evaluate

One-line install, capture without an API key (read later), your own key entered in the
extension, automatic pairing, a version check, and the installer tested from scratch in CI.
Still to come from this list: an optional **demo collection** of sample captures.

## Now: Chrome Web Store

A listing so installing the extension is one click instead of Developer mode (unlisted first,
for evaluators).

## Next: evidence you can work with

- **Review and correct.** Correct, retract or add each fact from the captures index.
  Corrections are kept append-only beside the original reading, and become test cases for
  the extractor. (Built; ships in the next release.)
- **Better handwriting transcription** of scanned certificates and registers.
- **More sources handled natively,** starting with the National Library of Ireland's parish
  register viewer.
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
