// Capsule popup: grab the page in view and POST it to the local backend.
// No build step — plain MV3.
//
// The page HTML is the page in view (already loaded in the user's own browser session), so Capsule's
// backend fetches nothing. Embedded scans/PDFs are trickier: a genealogy viewer
// often loads them from a cross-origin, self-authorizing signed URL (e.g. an
// Azure blob SAS URL) into a native PDF plugin. A fetch from the *page* context
// is blocked by the page's CSP (connect-src) and the asset's missing CORS
// headers, and the plugin's bytes aren't JS-readable. So we fetch those bytes
// from the *extension* context instead — which isn't subject to the page's CSP
// and, with a host permission the user grants at capture time, isn't subject to
// CORS either. The signed URL carries its own auth, so no credential is touched.

const BACKEND = "http://127.0.0.1:8765/capture";
// The backend serves the browsable captures index at its root; derived from
// BACKEND so both point at the same host/port.
const INDEX_URL = BACKEND.replace(/\/capture$/, "/");
const TOKEN_KEY = "capsuleToken";
// Note drafts are keyed per URL: requesting host permission closes this popup
// (Chrome shows its own dialog), so a note typed before Capture would be lost on
// the re-open. We persist it as you type and restore it, then clear it once the
// capture for that URL succeeds.
const NOTE_DRAFT_PREFIX = "noteDraft:";

// The URL of the tab in view, resolved once on open — the key for this note draft.
let currentUrl = "";
let pairRefused = false;

async function loadNoteDraft(url) {
  if (!url) return "";
  const key = NOTE_DRAFT_PREFIX + url;
  const got = await chrome.storage.local.get(key);
  return got[key] || "";
}

async function saveNoteDraft(url, text) {
  if (!url) return;
  const key = NOTE_DRAFT_PREFIX + url;
  // Store the trimmed draft, or drop the key entirely when it is empty, so a
  // captured/cleared note never lingers to reappear on the next open.
  if (text && text.trim()) {
    await chrome.storage.local.set({ [key]: text });
  } else {
    await chrome.storage.local.remove(key);
  }
}

// After a successful capture the note belongs to that capture — clear both the
// visible field and the saved draft so it does not reappear on the next open.
async function clearNoteDraft() {
  const el = document.getElementById("note");
  if (el) el.value = "";
  await saveNoteDraft(currentUrl, "");
}

// Client-side caps (the backend re-checks authoritatively).
const MAX_MEDIA_BYTES = 25 * 1024 * 1024; // 25 MiB per file
const MAX_MEDIA_ITEMS = 8;

// Filled by the on-open probe so the Capture click can request the exact media
// hosts as its *first* action (Chrome requires permissions.request to run inside
// the user gesture, before any await). `isPdf`/`pdfUrl` are set when the tab in
// view IS a PDF (Chrome's built-in viewer) rather than an HTML page — then the
// capture archives the document itself, with no model call (extraction is M4).
let detected = { origins: [], count: 0, isPdf: false, pdfUrl: "" };

// A local file:// document can't use the gesture-first host grant a remote PDF
// does: Chrome gates file access behind a manual, per-extension toggle, not
// permissions.request. This is the one-line instruction shown when a local read
// is blocked (and proactively when a local PDF is detected).
const FILE_ACCESS_HINT =
  'To capture a local file, enable "Allow access to file URLs" for Capsule at ' +
  "chrome://extensions (open its Details, then toggle it on) and click Capture again.";

// The tab in view is a document (not an HTML page) when its URL is a direct PDF.
// We can't inject a content script into Chrome's PDF viewer to sniff the content
// type, and `tab.url` is the authoritative address the user navigated to — so the
// URL's extension is the signal. The fetched bytes' MIME is checked before send.
function looksLikePdf(url) {
  try {
    return new URL(url).pathname.toLowerCase().endsWith(".pdf");
  } catch (_e) {
    return false;
  }
}

function filenameFromUrl(url) {
  try {
    const last = new URL(url).pathname.split("/").filter(Boolean).pop();
    return last ? decodeURIComponent(last) : "document.pdf";
  } catch (_e) {
    return "document.pdf";
  }
}

async function loadToken() {
  const stored = await chrome.storage.local.get(TOKEN_KEY);
  if (stored[TOKEN_KEY]) return stored[TOKEN_KEY];
  // v0.2 — pair automatically: the local backend hands the token to this extension only
  // (it checks the browser-set Origin). Pasting it by hand still works as a fallback.
  try {
    const resp = await fetch(BACKEND.replace(/\/capture$/, "/pair"), { method: "POST" });
    if (!resp.ok) {
      // 403: the app is running but doesn't know this extension's ID (an app older than a
      // Web Store copy of the extension, say). Only updating the app fixes that.
      pairRefused = resp.status === 403;
      return "";
    }
    const body = await resp.json();
    if (!body || typeof body.token !== "string" || !body.token) return "";
    await saveToken(body.token);
    render("Paired with Capsule on this computer. You're ready to capture.");
    return body.token;
  } catch (_e) {
    return ""; // backend not running yet
  }
}

