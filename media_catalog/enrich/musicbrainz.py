"""MusicBrainz + Cover Art Archive enrichment for albums — search a release
group by artist+album, take its cover from the Cover Art Archive. No API key,
but a descriptive User-Agent and ~1 req/s are required by MusicBrainz.
"""
from __future__ import annotations

import datetime
import json
import time
import urllib.parse
import urllib.request

from media_catalog import config

_MB = "https://musicbrainz.org/ws/2"
_CAA = "https://coverartarchive.org/release-group/{}/front-500"
_UA = "media-catalog/0.1 ( https://github.com/rbleite )"


def _http_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _cache_get(conn, key):
    row = conn.execute(
        "SELECT response FROM enrich_cache WHERE provider='musicbrainz' AND key=?",
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
                 " VALUES ('musicbrainz',?,?,?)", (key, json.dumps(payload), now))


def search_release_group(conn, artist: str, album: str) -> dict | None:
    key = f"rg|{(artist or '').lower()}|{album.lower()}"
    cached = _cache_get(conn, key)
    if cached is not None:
        return cached or None
    terms = f'releasegroup:"{album}"'
    if artist:
        terms += f' AND artist:"{artist}"'
    url = f"{_MB}/release-group/?query={urllib.parse.quote(terms)}&fmt=json&limit=3"
    data = _http_json(url)
    rgs = (data or {}).get("release-groups") or []
    best = rgs[0] if rgs else {}
    _cache_put(conn, key, best)
    conn.commit()
    return best or None


def _download_cover(rgid: str, work_id: int) -> str | None:
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.COVERS_DIR / f"album_{work_id}.jpg"
    if dest.exists():
        return str(dest)
    try:
        req = urllib.request.Request(_CAA.format(rgid), headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 500:                 # not a real image
            return None
        dest.write_bytes(data)
        return str(dest)
    except Exception:
        return None


def enrich_albums(conn, limit: int | None = None, sleep: float = 1.1,
                  progress=None) -> dict:
    rows = conn.execute(
        "SELECT id, title, artist FROM works WHERE type='album' AND enriched=0"
        + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    matched = missed = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for i, (wid, album, artist) in enumerate(rows):
        rg = search_release_group(conn, artist, album)
        cover = _download_cover(rg["id"], wid) if rg and rg.get("id") else None
        if cover:
            rd = (rg.get("first-release-date") or "")[:4]
            yr = int(rd) if rd.isdigit() else None
            conn.execute(
                "UPDATE works SET year=COALESCE(?, year), cover_path=?,"
                " identifier=?, provider='musicbrainz', genre=?, enriched=1,"
                " updated_at=? WHERE id=?",
                (yr, cover, f"mbid:{rg['id']}", rg.get("primary-type"),
                 now, wid))
            matched += 1
        else:
            conn.execute("UPDATE works SET enriched=1, provider='mb-miss',"
                         " updated_at=? WHERE id=?", (now, wid))
            missed += 1
        if i % 10 == 0:
            conn.commit()
            if progress:
                progress(i + 1, len(rows), matched, missed)
        if sleep:
            time.sleep(sleep)
    conn.commit()
    if progress:
        progress(len(rows), len(rows), matched, missed)
    return {"matched": matched, "missed": missed, "total": len(rows)}
