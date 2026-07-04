# media-catalog

**A visual gallery of your movies, albums (MP3/CD) and console games** — built
on top of the [drive-xray](../drive-xray) index (no re-scan), enriched with
covers and metadata from TMDB / IGDB / MusicBrainz / Deezer.

Companion to drive-xray: that tool is about *files* (sizes, dupes); this one is
about *titles* — a unified `works` model of `movie | album | game`, browsable,
filterable, and telling you **which drive each title lives on**.

> ℹ️ **This is a personal companion, not a standalone consumer app.** It reads
> the `.db` files that drive-xray produces for *your* drives, and the media
> roots in `discover.py` point at *your* collection layout. Someone else can
> install and run it, but they must point it at their own drive-xray indexes
> and edit the roots (see [Config](#config)) before the catalogue fills up.

## What it does

- 🎬🎮💿 **Unified catalogue** of movies, games (PS3/PS4/Switch/Wii/…), and
  albums, classified straight from the drive-xray index.
- 🖼️ **Cover gallery** with hover tooltips, filters (type, platform, genre,
  drive, year, search) and dedup across drives (`×N` copies collapse to one).
- ✨ **Metadata enrichment** — covers, year, genre, synopsis:
  - **TMDB** — movies (poster, year, genres, overview, IMDb link).
  - **IGDB** — games (cover, genre), via a Twitch dev app.
  - **MusicBrainz + Cover Art Archive** — albums; **Deezer** fallback for
    covers *and* real musical genres (Rock/Pop/…).
  - Every API response is cached in `enrich_cache` — re-runs never re-hit an API.
- 🎯 **Watched / played / wishlist status** per title, with a sidebar filter.
- ✏️ **Manual correction** — search the right title/cover and pin it (won't be
  auto-overwritten). Fix by IMDb id too.
- 🎮 **PS3/PS4 title-id resolution** — bare serials (`BCES01175`) resolved to
  real names via an offline GameTDB map.
- ⬇️ **Export inventory** to CSV (Excel-ready).
- 🔄 **Self-update** from GitHub, right in the sidebar.

## Quick install

### macOS / Linux

```bash
git clone https://github.com/rbleite/media-catalog.git
cd media-catalog
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py --server.port 8503
```

### Windows

Requirements: [Python 3.10+](https://www.python.org/downloads/) — during
install, tick **"Add Python to PATH"**.

```bat
git clone https://github.com/rbleite/media-catalog.git
cd media-catalog
run.bat
```

`run.bat` creates a virtual environment, installs dependencies, and launches
the UI at http://localhost:8503 — all in one step. Double-click it on
subsequent runs.

The only dependency is `streamlit`; everything else is Python's standard
library.

## How it works

1. **Discover** — `discover.py` reads a drive-xray `.db` and classifies index
   entries into games / movies / albums, extracting a raw title + identifier.
   - **Games**: folder- or file-unit under `GAME_ROOTS`, plus PS3/PS4
     title-id resolution. Sizes aggregated across descendants.
   - **Movies**: one work per *release folder*; a last-year title parser +
     release-tag stripper (`2001 A Space Odyssey 1968 720p` → title=`2001 A
     Space Odyssey`, year=`1968`).
   - **Albums**: one per audio folder; `artist` = the folder above it.
   - Movies/albums are scoped to `MOVIE_ROOTS` / `MUSIC_ROOTS` so loose clips,
     `node_modules` and game internals never masquerade as titles.
   - Windows backslash paths are normalised, and `._` AppleDouble junk is skipped.
2. **Enrich** — `enrich/` clients add covers + metadata, caching every response.
3. **Browse** — `app.py` (Streamlit) shows the filterable cover gallery.

## Usage

```bash
python mediacat.py scan            # scan every drive-xray-registered drive
python mediacat.py scan a.db b.db  # or specific drive-xray db files
python mediacat.py summary         # counts by type / platform
streamlit run app.py --server.port 8503   # the gallery
```

## Config

Media collection roots live in `discover.py` (`GAME_ROOTS`, `MOVIE_ROOTS`,
`MUSIC_ROOTS`) — a root only matches on drives where the path exists, so one
global list scopes naturally. **Adjust these to your own layout.**

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
- **MusicBrainz / Deezer** (albums): no key needed.

Env vars `MEDIACAT_TMDB_API_KEY` etc. override the file.
