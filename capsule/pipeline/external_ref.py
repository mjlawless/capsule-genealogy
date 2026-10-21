"""Normalize a link a page gives to its source document into an `ExternalRef`.

The split of labour matters: **the model finds, this code normalizes.** The
extraction reply returns only what it saw — a URL and its verbatim link text —
and every structured field (`authority`, `identifier`, `locator`) is derived
here, deterministically, from the URL. An LLM inventing an authority slug would
be an identifier we could never trust to bind two sources together.

Known authorities get a real identifier; anything else falls back to host + path,
which is still a stable, comparable key. Unrecognized is not unusable.

We record these references. We never follow them (plan §11) — nothing in this
module makes a request. See `docs/design/rootsireland-nli-linkage.md`.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

from capsule.contracts.evidence import ExternalRef
from capsule.pipeline.urlsafe import strip_credential_params

_SAFE_SCHEMES = ("http", "https")

# https://registers.nli.ie/registers/vtls000632043[#page/26/mode/1up]
_NLI_HOST = "registers.nli.ie"
_NLI_PATH_RE = re.compile(r"^/registers/(vtls\d+)/?$", re.IGNORECASE)
# BookReader fragments look like `page/26/mode/1up`; only the leaf is a locator.
_NLI_FRAGMENT_RE = re.compile(r"^page/(\d+)", re.IGNORECASE)


def _host(netloc: str) -> str:
    host = netloc.split("@")[-1].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _nli(host: str, path: str, fragment: str) -> Optional[tuple[str, str, Optional[str]]]:
    if host != _NLI_HOST:
        return None
    match = _NLI_PATH_RE.match(path)
    if not match:
        return None
    leaf = _NLI_FRAGMENT_RE.match(fragment)
    # A register link with no `#page/NN` resolves the *register*, not the leaf.
    # That is the common case, and it is why leaf-level binding is a hypothesis
    # rather than something this pipeline may assume.
    locator = f"page/{leaf.group(1)}" if leaf else None
    return "nli-registers", match.group(1).lower(), locator


def canonicalize(url: str, label: Optional[str] = None) -> Optional[ExternalRef]:
    """Derive an `ExternalRef` from `url`, or `None` if it cannot be one.

    `None` means the caller drops the reference: a non-http(s) scheme, or a URL
    with no path to identify a document. Never guess a substitute.
    """
    # Redact before anything else: a reference is written to extraction.json and
    # folded into content_hash, so a signed URL would persist a credential into
    # the evidence store (found live on the NYC certificate's Azure SAS link).
    url = strip_credential_params(url.strip())
    parts = urlsplit(url)
    if parts.scheme.lower() not in _SAFE_SCHEMES or not parts.netloc:
        return None

    host = _host(parts.netloc)
    path = parts.path or "/"
    fragment = parts.fragment or ""

    known = _nli(host, path, fragment)
    if known is not None:
        authority, identifier, locator = known
    else:
        # Fallback: the host names the authority, the path names the document.
        # A bare host with no path identifies nothing citable.
        identifier = path.strip("/")
        if not identifier:
            return None
        authority, locator = host, (fragment or None)

    return ExternalRef(
        authority=authority,
        identifier=identifier,
        locator=locator,
        url=url,
        label=(label.strip() or None) if label else None,
    )


def canonicalize_all(refs: list[tuple[str, Optional[str]]]) -> tuple[ExternalRef, ...]:
    """Canonicalize `(url, label)` pairs, dropping unusable ones and duplicates.

    Deduplicated on `(authority, identifier, locator)` — the same register linked
    twice on one page is one reference, and keeping both would inflate the
    apparent evidence for a claim the page makes once.
    """
    seen: set[tuple[str, str, Optional[str]]] = set()
    out: list[ExternalRef] = []
    for url, label in refs:
        ref = canonicalize(url, label)
        if ref is None:
            continue
        key = (ref.authority, ref.identifier, ref.locator)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return tuple(out)
