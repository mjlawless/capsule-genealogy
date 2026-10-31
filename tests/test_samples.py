"""The committed sample captures (`samples/`) stay valid, unedited and redistributable.

The samples are real capture output with the page and images left out (`samples/README.md`).
These pin that each one still parses against the contracts, that its `extraction.json` and
`citation.json` are byte-for-byte what the capture wrote (their manifest hashes still match),
that only derived JSON is present, and that nothing gated or signed slipped in.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from capsule.collection.read import read_all
from capsule.contracts.evidence import (
    Assertion,
    Citation,
    ExternalRef,
    GatingStatus,
    Persona,
    Snippet,
)
from capsule.contracts.transcript import TranscriptComparison, TranscriptField, Transcription

_SAMPLES = Path(__file__).resolve().parent.parent / "samples"
_FOLDERS = sorted(p for p in _SAMPLES.iterdir() if p.is_dir()) if _SAMPLES.is_dir() else []
_SHIPPED = {"manifest.json", "extraction.json", "citation.json"}
# Query parameters that make a link a signed, expiring credential (Azure SAS, S3, GCS, generic
# tokens). An image server's own locator (`?FIF=...`) is an ordinary address and may stay.
_SIGNING_PARAMS = {
    "sig", "se", "signature", "token", "x-amz-signature", "x-amz-credential",
    "x-goog-signature", "expires", "key-pair-id", "policy",
}
_URL = re.compile(r"""https?://[^\s"'<>)\]]+""")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_samples_exist():
    assert _FOLDERS, "no sample captures found under samples/"


@pytest.mark.parametrize("folder", _FOLDERS, ids=lambda p: p.name)
def test_only_derived_json_ships(folder: Path):
    files = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    transcripts = {f for f in files if f.startswith("transcript/")}
    assert files - transcripts == _SHIPPED
    for rel in transcripts:
        parts = rel.split("/")
        assert len(parts) == 3 and parts[2] == "transcript.json", rel


@pytest.mark.parametrize("folder", _FOLDERS, ids=lambda p: p.name)
def test_sample_parses_against_the_contracts(folder: Path):
    extraction = _json(folder / "extraction.json")
    for item in extraction["personas"]:
        Persona.model_validate(item)
    for item in extraction["snippets"]:
        Snippet.model_validate(item)
    for item in extraction["assertions"]:
        Assertion.model_validate(item)
    for item in extraction.get("external_refs") or []:
        ExternalRef.model_validate(item)
    Citation.model_validate(_json(folder / "citation.json"))
    for path in folder.glob("transcript/*/transcript.json"):
        transcript = _json(path)
        Transcription.model_validate(transcript["transcription"])
        for item in transcript["fields"]:
            TranscriptField.model_validate(item)
        for item in transcript.get("comparisons") or []:
            TranscriptComparison.model_validate(item)


@pytest.mark.parametrize("folder", _FOLDERS, ids=lambda p: p.name)
def test_sample_json_is_the_captured_bytes(folder: Path):
    manifest = _json(folder / "manifest.json")
    listed = manifest["hashes"]["files"]
    for name in ("extraction.json", "citation.json"):
        digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        assert listed[name] == digest, f"{name} no longer matches its manifest hash"
    assert manifest["source"]["content_hash"] == listed["extraction.json"]
    assert manifest["capture_id"] == folder.name


def _strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _signed_links(node: object) -> list[str]:
    """Every signed link anywhere in a JSON document, prose and transcripts included."""
    found = []
    for text in _strings(node):
        for url in _URL.findall(html.unescape(text)):
            query = parse_qs(urlsplit(url).query, keep_blank_values=True)
            if {k.lower() for k in query} & _SIGNING_PARAMS:
                found.append(url)
    return found


@pytest.mark.parametrize(
    "document",
    [
        {"media": [{"source_url": "https://img.example.org/a.jpg?sv=2020&sig=abc"}]},
        {"archived_url": "https://store.example.org/p.pdf?X-Amz-Signature=abc"},
        {"text": "Image seen at https://cdn.example.org/x.jpg?se=2026&amp;sig=abc today."},
        {"fields": [{"value": "see https://a.example.org/b?Token=abc"}]},
    ],
)
def test_a_signed_link_is_caught_wherever_it_sits(document: dict):
    assert _signed_links(document)


def test_an_image_server_locator_is_not_a_signed_link():
    assert not _signed_links({"url": "https://iiif.example.org/i?FIF=/a/b.jp2&CVT=jpeg"})


@pytest.mark.parametrize("folder", _FOLDERS, ids=lambda p: p.name)
def test_sample_is_public_and_carries_no_signed_links(folder: Path):
    manifest = _json(folder / "manifest.json")
    source = manifest["source"]
    assert GatingStatus(source["gating_status"]) is GatingStatus.PUBLIC
    assert source["copyright_flag"] is False
    for path in folder.rglob("*.json"):
        assert not _signed_links(_json(path)), f"signed link kept in {path.relative_to(folder)}"


def test_collection_reader_reads_every_sample():
    assert sorted(r.capture_id for r in read_all(_SAMPLES)) == [p.name for p in _FOLDERS]