// v0.2 — a 401 means the saved token is stale (Capsule was reinstalled, or its archive moved).
// Forget it and pair again by itself; the user just clicks Capture once more.
async function repairAfter401() {
  await chrome.storage.local.remove(TOKEN_KEY);
  const fresh = await loadToken();
  if (fresh) document.getElementById("token").value = fresh;
  return fresh;
}

async function saveToken(token) {
  await chrome.storage.local.set({ [TOKEN_KEY]: token });
}

// Runs in the PAGE context (serialized by chrome.scripting). Collects the page
// snapshot and its embedded-media candidates. It does NOT fetch cross-origin
// media (the page can't) — it returns http(s) candidates as descriptors for the
// popup to fetch, and only fetches page-local blob:/data: bytes itself.
async function grabPage(grabMedia, withBytes, maxItems, maxBytes) {
  // Capsule's own key prompt (v0.2) is not part of the record: drop it before serializing.
  const prompt = document.getElementById("capsule-key-prompt");
  if (prompt) prompt.remove();
  const page = {
    url: location.href,
    html: withBytes ? document.documentElement.outerHTML : "",
    remote: [], // http(s) media -> { url, filename, mime } (bytes fetched by the popup)
    inline: [], // blob:/data: media -> { filename, mime, source_url, data_base64 }
    origins: [], // distinct http(s) origins, for the permission prompt
  };
  if (!grabMedia) return page;

  const fileNameFromUrl = (u) => {
    try {
      if (u.startsWith("data:")) return "inline";
      const last = new URL(u).pathname.split("/").filter(Boolean).pop();
      return last ? decodeURIComponent(last) : "media";
    } catch (_e) {
      return "media";
    }
  };

  const candidates = new Map(); // absolute URL -> { filename, hintMime }
  const add = (raw, hintMime) => {
    if (!raw) return;
    let abs;
    try {
      abs = new URL(raw, location.href).href;
    } catch (_e) {
      return;
    }
    if (!/^(https?:|blob:|data:)/.test(abs)) return;
    if (!candidates.has(abs)) candidates.set(abs, { filename: fileNameFromUrl(abs), hintMime });
  };

  for (const el of document.querySelectorAll("embed[src]")) {
    add(el.getAttribute("src"), el.type || "");
  }
  for (const el of document.querySelectorAll("object[data]")) {
    add(el.getAttribute("data"), el.type || "");
  }
  // An <iframe> is usually another web page, not a file: on Find a Grave, ad, tracking and
  // consent frames were archived as inert .bin files with a warning each, Capture asked for
  // access to the ad hosts, and the frames took item slots a scan needed. Take an http(s)
  // frame only when its path names a file Capsule archives or it declares a PDF (the rules
  // media-detect.js already applies). Those frames are documents, so they still come before
  // the images. blob:/data: frames are page-local and stay candidates.
  const FRAME_FILE = /\.(pdf|jpe?g|png|gif|tiff?|webp|jp2)$/i;
  for (const el of document.querySelectorAll("iframe[src]")) {
    const src = el.getAttribute("src");
    // A signed URL names no file and expires, so a declared PDF type counts too, and so does
    // what a same-site frame is showing. KNOWN LIMIT: an untyped frame from another site at
    // a URL naming no file is indistinguishable from an ad without fetching it, and fetching
    // is what asked for the ad hosts; it is skipped. Every frame the corpus ever archived
    // was an ad, consent or captcha page.
    const type = el.getAttribute("type") || "";
    let shown = "";
    try {
      shown = (el.contentDocument && el.contentDocument.contentType) || "";
    } catch (_e) {
      // another site's frame: not readable
    }
    let path = "";
    try {
      const u = new URL(src, location.href);
      path = /^https?:$/.test(u.protocol) ? u.pathname : "local.pdf";
    } catch (_e) {
      continue;
    }
    if (FRAME_FILE.test(path) || /application\/pdf/i.test(type)) {
      add(src, type);
    } else if (/^(application\/pdf|image\/)/i.test(shown)) {
      add(src, shown);
    }
  }
  for (const img of document.querySelectorAll("img")) {
    if ((img.naturalWidth || 0) >= 400 && (img.naturalHeight || 0) >= 400) {
      add(img.currentSrc || img.src, "");
    }
  }

  const readAsB64 = (blob) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(reader.error || new Error("read failed"));
      reader.onload = () => {
        const s = String(reader.result);
        const comma = s.indexOf(",");
        resolve(comma >= 0 ? s.slice(comma + 1) : "");
      };
      reader.readAsDataURL(blob);
    });

  const originSet = new Set();
  for (const [abs, meta] of candidates) {
    if (page.remote.length + page.inline.length >= maxItems) break;
    const scheme = abs.slice(0, abs.indexOf(":")).toLowerCase();
    if (scheme === "http" || scheme === "https") {
      try {
        originSet.add(new URL(abs).origin);
      } catch (_e) {
        continue;
      }
      page.remote.push({ url: abs, filename: meta.filename, mime: meta.hintMime || null });
    } else if (withBytes) {
      // blob:/data: are page-local — the page can read them (CSP permits), so
      // encode them here. (blob: URLs are unreadable from the extension context.)
      try {
        const resp = await fetch(abs);
        if (!resp.ok) continue;
        const blob = await resp.blob();
        if (!blob.size || blob.size > maxBytes) continue;
        const b64 = await readAsB64(blob);
        if (!b64) continue;
        page.inline.push({
          filename: meta.filename,
          mime: blob.type || meta.hintMime || null,
          source_url: scheme === "data" ? null : abs,
          data_base64: b64,
        });
      } catch (_e) {
        // page-local read failed — skip; capture still succeeds.
      }
    }
  }
  page.origins = [...originSet];
  return page;
}

