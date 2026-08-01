# How it works

Everything lives in one `works` table, whatever the medium: a row is a
`movie | series | album | game`, with its title, year, cover, the drive it sits
on, and the status you gave it. That single model is what lets one gallery
filter across films, shows, records and games at once.

Enrichment responses are cached in `enrich_cache`, keyed by provider and query,
so a re-run never re-hits an API — and the catalogue keeps working with no
network at all.

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
   Two passes read the real files instead of the network, and only for drives
   mounted right now: `id3` (album artist/year/genre + embedded cover) and
   `container` (series/season/episode/year from inside MP4 and MKV files, which
   states what the file name only implies). Both are in the sidebar, and are
   `mediacat.py id3` / `mediacat.py tags` on the command line.
3. **Browse** — `app.py` (Streamlit) shows the filterable cover gallery.

## Command line

```bash
python mediacat.py scan            # scan every drive-xray-registered drive
python mediacat.py scan a.db b.db  # or specific drive-xray db files
python mediacat.py summary         # counts by type / platform
python mediacat.py tags            # read tags from inside mounted video files
streamlit run app.py --server.port 8503   # the gallery
```
