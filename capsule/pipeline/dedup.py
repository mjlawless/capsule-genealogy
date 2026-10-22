"""REQ-004 §1 — has this record already been captured?

The near-miss that motivates this: the research agent almost re-captured a death
record the collection already held. The capture entry point is a browser event any
page JS can dispatch, so the Genealogy side concluded (four review rounds) that the
check can only be enforced HERE, at Capsule's own check-and-create.

Identity, not URL string equality:
- An Ancestry record is `{collection_id, record_id}` — derived from the declared
  `_APID` or the URL itself (both URL shapes), never from a model. The ledger keys
  on exactly this, and the manifest scan matches it too, so an apid-less capture of
  the same record is still found. An EXTRA image of the record (REQ-009) adds its
  `page_label` to that identity; no label means the record's default image.
- Everything else falls back to the URL — WITH its query string. The spec suggested
  query-stripping (as BUG-015 did for the outcome mirror), but RootsIreland and
  IrishGenealogy carry the record id IN the query (`view_detail.php?recordid=277156`):
  stripping it would collapse every record on those sites into one identity and
  falsely BLOCK the second record ever captured. A false "new" merely re-captures;
  a false block loses evidence. Only the fragment is dropped, except on the Irish
  census site, which carries its record id there (BUG-024).

Blocking is reserved for VERIFIED-COMPLETE captures: `verify_capture` passes and the
record image is terminal-good (`full`, or `index-only-no-image`; a non-Ancestry
capture has no image expectation, and `none` — REQ-007 §3.2 — is the ordinary state of
a text record, not a fault). A partial, failed, or unverifiable prior capture must never
block — recapturing it is a repair, and BUG-007's screenshot-instead-of-certificate is
exactly the shape that has to stay repairable.

INGESTED files (REQ-008 — `capsule ingest`, marked by `archive.provenance`) are a
separate identity space. An ingest is keyed on (url, sha256), because one viewer URL can
serve many pages; and an ingest NEVER blocks a browser capture of its URL, because it
holds a file that came FROM the page, not the page itself. Blocking the page on the
strength of the file would be a false block — the kind that loses evidence.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from capsule.capture.manifest import verify_capture
from capsule.pipeline.ancestry import IdPair, parse_apid, record_ids_from_url

_logger = logging.getLogger(__name__)

_TERMINAL_GOOD = ("full", "index-only-no-image")
_MANIFEST = "manifest.json"


@dataclass(frozen=True)
class ExistingCapture:
    """A capture the collection already holds for a requested record identity."""

    capture_id: str
    #: True only for verified-complete (blockable); False means "held but repairable".
    complete: bool
    image_status: Optional[str]
    reason: str  # human-readable: why complete / why not
    #: True when this is an ingested file (REQ-008), which never blocks a page capture.
    ingested: bool = False


def record_identity(
    url: Optional[str],
    expected_apid: Optional[str] = None,
    page_label: Optional[str] = None,
) -> str:
    """The dedup identity key for a capture request (also the in-flight lock key).

    `page_label` (REQ-007 §1) is part of the identity because on a tile viewer the URL is
    NOT one. Archion serves all 173 images of register 239254 at a single address, so with
    the URL alone the collection can hold exactly one leaf of any register — and the second
    leaf a researcher tries to capture is refused as "already mined". Observed live on
    2026-08-22: a capture of image 6 was blocked by the morning capture of image 2.

    That is the failure the module docstring already names — a false block LOSES EVIDENCE,
    while a false "new" merely re-captures — so the leaf has to be in the key.

    REQ-009 extends this to Ancestry. A record can span two images (every two-page register
    opening), and on 2026-09-28 the facing page of 62073:223848 was refused as "already
    mined" because the record id was the whole key. An Ancestry label is set ONLY by the
    one-call flow for an image other than the record's default. The backend enforces that
    (BUG-019, `accepted_page_label`): off a tile viewer, a label survives only with an
    `expected_apid`, so a dropdown on an Ancestry browse page ("Fayette") no longer reaches
    this key, and a label-less Ancestry request is the record's default image.
    """
    ids = _resolve_ids(url, expected_apid)
    if ids is not None:
        key = f"ancestry:{ids[0]}:{ids[1]}"
    else:
        key = "url:" + _normalize_url(url or "")
    return f"{key}#page={page_label}" if page_label else key


def _resolve_ids(url: Optional[str], expected_apid: Optional[str]) -> Optional[IdPair]:
    ids = parse_apid(expected_apid) if expected_apid else None
    return ids if ids is not None else record_ids_from_url(url)


# BUG-024: the National Archives of Ireland census site links every search result to
# `census-record/#id=2285856&c20_year=1911`, and the record page reads the record from the
# FRAGMENT. Dropping it made every census record one identity, so the second census capture
# ever made would be falsely blocked. Its "Link to record" gives the same record as
# `?id=…&c20_year=…`, so both forms fold to that one query.
_NAI_HOSTS = frozenset({"nationalarchives.ie", "www.nationalarchives.ie"})
_NAI_CENSUS_RECORD = "/collections/search-the-census/census-record/"


def _normalize_url(url: str) -> str:
    """Case-fold scheme/host and drop the fragment. The query STAYS (see module doc).

    The one exception is a site whose record id lives in the fragment (BUG-024).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = parts.netloc.lower()
    query = parts.query
    if host in _NAI_HOSTS and parts.path == _NAI_CENSUS_RECORD:
        host = "nationalarchives.ie"
        params = dict(parse_qsl(parts.query))
        params.update(parse_qsl(parts.fragment))
        if params.get("id"):
            query = urlencode(
                [(k, params[k]) for k in ("id", "c20_year") if params.get(k)]
            )
    return urlunsplit((parts.scheme.lower(), host, parts.path, query, ""))