// Runs in the page's MAIN world, because `window.br` is a page global and the
// extension's isolated world cannot see it. Read-only: it reads viewer state and
// returns a plain descriptor. It never mutates the page.
//
// Why ask the viewer instead of scraping the DOM: registers.nli.ie renders an
// Internet Archive BookReader over an IIPImage tile server. There is no
// full-page <img> anywhere — only ~256px tiles — and, worse, BookReader keeps the
// PREVIOUS and NEXT leaves' tiles in the DOM. Viewing leaf 26, every tile present
// belongs to leaves 1-3. Scraping the biggest image, or stitching the tiles,
// would archive the wrong page under a citation naming the right one. A capture
// that fails loudly is recoverable; one that silently captures the wrong page is
// a corrupted source that survives into the tree.
//
// br.getPageURI(br.currentIndex()) is authoritative for the leaf in view. Its URL
// carries only FIF (the JP2 path); swapping the tile request for CVT=jpeg asks
// IIPImage for the whole leaf as one image.
function probeViewer() {
  const br = window.br;
  if (!br || typeof br.getPageURI !== "function") return null;

  const index = typeof br.currentIndex === "function" ? br.currentIndex() : br.currentIndex;
  if (typeof index !== "number" || index < 0) return null;

  let uri;
  try {
    uri = new URL(br.getPageURI(index), location.href);
  } catch (_e) {
    return null;
  }
  if (uri.protocol !== "http:" && uri.protocol !== "https:") return null;
  // Only the IIPImage shape we actually understand. Any other viewer backend
  // falls through to generic detection rather than guessing at its API.
  if (!/iipsrv\.fcgi$/i.test(uri.pathname)) return null;

  const fif = uri.searchParams.get("FIF");
  if (!fif) return null;

  const full = new URL(uri.origin + uri.pathname);
  full.searchParams.set("FIF", fif);
  full.searchParams.set("CVT", "jpeg"); // whole leaf, native resolution

  const leaf = (fif.split("/").pop() || "leaf").replace(/\.[^.]+$/, "");
  return {
    url: full.href,
    filename: leaf + ".jpg",
    mime: "image/jpeg",
    origin: uri.origin,
    // The manifest must say this URL was synthesized from viewer state, not
    // found in the page. A reader could not have discovered it in the DOM.
    provenance: "viewer:bookreader-iip leaf=" + index,
  };
}

function hostOfUrl(u) {
  try {
    return new URL(u).host;
  } catch (_e) {
    return "";
  }
}

async function runGrab(grabMedia, withBytes) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: grabPage,
    args: [grabMedia, withBytes, MAX_MEDIA_ITEMS, MAX_MEDIA_BYTES],
  });
  const page = result;
  if (!grabMedia || !page) return page;

  let viewer = null;
  try {
    const [{ result: found }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: probeViewer,
    });
    viewer = found;
  } catch (_e) {
    // No viewer, or MAIN-world injection refused — generic detection stands.
  }

  if (viewer) {
    // The viewer is authoritative for its own host. Drop same-host DOM
    // candidates: those are its tiles, and they are the wrong leaves.
    const viewerHost = hostOfUrl(viewer.url);
    const others = (page.remote || []).filter((d) => hostOfUrl(d.url) !== viewerHost);
    page.remote = [viewer, ...others];
    page.origins = [...new Set([...(page.origins || []), viewer.origin])];
  }
  return page;
}

