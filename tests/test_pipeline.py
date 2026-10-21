"""Tests for the capture pipeline — capsule/pipeline/.

Increment 2 (M0). Runs FULLY OFFLINE: a FakeFetcher supplies the page and a
FakeTransport supplies the one extraction "model call", so there is zero
network traffic and zero API cost. The pipeline's job is to turn one page in
view into a verifiable capture folder + append-only evidence rows, going
through exactly one Agent call and reading only that single page.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from capsule.contracts.evidence import EvidenceType, GatingStatus, SourceQuality

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)

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


def _ids():
    """Deterministic monotonic id factory: id-1, id-2, ..."""
    n = 0

    def _next() -> str:
        nonlocal n
        n += 1
        return f"id-{n}"

    return _next


def _snapshot(*, gating=GatingStatus.PUBLIC, url="https://www.findagrave.com/memorial/1"):
    from capsule.pipeline.fetch import PageSnapshot

    return PageSnapshot(
        url=url,
        html=_HTML,
        gating=gating,
        fetched_at=_DT,
        title="Mary Kelly (1810-1881)",
        site_name="Find a Grave",
    )


# -- fetch layer -----------------------------------------------------------


def test_fake_fetcher_records_the_requested_url():
    from capsule.pipeline.fetch import FakeFetcher

    fetcher = FakeFetcher(_snapshot())
    snap = fetcher.fetch("https://www.findagrave.com/memorial/1")
    assert fetcher.calls == ["https://www.findagrave.com/memorial/1"]
    assert snap.html == _HTML
    assert snap.gating is GatingStatus.PUBLIC


# -- extraction prompt + parsing -------------------------------------------


def test_extraction_prompt_carries_compliance_instructions():
    from capsule.pipeline.extract import build_extraction_prompt

    prompt = build_extraction_prompt(_snapshot())
    # The page in view is handed to the model; the transcription rules are stated.
    assert "https://www.findagrave.com/memorial/1" in prompt.user
    assert _HTML in prompt.user
    lowered = prompt.system.lower()
    assert "verbatim" in lowered
    assert "[?]" in prompt.system  # illegible marker is sacred
    assert "json" in lowered
    # Prompt-injection guard: the page is data, not instructions (#7).
    assert "data to analyze" in lowered
    assert "never instructions" in lowered
    # Richer uncertainty markers (#2) and the pre-output self-check.
    assert "[illegible]" in prompt.system
    assert "[torn]" in prompt.system
    assert "before returning" in lowered


def test_parse_extraction_strips_markdown_fences():
    from capsule.pipeline.extract import parse_extraction

    fenced = "```json\n" + json.dumps(_RAW) + "\n```"
    raw = parse_extraction(fenced)
    assert raw.record_type == "burial"
    assert raw.personas[0].label == "Mary Kelly"
    assert raw.assertions[0].evidence_type is EvidenceType.STATED


def test_parse_extraction_rejects_non_json():
    from capsule.pipeline.extract import parse_extraction

    with pytest.raises(ValueError):
        parse_extraction("I could not find any records on this page.")


def test_parse_extraction_rejects_unknown_evidence_type():
    from capsule.pipeline.extract import parse_extraction

    bad = json.loads(json.dumps(_RAW))
    bad["assertions"][0]["evidence_type"] = "guessed"
    with pytest.raises(ValueError):
        parse_extraction(json.dumps(bad))


# The verbatim reply that 422'd the NLI parish-register capture on 2026-07-09,
# recovered from data/captures/_failed/. The object ends at char 75; json.loads
# then hit the note at line 7, column 1 -> "Extra data: line 7 column 1 (char 76)".
_NLI_REPLY_WITH_TRAILING_PROSE = (
    '{\n  "record_type": "register_page",\n  "personas": [],\n  "assertions": []\n}\n'
    "\nNote: This page displays only the archived register viewer and metadata for "
    "the Islandeady (County Mayo) marriage register (Microfilm 04212/02). The actual "
    "page image is a scanned document served as an image; no transcribable "
    "genealogical text is present.\n"
)


def test_BUG_422_extraction_reply_with_trailing_prose():
    """Regression: prose after the JSON object must not fail the capture.

    An unindexed scan yields an empty extraction and an explanation. Every NLI
    parish-register page does this, so it is the normal result for that corpus,
    not an edge case.
    """
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction(_NLI_REPLY_WITH_TRAILING_PROSE)
    assert raw.record_type == "register_page"
    assert raw.personas == []
    assert raw.assertions == []


def test_BUG_422_trailing_prose_is_logged_not_silently_dropped(caplog):
    """Tolerant, but never silent — discarded prose is a prompt smell."""
    from capsule.pipeline.extract import parse_extraction

    with caplog.at_level("WARNING"):
        parse_extraction(_NLI_REPLY_WITH_TRAILING_PROSE)
    assert any("prose after the JSON object" in r.getMessage() for r in caplog.records)


def test_parse_extraction_tolerates_prose_before_the_object():
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction("Here is the extraction:\n\n" + json.dumps(_RAW))
    assert raw.record_type == "burial"


def test_parse_extraction_accepts_the_notes_field():
    """The sanctioned home for what the model would otherwise say as prose."""
    from capsule.pipeline.extract import parse_extraction

    payload = json.loads(json.dumps(_RAW))
    payload["notes"] = "Scanned image; no transcribable text."
    raw = parse_extraction(json.dumps(payload))
    assert raw.notes == "Scanned image; no transcribable text."


def test_parse_extraction_rejects_a_json_value_that_is_not_an_object():
    """A bare array (or any non-object) has no `{` and is refused, not coerced."""
    from capsule.pipeline.extract import parse_extraction

    with pytest.raises(ValueError, match="no JSON object found"):
        parse_extraction("[1, 2, 3]")


def test_empty_extraction_builds_a_capture_with_no_assertions():
    """A scan is a legitimate source: snapshot + citation, zero assertions."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.pipeline.fetch import PageSnapshot

    raw = parse_extraction(_NLI_REPLY_WITH_TRAILING_PROSE)
    snapshot = PageSnapshot(
        url="https://registers.nli.ie/registers/vtls000632043#page/26/mode/1up",
        html="<html><body>viewer</body></html>",
        gating=GatingStatus.PUBLIC,
        fetched_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
    )
    counter = iter(f"{i:032x}" for i in range(50))
    built = build_capture_models(
        snapshot,
        raw,
        new_id=lambda: next(counter),
        now=lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
    )
    assert built.extraction.personas == ()
    assert built.extraction.assertions == ()
    assert built.source.record_type == "register_page"
    # The URL fragment identifies the leaf — it is evidence, not decoration.
    assert built.source.url.endswith("#page/26/mode/1up")


