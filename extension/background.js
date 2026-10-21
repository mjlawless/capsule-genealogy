// Capsule background service worker.
//
// Turns a content-script capture message into an authenticated POST to the local
// backend. It holds no UI and needs no popup: the token is the one the user pasted
// into the popup once (chrome.storage.local), so a capture works WITHOUT opening
// the popup — which is what lets an automation agent trigger a capture by clicking
// an on-page button (see content.js). The page HTML is grabbed by the content
// script (which reads the page the user already has open in their own session), so this worker
// needs no host permission for the page — only the declared one for 127.0.0.1.

// `renderPagePdf(tabId)` — prints the tab in view to a PDF media item, or reports
// `{skipped}`. `armDownload`/`takeStagedDownload` — ingest the file Ancestry's own
// Download button produces (REQ-001). Classic (non-module) service worker, so:
importScripts("render-pdf.js", "download-intercept.js");

// Start listening for downloads immediately. The listener ignores everything that is not
// an armed Ancestry download, so the researcher's own files are never touched.
watchDownloads();

const BACKEND = "http://127.0.0.1:8765/capture";
const LEDGER_STARTED = "http://127.0.0.1:8765/ancestry/started";
const LEDGER_FAILED = "http://127.0.0.1:8765/ancestry/failed";
const TOKEN_KEY = "capsuleToken";

// Tell the ledger a capture BEGAN, before doing any of the work (BUG-005).
//
// This runs in the service worker, which survives the page navigating away — and that is
// exactly the point. A capture killed mid-flight (the agent moved on to the next record,
// the tab closed, the browser died) never reaches the backend at all, so the backend
// cannot log a failure: it never learns the capture existed. Without this marker the
// record just stays `seeded`, indistinguishable from one nobody has tried, and a silent
// abort is only ever caught by someone happening to see a toast flash.
//
// Best-effort by design: if the ledger cannot be reached we capture anyway. A missing
// trace is a visibility problem; refusing to capture would be a data-loss one.
// `pageLabel` (REQ-009) is sent only for an extra image of a record, so the record's own
// events keep the exact wire shape an older backend accepts.
async function markStarted(apid, token, pageLabel) {
  if (!apid) return;
  try {
    await fetch(LEDGER_STARTED, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Capsule-Token": token },
      body: JSON.stringify(pageLabel ? { apid, page_label: pageLabel } : { apid }),
    });
  } catch (_e) {
    /* the capture matters more than its bookkeeping */
  }
}

/** Record a failure the BACKEND cannot see — an identity mismatch, a dead render. */
async function markFailed(apid, token, error, pageLabel) {
  if (!apid) return;
  const body = { apid, error: String(error).slice(0, 500) };
  if (pageLabel) body.page_label = pageLabel;
  try {
    await fetch(LEDGER_FAILED, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Capsule-Token": token },
      body: JSON.stringify(body),
    });
  } catch (_e) {
    /* nothing more we can do */
  }
}

// The identity guard's browser half (REQ-001 R5). The backend is authoritative — it
// refuses with a 409 — but catching a mismatch here means we never even render or POST a
// page we already know is the wrong record. Mirrors capsule/pipeline/ancestry.py.
const APID_RE = /^\s*(?:\d+\s*,\s*)?(\d+)\s*::?\s*(\d+)\s*$/;
const SEARCH_RE = /\/search\/collections\/(\d+)\/records\/(\d+)/;
const DISCOVERY_RE = /\/discoveryui-content\/view\/(\d+):(\d+)/;

function recordIdsFromUrl(url) {
  let m = SEARCH_RE.exec(url || "");
  if (m) return [m[1], m[2]];
  m = DISCOVERY_RE.exec(url || "");
  if (m) return [m[2], m[1]]; // this URL shape is <rec>:<db> — note the swap
  return null;
}

