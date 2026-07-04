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

SCHEMA_VERSION = 1

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
    conn.execute(
        "INSERT OR IGNORE INTO meta (k, v) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
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
            " identifier, rel_path, drive_label, size_bytes, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (w["type"], w["title"], w.get("title_raw"), w.get("artist"),
             w.get("year"), w.get("platform"), w.get("identifier"),
             w["rel_path"], w["drive_label"], w.get("size_bytes"), now),
        )
    else:
        # keep enrichment; refresh only the index-derived fields
        conn.execute(
            "UPDATE works SET type=?, title_raw=?, artist=?, platform=?,"
            " identifier=?, size_bytes=?, updated_at=?,"
            " title=CASE WHEN enriched=1 THEN title ELSE ? END,"
            " year=CASE WHEN enriched=1 THEN year ELSE ? END"
            " WHERE id=?",
            (w["type"], w.get("title_raw"), w.get("artist"),
             w.get("platform"), w.get("identifier"), w.get("size_bytes"),
             now, w["title"], w.get("year"), existing[0]),
        )


def counts_by_type(conn: sqlite3.Connection) -> dict:
    return {t: n for t, n in conn.execute(
        "SELECT type, count(*) FROM works GROUP BY type")}


def counts_by_platform(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT platform, count(*), COALESCE(SUM(size_bytes),0) FROM works"
        " WHERE type='game' GROUP BY platform ORDER BY 2 DESC").fetchall()
