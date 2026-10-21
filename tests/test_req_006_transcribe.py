"""REQ-006 T3 — the two-pass transcriber, against scripted model replies.

Pass 1 (layout) sees each page downscaled and reports where each handwritten
field is, as a box in THOUSANDTHS of the image — immune to any resize the API
applies — plus the rotation that makes it upright. Pass 2 (read) sees every
field crop cut from the NATIVE pixels, padded, and reports verbatim text,
status, and confidence. These tests pin the guard that replaces the DOM path's
`_locate_snippet`: a reading is kept only if it names a crop that was sent, is
internally consistent, and anything shaky is flagged for review.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from capsule.agent.base import Agent
from capsule.agent.fake import FakeTransport
from capsule.pipeline.transcribe import (
    FieldStatus,
    parse_layout,
    parse_readings,
    transcribe_document,
)

_DT = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _pdf(tmp_path: Path, pages: int = 2) -> Path:
    imgs = [Image.new("RGB", (2000, 2400), "white") for _ in range(pages)]
    path = tmp_path / "1-d-q-1903-0000148.pdf"
    imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=300)
    return path


def _agents(layout_reply: dict, read_replies: list[dict]):
    layout_t = FakeTransport(json.dumps(layout_reply))
    read_t = FakeTransport([json.dumps(r) for r in read_replies])
    ids = iter(f"call{i}" for i in range(100))
    mk = lambda name, t: Agent(  # noqa: E731
        name=name, model="claude-opus-5", transport=t, now=lambda: _DT, new_id=lambda: next(ids)
    )
    return mk("layout", layout_t), mk("transcriber", read_t), layout_t, read_t


_LAYOUT = {
    "pages": [
        {
            "image": 1,
            "handwritten": True,
            "form": "Certificate and Record of Death (H-1899)",
            "fields": [
                {"key": "full_name", "label": "of", "box": [250, 100, 600, 130], "rotation": 0},
                {"key": "father_name", "label": "Father's Name.", "box": [520, 620, 560, 1000], "rotation": 270},
                {"key": "mother_name", "label": "Mother's Name.", "box": [400, 620, 440, 1000], "rotation": 270},
            ],
        },
        {"image": 2, "handwritten": False, "form": "instructions (reverse)", "fields": []},
    ],
    "notes": "",
}

_READ = {
    "fields": [
        {"image": 1, "key": "full_name", "status": "value", "verbatim": "Mary Doyle",
         "annotation": None, "confidence": 0.97},
        {"image": 2, "key": "father_name", "status": "value", "verbatim": "James Kilcoyne",
         "annotation": None, "confidence": 0.93},
        {"image": 3, "key": "mother_name", "status": "value", "verbatim": "Bridget. Kilcoyne",
         "annotation": "trailing period as written", "confidence": 0.9},
    ]
}


def test_two_passes_read_native_crops_and_skip_printed_pages(tmp_path):
    layout, reader, layout_t, read_t = _agents(_LAYOUT, [_READ])
    draft = transcribe_document(_pdf(tmp_path), layout_agent=layout, read_agent=reader)

    # pass 1 saw both pages, downscaled
    (layout_call,) = layout_t.calls
    assert [i.ref.page for i in layout_call["images"]] == [0, 1]
    assert all(max(i.ref.width, i.ref.height) <= 1568 for i in layout_call["images"])

    # pass 2 ran once — the printed reverse page produced no read call
    (read_call,) = read_t.calls
    crops = read_call["images"]
    assert [c.ref.label.split(" ")[0] for c in crops] == ["full_name", "father_name", "mother_name"]

    father = crops[1].ref
    # 520/1000*2000=1040 .. 560/1000*2000=1120, 620/1000*2400=1488 .. 2400; padded, clamped
    x0, y0, x1, y1 = father.bbox
    assert x0 < 1040 and x1 > 1120 and y0 < 1488 and y1 == 2400
    assert father.rotation == 270 and father.scale == 1.0  # native pixels
    assert (father.width, father.height) == (y1 - y0, x1 - x0)  # rotated

    values = {f.key: f.verbatim for f in draft.fields}
    assert values == {
        "full_name": "Mary Doyle",
        "father_name": "James Kilcoyne",
        "mother_name": "Bridget. Kilcoyne",  # the period survives
    }
    assert draft.pages[1].handwritten is False
    assert len(draft.calls) == 2 and draft.problems == []


def test_reading_carries_crop_bytes_and_provenance(tmp_path):
    layout, reader, *_ = _agents(_LAYOUT, [_READ])
    draft = transcribe_document(_pdf(tmp_path), layout_agent=layout, read_agent=reader)
    father = next(f for f in draft.fields if f.key == "father_name")
    assert father.crop.ref.label.startswith("father_name")
    assert father.crop.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert father.agent_call_id == draft.calls[1].id
    assert father.form_label == "Father's Name."


def test_unknown_and_blank_and_illegible_are_values_not_nulls(tmp_path):
    read = {"fields": [
        {"image": 1, "key": "full_name", "status": "illegible", "verbatim": "[illegible]", "confidence": 0.2},
        {"image": 2, "key": "father_name", "status": "unknown", "verbatim": "Unknown", "confidence": 0.95},
        {"image": 3, "key": "mother_name", "status": "blank", "verbatim": None, "confidence": 0.9},
    ]}
    layout, reader, *_ = _agents(_LAYOUT, [read])
    draft = transcribe_document(_pdf(tmp_path), layout_agent=layout, read_agent=reader)
    by = {f.key: f for f in draft.fields}
    assert by["father_name"].status is FieldStatus.UNKNOWN and by["father_name"].verbatim == "Unknown"
    assert by["mother_name"].status is FieldStatus.BLANK and by["mother_name"].verbatim is None
    assert by["full_name"].status is FieldStatus.ILLEGIBLE and by["full_name"].needs_review


@pytest.mark.parametrize(
    "reading, reason",
    [
        ({"image": 9, "key": "ghost", "status": "value", "verbatim": "x", "confidence": 0.9}, "not sent"),
        ({"image": 2, "key": "mother_name", "status": "value", "verbatim": "x", "confidence": 0.9}, "not sent"),
        ({"image": 1, "key": "full_name", "status": "value", "verbatim": "", "confidence": 0.9}, "invalid"),
        ({"image": 1, "key": "full_name", "status": "value", "verbatim": "x", "confidence": 1.7}, "invalid"),
        ({"image": 1, "key": "full_name", "status": "illegible", "verbatim": "Mary", "confidence": 0.3}, "invalid"),
    ],
)
def test_guard_drops_readings_that_do_not_match_a_sent_crop_or_are_inconsistent(tmp_path, reading, reason):
    layout, reader, *_ = _agents(_LAYOUT, [{"fields": [reading]}])
    draft = transcribe_document(_pdf(tmp_path), layout_agent=layout, read_agent=reader)
    assert all(f.verbatim != "x" and f.verbatim != "Mary" for f in draft.fields)
    assert any(reason in p for p in draft.problems)


def test_crop_with_no_reading_is_reported_not_invented(tmp_path):
    read = {"fields": [_READ["fields"][0]]}
    layout, reader, *_ = _agents(_LAYOUT, [read])
    draft = transcribe_document(_pdf(tmp_path), layout_agent=layout, read_agent=reader)
    assert [f.key for f in draft.fields] == ["full_name"]
    assert sum("no reading" in p for p in draft.problems) == 2


def test_low_confidence_and_markers_need_review(tmp_path):
    read = {"fields": [
        {"image": 1, "key": "full_name", "status": "value", "verbatim": "Mary Doyle", "confidence": 0.97},
        {"image": 2, "key": "father_name", "status": "value", "verbatim": "James K[?]lcoyne", "confidence": 0.95},
        {"image": 3, "key": "mother_name", "status": "value", "verbatim": "Bridget Kilcoyne", "confidence": 0.6},
    ]}
    layout, reader, *_ = _agents(_LAYOUT, [read])
    draft = transcribe_document(_pdf(tmp_path), layout_agent=layout, read_agent=reader)
    review = {f.key: f.needs_review for f in draft.fields}
    assert review == {"full_name": False, "father_name": True, "mother_name": True}


def test_bad_layout_box_is_a_problem_not_a_crash(tmp_path):
    layout_reply = {"pages": [{"image": 1, "handwritten": True, "form": "x", "fields": [
        {"key": "a", "label": "A", "box": [600, 100, 500, 130], "rotation": 0},
        {"key": "b", "label": "B", "box": [0, 0, 1400, 50], "rotation": 0},  # beyond the 1307px overview too
        {"key": "c", "label": "C", "box": [100, 100, 200, 130], "rotation": 45},
    ]}]}
    layout, reader, _lt, read_t = _agents(layout_reply, [{"fields": []}])
    draft = transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    assert draft.fields == [] and len(draft.problems) >= 3
    assert read_t.calls == []  # nothing valid to read → no read call


def test_many_fields_are_read_in_batches(tmp_path):
    fields = [
        {"key": f"f{i}", "label": f"F{i}", "box": [10 * i, 10, 10 * i + 8, 40], "rotation": 0}
        for i in range(25)
    ]
    layout_reply = {"pages": [{"image": 1, "handwritten": True, "form": "x", "fields": fields}]}
    replies = [
        {"fields": [{"image": j + 1, "key": f"f{i}", "status": "value", "verbatim": "v", "confidence": 0.99}
                    for j, i in enumerate(range(start, min(start + 20, 25)))]}
        for start in (0, 20)
    ]
    layout, reader, _lt, read_t = _agents(layout_reply, replies)
    draft = transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    assert [len(c["images"]) for c in read_t.calls] == [20, 5]
    assert len(draft.fields) == 25 and draft.problems == []


def test_parsers_tolerate_fences_and_reject_garbage():
    assert parse_layout("```json\n" + json.dumps(_LAYOUT) + "\n```").pages[0].fields[1].rotation == 270
    with pytest.raises(ValueError):
        parse_layout("the page is blank")
    with pytest.raises(ValueError):
        parse_readings('{"fields": "nope"}')


def test_prompts_carry_the_non_negotiables():
    from capsule.pipeline.transcribe import READ_SYSTEM

    for rule in ("verbatim", "[?]", "Unknown", "never guess", "annotation", "period"):
        assert rule.lower() in READ_SYSTEM.lower(), rule


# --- self-correction: the reader reports a bad crop, the pipeline re-cuts it ---
# Live run 2026-09-24 on Q-1903-0148: the layout pass gave every sideways table
# column rotation 90 (they needed 270) and boxes too short along the writing —
# the father's surname fell outside the crop. The reader SAW both ("image is
# upside down", "surname may lie outside the crop"). These pin the loop that
# acts on what it saw instead of keeping a reading of a bad crop.


def _one_field_layout(rotation=270, box=(500, 600, 540, 700)):
    return {"pages": [{"image": 1, "handwritten": True, "form": "x", "fields": [
        {"key": "fathers_name", "label": "Father's Name.", "box": list(box), "rotation": rotation},
    ]}]}


def _reading(verbatim="James Kilcoyne", confidence=0.93, **extra):
    return {"fields": [{"image": 1, "key": "fathers_name", "status": "value",
                        "verbatim": verbatim, "confidence": confidence, **extra}]}


def test_upside_down_crop_is_re_cut_rotated_and_the_bad_reading_discarded(tmp_path):
    first = _reading("[?James]", 0.35, orientation="upside_down")
    layout, reader, _lt, read_t = _agents(_one_field_layout(rotation=90), [first, _reading()])
    draft = transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)

    assert [c["images"][0].ref.rotation for c in read_t.calls] == [90, 270]
    (field,) = draft.fields
    assert field.verbatim == "James Kilcoyne" and field.attempts == 2
    assert field.agent_call_id == draft.calls[-1].id


def test_sideways_crop_is_turned_a_quarter_and_re_read(tmp_path):
    first = _reading("[illegible]", 0.1, status="illegible", orientation="sideways")
    first["fields"][0]["status"] = "illegible"
    layout, reader, _lt, read_t = _agents(_one_field_layout(rotation=0), [first, _reading()])
    transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    assert [c["images"][0].ref.rotation for c in read_t.calls] == [0, 90]


@pytest.mark.parametrize(
    "rotation, clipped_sent_edge, native_edge",
    [
        (0, "right", 2),
        (0, "bottom", 3),
        (270, "right", 3),  # upright writing's end = the native BOTTOM of a top-to-bottom column
        (270, "left", 1),
        (90, "right", 1),
        (180, "right", 0),
    ],
)
def test_clipped_edge_expands_the_matching_native_edge(tmp_path, rotation, clipped_sent_edge, native_edge):
    first = _reading("James", 0.5, clipped=[clipped_sent_edge])
    layout, reader, _lt, read_t = _agents(
        _one_field_layout(rotation=rotation, box=(400, 400, 440, 500)), [first, _reading()]
    )
    transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    before, after = (c["images"][0].ref.bbox for c in read_t.calls)
    grew = [i for i in range(4) if before[i] != after[i]]
    assert grew == [native_edge]
    # grew OUTWARD
    assert (after[native_edge] < before[native_edge]) == (native_edge in (0, 1))


def test_correction_stops_after_the_last_attempt_and_flags_why(tmp_path):
    bad = _reading("[?James]", 0.4, clipped=["right"])
    layout, reader, _lt, read_t = _agents(_one_field_layout(rotation=0), [bad, bad, bad, bad])
    draft = transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    assert len(read_t.calls) == 3  # 1 read + 2 corrections, never more
    (field,) = draft.fields
    assert field.attempts == 3 and field.needs_review
    assert "clipped" in (field.note or "")


def test_correction_rounds_batch_all_retries_together(tmp_path):
    layout_reply = {"pages": [{"image": 1, "handwritten": True, "form": "x", "fields": [
        {"key": f"f{i}", "label": "L", "box": [100 * i, 600, 100 * i + 40, 700], "rotation": 90}
        for i in range(3)
    ]}]}
    first = {"fields": [
        {"image": i + 1, "key": f"f{i}", "status": "value", "verbatim": "x", "confidence": 0.3,
         "orientation": "upside_down" if i < 2 else "upright"} for i in range(3)
    ]}
    second = {"fields": [
        {"image": i + 1, "key": f"f{i}", "status": "value", "verbatim": "ok", "confidence": 0.95}
        for i in range(2)
    ]}
    layout, reader, _lt, read_t = _agents(layout_reply, [first, second])
    draft = transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    assert [len(c["images"]) for c in read_t.calls] == [3, 2]
    assert sorted((f.key, f.verbatim) for f in draft.fields) == [("f0", "ok"), ("f1", "ok"), ("f2", "x")]


def test_default_padding_is_generous_along_the_writing_not_across_it(tmp_path):
    layout, reader, _lt, read_t = _agents(_one_field_layout(box=(500, 600, 540, 700)), [_reading()])
    transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    x0, y0, x1, y1 = read_t.calls[0]["images"][0].ref.bbox
    raw_w, raw_h = 80, 240  # 40/1000*2000, 100/1000*2400
    assert (x1 - x0) - raw_w < (y1 - y0) - raw_h  # the long axis gained more
    assert (y1 - y0) >= raw_h * 1.4


# --- layout units: live run 2026-09-24 on K-1905-22719 --------------------------
# The layout model answered one page in PIXELS of the overview it saw (x up to
# 1345 on a 1379-wide image) despite being asked for thousandths. The guard
# rejected all 25 boxes — safe, but the page's fields (father, death date) were
# lost. The overview's size is known exactly, so pixel boxes are converted — and
# the rescale is recorded, never silent.


def _pixel_layout(units=None, box=(652, 942, 700, 1500)):
    page = {"image": 1, "handwritten": True, "form": "x", "fields": [
        {"key": "fathers_name", "label": "Father's Name.", "box": list(box), "rotation": 0}]}
    if units:
        page["units"] = units
    return {"pages": [page]}


def test_the_prompt_states_each_overview_size_and_asks_for_thousandths(tmp_path):
    layout, reader, layout_t, _ = _agents(_pixel_layout(), [_reading()])
    transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    prompt = layout_t.calls[0]["prompt"]
    assert "1307x1568" in prompt and "thousandths" in prompt


@pytest.mark.parametrize("units", [None, "pixels"])
def test_pixel_boxes_are_rescaled_from_the_overview_and_noted(tmp_path, units):
    # overview of a 2000x2400 page is 1307x1568 (scale 0.6533); box 652..700 x 942..1500
    layout, reader, _lt, read_t = _agents(_pixel_layout(units), [_reading()])
    draft = transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    assert draft.problems == []
    x0, y0, x1, y1 = read_t.calls[0]["images"][0].ref.bbox
    # native: 652/1307*2000≈998 .. 700/1307*2000≈1071 (+ padding along x), 942/1568*2400≈1442 .. 2296
    assert 900 < x0 < 998 and 1071 < x1 < 1200 and 1380 < y0 < 1442 and 2296 < y1 <= 2400
    assert "pixels" in (draft.layout_notes or "") and "rescaled" in (draft.layout_notes or "")


def test_declared_pixels_below_1000_are_still_pixels(tmp_path):
    layout, reader, _lt, read_t = _agents(_pixel_layout("pixels", box=(100, 100, 300, 140)), [_reading()])
    transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    x0, _y0, x1, _y1 = read_t.calls[0]["images"][0].ref.bbox
    assert x1 > 300 / 1307 * 2000  # read as pixels (≈459 native), not thousandths (600)
    assert x1 < 600


def test_pixel_boxes_beyond_the_overview_are_still_invalid(tmp_path):
    layout, reader, _lt, read_t = _agents(_pixel_layout(box=(652, 942, 1400, 1500)), [_reading()])
    draft = transcribe_document(_pdf(tmp_path, pages=1), layout_agent=layout, read_agent=reader)
    assert draft.fields == [] and any("invalid box" in p for p in draft.problems)
