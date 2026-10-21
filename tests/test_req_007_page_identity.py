"""REQ-007 §1 — which page of the register is this?

Archion serves all 173 images of register 239254 at one URL. Three captures on disk name
that URL, carry the same title, and say nothing about which leaf they hold. A citation that
cannot name the page is not a citation.

The signal was in the DOM all along — `<select class="page-select">` with the current image
carrying `selected`. Read deterministically, never asked of a model: which leaf was on
screen is a structural fact about the document, not a reading of the record.

The two cases that matter here were found in the REAL snapshots, not in the spec:
  * the option text is the bare number ("2"), not the "Image 174" the spec described;
  * on 2026-08-19 that text sits inside two nested `<font>` tags, because the researcher had
    Google Translate rewriting the DOM. A naive `>([^<]*)<` returns empty there.

Both are pinned below against the actual files, so a refactor cannot quietly regress to
"works on the tidy example".
"""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from capsule.pipeline.page_identity import MAX_LABEL_CHARS, page_label_from

_DT = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}
_CAPTURES = Path(__file__).resolve().parent.parent / "data" / "captures"

_ARCHION_URL = "https://www.archion.de/en/viewer/churchRegister/239254"

#: The real markup, reduced to its shape. `value` is a 0-based index; the TEXT is the page.
_VIEWER_PAGE = (
    "<html><head><title>Taufregister 1798-1824 | Guntersblum</title></head><body>"
    '<select class="page-select form-select">'
    '<option selected="" value="0">2</option>'
    '<option value="1">3</option>'
    '<option value="2">4</option>'
    "</select></body></html>"
)

_RAW = {
    "record_type": "church register (viewer page)",
    "personas": [],
    "assertions": [],
    "provider_claims": [],
}


