// Capsule: ingest the file the SITE'S OWN download button produces.
// REQ-001 R1.2 (Ancestry) generalized to any tile viewer by REQ-007 §2.
//
// WHY THIS EXISTS. A record scan behind a tile viewer is painted into a <canvas> at screen
// resolution. There is no <img>, no PDF, and the media API 403s by design. The only
// sanctioned route to the full-resolution image is the provider's own subscriber feature:
// Ancestry's image viewer -> Tool menu -> Download; Archion's toolbar -> Bildausschnitt
// herunterladen. So Capsule does not fetch the image. It waits for the site's own download
// and ingests the file that lands.
//
// We do NOT reverse-engineer the private media/tile API. Ancestry's 403s deliberately;
// Archion's tile URLs are signed and expiring. Rebuilding a scan from intercepted tiles is
// fragile AND a terms problem on both. The official export is the compliant integration
// point, and it is the same posture on every site — which is exactly why this file should
// never have been hard-wired to one host.
//
// THE ONE THING THE DOWNLOADS API CANNOT DO: hand us the bytes. `chrome.downloads` gives
// events and metadata — never file contents. The file lands on the user's disk, and the
// only way to read it back is a `fetch("file:///...")` from the extension, which requires
// the one-time "Allow access to file URLs" toggle at chrome://extensions. Capsule already
// depends on that toggle for local-PDF capture, so it is not a new imposition — but it IS
// the single point this whole path hangs on, so we check it BEFORE arming and fail loudly
// rather than arming a flow whose last step is known-broken.
//
// CORRELATION — the safety property, and the reason the host check exists at all. A
// download is ingested only if it arrives inside an armed window, from a host belonging to
// the page we armed on, after we armed. Anything else is left ALONE: never read, never
// deleted. The researcher's own unrelated downloads are not ours to touch.

const ARMED_KEY = "capsuleArmedDownload";
// Long enough for a real download; short enough that a stale arm cannot silently swallow
// a download the researcher started themselves ten minutes later.
const ARM_TIMEOUT_MS = 60000;
const DOWNLOAD_MAX_BYTES = 25 * 1024 * 1024; // mirrors the backend's MAX_MEDIA_ITEM_BYTES

const INGESTIBLE_MIME = /^(image\/|application\/pdf)/i;

// Providers that serve their downloads from a SIBLING domain, which a suffix match on the
// armed page's own host cannot reach: an Ancestry export can arrive from ancestrycdn.com
// while the viewer is on ancestry.co.uk. Nothing else needs an entry — the general rule
// below covers a site that serves its own export from its own domain, which is the normal
// case and is what Archion does.
//
// This table lives in the EXTENSION, never in the arm message. The arm event is dispatched
// by page JS, and a page that could name its own allow-list could arm Capsule to swallow
// the next download from anywhere. What the page may say is WHICH RECORD it is arming for;
// what it may never say is which hosts to trust.
const SIBLING_HOSTS = {
  // Ancestry's own national domains, named. This was `ancestry(cdn)?\.[a-z.]+$`, which let
  // ANY host with an "ancestry" label through: `ancestry.evil.com` passed as Ancestry, both
  // for choosing the profile and for accepting a download (found 2026-09-29, BUG-018 review).
  ancestry: /(^|\.)ancestry(cdn)?\.(com|ca|de|fr|it|se|mx|co\.uk|com\.au)$/i,
  // BUG-018: the viewer is on sok.riksarkivet.se, but its IIIF image service, and with it
  // every download the viewer offers, is on lbiiif.riksarkivet.se. Those are siblings, not
  // parent and child, so the suffix rule alone refused the export.
  riksarkivet: /(^|\.)riksarkivet\.se$/i,
  // BUG-022: the viewer is on www.newspapers.com, but "Save as JPG" is served from
  // img.newspapers.com (confirmed from the saved file's own download provenance).
  newspapers: /(^|\.)newspapers\.com$/i,
};

// Profiles picked by the ARMED PAGE'S OWN HOST. Ancestry is not here: its profile is keyed
// to the apid (a record download), as it always was.
const HOST_SELECTED_PROFILES = ["riksarkivet", "newspapers"];