// Fetch + base64-encode one media URL from the POPUP (extension) context. With a
// host permission granted for its origin, this request is not subject to the page's CSP or CORS rules.
async function fetchEncodeRemote(url) {
  const resp = await fetch(url);
  if (!resp.ok) return null;
  const blob = await resp.blob();
  if (!blob.size || blob.size > MAX_MEDIA_BYTES) return null;
  const b64 = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.onload = () => {
      const s = String(reader.result);
      const comma = s.indexOf(",");
      resolve(comma >= 0 ? s.slice(comma + 1) : "");
    };
    reader.readAsDataURL(blob);
  });
  if (!b64) return null;
  return { b64, mime: blob.type || null };
}

function render(text) {
  document.getElementById("result").textContent = text;
}

// FastAPI's `detail` is a string for our own HTTPExceptions but a LIST of
// {loc, msg, type} objects for a pydantic request-validation failure. String-
// concatenating the list rendered "[object Object]" and hid the real error.
function errorDetail(body, resp) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => (Array.isArray(e.loc) ? e.loc.join(".") : "?") + ": " + e.msg)
      .join("; ");
  }
  if (detail) return JSON.stringify(detail);
  return resp.statusText || "(no detail)";
}

function hintFor(resp, body) {
  if (resp.status === 401) {
    return "\n\nThe token is wrong or stale. Copy the current one from the running `capsule serve`.";
  }
  // A 422 whose detail is a field list means the request shape itself was
  // rejected — almost always a backend older than this extension, since
  // `capsule serve` does not hot-reload.
  if (resp.status === 422 && Array.isArray(body && body.detail)) {
    return "\n\nThe backend rejected the request's shape. Restart `capsule serve` — it does not reload on code changes, so it may be older than this extension.";
  }
  return "";
}

function hostsOf(origins) {
  const hosts = [];
  for (const o of origins) {
    try {
      hosts.push(new URL(o).host);
    } catch (_e) {
      /* skip */
    }
  }
  return hosts;
}

// After a capture, the footer link opens THAT capture in the index (`#cap-<folder>`),
// where what was read can be checked against the record and corrected.
function pointIndexAtCapture(body) {
  const link = document.getElementById("index-link");
  const folder = String((body && body.capture_folder) || "").split(/[\\/]/).pop();
  if (!link || !folder) return;
  link.href = INDEX_URL + "#cap-" + encodeURIComponent(folder);
  link.textContent = "Review & correct this capture ↗";
}

function formatOutcome(body) {
  const lines = [
    "captured: " + body.url,
    "verify:   " + (body.verify_ok ? "OK" : "FAILED"),
    "gating:   " + body.gating,
    "record:   " + (body.record_type || "(unclassified)"),
    "media:    " + (body.media_count || 0),
    body.extraction_pending
      ? "extraction: PENDING — archived without an Anthropic API key; add one to read the record"
      : "assertions (" + body.assertions.length + "):",
  ];
  for (const a of body.assertions) {
    lines.push(
      "  - " +
        a.field +
        ": " +
        a.verbatim_value +
        " [" +
        a.evidence_type +
        "]" +
        (a.unverified ? "  [UNVERIFIED: not on page]" : "")
    );
  }
  return lines.join("\n");
}

