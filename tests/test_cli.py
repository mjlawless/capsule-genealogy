"""Tests for the CLI + real fetcher — capsule/cli/app.py, capsule/pipeline/fetch.py.

All OFFLINE. The real HttpxFetcher is driven through httpx.MockTransport (no
network); the `capsule capture` command is exercised via Typer's CliRunner with
the fetcher/agent factories monkeypatched to fakes, so no request or API call
ever leaves the process. The first genuinely live run is a separate, explicit
step the owner triggers.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx
import pytest
from typer.testing import CliRunner

import tests.test_pipeline as tp
from capsule.contracts.evidence import GatingStatus

_DT = datetime(2026, 7, 8, 14, 32, 5, tzinfo=timezone.utc)
_PAGE = (
    "<html><head><title>Mary Kelly &amp; Family</title>"
    '<meta property="og:site_name" content="Find a Grave"></head>'
    "<body><p>Died 3 March 1881.</p></body></html>"
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- HttpxFetcher (real fetcher, mocked transport) -------------------------


def test_httpxfetcher_public_page_parses_title_and_site():
    from capsule.pipeline.fetch import HttpxFetcher

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=_PAGE)

    fetcher = HttpxFetcher(client=_client(handler), now=lambda: _DT)
    snap = fetcher.fetch("https://www.findagrave.com/memorial/1")

    assert snap.gating is GatingStatus.PUBLIC
    assert snap.title == "Mary Kelly & Family"  # entity unescaped
    assert snap.site_name == "Find a Grave"  # from og:site_name
    assert snap.fetched_at == _DT
    assert len(calls) == 1  # exactly one request — no crawl


def test_httpxfetcher_raises_on_error_status():
    """A 4xx/5xx body is not content — raise instead of spending an API call on it."""
    from capsule.pipeline.fetch import HttpxFetcher

    for status in (403, 404, 500):
        fetcher = HttpxFetcher(client=_client(lambda r, s=status: httpx.Response(s, text="err")))
        with pytest.raises(ValueError, match=f"HTTP {status}"):
            fetcher.fetch("https://site.example/rec/9")


def test_BUG_httpxfetcher_rejects_internal_and_nonhttp_targets():
    """SSRF guard: internal/loopback/link-local hosts and file:// must be refused."""
    from capsule.pipeline.fetch import HttpxFetcher

    called = []

    def handler(request):
        called.append(str(request.url))
        return httpx.Response(200, text=_PAGE)

    for bad in (
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8080/admin",
        "http://localhost/x",
        "http://10.0.0.5/internal",
        "file:///etc/passwd",
    ):
        fetcher = HttpxFetcher(client=_client(handler))
        with pytest.raises(ValueError):
            fetcher.fetch(bad)
    assert called == []  # not one request left the process


def test_BUG_httpxfetcher_blocks_redirect_to_internal_host():
    """A page cannot bounce the fetch to an internal host via redirect."""
    from capsule.pipeline.fetch import HttpxFetcher

    hops = []

    def handler(request):
        hops.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    fetcher = HttpxFetcher(client=_client(handler))
    with pytest.raises(ValueError, match="internal"):
        fetcher.fetch("https://public.example/rec/1")
    # The original page was fetched once; the internal redirect target never was.
    assert hops == ["https://public.example/rec/1"]


def test_httpxfetcher_rejects_oversize_page(monkeypatch):
    from capsule.pipeline import fetch as fetch_mod
    from capsule.pipeline.fetch import HttpxFetcher

    monkeypatch.setattr(fetch_mod, "_MAX_RESPONSE_BYTES", 16)
    fetcher = HttpxFetcher(client=_client(lambda r: httpx.Response(200, text="x" * 100)))
    with pytest.raises(ValueError, match="too large"):
        fetcher.fetch("https://site.example/big")


def test_httpxfetcher_login_redirect_is_gated():
    from capsule.pipeline.fetch import HttpxFetcher

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, text="<title>Sign in</title>")
        return httpx.Response(302, headers={"Location": "https://site.example/login"})

    fetcher = HttpxFetcher(client=_client(handler))
    assert fetcher.fetch("https://site.example/rec/9").gating is GatingStatus.GATED


def test_httpxfetcher_override_beats_detection():
    from capsule.pipeline.fetch import HttpxFetcher

    # A clean 200 would detect PUBLIC; the user's override must win.
    fetcher = HttpxFetcher(
        client=_client(lambda r: httpx.Response(200, text=_PAGE)),
        gating_override=GatingStatus.GATED,
    )
    assert fetcher.fetch("https://site.example/rec/9").gating is GatingStatus.GATED


# -- failed-call sink -------------------------------------------------------


