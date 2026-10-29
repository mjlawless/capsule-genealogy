# REQ-013 — one guided install, and an install dialog that does not frighten people

**Status:** 🟡 **§A, §B, §C, §E-draft and §F BUILT 2026-10-28** (unreleased, target v0.2.7).
**§D MEASURED AND REJECTED 2026-10-28: `debugger` stays** (results under §D). Approved 2026-10-28
(owner rulings below).

> ## Implementation (2026-10-28)
>
> - **§A** `extension/welcome.html` + `welcome.js`, opened by `runtime.onInstalled`
>   (`reason === "install"` only). The popup's first-run code (installer constants,
>   `setupScreen`, `fetchHealth`, `versionAdvice`) moved unchanged into
>   `extension/setup-shared.js`, which both pages load first. The welcome page does not pair:
>   the popup and the first capture already pair by themselves.
> - **§B** `install.sh` / `install.ps1` end with the store URL and open it on a first install
>   only (no previous app folder), never under `CI` or `CAPSULE_NO_BROWSER=1`; Load unpacked is a
>   one-line fallback. `capsule setup`, the README, ROADMAP and the unofficial-copy message lead
>   with the store. `STORE_URL` lives in `capsule/config.py`; a test keeps the copies equal.
> - **§C** No declared content script. `background.js` registers `media-detect.js` + `content.js`
>   (`scripting.registerContentScripts`, id `capsule-page`) for every granted web origin except
>   the app's own, at worker start and on `permissions.onAdded` / `onRemoved`, serialized.
>   `downloads` is optional; `watchDownloads` attaches once the API exists; arming without it
>   says how to turn it on. **Popup › Settings › "Capsule button on every page, and keep site
>   downloads"** requests downloads + all web origins in one question, plus `file:///*` (needed to
>   read a finished download back) only when "Allow access to file URLs" is already on, because
>   Chrome refuses the whole request otherwise; when it is off, the popup says where to turn it
>   on. **Deviation:** `downloads` shares the page-button opt-in instead of a separate prompt
>   at first use: only page-script flows ever arm a download, and a content script cannot call
>   `permissions.request`.
> - **Withdrawal:** requests from page scripts (`capsule:capture`, `capsule:arm-download`,
>   `capsule:capture-ancestry-image`) are checked against the grant at that moment
>   (`pageStillAllowed`), because a tab already open keeps its injected script. **Known limit:**
>   such a tab still shows the button until it navigates; clicking it is refused in words.
> - **§F** `versionAdvice(app, ext, fromStore)`: the store copy is told Chrome updates it itself.
> - Codex adversarial review, 4 rounds: lost `file://` access for reading downloads (fixed);
>   a fresh install could not enable the setting because of `file:///*` (fixed); a withdrawn site
>   could still capture from an open tab (fixed); round 4 approved.
> - **Owner's own copy:** turn the setting on once, and turn on "Allow access to file URLs"
>   (already required for site downloads since REQ-001).

## Why

The Chrome Web Store approved 0.2.6 on 2026-10-28. Installing from the store, the owner hit three
problems a non-technical genealogist would also hit:

1. **Nothing says there is a second half to install.** The store description mentions the Capsule
   app once, in its fourth paragraph. The popup's first-run screen (v0.2.1) offers the installer,
   but only to someone who clicks the toolbar icon. The extension has no `runtime.onInstalled`
   handler, so a new user is left on the store page with no next step.
2. **The permission dialog is alarming:** "Access the page debugger backend", "Read and change
   all your data on all websites", "Manage your downloads".
3. **"Proceed with caution: this extension is not trusted by Enhanced Safe Browsing."** Chrome
   shows this to users with Enhanced Safe Browsing on, for developers who are new to the store.
   Google says new developers usually need "a few months to become trusted". No code change
   removes it; only time and a clean policy record do.

Meanwhile the installers, the README and `capsule setup` still tell people to turn on Developer
mode and use Load unpacked.

## Where each warning line comes from

From Chrome's permissions list (developer.chrome.com, checked 2026-10-28):

| Dialog line | Source in 0.2.6 | Can it be asked for later? |
|---|---|---|
| Access the page debugger backend | `debugger` (`render-pdf.js`, `Page.printToPDF`) | **No.** `debugger` is on Chrome's list of permissions that cannot be optional. |
| Read and change all your data on all websites | `debugger` **and** the `<all_urls>` content script (`media-detect.js`, `content.js`) | Only if both go |
| Manage your downloads | `downloads` (`download-intercept.js`) | Yes |