// Archive the PDF the tab is showing as the capture's primary document. No page
// HTML, no model call: the backend writes a capture-folder/v2 document capture.
// The bytes are fetched from the extension context (past the page CSP/CORS) after
// a gesture-first host grant for the PDF's origin — the same path media uses.
async function captureDocument(button, token, note) {
  const isLocalFile = detected.pdfUrl.startsWith("file:");
  // A remote PDF needs a gesture-first host grant for its origin; a local file://
  // can't be granted that way (file access is the manual chrome://extensions
  // toggle), so skip the request and let the fetch itself gate on that toggle.
  let granted = isLocalFile;
  if (!isLocalFile) {
    // GESTURE-FIRST: request host access as the click's first async act, or Chrome
    // rejects permissions.request (the user gesture is spent).
    try {
      granted = await chrome.permissions.request({
        origins: detected.origins.map((o) => o + "/*"),
      });
    } catch (_e) {
      // request refused/unavailable — handled below.
    }
  }

  button.disabled = true;
  await saveToken(token);
  try {
    if (!granted) {
      render("Access to the PDF's site is needed to read its bytes.\nClick Capture again and choose Allow.");
      return;
    }
    render(isLocalFile ? "Reading the local file…" : "Fetching the PDF in view…");
    let enc;
    try {
      enc = await fetchEncodeRemote(detected.pdfUrl);
    } catch (err) {
      // A file:// fetch rejects when "Allow access to file URLs" is off — the one
      // failure the user can fix, so name it instead of the generic backend error.
      if (isLocalFile) {
        render("Can't read this local file. " + FILE_ACCESS_HINT);
        return;
      }
      throw err;
    }
    if (!enc) {
      render(
        isLocalFile
          ? "Could not read the local file (empty, too large, or access blocked). " + FILE_ACCESS_HINT
          : "Could not read the PDF (too large, blocked, or empty). Nothing was captured."
      );
      return;
    }
    const gating = document.getElementById("gating").value || undefined;
    const resp = await fetch(BACKEND, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Capsule-Token": token,
        // BUG-021: which build sent this capture (see background.js postAndShape).
        "X-Capsule-Extension-Version": chrome.runtime.getManifest().version,
      },
      body: JSON.stringify({
        url: detected.pdfUrl,
        document: {
          data_base64: enc.b64,
          filename: filenameFromUrl(detected.pdfUrl),
          mime: enc.mime || "application/pdf",
        },
        gating,
        note,
      }),
    });
    const body = await resp.json().catch(() => ({}));
    if (resp.status === 401 && (await repairAfter401())) {
      render("Capsule was paired again on this computer. Click Capture once more.");
      return;
    }
    if (!resp.ok) {
      render("Error " + resp.status + ": " + errorDetail(body, resp) + hintFor(resp, body));
      return;
    }
    await clearNoteDraft();
    pointIndexAtCapture(body);
    render(formatOutcome(body) + "\n\n(archived as a document — no text extracted yet)");
  } catch (err) {
    render(
      "Could not reach the Capsule backend at " +
        BACKEND +
        "\nIs `capsule serve` running?\n\n" +
        err
    );
  } finally {
    button.disabled = false;
  }
}

// The IDs the Capsule app pairs with (capsule/config.py; a test keeps the two lists equal): the
// unpacked extension's, fixed by the manifest `key`, and the Chrome Web Store copy's.
const OFFICIAL_EXTENSION_IDS = ["jalijanpiopkphjagoiofcofclmmmaan", "dopmkpbopjegbdngekbdpgpkmpgkgeeo"];

// PURE (tested in node). What to say when the app refuses to pair. On the first real Windows
// install the cause was an unofficial copy (loaded from a folder without the manifest `key`), and
// "update the app" sent the user the wrong way; only an official copy means the app is behind.
function pairRefusedAdvice(extensionId) {
  if (!OFFICIAL_EXTENSION_IDS.includes(extensionId)) {
    return (
      "This copy of the extension (ID " + extensionId + ") isn't an official one, so Capsule " +
      "won't pair with it. In chrome://extensions, remove it, then click Load unpacked and " +
      "choose the extension folder inside your Capsule installation (the installer shows it), " +
      "or install Genealogy Capsule from the Chrome Web Store."
    );
  }
  return (
    "Capsule is running but doesn't recognize this copy of the extension, so it won't pair. " +
    "Update the Capsule app by running its installer again (your archive is kept), then " +
    "reopen this popup."
  );
}