// A download we refused during an armed window, kept under its OWN key: writing it into
// the armed record would race the listener staging the real download a moment later.
const REFUSED_KEY = "capsuleRefusedDownload";

function hostOf(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch (_e) {
    return "";
  }
}

/**
 * Does `host` belong to the site we armed on? PURE — the correlation rule, testable alone.
 *
 * Suffix match in both directions, so a viewer on `www.archion.de` accepts an export from
 * `archion.de` or `download.archion.de`, and vice versa. Deliberately NOT a registrable-
 * domain (eTLD+1) comparison: that needs a public-suffix list to be correct, and getting it
 * wrong widens the check rather than narrowing it. A dotted-suffix match cannot over-match
 * — `evil-archion.de` does not end in `.archion.de` — which is the direction to be wrong in.
 *
 * @param {string} host      the download's host
 * @param {string} armedHost the host of the page that armed
 * @param {string} profile   optional key into SIBLING_HOSTS
 */
function hostBelongsToArmedSite(host, armedHost, profile) {
  const h = String(host || "").toLowerCase();
  const a = String(armedHost || "").toLowerCase();
  if (!h) return false;
  if (a && (h === a || h.endsWith("." + a) || a.endsWith("." + h))) return true;
  const sibling = profile && SIBLING_HOSTS[profile];
  return sibling ? sibling.test(h) : false;
}

/**
 * Does this download belong to the record we armed for? PURE, so it can be tested alone.
 *
 * The URL the bytes CAME FROM decides. The referrer is only consulted when the download has
 * no host of its own — a `blob:` or `data:` URL, which is how a viewer that builds its
 * export client-side hands it over (`hostOf("blob:https://…")` is the empty string, not the
 * inner host). That case genuinely has no other signal.
 *
 * It used to be `[url, finalUrl, referrer].some(...)`, which let the referrer OVERRIDE a
 * mismatching url: during the armed window any image or PDF started from the armed page was
 * claimed, whatever host served it. Under the old Ancestry-only regex that was nearly
 * harmless; generalized to every site it is not, because `cleanUpDownload` DELETES the file
 * from the user's Downloads afterwards. Claiming a download we did not arm for is bad; then
 * erasing it is the part that is unrecoverable.
 *
 * The same reasoning applies to a redirect: the FINAL URL is where the bytes came from, so
 * it decides whenever it has a host, and the original URL only counts when it does not. It
 * used to be `[url, finalUrl].some(...)`, so a page armed on evil.example could start a
 * download at its own address that redirects to a provider file, and the matching ORIGINAL
 * host claimed the provider's bytes (Codex adversarial review, fifth pass, 2026-09-29).
 * A stranger redirecting INTO the armed site is still accepted: the bytes are the site's.
 */
function sourceHostOf(item) {
  return hostOf(item.finalUrl) || hostOf(item.url);
}

function fromArmedSite(item, armed) {
  const source = sourceHostOf(item);
  if (source) return hostBelongsToArmedSite(source, armed.host, armed.profile);
  return hostBelongsToArmedSite(hostOf(item.referrer), armed.host, armed.profile);
}

/**
 * Which SIBLING_HOSTS profile an arm gets. PURE, and decided here from the apid and the
 * content script's own `location.hostname`, never from anything the page event supplied: a
 * profile widens the correlation rule, so a page that could name one could name its own
 * allow-list.
 *
 * @param {{apid?: string, host?: string}} req
 * @returns {string | undefined}
 */
function armProfileFor({ apid, host } = {}) {
  const h = String(host || "").toLowerCase();
  if (!h) return undefined;
  // The apid comes from the PAGE EVENT: it names a record, it authorizes nothing. It used to
  // select the ancestry profile on its own, so any page could arm with a made-up apid and
  // claim (then, via cleanUpDownload, delete) the researcher's next Ancestry download
  // (Codex adversarial review, 2026-09-29). The page must actually be on Ancestry.
  if (apid) return SIBLING_HOSTS.ancestry.test(h) ? "ancestry" : undefined;
  return HOST_SELECTED_PROFILES.find((name) => SIBLING_HOSTS[name].test(h));
}

/**
 * The host a download came from, for a diagnostic. Host only, never the full URL, and the
 * same host `fromArmedSite` judged: the final URL's, else the original's, else the referrer.
 */
