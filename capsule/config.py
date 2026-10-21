"""Where Capsule's data lives — resolved in ONE place (BUG-003).

The CLI defaulted to `./capsule.db` while `capsule serve` wrote to `./data/capsule.db`.
Both files had every table, so nothing errored: `capsule ancestry status` simply read an
empty ledger and answered **"0 mined / 0 known"** for a record the backend had, seconds
earlier, refused to re-capture as *already mined*. A confident, silent, wrong zero.

That is worse than a crash. `status` is the operator's source of truth for a long
run — it is what answers "what is left?" — and it was answering "nothing is done", always.
A seeded queue would have landed in the wrong file too, and `/ancestry/next` would have
come back empty forever.

So there is now exactly ONE answer to "where is the database", and both the CLI and the
backend ask it. `CAPSULE_DATA_DIR` moves them together, or not at all.

WHERE, BY DEFAULT (release plan v0.1, 2026-10-21). It used to be `./data`, relative to
wherever the command ran, which is fine in a developer's checkout and a trap for anyone else:
run `capsule` from another folder and you are silently working in a new, empty archive. Now:

1. `CAPSULE_DATA_DIR`, if set, always wins.
2. An existing `./data/capsule.db` is kept: every checkout that already holds an archive
   (the owner's, with hundreds of captures and a launchd service started from the repo)
   keeps using it, so nothing is stranded or moved.
3. Otherwise the operating system's per-user data folder (`platformdirs`): on macOS
   `~/Library/Application Support/Capsule`, on Windows `%LOCALAPPDATA%\\Capsule`, on Linux
   `~/.local/share/Capsule`. The same answer from every working directory.
"""
from __future__ import annotations

import os
from pathlib import Path

import platformdirs

#: The legacy, working-directory-relative archive (rule 2 above).
LEGACY_DATA_DIR = Path("data")


def data_dir() -> Path:
    """The root of the archive: captures, the index DB, everything."""
    explicit = os.environ.get("CAPSULE_DATA_DIR")
    if explicit:
        return Path(explicit)
    if (LEGACY_DATA_DIR / "capsule.db").is_file():
        return LEGACY_DATA_DIR
    return Path(platformdirs.user_data_dir("Capsule", appauthor=False))


def work_queue_enabled() -> bool:
    """Is the Ancestry work queue on? Off unless `CAPSULE_ENABLE_WORK_QUEUE` is set (v0.1.0).

    The queue (`/ancestry/seed`, `/ancestry/next`, `capsule ancestry seed`) paces an agent
    through a list of records in the researcher's own logged-in session. That is the owner's
    research workflow, and on a subscription site it is the researcher's call to make under
    that site's terms, so a public install does not offer it until it is deliberately turned
    on (owner ruling, 2026-10-21). Capturing the record you are viewing, including the
    one-click Ancestry image capture, does not depend on it.
    """
    return os.environ.get("CAPSULE_ENABLE_WORK_QUEUE", "").strip().lower() in {"1", "true", "yes"}


#: The Chrome extension IDs allowed to pair without a pasted token (v0.2). The first is the
#: unpacked extension's: `extension/manifest.json` carries a public `key`, so Chrome gives it
#: this same ID on every machine (a test pins the two together). A Chrome Web Store build gets
#: its own ID; add it with CAPSULE_EXTENSION_IDS (comma-separated).
CAPSULE_EXTENSION_ID = "jalijanpiopkphjagoiofcofclmmmaan"


def pairable_extension_ids() -> frozenset[str]:
    extra = os.environ.get("CAPSULE_EXTENSION_IDS", "")
    return frozenset({CAPSULE_EXTENSION_ID} | {i.strip() for i in extra.split(",") if i.strip()})


def token_path() -> Path:
    """Where `capsule serve` keeps the extension pairing token (beside the database)."""
    return data_dir() / ".capsule-token"


def default_db_path() -> Path:
    return data_dir() / "capsule.db"


def default_captures_root() -> Path:
    return data_dir() / "captures"


def stray_databases(db_path: Path) -> list[Path]:
    """Other `capsule.db` files near this one — the shape of the BUG-003 mistake.

    Used to turn a confident zero into an honest question. If the ledger a command is
    reading is EMPTY and another capsule.db sits a directory away, the overwhelmingly
    likely explanation is that we are reading the wrong file — and saying so is far more
    useful than reporting that no work has ever been done.
    """
    db_path = Path(db_path)
    candidates = [Path("capsule.db"), data_dir() / "capsule.db", Path("data") / "capsule.db"]
    found: list[Path] = []
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved == db_path.resolve():
                continue
            if resolved in {f.resolve() for f in found}:
                continue
            found.append(candidate)
        except OSError:
            continue
    return found
