# Installing

The [README](../README.md) covers the quickest route. This page is the manual
one, for when you already have Python or want to know what the launchers do.

Requirement either way: **Python 3.10+**. The only dependency is `streamlit`;
everything else is Python's standard library.

## macOS / Linux

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

## Windows

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