function downloadHostOf(item) {
  return sourceHostOf(item) || hostOf(item.referrer);
}


/** Is the byte-read path even available? If not, say so before anything else happens. */
async function fileAccessAllowed() {
  try {
    return await chrome.extension.isAllowedFileSchemeAccess();
  } catch (_e) {
    return false;
  }
}

/** An absolute on-disk path -> a file:// URL the extension can fetch. */
function fileUrlOf(path) {
  const normalized = String(path).replace(/\\/g, "/");
  const withSlash = normalized.startsWith("/") ? normalized : "/" + normalized;
  // Encode each segment: a Windows Downloads path routinely contains spaces.
  const encoded = withSlash.split("/").map(encodeURIComponent).join("/");
  return "file://" + encoded.replace(/%3A/gi, ":"); // keep the "C:" drive colon intact
}

/**
 * Arm interception for one record. Serial by design: arming while armed is REFUSED,
 * because a second armed window could claim the first record's download and file a scan
 * of record A under record B — a silent, hashed, cited mis-capture.
 *
 * `key` identifies the record the download will be filed under. On Ancestry it is the
 * `_APID`; on any other site it is the record URL. It is what `takeStagedDownload` must be
 * given back, and the mismatch check there is the same anti-crossover guard as before —
 * only the shape of the identifier changed.
 *
 * `host` MUST come from the content script's own `location.hostname`, never from the page
 * event's detail. It is the whole basis of the correlation check, and a page that could
 * name it could point Capsule at a download from anywhere.
 *
 * @param {{key: string, tabId: number, host: string, profile?: string}} req
 * @returns {Promise<{ok: true} | {ok: false, reason: string}>}
 */
async function armDownload(req) {
  const { key, tabId, host, profile } = req || {};
  if (!key) return { ok: false, reason: "no record key supplied" };
  // Without a host there is no correlation rule left, and an unconditional arm would claim
  // whatever download happens next. Refuse rather than degrade to "trust anything".
  if (!host) {
    return { ok: false, reason: "cannot arm: the page did not report a host" };
  }
  // A profile WIDENS the correlation rule, so it is authorized HERE, centrally, for every
  // caller: only a page actually on the profile's own domains may arm with it. The arm
  // handler picks profiles with armProfileFor, but the one-call Ancestry flow passes
  // "ancestry" itself, and its identity guard checks URL PATHS only, so an attacker page
  // at evil.example/search/collections/.../records/... used to arm with Ancestry's
  // profile (Codex adversarial review, second pass, 2026-09-29).
  if (profile && !(SIBLING_HOSTS[profile] && SIBLING_HOSTS[profile].test(host))) {
    return { ok: false, reason: "cannot arm: this page is not on the site it claims to be" };
  }

  if (!(await fileAccessAllowed())) {
    return {
      ok: false,
      reason:
        'Capsule cannot read downloaded files. Enable "Allow access to file URLs" for ' +
        "Capsule at chrome://extensions (one-time), then retry.",
    };
  }

  return withArmLock(async () => {
    const existing = await readArmed();
    if (existing && Date.now() - existing.armedAt < ARM_TIMEOUT_MS) {
      // Generic: this reaches the page, and the armed key may be ANOTHER tab's record.
      return { ok: false, reason: "a download is already armed for another capture" };
    }

    // A new arm starts with no refusal on record: an expired arm is overwritten here without
    // going through clearArmed, and its note would otherwise linger in session storage.
    await chrome.storage.session.remove(REFUSED_KEY);
    await chrome.storage.session.set({
      [ARMED_KEY]: { key, tabId, host, profile, armedAt: Date.now(), staged: null },
    });
    return { ok: true };
  });
}

async function readArmed() {
  return (await chrome.storage.session.get(ARMED_KEY))[ARMED_KEY] || null;
}

async function clearArmed() {
  await chrome.storage.session.remove([ARMED_KEY, REFUSED_KEY]);
}

/**
 * Remember WHY a download that landed inside the armed window was turned away (BUG-018).
 * It is never staged and never read. It only lets the timeout say what actually happened,
 * instead of blaming the site for a download that arrived and was refused by us. Tied to
 * this arm (key + armedAt), so it cannot surface against a later one.
 */