/** A human-readable reason NOT to capture, or null when the page IS the right record. */
function identityMismatch(apid, url) {
  if (!apid) return null; // no expectation declared — nothing to guard
  const want = APID_RE.exec(apid);
  if (!want) return "cannot verify identity: " + apid + " is not a readable _APID";
  const got = recordIdsFromUrl(url);
  if (!got) {
    return (
      "cannot verify identity: expected " +
      want[1] + ":" + want[2] +
      ", but this is not a readable Ancestry record page"
    );
  }
  if (got[0] !== want[1] || got[1] !== want[2]) {
    return (
      "IDENTITY MISMATCH: expected " + want[1] + ":" + want[2] +
      ", but this page is " + got[0] + ":" + got[1] + " — NOTHING was captured"
    );
  }
  return null;
}

async function loadToken() {
  const stored = await chrome.storage.local.get(TOKEN_KEY);
  return stored[TOKEN_KEY] || "";
}

// FastAPI's `detail` is a string for our own HTTPExceptions but a list of
// {loc,msg,type} for a request-validation failure. Flatten either into one line.
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

const MEDIA_MAX_BYTES = 25 * 1024 * 1024;

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch (_e) {
    return null;
  }
}

/**
 * Fetch the record files the page embeds (BUG-007).
 *
 * The certificate on a NYC vital-records page is a SAS-signed Azure blob behind Chrome's
 * native PDF plugin: no <img>, no link, and the plugin's Download button is not in the
 * accessibility tree. The page's own CSP blocks a page-context fetch of that blob, and the
 * blob sends no CORS headers — so the ONLY way to get the bytes is from the extension's
 * context, with a host grant. The signed URL is time-limited and cannot be reconstructed
 * afterwards; it has to be taken while the page is live.
 *
 * A host we have NOT been granted cannot be fetched here: `permissions.request` needs a user
 * gesture and an agent-triggered capture has none. So we say so, loudly — the researcher
 * grants the host once from the popup and re-captures. What must never happen again is what
 * did happen: the file missed, and the capture reporting success anyway.
 *
 * @returns {Promise<{media: object[], missed: string[]}>}
 */
async function fetchRemoteMedia(remote) {
  const media = [];
  const missed = [];

  for (const item of remote || []) {
    const origin = originOf(item.url);
    if (!origin) continue;

    let granted = false;
    try {
      granted = await chrome.permissions.contains({ origins: [origin + "/*"] });
    } catch (_e) {
      granted = false;
    }
    if (!granted) {
      missed.push(
        item.filename +
          " — Capsule has no access to " +
          origin +
          ". Open the Capsule popup on this page and capture once to grant it, then re-run."
      );
      continue;
    }

    try {
      const resp = await fetch(item.url);
      if (!resp.ok) {
        missed.push(item.filename + " — HTTP " + resp.status + " fetching it");
        continue;
      }
      const buffer = await resp.arrayBuffer();
      if (!buffer.byteLength) {
        missed.push(item.filename + " — the file came back empty");
        continue;
      }
      if (buffer.byteLength > MEDIA_MAX_BYTES) {
        missed.push(item.filename + " — too large to archive");
        continue;
      }
      const bytes = new Uint8Array(buffer);
      let binary = "";
      for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      }
      media.push({
        data_base64: btoa(binary),
        filename: item.filename,
        mime: item.mime || resp.headers.get("content-type") || "application/pdf",
        // Evidence-grade provenance: this is where the record actually came from, and being
        // signed, that URL can never be reconstructed.
        source_url: item.url,
        provenance: "page:embedded",
      });
    } catch (err) {
      missed.push(item.filename + " — " + (err && err.message ? err.message : String(err)));
    }
  }

  return { media, missed };
}

