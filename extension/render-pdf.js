// Capsule: render the tab in view to a PDF, for archiving as capture media.
//
// A DOM snapshot is faithful but not *displayable* — you cannot hand a reviewer an
// outerHTML string and call it the record they would have seen. So on every capture
// we also print the live tab to PDF over the DevTools protocol (`chrome.debugger`
// -> `Page.printToPDF`) and attach the bytes as a media item. The bytes are
// synthesized by us from the already-loaded DOM: no request is made to the
// provider, and nothing leaves the machine — the backend hashes the PDF into the
// local capture folder like any other artifact.
//
// THE RENDER IS AN ENHANCEMENT, NEVER A GATE. Every failure path resolves to
// `{ skipped: reason }` — never a throw, never a partial item — so a capture that
// cannot be rendered still lands with its DOM snapshot intact. Callers depend on
// that: they attach media only when `data_base64` is present.
//
// Shared by both capture paths: the service worker `importScripts`es it
// (background.js), the popup loads it as a plain <script> (popup.html).

// Mirrors the backend's MAX_MEDIA_ITEM_BYTES (capsule/backend/app.py). This check
// is not a nicety: an oversized media item is a 422 that fails the WHOLE capture —
// DOM snapshot and all — before anything is written. A sprawling page whose render
// blows the cap must therefore drop the PDF here, degrading to snapshot-only
// rather than losing the capture.
const RENDER_MAX_BYTES = 25 * 1024 * 1024; // 25 MiB

// A wedged renderer must not hang the capture. On timeout we fall through to the
// `finally` detach, which cancels the in-flight print.
const RENDER_TIMEOUT_MS = 15000;

// REQ-015 §D: the whole page as Chrome saves it (MHTML), taken in the same debugger
// session as the PDF, from what the tab has already loaded. Measured on a local page that
// logs every request: `Page.captureSnapshot` made none. Its own timeout, AFTER the PDF is
// in hand, so a slow snapshot can never cost the PDF.
const PAGE_ARCHIVE_TIMEOUT_MS = 20000;
const PAGE_ARCHIVE_FILENAME = "page.mhtml";
const PAGE_ARCHIVE_MIME = "multipart/related";
const PAGE_ARCHIVE_PROVENANCE = "render:page-mhtml";
// Mirrors the backend's MAX_MEDIA_COUNT and MAX_MEDIA_TOTAL_BYTES. Past either, the backend
// refuses the WHOLE capture, so the full page is the item we leave out, never the record.
const MEDIA_MAX_ITEMS = 25;
const MEDIA_MAX_TOTAL_BYTES = 100 * 1024 * 1024;

const RENDER_FILENAME = "record.pdf";
const RENDER_MIME = "application/pdf";
// Free-form provenance (backend: MediaIn.provenance) recording how the artifact was
// arrived at: not read off the page, but rendered by the capturing tool.
const RENDER_PROVENANCE = "render:page-pdf";

// `printToPDF` paginates the whole document by definition — there is no "full page"
// flag to set. But its defaults strip what makes a record legible, hence:
const PRINT_PARAMS = {
  printBackground: true, // record pages are styled scans/tables; without this the PDF is skeletal
  displayHeaderFooter: false, // Chrome's date/URL furniture is not evidence
  preferCSSPageSize: false, // our paper size, not a print stylesheet's
};

// -- Overlay suppression (BUG-001) -------------------------------------------
//
// Chrome's print pipeline FLATTENS a top-most full-viewport layer into an opaque
// full-page fill emitted AFTER all the text. So an agent's "I am driving this tab"
// glow — Claude-in-Chrome paints one at z-index 2147483646 — turns the render into a
// white page with a perfect, invisible text layer beneath it. Every byte-level check
// passes (magic bytes, size, %%EOF, SHA-256, text extraction) while a human opening
// the file sees nothing: unreadable evidence that reports success.
//
// So suppress the known overlays for the print — and, because the next overlay will
// come from some extension we have never heard of, also anything that LOOKS like
// agent/extension chrome (fixed, effectively full-viewport, absurd z-index).
//
// Done as an `@media print` stylesheet, NOT display:none, deliberately: printToPDF
// renders with print media, so the effect on the PDF is identical, while the page the
// researcher is looking at is never mutated. If a restore is ever missed (timeout,
// closed tab), what leaks is an inert style tag rather than a live page with pieces
// invisibly removed.
const HIDE_STYLE_ID = "capsule-render-hide";
const HIDE_MARK_ATTR = "data-capsule-render-hide";
const HIDE_SELECTORS = [
  // Claude-in-Chrome agent chrome — the proven cause of BUG-001.
  "#claude-agent-glow-border",
  "#claude-agent-glow-border-inner",
  "#claude-phantom-cursor",
  // Capsule's own UI. An archival record must not contain the instrument that made
  // it: without this the PDF shows our own "Capturing…" toast and pill.
  "#capsule-capture-btn",
  "#capsule-toast",
  "#capsule-key-prompt",
];