def _client(tmp_path):
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend import app as backend
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    settings = Settings(
        db_path=tmp_path / "capsule.db",
        captures_root=tmp_path / "captures",
        token=_TOKEN,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    return TestClient(app), backend, settings


def _jpeg(width: int, height: int) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


def _real_snapshots():
    """The on-disk Archion captures, if this checkout has the owner's data."""
    if not _CAPTURES.is_dir():
        return []
    return sorted(_CAPTURES.glob("*kirchenbcheronlinemitarchion*/snapshot.html"))


# -- the parser, against real markup -----------------------------------------


@pytest.mark.skipif(not _real_snapshots(), reason="no on-disk Archion captures here")
def test_REQ_007_every_real_archion_snapshot_names_its_page():
    """All three captures on disk are Image 2 — and every one of them now says so."""
    labels = {
        p.parent.name: page_label_from(p.read_text(encoding="utf-8"), _ARCHION_URL)
        for p in _real_snapshots()
    }
    assert labels, labels
    assert all(v == "2" for v in labels.values()), labels


@pytest.mark.skipif(not _real_snapshots(), reason="no on-disk Archion captures here")
def test_REQ_007_a_google_translated_snapshot_still_names_its_page():
    """The 2026-08-19 snapshot has the option text inside two nested <font> tags.

    Google Translate rewrote the DOM in the researcher's browser before the snapshot was
    taken. Anything reading `>([^<]*)<` comes back empty on that capture — so this pins the
    one file that actually exercises tag-stripping, and asserts the mangling is really there
    rather than trusting a fixture to still represent it.
    """
    translated = [
        p
        for p in _real_snapshots()
        if re.search(r"<option[^>]*selected[^>]*>\s*<font", p.read_text(encoding="utf-8"))
    ]
    assert translated, "expected at least one translated snapshot on disk"
    for path in translated:
        assert page_label_from(path.read_text(encoding="utf-8"), _ARCHION_URL) == "2"


def test_REQ_007_two_different_leaves_get_different_labels():
    """THE acceptance criterion: two captures of one register must be tellable apart."""

    def at(page: int) -> Optional[str]:
        options = "".join(
            f'<option{" selected=\"\"" if n == page else ""} value="{n - 2}">{n}</option>'
            for n in (2, 3, 4)
        )
        html = f'<select class="page-select form-select">{options}</select>'
        return page_label_from(html, _ARCHION_URL)

    assert page_label_from(_VIEWER_PAGE, _ARCHION_URL) == "2"
    assert at(2) == "2"
    assert at(3) == "3"
    assert at(4) == "4"


def test_REQ_007_the_page_selector_wins_over_an_unrelated_dropdown():
    """A viewer page has other selects. The class hook ranks first, size breaks ties."""
    html = (
        "<html><body>"
        '<select class="lang-select"><option value="de">DE</option>'
        '<option selected="" value="en">EN</option></select>'
        '<select class="page-select form-select">'
        '<option value="0">2</option><option selected="" value="1">3</option></select>'
        "</body></html>"
    )
    assert page_label_from(html, _ARCHION_URL) == "3"


def test_REQ_007_no_selected_attribute_means_the_first_option():
    """A browser shows option 1 when nothing is marked, so that IS the page in view."""
    html = (
        '<select class="page-select"><option value="0">7</option><option>8</option></select>'
    )
    assert page_label_from(html, _ARCHION_URL) == "7"


def test_REQ_007_a_single_option_select_identifies_nothing():
    html = '<select class="page-select"><option selected="">only</option></select>'
    assert page_label_from(html, _ARCHION_URL) is None


def test_REQ_007_an_absurd_label_is_refused():
    """Matching the wrong control must yield None, not a paragraph in `page_label`."""
    long = "x" * (MAX_LABEL_CHARS + 1)
    html = (
        f'<select class="page-select"><option selected="">{long}</option>'
        "<option>b</option></select>"
    )
    assert page_label_from(html, _ARCHION_URL) is None


def test_REQ_007_a_page_label_is_never_guessed_off_a_viewer_host():
    """Most of the corpus has selects and none of them are page selectors.

    None must never be confused with "page 1" — so an ordinary site with a perfectly good
    dropdown gets no label at all.
    """
    html = '<select><option selected="">Kilmore</option><option>Cavan</option></select>'
    assert page_label_from(html, "https://www.rootsireland.ie/search") is None
    assert (
        page_label_from(html, "https://www.ancestry.com/search/collections/1/records/2")
        is None
    )
    assert page_label_from(None, _ARCHION_URL) is None
    assert page_label_from(_VIEWER_PAGE, None) is None


# -- through the pipeline -----------------------------------------------------


def test_REQ_007_the_page_label_reaches_the_manifest_and_the_response(tmp_path):
    """R4.3: the folder must be able to name its own leaf without the database."""
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _VIEWER_PAGE,
                "gating": "gated",
                "media": [
                    {
                        "filename": "export.jpg",
                        "mime": "image/jpeg",
                        "provenance": "site:official-download",
                        "data_base64": _jpeg(2644, 2164),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["page_label"] == "2"
        folder = settings.captures_root / resp.json()["capture_folder"]
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["source"]["page_label"] == "2"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_source_without_a_page_selector_omits_the_key_entirely(tmp_path):
    """Additive: every existing capture serializes byte-for-byte as it did before.

    The key is omitted rather than written as null, so no manifest on disk changes shape
    and no `content_hash` moves.
    """
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": "https://www.rootsireland.ie/view_detail.php?recordid=277156",
                "html": "<html><body><p>A text record.</p></body></html>",
                "gating": "gated",
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["page_label"] is None
        folder = settings.captures_root / resp.json()["capture_folder"]
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert "page_label" not in manifest["source"]
    finally:
        backend.app.dependency_overrides.clear()


# -- the trap the live page exposed ------------------------------------------


def test_REQ_007_the_live_reading_beats_the_snapshot(tmp_path):
    """The browser is the authority on which leaf was on screen. Verified live.

    Serializing a page writes the `selected` ATTRIBUTE, and that attribute never moves off
    the option the viewer first loaded: navigating changes the live `selectedIndex` and
    nothing else. Measured against Archion on 2026-08-22 — the screen showed image 6 while
    `document.documentElement.outerHTML` still marked image 2.

    So a backend parsing the archived HTML would confidently name the WRONG leaf, which is
    strictly worse than naming none: it puts a false page into a citation that looks
    complete. The content script therefore reads the live DOM and sends it, and that value
    wins. This test uses exactly the divergence observed: snapshot says 2, screen said 6.
    """
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _VIEWER_PAGE,          # its `selected` attribute still says "2"
                "page_label": "6",             # what the researcher was actually looking at
                "gating": "gated",
            },
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["page_label"] == "6"
        folder = settings.captures_root / resp.json()["capture_folder"]
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["source"]["page_label"] == "6"
        # And the parser alone, on that same HTML, would still have said "2" — which is
        # precisely why the override exists rather than being a nicety.
        assert page_label_from(_VIEWER_PAGE, _ARCHION_URL) == "2"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_the_parser_still_covers_a_caller_that_sends_nothing(tmp_path):
    """The CLI fetches HTML that was never navigated, so its attribute IS the truth."""
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={"url": _ARCHION_URL, "html": _VIEWER_PAGE, "gating": "gated"},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["page_label"] == "2"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_an_overlong_page_label_is_refused_at_the_door(tmp_path):
    """A caller cannot stuff a paragraph into the manifest through this field."""
    client, backend, _settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _VIEWER_PAGE,
                "page_label": "x" * 200,
                "gating": "gated",
            },
            headers=_AUTH,
        )
        assert resp.status_code == 422, resp.text
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_the_extension_reads_the_label_from_the_live_dom():
    """Pinned in source: the content script must not read it off serialized HTML."""
    content = (
        Path(__file__).resolve().parent.parent / "extension" / "content.js"
    ).read_text(encoding="utf-8")
    assert "function livePageLabel()" in content
    assert "sel.options[sel.selectedIndex]" in content
    # REQ-009: still the live DOM, but never on an Ancestry record, where a label is part of
    # the record's dedup identity and only the one-call flow may set one.
    assert (
        "options.apid || apidFromRecordUrl(location.href) ? undefined : livePageLabel()"
        in content
    )