async function doCapture(msg, tabId) {
  const token = await loadToken();
  if (!token) {
    return {
      ok: false,
      error:
        "No Capsule token saved. Open the Capsule popup once and paste the token " +
        "printed by `capsule serve`, then trigger the capture again.",
    };
  }

  // IDENTITY GUARD (R5). Refuse before we render or POST anything. The backend checks
  // again and is authoritative, but a wrong record should never get this far.
  const mismatch = identityMismatch(msg.apid, msg.url);
  if (mismatch) {
    await markFailed(msg.apid, token, mismatch);
    return { ok: false, error: mismatch };
  }

  // Mark it in flight BEFORE the slow work (render, download, POST). If the tab navigates
  // away now, this row is the only evidence the capture ever happened (BUG-005).
  await markStarted(msg.apid, token);

  // Render the page to PDF so the capture is displayable evidence, not just a DOM
  // string. Never gates the capture: on any failure this yields {skipped} and we
  // POST the snapshot alone (see render-pdf.js).
  const render = await renderPagePdf(tabId);
  // Only `render.media` goes on the wire — it holds exactly the fields MediaIn allows.
  // The render's local metadata (`hid`, `skipped`) stays here. Posting the whole object
  // is what 422'd every capture in BUG-002; keep the two separate.
  const media = render.media ? [render.media] : [];

  // THE RECORD FILES THE PAGE EMBEDS — the certificate, not a picture of it (BUG-007).
  // The blob: ones were read in the page; the http ones are fetched here, where the page's
  // CSP does not apply. Anything we could not get is reported, never swallowed.
  const embedded = await fetchRemoteMedia(msg.remote_media);
  for (const item of msg.page_media || []) media.push(item);
  for (const item of embedded.media) media.push(item);
  const missed = (msg.unreadable_media || []).concat(embedded.missed);

  // The full-resolution record image, if the SITE'S own Download produced one and we armed
  // for it. Same wire discipline: only `staged.media` goes on the wire.
  let staged = { skipped: "no download was armed for this capture" };
  if (msg.apid && msg.image_intent === "expected") {
    staged = await takeStagedDownload(msg.apid, tabId, hostOf(msg.url || ""));
    // R1.3 — the canvas fallback, ONLY when explicitly asked for. Never a silent
    // downgrade: it is screen-resolution, the backend measures it as DEGRADED whatever we
    // label it, and the ledger leaves the record unmined so we come back for the real scan.
    if (!staged.media && msg.allow_canvas_fallback) {
      const fallback = await captureViewerCanvas(tabId);
      if (fallback.media) staged = fallback;
    }
  } else if (msg.arm_key) {
    // REQ-007 §2 — every other site. There is no apid to key on, so the armed key is the
    // record URL. This is a cheap no-op when nothing is armed (the common case): with no
    // armed record `takeStagedDownload` returns immediately without polling, so an ordinary
    // capture pays nothing for asking. The canvas fallback stays Ancestry-only on purpose —
    // off Ancestry it is exactly the 689x545 preview REQ-007 was filed about, and offering
    // it here would rebuild the failure with a friendlier label.
    staged = await takeStagedDownload(msg.arm_key, tabId, hostOf(msg.url || ""));
  }
  if (staged.media) media.push(staged.media);

  return await postAndShape({
    token,
    url: msg.url,
    html: msg.html,
    note: msg.note,
    media,
    apid: msg.apid,
    pageLabel: msg.page_label,
    imageIntent: msg.image_intent,
    followUp: msg.follow_up,
    remine: msg.remine,
    missed,
    gating: msg.apid ? "gated" : undefined,
    render,
    stagedDownloadId: staged.downloadId,
    imageSkipped: staged.skipped,
  });
}

/**
 * POST an assembled capture to the backend and shape the reply the content script renders.
 *
 * Extracted so the ordinary capture (doCapture) and the one-call Ancestry image capture
 * (captureAncestryImage) share ONE definition of what goes on the wire and what comes back —
 * the BUG-002 wire discipline (MediaIn is `extra="forbid"`) is easy to get subtly wrong twice.
 */
