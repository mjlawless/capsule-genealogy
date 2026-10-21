"""REQ-001 R2/R3 — draining an Ancestry record, end to end.

Two things this pins, both exposed by the pilot record:

**R2 — the provider's own description of the source.** Ancestry prints `Source Citation`,
`Source Information` and — the load-bearing one — `Original data`, naming the NARA
microfilm the database was made from. That line is what lets a citation name the
MICROFILM rather than the database. It is a claim about the SOURCE, so it carries no
persona: there is no human in "NARA T623, 1854 rolls".

**R3 — drain the record.** A census page is not one birth date. It is nine people with
roles, and the boarders and servants are people too — someone may one day be looking for
exactly them. Same-named household members must never merge (RawPersona.id).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from capsule.contracts.evidence import GatingStatus

_DT = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)

_ORIGINAL_DATA = (
    "United States of America, Bureau of the Census. Twelfth Census of the United "
    "States, 1900. Washington, D.C.: National Archives and Records Administration, "
    "1900. T623, 1854 rolls."
)
_SOURCE_CITATION = (
    "Year: 1900; Census Place: Queens Ward 5, Queens, New York; Roll: 1150; "
    "Page: 14; Enumeration District: 0687"
)

_CENSUS_PAGE = (
    "<html><head><title>1900 United States Federal Census</title></head><body>"
    "<table>"
    "<tr><td>Thomas Doyal</td><td>50</td><td>Head</td></tr>"
    "<tr><td>Mary K Doyal</td><td>44</td><td>Wife</td></tr>"
    "<tr><td>James Doyal</td><td>12</td><td>Son</td></tr>"
    "<tr><td>Annie Smith</td><td>35</td><td>Servant</td></tr>"
    "</table>"
    f"<div>Source Citation: {_SOURCE_CITATION}</div>"
    f"<div>Original data: {_ORIGINAL_DATA}</div>"
    "</body></html>"
)

# What the extractor is expected to return for that page.
_CENSUS_RAW = {
    "record_type": "census",
    "personas": [
        {"id": "p1", "label": "Thomas Doyal", "role": "head"},
        {"id": "p2", "label": "Mary K Doyal", "role": "wife"},
        {"id": "p3", "label": "James Doyal", "role": "son"},
        {"id": "p4", "label": "Annie Smith", "role": "servant"},
    ],
    "assertions": [
        {
            "persona_id": "p1",
            "field": "Relation",
            "value": "Head",
            "evidence_type": "stated",
            "snippet": "Thomas Doyal 50 Head",
        },
        {
            "persona_id": "p4",
            "field": "Relation",
            "value": "Servant",
            "evidence_type": "stated",
            "snippet": "Annie Smith 35 Servant",
        },
        {
            "persona_id": "p3",
            "field": "Age",
            "value": "12",
            "evidence_type": "stated",
            "snippet": "James Doyal 12 Son",
        },
    ],
    "provider_claims": [
        {
            "key": "Source Citation",
            "value": _SOURCE_CITATION,
            "snippet": f"Source Citation: {_SOURCE_CITATION}",
        },
        {
            "key": "Original data",
            "value": _ORIGINAL_DATA,
            "snippet": f"Original data: {_ORIGINAL_DATA}",
        },
    ],
}


def _run(tmp_path):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    n = 0

    def _ids() -> str:
        nonlocal n
        n += 1
        return f"id-{n}"

    url = "https://www.ancestry.com/search/collections/7602/records/56510272"
    fetcher = FakeFetcher(
        PageSnapshot(
            url=url,
            html=_CENSUS_PAGE,
            gating=GatingStatus.GATED,
            fetched_at=_DT,
            title="1900 United States Federal Census",
        )
    )
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_CENSUS_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    outcome = run_capture(
        url,
        fetcher=fetcher,
        agent=agent,
        repository=repo,
        captures_root=tmp_path / "captures",
        now=lambda: _DT,
        new_id=_ids,
    )
    return outcome, repo


def test_the_NARA_original_data_line_is_captured(tmp_path):
    """The load-bearing field: what lets us cite the microfilm, not the database."""
    outcome, repo = _run(tmp_path)

    claims = {
        c.key: c.verbatim_value for c in repo.provider_claims_for_source(outcome.source.id)
    }
    assert "T623, 1854 rolls" in claims["original_data"]
    assert "Roll: 1150" in claims["source_citation"]


def test_provider_claims_carry_no_persona(tmp_path):
    """There is no human in "NARA T623". A source-claim must never invent one."""
    from capsule.contracts.evidence import ProviderClaim

    assert "persona_id" not in ProviderClaim.model_fields

    outcome, _repo = _run(tmp_path)
    # The personas are exactly the people on the record — no phantom persona was created
    # to carry the citation blocks.
    labels = {p.label for p in outcome.extraction.personas}
    assert labels == {"Thomas Doyal", "Mary K Doyal", "James Doyal", "Annie Smith"}


def test_provider_claims_are_snippet_linked(tmp_path):
    """A fabricated 'Original data' line would send a researcher to a microfilm that does
    not exist — worse than no citation at all. So it is anchored to the page like any
    other claim."""
    outcome, repo = _run(tmp_path)

    claim = next(
        c
        for c in repo.provider_claims_for_source(outcome.source.id)
        if c.key == "original_data"
    )
    snippet = repo.get_snippet(claim.snippet_id)
    assert _ORIGINAL_DATA in snippet.verbatim_text


def test_the_household_is_drained_every_member_is_a_persona_with_a_role(tmp_path):
    """R3.1/R3.2: a census page is people with roles, not one birth date.

    The servant and the boarders are people on the record. A researcher may one day be
    looking for exactly them, and this capture is the only place they appear.
    """
    outcome, _repo = _run(tmp_path)

    by_label = {p.label: p for p in outcome.extraction.personas}
    assert by_label["Annie Smith"].role == "servant"  # not dropped, not flattened

    # The relationship column is the record's evidence for the family's structure.
    relations = {
        a.persona_id: a.verbatim_value
        for a in outcome.extraction.assertions
        if a.field == "relation"
    }
    assert relations[by_label["Thomas Doyal"].id] == "Head"
    assert relations[by_label["Annie Smith"].id] == "Servant"


def test_provider_claims_reach_the_capture_folder(tmp_path):
    """The folder is the archive of record, and it is hashed."""
    outcome, _repo = _run(tmp_path)

    extraction = json.loads(
        (outcome.capture_folder / "extraction.json").read_text(encoding="utf-8")
    )
    keys = {c["key"] for c in extraction["provider_claims"]}
    assert keys == {"source_citation", "original_data"}
    assert outcome.verify.ok


def test_a_capture_without_provider_claims_is_byte_identical_to_before():
    """Additive: omitted when empty, so no existing capture's content_hash moves."""
    from capsule.capture.manifest import Extraction, serialize_extraction

    assert b"provider_claims" not in serialize_extraction(Extraction())