def find_capture(
    url: Optional[str],
    expected_apid: Optional[str] = None,
    *,
    db_path: Union[str, Path],
    captures_root: Union[str, Path],
    page_label: Optional[str] = None,
    include_ingested: bool = False,
) -> Optional[ExistingCapture]:
    """The existing capture for this record identity, or None.

    A returned `.complete` capture is the dedup BLOCK signal; an incomplete one
    means "proceed as a repair". Preference order: the ledger's latest word for an
    Ancestry identity (it already persists verify_ok/image_status per event), then
    a tolerant scan of the capture folders' manifests — which also finds apid-less
    Ancestry captures and every non-Ancestry capture.

    Ingested files are skipped unless `include_ingested` (the `query` lookup, which should
    find everything held at a URL). Even then a page capture wins, so `complete` keeps
    meaning "a page capture would be refused"; a returned ingest carries `ingested=True`.
    """
    ids = _resolve_ids(url, expected_apid)

    if ids is not None:
        row = _ledger_row(Path(db_path), ids, page_label)
        # The ledger is APPEND-ONLY, so it remembers captures the collection no
        # longer holds: `trash_capture` moves the folder to `_trash/` and
        # deliberately leaves the rows. A mined row whose folder is gone must not
        # block a legitimate recapture (review: trash false-block) — trust the row
        # only while its folder still lives in the active collection; otherwise
        # fall through to the manifest scan, which sees only what is actually held.
        if (
            row is not None
            and row.capture_id
            and (Path(captures_root) / row.capture_id).is_dir()
        ):
            return ExistingCapture(
                capture_id=row.capture_id,
                complete=bool(row.mined),
                image_status=row.image_status,
                reason="ledger: mined" if row.mined else f"ledger: {row.status}",
            )

    found = _scan_captures(
        Path(captures_root), url=url, ids=ids, page_label=page_label, ingested=False
    )
    if found is None and include_ingested:
        found = _scan_captures(
            Path(captures_root), url=url, ids=ids, page_label=page_label, ingested=True
        )
    return found


def find_ingested(
    url: str, sha256: str, *, captures_root: Union[str, Path]
) -> Optional[ExistingCapture]:
    """An existing capture of these exact bytes at this URL, or None (REQ-008 §3).

    Keyed on (normalized url, sha256 of the primary artifact): same URL + same bytes is the
    same evidence, while same URL + different bytes is a different page of the same volume
    and must not be blocked. Any capture qualifies — a browser document capture of the same
    PDF at the same URL is the same evidence too. Complete-only blocking, as everywhere.
    """
    root = Path(captures_root)
    if not root.is_dir():
        return None
    target = _normalize_url(url)
    fallback: Optional[ExistingCapture] = None
    for child, manifest in _manifests(root):
        archive = manifest.get("archive") or {}
        m_url = str((manifest.get("source") or {}).get("url") or "")
        if _normalize_url(m_url) != target or archive.get("sha256") != sha256:
            continue
        existing = _assess(child, manifest, ancestry=False)
        existing = replace(existing, ingested=_is_ingested(manifest))
        if existing.complete:
            return existing
        fallback = fallback or existing
    return fallback


def _is_ingested(manifest: dict) -> bool:
    return bool((manifest.get("archive") or {}).get("provenance"))


