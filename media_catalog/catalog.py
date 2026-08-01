"""Catalog storage — a SQLite db of `works` (movie | album | game).

Deliberately decoupled from drive-xray: the media catalog is about *titles*
(enriched, browsable), while drive-xray is about *files* (sizes, dupes). This
module owns the `works` schema; `discover.py` fills it by reading drive-xray
indexes, and the enrich/* clients augment rows in place (cached).
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

from media_catalog import config as _config

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id           INTEGER PRIMARY KEY,
    type         TEXT NOT NULL,              -- movie | album | game
    title        TEXT NOT NULL,              -- best available (enriched or raw)
    title_raw    TEXT,                       -- as parsed from path / tags
    artist       TEXT,                       -- albums only (band / performer)
    year         INTEGER,
    platform     TEXT,                       -- PS3, PS4, Switch, … (games only)
    identifier   TEXT,                       -- title-id / imdb / mbid (for enrichment)
    -- location (carried from the drive-xray index) -----------------------
    rel_path     TEXT NOT NULL,
    drive_label  TEXT NOT NULL,
    size_bytes   INTEGER,
    mtime        REAL,                       -- newest file in the work (unix ts)
    has_subtitles INTEGER NOT NULL DEFAULT 0, -- movie ships a .srt/.sub sidecar
    -- enrichment ---------------------------------------------------------
    cover_path   TEXT,                       -- local cached cover image
    genre        TEXT,
    extra_json   TEXT,                       -- provider payload (json)
    enriched     INTEGER NOT NULL DEFAULT 0,
    provider     TEXT,                       -- tmdb | igdb | musicbrainz | …
    manual       INTEGER NOT NULL DEFAULT 0, -- 1 = user-corrected, don't auto-touch
    hidden       INTEGER NOT NULL DEFAULT 0, -- 1 = junk, hide from the gallery
    updated_at   TEXT NOT NULL DEFAULT '',
    UNIQUE(drive_label, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_works_type     ON works(type);
CREATE INDEX IF NOT EXISTS idx_works_platform ON works(platform);
CREATE INDEX IF NOT EXISTS idx_works_title    ON works(title);

-- Raw API responses, cached so re-scans never re-hit a provider.
CREATE TABLE IF NOT EXISTS enrich_cache (
    provider     TEXT NOT NULL,
    key          TEXT NOT NULL,
    response     TEXT,
    fetched_at   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (provider, key)
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# Central catalog location — lives in the shared/synced data dir when one is
# configured (see media_catalog.config), else the legacy in-repo path.
DEFAULT_CATALOG = _config.CATALOG_DB



def ensure_search_index(conn: sqlite3.Connection) -> bool:
    """Create/refresh a lightweight FTS5 index for title search.

    Some Python/SQLite builds may lack FTS5; in that case the app falls back to
    LIKE search. Keeping this optional makes the catalog portable.
    """
    try:
        conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(
            title, title_raw, artist, genre, platform, rel_path,
            content='works', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS works_ai AFTER INSERT ON works BEGIN
            INSERT INTO works_fts(rowid,title,title_raw,artist,genre,platform,rel_path)
            VALUES (new.id,new.title,new.title_raw,new.artist,new.genre,new.platform,new.rel_path);
        END;
        CREATE TRIGGER IF NOT EXISTS works_ad AFTER DELETE ON works BEGIN
            INSERT INTO works_fts(works_fts,rowid,title,title_raw,artist,genre,platform,rel_path)
            VALUES('delete',old.id,old.title,old.title_raw,old.artist,old.genre,old.platform,old.rel_path);
        END;
        CREATE TRIGGER IF NOT EXISTS works_au AFTER UPDATE ON works BEGIN
            INSERT INTO works_fts(works_fts,rowid,title,title_raw,artist,genre,platform,rel_path)
            VALUES('delete',old.id,old.title,old.title_raw,old.artist,old.genre,old.platform,old.rel_path);
            INSERT INTO works_fts(rowid,title,title_raw,artist,genre,platform,rel_path)
            VALUES (new.id,new.title,new.title_raw,new.artist,new.genre,new.platform,new.rel_path);
        END;
        """)
        # External-content FTS tables can report rows from the content table
        # even before the lexical index has been built. Rebuild once per schema
        # version; triggers keep it current after that.
        row = conn.execute("SELECT v FROM meta WHERE k='fts_version'").fetchone()
        if not row or row[0] != str(SCHEMA_VERSION):
            conn.execute("INSERT INTO works_fts(works_fts) VALUES('rebuild')")
            conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('fts_version', ?)",
                         (str(SCHEMA_VERSION),))
        conn.commit()
        return True
    except sqlite3.Error:
        return False