// Wire fields a backend older than this extension will not know. The request model is
// `extra="forbid"`, so ONE unknown key 422s the entire capture — snapshot, media and all —
// rather than the backend ignoring it. That is the BUG-002 failure shape, and it is not
// hypothetical: on 2026-08-22 an auto-updated extension met a backend one commit behind and
// every viewer-page capture was lost with `body.page_label: Extra inputs are not permitted`.
//
// A distributed extension does not upgrade in lockstep with a local backend, so the answer
// is to DEGRADE, not to fail: drop the fields the backend rejected and send the capture
// again. Only these keys may ever be dropped — each is an enhancement whose absence costs a
// label, never evidence. Nothing that carries bytes or provenance is here, and nothing is
// dropped that the backend did not explicitly name.
const DROPPABLE_WIRE_FIELDS = new Set(["page_label", "image_skipped"]);

/** Field names a 422 complained were unknown, limited to the ones we may safely drop. */
function unknownFieldsIn(body) {
  const detail = body && body.detail;
  if (!Array.isArray(detail)) return [];
  const names = new Set();
  for (const e of detail) {
    if (!e || !Array.isArray(e.loc)) continue;
    if (!/extra/i.test(String(e.type || "") + " " + String(e.msg || ""))) continue;
    const name = e.loc[e.loc.length - 1];
    if (DROPPABLE_WIRE_FIELDS.has(name)) names.add(name);
  }
  return [...names];
}

