"""The fetch seam — one page in view, nothing else.

The pipeline never touches the network directly; it depends on a `Fetcher`
that returns a `PageSnapshot` for exactly the URL it is handed. This keeps the
compliance posture (plan §11, UC-4) enforceable at one point: a fetcher reads
only the page in view, makes zero background requests, and follows no links.
The gating decision (public vs. login/paywall) is the *fetcher's* to make —
it is the only layer that sees the HTTP response — so it is carried on the
snapshot rather than guessed downstream.

`FakeFetcher` is the offline test double. The real httpx-backed fetcher lands
with the CLI (increment 3), where the first live request actually happens.
"""
from __future__ import annotations

import html as html_lib
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlparse, urlsplit

from capsule.contracts.evidence import GatingStatus

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageSnapshot:
    """The captured page in view: its bytes, final URL, and access metadata.

    `fetched_at` must be timezone-aware UTC — it becomes the archive's
    `captured_at`, which the evidence model validates as UTC.
    """

    url: str
    html: str
    gating: GatingStatus
    fetched_at: datetime
    title: Optional[str] = None
    site_name: Optional[str] = None


class Fetcher(Protocol):
    """Fetch exactly one page. Implementations must not crawl or follow links."""

    def fetch(self, url: str) -> PageSnapshot: ...


class FakeFetcher:
    """Offline test double: records every requested URL, returns a fixed snapshot."""

    def __init__(self, snapshot: PageSnapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[str] = []

    def fetch(self, url: str) -> PageSnapshot:
        self.calls.append(url)
        return self._snapshot


# -- the real fetcher --------------------------------------------------------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_SITE_RE = re.compile(
    r"""<meta[^>]+property=["']og:site_name["'][^>]+content=["'](.*?)["']""",
    re.IGNORECASE,
)
# URL fragments that betray a login/paywall interstitial — err toward GATED.
_LOGIN_MARKERS = ("login", "signin", "sign-in", "log-in", "account/sign")

# Body markers that betray an *authenticated session*, and therefore gated content.
#
# The asymmetry is the whole point. A **login** link means you are NOT logged in,
# and nearly every public page on the web has one — sniffing for that would gate
# the entire internet. A **logout** link means you ARE logged in, so whatever the
# page is showing, it showed it to a subscriber. That is the signal.
#
# Word-bounded, because the naive substring "log out" also matches "blog output".
_AUTH_MARKERS = (
    re.compile(r"\b(?:log|sign)[\s\-_]?out\b", re.IGNORECASE),
    re.compile(r"\bmy[\s\-_]?subscription\b", re.IGNORECASE),
)

# A notice that you HAVE signed out says nothing about a session. Find a Grave carries a
# hidden "Sign out successful" dialog on every memorial, signed in or not, which gated them
# all (free-sites test pass, 2026-10-22).
_SIGNED_OUT_NOTICE = re.compile(
    r"\b(?:log|sign)[\s\-_]?out\s+(?:successful(?:ly)?|succeeded|complete)", re.IGNORECASE
)

# Markup that is not the page anyone sees: a single-page site ships its whole interface in
# script (Newspapers.com's translation table says "Sign out of all devices" and "Cancel my
# subscription" to signed-out visitors), so on a site the catalog calls free, only the page
# itself can show a session. Unknown sites keep counting it: the cautious direction.
_NOT_PAGE_TEXT = re.compile(
    r"<script\b.*?</script\s*>|<style\b.*?</style\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL
)

#: Catalog access levels whose pages are only shown to a signed-in user.
_SIGNED_IN_ACCESS = frozenset({"subscription", "free-registration"})


@lru_cache(maxsize=1)
def _catalog() -> tuple:
    """The supported-sites catalog, or nothing if it cannot be read (never fatal)."""
    try:
        from capsule.catalog import load_sites

        return tuple(load_sites())
    except (OSError, ValueError, KeyError) as exc:
        _logger.warning("sites catalog unavailable; gating falls back to page markers: %s", exc)
        return ()


def _catalog_access(url: str) -> Optional[str]:
    """The catalog's access level for this URL's site, or None for a site it doesn't know."""
    from capsule.catalog import site_for_host

    site = site_for_host(urlsplit(url).hostname or "", list(_catalog()))
    return site.access if site is not None else None

_DEFAULT_USER_AGENT = "capsule/0.0 (+evidence-capture; reads only the page in view)"


def _extract_title(html: str) -> Optional[str]:
    match = _TITLE_RE.search(html)
    if match is None:
        return None
    return html_lib.unescape(match.group(1)).strip() or None


def _extract_site_name(html: str, url: str) -> Optional[str]:
    match = _OG_SITE_RE.search(html)
    if match is not None:
        site = html_lib.unescape(match.group(1)).strip()
        if site:
            return site
    netloc = urlparse(url).netloc
    netloc = netloc[4:] if netloc.startswith("www.") else netloc
    return netloc or None


_MAX_REDIRECTS = 10
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MiB — refuse to buffer more than this
_ALLOWED_SCHEMES = ("http", "https")


