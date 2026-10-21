"""C4: write_fixture — turn a corrected capture into an eval fixture.

The flywheel payoff: after a human corrects a capture, its current assertions are
the gold. `write_fixture` projects that back into the fixture shape, and it must
round-trip through `harness.load_fixture` (the gold scored against itself passes)
and reflect the corrections (edited value in, retracted fact out, added fact in).

Fully offline: FakeFetcher + FakeTransport build a real HTML capture.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from capsule.contracts.evidence import CorrectionAction, EvidenceType
from capsule.evals.fixtures import write_fixture
from capsule.evals.harness import load_fixture, score
from capsule.pipeline.correct import CorrectionRequest, apply_correction

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_CORR_DT = datetime(2026, 7, 12, 9, 0, 0, tzinfo=timezone.utc)

_HTML = (
    "<html><head><title>Mary Kelly (1810-1881)</title></head>"
    "<body><h1>Mary Kelly</h1><p>Died 3 March 1881.</p>"
    "<p>Buried at Glasnevin Cemetery, Dublin.</p></body></html>"
)

_RAW = {
    "record_type": "burial",
    "personas": [{"label": "Mary Kelly", "role": "deceased"}],
    "assertions": [
        {
            "persona": "Mary Kelly",
            "field": "death_date",
            "value": "3 March 1881",
            "evidence_type": "stated",
            "snippet": "Died 3 March 1881.",
        },
        {
            "persona": "Mary Kelly",
            "field": "burial_place",
            "value": "Glasnevin Cemetery, Dublin",
            "evidence_type": "stated",
            "snippet": "Buried at Glasnevin Cemetery, Dublin.",
        },
    ],
}


def _ids(prefix: str):
    n = 0

    def _next() -> str:
        nonlocal n
        n += 1
        return f"{prefix}-{n}"

    return _next


def _run_html(tmp_path):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.contracts.evidence import GatingStatus
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    snapshot = PageSnapshot(
        url="https://www.findagrave.com/memorial/1",
        html=_HTML,
        gating=GatingStatus.PUBLIC,
        fetched_at=_DT,
        title="Mary Kelly (1810-1881)",
        site_name="Find a Grave",
    )
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    captures_root = tmp_path / "captures"
    repo = Repository(tmp_path / "capsule.db")
    outcome = run_capture(
        "https://www.findagrave.com/memorial/1",
        fetcher=FakeFetcher(snapshot),
        agent=agent,
        repository=repo,
        captures_root=captures_root,
        now=lambda: _DT,
        new_id=_ids("id"),
    )
    return outcome, repo, captures_root


def test_fixture_roundtrips_through_load_fixture(tmp_path):
    outcome, repo, captures = _run_html(tmp_path)
    fixtures_root = tmp_path / "fixtures"

    dest = write_fixture(
        repository=repo,
        captures_root=captures,
        capture_id=outcome.capture_folder.name,
        fixtures_root=fixtures_root,
        name="mary-kelly",
        now=lambda: _CORR_DT,
    )
    assert dest == fixtures_root / "mary-kelly"
    assert (dest / "snapshot.html").is_file()

    loaded = load_fixture(dest)
    assert loaded.url == "https://www.findagrave.com/memorial/1"
    assert loaded.gating.value == "public"
    assert loaded.expected.record_type == "burial"
    # The gold is self-consistent: scored against itself it passes exactly.
    assert score("mary-kelly", loaded.expected, loaded.expected).ok
    # And it carries the un-corrected model facts (no correction applied yet).
    # Assertions reference their persona by id now, so resolve id -> label.
    ref_to_label = {p.id: p.label for p in loaded.expected.personas}
    values = {
        (ref_to_label.get(a.persona_id), a.field, a.value) for a in loaded.expected.assertions
    }
    assert ("Mary Kelly", "death_date", "3 March 1881") in values
    assert ("Mary Kelly", "burial_place", "Glasnevin Cemetery, Dublin") in values


def test_fixture_reflects_corrections(tmp_path):
    outcome, repo, captures = _run_html(tmp_path)
    sid, cid = outcome.source.id, outcome.capture_folder.name
    current = {a.field: a for a in repo.current_assertions(sid)}

    # Edit the death date, retract the burial place, add the mother.
    apply_correction(
        CorrectionRequest(
            capture_id=cid, action=CorrectionAction.EDIT,
            assertion_id=current["death_date"].id, new_value="13 March 1881",
        ),
        repository=repo, captures_root=captures, now=lambda: _CORR_DT, new_id=_ids("e"),
    )
    apply_correction(
        CorrectionRequest(
            capture_id=cid, action=CorrectionAction.RETRACT,
            assertion_id=current["burial_place"].id,
        ),
        repository=repo, captures_root=captures, now=lambda: _CORR_DT, new_id=_ids("r"),
    )
    apply_correction(
        CorrectionRequest(
            capture_id=cid, action=CorrectionAction.ADD,
            persona_id=current["death_date"].persona_id, field="mother_name",
            new_value="Bridget Kelly", evidence_type=EvidenceType.STATED,
            snippet_text="Daughter of Bridget Kelly",
        ),
        repository=repo, captures_root=captures, now=lambda: _CORR_DT, new_id=_ids("a"),
    )

    dest = write_fixture(
        repository=repo, captures_root=captures, capture_id=cid,
        fixtures_root=tmp_path / "fixtures", name="corrected", now=lambda: _CORR_DT,
    )
    loaded = load_fixture(dest)
    facts = {(a.field, a.value) for a in loaded.expected.assertions}
    assert ("death_date", "13 March 1881") in facts  # edited value is the gold
    assert ("mother_name", "Bridget Kelly") in facts  # added fact is the gold
    assert not any(f == "burial_place" for f, _ in facts)  # retracted fact is gone
    # The added fact's snippet is the human-supplied text.
    (mother,) = [a for a in loaded.expected.assertions if a.field == "mother_name"]
    assert mother.snippet == "Daughter of Bridget Kelly"
    assert score("corrected", loaded.expected, loaded.expected).ok


def test_snapshot_bytes_are_copied_verbatim(tmp_path):
    outcome, repo, captures = _run_html(tmp_path)
    dest = write_fixture(
        repository=repo, captures_root=captures, capture_id=outcome.capture_folder.name,
        fixtures_root=tmp_path / "fixtures", name="mk",
    )
    assert (dest / "snapshot.html").read_bytes() == (
        outcome.capture_folder / "snapshot.html"
    ).read_bytes()


def test_meta_carries_capture_provenance(tmp_path):
    outcome, repo, captures = _run_html(tmp_path)
    dest = write_fixture(
        repository=repo, captures_root=captures, capture_id=outcome.capture_folder.name,
        fixtures_root=tmp_path / "fixtures", name="mk", now=lambda: _CORR_DT,
    )
    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert meta["capture_id"] == outcome.capture_folder.name
    assert meta["source"] == "auto-generated from corrected capture"
    assert meta["generated_at"].endswith("Z")


def test_document_capture_cannot_seed_a_fixture(tmp_path):
    """A document capture has no snapshot.html and no assertions — reject it."""
    from capsule.pipeline.pipeline import run_document_capture
    from capsule.store.repository import Repository

    repo = Repository(tmp_path / "capsule.db")
    captures = tmp_path / "captures"
    outcome = run_document_capture(
        "https://example.org/newsletter.pdf",
        document_bytes=b"%PDF-1.4\n1 0 obj\n%%EOF",
        ext="pdf",
        media_type="application/pdf",
        repository=repo,
        captures_root=captures,
        now=lambda: _DT,
        new_id=_ids("id"),
    )
    with pytest.raises(ValueError, match="snapshot"):
        write_fixture(
            repository=repo, captures_root=captures,
            capture_id=outcome.capture_folder.name, fixtures_root=tmp_path / "fixtures",
        )


@pytest.mark.parametrize("bad", ["../evil", "a/b", ".", "with space", "..", "a\nb"])
def test_unsafe_fixture_name_rejected(tmp_path, bad):
    outcome, repo, captures = _run_html(tmp_path)
    with pytest.raises(ValueError):
        write_fixture(
            repository=repo, captures_root=captures,
            capture_id=outcome.capture_folder.name, fixtures_root=tmp_path / "fixtures",
            name=bad,
        )
