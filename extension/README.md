# Capsule browser extension (M1 spike)

Minimal MV3 extension that captures **the page you are viewing** and posts it to
your local Capsule backend. It reads the page you already have open in your own
browser session; Capsule itself makes no requests to the site and bypasses
nothing. For a login-gated page, that means only what your own account already
shows you is captured, and it stays on your machine.

> Plain JavaScript, no build step (the ~100-line spike). TypeScript + tooling
> come once it grows past this.

## Use it

1. **Start the backend** (needs your Anthropic key):
   Put your key in a `.env` file at the repo root (git-ignored) and it is loaded
   on every `capsule` command:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
   ```bash
   uv run capsule serve
   ```
   An exported `ANTHROPIC_API_KEY` in the shell takes precedence over `.env`.
   It binds `127.0.0.1:8765` only. Captures land in `./data/captures/`, indexed
   in `./data/capsule.db` (override with `CAPSULE_DATA_DIR`).

   On start it prints an **auth token** (or set your own via `CAPSULE_TOKEN`).
   `/capture` requires it — without a token the backend refuses every request, so
   no random web page you visit can drive captures against your localhost backend.

2. **Load the extension** (once):
   - Chrome → `chrome://extensions` → enable **Developer mode**
   - **Load unpacked** → select this `extension/` folder

3. **Capture:** open the record page in your browser, click the Capsule toolbar
   icon, paste the token from step 1 into the **Token** field (stored, so it's a
   one-time paste), pick **Public** or **Gated**, leave **Also archive embedded
   scans / PDFs** checked (see below), and hit **Capture this page**. The panel
   shows the capture folder, verify status, media count, and extracted assertions.

## Archiving embedded scans / PDFs

With the checkbox on, the extension also archives the media the page already
shows — embedded certificate **PDFs** (`<embed>`/`<object>`/`<iframe>`) and
large **scan images** (`<img>` ≥ 400×400, so logos/icons are skipped). Each
media file's origin URL + MIME are recorded in the capture manifest's `media[]`
array (evidence-grade provenance) and its bytes are SHA-256 hashed like every
other file. Per-file cap 25 MiB, up to 8 files.

**How the bytes are fetched (and why it needs your grant).** Genealogy viewers
usually load the scan/PDF from a *cross-origin, self-authorizing* signed URL
(e.g. an Azure blob SAS URL) into a native PDF plugin. Those bytes can't be read
from the page's own JavaScript — the page's CSP blocks the fetch and the asset
sends no CORS headers, and the plugin's buffer isn't scriptable. So Capsule
fetches them from the **extension's own context**, which isn't bound by the
page's CSP and — with a host permission you grant — isn't bound by CORS either:

1. When you open the popup it detects embedded media and names the host(s)
   ("Detected 1 embedded file on `dorisarchive.blob.core.windows.net`").
2. On **Capture this page**, Chrome asks you to allow access to exactly that
   host. Allow it and the file is fetched and archived; decline and the capture
   proceeds with the page snapshot only (the panel notes what was skipped).

The signed URL carries its own authorization in the query string, so **no
login, cookie, or credential is ever handled** — Capsule just re-requests the
one asset you're already viewing. The grant is per-host and standing; revoke it
anytime under `chrome://extensions` → Capsule → *Site access*. `host_permissions`
in the manifest stays `127.0.0.1` only; media hosts are `optional_host_permissions`
you approve at capture time.

**Still-skipped cases.** A `blob:`-delivered file the page created is read in the
page context; a file that is neither reachable that way nor granted (or over the
size cap) is skipped, and the capture still succeeds with whatever landed — the
media count reflects reality.

## The rendered page PDF

Every page capture also archives a **PDF render of the page as you saw it**
(`media/0-record.pdf` in the capture folder, SHA-256 hashed like any other file,
with provenance `render:page-pdf`). A DOM snapshot proves *what the page said*;
the render is what you can actually put in front of another researcher.

It is printed from the tab you already have open, over Chrome's DevTools protocol
(`Page.printToPDF`) — **no request is made to the source site**, and nothing
leaves your machine. A gated record's render is local-only and copyright-flagged
exactly like its snapshot: never republished, never sent to Wayback.

Two things to expect:

- **The debugger banner.** While rendering (~1–3 s) Chrome shows *"Capsule …
  started debugging this browser"* at the top of the tab; it clears when the
  render finishes. Launch Chrome with `--silent-debugger-extension-api` to
  suppress it.
- **One debugger client per tab.** If DevTools is open on the page — or a
  CDP-driven automation tool is attached — the render is **skipped**, because
  Chrome allows only one client to attach. The capture still succeeds, with its
  page snapshot and full extraction; the toast says the PDF was skipped and why.
  A render is an enhancement, never a gate.

## Notes / limits

- Auth is a shared token: `capsule serve` prints it, you paste it into the
  extension once. The token also forces a CORS preflight the backend only
  approves for the extension's own origin, so a visited page can't even reach
  `/capture`. The token is persisted in `data/.capsule-token` and reused across
  restarts; to rotate it, stop `serve`, delete that file (or set `CAPSULE_TOKEN`),
  restart, and paste the new token into the extension.
- Image-only records (e.g. scanned certificates) capture + hash fine, but
  extraction will be thin until vision/HTR (M4) — the DOM has no text to read.
