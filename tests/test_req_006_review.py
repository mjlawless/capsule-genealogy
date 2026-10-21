"""REQ-006 T7 — the crop-beside-value review page.

§5.5 / §8.2: per-field confidence plus crop-beside-value review is "the feature that
makes the rest safe" — a human verifies the shaky fields against the pixels they were
read from. `GET /review/<capture_id>` renders the capture's latest transcript: the
caution banner, conflicts first (both values, no winner), then every field beside its
crop, least trustworthy first. Everything the model wrote is escaped — it is untrusted.
"""
from __future__ import annotations

import json
import shutil

import tests.test_req_006_reconcile as t5
import tests.test_req_006_storage as t4
import tests.test_req_006_transcribe_capture as t6
from capsule.collection.transcript_review import latest_transcript, render_review
from capsule.contracts.transcript import TranscriptStatus


def _doc(fields, comparisons=()):
    return {
        "kind": "machine-transcript",
        "derivative": True,
        "caution": "A machine reading of the scanned image — a reading aid, not the record.",
        "transcription": t4._transcription().model_dump(mode="json"),
        "fields": [f.model_dump(mode="json") for f in fields],
        "comparisons": [c.model_dump(mode="json") for c in comparisons],
    }


def test_page_leads_with_the_caution_and_the_conflicts():
    doc = _doc([t4._field()], [t5._comparison(transcript_field_id="tf1")])
    html = render_review("cap-1", doc)
    assert "not the record" in html
    assert html.index("1905-11-12") < html.index("Bridget. Kilcoyne")  # conflicts come first
    assert "Dec 12 1905" in html and "month differs" in html


def test_shakiest_fields_come_first_and_each_sits_beside_its_crop():
    fields = [
        t4._field(id="a", field_key="sure", verbatim_value="Ireland", confidence=0.97,
                  needs_review=False, crop_filename="crops/p1-sure.png"),
        t4._field(id="b", field_key="shaky", verbatim_value="Kil[?coyne]", confidence=0.4,
                  crop_filename="crops/p1-shaky.png"),
        t4._field(id="c", field_key="flagged", verbatim_value="Tenement", confidence=0.85,
                  crop_filename="crops/p1-flagged.png"),
    ]
    html = render_review("cap-1", _doc(fields))
    assert html.index("shaky") < html.index("flagged") < html.index(">sure<")
    assert 'src="/cap-1/transcript/tr1/crops/p1-shaky.png"' in html


def test_markers_unknown_and_blank_are_shown_as_what_they_are():
    fields = [
        t4._field(id="a", field_key="m", verbatim_value="Kil[?coyne]"),
        t4._field(id="b", field_key="u", status=TranscriptStatus.UNKNOWN, verbatim_value="Unknown"),
        t4._field(id="c", field_key="bl", status=TranscriptStatus.BLANK, verbatim_value=None),
    ]
    html = render_review("cap-1", _doc(fields))
    assert "<mark>[?coyne]</mark>" in html
    assert "Unknown" in html and "informant did not know" in html
    assert "(blank on the form)" in html


def test_everything_the_model_wrote_is_escaped():
    evil = t4._field(verbatim_value="<script>alert(1)</script>", annotation='"><img src=x onerror=1>',
                     note="<b>n</b>", form_label="<i>L</i>")
    html = render_review("cap-1", _doc([evil]))
    assert "<script>alert" not in html and "<img src=x" not in html and "<b>n</b>" not in html
    assert "&lt;script&gt;" in html


def test_latest_transcript_picks_the_newest_run(tmp_path):
    folder = tmp_path / "cap"
    for tid, when in (("old", "2026-09-24T10:00:00+00:00"), ("new", "2026-09-24T12:00:00+00:00")):
        d = folder / "transcript" / tid
        d.mkdir(parents=True)
        doc = _doc([])
        doc["transcription"]["id"], doc["transcription"]["created_at"] = tid, when
        (d / "transcript.json").write_text(json.dumps(doc), encoding="utf-8")
    assert latest_transcript(folder)["transcription"]["id"] == "new"
    assert latest_transcript(tmp_path / "nothing") is None


# --- the route --------------------------------------------------------------------


def _client(tmp_path):
    from fastapi.testclient import TestClient

    from capsule.backend.app import Settings, app, get_settings

    root, folder = t6._capture(tmp_path)
    repo = t6._repo(tmp_path)
    t6._run(repo, root, folder)
    repo.close()
    settings = Settings(db_path=tmp_path / "capsule.db", captures_root=root, token="t")
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), app, folder


def test_review_route_serves_the_page_and_its_crops(tmp_path):
    client, app, folder = _client(tmp_path)
    try:
        page = client.get(f"/review/{folder.name}")
        assert page.status_code == 200 and "not the record" in page.text
        assert page.headers["x-content-type-options"] == "nosniff"
        src = page.text.split('<img src="', 1)[1].split('"', 1)[0]
        crop = client.get(src)
        assert crop.status_code == 200 and crop.content[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        app.dependency_overrides.clear()


def test_review_route_404s_without_a_transcript_and_rejects_bad_ids(tmp_path):
    client, app, folder = _client(tmp_path)
    try:
        shutil.rmtree(folder / "transcript")
        assert client.get(f"/review/{folder.name}").status_code == 404
        assert client.get("/review/not a capture").status_code in (404, 422)
    finally:
        app.dependency_overrides.clear()
