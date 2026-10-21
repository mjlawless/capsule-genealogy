// Capsule: find the record files a page EMBEDS — and never mistake a render for them.
//
// BUG-007. A NYC birth certificate is served through Chrome's native PDF plugin: no <img>,
// no <a href="...pdf">, just an <embed> whose src is a time-limited, SAS-signed Azure blob
// URL. The certificate cannot be reconstructed later — the signature expires — so it has to
// be taken from the page while it is live.
//
// The capture reported `PDF ✓, verify OK` and archived a 977x1082 SCREENSHOT of the most
// important record on the line. Everything was green. That is the exact failure this whole
// project exists to prevent: A RENDER OF THE PAGE IS NOT THE RECORD.
//
// This runs as a content script, so it is inside the page — past auth, past the CSP — and
// can see what the page itself fetched.
//
// Runs in BOTH capture paths. The popup has had this capability since July; the
// agent-trigger path (the pill, and `capsule:capture`) never did — which is why something
// that worked on 9 July silently stopped working the moment the research moved to an agent.

const MEDIA_MAX_ITEMS = 8;
const MEDIA_MAX_BYTES = 25 * 1024 * 1024; // mirrors the backend's per-item cap

// A record file, as opposed to page furniture. Deliberately narrow: we want the document the
// page is SHOWING, not every asset it happens to load.
//
// BUG-008: this used to match `/pdf` ANYWHERE in the URL, so `.../plugins/pdfobject/
// pdfobject.min.js` looked like a PDF. It was fetched, renamed `.pdf`, and declared
// `application/pdf`. Match the PATH's extension, not a substring of the whole URL — a query
// string may say anything, and a directory called "pdfobject" is not a document.
const _PDF_PATH = /\.pdf$/i;
const _PDF_TYPE = /application\/pdf/i;

// The site's own furniture: stylesheets, sprites, plugins, logos. A RECORD does not live in
// a static-asset directory — the NYC certificate is a signed blob on a different host
// entirely, while the logo and the PDF plugin are under `/static/` on the page's own host.
// That is the cheap, principled rule, and it catches both halves of BUG-008 at once.
const _FURNITURE_PATH = /\/(static|assets|dist|build|vendor|plugins?|scripts?|js|css|img|images|icons?|sprites?|themes?)\//i;
const _FURNITURE_NAME = /(logo|sprite|icon|banner|header|footer|watermark|placeholder|spinner)/i;

/** Is this the page's own furniture rather than the record it is showing? */
function _isFurniture(url) {
  try {
    const parsed = new URL(url, location.href);
    if (_FURNITURE_PATH.test(parsed.pathname)) return true;
    if (_FURNITURE_NAME.test(parsed.pathname)) return true;
    return false;
  } catch (_e) {
    return false;
  }
}

// A THUMBNAIL is not the record (BUG-012). On an image-backed Ancestry record page the only
// image big enough to clear the 400px floor is the ~525x600 preview, and its URL is the
// provider's thumbnail endpoint: `.../api/media/retrieval/v2/thumbnail/namespaces/...`. Banked
// as `page:embedded` media it looked like the scan while the real one (1636x1868) sits behind
// the Download button — the capture said success and stored a picture nobody can read. So a
// `/thumbnail/` (or `/thumbnails/`) path segment is never a record file: never bank it.
const _THUMBNAIL_PATH = /\/thumbnails?\//i;

/** Does this URL name a thumbnail endpoint rather than a full-resolution record file? */
function _isThumbnail(url) {
  try {
    return _THUMBNAIL_PATH.test(new URL(url, location.href).pathname);
  } catch (_e) {
    return false;
  }
}

/** Does this URL name an actual PDF document? */
function _isPdfUrl(url) {
  try {
    return _PDF_PATH.test(new URL(url, location.href).pathname);
  } catch (_e) {
    return false;
  }
}

function _absolute(url) {
  try {
    return new URL(url, location.href).href;
  } catch (_e) {
    return null;
  }
}

function _filenameOf(url) {
  try {
    const path = new URL(url).pathname;
    const name = decodeURIComponent(path.split("/").pop() || "");
    return name || "record.pdf";
  } catch (_e) {
    return "record.pdf";
  }
}

