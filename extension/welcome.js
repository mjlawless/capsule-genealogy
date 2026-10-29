// Capsule welcome tab (REQ-013 §A). Opened once by background.js when the extension is
// installed. Uses setup-shared.js (loaded first): setupScreen, fetchHealth, versionAdvice and
// the installer constants, so it and the popup's first-run screen can never disagree.

// PURE (tested in node). What step 2 shows, given chrome.runtime.getPlatformInfo()'s `os`, the
// app's /health (null when nothing answers), this extension's version, and whether it is the
// Web Store copy. `appRunning` turns step 2 green; `banner` is the version advice, if any.
function welcomeView(os, health, extensionVersion, fromStore) {
  if (health) {
    return {
      appRunning: true,
      banner: versionAdvice(health.version, extensionVersion, fromStore),
      setup: null,
    };
  }
  // A brand-new install has never reached the app, so always the "install it" wording.
  return { appRunning: false, banner: null, setup: setupScreen(os, false) };
}

const POLL_MS = 2000;

function byId(id) {
  return document.getElementById(id);
}

function showInstall(s) {
  byId("app-title").textContent = s.download || s.command ? "Step 2: install the Capsule app" : s.title;
  byId("setup-body").textContent = s.body;

  const download = byId("setup-download");
  download.parentElement.hidden = !s.download;
  if (s.download) {
    // A plain link: the release asset is served as an attachment, so this page stays open
    // and turns green by itself when the app starts.
    download.href = s.download;
    download.textContent = "Download the Capsule installer";
  }
  byId("setup-steps").textContent = s.steps;
  byId("setup-steps").hidden = !s.steps;

  const box = byId("setup-command-box");
  box.hidden = !s.command;
  box.open = !!s.command && !s.download; // Linux: the command IS the installer
  if (!s.download) byId("setup-command-summary").textContent = "Install with one command";
  byId("setup-where").textContent = s.where;
  byId("setup-command").textContent = s.command;
  byId("setup-docs").href = INSTALL_DOCS_URL;
  byId("setup-wait").hidden = !s.command;
}

function showRunning(banner) {
  const step = byId("step-app");
  step.classList.remove("current");
  step.classList.add("done");
  byId("app-mark").textContent = "✔";
  byId("app-title").textContent = "Step 2: install the Capsule app";
  byId("app-install").hidden = true;
  byId("app-done").hidden = false;
  byId("step-use").classList.add("current");
  byId("lead-next").textContent = "Both are in place. You're ready to capture.";
  const el = byId("version-banner");
  el.textContent = banner || "";
  el.hidden = !banner;
}

async function start() {
  let os = "";
  try {
    os = (await chrome.runtime.getPlatformInfo()).os;
  } catch (_e) {
    // unknown platform: setupScreen says the app can't run here
  }
  const version = chrome.runtime.getManifest().version;
  const fromStore = chrome.runtime.id === STORE_EXTENSION_ID;

  byId("setup-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(byId("setup-command").textContent);
      byId("setup-copy").textContent = "Copied ✓";
    } catch (_e) {
      byId("setup-copy").textContent = "Select the text above and copy it";
    }
  });

  let installShown = false;
  const check = async () => {
    const view = welcomeView(os, await fetchHealth(), version, fromStore);
    if (view.appRunning) {
      showRunning(view.banner);
      return; // nothing more to wait for
    }
    if (!installShown) {
      showInstall(view.setup);
      installShown = true;
    }
    if (view.setup.command) setTimeout(check, POLL_MS); // else: no app for this device
  };
  await check();
}

start();