def _agentcall(error):
    from capsule.agent.base import AgentCall

    return AgentCall(
        id="call-1",
        agent="extractor",
        model="claude-opus-4-8",
        system=None,
        prompt="p",
        response="" if error else "r",
        max_tokens=16000,
        error=error,
        created_at=_DT,
    )


def test_failed_call_sink_writes_only_failures(tmp_path):
    from capsule.pipeline.failure_log import failed_call_sink

    sink = failed_call_sink(tmp_path / "captures")
    sink(_agentcall(error=None))  # success — ignored
    assert not (tmp_path / "captures" / "_failed").exists()

    failed = _agentcall(error="APIError: rate limited")
    sink(failed)
    written = tmp_path / "captures" / "_failed" / failed.log_filename
    assert written.is_file()
    assert "rate limited" in written.read_text(encoding="utf-8")


# -- CLI command (CliRunner + fakes) ---------------------------------------

runner = CliRunner()


def _patch_offline(monkeypatch, *, snapshot=None, override_probe=None):
    """Wire the CLI's factories to offline fakes; optionally record the override."""
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.cli import app as cli_app
    from capsule.pipeline.fetch import FakeFetcher

    snap = snapshot or tp._snapshot()

    def fake_build_fetcher(override):
        if override_probe is not None:
            override_probe["override"] = override
        return FakeFetcher(snap)

    def fake_build_agent(captures):
        return Agent(
            name="extractor",
            model="claude-opus-4-8",
            transport=FakeTransport(json.dumps(tp._RAW)),
            now=lambda: _DT,
            new_id=lambda: "call-1",
        )

    monkeypatch.setattr(cli_app, "_build_fetcher", fake_build_fetcher)
    monkeypatch.setattr(cli_app, "_build_agent", fake_build_agent)
    return cli_app


