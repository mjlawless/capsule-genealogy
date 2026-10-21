"""Localhost capture backend — POST the page in view, get a verifiable capture.

`POST /capture {url, html, gating?}` runs the SAME pipeline the CLI does, but on
content the caller *provides* (the user's browser already loaded the page, in the user's own session), so
Capsule makes no network request of its own to the site — it reads only the page
the user is viewing and bypasses nothing.

The server binds to localhost only (see `capsule serve`). The model Agent is a
FastAPI dependency (`provide_agent`) so tests inject a fake and never spend
tokens; `get_settings` is likewise injectable for tmp paths.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal, Optional, Sequence

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from capsule.agent.anthropic_transport import AnthropicTransport
from capsule.agent.base import DEFAULT_MODEL, Agent
from capsule.capture.manifest import MediaItem, resolve_capture_folder
from capsule.collection.escape import esc
from capsule.collection.index import render_index
from capsule.collection.read import read_all
from capsule.contracts.evidence import GatingStatus
from capsule.evals.fixtures import write_fixture
from capsule.pipeline.annotate import add_note_to_capture
from capsule.pipeline.correct import CorrectionRequest, apply_correction
from capsule.config import default_captures_root, default_db_path, work_queue_enabled
from capsule.pipeline.ancestry import (
    has_record_image,
    identity_mismatch,
    parse_apid,
    record_ids_from_url,
)
from capsule.pipeline.dedup import ExistingCapture, find_capture, record_identity
from capsule.pipeline.failure_log import failed_call_sink
from capsule.pipeline.image_check import (
    classify_image,
    image_warning,
    measure_record_file,
)
from capsule.pipeline.render_check import looks_blank
from capsule.pipeline.fetch import ProvidedContentFetcher
from capsule.pipeline.pipeline import CaptureOutcome, run_capture, run_document_capture
from capsule.pipeline.sniff import MAX_ARTIFACT_BYTES, sniff
from capsule.pipeline.page_identity import accepted_page_label
from capsule.pipeline.render_check import (
    EVIDENCE_IMAGE_STATUSES,
    RECORD_IMAGE_PROVENANCE,
    blank_render_warning,
)
from capsule.pipeline.trash import trash_capture
from capsule.store.ledger import AncestryLedger, DailyCapReached, TooSoon
from capsule.store.repository import Repository
from capsule.version import build_version

_AGENT_NAME = "extractor"
_TOKEN_HEADER = "X-Capsule-Token"
# BUG-021 — the extension names its own build on every capture. A HEADER, not a body field:
# `CaptureRequest` is `extra="forbid"`, so a new body field 422s against an older backend,
# while an unknown header is simply ignored.
_EXTENSION_VERSION_HEADER = "X-Capsule-Extension-Version"
# Chrome extension versions are one to four dot-separated integers. Anything else is not a
# version and is not recorded: the header is page-reachable text on its way into a manifest.
_EXTENSION_VERSION = re.compile(r"\d{1,5}(\.\d{1,5}){0,3}")

# BUG-021 — freeze the build identity NOW, while the code being served is the code on disk.
# `build_version()` caches its first answer; left to the first capture or /health call, a
# checkout that moved on after `capsule serve` started (a `git pull` while the server runs)
# would stamp the NEW commit onto captures made by the OLD code (Codex adversarial review).
build_version()
_logger = logging.getLogger(__name__)

# Caps on browser-provided media (an embedded scan/PDF the page already showed).
# Generous per item (a high-res certificate scan), bounded in aggregate so one
# POST can't exhaust local disk/memory. Module-level so tests can shrink them.
MAX_MEDIA_ITEM_BYTES = MAX_ARTIFACT_BYTES  # 25 MiB per file
MAX_MEDIA_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MiB per capture
MAX_MEDIA_COUNT = 25  # files per capture

# MIME -> extension fallback when the filename carries no usable extension.
_MIME_EXT = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/tiff": "tif",
    "image/tif": "tif",
}
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Extensions we accept from an (untrusted) page filename verbatim. Anything else
# — active-content types (.html/.svg/.js), or an over-long alnum run that would
# blow the filesystem name limit — falls back to the MIME map, then inert `bin`.
# This is a content-safety allow-list on top of the writer's charset allow-list.
_ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "tif", "tiff"}


class Settings(BaseModel):
    """Where the backend writes captures and its index (overridable in tests)."""

    model_config = ConfigDict(frozen=True)

    db_path: Path
    captures_root: Path
    token: Optional[str] = None
    # Where a correction auto-writes its eval fixture (the flywheel). Defaults to
    # the repo's committed `evals/fixtures/` so `capsule eval` finds it; tests
    # point it at a tmp dir. Has a default so existing Settings(...) callers stand.
    fixtures_root: Path = Path("evals/fixtures")


def get_settings() -> Settings:
    # ONE resolver, shared with the CLI (capsule/config.py). They defaulted to different
    # files, so `capsule ancestry status` read an empty ledger and confidently reported
    # "0 mined" for records the backend had just written — BUG-003.
    return Settings(
        db_path=default_db_path(),
        captures_root=default_captures_root(),
        token=os.environ.get("CAPSULE_TOKEN") or None,
        fixtures_root=Path(os.environ.get("CAPSULE_FIXTURES_DIR", "evals/fixtures")),
    )


def require_token(
    x_capsule_token: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Gate /capture on a shared secret so only the extension — not any visited
    page's JavaScript — can drive captures against this localhost backend.

    Fails *closed*: a server with no token configured refuses every request
    (503) rather than serving an open endpoint. A missing/incorrect token is a
    401. The compare is constant-time to avoid leaking the token by timing.
    """
    if not settings.token:
        raise HTTPException(
            status_code=503,
            detail="server has no CAPSULE_TOKEN configured; (re)start `capsule serve`",
        )
    if not x_capsule_token or not secrets.compare_digest(x_capsule_token, settings.token):
        raise HTTPException(status_code=401, detail=f"missing or invalid {_TOKEN_HEADER}")


