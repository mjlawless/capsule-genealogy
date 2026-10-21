"""BUG-017 + REQ-009 — the ledger after a refused duplicate, and a record's second image.

BUG-017 (live 2026-09-28): the extension writes `started` before it POSTs, and a refused
duplicate recorded nothing after it, so a captured, verified record read as STALLED.

REQ-009 (live 2026-09-28): the facing page of a two-page register opening (Ancestry
62073:223848, a second viewer image) was refused as "already mined", because an Ancestry
record id was the whole dedup identity. Owner ruling: a NULL `page_label` is the record's
default image and alone carries the record's mined state; an extra image has its own
(apid, page_label) history.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from tests.test_req_004_dedup import _ANC_SEARCH, _AUTH, _PAGE, _client

_APID = "1,6061::54221383"
_KEY = "6061:54221383"
_SECOND = "image 6061_000123_0481-00154"
_EXT = Path(__file__).resolve().parent.parent / "extension"


def _ledger(tmp_path):
    from capsule.store.ledger import AncestryLedger

    return AncestryLedger(tmp_path / "capsule.db")


def _body(**extra):
    return {"url": _ANC_SEARCH, "html": _PAGE, "gating": "gated", "expected_apid": _APID, **extra}


def _folder_id(response) -> str:
    return response.json()["capture_folder"].replace("\\", "/").rsplit("/", 1)[-1]


# -- BUG-017: a refused duplicate closes the ledger ----------------------------


def test_BUG_017_a_refused_duplicate_leaves_the_record_mined(tmp_path):
    """The live sequence: capture, then the extension's `started`, then a refused duplicate."""
    client, backend = _client(tmp_path)
    try:
        first = client.post("/capture", json=_body(), headers=_AUTH)
        assert first.status_code == 200, first.text

        started = client.post("/ancestry/started", json={"apid": _APID}, headers=_AUTH)
        assert started.status_code == 200
        ledger = _ledger(tmp_path)
        try:
            assert ledger.latest(_KEY).stalled  # the state BUG-017 left behind
        finally:
            ledger.close()

        second = client.post("/capture", json=_body(), headers=_AUTH)
        assert second.status_code == 409, second.text
        assert second.json()["dedup"] == "blocked"
    finally:
        backend.app.dependency_overrides.clear()

    ledger = _ledger(tmp_path)
    try:
        row = ledger.latest(_KEY)
        assert row.event == "dedup-blocked"
        assert row.capture_id == _folder_id(first)
        assert row.mined, "a refused duplicate must leave a mined record mined"
        assert row.status == "mined"
        assert _KEY not in [r.apid for r in ledger.unmined()], "the queue must not re-issue it"
    finally:
        ledger.close()


def test_BUG_017_dedup_blocked_copies_the_held_capture_facts_not_a_free_pass(tmp_path):
    """The restated row is judged by the same strict predicate: a held capture that was
    never mined (no assertions) stays unmined after a refusal restates it."""
    ledger = _ledger(tmp_path)
    try:
        ledger.record_capture(
            _KEY, capture_id="cap-1", image_status="full",
            assertion_count=0, persona_count=0, verify_ok=True,
        )
        ledger.record_started(_KEY)
        assert ledger.record_dedup_blocked(
            _KEY, capture_id="cap-1", fallback=lambda: {"verify_ok": True}
        )
        row = ledger.latest(_KEY)
        assert row.event == "dedup-blocked"
        assert row.assertion_count == 0
        assert not row.mined
    finally:
        ledger.close()


def test_BUG_017_facts_come_from_the_folder_when_the_ledger_never_saw_the_capture(tmp_path):
    """Review MEDIUM: an apid-less capture of an Ancestry record URL never enters the ledger,
    so a later refusal must read the held capture's facts from its own folder."""
    client, backend = _client(tmp_path)
    try:
        first = client.post(
            "/capture", json={"url": _ANC_SEARCH, "html": _PAGE, "gating": "gated"},
            headers=_AUTH,
        )
        assert first.status_code == 200, first.text
        ledger = _ledger(tmp_path)
        try:
            assert ledger.latest(_KEY) is None, "precondition: the ledger never saw it"
        finally:
            ledger.close()

        client.post("/ancestry/started", json={"apid": _APID}, headers=_AUTH)
        second = client.post("/capture", json=_body(), headers=_AUTH)
        assert second.status_code == 409, second.text
    finally:
        backend.app.dependency_overrides.clear()

    ledger = _ledger(tmp_path)
    try:
        row = ledger.latest(_KEY)
        assert row.event == "dedup-blocked"
        assert row.capture_id == _folder_id(first)
        assert row.verify_ok is True
        assert (row.assertion_count, row.persona_count) == (1, 1)  # from extraction.json
    finally:
        ledger.close()


