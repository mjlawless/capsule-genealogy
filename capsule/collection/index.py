"""Render and write `captures/index.html` — the human browse surface.

Spec: `docs/specs/captures-index-v2.md`. `render_index` is a pure function
(records + a timestamp -> an HTML document); `write_index` walks a `captures/`
directory via `read_all`, writes the result, and removes the superseded
`Captures Index.md` if it finds one.

Keeping `render_index` pure is what lets the same template serve two delivery
modes (spec §6): the static archival file written here, and — once phase 2 needs
actions — a page served by the localhost backend. Nothing in this module knows
which it is producing.

Every interpolated value goes through `escape.esc`, and every URL through
`escape.href`, without exception; see that module for why.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from capsule.collection.assets import INDEX_CORRECT_JS, INDEX_CSS, INDEX_DELETE_JS, INDEX_JS
from capsule.collection.escape import esc, href
from capsule.collection.help import HELP_FILENAME, render_help
from capsule.collection.read import AssertionView, CaptureRecord, CorrectionView, read_all

#: A plain, same-folder filename — no path separators. The primary-artifact name
#: comes from the manifest (untrusted); a relative link is only built when the
#: name is a bare file, so a hostile `local_snapshot` can never walk the path.
_SAFE_FILE_RE = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9._-]*\Z")

#: Inline SVGs (Feather "trash-2" / "file-plus"), stroked with `currentColor` so
#: CSS controls colour. Crisp and legible at any zoom, unlike a faint emoji glyph.
_TRASH_SVG = (
    '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="3 6 5 6 21 6"></polyline>'
    '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2">'
    "</path><line x1='10' y1='11' x2='10' y2='17'></line>"
    "<line x1='14' y1='11' x2='14' y2='17'></line></svg>"
)
_ADD_NOTE_SVG = (
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>'
    '<polyline points="14 2 14 8 20 8"></polyline>'
    "<line x1='12' y1='12' x2='12' y2='18'></line>"
    "<line x1='9' y1='15' x2='15' y2='15'></line></svg>"
)

_logger = logging.getLogger(__name__)

INDEX_FILENAME = "index.html"

#: The `captures-index/v1` filename. Deleted on write — a stale evidence index
#: is worse than no evidence index (spec §0).
LEGACY_INDEX_FILENAME = "Captures Index.md"

# Metadata a later milestone fills in; rendered as this until then (spec §4).
_PENDING = "—"

_COLUMNS = 6

_HEADINGS = (
    ("status", "Status"),
    ("type", "Record"),
    ("persona", "Person"),
    ("site", "Site"),
    ("date", "Captured"),
    (None, "Files"),
)


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stamp(captured_at: Optional[str]) -> str:
    """`2026-07-08 14:32 UTC` — the displayed capture moment.

    Access *time*, not just date, belongs in the citation: two captures of one URL
    on the same day can differ, and the folder id carries only the date. Rendered
    by slicing the ISO string rather than parsing it, so a malformed timestamp
    degrades to whatever prefix exists instead of raising inside the renderer.
    """
    if not captured_at:
        return ""
    day, _, rest = captured_at.partition("T")
    return f"{day} {rest[:5]} UTC" if len(rest) >= 5 else day


def _link(url: str) -> str:
    """An `<a>` if the URL is a safe http(s) link, else inert escaped text."""
    safe = href(url)
    if safe is None:
        return f'<span class="url">{esc(url)}</span>'
    return f'<a class="url" href="{safe}" target="_blank" rel="noopener noreferrer">{esc(url)}</a>'


def _search_blob(record: CaptureRecord) -> str:
    """The lowercased haystack the client-side filter matches against.

    Identity comes from `folder_name`, not the manifest's `capture_id`: the two
    normally agree, and preferring the directory keeps manifest-supplied text out
    of the rendered document entirely (spec §2, §3.3).
    """
    parts = (
        record.record_type or "",
        record.record_category,
        record.primary_persona or "",
        record.site_name or "",
        record.status,
        record.short_note or "",
        record.folder_name,
        record.url,
        # Searching "vtls000632043" should find the index entry that cites it,
        # not only the register capture itself.
        *(ref.citation for ref in record.external_refs),
        # Notes are how the researcher will re-find "why did I keep this" — the
        # filter must match their words, not only the extracted facts.
        *(note.text for note in record.notes),
    )
    return " ".join(part for part in parts if part).lower()


def _row(record: CaptureRecord, interactive: bool) -> str:
    """The always-visible one-line summary of a capture."""
    chips = "".join(f'<span class="chip">{esc(kind)}</span>' for kind in record.file_types)
    lock = (
        '<span class="lock" title="gated — local snapshot only">🔒</span>'
        if record.gating == "gated"
        else ""
    )
    refs = (
        f'<span class="chip refchip" title="references {len(record.external_refs)} '
        f'source document(s)">↗ source</span>'
        if record.external_refs
        else ""
    )
    # The trashcan only appears when the index is served interactively by the
    # backend (which alone can move the folder on disk). A static file never shows
    # a button that could not work.
    trash = (
        f'<button type="button" class="trash" data-capture-id="{esc(record.folder_name)}" '
        f'title="Delete this record" aria-label="Delete this record">{_TRASH_SVG}</button>'
        if interactive
        else ""
    )
    # REQ-011 — a superseded capture stays listed (nothing is ever hidden or deleted), but
    # says plainly that another capture replaces it, and links there.
    superseded = ""
    if record.superseded_by:
        superseded = (
            f'<a class="chip superseded" href="#cap-{esc(record.superseded_by)}" '
            f'title="superseded — cite the replacement">superseded</a>'
        )
    elif record.superseded:
        superseded = (
            '<span class="chip superseded" title="superseded — the replacement is not in '
            'this collection">superseded</span>'
        )
    pending = (
        '<span class="chip pending" title="archived without an API key; not read yet">'
        "not read yet</span>"
        if record.extraction_pending
        else ""
    )
    # The capture's own verdict that it does not hold the evidence (e.g. a scan viewer
    # whose image was not saved): it must not look like any other row.
    if record.verify_ok is False:
        why = "; ".join(record.verify_shortfall) or "this capture does not hold the record"
        pending += f'<span class="chip incomplete" title="{esc(why)}">incomplete</span>'
    return (
        '<tr class="row">'
        f'<td><span class="pill s-{esc(record.status)}">{esc(record.status)}</span>'
        f"{superseded}{pending}</td>"
        f'<td><a class="rowlink" href="#cap-{esc(record.folder_name)}">'
        f'<span title="{esc(record.record_type or "no record type given")}">'
        f'{esc(record.record_category)}</span></a></td>'
        f"<td>{esc(record.primary_persona or _PENDING)}</td>"
        f"<td><code>{esc(record.site_name or _PENDING)}</code></td>"
        f'<td class="stamp">{esc(_stamp(record.captured_at))}</td>'
        f'<td class="files">{lock}{refs}{chips}{trash}</td>'
        "</tr>"
    )


def _doc_link(record: CaptureRecord) -> Optional[str]:
    """A direct link to the archived document (`document.pdf`), or None.

    Only for a document capture whose primary-artifact name is a safe bare
    filename — so a manifest-supplied `local_snapshot` can never build a
    path-walking or scheme-bearing link. HTML snapshots are reached via the
    folder link instead.
    """
    name = record.primary_artifact or ""
    if not record.is_document or not _SAFE_FILE_RE.match(name):
        return None
    return f'<a href="./{esc(record.folder_name)}/{esc(name)}">{esc(name)}</a>'


def _reviewed(record: CaptureRecord) -> str:
    if not record.correction_count:
        return _PENDING
    noun = "correction" if record.correction_count == 1 else "corrections"
    latest = _stamp(record.reviewed_at)
    return esc(f"{record.correction_count} {noun}" + (f" · latest {latest}" if latest else ""))


def _meta(record: CaptureRecord, interactive: bool = False) -> list[tuple[str, str]]:
    """Definition-list pairs: the citation's facts, then the lifecycle spine."""
    media = str(len(record.media_files)) if record.media_files else _PENDING
    local = f'<a href="./{esc(record.folder_name)}/">{esc(record.folder_name)}/</a>'
    doc = _doc_link(record)
    pairs: list[tuple[str, str]] = [
        ("Source", _link(record.url) if record.url else _PENDING),
        # The reader's own words for the record; the row shows its common category.
        ("Record type", esc(f"{record.record_type} ({record.record_category})")
         if record.record_type else esc(record.record_category)),
        ("Local", local + (
            # Served page only: the backend opens the folder in Finder / File Explorer
            # (a web page cannot open a folder on disk itself).
            f' <button type="button" class="reveal" data-capture-id="{esc(record.folder_name)}">'
            "Open folder</button>" if interactive else ""
        )),
    ]
    if doc:
        pairs.append(("Document", doc))
    if record.superseded_by:
        pairs.append((
            "Superseded by",
            f'<a href="#cap-{esc(record.superseded_by)}">{esc(record.superseded_by)}</a>',
        ))
    elif record.superseded:
        pairs.append(("Superseded by", "a capture not in this collection"))
    pairs += [
        ("Captured", esc(record.captured_at or _PENDING)),
        ("Accessed", esc(record.access_date or _PENDING)),
    ]
    # Wayback submission was dropped (owner, 2026-10-23): the local snapshot IS the
    # archive. The field stays in the capture format; a capture that ever carries one
    # still shows it.
    if record.wayback_url:
        pairs.append(("Wayback", _link(record.wayback_url)))
    pairs += [
        ("Gating", esc(record.gating)),
        ("Copyright", "flagged" if record.copyright_flag else "not flagged"),
        ("Media", esc(media)),
        # `reviewed` comes from the correction log, overlaid only on the served page.
        # Only stages with data behind them are shown (spec §4): matched and attached
        # return as lines when the Gramps work gives them something real to say.
        ("Reviewed", _reviewed(record)),
        ("Transcribed", _transcribed(record, interactive)),
    ]
    if record.verify_ok is False:
        why = "; ".join(record.verify_shortfall) or "this capture does not hold the record"
        pairs.append(("Incomplete", esc(why)))
    return pairs


