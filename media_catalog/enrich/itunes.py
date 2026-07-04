"""iTunes Search API — album cover fallback (better cover coverage than the
Cover Art Archive). No API key. Used to fill albums MusicBrainz left cover-less.
"""
from __future__ import annotations

import datetime
import json
import re
import time
import urllib.parse
import urllib.request

from media_catalog import config

_API = "https://itunes.apple.com/search"
_UA = "media-catalog/0.1"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _http_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _cache_get(conn, key):
    row = conn.execute(
        "SELECT response FROM enrich_cache WHERE provider='itunes' AND key=?",
        (key,)).fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            return None
    return None


def _cache_put(conn, key, payload):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT OR REPLACE INTO enrich_cache (provider,key,response,fetched_at)"
                 " VALUES ('itunes',?,?,?)", (key, json.dumps(payload), now))


def search_album(conn, artist: str, album: str) -> dict | None:
    key = f"{(artist or '').lower()}|{album.lower()}"
    cached = _cache_get(conn, key)
    if cached is not None:
        return cached or None
    term = f"{artist} {album}".strip()
    url = (f"{_API}?term={urllib.parse.quote(term)}&entity=album&limit=5"
           "&media=music")
    data = _http_json(url)
    results = (data or {}).get("results") or []
    na, nal = _norm(artist), _norm(album)
    best = {}
    for r in results:                       # prefer a close album-name match
        if _norm(r.get("collectionName")) == nal:
            if not artist or _norm(r.get("artistName")) == na or na in _norm(r.get("artistName")):
                best = r
                break
    if not best and results:
        best = results[0]
    _cache_put(conn, key, best)
    conn.commit()
    return best or None


def _download_cover(art_url: str, work_id: int) -> str | None:
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.COVERS_DIR / f"album_{work_id}.jpg"
    # bump the artwork to 600x600
    url = re.sub(r"/\d+x\d+bb\.jpg$", "/600x600bb.jpg", art_url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 500:
            return None
        dest.write_bytes(data)
        return str(dest)
    except Exception:
        return None


def enrich_albums(conn, limit: int | None = None, sleep: float = 0.3,
                  progress=None) -> dict:
    """Fallback: fill albums that still have no cover (whatever their prior
    provider) using iTunes artwork."""
    rows = conn.execute(
        "SELECT id, title, artist FROM works"
        " WHERE type='album' AND cover_path IS NULL"
        + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    matched = missed = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for i, (wid, album, artist) in enumerate(rows):
        r = search_album(conn, artist, album)
        cover = _download_cover(r["artworkUrl100"], wid) \
            if r and r.get("artworkUrl100") else None
        if cover:
            rd = (r.get("releaseDate") or "")[:4]
            conn.execute(
                "UPDATE works SET cover_path=?, provider='itunes',"
                " year=COALESCE(year, ?), genre=COALESCE(genre, ?),"
                " updated_at=? WHERE id=?",
                (cover, int(rd) if rd.isdigit() else None,
                 r.get("primaryGenreName"), now, wid))
            matched += 1
        else:
            missed += 1
        if i % 20 == 0 and progress:
            conn.commit()
            progress(i + 1, len(rows), matched, missed)
        if sleep:
            time.sleep(sleep)
    conn.commit()
    if progress:
        progress(len(rows), len(rows), matched, missed)
    return {"matched": matched, "missed": missed, "total": len(rows)}