# -- payload trim: cheaper prompt, identical record ------------------------

# Shaped like the real RootsIreland marriage record: a positional husband/wife
# table, the microfilm link buried inside `class="noPrint"`, and the usual
# script/style/svg/attribute freight.
_PAGE_WITH_FREIGHT = """
<html><head><title>Church Marriage Record</title>
<link rel="stylesheet" href="/common.css"><meta name="keywords" content="genealogy">
<style>.record-detail1{font-weight:bold}</style>
<script>function blurLink(e){e.blur()}</script></head>
<body id="viewdetailmarriagePage">
<!-- Google Tag Manager -->
<svg viewBox="0 0 10 10"><path d="M0 0"/></svg>
<div class="welcome"><a href="/myaccount.php">Welcome, Subscriber</a></div>
<table id="record-table-id">
  <tr><td>Date of Marriage:</td><td class="record-detail1" colspan="2">29-Apr-1852</td></tr>
  <tr><td></td><th colspan="2">Husband</th><td></td><th colspan="2">Wife</th></tr>
  <tr><td>Name:</td>
      <td class="record-detail1">James</td><td class="record-detail1">Kilcoin</td><td></td>
      <td class="record-detail1">Bridget</td><td class="record-detail1">Brady</td></tr>
</table>
<div class="noPrint"><!-- MICROFILM_LINK -->
  <a href="https://registers.nli.ie/registers/vtls000632043" target="micro-link"
     onclick="track()" class="button-top2">View Microfilm of Parish Register</a>
</div>
<img src="https://blob.example/scan.jpg?sig=SECRET" alt="scan of the register">
</body></html>
"""


def test_trim_preserves_every_visible_character():
    """The invariant the trim exists to hold: no record text is ever dropped."""
    from capsule.pipeline.trim import trim_for_extraction, visible_text

    assert visible_text(trim_for_extraction(_PAGE_WITH_FREIGHT)) == visible_text(
        _PAGE_WITH_FREIGHT
    )


def test_trim_keeps_hrefs_because_external_refs_depend_on_them():
    """A text-only trim would silently destroy the RootsIreland->NLI link.

    The microfilm anchor sits inside `class="noPrint"`, so a chrome-stripping
    filter keyed on that class would delete it too.
    """
    from capsule.pipeline.trim import trim_for_extraction

    trimmed = trim_for_extraction(_PAGE_WITH_FREIGHT)
    assert 'href="https://registers.nli.ie/registers/vtls000632043"' in trimmed
    assert "View Microfilm of Parish Register" in trimmed


def test_trim_keeps_table_structure_so_columns_do_not_merge():
    """Husband and wife are distinguished only by cell position on this record."""
    from capsule.pipeline.trim import trim_for_extraction

    trimmed = trim_for_extraction(_PAGE_WITH_FREIGHT)
    assert '<th colspan="2">Husband</th>' in trimmed
    assert "<td>James</td><td>Kilcoin</td>" in trimmed.replace(" <", "<")
    assert "<td>Bridget</td><td>Brady</td>" in trimmed.replace(" <", "<")


def test_trim_drops_script_style_svg_and_layout_attributes():
    from capsule.pipeline.trim import trim_for_extraction

    trimmed = trim_for_extraction(_PAGE_WITH_FREIGHT)
    for gone in ("blurLink", "font-weight", "<svg", "<path", "<style", "<script",
                 "rel=", "stylesheet", "onclick", 'class="', "Google Tag Manager"):
        assert gone not in trimmed, gone
    assert "<title>Church Marriage Record</title>" in trimmed


def test_trim_drops_src_so_a_signed_media_url_is_never_sent_to_the_api():
    """The NYC certificate's src is an Azure SAS link — a self-authorizing secret."""
    from capsule.pipeline.trim import trim_for_extraction

    trimmed = trim_for_extraction(_PAGE_WITH_FREIGHT)
    assert "sig=SECRET" not in trimmed
    assert 'alt="scan of the register"' in trimmed  # the model still sees it exists


def test_trim_materially_shrinks_a_real_page():
    from capsule.pipeline.trim import trim_for_extraction

    trimmed = trim_for_extraction(_PAGE_WITH_FREIGHT)
    assert len(trimmed) < len(_PAGE_WITH_FREIGHT) * 0.6


def test_trim_does_not_remove_account_chrome():
    """Known limitation, asserted so it is not mistaken for solved.

    `Welcome, Subscriber` is a text node, not an attribute — the trim keeps every
    text node by design. Gated-snapshot PII is a separate problem (docs/backlog.md).
    """
    from capsule.pipeline.trim import trim_for_extraction

    assert "Welcome, Subscriber" in trim_for_extraction(_PAGE_WITH_FREIGHT)


def test_build_extraction_prompt_sends_the_trimmed_html():
    from capsule.pipeline.extract import build_extraction_prompt
    from capsule.pipeline.fetch import PageSnapshot

    snapshot = PageSnapshot(
        url="https://rootsireland.ie/ifhf/view_detail.php?recordid=643376",
        html=_PAGE_WITH_FREIGHT,
        gating=GatingStatus.GATED,
        fetched_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
    )
    prompt = build_extraction_prompt(snapshot)
    assert "blurLink" not in prompt.user  # trimmed
    assert "29-Apr-1852" in prompt.user  # record intact
    assert "registers.nli.ie/registers/vtls000632043" in prompt.user  # ref intact
    assert snapshot.html == _PAGE_WITH_FREIGHT  # the archived snapshot is untouched


# -- credentials never reach the API or the evidence store -----------------

# The real shape of the NYC certificate's blob link (signature value redacted).
_SAS_URL = (
    "https://dorisarchive.blob.core.windows.net/reference/REC0051_VitalRecords/"
    "MarriageCertificate/M-K-1873-000161A.pdf"
    "?sv=2021-08-06&se=2026-07-09T20%3A00%3A00Z&sr=b&sp=r&sig=REDACTEDSIGNATURE"
)


def test_BUG_sas_signature_is_stripped_from_a_pre_signed_url():
    """Regression: every NYC capture wrote an Azure SAS credential to disk.

    A signature parameter means the whole query is one credential — expiry,
    permissions and key id included — so the query goes, not just `sig`.
    """
    from capsule.pipeline.urlsafe import strip_credential_params

    cleaned = strip_credential_params(_SAS_URL)
    assert "sig=" not in cleaned and "se=" not in cleaned and "sp=" not in cleaned
    assert cleaned.endswith("M-K-1873-000161A.pdf")
    assert cleaned.startswith("https://dorisarchive.blob.core.windows.net/reference/")


