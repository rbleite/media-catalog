#!/usr/bin/env python3
"""mediacat — CLI for the media catalog.

    python mediacat.py scan            # scan every drive-xray-indexed drive
    python mediacat.py scan 8Tb.db …   # scan specific drive-xray db files
    python mediacat.py summary         # show catalog counts

Reads drive-xray's central registry to find indexed drives (no re-scan of the
filesystem — the catalog is derived from the existing index).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_catalog import catalog as C
from media_catalog import discover as D

DX_REGISTRY = Path.home() / ".config" / "drive-xray" / "registry.json"


def _registry_drives() -> list[tuple[Path, str]]:
    if not DX_REGISTRY.exists():
        return []
    data = json.loads(DX_REGISTRY.read_text(encoding="utf-8"))
    out = []
    for key, meta in data.get("drives", {}).items():
        db = Path(meta.get("db", key))
        if db.exists():
            out.append((db, meta.get("label", db.stem)))
    return out


def _db_label(db: Path) -> str:
    import sqlite3
    try:
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT label FROM drive LIMIT 1").fetchone()
        conn.close()
        return row[0] if row and row[0] else db.stem
    except Exception:
        return db.stem


def cmd_scan(args) -> None:
    if args.dbs:
        drives = [(Path(p), _db_label(Path(p))) for p in args.dbs]
    else:
        drives = _registry_drives()
    if not drives:
        sys.exit("no drives to scan (pass db files, or index some in drive-xray)")

    conn = C.open_catalog(Path(args.catalog))
    total = 0
    for db, label in drives:
        n = 0
        for work in D.scan_index(db, label):
            C.upsert_work(conn, work)
            n += 1
        conn.commit()
        print(f"  {label:<16} {n:>6} works  ({db.name})", file=sys.stderr)
        total += n

    print(f"\ncatalogued {total} works into {args.catalog}", file=sys.stderr)
    _print_summary(conn)


def cmd_summary(args) -> None:
    conn = C.open_catalog(Path(args.catalog))
    _print_summary(conn)


def cmd_id3(args) -> None:
    from media_catalog.enrich import id3
    roots = D.drive_roots()
    if not roots:
        sys.exit("no drive-xray drives mounted right now — plug one in and retry")
    print("mounted drives: " + ", ".join(f"{k} ({v})" for k, v in roots.items()),
          file=sys.stderr)
    conn = C.open_catalog(Path(args.catalog))
    res = id3.enrich_id3(conn, roots, force=args.force)
    print(f"\nID3: {res['updated']} albums updated · {res['covers']} embedded "
          f"covers · {res['skipped_offline']} offline / {res['total']} total",
          file=sys.stderr)


def _print_summary(conn) -> None:
    by_type = C.counts_by_type(conn)
    print("\n  by type:")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"    {t:<8} {n:>6}")
    plats = C.counts_by_platform(conn)
    if plats:
        print("\n  games by platform:")
        for plat, n, size in plats:
            gb = size / 1073741824 if size else 0
            print(f"    {str(plat):<10} {n:>5}   {gb:8.1f} GB")


def main() -> None:
    p = argparse.ArgumentParser(prog="mediacat")
    p.add_argument("--catalog", default=str(C.DEFAULT_CATALOG),
                   help="catalog .db path")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="scan drive-xray indexes into the catalog")
    ps.add_argument("dbs", nargs="*", help="drive-xray db files (default: all registered)")

    sub.add_parser("summary", help="show catalog counts")

    pi = sub.add_parser("id3", help="read ID3 tags off mounted MP3 drives")
    pi.add_argument("--force", action="store_true",
                    help="overwrite artist/album/year/genre even when already set")

    args = p.parse_args()
    {"scan": cmd_scan, "summary": cmd_summary, "id3": cmd_id3}[args.cmd](args)


if __name__ == "__main__":
    main()
