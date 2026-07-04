"""IGDB enrichment for games — OAuth (Twitch client-credentials) + game search
by name, attach cover art, genres, release year. Responses cached in
enrich_cache; covers downloaded once. stdlib-only (urllib).

IGDB uses the 'apicalypse' query language over POST bodies.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request

from media_catalog import config

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_API = "https://api.igdb.com/v4"
_IMG = "https://images.igdb.com/igdb/image/upload/t_cover_big/{}.jpg"
_UA = "media-catalog/0.1"

# region / dump / format tags to strip from ROM filenames before searching
_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_JUNK_RE = re.compile(
    r"\b(usa|europe|japan|eur|jpn|ntsc|pal|rev\s*\d|v\d+(\.\d+)*|"
    r"disc\s*\d|proper|multi\d?|en|fr|de|es|it|pt)\b", re.I)


def get_token() -> str | None:
    cid, secret = config.get("igdb_client_id"), config.get("igdb_client_secret")
    if not (cid and secret):
        return None
    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret,
        "grant_type": "client_credentials",
    }).encode()
    try:
        req = urllib.request.Request(_TOKEN_URL, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode()).get("access_token")
    except Exception:
        return None


def clean_game_name(name: str) -> str:
    n = _TAG_RE.sub("", name)          # drop (…) and […] groups
    n = re.sub(r"[._]+", " ", n)
    n = _JUNK_RE.sub("", n)
    return re.sub(r"\s+", " ", n).strip(" -")


def _post(endpoint: str, body: str, cid: str, token: str) -> list | None:
    try:
        req = urllib.request.Request(
            f"{_API}/{endpoint}", data=body.encode(),
            headers={"Client-ID": cid, "Authorization": f"Bearer {token}",
                     "Accept": "application/json", "User-Agent": _UA},
            method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _cache_get(conn, key):
    row = conn.execute(
        "SELECT response FROM enrich_cache WHERE provider='igdb' AND key=?",
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
                 " VALUES ('igdb',?,?,?)", (key, json.dumps(payload), now))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _best_match(results: list, query: str) -> dict:
    """IGDB ranks by popularity, so a search for 'The Last of Us' can return
    'Part II' first. Prefer an exact normalised name, then a prefix match, then
    fall back to the top result."""
    if not results:
        return {}
    nq = _norm(query)
    for r in results:                       # exact
        if _norm(r.get("name")) == nq:
            return r
    for r in results:                       # one is a prefix of the other
        nr = _norm(r.get("name"))
        if nr and (nr.startswith(nq) or nq.startswith(nr)):
            return r
    return results[0]


def search_game(conn, name: str, cid: str, token: str) -> dict | None:
    q = clean_game_name(name)
    if not q:
        return None
    key = f"search|{q.lower()}"
    cached = _cache_get(conn, key)
    if cached is not None:
        return cached or None
    safe = q.replace('"', "")               # escape quotes in the search term
    body = (f'search "{safe}"; fields name, cover.image_id, first_release_date,'
            f' genres.name, total_rating; limit 8;')
    res = _post("games", body, cid, token) or []
    best = _best_match(res, q)
    _cache_put(conn, key, best)
    conn.commit()
    return best or None


def search_candidates(name: str, cid: str, token: str) -> list:
    """Top IGDB matches for the manual picker (uncached — interactive)."""
    safe = (name or "").replace('"', "")
    body = (f'search "{safe}"; fields name, cover.image_id, first_release_date,'
            f' genres.name; limit 6;')
    return _post("games", body, cid, token) or []


def apply_candidate(conn, work_id: int, best: dict, cid: str) -> None:
    cover = None
    if best.get("cover", {}).get("image_id"):
        cover = _download_cover(best["cover"]["image_id"], work_id)
    ts = best.get("first_release_date")
    yr = datetime.datetime.utcfromtimestamp(ts).year if ts else None
    gg = ", ".join(g.get("name", "") for g in best.get("genres", []))
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE works SET title=?, year=COALESCE(?, year), genre=?,"
        " cover_path=COALESCE(?, cover_path), identifier=?, provider='igdb',"
        " enriched=1, manual=1, updated_at=? WHERE id=?",
        (best.get("name"), yr, gg or None, cover, f"igdb:{best['id']}",
         now, work_id))
    conn.commit()


def _download_cover(image_id: str, work_id: int) -> str | None:
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.COVERS_DIR / f"game_{work_id}.jpg"
    if dest.exists():
        return str(dest)
    try:
        req = urllib.request.Request(_IMG.format(image_id),
                                     headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            dest.write_bytes(r.read())
        return str(dest)
    except Exception:
        return None


def enrich_games(conn, limit: int | None = None, sleep: float = 0.28,
                 progress=None) -> dict:
    cid = config.get("igdb_client_id")
    token = get_token()
    if not token:
        return {"error": "no IGDB token", "matched": 0, "missed": 0, "total": 0}
    rows = conn.execute(
        "SELECT id, title FROM works WHERE type='game' AND enriched=0"
        + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    matched = missed = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for i, (wid, title) in enumerate(rows):
        best = search_game(conn, title, cid, token)
        cover = None
        if best and best.get("cover", {}).get("image_id"):
            cover = _download_cover(best["cover"]["image_id"], wid)
        if best and best.get("id"):
            ts = best.get("first_release_date")
            yr = datetime.datetime.utcfromtimestamp(ts).year if ts else None
            gg = ", ".join(g.get("name", "") for g in best.get("genres", []))
            conn.execute(
                "UPDATE works SET title=CASE WHEN ?<>'' THEN ? ELSE title END,"
                " year=COALESCE(?, year), genre=?, cover_path=?,"
                " identifier=COALESCE(identifier, ?), provider='igdb',"
                " enriched=1, extra_json=?, updated_at=? WHERE id=?",
                (best.get("name") or "", best.get("name") or title, yr,
                 gg or None, cover, f"igdb:{best['id']}",
                 json.dumps({"total_rating": best.get("total_rating")}),
                 now, wid))
            matched += 1
        else:
            conn.execute("UPDATE works SET enriched=1, provider='igdb-miss',"
                         " updated_at=? WHERE id=?", (now, wid))
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
