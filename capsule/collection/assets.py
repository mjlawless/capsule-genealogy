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

  facets.forEach(function (facet) {
    if (!facet.el) return;
    var seen = Object.create(null);
    var values = [];
    bodies.forEach(function (body) {
      var value = body.getAttribute("data-" + facet.key);
      if (value && !seen[value]) { seen[value] = true; values.push(value); }
    });
    values.sort().forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      facet.el.appendChild(option);
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
        if (ok && facet.el && facet.el.value) {
          ok = body.getAttribute("data-" + facet.key) === facet.el.value;
        }
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
    });
  });

  if (query) query.addEventListener("input", apply);
  facets.forEach(function (facet) {
    if (facet.el) facet.el.addEventListener("change", apply);
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

  apply();
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
