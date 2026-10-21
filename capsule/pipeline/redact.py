"""Remove the subscriber's account chrome from a captured page, before it is written.

A gated record page is rendered *for a logged-in person*, and it says so. The
RootsIreland baptism record carries, in its header:

    <a href=".../myaccount.php">Welcome, Jane Researcher</a>
    <a href=".../mysubscription.php">Subscription until :1st January 2030</a>

That is the account holder's name and subscription status. Left alone it is hashed
into `archive.sha256`, transmitted to the extraction model, and would ship inside
any capsule export. None of it is the record.

"Verbatim transcription is sacred" (CLAUDE.md) governs the **record** — the names,
dates, and places we transcribe into assertions. It has never governed the site's
account chrome, and the capture-folder spec did not previously distinguish the two.
This module draws that line, and the manifest records where it was drawn: a capture
that was redacted says so in `redactions[]` (spec §4d), so no reader ever mistakes a
redacted snapshot for a byte-true one.

Two design constraints, both learned the hard way:

**Never match on site-specific class names.** RootsIreland marks the account header
`class="welcome"` inside a `class="noPrint"` block — but it marks the record title,
and the `<!-- MICROFILM_LINK -->` anchor that produces `external_refs`, `noPrint`
too. A structural filter would silently destroy real evidence. We match instead on
the *shape* account chrome takes anywhere: a link into the site's own account or
subscription pages. Ancestry and FindMyPast need no new code, and a miss leaves the
page exactly as it is today rather than removing something it shouldn't.

**Never redact a logout link.** `fetch.detect_gating` proves a page was paywalled by
finding the text "Log Out" in its body. Blank that and `snapshot.html` loses the
evidence of its own gating: anything re-reading it later would classify a paywalled,
copyrighted record page as PUBLIC — a failure in the one direction the compliance
posture must never fail. "Log Out" is not personal data. It stays.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from capsule.capture.manifest import REDACTION_MARKER, Redaction
from capsule.pipeline.urlsafe import strip_credential_params

#: Path segments identifying a link into the viewer's OWN account. The text of such
#: a link is the account holder's name, subscription status, or email — never record
#: text. Deliberately excludes `logout`/`signout` (see the module docstring) and
#: `login`/`signin` (public pages carry those, and they say nothing about anyone).
_ACCOUNT_SEGMENTS = frozenset(
    {
        "myaccount",
        "my-account",
        "my_account",
        "mysubscription",
        "my-subscription",
        "my_subscription",
        "myprofile",
        "my-profile",
        "account",
        "subscription",
    }
)

#: `<a ...href="...">text</a>`. HTML forbids nested anchors, so the non-greedy body
#: match is safe. DOTALL because the chrome is often laid out across several lines.
_ANCHOR_RE = re.compile(
    r"""(<a\b[^>]*\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*>)(.*?)(</a\s*>)""",
    re.IGNORECASE | re.DOTALL,
)

_TAG_RE = re.compile(r"<[^>]*>")

_REASON = "account-chrome"


def _visible_text(body: str) -> str:
    """The anchor's text with markup gone. `<img alt="account">` has none."""
    return _TAG_RE.sub("", body).strip()


def _is_account_link(href: str) -> bool:
    """True when `href` points into the viewer's own account/subscription pages."""
    path = urlsplit(href).path.lower()
    # Compare per path segment with any extension dropped, so `/ifhf/myaccount.php`
    # and `/account/` both match while `/create-account-help` does not.
    for segment in path.split("/"):
        stem, _, _ = segment.partition(".")
        if stem in _ACCOUNT_SEGMENTS:
            return True
    return False


def redact_account_chrome(html: str) -> tuple[str, tuple[Redaction, ...]]:
    """Replace account-link text with the redaction marker; report what was removed.

    Returns the rewritten HTML and one `Redaction` per link redacted. A `Redaction`
    records the *href* and the reason — never the removed text, which is the very
    personal data this exists to contain.

    Idempotent: an already-redacted link is recognized and left alone, so a second
    pass changes nothing and reports nothing. Anchor tags and attributes survive
    intact, as do the document's structure and every other text node.
    """
    found: list[Redaction] = []

    def _replace(match: re.Match[str]) -> str:
        open_tag, double, single, body, close_tag = match.groups()
        href = double if double is not None else (single or "")
        if not _is_account_link(href):
            return match.group(0)
        if _visible_text(body) in ("", REDACTION_MARKER):
            # An icon-only link (`<img alt="account">` carries no text node), or one
            # already redacted. Change nothing and report nothing: a redactions[]
            # entry is a claim that text was removed, and must never be false.
            return match.group(0)
        found.append(
            Redaction(reason=_REASON, location=strip_credential_params(href.strip()))
        )
        return f"{open_tag}{REDACTION_MARKER}{close_tag}"

    return _ANCHOR_RE.sub(_replace, html), tuple(found)
