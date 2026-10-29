# Genealogy Capsule roadmap

Where Capsule is heading, in rough order. Nothing here has a promised date. Items that cost
money (store fees, code signing) are marked **needs budget** and happen only when that is
decided. Suggestions are welcome in [Discussions](../../discussions).

## Done in v0.2: easy to evaluate

One-line install, capture without an API key (read later), your own key entered in the
extension, automatic pairing, a version check, and the installer tested from scratch in CI.
Still to come from this list: an optional **demo collection** of sample captures.

## Done: Chrome Web Store

The extension is [on the Chrome Web Store](https://chromewebstore.google.com/detail/genealogy-capsule/dopmkpbopjegbdngekbdpgpkmpgkgeeo) (v0.2.6), so adding it is one click
instead of Developer mode.

## Now: a gentler install

- **A welcome page** when the extension is added: it says the Capsule app is the other half,
  offers the installer for your computer, and turns green when the two are connected.
- **Installers that end at the store page,** so starting from either half leads to the other.
- **Fewer permission warnings:** Capsule asks for a site or your downloads when it needs them,
  not at install. Saving the page PDF without Chrome's debugger is being measured first,
  because the PDF is evidence and must stay faithful.

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
- **Automatic updates** and a one-click "send diagnostics" bundle for support.

## Not planned

- A hosted service. Your evidence stays on your computer; that is the point.
- Bulk downloading or crawling of any site. Capsule captures the page you are looking at.
