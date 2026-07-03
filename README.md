# media-catalog

Catalog your **movies, albums (MP3/CD) and console games** — built on top of
the [drive-xray](../drive-xray) index (no re-scan), enriched with covers and
metadata, browsable as a visual gallery.

Companion to drive-xray: that tool is about *files* (sizes, dupes); this one is
about *titles* (a unified `works` model of `movie | album | game`).

## How it works

1. **Discover** — `discover.py` reads a drive-xray `.db` and classifies index
   entries into games / movies / albums, extracting a raw title + identifier.
   - **Games**: folder- or file-unit under `GAME_ROOTS`, plus a PS3/PS4
     title-id fallback (e.g. `BCES01175-[Uncharted 3]`). Sizes aggregated.
   - **Movies**: one work per *release folder*; a last-year title parser +
     release-tag stripper (`2001 A Space Odyssey 1968 720p` → title=`2001 A
     Space Odyssey`, year=`1968`).
   - **Albums**: one per audio folder; `artist` = the folder above it.
   - Movies/albums are scoped to `MOVIE_ROOTS` / `MUSIC_ROOTS` so loose clips,
     `node_modules` and game internals never masquerade as titles.
2. **Enrich** — `enrich/` clients add covers + metadata, caching every response
   in `enrich_cache` (re-runs never re-hit an API):
   - `tmdb.py` — movies (poster, year, genres, overview). **Live.**
   - IGDB (games) and MusicBrainz (albums) — planned.
3. **Browse** — `app.py` (Streamlit) shows a filterable cover grid with "which
   drive is it on".

## Usage

```bash
python mediacat.py scan            # scan every drive-xray-registered drive
python mediacat.py scan a.db b.db  # or specific drive-xray db files
python mediacat.py summary         # counts by type / platform
streamlit run app.py               # the gallery
```

## Config

Media collection roots live in `discover.py` (`GAME_ROOTS`, `MOVIE_ROOTS`,
`MUSIC_ROOTS`) — a root only matches on drives where the path exists, so one
global list scopes naturally. Adjust to your layout.

API keys go in a **gitignored** `secrets.json` (never committed):

```json
{
  "tmdb_api_key": "…",
  "igdb_client_id": "…",
  "igdb_client_secret": "…"
}
```

- **TMDB** (movies): free key at themoviedb.org → Settings → API.
- **IGDB** (games): a Twitch dev app → Client ID + Secret.
- **MusicBrainz** (albums): no key needed.

Env vars `MEDIACAT_TMDB_API_KEY` etc. override the file.

## Status

Foundation + gallery + TMDB movie enrichment. Games/movies/albums catalogued
from the drive-xray index; IGDB + MusicBrainz enrichment next.