// -- Ads (REQ-015 §C, owner 2026-10-29: "hide the ads by default") -----------------
//
// Hidden for the PDF and the full page by the conventions ad networks use, and NOTHING
// broader. Deliberately not iframes, embeds, objects or dialogs in general: certificate
// sites show the record itself in an <embed> or <iframe> (media-detect.js), and hiding one
// would erase the evidence from its own archive. An iframe goes only when its host is an
// ad network. Measured before shipping on all 478 captured pages in the owner's archive: the
// matches were Find a Grave's ad slots, Ancestry's invisible 0x0 tracking frames and
// Newspapers.com's ad frames, and none held any text of a fact read from its record.
// tests/test_req_015_render_ads.py keeps this list from growing into record content.
const AD_SELECTORS = [
  '[id^="div-gpt-ad"]', '[id*="google_ads"]', "ins.adsbygoogle", "[data-ad]",
  "[data-ad-slot]", "[data-ad-unit]", "[data-google-query-id]", '[id^="ad-"]',
  '[class^="ad-"]', '[class*=" ad-"]', '[class~="ad"]', '[class~="ads"]',
  '[class*="advert"]', '[aria-label="Advertisement" i]',
];
const AD_FRAME_HOSTS =
  "(^|\\.)(doubleclick\\.net|googlesyndication\\.com|googleadservices\\.com|" +
  "amazon-adsystem\\.com|adnxs\\.com|criteo\\.(com|net)|taboola\\.com|outbrain\\.com|" +
  "rubiconproject\\.com|pubmatic\\.com|openx\\.net|casalemedia\\.com|moatads\\.com|" +
  "mediatradecraft\\.com|bounceexchange\\.com)$";

// Runs in the page (CDP Runtime.evaluate). Returns what it suppressed, so a heuristic
// hit is reported rather than silently changing what the render contains.
//
// Hides with a DIRECT inline `display:none !important`, not an `@media print` rule.
// The print-media approach shipped first and did NOT work: the E2E render still
// contained Capsule's own toast and pill even though both ids were in the list. The
// most likely reason is that print emulation is not in force for `printToPDF` on a
// tab another CDP client is driving, so `@media print` rules simply never match.
// Inline display:none needs no media assumption at all — it is what the bug reporter
// hid the overlays with by hand, and it demonstrably worked. Correctness beats the
// flicker-free elegance of the stylesheet.
//
// Every mutation is recorded and undone by RESTORE_EXPRESSION.
const HIDE_EXPRESSION = `(() => {
  const SELECTORS = ${JSON.stringify(HIDE_SELECTORS)};
  const AD_SELECTORS = ${JSON.stringify(AD_SELECTORS)};
  const AD_FRAME_HOSTS = new RegExp(${JSON.stringify(AD_FRAME_HOSTS)}, "i");
  const MARK = ${JSON.stringify(HIDE_MARK_ATTR)};
  const hid = [];
  const heuristic = [];
  const ads = [];

  // Remember the exact inline display value we overwrite, on the element itself, so
  // the restore puts back precisely what was there (usually nothing) rather than
  // guessing "" and clobbering a page's own inline style.
  const hide = (el, label, into) => {
    if (!el || el.hasAttribute(MARK)) return;
    el.setAttribute(MARK, el.style.getPropertyValue("display") || "");
    el.style.setProperty("display", "none", "important");
    into.push(label);
  };

  for (const sel of SELECTORS) {
    for (const el of document.querySelectorAll(sel)) hide(el, sel, hid);
  }

  // Ads. Each one is marked like every other hidden element, so the full page records
  // exactly what was hidden, and reported back so the outcome says how many.
  for (const sel of AD_SELECTORS) {
    for (const el of document.querySelectorAll(sel)) hide(el, "ad " + sel, ads);
  }
  for (const frame of document.querySelectorAll("iframe[src]")) {
    let host = "";
    try { host = new URL(frame.src, location.href).hostname; } catch (e) { continue; }
    if (AD_FRAME_HOSTS.test(host)) hide(frame, "ad frame " + host, ads);
  }

  // The unknown-overlay heuristic. An element that is fixed, covers essentially the
  // entire viewport, and sits at an absurd z-index is chrome, not content — no record
  // page paints itself that way. This is what defends against the NEXT overlay, from
  // an extension we have never heard of.
  const vw = window.innerWidth, vh = window.innerHeight;
  const all = document.body ? document.body.querySelectorAll("*") : [];
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (cs.position !== "fixed" && cs.position !== "sticky") continue;
    if (cs.display === "none" || cs.visibility === "hidden") continue;
    const z = parseInt(cs.zIndex, 10);
    if (!Number.isFinite(z) || z <= 1000000) continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < 0.9 * vw * vh) continue;
    hide(el, (el.id && "#" + el.id) || el.tagName.toLowerCase() + "[z=" + z + "]", heuristic);
  }

  return JSON.stringify({ hid, heuristic, ads });
})()`;