def test_REQ_007_the_outcome_the_agent_reads_carries_the_leaf_and_the_shortfall():
    """`postAndShape` builds a CURATED outcome, so a response field is not automatically
    visible to anyone.

    Found live on 2026-08-22: the manifest said `page_label: "6"` and the API returned it,
    while the outcome mirror the driving agent reads said `undefined` — because the shaper
    never forwarded it. The same gap silently disabled `verify_shortfall`, whose whole
    purpose is to explain a `verify_ok: false` that the operator would otherwise see
    contradicted by `IMAGE full`.

    Asserting the two fields are FORWARDED, not merely mentioned somewhere: the earlier
    version of this check looked for the string in content.js and passed while the value
    never arrived.
    """
    background = (
        Path(__file__).resolve().parent.parent / "extension" / "background.js"
    ).read_text(encoding="utf-8")
    assert "page_label: body.page_label" in background
    assert "verify_shortfall: Array.isArray(body.verify_shortfall)" in background


# -- identity: a register is not one record ----------------------------------


def test_REQ_007_two_leaves_of_one_register_are_different_records(tmp_path):
    """Observed LIVE 2026-08-22: capturing image 6 was refused as "already mined".

    Dedup keys a non-Ancestry capture on its URL, and a tile viewer serves all 173 images
    of register 239254 at ONE address. So the collection could hold exactly one leaf of any
    register and refused every other — for the Guntersblum registers this REQ exists to
    reach, that is fatal, and it is the module docstring failure verbatim: a false block
    LOSES EVIDENCE, while a false "new" merely re-captures.
    """
    client, backend, settings = _client(tmp_path)
    try:
        first = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _VIEWER_PAGE,
                "page_label": "2",
                "gating": "gated",
                "media": [
                    {
                        "filename": "e.jpg",
                        "mime": "image/jpeg",
                        "provenance": "site:official-download",
                        "data_base64": _jpeg(2644, 2164),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert first.status_code == 200, first.text
        assert first.json()["dedup"] == "new"

        # The SAME leaf again is still refused — the guard has not simply been switched off.
        same = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _VIEWER_PAGE,
                "page_label": "2",
                "gating": "gated",
                "media": [
                    {
                        "filename": "e.jpg",
                        "mime": "image/jpeg",
                        "provenance": "site:official-download",
                        "data_base64": _jpeg(2644, 2164),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert same.status_code == 409, same.text

        # A DIFFERENT leaf of the same register is a different record, and lands.
        other = client.post(
            "/capture",
            json={
                "url": _ARCHION_URL,
                "html": _VIEWER_PAGE,
                "page_label": "6",
                "gating": "gated",
                "media": [
                    {
                        "filename": "e.jpg",
                        "mime": "image/jpeg",
                        "provenance": "site:official-download",
                        "data_base64": _jpeg(2644, 2164),
                    }
                ],
            },
            headers=_AUTH,
        )
        assert other.status_code == 200, other.text
        assert other.json()["dedup"] == "new"
        assert other.json()["page_label"] == "6"
        assert other.json()["capture_folder"] != first.json()["capture_folder"]
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_an_ordinary_url_keyed_source_is_unchanged(tmp_path):
    """RootsIreland has no leaves. Its identity must behave exactly as it did before."""
    client, backend, _settings = _client(tmp_path)
    url = "https://www.rootsireland.ie/view_detail.php?recordid=277156"
    body = {"url": url, "html": "<html><body>rec</body></html>", "gating": "gated"}
    try:
        first = client.post("/capture", json=body, headers=_AUTH)
        assert first.status_code == 200, first.text
        second = client.post("/capture", json=body, headers=_AUTH)
        # Still deduped on the URL alone: no page_label on either side.
        assert second.status_code in (200, 409), second.text
        assert second.json().get("dedup") in ("blocked", "repaired")
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_the_identity_key_carries_the_leaf():
    """The in-flight lock key must separate leaves too, or two cannot capture at once."""
    from capsule.pipeline.dedup import record_identity

    a = record_identity(_ARCHION_URL, None, "2")
    b = record_identity(_ARCHION_URL, None, "6")
    bare = record_identity(_ARCHION_URL, None)
    assert a != b and a != bare and b != bare
    # REQ-009 (2026-09-28) reversed "an Ancestry record has no leaves": a record can span two
    # images, and the second was refused as "already mined". No label is still the record's
    # default image, so every label-less key is unchanged.
    ancestry = "https://www.ancestry.com/search/collections/7602/records/56510272"
    assert record_identity(ancestry, None, "image 00154") != record_identity(ancestry, None)
    assert record_identity(ancestry, None) == "ancestry:7602:56510272"


# -- version handshake: an older backend must degrade, not lose the capture --


#: The REAL FastAPI/pydantic 422 body, captured from the running backend on 2026-08-23 by
#: POSTing an unknown key. Written down verbatim rather than guessed, because the parser
#: only has to understand one shape and it is this one.
_EXTRA_FORBIDDEN_422 = {
    "detail": [
        {
            "type": "extra_forbidden",
            "loc": ["body", "page_label"],
            "msg": "Extra inputs are not permitted",
            "input": "6",
        }
    ]
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_REQ_007_an_older_backend_degrades_instead_of_losing_the_capture():
    """A new wire field must never take the whole capture down with it.

    The request model is `extra="forbid"`, so ONE unknown key 422s everything — snapshot,
    media and all. Observed 2026-08-22: an auto-updated extension met a backend one commit
    behind and every viewer-page capture was lost to
    `body.page_label: Extra inputs are not permitted`. A distributed extension does not
    upgrade in lockstep with a local backend, so the answer is to drop what the backend
    NAMED and send again.

    Evaluated out of the shipped background.js rather than restated here: a Python
    restatement would happily agree with itself while the JS that actually runs said
    something else.
    """
    script = """
      const fs = require("fs");
      const src = fs.readFileSync(process.argv[1], "utf8");
      const start = src.indexOf("const DROPPABLE_WIRE_FIELDS");
      const end = src.indexOf("async function postAndShape");
      if (start < 0 || end < 0 || end <= start) { console.log(JSON.stringify({error: "markers"})); process.exit(0); }
      eval(src.slice(start, end));
      const cases = JSON.parse(process.argv[2]);
      console.log(JSON.stringify(cases.map(c => unknownFieldsIn(c))));
    """
    background = str(Path(__file__).resolve().parent.parent / "extension" / "background.js")
    other_field = {
        "detail": [dict(_EXTRA_FORBIDDEN_422["detail"][0], loc=["body", "zzz_unknown"])]
    }
    missing_required = {
        "detail": [{"type": "missing", "loc": ["body", "url"], "msg": "Field required"}]
    }
    cases = [_EXTRA_FORBIDDEN_422, other_field, missing_required, {"detail": "a string"}, {}]
    proc = subprocess.run(
        ["node", "-e", script, background, json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip())
    assert got[0] == ["page_label"], "must drop the field the backend named"
    # Everything else must be left ALONE. Dropping a field the backend did not name, or
    # reacting to an error that is not about an unknown key, would silently mangle requests.
    assert got[1] == [], "a field we are not allowed to drop is not dropped"
    assert got[2] == [], "a MISSING required field is not an unknown-field error"
    assert got[3] == [] and got[4] == [], "malformed / empty detail is not actionable"


def test_REQ_007_only_enhancements_are_ever_droppable():
    """Nothing carrying bytes, provenance or identity may be dropped to please a backend."""
    background = (
        Path(__file__).resolve().parent.parent / "extension" / "background.js"
    ).read_text(encoding="utf-8")
    block = background[background.index("const DROPPABLE_WIRE_FIELDS") :][:200]
    for forbidden in ("media", "html", "url", "gating", "expected_apid", "missed_media"):
        assert f'"{forbidden}"' not in block, f"{forbidden} must never be droppable"
    # The retry happens ONCE. A loop could strip a request to nothing while reporting
    # success, so pin the single call site (the `function` declaration also contains the
    # name, which is why this counts the invocation rather than the identifier).
    assert background.count("= unknownFieldsIn(body)") == 1
    assert "wire_downgraded: downgraded" in background


def test_REQ_007_the_operator_is_told_the_capture_was_downgraded():
    """A capture missing an enhancement must not look like one that never needed it."""
    content = (
        Path(__file__).resolve().parent.parent / "extension" / "content.js"
    ).read_text(encoding="utf-8")
    assert "res.wire_downgraded" in content
    assert "downgradeText" in content
    assert "older than this extension" in content


# -- §4: the citation names the archive, not just the website ----------------


def _cite(hierarchy, page_label="6", site="Kirchenbuecher online mit ARCHION"):
    from datetime import date
    from types import SimpleNamespace

    from capsule.pipeline.citation import build_citation

    snap = SimpleNamespace(url=_ARCHION_URL, title="a long page title", site_name=site)
    return build_citation(
        citation_id="c", source_id="s", snapshot=snap, access_date=date(2026, 8, 23),
        archived_url=_ARCHION_URL, hierarchy=hierarchy, page_label=page_label,
    )


@pytest.mark.skipif(not _real_snapshots(), reason="no on-disk Archion captures here")
def test_REQ_007_the_breadcrumb_decomposes_into_a_citation():
    """Every part a citation needs was on the page all along, and none was used."""
    from capsule.pipeline.page_identity import archive_hierarchy_from

    html = _real_snapshots()[-1].read_text(encoding="utf-8")
    h = archive_hierarchy_from(html, _ARCHION_URL)
    assert h is not None
    assert h.custodian == "Zentralarchiv der Evangelischen Kirche in Hessen und Nassau"
    assert h.district == "Dekanat Oppenheim"
    assert h.parish == "Guntersblum"
    assert h.volume.startswith("Taufregister 1798-1824")
    # Navigation is not archival structure.
    assert "All Archives" not in h.trail and "Back" not in h.trail


def test_REQ_007_the_citation_names_who_holds_the_original():
    """THE point of §4: Archion is the access path, the archive is the custodian.

    `generic-web-v1` cited these as a web page — the raw title, the site name twice, and no
    page at all. A citation that stops at the website tells a future researcher nothing
    about where to go when the website is gone, and REQ-001 constraint 4 says to expect
    exactly that.
    """
    from capsule.pipeline.page_identity import ArchiveHierarchy

    h = ArchiveHierarchy(trail=("Hessen", "Zentralarchiv X", "Dekanat Oppenheim",
                                "Guntersblum", "Taufregister 1798-1824"))
    c = _cite(h)
    assert c.template_id == "register-image-v1"
    assert c.reference_note.startswith("Guntersblum (Dekanat Oppenheim),")
    assert "citing Zentralarchiv X." in c.reference_note
    assert "Image 6;" in c.reference_note
    assert "accessed 23 August 2026" in c.reference_note
    # Evidence Explained puts the page straight after the quoted title, with no second
    # comma outside the quotes.
    assert not_in_double_comma(c.reference_note)
    assert "Citing Zentralarchiv X." in c.source_list_entry


def not_in_double_comma(text):
    return chr(34) + "," + chr(34) not in text and (chr(44) + chr(34) + chr(44)) not in text


def test_REQ_007_a_trail_too_short_names_no_custodian():
    """A citation that names the WRONG custodian is worse than one that names none."""
    from capsule.pipeline.page_identity import ArchiveHierarchy

    short = ArchiveHierarchy(trail=("Guntersblum", "Taufregister 1798-1824"))
    assert short.volume == "Taufregister 1798-1824"
    assert short.parish == "Guntersblum"
    assert short.district is None and short.custodian is None
    note = _cite(short).reference_note
    assert "citing" not in note
    assert note.startswith("Guntersblum,")


def test_REQ_007_the_named_parts_are_read_from_the_end():
    """An archive that adds a level ABOVE must not shift what everything means."""
    from capsule.pipeline.page_identity import ArchiveHierarchy

    deeper = ArchiveHierarchy(trail=("Deutschland", "Hessen", "Zentralarchiv X",
                                     "Dekanat Oppenheim", "Guntersblum", "Volume"))
    assert deeper.volume == "Volume"
    assert deeper.parish == "Guntersblum"
    assert deeper.district == "Dekanat Oppenheim"
    assert deeper.custodian == "Zentralarchiv X"


def test_REQ_007_a_breadcrumb_is_only_read_on_a_recognised_viewer():
    """Home > Search > Results is navigation, and must never become provenance."""
    from capsule.pipeline.page_identity import archive_hierarchy_from

    html = ('<div class="breadcrumb"><a href="/">Home</a><a href="/s">Search</a>'
            '<a href="/r">Results</a></div>')
    assert archive_hierarchy_from(html, "https://www.rootsireland.ie/search") is None
    assert archive_hierarchy_from(html, _ARCHION_URL) is not None  # host-scoped, not content


def test_REQ_007_the_anchor_match_does_not_swallow_other_tags():
    """`<a` must not match `<abbr>` or `<address>` — the boundary is load-bearing."""
    from capsule.pipeline.page_identity import archive_hierarchy_from

    html = ('<div class="breadcrumb"><abbr title="x">NOPE</abbr>'
            '<address>ALSO NOPE</address><a href="/v">Guntersblum</a>'
            '<a href="/w">Taufregister</a></div>')
    h = archive_hierarchy_from(html, _ARCHION_URL)
    assert h.trail == ("Guntersblum", "Taufregister"), h.trail


def test_REQ_007_a_page_with_no_breadcrumb_still_cites_generically(tmp_path):
    """Nothing else changes: an ordinary page keeps `generic-web-v1`."""
    client, backend, settings = _client(tmp_path)
    try:
        resp = client.post(
            "/capture",
            json={"url": "https://www.rootsireland.ie/x", "html": "<html><body>r</body></html>",
                  "gating": "gated"},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        folder = settings.captures_root / resp.json()["capture_folder"]
        cit = json.loads((folder / "citation.json").read_text(encoding="utf-8"))
        assert cit["template_id"] == "generic-web-v1"
    finally:
        backend.app.dependency_overrides.clear()


def test_REQ_007_a_viewer_capture_gets_the_register_citation_end_to_end(tmp_path):
    """Through the pipeline: the folder carries a citation naming the archive."""
    client, backend, settings = _client(tmp_path)
    html = ('<html><head><title>t</title></head><body>'
            '<div class="dvbreadcrumb"><a href="/">All Archives</a><a href="/h">Hessen</a>'
            '<a href="/z">Zentralarchiv X</a><a href="/d">Dekanat Oppenheim</a>'
            '<a href="/g">Guntersblum</a><a href="/v">Taufregister 1798-1824</a>'
            '<a href="/b">Back</a></div>'
            '<select class="page-select"><option selected="" value="0">6</option>'
            '<option value="1">7</option></select></body></html>')
    try:
        resp = client.post(
            "/capture",
            json={"url": _ARCHION_URL, "html": html, "page_label": "6", "gating": "gated"},
            headers=_AUTH,
        )
        assert resp.status_code == 200, resp.text
        folder = settings.captures_root / resp.json()["capture_folder"]
        cit = json.loads((folder / "citation.json").read_text(encoding="utf-8"))
        assert cit["template_id"] == "register-image-v1"
        assert "citing Zentralarchiv X." in cit["reference_note"]
        assert "Guntersblum (Dekanat Oppenheim)" in cit["reference_note"]
        assert "Image 6;" in cit["reference_note"]
    finally:
        backend.app.dependency_overrides.clear()
