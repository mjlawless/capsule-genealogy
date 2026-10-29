# Chrome Web Store listing — Genealogy Capsule

Everything the developer dashboard asks for, ready to paste. Build the upload with
`release/package-extension.sh capsule-store-<version>.zip` (it removes the manifest `key`, which
the store does not accept).

**Visibility for the first submission: Unlisted** (installable by link only), so evaluators get a
one-click install before a public listing.

## Store listing

**Name:** Genealogy Capsule (taken from the manifest `name`; the store shows it as is)

**Summary (132 characters max):**
Save the record page you're viewing as cited, hashed evidence on your own computer: page, record
image and citation.

**Category:** Productivity → Tools

**Description:**

Name no third-party sites in the description, not even in a disclaimer: 0.2.5 was rejected
2026-10-24 ("Spam and Placement in the Store", ref. Yellow Argon) for "excessive keywords", which
was the one-line list of eight supported sites. Point at SITES.md instead.

The "About the warnings" section must match the permission dialog of the version on the store
(REQ-013 §E). The text below is for 0.2.7; rewrite it when the permissions change.

Genealogy Capsule turns the genealogy record you are looking at into evidence you keep.

CAPSULE HAS TWO PARTS. This extension is one. The other is the free Capsule app, which keeps
your archive on your own computer. Install both:
1. The Capsule app (Windows, macOS, Linux; one download, no account):
   https://github.com/mjlawless/capsule-genealogy#install-macos-or-windows
2. This extension: click "Add to Chrome" above.
The extension finds the app by itself the first time you click its icon.

Click Capture and it saves, on your own computer:

• the full page as you saw it, with its pictures and layout, and a PDF of it (ads hidden;
  the capture message says if either could not be made)
• the record image, from the site's own download button where there is one
• an Evidence Explained-style citation, with the date you viewed it
• fingerprints (SHA-256 hashes) of everything, so you can show later that nothing changed

With your own Anthropic API key (optional), Capsule also reads the record: every person, date and
place, each linked to the exact words it came from and marked stated, calculated or estimated.

It works on the major genealogy record sites, archive image viewers and ordinary web pages. The
list of supported sites is at https://github.com/mjlawless/capsule-genealogy/blob/main/SITES.md

ABOUT THE WARNINGS CHROME SHOWS WHEN YOU ADD CAPSULE

Chrome describes every extension's permissions in broad, standard words that it chooses, not
the developer. Here is what each one means for Capsule:

• "Read and change all your data on all websites" and "Access the page debugger backend":
  Capsule saves a PDF and a full copy of the page you capture with Chrome's own print and save
  tools, and Chrome only lets an extension use those through its debugger, which it describes
  this broadly. Capsule uses it on one tab, only when you click Capture, hides the page's ads
  for that moment, then puts the page back and lets go as soon as the copies are made.
  Capsule never changes your data on any site.
• Later, only if you choose: turning on "Capsule button on every page, and keep site
  downloads" in Capsule's Settings makes Chrome ask for access to all sites (for the button)
  and to "Manage your downloads" (so the file you download with a record site's own Download
  button is kept as the record image). Capsule watches for downloads only during the few
  seconds after you start a capture. Where Capsule offers a one-click image capture, starting
  it has Capsule press the site's own Download button for that one record; otherwise it never
  starts a download. Capturing from a site may also make Chrome ask about that one site.
• "Proceed with caution: this extension is not trusted by Enhanced Safe Browsing": you see this
  only if you have turned on Chrome's Enhanced Safe Browsing. It reflects how long the developer
  has been publishing on the Chrome Web Store, not anything the extension does. Google treats
  new developers this way for their first few months. Capsule is open source, so anyone can read
  exactly what it does: https://github.com/mjlawless/capsule-genealogy

WHAT STAYS PRIVATE

• What the extension captures goes only to the Capsule app on your own computer. There are no
  accounts and no Capsule servers. If you add your own AI key, the pages you choose to have read
  are sent to Anthropic for reading, and nowhere else.