def test_BUG_017_a_refusal_never_puts_an_unseeded_record_on_the_queue(tmp_path):
    ledger = _ledger(tmp_path)
    try:
        assert not ledger.record_dedup_blocked(_KEY, capture_id="cap-1", fallback=lambda: {})
        assert ledger.latest(_KEY) is None
        assert ledger.all_latest() == []
    finally:
        ledger.close()


def test_BUG_017_a_capture_that_dies_mid_flight_still_reads_stalled(tmp_path):
    """BUG-005 must not regress: only a REFUSAL closes a `started`."""
    ledger = _ledger(tmp_path)
    try:
        ledger.record_capture(
            _KEY, capture_id="cap-1", image_status="full",
            assertion_count=3, persona_count=1, verify_ok=True,
        )
        ledger.record_started(_KEY)
        assert ledger.latest(_KEY).stalled
    finally:
        ledger.close()


def test_BUG_017_an_older_ledger_is_rebuilt_with_every_row_kept(tmp_path):
    """A database from before this change: CHECK without `dedup-blocked`, no `page_label`."""
    db = tmp_path / "capsule.db"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE ancestry_ledger_event (
            id TEXT PRIMARY KEY, apid TEXT NOT NULL, collection_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            event TEXT NOT NULL CHECK (event IN ('seeded', 'issued', 'started', 'captured',
                                                 'failed', 'remine-requested')),
            capture_id TEXT,
            image_status TEXT CHECK (image_status IS NULL OR image_status IN
                ('full', 'degraded-canvas', 'index-only-no-image', 'failed', 'none')),
            assertion_count INTEGER, persona_count INTEGER,
            verify_ok INTEGER CHECK (verify_ok IS NULL OR verify_ok IN (0, 1)),
            follow_up TEXT, error TEXT, created_at TEXT NOT NULL
        );
        INSERT INTO ancestry_ledger_event VALUES
            ('e1', '6061:54221383', '6061', '54221383', 'captured', 'cap-1', 'full',
             3, 1, 1, NULL, NULL, '2026-09-01T00:00:00+00:00'),
            ('e2', '6061:54221383', '6061', '54221383', 'started', NULL, NULL,
             NULL, NULL, NULL, NULL, NULL, '2026-09-02T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    ledger = _ledger(tmp_path)
    try:
        count = ledger.connection.execute(
            "SELECT COUNT(*) FROM ancestry_ledger_event"
        ).fetchone()[0]
        assert count == 2
        assert ledger.latest(_KEY).stalled
        assert ledger.record_dedup_blocked(_KEY, capture_id="cap-1")
        assert ledger.latest(_KEY).mined
        # Still append-only after the rebuild.
        try:
            ledger.connection.execute("DELETE FROM ancestry_ledger_event")
        except sqlite3.DatabaseError as exc:
            assert "append-only" in str(exc)
        else:
            raise AssertionError("the rebuilt ledger accepted a DELETE")
    finally:
        ledger.close()


# -- REQ-009: a record's second image ------------------------------------------


def test_REQ_009_a_second_image_is_its_own_capture_and_the_record_stays_mined(tmp_path):
    client, backend = _client(tmp_path)
    try:
        first = client.post("/capture", json=_body(), headers=_AUTH)
        assert first.status_code == 200, first.text

        # The extension's own `started` for the second image must not stall the record.
        client.post(
            "/ancestry/started", json={"apid": _APID, "page_label": _SECOND}, headers=_AUTH
        )
        second = client.post("/capture", json=_body(page_label=_SECOND), headers=_AUTH)
        assert second.status_code == 200, second.text
        assert second.json()["dedup"] == "new"
        second_id = _folder_id(second)
        manifest = json.loads(
            (tmp_path / "captures" / second_id / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["source"]["page_label"] == _SECOND

        # Repeating the second image is refused, pointing at the SECOND capture.
        again = client.post("/capture", json=_body(page_label=_SECOND), headers=_AUTH)
        assert again.status_code == 409, again.text
        assert again.json()["duplicate_of"] == second_id

        # And the default image is still refused against the FIRST capture.
        default = client.post("/capture", json=_body(), headers=_AUTH)
        assert default.status_code == 409
        assert default.json()["duplicate_of"] == _folder_id(first)
    finally:
        backend.app.dependency_overrides.clear()

    ledger = _ledger(tmp_path)
    try:
        record = ledger.latest(_KEY)
        assert record.page_label is None
        assert record.capture_id == _folder_id(first)
        assert record.mined
        extra = ledger.images(_KEY)
        assert [(r.page_label, r.capture_id) for r in extra] == [(_SECOND, second_id)]
        assert extra[0].mined
        assert [r.apid for r in ledger.all_latest()] == [_KEY], "one record, not two"
    finally:
        ledger.close()


def test_REQ_009_capsule_query_points_at_the_other_images(tmp_path):
    from typer.testing import CliRunner

    from capsule.cli.app import app as cli

    client, backend = _client(tmp_path)
    try:
        first = client.post("/capture", json=_body(), headers=_AUTH)
        second = client.post("/capture", json=_body(page_label=_SECOND), headers=_AUTH)
        assert first.status_code == second.status_code == 200
    finally:
        backend.app.dependency_overrides.clear()

    result = CliRunner().invoke(
        cli,
        ["query", "--apid", _APID, "--db", str(tmp_path / "capsule.db"),
         "--captures", str(tmp_path / "captures")],
    )
    assert result.exit_code == 0, result.output
    answer = json.loads(result.output)
    assert answer["capture_id"] == _folder_id(first)
    assert answer["complete"] is True
    assert answer["other_images"] == [
        {"page_label": _SECOND, "capture_id": _folder_id(second), "mined": True}
    ]


# -- the extension, evaluated out of the shipped JS ------------------------------


def _eval_extra_image_label(cases):
    script = """
      const fs = require("fs");
      const src = fs.readFileSync(process.argv[1], "utf8");
      const start = src.indexOf("function extraImageLabel");
      const end = src.indexOf("async function captureAncestryImage");
      const location = { href: "https://www.ancestry.com/discoveryui-content/view/223848:62073" };
      eval(src.slice(start, end));
      const cases = JSON.parse(process.argv[2]);
      console.log(JSON.stringify(cases.map(([a, b]) => extraImageLabel(a, b) ?? null)));
    """
    proc = subprocess.run(
        ["node", "-e", script, str(_EXT / "content.js"), json.dumps(cases)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_REQ_009_the_extension_labels_only_an_image_other_than_the_records_own():
    own = "https://www.ancestry.com/imageviewer/collections/62073/images/62073_302022005552_0481-00153?pId=223848"
    facing = "https://www.ancestry.com/imageviewer/collections/62073/images/62073_302022005552_0481-00154?pId=223848"
    got = _eval_extra_image_label([[facing, own], [own, own], [None, own], [facing, None]])
    assert got[0] == "image 62073_302022005552_0481-00154"
    assert got[1] is None, "the record's own image keeps its label-less identity"
    assert got[2] is None, "no viewer URL requested: the default image"
    assert got[3] is None, "nothing to compare against: treat it as the default image"


def test_REQ_009_the_one_call_flow_forwards_label_and_remine():
    """The 409's own advice ("send remine true") must be reachable from this entry point,
    and the label must reach the ledger events, the download key and the POST."""
    content = (_EXT / "content.js").read_text(encoding="utf-8")
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    one_call = content[content.index("async function captureAncestryImage") :]
    assert "remine: detail.remine === true ? true : undefined" in one_call
    assert "extraImageLabel(detail.record_viewer_url, ownViewerUrl)" in one_call

    flow = background[background.index("async function captureAncestryImage") :]
    flow = flow[: flow.index("// Content scripts message us here.")]
    assert "key: armKey" in flow
    assert "takeStagedDownload(armKey, tabId," in flow
    assert "markStarted(msg.apid, token, pageLabel)" in flow
    assert "markFailed(msg.apid, token, reason)" not in flow, "every failure carries the label"
    assert "pageLabel,\n      remine: msg.remine," in flow


def test_BUG_017_only_a_dedup_refusal_skips_the_failure_row():
    """An identity-refusal 409 must still close the extension's own `started`."""
    background = (_EXT / "background.js").read_text(encoding="utf-8")
    assert 'if (resp.status !== 409 || body.dedup !== "blocked")' in background