const RESTORE_EXPRESSION = `(() => {
  for (const el of document.querySelectorAll("[${HIDE_MARK_ATTR}]")) {
    const prev = el.getAttribute("${HIDE_MARK_ATTR}");
    if (prev) el.style.setProperty("display", prev);
    else el.style.removeProperty("display");
    el.removeAttribute("${HIDE_MARK_ATTR}");
  }
  return "ok";
})()`;

async function evaluateInPage(target, expression) {
  const reply = await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
    expression,
    returnByValue: true,
  });
  return reply && reply.result ? reply.result.value : null;
}

function withTimeout(promise, ms, reason) {
  let timer;
  const timeout = new Promise((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error(reason)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/** Base64 of the snapshot text. MHTML is 7-bit by construction; anything else is UTF-8. */
function base64OfText(text) {
  if (!/[^\x00-\x7f]/.test(text)) return btoa(text);
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/** Decoded size of a base64 payload. */
function decodedBytes(b64) {
  return Math.floor(((b64 || "").length * 3) / 4);
}

/**
 * Append the full page (`render.page`) to a capture's media, last, if it fits.
 *
 * Returns null when appended, else why not. The backend refuses a whole capture over 25
 * items or 100 MiB, so the full page is checked against what is already going and left
 * out rather than risk the record. Only `render.page` goes on the wire: it holds exactly
 * MediaIn's fields (BUG-002).
 */
function appendFullPage(media, render) {
  if (!render || !render.page) return (render && render.page_skipped) || "not made";
  if (media.length >= MEDIA_MAX_ITEMS) return "the capture already holds " + MEDIA_MAX_ITEMS + " files";
  const total = media.reduce((n, m) => n + decodedBytes(m.data_base64), 0);
  if (total + decodedBytes(render.page.data_base64) > MEDIA_MAX_TOTAL_BYTES) {
    return "with it the capture would exceed " + MEDIA_MAX_TOTAL_BYTES / 1024 / 1024 + " MiB";
  }
  media.push(render.page);
  return null;
}

function messageOf(err) {
  if (err && err.message) return err.message;
  if (chrome.runtime.lastError) return chrome.runtime.lastError.message;
  return String(err);
}

/**
 * Render `tabId` to a PDF media item.
 *
 * The wire payload is returned SEPARATELY from the local metadata, and that
 * separation is load-bearing (BUG-002). This function used to return one flat object
 * that callers spread straight into the POST body; when a `hid` field was added here,
 * it rode along onto the wire, hit the backend's `extra="forbid"` MediaIn, and 422'd
 * EVERY capture. `media` is exactly the four fields the backend accepts and nothing
 * else — so a field added here can never again break the wire contract.
 *
 * @param {number} tabId
 * @returns {Promise<{media: {data_base64: string, filename: string, mime: string,
 *                            provenance: string},
 *                    hid: string[]} | {skipped: string}>}
 *   The media item to POST plus what was suppressed to produce it, or `{skipped}`
 *   with a human-readable reason. Never rejects.
 */
async function renderPagePdf(tabId) {
  if (tabId == null) return { skipped: "no tab id" };

  const target = { tabId };
  // Held so cleanup can wait on the attach's *eventual* outcome. A plain `attached`
  // boolean would race: if the timeout fires while attach is still pending, the flag
  // flips true after cleanup has already run, and the tab stays attached forever.
  let attaching = null;
  try {
    // attach() fails when another client already holds this tab's debugger —
    // DevTools open, or a CDP-driven automation tool. A legitimate skip, not an error.
    // BOTH steps live inside the timeout: a hung attach would otherwise hang the
    // whole capture, which is exactly what the render is forbidden to do.
    attaching = chrome.debugger.attach(target, "1.3");
    const printed = await withTimeout(
      attaching.then(async () => {
        // Suppress agent/extension chrome first — an overlay left in place is
        // flattened into an opaque fill that blanks the whole render (BUG-001).
        // A failure to hide is NOT fatal: we would rather archive a page with a
        // glow on it than archive nothing. It is reported, not swallowed.
        let suppressed = null;
        try {
          suppressed = JSON.parse(await evaluateInPage(target, HIDE_EXPRESSION));
        } catch (err) {
          suppressed = { hid: [], heuristic: [], error: messageOf(err) };
        }
        const result = await chrome.debugger.sendCommand(
          target,
          "Page.printToPDF",
          PRINT_PARAMS
        );
        return { result, suppressed };
      }),
      RENDER_TIMEOUT_MS,
      "render timed out after " + RENDER_TIMEOUT_MS / 1000 + "s"
    );

    const result = printed.result;
    const suppressed = printed.suppressed || { hid: [], heuristic: [], ads: [] };
    const data = result && result.data;

    // The whole page, while the ads are still hidden, so the full page records what was
    // hidden (each element carries the mark). A failure leaves the PDF untouched.
    let page = null;
    let pageSkipped = null;
    try {
      const snap = await withTimeout(
        chrome.debugger.sendCommand(target, "Page.captureSnapshot", { format: "mhtml" }),
        PAGE_ARCHIVE_TIMEOUT_MS,
        "saving the page timed out after " + PAGE_ARCHIVE_TIMEOUT_MS / 1000 + "s"
      );
      const text = snap && snap.data;
      if (!text) pageSkipped = "the browser returned no page";
      else if (text.length > RENDER_MAX_BYTES) {
        pageSkipped = "page too large (" + Math.round(text.length / 1024 / 1024) + " MiB)";
      } else {
        // EXACTLY MediaIn's fields, as for the PDF (BUG-002).
        page = {
          data_base64: base64OfText(text),
          filename: PAGE_ARCHIVE_FILENAME,
          mime: PAGE_ARCHIVE_MIME,
          provenance: PAGE_ARCHIVE_PROVENANCE,
        };
      }
    } catch (err) {
      pageSkipped = messageOf(err);
    }
    const ads = suppressed.ads || [];
    const extras = { page, page_skipped: pageSkipped, ads };

    if (!data) return { skipped: "renderer returned no data", ...extras };

    // base64 inflates by 4/3; compare the DECODED size against the backend's cap.
    const bytes = (data.length * 3) / 4;
    if (bytes > RENDER_MAX_BYTES) {
      return {
        skipped:
          "render too large (" +
          Math.round(bytes / 1024 / 1024) +
          " MiB > " +
          RENDER_MAX_BYTES / 1024 / 1024 +
          " MiB cap)",
        ...extras,
      };
    }

    return {
      // EXACTLY the fields the backend's MediaIn accepts, listed explicitly. Do not
      // spread anything else in here: MediaIn is `extra="forbid"`, so one stray key
      // 422s the whole capture (BUG-002). No source_url — these bytes came from no
      // URL; we made them.
      media: {
        data_base64: data,
        filename: RENDER_FILENAME,
        mime: RENDER_MIME,
        provenance: RENDER_PROVENANCE,
      },
      // LOCAL metadata — for the toast and the self-test, never for the wire. What we
      // suppressed to get a clean print: an unknown overlay caught by the heuristic is
      // surfaced, never silent, because it changed what the archived record looks like.
      hid: (suppressed.hid || []).concat(suppressed.heuristic || []),
      // The full page (MediaIn-shaped, or null) and why not; appended by the caller with
      // appendFullPage, last, so it can never displace the record. The ads hidden, for
      // the outcome.
      ...extras,
    };
  } catch (err) {
    return { skipped: messageOf(err) };
  } finally {
    // Restore the page, THEN release the tab. Never leave a tab attached: a stuck
    // debugger banner outlives the capture and blocks the next render. Both steps
    // run only if our attach actually succeeded, however late that turns out to be.
    //
    // Deliberately NOT awaited: the caller must not wait on cleanup (that would
    // reintroduce the very hang the timeout exists to prevent). A rejected attach
    // means we never held the tab, so there is nothing to release; a rejected
    // restore/detach means the tab is already gone. The restore is idempotent, so
    // running it after a timeout that already ran it is harmless.
    if (attaching) {
      attaching
        .then(async () => {
          try {
            await evaluateInPage(target, RESTORE_EXPRESSION);
          } finally {
            await chrome.debugger.detach(target);
          }
        })
        .catch(() => {});
    }
  }
}