async function postAndShape(o) {
  const payload = {
    url: o.url,
    html: o.html,
    note: o.note,
    media: o.media,
    // Ancestry-only; absent for every other capture, so nothing else changes.
    expected_apid: o.apid,
    image_intent: o.imageIntent,
    follow_up: o.followUp,
    remine: o.remine,
    // Record files the page held that we could not get. The backend records them, so a
    // missed certificate is on disk as a fact and not only in a toast that scrolls away.
    missed_media: o.missed || [],
    // Ancestry is login-walled: declare it rather than relying on inference, which
    // forces copyright_flag=True and forbids a Wayback URL, backend-side.
    gating: o.gating,
    // REQ-007 §1 — which leaf of a multi-image viewer was on screen. Read from the
    // LIVE DOM by the content script, because serializing the page cannot express it.
    page_label: o.pageLabel,
    // REQ-010 — why we hold no record image, so the manifest and the ledger can say it and
    // not only the toast. The backend records it only when the capture has no record image.
    image_skipped: o.imageSkipped || undefined,
  };

  const send = (p) =>
    fetch(BACKEND, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Capsule-Token": o.token,
        // BUG-021: which build sent this capture, recorded in its manifest. A header, so an
        // older backend ignores it rather than 422ing the capture.
        "X-Capsule-Extension-Version": chrome.runtime.getManifest().version,
      },
      body: JSON.stringify(p),
    });
  const unreachable = {
    ok: false,
    error: "Backend unreachable at " + BACKEND + " — is `capsule serve` running?",
  };

  let resp;
  try {
    resp = await send(payload);
  } catch (err) {
    return unreachable;
  }
  let body = await resp.json().catch(() => ({}));

  // An older backend rejects a field it has never heard of and takes the whole capture down
  // with it. Drop exactly what it named — never more — and send once more. ONCE: a loop here
  // could strip a request to nothing while insisting it succeeded.
  let downgraded = [];
  if (resp.status === 422) {
    const unknown = unknownFieldsIn(body);
    if (unknown.length) {
      for (const key of unknown) delete payload[key];
      downgraded = unknown;
      try {
        resp = await send(payload);
      } catch (err) {
        return unreachable;
      }
      body = await resp.json().catch(() => ({}));
    }
  }

  if (!resp.ok) {
    const error = "HTTP " + resp.status + ": " + errorDetail(body, resp);
    // A dedup 409 is the system WORKING — the backend closes the ledger itself with a
    // `dedup-blocked` event (BUG-017). Anything else, INCLUDING an identity-refusal 409,
    // leaves an error row: otherwise our own `started` stands as the record's latest word
    // and reads as a capture that vanished.
    if (resp.status !== 409 || body.dedup !== "blocked") {
      await markFailed(o.apid, o.token, error, o.pageLabel);
    }
    // REQ-004: a dedup refusal names the capture already held, so the driving agent
    // can cite the existing capture instead of hunting for it.
    return {
      ok: false,
      error,
      dedup: body.dedup || null,
      duplicate_of: body.duplicate_of || null,
    };
  }

  // The capture is safely on disk and hashed, so the loose copy in Downloads is clutter.
  // Only ever the file we ourselves armed for — never one of the user's own.
  if (o.stagedDownloadId != null) await cleanUpDownload(o.stagedDownloadId);

  const render = o.render || {};
  return {
    ok: true,
    // REQ-004: "new" or "repaired" (a prior partial capture existed — duplicate_of).
    dedup: body.dedup || null,
    duplicate_of: body.duplicate_of || null,
    // full | degraded-canvas | index-only-no-image | failed — decided by the backend
    // DECODING the image, not by what the download claimed to be.
    image_status: body.image_status || null,
    image_skipped: o.imageSkipped || null,
    // Record files the page held that we did NOT get. This must reach the researcher: a
    // capture that quietly substitutes a page render for the record is the failure this
    // whole project exists to prevent, and it is invisible unless we say it out loud.
    missed_media: o.missed || [],
    url: body.url,
    record_type: body.record_type || null,
    assertion_count: Array.isArray(body.assertions) ? body.assertions.length : 0,
    verify_ok: !!body.verify_ok,
    // WHY verify failed when every byte is intact (REQ-007 §3.3). `verify_ok` alone is a
    // bare boolean, and on a zoomed export it reads `verify FAILED` beside `IMAGE full` —
    // two true statements that contradict each other. Without this the toast and the
    // driving agent see the contradiction and nothing that resolves it.
    verify_shortfall: Array.isArray(body.verify_shortfall) ? body.verify_shortfall : [],
    // WHICH leaf of a multi-image viewer this capture holds (REQ-007 §1). The driving
    // agent reads this outcome, and on a register that serves 173 images at one URL it is
    // the only field that says what was actually banked.
    page_label: body.page_label || null,
    media_count: body.media_count || 0,
    // The backend rasterizes the render and tells us when it came out BLANK (BUG-001).
    // An attached-but-blank PDF is a failure dressed as a success — the worst kind,
    // because nobody re-runs a capture that said it worked. Report it as one.
    media_warnings: Array.isArray(body.media_warnings) ? body.media_warnings : [],
    pdf: render.media ? "attached" : "skipped",
    pdf_skipped_reason: render.skipped || null,
    // What we suppressed for the print, so an unknown overlay caught by the heuristic
    // is visible rather than a silent change to what the archive contains.
    pdf_hid: render.hid || [],
    // Fields this backend was too old to accept, dropped so the capture could land. Never
    // silent: the capture is real but missing an enhancement, and the operator has to know
    // which — otherwise a register capture with no `page_label` looks like a page with no
    // page selector.
    wire_downgraded: downgraded,
  };
}

// -- REQ-002: one-call Ancestry image capture --------------------------------
//
// Collapse the ~8-step arm -> open viewer -> find Tools menu -> click Download -> navigate
// back -> capture choreography into a SINGLE event the agent fires on the record page. The
// per-record DOM lookups were the slow part of capturing a record image.
//
// The whole flow is internal to the worker, which survives the tab work a content script
// cannot. The record page's HTML is captured up front (it carries the genealogical fields
// the extractor needs — the viewer is only a canvas), then the viewer is opened in a
// background tab purely to operate Ancestry's OWN Download control. That control is the
// only sanctioned route to the full-resolution scan; REQ-002 opts INTO clicking it for the
// operator, which is the one posture line it consciously relaxes. Everything else holds:
// the page in view is the record, the image comes from the provider's own download, and no
// media/tile API is touched.

