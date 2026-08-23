"""One file per distinct cover image, shared by every work that uses it.

Every provider wrote its cover as ``<type>_<work_id>.jpg`` — keyed by the row
id. But ``works`` carries ``UNIQUE(drive_label, rel_path)``, so the same album
sitting on three drives is three rows. That meant three downloads and three
files holding byte-identical images, in a folder that is normally cloud-synced
(``COVERS_DIR`` lives under the shared data dir, which defaults to drive-xray's
synced ``db_dir``). Enriching a large collection produced hundreds of separate
uploads, many of them the same picture.

Content addressing fixes both halves at once:

* the file name IS the hash of its bytes, so an image already in the store is
  never written a second time, however many works point at it;
* ``cover_path`` becomes a name relative to the store instead of an absolute
  path from whichever machine happened to run the enrichment — which is why
  covers went missing when the catalogue was opened on the other computer.

The download is deduplicated separately, by provider identifier: three rows
for the same album share one ``mbid:``, so the second and third can adopt the
first one's cover without touching the network at all.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import config

# Everything content-addressed lives in one subdirectory, so the legacy
# `<type>_<id>.jpg` files can sit beside it untouched during migration and
# afterwards as a fallback for anything not yet converted.
CONTENT_SUBDIR = "by-content"

# Below this, it is not an image — a 404 body, an empty response, a provider
# returning a 1x1 placeholder. The old code used the same threshold.
MIN_BYTES = 500


def _sniff_ext(data: bytes) -> str | None:
    """Identify the format from the bytes themselves.

    Deliberately not from the URL: providers serve images from paths ending in
    .jpg that are really PNG, and an error page delivered with a 200 has a
    .jpg URL too. The magic number is the only thing that cannot lie.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def store(data: bytes) -> str | None:
    """Put image bytes into the shared store.

    Returns the name to record in ``works.cover_path`` — relative, of the form
    ``by-content/<sha256>.<ext>`` — or None when the bytes are not a usable
    image. Storing the same image again is a no-op that returns the same name.
    """
    if not data or len(data) < MIN_BYTES:
        return None
    ext = _sniff_ext(data)
    if ext is None:
        return None

    digest = hashlib.sha256(data).hexdigest()
    name = f"{CONTENT_SUBDIR}/{digest}.{ext}"
    dest = config.COVERS_DIR / CONTENT_SUBDIR / f"{digest}.{ext}"
    if dest.exists():
        return name

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write beside it and rename. In a synced folder a sync client watching the
    # directory would otherwise upload a half-written image, and the name
    # promises the hash of complete contents.
    tmp = dest.with_name(dest.name + ".part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except OSError:
        tmp.unlink(missing_ok=True)
        return None
    return name


def resolve(name: str | None) -> Path | None:
    """Turn a stored name into a real path on this machine, or None.

    Accepts the legacy forms too — an absolute path written by another machine,
    or a bare ``album_12.jpg`` — because a catalogue mid-migration holds all
    three at once.
    """
    if not name:
        return None
    raw = str(name).replace("\\", "/")

    candidates: list[Path] = []
    if raw.startswith(f"{CONTENT_SUBDIR}/"):
        candidates.append(config.COVERS_DIR / raw)
    else:
        p = Path(raw)
        if p.is_absolute():
            candidates.append(p)                      # same machine as enrichment
        candidates.append(config.COVERS_DIR / raw)    # relative to this store
        candidates.append(config.COVERS_DIR / p.name) # bare filename fallback

    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def reuse_for_identifier(conn, identifier: str | None) -> str | None:
    """The cover another row already holds for this exact provider identifier.

    This is the network half of the deduplication. The same album on three
    drives is three rows sharing one ``mbid:``; without this each one fetches
    the same image again, and rate-limited providers make that slow as well as
    wasteful.
    """
    if not identifier:
        return None
    row = conn.execute(
        "SELECT cover_path FROM works"
        " WHERE identifier = ? AND cover_path IS NOT NULL AND cover_path <> ''"
        " LIMIT 1",
        (identifier,),
    ).fetchone()
    if not row or not row[0]:
        return None
    # Only offer it if the file is actually here; a stale absolute path from
    # another machine would otherwise suppress the download and leave the work
    # with no cover at all.
    return row[0] if resolve(row[0]) else None


_SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cover_sources (
    source TEXT PRIMARY KEY,   -- provider-side id: mbid, poster path, image id
    name   TEXT NOT NULL       -- the by-content/<sha>.<ext> it resolved to
);
"""


def store_from_source(conn, source: str, download) -> str | None:
    """Fetch a cover, but only the first time anyone asks for this source.

    `source` is whatever identifies the image at the provider — a release-group
    id, a TMDB poster path, an IGDB image id. Three works for the same album
    carry the same one, so the second and third adopt the first's file without
    touching the network. That matters beyond bandwidth: MusicBrainz allows
    roughly one request a second, so re-fetching an image already on disk is
    the slowest thing in an enrichment run.

    `download` is called only on a miss and must return bytes or None.
    """
    if not source:
        data = download()
        return store(data) if data else None

    conn.execute(_SOURCES_SCHEMA)
    row = conn.execute("SELECT name FROM cover_sources WHERE source = ?",
                       (source,)).fetchone()
    # Re-check the file exists: the row is worthless if the store was pruned
    # or the catalogue came from another machine that had different covers.
    if row and row[0] and resolve(row[0]):
        return row[0]

    data = download()
    name = store(data) if data else None
    if name:
        conn.execute(
            "INSERT OR REPLACE INTO cover_sources (source, name) VALUES (?, ?)",
            (source, name))
        conn.commit()
    return name


def migrate_legacy(conn) -> dict:
    """Fold existing ``<type>_<id>.<ext>`` covers into the content store.

    Identical images collapse into one file. Returns counts; safe to run more
    than once, and it copies rather than deletes so an interrupted run cannot
    lose a cover that no row points at yet.
    """
    stats = {"scanned": 0, "stored": 0, "rows_updated": 0, "freed_files": 0}
    covers_dir = config.COVERS_DIR
    if not covers_dir.is_dir():
        return stats

    by_old_name: dict[str, str] = {}
    for f in sorted(covers_dir.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        stats["scanned"] += 1
        try:
            new_name = store(f.read_bytes())
        except OSError:
            continue
        if not new_name:
            continue
        by_old_name[f.name] = new_name
        stats["stored"] += 1

    if not by_old_name:
        return stats

    rows = conn.execute(
        "SELECT id, cover_path FROM works"
        " WHERE cover_path IS NOT NULL AND cover_path <> ''").fetchall()
    for wid, old in rows:
        base = str(old).replace("\\", "/").rsplit("/", 1)[-1]
        new_name = by_old_name.get(base)
        if new_name and new_name != old:
            conn.execute("UPDATE works SET cover_path=? WHERE id=?",
                         (new_name, wid))
            stats["rows_updated"] += 1
    conn.commit()

    # how many duplicate files the store collapsed
    stats["freed_files"] = stats["stored"] - len(set(by_old_name.values()))
    return stats