def _transcribed(record: CaptureRecord, interactive: bool) -> str:
    """When the scan was last read by vision; on the served page, a link to that reading."""
    if not record.transcribed_at:
        return _PENDING
    when = esc(_stamp(record.transcribed_at))
    if not interactive:  # the review page exists only where the backend serves it
        return when
    return f'<a href="/review/{esc(record.folder_name)}">{when} · review the reading</a>'


def _history_line(c: CorrectionView) -> str:
    """`“Anna Grimm” → “Annie Grimm” · owner · 2026-10-23 18:02 UTC — why`, as plain
    text (the caller escapes it): one correction, both sides named."""
    who = f" · {c.actor}"
    when = _stamp(c.created_at)
    who += f" · {when}" if when else ""
    who += f" — {c.rationale}" if c.rationale else ""
    if c.action == "edit":
        return f"“{c.previous_value or ''}” → “{c.new_value or ''}”{who}"
    if c.action == "add":
        return f"added “{c.new_value or ''}”{who}"
    return f"retracted “{c.previous_value or ''}”{who}"


def _tag(a: AssertionView) -> str:
    """A one-word mark that a fact was corrected; the history itself is in the ✎ form
    (and in the tag's tooltip, for a retracted fact, which has no form)."""
    if not a.corrections:
        return ""
    actions = [c.action for c in a.corrections]
    if a.retracted:
        word = "retracted"
    elif actions[0] == "add":
        word = "added, edited" if "edit" in actions else "added"
    else:
        word = "edited"
    history = "\n".join(_history_line(c) for c in a.corrections)
    return f' <span class="tag" title="{esc(history)}">{word}</span>'