async function onCapture() {
  const button = document.getElementById("capture");
  const token = document.getElementById("token").value.trim();
  if (!token) {
    render(
      pairRefused
        ? pairRefusedAdvice(chrome.runtime.id)
        : "Capsule isn't paired yet. Reopen this popup and it pairs by itself; or paste the " +
            "token printed by `capsule serve` into the Token field."
    );
    return;
  }
  const note = document.getElementById("note").value.trim() || undefined;

  // The tab in view is a PDF: archive the document itself, not a page snapshot.
  if (detected.isPdf) {
    await captureDocument(button, token, note);
    return;
  }

  const grabMedia = document.getElementById("grab-media").checked;

  // GESTURE-FIRST: request host permission for the media origins detected on
  // open. This MUST be the click handler's first async call — Chrome rejects
  // permissions.request if a prior await has ended the user gesture.
  let grantedOrigins = [];
  if (grabMedia && detected.origins.length) {
    try {
      const granted = await chrome.permissions.request({
        origins: detected.origins.map((o) => o + "/*"),
      });
      if (granted) grantedOrigins = detected.origins;
    } catch (_e) {
      // request refused/unavailable — fall through and capture page-only.
    }
  }

  button.disabled = true;
  await saveToken(token);
  render("Capturing the page in view…");
  try {
    const page = await runGrab(grabMedia, true);
    const media = [...(page.inline || [])];
    let skipped = 0;
    if (grabMedia) {
      for (const d of page.remote || []) {
        if (media.length >= MAX_MEDIA_ITEMS) break;
        let origin;
        try {
          origin = new URL(d.url).origin;
        } catch (_e) {
          continue;
        }
        if (!grantedOrigins.includes(origin)) {
          skipped += 1;
          continue;
        }
        try {
          const enc = await fetchEncodeRemote(d.url);
          if (enc) {
            media.push({
              filename: d.filename,
              mime: enc.mime || d.mime || null,
              source_url: d.url,
              provenance: d.provenance || undefined,
              data_base64: enc.b64,
            });
          } else {
            skipped += 1;
          }
        } catch (_e) {
          skipped += 1;
        }
      }
    }

    // Render the page itself to PDF, so the capture is displayable evidence and not
    // only a DOM string. Appended LAST, so page-derived media keep the low indices.
    // Never gates the capture: a failed render yields {skipped} and we POST without
    // it (see render-pdf.js). Unreachable for a document capture — the isPdf branch
    // returned above — which is what keeps media[] off a document POST (it is a 422).
    let rendered = { skipped: "media slots full" };
    if (media.length < MAX_MEDIA_ITEMS) {
      const [renderTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      rendered = renderTab ? await renderPagePdf(renderTab.id) : { skipped: "no tab" };
      // Push ONLY the wire payload. The render's local metadata (`hid`) is not a
      // MediaIn field, and MediaIn forbids extras — sending the whole object 422s the
      // capture (BUG-002, which broke this path too, not just the on-page trigger).
      if (rendered.media) media.push(rendered.media);
    }

    // Empty means "auto-detect": omit the field entirely so the backend infers
    // gating from the page. An explicit declaration always wins over inference,
    // so sending "public" by default made that inference unreachable.
    const gating = document.getElementById("gating").value || undefined;
    const resp = await fetch(BACKEND, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Capsule-Token": token,
        // BUG-021: which build sent this capture (see background.js postAndShape).
        "X-Capsule-Extension-Version": chrome.runtime.getManifest().version,
      },
      body: JSON.stringify({ url: page.url, html: page.html, gating, media, note }),
    });
    const body = await resp.json().catch(() => ({}));
    if (resp.status === 401 && (await repairAfter401())) {
      render("Capsule was paired again on this computer. Click Capture once more.");
      return;
    }
    if (!resp.ok) {
      render("Error " + resp.status + ": " + errorDetail(body, resp) + hintFor(resp, body));
      return;
    }
    await clearNoteDraft();
    pointIndexAtCapture(body);
    let out = formatOutcome(body);
    // Say whether the page render landed. Silence here would make a snapshot-only
    // capture look identical to a complete one — and the commonest skip (DevTools
    // open on the tab) is invisible unless we name it.
    out += rendered.media
      ? "\n\npage PDF: archived (media/0-record.pdf)"
      : "\n\npage PDF: skipped (" + (rendered.skipped || "unknown") + ") — the page " +
        "snapshot was still captured.";
    // The backend looked at the render's pixels (BUG-001). A blank PDF is not evidence,
    // and must not read as a success here any more than it does in the toast.
    for (const warning of body.media_warnings || []) {
      out += "\n\n⚠ " + warning;
    }
    if (skipped) {
      out +=
        "\n\n(" +
        skipped +
        " embedded file(s) could not be archived — host access declined, too " +
        "large, or unreadable.)";
    }
    render(out);
  } catch (err) {
    render(
      "Could not reach the Capsule backend at " +
        BACKEND +
        "\nIs `capsule serve` running (with ANTHROPIC_API_KEY set)?\n\n" +
        err
    );
  } finally {
    button.disabled = false;
  }
}

document.getElementById("capture").addEventListener("click", onCapture);

// -- v0.2: the user's own Anthropic key, and captures waiting to be read --------------------

const SETTINGS_BASE = BACKEND.replace(/\/capture$/, "");