def test_strip_credential_params_keeps_a_record_identifier():
    """`?recordid=643376` is the record, not a secret. Never destroy it."""
    from capsule.pipeline.urlsafe import strip_credential_params

    url = "https://rootsireland.ie/ifhf/view_detail.php?recordid=643376&type=mch"
    assert strip_credential_params(url) == url


def test_strip_credential_params_removes_only_named_secrets_when_unsigned():
    from capsule.pipeline.urlsafe import strip_credential_params

    cleaned = strip_credential_params("https://x.test/r?id=7&access_token=abc&page=2")
    assert "access_token" not in cleaned
    assert "id=7" in cleaned and "page=2" in cleaned


def test_strip_credential_params_preserves_path_and_fragment():
    from capsule.pipeline.urlsafe import strip_credential_params

    url = "https://registers.nli.ie/registers/vtls000632043#page/26/mode/1up"
    assert strip_credential_params(url) == url


def test_BUG_a_signed_url_never_lands_in_an_external_ref():
    from capsule.pipeline.external_ref import canonicalize

    ref = canonicalize(_SAS_URL, "Certificate")
    assert ref is not None
    assert "sig=" not in ref.url
    assert ref.authority == "dorisarchive.blob.core.windows.net"


def test_BUG_a_signed_href_is_never_transmitted_to_the_model():
    """The trim keeps hrefs for external_refs; it must not keep the signature."""
    from capsule.pipeline.trim import trim_for_extraction

    trimmed = trim_for_extraction(f'<a href="{_SAS_URL}">Certificate</a>')
    assert "REDACTEDSIGNATURE" not in trimmed
    assert "M-K-1873-000161A.pdf" in trimmed


# -- gating detection: a logout link proves you were logged in -------------


def test_BUG_paywalled_page_with_a_logout_link_is_gated_not_public():
    """Regression: a RootsIreland record auto-detected PUBLIC.

    It has no login link (you are already in), a `logout.php` link, and an
    explicit copyright notice. URL-only inference called it public — one deferred
    feature away from submitting a copyrighted record to Wayback.
    """
    from capsule.pipeline.fetch import detect_gating

    html = (
        "<div class='welcome'><a href='/ifhf/myaccount.php'>Welcome, Subscriber</a>"
        "<a href='/ifhf/mysubscription.php'>Subscription until :1st January 2030</a></div>"
        "<a href='/ifhf/logout.php'>Log Out</a>"
        "<td class='copyright1'>&copy; 2026 Copyright Example Heritage Centre</td>"
    )
    url = "https://rootsireland.ie/ifhf/viewdetailmarriage.php"
    assert detect_gating(url, html) is GatingStatus.GATED


def test_a_login_link_alone_does_not_gate_a_public_page():
    """The asymmetry: a login link means you are NOT logged in. Public pages have one."""
    from capsule.pipeline.fetch import detect_gating

    html = "<nav><a href='/login'>Log in</a><a href='/signin'>Sign in</a></nav><p>Free record</p>"
    url = "https://registers.nli.ie/registers/vtls000632043"
    assert detect_gating(url, html) is GatingStatus.PUBLIC


def test_auth_markers_are_word_bounded():
    """`blog output` contains the substring `log out` and must not gate the page."""
    from capsule.pipeline.fetch import detect_gating

    benign = "<p>the blog output was fine</p>"
    assert detect_gating("https://x.test/a", benign) is GatingStatus.PUBLIC
    for marker in ("Log Out", "logout", "Sign out", "log-out", "My Subscription"):
        assert detect_gating("https://x.test/a", f"<a>{marker}</a>") is GatingStatus.GATED


def test_a_login_url_still_gates_regardless_of_body():
    from capsule.pipeline.fetch import detect_gating

    assert detect_gating("https://x.test/account/signin", "") is GatingStatus.GATED


def test_provided_content_fetcher_infers_gating_when_the_caller_declares_none():
    """The browser path is where gated pages arrive; defaulting to PUBLIC was the bug."""
    from capsule.pipeline.fetch import ProvidedContentFetcher

    gated = ProvidedContentFetcher("<a href='/logout.php'>Log Out</a>").fetch("https://x.test/r")
    assert gated.gating is GatingStatus.GATED

    public = ProvidedContentFetcher("<p>a free record</p>").fetch("https://x.test/r")
    assert public.gating is GatingStatus.PUBLIC


def test_an_explicit_gating_declaration_still_wins():
    from capsule.pipeline.fetch import ProvidedContentFetcher

    snap = ProvidedContentFetcher("<p>free</p>", gating=GatingStatus.GATED).fetch("https://x/r")
    assert snap.gating is GatingStatus.GATED


# -- external refs: the model finds, the canonicalizer normalizes ----------

_NLI_REGISTER = "https://registers.nli.ie/registers/vtls000632043"


def test_canonicalize_nli_register_link_is_document_level_not_page_level():
    """The RootsIreland link carries no #page/NN — so `locator` must stay None.

    This is the load-bearing property of the RootsIreland<->NLI linkage: the index
    entry states *which register*, never *which leaf*. Inventing a locator here
    would silently promote an inference to a stated fact.
    """
    from capsule.pipeline.external_ref import canonicalize

    ref = canonicalize(_NLI_REGISTER, "View Microfilm of Parish Register")
    assert ref is not None
    assert (ref.authority, ref.identifier) == ("nli-registers", "vtls000632043")
    assert ref.locator is None
    assert ref.label == "View Microfilm of Parish Register"
    assert ref.url == _NLI_REGISTER


def test_canonicalize_keeps_a_leaf_locator_when_the_url_actually_has_one():
    from capsule.pipeline.external_ref import canonicalize

    ref = canonicalize(_NLI_REGISTER + "#page/26/mode/1up")
    assert ref is not None
    assert ref.locator == "page/26"


def test_canonicalize_falls_back_to_host_and_path_for_unknown_authorities():
    from capsule.pipeline.external_ref import canonicalize

    ref = canonicalize("https://www.findagrave.com/memorial/12345")
    assert ref is not None
    assert (ref.authority, ref.identifier) == ("findagrave.com", "memorial/12345")


def test_canonicalize_refuses_unusable_urls():
    from capsule.pipeline.external_ref import canonicalize

    assert canonicalize("javascript:alert(1)") is None
    assert canonicalize("mailto:enquiries@rootsireland.ie") is None
    assert canonicalize("https://rootsireland.ie") is None  # a bare host cites nothing
    assert canonicalize("not a url") is None


def test_canonicalize_all_dedupes_the_same_register_linked_twice():
    """One page making a claim once must not look like it made it twice."""
    from capsule.pipeline.external_ref import canonicalize_all

    refs = canonicalize_all(
        [(_NLI_REGISTER, "View Microfilm"), (_NLI_REGISTER + "/", "Microfilm"), ("#top", None)]
    )
    assert len(refs) == 1


