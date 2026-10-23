"""Inline CSS + JS for the Captures Index (spec `captures-index/v2` §3.1, §3.2).

These are string constants, not files on disk, because the index must be a single
self-contained document: no CDN, no stylesheet link, no build step. It travels in
the capsule export zip and has to open from a `file://` URL, offline, on a
machine that has never run Capsule.

The script only *enhances*. Every capture — row and detail alike — is already in
the HTML when the file is written. `INDEX_JS` hides the inline details, wires up
filtering/sorting, and clones the selected capture's detail into the side pane.
With JavaScript off, the collapse rule (`.js .detail`) never applies and the
document reads as a complete, if long, index. There is exactly one copy of the
data in the file: the DOM.
"""
from __future__ import annotations

INDEX_CSS = """\
*,*::before,*::after{box-sizing:border-box}
:root{
  color-scheme:light dark;
  --bg:#fdfdfc;--panel:#fff;--fg:#1c1c1a;--muted:#6b6b66;--line:#e3e3de;
  --accent:#7a5c2e;--accent-bg:#f6efe2;--chip:#eeeeea;--hover:#f5f3ee;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#16161a;--panel:#1d1d22;--fg:#e6e6e3;--muted:#9a9a94;--line:#32323a;
    --accent:#d3ae6e;--accent-bg:#2b2418;--chip:#2a2a31;--hover:#22222a;
  }
}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}
code,.chip,.url{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}

header{position:sticky;top:0;z-index:5;background:var(--bg);
  border-bottom:1px solid var(--line);padding:.9rem 1.25rem}
h1{font-size:1.05rem;margin:0}
.meta{color:var(--muted);font-size:.8rem;margin:.2rem 0 0}
.controls{display:none;gap:.5rem;margin-top:.7rem;flex-wrap:wrap;align-items:center}
.js .controls{display:flex}
input,select{font:inherit;padding:.35rem .5rem;border:1px solid var(--line);
  border-radius:6px;background:var(--panel);color:var(--fg)}
input{flex:1 1 18rem;min-width:11rem}
.ms{position:relative}
.ms>summary{list-style:none;cursor:pointer;padding:.35rem .5rem;border:1px solid var(--line);
  border-radius:6px;background:var(--panel);white-space:nowrap}
.ms>summary::-webkit-details-marker{display:none}
.ms>summary::after{content:" ▾";color:var(--muted)}
.ms-list{position:absolute;z-index:6;margin-top:.25rem;min-width:14rem;max-height:20rem;
  overflow:auto;padding:.4rem .5rem;border:1px solid var(--line);border-radius:6px;
  background:var(--panel);box-shadow:0 6px 18px rgba(0,0,0,.15)}
.ms-list label{display:block;white-space:nowrap;font-size:.82rem;padding:.12rem 0;cursor:pointer}
.ms-list input{flex:none;min-width:0;margin:0 .3rem 0 0;padding:0}
.ms-clear{margin-top:.35rem;font:inherit;font-size:.75rem;color:var(--accent);
  background:transparent;border:0;padding:0;cursor:pointer}
.count{color:var(--muted);font-size:.78rem;margin-left:auto}

.wrap{display:grid;grid-template-columns:minmax(0,1fr);gap:1.25rem;
  padding:1.25rem;align-items:start}
.js .wrap{grid-template-columns:minmax(0,1fr) 26rem}
@media (max-width:900px){.js .wrap{grid-template-columns:minmax(0,1fr)}}

table{width:100%;border-collapse:collapse}
#captures{font-size:.9rem}
thead th{text-align:left;font-size:.7rem;letter-spacing:.07em;font-weight:600;
  text-transform:uppercase;color:var(--muted);padding:.45rem .6rem;
  border-bottom:1px solid var(--line);white-space:nowrap}
.js thead th[data-key]{cursor:pointer}
.js thead th[data-key]:hover{color:var(--fg)}
th[aria-sort=ascending]::after{content:" \\2191"}
th[aria-sort=descending]::after{content:" \\2193"}
tbody.capture{border-bottom:1px solid var(--line)}
tbody[hidden]{display:none}
.row>td{padding:.45rem .6rem;vertical-align:top}
.js .row{cursor:pointer}
.js .row:hover{background:var(--hover)}
.js tbody.selected .row{background:var(--accent-bg)}
.js tbody.selected .row>td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
.rowlink{text-decoration:none;font-weight:600}
.js .detail{display:none}
.detail>td{padding:.2rem .6rem 1.4rem}

.pill{display:inline-block;border-radius:999px;padding:.05rem .5rem;
  font-size:.7rem;font-weight:600;background:var(--chip);color:var(--muted)}
.pill.s-extracted{background:var(--accent-bg);color:var(--accent)}
.pill.s-reviewed{background:var(--accent);color:var(--bg)}
.chip.incomplete{background:rgba(214,69,69,.15);color:#d64545}
.help-link{font-size:.75rem;font-weight:600;margin-left:.6rem;vertical-align:middle}
.d-cite{margin-top:.9rem}
.cite{font-size:.8rem;margin:.3rem 0}
.cite-label{display:block;font-size:.68rem;color:var(--muted);text-transform:uppercase}
.cite-text{user-select:all}
.copy,.copy-ai{font:inherit;font-size:.72rem;color:var(--accent);cursor:pointer;margin-left:.4rem;
  padding:.1rem .45rem;border:1px solid var(--line);border-radius:5px;background:transparent}
.copy-ai{margin-left:0;font-weight:600}
.copy:hover,.copy-ai:hover,.copy:focus-visible,.copy-ai:focus-visible{border-color:var(--accent);outline:none}
.reveal{font:inherit;font-size:.72rem;color:var(--accent);cursor:pointer;margin-left:.4rem;
  padding:.1rem .45rem;border:1px solid var(--line);border-radius:5px;background:transparent}
.reveal:hover,.reveal:focus-visible{border-color:var(--accent);outline:none}
.assertions .tag{display:inline-block;margin-left:.35rem;padding:0 .35rem;border-radius:4px;
  font-size:.66rem;font-weight:600;background:var(--accent-bg);color:var(--accent);cursor:help}
.capsule-fix-history{margin:.6rem 0 0;font-size:.75rem;color:var(--muted)}
.capsule-fix-history ol{margin:.2rem 0 0;padding-left:1.2rem;max-height:9rem;overflow:auto}
.assertions tr.retracted td{color:var(--muted)}
.chip{display:inline-block;background:var(--chip);color:var(--muted);
  border-radius:4px;padding:.05rem .35rem;font-size:.7rem;margin-right:.2rem}
.refchip{background:var(--accent-bg);color:var(--accent)}
.lock{margin-right:.3rem}
.stamp{white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--muted)}

.d-refs{margin-top:.9rem}
.d-refs h3{font-size:.7rem;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);margin:0 0 .35rem;font-weight:600}
.d-refs ul{list-style:none;margin:0;padding:0;font-size:.78rem}
.d-refs li{border-left:2px solid var(--accent);padding:.15rem 0 .15rem .5rem;
  margin-bottom:.45rem;overflow-wrap:break-word}
.d-notes{margin-top:.9rem}
.d-notes h3{font-size:.7rem;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);margin:0 0 .35rem;font-weight:600}
.notes{list-style:none;margin:0;padding:0;font-size:.82rem}
.notes li{border-left:2px solid var(--muted);padding:.15rem 0 .3rem .55rem;
  margin-bottom:.5rem;overflow-wrap:break-word}
.note-text{margin:0;white-space:pre-wrap}
.note-by{margin:.15rem 0 0;font-size:.72rem;color:var(--muted)}
.pending{color:var(--muted)}

aside{display:none}
.js aside{display:block;position:sticky;top:7.5rem;background:var(--panel);
  border:1px solid var(--line);border-radius:10px;padding:1rem;
  max-height:calc(100vh - 9rem);overflow:auto}
.js aside:empty::before{content:"Select a capture to see its citation, links, and extracted details.";
  color:var(--muted);font-size:.85rem}
aside:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

.d-title{font-size:1rem;margin:0}
.d-ref{font-size:.85rem;margin:.35rem 0 0}
.d-meta{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.1rem .75rem;
  font-size:.78rem;margin:.85rem 0 0}
.d-meta dt{color:var(--muted)}
.d-meta dd{margin:0;overflow-wrap:anywhere}
.url{font-size:.75rem;overflow-wrap:anywhere}
.assertions{margin-top:.9rem;font-size:.78rem}
.assertions th{text-align:left;color:var(--muted);font-weight:600;
  border-bottom:1px solid var(--line);padding:.25rem .4rem}
/* break-word, not anywhere: a long URL must wrap, but `death_date` must not
   split into `death_dat / e`. Only genuinely unfittable tokens break. */
.assertions td{padding:.25rem .4rem;border-bottom:1px solid var(--line);
  vertical-align:top;overflow-wrap:break-word}
.empty-note,.empty{color:var(--muted);font-style:italic}
.empty-note{font-size:.8rem;margin:.9rem 0 0}
.empty{text-align:center;padding:3rem 0}
/* Trashcan (delete) — only rendered when the backend serves the index. */
.trash{display:inline-flex;align-items:center;justify-content:center;border:0;
  background:transparent;cursor:pointer;padding:.2rem;margin-left:.4rem;
  color:var(--muted);border-radius:6px;vertical-align:middle}
.trash:hover,.trash:focus-visible{color:#d64545;background:rgba(214,69,69,.15);outline:none}
/* Add-a-note button — below the notes, interactive-only. */
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.assertions td.fixes{white-space:nowrap;text-align:right;width:1%}
.fix{border:0;background:transparent;color:var(--muted);cursor:pointer;font:inherit;
  font-size:.85rem;line-height:1;padding:.15rem .3rem;border-radius:5px}
.fix:hover,.fix:focus-visible{color:var(--accent);background:var(--accent-bg);outline:none}
.fix-retract:hover,.fix-retract:focus-visible{color:#d64545;background:rgba(214,69,69,.15)}
.fix-add{display:inline-flex;margin-top:.5rem;font:inherit;font-size:.78rem;font-weight:600;
  color:var(--accent);cursor:pointer;padding:.3rem .65rem;border:1px solid var(--line);
  border-radius:7px;background:transparent}
.fix-add:hover,.fix-add:focus-visible{border-color:var(--accent);outline:none}
.capsule-fix-field{display:block;margin-top:.6rem;font-size:.75rem;color:var(--muted)}
.capsule-fix-field>span{display:block;margin-bottom:.2rem}
.capsule-fix-who{font-size:.82rem;margin:0}
.capsule-fix-quote{margin:.6rem 0 0;padding:.45rem .6rem;border-left:3px solid var(--accent);
  background:var(--accent-bg);font-size:.82rem}
.capsule-fix-says{display:block;font-size:.68rem;color:var(--muted);text-transform:uppercase}
.capsule-modal-error{color:#d64545;font-size:.78rem;margin:.6rem 0 0}
.d-notes-add{display:inline-flex;align-items:center;gap:.4rem;margin-top:.6rem;
  font:inherit;font-size:.78rem;font-weight:600;color:var(--accent);cursor:pointer;
  padding:.35rem .7rem;border:1px solid var(--line);border-radius:7px;background:transparent}
.d-notes-add:hover,.d-notes-add:focus-visible{border-color:var(--accent);
  background:var(--accent-bg);outline:none}
.d-notes-add svg{display:block}
/* Add-note modal (JS-created; replaces the native single-line prompt). */
.capsule-modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:1000;
  display:flex;align-items:center;justify-content:center;padding:1rem}
.capsule-modal{background:var(--panel);color:var(--fg);border:1px solid var(--line);
  border-radius:12px;padding:1.15rem 1.25rem;width:min(92vw,480px);
  box-shadow:0 16px 48px rgba(0,0,0,.35)}
.capsule-modal h2{font-size:1rem;margin:0 0 .7rem}
.capsule-modal-text{width:100%;font:inherit;font-size:.9rem;padding:.55rem .65rem;
  border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);
  resize:vertical;min-height:7em}
.capsule-modal-hint{font-size:.72rem;color:var(--muted);margin:.45rem 0 0}
.capsule-modal-actions{display:flex;justify-content:flex-end;gap:.55rem;margin-top:.85rem}
.capsule-modal-actions button{font:inherit;font-size:.82rem;padding:.4rem .9rem;
  border-radius:7px;cursor:pointer;border:1px solid var(--line);
  background:transparent;color:var(--fg)}
.capsule-modal-actions .capsule-modal-save{background:var(--accent);
  border-color:var(--accent);color:#fff;font-weight:600}
.capsule-modal-actions button:disabled{opacity:.6;cursor:default}
"""

