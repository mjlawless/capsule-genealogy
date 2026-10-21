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
  return stored[TOKEN_KEY] || "";
}

async function saveToken(token) {
  await chrome.storage.local.set({ [TOKEN_KEY]: token });
}

// Runs in the PAGE context (serialized by chrome.scripting). Collects the page
// snapshot and its embedded-media candidates. It does NOT fetch cross-origin
// media (the page can't) — it returns http(s) candidates as descriptors for the
// popup to fetch, and only fetches page-local blob:/data: bytes itself.
async function grabPage(grabMedia, withBytes, maxItems, maxBytes) {
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

  for (const el of document.querySelectorAll("embed[src], iframe[src]")) {
    add(el.getAttribute("src"), el.type || "");
  }
  for (const el of document.querySelectorAll("object[data]")) {
    add(el.getAttribute("data"), el.type || "");
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

function formatOutcome(body) {
  const lines = [
    "captured: " + body.url,
    "verify:   " + (body.verify_ok ? "OK" : "FAILED"),
    "gating:   " + body.gating,
    "record:   " + (body.record_type || "(unclassified)"),
    "media:    " + (body.media_count || 0),
    "assertions (" + body.assertions.length + "):",
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
    if (!resp.ok) {
      render("Error " + resp.status + ": " + errorDetail(body, resp) + hintFor(resp, body));
      return;
    }
    await clearNoteDraft();
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

async function onCapture() {
  const button = document.getElementById("capture");
  const token = document.getElementById("token").value.trim();
  if (!token) {
    render("Paste the auth token printed by `capsule serve` into the Token field first.");
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
    if (!resp.ok) {
      render("Error " + resp.status + ": " + errorDetail(body, resp) + hintFor(resp, body));
      return;
    }
    await clearNoteDraft();
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

// On open: prefill the token, and probe the page for embedded-media hosts so the
// Capture click can request them gesture-first. (Opening the popup grants
// activeTab, so the probe's executeScript is allowed.)
async function init() {
  loadToken().then((token) => {
    if (token) document.getElementById("token").value = token;
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
