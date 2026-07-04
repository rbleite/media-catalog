"""NFO sidecar matcher — pull the IMDb id out of Kodi/Plex `.nfo` files and use
it for an *exact* TMDB match, killing fuzzy-title misses.

Like the ID3 pass, this touches real files, so it is opportunistic: it only runs
on movies whose drive is mounted now. A `.nfo` is tiny and read locally; the only
network call is TMDB's find-by-IMDb (cached in `enrich_cache`).

.nfo formats handled:
  - Kodi XML: <uniqueid type="imdb">tt1234567</uniqueid>, <imdbid>tt…</imdbid>,
    <id>tt…</id>
  - plain-text .nfo with an imdb.com/title/tt… URL
  - any bare tt\\d+ token as a last resort
"""
from __future__ import annotations

import re
from pathlib import Path

_IMDB_RE = re.compile(rb"tt\d{7,9}")
_NFO_EXT = ".nfo"


def read_imdb_id(path: Path, max_bytes: int = 1 << 18) -> str | None:
    """Return the first IMDb id (tt…) found in a .nfo, or None."""
    try:
        with open(path, "rb") as f:
            blob = f.read(max_bytes)
    except Exception:
        return None
    # prefer an explicit imdb tag/url over a stray tt token
    for pat in (rb'type="imdb"[^>]*>\s*(tt\d{7,9})',
                rb'<imdbid>\s*(tt\d{7,9})',
                rb'imdb\.com/title/(tt\d{7,9})'):
        m = re.search(pat, blob, re.IGNORECASE)
        if m:
            return m.group(1).decode("ascii")
    m = _IMDB_RE.search(blob)
    return m.group(0).decode("ascii") if m else None


def _find_nfo(folder: Path, stem: str | None = None) -> Path | None:
    """A .nfo in the folder — preferring one matching the video's stem, then the
    conventional movie.nfo, then any .nfo. Recurses one level for CD1/CD2."""
    try:
        entries = sorted(folder.iterdir())
    except Exception:
        return None
    nfos = [p for p in entries if p.is_file()
            and p.suffix.lower() == _NFO_EXT and not p.name.startswith("._")]
    if stem:
        for p in nfos:
            if p.stem.lower() == stem.lower():
                return p
    for p in nfos:
        if p.stem.lower() == "movie":
            return p
    if nfos:
        return nfos[0]
    for p in entries:
        if p.is_dir():
            hit = _find_nfo(p)
            if hit:
                return hit
    return None


def enrich_nfo(conn, roots_by_label: dict, api_key: str, force: bool = False,
               progress=None) -> dict:
    """For each movie whose drive is mounted, read the IMDb id from a .nfo sidecar
    and pin an exact TMDB match. Skips manual=1 rows unless force."""
    from media_catalog.enrich import tmdb
    rows = conn.execute(
        "SELECT id, rel_path, drive_label, title, identifier, COALESCE(manual,0)"
        " FROM works WHERE type='movie'").fetchall()
    matched = no_id = skipped_offline = 0
    total = len(rows)
    for i, (wid, rel, label, title, ident, manual) in enumerate(rows):
        root = roots_by_label.get(label)
        if not root:
            skipped_offline += 1
            continue
        if manual and not force:
            continue
        p = Path(root) / rel.replace("\\", "/")
        if p.is_dir():
            folder, stem = p, None
        elif p.parent.is_dir():
            folder, stem = p.parent, p.stem     # root-direct movie: nfo is a sibling
        else:
            skipped_offline += 1
            continue
        nfo = _find_nfo(folder, stem)
        if not nfo:
            continue
        imdb = read_imdb_id(nfo)
        if not imdb:
            no_id += 1
            continue
        best = tmdb.find_by_imdb(imdb, api_key)
        if best:
            tmdb.apply_candidate(conn, wid, best, api_key)
            matched += 1
        if i % 25 == 0:
            conn.commit()
            if progress:
                progress(i + 1, total, matched, no_id)
    conn.commit()
    if progress:
        progress(total, total, matched, no_id)
    return {"matched": matched, "no_id": no_id,
            "skipped_offline": skipped_offline, "total": total}