async function noteRefusal(armed, reason) {
  try {
    await chrome.storage.session.set({
      [REFUSED_KEY]: { key: armed.key, armedAt: armed.armedAt, reason },
    });
  } catch (_e) {
    /* purely diagnostic: losing the note must never become an unhandled rejection */
  }
}

async function refusalFor(armed) {
  const note = (await chrome.storage.session.get(REFUSED_KEY))[REFUSED_KEY];
  return note && note.key === armed.key && note.armedAt === armed.armedAt ? note.reason : null;
}

// Every read-modify-write of the armed record runs through this one queue. The service
// worker is a single JS instance, so an in-memory chain serializes them; a worker restart
// ends every in-flight listener anyway. Without it, two downloads completing together both
// read the same unstaged arm, and the slower one wrote its STALE copy back after the first
// had been collected, resurrecting the arm and filing a second scan under the first record
// (Codex adversarial review, sixth pass, 2026-09-29; pre-existing).
let armLock = Promise.resolve();
function withArmLock(fn) {
  const run = armLock.then(fn);
  armLock = run.catch(() => {});
  return run;
}

/**
 * Stage `staged` into the arm the listener started from, ONLY if that exact arm (same key
 * and armedAt) is still current and still empty. Anything else (collected, cleared,
 * replaced by a newer arm, or already holding a download) means this download lost, and it
 * is dropped: never staged, so never handed to cleanUpDownload.
 */
async function stage(armed, staged) {
  return withArmLock(async () => {
    const current = await readArmed();
    if (!current || current.key !== armed.key || current.armedAt !== armed.armedAt) return false;
    if (current.staged) return false;
    await chrome.storage.session.set({ [ARMED_KEY]: { ...current, staged } });
    return true;
  });
}

