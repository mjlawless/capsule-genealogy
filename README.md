# Capsule

**Evidence-grade genealogy capture.** A Chrome extension plus a small program on your own
computer that turn the record page you are looking at into an archived, cited, hashed source
that you keep: the page, the record image, what the record says, and an Evidence Explained
citation.

> **Status: v0.1.0, early access.** Capsule has been used daily on real research (hundreds
> of captures from Irish parish registers, NYC vital records, Ancestry, Archion, Riksarkivet,
> Newspapers.com and more), but installing it still takes about 15 minutes of following
> steps in a terminal. A one-click installer and a Chrome Web Store listing are planned,
> not built. If you are comfortable copying commands into a terminal, you can use it today.

## What it does

Open a record page, click **Capture**. Capsule saves a **capture folder** on your computer:

- the page exactly as you saw it (`snapshot.html`) and a PDF render of it;
- the **record image**, from the site's own download button where one exists, never a
  screenshot standing in for the record;
- an AI **extraction** of what the record says: people, dates, places, each fact linked to
  the words on the page it came from and marked *stated*, *calculated* or *estimated*;
- an **Evidence Explained-style citation**, with the access date;
- **SHA-256 hashes** over all of it, so you can prove later that nothing changed.

Everything lands in a browsable collection page (`index.html`) on your own disk.

The rules it will not bend:

- **Verbatim is sacred.** What the record says is kept exactly; normalizing is a separate
  annotation; illegible is `[?]`.
- **It never merges people.** Two records naming "John Murphy" are two personas until *you*
  decide otherwise, and every fact stays linked to its source.
- **You drive.** Capsule reads only the page you are looking at, when you click. No
  background requests to record sites, no crawling, no handling of your site passwords.
- **Paywalled content stays private.** Pages from subscription sites are kept only on your
  computer, marked copyright-protected, and never published or archived anywhere else.
- **A capture that does not hold the record does not report success.**

## Before you install: what to know

- **You need your own Anthropic API key.** The extraction is done by Claude, through
  Anthropic's API, billed to you. Get a key at
  [console.anthropic.com](https://console.anthropic.com/) and set a monthly spending limit.
  A typical page capture uses about 11,000 input and 1,400 output tokens with the default
  model; see [anthropic.com/pricing](https://www.anthropic.com/pricing) for what that costs.
  Archiving a PDF or image file makes no model call and costs nothing.
- **What leaves your computer:** the text of the page you capture is sent to Anthropic for
  extraction, using your key. Nothing is sent to the authors of Capsule or anyone else.
- **Respect each site's terms.** Capsule only reads pages you can already see in your own
  browser session, but some subscription sites limit what you may save. You are responsible
  for using it within the terms of the sites you use.
- **Chrome permissions:** the extension asks for `debugger` (used only to save a PDF of the
  page when you click Capture), `downloads` (to collect a record image you download through
  the site's own Download button), and access to the sites you capture from.
- **On Ancestry**, the one-click image capture clicks Ancestry's own Download button for
  you, on the record you are viewing, only when you start it.

Capsule is an independent project. It is not affiliated with or endorsed by Ancestry,
FamilySearch, Archion, Riksarkivet, Newspapers.com or any other site it works with.

## Install (macOS or Windows)

You need about 15 minutes, Google Chrome, and an internet connection.

1. **Install `uv`**, the tool that runs Capsule (it installs Python for you):
   - macOS: open *Terminal* and run
     `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows: open *PowerShell* and run
     `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

   Then close and reopen the terminal.
2. **Download Capsule:** on this page click **Code → Download ZIP**, and unzip it somewhere
   you will keep it (for example your Documents folder). Or, with git:
   `git clone https://github.com/mjlawless/capsule-genealogy.git`.
3. **In the terminal, go to that folder** (e.g. `cd ~/Documents/capsule`) and run:

   ```
   uv run capsule setup
   ```

   The first run downloads what Capsule needs (a minute or two). It then asks for your
   Anthropic API key, checks it with Anthropic, and saves it in your computer's own
   password store (Keychain on macOS, Credential Manager on Windows). It finishes by showing
   a **pairing token**.
4. **Start Capsule** and leave the window open:

   ```
   uv run capsule serve
   ```

5. **Add the extension to Chrome:** go to `chrome://extensions`, turn on **Developer mode**
   (top right), click **Load unpacked**, and choose the `extension` folder inside the
   Capsule folder.
6. **Pair it:** click the Capsule icon in Chrome's toolbar (pin it from the puzzle-piece
   menu) and paste the pairing token from step 3 into the **Token** field.
7. **Capture something:** open a record page and click **Capture** in the Capsule popup.
   When it finishes, open your collection at `http://127.0.0.1:8765/`.

Your archive is kept in your user data folder (`~/Library/Application Support/Capsule` on
macOS, `%LOCALAPPDATA%\Capsule` on Windows). Set `CAPSULE_DATA_DIR` to keep it somewhere
else. Uninstalling Capsule never deletes it.

## Everyday use

```bash
uv run capsule serve                 # start the local backend the extension talks to
uv run capsule ingest scan.pdf --url <page it came from> [--gating gated]
                                     # archive a PDF/image you already downloaded
uv run capsule note <capture_id> "why I kept this"
uv run capsule query --url <url>     # has this page been captured already?
uv run capsule supersede <old_capture_id> --by <new_capture_id>
                                     # mark a capture as replaced by a better one
uv run capsule index                 # rebuild the collection page
uv run capsule transcribe <capture_id>
                                     # AI reading of a handwritten scan (a reading aid,
                                     # never evidence; about $0.60 per two-page certificate)
uv run capsule --help                # everything else
```

**Keeping the backend running on Windows:** `scripts\capsule-autostart.ps1 install`
registers a logon task (no admin rights); see the script's header for `status`, `restart`
and `uninstall`.

## Getting help

- Something went wrong? Open an **[issue](../../issues)** and include what you clicked, the
  message you saw, and the version shown at `http://127.0.0.1:8765/health` while Capsule is
  running. Please do not paste record content from subscription sites.
- Questions and ideas: **[Discussions](../../discussions)**.

## For developers

```bash
uv sync
uv run pytest -q          # ~1,000 tests; the extension's logic is tested in node
uv run ruff check capsule tests
```

The design is in [`docs/plan.md`](docs/plan.md). The on-disk formats are versioned
contracts: [`capture-folder`](docs/specs/capture-folder-v2.md),
[`captures-index`](docs/specs/captures-index-v2.md) and
[`capsule-db-v1`](docs/specs/capsule-db-v1.md). Numbered requirements and bug reports, each
with its fix and evidence, are in [`docs/specs/`](docs/specs/) and [`docs/bugs/`](docs/bugs/).
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).
