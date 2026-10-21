// Capsule Tier-1 capture self-test.
//
// Run this in the PAGE context of a real genealogy RECORD page (one with a
// transcript to extract) — via a browsing agent's JS-execution tool, or by
// pasting it into the DevTools console. It exercises the full on-page trigger:
//
//   content script injected  ->  `capsule:capture` event dispatched  ->
//   background worker POSTs to the local backend  ->  toast reports the outcome
//
// It performs a REAL capture (that is the only true end-to-end test), then returns
// and logs a verdict object: { verdict: "PASS"|"FAIL", detail, toast, ... }.
// A browsing agent should read the returned object; a human reads the console line.
//
// Prereqs the test itself cannot set up (it will report if they are missing):
//   1. The extension is loaded/reloaded at chrome://extensions (v0.0.8+).
//   2. `capsule serve` is running.
//   3. The token was pasted into the Capsule popup once (the background reads it
//      from storage; this page never opens the popup).
//   4. NOTHING ELSE IS ATTACHED TO THIS TAB'S DEBUGGER. The capture renders the page
//      to PDF over the DevTools protocol, and Chrome allows only one client per tab —
//      so open DevTools, or a CDP-driven automation tool (e.g. a devtools MCP),
//      makes the render skip. The capture still succeeds, but this test FAILS it: a
//      capture without its rendered page is not the artifact we want. Run it from an
//      extension-based JS tool, or from the console with DevTools closed on the tab
//      under test.
//
// The capture it creates is a normal capture of THIS page, tagged with a note so
// you can find and trash it from the index at http://127.0.0.1:8765/ if you want.
(async () => {
  const CAPTURE_TIMEOUT_MS = 45000;

  const report = (verdict, detail, extra) => {
    const out = Object.assign({ verdict, detail }, extra || {});
    console.log("[capsule-selftest] " + JSON.stringify(out));
    return out;
  };

  // 1. Is the content script present? Its floating button is the tell.
  if (!document.getElementById("capsule-capture-btn")) {
    return report(
      "FAIL",
      "Content script not injected: the '⬇ Capsule' button is absent. Reload the " +
        "extension at chrome://extensions, then refresh this page and retry."
    );
  }

  // 2. Sanity: don't 'capture' a blank/new tab — there is no record to extract.
  if (!/^https?:/i.test(location.href)) {
    return report(
      "FAIL",
      "Run this on a real record page (an http/https genealogy record), not " +
        location.href
    );
  }

  // 3. Watch the toast that content.js writes; resolve on its final state.
  const waitForOutcome = (timeoutMs) =>
    new Promise((resolve) => {
      const started = Date.now();
      const poll = () => {
        const t = document.getElementById("capsule-toast");
        const text = t ? t.textContent : "";
        const settled = text && !/Capturing/i.test(text);
        if (settled) {
          return resolve({ text: text, waitedMs: Date.now() - started });
        }
        if (Date.now() - started > timeoutMs) {
          return resolve({ text: text || "(no toast appeared)", timedOut: true, waitedMs: Date.now() - started });
        }
        setTimeout(poll, 250);
      };
      poll();
    });

  // 4. Fire the agent trigger (the same event a researcher would use in the wild)
  //    and await the outcome.
  document.dispatchEvent(
    new CustomEvent("capsule:capture", { detail: { note: "Capsule Tier-1 self-test" } })
  );
  const res = await waitForOutcome(CAPTURE_TIMEOUT_MS);

  if (res.timedOut) {
    return report(
      "FAIL",
      "No capture outcome within " + CAPTURE_TIMEOUT_MS / 1000 + "s. Check that " +
        "`capsule serve` is running and the token was pasted into the popup once.",
      { toast: res.text, waitedMs: res.waitedMs }
    );
  }

  const text = res.text;
  const isError = /^Capsule:|not reachable|No response/i.test(text);
  const captured = /^Captured/i.test(text);
  const verifyOk = /verify OK/i.test(text);
  // The rendered page-PDF is part of a complete capture, so the self-test demands
  // it. A skip degrades gracefully in the wild (the snapshot is still archived) —
  // but here, unasserted, it would let a silently PDF-less capture read as PASS.
  const pdfOk = /PDF ✓/.test(text);
  // The backend rasterized the render and found it blank (BUG-001): something was
  // composited over the page. The capture is fine; the PDF is not evidence.
  const pdfBlank = /PDF ✗/.test(text);

  if (captured && pdfBlank) {
    return report(
      "FAIL",
      "The page PDF rendered BLANK — something was composited over the page (an agent " +
        "glow, an extension overlay). The capture itself is archived and verified, but " +
        "the PDF is unreadable and must be re-captured. If this ran in an agent-driven " +
        "tab, the overlay suppression in render-pdf.js did not cover it: get the " +
        "overlay's element id and add it to HIDE_SELECTORS.",
      { toast: text, waitedMs: res.waitedMs }
    );
  }

  if (captured && verifyOk && pdfOk && !isError) {
    return report("PASS", "Capture succeeded, verified, and the page render attached.", {
      toast: text,
      page: location.href,
      waitedMs: res.waitedMs,
      next:
        "Confirm it persisted: open http://127.0.0.1:8765/, check the newest capture " +
        "row, and open its media/0-record.pdf.",
    });
  }
  if (captured && verifyOk && !pdfOk) {
    return report(
      "FAIL",
      "Capture succeeded but the page PDF was NOT rendered (the toast carries the " +
        "reason). Most often: something else holds this tab's debugger — close " +
        "DevTools / detach any CDP tool and retry. The capture itself is intact.",
      { toast: text, waitedMs: res.waitedMs }
    );
  }
  if (captured && !verifyOk) {
    return report("FAIL", "Capture was written but hash-verify FAILED — investigate the capture folder.", {
      toast: text,
      waitedMs: res.waitedMs,
    });
  }
  return report("FAIL", "Capture reported an error (see toast).", {
    toast: text,
    waitedMs: res.waitedMs,
  });
})();
