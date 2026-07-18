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
        C.set_scan_time(conn, label)
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


def cmd_nfo(args) -> None:
    from media_catalog import config
    from media_catalog.enrich import nfo
    if not config.has_tmdb():
        sys.exit("nfo matching needs a TMDB api key (see README)")
    roots = D.drive_roots()
    if not roots:
        sys.exit("no drive-xray drives mounted right now — plug one in and retry")
    print("mounted drives: " + ", ".join(roots), file=sys.stderr)
    conn = C.open_catalog(Path(args.catalog))
    res = nfo.enrich_nfo(conn, roots, config.get("tmdb_api_key"), force=args.force)
    print(f"\nNFO: {res['matched']} movies matched via IMDb · {res['no_id']} "
          f".nfo without id · {res['skipped_offline']} offline / {res['total']} total",
          file=sys.stderr)


def cmd_export_patch(args) -> None:
    conn = C.open_catalog(Path(args.catalog))
    data = C.export_overrides(conn)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"exported {data['count']} overrides -> {args.out}", file=sys.stderr)


def cmd_import_patch(args) -> None:
    conn = C.open_catalog(Path(args.catalog))
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    res = C.import_overrides(conn, data)
    print(f"applied {res['applied']} / {res['total']} overrides"
          f" ({res['missing']} not in catalog)", file=sys.stderr)


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

    pn = sub.add_parser("nfo", help="match movies via IMDb id in .nfo sidecars")
    pn.add_argument("--force", action="store_true",
                    help="also re-match movies already corrected by hand (manual=1)")

    pe = sub.add_parser("export-patch", help="save manual overrides to a JSON patch")
    pe.add_argument("out", nargs="?", default="overrides.json",
                    help="output file (default: overrides.json)")

    pm = sub.add_parser("import-patch", help="re-apply a JSON patch of overrides")
    pm.add_argument("file", help="patch file from export-patch")

    args = p.parse_args()
    # first run with a shared/synced data dir: migrate legacy catalog+covers
    from media_catalog import config as _config
    msg = _config.ensure_data_dir()
    if msg:
        print(f"  {msg}", file=sys.stderr)
    {"scan": cmd_scan, "summary": cmd_summary, "id3": cmd_id3, "nfo": cmd_nfo,
     "export-patch": cmd_export_patch, "import-patch": cmd_import_patch}[args.cmd](args)


if __name__ == "__main__":
    main()