INDEX_JS = """\
(function () {
  "use strict";
  var table = document.getElementById("captures");
  var pane = document.getElementById("detail");
  if (!table || !pane) return;

  var bodies = Array.prototype.slice.call(table.tBodies);
  var query = document.getElementById("q");
  var counter = document.getElementById("shown");
  var heads = Array.prototype.slice.call(table.tHead.querySelectorAll("th[data-key]"));

  // Facet options are derived from the rows, so the renderer never has to
  // compute a vocabulary it would then have to keep in sync.
  var facets = [["f-type", "type"], ["f-site", "site"], ["f-status", "status"]]
    .map(function (pair) { return { el: document.getElementById(pair[0]), key: pair[1] }; });

  // A facet is a <select> (one choice) or a <details class="ms"> checkbox list (several).
  facets.forEach(function (facet) {
    if (!facet.el) return;
    facet.multi = facet.el.tagName === "DETAILS";
    var counts = Object.create(null);
    var values = [];
    bodies.forEach(function (body) {
      var value = body.getAttribute("data-" + facet.key);
      if (!value) return;
      if (!counts[value]) { counts[value] = 0; values.push(value); }
      counts[value] += 1;
    });
    values.sort();
    if (!facet.multi) {
      values.forEach(function (value) {
        var option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        facet.el.appendChild(option);
      });
      return;
    }
    var list = facet.el.querySelector(".ms-list");
    values.forEach(function (value) {
      var label = document.createElement("label");
      var box = document.createElement("input");
      box.type = "checkbox";
      box.value = value;
      box.addEventListener("change", function () { summarize(facet); apply(); });
      label.appendChild(box);
      label.appendChild(document.createTextNode(" " + value + " (" + counts[value] + ")"));
      list.appendChild(label);
    });
    var clear = document.createElement("button");
    clear.type = "button";
    clear.className = "ms-clear";
    clear.textContent = "Clear";
    clear.addEventListener("click", function () {
      list.querySelectorAll("input:checked").forEach(function (b) { b.checked = false; });
      summarize(facet);
      apply();
    });
    list.appendChild(clear);
  });
  function chosen(facet) {
    if (!facet.el) return [];
    if (!facet.multi) return facet.el.value ? [facet.el.value] : [];
    return Array.prototype.map.call(
      facet.el.querySelectorAll(".ms-list input:checked"), function (b) { return b.value; });
  }
  function summarize(facet) {
    var picked = chosen(facet);
    facet.el.querySelector("summary").textContent = !picked.length
      ? facet.el.getAttribute("data-all")
      : picked.length === 1 ? picked[0] : picked.length + " " + facet.el.getAttribute("data-noun");
  }
  // A checkbox dropdown closes when you click anywhere else.
  document.addEventListener("click", function (event) {
    facets.forEach(function (facet) {
      if (facet.multi && facet.el.open && !facet.el.contains(event.target)) facet.el.open = false;
    });
  });

  // Move the capture's own detail nodes into the pane. Cloning the DOM rather
  // than assigning innerHTML means nothing in this file is ever re-parsed as
  // markup after generation.
  function show(body) {
    bodies.forEach(function (other) { other.classList.toggle("selected", other === body); });
    pane.textContent = "";
    var cell = body && body.querySelector(".detail > td");
    if (!cell) return;
    for (var i = 0; i < cell.childNodes.length; i++) {
      pane.appendChild(cell.childNodes[i].cloneNode(true));
    }
  }

  function apply() {
    var needle = ((query && query.value) || "").trim().toLowerCase();
    bodies.forEach(function (body) {
      var ok = !needle || (body.getAttribute("data-search") || "").indexOf(needle) > -1;
      facets.forEach(function (facet) {
        var picked = ok ? chosen(facet) : [];
        if (picked.length) ok = picked.indexOf(body.getAttribute("data-" + facet.key)) > -1;
      });
      body.hidden = !ok;
    });

    var live = bodies.filter(function (body) { return !body.hidden; });
    if (counter) counter.textContent = String(live.length);

    var selected = bodies.filter(function (body) {
      return body.classList.contains("selected");
    })[0];
    if (selected && !selected.hidden) return;
    if (live.length) {
      show(live[0]);
    } else {
      pane.textContent = "";
      bodies.forEach(function (body) { body.classList.remove("selected"); });
    }
  }

  var sortKey = null;
  var sortDir = 1;
  function sortBy(key, th) {
    if (sortKey === key) {
      sortDir = -sortDir;
    } else {
      sortKey = key;
      sortDir = key === "date" ? -1 : 1;  // newest first is the useful default
    }
    bodies.slice().sort(function (a, b) {
      var x = a.getAttribute("data-" + key) || "";
      var y = b.getAttribute("data-" + key) || "";
      return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
    }).forEach(function (body) { table.appendChild(body); });
    heads.forEach(function (head) {
      head.setAttribute("aria-sort", head !== th ? "none"
        : (sortDir > 0 ? "ascending" : "descending"));
    });
  }

  bodies.forEach(function (body) {
    var row = body.querySelector(".row");
    if (!row) return;
    row.addEventListener("click", function (event) {
      // The record-type cell is a real anchor to the inline detail, so it works
      // with JS off. With JS on we intercept it and open the pane instead.
      var anchor = event.target.closest ? event.target.closest("a") : null;
      if (anchor && !anchor.classList.contains("rowlink")) return;
      event.preventDefault();
      show(body);
      pane.focus();
      // Remember the open capture in the address bar, so a reload (after a
      // correction) or a shared link reopens it.
      var id = body.querySelector(".detail") && body.querySelector(".detail").id;
      if (id && window.history && history.replaceState) history.replaceState(null, "", "#" + id);
    });
  });

  // `#cap-<folder>` opens that capture: on load (the popup's Review link, a
  // reload) and when an in-page link such as a "superseded" chip changes it.
  function fromHash() {
    var id = decodeURIComponent((window.location.hash || "").slice(1));
    if (id.indexOf("cap-") !== 0) return false;
    var target = document.getElementById(id);
    var body = target && target.closest ? target.closest("tbody.capture") : null;
    if (!body || bodies.indexOf(body) < 0) return false;
    body.hidden = false;
    show(body);
    if (body.scrollIntoView) body.scrollIntoView({ block: "nearest" });
    return true;
  }
  window.addEventListener("hashchange", fromHash);

  if (query) query.addEventListener("input", apply);
  facets.forEach(function (facet) {
    if (facet.el && !facet.multi) facet.el.addEventListener("change", apply);
  });
  heads.forEach(function (head) {
    head.tabIndex = 0;
    head.addEventListener("click", function () { sortBy(head.getAttribute("data-key"), head); });
    head.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        sortBy(head.getAttribute("data-key"), head);
      }
    });
  });

  // Copy: one citation for a family-tree program, or the whole capture as text to paste
  // into an AI assistant. Built from what the detail shows; only reads the page.
  function copyFallback(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    area.remove();
    return ok;
  }
  function copyText(text, button) {
    var label = button.textContent;
    function done(ok) {
      button.textContent = ok ? "Copied" : "Could not copy: select the text instead";
      setTimeout(function () { button.textContent = label; }, 1800);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { done(true); }, function () { done(copyFallback(text)); });
    } else {
      done(copyFallback(text));
    }
  }
  function textOf(el) { return el ? el.textContent.trim() : ""; }
  function captureAsText(box) {
    var lines = ["A genealogy source, captured with Genealogy Capsule.",
                 "Title: " + textOf(box.querySelector(".d-title"))];
    var gated = false;
    Array.prototype.forEach.call(box.querySelectorAll(".d-meta dt"), function (dt) {
      var key = dt.textContent;
      var dd = dt.nextElementSibling;
      if (["Source", "Record type", "Captured", "Accessed", "Gating", "Local"].indexOf(key) < 0) return;
      var link = dd && dd.querySelector("a");
      var value = key === "Source" && link ? link.href : textOf(dd).replace(/\\s*Open folder$/, "");
      if (key === "Gating" && value === "gated") gated = true;
      lines.push((key === "Local" ? "Capture folder" : key) + ": " + value);
    });
    Array.prototype.forEach.call(box.querySelectorAll(".cite"), function (p) {
      lines.push(textOf(p.querySelector(".cite-label")) + ": " + textOf(p.querySelector(".cite-text")));
    });
    if (gated) lines.push("Note: from a subscription or sign-in site; for personal research only.");
    var rows = box.querySelectorAll("table.assertions tbody tr");
    if (rows.length) {
      lines.push("", "Facts (person | field | value exactly as written | evidence | the record's words):");
      Array.prototype.forEach.call(rows, function (tr) {
        if (tr.classList.contains("retracted")) return;
        var cells = tr.children;
        var v = tr.querySelector(".v");
        var value = v ? v.textContent : (cells[2].firstChild ? cells[2].firstChild.textContent : "");
        var line = "- " + [textOf(cells[0]), textOf(cells[1]), value.trim(), textOf(cells[3])].join(" | ");
        var words = tr.getAttribute("data-snippet");
        lines.push(words ? line + ' | "' + words + '"' : line);
      });
    }
    return lines.join("\\n");
  }
  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest(".copy, .copy-ai") : null;
    if (!button) return;
    event.preventDefault();
    if (button.classList.contains("copy")) {
      copyText(textOf(button.parentNode.querySelector(".cite-text")), button);
      return;
    }
    var box = button.closest("#detail") || button.closest(".detail > td");
    if (box) copyText(captureAsText(box), button);
  });

  apply();
  fromHash();
})();
"""