`optional_host_permissions` (`https://*/*`, `http://*/*`) already produce no install warning.

## Rulings (owner, 2026-10-28)

- Start 0.2.7 with §A, §B, §C, §E and §F, plus the unreleased fixes (BUG-025, BUG-026, BUG-027,
  the re-read fix) and BUG-028.
- **§D, the page PDF: measure, then switch.** Change nothing until a corpus comparison shows the
  new PDF is as faithful as today's.
- **§E (the owner's addition):** the store listing explains why each warning appears and why it
  is needed, so users are neither scared nor confused.
- Signed installers: **deferred** (not declined).

## §A — a welcome tab when the extension is installed

`runtime.onInstalled` with `reason === "install"` opens a page bundled with the extension
(`welcome.html`). It does not open on update.

- It leads with **"Capsule has two parts. You've added the first."**, then **Step 2: install the
  Capsule app**, with one large button for the visitor's system (the v0.2.1 popup's
  `INSTALLERS`: `Install-Capsule.cmd`, `Install-Capsule-mac.zip`; the one-line command for Linux,
  as the popup does now).
- It polls `GET http://127.0.0.1:8765/health` (already allowed by `host_permissions`). When the app
  answers, it shows **✔ Connected**, then **Step 3: pin the icon and capture a page**. If pairing
  needs the popup (it pairs itself on first click), say exactly that.
- It shares the popup's installer constants rather than copying them: one module both load.
- The page is static: no remote code, nothing fetched but `/health`.

## §B — every install path ends at the store

- `install.sh`, `install.ps1` and `capsule setup` end by **opening the store page** in the default
  browser, with the URL printed as a fallback. "Load unpacked" stays as a short fallback line for
  people who cannot use the store, not as the main step.
- README "Install" step 2 becomes "Add Genealogy Capsule from the Chrome Web Store" (link). The
  status line and ROADMAP stop listing the store as future work.
- `popup.js`'s message for an unofficial copy (around line 566) leads with the store link.
- Starting from either half hands the user to the other: the installer opens the store, and the
  store install opens the welcome tab (§A).

## §C — permissions asked for when they are used