# ---------------------------------------------------------------------------
# BUG-011 — RawProviderClaim must accept `annotation` like the other raw claims
# ---------------------------------------------------------------------------


def test_BUG_011_raw_provider_claim_accepts_annotation():
    """Regression: the extractor may annotate a provider claim (e.g. the 'Original data'
    block); RawProviderClaim must accept `annotation` like RawAssertion/RawCoverage do.

    A strict extra="forbid" that lacked the field turned a legitimate producer field into
    a hard 422, blocking full-image capture on any record the extractor annotated.
    """
    from capsule.pipeline.extract import RawProviderClaim

    rc = RawProviderClaim(
        key="original_data",
        value="NARA M237, roll 351",
        snippet="Original data: NARA M237, roll 351",
        annotation="the source is spelled out inline",
    )
    assert rc.annotation == "the source is spelled out inline"


def test_BUG_011_extraction_reply_with_annotated_provider_claim_is_accepted():
    """The exact shape that 422'd: a provider_claims entry carrying `annotation`."""
    from capsule.pipeline.extract import RawExtraction

    RawExtraction.model_validate(
        {
            "record_type": "immigration",
            "personas": [],
            "assertions": [],
            "coverage": [],
            "external_refs": [],
            "provider_claims": [
                {
                    "key": "original_data",
                    "value": "NARA M237, roll 351",
                    "snippet": "Original data: NARA M237, roll 351",
                    "annotation": "the source is spelled out inline",
                }
            ],
        }
    )  # must not raise


def test_BUG_011_provider_claim_annotation_survives_into_the_canonical_model(tmp_path):
    """The note must not be silently dropped once ingestion accepts it: build.py threads
    it through _flag_unverified into the canonical ProviderClaim, exactly like assertions
    and coverage do."""
    raw = json.loads(json.dumps(_CENSUS_RAW))
    raw["provider_claims"][1]["annotation"] = "NARA microfilm spelled out inline"

    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    url = "https://www.ancestry.com/search/collections/7602/records/56510272"
    fetcher = FakeFetcher(
        PageSnapshot(
            url=url,
            html=_CENSUS_PAGE,
            gating=GatingStatus.GATED,
            fetched_at=_DT,
            title="1900 United States Federal Census",
        )
    )
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(raw)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    n = 0

    def _ids() -> str:
        nonlocal n
        n += 1
        return f"id-{n}"

    repo = Repository(tmp_path / "capsule.db")
    outcome = run_capture(
        url,
        fetcher=fetcher,
        agent=agent,
        repository=repo,
        captures_root=tmp_path / "captures",
        now=lambda: _DT,
        new_id=_ids,
    )
    assert outcome.verify.ok

    claim = next(
        c
        for c in repo.provider_claims_for_source(outcome.source.id)
        if c.key == "original_data"
    )
    assert claim.annotation == "NARA microfilm spelled out inline"