def open_catalog(path: Path = DEFAULT_CATALOG,
                 check_same_thread: bool = True) -> sqlite3.Connection:
    # Streamlit reruns the script across worker threads, so the gallery opens
    # with check_same_thread=False to share one cached connection safely.
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    # wait out brief writer locks (e.g. the gallery reading while an enrichment
    # pass writes) instead of raising "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    # migrate older catalogs: add columns introduced after first release
    cols = {r[1] for r in conn.execute("PRAGMA table_info(works)")}
    for col in ("manual", "hidden"):
        if col not in cols:
            conn.execute(f"ALTER TABLE works ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    # status: '' | 'done' (watched/played/listened) | 'want' (wishlist)
    if "status" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN status TEXT NOT NULL DEFAULT ''")
    if "mtime" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN mtime REAL")
    if "has_subtitles" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN has_subtitles INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "INSERT OR IGNORE INTO meta (k, v) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    ensure_search_index(conn)
    return conn


def set_scan_time(conn: sqlite3.Connection, drive_label: str,
                  when: str | None = None) -> None:
    """Record when a drive's index was last scanned into the catalog
    (shown in the app's "Drives no catálogo" panel)."""
    when = when or datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)",
                 (f"scanned::{drive_label}", when))


def scan_times(conn: sqlite3.Connection) -> dict:
    """{drive_label: iso-timestamp} of each drive's last catalog scan."""
    return {k.split("::", 1)[1]: v for k, v in conn.execute(
        "SELECT k, v FROM meta WHERE k LIKE 'scanned::%'")}


def upsert_work(conn: sqlite3.Connection, w: dict) -> None:
    """Insert or update a work, keyed on (drive_label, rel_path). Enrichment
    fields are preserved on update unless explicitly provided — a re-scan of
    the index must not wipe covers/metadata already fetched."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    existing = conn.execute(
        "SELECT id, enriched, cover_path, genre, extra_json, provider, year, title"
        " FROM works WHERE drive_label=? AND rel_path=?",
        (w["drive_label"], w["rel_path"]),
    ).fetchone()

    if existing is None:
        conn.execute(
            "INSERT INTO works (type, title, title_raw, artist, year, platform,"
            " identifier, rel_path, drive_label, size_bytes, mtime,"
            " has_subtitles, extra_json, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (w["type"], w["title"], w.get("title_raw"), w.get("artist"),
             w.get("year"), w.get("platform"), w.get("identifier"),
             w["rel_path"], w["drive_label"], w.get("size_bytes"),
             w.get("mtime"), int(w.get("has_subtitles", 0)),
             w.get("extra_json"), now),
        )
    else:
        # keep enrichment; refresh only the index-derived fields. extra_json holds
        # provider payload once enriched, but the discovery-time 'have_*' summary
        # (series seasons/episodes) before that — so refresh it only while enriched=0.
        conn.execute(
            "UPDATE works SET type=?, title_raw=?, artist=?, platform=?,"
            " identifier=?, size_bytes=?, mtime=?, has_subtitles=?, updated_at=?,"
            " title=CASE WHEN enriched=1 THEN title ELSE ? END,"
            " year=CASE WHEN enriched=1 THEN year ELSE ? END,"
            " extra_json=CASE WHEN enriched=1 THEN extra_json ELSE ? END"
            " WHERE id=?",
            (w["type"], w.get("title_raw"), w.get("artist"),
             w.get("platform"), w.get("identifier"), w.get("size_bytes"),
             w.get("mtime"), int(w.get("has_subtitles", 0)), now,
             w["title"], w.get("year"), w.get("extra_json"), existing[0]),
        )


def counts_by_type(conn: sqlite3.Connection) -> dict:
    return {t: n for t, n in conn.execute(
        "SELECT type, count(*) FROM works GROUP BY type")}


def counts_by_platform(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT platform, count(*), COALESCE(SUM(size_bytes),0) FROM works"
        " WHERE type='game' GROUP BY platform ORDER BY 2 DESC").fetchall()


# ── override patch: the user's hand-work, portable across rebuilds ───────────
_OVR_COLS = ["drive_label", "rel_path", "type", "manual", "status", "hidden",
             "title", "year", "genre", "identifier", "provider", "extra_json",
             "cover_path"]


def export_overrides(conn: sqlite3.Connection) -> dict:
    """Snapshot everything the user did by hand — manual corrections, watch/
    wishlist status, hidden junk — as a re-appliable overlay keyed by
    (drive_label, rel_path). The catalog rebuilds from the drive-xray index;
    this captures the part that can't be regenerated."""
    rows = conn.execute(
        f"SELECT {', '.join(_OVR_COLS)} FROM works"
        " WHERE manual=1 OR status!='' OR COALESCE(hidden,0)=1").fetchall()
    overrides = [dict(zip(_OVR_COLS, r)) for r in rows]
    return {
        "version": 1,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(overrides),
        "overrides": overrides,
    }


