"""Tests for capsule/pipeline/redact.py — the account-chrome redactor.

The page shape here mirrors the real RootsIreland header (including its malformed
`<\\div>` closing tag), because that is the page this module exists for. Nothing here
touches a real capture; the name below is a synthetic literal.
"""
from __future__ import annotations

import pytest

from capsule.capture.manifest import REDACTION_MARKER
from capsule.pipeline.redact import redact_account_chrome

# The account header as RootsIreland actually renders it, malformed tag and all.
_HEADER = """\
<div class="noPrint"><div id="header_right">
<div class="welcome"><a href="https://example.org/ifhf/myaccount.php" \
title="Update Personal Details">Welcome, Jane Researcher</a>
<a href="https://example.org/ifhf/mysubscription.php" title="My Subscription" \
class="subscription">Subscription until :1st January 2030</a>
<\\div><a href="https://example.org/ifhf/subscribe-confirm.php?renew=1">Renew</a>
<a href="https://example.org/ifhf/logout.php" title="Log Out">Log Out</a>
</div></div>
"""

# The record itself, plus the microfilm link that becomes an external_ref.
_RECORD = """\
<div id="birth-record">
<h1>Church Baptism Record for: Robert Euen</h1>
<table><tr><td>Date of Birth</td><td>29-Apr-1852</td></tr></table>
<div class="noPrint"><!-- MICROFILM_LINK -->
<a href="https://registers.nli.ie/registers/vtls000633924">View Microfilm of Parish Register</a>
</div></div>
"""

_PAGE = _HEADER + _RECORD


def test_account_name_and_subscription_status_are_removed():
    out, redactions = redact_account_chrome(_PAGE)
    assert "Welcome, Jane Researcher" not in out
    assert "Subscription until" not in out
    assert out.count(REDACTION_MARKER) == 2
    assert {r.reason for r in redactions} == {"account-chrome"}
    assert len(redactions) == 2


def test_BUG_a_logout_link_is_never_redacted():
    """Regression: `detect_gating` proves a page was paywalled by finding "Log Out"
    in its body. Blanking that text would leave `snapshot.html` unable to evidence
    its own gating — anything re-reading it would call a paywalled, copyrighted
    record page PUBLIC. That is the one direction the compliance posture must never
    fail, and "Log Out" is not personal data, so removing it buys nothing.
    """
    out, _ = redact_account_chrome(_PAGE)
    assert "Log Out" in out
    assert "logout.php" in out


def test_BUG_a_redacted_gated_page_still_detects_as_gated():
    """The end-to-end form of the test above, across the module boundary.

    This is the assertion that actually matters: whatever the redactor removes, a
    gated page written to disk must remain recognizable as gated.
    """
    from capsule.contracts.evidence import GatingStatus
    from capsule.pipeline.fetch import detect_gating

    url = "https://example.org/ifhf/view_detail.php?recordid=1"
    assert detect_gating(url, _PAGE) is GatingStatus.GATED  # the premise
    out, _ = redact_account_chrome(_PAGE)
    assert detect_gating(url, out) is GatingStatus.GATED


def test_the_record_and_its_external_ref_survive():
    """RootsIreland wraps the account header, the record title, AND the microfilm
    link in `class="noPrint"`. A structural filter would destroy two of the three."""
    out, _ = redact_account_chrome(_PAGE)
    assert "Church Baptism Record for: Robert Euen" in out
    assert "29-Apr-1852" in out
    assert "https://registers.nli.ie/registers/vtls000633924" in out
    assert "View Microfilm of Parish Register" in out


def test_redaction_records_the_href_never_the_removed_text():
    """The manifest must not become the place the PII ends up instead."""
    _, redactions = redact_account_chrome(_PAGE)
    blob = repr(redactions)
    assert "Jane Researcher" not in blob
    assert "1st January 2030" not in blob
    assert {r.location for r in redactions} == {
        "https://example.org/ifhf/myaccount.php",
        "https://example.org/ifhf/mysubscription.php",
    }


def test_redaction_is_idempotent():
    once, first = redact_account_chrome(_PAGE)
    twice, second = redact_account_chrome(once)
    assert twice == once
    assert second == ()  # a redactions[] entry must mean text was actually removed
    assert len(first) == 2


def test_a_page_with_no_account_chrome_is_returned_unchanged():
    out, redactions = redact_account_chrome(_RECORD)
    assert out == _RECORD
    assert redactions == ()


@pytest.mark.parametrize(
    "href",
    [
        "https://example.org/ifhf/myaccount.php",
        "https://example.org/account/",
        "https://example.org/my-account",
        "/mysubscription.php?x=1",
        "https://example.org/subscription",
        "https://example.org/myprofile",
    ],
)
def test_account_shaped_links_are_matched_across_sites(href):
    html = f'<a href="{href}">Some Name</a>'
    out, redactions = redact_account_chrome(html)
    assert "Some Name" not in out
    assert len(redactions) == 1


@pytest.mark.parametrize(
    "href",
    [
        "https://example.org/ifhf/logout.php",  # the gating marker — must survive
        "https://example.org/signout",
        "https://example.org/login.php",  # public pages carry these
        "https://example.org/create-account-help",  # substring, not a path segment
        "https://example.org/accounts-payable",
        "https://registers.nli.ie/registers/vtls000633924",  # an external_ref!
    ],
)
def test_non_account_links_are_left_alone(href):
    html = f'<a href="{href}">Keep This Text</a>'
    out, redactions = redact_account_chrome(html)
    assert out == html
    assert redactions == ()


def test_an_icon_only_account_link_reports_no_redaction():
    """Nothing was removed, so nothing is declared. A redactions[] entry is a claim."""
    html = '<a href="/account/"><img alt="account"></a>'
    out, redactions = redact_account_chrome(html)
    assert out == html
    assert redactions == ()


def test_single_quoted_hrefs_are_matched():
    html = "<a href='/myaccount.php'>Welcome, Jane Researcher</a>"
    out, redactions = redact_account_chrome(html)
    assert "Jane Researcher" not in out
    assert len(redactions) == 1
