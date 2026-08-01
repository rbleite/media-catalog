#!/usr/bin/env python3
"""Explain where this machine keeps the catalogue and its covers, and why.

Run it on BOTH machines and compare. Covers missing on one of them is almost
always a difference printed here — a data dir resolved from a different source,
or cover files that live beside the catalogue on one machine only.

    python3 scripts/diagnose_sync.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_catalog import config   # noqa: E402


def main() -> None:
    print("=== where the data dir comes from ===")
    env = os.environ.get("MEDIACAT_DATA_DIR")
    cfg = config.read_app_config().get("data_dir")
    dxdb = config._dx_db_dir()
    print(f"  1. $MEDIACAT_DATA_DIR      : {env or '(unset)'}")
    print(f"  2. config.json data_dir    : {cfg or '(unset)'}")
    print(f"  3. drive-xray db folder    : {dxdb or '(drive-xray not configured)'}")
    src = ("$MEDIACAT_DATA_DIR" if env else
           "config.json" if cfg else
           "inherited from drive-xray" if dxdb and dxdb.is_dir() else
           "NONE -> legacy in-repo layout (never synced!)")
    print(f"  -> in use                  : {src}")
    print(f"  -> resolved data dir       : {config._DATA_DIR or '(none)'}")

    print("\n=== catalogue ===")
    db = config.CATALOG_DB
    print(f"  catalog.db   : {db}")
    print(f"  exists       : {db.is_file()}"
          f"{f'  ({db.stat().st_size:,} bytes)' if db.is_file() else ''}")

    print("\n=== covers ===")
    cov = config.COVERS_DIR
    print(f"  covers dir   : {cov}")
    if cov.is_dir():
        files = [f for f in cov.iterdir() if f.is_file()]
        empty = [f for f in files if f.stat().st_size == 0]
        print(f"  files        : {len(files)}")
        if empty:
            # a 0-byte file is the classic cloud placeholder: OneDrive/iCloud
            # list it, but the bytes are still online
            print(f"  !! {len(empty)} are 0 bytes — cloud placeholders, not yet"
                  f" downloaded. Mark the folder 'Always keep on this device'.")
    else:
        print("  files        : (folder does not exist)")

    if db.is_file():
        print("\n=== what the catalogue expects ===")
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT cover_path FROM works"
                " WHERE cover_path IS NOT NULL AND cover_path<>''").fetchall()
        except sqlite3.Error as e:
            print(f"  (cannot read works: {e})")
            rows = []
        conn.close()
        found = sum(1 for (p,) in rows if config.resolve_cover(p))
        print(f"  works with a cover recorded : {len(rows)}")
        print(f"  of those, resolvable here   : {found}")
        if rows and found < len(rows):
            print(f"  -> {len(rows) - found} covers are recorded but their file"
                  f" is not in this machine's covers dir.")
            for (p,) in rows[:3]:
                if not config.resolve_cover(p):
                    print(f"     missing: {p}")

    print("\n=== secrets ===")
    for p in config._SECRETS_PATHS:
        print(f"  {p}  ->  {'present' if p.is_file() else 'absent'}")


if __name__ == "__main__":
    main()
