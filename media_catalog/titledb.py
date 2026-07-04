"""Offline title-id → name resolution for console games.

Some game folders are named only by their serial (e.g. a bare `BLUS31202`),
which no cover DB can search. This resolves those serials to real titles using
GameTDB's offline databases, so the IGDB pass can then find covers.

The parsed serial→title map is cached as a compact JSON in data/ so we don't
re-parse the 6 MB XML each run.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PS3_MAP_JSON = DATA_DIR / "ps3_titles.json"

# GameTDB downloadable DBs (unzip → *.xml). Kept out of git; fetch on demand.
GAMETDB_URLS = {
    "PS3": "https://www.gametdb.com/ps3tdb.zip",
    "Wii": "https://www.gametdb.com/wiitdb.zip",
    "WiiU": "https://www.gametdb.com/wiiutdb.zip",
}

_SERIAL_RE = re.compile(r"^([A-Z]{4}\d{4,5})")


_GAME_RE = re.compile(r"<game\b(?P<attrs>[^>]*)>(?P<body>.*?)</game>", re.S)
_ID_RE = re.compile(r"<id>\s*([^<\s]+)\s*</id>")
_EN_TITLE_RE = re.compile(r'<locale lang="EN">\s*<title>([^<]*)</title>', re.S)
_NAME_ATTR_RE = re.compile(r'name="([^"]*)"')
_YEAR_RE = re.compile(r'<date year="(\d{4})"')


def parse_gametdb(xml_path: Path) -> dict:
    """Parse a GameTDB *tdb.xml into {serial: {'title':…, 'year':…}}.

    Uses regex block extraction rather than a strict XML parser — GameTDB dumps
    occasionally contain an invalid token that breaks ElementTree, and we only
    need id + title + year per <game>."""
    text = xml_path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, dict] = {}
    for g in _GAME_RE.finditer(text):
        body = g.group("body")
        mid = _ID_RE.search(body)
        if not mid:
            continue
        gid = mid.group(1).strip().upper()
        mt = _EN_TITLE_RE.search(body)
        if mt and mt.group(1).strip():
            title = mt.group(1).strip()
        else:  # fall back to the name attribute (drop a trailing region tag)
            na = _NAME_ATTR_RE.search(g.group("attrs"))
            title = re.sub(r"\s*\([^)]*\)$", "", na.group(1)).strip() if na else ""
        my = _YEAR_RE.search(body)
        if title and gid not in out:
            out[gid] = {"title": title,
                        "year": int(my.group(1)) if my else None}
    return out


def save_map(m: dict, path: Path = PS3_MAP_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


def load_map(path: Path = PS3_MAP_JSON) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _serial_of(title: str, identifier: str | None) -> str | None:
    for cand in (identifier or "", title or ""):
        m = _SERIAL_RE.match(cand.strip().upper())
        if m:
            return m.group(1)
    return None


def resolve_serial_titles(conn: sqlite3.Connection, title_map: dict,
                          platform: str = "PS3") -> int:
    """For games whose title is just a serial, look the serial up and replace
    the title with the real name (and reset enrichment so covers get fetched).
    Returns how many were resolved."""
    rows = conn.execute(
        "SELECT id, title, identifier FROM works WHERE type='game' AND platform=?",
        (platform,)).fetchall()
    resolved = 0
    for wid, title, ident in rows:
        # only touch serial-only titles (has a serial, and the title *is* one)
        serial = _serial_of(title, ident)
        if not serial or not _SERIAL_RE.match((title or "").strip().upper()):
            continue
        hit = title_map.get(serial)
        if hit and hit["title"]:
            conn.execute(
                "UPDATE works SET title=?, year=COALESCE(year, ?),"
                " identifier=COALESCE(identifier, ?), enriched=0, provider=NULL,"
                " cover_path=NULL WHERE id=?",
                (hit["title"], hit.get("year"), serial, wid))
            resolved += 1
    conn.commit()
    return resolved
