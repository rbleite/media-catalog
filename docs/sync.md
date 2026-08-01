# Sharing a catalogue between computers

There are two ways, and they solve different problems.

| | Bundle | Shared folder |
|---|---|---|
| Needs a cloud service | no | yes |
| Needs API keys on the other machine | **no** | no |
| Needs a drive-xray index there | **no** | yes |
| Keeps machines in step automatically | no | yes |
| Good for | a laptop, someone else's PC, a one-off copy | your own machines, continuously |

## Bundle: covers and metadata in one file

Enrichment costs API keys and time. Once a title has its cover and its
metadata, no other computer should have to earn that again -- and it does not
need TMDB or IGDB accounts of its own to receive it.

```bash
python mediacat.py export-bundle                  # -> media-catalog-bundle.zip
```

Copy that file anywhere -- USB stick, email, whatever -- and on the other
machine:

```bash
python mediacat.py import-bundle media-catalog-bundle.zip
```

Both are also in the sidebar, under **Levar capas e dados para outro PC**,
as a download and an upload.

What travels: every enriched title, its year, genre, provider data, your
watched/played status, and **the cover image files themselves**. What does not:
`secrets.json`. A bundle is meant to be copied around, so API keys never go
inside one -- and the receiving machine does not need them anyway.

The bundle is self-sufficient. A computer with no drive-xray index at all can
import one and browse the whole collection, seeing which drive each title lives
on. Titles it *has* scanned are updated in place instead. A correction you made
by hand on the receiving machine is never overwritten.

Re-importing the same bundle is safe.

## Shared folder: machines that stay in step

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

