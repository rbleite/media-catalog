# media-catalog

**A visual gallery of your movies, TV series, albums and console games** —
built on top of the [drive-xray](https://github.com/rbleite/drive-xray) index
(no re-scan), enriched with covers and metadata from TMDB, IGDB, MusicBrainz
and Deezer.

drive-xray is about *files* — sizes, duplicates, what is where. This one is
about *titles*: a browsable, filterable catalogue that also tells you **which
drive each title lives on**.

> ℹ️ **This is a personal companion, not a standalone consumer app.** It reads
> the `.db` files that drive-xray produces for *your* drives, and the media
> roots in `discover.py` point at *your* collection layout. Someone else can
> install and run it, but they must point it at their own drive-xray indexes
> and edit the roots (see [docs/configuration.md](docs/configuration.md))
> before the catalogue fills up.

## What it does

- 🎬🎮💿 **Unified catalogue** of movies, series, games (PS3/PS4/Switch/Wii/…)
  and albums, classified straight from the drive-xray index.
- 🖼️ **Cover gallery** with hover tooltips, filters (type, platform, genre,
  drive, year, search) and dedup across drives (`×N` copies collapse to one).
- ✨ **Metadata enrichment** — covers, year, genre, synopsis, from TMDB (films
  and series), IGDB (games), MusicBrainz and Deezer (albums). Every response is
  cached, so re-runs never re-hit an API.
- 🏷️ **Reads the files themselves** when a drive is plugged in: ID3 tags for
  albums, and embedded MP4/MKV tags for series — which *state* the season and
  episode the file name only implies.
- 🎯 **Watched / played / wishlist** status per title, with a sidebar filter.
- ✏️ **Manual correction** — pin the right title or cover and it is never
  auto-overwritten. Fix by IMDb id too.
- 🎮 **PS3/PS4 title-id resolution** — bare serials (`BCES01175`) resolved to
  real names via an offline GameTDB map.
- ⬇️ **Export** the inventory to CSV, and 🔄 **self-update** from the sidebar.

## Install

### Windows

The [drive-xray installer](https://github.com/rbleite/drive-xray#install) sets
up **both apps** in one line — that is the easiest route, and you need
drive-xray anyway:

```powershell
irm https://raw.githubusercontent.com/rbleite/drive-xray/main/install.ps1 | iex
```

### macOS / Linux

```bash
git clone https://github.com/rbleite/media-catalog.git
cd media-catalog
bash build_app.sh
open ~/Applications/media-catalog.app
```

That builds a double-click launcher serving the gallery on port 8503.

> Prefer to install by hand? See **[docs/install.md](docs/install.md)**.

## First steps

**Index a drive in drive-xray first.** media-catalog reads what drive-xray
produces, so until a drive has been scanned there is nothing to show.

Then:

1. **Point the roots at your layout.** `MOVIE_ROOTS`, `MUSIC_ROOTS` and
   `GAME_ROOTS` in `discover.py` decide what counts as a title, so a loose clip
   in Downloads never masquerades as a film. This is the one step nobody can do
   for you — see [docs/configuration.md](docs/configuration.md).
2. **Scan:** `python mediacat.py scan`, or the sidebar button. The catalogue
   fills with raw titles read from the index.
3. **Add API keys** for covers and metadata — TMDB and IGDB are free, and
   albums need no key at all. Setup is in
   [docs/configuration.md](docs/configuration.md).
4. **Enrich**, from the sidebar. Everything is cached, so it only ever runs once
   per title.
5. **Plug a drive in and read its tags** — the ID3 and video-tag buttons only
   act on drives mounted right now, and fill in what the file names could not.

## Documentation

| Page | What's in it |
|---|---|
| [docs/configuration.md](docs/configuration.md) | Collection roots, and step-by-step API account setup |
| [docs/install.md](docs/install.md) | Manual install on each platform |
| [docs/how-it-works.md](docs/how-it-works.md) | How titles are discovered and enriched, plus the CLI |
| [docs/sync.md](docs/sync.md) | Sharing a catalogue between computers — a portable bundle, or a synced folder |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Missing covers, offline drives, providers that do nothing |

**On another computer and missing the covers?** You do not need API keys there
— export a bundle on the machine that has them and import it on the other:

```bash
python mediacat.py export-bundle      # then copy the .zip over
python mediacat.py import-bundle media-catalog-bundle.zip
```

Covers and metadata travel inside it; credentials never do. See
[docs/sync.md](docs/sync.md).

Covers missing on a second machine is the common one, and
[`scripts/diagnose_sync.py`](scripts/diagnose_sync.py) answers it with facts —
run it on both machines and compare.
