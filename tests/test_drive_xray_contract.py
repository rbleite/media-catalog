"""The schema contract media-catalog depends on in a drive-xray index.

media-catalog reads drive-xray's .db directly, so a schema change over there
can silently empty the catalogue here — with no error, just zero works. That
already nearly happened: drive-xray v7 moved the rows into `entries_core` and
turned `entries` into a VIEW. The read survived only because the view re-exposes
the same columns.

This builds a v7-shaped index by hand (no drive-xray checkout needed, so it runs
in CI) and asserts scan_index still finds each kind of work. If drive-xray ever
drops the view or renames a column, this fails here instead of quietly
cataloguing nothing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from media_catalog.discover import scan_index

# the shape drive-xray v7 presents: physical rows in entries_core + paths,
# with `entries` as a compatibility VIEW
V7_SCHEMA = """
CREATE TABLE snapshots (id INTEGER PRIMARY KEY, taken_at TEXT);
CREATE TABLE paths (id INTEGER PRIMARY KEY, full_path TEXT);
CREATE TABLE entries_core (
    id INTEGER PRIMARY KEY, snapshot_id INTEGER, path_id INTEGER,
    is_dir INTEGER, size INTEGER, mtime REAL, error TEXT
);
CREATE VIEW entries AS
    SELECT c.id, c.snapshot_id, p.full_path AS rel_path, c.is_dir,
           c.size, c.mtime, c.error
      FROM entries_core c JOIN paths p ON p.id = c.path_id;
"""

FILES = [
    ("Ricardo/HD Movies", 1),
    ("Ricardo/HD Movies/Blade.Runner.1982.1080p.BluRay.x264-LOL.mkv", 0),
    ("MP3", 1), ("MP3/Pink Floyd", 1), ("MP3/Pink Floyd/The Wall", 1),
    ("MP3/Pink Floyd/The Wall/01 - Another Brick.mp3", 0),
    ("Series", 1), ("Series/Breaking Bad", 1),
    ("Series/Breaking Bad/Breaking.Bad.S01E01.720p.HDTV-FQM.mkv", 0),
    # the NxNN convention must reach the catalogue too
    ("Series/Os Maias", 1), ("Series/Os Maias/Os.Maias.2x05.mkv", 0),
]


@pytest.fixture()
def v7_index(tmp_path) -> Path:
    db = tmp_path / "dx.db"
    conn = sqlite3.connect(db)
    conn.executescript(V7_SCHEMA)
    conn.execute("INSERT INTO snapshots (id, taken_at) VALUES (1, '2026-01-01')")
    for i, (rel, is_dir) in enumerate(FILES, 1):
        conn.execute("INSERT INTO paths (id, full_path) VALUES (?,?)", (i, rel))
        conn.execute(
            "INSERT INTO entries_core"
            " (snapshot_id, path_id, is_dir, size, mtime, error)"
            " VALUES (1,?,?,?,?,NULL)",
            (i, is_dir, 0 if is_dir else 500_000, 1700000000.0))
    conn.commit()
    conn.close()
    return db


def test_reads_a_v7_index_through_the_entries_view(v7_index):
    works = list(scan_index(v7_index, "TestDrive"))
    assert works, "a v7 index must still yield works — the view is the contract"


def test_finds_each_kind_of_work(v7_index):
    titles = {w["title"] for w in scan_index(v7_index, "TestDrive")}
    assert "Blade Runner" in titles      # movie, under a movie root
    assert "The Wall" in titles          # album, under a music root
    assert "Breaking Bad" in titles      # series, SxxEyy
    assert "Os Maias" in titles          # series, NxNN