def _manifests(captures_root: Path):
    """(folder, manifest) for every readable capture, newest first.

    Folder names lead with YYYYMMDD, so reverse-sorted iteration sees newest first and a
    complete match can short-circuit without verifying older folders.
    """
    for child in sorted(captures_root.iterdir(), reverse=True):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        try:
            manifest = json.loads((child / _MANIFEST).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # not a capture / unreadable — tolerant, like the index reader
        if isinstance(manifest, dict):
            yield child, manifest


def _ledger_row(db_path: Path, ids: IdPair, page_label: Optional[str] = None):
    from capsule.store.ledger import AncestryLedger

    try:
        ledger = AncestryLedger(db_path)
    except Exception as exc:  # noqa: BLE001 — a broken index must not break capture
        _logger.warning("dedup: could not open the ledger at %s: %s", db_path, exc)
        return None
    try:
        return ledger.latest(f"{ids[0]}:{ids[1]}", page_label)
    finally:
        ledger.close()


def _scan_captures(
    captures_root: Path,
    *,
    url: Optional[str],
    ids: Optional[IdPair],
    page_label: Optional[str] = None,
    ingested: bool = False,
) -> Optional[ExistingCapture]:
    """Scan one identity space: page captures (`ingested=False`) or ingested files."""
    if not captures_root.is_dir():
        return None
    target = _normalize_url(url or "")
    fallback: Optional[ExistingCapture] = None
    for child, manifest in _manifests(captures_root):
        if _is_ingested(manifest) != ingested:
            continue
        m_url = str(((manifest.get("source") or {}).get("url")) or "")
        if not m_url:
            continue
        # REQ-007 §1 / REQ-009 — the leaf is part of the identity on a source that serves
        # many images for one record. A capture banked before `page_label` existed carries
        # None, so it matches only a request that also has none: it can still be repaired,
        # and it can never block a DIFFERENT leaf it cannot be compared against. For
        # Ancestry, None is the record's default image — every capture on disk today.
        m_label = (manifest.get("source") or {}).get("page_label")
        if ids is not None:
            matched = record_ids_from_url(m_url) == ids and m_label == page_label
        else:
            matched = _normalize_url(m_url) == target and m_label == page_label
        if not matched:
            continue
        existing = _assess(child, manifest, ancestry=ids is not None)
        if ingested:
            existing = replace(existing, ingested=True)
        if existing.complete:
            return existing
        fallback = fallback or existing
    return fallback


def _assess(folder: Path, manifest: dict, *, ancestry: bool) -> ExistingCapture:
    """Verified-complete, or held-but-repairable — never a guess.

    The manifest's `image` field is the R4.3 self-description. Absent on an
    Ancestry-record capture it means the capture predates the status (or worse) —
    NOT provably complete, so it must not block. Absent on a non-Ancestry capture
    it is simply "no record image expected".

    `none` (REQ-007 §3.2) is a THIRD thing, and reading it as a failure would be a serious
    regression: it says "this capture holds no record image", which is the ordinary and
    correct state of every text record in the collection — RootsIreland, IrishGenealogy,
    FamilySearch. Treating it as not-terminal-good would mark every one of them incomplete
    and switch dedup off for most of the corpus. Whether holding no image is FATAL is a
    question about the source, not about the status string, and `verify_capture` already
    owns it (§3.3: an `original` that yielded no text must hold a real raster). So `none`
    falls through to verification and lets that gate decide.
    """
    if manifest.get("extraction") == "pending":
        # v0.2 — archived without a key, never read. Not complete, so re-capturing the page
        # once a key exists is allowed (or `capsule extract` reads this one).
        return ExistingCapture(
            capture_id=folder.name,
            complete=False,
            image_status=manifest.get("image"),
            reason="extraction pending (archived without an API key)",
        )
    image = manifest.get("image")
    if image == "none" and not ancestry:
        pass  # not a verdict — see above; verify_capture decides whether it matters
    elif image is not None:
        if image not in _TERMINAL_GOOD:
            return ExistingCapture(
                capture_id=folder.name,
                complete=False,
                image_status=image,
                reason=f"image not terminal-good ({image})",
            )
    elif ancestry:
        return ExistingCapture(
            capture_id=folder.name,
            complete=False,
            image_status=None,
            reason="an Ancestry record capture with no declared image status",
        )
    try:
        ok = verify_capture(folder).ok
    except (FileNotFoundError, ValueError, OSError) as exc:
        return ExistingCapture(
            capture_id=folder.name,
            complete=False,
            image_status=image,
            reason=f"cannot verify: {exc}",
        )
    return ExistingCapture(
        capture_id=folder.name,
        complete=ok,
        image_status=image,
        reason="verified complete" if ok else "verify FAILED",
    )
