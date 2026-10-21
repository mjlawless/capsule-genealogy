"""`capsule capture <url>` — the M0 command-line entry point.

This is the composition root: it builds the *real* fetcher and model transport
and drives the offline-tested pipeline. Running it is the first (and only) place
M0 touches the network and spends API tokens, so the compliance posture is
live here — one page in view, no crawl, gated pages local-only, the user driving
the gating decision via `--gated/--public`.

Dependency construction is funnelled through `_build_fetcher` / `_build_agent`
so tests can substitute offline fakes and never hit the network.
"""
from __future__ import annotations

import json
import os
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import anthropic
import httpx
import typer
from dotenv import load_dotenv

from capsule.agent.anthropic_transport import AnthropicTransport
from capsule.agent.base import DEFAULT_MODEL, Agent
from capsule.capture.manifest import sha256_hex
from capsule.collection.index import write_index
from capsule.config import (
    data_dir,
    default_captures_root,
    default_db_path,
    stray_databases,
    work_queue_enabled,
)
from capsule.credentials import (
    keyring_api_key,
    load_api_key,
    resolve_token,
    store_api_key,
    validate_api_key,
)
from capsule.contracts.evidence import GatingStatus, SourceQuality
from capsule.evals.harness import SnapshotMissing, load_fixture, run_eval
from capsule.pipeline.annotate import add_note_to_capture
from capsule.pipeline.dedup import find_capture, find_ingested
from capsule.pipeline.failure_log import failed_call_sink
from capsule.pipeline.fetch import HttpxFetcher
from capsule.pipeline.pipeline import CaptureOutcome, run_capture, run_document_capture
from capsule.pipeline.sniff import MAX_ARTIFACT_BYTES, sniff
from capsule.pipeline.supersede import (
    reconcile_lifecycle,
    reinstate_capture,
    resync_capture,
    supersede_capture,
)
from capsule.store.repository import Repository

app = typer.Typer(help="Capsule — evidence-grade genealogy capture.", no_args_is_help=True)

_AGENT_NAME = "extractor"


DOTENV_FILE = Path(".env")


def _load_dotenv() -> bool:
    """Load `./.env` (relative to the working directory, like `data/`) into the
    environment, without overriding anything already set.

    Returns True when a file was found and read. `ANTHROPIC_API_KEY` lives here
    so the key does not have to be re-exported in every new terminal; a value
    already in the environment always wins, so `ANTHROPIC_API_KEY=... capsule
    serve` and tests that monkeypatch the variable behave exactly as before.
    """
    return load_dotenv(DOTENV_FILE, override=False)


@app.callback()
def _main() -> None:
    """Capsule — evidence-grade genealogy capture.

    A callback (even an empty one) forces Typer to keep sub-command names, so
    the CLI stays `capsule capture <url>` rather than collapsing to a single
    bare command when `capture` is the only command. It also runs before every
    sub-command, which makes it the one place to load `.env`, and then the API key from
    the OS keyring if neither the environment nor `.env` had one (`capsule setup`).
    """
    _load_dotenv()
    load_api_key()


def _build_fetcher(gating_override: Optional[GatingStatus]) -> Any:
    """The real single-page fetcher (patched to a fake in tests)."""
    return HttpxFetcher(gating_override=gating_override)