1. **`downloads` → `optional_permissions`.** Request it (from the user's click) the first time a
   capture would wait for the site's own download. If it is refused, the capture still saves the
   page and says what it did not get, as a refused site grant does today.
2. **The every-page content script becomes opt-in.** Remove the static `content_scripts` entry.
   - **The popup's Capture** keeps working on any page through `activeTab` + `scripting`: inject
     `media-detect.js` and `content.js` on click if they are not already there.
   - **Sites the user has granted** (the popup already asks for site access where a record image
     must be fetched) get the content script through `scripting.registerContentScripts`, kept in
     step with `permissions.onAdded` / `onRemoved` and re-checked at service-worker start.
   - **A setting, "Show the Capsule button on every page"**, requests `https://*/*` + `http://*/*`
     and registers the scripts everywhere. **The owner's own install must turn it on:** the
     research session drives captures with the on-page button and the `capsule:capture` /
     `capsule:arm-download` events, and the Ancestry auto-download (`background.js`'s
     `tabs.sendMessage` to the viewer tab) needs the script in that tab.
   - Audit every `executeScript` and `tabs.sendMessage` target (`popup.js`,
     `download-intercept.js`, `background.js`): each tab must be covered by `activeTab` or a
     granted origin, or it gets a clear message, never a silent failure.
3. Removing warnings in an update does not disable existing installs (only added warnings do).

With §C alone, the dialog still shows the two `debugger` lines. Only §D removes them.

## §D — the page PDF made by the Capsule app (measure first)

**Proposal:** the extension stops using `debugger`. The app renders `page.pdf` from the saved
`snapshot.html` with the user's own Chrome, run headless (`--headless --print-to-pdf`), located
the way the installers already locate it.

**Gate: a measurement, shown to the owner before anything changes.**
- Sample ≥ 30 captures that have both `snapshot.html` and today's page PDF, spread across sites
  and page kinds (index/transcription pages, image viewers, newspapers, plain pages, gated and
  public).
- Re-render each from its snapshot **offline only**: network blocked, a throwaway profile.
  Rendering with the network on would make Chrome fetch from record sites in the background,
  which the compliance posture (plan §11) forbids, and the real renderer must run offline too.
  Compare page count, the text layer (diff), and a rendered image of page 1 side by side.
- Report what is lost: missing images that needed the logged-in session, lazy-loaded content,
  layout. **Fidelity is an evidence property:** a silent downgrade is not acceptable.
- Existing captures are never re-rendered (append-only); this concerns new captures only.

If the measurement fails, `debugger` stays and §E explains it. If it passes, 0.2.7 or a later
release drops `debugger`, and the dialog then shows only the localhost line (if any).

**Watch:** the snapshot is hashed as captured. A PDF rendered later from it is a derivative and
its manifest provenance must say so (`render:snapshot-pdf`, distinct from today's
`render:page-pdf`), so a reader can tell the two apart.

### §D result (2026-10-28): not viable, `debugger` stays

56 comparable captures across 12 hosts (60 sampled from 450 eligible; 4 excluded because their
own capture-time PDF is blank), each re-rendered from its snapshot with the network blocked, four
ways (scripts off, scripts on, scripts off with record images pointed at the capture's saved
media, and the command-line `--print-to-pdf`). **0 of 56 faithful in any variant.**

- **Record image lost in 23 of 23** captures that had one (remote images, a PDF viewer, canvas).
  In 12 of those the page PDF is the only copy of that image in the capture.
- **Layout lost in 53:** 439 of 450 snapshots link outside stylesheets; none are inlined. Only
  about 1% of the snapshots' images are stored inline.
- **Text the page never showed prints in 44** (hidden menus, dialogs, banners): for evidence,
  worse than an omission.
- **Capsule's own UI prints in all 56:** the on-page button and the "Capturing…" toast are saved
  in the snapshots (BUG-029).
- **Compliance:** with the network blocked nothing was received, but scripts-off renders still
  *attempted* the record sites, and Chrome tried Google's own servers. A real renderer would have
  to strip every remote reference first. The command-line print produces no PDF with scripts off.

Making it viable would need the extension to inline stylesheets, images, canvases and embedded
PDFs into the snapshot at capture time (a SingleFile-style rewrite), with likely blockers:
cross-origin stylesheet rules and tainted canvases cannot be read, and re-fetching would be a
new request to the provider. Not pursued. `chrome.pageCapture` (MHTML) is unmeasured; check its
own install warning before measuring. The measurement's report holds renders of gated pages, so
it stays local and unpublished.

**Consequence for §E:** the 0.2.7 dialog still shows the two `debugger` lines; only "Manage your
downloads" goes. The listing text must be revised for 0.2.7 at release: drop the downloads bullet,
and drop the claim that the all-sites warning covers the on-page button (it is opt-in now).

## §E — the store listing explains the warnings

The description gains, near the top, **"Two parts"** (the app and this extension, with the
install link), and a short section **"About the warnings Chrome shows"** that explains each line
in plain words: what Capsule uses it for, when, and that what it captures goes only to the Capsule
app on the user's own computer. It also explains "Proceed with caution": shown only with Enhanced
Safe Browsing, about the developer's time on the store, not about what the extension does, and
that the code is open for anyone to read.

The text must match the dialog of the version on the store, so it is rewritten whenever §C/§D
change the permissions. It names no third-party sites (the 0.2.5 "excessive keywords"
rejection). The same explanation appears on the welcome tab (§A), for people who never read the
listing. Draft: `docs/web-store/listing.md`.

## §F — warn when the extension and the app drift apart

The store updates the extension by itself; the app updates only when the installer runs again.
The popup and welcome tab compare the extension's version with `/health`'s and, when the app is
older than the extension expects, say "Update the Capsule app" with the installer link. Define the
oldest app version each extension version works with, in one place, and test it.

## Out of scope

- Signed installers and a no-terminal app (deferred by the owner).
- Adding the store extension from the installer through Chrome's external-extension mechanism.
  On Windows the documented location needs administrator rights, which the per-user installers do
  not have. Chrome then asks the user to enable the extension on its next start, and never re-adds
  one the user removed. Revisit with signed installers, after seeing the real prompt.

## Acceptance

- A clean Chrome profile, installing from the store: the welcome tab opens, names step 2, offers
  the right installer, and turns ✔ when the app is running, without the user opening the popup.
- Running an installer ends with the store page open.
- With §C, the install dialog no longer shows "Manage your downloads". With §D too, it no longer
  shows either `debugger` line. Check the dialog on a packed build, not by reading the manifest.
- The owner's research flows (on-page button, `capsule:capture`, `capsule:arm-download`, Ancestry
  auto-download) work after turning on "Show the Capsule button on every page".
- Tests: the welcome page's state machine, the version-skew rule, and the permission paths
  (granted, refused, revoked) in the node extension tests.
