# Troubleshooting

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
