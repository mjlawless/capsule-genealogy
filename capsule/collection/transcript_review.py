"""The crop-beside-value review page for a machine transcript (REQ-006 T7).

§5.5 calls per-field confidence plus crop-beside-value review "the feature that makes
the rest safe": a human checks the shaky readings against the exact pixels they came
from. The page renders a capture's latest `transcript/<id>/transcript.json` —

1. the caution banner (a machine reading, not the record),
2. image-vs-index conflicts first, both values side by side, no winner,
3. every field beside its crop, the least trustworthy first (needs-review, then
   lowest confidence), uncertainty markers highlighted.

Read-only. Everything the model wrote is untrusted and escaped.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from capsule.capture.manifest import TRANSCRIPT_DIR
from capsule.collection.escape import esc

_MARKER_RE = re.compile(r"\[[^\]]*\]")


def latest_transcript(capture_folder: Path) -> Optional[dict]:
    """The newest readable transcript.json under the capture, or None."""
    newest: Optional[dict] = None
    for path in sorted((Path(capture_folder) / TRANSCRIPT_DIR).glob("*/transcript.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            created = doc["transcription"]["created_at"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if newest is None or created > newest["transcription"]["created_at"]:
            newest = doc
    return newest


def render_review(capture_id: str, doc: dict) -> str:
    run = doc["transcription"]
    fields = sorted(doc.get("fields", []), key=lambda f: (not f["needs_review"], f["confidence"]))
    by_id = {f["id"]: f for f in fields}
    comparisons = doc.get("comparisons", [])
    conflicts = [c for c in comparisons if c["verdict"] == "conflict"]
    unclear = [c for c in comparisons if c["verdict"] == "unclear"]
    agree = sum(c["verdict"] == "agree" for c in comparisons)
    to_review = sum(f["needs_review"] for f in fields)
    crop_base = f"/{capture_id}/{TRANSCRIPT_DIR}/{run['id']}/"

    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        f"<title>Transcript review · {esc(capture_id)}</title>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<style>{_CSS}</style></head><body>",
        f"<p class=caution>{esc(doc.get('caution', ''))}</p>",
        f"<h1>{esc(run['media_filename'])}</h1>",
        f"<p class=meta>capture <code>{esc(capture_id)}</code> · run <code>{esc(run['id'])}</code> "
        f"· {esc(run['model'])} · {esc(run['created_at'])} · status <b>{esc(run['status'])}</b> "
        f"· {len(fields)} fields, {to_review} to review · index: {len(conflicts)} conflict(s), "
        f"{len(unclear)} unclear, {agree} agree</p>",
    ]
    if run.get("problems"):
        parts.append("<details><summary>Pipeline problems</summary><ul>")
        parts += [f"<li>{esc(p)}</li>" for p in run["problems"]]
        parts.append("</ul></details>")
    if conflicts or unclear:
        parts.append("<h2>Image vs. index</h2><p class=meta>Both values are kept. "
                     "Neither wins; deciding is yours.</p><table class=cmp>"
                     "<tr><th>Index says</th><th>Image says</th><th>Verdict</th><th>Why</th><th>Crop</th></tr>")
        for c in conflicts + unclear:
            field = by_id.get(c["transcript_field_id"])
            crop = _img(crop_base, field) if field else ""
            parts.append(
                f"<tr class=\"{esc(c['verdict'])}\"><td>{esc(c['index_value'])}</td>"
                f"<td>{_value(c.get('transcript_value'))}</td><td>{esc(c['verdict'])}</td>"
                f"<td>{esc(c.get('explanation') or '')}</td><td>{crop}</td></tr>"
            )
        parts.append("</table>")
    parts.append("<h2>Fields</h2><table class=fields><tr><th>Crop</th><th>Reading</th></tr>")
    for f in fields:
        cls = "review" if f["needs_review"] else "ok"
        parts.append(
            f"<tr class=\"{cls}\"><td>{_img(crop_base, f)}</td><td>"
            f"<div class=label><code>{esc(f['field_key'])}</code> {esc(f.get('form_label') or '')}</div>"
            f"<div class=value>{_reading(f)}</div>"
            f"<div class=meta>{esc(f['status'])} · confidence {f['confidence']:.2f}"
            f"{' · to review' if f['needs_review'] else ''}"
            f"{' · attempts ' + str(f['attempts']) if f.get('attempts', 1) > 1 else ''}</div>"
            + (f"<div class=ann>annotation: {esc(f['annotation'])}</div>" if f.get("annotation") else "")
            + (f"<div class=note>note: {esc(f['note'])}</div>" if f.get("note") else "")
            + "</td></tr>"
        )
    parts.append("</table></body></html>")
    return "".join(parts)


def _img(base: str, field: dict) -> str:
    return f'<img src="{esc(base + field["crop_filename"])}" alt="{esc(field["field_key"])}" loading=lazy>'


def _value(text: Optional[str]) -> str:
    if text is None:
        return "<i>(blank on the form)</i>"
    return _MARKER_RE.sub(lambda m: f"<mark>{m.group(0)}</mark>", esc(text))


def _reading(field: dict) -> str:
    shown = _value(field.get("verbatim_value"))
    if field["status"] == "unknown":
        shown += " <span class=meta>— the informant did not know</span>"
    return shown


_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1b18;--muted:#6b665d;--line:#e2ded5;--warn:#fff4d6;--bad:#fde8e6;--mark:#ffe08a}
@media (prefers-color-scheme:dark){:root{--bg:#171614;--fg:#ece8e0;--muted:#a39d91;--line:#35322d;
--warn:#3a3220;--bad:#3d2522;--mark:#6b5a1c}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px}
.caution{background:var(--warn);border:1px solid var(--line);padding:10px 12px;border-radius:6px}
.meta{color:var(--muted);font-size:13px}
table{border-collapse:collapse;width:100%}td,th{border-top:1px solid var(--line);padding:8px;vertical-align:top;text-align:left}
tr.review td{background:var(--warn)}tr.conflict td{background:var(--bad)}
.fields td:first-child{width:55%}img{max-width:100%;height:auto;border:1px solid var(--line)}
.cmp img{max-width:260px}.value{font:17px/1.4 ui-monospace,monospace;margin:4px 0}mark{background:var(--mark);color:inherit}
"""