/**
 * Every record file this page embeds.
 *
 * @returns {{inline: object[], remote: object[]}}
 *   `inline` — blob:/data: sources, whose bytes only the PAGE can read (the extension cannot
 *   fetch a page's blob: URL). Read here, in place.
 *   `remote` — http(s) sources. Their bytes are fetched by the service worker, which is not
 *   bound by the page's CSP and — with a host grant — not by CORS either. That is the only
 *   way to read the Azure blob: the page's own CSP blocks a page-context fetch of it.
 */
function detectPageMedia() {
  const found = { inline: [], remote: [] };
  const seen = new Set();

  const add = (rawUrl, hintMime) => {
    const url = _absolute(rawUrl);
    if (!url || seen.has(url)) return;
    if (found.inline.length + found.remote.length >= MEDIA_MAX_ITEMS) return;
    // The site's own furniture is not evidence (BUG-008). A logo in every capture folder
    // invites exactly the confusion the manifest exists to prevent.
    if (_isFurniture(url)) return;
    // A thumbnail is not the record (BUG-012). Banking Ancestry's ~600px preview as though it
    // were the scan is the "believe you hold a record you do not hold" footgun this whole
    // sweep is meant to avoid — drop it, and let the arm-download flow get the real image.
    if (_isThumbnail(url)) return;
    seen.add(url);

    const item = { url, filename: _filenameOf(url), mime: hintMime || null };
    if (url.startsWith("blob:") || url.startsWith("data:")) {
      found.inline.push(item);
    } else if (url.startsWith("http:") || url.startsWith("https:")) {
      found.remote.push(item);
    }
    // Anything else (chrome-extension://, filesystem:) is not a record.
  };

  // The document the page is DISPLAYING. Chrome's native PDF plugin is an <embed>, and its
  // src carries the signed URL — the plugin's own toolbar is not in the accessibility tree,
  // so its Download button cannot be clicked, but the src is right there in the DOM.
  for (const el of document.querySelectorAll("embed[src], object[data], iframe[src]")) {
    const src = el.getAttribute("src") || el.getAttribute("data");
    if (!src) continue;
    const type = el.getAttribute("type") || "";
    // A blob:/data: embed carries no filename, so the element's declared TYPE is all we
    // have — but for an http URL the path must actually name a PDF.
    const isBlobbish = src.startsWith("blob:") || src.startsWith("data:");
    if (_isPdfUrl(src) || _PDF_TYPE.test(type) || (isBlobbish && _PDF_TYPE.test(type))) {
      add(src, "application/pdf");
    }
  }

  // A large scan served as an image. The 400px floor keeps logos and icons out.
  for (const img of document.querySelectorAll("img[src]")) {
    if ((img.naturalWidth || 0) >= 400 && (img.naturalHeight || 0) >= 400) {
      add(img.getAttribute("src"), null);
    }
  }

  // What the page ACTUALLY FETCHED — the last resort, and the one that catches a viewer
  // handing bytes to a plugin without ever putting the URL in the DOM. `performance` sees
  // the request the page made, so a signed URL stays observable even when nothing links it.
  try {
    for (const entry of performance.getEntriesByType("resource")) {
      if (_isPdfUrl(entry.name)) add(entry.name, "application/pdf");
    }
  } catch (_e) {
    /* performance API unavailable — the DOM scan above still stands */
  }

  return found;
}

/**
 * Read a blob:/data: source, in the page, where its bytes actually live.
 *
 * Returns a ready-to-POST media item, or null. Never throws: a file we cannot read is
 * REPORTED and the capture continues — but it is never silently dropped. That silence is
 * BUG-007.
 */
async function readInlineMedia(item) {
  try {
    const resp = await fetch(item.url);
    const buffer = await resp.arrayBuffer();
    if (!buffer.byteLength || buffer.byteLength > MEDIA_MAX_BYTES) return null;

    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return {
      data_base64: btoa(binary),
      filename: item.filename,
      mime: item.mime || resp.headers.get("content-type") || "application/pdf",
      // These bytes came from the page's own blob — say so.
      provenance: "page:embedded",
    };
  } catch (_e) {
    return null;
  }
}