function base64Of(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  // Chunked: a 20 MiB scan blows the argument limit if spread in a single call.
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/**
 * The downloads listener. Ingests ONLY a download belonging to the armed record.
 * Everything else is ignored — the user's own downloads are none of our business.
 */
function watchDownloads() {
  chrome.downloads.onChanged.addListener(async (delta) => {
    if (!delta.state || delta.state.current !== "complete") return;

    const armed = await readArmed();
    if (!armed) return; // nothing armed: this download is the user's, not ours
    // The window expired: do not claim a late arrival. Do NOT clear the arm here either.
    // Clearing on an unrelated download's event made a later capture's outcome depend on
    // whether the researcher downloaded something else, which any page could observe.
    // Expiry is settled where it is read: takeStagedDownload and armDownload.
    if (Date.now() - armed.armedAt > ARM_TIMEOUT_MS) return;
    if (armed.staged) return; // we already have one; never overwrite it with a second

    const [item] = await chrome.downloads.search({ id: delta.id });
    if (!item || !item.filename) return;
    // Only a download STARTED after the arm can be the one we armed for. One the researcher
    // started earlier that merely finishes inside the window is theirs: not read, not
    // claimed, and never deleted by cleanUpDownload. Unparseable counts as "earlier".
    if (!(Date.parse(item.startTime) >= armed.armedAt)) return;
    // Not the file we armed for. Left alone (never read, never deleted), but NOT silently:
    // BUG-018 was a real export refused here, then reported as "no download arrived".
    //
    // Said ONLY in the service-worker console, which no page can read. The capture's
    // reason reaches the page (toast, `data-capsule-last-outcome`), and any page can arm,
    // so even "a download from somewhere else arrived" would let it watch the researcher's
    // other downloads. The page-visible timeout is identical whether or not one did.
    if (!fromArmedSite(item, armed)) {
      console.warn(
        "[capsule] armed for " + (armed.host || armed.profile) + "; left alone a download from " +
          (downloadHostOf(item) || "an unknown host")
      );
      return;
    }
    if (item.mime && !INGESTIBLE_MIME.test(item.mime)) {
      await noteRefusal(
        armed,
        "the armed site's download was " + item.mime + ", not an image or PDF, so it was left alone"
      );
      return;
    }
    if (item.bytesReceived > DOWNLOAD_MAX_BYTES) {
      await stage(armed, { error: "the downloaded file is too large to archive" });
      return;
    }

    try {
      const resp = await fetch(fileUrlOf(item.filename));
      const buffer = await resp.arrayBuffer();
      if (!buffer.byteLength) throw new Error("the downloaded file was empty");
      await stage(armed, {
        downloadId: item.id,
        filename: item.filename.split(/[\\/]/).pop(),
        mime: item.mime || "image/jpeg",
        data_base64: base64Of(buffer),
      });
    } catch (err) {
      await stage(armed, {
        error:
          "could not read the downloaded file: " +
          (err && err.message ? err.message : String(err)) +
          '. Is "Allow access to file URLs" enabled for Capsule?',
      });
    }
  });
}

/**
 * The media item for the armed record's download, if one landed.
 *
 * `media` holds EXACTLY the four fields the backend's MediaIn accepts and nothing else —
 * the BUG-002 lesson: MediaIn is `extra="forbid"`, and one stray key 422s the whole
 * capture. Local metadata never rides onto the wire.
 *
 * @returns {Promise<{media: object, downloadId: number} | {skipped: string}>}
 */
// How long the capture will WAIT for an armed download to finish landing. The agent clicks
// Ancestry's Download and then fires the capture; the download is very often still in flight
// at that moment. Without this wait, the capture asked "is it here yet?", heard "no", and
// reported IMAGE ✗ failed — while the file was arriving a second later (BUG-010).
const STAGE_WAIT_MS = 20000;
const STAGE_POLL_MS = 300;

/**
 * CONSUMER AUTHORIZATION: may this caller collect what `armed` holds? Only the tab that
 * armed, and only while that tab is still on the armed site. `tabId` is Chrome's
 * `sender.tab.id`, and `host` is the capturing page's own address, taken from the content
 * script's `location.href`. The page can forge neither. `key` is page-supplied, and on its
 * own it let another tab (Codex adversarial review, third pass) or the same tab after
 * navigating to an attacker page (fourth pass) walk off with the staged download. The host
 * binding, rather than Chrome's per-document id, keeps the old Ancestry flow working: arm
 * in the viewer, capture from the record page, same site but a different document.
 */
function ownedBy(armed, tabId, host) {
  return (
    tabId != null &&
    armed.tabId === tabId &&
    hostBelongsToArmedSite(String(host || "").toLowerCase(), armed.host, armed.profile)
  );
}

async function takeStagedDownload(key, tabId, host) {
  let armed = await readArmed();
  // A stranger gets exactly the "nothing armed" answer and nothing is cleared, so it learns
  // nothing and disturbs nothing.
  if (armed && !ownedBy(armed, tabId, host)) return { skipped: "no download was armed" };
  // An expired arm with nothing staged is no arm at all. Settled here, where it is read,
  // so the outcome never depends on whether some other download fired the listener.
  if (armed && !armed.staged && Date.now() - armed.armedAt > ARM_TIMEOUT_MS) {
    const expired = armed;
    await withArmLock(async () => {
      const current = await readArmed();
      if (current && current.armedAt === expired.armedAt && !current.staged) await clearArmed();
    });
    armed = null;
  }
  if (!armed) return { skipped: "no download was armed" };
  if (armed.key !== key) {
    // A staged image from a DIFFERENT record must never ride into this capture.
    // Generic on purpose: this reaches the page, and the armed key is ANOTHER tab's record.
    return { skipped: "the armed download belongs to a different record than this capture" };
  }

  // WAIT for the download to land. It is armed for this record, so a capture firing before
  // the bytes arrive should hold, not fail — the onChanged listener stages the file the
  // moment it completes. Bounded, so a download that never comes still fails honestly.
  const deadline = Date.now() + STAGE_WAIT_MS;
  while (!armed.staged && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, STAGE_POLL_MS));
    armed = await readArmed();
    if (!armed || armed.key !== key || !ownedBy(armed, tabId, host)) {
      return { skipped: "the armed download was cleared before it landed" };
    }
  }

  if (!armed.staged) {
    const refused = await refusalFor(armed);
    return {
      skipped: refused
        ? "no usable download within " + STAGE_WAIT_MS / 1000 + "s of the capture: " + refused
        : "no download from the armed site arrived within " +
          STAGE_WAIT_MS / 1000 +
          "s of the capture — did the site's own Download actually start? If it did, the " +
          "Capsule service-worker console names the host it came from.",
    };
  }
  if (armed.staged.error) return { skipped: armed.staged.error };

  // Consume ATOMICALLY: under the lock, take the staged value from the CURRENT arm of this
  // generation and clear it in the same step. Only the caller that clears it gets the bytes.
  // Two captures collecting at once used to both return the same download, attaching one
  // export to two captures (Codex adversarial review, seventh pass, 2026-09-29). A newer arm
  // that replaced this one belongs to another capture and is left alone.
  const staged = await withArmLock(async () => {
    const current = await readArmed();
    if (!current || current.armedAt !== armed.armedAt || current.key !== armed.key) return null;
    if (!current.staged || current.staged.error) return null;
    await clearArmed();
    return current.staged;
  });
  if (!staged) return { skipped: "the armed download was already collected by another capture" };
  return {
    media: {
      data_base64: staged.data_base64,
      filename: staged.filename,
      mime: staged.mime,
      // The manifest must say these bytes came from the provider's own export feature —
      // not from the page, and not from any API we were never offered. The two labels are
      // NOT cosmetic: the backend gates record-provenance images on pixels, and REQ-007
      // §3.3 will not accept a page render in their place.
      provenance: armed.profile === "ancestry" ? "ancestry:download" : "site:official-download",
    },
    downloadId: staged.downloadId,
  };
}