def provide_agent(settings: Settings = Depends(get_settings)) -> Optional[Agent]:
    """The real extraction Agent, or None when no API key is configured.

    A document (archive-only) capture makes NO model call, so a missing key must
    not fail it. The html path checks for None and returns 503 itself, preserving
    the original 'no key -> 503' behavior for page captures.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return Agent(
        name=_AGENT_NAME,
        model=DEFAULT_MODEL,
        transport=AnthropicTransport(),
        sink=failed_call_sink(settings.captures_root),
    )


def get_repository(settings: Settings = Depends(get_settings)) -> Iterator[Repository]:
    """Yield an open evidence-store Repository, closed when the request finishes.

    A yield dependency owns the connection lifecycle: FastAPI runs the `finally`
    even when the handler (or an exception handler) raises, so no endpoint has to
    wrap its body in `try/finally` to avoid a connection leak.
    """
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    repository = Repository(settings.db_path)
    try:
        yield repository
    finally:
        repository.close()


class MediaIn(BaseModel):
    """One in-page media file (e.g. an embedded scan/PDF) the browser grabbed.

    The bytes arrive base64-encoded; `filename`/`mime` inform the on-disk name,
    `source_url` records where the page fetched the bytes from. The backend is
    the trust boundary: it decodes, size-caps, and sanitizes the filename into a
    safe slug/ext before anything is written.
    """

    model_config = ConfigDict(extra="forbid")

    data_base64: str = Field(..., description="Base64-encoded media bytes.")
    filename: Optional[str] = Field(None, description="Original page filename (for slug/ext).")
    mime: Optional[str] = Field(None, description="Declared content type, e.g. application/pdf.")
    source_url: Optional[str] = Field(None, description="URL the page fetched the bytes from.")
    # Set when the URL was DERIVED rather than read off the page (e.g. a tiled
    # book viewer asked for the leaf in view). Untrusted text, stored verbatim
    # and length-capped, exactly like source_url.
    provenance: Optional[str] = Field(
        None, max_length=200, description="How the URL was arrived at, if derived."
    )


class DocumentIn(BaseModel):
    """A document the page IS (e.g. a PDF the browser is viewing), base64-encoded.

    Used instead of `html` when the page in view is not an HTML page but a
    document — an archive-only, zero-assertion capture (no model call). The
    backend decodes, size-caps, and derives a safe extension before writing.
    """

    model_config = ConfigDict(extra="forbid")

    data_base64: str = Field(..., description="Base64-encoded document bytes.")
    filename: Optional[str] = Field(None, description="Original page filename (for title/ext).")
    mime: Optional[str] = Field(None, description="Declared content type, e.g. application/pdf.")


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., description="The URL of the page in view.")
    html: Optional[str] = Field(
        None, description="The captured page content (rendered outerHTML). Omit for a document."
    )
    document: Optional[DocumentIn] = Field(
        None, description="A document the page IS (a PDF). Provide instead of html."
    )
    # REQ-007 §1 — which leaf of a multi-image viewer was on screen, read LIVE from the
    # DOM by the content script. The browser is the only authority on this: serializing the
    # page writes the `selected` ATTRIBUTE, which stays on the leaf the viewer first loaded
    # even after the researcher navigates to another. Verified live 2026-08-22 — the screen
    # showed image 6 while `outerHTML` still marked image 2. So the snapshot cannot be
    # trusted here, and this field overrides what parsing it would say.
    page_label: Optional[str] = Field(default=None, max_length=60)
    gating: Optional[GatingStatus] = Field(
        None, description="public|gated; omitted defaults to public."
    )
    media: list[MediaIn] = Field(
        default_factory=list, description="In-page media (embedded scans/PDFs) to archive."
    )
    note: Optional[str] = Field(
        None, description="Optional researcher note — why this was collected (not evidence)."
    )
    note_author: str = Field("owner", description="Who authored the note.")
    # The record this capture is SUPPOSED to be. Set it and the backend refuses to write
    # anything unless the page really holds that record (REQ-001 R5). Omit it and no
    # identity claim is made — every existing caller is unaffected.
    expected_apid: Optional[str] = Field(
        None, description='The tree\'s _APID for this record, e.g. "1,7602::56510272".'
    )
    # Was a record IMAGE expected here? "expected" means one was attempted, so its
    # absence is a FAILURE; "none" means the record is index-only, so its absence is the
    # honest answer. Without this distinction the two are indistinguishable — and a
    # failed image would quietly look like a record that simply never had one.
    image_intent: Optional[Literal["expected", "none"]] = Field(
        None, description="expected|none — whether a record image was meant to be here."
    )
    remine: bool = Field(
        False, description="Capture a record the ledger already considers mined."
    )
    # Record files the PAGE HELD and the browser could not get (BUG-007) — a signed blob we
    # lack the host grant for, an unreadable embed. Recorded because a capture that quietly
    # substitutes a page RENDER for the record is the failure this project exists to
    # prevent, and it is invisible: the toast said `verify OK, PDF ✓` while what we held was
    # a 977x1082 screenshot of a birth certificate.
    # REQ-010 — WHY the extension holds no record image ("no download was armed for this
    # capture", "no download from the armed site arrived within 20s …"). It used to live only
    # in a toast. Recorded in the manifest and the ledger when the capture has no record image.
    image_skipped: Optional[str] = Field(
        None, max_length=500, description="Why the extension has no record image to send."
    )
    missed_media: list[str] = Field(
        default_factory=list,
        description="Record files the page held that could not be captured.",
    )
    # The TRUE source still to chase for an index-only record (R4.2) — e.g. "the SS-5
    # application" behind an SSDI entry. Taken from the page's own `Original data` line,
    # so it is the provider's claim, never our invention.
    follow_up: Optional[str] = Field(
        None, description="For an index-only record: the real source still to be found."
    )

    @model_validator(mode="after")
    def _exactly_one_primary(self) -> "CaptureRequest":
        if (self.html is None) == (self.document is None):
            raise ValueError("provide exactly one of html / document")
        if self.document is not None and self.media:
            raise ValueError("media[] is only for an html page capture, not a document")
        return self


def _derive_slug_ext(filename: Optional[str], mime: Optional[str]) -> tuple[str, str]:
    """Turn an untrusted page filename + MIME into a safe (slug, ext) pair.

    The capture-folder writer allow-lists slug/ext and *raises* on a bad one; we
    sanitize here so a page's odd filename never fails a capture. Slug is the
    lowercased filename stem with non-alphanumerics collapsed to hyphens (leading
    alnum guaranteed); ext comes from the filename, falling back to the MIME map,
    then `bin`.
    """
    stem, dot, raw_ext = (filename or "").rpartition(".")
    if not dot:  # no extension in the filename
        stem, raw_ext = (filename or ""), ""
    ext = _NON_ALNUM_RE.sub("", raw_ext.lower())
    if ext not in _ALLOWED_EXT:
        ext = _MIME_EXT.get((mime or "").lower().split(";")[0].strip(), "bin")
    slug = _NON_ALNUM_RE.sub("-", stem.lower()).strip("-")[:48].strip("-")
    if not slug or not slug[0].isalnum():
        slug = "media"
    return slug, ext


def _decode_media(items: Sequence[MediaIn]) -> list[MediaItem]:
    """Decode + validate browser-provided media into writer-ready MediaItems.

    Raises HTTP 422 for too many items, undecodable base64, or bytes over the
    per-item / aggregate caps — the boundary refuses bad input rather than
    letting the writer or the filesystem deal with it.
    """
    if len(items) > MAX_MEDIA_COUNT:
        raise HTTPException(
            status_code=422, detail=f"too many media items: {len(items)} > {MAX_MEDIA_COUNT}"
        )
    decoded: list[MediaItem] = []
    total = 0
    for i, item in enumerate(items):
        # Reject on the encoded length BEFORE decoding, so an oversized item is
        # bounded without first materializing its bytes (base64 decodes to ~3/4
        # of its length). The exact decoded size is re-checked below.
        if (len(item.data_base64) * 3) // 4 > MAX_MEDIA_ITEM_BYTES:
            raise HTTPException(
                status_code=422, detail=f"media[{i}] too large: exceeds {MAX_MEDIA_ITEM_BYTES} bytes"
            )
        try:
            data = base64.b64decode(item.data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"media[{i}] is not valid base64: {exc}"
            ) from exc
        if len(data) > MAX_MEDIA_ITEM_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"media[{i}] too large: {len(data)} bytes > {MAX_MEDIA_ITEM_BYTES}",
            )
        total += len(data)
        if total > MAX_MEDIA_TOTAL_BYTES:
            raise HTTPException(
                status_code=422, detail=f"media too large in total: > {MAX_MEDIA_TOTAL_BYTES} bytes"
            )
        slug, ext = _derive_slug_ext(item.filename, item.mime)
        mime = item.mime

        # THE BYTES DECIDE (BUG-008). A `.js` file was fetched, renamed `.pdf` and declared
        # `application/pdf`; it would have passed every hash check forever while being
        # neither a PDF nor evidence. A filename and a Content-Type are hints from an
        # untrusted page — the magic bytes are the file.
        # `capsule.pipeline.sniff` holds the magic-byte table and the full rationale.
        sniffed = sniff(data)
        if sniffed is not None:
            true_ext, true_mime = sniffed
            ext, mime = true_ext, true_mime
        else:
            # We do not recognize these bytes as any archivable record type, so they are
            # stored INERT rather than under a type they might not be. The claimed type is
            # dropped: declaring `application/pdf` over unrecognized bytes is the lie.
            ext, mime = "bin", "application/octet-stream"

        decoded.append(
            MediaItem(
                slug=slug,
                ext=ext,
                data=data,
                source_url=item.source_url,
                mime=mime,
                provenance=item.provenance,
            )
        )
    return decoded


def _mistyped_media(items: Sequence[MediaIn], decoded: Sequence[MediaItem]) -> list[str]:
    """Warn where the browser's claimed type contradicted the actual bytes (BUG-008).

    Corrected, not merely recorded — but a contradiction is worth saying out loud, because
    it usually means the grabber picked up something that is not the record at all (a
    JavaScript plugin, a logo), and a capture folder that quietly holds a mistyped file
    invites exactly the confusion the manifest exists to prevent.
    """
    warnings: list[str] = []
    for claimed, stored in zip(items, decoded):
        claimed_mime = (claimed.mime or "").lower().split(";")[0].strip()
        if not claimed_mime or claimed_mime == stored.mime:
            continue
        warnings.append(
            f"{claimed.filename or 'a media file'} was declared {claimed_mime} but its "
            f"bytes are {stored.mime} — stored as {stored.slug}.{stored.ext}. A checksum "
            "proves integrity, not identity."
        )
    return warnings


def _decode_document(doc: DocumentIn) -> tuple[bytes, str, Optional[str]]:
    """Decode + size-cap + safely-name a browser-provided document.

    Returns (bytes, ext, mime). `ext` comes from the same allow-list as media
    (image/PDF types; active-content and over-long extensions fall back to the
    MIME map, then inert `bin`), so a document's on-disk name is always writer-safe.
    Raises HTTP 422 for undecodable base64 or bytes over the per-item cap.
    """
    if (len(doc.data_base64) * 3) // 4 > MAX_MEDIA_ITEM_BYTES:
        raise HTTPException(
            status_code=422, detail=f"document too large: exceeds {MAX_MEDIA_ITEM_BYTES} bytes"
        )
    try:
        data = base64.b64decode(doc.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"document is not valid base64: {exc}") from exc
    if len(data) > MAX_MEDIA_ITEM_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"document too large: {len(data)} bytes > {MAX_MEDIA_ITEM_BYTES}",
        )
    _slug, ext = _derive_slug_ext(doc.filename, doc.mime)
    return data, ext, doc.mime


class AssertionOut(BaseModel):
    # `id` and `persona_label` let the accept/correct UI target one fact (POST
    # /correction, C5) and group facts by the person they describe. `id` is the
    # assertion's own id; `persona_label` is the label of its persona on the source.
    id: str
    persona_label: str
    field: str
    verbatim_value: str
    evidence_type: str
    unverified: bool


class CaptureResponse(BaseModel):
    capture_folder: str
    verify_ok: bool
    url: str
    gating: str
    record_type: Optional[str]
    media_count: int
    assertions: list[AssertionOut]
    # Problems with what was archived that do NOT invalidate the capture — today,
    # a rendered page-PDF that turns out to be visually blank (BUG-001). The capture
    # still lands (its snapshot and extraction are intact and the bytes are hashed);
    # the researcher is told the render is not usable as evidence, because a `PDF ✓`
    # over a white page is worse than no PDF at all — nobody re-runs a success.
    media_warnings: list[str] = []
    # Why `verify_ok` is false when every byte is intact: this capture does not hold
    # the record it claims (REQ-007 §3.3). Deliberately NOT folded into
    # `media_warnings`, which is documented above as problems that do NOT invalidate
    # the capture — this one does, and flattening the two would teach the operator to
    # skim past both.
    #
    # It has to reach the operator at all, which is the whole point. The zoomed Archion
    # export proves why: at 896px it reads `IMAGE full` (correct — it clears the 800px
    # floor, BUG-006) and `verify FAILED` at the same time, and with only a boolean the
    # two contradict each other with no way to tell which one is actionable.
    verify_shortfall: list[str] = []
    # WHICH page of a multi-image source this capture holds (REQ-007 §1). Absent for a
    # source with no page selector we recognize — never guessed at.
    page_label: Optional[str] = None
    # full | degraded-canvas | index-only-no-image | failed | none — what this
    # capture ACTUALLY got for its record image (REQ-001 R4.3). Never implied: "no
    # image" and "the image failed" are different facts, and only one of them means
    # we have to come back. `none` (REQ-007 §3.2) is a third: we looked at our own
    # bytes and hold no record image.
    image_status: Optional[str] = None
    # For an index-only record: the TRUE source still to be chased, in the record's own
    # words. Capturing the index is not capturing the record (R4.2).
    follow_up: Optional[str] = None
    # REQ-004 §1 — how this capture relates to what the collection already held:
    # "new" (nothing matched), or "repaired" (a matching but NOT verified-complete
    # capture existed; its id is `duplicate_of`). A verified-complete match never
    # reaches this response — it is refused with a 409 carrying `dedup: "blocked"`.
    dedup: str = "new"
    duplicate_of: Optional[str] = None


class NoteRequest(BaseModel):
    """Add a researcher note to a capture that already exists on disk."""

    model_config = ConfigDict(extra="forbid")

    capture_id: str = Field(..., description="The capture folder id to annotate.")
    text: str = Field(..., description="The note — why this was collected (not evidence).")
    author: str = Field("owner", description="Who authored the note.")


class NoteResponse(BaseModel):
    capture_id: str
    note_id: str
    text: str
    author: str
    created_at: str


class CorrectionResponse(BaseModel):
    capture_id: str
    action: str
    source_id: str
    assertion_id: str  # the assertion the correction produced (superseding/new)
    supersedes_id: Optional[str]  # the corrected assertion (None for an add)
    field: str
    # The eval fixture (re)generated from the corrected capture, or None if the
    # fixture write was skipped (e.g. a correction on a document capture).
    fixture: Optional[str]


app = FastAPI(title="Capsule capture backend")

# Distinguish a Chrome extension origin from an ordinary web page. Requiring a
# custom auth header forces a CORS preflight; because a web page's origin never
# matches this regex, the browser never sends its actual POST. Note this admits
# *any* extension origin (all IDs are 32 chars a–p), not only Capsule's — so
# CORS is a coarse first layer; the shared token (require_token) is the real
# gate. `Origin` is browser-set and unspoofable from page JS.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", _TOKEN_HEADER, _EXTENSION_VERSION_HEADER],
)


# Centralized error -> HTTP mapping. Endpoints let these propagate instead of
# each repeating the same try/except: a service raising `ValueError` is a bad
# request (422), a missing capture (`FileNotFoundError`) is 404, storage failures
# (`sqlite3.Error`/other `OSError`) are 500, and a model transport failure is 502.
# NOTE order matters only conceptually: Starlette resolves by the exception's MRO,
# so `FileNotFoundError` (an `OSError` subclass) matches its 404 handler before the
# generic `OSError` 500 handler. `HTTPException` (e.g. the 401/403/503/inline
# cases) is handled by FastAPI itself and never reaches these.


@app.exception_handler(FileNotFoundError)
async def _handle_not_found(request: Request, exc: FileNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"not found: {exc}"})


@app.exception_handler(ValueError)
async def _handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": f"invalid request: {exc}"})


@app.exception_handler(sqlite3.Error)
async def _handle_sqlite_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": f"storage error: {exc}"})


@app.exception_handler(OSError)
async def _handle_os_error(request: Request, exc: OSError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": f"storage error: {exc}"})


@app.exception_handler(anthropic.AnthropicError)
async def _handle_anthropic_error(request: Request, exc: anthropic.AnthropicError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"model call failed: {exc}"})


@app.get("/health")
def health() -> dict:
    # BUG-021: the running backend says which build it is, for triage and support.
    return {"status": "ok", "version": build_version()}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Swallow the browser's automatic favicon request.

    Without a route of its own, `/favicon.ico` falls through to the
    `/{capture_id}/{file_path}` catch-all below: Starlette redirects (307) to get a
    second path segment, then the id validator rejects `favicon.ico` as a malformed
    capture id (422). Harmless, but it prints two alarming lines per page view — and
    a 422 that means nothing teaches you to ignore the 422s that mean something.

    MUST stay declared BEFORE the catch-all: routes match in declaration order.
    """
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def index(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    """Serve the captures index live, with delete/add-note wired to this backend.

    Deliberately UNauthenticated: a browser navigating here can't attach the token
    header, and this is a localhost-only page. The token is embedded so the page's
    own JS can call the token-gated `DELETE`/`POST /note` same-origin. A visited
    web page cannot read this response cross-origin (no CORS grant for its origin)
    nor `<script src>`-include it (the `<!doctype html>` prefix is invalid JS, and
    `nosniff` blocks MIME confusion), so the primary threat — a malicious site's
    JavaScript — is closed.

    SECURITY TRADEOFF (accepted for a localhost, single-user M0 tool): CORS binds
    only *browsers*, so a local non-browser process, or another installed browser
    extension holding broad `host_permissions`, CAN read the embedded token here
    and then drive the mutating endpoints. This is inherent to "serve an
    interactive index from the backend"; it does not expose the token to ordinary
    web pages. With no token configured the index still renders, read-only.
    """
    records = read_all(settings.captures_root)
    html = render_index(
        records,
        generated_at=datetime.now(timezone.utc),
        delete_token=settings.token,
    )
    # nosniff so the HTML can't be reinterpreted as another type; combined with the
    # `<!doctype html>` prefix this makes the token unreadable via <script> include.
    return HTMLResponse(html, headers={"X-Content-Type-Options": "nosniff"})


@app.delete("/capture/{capture_id}")
def delete_capture(
    capture_id: str,
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Move a capture to `captures/_trash/` (recoverable) and re-render the index.

    'Delete' is a recoverable trash: the folder is moved out of the collection so
    it leaves the index, while the append-only DB evidence rows are retained (they
    cannot be deleted by design). A leaked FileNotFoundError/ValueError/OSError
    maps to 404/422/500 via the app exception handlers.
    """
    trashed_to = trash_capture(settings.captures_root, capture_id)
    return {"ok": True, "capture_id": capture_id, "trashed_to": str(trashed_to)}


def _resolve_capture_path(captures_root: Path, capture_id: str, file_path: str) -> Path:
    """Resolve a file requested inside a capture folder, safely.

    `capture_id` is validated by `resolve_capture_folder` (format + containment +
    that a capture actually exists there); `file_path` is then joined and its
    resolved target asserted to stay inside the folder, so no `..`/absolute path
    can escape. Raises `ValueError` (bad id / traversal) or `FileNotFoundError`
    (no such capture) — both mapped to 422/404 by the app exception handlers.
    """
    folder = resolve_capture_folder(captures_root, capture_id).resolve()
    target = (folder / file_path).resolve()
    if target != folder and folder not in target.parents:
        raise ValueError(f"path escapes the capture folder: {file_path!r}")
    return target


def _render_dir_listing(capture_id: str, folder: Path) -> str:
    """A minimal listing of a capture folder — the served analogue of opening the
    folder from disk, so the index's `Local` link resolves when served over HTTP."""
    rows = []
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            rel = path.relative_to(folder).as_posix()
            rows.append(f'<li><a href="{esc(rel)}">{esc(rel)}</a></li>')
    items = "".join(rows) or "<li><em>(empty)</em></li>"
    return (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        f"<title>{esc(capture_id)}</title>"
        "<body style='font:14px system-ui;margin:2rem'>"
        f"<h1 style='font-size:1rem'>{esc(capture_id)}</h1>"
        f"<ul>{items}</ul></body></html>"
    )


# NOTE: the two Ancestry GET routes are declared HERE, deliberately BEFORE the catch-all
# below. Routes match in declaration order, so `/ancestry/next` declared after
# `/{capture_id}/{file_path}` would be swallowed by it and answered as "no such capture
# named 'ancestry'" — exactly the trap `/favicon.ico` fell into. Do not move them down.


class LedgerEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apid: str = Field(..., description='The record, e.g. "1,7602::56510272".')
    error: Optional[str] = Field(None, description="Why it failed, for a `failed` event.")
    # REQ-009 — which EXTRA image of the record the event is about. Absent for the record's
    # default image, so a `started` for a second image can never stall the record itself.
    page_label: Optional[str] = Field(default=None, max_length=60)


@app.post("/ancestry/started")
def ancestry_started(
    req: LedgerEventRequest,
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Mark a capture as IN FLIGHT, before the work begins (BUG-005).

    The only way a capture that dies mid-flight leaves a trace. The one that prompted this
    navigated away while it was still running — the POST never arrived, so the backend
    could not have recorded a failure: it never learned the capture existed. The record
    stayed `seeded`, indistinguishable from one nobody had tried.
    """
    ledger = AncestryLedger(settings.db_path)
    try:
        ledger.record_started(req.apid, page_label=req.page_label)
        return {"ok": True}
    finally:
        ledger.close()


@app.post("/ancestry/failed")
def ancestry_failed(
    req: LedgerEventRequest,
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Record a capture that failed OUTSIDE the backend — so it is visible, not silent.

    An identity mismatch, a render that died, a tab closed mid-capture: the backend cannot
    see any of these. The caller can, and the ledger has always had an `error` column
    waiting for them.
    """
    ledger = AncestryLedger(settings.db_path)
    try:
        ledger.record_failure(
            req.apid, error=req.error or "unspecified failure", page_label=req.page_label
        )
        return {"ok": True}
    finally:
        ledger.close()


@app.get("/ancestry/next")
def ancestry_next(
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    """The next record to mine — but only when it is time (R7.2).

    THIS is the pacing: not a delay the caller is asked to observe, but a queue that will
    not answer early. The agent cannot learn what to capture next without asking, so the
    rate limit is a mechanism rather than a good intention. 429 + Retry-After.

    The record URL is CONSTRUCTED, never fetched — the backend must never touch Ancestry.
    Only the researcher's own authenticated browser does that.
    """
    _require_work_queue()
    ledger = AncestryLedger(settings.db_path)
    try:
        apid = ledger.next_record()
    except TooSoon as exc:
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}
        ) from exc
    except DailyCapReached as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    finally:
        ledger.close()

    if apid is None:
        return {"apid": None, "record_url": None, "done": True}
    collection_id, record_id = apid.split(":")
    return {
        "apid": apid,
        "record_url": (
            f"https://www.ancestry.com/search/collections/{collection_id}/records/{record_id}"
        ),
        "done": False,
    }


@app.get("/ancestry/ledger")
def ancestry_ledger_view(
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    """What has been mined, what has not, and what still owes a follow-up (R6.1)."""
    ledger = AncestryLedger(settings.db_path)
    try:
        rows = ledger.all_latest()
        return {
            "total": len(rows),
            "mined": sum(1 for r in rows if r.mined),
            # `status` distinguishes "tried and vanished" from "not done yet" (BUG-005).
            "stalled": [{"apid": r.apid} for r in rows if r.stalled],
            "unmined": [
                {
                    "apid": r.apid,
                    "event": r.event,
                    "image": r.image_status,
                    "error": r.error,
                    "status": r.status,
                }
                for r in rows
                if not r.mined
            ],
            "follow_ups": [
                {"apid": r.apid, "follow_up": r.follow_up} for r in rows if r.follow_up
            ],
        }
    finally:
        ledger.close()


@app.get("/review/{capture_id}", response_class=HTMLResponse)
def review_transcript(
    capture_id: str,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """The crop-beside-value review page for a capture's latest machine transcript
    (REQ-006 §5.5). Read-only and unauthenticated like `GET /`; its crops load through
    the capture-file route below. MUST stay declared BEFORE that catch-all."""
    from capsule.collection.transcript_review import latest_transcript, render_review

    try:
        folder = resolve_capture_folder(settings.captures_root, capture_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no such capture: {capture_id}") from exc
    doc = latest_transcript(folder)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"no transcript yet — run `capsule transcribe {capture_id}`",
        )
    return HTMLResponse(
        render_review(folder.name, doc), headers={"X-Content-Type-Options": "nosniff"}
    )


@app.get("/{capture_id}/{file_path:path}", response_model=None)
def capture_file(
    capture_id: str,
    file_path: str,
    settings: Settings = Depends(get_settings),
) -> FileResponse | HTMLResponse:
    """Serve a file (or a folder listing) from a capture, so the served index's
    relative `Local`/`Document` links resolve — the backend, not the filesystem,
    is the origin when the index is viewed at http://127.0.0.1:PORT/.

    Unauthenticated like `GET /` (localhost-only, the owner's own archive); the
    same accepted token-disclosure tradeoff applies. A traversing path is 422, an
    unknown capture 404.
    """
    target = _resolve_capture_path(settings.captures_root, capture_id, file_path)
    if target.is_dir():
        return HTMLResponse(_render_dir_listing(capture_id, target))
    if target.is_file():
        return FileResponse(target)
    raise HTTPException(status_code=404, detail=f"no such file: {capture_id}/{file_path}")


# Media we synthesized or downloaded FOR the record — as opposed to media the page
# itself served. Only these are pixel-GATED: a parish register's mostly-blank scan is
# legitimately near-white, and a page-served thumbnail may legitimately be a thumbnail.
# Gating those would cry wolf on real evidence.
# `site:official-download` (REQ-007 §2) is the same thing as `ancestry:download` on every
# other site — the provider's own export, driven by the operator, and the ONLY sanctioned
# full-resolution route on a tile viewer. It belongs in this tuple: it is what the capture
# claims IS the record, so it must be classified and pixel-gated like one. Left out, an
# Archion export would be measured and then judged by nothing.
_RECORD_IMAGE_PROVENANCE = RECORD_IMAGE_PROVENANCE

# Every raster is MEASURED, though — see `_check_record_images`. Extensions are the
# fallback for a file the page declared no MIME for. `pdf` is here because a tile viewer's
# official export is a PDF wrapping the scan, and its embedded raster is exactly the number
# REQ-007 §3.3 needs.
_MEASURABLE_EXTS = frozenset(
    {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "pdf"}
)


# The subset of record provenances that could only have come from a LOGGED-IN session: the
# provider's own export control. A canvas grab is not here — anyone can screenshot a public
# viewer — so it is not evidence of gating.
_PROVIDER_DOWNLOAD_PROVENANCE = ("ancestry:download", "site:official-download")


def _is_record_image(item: MediaItem) -> bool:
    provenance = item.provenance or ""
    return any(provenance.startswith(prefix) for prefix in _RECORD_IMAGE_PROVENANCE)


def _is_provider_download(item: MediaItem) -> bool:
    """Did these bytes come from the site's own subscriber export? (REQ-007 §2)"""
    provenance = item.provenance or ""
    return any(provenance.startswith(p) for p in _PROVIDER_DOWNLOAD_PROVENANCE)


def _looks_like_measurable(item: MediaItem) -> bool:
    """Is this item worth measuring at all? A cheap pre-check, not a guarantee.

    Asked before `measure_record_file`, which logs a warning on anything it cannot read —
    without this, every archived .html or .txt would log one on every capture. PDFs ARE
    measurable now (REQ-007 §2 — a tile viewer's export is one), so they belong here.
    """
    mime = (item.mime or "").lower()
    if mime.startswith("image/") or mime.startswith("application/pdf"):
        return True
    return item.ext.lower() in _MEASURABLE_EXTS


def _with_measured_size(item: MediaItem) -> MediaItem:
    """Stamp the file's REAL measured dimensions onto the item, or leave it unchanged.

    For a PDF that is the biggest raster INSIDE it (REQ-007 §2), so `manifest.media[]` can
    answer "how much detail is in this capture?" for a tile viewer's export as well as for
    a plain image — the question §3.3's floor is built on.
    """
    facts = measure_record_file(item.data, ext=item.ext, mime=item.mime or "")
    return replace(item, width=facts.width, height=facts.height) if facts else item


def _check_record_images(
    media: Sequence[MediaItem],
) -> tuple[list[MediaItem], list[str], Optional[str]]:
    """Pixel-gate the record image(s). Returns (media with sizes, warnings, status).

    REQ-001 R1.4: a 96x95 thumbnail must be a hard FAIL, not a silent success. The status
    is decided by DECODING the bytes, never by trusting the provenance label — if a "full
    download" measures 1370px, the measurement wins.

    The measured size is stamped back onto the MediaItem so it reaches the manifest: the
    capture folder has to be able to say what its image is without the database (R4.3).

    MEASURING and GATING are separated (REQ-007 §3). Every raster is measured, including
    the ones the page itself served, because "how big is the biggest image in this
    capture?" is the question that exposes an Archion capture holding a 689x545 viewer
    preview and nothing else — and answering it should not cost opening every file. Only
    record-provenance images are GATED, though: a page-served thumbnail is legitimately a
    thumbnail, and failing it would cry wolf on real evidence.
    """
    warnings: list[str] = []
    status: Optional[str] = None
    stamped: list[MediaItem] = []
    for item in media:
        if not _is_record_image(item):
            stamped.append(_with_measured_size(item) if _looks_like_measurable(item) else item)
            continue
        name = f"{item.slug}.{item.ext}"
        # PROVENANCE decides full vs degraded (BUG-006): a provider download IS the full
        # resolution, whatever its pixel count — a WWII draft card is a small piece of card,
        # and 1815px is a complete scan of it, not a downscaled census sheet. Pixels remain
        # only as a floor, to catch a "download" that came back as a thumbnail.
        # `ext`/`mime` are passed because a record image is not always a raster FILE: a
        # tile viewer's official export is a PDF wrapping the scan, and PIL cannot open one
        # — without this the single most important file in an Archion capture measures
        # FAILED (REQ-007 §2).
        warning = image_warning(
            item.data, name=name, provenance=item.provenance, ext=item.ext, mime=item.mime or ""
        )
        if warning:
            warnings.append(warning)
        measured = classify_image(
            item.data, provenance=item.provenance, ext=item.ext, mime=item.mime or ""
        )
        stamped.append(_with_measured_size(item))
        # Worst case wins when several record images ride along: a capture is only as good
        # as its weakest evidence, and optimism here is how a bad scan gets filed as mined
        # and never revisited.
        if status is None or _IMAGE_RANK[measured.value] < _IMAGE_RANK[status]:
            status = measured.value
    return stamped, warnings, status


# Worst-first, so a capture carrying both a good and a bad image reports the bad one.
# `none` is NOT in this table on purpose: it is not a grade of record image, it is the
# absence of one, and it is only ever reached when nothing was measured at all.
_IMAGE_RANK = {"failed": 0, "degraded-canvas": 1, "full": 2, "index-only-no-image": 3}


def _derive_image_status(
    *, html: Optional[str], measured: Optional[str], on_ancestry_record: bool
) -> Optional[str]:
    """What this capture actually got — decided from the RECORD, not from the caller.

    BUG-004. This used to obey `image_intent`, the caller's *expectation*. But whether an
    image exists is a *fact about the record*: an image-backed page links to its scan, an
    index-only page does not. Trusting the caller produced two wrong outcomes on the very
    same SSDI record:

      - told "expected", it said `failed` — jamming the queue forever, chasing a scan that
        does not exist;
      - told nothing, it said NOTHING, and the record closed as MINED with no image status
        and no follow-up. We "mined" the Social Security Death INDEX and threw away the
        knowledge that the SS-5 application is the actual source.

    So now the page decides:
      - no image on the record        -> index-only-no-image  (legitimately absent)
      - an image, and we got it       -> whatever it MEASURES (full / degraded / failed)
      - an image, and we did not      -> failed               (we must come back)

    A caller who says "expected" about an index-only record is not an error — it is simply
    mistaken about the record, and the record wins. That also means no operator can
    mislabel a record into a permanently stuck state.

    OFF ANCESTRY (REQ-007 §3.1) we report what we MEASURED and infer nothing. This used to
    return None unconditionally for every other host — so on Archion, Matricula, NLI and
    the rest, everything BUG-006 and BUG-012 built was inert and `verify_ok` degraded to
    "the hashes match". A measured image is a fact about the bytes and is host-independent,
    so it is now reported everywhere. What does NOT generalize is the ABSENCE test:
    `has_record_image` looks for Ancestry's `/imageviewer/collections/...` link, so on any
    other site it would answer "no image on this record" about a page it cannot read.

    Which is why REQ-007 §3.2 splits absence in two, and the split is the whole point:

      - `index-only-no-image` is a claim about the RECORD — it legitimately has no scan.
        Terminal-GOOD: it closes the record, and `dedup._assess` will let it block a
        re-capture. Only Ancestry's markup supports that claim, so only Ancestry makes it.
      - `none` is a fact about THIS CAPTURE — we looked at what we banked and there is no
        record image in it. It asserts nothing about what the record holds, needs no site
        knowledge to be true, and is terminal-BAD: work remains.

    `null` was doing both jobs and a third one ("not applicable"), which is precisely how an
    empty Archion capture read as a clean one. It now means only the third.
    """
    if html is None:
        return None  # a document capture; no record page to read
    if not on_ancestry_record:
        # No absence INFERENCE off Ancestry — but "we hold no record image" is not an
        # inference, it is a report on our own bytes, and it is always safe to make.
        return measured or "none"
    if not has_record_image(html):
        return "index-only-no-image"
    return measured or "failed"


# The provider's own words for what the true source IS. `original_data` is the NARA-style
# line naming the microfilm or the agency file; `source_information` is the fallback.
_FOLLOW_UP_KEYS = ("original_data", "source_information")


def _derive_follow_up(
    outcome: CaptureOutcome, *, image_status: Optional[str], declared: Optional[str]
) -> Optional[str]:
    """For an index-only record: WHAT is the real source we still have to chase? (R4.2)

    Taking the index is not taking the record. Capturing the SSDI without recording that
    the SS-5 application is the actual source loses the only pointer to where the record
    really lives — and roughly a third of the in-scope corpus is index-only, so this is not
    an edge case.

    Derived from the record's OWN `Original data` line, which the extraction already
    captures verbatim as a provider claim — never from a hard-coded table of collections.
    There are 284 databases in play; a lookup table would be wrong for most of them and
    silently stale for the rest.
    """
    if image_status != "index-only-no-image":
        return None
    if declared:
        return declared  # the caller knows better than we do; let them say so
    claims = {c.key: c.verbatim_value for c in outcome.extraction.provider_claims}
    for key in _FOLLOW_UP_KEYS:
        if claims.get(key):
            return claims[key]
    # We know it is an index and we could NOT read what it indexes. Say exactly that: an
    # honest unknown keeps the record open, where a silent None would close it.
    return "UNKNOWN — this is an index; its true source was not stated on the page"


#: How every extension "nothing was armed" reason begins: `background.js`'s default
#: ("… for this capture") and `takeStagedDownload`'s ("no download was armed"), which an
#: ordinary capture gets because it always asks with its own URL as the key. Off Ancestry that
#: is every ordinary page, so it is only worth recording when the record is KNOWN to have an
#: image (`failed`). A test pins this prefix to each extension string.
_NOT_ARMED = "no download was armed"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _missing_image_reason(image_status: Optional[str], skipped: Optional[str]) -> Optional[str]:
    """Why this capture holds no record image, for the manifest and the ledger (REQ-010).

    - `failed`: the record has an image and we did not get it. Always worth saying why, and
      when nothing was armed, the remedy is the one-call image flow (the 57e4eb68 case: a
      plain capture of an image-backed Ancestry record, noted by the researcher as "index
      only" though Ancestry held the scan).
    - `none`: we hold no record image. Worth saying only when a download WAS armed and did
      not arrive (BUG-018, BUG-022); "nothing was armed" is true of every ordinary web page.
    """
    if image_status not in ("failed", "none") or not skipped:
        return None
    reason = " ".join(_CONTROL_CHARS.sub(" ", skipped).split())[:300]
    if not reason:
        return None
    not_armed = reason.startswith(_NOT_ARMED)
    if image_status == "none" and not_armed:
        return None
    if image_status == "failed" and not_armed:
        return (
            f"NO RECORD IMAGE: {reason}. This record has an image: capture it with the "
            "one-call Ancestry image flow to bank the scan."
        )
    return f"NO RECORD IMAGE: {reason}."


def _evidence_image_name(media: Sequence[MediaItem], measured: Optional[str]) -> Optional[str]:
    """The record image's name when the capture holds the record itself, else None (BUG-020)."""
    if measured not in EVIDENCE_IMAGE_STATUSES:
        return None
    for item in media:
        if _is_record_image(item):
            return f"{item.slug}.{item.ext}"
    return None


def _check_rendered_media(
    media: Sequence[MediaItem], *, record_image: Optional[str] = None
) -> list[str]:
    """Warn about a rendered page-PDF that a human could not actually read (BUG-001).

    Only RENDERS are checked (`provenance` = `render:page-pdf`), not media the page
    itself served: a scan of a mostly-blank register page is legitimately near-white,
    and failing it would cry wolf on real evidence. A render, by contrast, is supposed
    to be a picture of the page in view — if it comes out blank, something composited
    over it (an agent's full-viewport glow flattened into an opaque fill) and the file
    is not evidence, however perfect its text layer.

    Never raises and never blocks the capture: the bytes are archived and hashed
    regardless. It is the SUCCESS REPORT that must tell the truth.

    `record_image` names the record image the capture ALSO holds, when one measured
    `full`/`degraded-canvas`. Then a blank render is expected and harmless (BUG-020), and
    the warning says so instead of sending the researcher to re-capture.
    """
    warnings: list[str] = []
    for item in media:
        if item.provenance != "render:page-pdf":
            continue
        name = f"{item.slug}.{item.ext}"
        verdict = looks_blank(item.data)
        if verdict is True:
            warnings.append(blank_render_warning(name, record_image))
        elif verdict is None:
            warnings.append(f"{name} could not be checked for blankness.")
    return warnings


def _record_ledger_failure(
    settings: Settings,
    apid: Optional[str],
    exc: Exception,
    page_label: Optional[str] = None,
) -> None:
    """A capture that reached us and then blew up must not vanish (BUG-005).

    The ledger had an `error` column from the first commit and never wrote to it, so a
    failure looked exactly like a record nobody had got to yet. Recording it must itself
    never raise — a broken ledger write cannot be allowed to mask the real error.
    """
    if not apid:
        return
    try:
        ledger = AncestryLedger(settings.db_path)
        try:
            ledger.record_failure(
                apid, error=f"{type(exc).__name__}: {exc}", page_label=page_label
            )
        finally:
            ledger.close()
    except Exception as inner:  # noqa: BLE001 — never mask the original failure
        _logger.warning("could not record the capture failure in the ledger: %s", inner)


@app.post("/capture", response_model=CaptureResponse)
def capture(
    req: CaptureRequest,
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
    agent: Optional[Agent] = Depends(provide_agent),
    repository: Repository = Depends(get_repository),
    x_capsule_extension_version: Optional[str] = Header(default=None),
) -> CaptureResponse | JSONResponse:
    ext_version = x_capsule_extension_version
    if ext_version is not None and not _EXTENSION_VERSION.fullmatch(ext_version):
        ext_version = None
    # BUG-019 — the backend, not the extension, decides whether a page label is a page
    # identifier. Applied before anything reads it: the dedup key, the ledger and the folder.
    scoped_label = accepted_page_label(req.url, req.page_label, expected_apid=req.expected_apid)
    if scoped_label != req.page_label:
        req = req.model_copy(update={"page_label": scoped_label})
    try:
        return _capture(req, settings, agent, repository, extension_version=ext_version)
    except HTTPException as exc:
        # A refusal is a decision, not a fault — an identity mismatch or an already-mined
        # no-op is the system working. But a 5xx is a real failure and must be visible.
        if exc.status_code >= 500:
            _record_ledger_failure(settings, req.expected_apid, exc, req.page_label)
        raise
    except Exception as exc:
        _record_ledger_failure(settings, req.expected_apid, exc, req.page_label)
        raise


#: REQ-004 §1 atomicity — record identities with a capture currently IN FLIGHT. The
#: check-and-create must have no gap in which a second dispatch of the same record
#: creates a sibling capture: FastAPI runs sync endpoints on a threadpool, so two
#: rapid dispatches are genuinely concurrent. Claiming the identity under a lock
#: BEFORE any work (and releasing in a finally) closes that gap.
_INFLIGHT: set[str] = set()
_INFLIGHT_LOCK = threading.Lock()


def _begin_inflight(identity: str) -> bool:
    with _INFLIGHT_LOCK:
        if identity in _INFLIGHT:
            return False
        _INFLIGHT.add(identity)
        return True


def _end_inflight(identity: str) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT.discard(identity)


def _record_dedup_blocked(
    settings: Settings, req: "CaptureRequest", existing: ExistingCapture
) -> None:
    """Close the ledger over a refused Ancestry duplicate (BUG-017).

    The extension writes `started` before it POSTs, and a refusal is not a failure, so
    without this the refused attempt's `started` is the record's latest event and a mined
    record reads as STALLED. Best-effort: bookkeeping must never turn a refusal into a 500.
    """
    ids = parse_apid(req.expected_apid) if req.expected_apid else None
    if ids is None:
        ids = record_ids_from_url(req.url)
    if ids is None:
        return  # not an Ancestry record: nothing in the ledger to close
    try:
        ledger = AncestryLedger(settings.db_path)
        try:
            ledger.record_dedup_blocked(
                f"{ids[0]}:{ids[1]}",
                capture_id=existing.capture_id,
                page_label=req.page_label,
                fallback=lambda: _held_capture_facts(settings, existing),
            )
        finally:
            ledger.close()
    except Exception as exc:  # noqa: BLE001 — the 409 must still go out
        _logger.warning("could not record the refused duplicate in the ledger: %s", exc)


def _held_capture_facts(settings: Settings, existing: ExistingCapture) -> dict:
    """The held capture's facts, read from its own folder, for a record whose original
    capture never entered the ledger. `existing.complete` means it just verified."""
    facts: dict = {"image_status": existing.image_status, "verify_ok": True}
    try:
        folder = resolve_capture_folder(settings.captures_root, existing.capture_id)
        extraction = json.loads((folder / "extraction.json").read_text(encoding="utf-8"))
        facts["assertion_count"] = len(extraction.get("assertions") or [])
        facts["persona_count"] = len(extraction.get("personas") or [])
    except (OSError, ValueError) as exc:
        _logger.warning("could not count the held capture's evidence: %s", exc)
    return facts


def _dedup_blocked(detail: str, duplicate_of: Optional[str]) -> JSONResponse:
    """The structured 409 for a duplicate (REQ-004 §1.4).

    `detail` stays a human STRING (an older extension renders it verbatim — the
    `[object Object]` lesson); `dedup`/`duplicate_of` ride alongside for the agent.
    """
    return JSONResponse(
        status_code=409,
        content={"detail": detail, "dedup": "blocked", "duplicate_of": duplicate_of},
    )


def _capture(
    req: CaptureRequest,
    settings: Settings,
    agent: Optional[Agent],
    repository: Repository,
    *,
    extension_version: Optional[str] = None,
) -> CaptureResponse | JSONResponse:
    identity = record_identity(req.url, req.expected_apid, req.page_label)
    if not _begin_inflight(identity):
        return _dedup_blocked(
            "a capture of this record is already in flight — one capture per record "
            "at a time; wait for it to finish and re-check.",
            None,
        )
    try:
        return _capture_locked(
            req, settings, agent, repository, extension_version=extension_version
        )
    finally:
        _end_inflight(identity)


def _capture_locked(
    req: CaptureRequest,
    settings: Settings,
    agent: Optional[Agent],
    repository: Repository,
    *,
    extension_version: Optional[str] = None,
) -> CaptureResponse | JSONResponse:
    # A document capture (the page IS a PDF) is archive-only: decode + size-cap the
    # bytes, then write a v2 capture with no model call. An html capture runs the
    # extraction pipeline. Exactly one of html/document is present (request
    # validator). Errors propagate to the app exception handlers (422/500/502); the
    # repository is closed by the get_repository dependency.
    # IDENTITY GUARD (REQ-001 R5) — first, before a single byte is written or a single
    # model call is spent. A capture of the WRONG record, hashed and cited onto a direct
    # ancestor, is worse than no capture at all: it succeeds, so nobody ever re-checks it.
    # 409 (conflict), not 422: the request is well-formed, it is the WORLD that disagrees
    # with it — the page in the browser is not the record that was asked for.
    # Ordered BEFORE the dedup check (review): with a stale expected_apid, dedup would
    # refuse with "already mined: <unrelated id>" when the actual defect is the mismatch.
    mismatch = identity_mismatch(expected_apid=req.expected_apid, url=req.url)
    if mismatch is not None:
        _logger.warning("identity guard refused a capture: %s", mismatch)
        raise HTTPException(status_code=409, detail=mismatch)

    # IDEMPOTENCY (R6.3), generalized by REQ-004 §1: a record already HELD as a
    # verified-complete capture is a refusal, whatever the entry point named it by
    # (declared apid, apid-less Ancestry URL, or any other URL). A partial, failed,
    # or unverifiable prior capture never blocks — that recapture is a REPAIR
    # (BUG-007's screenshot-instead-of-certificate must stay repairable).
    # `remine=true` is the explicit human override.
    dedup = "new"
    duplicate_of: Optional[str] = None
    if not req.remine:
        existing = find_capture(
            req.url,
            req.expected_apid,
            db_path=settings.db_path,
            captures_root=settings.captures_root,
            # REQ-007 §1 — a tile viewer serves every leaf of a register at ONE URL, so
            # without this the collection holds one image per register and refuses the rest
            # as "already mined". Observed live 2026-08-22 on Archion 239254.
            page_label=req.page_label,
        )
        if existing is not None and existing.complete:
            _record_dedup_blocked(settings, req, existing)
            return _dedup_blocked(
                f"already mined: {req.expected_apid or 'this record'} was captured as "
                f"{existing.capture_id} ({existing.image_status or 'no record image expected'}; "
                f"{existing.reason}). Send remine=true to capture it again.",
                existing.capture_id,
            )
        if existing is not None:
            dedup = "repaired"
            duplicate_of = existing.capture_id

    # A document capture (the page IS a PDF) is archive-only: decode + size-cap the
    # bytes, then write a v2 capture with no model call. An html capture runs the
    # extraction pipeline. Exactly one of html/document is present (request
    # validator). Errors propagate to the app exception handlers (422/500/502); the
    # repository is closed by the get_repository dependency.
    media_count = 0
    media_warnings: list[str] = []
    image_status: Optional[str] = None
    image_reason: Optional[str] = None  # REQ-010; set on the html branch only
    follow_up: Optional[str] = None
    if req.document is not None:
        doc_bytes, ext, mime = _decode_document(req.document)
        outcome = run_document_capture(
            req.url,
            document_bytes=doc_bytes,
            ext=ext,
            media_type=mime,
            repository=repository,
            captures_root=settings.captures_root,
            gating=req.gating or GatingStatus.PUBLIC,
            note=req.note,
            note_author=req.note_author,
            extension_version=extension_version,
        )
    else:
        assert req.html is not None  # guaranteed by the request validator
        if agent is None:
            raise HTTPException(
                status_code=503,
                detail="No Anthropic API key (ANTHROPIC_API_KEY) on the server: run `capsule setup`, then "
                "restart `capsule serve`",
            )
        media = _decode_media(req.media)
        media_count = len(media)
        # The bytes disagreed with what the browser called them (BUG-008). Corrected on the
        # way in, and said out loud — a mistyped file usually means the grabber picked up
        # something that is not the record at all.
        mistyped = _mistyped_media(req.media, media)
        # Pixel-gate the record image and state what we actually got (R1.4/R4.3). The
        # bytes are archived either way — they are evidence of what Ancestry served — but
        # the SUCCESS REPORT must tell the truth, which is the whole BUG-001 lesson.
        media, image_probs, measured = _check_record_images(media)
        # Measured FIRST, so a blank page render can be judged against what else the
        # capture holds: beside a full record image it is harmless (BUG-020).
        media_warnings = _check_rendered_media(
            media, record_image=_evidence_image_name(media, measured)
        )
        media_warnings += mistyped
        media_warnings += image_probs
        # A record file the page HELD and we did not get (BUG-007). It is a warning, not a
        # refusal: the snapshot, the extraction and the render are still real evidence and
        # are still worth keeping. But the capture must not READ as complete, because what
        # is missing is the record itself.
        for miss in req.missed_media:
            media_warnings.append(
                f"RECORD FILE NOT CAPTURED — this capture holds a page render, not the "
                f"record: {miss}"
            )
        # Classified from the RECORD PAGE, not from what the caller expected (BUG-004).
        #
        # BUG-012: derive this for ANY Ancestry record page, not only when the caller
        # declared an `expected_apid`. The floating button and a bare `capsule:capture`
        # carry no apid, so this branch was skipped — and on an image-backed record the
        # in-page media sweep banks the page's ~600px thumbnail while the capture reports a
        # clean success, exactly the "believe you hold a record you do not hold" footgun.
        # A record page is one whose URL itself names it (`/collections/<db>/records/<rec>`
        # or the discovery-view shape); keying off that fact — not the caller's expectation
        # — is the same "the record decides" rule as BUG-004.
        #
        # REQ-007 §3.1: the derivation itself is no longer skipped off Ancestry — a
        # measured image is reported on any host. What stays Ancestry-scoped is the
        # "this record has no image" INFERENCE, which only Ancestry's markup supports.
        on_ancestry_record = record_ids_from_url(req.url) is not None
        image_status = _derive_image_status(
            html=req.html,
            measured=measured,
            on_ancestry_record=bool(req.expected_apid or on_ancestry_record),
        )
        image_reason = _missing_image_reason(image_status, req.image_skipped)
        if image_reason:
            media_warnings.append(image_reason)
        # No declared gating means "infer it", NOT "assume public". The browser
        # path is where gated pages arrive — you had to log in to see them.
        #
        # But the inference is weak in a way that matters here: `detect_gating` looks for
        # ENGLISH authenticated-session markers ("log out", "my subscription"), and its own
        # docstring records the limit — a page showing none of them auto-detects PUBLIC.
        # Archion says "Abmelden". Matricula, the German and Austrian diocesan archives, and
        # most of the remaining tile-viewer corpus are not in English either, so the whole
        # class REQ-007 §2 just opened up is the class the inference is worst at.
        #
        # A staged provider download is direct evidence the inference cannot see: nobody
        # reaches a subscriber-only export control without a subscriber session. Treat it as
        # such. This errs GATED — local snapshot only, never republished, never submitted to
        # Wayback — which is the safe direction under plan §11 and the CLAUDE.md hard rule,
        # and it is a floor rather than an override: a caller who explicitly declared its
        # gating is still the authority, because they are the one looking at the page.
        gating = req.gating
        if gating is None and any(_is_provider_download(item) for item in media):
            gating = GatingStatus.GATED
            _logger.info(
                "gating inferred GATED: this capture carries a provider download, so it "
                "came from an authenticated session"
            )
        fetcher = ProvidedContentFetcher(req.html, gating=gating)
        outcome = run_capture(
            req.url,
            fetcher=fetcher,
            agent=agent,
            repository=repository,
            captures_root=settings.captures_root,
            media=media,
            note=req.note,
            note_author=req.note_author,
            # R4.3 — stamped into manifest.json, so the capture folder can say what its
            # record image IS even if the index database is lost or the folder is moved.
            image_status=image_status,
            page_label=req.page_label,
            # BUG-016 — the same list that goes back in the response now also goes into
            # the manifest. It was assembled above (blank render, mistyped bytes, image
            # probes, a record file the page held and we did not get) and, until now,
            # died with the toast that displayed it.
            media_warnings=media_warnings,
            extension_version=extension_version,
        )
    # Record what actually happened (R6.2). The ledger decides `mined` from these facts —
    # notably, a degraded or failed image leaves the record ON the queue, so a
    # screen-resolution scan can never quietly close it forever (constraint 4).
    if req.expected_apid:
        # An index-only record is not done until we have said what it INDEXES. Derived
        # from the record's own `Original data` line, so it works across all 284
        # databases rather than a hard-coded table (BUG-004, R4.2).
        follow_up = _derive_follow_up(
            outcome, image_status=image_status, declared=req.follow_up
        )
        ledger = AncestryLedger(settings.db_path)
        try:
            ledger.record_capture(
                req.expected_apid,
                capture_id=outcome.capture_folder.name,
                image_status=image_status,
                assertion_count=len(outcome.extraction.assertions),
                persona_count=len(outcome.extraction.personas),
                verify_ok=outcome.verify.ok,
                follow_up=follow_up,
                page_label=req.page_label,
                # REQ-010 — on a `captured` event, `error` says why the record image is
                # missing; the capture itself succeeded (owner deference, 2026-10-21).
                error=image_reason,
            )
        finally:
            ledger.close()

    return _to_response(
        outcome,
        media_count=media_count,
        media_warnings=media_warnings,
        image_status=image_status,
        follow_up=follow_up if req.expected_apid else None,
        dedup=dedup,
        duplicate_of=duplicate_of,
    )


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apids: list[str] = Field(..., description="The _APIDs to put on the work queue.")


def _require_work_queue() -> None:
    """403 unless the Ancestry work queue is deliberately enabled (`work_queue_enabled`)."""
    if not work_queue_enabled():
        raise HTTPException(
            status_code=403,
            detail="The Ancestry work queue is off. Set CAPSULE_ENABLE_WORK_QUEUE=1 to enable it.",
        )


@app.post("/ancestry/seed")
def ancestry_seed(
    req: SeedRequest,
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Put records on the work queue (R7.1). Idempotent — re-seeding adds nothing."""
    _require_work_queue()
    ledger = AncestryLedger(settings.db_path)
    try:
        added = ledger.seed(req.apids)
        return {"seeded": added, "total": len(ledger.all_latest())}
    finally:
        ledger.close()


@app.post("/note", response_model=NoteResponse)
def add_note(
    req: NoteRequest,
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
    repository: Repository = Depends(get_repository),
) -> NoteResponse:
    """Annotate a capture that already exists (the later-annotation path).

    Archive-safe: no model call, and the note is EXCLUDED from the capture's
    hashes, so it can be added long after sealing without affecting integrity.
    404 if the capture id names nothing; 422 on a malformed id or blank text
    (mapped by the app exception handlers).
    """
    note = add_note_to_capture(
        repository=repository,
        captures_root=settings.captures_root,
        capture_id=req.capture_id,
        text=req.text,
        author=req.author,
    )
    return NoteResponse(
        capture_id=req.capture_id,
        note_id=note.id,
        text=note.text,
        author=note.author,
        # Normalize to UTC first (matches manifest `_iso_z`): a non-UTC tzinfo
        # would leave a `+hh:mm` offset that the `Z` replace would miss.
        created_at=note.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


@app.post("/correction", response_model=CorrectionResponse)
def submit_correction(
    req: CorrectionRequest,
    _auth: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
    repository: Repository = Depends(get_repository),
) -> CorrectionResponse:
    """Apply a human correction (edit / add / retract) to a sealed capture.

    The correction lands in the append-only DB layer — a superseding or new
    assertion plus a `correction_event` — and NEVER rewrites the sealed capture.
    On success the capture's corrected state is projected into an eval fixture
    (the flywheel: corrected outputs become fixtures). 404 for an unknown capture,
    422 for a malformed request or an unknown/already-superseded target (mapped by
    the app exception handlers; the request body is validated by CorrectionRequest).
    """
    outcome = apply_correction(
        req, repository=repository, captures_root=settings.captures_root
    )
    # Best-effort: the correction is already committed above, so a fixture-write
    # failure (e.g. an `add` on a document capture, which has no snapshot to be a
    # fixture) must not fail the request that already recorded the correction.
    fixture_name: Optional[str] = None
    try:
        dest = write_fixture(
            repository=repository,
            captures_root=settings.captures_root,
            capture_id=req.capture_id,
            fixtures_root=settings.fixtures_root,
        )
        fixture_name = dest.name
    except Exception as exc:  # noqa: BLE001 — flywheel side effect, never fatal
        _logger.warning("could not write eval fixture for %s: %s", req.capture_id, exc)
    return CorrectionResponse(
        capture_id=req.capture_id,
        action=outcome.event.action.value,
        source_id=outcome.source_id,
        assertion_id=outcome.assertion.id,
        supersedes_id=outcome.assertion.supersedes_id,
        field=outcome.event.field,
        fixture=fixture_name,
    )


def _to_response(
    outcome: CaptureOutcome,
    *,
    media_count: int,
    media_warnings: Sequence[str] = (),
    image_status: Optional[str] = None,
    follow_up: Optional[str] = None,
    dedup: str = "new",
    duplicate_of: Optional[str] = None,
) -> CaptureResponse:
    persona_labels = {p.id: p.label for p in outcome.extraction.personas}
    return CaptureResponse(
        media_warnings=list(media_warnings),
        # Taken straight off the VerifyResult rather than recomputed, so the response can
        # never say something different from the folder's own verdict (REQ-007 §3.3).
        verify_shortfall=list(outcome.verify.insufficient),
        page_label=outcome.source.page_label,
        image_status=image_status,
        follow_up=follow_up,
        dedup=dedup,
        duplicate_of=duplicate_of,
        capture_folder=str(outcome.capture_folder),
        verify_ok=outcome.verify.ok,
        url=outcome.source.url,
        gating=outcome.source.gating_status.value,
        record_type=outcome.source.record_type,
        media_count=media_count,
        assertions=[
            AssertionOut(
                id=a.id,
                # Fall back to the raw persona_id if a persona is somehow missing,
                # so the UI still has a stable, non-empty grouping key.
                persona_label=persona_labels.get(a.persona_id, a.persona_id),
                field=a.field,
                verbatim_value=a.verbatim_value,
                evidence_type=a.evidence_type.value,
                unverified=bool(a.annotation and "[unverified" in a.annotation),
            )
            for a in outcome.extraction.assertions
        ],
    )
