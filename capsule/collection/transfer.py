"""Move an archive between computers: `capsule export` packages one, `capsule import` merges one.

The owner's research archive lives on one computer and a fresh install on another; the same
need arrives for anyone who buys a new computer. Copying `capsule.db` and `captures/` by hand
gets three things wrong, and this module exists to get them right:

- **A live database.** The server may be mid-capture. A file copy can miss the write-ahead
  log or catch half a transaction, so export takes a snapshot with SQLite's backup API, and
  packages only the folders whose source row is in that snapshot.
- **Two archives, not one.** The target usually holds captures of its own. Import MERGES:
  every table is keyed by a globally unique id, so a row the target lacks is added and a row
  it has is left alone. Nothing is ever overwritten, moved or deleted, which is the store's
  append-only rule applied across computers. Re-running an import adds only what is new, so
  export and import again later pulls the other computer's newer captures across.
- **Half an import.** Folders are copied under dot-prefixed staging names (the collection
  ignores those), the rows commit in ONE transaction with foreign keys checked, and only then
  are the folders renamed into place. A failure before the commit leaves the target as it
  was; a crash after it is repaired by the next run, which finds the rows present and the
  folders missing.

A folder trashed on the source arrives in the target's `_trash/`. A folder the target already
holds is never touched, so a capture trashed on one computer after an earlier import stays
active on the other: the import reports it rather than acting on it. The same rule decides a
capture marked replaced or reinstated differently on each computer: this computer's history
is kept, the other's events for it are not imported, and the import says so.

KNOWN LIMITS (accepted after 17 adversarial-review rounds; the supported use is exporting one
computer's archive and importing it on another, with nothing else importing at the time):
- Moving or trashing a capture on the TARGET while an import runs is not coordinated with it
  (the lock covers imports only). Export detects a move on its own side and fails.
- Rival corrections of one fact refuse the import; the correction loop that could resolve
  them is not built, and no archive has corrections yet.
- Importing straight from an archive FOLDER assumes nothing is writing to it: a capture moved
  there mid-import can arrive as rows without files. Use `capsule export` for a live archive;
  an export file never changes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Optional

from capsule.capture.manifest import verify_capture
from capsule.pipeline.trash import TRASH_DIRNAME
from capsule.version import product_version

FORMAT = "capsule-archive/v1"
META_NAME = "capsule-archive.json"
DB_NAME = "capsule.db"
CAPTURES = "captures"
STAGING_PREFIX = ".importing-"

# Windows refuses these in a file name, and a full path much past 250 characters.
_WINDOWS_FORBIDDEN = re.compile(r'[<>:"|?*\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)
_MAX_PATH = 250
#: The parts of a sealed capture that grow after it is made: a transcription run, an agent
#: log. Both are outside its hashes and append-only, so a file the target lacks there is
#: added (never one it has). Everything else in a held folder is left exactly as it is;
#: `notes.json` and `lifecycle.json` are rebuilt from the merged store instead.
_GROWING_DIRS = ("transcript", "agent_log")
_PART_SUFFIX = ".capsule-importing"
#: Only Windows has those limits; elsewhere the checks would refuse files that store fine.
_ON_WINDOWS = os.name == "nt"


class TransferError(Exception):
    """The archive cannot be exported or imported; nothing was changed."""


@dataclass(frozen=True)
class Folder:
    """One capture folder of an archive."""

    name: str
    trashed: bool
    path: Path

    @property
    def relative(self) -> str:
        return f"{TRASH_DIRNAME}/{self.name}" if self.trashed else self.name


@dataclass
class ImportPlan:
    source_folders: int = 0
    new_folders: list[Folder] = field(default_factory=list)
    present: int = 0
    conflicts: list[str] = field(default_factory=list)
    trashed_elsewhere: list[str] = field(default_factory=list)
    new_rows: dict[str, int] = field(default_factory=dict)
    #: Files a capture both archives hold gained on the other computer, (from, to).
    new_files: list[tuple[Path, Path, Path]] = field(default_factory=list)  # (from, to, its capture)
    problems: list[str] = field(default_factory=list)
    #: Folders with no row in the source's database snapshot: left for the next import.
    not_in_database: list[str] = field(default_factory=list)
    #: The transcription runs the source snapshot holds (only those are copied).
    runs: set[str] = field(default_factory=set)
    #: New database records with no capture folder in either archive.
    records_without_folder: int = 0
    #: Captures marked replaced/reinstated differently on each computer: this one's kept.
    lifecycle_kept_here: list[str] = field(default_factory=list)

    @property
    def nothing_new(self) -> bool:
        return not (self.new_folders or self.new_rows or self.new_files)


@dataclass
class ImportResult:
    plan: ImportPlan
    #: False for a dry run, a declined confirmation, or nothing to add: nothing was written.
    applied: bool = False
    added_folders: int = 0
    added_files: int = 0
    notes_refreshed: int = 0
    #: Folders an interrupted earlier import had committed but not yet put in place.
    recovered: int = 0
    not_verified: list[str] = field(default_factory=list)


@dataclass
class ExportResult:
    path: Path
    sources: int
    folders: int
    skipped: list[str]


# -- reading an archive --------------------------------------------------------


def _source_id(folder: Path) -> Optional[str]:
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    source = manifest.get("source") if isinstance(manifest, dict) else None
    sid = source.get("id") if isinstance(source, dict) else None
    return sid if isinstance(sid, str) else None


def _sealed_files(folder: Path) -> Optional[set[str]]:
    """The manifest plus every file its hashes list, or None when it cannot be read."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        listed = (manifest.get("hashes") or {}).get("files") or {}
    except (OSError, ValueError, AttributeError):
        return None
    return {"manifest.json", *listed} if isinstance(listed, dict) else None