def test_external_refs_flow_from_the_reply_into_the_extraction():
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.pipeline.fetch import PageSnapshot

    payload = json.loads(json.dumps(_RAW))
    payload["external_refs"] = [
        {"url": _NLI_REGISTER, "label": "View Microfilm of Parish Register"}
    ]
    raw = parse_extraction(json.dumps(payload))

    snapshot = PageSnapshot(
        url="https://rootsireland.ie/ifhf/viewdetailmarriage.php",
        html="<html><body>Mary Kelly</body></html>",
        gating=GatingStatus.GATED,
        fetched_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
    )
    counter = iter(f"{i:032x}" for i in range(50))
    built = build_capture_models(
        snapshot,
        raw,
        new_id=lambda: next(counter),
        now=lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
    )
    assert len(built.extraction.external_refs) == 1
    assert built.extraction.external_refs[0].identifier == "vtls000632043"
    assert built.extraction.external_refs[0].locator is None


def test_serialize_extraction_omits_external_refs_when_empty():
    """Additive: a capture without refs hashes exactly as it did before the field."""
    from capsule.capture.manifest import Extraction, serialize_extraction

    assert b"external_refs" not in serialize_extraction(Extraction())


# -- citation builder (deterministic, no model call) -----------------------


def test_build_citation_layers_reference_short_and_list():
    from capsule.pipeline.citation import build_citation

    cite = build_citation(
        citation_id="cite-1",
        source_id="src-1",
        snapshot=_snapshot(),
        access_date=_DT.date(),
        archived_url="https://www.findagrave.com/memorial/1",
    )
    assert cite.source_id == "src-1"
    assert "Find a Grave" in cite.reference_note
    assert "Mary Kelly" in cite.reference_note
    assert "2026" in cite.reference_note  # access date rendered
    assert cite.short_note and cite.source_list_entry


def test_build_citation_generic_when_no_record_to_cite():
    """With no assertions (a document capture / unindexed scan) the citation falls
    back to the title-only generic web template."""
    from capsule.pipeline.citation import build_citation

    cite = build_citation(
        citation_id="cite-1",
        source_id="src-1",
        snapshot=_snapshot(),
        access_date=_DT.date(),
        archived_url="https://www.findagrave.com/memorial/1",
    )
    assert cite.template_id == "generic-web-v1"


def test_BUG_record_transcript_citation_names_record_not_page_title():
    """A record transcript must be cited by its RECORD — the layered EE form
    record -> database -> what the database cites — not the page's boilerplate
    <title>. Regression: every RootsIreland record cited identically as the site's
    title, naming neither the person nor the record."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.pipeline.fetch import PageSnapshot

    html = (
        "<html><head><title>Birth Death Marriage Records Ireland - IFHF</title></head>"
        "<body><h1>Church Baptism Record for: Mary Kilcoyne</h1>"
        "<table>"
        "<tr><td>Name:</td><td>Mary Kilcoyne</td></tr>"
        "<tr><td>Date of Baptism:</td><td>5-Jun-1853</td></tr>"
        "<tr><td>Parish / District:</td><td>Islandeady RC parish</td></tr>"
        "<tr><td>County:</td><td>Co. Mayo</td></tr>"
        '<tr><td>Father:</td><td>James Kilcoyne</td></tr>'
        '<a href="https://registers.nli.ie/registers/vtls000632042">'
        "View Microfilm of Parish Register</a>"
        "</table></body></html>"
    )
    raw_obj = {
        "record_type": "baptism",
        "personas": [
            {"id": "p1", "label": "Mary Kilcoyne", "role": "child"},
            {"id": "p2", "label": "James Kilcoyne", "role": "father"},
        ],
        "assertions": [
            {"persona_id": "p1", "field": "Name", "value": "Mary Kilcoyne",
             "evidence_type": "stated", "snippet": "Name: Mary Kilcoyne"},
            {"persona_id": "p1", "field": "Date of Baptism", "value": "5-Jun-1853",
             "evidence_type": "stated", "snippet": "Date of Baptism: 5-Jun-1853"},
            {"persona_id": "p1", "field": "Parish / District",
             "value": "Islandeady RC parish", "evidence_type": "stated",
             "snippet": "Parish / District: Islandeady RC parish"},
            {"persona_id": "p1", "field": "County", "value": "Co. Mayo",
             "evidence_type": "stated", "snippet": "County: Co. Mayo"},
            {"persona_id": "p2", "field": "Father", "value": "James Kilcoyne",
             "evidence_type": "stated", "snippet": "Father: James Kilcoyne"},
        ],
        "external_refs": [
            {"url": "https://registers.nli.ie/registers/vtls000632042",
             "label": "View Microfilm of Parish Register"}
        ],
    }
    snap = PageSnapshot(
        url="https://www.rootsireland.ie/ifhf/view_detail.php?recordid=1",
        html=html,
        gating=GatingStatus.GATED,
        fetched_at=_DT,
        title="Birth Death Marriage Records Ireland - IFHF",
        site_name="rootsireland.ie",
    )
    built = build_capture_models(
        snap, parse_extraction(json.dumps(raw_obj)), new_id=_ids(), now=lambda: _DT
    )
    note = built.citation.reference_note
    assert built.citation.template_id == "record-transcript-v1"
    # Layer 1 — the record: who, what, when, where (the child, not the father).
    assert "baptism of Mary Kilcoyne" in note
    assert "5-Jun-1853" in note
    assert "Islandeady RC parish" in note and "Co. Mayo" in note
    # Layer 2 — the database that served it.
    assert "rootsireland.ie" in note
    # Layer 3 — the original the database itself cites (the NLI register).
    assert "vtls000632042" in note
    # The old failure mode: the page-title boilerplate must NOT be the citation.
    assert "Birth Death Marriage" not in note


# -- model builder ---------------------------------------------------------


def test_build_capture_models_links_everything_to_one_source():
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction(json.dumps(_RAW))
    built = build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)

    src_id = built.source.id
    assert all(p.source_id == src_id for p in built.extraction.personas)
    assert all(s.source_id == src_id for s in built.extraction.snippets)
    assert all(a.source_id == src_id for a in built.extraction.assertions)
    # Every assertion is snippet- and persona-linked (the contract's core rule).
    persona_ids = {p.id for p in built.extraction.personas}
    snippet_ids = {s.id for s in built.extraction.snippets}
    for a in built.extraction.assertions:
        assert a.persona_id in persona_ids
        assert a.snippet_id in snippet_ids
    assert built.source.source_quality is SourceQuality.DERIVATIVE


def test_build_capture_models_content_hash_matches_serialized_extraction():
    from capsule.capture.manifest import serialize_extraction, sha256_hex
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction(json.dumps(_RAW))
    built = build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)
    assert built.source.content_hash == sha256_hex(serialize_extraction(built.extraction))
    assert built.source.archive.sha256 == sha256_hex(_HTML.encode("utf-8"))


def test_build_capture_models_retains_record_type():
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction(json.dumps(_RAW))
    built = build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)
    assert built.source.record_type == "burial"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Borough", "borough"),
        ("Date of Marriage", "date_of_marriage"),
        ("record_type", "record_type"),
        ("Certificate #", "certificate"),
        ("  Age at Death  ", "age_at_death"),
        ("Name:", "name"),
        ("SOUNDEX", "soundex"),
        ("", ""),
        ("???", ""),
    ],
)
def test_snake_case_folds_printed_labels(label, expected):
    """Typography is code's problem; meaning is the model's."""
    from capsule.pipeline.fieldname import snake_case

    assert snake_case(label) == expected


def test_build_snake_cases_the_printed_field_label():
    """The model copies the record's label; code folds it to a stable identifier.

    Without this, two captures of the same register disagree on `Date of Marriage`
    vs `date_of_marriage` purely by how each page was styled, and the M2 matcher —
    which compares assertions across sources BY FIELD NAME — silently misses them.
    """
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw_data = json.loads(json.dumps(_RAW))
    raw_data["assertions"][0]["field"] = "Date of Death"
    raw = parse_extraction(json.dumps(raw_data))
    built = build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)

    assert "date_of_death" in {a.field for a in built.extraction.assertions}


def test_build_passes_normalized_field_through_untouched():
    """`normalized_field` is the model's judgment, not a transform we may redo.

    Code folds its casing (it is still an identifier) but must never supply,
    rewrite, or infer one — a human has to be able to see exactly which synonym
    the model chose, and disagree with it.
    """
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw_data = json.loads(json.dumps(_RAW))
    raw_data["assertions"][0]["field"] = "Type"
    raw_data["assertions"][0]["normalized_field"] = "Record Type"
    raw = parse_extraction(json.dumps(raw_data))
    built = build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)

    by_field = {a.field: a for a in built.extraction.assertions}
    assert by_field["type"].normalized_field == "record_type"
    # Every other assertion is left without one — build.py never invents a synonym.
    assert all(
        a.normalized_field is None for a in built.extraction.assertions if a.field != "type"
    )


@pytest.mark.parametrize("bad", ["???", "   ", "--"])
def test_build_rejects_a_field_label_with_no_alphanumerics(bad):
    """A label that folds to "" names no fact. Fail the capture loudly rather than
    store an assertion no query can find and no human can read; the raw reply
    survives in `agent_log/` regardless."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw_data = json.loads(json.dumps(_RAW))
    raw_data["assertions"][0]["field"] = bad
    raw = parse_extraction(json.dumps(raw_data))
    with pytest.raises(ValueError, match="alphanumeric"):
        build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)