# Interactive actions (delete + add-note), emitted ONLY when the backend serves
# the index and injects `window.__CAPSULE_TOKEN__`. Every request carries the
# token and targets the same localhost origin that served the page. Uses event
# delegation so it also covers rows added after load.
INDEX_DELETE_JS = """\
(function () {
  "use strict";
  var token = window.__CAPSULE_TOKEN__ || "";
  function backend(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "X-Capsule-Token": token }, opts.headers || {});
    return fetch(path, opts);
  }
  function failText(r, b, verb) {
    var detail = b && b.detail ? b.detail : r.statusText;
    return verb + " failed (" + r.status + "): " + detail;
  }
  // A small in-page modal with a multi-line textarea — nicer than the native
  // single-line prompt. Created on demand and removed on close.
  function openNoteModal(captureId) {
    var backdrop = document.createElement("div");
    backdrop.className = "capsule-modal-backdrop";
    var modal = document.createElement("div");
    modal.className = "capsule-modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", "Add a note");
    var h = document.createElement("h2");
    h.textContent = "Add a note";
    var ta = document.createElement("textarea");
    ta.className = "capsule-modal-text";
    ta.rows = 5;
    ta.placeholder = "why you're keeping this, what to revisit…";
    var hint = document.createElement("p");
    hint.className = "capsule-modal-hint";
    hint.textContent = "Ctrl/⌘ + Enter to save · Esc to cancel";
    var actions = document.createElement("div");
    actions.className = "capsule-modal-actions";
    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "capsule-modal-cancel";
    cancel.textContent = "Cancel";
    var save = document.createElement("button");
    save.type = "button";
    save.className = "capsule-modal-save";
    save.textContent = "Save note";
    actions.appendChild(cancel);
    actions.appendChild(save);
    modal.appendChild(h);
    modal.appendChild(ta);
    modal.appendChild(hint);
    modal.appendChild(actions);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    ta.focus();

    function close() {
      document.removeEventListener("keydown", onKey);
      backdrop.remove();
    }
    function onKey(ev) {
      if (ev.key === "Escape") close();
    }
    document.addEventListener("keydown", onKey);
    backdrop.addEventListener("click", function (ev) {
      if (ev.target === backdrop) close();
    });
    cancel.addEventListener("click", close);
    ta.addEventListener("keydown", function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") save.click();
    });
    save.addEventListener("click", function () {
      var text = ta.value.trim();
      if (!text) {
        ta.focus();
        return;
      }
      save.disabled = true;
      cancel.disabled = true;
      backend("/note", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ capture_id: captureId, text: text }),
      })
        .then(function (r) {
          if (r.ok) {
            window.location.reload(); // re-render so the new note appears
          } else {
            r.json().catch(function () { return {}; }).then(function (b) {
              save.disabled = false;
              cancel.disabled = false;
              window.alert(failText(r, b, "Add note"));
            });
          }
        })
        .catch(function (err) {
          save.disabled = false;
          cancel.disabled = false;
          window.alert("Add note failed: " + err);
        });
    });
  }
  document.addEventListener("click", function (e) {
    var del = e.target.closest(".trash");
    if (del) {
      e.preventDefault();
      var id = del.getAttribute("data-capture-id");
      if (!id) return;
      if (!window.confirm("Are you sure you want to delete this record and all of its contents?")) {
        return;
      }
      backend("/capture/" + encodeURIComponent(id), { method: "DELETE" })
        .then(function (r) {
          if (r.ok) {
            var tb = del.closest("tbody.capture");
            if (tb) tb.remove();
          } else {
            r.json().catch(function () { return {}; }).then(function (b) {
              window.alert(failText(r, b, "Delete"));
            });
          }
        })
        .catch(function (err) { window.alert("Delete failed: " + err); });
      return;
    }
    var add = e.target.closest(".d-notes-add");
    if (add) {
      e.preventDefault();
      var cid = add.getAttribute("data-capture-id");
      if (cid) openNoteModal(cid);
      return;
    }
  });
})();
"""

