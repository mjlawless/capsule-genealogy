"""C6 slice 2: correcting from the served index, and where correction fixtures go.

The buttons are rendered only on the served page; the static file never has them.
A correction's eval fixture is written beside the archive's database, never into
the repo, because it holds the record's values (owner ruling, 2026-10-23).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_TOKEN = "test-token-abc123"
_AUTH = {"X-Capsule-Token": _TOKEN}
_PAGE = (
    "<html><head><title>Mary Kelly (1810-1881)</title></head>"
    "<body><h1>Mary Kelly</h1><p>Died 3 March 1881.</p>"
    "<p>Buried at Glasnevin Cemetery, Dublin.</p></body></html>"
)
_RAW = {
    "record_type": "burial",
    "personas": [{"label": "Mary Kelly", "role": "deceased"}],
    "assertions": [
        {"persona": "Mary Kelly", "field": "death_date", "value": "3 March 1881",
         "evidence_type": "stated", "snippet": "Died 3 March 1881."},
        {"persona": "Mary Kelly", "field": "burial_place", "value": "Glasnevin Cemetery, Dublin",
         "evidence_type": "stated", "snippet": "Buried at Glasnevin Cemetery, Dublin."},
    ],
}


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A backend with ONE capture, the default fixture location, and a cwd that is not
    the archive (as for the Mac server, which runs in the repo)."""
    from fastapi.testclient import TestClient

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.backend.app import Settings, app, get_settings, provide_agent

    monkeypatch.delenv("CAPSULE_FIXTURES_DIR", raising=False)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    archive = tmp_path / "archive"
    settings = Settings(db_path=archive / "capsule.db", captures_root=archive / "captures",
                        token=_TOKEN)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[provide_agent] = lambda: Agent(
        name="extractor", model="claude-opus-5-5", transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT, new_id=lambda: "call-1",
    )
    client = TestClient(app)
    resp = client.post("/capture", json={"url": "https://www.findagrave.com/memorial/1",
                                         "html": _PAGE, "gating": "gated"}, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    try:
        yield client, settings, Path(resp.json()["capture_folder"]).name, cwd
    finally:
        app.dependency_overrides.clear()


def _row(page: str, field: str) -> str:
    # re.S: a correction tag's tooltip holds one line per correction.
    rows = re.findall(r"<tr[^>]*><td>[^<]*</td><td>" + re.escape(field) + r"</td>.*?</tr>", page,
                      re.S)
    assert len(rows) == 1, rows
    return rows[0]


def _id(row: str) -> str:
    m = re.search(r'data-assertion-id="([0-9a-f]+)"', row)
    assert m, row
    return m.group(1)


def test_a_correction_fixture_is_written_beside_the_archive_not_in_the_repo(served):
    client, settings, cid, cwd = served
    death = _id(_row(client.get("/").text, "death_date"))
    resp = client.post("/correction", headers=_AUTH, json={
        "capture_id": cid, "action": "edit", "assertion_id": death, "new_value": "13 March 1881"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["fixture"] == cid
    expected = settings.db_path.parent / "eval-fixtures" / cid / "expected.json"
    assert "13 March 1881" in expected.read_text(encoding="utf-8")
    assert not (cwd / "evals").exists()  # nothing lands in the working directory (the repo)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_correction_fixtures_are_gitignored_wherever_the_archive_is():
    """Even with CAPSULE_DATA_DIR=. (the archive IS the checkout), git ignores them."""
    repo = Path(__file__).resolve().parents[1]
    if not (repo / ".git").exists():
        pytest.skip("not a git checkout")
    paths = [f"{base}eval-fixtures/20261023-site-abc/{name}"
             for base in ("", "data/", "some/archive/")
             for name in ("expected.json", "meta.json", "snapshot.html")]
    proc = subprocess.run(["git", "check-ignore", "--no-index", *paths], cwd=repo,
                          capture_output=True, text=True, encoding="utf-8")
    assert sorted(proc.stdout.split()) == sorted(paths), proc.stdout + proc.stderr


def test_capsule_fixtures_dir_still_overrides(monkeypatch, tmp_path):
    from capsule.config import correction_fixtures_root

    monkeypatch.setenv("CAPSULE_FIXTURES_DIR", str(tmp_path / "elsewhere"))
    assert correction_fixtures_root(tmp_path / "capsule.db") == tmp_path / "elsewhere"
    monkeypatch.delenv("CAPSULE_FIXTURES_DIR")
    assert correction_fixtures_root(tmp_path / "capsule.db") == tmp_path / "eval-fixtures"


def test_capsule_eval_reads_the_gold_and_the_correction_fixtures(monkeypatch, tmp_path):
    """Without --fixtures, eval looks in the repo's gold AND beside the archive; with
    neither present it says where it looked (and exits before spending any tokens)."""
    from typer.testing import CliRunner

    from capsule.cli.app import _fixture_dirs, app

    monkeypatch.delenv("CAPSULE_FIXTURES_DIR", raising=False)
    monkeypatch.setenv("CAPSULE_DATA_DIR", str(tmp_path / "archive"))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["eval"])
    assert result.exit_code == 1
    out = " ".join((result.output or "").split())
    assert "evals/fixtures" in out.replace("\\", "/") and "eval-fixtures" in out

    (tmp_path / "evals" / "fixtures" / "gold-one").mkdir(parents=True)
    (tmp_path / "archive" / "eval-fixtures" / "20261023-site-abc").mkdir(parents=True)
    roots = [Path("evals/fixtures"), tmp_path / "archive" / "eval-fixtures"]
    assert [d.name for d in _fixture_dirs(roots, None)] == ["gold-one", "20261023-site-abc"]
    assert [d.name for d in _fixture_dirs(roots, "20261023-site-abc")] == ["20261023-site-abc"]


def test_the_served_page_offers_edit_retract_and_add(served):
    client, _settings, cid, _cwd = served
    page = client.get("/").text
    row = _row(page, "death_date")
    assert 'class="fix fix-edit"' in row and 'class="fix fix-retract"' in row
    assert 'data-snippet="Died 3 March 1881."' in row
    assert '<span class="v">3 March 1881</span>' in row
    assert f'<table class="assertions" data-capture-id="{cid}">' in page
    add = re.search(r'<button type="button" class="fix-add" data-capture-id="([^"]+)" '
                    r'data-personas="([^"]+)">', page)
    assert add and add.group(1) == cid
    people = json.loads(add.group(2).replace("&quot;", '"'))
    assert [p["label"] for p in people] == ["Mary Kelly (deceased)"] and len(people[0]["id"]) == 32
    assert "INDEX_CORRECT_JS" not in page and 'fetch("/correction"' in page


def test_same_name_people_are_told_apart_in_the_add_form():
    """57 of 420 real captures name two people alike (15 with the same role too)."""
    from capsule.collection.index import _add_fact_button
    from capsule.collection.read import CaptureRecord, PersonaView

    people = (PersonaView("John Murphy", "father", id="a" * 32),
              PersonaView("John Murphy", "son", id="b" * 32),
              PersonaView("Mary Murphy", "sponsor", id="c" * 32),
              PersonaView("Mary Murphy", "sponsor", id="d" * 32),
              PersonaView("Unlabeled", "?", id="e" * 32))
    record = CaptureRecord(
        capture_id="x", folder_name="20261023-site-abc", url="", title=None, site_name=None,
        record_type=None, gating="public", copyright_flag=False, captured_at=None,
        wayback_url=None, short_note=None, source_list_entry=None, access_date=None,
        archived_url=None, personas=people, assertions=(), external_refs=(), media_files=(),
        file_types=())
    html = _add_fact_button(record)
    labels = [p["label"] for p in json.loads(
        re.search(r'data-personas="([^"]+)"', html).group(1).replace("&quot;", '"'))]
    assert labels == ["John Murphy (father)", "John Murphy (son)", "Mary Murphy (sponsor) #1",
                      "Mary Murphy (sponsor) #2", "Unlabeled"]


def test_after_an_edit_the_row_keeps_its_supporting_words_and_buttons(served):
    client, _settings, cid, _cwd = served
    death = _id(_row(client.get("/").text, "death_date"))
    client.post("/correction", headers=_AUTH, json={
        "capture_id": cid, "action": "edit", "assertion_id": death, "new_value": "13 March 1881"})
    row = _row(client.get("/").text, "death_date")
    assert 'data-snippet="Died 3 March 1881."' in row and "fix-edit" in row


def test_a_retracted_fact_has_no_buttons(served):
    client, _settings, cid, _cwd = served
    burial = _id(_row(client.get("/").text, "burial_place"))
    client.post("/correction", headers=_AUTH, json={
        "capture_id": cid, "action": "retract", "assertion_id": burial,
        "rationale": "the <b>wrong</b> cemetery"})
    row = _row(client.get("/").text, "burial_place")
    assert "fix-edit" not in row and "fix-retract" not in row
    # The researcher's reason is shown, escaped like every other value.
    assert ">retracted</span>" in row and "— the &lt;b&gt;wrong&lt;/b&gt; cemetery" in row


def test_the_static_file_has_no_correction_controls(served):
    from capsule.collection.index import write_index

    _client, settings, _cid, _cwd = served
    static = write_index(settings.captures_root).read_text(encoding="utf-8")
    for marker in ('class="fix fix-edit"', 'class="fix-add"', 'fetch("/correction"',
                   'class="v"', 'data-personas="'):
        assert marker not in static, marker
