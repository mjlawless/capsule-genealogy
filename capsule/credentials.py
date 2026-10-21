"""The two secrets Capsule needs, found in one place: the Anthropic API key and the pairing token.

THE API KEY (release plan v0.1). A researcher installing from source should not have to export
an environment variable in every terminal. `capsule setup` validates the key once and stores it
in the operating system's keyring (macOS Keychain, Windows Credential Manager, Linux Secret
Service). Resolution order, so every existing setup keeps working:

1. `ANTHROPIC_API_KEY` in the environment (a developer, CI, a test),
2. `./.env` (loaded into the environment by the CLI before this runs),
3. the keyring entry `capsule` / `anthropic_api_key`.

A machine with no usable keyring (a headless Linux box) is not an error: the key is simply not
found there, and setup says how to use an environment variable instead.

THE PAIRING TOKEN. `capsule serve` gates `/capture` on a shared secret so that only the
extension, not a web page's JavaScript, can drive captures. It is stable across restarts: an
explicit `CAPSULE_TOKEN` wins, else the saved token file is reused, else one is minted AND
saved. A token that rotated on every restart would 401 the next capture, possibly on a page
that cannot be revisited.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from capsule.config import LEGACY_DATA_DIR, token_path

_logger = logging.getLogger(__name__)

KEYRING_SERVICE = "capsule"
KEYRING_USERNAME = "anthropic_api_key"
API_KEY_ENV = "ANTHROPIC_API_KEY"


def keyring_api_key() -> Optional[str]:
    """The key stored by `capsule setup`, or None (also when no keyring backend exists)."""
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception as exc:  # noqa: BLE001 — no backend, locked keychain, …: not found
        _logger.debug("keyring unavailable: %s", exc)
        return None
    return value.strip() if value and value.strip() else None


def store_api_key(key: str) -> None:
    """Save the key in the OS keyring. Raises the keyring's own error if there is none."""
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key.strip())


def load_api_key() -> Optional[str]:
    """Put the API key into the environment from the keyring, if it is not already there.

    Returns where it came from ("environment", "keyring") or None if there is no key. The
    model client reads the environment, so this is all the rest of Capsule needs.
    """
    if os.environ.get(API_KEY_ENV):
        return "environment"
    key = keyring_api_key()
    if key:
        os.environ[API_KEY_ENV] = key
        return "keyring"
    return None


def validate_api_key(key: str) -> Optional[str]:
    """None if Anthropic accepts the key, else a plain-language reason.

    Lists one model, which proves the key without generating anything or costing tokens. This
    is a credential check, not a model call, so it does not go through `Agent`.
    """
    import anthropic

    try:
        anthropic.Anthropic(api_key=key.strip()).models.list(limit=1)
    except anthropic.AuthenticationError:
        return "Anthropic rejected this key (check that you copied all of it)."
    except anthropic.PermissionDeniedError:
        return "this key is not allowed to use the API (check your Anthropic console)."
    except anthropic.APIConnectionError:
        return "could not reach Anthropic to check the key (are you online?)."
    except anthropic.APIError as exc:
        return f"Anthropic returned an error while checking the key: {exc}"
    return None


def resolve_token(path: Optional[Path] = None) -> tuple[str, str]:
    """`(token, where it came from)`; mints and saves one if there is none yet."""
    path = path or token_path()
    token = os.environ.get("CAPSULE_TOKEN")
    if token:
        return token, "environment (CAPSULE_TOKEN)"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token, f"reused {path}"
    # Before 2026-10-21 `serve` always kept the token at ./data/.capsule-token, even when
    # CAPSULE_DATA_DIR pointed elsewhere. Carry it over rather than mint a new one: the
    # extension still holds the old token, and a new one would 401 every capture (Codex review).
    legacy = LEGACY_DATA_DIR / ".capsule-token"
    try:
        if legacy.resolve() != path.resolve():
            token = legacy.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token, _save(path, token, f"carried over from {legacy} to {path}")
    token = secrets.token_urlsafe(32)
    return token, _save(path, token, f"minted, saved to {path} (stable across restarts)")


def _save(path: Path, token: str, saved: str) -> str:
    """Persist the token (owner-only where the OS supports it); return how it went."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)  # POSIX best-effort; no-op on Windows
        except OSError:
            pass
    except OSError:
        return "COULD NOT PERSIST — will rotate on restart!"
    return saved
