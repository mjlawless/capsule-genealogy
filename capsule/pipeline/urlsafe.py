"""Strip credentials out of a URL before it is transmitted or written to disk.

Found live: every NYC certificate capture wrote an Azure SAS URL into
`extraction.json` as an `external_refs[].url` — 231 characters carrying `sig=`,
`se=`, and `sp=`. That is a signed, time-limited credential for the blob. It was
folded into the capture's `content_hash`, it would travel inside a capsule export
zip, and it was transmitted to the Anthropic API in the extraction prompt.

An evidence store archives what a page *said*. It must not archive the key that
was used to fetch it.

Two rules, deliberately narrow so a real record identifier is never destroyed:

1. **A signature parameter poisons the whole query.** `sig`, `signature`,
   `x-amz-signature`, `x-goog-signature` appear only on a pre-signed URL, where
   every other parameter — expiry, permissions, key id — is part of the same
   credential. Drop the entire query string.
2. **Otherwise, drop only named secrets.** `token`, `access_token`, `api_key`,
   and friends. A `?recordid=643376` survives, because that is the record.

Path and fragment are always kept: `.../registers/vtls000632043#page/26` is the
identity of the leaf, not a secret.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Presence of any of these means the entire query string is one credential.
_SIGNATURE_PARAMS = frozenset({"sig", "signature", "x-amz-signature", "x-goog-signature"})

#: Individually-named secrets, removed without touching their neighbours.
_SECRET_PARAMS = frozenset(
    {
        "token",
        "access_token",
        "accesstoken",
        "api_key",
        "apikey",
        "auth_token",
        "password",
        "secret",
        "x-amz-security-token",
    }
)


def strip_credential_params(url: str) -> str:
    """Return `url` with any credential-bearing query parameters removed.

    Idempotent, and a no-op for the overwhelming majority of URLs. Never touches
    scheme, host, path, or fragment. A malformed URL is returned unchanged rather
    than raising — this is a redaction pass, not a validator.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    names = {name.lower() for name, _ in pairs}

    if names & _SIGNATURE_PARAMS:
        query = ""  # a pre-signed URL's query is one indivisible credential
    else:
        kept = [(n, v) for n, v in pairs if n.lower() not in _SECRET_PARAMS]
        if len(kept) == len(pairs):
            return url  # nothing to redact; preserve the original byte-for-byte
        query = urlencode(kept)

    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