def _ensure_api_key() -> None:
    """Fail cleanly (not with a traceback) if the model key is unset."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.secho(
            "No Anthropic API key found. Run `capsule setup` to store one, or set "
            "ANTHROPIC_API_KEY.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


def _build_agent(captures_root: Path) -> Agent:
    """The real extraction Agent, with a sink that records failed calls to disk."""
    _ensure_api_key()
    return Agent(
        name=_AGENT_NAME,
        model=DEFAULT_MODEL,
        transport=AnthropicTransport(),
        sink=failed_call_sink(captures_root),
    )


def _close_quietly(obj: Any) -> None:
    """Close `obj` if it is closeable; a cleanup failure must not crash the CLI."""
    close = getattr(obj, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:  # noqa: BLE001 — cleanup, best effort
            typer.secho(f"warning: cleanup failed: {exc}", fg=typer.colors.YELLOW, err=True)


def _format_outcome(outcome: CaptureOutcome) -> str:
    src = outcome.source
    verify = "OK" if outcome.verify.ok else f"FAILED {outcome.verify}"
    gating = src.gating_status.value + (
        "  (copyright-flagged; local snapshot only)" if src.copyright_flag else ""
    )
    lines = [
        f"captured: {src.url}",
        f"  folder:  {outcome.capture_folder}",
        f"  verify:  {verify}",
        f"  gating:  {gating}",
        f"  record:  {src.record_type or '(unclassified)'}",
        f"  assertions ({len(outcome.extraction.assertions)}):",
    ]
    for assertion in outcome.extraction.assertions:
        unverified = assertion.annotation and "[unverified" in assertion.annotation
        flag = "  [UNVERIFIED: not located in source]" if unverified else ""
        lines.append(
            f"    - {assertion.field}: {assertion.verbatim_value} "
            f"[{assertion.evidence_type.value}]{flag}"
        )
    return "\n".join(lines)


@app.command()
def capture(
    url: str = typer.Argument(..., help="The single page in view to capture."),
    gated: Optional[bool] = typer.Option(
        None,
        "--gated/--public",
        help="Force gating instead of auto-detecting (you are looking at the page).",
    ),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
    captures: Path = typer.Option(
        default_factory=default_captures_root, help="Capture-folder collection dir."
    ),
    recapture: bool = typer.Option(
        False,
        "--recapture",
        help="Capture again even though a verified-complete capture already exists.",
    ),
) -> None:
    """Fetch URL, extract its record, and write a verifiable, cited capture."""
    # REQ-004 §1 — the CLI is an entry point too. Refuse (before spending a fetch or
    # a model call) when a verified-complete capture of this record already exists.
    if not recapture:
        existing = find_capture(url, None, db_path=db, captures_root=captures)
        if existing is not None and existing.complete:
            typer.secho(
                f"already captured as {existing.capture_id} ({existing.reason}) — "
                "use --recapture to capture it again.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=3)
    override = None if gated is None else (GatingStatus.GATED if gated else GatingStatus.PUBLIC)
    fetcher = _build_fetcher(override)
    agent = _build_agent(captures)
    repository = Repository(db)
    try:
        outcome = run_capture(
            url,
            fetcher=fetcher,
            agent=agent,
            repository=repository,
            captures_root=captures,
        )
    except (httpx.HTTPError, ValueError, anthropic.AnthropicError, OSError, sqlite3.Error) as exc:
        typer.secho(
            f"capture failed: {type(exc).__name__}: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    finally:
        repository.close()
        _close_quietly(fetcher)
    typer.echo(_format_outcome(outcome))
    if override is None and outcome.source.gating_status is GatingStatus.PUBLIC:
        typer.secho(
            "note: gating was auto-detected as PUBLIC. If this page is actually "
            "behind a login or paywall, re-run with --gated (local snapshot only, "
            "never republished).",
            fg=typer.colors.YELLOW,
            err=True,
        )


@app.command()
def note(
    capture_id: str = typer.Argument(..., help="The capture folder id to annotate."),
    text: str = typer.Argument(..., help="The note — e.g. why you collected this."),
    author: str = typer.Option("owner", help="Who is writing the note."),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
    captures: Path = typer.Option(
        default_factory=default_captures_root, help="Capture-folder collection dir."
    ),
) -> None:
    """Add a researcher note to an already-collected capture (not evidence).

    Notes are your own words — why you kept this, what to revisit later. They are
    stored append-only and EXCLUDED from the capture's hashes, so you can annotate
    anything already collected (a page or a PDF) without disturbing its integrity.
    """
    repository = Repository(db)
    try:
        added = add_note_to_capture(
            repository=repository,
            captures_root=captures,
            capture_id=capture_id,
            text=text,
            author=author,
        )
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        typer.secho(
            f"could not add note: {type(exc).__name__}: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    finally:
        repository.close()
    typer.echo(f"noted on {capture_id}: {added.text}")


@app.command()
def setup(
    replace: bool = typer.Option(False, help="Replace an API key that is already stored."),
    validate: bool = typer.Option(True, help="Check the key with Anthropic before saving it."),
) -> None:
    """First-time setup: store your Anthropic API key and show how to connect the extension.

    The key is checked with Anthropic (without using any tokens) and saved in your
    computer's own password store (Keychain on macOS, Credential Manager on Windows), so you
    never have to type it again. Nothing is sent anywhere else.
    """
    typer.secho("Capsule setup", bold=True)
    typer.echo(f"  Your archive lives in: {data_dir().resolve()}")

    source = "environment" if os.environ.get("ANTHROPIC_API_KEY") else None
    stored = keyring_api_key()
    if (source or stored) and not replace:
        where = "the ANTHROPIC_API_KEY environment variable" if source else "your keyring"
        typer.echo(f"  Anthropic API key: already set (from {where}). Use --replace to change it.")
    else:
        typer.echo(
            "  Paste your Anthropic API key (from console.anthropic.com > API Keys). "
            "It will not be shown."
        )
        key = typer.prompt("  API key", hide_input=True).strip()
        if not key:
            typer.secho("  No key entered; nothing saved.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        if validate:
            problem = validate_api_key(key)
            if problem:
                typer.secho(f"  Not saved: {problem}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)
            typer.echo("  Key checked with Anthropic: OK.")
        try:
            store_api_key(key)
        except Exception as exc:  # noqa: BLE001 — any keyring failure gets the same advice
            typer.secho(
                f"  Could not save the key in your keyring ({exc}). Instead, set the "
                "ANTHROPIC_API_KEY environment variable, or put ANTHROPIC_API_KEY=<key> in a "
                "file named .env in the folder you run Capsule from.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from exc
        typer.echo("  Key saved in your keyring.")

    token, token_source = resolve_token()
    typer.echo("")
    typer.secho("Next:", bold=True)
    typer.echo("  1. Start Capsule:            capsule serve   (leave it running)")
    typer.echo("  2. In Chrome, open chrome://extensions, turn on Developer mode, click")
    typer.echo("     'Load unpacked' and choose the 'extension' folder of this download.")
    typer.echo("  3. Click the Capsule icon and paste this pairing token into the Token field:")
    typer.secho(f"       {token}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"     ({token_source})")


@app.command()
def supersede(
    capture_id: str = typer.Argument(..., help="The capture that has been replaced."),
    by: Optional[str] = typer.Option(None, "--by", help="The capture that replaces it."),
    reinstate: bool = typer.Option(
        False, "--reinstate", help="Withdraw this capture's current supersession instead."
    ),
    resync: bool = typer.Option(
        False,
        "--resync",
        help="Rewrite this capture's lifecycle.json from the database (resolves a conflict).",
    ),
    reason: Optional[str] = typer.Option(None, help="Why — kept with the event."),
    actor: str = typer.Option("owner", help="Who is making this call."),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
    captures: Path = typer.Option(
        default_factory=default_captures_root, help="Capture-folder collection dir."
    ),
) -> None:
    """Mark a capture as superseded by a better one, or reinstate it (REQ-011).

    Nothing is edited or deleted: the old capture keeps its folder, hashes and
    assertions, and gains a lifecycle event saying which capture to cite instead. The
    collection index shows it as superseded. `--reinstate` withdraws the latest
    supersession, again as a new event.
    """
    if sum((bool(by), reinstate, resync)) != 1:
        typer.secho(
            "give exactly one of --by <capture>, --reinstate or --resync",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    repository = Repository(db)
    try:
        if resync:
            resync_capture(repository=repository, captures_root=captures, capture_id=capture_id)
            message = f"re-synced {capture_id}'s lifecycle.json from the database"
        elif reinstate:
            reinstate_capture(
                repository=repository, captures_root=captures, capture_id=capture_id,
                reason=reason, actor=actor,
            )
            message = f"reinstated {capture_id}"
        else:
            supersede_capture(
                repository=repository, captures_root=captures, capture_id=capture_id,
                by_capture_id=by, reason=reason, actor=actor,
            )
            message = f"{capture_id} is superseded by {by}"
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        typer.secho(
            f"could not record it: {type(exc).__name__}: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    finally:
        repository.close()
    typer.echo(message)


@app.command()
def coverage(
    place: Optional[str] = typer.Option(None, help="Place, as the index prints it (e.g. Kilmore)."),
    record_type: Optional[str] = typer.Option(
        None, "--type", help="Record type (e.g. baptism, marriage)."
    ),
    year: Optional[int] = typer.Option(None, help="The year you searched."),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
) -> None:
    """Ask what an index SAYS IT COVERS — before you trust a negative search.

    "I searched Kilmore baptisms for 1852 and found nothing" means nothing on its own.
    It means something only if the index covers 1852. This answers that from the index's
    own declared holdings, as captured from its coverage page.

    Matching is deliberately generous (case-insensitive substring): it surfaces candidate
    claims for YOU to read against their verbatim snippets — it does not adjudicate
    coverage. Finding nothing means only "nothing captured says so", NEVER "not covered".
    """
    repository = Repository(db)
    try:
        claims = repository.coverage_for(place=place, record_type=record_type, year=year)
        if not claims:
            typer.secho(
                "No captured coverage claim matches"
                + (f" {year}" if year is not None else "")
                + ". That is NOT evidence of no coverage — it means nothing captured says "
                "so. Capture the index's coverage/holdings page first.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(code=1)
        for claim in claims:
            span = "-".join(
                str(y) if y is not None else "?" for y in (claim.year_start, claim.year_end)
            )
            snippet = repository.get_snippet(claim.snippet_id)
            typer.echo(
                f"{claim.place} — {claim.record_type} — {span} [{claim.evidence_type.value}]"
            )
            # The verbatim declaration, always. The parsed years are a convenience; what
            # the page actually printed is the evidence.
            typer.echo(f'    "{snippet.verbatim_text}"')
            if claim.annotation:
                typer.secho(f"    ({claim.annotation})", fg=typer.colors.YELLOW)
    finally:
        repository.close()


@app.command("ancestry")
def ancestry(
    action: str = typer.Argument("status", help="status | seed | followups"),
    apid_file: Optional[Path] = typer.Option(
        None, "--file", help="For `seed`: a file of _APIDs, one per line."
    ),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
) -> None:
    """The Ancestry work queue: what is mined, what is not, and what still owes a source.

    `status` — how many records are done, and which are not (with why).
    `seed --file <path>` — put _APIDs on the queue (idempotent; re-seeding adds nothing).
    `followups` — index-only records whose TRUE source is still to be chased: capturing
    the Social Security Death Index is not capturing the SS-5 application.

    A record counts as MINED only when it was captured, verified, yielded assertions, and
    its image is either full-resolution or legitimately absent. A screen-resolution or
    failed image leaves it on the queue — because the one outcome we cannot afford is
    BELIEVING we hold a record we do not hold.
    """
    from capsule.store.ledger import AncestryLedger

    ledger = AncestryLedger(db)
    try:
        if action == "seed":
            if not work_queue_enabled():
                typer.secho(
                    "The Ancestry work queue is off. Set CAPSULE_ENABLE_WORK_QUEUE=1 to enable it.",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(code=1)
            if not apid_file:
                typer.secho("seed needs --file <path>", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)
            lines = [ln.strip() for ln in apid_file.read_text(encoding="utf-8").splitlines()]
            added = ledger.seed([ln for ln in lines if ln and not ln.startswith("#")])
            typer.echo(
                f"seeded {added} new record(s); {len(ledger.all_latest())} on the queue"
            )
            return

        if action == "followups":
            rows = ledger.follow_ups()
            if not rows:
                typer.echo("no outstanding follow-ups")
                return
            for row in rows:
                typer.echo(f"{row.apid} — {row.follow_up}")
            return

        rows = ledger.all_latest()
        # An EMPTY ledger with another capsule.db a directory away almost certainly means
        # we are reading the wrong file (BUG-003). Say so. A confident, silent, wrong zero
        # is the failure mode here — it tells an operator that nothing is done when
        # everything is, and sends them to re-mine records they already hold.
        if not rows:
            for stray in stray_databases(db):
                typer.secho(
                    f"warning: this ledger ({db}) is EMPTY, but another database exists at "
                    f"{stray}. If captures are missing, you are probably reading the wrong "
                    f"file — try --db {stray}",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
        mined = [r for r in rows if r.mined]
        unmined = [r for r in rows if not r.mined]
        stalled = [r for r in rows if r.stalled]
        typer.echo(f"{len(mined)} mined / {len(rows)} known")
        for row in unmined:
            # "Tried and vanished" is a different thing from "not done yet" (BUG-005), and
            # over a long run the operator has to be able to tell them apart. Red for the
            # ones that were attempted and never came back — those want a human's eye.
            colour = (
                typer.colors.RED
                if (row.stalled or row.event == "failed")
                else typer.colors.YELLOW
            )
            typer.secho(f"  UNMINED {row.apid} — {row.status}", fg=colour)
        if stalled:
            typer.secho(
                f"\n{len(stalled)} capture(s) STARTED and never finished — a tab was "
                "navigated away or the browser died mid-capture. Nothing was written (the "
                "record is safe and still queued), but they are worth re-running.",
                fg=typer.colors.RED,
                err=True,
            )
    finally:
        ledger.close()


@app.command()
def query(
    apid: Optional[str] = typer.Option(
        None, help="Ancestry _APID (e.g. 1,7602::56510272) to look up."
    ),
    url: Optional[str] = typer.Option(None, help="Record URL to look up."),
    page_label: Optional[str] = typer.Option(
        None,
        help="Which extra image of the record (REQ-009), e.g. 'image 62073_..._0481-00154'. "
        "Omit for the record's default image.",
    ),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
    captures: Path = typer.Option(
        default_factory=default_captures_root, help="Capture-folder collection dir."
    ),
) -> None:
    """Has this record already been captured? The supported pre-capture check (REQ-004 §4).

    Prints one JSON object: `captured` (a matching capture exists), `capture_id`,
    `complete` (verified-complete — the backend would REFUSE a duplicate capture),
    `image_status`, and a human `reason`. `complete: false` with `captured: true`
    means the record is held but repairable — a recapture would proceed as a repair.
    `other_images` lists the record's EXTRA images the ledger knows (REQ-009), so an apid
    lookup never hides the facing page behind the default one.
    """
    if not apid and not url:
        typer.secho("give --apid and/or --url", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    existing = find_capture(
        url,
        apid,
        db_path=db,
        captures_root=captures,
        page_label=page_label,
        include_ingested=True,
    )
    other_images: list[dict] = []
    if apid and db.exists():
        from capsule.store.ledger import AncestryLedger

        ledger = AncestryLedger(db)
        try:
            other_images = [
                {"page_label": row.page_label, "capture_id": row.capture_id, "mined": row.mined}
                for row in ledger.images(apid)
                if row.page_label != page_label
            ]
        finally:
            ledger.close()
    typer.echo(
        json.dumps(
            {
                "captured": existing is not None,
                "capture_id": existing.capture_id if existing else None,
                "complete": bool(existing.complete) if existing else False,
                "image_status": existing.image_status if existing else None,
                "reason": existing.reason if existing else "no matching capture",
                "ingested": bool(existing.ingested) if existing else False,
                "other_images": other_images,
            }
        )
    )


class IngestProvenance(str, Enum):
    """How an ingested file reached the researcher (REQ-008 §2) — evidentiary, not cosmetic."""

    USER_FILE = "user:file"
    OFFICIAL_DOWNLOAD = "site:official-download"


@app.command()
def ingest(
    file: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True, help="The PDF or image on disk."
    ),
    url: str = typer.Option(
        ...,
        "--url",
        help="Where this file came from — the URL the citation will name (required).",
    ),
    gating: GatingStatus = typer.Option(
        GatingStatus.PUBLIC,
        "--gating",
        help="gated for a paywalled/login export: copyright-flagged, never republished.",
    ),
    provenance: IngestProvenance = typer.Option(
        IngestProvenance.USER_FILE,
        "--provenance",
        help="site:official-download if it came from the source's own export control.",
    ),
    note: Optional[str] = typer.Option(
        None, "--note", help="Why you collected it (stored as a note, not evidence)."
    ),
    record_type: Optional[str] = typer.Option(
        None, "--record-type", help="e.g. death, baptism — you are stating it, not Capsule."
    ),
    source_quality: Optional[SourceQuality] = typer.Option(
        None, "--source-quality", help="original | derivative (default: derivative)."
    ),
    recapture: bool = typer.Option(
        False, "--recapture", help="Ingest again even though these exact bytes are held."
    ),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
    captures: Path = typer.Option(
        default_factory=default_captures_root, help="Capture-folder collection dir."
    ),
) -> None:
    """Turn a PDF or image already on disk into a hashed, cited capture (REQ-008).

    No browser, no fetch, no model call: `--url` is recorded as provenance and is never
    downloaded. Prints one JSON object (capture_id, capture_folder, verify_ok, dedup) so
    an agent can chain it. Exit 3 means these exact bytes from this URL are already held.
    """
    # A hash of an anonymous file is not evidence: the URL IS the provenance, and a
    # file:// URL would only restate the local path.
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        typer.secho(
            f"--url must be the http(s) page the file came from, not {url!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    size = file.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        typer.secho(
            f"{file.name} is {size} bytes; the limit is {MAX_ARTIFACT_BYTES}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    data = file.read_bytes()
    # The bytes decide what the file is (BUG-008); its extension is only a hint.
    sniffed = sniff(data)
    if sniffed is None:
        typer.secho(
            f"{file.name} is not a recognized PDF or image (by its bytes, not its name)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    ext, mime = sniffed
    digest = sha256_hex(data)

    dedup = "new"
    duplicate_of: Optional[str] = None
    if not recapture:
        existing = find_ingested(url, digest, captures_root=captures)
        if existing is not None and existing.complete:
            typer.echo(
                json.dumps(
                    {
                        "capture_id": None,
                        "capture_folder": None,
                        "verify_ok": None,
                        "dedup": "blocked",
                        "duplicate_of": existing.capture_id,
                        "sha256": digest,
                    }
                )
            )
            typer.secho(
                f"already held as {existing.capture_id} (same URL, same bytes; "
                f"{existing.reason}) — use --recapture to ingest it again.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=3)
        if existing is not None:
            dedup, duplicate_of = "repaired", existing.capture_id

    repository = Repository(db)
    try:
        outcome = run_document_capture(
            url,
            document_bytes=data,
            ext=ext,
            media_type=mime,
            repository=repository,
            captures_root=captures,
            gating=gating,
            note=note,
            provenance=provenance.value,
            record_type=record_type,
            source_quality=source_quality,
        )
    except (ValueError, OSError, sqlite3.Error) as exc:
        typer.secho(
            f"ingest failed: {type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=1) from exc
    finally:
        repository.close()
    typer.echo(
        json.dumps(
            {
                "capture_id": outcome.capture_folder.name,
                "capture_folder": str(outcome.capture_folder),
                "verify_ok": outcome.verify.ok,
                "dedup": dedup,
                "duplicate_of": duplicate_of,
                "sha256": digest,
                "media_type": mime,
                "provenance": provenance.value,
                "gating": outcome.source.gating_status.value,
            }
        )
    )


#: The vision transcriber's default model and thinking effort (REQ-006). Owner's call on
#: 2026-09-28, after the model trial: Sonnet 5.5 at high. It was about 6x cheaper and 6x
#: faster than Opus 5 at high on the gold certificate, but it never hedges a doubtful
#: reading, so its misreads surface only through `needs_review` on the review page.
#: Configurable per run; the DOM extractor keeps DEFAULT_MODEL and sends no effort.
TRANSCRIBE_MODEL = "claude-sonnet-5-5"
TRANSCRIBE_EFFORT = "high"


@app.command()
def transcribe(
    capture_id: str = typer.Argument(..., help="The capture folder id whose scan to read."),
    media: Optional[str] = typer.Option(
        None, help="Which scan, when the capture holds more than one (e.g. 1-d-q-1903-0000148.pdf)."
    ),
    model: str = typer.Option(TRANSCRIBE_MODEL, help="Claude model for the vision passes."),
    effort: str = typer.Option(
        TRANSCRIBE_EFFORT, help="Thinking effort: low, medium, high, xhigh or max."
    ),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
    captures: Path = typer.Option(
        default_factory=default_captures_root, help="Capture-folder collection dir."
    ),
) -> None:
    """Read what a captured scan SAYS, field by field (REQ-006).

    Locates each handwritten field, reads it from a full-resolution crop, and
    compares it with the page's own index claims. The result is a READING AID
    stored beside the image — never evidence: nothing is written to the
    assertions, and a disagreement with the index is reported, not resolved.
    Every run is kept; re-running adds a new transcript. On the default (Sonnet 5.5,
    effort high), roughly $0.10 and under a minute per two-page certificate.
    """
    from capsule.pipeline.transcribe_capture import transcribe_capture

    _ensure_api_key()
    transport = AnthropicTransport(effort=effort)
    sink = failed_call_sink(captures)
    agents = {
        name: Agent(name=name, model=model, transport=transport, sink=sink)
        for name in ("layout", "transcriber", "reconciler")
    }
    repository = Repository(db)
    try:
        outcome = transcribe_capture(
            repository=repository,
            captures_root=captures,
            capture_id=capture_id,
            layout_agent=agents["layout"],
            read_agent=agents["transcriber"],
            reconcile_agent=agents["reconciler"],
            media=media,
        )
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        typer.secho(f"could not transcribe: {type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        repository.close()

    t = outcome.transcription
    review = [f for f in outcome.fields if f.needs_review]
    typer.echo(f"transcript {t.id} [{t.status}] of {t.media_filename}")
    typer.echo(f"  {len(outcome.fields)} fields, {len(review)} to review against their crops")
    for comparison in outcome.comparisons:
        if comparison.verdict == "conflict":
            typer.secho(
                f"  CONFLICT  index {comparison.index_value!r}  vs  image "
                f"{comparison.transcript_value!r} — {comparison.explanation}",
                fg=typer.colors.YELLOW,
            )
    for problem in t.problems:
        typer.echo(f"  problem: {problem}")
    typer.echo(f"  folder:  {outcome.transcript_folder}")
    typer.echo(f"  review:  http://127.0.0.1:8765/review/{outcome.capture_folder.name}  (while `capsule serve` runs)")


@app.command("eval-transcript")
def eval_transcript(
    gold_dir: Path = typer.Option(Path("evals/transcripts"), help="Transcript gold files (*.json)."),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
    captures: Path = typer.Option(
        default_factory=default_captures_root, help="Capture-folder collection dir."
    ),
) -> None:
    """Score each capture's LATEST stored transcript against hand-read gold (REQ-006).

    No model call — run `capsule transcribe <capture>` first to produce a transcript.
    Fails (exit 1) on a confident wrong or missing HARD field (the parents' names),
    or a missing expected image-vs-index conflict. `honest` = marked uncertain,
    which is a pass; `disputed` = gold awaiting the owner, never scored.
    """
    from capsule.evals.transcript_eval import load_gold, score_transcript

    golds = [load_gold(p) for p in sorted(gold_dir.glob("*.json"))]
    repository = Repository(db)
    all_passed = True
    try:
        for gold in golds:
            source_id = _source_id_for_capture(captures, gold.capture_id)
            runs = repository.transcriptions_for_source(source_id) if source_id else []
            if not runs:
                typer.secho(f"{gold.name}: no stored transcript — run `capsule transcribe {gold.capture_id}`",
                            fg=typer.colors.YELLOW)
                all_passed = False
                continue
            latest = runs[-1]
            score = score_transcript(
                gold,
                repository.fields_for_transcription(latest.id),
                comparisons=repository.comparisons_for_transcription(latest.id),
            )
            verdict = "PASS" if score.passed else "FAIL"
            typer.secho(f"{gold.name}: {verdict}  {score.counts}  (transcript {latest.id})",
                        fg=typer.colors.GREEN if score.passed else typer.colors.RED)
            for r in score.results:
                if r.outcome != "exact":
                    flag = " [HARD]" if r.hard else ""
                    typer.echo(f"  {r.outcome:8} {r.label}{flag}: expected {r.expected!r}, got {r.got!r}"
                               + (f" — {r.disputed}" if r.disputed else ""))
            for value in score.conflicts_missing:
                typer.echo(f"  missing conflict against index value {value!r}")
            all_passed &= score.passed
    finally:
        repository.close()
    if not all_passed:
        raise typer.Exit(code=1)


def _source_id_for_capture(captures: Path, capture_id: str) -> Optional[str]:
    """The source id recorded in a capture's manifest, or None if it can't be read."""
    manifest = captures / capture_id / "manifest.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))["source"]["id"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None


@app.command("index")
def reindex(
    captures: Path = typer.Option(
        default_factory=default_captures_root, help="Capture-folder collection dir."
    ),
    db: Path = typer.Option(default_factory=default_db_path, help="SQLite evidence index path."),
) -> None:
    """Regenerate `captures/index.html` — the human-browsable index of the collection.

    A pure projection of the capture folders; safe to run anytime. Captures also
    refresh it automatically, so this is for rebuilding after a manual change or
    a fresh clone of a `captures/` directory. With an evidence database present, each
    capture's `lifecycle.json` (REQ-011) is first re-synced from it.
    """
    if db.exists():
        repository = Repository(db)
        try:
            reconcile_lifecycle(repository, captures)
        finally:
            repository.close()
    path = write_index(captures)
    typer.echo(f"wrote {path}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address (loopback only)."),
    port: int = typer.Option(8765, help="Port for the localhost capture backend."),
) -> None:
    """Run the localhost backend the browser extension posts the page in view to."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho(
            f"refusing to bind non-loopback host {host!r} — the backend is localhost only.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    import uvicorn

    # Gate /capture on a shared secret so only the extension — not any web page's
    # JavaScript — can drive captures against this localhost backend.
    #
    # Token resolution is STABLE ACROSS RESTARTS by design: an explicit
    # CAPSULE_TOKEN wins; else a persisted token file is reused; else one is minted
    # AND SAVED. The failure this prevents is real, not cosmetic — an ephemeral
    # token silently rotates on every restart, so the extension's saved token goes
    # stale and the next Capture click 401s. That click may be on an irreplaceable
    # page (RootsIreland is sunsetting; some registers exist nowhere else), so a
    # rotated token is a risk to the evidence itself. `os.environ` hands the token
    # to the in-process uvicorn app, which reads CAPSULE_TOKEN.
    token, token_source = resolve_token()
    os.environ["CAPSULE_TOKEN"] = token

    typer.echo(f"Capsule backend on http://{host}:{port}  (POST /capture)")
    typer.secho(
        "Auth token — paste this into the Capsule extension (Token field):",
        fg=typer.colors.CYAN,
        err=True,
    )
    typer.secho(f"  {token}", fg=typer.colors.CYAN, bold=True, err=True)
    typer.secho(f"  (source: {token_source})", fg=typer.colors.BLUE, err=True)
    uvicorn.run("capsule.backend.app:app", host=host, port=port)


def _fixture_dirs(root: Path, only: Optional[str]) -> list[Path]:
    if not root.is_dir():
        return []
    dirs = sorted(d for d in root.iterdir() if d.is_dir())
    return [d for d in dirs if d.name == only] if only else dirs


@app.command("eval")
def run_evals(
    fixtures: Path = typer.Option(
        Path("evals/fixtures"), help="Directory of eval fixtures (one folder each)."
    ),
    only: Optional[str] = typer.Option(None, help="Run only the fixture with this name."),
) -> None:
    """Score the current extractor against hand-labeled gold captures.

    Runs the single extraction call per fixture (this spends API tokens) and
    prints a PASS/FAIL report. Exits non-zero if any runnable fixture regresses,
    so it can gate a prompt/model change. Fixtures whose local `snapshot.html`
    is absent (it stays gitignored) are skipped with a warning, not failed.
    """
    dirs = _fixture_dirs(fixtures, only)
    if not dirs:
        typer.secho(f"no fixtures found under {fixtures}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)

    _ensure_api_key()
    agent = Agent(name=_AGENT_NAME, model=DEFAULT_MODEL, transport=AnthropicTransport())

    regressions = 0
    ran = 0
    for fixture_dir in dirs:
        try:
            fixture = load_fixture(fixture_dir)
        except SnapshotMissing as exc:
            typer.secho(f"skip {fixture_dir.name}: {exc}", fg=typer.colors.YELLOW, err=True)
            continue
        except ValueError as exc:
            # Corrupt committed gold is a real error, not a benign skip.
            typer.secho(f"[ERROR] {fixture_dir.name}: {exc}", fg=typer.colors.RED, err=True)
            regressions += 1
            continue
        try:
            report = run_eval(fixture, agent=agent)
        except (ValueError, anthropic.AnthropicError) as exc:
            typer.secho(
                f"[ERROR] {fixture_dir.name}: {type(exc).__name__}: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            regressions += 1
            continue
        ran += 1
        typer.secho(report.summary(), fg=(typer.colors.GREEN if report.ok else typer.colors.RED))
        if not report.ok:
            regressions += 1

    if ran == 0:
        typer.secho(
            "no fixtures were runnable (need a local snapshot.html — re-capture to seed).",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if regressions:
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