def import_overrides(conn: sqlite3.Connection, data: dict) -> dict:
    """Re-apply an export_overrides() patch onto the current catalog, matching by
    (drive_label, rel_path). Rows absent from the catalog are reported, not
    created (they'll reappear on the next scan of that drive)."""
    import os
    now = datetime.datetime.now().isoformat(timespec="seconds")
    applied = missing = 0
    for o in data.get("overrides", []):
        row = conn.execute(
            "SELECT id FROM works WHERE drive_label=? AND rel_path=?",
            (o.get("drive_label"), o.get("rel_path"))).fetchone()
        if not row:
            missing += 1
            continue
        sets = ["status=?", "hidden=?", "updated_at=?"]
        vals = [o.get("status") or "", int(o.get("hidden") or 0), now]
        if o.get("manual"):
            sets += ["title=?", "year=?", "genre=?", "identifier=?",
                     "provider=?", "extra_json=?", "manual=1", "enriched=1"]
            vals += [o.get("title"), o.get("year"), o.get("genre"),
                     o.get("identifier"), o.get("provider"), o.get("extra_json")]
            cp = o.get("cover_path")
            if cp and os.path.exists(cp):        # cover still on disk → re-link
                sets.append("cover_path=?")
                vals.append(cp)
        vals.append(row[0])
        conn.execute(f"UPDATE works SET {', '.join(sets)} WHERE id=?", vals)
        applied += 1
    conn.commit()
    return {"applied": applied, "missing": missing,
            "total": len(data.get("overrides", []))}


# --- portable bundle ------------------------------------------------------
# Enrichment costs API keys and time. Once a title has its cover and metadata,
# no other machine should have to earn that again — especially not by setting
# up TMDB and IGDB accounts of its own. A bundle is the whole result of
# enrichment (metadata + the actual cover images) in one file you can copy on a
# USB stick, with no cloud and no credentials involved.

_BUNDLE_COLS = ["drive_label", "rel_path", "type", "title", "title_raw",
                "artist", "year", "platform", "genre", "identifier", "provider",
                "extra_json", "status", "hidden", "manual", "enriched",
                "has_subtitles"]


