// Capsule: what the popup's first-run screen and the welcome tab both need (REQ-013 §A).
// Loaded by popup.html and welcome.html before their own script; plain globals, no modules,
// so node tests can still run each PURE function from this file's source.

// The same host and port as popup.js and background.js's BACKEND.
const APP_URL = "http://127.0.0.1:8765";
const HEALTH_URL = APP_URL + "/health";
const PAIR_URL = APP_URL + "/pair";
const REPO_URL = "https://github.com/mjlawless/capsule-genealogy";
const INSTALL_DOCS_URL = REPO_URL + "#install-macos-or-windows";
// REQ-013: the Chrome Web Store copy of the extension. Its ID is CAPSULE_STORE_EXTENSION_ID in
// capsule/config.py, which the app pairs with.
const STORE_EXTENSION_ID = "dopmkpbopjegbdngekbdpgpkmpgkgeeo";
const STORE_URL = "https://chromewebstore.google.com/detail/genealogy-capsule/" + STORE_EXTENSION_ID;
// Release assets built by installers/package-installers.sh: the names are a contract with it.
const SETUP_DOWNLOADS = {
  win: REPO_URL + "/releases/latest/download/Install-Capsule.cmd",
  mac: REPO_URL + "/releases/latest/download/Install-Capsule-mac.zip",
};
// The README's one-line installs, for people who prefer a terminal (and the only way on Linux).
const SETUP_COMMANDS = {
  unix: "curl -LsSf https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.sh | sh",
  win:
    'powershell -ExecutionPolicy ByPass -c "irm ' +
    'https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.ps1 | iex"',
};
// Set once this extension has reached the app: after that, no answer means "stopped", not
// "never installed".
const SEEN_APP_KEY = "capsuleSeenApp";

// PURE (tested in node). What the first-run screen says, given chrome.runtime.getPlatformInfo()'s
// `os` and whether this extension has ever reached the app.
function setupScreen(os, seenApp) {
  if (os !== "mac" && os !== "win" && os !== "linux") {
    return {
      title: "Capsule needs a computer",
      body:
        "Capsule saves pages into a free app on your own computer, and that app runs on " +
        "Windows, macOS and Linux. It can't be installed on this device.",
      download: "",
      steps: "",
      where: "",
      command: "",
    };
  }
  const download = SETUP_DOWNLOADS[os] || "";
  const steps = {
    win:
      "Open the downloaded Install-Capsule.cmd. Windows may say it protected your PC, " +
      "because the installer isn't signed yet: click More info, then Run anyway. A window " +
      "shows the install, which takes a minute or two.",
    mac:
      "Open the downloaded Install-Capsule-mac.zip, then double-click Install Capsule. If " +
      "macOS says it can't verify it (the installer isn't signed yet), click Done, open " +
      "System Settings › Privacy & Security, scroll down, click Open Anyway and confirm. A " +
      "Terminal window shows the install, which takes a minute or two.",
    linux: "",
  }[os];
  const where = {
    win: "Open PowerShell (Start menu › PowerShell), paste this and press Enter:",
    mac: "Open Terminal (Applications › Utilities), paste this and press Return:",
    linux: "Open a terminal, paste this and press Enter:",
  }[os];
  const command = os === "win" ? SETUP_COMMANDS.win : SETUP_COMMANDS.unix;
  if (seenApp) {
    return {
      title: "Genealogy Capsule isn't running",
      body:
        "The Capsule app on this computer isn't answering. It starts by itself when you log " +
        "in, so restarting your computer usually fixes this. Or run the installer again: it " +
        "starts Capsule and keeps your archive.",
      download,
      steps,
      where,
      command,
    };
  }
  return {
    title: "One more step: install the Genealogy Capsule app",
    body:
      "This extension saves pages into Genealogy Capsule, a free app on your own computer, so " +
      "your archive never leaves it. Install it once:",
    download,
    steps,
    where,
    command,
  };
}

// The app's /health, or null when nothing answers. Capped, so a hung listener counts as "not
// running" rather than a popup that never finishes opening.
async function fetchHealth() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 2500);
  try {
    const resp = await fetch(HEALTH_URL, { signal: ctrl.signal });
    if (!resp.ok) return null;
    const body = await resp.json();
    return body && body.status === "ok" ? body : null;
  } catch (_e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// v0.2 — the extension and the Capsule app are released together and should match. Chrome can
// update one without the other, so say which one is behind, and how to fix it, in plain words.
// PURE (tested in node): returns null when they match or a version cannot be read.
// REQ-013 §F: `fromStore` is true for the Chrome Web Store copy, which Chrome updates by itself;
// only a copy loaded from a folder needs the reload arrow.
function versionAdvice(appVersion, extensionVersion, fromStore) {
  const parse = (v) => {
    const m = /^(\d+)\.(\d+)\.(\d+)/.exec(String(v || ""));
    return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
  };
  const app = parse(appVersion);
  const ext = parse(extensionVersion);
  if (!app || !ext) return null;
  const cmp = app[0] - ext[0] || app[1] - ext[1] || app[2] - ext[2];
  if (cmp === 0) return null;
  const a = app.join(".");
  const e = ext.join(".");
  if (cmp < 0) {
    return (
      "The Capsule app (" + a + ") is older than this extension (" + e + "). Update it by " +
      "running the Capsule installer again; your archive is kept."
    );
  }
  if (fromStore) {
    return (
      "This extension (" + e + ") is older than the Capsule app (" + a + "). Chrome updates it " +
      "by itself, usually within a day. To update now: open chrome://extensions, turn on " +
      "Developer mode (top right) and click Update."
    );
  }
  return (
    "This extension (" + e + ") is older than the Capsule app (" + a + "). Update it: open " +
    "chrome://extensions and click the reload arrow on Capsule (or load the new extension folder)."
  );
}