def test_build_flags_assertion_whose_snippet_is_not_on_the_page():
    """A snippet the model claims is verbatim but isn't on the page is flagged,
    not dropped — and a genuine snippet is left clean."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    tampered = json.loads(json.dumps(_RAW))
    tampered["assertions"][0]["snippet"] = "Died in a duel with Aaron Burr in 1804."
    raw = parse_extraction(json.dumps(tampered))
    built = build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)

    by_field = {a.field: a for a in built.extraction.assertions}
    assert "[unverified" in (by_field["death_date"].annotation or "")
    # The burial snippet really is on the page, so it stays unflagged.
    assert by_field["burial_place"].annotation is None
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction(json.dumps(_RAW))
    built = build_capture_models(
        _snapshot(gating=GatingStatus.GATED), raw, new_id=_ids(), now=lambda: _DT
    )
    assert built.source.gating_status is GatingStatus.GATED
    assert built.source.copyright_flag is True
    assert built.source.archive.wayback_url is None


def test_BUG_soft_flag_matches_snippet_across_markup_and_whitespace():
    """Real NYC DOM `<b>Name</b>: Mary Doyle` must VERIFY the snippet
    'Name: Mary Doyle' — tag/whitespace around punctuation must not falsely
    flag it unverified (regression from the first real browser capture)."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.pipeline.fetch import PageSnapshot

    html = (
        "<html><body>"
        "<p><b>Name</b>: Mary Doyle</p>"
        "<p><b>Soundex</b>: L420</p>"
        "<p><b>Date</b>: 1903-01-19</p>"
        "</body></html>"
    )
    raw_obj = {
        "record_type": "death",
        "personas": [{"label": "Mary Doyle", "role": "deceased"}],
        "assertions": [
            {"persona": "Mary Doyle", "field": "name", "value": "Mary Doyle",
             "evidence_type": "stated", "snippet": "Name: Mary Doyle"},
            {"persona": "Mary Doyle", "field": "soundex", "value": "L420",
             "evidence_type": "stated", "snippet": "Soundex: L420"},
            {"persona": "Mary Doyle", "field": "death_date", "value": "1903-01-19",
             "evidence_type": "stated", "snippet": "Date: 1903-01-19"},
        ],
    }
    snap = PageSnapshot(
        url="https://a860-historicalvitalrecords.nyc.gov/view/4017734",
        html=html,
        gating=GatingStatus.PUBLIC,
        fetched_at=_DT,
    )
    built = build_capture_models(
        snap, parse_extraction(json.dumps(raw_obj)), new_id=_ids(), now=lambda: _DT
    )
    assert all(a.annotation is None for a in built.extraction.assertions)


def test_BUG_snippet_split_across_table_cells_locates_per_cell():
    """A name printed across separate <td> cells under a shared label must VERIFY.

    Regression: real RootsIreland marriage record — a 6-column table with a single
    `Name:` label cell, then groom given|surname, a spacer, bride given|surname. The
    model's verbatim snippet `"Name: Sera McShane"` is not a contiguous run of page
    text (the bride's cells are two columns right of the label), so the old
    contiguous check flagged the wife, both fathers, and both witnesses `[unverified]`
    — every name but the first-column groom's. Anchored per cell they all verify."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.pipeline.fetch import PageSnapshot

    html = (
        "<html><body><table>"
        "<tr><td>Name:</td>"
        "<td>Pat</td><td>Doyle</td><td></td><td>Sera</td><td>McShane</td></tr>"
        "</table></body></html>"
    )
    raw_obj = {
        "record_type": "marriage",
        "personas": [
            {"id": "p1", "label": "Pat Doyle", "role": "husband"},
            {"id": "p2", "label": "Sera McShane", "role": "wife"},
        ],
        "assertions": [
            {"persona_id": "p1", "field": "name", "value": "Pat Doyle",
             "evidence_type": "stated", "snippet": "Name: Pat Doyle"},
            {"persona_id": "p2", "field": "name", "value": "Sera McShane",
             "evidence_type": "stated", "snippet": "Name: Sera McShane"},
        ],
    }
    snap = PageSnapshot(
        url="https://www.rootsireland.ie/ifhf/view_detail.php?recordid=1",
        html=html,
        gating=GatingStatus.GATED,
        fetched_at=_DT,
    )
    built = build_capture_models(
        snap, parse_extraction(json.dumps(raw_obj)), new_id=_ids(), now=lambda: _DT
    )
    # The bride's name is split across cells to the right of the label; it is real
    # evidence, not an unlocated snippet.
    assert all(a.annotation is None for a in built.extraction.assertions)


def test_BUG_build_rejects_assertion_referencing_unknown_persona():
    """An assertion whose persona label was never declared must fail fast."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    bad = json.loads(json.dumps(_RAW))
    bad["assertions"][0]["persona"] = "Somebody Else"
    raw = parse_extraction(json.dumps(bad))
    with pytest.raises(ValueError, match="persona"):
        build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)


