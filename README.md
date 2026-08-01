# media-catalog

**A visual gallery of your movies, TV series, albums (MP3/CD) and console
games** — built on top of the [drive-xray](../drive-xray) index (no re-scan),
enriched with covers and metadata from TMDB / IGDB / MusicBrainz / Deezer.

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

Or build a **clickable `.app` launcher** (recommended for daily use):

```bash
bash build_app.sh
open ~/Applications/media-catalog.app
```

Double-click it in Finder (or drag it to the Dock) to launch the gallery on
port 8503 — it auto-bumps to a free port if 8503 is busy, and opens your
browser. Logs go to `~/Library/Logs/media-catalog.log`. The launcher finds
streamlit in a project `.venv` if present, otherwise on your `PATH`.

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
   - **TV series**: one work per *show* (not per episode), under `SERIES_ROOTS`
     (`Series/`, `TV/`…). Parses `Show.S01E02`, `Show Season 3` packs, and the
     compact `show.401`/`show.2301` forms, grouping episodes/seasons; TMDB TV
     enrichment attaches poster/overview and the total season count.
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

#### Getting the keys

- **TMDB** — movies and series. Free.
  1. Create an account at <https://www.themoviedb.org/signup>.
  2. Settings → **API** → *Request an API key* → choose **Developer**.
  3. It asks for an application name and URL; a personal project with any
     placeholder URL is accepted.
  4. Copy **API Key (v3 auth)** — the 32-character hex string — into
     `tmdb_api_key`.

- **IGDB** — games. Free, but it authenticates through Twitch.
  1. Create a Twitch account and enable two-factor auth (required before you
     can register an app) at <https://dev.twitch.tv/console>.
  2. **Applications** → *Register Your Application*. OAuth Redirect URL
     `http://localhost`, Category *Application Integration*.
  3. Copy the **Client ID**, then *New Secret* for the **Client Secret**, into
     `igdb_client_id` / `igdb_client_secret`.
  4. media-catalog exchanges these for a short-lived token itself — there is
     nothing else to renew.

- **MusicBrainz / Deezer / iTunes** — albums. **No key needed**, nothing to
  set up.

Enrichment degrades gracefully: a provider with no key is skipped, and
everything else still runs.

Env vars `MEDIACAT_TMDB_API_KEY` etc. override the file.

## Multi-machine sync (OneDrive / Google Drive / Dropbox)

The catalogue (`catalog.db` + downloaded covers) can live in a synced folder
so **every machine sees the same titles, covers and watched/played status** —
the same strategy drive-xray uses for its indexes. The data dir is resolved
in this order:

1. `$MEDIACAT_DATA_DIR` env var;
2. `data_dir` in `~/.config/media-catalog/config.json` — set it in the app
   under **⚙️ Sincronização entre máquinas**;
3. **inherited from drive-xray**: when drive-xray has a configured `.db`
   folder (Settings → db folder pointing at OneDrive/GDrive/Dropbox), the
   catalogue automatically lives in `<that folder>/media-catalog/` — zero
   extra setup;
4. otherwise the legacy local paths (as before).

On the first run with a shared dir, the existing local catalogue and covers
are **copied** into it (originals kept). Cover paths recorded on another
machine are resolved by filename inside the shared `covers/` folder, so art
shows up everywhere. Drop your `secrets.json` into the shared dir and the
API keys follow you too.

Typical setup: configure the OneDrive folder once in **drive-xray** (its
Settings already sync the drive indexes), scan/enrich on whichever machine,
and open the gallery anywhere.

To check what a given machine actually resolved — and why — run
`python3 scripts/diagnose_sync.py` on it. See **Troubleshooting** below when
covers do not follow.

## Troubleshooting

### The titles are there but the covers are missing on another machine

Almost always the second machine is falling back to the **legacy layout**,
where the catalogue and the covers live in two *different* places:

| | legacy layout | shared data dir |
|---|---|---|
| `catalog.db` | `~/tools/media-catalog/` | `<data dir>/catalog.db` |
| covers | `<repo>/covers/` | `<data dir>/covers/` |

`<repo>/covers/` is gitignored, so a fresh clone starts empty. If you copied
`catalog.db` across by hand but not the covers, you get exactly this: every
title, no art.

Run this on **both** machines and compare the output:

```bash
python3 scripts/diagnose_sync.py
```

It prints which of the four sources the data dir came from, where the
catalogue and covers actually are, how many covers each machine has, and how
many the catalogue expects but cannot find. `-> in use : NONE` on either
machine is the problem — that machine is not sharing anything.

The fix is to give it a shared dir, easiest by pointing **drive-xray**'s `.db`
folder at OneDrive/Drive/Dropbox (media-catalog then inherits it with no extra
setup), or by setting `data_dir` in the app under
**⚙️ Sincronização entre máquinas**. On the next start the local catalogue and
covers are **copied** into the shared dir — originals are kept.

### Covers are listed but do not display

Check for `0 bytes` in the diagnostic output. Cloud clients show
not-yet-downloaded files as empty placeholders. Right-click the shared folder
→ **Always keep on this device**, and wait for the sync to finish.

### A provider never enriches anything

`secrets.json` is looked up in the repo first, then in the shared data dir.
The diagnostic prints both paths and whether each exists. Put the file in the
**shared** dir and every machine picks up the keys.