def export_bundle(conn: sqlite3.Connection, out_path: Path,
                  covers_dir: Path | None = None, progress=None) -> dict:
    """Write every enriched title plus its cover image to a portable .zip.

    Deliberately excludes secrets.json: a bundle is meant to be copied around,
    and API keys must not travel with it. The point is that the receiving
    machine never needs keys at all.
    """
    import zipfile
    covers_dir = Path(covers_dir or _config.COVERS_DIR)
    rows = conn.execute(
        f"SELECT {', '.join(_BUNDLE_COLS)}, cover_path FROM works"
        " WHERE enriched=1 OR manual=1 OR status!='' OR cover_path IS NOT NULL"
    ).fetchall()

    items, seen_covers = [], set()
    for r in rows:
        item = dict(zip(_BUNDLE_COLS, r[:-1]))
        cover = r[-1]
        if cover:
            # Store the file name only. Absolute paths are meaningless on the
            # receiving machine, and resolve_cover() already looks up by name.
            name = str(cover).replace("\\", "/").rsplit("/", 1)[-1]
            src = covers_dir / name
            if src.is_file() and src.stat().st_size > 0:
                item["cover"] = name
                seen_covers.add(name)
        items.append(item)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(items) + len(seen_covers)
    done = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({
            "version": 1,
            "kind": "media-catalog-bundle",
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "works": len(items),
            "covers": len(seen_covers),
        }, indent=2, ensure_ascii=False))
        z.writestr("works.json",
                   json.dumps(items, ensure_ascii=False, indent=1))
        done += len(items)
        for name in sorted(seen_covers):
            z.write(covers_dir / name, f"covers/{name}")
            done += 1
            if progress and done % 25 == 0:
                progress(done, total)
    if progress:
        progress(total, total)
    return {"works": len(items), "covers": len(seen_covers),
            "path": str(out_path), "bytes": out_path.stat().st_size}


def import_bundle(conn: sqlite3.Connection, zip_path: Path,
                  covers_dir: Path | None = None, progress=None) -> dict:
    """Apply a bundle onto this machine's catalog.

    Titles already present are updated; titles this machine has never scanned
    are *created*, so a bundle alone is enough to browse the collection on a
    computer that has no drive-xray index and no API keys. Such rows carry no
    size or mtime -- there is no file here to measure -- but they show the
    drive each title lives on, which is the question being asked.

    A local manual correction is never overwritten by an imported row.
    """
    import shutil
    import zipfile
    covers_dir = Path(covers_dir or _config.COVERS_DIR)
    covers_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    updated = created = skipped_manual = covers = 0

    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if "works.json" not in names:
            raise ValueError(f"{zip_path} is not a media-catalog bundle")
        items = json.loads(z.read("works.json").decode("utf-8"))

        for name in names:
            if not name.startswith("covers/") or name.endswith("/"):
                continue
            # basename only: never let an archive path escape covers_dir
            base = name.split("/")[-1]
            if not base or base in (".", ".."):
                continue
            dest = covers_dir / base
            if dest.exists() and dest.stat().st_size > 0:
                continue
            with z.open(name) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            covers += 1

        total = len(items)
        for i, o in enumerate(items):
            row = conn.execute(
                "SELECT id, COALESCE(manual,0) FROM works"
                " WHERE drive_label=? AND rel_path=?",
                (o.get("drive_label"), o.get("rel_path"))).fetchone()
            cover_path = str(covers_dir / o["cover"]) if o.get("cover") else None
            if row and row[1] and not o.get("manual"):
                skipped_manual += 1          # a correction made here wins
                continue
            fields = [c for c in _BUNDLE_COLS
                      if c not in ("drive_label", "rel_path")]
            if row:
                sets = [f"{c}=?" for c in fields] + ["updated_at=?"]
                vals = [o.get(c) for c in fields] + [now]
                if cover_path:
                    sets.append("cover_path=?"); vals.append(cover_path)
                vals.append(row[0])
                conn.execute(f"UPDATE works SET {', '.join(sets)} WHERE id=?", vals)
                updated += 1
            else:
                cols = ["drive_label", "rel_path"] + fields + ["updated_at"]
                vals = [o.get("drive_label"), o.get("rel_path")] \
                    + [o.get(c) for c in fields] + [now]
                if cover_path:
                    cols.append("cover_path"); vals.append(cover_path)
                conn.execute(
                    f"INSERT INTO works ({', '.join(cols)})"
                    f" VALUES ({', '.join('?' * len(cols))})", vals)
                created += 1
            if progress and i % 50 == 0:
                progress(i + 1, total)
    conn.commit()
    if progress:
        progress(len(items), len(items))
    return {"updated": updated, "created": created, "covers": covers,
            "skipped_manual": skipped_manual, "total": len(items)}
