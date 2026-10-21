# Chrome Web Store listing — Capsule

Everything the developer dashboard asks for, ready to paste. Build the upload with
`release/package-extension.sh capsule-store-<version>.zip` (it removes the manifest `key`, which
the store does not accept).

**Visibility for the first submission: Unlisted** (installable by link only), so evaluators get a
one-click install before a public listing.

## Store listing

**Name:** Capsule — genealogy evidence capture

**Summary (132 characters max):**
Save the record page you're viewing as cited, hashed evidence on your own computer: page, record
image and citation.

**Category:** Productivity (or "Tools")

**Description:**

Capsule turns the genealogy record you are looking at into evidence you keep. Click Capture and
it saves, on your own computer:

• the page exactly as you saw it, and a PDF of it
• the record image, from the site's own download button where there is one
• an Evidence Explained-style citation, with the date you viewed it
• fingerprints (SHA-256 hashes) of everything, so you can show later that nothing changed

With your own Anthropic API key (optional), Capsule also reads the record: every person, date and
place, each linked to the exact words it came from and marked stated, calculated or estimated.

It works on Ancestry, FamilySearch, Find a Grave, Newspapers.com, Archion, the National Library
of Ireland, Riksarkivet, NYC vital records and ordinary web pages.

Capsule needs its free companion program running on your computer (one-line install, macOS and
Windows): https://github.com/mjlawless/capsule-genealogy

• Your captures never leave your computer. There are no accounts and no servers.
• It reads only the page you choose to capture, when you click. No crawling, no background
  downloads.
• Pages from subscription sites stay private: archived only on your computer, never published.
• Open source (MIT).

Capsule is independent and not affiliated with Ancestry, FamilySearch or any other site.

**Homepage URL:** https://github.com/mjlawless/capsule-genealogy
**Support URL:** https://github.com/mjlawless/capsule-genealogy/issues
**Privacy policy URL:** https://github.com/mjlawless/capsule-genealogy/blob/main/PRIVACY.md

## Privacy practices tab

**Single purpose:**
Capture the web page the user is viewing (its content, a PDF render and the record image the user
downloads) and save it as cited, hashed evidence in the Capsule program on the user's own
computer.

**Permission justifications:**

- **debugger:** Used only when the user clicks Capture, on that tab, to print the page to PDF with
  the Chrome DevTools Protocol (Page.printToPDF). The PDF is the archival picture of the page as
  the user saw it. It is detached immediately after.
- **downloads:** While a capture is waiting for it, the extension recognizes the record image the
  user downloads with the website's own Download button and attaches that file to the capture. It
  only looks at downloads during that short, user-started window and never starts downloads.
- **scripting:** Injects the capture code into the tab the user chose to capture, to read the page
  content.
- **activeTab:** Gives access to the tab the user is capturing, on the user's click.
- **storage:** Remembers the local pairing with the Capsule program and the user's preferences.
- **Host permission (content script on all URLs):** Shows the small Capsule button on pages and
  receives the capture command. It reads a page only when the user clicks Capture.
- **Host permission for http://127.0.0.1:8765:** The extension sends the captured page to the
  Capsule program running on the user's own computer, and to nothing else.
- **Optional host permissions (requested per site at capture time):** To fetch a record file (for
  example a PDF certificate) that the page the user is capturing links to.

**Remote code:** No. All code is in the package.

**Data usage (check these):**
- Website content: collected, sent ONLY to the user's own computer (the local Capsule program).
  Not sent to the developer or any third party by the extension.
- Not sold, not used for unrelated purposes, not used for creditworthiness or lending.

**Certify:** the disclosures above are accurate.

## Graphics

- **Icon:** `extension/icons/icon-128.png` (already in the package).
- **Screenshots (1280×800 or 640×400, at least 1):** take them on a PUBLIC record page, never on a
  subscription site (the screenshots are published):
  1. a record page with the Capsule popup open, showing **Capture this page**;
  2. the same page after a capture, with the green "Captured …" message;
  3. the collection page at http://127.0.0.1:8765/ showing that capture.
- **Small promo tile (440×280):** optional for an unlisted item.

## After the first upload

The store assigns the extension its own ID (shown in the dashboard). Add it to the backend's
pairing allow-list (`capsule/config.py`, `pairable_extension_ids`) and release, so the store build
pairs automatically like the unpacked one.
