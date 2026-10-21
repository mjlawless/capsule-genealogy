"""The version a capture records about the code that made it (BUG-021).

`pyproject.toml` carries the PRODUCT version, one number for the backend and the extension
released together (`extension/manifest.json` must match it; a test enforces that). On top of
it, a git checkout adds the exact commit, `0.1.0+g1a2b3c4`, with `.dirty` when the working
tree has uncommitted changes. That suffix is what answers the triage question every manifest
used to leave open: "was this captured before or after the fix?"

The suffix is best-effort. A download from GitHub's "Download ZIP" has no `.git`, and a
machine may have no `git` binary; both fall back to the plain product version, never to an
error. Computed once per process, because `capsule serve` runs for days and a capture must
not pay for a subprocess. The backend calls it at import, so the cached answer describes the
code the server actually loaded, not whatever the checkout holds when the first capture
arrives.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from importlib import metadata
from pathlib import Path

#: The repository root, if this package is running from a checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The paths whose uncommitted changes make a build `.dirty`.
_CODE_PATHS = ("capsule", "extension", "pyproject.toml", "uv.lock")


def product_version() -> str:
    """The released product version, from the installed package metadata."""
    try:
        return metadata.version("capsule")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _git(*args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_suffix() -> str | None:
    """`g<sha>` or `g<sha>.dirty` for a git checkout, else None."""
    if not (_REPO_ROOT / ".git").exists():
        return None
    sha = _git("rev-parse", "--short", "HEAD")
    if not sha:
        return None
    # Only the code that runs decides "dirty": an edited doc or editor setting does not
    # change what a capture was made by, and would otherwise mark every capture.
    status = _git("status", "--porcelain", "--untracked-files=no", "--", *_CODE_PATHS)
    return f"g{sha}.dirty" if status else f"g{sha}"


@lru_cache(maxsize=1)
def build_version() -> str:
    """The product version plus, from a checkout, the commit: `0.1.0+g1a2b3c4[.dirty]`."""
    suffix = git_suffix()
    return f"{product_version()}+{suffix}" if suffix else product_version()