def test_BUG_build_rejects_a_label_reference_to_a_non_unique_name():
    """A LABEL reference is ambiguous when two personas share the name — refuse it
    (and tell the caller to use ids), rather than silently binding to the first.

    Regression for the DAR/lineage bug: the old code rejected the whole capture the
    moment two labels matched; now labels may repeat, but a label-based *reference*
    to a repeated name is what's ambiguous.
    """
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    dup = json.loads(json.dumps(_RAW))  # assertions reference "Mary Kelly" by label
    dup["personas"].append({"label": "Mary Kelly", "role": "informant"})
    raw = parse_extraction(json.dumps(dup))
    with pytest.raises(ValueError, match="non-unique label"):
        build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)


def test_two_personas_with_the_same_label_capture_when_referenced_by_id():
    """The fix: a page naming two different people 'JOHN B.' captures cleanly when
    each persona has an id and assertions reference persona_id. Two distinct
    persona rows result — never merged."""
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction(json.dumps({
        "record_type": "lineage",
        "personas": [
            {"id": "p1", "label": "JOHN B.", "role": "ancestor"},
            {"id": "p2", "label": "JOHN B.", "role": "descendant"},
        ],
        "assertions": [
            {"persona_id": "p1", "field": "born", "value": "1750",
             "evidence_type": "stated", "snippet": "b. 1750"},
            {"persona_id": "p2", "field": "born", "value": "1789",
             "evidence_type": "stated", "snippet": "b. 1789"},
        ],
    }))
    built = build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)

    assert [p.label for p in built.extraction.personas] == ["JOHN B.", "JOHN B."]
    pids = {p.id for p in built.extraction.personas}
    assert len(pids) == 2  # two distinct personas, not merged
    # Each assertion binds to the right one: 1750 -> ancestor, 1789 -> descendant.
    by_pid = {p.id: p.role for p in built.extraction.personas}
    born = {by_pid[a.persona_id]: a.verbatim_value for a in built.extraction.assertions}
    assert born == {"ancestor": "1750", "descendant": "1789"}


def test_build_rejects_duplicate_persona_id():
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction(json.dumps({
        "record_type": "lineage",
        "personas": [
            {"id": "p1", "label": "A", "role": "x"},
            {"id": "p1", "label": "B", "role": "y"},
        ],
        "assertions": [
            {"persona_id": "p1", "field": "f", "value": "v",
             "evidence_type": "stated", "snippet": "s"},
        ],
    }))
    with pytest.raises(ValueError, match="duplicate persona id"):
        build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)


def test_build_rejects_assertion_referencing_unknown_persona_id():
    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction

    raw = parse_extraction(json.dumps({
        "record_type": "lineage",
        "personas": [{"id": "p1", "label": "A", "role": "x"}],
        "assertions": [
            {"persona_id": "p9", "field": "f", "value": "v",
             "evidence_type": "stated", "snippet": "s"},
        ],
    }))
    with pytest.raises(ValueError, match="unknown persona id"):
        build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)


def test_BUG_add_capture_is_atomic_on_failure(tmp_path):
    """A mid-write failure must roll the whole index back — no source without
    its assertions (the append-only 'no orphaned assertions' invariant)."""
    import sqlite3

    from capsule.pipeline.build import build_capture_models
    from capsule.pipeline.extract import parse_extraction
    from capsule.store.repository import Repository

    raw = parse_extraction(json.dumps(_RAW))
    built = build_capture_models(_snapshot(), raw, new_id=_ids(), now=lambda: _DT)
    # Corrupt one assertion's snippet FK so the LAST insert in the transaction
    # fails, after source/citation/persona/snippet rows have already been issued.
    bad = built.extraction.assertions[0].model_copy(update={"snippet_id": "ghost"})

    repo = Repository(tmp_path / "capsule.db")
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_capture(
            source=built.source,
            citation=built.citation,
            personas=built.extraction.personas,
            snippets=built.extraction.snippets,
            assertions=(bad,),
        )
    # Whole transaction rolled back: the source itself must not have committed.
    with pytest.raises(LookupError):
        repo.get_source(built.source.id)
    repo.close()


# -- end-to-end orchestration (offline) ------------------------------------


def _run(tmp_path, *, gating=GatingStatus.PUBLIC):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.pipeline.fetch import FakeFetcher
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    fetcher = FakeFetcher(_snapshot(gating=gating))
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    outcome = run_capture(
        "https://www.findagrave.com/memorial/1",
        fetcher=fetcher,
        agent=agent,
        repository=repo,
        captures_root=tmp_path / "captures",
        now=lambda: _DT,
        new_id=_ids(),
    )
    return outcome, fetcher, repo


