"""media-catalog — visual gallery for your movies / albums / games.

Reads catalog.db (built by `mediacat.py scan`), shows a filterable cover grid,
tells you which drive each title is on, and can enrich movies via TMDB.

    streamlit run app.py
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import streamlit as st

from media_catalog import catalog as C
from media_catalog import config

st.set_page_config(page_title="Media Catalog", page_icon="🎬", layout="wide")

st.markdown("""
<style>
.mc-cover {
  width: 100%;
  aspect-ratio: 1 / 1;
  border-radius: 8px;
  overflow: hidden;
  background: #f1f3f5;
  margin-bottom: 0.55rem;
}
.mc-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.mc-placeholder {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 3rem;
}
.mc-title {
  min-height: 2.7em;
  line-height: 1.32;
  font-weight: 700;
  font-size: 0.96rem;
  margin: 0 0 0.28rem 0;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.mc-meta {
  min-height: 2.55em;
  line-height: 1.35;
  color: rgba(49, 51, 63, 0.70);
  font-size: 0.86rem;
  margin-bottom: 0.32rem;
}
.mc-drive {
  min-height: 1.35em;
  color: rgba(49, 51, 63, 0.70);
  font-size: 0.84rem;
  margin-bottom: 0.50rem;
}
.mc-badge {
  display: inline-block;
  margin-left: 0.35rem;
  padding: 0.04rem 0.25rem;
  border-radius: 4px;
  background: #e7f6e7;
  color: #1b6b1b;
  font-size: 0.72rem;
  vertical-align: 0.08rem;
}
</style>
""", unsafe_allow_html=True)

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


def _has_fts() -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='works_fts'"
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _fts_query(q: str) -> str:
    tokens = re.findall(r"\w+", q.lower(), flags=re.UNICODE)
    return " ".join(f"{t}*" for t in tokens[:8])

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

# genres — comma-separated in the DB (e.g. "Drama, Thriller"); split into a set
_genre_set: set = set()
for (_g,) in conn.execute(
        "SELECT DISTINCT genre FROM works WHERE genre IS NOT NULL AND genre!=''"):
    for _part in str(_g).split(","):
        _p = _part.strip()
        if _p:
            _genre_set.add(_p)
sel_genres = st.sidebar.multiselect("Género (filmes / álbuns / jogos)",
                                    sorted(_genre_set), default=[])

_STATUS_OPTS = {"": "Todos", "todo": "⬜ Por ver / jogar",
                "done": "✅ Visto / jogado", "want": "⭐ Wishlist"}
sel_status = st.sidebar.radio("Estado", list(_STATUS_OPTS),
                              format_func=lambda k: _STATUS_OPTS[k],
                              horizontal=False, key="sel_status")

# ordering — default groups by cover/type/title; the rest are user picks
_SORT_OPTS = {
    "default": "Predefinição (com capa → tipo → título)",
    "recent": "🆕 Recentes primeiro (data do ficheiro)",
    "year_desc": "Ano ↓",
    "size_desc": "Tamanho ↓",
    "title_az": "Título A–Z",
}
_SORT_SQL = {
    "default": "(cover_path IS NULL), type, title",
    "recent": "mtime DESC NULLS LAST, updated_at DESC",
    "year_desc": "year DESC NULLS LAST, title",
    "size_desc": "size_bytes DESC NULLS LAST",
    "title_az": "title COLLATE NOCASE",
}
sel_sort = st.sidebar.selectbox("Ordenar por", list(_SORT_OPTS),
                                format_func=lambda k: _SORT_OPTS[k])

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
dedup = st.sidebar.checkbox("🔀 Ocultar duplicados", value=True,
                            help="Colapsa o mesmo título repetido em várias "
                                 "drives/pastas numa só entrada (×N).")
show_hidden = st.sidebar.checkbox("🚫 Mostrar ocultos", value=False)

_REVIEW_OPTS = {
    "": "Galeria normal",
    "missing_cover": "Sem capa",
    "missing_meta": "Metadados em falta",
    "misses": "Falhas de enriquecimento",
    "possible_dups": "Possíveis duplicados",
}
review_mode = st.sidebar.selectbox(
    "Modo de revisão", list(_REVIEW_OPTS),
    format_func=lambda k: _REVIEW_OPTS[k],
    help="Filas rápidas para limpar o catálogo.")

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

_pa = conn.execute(
    "SELECT COUNT(*) FROM works WHERE type='album' AND enriched=0").fetchone()[0]
if st.sidebar.button(f"💿 Enriquecer álbuns via MusicBrainz ({_pa} por fazer)",
                     disabled=_pa == 0):
    from media_catalog.enrich import musicbrainz as _mb
    prog = st.sidebar.progress(0.0, text="A enriquecer… (~1s/álbum)")
    def _cba(i, n, m, ms):
        prog.progress(i / max(n, 1), text=f"{i}/{n} · {m} capas")
    res = _mb.enrich_albums(conn, progress=_cba)
    st.sidebar.success(f"✓ {res['matched']} capas · {res['missed']} sem match")
    st.cache_resource.clear()
    st.rerun()

_pai = conn.execute(
    "SELECT COUNT(*) FROM works WHERE type='album' AND cover_path IS NULL").fetchone()[0]
if st.sidebar.button(f"💿 Capas em falta via Deezer ({_pai})", disabled=_pai == 0):
    from media_catalog.enrich import deezer as _dz
    prog = st.sidebar.progress(0.0, text="A procurar capas…")
    def _cbi(i, n, m, ms):
        prog.progress(i / max(n, 1), text=f"{i}/{n} · {m} capas")
    res = _dz.enrich_albums(conn, progress=_cbi)
    st.sidebar.success(f"✓ +{res['matched']} capas Deezer")
    st.cache_resource.clear()
    st.rerun()

if st.sidebar.button("🎭 Géneros dos álbuns (Deezer)"):
    from media_catalog.enrich import deezer as _dz2
    prog = st.sidebar.progress(0.0, text="A buscar géneros…")
    def _cbg2(i, n, m, ms):
        prog.progress(i / max(n, 1), text=f"{i}/{n} · {m}")
    res = _dz2.enrich_genres(conn, progress=_cbg2)
    st.sidebar.success(f"✓ {res['matched']} géneros")
    st.cache_resource.clear()
    st.rerun()

# ID3 tags — read straight off the MP3s, only for drives mounted right now
from media_catalog import discover as _disc
_id3_roots = _disc.drive_roots()
_id3_force = st.sidebar.checkbox("↻ Sobrescrever tudo (ID3)", value=False,
                                 help="Por defeito só preenche campos vazios.")
if st.sidebar.button(
        "🎵 Ler tags ID3 (drives montadas)",
        disabled=not _id3_roots,
        help=("Lê Artista/Álbum/Ano/Género e capa embutida dos ficheiros MP3 "
              "reais. Só funciona nas drives ligadas agora: "
              + (", ".join(_id3_roots) or "nenhuma montada"))):
    from media_catalog.enrich import id3 as _id3
    prog = st.sidebar.progress(0.0, text="A ler tags ID3…")
    def _cbi3(i, n, u, cv):
        prog.progress(i / max(n, 1), text=f"{i}/{n} · {u} álbuns · {cv} capas")
    res = _id3.enrich_id3(conn, _id3_roots, force=_id3_force, progress=_cbi3)
    st.sidebar.success(f"✓ {res['updated']} álbuns · {res['covers']} capas "
                       f"embutidas ({res['skipped_offline']} offline)")
    st.cache_resource.clear()
    st.rerun()
if not _id3_roots:
    st.sidebar.caption("🎵 ID3: nenhuma drive montada agora.")

# NFO sidecars — exact IMDb match for movies whose drive is mounted
if config.has_tmdb():
    _nfo_force = st.sidebar.checkbox("↻ Re-igualar corrigidos (NFO)", value=False,
                                     help="Também re-iguala filmes já corrigidos à mão.")
    if st.sidebar.button(
            "🎬 Igualar filmes por .nfo (IMDb)",
            disabled=not _id3_roots,
            help=("Lê o IMDb id dos ficheiros .nfo (Kodi/Plex) das pastas dos "
                  "filmes → match TMDB exato. Só nas drives ligadas: "
                  + (", ".join(_id3_roots) or "nenhuma montada"))):
        from media_catalog.enrich import nfo as _nfo
        prog = st.sidebar.progress(0.0, text="A ler .nfo…")
        def _cbn(i, n, m, nid):
            prog.progress(i / max(n, 1), text=f"{i}/{n} · {m} igualados")
        res = _nfo.enrich_nfo(conn, _id3_roots, config.get("tmdb_api_key"),
                              force=_nfo_force, progress=_cbn)
        st.sidebar.success(f"✓ {res['matched']} filmes via IMDb · "
                           f"{res['no_id']} .nfo sem id ({res['skipped_offline']} offline)")
        st.cache_resource.clear()
        st.rerun()

# ── maintenance: re-scan the drive-xray indexes for new titles ─────────────
st.sidebar.divider()
st.sidebar.caption("**Manutenção**")
if st.sidebar.button("🔄 Atualizar catálogo (reler drives)",
                     help="Relê os índices do drive-xray e adiciona títulos "
                          "novos. Refresca a drive no drive-xray primeiro."):
    with st.spinner("A reler as drives indexadas…"):
        p = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "mediacat.py"), "scan"],
            capture_output=True, text=True)
    if p.returncode == 0:
        st.sidebar.success("Catálogo atualizado. Enriquece os novos títulos.")
    else:
        st.sidebar.error((p.stderr or "erro")[:300])
    st.cache_resource.clear()
    st.rerun()


def _inventory_csv() -> bytes:
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["tipo", "titulo", "artista", "ano", "plataforma", "genero",
                "estado", "drive", "caminho", "tamanho_bytes", "fonte", "id"])
    _stmap = {"done": "feito", "want": "wishlist", "": ""}
    for row in conn.execute(
            "SELECT type,title,artist,year,platform,genre,status,drive_label,"
            "rel_path,size_bytes,provider,identifier FROM works"
            " WHERE COALESCE(hidden,0)=0 ORDER BY type,title"):
        row = list(row)
        row[6] = _stmap.get(row[6], row[6] or "")
        w.writerow(["" if c is None else c for c in row])
    return buf.getvalue().encode("utf-8")


st.sidebar.download_button(
    "⬇️ Exportar inventário (CSV)", data=_inventory_csv(),
    file_name="catalogo-media.csv", mime="text/csv",
    use_container_width=True,
    help="Descarrega todas as entradas não ocultas para Excel/Numbers.")

# override patch — back up / restore the hand-work (corrections, status, hidden)
with st.sidebar.expander("💾 Correções (backup/restauro)", expanded=False):
    import json as _json_p
    _patch = C.export_overrides(conn)
    st.download_button(
        f"⬇️ Exportar correções ({_patch['count']})",
        data=_json_p.dumps(_patch, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="overrides.json", mime="application/json",
        use_container_width=True,
        help="Guarda só as tuas correções manuais, estados e ocultações. "
             "Reconstrói o catálogo do índice e reaplica isto por cima.")
    _up = st.file_uploader("Restaurar de um overrides.json", type="json",
                           key="patch_up")
    if _up is not None and st.button("↩️ Aplicar correções", use_container_width=True):
        try:
            res = C.import_overrides(conn, _json_p.loads(_up.getvalue()))
            st.success(f"✓ {res['applied']}/{res['total']} aplicadas "
                       f"({res['missing']} não estão no catálogo)")
            st.cache_resource.clear()
            st.rerun()
        except Exception as e:
            st.error(str(e)[:200])

with st.sidebar.expander("🔄 Atualizações (GitHub)", expanded=False):
    import update as _upd
    if st.button("Verificar atualizações", key="upd_check", use_container_width=True):
        st.session_state["upd_status"] = _upd.check_updates()
    _us = st.session_state.get("upd_status")
    if _us:
        if not _us.get("ok"):
            st.warning(_us.get("error"))
        elif _us["behind"] == 0:
            st.success("✅ Estás na versão mais recente.")
        else:
            st.info(f"🆕 {_us['behind']} atualização(ões):")
            for _c in _us["commits"][:8]:
                st.caption(f"• {_c}")
            if st.button("⬇️ Atualizar agora", type="primary", key="upd_apply"):
                with st.spinner("A atualizar…"):
                    _r = _upd.apply_update()
                (st.success if _r.get("ok") else st.error)(_r.get("message"))
                st.session_state.pop("upd_status", None)

# ── build query ────────────────────────────────────────────────────────────
where, params = ["1=1"], []
if not show_hidden:
    where.append("COALESCE(hidden,0)=0")
if sel_types:
    where.append("type IN (%s)" % ",".join("?" * len(sel_types)))
    params += sel_types
if sel_plats:
    where.append("platform IN (%s)" % ",".join("?" * len(sel_plats)))
    params += sel_plats
if sel_genres:
    # genre column is comma-separated, so match each selected genre by substring
    where.append("(" + " OR ".join(["genre LIKE ?"] * len(sel_genres)) + ")")
    params += [f"%{g}%" for g in sel_genres]
if sel_status == "done":
    where.append("status='done'")
elif sel_status == "want":
    where.append("status='want'")
elif sel_status == "todo":
    where.append("(status IS NULL OR status='')")
if sel_drives:
    where.append("drive_label IN (%s)" % ",".join("?" * len(sel_drives)))
    params += sel_drives
if review_mode == "missing_cover":
    where.append("cover_path IS NULL")
elif review_mode == "missing_meta":
    where.append("(year IS NULL OR genre IS NULL OR genre='')")
elif review_mode == "misses":
    where.append("provider LIKE '%-miss'")
elif review_mode == "possible_dups":
    where.append("lower(title) IN (SELECT lower(title) FROM works WHERE COALESCE(hidden,0)=0 GROUP BY lower(title) HAVING COUNT(*) > 1)")
if query.strip():
    fts = _fts_query(query.strip())
    if fts and _has_fts():
        where.append("id IN (SELECT rowid FROM works_fts WHERE works_fts MATCH ?)")
        params.append(fts)
    else:
        where.append("(title LIKE ? OR title_raw LIKE ? OR artist LIKE ? OR rel_path LIKE ?)")
        qlike = f"%{query.strip()}%"
        params += [qlike, qlike, qlike, qlike]
if yr_range:
    where.append("year BETWEEN ? AND ?")
    params += [yr_range[0], yr_range[1]]
if only_cover:
    where.append("cover_path IS NOT NULL")

sql = ("SELECT id, type, title, artist, year, platform, size_bytes,"
       " drive_label, rel_path, cover_path, genre, identifier, status,"
       " mtime, has_subtitles"
       f" FROM works WHERE {' AND '.join(where)}"
       f" ORDER BY {_SORT_SQL.get(sel_sort, _SORT_SQL['default'])}")
rows = conn.execute(sql, params).fetchall()


def _dup_key(r) -> str:
    """Identity of a title across drives: the canonical enriched id when we
    have one, else type + normalised title + (year/platform/artist)."""
    ident = r[11]
    if ident and ident.split(":", 1)[0] in ("tmdb", "igdb", "mbid"):
        return ident
    norm = re.sub(r"[^a-z0-9]", "", (r[2] or "").lower())
    return f"{r[1]}|{norm}|{r[4] or r[5] or r[3] or ''}"


# each item = (representative_row, copies) where copies = [(drive, rel), …]
if dedup:
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(_dup_key(r), []).append(r)
    items = []
    for grp in groups.values():
        rep = next((x for x in grp if x[9]), grp[0])  # prefer one with a cover
        copies = [(x[7], x[8], x[6] or 0) for x in grp]
        items.append((rep, copies))
    # keep the cover-first / type / title ordering of the representatives
    items.sort(key=lambda it: (it[0][9] is None, it[0][1], (it[0][2] or "").lower()))
else:
    items = [(r, [(r[7], r[8], r[6] or 0)]) for r in rows]

# ── header metrics ─────────────────────────────────────────────────────────
st.title("🎬 Media Catalog")
by_type: dict[str, int] = {}
total_size = 0
for rep, copies in items:
    by_type[rep[1]] = by_type.get(rep[1], 0) + 1
    total_size += sum(c[2] for c in copies) if dedup else (rep[6] or 0)
mcols = st.columns(4)
mcols[0].metric("Resultados", f"{len(items):,}"
                + (f" (de {len(rows):,})" if dedup and len(items) != len(rows) else ""))
mcols[1].metric("🎮 Jogos", f"{by_type.get('game', 0):,}")
mcols[2].metric("🎬 Filmes", f"{by_type.get('movie', 0):,}")
mcols[3].metric("💿 Álbuns", f"{by_type.get('album', 0):,}")
_cap = f"Tamanho total: **{human(total_size)}**  ·  {sum(1 for it in items if it[0][9])} com capa"
if dedup and len(items) != len(rows):
    _cap += f"  ·  🔀 {len(rows) - len(items)} duplicados ocultados"
st.caption(_cap)
if review_mode:
    st.info(f"Modo de revisão ativo: {_REVIEW_OPTS[review_mode]}")

# ── pagination ─────────────────────────────────────────────────────────────
PER_PAGE = 60
n_pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)
page = max(1, min(n_pages, int(st.session_state.get("gallery_page", 1))))


def _page_nav(prefix: str):
    """First / ±10 / ±5 / last controls + 'page X of N'. Rendered top & bottom."""
    if n_pages <= 1:
        st.caption(f"Página 1 de 1  ·  {len(items)} resultados")
        return

    def _go(p):
        st.session_state["gallery_page"] = max(1, min(n_pages, p))
        st.rerun()

    c = st.columns([1, 1, 1, 1, 3, 1, 1, 1, 1])
    if c[0].button("⏮", key=f"{prefix}_first", disabled=page <= 1,
                   help="Primeira", use_container_width=True):
        _go(1)
    if c[1].button("−10", key=f"{prefix}_b10", disabled=page <= 1,
                   use_container_width=True):
        _go(page - 10)
    if c[2].button("−5", key=f"{prefix}_b5", disabled=page <= 1,
                   use_container_width=True):
        _go(page - 5)
    if c[3].button("‹", key=f"{prefix}_prev", disabled=page <= 1,
                   help="Anterior", use_container_width=True):
        _go(page - 1)
    c[4].markdown(
        f"<div style='text-align:center;padding-top:6px'>Página "
        f"<b>{page}</b> de <b>{n_pages}</b> · {len(items)} resultados</div>",
        unsafe_allow_html=True)
    if c[5].button("›", key=f"{prefix}_next", disabled=page >= n_pages,
                   help="Seguinte", use_container_width=True):
        _go(page + 1)
    if c[6].button("+5", key=f"{prefix}_f5", disabled=page >= n_pages,
                   use_container_width=True):
        _go(page + 5)
    if c[7].button("+10", key=f"{prefix}_f10", disabled=page >= n_pages,
                   use_container_width=True):
        _go(page + 10)
    if c[8].button("⏭", key=f"{prefix}_last", disabled=page >= n_pages,
                   help="Última", use_container_width=True):
        _go(n_pages)


_page_nav("top")
page_items = items[(page - 1) * PER_PAGE: page * PER_PAGE]

# ── detail + manual correction dialog ──────────────────────────────────────
def _sibling_ids(wid, typ, title, ident):
    if ident and ident.split(":", 1)[0] in ("tmdb", "igdb", "mbid"):
        rs = conn.execute("SELECT id FROM works WHERE identifier=?", (ident,)).fetchall()
    else:
        rs = conn.execute("SELECT id FROM works WHERE type=? AND lower(title)=lower(?)",
                          (typ, title or "")).fetchall()
    return [x[0] for x in rs] or [wid]


def _manual_cover_from_url(wid, typ, url):
    import urllib.request
    dest = config.COVERS_DIR / f"{typ}_{wid}.jpg"
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "media-catalog/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    dest.write_bytes(data)
    conn.execute("UPDATE works SET cover_path=?, manual=1, updated_at=datetime('now')"
                 " WHERE id=?", (str(dest), wid))
    conn.commit()


@st.dialog("Detalhe", width="large")
def show_detail(wid):
    import json as _json
    w = conn.execute(
        "SELECT id,type,title,artist,year,platform,genre,size_bytes,cover_path,"
        "identifier,provider,extra_json,status,mtime,has_subtitles"
        " FROM works WHERE id=?", (wid,)).fetchone()
    if not w:
        st.write("—"); return
    (_id, typ, title, artist, year, platform, genre, size, cover, ident,
     provider, extra, status, mtime, has_subs) = w
    c1, c2 = st.columns([1, 2])
    with c1:
        if cover and Path(cover).exists():
            st.image(cover, use_container_width=True)
        else:
            st.markdown(f'<div style="background:{PLACEHOLDER_BG.get(typ,"#333")};'
                        f'height:260px;border-radius:8px;display:flex;'
                        f'align-items:center;justify-content:center;font-size:4em">'
                        f'{TYPE_EMOJI.get(typ,"?")}</div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f"### {title}")
        meta = " · ".join(str(x) for x in [TYPE_LABEL.get(typ, typ), platform,
                          artist, year, genre, human(size),
                          ("🔤 legendas" if has_subs else ""),
                          (f"🆕 {_fmt_date(mtime)}" if mtime else "")] if x)
        st.caption(meta)
        if extra:
            try:
                d = _json.loads(extra)
                if d.get("overview"):
                    st.write(d["overview"])
                if d.get("vote_average"):
                    st.caption(f"⭐ {d['vote_average']}")
            except Exception:
                pass
        st.caption(f"Fonte: `{provider or '—'}`  ·  `{ident or ''}`")
        # external links (movies) — explore on TMDB / IMDb
        if typ == "movie" and ident and ident.startswith("tmdb:"):
            tmdb_id = ident.split(":", 1)[1]
            links = [f"[TMDB](https://www.themoviedb.org/movie/{tmdb_id})"]
            if config.has_tmdb():
                from media_catalog.enrich import tmdb as _t
                _imdb = _t.imdb_id_for(conn, tmdb_id, config.get("tmdb_api_key"))
                if _imdb:
                    links.insert(0, f"**[▶ IMDb](https://www.imdb.com/title/{_imdb}/)**")
            st.markdown("🔗 " + "  ·  ".join(links))

    sibs = _sibling_ids(wid, typ, title, ident)
    st.markdown(f"**Cópias ({len(sibs)}):**")
    for sid in sibs:
        d, rp = conn.execute("SELECT drive_label, rel_path FROM works WHERE id=?",
                             (sid,)).fetchone()
        st.caption(f"📀 **{d}** · `{rp}`")

    st.divider()
    _done_lbl = {"movie": "✅ Visto", "game": "✅ Jogado",
                 "album": "✅ Ouvido"}.get(typ, "✅ Feito")
    _cur = {"done": _done_lbl.replace("✅", "✅ marcado:"),
            "want": "⭐ na wishlist"}.get(status, "sem estado")
    st.markdown(f"**🎯 Estado:** {_cur}")
    sc1, sc2, sc3 = st.columns(3)

    def _set_status(val):
        conn.executemany("UPDATE works SET status=? WHERE id=?",
                         [(val, s) for s in sibs])
        conn.commit()
        st.rerun()

    if sc1.button(_done_lbl, key=f"st_done_{wid}", use_container_width=True,
                  type="primary" if status == "done" else "secondary"):
        _set_status("done")
    if sc2.button("⭐ Wishlist", key=f"st_want_{wid}", use_container_width=True,
                  type="primary" if status == "want" else "secondary"):
        _set_status("want")
    if sc3.button("⬜ Limpar", key=f"st_clear_{wid}", use_container_width=True):
        _set_status("")

    st.divider()
    st.markdown("**✏️ Corrigir**")
    apply_all = st.checkbox(f"Aplicar a todas as cópias ({len(sibs)})",
                            value=len(sibs) > 1, key=f"aa_{wid}")
    tgt = sibs if apply_all else [wid]

    qc1, qc2 = st.columns([3, 1])
    dq = qc1.text_input("Procurar título certo", value=title or "", key=f"q_{wid}")
    lang = "en-US"
    if typ == "movie":
        lang = "pt-PT" if qc2.checkbox("PT", key=f"pt_{wid}") else "en-US"

    if st.button("🔍 Procurar", key=f"go_{wid}"):
        cands = []
        try:
            if typ == "movie" and config.has_tmdb():
                from media_catalog.enrich import tmdb
                cands = [("tmdb", c) for c in
                         tmdb.search_candidates(dq, None, config.get("tmdb_api_key"), lang)]
            elif typ == "game" and config.has_igdb():
                from media_catalog.enrich import igdb
                tok = igdb.get_token()
                cands = [("igdb", c) for c in
                         igdb.search_candidates(dq, config.get("igdb_client_id"), tok)]
            elif typ == "album":
                from media_catalog.enrich import deezer
                cands = [("deezer", c) for c in deezer.search_candidates(artist or "", dq)]
        except Exception as e:
            st.error(str(e)[:200])
        st.session_state[f"cands_{wid}"] = cands

    for j, (prov, c) in enumerate(st.session_state.get(f"cands_{wid}", [])):
        cc1, cc2, cc3 = st.columns([1, 3, 1])
        if prov == "tmdb":
            pp = c.get("poster_path")
            img = f"https://image.tmdb.org/t/p/w154{pp}" if pp else None
            label = f"{c.get('title')} ({(c.get('release_date') or '')[:4]})"
        elif prov == "igdb":
            iid = (c.get("cover") or {}).get("image_id")
            img = f"https://images.igdb.com/igdb/image/upload/t_cover_small/{iid}.jpg" if iid else None
            ts = c.get("first_release_date")
            label = f"{c.get('name')} ({__import__('datetime').datetime.utcfromtimestamp(ts).year if ts else '?'})"
        else:
            img = c.get("cover_medium")
            label = f"{c.get('title')} — {(c.get('artist') or {}).get('name','')}"
        if img:
            cc1.image(img, width=60)
        cc2.write(label)
        if cc3.button("usar", key=f"use_{wid}_{j}"):
            from media_catalog.enrich import tmdb, igdb, deezer
            for sid in tgt:
                if prov == "tmdb":
                    tmdb.apply_candidate(conn, sid, c, config.get("tmdb_api_key"))
                elif prov == "igdb":
                    igdb.apply_candidate(conn, sid, c, config.get("igdb_client_id"))
                else:
                    deezer.apply_candidate(conn, sid, c)
            st.cache_resource.clear()
            st.rerun()

    # precise correction by IMDb id/URL (movies)
    if typ == "movie" and config.has_tmdb():
        ic1, ic2 = st.columns([3, 1])
        imdb_in = ic1.text_input("…ou corrigir por IMDb (tt… ou link)", key=f"imdb_{wid}")
        if ic2.button("usar IMDb", key=f"imdbgo_{wid}") and imdb_in.strip():
            from media_catalog.enrich import tmdb as _t
            best = _t.find_by_imdb(imdb_in.strip(), config.get("tmdb_api_key"))
            if best:
                for sid in tgt:
                    _t.apply_candidate(conn, sid, best, config.get("tmdb_api_key"))
                st.cache_resource.clear(); st.rerun()
            else:
                st.error("IMDb não encontrado nesse ID/URL.")

    st.divider()
    mc1, mc2 = st.columns([3, 1])
    murl = mc1.text_input("…ou capa por URL", key=f"url_{wid}")
    if mc2.button("Aplicar capa", key=f"mc_{wid}") and murl.strip():
        try:
            for sid in tgt:
                _manual_cover_from_url(sid, typ, murl.strip())
            st.cache_resource.clear(); st.rerun()
        except Exception as e:
            st.error(str(e)[:200])

    if st.button(f"🚫 Ocultar {'estas cópias' if apply_all else 'esta entrada'} (não é media)",
                 key=f"hide_{wid}"):
        conn.executemany("UPDATE works SET hidden=1 WHERE id=?", [(s,) for s in tgt])
        conn.commit()
        st.cache_resource.clear(); st.rerun()


# ── hover tooltip helpers ──────────────────────────────────────────────────
import base64


@st.cache_data(show_spinner=False, max_entries=4096)
def _cover_b64(path: str, _mtime: float) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _tooltip(r, copies) -> str:
    (wid, typ, title, artist, year, platform, size, drive, rel, cover,
     genre, ident, status, mtime, has_subs) = r
    lines = [title or ""]
    meta = " · ".join(str(x) for x in
                      [TYPE_LABEL.get(typ, typ), platform, artist, year] if x)
    if meta:
        lines.append(meta)
    if genre:
        lines.append("🎭 " + genre)
    if has_subs:
        lines.append("🔤 legendas")
    if size:
        lines.append(human(size))
    if mtime:
        lines.append("🆕 " + _fmt_date(mtime))
    _dr = ", ".join(sorted({d for d, *_ in copies}))
    lines.append(f"📀 {_dr}" + (f"  (×{len(copies)})" if len(copies) > 1 else ""))
    return "&#10;".join(_esc(l) for l in lines if l)


def _fmt_date(ts) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except Exception:
        return ""


# ── recently-added strip ────────────────────────────────────────────────────
# Independent of the active filters/sort: the newest titles by file mtime, so
# "what did I just add to the disk?" is always one glance away.
_recent = conn.execute(
    "SELECT id, type, title, cover_path, mtime FROM works"
    " WHERE COALESCE(hidden,0)=0 AND mtime IS NOT NULL"
    " ORDER BY mtime DESC LIMIT 12").fetchall()
if _recent and page == 1:
    with st.expander("🆕 Recém-adicionados", expanded=True):
        rcols = st.columns(len(_recent))
        for rc, (rid, rtyp, rtitle, rcover, rmt) in zip(rcols, _recent):
            with rc:
                if rcover and Path(rcover).exists():
                    _rb = _cover_b64(rcover, Path(rcover).stat().st_mtime)
                    st.markdown(
                        f'<div class="mc-cover"><img src="data:image/jpeg;base64,{_rb}" '
                        f'title="{_esc(rtitle)} · {_fmt_date(rmt)}" alt="{_esc(rtitle)}"></div>',
                        unsafe_allow_html=True)
                else:
                    st.markdown(
                        f'<div class="mc-cover" style="background:{PLACEHOLDER_BG.get(rtyp,"#333")}">'
                        f'<div class="mc-placeholder">{TYPE_EMOJI.get(rtyp,"?")}</div></div>',
                        unsafe_allow_html=True)
                st.caption(f"{TYPE_EMOJI.get(rtyp,'')} {_fmt_date(rmt)}")
                if st.button("🔍", key=f"rec_{rid}", help=rtitle,
                             use_container_width=True):
                    show_detail(rid)

# ── card grid ──────────────────────────────────────────────────────────────
NCOL = 6
for row_start in range(0, len(page_items), NCOL):
    cols = st.columns(NCOL)
    for col, (r, copies) in zip(cols, page_items[row_start: row_start + NCOL]):
        (wid, typ, title, artist, year, platform, size, drive, rel, cover,
         genre, ident, status, mtime, has_subs) = r
        n_copies = len(copies)
        with col:
            _tip = _tooltip(r, copies)
            _title = _esc(title or "")
            if cover and Path(cover).exists():
                _b = _cover_b64(cover, Path(cover).stat().st_mtime)
                st.markdown(
                    f'<div class="mc-cover"><img src="data:image/jpeg;base64,{_b}" '
                    f'title="{_tip}" alt="{_title}"></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    f'<div class="mc-cover" title="{_tip}" '
                    f'style="background:{PLACEHOLDER_BG.get(typ,"#333")}">'
                    f'<div class="mc-placeholder">{TYPE_EMOJI.get(typ,"?")}</div></div>',
                    unsafe_allow_html=True)

            _badges = ""
            if n_copies > 1:
                _badges += f'<span class="mc-badge">×{n_copies}</span>'
            if status == "done":
                _badges += '<span class="mc-badge">✓</span>'
            elif status == "want":
                _badges += '<span class="mc-badge">★</span>'
            if has_subs:
                _badges += '<span class="mc-badge" title="tem legendas">🔤</span>'
            st.markdown(f'<div class="mc-title">{_title}{_badges}</div>',
                        unsafe_allow_html=True)

            sub = artist or platform or (str(year) if year else "")
            line = " · ".join(x for x in [sub, str(year) if (year and sub != str(year)) else "",
                                          human(size)] if x)
            st.markdown(f'<div class="mc-meta">{_esc(line)}</div>',
                        unsafe_allow_html=True)
            _drv = ", ".join(sorted({d for d, *_ in copies}))
            st.markdown(f'<div class="mc-drive">📀 {_esc(_drv)}</div>',
                        unsafe_allow_html=True)
            if st.button("🔍 Detalhes / corrigir", key=f"det_{wid}",
                         use_container_width=True):
                show_detail(wid)

if page_items:
    st.divider()
    _page_nav("bottom")

if not rows:
    st.info("Nada corresponde aos filtros. Corre `python mediacat.py scan` "
            "para (re)construir o catálogo.")
