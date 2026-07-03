"""Runtime config — API keys from a gitignored secrets file (or env vars).

Never hard-code keys; never commit secrets.json. Env vars win over the file so
CI / headless runs can inject them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_SECRETS = Path(__file__).resolve().parent.parent / "secrets.json"

# Local cache of downloaded cover art (gitignored).
COVERS_DIR = Path(__file__).resolve().parent.parent / "covers"

# TMDB image CDN — poster sizes: w92 w154 w185 w342 w500 w780 original
TMDB_IMG = "https://image.tmdb.org/t/p/w342"


def _load() -> dict:
    if _SECRETS.exists():
        try:
            return json.loads(_SECRETS.read_text(encoding="utf-8"))
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
