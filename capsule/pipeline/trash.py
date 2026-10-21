"""Move a capture out of the collection (recoverable trash) — slice 9.

Deleting a capture collides with the append-only hard rule: every evidence table
is `BEFORE DELETE`-protected, so the DB rows can never be removed. Instead a
"delete" MOVES the capture folder to `captures/_trash/<id>` — the collection
reader skips `_`-prefixed directories, so it vanishes from the index — while the
DB evidence rows are RETAINED untouched. This keeps the append-only guarantee
intact and makes the operation fully reversible: restoring is just moving the
folder back and reindexing.

The move is atomic (`os.replace` within the same collection dir), so a capture is
never left half-trashed. A name collision (the same id trashed twice) is resolved
by suffixing, so an earlier trashed copy is never overwritten.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Union

from capsule.capture.manifest import resolve_capture_folder
from capsule.collection.index import write_index

_logger = logging.getLogger(__name__)

TRASH_DIRNAME = "_trash"


def trash_capture(
    captures_root: Union[str, Path],
    capture_id: str,
    *,
    now: Optional[Callable[[], datetime]] = None,
) -> Path:
    """Move a capture folder to `captures/_trash/`; return its new path.

    Resolves `capture_id` to its folder (trust-boundary validated: a malformed or
    traversing id raises `ValueError`, an unknown one `FileNotFoundError`), moves
    it under `_trash/`, and regenerates the index so the capture no longer appears.
    The append-only DB rows are deliberately NOT touched — the evidence is retained
    and the move is reversible.
    """
    root = Path(captures_root)
    folder = resolve_capture_folder(root, capture_id)

    trash_root = root / TRASH_DIRNAME
    trash_root.mkdir(exist_ok=True)
    dest = trash_root / capture_id
    # Never overwrite a previously trashed copy of the same id — suffix instead.
    if dest.exists():
        i = 2
        while (trash_root / f"{capture_id}.{i}").exists():
            i += 1
        dest = trash_root / f"{capture_id}.{i}"

    os.replace(folder, dest)  # atomic within the same filesystem (same root)

    # Regenerate the human-readable index without the trashed capture. Best-effort
    # like every other index refresh: the capture is already gone from disk.
    try:
        write_index(root, now=now)
    except Exception as exc:  # noqa: BLE001 — projection refresh, never fatal
        _logger.warning("could not regenerate captures index after trash: %s", exc)
    return dest
