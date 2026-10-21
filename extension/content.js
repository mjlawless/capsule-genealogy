// Capsule content script: an on-page capture trigger the toolbar popup cannot be.
//
// An extension's toolbar button and popup are browser chrome — no automation agent
// can click them. But an agent CAN click a page element or dispatch a DOM event.
// So this script, injected into every page, provides two page-level triggers:
//
//   1. a small floating "⬇ Capsule" button (click it — by hand or by an agent), and
//   2. a `capsule:capture` DOM event: `document.dispatchEvent(new CustomEvent(
//      "capsule:capture", { detail: { note: "..." } }))` — which an agent can run
//      in the page to fire a capture without any visible click.
//
// Either way it captures ONLY the page in view, on an explicit trigger — the same
// compliance posture as the popup. The script is inside the page, so it reads the
// already-rendered DOM (the page the user is viewing, in their own session) directly and hands it to the background
// worker, which holds the token and does the POST (see background.js).

(() => {
  const EVENT = "capsule:capture";
  const BTN_ID = "capsule-capture-btn";
  const TOAST_ID = "capsule-toast";

  function toast(text, ok) {
    let el = document.getElementById(TOAST_ID);
    if (!el) {
      el = document.createElement("div");
      el.id = TOAST_ID;
      el.style.cssText =
        "position:fixed;bottom:56px;right:12px;z-index:2147483647;max-width:320px;" +
        "padding:8px 12px;border-radius:6px;font:12px/1.4 system-ui,sans-serif;" +
        "color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.3);white-space:pre-wrap;";
      document.documentElement.appendChild(el);
    }
    el.style.background = ok ? "#1a7f37" : "#b42318";
    el.textContent = text;
    clearTimeout(el._t);
    el._t = setTimeout(() => el.remove(), 6000);
  }

  // A machine-readable capture verdict for the driving agent (live-run finding). The toast
  // self-removes after 6 s, but an image capture's render+download+verify can take 20-40 s —
  // so by the time the agent reads back, the verdict is gone and it must inspect disk and
  // measure pixels to know what happened. Instead we mirror the outcome onto the page's own
  // <html> element, where the agent (in either world) can read it deterministically:
  //   - `data-capsule-capturing="true"` for the duration of a capture, and
  //   - `data-capsule-last-outcome` = the JSON result (with `url` query-stripped — BUG-015),
  //     persisting until the next capture.
  // A CustomEvent's `detail.onResult` callback was the other option, but a function does not
  // survive the main->isolated world hop; a DOM attribute always does.
  function setCapturing(on) {
    try {
      if (on) document.documentElement.setAttribute("data-capsule-capturing", "true");
      else document.documentElement.removeAttribute("data-capsule-capturing");
    } catch (_e) {
      /* never let a status attribute break a capture */
    }
  }

  // BUG-015: the mirrored `url` must carry no query string. Record URLs embed
  // identity/session-shaped params (FamilySearch `?personArk=…&groupId=…`, Ancestry search
  // params), and an automation harness reading the attribute back rightly blocks
  // credential-shaped data — forcing the driving agent to hand-pick fields one by one.
  // This attribute is a status channel, not evidence: the full URL is durably recorded in
  // manifest.json and the citation, and origin+path still says WHICH record this verdict
  // belongs to.
  function stripQuery(url) {
    if (!url) return undefined;
    try {
      const u = new URL(url);
      return u.origin + u.pathname;
    } catch (_e) {
      return String(url).split(/[?#]/)[0];
    }
  }

  function setOutcome(outcome) {
    try {
      const out = outcome || {};
      document.documentElement.setAttribute(
        "data-capsule-last-outcome",
        // `url: undefined` (error outcomes have none) is dropped by JSON.stringify.
        JSON.stringify({ ...out, url: stripQuery(out.url), at: new Date().toISOString() })
      );
    } catch (_e) {
      /* never let a status attribute break a capture */
    }
  }

  let capturing = false;

  // The key a download is filed under. Arming and capturing MUST agree on it, so it is
  // computed in exactly one place. On Ancestry it is the `_APID`; anywhere else it is this
  // page's own URL — which means that if the tab navigates between arming and capturing,
  // the keys stop matching and the staged file is honestly skipped rather than filed under
  // whatever page happens to be open now.
  function armKeyFor(detail) {
    return (detail && detail.apid) || location.href;
  }

  // REQ-007 §1 — WHICH page of a multi-image viewer is on screen, read from the LIVE DOM.
  //
  // This has to happen here and cannot be recovered later from the snapshot. Serializing a
  // page writes the `selected` ATTRIBUTE, and that attribute never moves off the option the
  // viewer first loaded — navigating the viewer changes the live `selectedIndex` and nothing
  // else. Verified against Archion on 2026-08-22: the screen showed image 6 while
  // `document.documentElement.outerHTML` still marked image 2. A backend parsing that HTML
  // would confidently cite the wrong leaf, which is worse than citing none.
  //
  // Ranking mirrors the backend fallback: the viewer's own class hook first, then the
  // largest option list — a register selector has 172 entries, a language dropdown two.
  // Returns undefined when nothing looks like a page selector; the backend then falls back
  // to parsing, and an ordinary page simply has no label.
  function livePageLabel() {
    try {
      let best, bestRank = [0, 0];
      for (const sel of document.querySelectorAll("select")) {
        if (sel.options.length < 2) continue;
        const rank = [/page-select/i.test(sel.className) ? 1 : 0, sel.options.length];
        if (rank[0] < bestRank[0] || (rank[0] === bestRank[0] && rank[1] <= bestRank[1])) continue;
        const option = sel.options[sel.selectedIndex];
        const text = option && (option.textContent || "").trim().replace(/\s+/g, " ");
        if (text && text.length <= 60) { best = text; bestRank = rank; }
      }
      return best;
    } catch (_e) {
      return undefined; // never let a label lookup break a capture
    }
  }

  // REQ-001, generalized by REQ-007 §2: arm interception of the SITE'S OWN download
  // control, then let the agent click it.
  //
  //   document.dispatchEvent(new CustomEvent("capsule:arm-download",
  //     { detail: { apid: "1,7602::56510272" } }))          // Ancestry
  //   document.dispatchEvent(new CustomEvent("capsule:arm-download"))  // anywhere else
  //   ...agent clicks the site's own export control (Ancestry: Tool menu -> Download;
  //      Archion: toolbar -> Bildausschnitt herunterladen -> Herunterladen)...
  //   document.dispatchEvent(new CustomEvent("capsule:capture", { detail: { ... } }))
  //
  // Capsule holds NO provider selectors and clicks nothing on their site: the actor that
  // already drives the tab operates the site's own UI with a trusted click, and Capsule
  // arms, intercepts, verifies and ingests. Arming reports back through the toast, and
  // through `detail.onResult` if the caller supplies one.
  //
  // `host` is read from `location.hostname` HERE and is never taken from `detail`. It is
  // the entire basis of the download-correlation check, and this listener fires on an
  // event any page JS can dispatch — a page that could name its own host could arm Capsule
  // to swallow the next download from anywhere.
  document.addEventListener("capsule:arm-download", async (e) => {
    const detail = (e && e.detail) || {};
    toast("Arming the site's download…", true);
    try {
      const res = await chrome.runtime.sendMessage({
        type: "capsule:arm-download",
        apid: detail.apid,
        key: armKeyFor(detail),
        host: location.hostname,
      });
      if (res && res.ok) {
        toast("Armed. Click the site's own Download now.", true);
      } else {
        toast("Capsule could not arm: " + ((res && res.reason) || "unknown"), false);
      }
      if (typeof detail.onResult === "function") detail.onResult(res);
    } catch (err) {
      toast("Capsule not reachable: " + (err && err.message ? err.message : err), false);
    }
  });

  async function capture(note, opts) {
    if (capturing) return;
    capturing = true;
    setCapturing(true);
    const options = opts || {};
    const btn = document.getElementById(BTN_ID);
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Capturing…";
    }
    toast("Capturing this page…", true);
    try {
      // The record files the page EMBEDS — the certificate itself, not a picture of it.
      // BUG-007: this path archived a screen render of a NYC birth certificate and called
      // it a success. A render of the page is NOT the record.
      //
      // blob:/data: bytes can only be read HERE, in the page. Remote (http) ones are fetched
      // by the service worker, which is not bound by the page's CSP — the Azure blob that
      // carries the certificate cannot be fetched from the page at all.
      // The generic in-page media sweep is for pages that EMBED their record (a NYC
      // certificate blob, say). On a known Ancestry record — one with an `apid` — the scan
      // comes from the armed Download, and the only things this sweep finds are the
      // record-page preview thumbnail and the "Suggested records" sidebar: other people's
      // records, offered as hints. Grabbing those wrote junk to disk and, worse, fired the
      // "RECORD FILE NOT CAPTURED" alarm on records that have no record file — teaching the
      // operator to ignore the one warning that must never cry wolf (BUG-009). So on an
      // Ancestry capture we skip the sweep entirely and trust the download path.
      const inline = [];
      const unreadable = [];
      const remote = [];
      if (!options.apid) {
        const detected = detectPageMedia();
        for (const item of detected.inline) {
          const media = await readInlineMedia(item);
          if (media) inline.push(media);
          else unreadable.push(item.filename + " (could not read the page's blob)");
        }
        for (const item of detected.remote) remote.push(item);
      }

      const res = await chrome.runtime.sendMessage({
        type: EVENT,
        url: location.href,
        html: document.documentElement.outerHTML,
        note: note || undefined,
        // Bytes we already hold, plus the URLs the worker must fetch for us. All empty on
        // an Ancestry (apid) capture — its image comes from the armed Download.
        page_media: inline,
        remote_media: remote,
        unreadable_media: unreadable,
        // REQ-001, all optional and absent for an ordinary capture.
        apid: options.apid,
        // REQ-007 §2 — which armed download (if any) belongs to this capture. Computed the
        // same way as at arming time; a mismatch means the staged file is skipped, never
        // guessed at.
        arm_key: armKeyFor(options),
        // REQ-007 §1 — read from the live DOM, because the snapshot cannot say it. Never on
        // an Ancestry record (REQ-009): there the label is part of the record's dedup
        // identity, so only the one-call flow may set one, deliberately, for an extra image.
        // A stray <select> on a record page must not turn a true duplicate into a "new".
        page_label:
          options.apid || apidFromRecordUrl(location.href) ? undefined : livePageLabel(),
        image_intent: options.image_intent,
        follow_up: options.follow_up,
        remine: options.remine,
        // R1.3: opt in explicitly to the screen-resolution canvas fallback. Off by
        // default — a degraded image must never quietly stand in for the real scan.
        allow_canvas_fallback: options.allow_canvas_fallback,
      });
      toastFromResult(res);
    } catch (err) {
      const msg = "Capsule not reachable: " + (err && err.message ? err.message : err);
      toast(msg, false);
      setOutcome({ ok: false, error: msg });
    } finally {
      capturing = false;
      setCapturing(false);
      if (btn) {
        btn.disabled = false;
        btn.textContent = "⬇ Capsule";
      }
    }
  }

  // The one place a capture RESULT becomes a toast, shared by every trigger (the button,
  // `capsule:capture`, and the one-call `capsule:capture-ancestry-image`). A green toast is
  // reserved for a genuinely complete capture: verify OK, no media warning, nothing missed,
  // and — if an image was in play — `full` (or a legitimately image-less record).
  function toastFromResult(res) {
    // Mirror the structured verdict onto <html> for the driving agent (persists past the
    // 6 s toast). The backend response IS the machine-readable result — exposed whole,
    // except the query-stringed `url`, which setOutcome sanitizes (BUG-015).
    setOutcome(res || { ok: false, error: "no response from the extension" });
    if (!res) {
      toast("No response — is the Capsule extension loaded/enabled?", false);
      return;
    }
    if (!res.ok) {
      toast("Capsule: " + res.error, false);
      return;
    }
    // A blank render is NOT a ✓ — the backend measured the pixels (BUG-001). A warning
    // reddens the whole toast: this capture needs re-running.
    const warnings = res.media_warnings || [];
    const pdf =
      res.pdf === "attached"
        ? warnings.length
          ? ", PDF ✗ " + warnings.join(" ")
          : ", PDF ✓"
        : ", PDF skipped (" + (res.pdf_skipped_reason || "unknown") + ")";
    // The record image (REQ-001). `full` is the only good outcome: degraded or failed means
    // the record is NOT mined and stays on the queue — a screen-res scan is not evidence we
    // can rely on once Ancestry is gone.
    let image = "";
    if (res.image_status) {
      image =
        res.image_status === "full"
          ? ", IMAGE ✓ full"
          : res.image_status === "index-only-no-image"
            ? ", index-only (no image)"
            : ", IMAGE ✗ " + res.image_status + " — NOT mined";
    }
    // WHY verify failed, when it failed for want of a record rather than a bad hash
    // (REQ-007 §3.3). Without this the zoomed Archion export reads "verify FAILED,
    // IMAGE ✓ full" — two true statements that contradict each other, and no way to tell
    // from the toast which one to act on.
    // Fields this backend was too old to accept. The capture is REAL, but an enhancement
    // is missing — say so, or a register capture with no page label looks identical to a
    // page that never had a page selector.
    const downgraded = res.wire_downgraded || [];
    const downgradeText = downgraded.length
      ? "\n⚠ NOTE: your backend is older than this extension; it refused " +
        downgraded.join(", ") +
        ". The capture landed without it — restart `capsule serve` from the current build."
      : "";

    const shortfall = res.verify_shortfall || [];
    const shortfallText = shortfall.length
      ? "\n⚠ THIS CAPTURE DOES NOT HOLD THE RECORD:\n• " +
        shortfall.join("\n• ")
      : "";

    // A record file the page HELD and we did not get (BUG-007) — the loudest thing on the
    // toast, because the capture would otherwise look completely healthy.
    const missed = res.missed_media || [];
    const missedText = missed.length
      ? "\n⚠ RECORD FILE NOT CAPTURED — you hold only a page render:\n• " +
        missed.join("\n• ")
      : "";

    toast(
      "Captured " +
        (res.record_type || "page") +
        " — " +
        res.assertion_count +
        " assertion(s), verify " +
        (res.verify_ok ? "OK" : "FAILED") +
        pdf +
        image +
        shortfallText +
        missedText +
        downgradeText,
      res.verify_ok &&
        !warnings.length &&
        !missed.length &&
        (!res.image_status ||
          res.image_status === "full" ||
          res.image_status === "index-only-no-image")
    );
  }

  // -- REQ-002: one-call Ancestry image capture ------------------------------
  //
  //   document.dispatchEvent(new CustomEvent("capsule:capture-ancestry-image",
  //     { detail: { apid: "1,6061::20844342" } }))
  //
  // One event fired on the RECORD page does the whole arm -> open viewer -> click Ancestry's
  // own Tools->Download -> capture dance internally (see background.js). It replaces the
  // ~8-step per-record choreography that was the slow part of capturing a record image.

  // The Ancestry record URL shapes, mirrored from the backend guard (ancestry.py). The apid
  // is derived from the URL, never invented: `<collection>:<record>`.
  const _SEARCH_RE = /\/search\/collections\/(\d+)\/records\/(\d+)/;
  const _DISCOVERY_RE = /\/discoveryui-content\/view\/(\d+):(\d+)/;

  function apidFromRecordUrl(url) {
    let m = _SEARCH_RE.exec(url || "");
    if (m) return m[1] + ":" + m[2];
    m = _DISCOVERY_RE.exec(url || "");
    if (m) return m[2] + ":" + m[1]; // this URL shape is <rec>:<db> — note the swap
    return null;
  }

  /** The image-viewer link the record page offers, or null for an index-only record. */
  function findViewerUrl() {
    const a = document.querySelector('a[href*="/imageviewer/collections/"]');
    return a ? a.href : null;
  }

  // REQ-009 — the label for an image OTHER than the one the record page links to, e.g.
  // "image 62073_302022005552_0481-00154" (the facing page of a register opening). The
  // record's own image gets no label: it is the record's default, and its dedup identity
  // stays exactly what it always was. The viewer's image id is the label, verbatim, so the
  // manifest names which image it holds.
  function extraImageLabel(requestedViewerUrl, ownViewerUrl) {
    const imageId = (u) => {
      try {
        const segments = new URL(u, location.href).pathname.split("/").filter(Boolean);
        return segments.length ? decodeURIComponent(segments[segments.length - 1]) : null;
      } catch (_e) {
        return null;
      }
    };
    if (!requestedViewerUrl || !ownViewerUrl) return undefined;
    const requested = imageId(requestedViewerUrl);
    if (!requested || requested === imageId(ownViewerUrl)) return undefined;
    return ("image " + requested).slice(0, 60);
  }

  async function captureAncestryImage(detail) {
    if (capturing) return;
    capturing = true;
    setCapturing(true);
    toast("Capturing full record image — arming Ancestry's Download…", true);
    try {
      const apid = detail.apid || apidFromRecordUrl(location.href);
      if (!apid) {
        const msg = "Capsule: no _APID given and this is not a readable Ancestry record URL";
        toast(msg, false);
        setOutcome({ ok: false, error: msg });
        return;
      }
      const ownViewerUrl = findViewerUrl();
      const viewerUrl = detail.record_viewer_url || ownViewerUrl;
      if (!viewerUrl) {
        const msg =
          "Capsule: no image-viewer link on this page — is this an image-backed record?";
        toast(msg, false);
        setOutcome({ ok: false, error: msg });
        return;
      }
      // Grab the record page's own HTML NOW — it carries the fields the extractor needs.
      const res = await chrome.runtime.sendMessage({
        type: "capsule:capture-ancestry-image",
        apid,
        recordUrl: location.href,
        recordHtml: document.documentElement.outerHTML,
        viewerUrl,
        // REQ-009 — an explicit label wins; otherwise one is derived when the caller asked
        // for an image other than the record page's own.
        page_label:
          detail.page_label || extraImageLabel(detail.record_viewer_url, ownViewerUrl),
        // The 409's own advice ("send remine true") must be followable from this entry point.
        remine: detail.remine === true ? true : undefined,
        note: detail.note || undefined,
        // Open the viewer visibly (for debugging a selector drift) only if asked.
        showViewer: !!detail.show_viewer,
      });
      toastFromResult(res);
      if (typeof detail.onResult === "function") detail.onResult(res);
    } catch (err) {
      const msg = "Capsule not reachable: " + (err && err.message ? err.message : err);
      toast(msg, false);
      setOutcome({ ok: false, error: msg });
    } finally {
      capturing = false;
      setCapturing(false);
    }
  }

  document.addEventListener("capsule:capture-ancestry-image", (e) => {
    captureAncestryImage((e && e.detail) || {});
  });

  // -- REQ-002: the viewer half — click Ancestry's own Tools -> Download ------
  //
  // When the worker opens the image viewer for the one-call flow, it messages THIS content
  // script (auto-injected into that tab) to operate Ancestry's own Download control. We
  // match the controls by their visible text, not by CSS class: Ancestry's classes are
  // obfuscated and churn, but "Tools" and "Download" are stable, human-facing labels. The
  // click is dispatched as a full bubbling pointer/mouse sequence so the site's React
  // handlers fire. If the controls cannot be found, we say why — never a silent miss.
  function _clickables() {
    return Array.from(
      document.querySelectorAll('button, a[href], [role="button"], [role="menuitem"]')
    );
  }

  function _label(el) {
    const aria =
      (el.getAttribute && (el.getAttribute("aria-label") || el.getAttribute("title"))) || "";
    return ((el.textContent || "") + " " + aria).replace(/\s+/g, " ").trim();
  }

  function _visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  async function _waitForControl(re, timeoutMs) {
    return _waitFor(() => {
      for (const el of _clickables()) {
        if (_visible(el) && re.test(_label(el))) return el;
      }
      return null;
    }, timeoutMs);
  }

  async function _waitFor(finder, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const el = finder();
      if (el) return el;
      await new Promise((r) => setTimeout(r, 250));
    }
    return null;
  }

  // The viewer's tool-menu trigger. Confirmed against the live DOM (E2E 2026-07-16): it is a
  // WRENCH ICON whose label is "Tool menu" — NOT the word "Tools" (the first finder matched
  // "Tools" and never fired). Match the real label, and fall back to Ancestry's own classes
  // in case the label is localized or dropped. Once it opens, the "Download" item IS plain
  // visible text, so that half of the finder was always fine.
  function _findToolMenu() {
    for (const el of _clickables()) {
      if (_visible(el) && /tool\s*menu|^\s*tools?\s*$/i.test(_label(el))) return el;
    }
    const byClass = document.querySelector(
      "button.singleFloatButton, .calloutTriggerNoArrow, button.calloutTriggerNoArrow"
    );
    return byClass && _visible(byClass) ? byClass : null;
  }

  function _fireClick(el) {
    el.scrollIntoView({ block: "center" });
    const opts = { bubbles: true, cancelable: true, view: window };
    for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
      try {
        el.dispatchEvent(new MouseEvent(type, opts));
      } catch (_e) {
        /* PointerEvent unsupported for pointerdown on old engines — the click still fires */
      }
    }
    if (typeof el.click === "function") el.click();
  }

  async function clickAncestryDownload(apid) {
    if (!/\/imageviewer\/collections\//.test(location.href)) {
      return { ok: false, reason: "this tab is not an Ancestry image viewer" };
    }
    const tools = await _waitFor(_findToolMenu, 10000);
    if (!tools) return { ok: false, reason: "could not find Ancestry's Tool menu" };
    _fireClick(tools);
    const download = await _waitForControl(/download/i, 6000);
    if (!download) {
      return { ok: false, reason: "the Tools menu opened but had no Download item" };
    }
    _fireClick(download);
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "capsule:auto-download") {
      clickAncestryDownload(msg.apid).then(sendResponse);
      return true; // async reply
    }
  });

  function injectButton() {
    if (document.getElementById(BTN_ID)) return;
    const btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.type = "button";
    btn.textContent = "⬇ Capsule";
    btn.title = "Capture this page into Capsule";
    btn.style.cssText =
      "position:fixed;bottom:12px;right:12px;z-index:2147483647;padding:6px 10px;" +
      "border:0;border-radius:6px;background:#3b2f80;color:#fff;opacity:.72;" +
      "font:12px/1 system-ui,sans-serif;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.3);";
    btn.addEventListener("mouseenter", () => (btn.style.opacity = "1"));
    btn.addEventListener("mouseleave", () => (btn.style.opacity = ".72"));
    btn.addEventListener("click", () => capture());
    document.documentElement.appendChild(btn);
  }

  // The agent's event path: a CustomEvent dispatched on `document` from the page's
  // main world crosses into this isolated-world listener (same DOM node).
  document.addEventListener(EVENT, (e) => {
    const detail = (e && e.detail) || {};
    capture(detail.note, detail);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectButton);
  } else {
    injectButton();
  }
})();
