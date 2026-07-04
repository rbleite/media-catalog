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

DEFAULT_CATALOG = Path.home() / "tools" / "media-catalog" / "catalog.db"



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
            " has_subtitles, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (w["type"], w["title"], w.get("title_raw"), w.get("artist"),
             w.get("year"), w.get("platform"), w.get("identifier"),
             w["rel_path"], w["drive_label"], w.get("size_bytes"),
             w.get("mtime"), int(w.get("has_subtitles", 0)), now),
        )
    else:
        # keep enrichment; refresh only the index-derived fields
        conn.execute(
            "UPDATE works SET type=?, title_raw=?, artist=?, platform=?,"
            " identifier=?, size_bytes=?, mtime=?, has_subtitles=?, updated_at=?,"
            " title=CASE WHEN enriched=1 THEN title ELSE ? END,"
            " year=CASE WHEN enriched=1 THEN year ELSE ? END"
            " WHERE id=?",
            (w["type"], w.get("title_raw"), w.get("artist"),
             w.get("platform"), w.get("identifier"), w.get("size_bytes"),
             w.get("mtime"), int(w.get("has_subtitles", 0)), now,
             w["title"], w.get("year"), existing[0]),
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