def capture_folders(captures_root: Path) -> Iterator[Folder]:
    """Every capture folder, active and trashed. Staging and other `_`/`.` names are not."""
    if not captures_root.is_dir():
        return
    for child in sorted(captures_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name == TRASH_DIRNAME:
            for trashed in sorted(child.iterdir()):
                if trashed.is_dir() and not trashed.name.startswith("."):
                    yield Folder(trashed.name, True, trashed)
        elif not child.name.startswith("_"):
            yield Folder(child.name, False, child)


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    # A URI keeps the source untouched; as_uri() quotes spaces and Windows drive letters.
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    return [r[0] for r in rows]


def _columns(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[str]]:
    """(all columns, primary-key columns in key order)."""
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = [row[1] for row in info]
    keys = [row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    return columns, keys


# -- export ----------------------------------------------------------------------


def export_archive(db_path: Path, captures_root: Path, out_path: Path) -> ExportResult:
    """Package the archive as one zip: a consistent database snapshot plus its folders."""
    db_path, captures_root, out_path = Path(db_path), Path(captures_root), Path(out_path)
    if not db_path.is_file():
        raise TransferError(f"no archive database at {db_path}")
    if out_path.exists():
        raise TransferError(f"{out_path} already exists; choose a new file name")
    if out_path.resolve().is_relative_to(captures_root.resolve()):
        raise TransferError("write the export outside the captures folder")

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / DB_NAME
        live = sqlite3.connect(str(db_path))
        try:
            copy = sqlite3.connect(str(snapshot))
            try:
                live.backup(copy)
            finally:
                copy.close()
        finally:
            live.close()
        conn = sqlite3.connect(str(snapshot))
        try:
            sources = {r[0] for r in conn.execute("SELECT id FROM source")}
            # A transcription writes its folder BEFORE committing its rows, so one that
            # finishes during an export would ship files the snapshot has no rows for. Take
            # only the runs the snapshot holds; the next export brings the rest (Codex review).
            runs = {r[0] for r in conn.execute("SELECT id FROM transcription")}
        finally:
            conn.close()

        folders, skipped = [], []
        for folder in capture_folders(captures_root):
            if not folder.path.is_dir():
                raise TransferError(f"{folder.relative} moved while exporting; run the export again")
            sid = _source_id(folder.path)
            if sid in sources:
                folders.append(folder)
            else:
                # A capture still being written, or a folder with no row: the next export
                # takes it once its row exists.
                skipped.append(folder.relative)

        meta = {
            "format": FORMAT,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "capsule_version": product_version(),
            "sources": len(sources),
            "folders": len(folders),
        }
        # A file of our own (created exclusively), published without replacing anything.
        fd, partial_name = tempfile.mkstemp(
            prefix=".capsule-export-", suffix=".partial", dir=out_path.parent
        )
        os.close(fd)
        partial = Path(partial_name)
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.writestr(META_NAME, json.dumps(meta, indent=2))
                zf.write(snapshot, DB_NAME)
                for folder in folders:
                    # A folder moved (say, to the trash) while it is being read would export
                    # as rows with some or none of its files. Every file its manifest lists
                    # must make it into the package, or the export fails (Codex review).
                    sealed = _sealed_files(folder.path)
                    # A listed file already missing is the capture's own state (it fails
                    # verify where it is too), not a move: only what is there now must ship.
                    required = (
                        None if sealed is None
                        else {r for r in sealed if (folder.path / r).is_file()}
                    )
                    written: set[str] = set()
                    for file in sorted(folder.path.rglob("*")):
                        if not file.is_file() or file.is_symlink():
                            continue
                        parts = file.relative_to(folder.path).parts
                        if parts[0] == "transcript" and len(parts) > 2 and parts[1] not in runs:
                            continue
                        rel = "/".join(parts)
                        try:
                            zf.write(file, f"{CAPTURES}/{folder.relative}/{rel}")
                        except FileNotFoundError:
                            continue
                        written.add(rel)
                    gone = not (folder.path / "manifest.json").is_file()
                    if gone or required is None or not required <= written:
                        raise TransferError(
                            f"{folder.relative} moved while exporting; run the export again"
                        )
            # The last word on completeness, whatever happened during the listing: every
            # capture of the snapshot that is on disk now must be in the package. A trash or
            # restore during the export fails it, and the user runs it again (Codex review).
            packaged = {_source_id(f.path) for f in folders}
            on_disk = {_source_id(f.path) for f in capture_folders(captures_root)}
            if (on_disk & sources) - packaged:
                raise TransferError("the archive changed while exporting; run the export again")
            _publish(partial, out_path)
        finally:
            partial.unlink(missing_ok=True)
    return ExportResult(out_path, len(sources), len(folders), skipped)


def _publish(partial: Path, out_path: Path) -> None:
    """Give the finished file its name, failing rather than replacing a file made meanwhile."""
    try:
        os.link(partial, out_path)  # atomic, and refuses an existing name
    except FileExistsError:
        raise TransferError(f"{out_path} already exists; choose a new file name") from None
    except OSError:
        # A disk without hard links (FAT, a USB stick, some network shares): claim the name
        # exclusively, then fill it, so a file made meanwhile is never replaced (Codex review).
        try:
            fd = os.open(out_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
        except FileExistsError:
            raise TransferError(f"{out_path} already exists; choose a new file name") from None
        try:
            with os.fdopen(fd, "wb") as out, open(partial, "rb") as src:
                shutil.copyfileobj(src, out, 1024 * 1024)
        except BaseException:
            out_path.unlink(missing_ok=True)  # ours: created exclusively above
            raise


# -- import ----------------------------------------------------------------------


def _safe_member(name: str) -> bool:
    """A zip entry may only land inside the extraction folder (no zip-slip)."""
    if not name or "\\" in name or name.startswith("/") or ":" in name:
        return False
    parts = PurePosixPath(name).parts
    if any(p in ("", ".", "..") for p in parts):
        return False
    return name in (META_NAME, DB_NAME) or (parts[0] == CAPTURES and len(parts) >= 3)


def _extract(package: Path, into: Path) -> None:
    with zipfile.ZipFile(package) as zf:
        members = zf.infolist()
        bad = [m.filename for m in members if not m.is_dir() and not _safe_member(m.filename)]
        links = [m.filename for m in members if (m.external_attr >> 16) & 0o170000 == 0o120000]
        if bad or links:
            raise TransferError(f"not a Capsule archive, or unsafe entries: {(bad + links)[:5]}")
        # Windows would silently rename some names on extraction, and a case-insensitive
        # disk would merge names differing only in case: either would let one file overwrite
        # another. Refuse such a package before writing anything (Codex review).
        names = [m.filename for m in members if not m.is_dir()]
        folded: dict[str, str] = {}
        clashes = []
        for name in names:
            parts = name.split("/")
            for i in range(1, len(parts) + 1):  # every folder on the way, and the file
                prefix = "/".join(parts[:i])
                if folded.setdefault(prefix.casefold(), prefix) != prefix:
                    clashes.append(name)
                    break
        if clashes:
            raise TransferError(f"names that differ only in case: {clashes[:5]}")
        if _ON_WINDOWS:
            refused = [n for n in names if any(_refused_by_windows(p) for p in n.split("/"))]
            if refused:
                raise TransferError(f"names Windows cannot store: {refused[:5]}")
        meta = json.loads(zf.read(META_NAME)) if META_NAME in zf.namelist() else {}
        if meta.get("format", FORMAT) != FORMAT:
            raise TransferError(f"unsupported archive format {meta.get('format')!r}")
        for member in members:
            if not member.is_dir():
                zf.extract(member, into)


def _refused_by_windows(part: str) -> bool:
    return bool(
        _WINDOWS_FORBIDDEN.search(part) or part.endswith((".", " ")) or _WINDOWS_RESERVED.match(part)
    )


def _windows_problems(folder: Folder, target_root: Path) -> list[str]:
    problems = []
    for file in folder.path.rglob("*"):
        rel = file.relative_to(folder.path)
        for part in rel.parts:
            if _refused_by_windows(part):
                problems.append(f"{folder.relative}/{rel.as_posix()}: a name Windows refuses")
        final = target_root / folder.relative / rel
        # The staging name (".importing-xxxxxxxx-a-") is the longer of its two paths.
        if len(str(final)) + 22 > _MAX_PATH:
            problems.append(f"{folder.relative}/{rel.as_posix()}: path too long for Windows")
    return problems


def _plan(src_db: Path, src_captures: Path, db_path: Path, captures_root: Path) -> ImportPlan:
    """What merging `src_db` (a SNAPSHOT, never a live database) and its folders would add.

    Only folders and transcription runs the snapshot has rows for are taken, so a capture
    finishing on the other side while this runs can never arrive as rows without files, or
    files without rows (Codex review); the next import brings it."""
    plan = ImportPlan()
    src = _connect_read_only(src_db)
    dst = sqlite3.connect(str(db_path))
    try:
        sources = {r[0] for r in src.execute("SELECT id FROM source")}
        runs = {r[0] for r in src.execute("SELECT id FROM transcription")} if "transcription" in _tables(src) else set()
        dst_tables = set(_tables(dst))
        for table in _tables(src):
            if table not in dst_tables:
                raise TransferError(
                    f"the archive has a table this Capsule does not ({table}): update Capsule first"
                )
            src_cols, keys = _columns(src, table)
            dst_cols, _ = _columns(dst, table)
            missing = [c for c in src_cols if c not in dst_cols]
            if missing:
                raise TransferError(
                    f"the archive's {table} table has columns this Capsule does not "
                    f"({', '.join(missing)}): update Capsule first"
                )
            if not keys:
                raise TransferError(f"table {table} has no primary key; cannot merge it safely")
            have = set(dst.execute(f'SELECT {", ".join(keys)} FROM "{table}"'))
            new = sum(1 for k in src.execute(f'SELECT {", ".join(keys)} FROM "{table}"') if k not in have)
            if new:
                plan.new_rows[table] = new
        # One assertion may be superseded only once (`ux_assertion_supersedes_id`). If both
        # computers corrected the same fact differently, the merge cannot hold both: say so
        # before writing (Codex review). Resolving rival corrections belongs to the
        # correction loop, which does not exist yet; today no archive has corrections.
        if "assertion" in plan.new_rows:
            held = dict(dst.execute(
                "SELECT supersedes_id, id FROM assertion WHERE supersedes_id IS NOT NULL"
            ))
            rival = [
                sid for aid, sid in src.execute(
                    "SELECT id, supersedes_id FROM assertion WHERE supersedes_id IS NOT NULL"
                )
                if sid in held and held[sid] != aid
            ]
            if rival:
                raise TransferError(
                    "the same fact was corrected differently on each computer; merging rival "
                    f"corrections is not supported yet ({len(rival)} assertion(s), e.g. {rival[0]})"
                )
        if "capture_lifecycle_event" in plan.new_rows:
            split = _split_histories(src, dst)
            if split:
                plan.lifecycle_kept_here = sorted(split)
                ours_ids = {r[0] for r in dst.execute("SELECT id FROM capture_lifecycle_event")}
                skipped = sum(
                    1
                    for eid, sid in src.execute("SELECT id, source_id FROM capture_lifecycle_event")
                    if sid in split and eid not in ours_ids
                )
                remaining = plan.new_rows["capture_lifecycle_event"] - skipped
                if remaining:
                    plan.new_rows["capture_lifecycle_event"] = remaining
                else:
                    del plan.new_rows["capture_lifecycle_event"]
    finally:
        src.close()
        dst.close()

    plan.runs = runs
    held = {f.name: f for f in capture_folders(captures_root)}
    # Records with no folder on either side are carried as they are (an archive can hold
    # some, from captures that failed part-way), but never silently (Codex review).
    dst = sqlite3.connect(str(db_path))
    try:
        held_sources = {r[0] for r in dst.execute("SELECT id FROM source")}
    finally:
        dst.close()
    with_folder = {_source_id(f.path) for f in capture_folders(src_captures)}
    with_folder |= {_source_id(f.path) for f in held.values()}
    plan.records_without_folder = len(sources - held_sources - with_folder)
    for folder in capture_folders(src_captures):
        if _source_id(folder.path) not in sources:
            plan.not_in_database.append(folder.relative)
            continue
        plan.source_folders += 1
        mine = held.get(folder.name)
        if mine is None:
            plan.new_folders.append(folder)
            # A link could pull files from outside the archive into it (and into the next
            # export); a zip may not carry one, and neither may an archive folder.
            if _has_link(folder.path):
                plan.problems.append(f"{folder.relative}: holds a link to somewhere else")
            if _ON_WINDOWS:
                plan.problems += _windows_problems(folder, captures_root)
            continue
        plan.present += 1
        same = _read(folder.path / "manifest.json") == _read(mine.path / "manifest.json")
        if not same:
            plan.conflicts.append(folder.relative)
            continue
        if folder.trashed and not mine.trashed:
            plan.trashed_elsewhere.append(folder.name)
        for sub in _GROWING_DIRS:
            if _has_link(folder.path / sub) or _has_link(mine.path / sub):
                plan.problems.append(f"{folder.relative}/{sub}: holds a link to somewhere else")
                continue
            for file in sorted((folder.path / sub).rglob("*")):
                if not file.is_file() or file.is_symlink() or file.name.endswith(_PART_SUFFIX):
                    continue
                rel = file.relative_to(folder.path)
                if rel.parts[0] == "transcript" and len(rel.parts) > 2 and rel.parts[1] not in runs:
                    continue
                dest = mine.path / rel
                if not dest.exists():
                    plan.new_files.append((file, dest, mine.path))
                    if _ON_WINDOWS and len(str(dest)) > _MAX_PATH:
                        plan.problems.append(f"{dest}: path too long for Windows")
    return plan


def _has_link(path: Path) -> bool:
    """Is `path`, or anything under it, a link? (rglob does not follow linked folders.)"""
    if path.is_symlink():
        return True
    return path.is_dir() and any(p.is_symlink() for p in path.rglob("*"))


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _insert_rows(src_db: Path, db_path: Path) -> set[str]:
    """Add the rows the target lacks, in one transaction; return the source ids that gained
    a note (their `notes.json` is a projection of those rows and must be rewritten)."""
    noted: set[str] = set()
    src = _connect_read_only(src_db)
    dst = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    try:
        dst.execute("PRAGMA foreign_keys = ON")
        dst.execute("BEGIN IMMEDIATE")
        try:
            # Reset by every COMMIT, so set inside the transaction: rows may reference
            # rows of a table copied later (or of the same table).
            dst.execute("PRAGMA defer_foreign_keys = ON")
            # Decided here, inside the write transaction, so a decision made on this computer
            # since the plan is seen too (Codex review).
            keep_here = _split_histories(src, dst) if "capture_lifecycle_event" in _tables(src) else set()
            for table in _tables(src):
                cols, keys = _columns(src, table)
                have = set(dst.execute(f'SELECT {", ".join(keys)} FROM "{table}"'))
                key_at = [cols.index(k) for k in keys]
                col_list = ", ".join(f'"{c}"' for c in cols)
                marks = ", ".join("?" for _ in cols)
                for row in src.execute(f'SELECT {col_list} FROM "{table}"'):
                    if table == "capture_lifecycle_event" and row[cols.index("source_id")] in keep_here:
                        continue
                    if tuple(row[i] for i in key_at) not in have:
                        dst.execute(f'INSERT INTO "{table}" ({col_list}) VALUES ({marks})', row)
                        if table == "capture_note":
                            noted.add(row[cols.index("source_id")])
            _refuse_supersession_loops(dst)
            broken = dst.execute("PRAGMA foreign_key_check").fetchmany(5)
            if broken:
                raise TransferError(f"the merged archive would have broken links: {broken}")
            dst.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            dst.execute("ROLLBACK")
            raise TransferError(f"the two archives disagree in a way a merge cannot hold: {exc}") from exc
        except BaseException:
            dst.execute("ROLLBACK")
            raise
    finally:
        src.close()
        dst.close()
    return noted


def _split_histories(src: sqlite3.Connection, dst: sqlite3.Connection) -> set[str]:
    """Captures whose replaced/reinstated history each side added to without the other.

    The history is read latest-event-wins in append order, so merging two such histories
    would let the merge's order pick which computer's decision stands, and the two computers
    would end up disagreeing. Events are append-only, so the split can never be undone
    either; refusing would block every later import. So THIS computer's decision is kept:
    the other side's events for these captures are not imported, and the import says so
    (Codex review)."""

    def histories(conn: sqlite3.Connection) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for eid, sid in conn.execute("SELECT id, source_id FROM capture_lifecycle_event"):
            out.setdefault(sid, set()).add(eid)
        return out

    theirs, ours = histories(src), histories(dst)
    return {
        sid for sid in theirs.keys() & ours.keys() if theirs[sid] - ours[sid] and ours[sid] - theirs[sid]
    }


def _refuse_supersession_loops(dst: sqlite3.Connection) -> None:
    """Each computer checks for loops when a capture is marked superseded, but a merge can
    join "A replaced by B" from one with "B replaced by A" from the other, leaving no current
    capture. Check the merged state, latest event per capture in append order as the store
    reads it, before committing (Codex review)."""
    current: dict[str, Optional[str]] = {}
    for sid, event, by in dst.execute(
        "SELECT source_id, event, superseded_by FROM capture_lifecycle_event ORDER BY rowid"
    ):
        current[sid] = by if event == "superseded" else None
    for start in current:
        seen: set[str] = set()
        cursor: Optional[str] = start
        while cursor is not None:
            if cursor in seen:
                raise TransferError(
                    "the two computers marked captures as replacing each other, which would "
                    f"leave none current (around {start}); reinstate one first"
                )
            seen.add(cursor)
            cursor = current.get(cursor)


def refresh_notes(db_path: Path, captures_root: Path, source_ids: Optional[set[str]] = None) -> int:
    """Rewrite `notes.json` from the store for the captures in `source_ids` (every capture
    with a note when None), so a note merged from another computer shows up (Codex review).
    `notes.json` is a projection of the store, so rewriting it is always safe, and running
    it again repairs an import that died between its commit and this step."""
    from capsule.capture.manifest import write_notes_file
    from capsule.store.repository import Repository

    repository = Repository(db_path)
    refreshed = 0
    try:
        if source_ids is None:
            source_ids = {
                r[0] for r in repository.connection.execute("SELECT DISTINCT source_id FROM capture_note")
            }
        if not source_ids:
            return 0
        for folder in capture_folders(captures_root):
            sid = _source_id(folder.path)
            if sid in source_ids:
                write_notes_file(folder.path, repository.notes_for_source(sid))
                refreshed += 1
    finally:
        repository.close()
    return refreshed


@contextmanager
def import_lock(captures_root: Path) -> Iterator[None]:
    """One import at a time per archive, held from staging to the last rename. The operating
    system releases it if the process dies, so an interrupted import never wedges the next."""
    path = captures_root / ".capsule-import.lock"
    handle = open(path, "a+b")
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise TransferError("another import into this archive is running") from exc
        yield
    finally:
        # Unlock explicitly: Windows releases a lock on close only "after a delay", which
        # would make the next import in quick succession think one is still running.
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


_STAGE_RE = re.compile(r"^\.importing-[0-9a-f]{8}-([at])-(.+)$")
_DONE_SUFFIX = ".done"


def _done_marker(stage: Path) -> Path:
    """Beside a staged folder (never inside it, which would break its seal): the copy finished."""
    return stage.with_name(stage.name + _DONE_SUFFIX)


def _stage_path(captures_root: Path, folder: Folder) -> Path:
    """A staging name that records where the folder belongs (a = active, t = trash)."""
    return captures_root / f"{STAGING_PREFIX}{uuid.uuid4().hex[:8]}-{'t' if folder.trashed else 'a'}-{folder.name}"


def recover_staging(captures_root: Path, db_path: Path) -> int:
    """Finish what an interrupted import left behind; return how many folders it published.

    A staged folder whose record is committed holds the only copy of that evidence: it is
    put where its name says, never deleted. One whose record never committed is debris from
    a rolled-back import and is removed (Codex review). Call under the import lock.
    """
    if not captures_root.is_dir():
        return 0
    committed: set[str] = set()
    committed_runs: set[str] = set()
    if db_path.is_file():
        conn = sqlite3.connect(str(db_path))
        try:
            committed = {r[0] for r in conn.execute("SELECT id FROM source")}
            committed_runs = {r[0] for r in conn.execute("SELECT id FROM transcription")}
        finally:
            conn.close()
    published = 0
    for child in sorted(captures_root.iterdir()):
        if not child.name.startswith(STAGING_PREFIX):
            continue
        if not child.is_dir():
            if child.name.endswith(_DONE_SUFFIX) and not child.with_name(
                child.name[: -len(_DONE_SUFFIX)]
            ).exists():
                child.unlink(missing_ok=True)  # a marker whose folder was published
            continue
        match = _STAGE_RE.match(child.name)
        final = None
        if match:
            where, name = match.groups()
            final = captures_root / TRASH_DIRNAME / name if where == "t" else captures_root / name
        complete = _done_marker(child).exists()
        committed_here = _source_id(child) in committed
        if final is not None and complete and committed_here and not final.exists():
            # The source row may predate this import (a folder being restored), so it does
            # not prove the import's own rows committed: keep only transcription runs the
            # database holds now, so a rolled-back run never appears (Codex review).
            _prune_runs(child, committed_runs)
            final.parent.mkdir(exist_ok=True)
            os.replace(child, final)
            _done_marker(child).unlink(missing_ok=True)
            published += 1
        elif final is None or not complete or not committed_here:
            # Never committed, or a copy that never finished: the package still holds it,
            # and the next import of that package copies it again.
            shutil.rmtree(child)
            _done_marker(child).unlink(missing_ok=True)
        # else: committed and complete, but its place is taken; left for a person to look at.
    return published


def _migrate_target(db_path: Path) -> None:
    """Bring the target database to this build's schema before comparing it."""
    from capsule.store.ledger import AncestryLedger
    from capsule.store.repository import Repository

    Repository(db_path).close()
    AncestryLedger(db_path).close()


def _backup(source: Path, dest: Path, *, read_only: bool = False) -> None:
    """A transactionally consistent copy of a SQLite database, live or not."""
    live = _connect_read_only(source) if read_only else sqlite3.connect(str(source))
    try:
        copy = sqlite3.connect(str(dest))
        try:
            live.backup(copy)
        finally:
            copy.close()
    finally:
        live.close()


def _refuse_unsafe(plan: ImportPlan) -> None:
    if plan.problems:
        raise TransferError("files that cannot be imported: " + "; ".join(plan.problems[:5]))
    if plan.conflicts:
        # Merging rows while skipping the folder would leave evidence with no files
        # (Codex review). Rare: a folder name carries 8 characters of a 32-character id.
        raise TransferError(
            "a capture here and one in the import share a folder name but are not the same "
            "capture; move one of them to the trash first: " + ", ".join(plan.conflicts[:5])
        )


def _prune_runs(folder: Path, runs: set[str]) -> None:
    """Drop transcription runs the source snapshot has no rows for (still being written)."""
    transcripts = folder / "transcript"
    if transcripts.is_dir():
        for run in transcripts.iterdir():
            if run.is_dir() and run.name not in runs:
                shutil.rmtree(run)


def import_archive(
    package: Path,
    *,
    db_path: Path,
    captures_root: Path,
    confirm: Callable[[ImportPlan], bool],
    work_dir: Optional[Path] = None,
) -> ImportResult:
    """Merge the archive in `package` (a zip from `capsule export`, or an archive folder
    holding `capsule.db` and `captures/`) into this one.

    `confirm` sees the plan and decides: False stops there (a dry run) with nothing written.
    """
    package, db_path, captures_root = Path(package), Path(db_path), Path(captures_root)
    # Nothing in the target is created or migrated until `confirm` says yes (Codex review):
    # the plan is made against a migrated COPY of the target database, and a zip is unpacked
    # beside the archive only when that folder already exists (same disk, so the folders
    # move into place by rename), else in the system's temporary folder.
    if work_dir:
        work = Path(work_dir)
    elif captures_root.parent.is_dir():
        work = captures_root.parent
    else:
        work = Path(tempfile.gettempdir())
    tmp: Optional[Path] = None
    # Antivirus can hold a file for a moment on Windows; a cleanup hiccup after a finished
    # import must not turn it into a reported failure.
    planning = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        if package.is_file():
            tmp = work / f".capsule-import-{uuid.uuid4().hex[:12]}"
            tmp.mkdir()
            _extract(package, tmp)
            root = tmp
        elif package.is_dir():
            root = package
        else:
            raise TransferError(f"{package} is neither an export file nor an archive folder")
        live_src, src_captures = root / DB_NAME, root / CAPTURES
        if not live_src.is_file():
            raise TransferError(f"{package} holds no {DB_NAME}")
        if live_src.resolve() == db_path.resolve() or (
            src_captures.exists() and src_captures.resolve() == captures_root.resolve()
        ):
            raise TransferError("that is this archive; nothing to import")

        # ONE snapshot of the source, used for every read below: an archive folder may be
        # live, and re-reading it after confirmation could merge rows planned without
        # their folders (Codex review).
        src_db = Path(planning.name) / "source.db"
        _backup(live_src, src_db, read_only=True)
        plan_db = Path(planning.name) / DB_NAME
        if db_path.is_file():
            _backup(db_path, plan_db)
        _migrate_target(plan_db)
        plan = _plan(src_db, src_captures, plan_db, captures_root)
        result = ImportResult(plan)
        _refuse_unsafe(plan)
        if plan.nothing_new or not confirm(plan):
            return result

        captures_root.mkdir(parents=True, exist_ok=True)
        _migrate_target(db_path)
        with import_lock(captures_root):
            result.recovered = recover_staging(captures_root, db_path)
            # Plan again, under the lock, against the real archive: another import may have
            # finished since the plan the user confirmed (Codex review). What is left to add
            # can only have shrunk, so this never adds anything unconfirmed.
            plan = _plan(src_db, src_captures, db_path, captures_root)
            result.plan = plan
            _refuse_unsafe(plan)
            if plan.nothing_new:
                return result
            staged: list[tuple[Path, Path]] = []
            try:
                for folder in plan.new_folders:
                    stage = _stage_path(captures_root, folder)
                    staged.append((stage, captures_root / folder.relative))  # before: cleanup
                    if tmp is not None:
                        shutil.move(folder.path, stage)  # a rename on the same disk
                    else:
                        shutil.copytree(folder.path, stage, symlinks=False)
                    _prune_runs(stage, plan.runs)
                    _done_marker(stage).touch()  # only a complete copy is ever published
                noted = _insert_rows(src_db, db_path)
            except BaseException:
                # Did the rows commit? An interruption can land just after COMMIT, so ask the
                # database instead of assuming a rollback: recovery publishes what committed
                # and was copied whole, and removes the rest (Codex review).
                try:
                    recover_staging(captures_root, db_path)
                except Exception:  # noqa: BLE001 — never hide the original failure
                    pass
                raise
            result.applied = True

            (captures_root / TRASH_DIRNAME).mkdir(exist_ok=True)
            for stage, final in staged:
                os.replace(stage, final)
                _done_marker(stage).unlink(missing_ok=True)
                result.added_folders += 1
                try:
                    if not verify_capture(final).ok:
                        result.not_verified.append(final.relative_to(captures_root).as_posix())
                except (OSError, ValueError):
                    result.not_verified.append(final.relative_to(captures_root).as_posix())
            # After the commit: a run that stops part-way finds the rest still missing next
            # time. Each file lands whole (written aside, then renamed), never half.
            for source_file, dest, capture in plan.new_files:
                # Only into the capture where it was planned: one trashed meanwhile has moved,
                # and writing here would recreate a manifest-less folder in its old place.
                # The next import adds the file where the capture now is (Codex review).
                if dest.exists() or not (capture / "manifest.json").is_file():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(dest.name + _PART_SUFFIX)
                shutil.copy2(source_file, part)
                os.replace(part, dest)
                result.added_files += 1
            result.notes_refreshed = refresh_notes(db_path, captures_root, noted)
        return result
    finally:
        planning.cleanup()
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