def test_BUG_011_raw_claim_models_share_the_optional_annotation_shape():
    """Contract: the three raw claim models must not drift again — each carries an
    optional `annotation` field so the extractor can annotate any of them."""
    from capsule.pipeline.extract import RawAssertion, RawCoverage, RawProviderClaim

    for model in (RawAssertion, RawCoverage, RawProviderClaim):
        field = model.model_fields.get("annotation")
        assert field is not None, f"{model.__name__} is missing `annotation`"
        assert not field.is_required(), f"{model.__name__}.annotation must be optional"


# ---------------------------------------------------------------------------
# BUG-014 — surface provider_claims where the CONSUMER actually reads them
# ---------------------------------------------------------------------------
#
# The extraction captured the NARA roll/ED/microfilm all along (the tests above prove it),
# but the consumer reads the manifest and the citation, not the raw extraction — and neither
# carried the provider's blocks. So from their seat the microfilm "was not in the capture"
# and they re-transcribed it by hand. Surface it in both.


def test_BUG_014_manifest_surfaces_provider_claims(tmp_path):
    """The manifest is the folder's self-describing record (R4.3). The NARA roll/ED/microfilm
    must be IN it, or the roll/ED are lost the moment the index DB is."""
    outcome, _repo = _run(tmp_path)

    manifest = json.loads(
        (outcome.capture_folder / "manifest.json").read_text(encoding="utf-8")
    )
    claims = {c["key"]: c["value"] for c in manifest["provider_claims"]}
    assert "T623, 1854 rolls" in claims["original_data"]
    assert "Roll: 1150" in claims["source_citation"]


def test_BUG_014_citation_names_the_original_not_only_the_database(tmp_path):
    """The reference note must cite the microfilm the database was made from — the
    difference between a derivative citation and a layered one."""
    outcome, _repo = _run(tmp_path)

    note = outcome.citation.reference_note
    assert "; citing" in note
    assert "T623, 1854 rolls" in note  # the NARA original is named
    assert "database entry" in note  # still layered ON the database, not replacing it


def test_BUG_014_original_data_is_preferred_and_its_trailing_period_dropped():
    """`original_data` (the microfilm) wins over `source_citation` (roll/ED), and the note's
    own closing period is not doubled onto a value ending in 'rolls.'."""
    from capsule.contracts.evidence import ProviderClaim
    from capsule.pipeline.citation import _provider_citing

    claims = [
        ProviderClaim(
            id="1", source_id="s", snippet_id="a", key="source_citation",
            verbatim_value="Roll: 1150", created_at=_DT,
        ),
        ProviderClaim(
            id="2", source_id="s", snippet_id="b", key="original_data",
            verbatim_value="NARA T623, 1854 rolls.", created_at=_DT,
        ),
    ]
    assert _provider_citing(claims) == "; citing NARA T623, 1854 rolls"
    assert _provider_citing([]) == ""  # no provider block -> no citing layer


# ---------------------------------------------------------------------------
# result.json — the machine-readable outcome sidecar (live-run finding)
# ---------------------------------------------------------------------------


def test_result_json_is_written_and_does_not_break_verify(tmp_path):
    """The driving agent reads the verdict from result.json, not by measuring pixels — and
    the sidecar must be EXCLUDED from the hash manifest so writing it never fails verify."""
    from capsule.capture.manifest import verify_capture

    outcome, _repo = _run(tmp_path)

    result = json.loads(
        (outcome.capture_folder / "result.json").read_text(encoding="utf-8")
    )
    assert result["capture_id"] == outcome.capture_folder.name
    assert result["record_type"] == "census"
    assert result["verify_ok"] is True
    assert result["assertion_count"] == len(outcome.extraction.assertions)
    assert result["persona_count"] == len(outcome.extraction.personas)

    # The folder — now containing result.json — must still verify: it is an excluded sidecar,
    # not hashed evidence. A fresh verify (after the file exists) is the real test.
    assert verify_capture(outcome.capture_folder).ok
