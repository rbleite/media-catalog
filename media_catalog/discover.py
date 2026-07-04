"""Discover media *works* by reading a drive-xray index (no re-scan).

Classifies index entries into games / movies / albums and extracts a raw title
+ identifier for later enrichment. The rules are intentionally data-driven
(GAME_ROOTS, extension sets, title-id regexes) so new layouts/platforms are a
config change, not a code change.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterator

DX_REGISTRY = Path.home() / ".config" / "drive-xray" / "registry.json"

# --- extensions -----------------------------------------------------------
# NB: no bare "ts" — on a dev machine that's overwhelmingly TypeScript, not
# MPEG transport-stream video. Keep m2ts (unambiguous).
VIDEO_EXT = {
    "mkv", "mp4", "avi", "m4v", "mov", "wmv", "mpg", "mpeg",
    "m2ts", "vob", "divx", "flv", "webm", "mpe",
}
AUDIO_EXT = {
    "mp3", "flac", "wav", "m4a", "aac", "ogg", "wma", "opus",
    "aiff", "ape", "alac", "wv",
}
# subtitle sidecars — detected straight from the index (no file read) to flag
# movies that ship subtitles.
SUB_EXT = {"srt", "sub", "ass", "ssa", "vtt", "idx", "smi"}
# rom / disc-image containers that stand for a whole game
GAME_FILE_EXT = {
    "iso", "nsp", "xci", "nsz", "xcz", "wbfs", "rvz", "wad", "cso", "chd",
    "pkg", "gb", "gba", "gbc", "nds", "3ds", "z64", "n64", "v64", "nes",
    "sfc", "smc", "gcm", "rpx", "wux",
    # ROMs are frequently stored compressed under the console folders
    "zip", "7z", "rar",
}

# --- title-id → platform --------------------------------------------------
# PS3 serials: 4 letters + 5 digits (BCES01175, BLUS31550, NPEB…). PS4: CUSA.
_TITLEID_RE = re.compile(r"^([A-Z]{4})(\d{4,5})")
_PS3_PREFIXES = {"BCES", "BCUS", "BCJS", "BCAS", "BLES", "BLUS", "BLJM",
                 "BLJS", "BLAS", "BLKS", "NPEB", "NPUB", "NPJB", "NPEA",
                 "NPUA", "NPHB", "NPHA", "NPGB", "MRTC"}


def platform_from_titleid(name: str) -> str | None:
    m = _TITLEID_RE.match(name)
    if not m:
        return None
    prefix = m.group(1)
    if prefix == "CUSA":
        return "PS4"
    if prefix in _PS3_PREFIXES:
        return "PS3"
    return None


def titleid_and_name(folder_name: str) -> tuple[str | None, str]:
    """Split 'BCES01175-[Uncharted 3 Drakes Deception]' -> (id, clean name).
    Tolerates the mangled variants seen in the wild (missing bracket etc.)."""
    m = _TITLEID_RE.match(folder_name)
    tid = folder_name[: m.end()] if m else None
    rest = folder_name[m.end():] if m else folder_name
    rest = rest.lstrip(" -_")
    rest = rest.strip("[]").strip()
    return tid, rest


# Switch/3DS title-id: 16 hex in brackets, e.g. [010099C022B96000]
_ROM_ID_RE = re.compile(r"\[([0-9A-Fa-f]{16})\]")
# common site/scene prefixes in ROM filenames
_SITE_RE = re.compile(r"^\s*\[[^\]]*(?:\.com|nsw|romsns|ziperto)[^\]]*\]\s*", re.I)


def clean_rom_name(filename: str) -> tuple[str, str | None]:
    """ROM filename -> (clean title, title-id or None). Handles the
    'Name [0100…][v0][US](site).nsp' scene layout: title is the text before the
    first [ or (, the 16-hex id is extracted, version/region/site tags dropped."""
    stem = filename.rsplit(".", 1)[0]
    stem = _SITE_RE.sub("", stem)                    # drop a leading [site] tag
    mid = _ROM_ID_RE.search(stem)
    tid = mid.group(1).upper() if mid else None
    title = re.split(r"[\[\(]", stem)[0]             # cut at first [ or (
    title = re.sub(r"\s*-?\s*v\d+(?:\.\d+)*\s*$", "", title)  # trailing version
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -")
    return (title or stem), tid


# --- games layout ---------------------------------------------------------
# (path prefix, platform, unit): 'folder' = each direct sub-dir is one game;
# 'file' = each direct file is one game. Matches the 8Tb layout; extend freely.
GAME_ROOTS: list[tuple[str, str, str]] = [
    ("M2/FuncionaisISO", "PS3", "folder"),
    ("PS4", "PS4", "folder"),
    ("Consolas/ROMS/Switch", "Switch", "file"),
    ("Switch", "Switch", "file"),          # 6Tb top-level Switch collection
    ("M2/Switch", "Switch", "file"),       # 8Tb
    ("Users/rleit/Desktop/Switch", "Switch", "file"),  # Criativo (Windows)
    ("Consolas/ROMS/Wii", "Wii", "file"),
    ("Consolas/ROMS/WiiU", "WiiU", "file"),
    ("Consolas/ROMS/PS2", "PS2", "file"),
    ("Consolas/ROMS/PS1", "PS1", "file"),
    ("Consolas/ROMS/GameCube", "GameCube", "file"),
    ("Consolas/ROMS/3DS", "3DS", "file"),
    ("Consolas/ROMS/DS", "DS", "file"),
]

# Movies and music are scoped to collection roots — a video/audio file loose
# in Downloads, node_modules or a game folder is NOT a catalogued title.
# These match the 6Tb layout (discovered from the drive-xray index); roots only
# match on drives where the path exists, so a global list scopes naturally.
MOVIE_ROOTS: list[str] = [
    "Ricardo/HD Movies",
    "Ricardo/Divx",
    "Ricardo/Disney.Home.Collection.PORTUGUESE.DVDRip.Fox-dh",
    "Air2/Movies",
    # NB: the nested drive backups (Ricardo/2Tb, Passport1G, WD500) are
    # deliberately NOT movie roots — they're full-drive backups full of home
    # videos / work files, which polluted the movie catalog (19% TMDB match vs
    # 59% on the curated folders). They ARE kept in MUSIC_ROOTS below, where the
    # MP3/Artist/Album structure is clean.
]
MUSIC_ROOTS: list[str] = [
    "MP3",
    "Ricardo/2Tb",
    "Ricardo/Passport1G",
    "Ricardo/WD500",
]

_SKIP_NAMES = {".ds_store", "._.ds_store"}


def _under_prefixes(rel: str, prefixes: list[str]) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


def _ext(name: str) -> str:
    d = name.rfind(".")
    return name[d + 1:].lower() if d >= 0 else ""


def _basename(rel: str) -> str:
    s = rel.rfind("/")
    return rel[s + 1:] if s >= 0 else rel


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# release / quality / source tags — everything from the first one on is junk
_TAG_RE = re.compile(
    r"\b(?:1080p|720p|480p|2160p|4k|uhd|bluray|blu-ray|brrip|bdrip|dvdrip|"
    r"dvdscr|hdrip|webrip|web-dl|webdl|hdtv|hdts|x264|x265|h264|h265|hevc|"
    r"xvid|divx|ac3|aac|dts|cam|ts|r5|line|remux|proper|repack|internal|"
    r"limited|unrated|extended|remastered|multi|dual|dublado|legendado)\b",
    re.I,
)


def _clean_movie_title(name: str) -> tuple[str, int | None]:
    """Parse a release name into (title, year). Uses the LAST plausible year
    so a title containing a number ('2001 A Space Odyssey 1968 720p') keeps the
    number and picks 1968. Everything from the first release/quality tag on is
    dropped. Good enough for a TMDB search to disambiguate."""
    stem = name
    for ext in (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    spaced = re.sub(r"[._]+", " ", stem)
    years = list(_YEAR_RE.finditer(spaced))
    if years:
        last = years[-1]
        year = int(last.group(0))
        title = spaced[: last.start()]
    else:
        year, title = None, spaced
        tag = _TAG_RE.search(title)      # no year → still cut at first tag
        if tag:
            title = title[: tag.start()]
    title = re.sub(r"\s+", " ", title).strip(" -[]()")
    return (title or spaced), year


def scan_index(db_path: Path, label: str,
               movie_roots: list[str] | None = None,
               music_roots: list[str] | None = None) -> Iterator[dict]:
    """Yield work dicts discovered in one drive-xray db (latest snapshot).

    Games are found by GAME_ROOTS + title-id patterns (folder- or file-unit).
    Movies/albums are only catalogued under `movie_roots`/`music_roots`
    (default = module MOVIE_ROOTS/MUSIC_ROOTS) so scattered clips, camera
    footage and node_modules never masquerade as titles."""
    movie_roots = MOVIE_ROOTS if movie_roots is None else movie_roots
    music_roots = MUSIC_ROOTS if music_roots is None else music_roots

    conn = sqlite3.connect(db_path)
    try:
        sid = conn.execute("SELECT MAX(id) FROM snapshots").fetchone()[0]
        if sid is None:
            return
        rows = conn.execute(
            "SELECT rel_path, is_dir, size, mtime FROM entries"
            " WHERE snapshot_id=? AND error IS NULL", (sid,),
        ).fetchall()
    finally:
        conn.close()

    # Windows-indexed drives use backslash separators — normalise to '/' so the
    # root/prefix and basename logic (all '/'-based) works uniformly.
    rows = [(rel.replace("\\", "/"), is_dir, size, mtime)
            for rel, is_dir, size, mtime in rows]

    game_roots = sorted(GAME_ROOTS, key=lambda r: -len(r[0]))  # longest first

    def under_game_root(rel: str):
        for prefix, platform, unit in game_roots:
            if rel == prefix or rel.startswith(prefix + "/"):
                return prefix, platform, unit
        return None

    # --- pass 1: identify games ---------------------------------------------
    folder_games: dict[str, dict] = {}   # rel_path -> work (size filled below)
    file_games: list[dict] = []
    for rel, is_dir, size, mtime in rows:
        base = _basename(rel)
        if base.lower() in _SKIP_NAMES or base.startswith("._"):
            continue  # macOS AppleDouble sidecars (._foo) are not titles
        hit = under_game_root(rel)
        if hit:
            prefix, platform, unit = hit
            # direct child of the root: non-empty sub-path with no further "/".
            # (excludes the root folder itself and anything deeper.)
            sub = rel[len(prefix):].strip("/")
            is_direct = bool(sub) and "/" not in sub
            if unit == "folder" and is_dir and is_direct:
                tid, name = titleid_and_name(base)
                folder_games[rel] = {
                    "type": "game", "platform": platform,
                    "title": name or base, "title_raw": base,
                    "identifier": tid, "rel_path": rel,
                    "drive_label": label, "size_bytes": 0,
                    "mtime": mtime or 0.0,
                }
            elif unit == "file" and not is_dir and is_direct:
                if _ext(base) not in GAME_FILE_EXT:
                    continue  # skip .rar/.zip/sigpatches/etc under a rom root
                title, tid = clean_rom_name(base)
                file_games.append({
                    "type": "game", "platform": platform,
                    "title": title, "title_raw": base,
                    "identifier": tid, "rel_path": rel,
                    "drive_label": label, "size_bytes": size,
                    "mtime": mtime or 0.0,
                })
            continue
        if is_dir:  # title-id folder anywhere (fallback outside known roots)
            plat = platform_from_titleid(base)
            if plat:
                tid, name = titleid_and_name(base)
                folder_games[rel] = {
                    "type": "game", "platform": plat,
                    "title": name or base, "title_raw": base,
                    "identifier": tid, "rel_path": rel,
                    "drive_label": label, "size_bytes": 0,
                    "mtime": mtime or 0.0,
                }

    # --- aggregate descendant sizes for folder-unit games (games don't nest,
    #     so each file belongs to at most one game folder) --------------------
    if folder_games:
        for rel, is_dir, size, mtime in rows:
            if is_dir or not size:
                continue
            parts = rel.split("/")
            for k in range(len(parts) - 1, 0, -1):
                anc = "/".join(parts[:k])
                if anc in folder_games:
                    folder_games[anc]["size_bytes"] += size
                    if (mtime or 0) > (folder_games[anc].get("mtime") or 0):
                        folder_games[anc]["mtime"] = mtime
                    break

    yield from folder_games.values()
    yield from file_games

    # --- pass 2: movies / albums, scoped to collection roots ----------------
    if not (movie_roots or music_roots):
        return

    def _parent(rel: str) -> str:
        s = rel.rfind("/")
        return rel[:s] if s >= 0 else "."

    # Movies: one work per *release folder* (the video's parent). A video
    # sitting directly in a root becomes a per-file movie. Grouping by folder
    # collapses CD1/CD2/sample files and uses the tidy release-folder name, and
    # works at any depth inside the nested drive backups.
    movie_folders: dict[str, dict] = {}   # folder -> work
    movie_folder_size: dict[str, int] = {}
    movie_folder_mtime: dict[str, float] = {}
    movie_dir_has_sub: set = set()        # folders holding a subtitle sidecar
    # Albums: one work per folder that directly contains audio; artist = the
    # folder above it (Artist/Album/tracks), when that is not the root.
    album_folders: dict[str, dict] = {}
    album_folder_size: dict[str, int] = {}
    album_folder_mtime: dict[str, float] = {}

    for rel, is_dir, size, mtime in rows:
        if is_dir:
            continue
        ext = _ext(_basename(rel))
        if ext in SUB_EXT and _under_prefixes(rel, movie_roots):
            movie_dir_has_sub.add(_parent(rel))
            continue
        if ext in VIDEO_EXT and _under_prefixes(rel, movie_roots):
            folder = _parent(rel)
            if _under_prefixes(folder, movie_roots) and folder not in (movie_roots):
                key, name = folder, _basename(folder)
            else:                       # video directly in a root
                key, name = rel, _basename(rel)
            movie_folder_size[key] = movie_folder_size.get(key, 0) + (size or 0)
            if (mtime or 0) > movie_folder_mtime.get(key, 0):
                movie_folder_mtime[key] = mtime or 0.0
            if key not in movie_folders:
                title, year = _clean_movie_title(name)
                movie_folders[key] = {
                    "type": "movie", "platform": None, "title": title,
                    "title_raw": name, "year": year, "identifier": None,
                    "rel_path": key, "drive_label": label,
                }
        elif ext in AUDIO_EXT and _under_prefixes(rel, music_roots):
            folder = _parent(rel)
            album_folder_size[folder] = album_folder_size.get(folder, 0) + (size or 0)
            if (mtime or 0) > album_folder_mtime.get(folder, 0):
                album_folder_mtime[folder] = mtime or 0.0
            if folder not in album_folders:
                # artist = the folder above the album, unless that IS a root
                # (Artist/Album/tracks -> artist; Root/Album/tracks -> none)
                parent = _parent(folder)
                artist = (_basename(parent)
                          if parent not in music_roots and parent != "."
                          else None)
                album_folders[folder] = {
                    "type": "album", "platform": None,
                    "title": _basename(folder), "title_raw": _basename(folder),
                    "artist": artist, "identifier": None,
                    "rel_path": folder, "drive_label": label,
                }

    for key, w in movie_folders.items():
        w["size_bytes"] = movie_folder_size.get(key)
        w["mtime"] = movie_folder_mtime.get(key, 0.0)
        # a subtitle sitting in the release folder (or, for a root-direct movie,
        # in the same dir) marks the film as subtitled — read straight from the
        # index, no file access.
        w["has_subtitles"] = int(key in movie_dir_has_sub
                                 or _parent(key) in movie_dir_has_sub)
        yield w
    for folder, w in album_folders.items():
        w["size_bytes"] = album_folder_size.get(folder)
        w["mtime"] = album_folder_mtime.get(folder, 0.0)
        yield w


def drive_roots() -> dict:
    """Map drive_label -> absolute root_path for every registered drive-xray db
    whose root is mounted *right now*. Used by the opportunistic ID3 pass to
    reach the real files (media-catalog is otherwise index-only)."""
    out: dict = {}
    if not DX_REGISTRY.exists():
        return out
    try:
        data = json.loads(DX_REGISTRY.read_text(encoding="utf-8"))
    except Exception:
        return out
    for key, meta in data.get("drives", {}).items():
        db = Path(meta.get("db", key))
        if not db.exists():
            continue
        label = meta.get("label", db.stem)
        try:
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT root_path FROM drive LIMIT 1").fetchone()
            conn.close()
        except Exception:
            continue
        if row and row[0] and os.path.isdir(row[0]):
            out[label] = row[0]
    return out
