"""Long writes run against a local copy; the synced folder sees one finished file.

`config.data_dir()` defaults to `<drive-xray db_dir>/media-catalog` — the same
OneDrive/Drive/Dropbox folder that already syncs the drive indexes. That is
deliberate and it is what makes the catalogue follow you between machines. It
is also the worst place to run SQLite: the sync client re-uploads the changing
`catalog.db` and its `-wal` continuously *while* a scan or an import writes
them, competing for disk and network with the operation itself, and a database
captured mid-write is what conflict copies are made of.

A `mediacat scan` over eight drives, or an `import-bundle`, writes for minutes.
Doing that in place means minutes of churn and a real chance of the sync client
publishing a half-written file to the other machine.

This is deliberately a copy of drive-xray's mechanism rather than an import of
it. media-catalog treats drive-xray as an optional dependency — `_dx_module()`
returns None and everything still works — so borrowing this would make a
hard requirement out of something that is currently a nicety.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import time
from pathlib import Path

# Matched case-insensitively against each directory name, with startswith so
# "OneDrive - Some Company" matches too.
CLOUD_DIR_PREFIXES = (
    "cloudstorage",        # ~/Library/CloudStorage/* (macOS Monterey+)
    "mobile documents",    # ~/Library/Mobile Documents (iCloud Drive)
    "icloud drive",
    "onedrive",
    "google drive",
    "googledrive",
    "dropbox",
    "pclouddrive",
    "mega",
    "creative cloud files",
    "sync.com",
    "box sync",
)

# NOT /tmp. macOS purges it after three days, and an interrupted scan has to
# be able to resume from what it already wrote — a staging dir that evaporates
# turns resumption into starting over, silently.
STAGING_DIR = Path.home() / ".media-catalog-staging"


def is_cloud_dir(name: str) -> bool:
    n = name.strip().lower()
    return any(n.startswith(p) for p in CLOUD_DIR_PREFIXES)


def in_cloud_dir(p: Path) -> bool:
    try:
        return any(is_cloud_dir(part) for part in Path(p).resolve().parts)
    except OSError:
        return False


def _checkpoint(db: Path) -> None:
    """Fold -wal into the .db so a plain copy or move carries every commit."""
    try:
        conn = sqlite3.connect(db, isolation_level=None)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass


def _drop_sidecars(db: Path) -> None:
    for ext in ("-wal", "-shm", "-journal"):
        Path(str(db) + ext).unlink(missing_ok=True)


def stage_for_write(db: Path) -> tuple[Path, bool]:
    """Return (path to write to, staged?).

    Only kicks in inside a cloud-synced folder. MEDIACAT_NO_STAGING=1 disables
    it. A staged copy left by an interrupted run is reused rather than
    overwritten, so the work already done is not thrown away.
    """
    db = Path(db)
    if os.environ.get("MEDIACAT_NO_STAGING", "").lower() in {"1", "true", "yes"}:
        return db, False
    if not in_cloud_dir(db):
        return db, False
    try:
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        staged = STAGING_DIR / db.name
        if staged.exists() and (not db.exists()
                                or staged.stat().st_mtime >= db.stat().st_mtime):
            pass                       # leftover from an interrupted run
        elif db.exists():
            _checkpoint(db)
            _drop_sidecars(staged)
            shutil.copy2(db, staged)
        else:
            staged.unlink(missing_ok=True)
            _drop_sidecars(staged)
    except OSError:
        return db, False               # staging unavailable — write in place
    return staged, True


def finalize(staged: Path, db: Path) -> str:
    """Move the finished copy back — one upload instead of thousands.

    Retried: sync clients briefly lock files while scanning them, and losing
    the move would strand the only up-to-date catalogue in the staging dir.
    """
    _checkpoint(staged)
    _drop_sidecars(staged)
    last = ""
    for attempt in range(5):
        try:
            _drop_sidecars(db)
            db.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(db))
            return f"moved to {db}"
        except OSError as e:
            last = str(e)
            time.sleep(2 * (attempt + 1))
    return f"could not move back ({last}) — catalogue kept at {staged}"


@contextlib.contextmanager
def staged_catalog(db: Path):
    """Yield the path a write should target, then publish it.

    No try/finally, on purpose. If the operation raises, the staged copy stays
    where it is and the synced catalogue is left untouched: the next run
    resumes from it, and a half-written database never reaches the folder the
    other machine reads. A `finally` here would publish precisely the broken
    state this exists to avoid.
    """
    target, staged = stage_for_write(Path(db))
    yield target
    if staged:
        finalize(target, Path(db))
