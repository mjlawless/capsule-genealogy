# Genealogy Capsule

**Evidence-grade genealogy capture.** A Chrome extension plus a small program on your own
computer that turn the record page you are looking at into an archived, cited, hashed source
that you keep: the page, the record image, what the record says, and an Evidence Explained
citation.

> **Status: v0.2.1, early access.** Capsule has been used daily on real research (hundreds
> of captures from Irish parish registers, NYC vital records, Ancestry, Archion, Riksarkivet,
> Newspapers.com and more). Installing it takes one command and a few clicks in Chrome. You can
> try it **without an AI key**; add one later to have records read. A Chrome Web Store listing
> and a no-terminal app are on the [roadmap](ROADMAP.md).

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

- **No AI key is needed to try it.** Without one, Capsule archives the page, its PDF, the record
  image and a citation, and marks the record "not read yet". With **your own Anthropic API key**
  it also reads the record: every person, date and place, each linked to the words it came from.
  Archived pages can be read later; nothing needs capturing again. A key is billed to you by
  Anthropic: get one at [console.anthropic.com](https://console.anthropic.com/) and set a monthly
  spending limit. A typical page uses about 11,000 input and 1,400 output tokens with the default
  model; see [anthropic.com/pricing](https://www.anthropic.com/pricing).
- **What leaves your computer:** with a key, the text of the page you capture is sent to
  Anthropic for reading, using your key. Nothing is sent to the authors of Capsule or anyone else.
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

You need about 10 minutes and Google Chrome.

1. **Install the Capsule app: download it and double-click.**
   - **Windows:** [Install-Capsule.cmd](https://github.com/mjlawless/capsule-genealogy/releases/latest/download/Install-Capsule.cmd).
     Windows may say it protected your PC (the installer isn't signed yet): click **More info**,
     then **Run anyway**.
   - **macOS:** [Install-Capsule-mac.zip](https://github.com/mjlawless/capsule-genealogy/releases/latest/download/Install-Capsule-mac.zip).
     Open it, then double-click **Install Capsule**. If macOS says it can't verify it, click
     **Done**, open **System Settings › Privacy & Security**, scroll down, click **Open Anyway**
     and confirm.

   A window shows the install; it takes a minute or two. The Capsule extension's popup offers
   the same download if you added the extension first.

   **Or with one command,** if you prefer a terminal:
   - macOS (or Linux): open *Terminal* and paste

     ```
     curl -LsSf https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.sh | sh
     ```

   - Windows: open *PowerShell* and paste

     ```
     powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.ps1 | iex"
     ```

   It installs everything Capsule needs, starts it (and sets it to start whenever you log in),
   and ends by showing you the folder of the Chrome extension.
2. **Add the extension to Chrome:** go to `chrome://extensions`, turn on **Developer mode**
   (top right), click **Load unpacked**, and choose the folder the installer showed you
   (`~/Capsule/app/extension` on macOS, `%LOCALAPPDATA%\Capsule\app\extension` on Windows).
3. **Pin the Capsule icon** (puzzle-piece menu) and click it once. It connects to Capsule by
   itself.
4. **Capture something:** open a record page and click **Capture**. Your collection is at
   `http://127.0.0.1:8765/`.
5. **Optional: add your Anthropic API key** in the Capsule popup (*Anthropic API key*). It is
   checked, kept in your computer's own password store, and used from then on. Then click
   **Read them now** to read the pages you archived before.

Your archive is kept in your user data folder (`~/Library/Application Support/Capsule` on
macOS, `%LOCALAPPDATA%\Capsule` on Windows), not in the program folder, so updating or
reinstalling never touches it. To update, run the same install command again.

<details><summary>Prefer to install by hand?</summary>

Install [`uv`](https://docs.astral.sh/uv/), download this repository (**Code → Download ZIP**,
or `git clone https://github.com/mjlawless/capsule-genealogy.git`), and in its folder run
`uv run capsule setup`, then `uv run capsule serve`. Load the `extension` folder in Chrome as
above.
</details>

## Everyday use

```bash
uv run capsule serve                 # start the local backend the extension talks to
uv run capsule ingest scan.pdf --url <page it came from> [--gating gated]
                                     # archive a PDF/image you already downloaded
uv run capsule note <capture_id> "why I kept this"
uv run capsule query --url <url>     # has this page been captured already?
uv run capsule extract --all          # read pages archived before you added a key
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

## Where it's heading

See the [roadmap](ROADMAP.md): next up is reviewing and correcting what Capsule read, and a
Chrome Web Store listing.

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
