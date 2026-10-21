"""Release plan v0.1, wave 2 — what a stranger installing from source needs to just work.

1. The archive lives in ONE place whatever folder you run from: `CAPSULE_DATA_DIR`, else an
   existing `./data/capsule.db` (so no existing checkout is stranded), else the OS per-user
   data folder.
2. The pairing token lives beside the database, not in a hard-coded `./data` (BUG-003's shape).
3. The API key comes from the environment, `.env`, or the OS keyring (`capsule setup`).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def elsewhere(tmp_path, monkeypatch):
    """Run from an empty folder with no CAPSULE_DATA_DIR, and a fake per-user data folder."""
    import platformdirs

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAPSULE_DATA_DIR", raising=False)
    user_dir = tmp_path / "user-data" / "Capsule"
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *a, **k: str(user_dir))
    return tmp_path, user_dir


def test_a_new_install_uses_the_per_user_data_folder(elsewhere):
    from capsule.config import data_dir, default_db_path, token_path

    _, user_dir = elsewhere
    assert data_dir() == user_dir
    assert default_db_path() == user_dir / "capsule.db"
    assert token_path() == user_dir / ".capsule-token"


def test_an_existing_checkout_archive_is_kept(elsewhere):
    """The owner's ./data (hundreds of captures, a launchd service) must not be stranded."""
    from capsule.config import data_dir

    tmp_path, _ = elsewhere
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "capsule.db").write_bytes(b"")
    assert data_dir() == Path("data")


def test_capsule_data_dir_always_wins(elsewhere, monkeypatch):
    from capsule.config import data_dir

    tmp_path, _ = elsewhere
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "capsule.db").write_bytes(b"")
    monkeypatch.setenv("CAPSULE_DATA_DIR", str(tmp_path / "chosen"))
    assert data_dir() == tmp_path / "chosen"


def test_serve_no_longer_hard_codes_the_token_folder():
    source = (ROOT / "capsule" / "cli" / "app.py").read_text(encoding="utf-8")
    assert 'Path("data") / ".capsule-token"' not in source
    assert "resolve_token()" in source


def test_the_token_is_minted_once_and_then_reused(elsewhere, monkeypatch):
    from capsule.credentials import resolve_token

    monkeypatch.delenv("CAPSULE_TOKEN", raising=False)
    _, user_dir = elsewhere
    first, how = resolve_token()
    assert "minted" in how and (user_dir / ".capsule-token").read_text().strip() == first
    again, how = resolve_token()
    assert again == first and how.startswith("reused")
    monkeypatch.setenv("CAPSULE_TOKEN", "explicit")
    assert resolve_token()[0] == "explicit"


class _FakeKeyring:
    def __init__(self, value=None, broken=False):
        self.value, self.broken, self.saved = value, broken, None

    def get_password(self, service, user):
        if self.broken:
            raise RuntimeError("no keyring backend")
        return self.value

    def set_password(self, service, user, value):
        if self.broken:
            raise RuntimeError("no keyring backend")
        self.saved = (service, user, value)


@pytest.fixture()
def fake_keyring(monkeypatch):
    import keyring

    fake = _FakeKeyring()
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return fake


def test_the_environment_key_wins_over_the_keyring(fake_keyring, monkeypatch):
    from capsule.credentials import load_api_key

    fake_keyring.value = "from-keyring"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert load_api_key() == "environment"
    assert os.environ["ANTHROPIC_API_KEY"] == "from-env"


def test_the_keyring_key_is_used_when_the_environment_has_none(fake_keyring, monkeypatch):
    from capsule.credentials import load_api_key

    fake_keyring.value = "sk-ant-from-keyring"
    assert load_api_key() == "keyring"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-keyring"
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # do not leak into later tests


def test_no_keyring_backend_is_not_an_error(fake_keyring):
    from capsule.credentials import load_api_key

    fake_keyring.broken = True
    assert load_api_key() is None
    assert "ANTHROPIC_API_KEY" not in os.environ


def _setup(args, keyboard, monkeypatch, *, problem=None):
    from typer.testing import CliRunner

    import capsule.cli.app as cli

    monkeypatch.setattr(cli, "validate_api_key", lambda key: problem)
    return CliRunner().invoke(cli.app, ["setup", *args], input=keyboard)


def test_setup_checks_and_stores_the_key_and_shows_the_token(
    elsewhere, fake_keyring, monkeypatch
):
    monkeypatch.delenv("CAPSULE_TOKEN", raising=False)
    result = _setup([], "sk-ant-test-key\n", monkeypatch)
    assert result.exit_code == 0, result.output
    assert fake_keyring.saved == ("capsule", "anthropic_api_key", "sk-ant-test-key")
    _, user_dir = elsewhere
    token = (user_dir / ".capsule-token").read_text().strip()
    assert token in result.output
    assert "Load unpacked" in result.output
    assert "sk-ant-test-key" not in result.output  # never echoed


def test_setup_refuses_a_key_anthropic_rejects(elsewhere, fake_keyring, monkeypatch):
    result = _setup([], "sk-ant-wrong\n", monkeypatch, problem="Anthropic rejected this key.")
    assert result.exit_code == 1
    assert fake_keyring.saved is None
    assert "Not saved" in result.output


def test_setup_leaves_an_existing_key_alone_unless_told(elsewhere, fake_keyring, monkeypatch):
    fake_keyring.value = "sk-ant-already"
    result = _setup([], "", monkeypatch)
    assert result.exit_code == 0, result.output
    assert "already set" in result.output and fake_keyring.saved is None
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # the CLI callback loaded it


def test_setup_without_a_keyring_explains_the_alternative(elsewhere, fake_keyring, monkeypatch):
    fake_keyring.broken = True
    result = _setup([], "sk-ant-test-key\n", monkeypatch)
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output and ".env" in result.output


def test_found_cli_path_defaults_are_resolved_after_dotenv(elsewhere, monkeypatch):
    """Codex review: option defaults were computed at import, BEFORE `.env` was loaded, so a
    fresh install with `CAPSULE_DATA_DIR=./data` in `.env` sent CLI commands to the OS folder
    while `serve` used ./data: two archives that each looked complete."""
    from typer.testing import CliRunner

    from capsule.cli.app import app

    import capsule.cli.app as cli

    monkeypatch.setattr(cli, "DOTENV_FILE", Path(".env"))  # this test is about .env
    tmp_path, user_dir = elsewhere
    chosen = tmp_path / "chosen-archive"
    (tmp_path / ".env").write_text(f"CAPSULE_DATA_DIR={chosen}\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["index"])
    assert result.exit_code == 0, result.output
    assert (chosen / "captures" / "index.html").is_file()
    assert not (tmp_path / "data").exists() and not user_dir.exists()


def test_found_an_upgrade_keeps_the_legacy_token(elsewhere, monkeypatch):
    """Codex review: `serve` used to keep the token in ./data even with CAPSULE_DATA_DIR set
    elsewhere. The first restart after upgrading must reuse it, not mint one the extension
    does not have."""
    from capsule.credentials import resolve_token

    tmp_path, _ = elsewhere
    monkeypatch.delenv("CAPSULE_TOKEN", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / ".capsule-token").write_text("the-paired-token\n", encoding="utf-8")
    monkeypatch.setenv("CAPSULE_DATA_DIR", str(tmp_path / "archive"))
    token, how = resolve_token()
    assert token == "the-paired-token" and "carried over" in how
    assert (tmp_path / "archive" / ".capsule-token").read_text().strip() == "the-paired-token"
    assert resolve_token()[1].startswith("reused")
