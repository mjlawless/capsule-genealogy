"""BUG-019 — `page_label` was read from any dropdown on any site, and joined the dedup identity.

The extension takes the biggest `<select>` on any page, and the backend trusted whatever label
it was sent, so 29 of 31 labelled captures carried junk: "Select Language" (NYC), "Cemetery
office has no record of this person" (Find a Grave), "Fayette" (an Ancestry county dropdown).
The backend now decides (`accepted_page_label`), which also covers extensions already
installed. The trap the bug report named is pinned too: on RootsIreland the label is what keeps
different records at one search URL apart, so dropping it there would cause false blocks.
"""
from __future__ import annotations

import json

import pytest

import tests.test_backend as tb

_NYC = "https://a860-historicalvitalrecords.nyc.gov/view/608725"
_ARCHION = "https://www.archion.de/de/viewer/churchRegister/239254?cHash=abc"
_RIKS = "https://sok.riksarkivet.se/bildvisning/C0010681_00040"
_ROOTS = "https://www.rootsireland.ie/mayo/quis.php?page=0&prevStartQuery=0"
_BROWSE = "https://www.ancestry.com/imageviewer/collections/8802/images/005537869_00063"
_FINDAGRAVE = "https://www.findagrave.com/memorial/123/john-doe"


@pytest.mark.parametrize(
    ("url", "label", "apid", "want"),
    [
        (_NYC, "Select Language", None, None),
        (_FINDAGRAVE, "Cemetery office has no record of this person", None, None),
        (_BROWSE, "Fayette", None, None),
        ("https://www.irishgenealogy.ie/view?record=1", "-", None, None),
        (_ARCHION, "6", None, "6"),
        ("https://registers.nli.ie/registers/vtls000632043#page/26/mode/1up", "26", None, "26"),
        (_RIKS, "Bild 40 / Sida 63", None, "Bild 40 / Sida 63"),
        (_ROOTS, "BURRISCARRA RC PARISH (Roman Catholic)", None,
         "BURRISCARRA RC PARISH (Roman Catholic)"),
        # REQ-009: the one-call flow names a record's extra image, with its apid.
        ("https://www.ancestry.com/imageviewer/collections/62073/images/x_00154",
         "62073_302022005552_0481-00153", "62073:223848", "62073_302022005552_0481-00153"),
        # Lookalike hosts get nothing.
        ("https://archion.de.evil.example/viewer", "6", None, None),
        ("https://evilrootsireland.ie/quis.php", "X", None, None),
        (_ARCHION, None, None, None),
        (_ARCHION, "", None, None),
    ],
)
def test_BUG_019_which_labels_survive(url, label, apid, want):
    from capsule.pipeline.page_identity import accepted_page_label

    assert accepted_page_label(url, label, expected_apid=apid) == want


def _post(client, url, label):
    return client.post(
        "/capture",
        json={"url": url, "html": tb._PAGE, "page_label": label},
        headers=tb._AUTH,
    )


def _manifests(tmp_path):
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((tmp_path / "captures").glob("*/manifest.json"))
    ]


def test_BUG_019_a_junk_label_never_reaches_the_folder(tmp_path):
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        assert _post(client, _NYC, "Select Language").status_code == 200
    finally:
        backend.app.dependency_overrides.clear()
    (manifest,) = _manifests(tmp_path)
    assert "page_label" not in manifest["source"]


def test_BUG_019_a_junk_label_no_longer_splits_the_dedup_identity(tmp_path):
    """The same NYC certificate twice: the second is a duplicate, whatever dropdown it saw."""
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        assert _post(client, _NYC, None).status_code == 200
        second = _post(client, _NYC, "Select Language")
    finally:
        backend.app.dependency_overrides.clear()
    assert second.status_code == 409, second.text
    assert len(_manifests(tmp_path)) == 1


def test_BUG_019_a_riksarkivet_label_is_kept(tmp_path):
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        assert _post(client, _RIKS, "Bild 40 / Sida 63").status_code == 200
    finally:
        backend.app.dependency_overrides.clear()
    (manifest,) = _manifests(tmp_path)
    assert manifest["source"]["page_label"] == "Bild 40 / Sida 63"


def test_BUG_019_the_trap_two_rootsireland_records_at_one_url_are_both_kept(tmp_path):
    """Dropping RootsIreland's label would refuse the second record as already mined."""
    client, backend = tb._client(tmp_path, json.dumps(tb._RAW))
    try:
        first = _post(client, _ROOTS, "BURRISCARRA RC PARISH (Roman Catholic)")
        second = _post(client, _ROOTS, "BALLINROBE RC PARISH (Roman Catholic)")
    finally:
        backend.app.dependency_overrides.clear()
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(_manifests(tmp_path)) == 2


def test_BUG_019_record_identity_off_a_tile_viewer():
    """The key itself, for a non-tile host with and without a (scoped) label."""
    from capsule.pipeline.dedup import record_identity
    from capsule.pipeline.page_identity import accepted_page_label

    bare = record_identity(_NYC)
    scoped = accepted_page_label(_NYC, "Select Language")
    assert record_identity(_NYC, None, scoped) == bare
    assert "#page=" not in bare
