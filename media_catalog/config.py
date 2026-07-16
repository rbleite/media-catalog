"""Runtime config — API keys from a gitignored secrets file (or env vars),
and the central data dir (catalog.db + covers) that can live in a synced
folder (OneDrive / Google Drive / Dropbox) so every machine sees the same
catalogue — mirroring drive-xray's multi-machine strategy.

Never hard-code keys; never commit secrets.json. Env vars win over the file so
CI / headless runs can inject them.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent

# TMDB image CDN — poster sizes: w92 w154 w185 w342 w500 w780 original
TMDB_IMG = "https://image.tmdb.org/t/p/w342"

# ── central data dir (catalog.db + covers/) ─────────────────────────────────
# Resolution order:
#   1. $MEDIACAT_DATA_DIR (explicit override)
#   2. "data_dir" in ~/.config/media-catalog/config.json (set via the UI)
#   3. "<drive-xray db_dir>/media-catalog" when drive-xray has a configured
#      db_dir — the same OneDrive/GDrive/Dropbox folder that already syncs
#      the drive indexes between machines, so the catalogue follows for free
#   4. legacy in-repo locations (catalog.db per mediacat default, <repo>/covers)

CONFIG_PATH = Path.home() / ".config" / "media-catalog" / "config.json"
_DX_CONFIG = Path.home() / ".config" / "drive-xray" / "config.json"


def read_app_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def write_app_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")


def _dx_db_dir() -> Path | None:
    """drive-xray's configured .db folder (None when not configured)."""
    try:
        raw = json.loads(_DX_CONFIG.read_text(encoding="utf-8")).get("db_dir")
        return Path(raw).expanduser() if raw else None
    except Exception:
        return None


def data_dir() -> Path | None:
    """The shared data dir, or None to keep the legacy in-repo layout."""
    env = os.environ.get("MEDIACAT_DATA_DIR")
    if env:
        return Path(env).expanduser()
    raw = read_app_config().get("data_dir")
    if raw:
        return Path(raw).expanduser()
    dx = _dx_db_dir()
    if dx and dx.is_dir():
        return dx / "media-catalog"
    return None


_DATA_DIR = data_dir()

_LEGACY_CATALOG = Path.home() / "tools" / "media-catalog" / "catalog.db"
_LEGACY_COVERS = _REPO_DIR / "covers"

CATALOG_DB = (_DATA_DIR / "catalog.db") if _DATA_DIR else _LEGACY_CATALOG
COVERS_DIR = (_DATA_DIR / "covers") if _DATA_DIR else _LEGACY_COVERS


def ensure_data_dir() -> str | None:
    """Create the shared data dir and migrate the legacy catalog/covers into
    it once (copy, never delete — the legacy files stay as a backup).
    Returns a short human message when a migration happened, else None."""
    if _DATA_DIR is None:
        return None
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    moved = []
    if not CATALOG_DB.exists() and _LEGACY_CATALOG.exists():
        shutil.copy2(_LEGACY_CATALOG, CATALOG_DB)
        moved.append(f"catalog.db (de {_LEGACY_CATALOG})")
    if _LEGACY_COVERS.is_dir() and _LEGACY_COVERS != COVERS_DIR:
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in _LEGACY_COVERS.iterdir():
            dest = COVERS_DIR / f.name
            if f.is_file() and not dest.exists():
                shutil.copy2(f, dest)
                n += 1
        if n:
            moved.append(f"{n} capas")
    if moved:
        return f"migrado para {_DATA_DIR}: " + ", ".join(moved)
    return None


def resolve_cover(p: str | None) -> str | None:
    """cover_path values are absolute paths from whichever machine enriched
    the title (e.g. /Users/... on the Mac). On another machine, resolve by
    filename inside this machine's COVERS_DIR. Returns a usable path or None."""
    if not p:
        return None
    q = Path(p)
    try:
        if q.is_file():
            return str(q)
    except OSError:
        pass
    name = p.replace("\\", "/").rsplit("/", 1)[-1]
    alt = COVERS_DIR / name
    try:
        return str(alt) if alt.is_file() else None
    except OSError:
        return None


# ── API keys ────────────────────────────────────────────────────────────────
# secrets.json is looked up in the repo first (historical location), then in
# the shared data dir — put it there and every machine gets the API keys too.
_SECRETS_PATHS = [_REPO_DIR / "secrets.json"]
if _DATA_DIR is not None:
    _SECRETS_PATHS.append(_DATA_DIR / "secrets.json")


def _load() -> dict:
    for path in _SECRETS_PATHS:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


_cache = _load()


def get(key: str) -> str | None:
    """API key by name, env var first (MEDIACAT_<KEY>), then secrets.json."""
    env = os.environ.get("MEDIACAT_" + key.upper())
    if env:
        return env
    val = _cache.get(key)
    return val or None


def has_tmdb() -> bool:
    return bool(get("tmdb_api_key"))


def has_igdb() -> bool:
    return bool(get("igdb_client_id") and get("igdb_client_secret"))
