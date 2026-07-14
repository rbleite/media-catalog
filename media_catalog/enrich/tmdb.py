"""TMDB enrichment for movies — search by title(+year), attach poster, year,
overview, genres, rating. Responses are cached in enrich_cache; posters are
downloaded once into COVERS_DIR. stdlib-only (urllib) to avoid a hard dep.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

from media_catalog import config

_API = "https://api.themoviedb.org/3"
_UA = {"User-Agent": "media-catalog/0.1 (+personal)"}
_genre_map: dict[int, str] | None = None


def _http_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _cache_get(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute(
        "SELECT response FROM enrich_cache WHERE provider='tmdb' AND key=?",
        (key,),
    ).fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            return None
    return None


def _cache_put(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO enrich_cache (provider, key, response, fetched_at)"
        " VALUES ('tmdb', ?, ?, ?)",
        (key, json.dumps(payload), now),
    )


def _genres(api_key: str) -> dict[int, str]:
    global _genre_map
    if _genre_map is None:
        data = _http_json(f"{_API}/genre/movie/list?api_key={api_key}&language=en-US")
        _genre_map = {g["id"]: g["name"] for g in (data or {}).get("genres", [])}
    return _genre_map


def search_movie(conn: sqlite3.Connection, title: str, year: int | None,
                 api_key: str) -> dict | None:
    """Return the best TMDB match (cached). None if nothing found."""
    key = f"search|{title.lower()}|{year or ''}"
    cached = _cache_get(conn, key)
    if cached is not None:
        return cached or None  # {} cached = known-miss
    q = urllib.parse.quote(title)
    url = f"{_API}/search/movie?api_key={api_key}&query={q}"
    if year:
        url += f"&year={year}"
    data = _http_json(url)
    results = (data or {}).get("results") or []
    best = results[0] if results else {}
    _cache_put(conn, key, best)
    conn.commit()
    return best or None


def _norm(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _acceptable(best: dict, title: str, year: int | None) -> bool:
    """Reject TMDB false positives. Year is the strong signal: when the folder
    has a year, require the release year within ±1. Year-less folders (often
    home videos) must match the title exactly once normalised — this culls the
    'Ricardo'/'Marta' junk that otherwise matches obscure films."""
    rel = best.get("release_date") or ""
    by = int(rel[:4]) if rel[:4].isdigit() else None
    if year:
        return by is not None and abs(by - year) <= 1
    return _norm(best.get("title")) == _norm(title) and len(_norm(title)) >= 4


_IMDB_RE = re.compile(r"tt\d{6,}")


def imdb_id_for(conn: sqlite3.Connection, tmdb_id: str | int,
                api_key: str) -> str | None:
    """The IMDb id (tt…) for a TMDB movie, fetched on demand and cached."""
    key = f"imdb|{tmdb_id}"
    cached = _cache_get(conn, key)
    if cached is not None:
        return (cached or {}).get("imdb_id")
    data = _http_json(f"{_API}/movie/{tmdb_id}/external_ids?api_key={api_key}") or {}
    _cache_put(conn, key, data)
    conn.commit()
    return data.get("imdb_id")


def find_by_imdb(imdb: str, api_key: str) -> dict | None:
    """Resolve an IMDb id or URL (tt…) to the matching TMDB movie."""
    m = _IMDB_RE.search(imdb or "")
    if not m:
        return None
    data = _http_json(f"{_API}/find/{m.group(0)}?api_key={api_key}"
                      "&external_source=imdb_id")
    res = (data or {}).get("movie_results") or []
    return res[0] if res else None


def search_candidates(title: str, year: int | None, api_key: str,
                      lang: str = "en-US") -> list:
    """Top TMDB matches for the manual picker (uncached — interactive)."""
    url = (f"{_API}/search/movie?api_key={api_key}"
           f"&query={urllib.parse.quote(title)}&language={lang}")
    if year:
        url += f"&year={year}"
    return ((_http_json(url) or {}).get("results") or [])[:6]


def apply_candidate(conn: sqlite3.Connection, work_id: int, best: dict,
                    api_key: str) -> None:
    """Apply a user-picked TMDB result to a work (download cover, mark manual)."""
    poster = best.get("poster_path")
    cover = _download_cover(poster, work_id) if poster else None
    genres = _genres(api_key)
    gnames = ", ".join(genres.get(g, "") for g in (best.get("genre_ids") or [])).strip(", ")
    rel = best.get("release_date") or ""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE works SET title=?, year=?, genre=?, cover_path=COALESCE(?, cover_path),"
        " identifier=?, provider='tmdb', enriched=1, manual=1, extra_json=?,"
        " updated_at=? WHERE id=?",
        (best.get("title"), int(rel[:4]) if rel[:4].isdigit() else None,
         gnames or None, cover, f"tmdb:{best['id']}",
         json.dumps({"overview": best.get("overview"),
                     "vote_average": best.get("vote_average"),
                     "poster_path": poster}), now, work_id))
    conn.commit()


# ── TV series ───────────────────────────────────────────────────────────────
_genre_map_tv: dict[int, str] | None = None


def _genres_tv(api_key: str) -> dict[int, str]:
    global _genre_map_tv
    if _genre_map_tv is None:
        data = _http_json(f"{_API}/genre/tv/list?api_key={api_key}&language=en-US")
        _genre_map_tv = {g["id"]: g["name"] for g in (data or {}).get("genres", [])}
    return _genre_map_tv


def search_tv(conn: sqlite3.Connection, title: str, api_key: str) -> dict | None:
    key = f"tv|{title.lower()}"
    cached = _cache_get(conn, key)
    if cached is not None:
        return cached or None
    url = f"{_API}/search/tv?api_key={api_key}&query={urllib.parse.quote(title)}"
    results = (_http_json(url) or {}).get("results") or []
    best = results[0] if results else {}
    _cache_put(conn, key, best)
    conn.commit()
    return best or None


def search_candidates_tv(title: str, api_key: str, lang: str = "en-US") -> list:
    url = (f"{_API}/search/tv?api_key={api_key}"
           f"&query={urllib.parse.quote(title)}&language={lang}")
    return ((_http_json(url) or {}).get("results") or [])[:6]


def _tv_acceptable(best: dict, title: str) -> bool:
    """Guard TV matches. Names are distinctive, so accept exact/substring/long
    common-prefix and reject the rest (better a miss than a wrong pin)."""
    nt = _norm(title)
    nb = _norm(best.get("name") or best.get("original_name"))
    if not nt or not nb:
        return False
    if nt == nb or nt in nb or nb in nt:
        return True
    i = 0
    while i < len(nt) and i < len(nb) and nt[i] == nb[i]:
        i += 1
    return i >= 5


def _download_cover_tv(poster_path: str, work_id: int) -> str | None:
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.COVERS_DIR / f"series_{work_id}.jpg"
    try:
        req = urllib.request.Request(config.TMDB_IMG + poster_path, headers=_UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            dest.write_bytes(r.read())
        return str(dest)
    except Exception:
        return None


def _tv_payload(best: dict, have_json: str | None) -> str:
    """Merge the discovery-time 'have_*' summary with the TMDB payload so the
    'seasons you own' info survives enrichment."""
    base: dict = {}
    if have_json:
        try:
            base = json.loads(have_json)
        except Exception:
            base = {}
    base.update({
        "overview": best.get("overview"),
        "vote_average": best.get("vote_average"),
        "poster_path": best.get("poster_path"),
        "tmdb_seasons": best.get("number_of_seasons"),
    })
    return json.dumps(base)


def apply_candidate_tv(conn: sqlite3.Connection, work_id: int, best: dict,
                       api_key: str) -> None:
    """Apply a user-picked TMDB TV result to a series work (manual pin)."""
    poster = best.get("poster_path")
    cover = _download_cover_tv(poster, work_id) if poster else None
    gnames = ", ".join(_genres_tv(api_key).get(g, "")
                       for g in (best.get("genre_ids") or [])).strip(", ")
    fad = best.get("first_air_date") or ""
    have = conn.execute("SELECT extra_json FROM works WHERE id=?",
                        (work_id,)).fetchone()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE works SET title=?, year=?, genre=?, cover_path=COALESCE(?, cover_path),"
        " identifier=?, provider='tmdb', enriched=1, manual=1, extra_json=?,"
        " updated_at=? WHERE id=?",
        (best.get("name") or best.get("original_name"),
         int(fad[:4]) if fad[:4].isdigit() else None, gnames or None, cover,
         f"tmdbtv:{best['id']}", _tv_payload(best, have[0] if have else None),
         now, work_id))
    conn.commit()


def enrich_series(conn: sqlite3.Connection, api_key: str,
                  limit: int | None = None, sleep: float = 0.05,
                  progress=None) -> dict:
    """Enrich un-enriched series works via the TMDB TV endpoint."""
    rows = conn.execute(
        "SELECT id, title, extra_json FROM works"
        " WHERE type='series' AND enriched=0"
        + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    genres = _genres_tv(api_key)
    matched = missed = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for i, (wid, title, have) in enumerate(rows):
        best = search_tv(conn, title, api_key)
        if best and best.get("id") and _tv_acceptable(best, title):
            poster = best.get("poster_path")
            cover = _download_cover_tv(poster, wid) if poster else None
            gnames = ", ".join(genres.get(g, "")
                               for g in (best.get("genre_ids") or [])).strip(", ")
            fad = best.get("first_air_date") or ""
            conn.execute(
                "UPDATE works SET title=?, year=?, genre=?, cover_path=?,"
                " identifier=?, provider='tmdb', enriched=1, extra_json=?,"
                " updated_at=? WHERE id=?",
                (best.get("name") or title,
                 int(fad[:4]) if fad[:4].isdigit() else None, gnames or None,
                 cover, f"tmdbtv:{best['id']}", _tv_payload(best, have), now, wid))
            matched += 1
        else:
            conn.execute(
                "UPDATE works SET enriched=1, provider='tmdb-miss', updated_at=?"
                " WHERE id=?", (now, wid))
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


def _download_cover(poster_path: str, work_id: int) -> str | None:
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.COVERS_DIR / f"movie_{work_id}.jpg"
    if dest.exists():
        return str(dest)
    try:
        req = urllib.request.Request(config.TMDB_IMG + poster_path, headers=_UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            dest.write_bytes(r.read())
        return str(dest)
    except Exception:
        return None


def enrich_movies(conn: sqlite3.Connection, api_key: str,
                  limit: int | None = None, sleep: float = 0.05,
                  progress=None) -> dict:
    """Enrich un-enriched movie works. Returns {matched, missed, total}."""
    rows = conn.execute(
        "SELECT id, title, year FROM works"
        " WHERE type='movie' AND enriched=0"
        + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()
    genres = _genres(api_key)
    matched = missed = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for i, (wid, title, year) in enumerate(rows):
        best = search_movie(conn, title, year, api_key)
        if best and best.get("id") and _acceptable(best, title, year):
            poster = best.get("poster_path")
            cover = _download_cover(poster, wid) if poster else None
            gnames = ", ".join(
                genres.get(g, "") for g in (best.get("genre_ids") or [])
            ).strip(", ")
            rel = best.get("release_date") or ""
            byear = int(rel[:4]) if rel[:4].isdigit() else year
            conn.execute(
                "UPDATE works SET title=?, year=?, genre=?, cover_path=?,"
                " identifier=?, provider='tmdb', enriched=1, extra_json=?,"
                " updated_at=? WHERE id=?",
                (best.get("title") or title, byear, gnames or None, cover,
                 f"tmdb:{best['id']}", json.dumps({
                     "overview": best.get("overview"),
                     "vote_average": best.get("vote_average"),
                     "poster_path": poster,
                 }), now, wid),
            )
            matched += 1
        else:
            conn.execute(
                "UPDATE works SET enriched=1, provider='tmdb-miss', updated_at=?"
                " WHERE id=?", (now, wid))
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