async function settingsFetch(path, init) {
  const token = document.getElementById("token").value.trim();
  if (!token) return { ok: false, status: 0, body: { detail: "Paste the pairing token first." } };
  const resp = await fetch(SETTINGS_BASE + path, {
    ...(init || {}),
    headers: { "Content-Type": "application/json", "X-Capsule-Token": token },
  });
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

async function refreshKeyStatus() {
  const statusEl = document.getElementById("key-status");
  const details = document.getElementById("key-settings");
  const pendingRow = document.getElementById("pending-row");
  let res;
  try {
    res = await settingsFetch("/settings/status");
  } catch (err) {
    statusEl.textContent = "backend not running";
    return null;
  }
  if (!res.ok) {
    statusEl.textContent = res.status === 401 ? "pair first (token)" : "unknown";
    return null;
  }
  const s = res.body;
  statusEl.textContent =
    s.api_key === "set" ? "set ✓" : "not set (captures are archived but not read)";
  if (s.api_key !== "set") details.open = true;
  const reading = s.reading || {};
  if (reading.running) {
    pendingRow.hidden = false;
    document.getElementById("pending-text").textContent =
      "Reading captures: " + reading.done + " of " + reading.total + "…";
    document.getElementById("read-pending").disabled = true;
  } else if (s.pending > 0) {
    pendingRow.hidden = false;
    // The last run's failure is shown again when the popup reopens, not just once.
    if (reading.failed || reading.last_error) render(readingSummary(reading));
    document.getElementById("pending-text").textContent =
      s.pending + " capture(s) waiting to be read.";
    document.getElementById("read-pending").disabled = s.api_key !== "set";
  } else {
    pendingRow.hidden = true;
  }
  return s;
}

// Never call a run that failed a success (Codex review): say what was read, what was not, and why.
function readingSummary(reading) {
  const r = reading || {};
  if (!r.failed && !r.last_error) {
    return "Finished reading " + (r.done || 0) + " capture(s). Open the Capsule index to see them.";
  }
  return (
    "Read " + (r.done || 0) + " of " + (r.total || 0) + " capture(s); " + (r.failed || 0) +
    " could not be read.\nLast problem: " + (r.last_error || "unknown") +
    "\nNothing was lost: those captures are still archived, and you can try again."
  );
}

async function onSaveKey() {
  const input = document.getElementById("api-key");
  const key = input.value.trim();
  if (!key) {
    render("Paste your Anthropic API key first.");
    return;
  }
  render("Checking the key with Anthropic…");
  let res;
  try {
    res = await settingsFetch("/settings/api-key", {
      method: "POST",
      body: JSON.stringify({ api_key: key }),
    });
  } catch (err) {
    render("Could not reach the Capsule backend. Is `capsule serve` running?");
    return;
  }
  input.value = ""; // never keep the key in the popup
  render(res.ok ? "Key saved. New captures will be read." : "Not saved: " + errorDetail(res.body, res));
  await refreshKeyStatus();
}

async function onReadPending() {
  const res = await settingsFetch("/settings/read-pending", { method: "POST" });
  if (!res.ok) {
    render("Could not start: " + errorDetail(res.body, res));
    return;
  }
  const poll = async () => {
    const s = await refreshKeyStatus();
    if (s && s.reading && s.reading.running) setTimeout(poll, 2000);
    else if (s) render(readingSummary(s.reading));
  };
  poll();
}

document.getElementById("save-key").addEventListener("click", onSaveKey);
document.getElementById("read-pending").addEventListener("click", onReadPending);

// On open: prefill the token, and probe the page for embedded-media hosts so the
// Capture click can request them gesture-first. (Opening the popup grants
// activeTab, so the probe's executeScript is allowed.)
// v0.2 — the extension and the Capsule app are released together and should match. Chrome can
// update one without the other, so say which one is behind, and how to fix it, in plain words.
// PURE (tested in node): returns null when they match or a version cannot be read.
function versionAdvice(appVersion, extensionVersion) {
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
  return (
    "This extension (" + e + ") is older than the Capsule app (" + a + "). Update it: open " +
    "chrome://extensions and click the reload arrow on Capsule (or load the new extension folder)."
  );
}

function checkVersions(health) {
  const advice = versionAdvice(health.version, chrome.runtime.getManifest().version);
  const banner = document.getElementById("version-banner");
  if (advice && banner) {
    banner.textContent = advice;
    banner.hidden = false;
  }
}

// -- v0.2.1: first run — the Capsule app isn't installed, or isn't running ----------------------
// Most people add the extension (from the Web Store) before the app exists. Instead of a dead
// popup, say what's missing, offer the double-click installer for this system, and switch to
// the normal popup by itself once the app answers.

const HEALTH_URL = BACKEND.replace(/\/capture$/, "/health");
const REPO_URL = "https://github.com/mjlawless/capsule-genealogy";
const INSTALL_DOCS_URL = REPO_URL + "#install-macos-or-windows";
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

async function showSetup() {
  const stored = await chrome.storage.local.get(SEEN_APP_KEY);
  let os = "";
  try {
    os = (await chrome.runtime.getPlatformInfo()).os;
  } catch (_e) {
    // unknown platform: the screen says the app can't run here
  }
  const s = setupScreen(os, !!stored[SEEN_APP_KEY]);
  const byId = (id) => document.getElementById(id);
  byId("setup-title").textContent = s.title;
  byId("setup-body").textContent = s.body;

  const download = byId("setup-download");
  download.parentElement.hidden = !s.download;
  if (s.download) {
    download.href = s.download;
    download.textContent = "Download the Capsule installer";
    // Download in place, so this popup stays open and can turn green when the app starts.
    download.addEventListener("click", (event) => {
      event.preventDefault();
      chrome.downloads.download({ url: s.download }).catch(() => chrome.tabs.create({ url: s.download }));
    });
  }
  byId("setup-steps").textContent = s.steps;
  byId("setup-steps").hidden = !s.steps;

  const box = byId("setup-command-box");
  box.hidden = !s.command;
  box.open = !!s.command && !s.download; // Linux: the command IS the installer
  if (!s.download) byId("setup-command-summary").textContent = "Install with one command";
  byId("setup-where").textContent = s.where;
  byId("setup-command").textContent = s.command;
  byId("setup-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(s.command);
      byId("setup-copy").textContent = "Copied ✓";
    } catch (_e) {
      byId("setup-copy").textContent = "Select the text above and copy it";
    }
  });
  byId("setup-docs").href = INSTALL_DOCS_URL;
  byId("setup-wait").hidden = !s.command;

  byId("main").hidden = true;
  byId("setup").hidden = false;
  if (!s.command) return; // nothing to install here, so nothing to wait for

  const watch = async () => {
    const health = await fetchHealth();
    if (!health) {
      setTimeout(watch, 2000);
      return;
    }
    byId("setup").hidden = true;
    byId("main").hidden = false;
    render("Capsule is running. You're ready to capture.");
    startMain(health);
  };
  setTimeout(watch, 2000);
}

