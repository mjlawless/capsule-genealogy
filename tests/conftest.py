"""Shared test guards.

Every `capsule` CLI invocation loads the API key from the OS keyring when the environment has
none (release plan v0.1). Tests must never read, or write, the real keyring of the machine they
run on: a developer who has run `capsule setup` would otherwise have their real key loaded into
the test process. So the keyring is an empty fake by default; tests about the keyring install
their own fake over this one.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    import keyring

    def refuse(*_args, **_kwargs):
        raise RuntimeError("tests never write the real keyring")

    monkeypatch.setattr(keyring, "get_password", lambda *_a, **_k: None)
    monkeypatch.setattr(keyring, "set_password", refuse)


@pytest.fixture(autouse=True)
def _work_queue_on_unless_a_test_says_otherwise(monkeypatch):
    """The Ancestry work queue is OFF by default for users (v0.1.0). The existing queue tests
    exercise it, so the suite turns it on; `tests/test_work_queue_gate.py` turns it off."""
    monkeypatch.setenv("CAPSULE_ENABLE_WORK_QUEUE", "1")


@pytest.fixture(autouse=True)
def _no_real_dotenv(monkeypatch):
    """The CLI loads `./.env` on every command. In a developer's checkout that file holds their
    real API key and their own settings (e.g. CAPSULE_ENABLE_WORK_QUEUE), which must never leak
    into tests. Tests about `.env` point `DOTENV_FILE` back at a file they wrote."""
    import capsule.cli.app as cli

    monkeypatch.setattr(cli, "DOTENV_FILE", cli.Path(".env.never-loaded-by-tests"))


@pytest.fixture(autouse=True)
def _no_ambient_api_key(monkeypatch):
    """Every test starts with no ANTHROPIC_API_KEY and leaves none behind. `set_api_key` (v0.2)
    exports the key into the process environment so it works without a restart; without this,
    one test's key would leak into every test after it."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
