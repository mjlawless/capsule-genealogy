"""BUG-023 — `source.content_hash` is the digest of `extraction.json`, not of the document.

So every capture with an empty extraction (an ingest, a document capture, a page with no
record text) shares one `content_hash`. That is correct as defined and now documented in the
contracts; these tests pin it, so nobody later "fixes" a dedup by keying on it.
"""
from __future__ import annotations

import base64
import json

import tests.test_backend_document as td

#: sha256 of an empty `extraction.json`.
EMPTY_EXTRACTION_DIGEST = "8f144456e95bd74b81c2daa8160bad6f16220c6d9f74fc264f4a6f20ec4d4840"


def test_BUG_023_the_empty_extraction_digest_is_the_documented_constant():
    from capsule.capture.manifest import Extraction, serialize_extraction, sha256_hex

    assert sha256_hex(serialize_extraction(Extraction())) == EMPTY_EXTRACTION_DIGEST


def test_BUG_023_two_different_documents_share_content_hash_but_not_their_bytes(tmp_path):
    client = td._client(tmp_path)
    try:
        for i, body in enumerate((b"%PDF-1.4\nfirst\n%%EOF", b"%PDF-1.4\nsecond\n%%EOF")):
            resp = client.post(
                "/capture",
                headers={"X-Capsule-Token": td._TOKEN},
                json={
                    "url": f"https://example.org/doc-{i}.pdf",
                    "document": {"data_base64": base64.b64encode(body).decode("ascii")},
                },
            )
            assert resp.status_code == 200, resp.text
    finally:
        td.app.dependency_overrides.clear()
    manifests = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((tmp_path / "captures").glob("*/manifest.json"))
    ]
    assert len(manifests) == 2
    assert {m["source"]["content_hash"] for m in manifests} == {EMPTY_EXTRACTION_DIGEST}
    assert len({m["archive"]["sha256"] for m in manifests}) == 2