/** Close a tab we opened; never fatal if it is already gone. */
async function closeViewerTab(tabId) {
  if (tabId == null) return;
  try {
    await chrome.tabs.remove(tabId);
  } catch (_e) {
    /* already closed, or the user closed it — nothing to do */
  }
}

/**
 * Tell the viewer tab's (auto-injected) content script to click Ancestry's Tools -> Download.
 *
 * We retry the message rather than wait on `tabs.onUpdated`: without the "tabs" permission a
 * background tab's load state is not reliably observable, but a content script that is not
 * ready simply rejects the sendMessage — so retrying until it answers IS the readiness check.
 * The content script returns `{ok}` once it has clicked, or `{ok:false, reason}` if it could
 * not find the control (Ancestry's DOM drifted) — reported, never silently swallowed.
 */
async function driveViewerDownload(viewerTabId, apid) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const res = await chrome.tabs.sendMessage(viewerTabId, {
        type: "capsule:auto-download",
        apid,
      });
      if (res) return res;
    } catch (_e) {
      /* the viewer content script is not loaded yet — retry */
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return { ok: false, reason: "the image viewer did not become ready in time" };
}

/**
 * REQ-002. One event -> a verified full-resolution Ancestry record image on disk.
 *
 * `msg` carries the RECORD page's own url+html (captured before any navigation, so the
 * extraction sees the record's fields) and the viewer url derived from that page.
 */