def _assertion_row(a: AssertionView, interactive: bool) -> str:
    """One fact. On the served page it carries its id (the target of a correction)
    and, once corrected, a tag; a retracted fact stays visible, struck."""
    if a.retracted:
        value = f"<s>{esc(a.value)}</s>"
    elif interactive:  # the served page's script reads the current value from here
        value = f'<span class="v">{esc(a.value)}</span>'
    else:
        value = esc(a.value)
    value += _tag(a)
    # The record's own words for the fact, on both pages: the ✎ form quotes them, and
    # "Copy for AI" carries them so the assistant can check each value against the record.
    attrs = f' data-snippet="{esc(a.snippet)}"' if a.snippet else ""
    actions = ""
    if interactive and a.id:
        attrs += f' data-assertion-id="{esc(a.id)}"'
        if a.corrections:
            lines = [_history_line(c) for c in a.corrections]
            attrs += f' data-history="{esc(json.dumps(lines, ensure_ascii=False))}"'
        actions = (
            '<button type="button" class="fix fix-edit" title="Correct this value" '
            'aria-label="Correct this value">✎</button>'
            '<button type="button" class="fix fix-retract" title="Retract this fact" '
            'aria-label="Retract this fact">✕</button>'
        )
    if a.retracted:
        attrs += ' class="retracted"'
    # The actions cell exists only on the served page; the static file keeps four columns.
    cell = f'<td class="fixes">{actions}</td>' if interactive else ""
    return (
        f"<tr{attrs}><td>{esc(a.persona_label)}</td><td>{esc(a.field)}</td>"
        f"<td>{value}</td><td>{esc(a.evidence_type)}</td>{cell}</tr>"
    )


