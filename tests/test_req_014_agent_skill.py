"""REQ-014 — the public agent skill (`skills/genealogy-capsule/SKILL.md`).

The skill is an operating manual a browser agent follows literally, so a name that drifts from
the extension is a capture that silently never fires. These check every event, `detail` field,
page attribute, element id and quoted message the skill names against the shipped code, and
that the skill ships in the public export with nothing personal in it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SKILL_DIR = _ROOT / "skills" / "genealogy-capsule"
_SKILL = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
_CONTENT = (_ROOT / "extension" / "content.js").read_text(encoding="utf-8")
_BACKGROUND = (_ROOT / "extension" / "background.js").read_text(encoding="utf-8")
_INTERCEPT = (_ROOT / "extension" / "download-intercept.js").read_text(encoding="utf-8")
_POPUP_HTML = (_ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
_BACKEND = (_ROOT / "capsule" / "backend" / "app.py").read_text(encoding="utf-8")


def _frontmatter() -> dict[str, str]:
    m = re.match(r"---\n(.*?)\n---\n", _SKILL, re.S)
    assert m, "SKILL.md must open with YAML frontmatter"
    fields = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_frontmatter_names_the_skill_and_says_when_to_use_it():
    fm = _frontmatter()
    assert fm["name"] == _SKILL_DIR.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fm["name"])
    assert 0 < len(fm["description"]) <= 1024
    assert "Use when" in fm["description"]


def test_every_event_the_skill_names_is_listened_for():
    events = set(re.findall(r'"(capsule:[a-z-]+)"', _SKILL))
    assert {"capsule:capture", "capsule:capture-ancestry-image", "capsule:arm-download"} <= events
    for event in events:
        listened = f'document.addEventListener("{event}"' in _CONTENT or (
            f'const EVENT = "{event}"' in _CONTENT and "document.addEventListener(EVENT" in _CONTENT
        )
        assert listened, f"content.js has no listener for {event}"


def test_every_detail_field_the_skill_names_is_read():
    fields = {"note", "apid", "image_intent", "record_viewer_url", "page_label", "remine"}
    for field in fields:
        assert field in _SKILL, f"the skill no longer mentions {field}; update this test"
        assert re.search(rf"\b(detail|options)\.{field}\b", _CONTENT), f"content.js never reads {field}"


def test_page_attributes_and_element_ids_exist():
    for attr in set(re.findall(r"data-capsule-[a-z-]+", _SKILL)):
        assert f'"{attr}"' in _CONTENT, f"content.js never sets {attr}"
    for element_id in set(re.findall(r'"(capsule-[a-z-]+)"', _SKILL)) | {"capsule-toast"}:
        assert f'"{element_id}"' in _CONTENT, f"content.js has no element {element_id}"


def _shaped_outcome_keys() -> set[str]:
    """The keys of the objects `postAndShape` returns: the capture result the page receives."""
    start = _BACKGROUND.index("async function postAndShape(")
    body = _BACKGROUND[start : _BACKGROUND.index("\n}\n", start)]
    keys: set[str] = set()
    for literal in re.findall(r"return \{\n(.*?)\n\s*\};", body, re.S):
        keys |= set(re.findall(r"^\s+([a-z_]+)(?::|,)", literal, re.M))
    return keys


@pytest.mark.parametrize(
    ("fn", "refusals"),
    [("async function capture(note", 0), ("async function captureAncestryImage(", 2)],
)
def test_capturing_is_flagged_before_the_first_await(fn, refusals):
    # The skill's capture wrapper reads the page right after dispatchEvent: still capturing,
    # or a fresh verdict, or neither (= the event was not heard). That holds only if the flag
    # is set synchronously, and every refusal before the first await writes an outcome.
    start = _CONTENT.index(fn)
    body = _CONTENT[start : _CONTENT.index("\n  }\n", start)]
    first_await = body.index("await ")
    assert body.index("setCapturing(true)") < first_await
    lines = body[:first_await].splitlines()
    early = [i for i, line in enumerate(lines) if line.strip() == "return;"]
    assert len(early) == refusals, "a synchronous refusal was added or removed; recheck the skill"
    for i in early:
        assert "setOutcome(" in lines[i - 1], "a synchronous refusal returns without an outcome"
    assert 'busy: de.getAttribute("data-capsule-capturing") === "true"' in _SKILL
    assert "outcome: outcome && Date.parse(outcome.at) >= started ? outcome : null" in _SKILL
    assert "Date.parse(outcome.at) >= (window.__capsuleStarted || 0)" in _SKILL


def test_every_outcome_field_the_skill_lists_reaches_the_page():
    # The completeness rule treats missed_media / verify_shortfall / media_warnings being
    # absent as clean, so each must be a key of the shaped result itself, not merely a word
    # somewhere in the code.
    listed = _SKILL[_SKILL.index("The outcome carries:") : _SKILL.index("A new capture's id")]
    fields = set(re.findall(r"`([a-z_]+)`", listed))
    assert {"ok", "verify_ok", "image_status", "missed_media", "verify_shortfall", "dedup"} <= fields
    shaped = _shaped_outcome_keys()
    assert {"ok", "error", "missed_media", "verify_shortfall", "media_warnings"} <= shaped
    # setOutcome mirrors the whole result onto <html>, adding `at` and a query-stripped `url`.
    assert "...out, url: stripQuery(out.url), at:" in _CONTENT
    missing = fields - shaped - {"at"}
    assert not missing, f"postAndShape no longer returns {missing}"


def test_quoted_messages_match_the_code():
    assert "Armed. Click the site's own Download now." in _CONTENT
    assert '"Capsule could not arm: "' in _CONTENT
    assert '"IDENTITY MISMATCH: expected "' in _BACKGROUND
    assert '"cannot verify identity: ' in _BACKGROUND
    assert "already mined: " in _BACKEND
    assert '"blocked"' in _BACKEND and "duplicate_of" in _BACKEND
    # The setting the skill tells the human to turn on, word for word, and the arm refusal
    # (DOWNLOADS_OFF) that sends them to it.
    label = "Capsule button on every page, and keep site downloads"
    assert label in _SKILL and label in _POPUP_HTML and label in _INTERCEPT


def test_request_and_pdf_literals_match_the_code():
    # The image_intent values the skill sends are the ones the backend accepts...
    sent = set(re.findall(r'image_intent: "([a-z]+)"', _SKILL))
    assert sent == {"expected", "none"}
    accepted = re.search(r'image_intent: Optional\[Literal\[([^\]]+)\]\]', _BACKEND)
    assert accepted, "the backend's image_intent schema moved"
    assert sent <= set(re.findall(r'"([a-z]+)"', accepted.group(1)))
    # ...and the PDF status the skill reads is the one postAndShape sends.
    assert '`pdf` is not `"attached"`' in _SKILL
    assert 'pdf: render.media ? "attached" : "skipped"' in _BACKGROUND
    # A second image's recovery check names other_images, which `capsule query` prints.
    assert "other_images" in _SKILL
    assert "other_images" in (_ROOT / "capsule" / "cli" / "app.py").read_text(encoding="utf-8")


def test_image_statuses_match_what_the_backend_sends():
    # The completeness rule is literal string comparison, so a renamed status must fail here.
    from capsule.pipeline.image_check import ImageStatus

    named = set(re.findall(r'`?"([a-z-]+)"`?', _SKILL)) & {
        "full", "index-only-no-image", "degraded-canvas", "failed", "none", "degraded",
    }
    assert {"full", "index-only-no-image", "degraded-canvas", "failed", "none"} <= named
    produced = {s.value for s in ImageStatus} | {"index-only-no-image", "none"}
    assert named <= produced, f"the skill names statuses the code never sends: {named - produced}"
    assert '"index-only-no-image"' in _BACKEND and 'image_status === "none"' in _CONTENT


def _cli_options(command: str) -> dict[str, object]:
    """The CLI's own parameters for a command, read from click, not from formatted --help
    (rich colours split `--provenance` into separately styled runs on CI)."""
    import typer

    from capsule.cli.app import app

    group = typer.main.get_command(app)
    params = group.commands[command].params
    return {opt: p for p in params for opt in getattr(p, "opts", [])}


def test_cli_commands_the_skill_gives_exist():
    ingest = _cli_options("ingest")
    assert "--provenance" in ingest and "--url" in ingest
    choices = getattr(ingest["--provenance"].type, "choices", None)
    assert choices is not None and "site:official-download" in [str(c) for c in choices]
    query = _cli_options("query")
    assert "--url" in query and "--apid" in query
    assert '"complete": ' in (_ROOT / "capsule" / "cli" / "app.py").read_text(encoding="utf-8")
    assert "--file" in _cli_options("ancestry")
    from capsule.cli.app import ancestry

    for action in ("seed", "status", "followups"):
        assert action in (ancestry.__doc__ or "")
    assert "capsule ancestry seed --file" in _SKILL and "`complete`" in _SKILL
    for given in ("--provenance site:official-download", "capsule query --url", "--apid <_APID>"):
        assert given in _SKILL


# The export's own rules live in release/, which the public tree does not carry: there these
# checks have nothing to check (the export already applied them before publishing).
_RELEASE = _ROOT / "release"
_IN_PRIVATE_REPO = (_RELEASE / "exclude.txt").is_file() and (_RELEASE / "forbidden.txt").is_file()
_PRIVATE_ONLY = pytest.mark.skipif(not _IN_PRIVATE_REPO, reason="export rules exist only privately")


@_PRIVATE_ONLY
def test_the_skill_ships_in_the_public_export():
    excluded = [
        line.strip()
        for line in (_RELEASE / "exclude.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    path = "skills/genealogy-capsule/SKILL.md"
    assert not any(path == e or path.startswith(e.rstrip("/") + "/") for e in excluded)


@_PRIVATE_ONLY
@pytest.mark.parametrize(
    "pattern",
    [
        line
        for line in (_RELEASE / "forbidden.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    if _IN_PRIVATE_REPO
    else ["(no export rules in this tree)"],
)
def test_no_personal_identifiers(pattern):
    assert not re.search(pattern, _SKILL, re.I)


def test_no_real_record_ids():
    # Examples use placeholders, never a record from someone's own research.
    assert not re.search(r"\d+,\d+::\d+", _SKILL)
    assert not re.search(r"/records/\d+", _SKILL)
