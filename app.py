"""media-catalog — visual gallery for your movies / albums / games.

Reads catalog.db (built by `mediacat.py scan`), shows a filterable cover grid,
tells you which drive each title is on, and can enrich movies via TMDB.

    streamlit run app.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st

from media_catalog import catalog as C
from media_catalog import config

st.set_page_config(page_title="Media Catalog", page_icon="🎬", layout="wide")

TYPE_EMOJI = {"movie": "🎬", "album": "💿", "game": "🎮"}
TYPE_LABEL = {"movie": "Filmes", "album": "Álbuns", "game": "Jogos"}
PLACEHOLDER_BG = {"movie": "#37474f", "album": "#4a148c", "game": "#1b5e20"}


def human(n: int | None) -> str:
    if not n:
        return ""
    v = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or u == "TB":
            return f"{v:.1f} {u}" if u not in ("B", "KB") else f"{int(v)} {u}"
        v /= 1024
    return f"{v:.1f} TB"


@st.cache_resource
def _conn() -> sqlite3.Connection:
    # check_same_thread=False: Streamlit serves reruns from a thread pool, so
    # the single cached connection must be usable across threads.
    return C.open_catalog(C.DEFAULT_CATALOG, check_same_thread=False)


conn = _conn()

# ── sidebar filters ────────────────────────────────────────────────────────
st.sidebar.title("🎬 Media Catalog")

_types = [r[0] for r in conn.execute(
    "SELECT DISTINCT type FROM works ORDER BY type")]
sel_types = st.sidebar.multiselect(
    "Tipo", _types, default=_types,
    format_func=lambda t: f"{TYPE_EMOJI.get(t,'')} {TYPE_LABEL.get(t,t)}")

_plats = [r[0] for r in conn.execute(
    "SELECT DISTINCT platform FROM works WHERE platform IS NOT NULL ORDER BY platform")]
sel_plats = st.sidebar.multiselect("Plataforma (jogos)", _plats, default=[])

_drives = [r[0] for r in conn.execute(
    "SELECT DISTINCT drive_label FROM works ORDER BY drive_label")]
sel_drives = st.sidebar.multiselect("Drive", _drives, default=[])

query = st.sidebar.text_input("🔍 Pesquisar título / artista", "")

_yr = conn.execute(
    "SELECT MIN(year), MAX(year) FROM works WHERE year IS NOT NULL").fetchone()
yr_lo, yr_hi = (_yr[0] or 1950), (_yr[1] or 2026)
use_year = st.sidebar.checkbox("Filtrar por ano", value=False)
if use_year and yr_lo < yr_hi:
    yr_range = st.sidebar.slider("Ano", yr_lo, yr_hi, (yr_lo, yr_hi))
else:
    yr_range = None

only_cover = st.sidebar.checkbox("Só com capa", value=False)

# ── TMDB enrichment control ────────────────────────────────────────────────
st.sidebar.divider()
if config.has_tmdb():
    _pend = conn.execute(
        "SELECT COUNT(*) FROM works WHERE type='movie' AND enriched=0").fetchone()[0]
    if st.sidebar.button(f"🎬 Enriquecer filmes via TMDB ({_pend} por fazer)",
                         disabled=_pend == 0):
        from media_catalog.enrich import tmdb
        prog = st.sidebar.progress(0.0, text="A enriquecer…")
        def _cb(i, n, m, ms):
            prog.progress(i / max(n, 1), text=f"{i}/{n} · {m} capas")
        res = tmdb.enrich_movies(conn, config.get("tmdb_api_key"), progress=_cb)
        st.sidebar.success(f"✓ {res['matched']} capas · {res['missed']} sem match")
        st.cache_resource.clear()
        st.rerun()
else:
    st.sidebar.caption("Sem chave TMDB — vê o README para ligar capas.")

if config.has_igdb():
    _pg = conn.execute(
        "SELECT COUNT(*) FROM works WHERE type='game' AND enriched=0").fetchone()[0]
    if st.sidebar.button(f"🎮 Enriquecer jogos via IGDB ({_pg} por fazer)",
                         disabled=_pg == 0):
        from media_catalog.enrich import igdb
        prog = st.sidebar.progress(0.0, text="A enriquecer…")
        def _cbg(i, n, m, ms):
            prog.progress(i / max(n, 1), text=f"{i}/{n} · {m} capas")
        res = igdb.enrich_games(conn, progress=_cbg)
        st.sidebar.success(f"✓ {res['matched']} capas · {res['missed']} sem match")
        st.cache_resource.clear()
        st.rerun()
else:
    st.sidebar.caption("Sem chaves IGDB — vê o README para capas de jogos.")

# ── build query ────────────────────────────────────────────────────────────
where, params = ["1=1"], []
if sel_types:
    where.append("type IN (%s)" % ",".join("?" * len(sel_types)))
    params += sel_types
if sel_plats:
    where.append("platform IN (%s)" % ",".join("?" * len(sel_plats)))
    params += sel_plats
if sel_drives:
    where.append("drive_label IN (%s)" % ",".join("?" * len(sel_drives)))
    params += sel_drives
if query.strip():
    where.append("(title LIKE ? OR artist LIKE ?)")
    params += [f"%{query.strip()}%", f"%{query.strip()}%"]
if yr_range:
    where.append("year BETWEEN ? AND ?")
    params += [yr_range[0], yr_range[1]]
if only_cover:
    where.append("cover_path IS NOT NULL")

sql = ("SELECT id, type, title, artist, year, platform, size_bytes,"
       " drive_label, rel_path, cover_path, genre"
       f" FROM works WHERE {' AND '.join(where)}"
       " ORDER BY (cover_path IS NULL), type, title")
rows = conn.execute(sql, params).fetchall()

# ── header metrics ─────────────────────────────────────────────────────────
st.title("🎬 Media Catalog")
by_type: dict[str, int] = {}
total_size = 0
for r in rows:
    by_type[r[1]] = by_type.get(r[1], 0) + 1
    total_size += r[6] or 0
mcols = st.columns(4)
mcols[0].metric("Resultados", f"{len(rows):,}")
mcols[1].metric("🎮 Jogos", f"{by_type.get('game', 0):,}")
mcols[2].metric("🎬 Filmes", f"{by_type.get('movie', 0):,}")
mcols[3].metric("💿 Álbuns", f"{by_type.get('album', 0):,}")
st.caption(f"Tamanho total filtrado: **{human(total_size)}**  ·  "
           f"{sum(1 for r in rows if r[9])} com capa")

# ── pagination ─────────────────────────────────────────────────────────────
PER_PAGE = 60
n_pages = max(1, (len(rows) + PER_PAGE - 1) // PER_PAGE)
page = st.number_input("Página", 1, n_pages, 1, step=1) if n_pages > 1 else 1
page_rows = rows[(page - 1) * PER_PAGE: page * PER_PAGE]

# ── card grid ──────────────────────────────────────────────────────────────
NCOL = 6
cols = st.columns(NCOL)
for i, r in enumerate(page_rows):
    (wid, typ, title, artist, year, platform, size, drive, rel, cover, genre) = r
    with cols[i % NCOL]:
        if cover and Path(cover).exists():
            st.image(cover, use_container_width=True)
        else:
            st.markdown(
                f'<div style="background:{PLACEHOLDER_BG.get(typ,"#333")};'
                f'height:210px;border-radius:8px;display:flex;align-items:center;'
                f'justify-content:center;font-size:3em">{TYPE_EMOJI.get(typ,"?")}</div>',
                unsafe_allow_html=True)
        sub = artist or platform or (str(year) if year else "")
        st.markdown(f"**{(title or '')[:40]}**")
        line = " · ".join(x for x in [sub, str(year) if (year and sub != str(year)) else "",
                                      human(size)] if x)
        st.caption(line)
        st.caption(f"📀 {drive}")
        with st.popover("onde está"):
            st.write(f"**Drive:** {drive}")
            st.code(rel, language=None)
            if genre:
                st.write(f"**Género:** {genre}")

if not rows:
    st.info("Nada corresponde aos filtros. Corre `python mediacat.py scan` "
            "para (re)construir o catálogo.")