# M1 corrections (C6), emitted ONLY on the served page, beside INDEX_DELETE_JS: ✎ edit,
# ✕ retract and "+ Add a fact" each open one small form and POST /correction with the
# page's token. The backend appends the correction (nothing is overwritten); the page
# then reloads, and the address keeps the capture open (`#cap-<folder>`).
INDEX_CORRECT_JS = """\
(function () {
  "use strict";
  var token = window.__CAPSULE_TOKEN__ || "";
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function detail(b, r) {
    var d = b && b.detail;
    if (Array.isArray(d)) d = d.map(function (x) { return x.msg || JSON.stringify(x); }).join("; ");
    return d || r.statusText;
  }
  // `build(modal)` adds the form's fields and returns `read()`, which gives the request
  // body, or null after focusing whatever still needs filling in.
  function openForm(title, build, submitLabel) {
    var backdrop = el("div", "capsule-modal-backdrop");
    var modal = el("div", "capsule-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", title);
    modal.appendChild(el("h2", null, title));
    var read = build(modal);
    var err = el("p", "capsule-modal-error");
    err.hidden = true;
    modal.appendChild(err);
    modal.appendChild(el("p", "capsule-modal-hint", "Ctrl/⌘ + Enter to save · Esc to cancel"));
    var actions = el("div", "capsule-modal-actions");
    var cancel = el("button", "capsule-modal-cancel", "Cancel");
    cancel.type = "button";
    var save = el("button", "capsule-modal-save", submitLabel);
    save.type = "button";
    actions.appendChild(cancel);
    actions.appendChild(save);
    modal.appendChild(actions);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    var first = modal.querySelector("input,textarea,select");
    (first || save).focus();

    // While a save is in flight the form cannot be dismissed: a failure must land in a
    // form that is still on screen, with what was typed, never in a detached one.
    var pending = false;
    function close() {
      if (pending) return;
      document.removeEventListener("keydown", onKey);
      backdrop.remove();
    }
    function onKey(ev) {
      if (ev.key === "Escape") close();
      else if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") save.click();
    }
    function fail(msg) {
      pending = false;
      save.disabled = false;
      cancel.disabled = false;
      err.textContent = "Not saved: " + msg;
      err.hidden = false;
    }
    document.addEventListener("keydown", onKey);
    backdrop.addEventListener("click", function (ev) { if (ev.target === backdrop) close(); });
    cancel.addEventListener("click", close);
    save.addEventListener("click", function () {
      if (save.disabled) return;
      var body = read();
      if (!body) return;
      pending = true;
      save.disabled = true;
      cancel.disabled = true;
      err.hidden = true;
      fetch("/correction", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Capsule-Token": token },
        body: JSON.stringify(body),
      })
        .then(function (r) {
          if (r.ok) { window.location.reload(); return; }
          return r.json().catch(function () { return {}; }).then(function (b) {
            fail("(" + r.status + ") " + detail(b, r));
          });
        })
        .catch(function (e) { fail(String(e)); });
    });
  }
  function field(modal, labelText, input) {
    var label = el("label", "capsule-fix-field");
    label.appendChild(el("span", null, labelText));
    label.appendChild(input);
    modal.appendChild(label);
    return input;
  }
  function textInput(value) {
    var i = el("input", "capsule-modal-text");
    i.type = "text";
    i.value = value || "";
    return i;
  }
  function textArea(rows, placeholder) {
    var t = el("textarea", "capsule-modal-text");
    t.rows = rows;
    if (placeholder) t.placeholder = placeholder;
    return t;
  }
  function select(options) {
    var s = el("select", "capsule-modal-text");
    options.forEach(function (o) {
      var opt = el("option", null, o[1]);
      opt.value = o[0];
      s.appendChild(opt);
    });
    return s;
  }
  // The supporting words, so a value is checked against the record, not memory.
  function quote(modal, text) {
    if (!text) return;
    var q = el("blockquote", "capsule-fix-quote");
    q.appendChild(el("span", "capsule-fix-says", "The page says"));
    q.appendChild(el("span", null, text));
    modal.appendChild(q);
  }
  // The fact's earlier corrections, oldest first: kept here, not in the table row.
  function historyList(modal, lines) {
    if (!lines.length) return;
    var box = el("div", "capsule-fix-history");
    box.appendChild(el("span", "capsule-fix-says", "Earlier corrections"));
    var list = el("ol");
    lines.forEach(function (line) { list.appendChild(el("li", null, line)); });
    box.appendChild(list);
    modal.appendChild(box);
  }
  function factOf(button) {
    var row = button.closest("tr");
    var table = button.closest("table.assertions");
    var v = row.querySelector(".v");
    var history = [];
    try { history = JSON.parse(row.getAttribute("data-history") || "[]"); } catch (e) { history = []; }
    return {
      history: history,
      captureId: table.getAttribute("data-capture-id"),
      id: row.getAttribute("data-assertion-id"),
      snippet: row.getAttribute("data-snippet"),
      person: row.children[0].textContent,
      field: row.children[1].textContent,
      value: v ? v.textContent : "",
    };
  }
  function rationale(input) { return input.value.trim() || null; }

  function editFact(button) {
    var f = factOf(button);
    openForm("Correct " + f.field, function (modal) {
      modal.appendChild(el("p", "capsule-fix-who", f.person + " · " + f.field));
      quote(modal, f.snippet);
      historyList(modal, f.history);
      var value = field(modal, "Value, exactly as the record has it ([?] for illegible)",
        textInput(f.value));
      var why = field(modal, "Why (optional)", textArea(2, "e.g. the day is 18, not 13"));
      return function () {
        var v = value.value.trim();
        if (!v || v === f.value) { value.focus(); return null; }
        return { capture_id: f.captureId, action: "edit", assertion_id: f.id,
                 new_value: v, rationale: rationale(why) };
      };
    }, "Save correction");
  }

  function retractFact(button) {
    var f = factOf(button);
    openForm("Retract this fact?", function (modal) {
      modal.appendChild(el("p", "capsule-fix-who", f.person + " · " + f.field + ": " + f.value));
      quote(modal, f.snippet);
      historyList(modal, f.history);
      modal.appendChild(el("p", "capsule-modal-hint",
        "It stays listed, struck through, with your reason. Nothing is deleted."));
      var why = field(modal, "Why (optional)",
        textArea(2, "e.g. this is the informant's age, not the deceased's"));
      return function () {
        return { capture_id: f.captureId, action: "retract", assertion_id: f.id,
                 rationale: rationale(why) };
      };
    }, "Retract");
  }

  function addFact(button) {
    var people = [];
    try { people = JSON.parse(button.getAttribute("data-personas") || "[]"); } catch (e) { people = []; }
    var captureId = button.getAttribute("data-capture-id");
    openForm("Add a fact", function (modal) {
      var who = field(modal, "Person", select(people.map(function (p) { return [p.id, p.label]; })));
      var name = field(modal, "Field (e.g. birth_date, residence, father)", textInput(""));
      var value = field(modal, "Value, exactly as the record has it ([?] for illegible)",
        textInput(""));
      var kind = field(modal, "Evidence", select([
        ["stated", "stated: the record says it"],
        ["calculated", "calculated: worked out from what it says"],
        ["estimated", "estimated"],
      ]));
      var words = field(modal, "The words on the page that support it",
        textArea(3, "copy them exactly from the record"));
      var why = field(modal, "Why (optional)", textArea(2, ""));
      return function () {
        var body = { capture_id: captureId, action: "add", persona_id: who.value,
                     field: name.value.trim(), new_value: value.value.trim(),
                     evidence_type: kind.value, snippet_text: words.value.trim(),
                     rationale: rationale(why) };
        var missing = [[name, body.field], [value, body.new_value], [words, body.snippet_text]]
          .filter(function (p) { return !p[1]; });
        if (missing.length) { missing[0][0].focus(); return null; }
        return body;
      };
    }, "Add fact");
  }

  // "Open folder": the backend opens it in Finder / File Explorer on this computer.
  document.addEventListener("click", function (e) {
    var reveal = e.target.closest ? e.target.closest(".reveal") : null;
    if (!reveal) return;
    e.preventDefault();
    var id = reveal.getAttribute("data-capture-id");
    var label = reveal.textContent;
    reveal.disabled = true;
    fetch("/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Capsule-Token": token },
      body: JSON.stringify(id ? { capture_id: id } : {}),
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          reveal.textContent = r.ok ? "Opened" : "Could not open (" + detail(b, r) + ")";
        });
      })
      .catch(function (err) { reveal.textContent = "Could not open (" + err + ")"; })
      .then(function () {
        setTimeout(function () { reveal.textContent = label; reveal.disabled = false; }, 2500);
      });
  });

  document.addEventListener("click", function (e) {
    var button = e.target.closest ? e.target.closest(".fix-edit,.fix-retract,.fix-add") : null;
    if (!button) return;
    e.preventDefault();
    if (button.classList.contains("fix-edit")) editFact(button);
    else if (button.classList.contains("fix-retract")) retractFact(button);
    else addFact(button);
  });
})();
"""
