"""Deezer Search API — album cover fallback. No API key, and far friendlier
rate limits than iTunes (which 403s under bulk use). Reuses the album-name
cleaner from the iTunes client.
"""
from __future__ import annotations

import datetime
import json
import time
import urllib.parse
import urllib.request

from media_catalog import config
from media_catalog.enrich.itunes import clean_album, _norm

_API = "https://api.deezer.com/search/album"
_UA = "media-catalog/0.1"


def _http_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _cache_get(conn, key):
    row = conn.execute(
        "SELECT response FROM enrich_cache WHERE provider='deezer' AND key=?",
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
                 " VALUES ('deezer',?,?,?)", (key, json.dumps(payload), now))


def search_album(conn, artist: str, album: str) -> dict | None:
    key = f"{(artist or '').lower()}|{album.lower()}"
    cached = _cache_get(conn, key)
    if cached is not None:
        return cached or None
    q = f'artist:"{artist}" album:"{album}"' if artist else f'album:"{album}"'
    url = f"{_API}?q={urllib.parse.quote(q)}&limit=5"
    data = _http_json(url)
    results = (data or {}).get("data") or []
    nal, na = _norm(album), _norm(artist)
    best = {}
    for r in results:
        if _norm(r.get("title")) == nal and (
                not artist or na in _norm((r.get("artist") or {}).get("name"))):
            best = r
            break
    if not best and results:
        best = results[0]
    _cache_put(conn, key, best)
    conn.commit()
    return best or None


def search_candidates(artist: str, album: str) -> list:
    """Top Deezer album matches for the manual picker (uncached)."""
    q = f"{artist} {album}".strip()
    url = f"{_API}?q={urllib.parse.quote(q)}&limit=6"
    return ((_http_json(url) or {}).get("data") or [])[:6]


def apply_candidate(conn, work_id: int, best: dict) -> None:
    import datetime
    art = best.get("cover_xl") or best.get("cover_big")
    cover = _download_cover(art, work_id) if art else None
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE works SET title=?, artist=?, cover_path=COALESCE(?, cover_path),"
        " provider='deezer', enriched=1, manual=1, updated_at=? WHERE id=?",
        (best.get("title"), (best.get("artist") or {}).get("name"),
         cover, now, work_id))
    conn.commit()


def _download_cover(url: str, work_id: int) -> str | None:
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.COVERS_DIR / f"album_{work_id}.jpg"
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


def enrich_genres(conn, sleep: float = 0.12, progress=None) -> dict:
    """Fill real musical genres (Rock/Pop/…) for albums. The MusicBrainz pass
    left release *types* (Album/Single/EP) in the genre column — clear those and
    resolve the Deezer genre_id of each album to a name."""
    gm = {}
    gdata = _http_json("https://api.deezer.com/genre")
    for g in (gdata or {}).get("data", []):
        gm[g.get("id")] = g.get("name")
    conn.execute("UPDATE works SET genre=NULL WHERE type='album' AND"
                 " genre IN ('Album','Single','EP','Worldwide','Broadcast','')")
    conn.commit()
    rows = conn.execute(
        "SELECT id, title, artist FROM works WHERE type='album'"
        " AND (genre IS NULL OR genre='')").fetchall()
    matched = 0
    now = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    for i, (wid, album, artist) in enumerate(rows):
        a, al = clean_album(artist, album)
        if not al:
            continue
        r = search_album(conn, a, al)          # cached where already searched
        gid = r.get("genre_id") if r else None
        name = gm.get(gid) if gid and gid > 0 else None
        if name:
            conn.execute("UPDATE works SET genre=?, updated_at=? WHERE id=?",
                         (name, now, wid))
            matched += 1
        if i % 20 == 0:
            conn.commit()
            if progress:
                progress(i + 1, len(rows), matched, 0)
        if sleep:
            time.sleep(sleep)
    conn.commit()
    if progress:
        progress(len(rows), len(rows), matched, 0)
    return {"matched": matched, "total": len(rows)}


def enrich_albums(conn, limit: int | None = None, sleep: float = 0.15,
                  progress=None) -> dict:
    rows = conn.execute(
        "SELECT id, title, artist FROM works"
        " WHERE type='album' AND cover_path IS NULL"
        + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    matched = missed = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for i, (wid, album, artist) in enumerate(rows):
        artist, album = clean_album(artist, album)
        if not album or album.lower() in ("unknown album", "unknown"):
            missed += 1
            continue
        r = search_album(conn, artist, album)
        art = (r.get("cover_xl") or r.get("cover_big")) if r else None
        cover = _download_cover(art, wid) if art else None
        if cover:
            conn.execute(
                "UPDATE works SET cover_path=?, provider='deezer',"
                " updated_at=? WHERE id=?", (cover, now, wid))
            matched += 1
        else:
            missed += 1
        if i % 20 == 0:
            conn.commit()
            if progress:
                progress(i + 1, len(rows), matched, missed)
        if sleep:
            time.sleep(sleep)
    conn.commit()
    if progress:
        progress(len(rows), len(rows), matched, missed)
    return {"matched": matched, "missed": missed, "total": len(rows)}