async function captureAncestryImage(msg, tabId) {
  const token = await loadToken();
  if (!token) {
    return {
      ok: false,
      error:
        "No Capsule token saved. Open the Capsule popup once and paste the token " +
        "printed by `capsule serve`, then trigger the capture again.",
    };
  }

  // ORIGIN GUARD — before ANY work, including the ledger writes below. The identity guard
  // checks URL PATHS, so evil.example/search/collections/7602/records/56510272 passes it;
  // only the host says whether this is Ancestry. `recordUrl` is the content script's own
  // `location.href`, so the page cannot forge it. armDownload refuses the ancestry profile
  // off Ancestry too; this makes the refusal early and writes nothing (Codex adversarial
  // review, second pass, 2026-09-29).
  if (!SIBLING_HOSTS.ancestry.test(hostOf(msg.recordUrl || ""))) {
    return { ok: false, error: "the one-call image capture only runs on an Ancestry record page" };
  }

  // IDENTITY GUARD (R5) — on the record page, before any work. A wrong record must never
  // get as far as arming a download or opening a tab.
  // REQ-009 — an extra image of the record carries a label, and every ledger event and the
  // download correlation carry it too, so a second image can never stall or claim the first.
  const pageLabel = msg.page_label || undefined;
  const armKey = pageLabel ? msg.apid + "#" + pageLabel : msg.apid;

  const mismatch = identityMismatch(msg.apid, msg.recordUrl);
  if (mismatch) {
    await markFailed(msg.apid, token, mismatch, pageLabel);
    return { ok: false, error: mismatch };
  }
  if (!msg.viewerUrl) {
    return {
      ok: false,
      error:
        "no image-viewer link on this record page — is it an image-backed record? " +
        "(an index-only record has no scan to download)",
    };
  }

  await markStarted(msg.apid, token, pageLabel);

  // Render the RECORD page to PDF NOW, while `tabId` still shows it — before we open the
  // viewer. The displayable evidence must be the record, not the canvas viewer.
  const render = await renderPagePdf(tabId);
  const media = render.media ? [render.media] : [];

  // Arm interception BEFORE opening the viewer, so the download the viewer produces is
  // correlated to this record and nothing else can claim it (download-intercept.js).
  //
  // This flow OPENS the viewer in a second tab, so the download can arrive from a host the
  // record page never mentions. That is precisely what the `ancestry` profile is for; the
  // record URL's own host is passed too, so both rules apply rather than only the broader.
  const armed = await armDownload({
    key: armKey,
    tabId,
    host: hostOf(msg.recordUrl || ""),
    profile: "ancestry",
  });
  if (!armed || !armed.ok) {
    const reason = "could not arm the download: " + ((armed && armed.reason) || "unknown");
    await markFailed(msg.apid, token, reason, pageLabel);
    return { ok: false, error: reason };
  }

  let viewerTabId = null;
  try {
    // Open the viewer in a background tab (active only if the caller asked to watch it),
    // then have its content script operate Ancestry's own Tools -> Download.
    const tab = await chrome.tabs.create({ url: msg.viewerUrl, active: !!msg.showViewer });
    viewerTabId = tab.id;
    const clicked = await driveViewerDownload(viewerTabId, msg.apid);
    if (!clicked.ok) {
      await closeViewerTab(viewerTabId);
      const reason = "Ancestry's Download was not triggered: " + clicked.reason;
      await markFailed(msg.apid, token, reason, pageLabel);
      return { ok: false, error: reason };
    }

    // Wait for the armed download to land (bounded; takeStagedDownload polls).
    const staged = await takeStagedDownload(armKey, tabId, hostOf(msg.recordUrl || ""));
    await closeViewerTab(viewerTabId);
    viewerTabId = null;
    if (!staged.media) {
      const reason = staged.skipped || "no download arrived from Ancestry's viewer";
      await markFailed(msg.apid, token, reason, pageLabel);
      return { ok: false, error: reason, image_skipped: reason };
    }
    media.push(staged.media);

    return await postAndShape({
      token,
      url: msg.recordUrl,
      html: msg.recordHtml,
      note: msg.note,
      media,
      apid: msg.apid,
      pageLabel,
      remine: msg.remine,
      // The whole point: an Ancestry image capture. The backend measures the bytes and is
      // the authority on full vs degraded — image_intent only says one WAS expected.
      imageIntent: "expected",
      missed: [],
      gating: "gated",
      render,
      stagedDownloadId: staged.downloadId,
      imageSkipped: staged.skipped,
    });
  } catch (err) {
    await closeViewerTab(viewerTabId);
    const reason = "one-call capture failed: " + (err && err.message ? err.message : err);
    await markFailed(msg.apid, token, reason, pageLabel);
    return { ok: false, error: reason };
  }
}

// Content scripts message us here. `return true` keeps the channel open for the
// async reply (MV3 requirement). `sender.tab.id` is the tab we render to PDF — it
// is the authoritative id of the page that asked to be captured, so a capture can
// never render some other tab the user happens to be looking at.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return;
  const tabId = sender && sender.tab ? sender.tab.id : null;

  // Arm BEFORE Ancestry's own Download control is clicked. This path holds no Ancestry
  // selectors: the click comes from whoever is driving the tab in the user's own session,
  // using the provider's own subscriber download feature.
  // We only listen for the file it produces (see download-intercept.js).
  if (msg.type === "capsule:arm-download") {
    // `msg.host` is the content script's own `location.hostname`, not anything the page
    // event supplied — see the listener in content.js. The `ancestry` profile is what lets
    // an export arrive from ancestrycdn.com while the viewer sits on ancestry.co.uk; every
    // other site serves its own export from its own domain and needs no entry.
    armDownload({
      key: msg.key || msg.apid,
      tabId,
      host: msg.host,
      profile: armProfileFor({ apid: msg.apid, host: msg.host }),
    }).then(sendResponse);
    return true;
  }

  if (msg.type === "capsule:capture") {
    doCapture(msg, tabId).then(sendResponse);
    return true;
  }

  // REQ-002: the one-call flow, driven from the record page.
  if (msg.type === "capsule:capture-ancestry-image") {
    captureAncestryImage(msg, tabId).then(sendResponse);
    return true;
  }
});