• It reads only the page you choose to capture, when you click. No crawling, no background
  downloads.
• Pages from subscription sites stay private: archived only on your computer, never published.
• Open source (MIT).

Genealogy Capsule is independent and not affiliated with any of the sites it works on.

**Homepage URL:** https://github.com/mjlawless/capsule-genealogy
**Support URL:** https://github.com/mjlawless/capsule-genealogy/issues
**Privacy policy URL:** https://github.com/mjlawless/capsule-genealogy/blob/main/PRIVACY.md

## Privacy practices tab

**Single purpose:**
Capture the web page the user is viewing (its content, a PDF render, a full copy of the page and
the record image the user downloads) and save it as cited, hashed evidence in the Capsule program
on the user's own computer.

**Permission justifications:**

- **debugger:** Used only when the user clicks Capture, on that tab, to print the page to PDF
  (Page.printToPDF) and save a full copy of it as MHTML (Page.captureSnapshot) with the Chrome
  DevTools Protocol, from what the tab has already loaded. Ads are hidden for that moment and the
  page is restored. These are the archival pictures of the page as the user saw it. It is
  detached immediately after.
- **downloads (optional, requested only when the user turns it on in Settings):** While a capture
  is waiting for it, the extension recognizes the record image the user downloads with the
  website's own Download button and attaches that file to the capture. It only looks at downloads
  during that short, user-started window. Where the extension offers a one-click image capture,
  starting it has the extension press the website's own Download button for that one record;
  otherwise it never starts downloads.
- **scripting:** Injects the capture code into the tab the user chose to capture, to read the page
  content.
- **activeTab:** Gives access to the tab the user is capturing, on the user's click.
- **storage:** Remembers the local pairing with the Capsule program and the user's preferences.
- **Optional host permission for all sites (requested only when the user turns on the page
  button in Settings):** Shows the small Capsule button on pages and receives the capture command.
  It reads a page only when the user clicks Capture.
- **Host permission for http://127.0.0.1:8765:** The extension sends the captured page to the
  Capsule program running on the user's own computer, and to nothing else.
- **Optional host permissions (requested per site at capture time):** To fetch a record file (for
  example a PDF certificate) that the page the user is capturing links to.

**Remote code:** No. All code is in the package.

**Data usage (check these):** the store counts data as collected even when it goes only to the
local Capsule program, so disclose generously.
- **Website content:** page text, a PDF and a full copy (MHTML) of the pages the user captures,
  and their images.
- **Web history:** each capture records the page's address, title and the date viewed (the
  citation needs them).
- **Authentication information:** the user's own Anthropic API key, if they enter it in the
  popup. It goes only to the local Capsule program, which stores it in the OS keychain.
- Not checked: personally identifiable information, health, financial, personal communications,
  location, user activity.
- Nothing goes to the developer or any third party from the extension. Tick all three
  certifications (not sold; not used for unrelated purposes; not used for creditworthiness or
  lending).

**Certify:** the disclosures above are accurate.

## Graphics

- **Store icon (128×128):** `docs/web-store/store-icon-128.png`, the extension icon at 96×96
  with the 16px transparent margin the store's image guidelines ask for. (The package's own
  `extension/icons/icon-128.png` fills the square, which is right for the toolbar.)
- **Screenshots (1280×800 or 640×400, at least 1):** take them on a PUBLIC record page, never on a
  subscription site (the screenshots are published):
  1. a record page with the Capsule popup open, showing **Capture this page**;
  2. the same page after a capture, with the green "Captured …" message;
  3. the collection page at http://127.0.0.1:8765/ showing that capture.
- **Small promo tile (440×280):** optional for an unlisted item.

## Store item ID

The store assigned **`dopmkpbopjegbdngekbdpgpkmpgkgeeo`**. It is in the backend's pairing
allow-list since 0.2.1 (`capsule/config.py`, `CAPSULE_STORE_EXTENSION_ID`), so a store install
pairs by itself like the unpacked one. The ID is fixed for the life of the item; nothing needs
changing on later uploads.