async function init() {
  const health = await fetchHealth();
  if (!health) {
    await showSetup();
    return;
  }
  await startMain(health);
}

async function startMain(health) {
  chrome.storage.local.set({ [SEEN_APP_KEY]: true });
  checkVersions(health);
  loadToken().then((token) => {
    if (token) document.getElementById("token").value = token;
    refreshKeyStatus();
  });

  const indexLink = document.getElementById("index-link");
  if (indexLink) indexLink.href = INDEX_URL;

  const noteEl = document.getElementById("note");
  // Resolve the tab URL up front so the note draft is restored — and re-saved on
  // every keystroke — for the exact page in view, whether it is a PDF or a page.
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    currentUrl = (tab && tab.url) || "";
  } catch (_e) {
    currentUrl = "";
  }
  const draft = await loadNoteDraft(currentUrl);
  if (draft) noteEl.value = draft;
  noteEl.addEventListener("input", () => {
    // Fire-and-forget: the draft must be safe on disk before the permission
    // dialog (which closes this popup) can discard the in-memory value.
    saveNoteDraft(currentUrl, noteEl.value);
  });

  // If the tab in view is itself a PDF, switch to document mode: there is no
  // page to snapshot or media to grab — we archive the document. Detected from
  // the tab URL because a content script can't reach into Chrome's PDF viewer.
  if (looksLikePdf(currentUrl)) {
    detected.isPdf = true;
    detected.pdfUrl = currentUrl;
    try {
      detected.origins = [new URL(currentUrl).origin];
    } catch (_e) {
      detected.origins = [];
    }
    document.getElementById("capture").textContent = "Capture this PDF";
    const mediaRow = document.getElementById("grab-media").closest(".row");
    if (mediaRow) mediaRow.style.display = "none";
    render(
      currentUrl.startsWith("file:")
        ? "This tab is a LOCAL PDF. Capture will archive the file itself and cite " +
            "it (no text extracted yet), flagged as a private, copyright document — " +
            "never republished.\n\n" +
            FILE_ACCESS_HINT +
            "\n\nYour note is saved as you type."
        : "This tab is a PDF. Capture will archive the document itself and cite it " +
            "(no text is extracted yet). You'll be asked to grant access to its site " +
            "so its bytes can be read.\n\nYour note is saved as you type, so it will " +
            "still be here after you allow access."
    );
    return; // no page/media probe for a document
  }

  try {
    const probe = await runGrab(true, false);
    detected.origins = probe.origins || [];
    detected.count = (probe.remote || []).length + (probe.inline || []).length;
    if (detected.count) {
      const hosts = hostsOf(detected.origins);
      const where = hosts.length ? " on " + hosts.join(", ") : "";
      render(
        "Detected " +
          detected.count +
          " embedded file(s)" +
          where +
          ".\nOn Capture you'll be asked to grant access so they can be archived."
      );
    }
  } catch (_e) {
    // probe failed (restricted page, etc.) — capture still works page-only.
  }
}

init();