def _add_fact_button(record: CaptureRecord) -> str:
    """"+ Add a fact", on the served page, when the capture names someone to add it to."""
    named = [p for p in record.personas if p.id]
    if not named:
        return ""
    # Same-name people are common (a household, a Sr and Jr), and choosing the wrong one
    # would put the fact on the wrong person: name the role, and number exact repeats.
    repeats = Counter((p.label, p.role) for p in named)
    seen: Counter[tuple[str, str]] = Counter()
    people = []
    for p in named:
        key = (p.label, p.role)
        seen[key] += 1
        text = p.label + (f" ({p.role})" if p.role and p.role != "?" else "")
        if repeats[key] > 1:
            text += f" #{seen[key]}"
        people.append({"id": p.id, "label": text})
    return (
        f'<button type="button" class="fix-add" data-capture-id="{esc(record.folder_name)}" '
        f'data-personas="{esc(json.dumps(people))}">+ Add a fact</button>'
    )


def _assertions(record: CaptureRecord, interactive: bool = False) -> str:
    """The fact table; on the served page, with ✎/✕ per fact and "+ Add a fact"."""
    add = _add_fact_button(record) if interactive else ""
    return _assertion_table(record, interactive) + add


def _assertion_table(record: CaptureRecord, interactive: bool) -> str:
    if not record.assertions:
        # Prefer the MODEL's own explanation of why this page yielded nothing ("the
        # record is a scanned image; no transcribable text") over our generic guess.
        # That sentence is what distinguishes "this source holds nothing" from "this
        # source holds a scan we cannot read yet" — the whole reason a zero-assertion
        # capture is a SUCCESSFUL capture. Attributed to the extractor, so it can never
        # be mistaken for the researcher's own note. Escaped like any other LLM output.
        if record.extraction_notes:
            return (
                '<p class="empty-note">No structured assertions extracted. '
                f"<em>Extractor:</em> {esc(record.extraction_notes)}</p>"
            )
        if record.extraction_pending:
            return (
                '<p class="empty-note">Not read yet: this page was saved without an '
                "Anthropic API key. Add a key in the Capsule popup and choose "
                "“Read them now”.</p>"
            )
        return '<p class="empty-note">No facts were read from this page.</p>'
    rows = "".join(_assertion_row(a, interactive) for a in record.assertions)
    table_attrs = f' data-capture-id="{esc(record.folder_name)}"' if interactive else ""
    fixes_head = '<th><span class="sr-only">Correct</span></th>' if interactive else ""
    return (
        f'<table class="assertions"{table_attrs}><thead><tr><th>Persona</th><th>Field</th>'
        f"<th>Value</th><th>Evidence</th>{fixes_head}</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _references(record: CaptureRecord) -> str:
    """What original document this page says it was made from.

    A derivative — a RootsIreland index entry, say — names the register it
    transcribes. We show the pointer and link it, but the index draws no line
    between two captures: whether the referenced register is one we hold, and
    whether it is *this leaf* of it, is an evidence-typed judgment that belongs
    to `SourceRelation` at M2. See `docs/design/rootsireland-nli-linkage.md`.
    """
    if not record.external_refs:
        return ""
    items = []
    for ref in record.external_refs:
        target = _link(ref.url) if ref.url else esc(ref.citation)
        label = f"{esc(ref.label)} — " if ref.label else ""
        scope = "" if ref.locator else ' <span class="pending">(document, not page)</span>'
        items.append(
            f'<li>{label}<code>{esc(ref.citation)}</code>{scope}<br>{target}</li>'
        )
    return (
        '<div class="d-refs"><h3>References source document</h3>'
        f'<ul>{"".join(items)}</ul></div>'
    )


def _notes_block(record: CaptureRecord, interactive: bool) -> str:
    """The researcher's notes — the user's own words about why this was kept.

    Rendered distinctly from assertions (which are source evidence): notes are
    analysis, carry no evidence type, and are shown newest-context last (append
    order). When the index is served interactively an "add note" pencil is shown
    (even with no notes yet, so the first can be added); a static file with no
    notes renders nothing.
    """
    # The add-note control sits BELOW the notes (after the list / the empty
    # message), only in interactive mode.
    add = (
        f'<button type="button" class="d-notes-add" data-capture-id="{esc(record.folder_name)}" '
        f'title="Add a note">{_ADD_NOTE_SVG}<span>Add note</span></button>'
        if interactive
        else ""
    )
    if not record.notes:
        if not interactive:
            return ""
        return (
            '<div class="d-notes"><h3>Notes</h3>'
            f'<p class="empty-note">No notes yet.</p>{add}</div>'
        )
    items = []
    for note in record.notes:
        when = _stamp(note.created_at)
        meta = f" · {esc(when)}" if when else ""
        items.append(
            f'<li><p class="note-text">{esc(note.text)}</p>'
            f'<p class="note-by">— {esc(note.author)}{meta}</p></li>'
        )
    return (
        '<div class="d-notes"><h3>Notes</h3>'
        f'<ul class="notes">{"".join(items)}</ul>{add}</div>'
    )


def _citation_block(record: CaptureRecord) -> str:
    """The citation to carry into a family-tree program, with Copy buttons, and "Copy for
    AI" (the whole capture as text to paste into an assistant). Copying only reads the
    page, so unlike the served page's actions it works in the static file too."""
    rows = []
    for label, text in (("Reference note", record.reference_note),
                        ("Source list entry", record.source_list_entry)):
        if text:
            rows.append(
                f'<p class="cite"><span class="cite-label">{label}</span>'
                f'<span class="cite-text">{esc(text)}</span>'
                '<button type="button" class="copy">Copy</button></p>'
            )
    return (
        '<div class="d-cite"><h3>Citation</h3>'
        + "".join(rows)
        + '<p><button type="button" class="copy-ai" title="Copy this capture as text to paste '
        'into Claude or another AI assistant">Copy for AI</button></p></div>'
    )


def _detail(record: CaptureRecord, interactive: bool) -> str:
    """The full record. Present in the HTML; the script hides it (spec §3.2)."""
    title = record.primary_persona or record.title or record.record_type or record.folder_name
    parts = [f'<h2 class="d-title">{esc(title)}</h2>']
    if record.short_note:
        parts.append(f'<p class="d-ref">{esc(record.short_note)}</p>')
    pairs = "".join(
        f"<dt>{key}</dt><dd>{value}</dd>" for key, value in _meta(record, interactive)
    )
    parts.append(f'<dl class="d-meta">{pairs}</dl>')
    parts.append(_citation_block(record))
    parts.append(_references(record))
    parts.append(_notes_block(record, interactive))
    parts.append(_assertions(record, interactive))
    return (
        f'<tr class="detail" id="cap-{esc(record.folder_name)}">'
        f'<td colspan="{_COLUMNS}">{"".join(parts)}</td></tr>'
    )


def _capture(record: CaptureRecord, interactive: bool) -> str:
    """One capture is one `<tbody>`: its row plus its detail, sorted as a unit."""
    attrs = " ".join(
        (
            # The common category (`record_types`), so the filter offers a dozen choices
            # rather than every wording the reader used; the exact type is in the detail.
            f'data-type="{esc(record.record_category)}"',
            f'data-site="{esc(record.site_name or "unknown")}"',
            f'data-status="{esc(record.status)}"',
            f'data-gating="{esc(record.gating)}"',
            # The full ISO timestamp, so sorting separates two captures made on
            # the same day. ISO-8601 UTC sorts correctly as a plain string.
            f'data-date="{esc(record.captured_at or "")}"',
            f'data-refs="{"yes" if record.external_refs else "no"}"',
            f'data-persona="{esc(record.primary_persona or "")}"',
            f'data-search="{esc(_search_blob(record))}"',
        )
    )
    return f'<tbody class="capture" {attrs}>{_row(record, interactive)}{_detail(record, interactive)}</tbody>'


def _thead() -> str:
    cells = "".join(
        f'<th data-key="{key}" aria-sort="none">{label}</th>' if key else f"<th>{label}</th>"
        for key, label in _HEADINGS
    )
    return f"<thead><tr>{cells}</tr></thead>"


def _controls(count: int) -> str:
    return (
        '<div class="controls">'
        '<input id="q" type="search" placeholder="Filter captures…" aria-label="Filter captures">'
        # Record and site take several choices at once (owner, 2026-10-23): a checkbox list
        # in a dropdown, filled by the script from the rows. Status stays a single choice.
        '<details class="ms" id="f-type" data-all="All records" data-noun="record types">'
        '<summary aria-label="Filter by record type">All records</summary>'
        '<div class="ms-list"></div></details>'
        '<details class="ms" id="f-site" data-all="All sites" data-noun="sites">'
        '<summary aria-label="Filter by site">All sites</summary>'
        '<div class="ms-list"></div></details>'
        '<select id="f-status" aria-label="Filter by status">'
        '<option value="">All statuses</option></select>'
        f'<span class="count"><span id="shown">{count}</span> shown</span>'
        "</div>"
    )


def render_index(
    records: Sequence[CaptureRecord],
    *,
    generated_at: datetime,
    delete_token: Optional[str] = None,
    archive_root: Optional[str] = None,
) -> str:
    """Render the collection to a self-contained HTML document (pure — no I/O).

    When `delete_token` is given the index is *interactive*: each capture gets a
    trashcan wired to `DELETE /capture/{id}` on the localhost backend, carrying
    the token. This is only meaningful when the backend serves the page (it alone
    can move the folder on disk), so the static file written by `write_index`
    passes no token and shows no buttons.
    """
    interactive = delete_token is not None
    count = len(records)
    noun = "source" if count == 1 else "sources"

    if records:
        captures = "".join(_capture(record, interactive) for record in records)
        main = (
            '<main class="wrap">'
            f'<table id="captures">{_thead()}{captures}</table>'
            '<aside id="detail" tabindex="-1" aria-label="Capture detail"></aside>'
            "</main>"
        )
        controls = _controls(count)
    else:
        main = '<main class="wrap"><p class="empty">No captures yet.</p></main>'
        controls = ""

    # The delete script + its token are emitted ONLY in interactive mode. The
    # token is JSON-encoded into a JS string; `<` is additionally escaped to
    # `<` so a token containing `</script>` (or `<!--`) can never break out
    # of the script element into HTML. It authorizes the same localhost backend
    # that served this page (same-origin), never a visited web page.
    # Where the records are on this computer, to look at by hand (served page only).
    archive_line = (
        f'<p class="meta">Your archive: <code>{esc(archive_root)}</code> '
        '<button type="button" class="reveal">Open archive folder</button></p>\n'
        if interactive and archive_root
        else ""
    )
    token_literal = json.dumps(delete_token).replace("<", "\\u003c")
    delete_script = (
        f"<script>window.__CAPSULE_TOKEN__={token_literal};</script>\n"
        f"<script>{INDEX_DELETE_JS}</script>\n"
        f"<script>{INDEX_CORRECT_JS}</script>\n"
        if interactive
        else ""
    )

    return (
        "<!doctype html>\n"
        '<html class="no-js" lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="generator" content="capsule captures-index/v2">\n'
        "<title>Genealogy Capsule — Captures Index</title>\n"
        # Swap the class before first paint so the enhanced layout never flashes
        # through its no-JS form. Everything else runs after the table exists.
        '<script>document.documentElement.className="js";</script>\n'
        f"<style>{INDEX_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        '<h1>Genealogy Capsule — Captures Index <a class="help-link" href="help.html">'
        "Help</a></h1>\n"
        f'<p class="meta">{count} {noun} · generated {_iso_z(generated_at)} · '
        "generated file, do not edit by hand · spec <code>captures-index/v2</code></p>\n"
        f"{archive_line}"
        f"{controls}\n"
        "</header>\n"
        f"{main}\n"
        f"<script>{INDEX_JS}</script>\n"
        f"{delete_script}"
        "</body>\n"
        "</html>\n"
    )


def _remove_legacy(root: Path) -> None:
    """Delete a `captures-index/v1` Markdown index left over from before (spec §0)."""
    legacy = root / LEGACY_INDEX_FILENAME
    try:
        legacy.unlink(missing_ok=True)
    except OSError as exc:  # a locked file must not fail an otherwise good write
        _logger.warning("could not remove superseded %s: %s", legacy, exc)


def write_index(
    captures_root: str | Path,
    *,
    now: Optional[Callable[[], datetime]] = None,
) -> Path:
    """Regenerate `captures/index.html` from the capture folders.

    Reads every capture under `captures_root`, writes the rendered document, and
    removes the superseded `Captures Index.md`. Returns the path written.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    root = Path(captures_root)
    records = read_all(root)
    document = render_index(records, generated_at=now())
    root.mkdir(parents=True, exist_ok=True)
    path = root / INDEX_FILENAME
    path.write_text(document, encoding="utf-8")
    # The guide the index links to, beside it, so the link works offline and in an export.
    (root / HELP_FILENAME).write_text(render_help(), encoding="utf-8")
    _remove_legacy(root)
    return path