def test_cli_capture_writes_and_reports(tmp_path, monkeypatch):
    cli_app = _patch_offline(monkeypatch)
    result = runner.invoke(
        cli_app.app,
        [
            "capture",
            "https://www.findagrave.com/memorial/1",
            "--db",
            str(tmp_path / "capsule.db"),
            "--captures",
            str(tmp_path / "captures"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "captured:" in result.output
    assert "verify:  OK" in result.output
    assert "death_date" in result.output
    assert "burial" in result.output  # record_type surfaced
    # Auto-detected PUBLIC (no override) must warn so it's never silently trusted.
    assert "auto-detected as PUBLIC" in result.output
    assert (tmp_path / "captures").is_dir()
    assert (tmp_path / "capsule.db").is_file()


def test_cli_gated_flag_forces_override(tmp_path, monkeypatch):
    probe: dict = {}
    cli_app = _patch_offline(
        monkeypatch,
        snapshot=tp._snapshot(gating=GatingStatus.GATED),
        override_probe=probe,
    )
    result = runner.invoke(
        cli_app.app,
        [
            "capture",
            "https://paywall.example/rec/9",
            "--gated",
            "--db",
            str(tmp_path / "capsule.db"),
            "--captures",
            str(tmp_path / "captures"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert probe["override"] is GatingStatus.GATED  # flag reached the fetcher
    assert "copyright-flagged" in result.output


def test_cli_reports_fetch_failure_with_exit_1(tmp_path, monkeypatch):
    from capsule.agent.base import Agent
    from capsule.agent.fake import FakeTransport
    from capsule.cli import app as cli_app

    class _BoomFetcher:
        def fetch(self, url):
            raise httpx.ConnectError("name resolution failed")

    monkeypatch.setattr(cli_app, "_build_fetcher", lambda override: _BoomFetcher())
    monkeypatch.setattr(
        cli_app,
        "_build_agent",
        lambda captures: Agent(
            name="extractor", model="claude-opus-4-8", transport=FakeTransport("x")
        ),
    )
    result = runner.invoke(
        cli_app.app,
        [
            "capture",
            "https://nope.example/rec/1",
            "--db",
            str(tmp_path / "capsule.db"),
            "--captures",
            str(tmp_path / "captures"),
        ],
    )
    assert result.exit_code == 1
    assert "capture failed" in result.output


def test_cli_missing_api_key_exits_cleanly(tmp_path, monkeypatch):
    """No ANTHROPIC_API_KEY must give a clean exit-1 message, not a traceback."""
    from capsule.cli import app as cli_app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # the repo root may hold a real .env; don't load it
    result = runner.invoke(
        cli_app.app,
        [
            "capture",
            "https://site.example/rec/1",
            "--db",
            str(tmp_path / "capsule.db"),
            "--captures",
            str(tmp_path / "captures"),
        ],
    )
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


def test_build_fetcher_and_agent_wire_the_real_objects(tmp_path, monkeypatch):
    """The composition root itself (unpatched) must construct the real seam."""
    from capsule.agent.anthropic_transport import AnthropicTransport
    from capsule.agent.base import Agent
    from capsule.cli import app as cli_app
    from capsule.pipeline.fetch import HttpxFetcher

    fetcher = cli_app._build_fetcher(GatingStatus.GATED)
    assert isinstance(fetcher, HttpxFetcher)
    assert fetcher._gating_override is GatingStatus.GATED

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")
    agent = cli_app._build_agent(tmp_path / "captures")
    assert isinstance(agent, Agent)
    assert agent.name == "extractor"
    assert isinstance(agent._transport, AnthropicTransport)
    assert agent._sink is not None  # failed-call sink is wired


# ---------------------------------------------------------------------------
# `capsule coverage` — was the year I searched actually covered? (P2)
# ---------------------------------------------------------------------------


def _seed_coverage(db_path):
    """One captured coverage claim: Kilmore, R.C. Baptisms, 1845-1880."""
    from capsule.contracts.evidence import (
        ArchiveRecord,
        CoverageClaim,
        Snippet,
        SnippetAnchor,
        Source,
        SourceQuality,
    )
    from capsule.store.repository import Repository

    repo = Repository(db_path)
    repo.add_source(
        Source(
            id="src-1",
            url="https://www.rootsireland.ie/kilmore/coverage",
            title="Kilmore Parish — Records Held",
            gating_status=GatingStatus.PUBLIC,
            copyright_flag=False,
            source_quality=SourceQuality.DERIVATIVE,
            archive=ArchiveRecord(
                local_snapshot="snapshot.html", sha256="c" * 64, captured_at=_DT
            ),
            content_hash="d" * 64,
        )
    )
    repo.add_snippet(
        Snippet(
            id="snip-1",
            source_id="src-1",
            verbatim_text="R.C. Baptisms 1845-1880",
            anchor=SnippetAnchor(kind="text"),
        )
    )
    repo.add_coverage_claim(
        CoverageClaim(
            id="cov-1",
            source_id="src-1",
            snippet_id="snip-1",
            place="Kilmore",
            record_type="R.C. Baptisms",
            year_start=1845,
            year_end=1880,
            created_at=_DT,
        )
    )
    repo.close()


def test_coverage_reports_a_covered_year_with_its_verbatim_snippet(tmp_path):
    from capsule.cli.app import app

    db = tmp_path / "capsule.db"
    _seed_coverage(db)

    result = CliRunner().invoke(
        app, ["coverage", "--place", "kilmore", "--type", "baptism", "--year", "1852", "--db", str(db)]
    )
    assert result.exit_code == 0, result.output
    assert "Kilmore" in result.output
    assert "1845-1880" in result.output
    # The verbatim declaration is shown, not just our parse of it.
    assert "R.C. Baptisms 1845-1880" in result.output


def test_BUG_no_match_never_reads_as_not_covered(tmp_path):
    """Regression: silence must not be mistaken for "the index does not cover it".

    That confusion IS the bug P2 exists to kill — an absence treated as meaningful when
    nothing ever established coverage. A year outside every captured claim must say so
    explicitly, and exit non-zero so a script cannot read it as a clean "no".
    """
    from capsule.cli.app import app

    db = tmp_path / "capsule.db"
    _seed_coverage(db)

    result = CliRunner().invoke(
        app, ["coverage", "--place", "kilmore", "--year", "1901", "--db", str(db)]
    )
    assert result.exit_code == 1
    assert "NOT evidence of no coverage" in result.output


def test_cli_loads_dotenv_from_cwd(tmp_path, monkeypatch):
    """A `.env` in the working directory supplies ANTHROPIC_API_KEY to every command."""
    from capsule.cli import app as cli_app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli_app, "DOTENV_FILE", cli_app.Path(".env"))  # this test is about .env
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-from-dotenv\n", encoding="utf-8")

    assert cli_app._load_dotenv() is True
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-dotenv"


def test_cli_dotenv_never_overrides_the_environment(tmp_path, monkeypatch):
    """An explicitly exported key wins over `.env`, so `KEY=... capsule serve` still works."""
    from capsule.cli import app as cli_app

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-shell")
    monkeypatch.setattr(cli_app, "DOTENV_FILE", cli_app.Path(".env"))  # this test is about .env
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-from-dotenv\n", encoding="utf-8")

    assert cli_app._load_dotenv() is True  # the file WAS read; the environment still wins
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-shell"


def test_cli_without_dotenv_is_a_noop(tmp_path, monkeypatch):
    """No `.env` is not an error — the environment alone is still a valid setup."""
    from capsule.cli import app as cli_app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli_app, "DOTENV_FILE", cli_app.Path(".env"))  # the real name, absent
    monkeypatch.chdir(tmp_path)
    assert cli_app._load_dotenv() is False
    assert "ANTHROPIC_API_KEY" not in os.environ