/**
 * R1.3 — the LAST resort: grab the viewer's canvas.
 *
 * This is ~1370px, i.e. SCREEN resolution. It does not satisfy constraint 4 ("assume
 * everything will be walled off; we must not have to return") and it is NOT a substitute
 * for the real download. So it is:
 *   - never automatic — the caller must ask for it explicitly, because a silent downgrade
 *     is precisely the masquerade R1.3 forbids;
 *   - labelled `viewer:canvas-fallback` in the manifest; and
 *   - measured anyway by the backend, which classifies it DEGRADED from its pixels rather
 *     than trusting the label. The ledger then leaves the record UNMINED, so we come back
 *     for the real scan.
 *
 * A tainted canvas (cross-origin pixels) throws SecurityError — we report that and
 * capture nothing rather than screenshotting around it.
 */
async function captureViewerCanvas(tabId) {
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: () => {
        const canvases = Array.from(document.querySelectorAll("canvas"));
        if (!canvases.length) return { error: "no canvas on this page" };
        // The record scan is the biggest canvas by far; viewer chrome is small.
        const canvas = canvases.sort((a, b) => b.width * b.height - a.width * a.height)[0];
        if (!canvas.width || !canvas.height) return { error: "the canvas is empty" };
        try {
          return {
            data_url: canvas.toDataURL("image/jpeg", 0.95),
            width: canvas.width,
            height: canvas.height,
          };
        } catch (e) {
          // Cross-origin pixels: the browser refuses to let us read them back.
          return { error: "the canvas is tainted (cross-origin): " + e.message };
        }
      },
    });

    const value = result && result.result;
    if (!value || value.error) {
      return { skipped: (value && value.error) || "could not read the viewer canvas" };
    }
    return {
      media: {
        data_base64: value.data_url.split(",")[1],
        filename: "record-canvas.jpg",
        mime: "image/jpeg",
        // Says plainly what this is. The backend measures it regardless.
        provenance: "viewer:canvas-fallback",
      },
      degraded: true,
    };
  } catch (err) {
    return { skipped: "canvas capture failed: " + (err && err.message ? err.message : err) };
  }
}

/** Once the capture is safely on disk, the loose copy in Downloads is clutter. */
async function cleanUpDownload(downloadId) {
  if (downloadId == null) return;
  try {
    await chrome.downloads.removeFile(downloadId);
    await chrome.downloads.erase({ id: downloadId });
  } catch (_e) {
    /* already gone, or the user moved it — never fatal */
  }
}
// Node (the correlation test) loads this file directly; Chrome has no `module`.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    hostBelongsToArmedSite,
    hostOf,
    fromArmedSite,
    armProfileFor,
    armDownload,
    takeStagedDownload,
    watchDownloads,
  };
}