def _is_internal_host(host: str) -> bool:
    """True for loopback/private/link-local/reserved hosts — never fetched.

    Only IP *literals* are classified here; a hostname passes. M0 is a
    single-user local CLI, so DNS-resolution SSRF hardening (a public name
    resolving to a private IP) is deferred — see docs/backlog.md. This still
    closes the concrete holes: 169.254.169.254, localhost, 127.*, 10.*, etc.
    """
    h = host.lower().strip("[]")  # strip IPv6 brackets
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _guard_url(url: str) -> None:
    """Reject non-http(s) schemes and requests to internal hosts (SSRF guard)."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"refusing non-http(s) URL: {url!r}")
    if _is_internal_host(parts.hostname or ""):
        raise ValueError(f"refusing request to internal/loopback host in {url!r}")


def detect_gating(final_url: str, html: str = "") -> GatingStatus:
    """Conservative gating inference from the final URL *and* the page body.

    A redirect that landed on a login URL means the content is behind auth. So
    does a page that offers to log you *out*: it was rendered for a subscriber.
    The RootsIreland church record that motivated this has no login link, a
    `logout.php` link, and an explicit copyright notice — under URL-only
    inference it auto-detected PUBLIC, one deferred feature away from being
    submitted to Wayback (docs/backlog.md).

    Body inspection is deliberately one-directional. Only markers implying an
    *authenticated* session gate the page; the presence of a login link proves
    nothing, since public pages carry them too. False positives therefore err
    toward GATED — local snapshot only, never republished — which is the safe
    direction.

    The user overrides with --gated/--public — they are the one looking at the page
    (plan §11, 'the user drives'). Non-2xx never reaches here: `fetch` raises on
    it rather than capturing an error page. KNOWN LIMIT: a soft paywall that
    shows no authenticated-session marker still auto-detects PUBLIC; the CLI
    warns on any auto-detected PUBLIC so it is never silently trusted.
    """
    if any(marker in final_url.lower() for marker in _LOGIN_MARKERS):
        return GatingStatus.GATED
    # A site the catalog knows is decided by what it is, not by English words on the page:
    # a subscription or sign-up site's records are only shown to a signed-in user (Archion
    # says "Abmelden", which no marker reads), and a free site is public unless the page
    # itself shows a session.
    access = _catalog_access(final_url)
    if access in _SIGNED_IN_ACCESS:
        return GatingStatus.GATED
    if html:
        body = _SIGNED_OUT_NOTICE.sub(" ", html)
        if access is not None:
            body = _NOT_PAGE_TEXT.sub(" ", body)
        if any(marker.search(body) for marker in _AUTH_MARKERS):
            return GatingStatus.GATED
    return GatingStatus.PUBLIC


class ProvidedContentFetcher:
    """A Fetcher for content captured elsewhere — the page in view from the
    user's own browser — so the pipeline never fetches it itself.

    This is the M0->M1 bridge and the compliance-clean way to capture JS-rendered
    or authenticated sources: the user's browser already loaded the page in the
    user's own session, and hands that content to Capsule. Capsule makes zero
    network requests of its own, so it bypasses nothing and handles no credential.

    Gating is declared by the caller (the browser/user knows). When it is *not*
    declared, it is inferred from the URL and body via `detect_gating` — never
    assumed PUBLIC. This is the path gated pages actually arrive on, since seeing
    them required logging in.
    """

    def __init__(
        self,
        html: str,
        *,
        gating: Optional[GatingStatus] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._html = html
        self._gating = gating
        self._now = now or (lambda: datetime.now(timezone.utc))

    def fetch(self, url: str) -> PageSnapshot:
        # `gating=None` means the caller did not declare one, so infer it — the
        # browser path is exactly where gated pages arrive (you must be logged in
        # to see them), and defaulting that to PUBLIC was the bug.
        gating = self._gating or detect_gating(url, self._html)
        return PageSnapshot(
            url=url,
            html=self._html,
            gating=gating,
            fetched_at=self._now(),
            title=_extract_title(self._html),
            site_name=_extract_site_name(self._html, url),
        )


class HttpxFetcher:
    """Fetch exactly one page over HTTP — no auth, no crawl, no link-following.

    Redirects are followed manually, one hop at a time, and every hop passes
    through `_guard_url` *before* the request is made — so a page cannot bounce
    the fetch to an internal host or a non-http(s) scheme (SSRF / 'read only the
    page in view'). Gating is inferred by `detect_gating` unless overridden.
    Non-2xx responses raise (no API spend on an error page). The client is
    injectable and closeable so tests run offline and no socket leaks.
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        *,
        gating_override: Optional[GatingStatus] = None,
        now: Optional[Callable[[], datetime]] = None,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
    ) -> None:
        self._client = client
        self._gating_override = gating_override
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._timeout = timeout
        self._user_agent = user_agent or _DEFAULT_USER_AGENT

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx  # lazy: offline paths (FakeFetcher) need no httpx import

            self._client = httpx.Client()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> "HttpxFetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(self, url: str) -> PageSnapshot:
        client = self._get_client()
        current = url
        response = None
        for _ in range(_MAX_REDIRECTS + 1):
            _guard_url(current)  # validate EVERY hop before requesting it
            response = client.get(
                current,
                headers={"User-Agent": self._user_agent},
                follow_redirects=False,
                timeout=self._timeout,
            )
            location = response.headers.get("location")
            if response.is_redirect and location:
                current = str(response.url.join(location))
                continue
            break
        else:
            raise ValueError(f"too many redirects (> {_MAX_REDIRECTS}) fetching {url!r}")

        if response is None:  # pragma: no cover - defensive; the loop always assigns
            raise ValueError(f"no response fetching {url!r}")
        if response.status_code >= 400:
            raise ValueError(
                f"fetch failed: HTTP {response.status_code} for {str(response.url)!r}"
            )
        content = response.content
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ValueError(
                f"page too large: {len(content)} bytes > {_MAX_RESPONSE_BYTES} limit"
            )

        html = response.text
        final_url = str(response.url)
        gating = self._gating_override or detect_gating(final_url, html)
        return PageSnapshot(
            url=final_url,
            html=html,
            gating=gating,
            fetched_at=self._now(),
            title=_extract_title(html),
            site_name=_extract_site_name(html, final_url),
        )