def test_BUG_account_chrome_never_reaches_the_disk_or_the_model(tmp_path):
    """A gated page names the subscriber. That is not the record, and it must not be
    hashed into the archive, sent to the extraction model, or shipped in a capsule.

    Redaction happens between `fetcher.fetch` and `build_extraction_prompt`, so the
    prompt, `snapshot.html`, and `archive.sha256` all describe the same document —
    and the manifest declares the removal, so nothing pretends to be byte-true.
    """
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.capture.manifest import verify_capture
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    page = (
        '<html><body><a href="https://example.org/myaccount.php">Welcome, Jane Researcher</a>'
        '<a href="https://example.org/logout.php">Log Out</a>'
        f"{_HTML}</body></html>"
    )
    snapshot = PageSnapshot(
        url="https://www.findagrave.com/memorial/1",
        html=page,
        gating=GatingStatus.GATED,
        fetched_at=_DT,
        title="John Murphy",
        site_name="Find a Grave",
    )
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    outcome = run_capture(
        "https://www.findagrave.com/memorial/1",
        fetcher=FakeFetcher(snapshot),
        agent=agent,
        repository=repo,
        captures_root=tmp_path / "captures",
        now=lambda: _DT,
        new_id=_ids(),
    )
    repo.close()

    written = (outcome.capture_folder / "snapshot.html").read_text(encoding="utf-8")
    assert "Welcome, Jane Researcher" not in written
    # The prompt is what actually left the machine.
    assert "Welcome, Jane Researcher" not in outcome.agent_call.prompt
    # ...and the logout marker survives, so the archived page still evidences its own
    # gating (see tests/test_redact.py::test_BUG_a_logout_link_is_never_redacted).
    assert "Log Out" in written

    manifest = json.loads(
        (outcome.capture_folder / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["redactions"] == [
        {"reason": "account-chrome", "location": "https://example.org/myaccount.php"}
    ]
    # The hash attests the redacted bytes, and the capture declares why.
    assert verify_capture(outcome.capture_folder).ok


def test_run_capture_writes_a_verifiable_capture(tmp_path):
    from capsule.capture.manifest import verify_capture

    outcome, _, _ = _run(tmp_path)
    assert outcome.capture_folder.is_dir()
    assert (outcome.capture_folder / "snapshot.html").is_file()
    assert (outcome.capture_folder / "extraction.json").is_file()
    assert (outcome.capture_folder / "agent_log" / outcome.agent_call.log_filename).is_file()
    assert verify_capture(outcome.capture_folder).ok is True
    assert outcome.verify.ok is True


def test_run_capture_persists_appendonly_rows(tmp_path):
    outcome, _, repo = _run(tmp_path)
    stored = repo.get_source(outcome.source.id)
    assert stored.url == "https://www.findagrave.com/memorial/1"
    assert stored.record_type == "burial"  # survives the DB round-trip
    current = repo.current_assertions(outcome.source.id)
    assert {a.field for a in current} == {"death_date", "burial_place"}
    repo.close()


def test_run_capture_reads_only_the_page_in_view(tmp_path):
    """Compliance: exactly one fetch of the given URL, and one Agent call."""
    outcome, fetcher, repo = _run(tmp_path)
    assert fetcher.calls == ["https://www.findagrave.com/memorial/1"]
    assert outcome.agent_call.prompt  # the single extraction call happened
    repo.close()


def test_BUG_run_capture_records_paid_call_when_capture_fails_downstream(tmp_path):
    """A successful (paid) call whose capture fails to build must still hit _failed/."""
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.pipeline.fetch import FakeFetcher
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    fetcher = FakeFetcher(_snapshot())
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport("this is not JSON"),  # call succeeds, parse fails
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    captures = tmp_path / "captures"
    with pytest.raises(ValueError):
        run_capture(
            "https://www.findagrave.com/memorial/1",
            fetcher=fetcher,
            agent=agent,
            repository=repo,
            captures_root=captures,
            now=lambda: _DT,
            new_id=_ids(),
        )
    assert len(list((captures / "_failed").glob("*.json"))) == 1  # paid call preserved
    repo.close()


def test_run_capture_gated_page_stays_local_only(tmp_path):
    from capsule.capture.manifest import verify_capture

    outcome, _, repo = _run(tmp_path, gating=GatingStatus.GATED)
    assert outcome.source.gating_status is GatingStatus.GATED
    assert outcome.source.copyright_flag is True
    assert outcome.source.archive.wayback_url is None
    assert verify_capture(outcome.capture_folder).ok is True
    repo.close()


# ---------------------------------------------------------------------------
# P2 — an index's declared coverage, end to end
# ---------------------------------------------------------------------------

_COVERAGE_PAGE = (
    "<html><head><title>Kilmore Parish — Records Held</title></head><body>"
    "<h1>Kilmore Parish (Co. Cavan) — Records Held</h1>"
    "<table>"
    "<tr><td>R.C. Baptisms</td><td>1845-1880</td></tr>"
    "<tr><td>R.C. Marriages</td><td>1864-1899</td></tr>"
    "</table></body></html>"
)

_COVERAGE_RAW = {
    "record_type": "index-coverage",
    "personas": [],
    "assertions": [],
    "coverage": [
        {
            "place": "Kilmore",
            "record_type": "R.C. Baptisms",
            "year_start": 1845,
            "year_end": 1880,
            "snippet": "R.C. Baptisms 1845-1880",
            "evidence_type": "stated",
        },
        {
            "place": "Kilmore",
            "record_type": "R.C. Marriages",
            "year_start": 1864,
            "year_end": 1899,
            "snippet": "R.C. Marriages 1864-1899",
            "evidence_type": "stated",
        },
    ],
}


def _run_coverage(tmp_path):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    fetcher = FakeFetcher(
        PageSnapshot(
            url="https://www.rootsireland.ie/kilmore/coverage",
            html=_COVERAGE_PAGE,
            gating=GatingStatus.PUBLIC,
            fetched_at=_DT,
            title="Kilmore Parish — Records Held",
        )
    )
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(_COVERAGE_RAW)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    outcome = run_capture(
        "https://www.rootsireland.ie/kilmore/coverage",
        fetcher=fetcher,
        agent=agent,
        repository=repo,
        captures_root=tmp_path / "captures",
        now=lambda: _DT,
        new_id=_ids(),
    )
    return outcome, repo


def test_coverage_page_becomes_queryable_coverage_claims(tmp_path):
    """The payoff: capture the holdings table, then ask whether a year was covered.

    This is what turns a negative search into evidence. "I searched Kilmore baptisms
    for 1852 and found nothing" means nothing on its own; with the index's own
    declaration that it covers 1845-1880, the absence is real.
    """
    outcome, repo = _run_coverage(tmp_path)
    assert outcome.verify.ok

    # A holdings page has no people on it. That is a SUCCESSFUL extraction, not a
    # failure — and crucially it invents no personas to hang the coverage on.
    assert outcome.extraction.personas == ()
    assert outcome.extraction.assertions == ()

    claims = repo.coverage_for(place="kilmore", record_type="baptism", year=1852)
    assert len(claims) == 1
    assert claims[0].year_start == 1845 and claims[0].year_end == 1880
    # The verbatim declaration is one hop away, so the parsed years can be audited.
    assert repo.get_snippet(claims[0].snippet_id).verbatim_text == "R.C. Baptisms 1845-1880"

    # 1901 is past the end of the index: an absence there proves NOTHING.
    assert repo.coverage_for(place="kilmore", record_type="baptism", year=1901) == []
    # Marriages ran later than baptisms — the distinction the table exists to make.
    assert repo.coverage_for(place="kilmore", record_type="marriage", year=1890)


def test_coverage_is_written_to_the_capture_folder(tmp_path):
    """The claims live in the capture folder too, not only the DB — the folder is the
    archive of record, and it is hashed."""
    outcome, _repo = _run_coverage(tmp_path)
    extraction = json.loads(
        (outcome.capture_folder / "extraction.json").read_text(encoding="utf-8")
    )
    assert [c["record_type"] for c in extraction["coverage"]] == [
        "R.C. Baptisms",
        "R.C. Marriages",
    ]
    assert extraction["coverage"][0]["place"] == "Kilmore"


def test_a_record_page_declares_no_coverage(tmp_path):
    """A capture with no holdings table adds no coverage — and its extraction.json is
    byte-identical to before the field existed, so no existing content_hash moves."""
    outcome, _fetcher, repo = _run(tmp_path)
    assert outcome.extraction.coverage == ()
    assert repo.coverage_for() == []
    extraction = json.loads(
        (outcome.capture_folder / "extraction.json").read_text(encoding="utf-8")
    )
    assert "coverage" not in extraction


# ---------------------------------------------------------------------------
# source_quality — original vs derivative, earned not assumed
# ---------------------------------------------------------------------------


def _run_with_raw(tmp_path, raw: dict, html: str = "<p>Died 3 March 1881.</p>"):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.pipeline.fetch import FakeFetcher, PageSnapshot
    from capsule.pipeline.pipeline import run_capture
    from capsule.store.repository import Repository

    fetcher = FakeFetcher(
        PageSnapshot(
            url="https://registers.nli.ie/registers/vtls000632043",
            html=html,
            gating=GatingStatus.PUBLIC,
            fetched_at=_DT,
            title="Parish register",
        )
    )
    agent = Agent(
        name="extractor",
        model="claude-opus-4-8",
        transport=FakeTransport(json.dumps(raw)),
        now=lambda: _DT,
        new_id=lambda: "call-1",
    )
    repo = Repository(tmp_path / "capsule.db")
    outcome = run_capture(
        "https://registers.nli.ie/registers/vtls000632043",
        fetcher=fetcher,
        agent=agent,
        repository=repo,
        captures_root=tmp_path / "captures",
        now=lambda: _DT,
        new_id=_ids(),
    )
    return outcome, repo


def test_a_model_reported_original_is_recorded_as_original(tmp_path):
    """The NLI register scan IS the record. It must not be filed as a derivative."""
    from capsule.contracts.evidence import SourceQuality

    raw = dict(_RAW)
    raw["source_quality"] = "original"
    raw["source_quality_rationale"] = "A photographed page of the parish register itself."
    outcome, _repo = _run_with_raw(tmp_path, raw)

    assert outcome.source.source_quality is SourceQuality.ORIGINAL
    # The judgment is auditable: WHY it was called an original travels with it.
    assert "register" in (outcome.source.source_quality_rationale or "")


def test_a_model_reported_derivative_is_recorded_as_derivative(tmp_path):
    from capsule.contracts.evidence import SourceQuality

    raw = dict(_RAW)
    raw["source_quality"] = "derivative"
    raw["source_quality_rationale"] = "A transcription/index entry, not the register."
    outcome, _repo = _run_with_raw(tmp_path, raw)

    assert outcome.source.source_quality is SourceQuality.DERIVATIVE


def test_BUG_an_unsure_model_never_produces_an_original(tmp_path):
    """Regression: ORIGINAL is the STRONG claim and must be earned, never defaulted.

    A model that says nothing (or a legacy reply predating the field) must fall back to
    DERIVATIVE — the weaker, safer claim — exactly as gating errs GATED. Wrongly calling
    a source "original" corrupts M2 conflict resolution, which prefers originals;
    wrongly calling it derivative only understates what we hold.
    """
    from capsule.contracts.evidence import SourceQuality

    outcome, _repo = _run_with_raw(tmp_path, dict(_RAW))  # no source_quality key at all

    assert outcome.source.source_quality is SourceQuality.DERIVATIVE
    assert outcome.source.source_quality_rationale is None


def test_an_unrecognized_source_quality_falls_back_to_derivative(tmp_path):
    """Garbage in the field is 'unsure', not a crash and not an upgrade."""
    from capsule.contracts.evidence import SourceQuality

    raw = dict(_RAW)
    raw["source_quality"] = "probably the original?"
    outcome, _repo = _run_with_raw(tmp_path, raw)

    assert outcome.source.source_quality is SourceQuality.DERIVATIVE


# ---------------------------------------------------------------------------
# The model's `notes` — why a capture has no assertions (was discarded)
# ---------------------------------------------------------------------------


def test_extraction_notes_survive_to_the_capture_folder(tmp_path):
    """Regression: the model's explanation was thrown away.

    Every NLI register page extracts to ZERO assertions, and the model says exactly
    why: "the page image is a scanned document; no transcribable genealogical text is
    present." That sentence is the difference between "this source holds nothing" and
    "this source holds a scan we cannot read yet" — and it only survived in agent_log/,
    never reaching extraction.json or the index, which showed a generic
    "No structured assertions extracted yet" instead.
    """
    raw = json.loads(json.dumps(_RAW))
    raw["personas"] = []
    raw["assertions"] = []
    raw["notes"] = "The page image is a scanned document; no transcribable text."

    outcome, _repo = _run_with_raw(tmp_path, raw)

    assert outcome.extraction.assertions == ()
    assert outcome.extraction.notes == raw["notes"]

    on_disk = json.loads(
        (outcome.capture_folder / "extraction.json").read_text(encoding="utf-8")
    )
    assert on_disk["notes"] == raw["notes"]
    # INTEGRITY is what this test is about: the note rides inside the hashed extraction,
    # not beside it, so nothing is missing, mismatched or unlisted.
    #
    # Not `verify.ok`. This fixture is an NLI register page — zero assertions and no image
    # attached — which REQ-007 §3.3 now correctly fails for sufficiency: a register whose
    # scan we did not capture archives no record. That verdict is the subject of
    # tests/test_req_007_media_sufficiency.py; asserting it here would couple a note test
    # to an unrelated gate and would have to be re-litigated every time the gate moves.
    assert outcome.verify.missing == ()
    assert outcome.verify.mismatched == ()
    assert outcome.verify.extra == ()


def test_a_capture_without_notes_is_byte_identical_to_before(tmp_path):
    """Additive: `notes` is omitted when absent, so no existing content_hash moves."""
    outcome, _repo = _run_with_raw(tmp_path, dict(_RAW))
    on_disk = json.loads(
        (outcome.capture_folder / "extraction.json").read_text(encoding="utf-8")
    )
    assert "notes" not in on_disk
    assert outcome.extraction.notes is None
