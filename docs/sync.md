# One catalogue across several machines

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

