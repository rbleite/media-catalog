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
       " drive_label, rel_path, cover_path, genre, identifier"
       f" FROM works WHERE {' AND '.join(where)}"
       " ORDER BY (cover_path IS NULL), type, title")
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
        copies = [(x[7], x[8]) for x in grp]
        items.append((rep, copies))
    # keep the cover-first / type / title ordering of the representatives
    items.sort(key=lambda it: (it[0][9] is None, it[0][1], (it[0][2] or "").lower()))
else:
    items = [(r, [(r[7], r[8])]) for r in rows]

# ── header metrics ─────────────────────────────────────────────────────────
st.title("🎬 Media Catalog")
by_type: dict[str, int] = {}
total_size = 0
for rep, copies in items:
    by_type[rep[1]] = by_type.get(rep[1], 0) + 1
    total_size += rep[6] or 0
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
        "identifier,provider,extra_json FROM works WHERE id=?", (wid,)).fetchone()
    if not w:
        st.write("—"); return
    (_id, typ, title, artist, year, platform, genre, size, cover, ident,
     provider, extra) = w
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
                          artist, year, genre, human(size)] if x)
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
     genre, ident) = r
    lines = [title or ""]
    meta = " · ".join(str(x) for x in
                      [TYPE_LABEL.get(typ, typ), platform, artist, year] if x)
    if meta:
        lines.append(meta)
    if genre:
        lines.append("🎭 " + genre)
    if size:
        lines.append(human(size))
    _dr = ", ".join(sorted({d for d, _ in copies}))
    lines.append(f"📀 {_dr}" + (f"  (×{len(copies)})" if len(copies) > 1 else ""))
    return "&#10;".join(_esc(l) for l in lines if l)


# ── card grid ──────────────────────────────────────────────────────────────
NCOL = 6
cols = st.columns(NCOL)
for i, (r, copies) in enumerate(page_items):
    (wid, typ, title, artist, year, platform, size, drive, rel, cover, genre, ident) = r
    n_copies = len(copies)
    with cols[i % NCOL]:
        _tip = _tooltip(r, copies)
        if cover and Path(cover).exists():
            _b = _cover_b64(cover, Path(cover).stat().st_mtime)
            st.markdown(
                f'<img src="data:image/jpeg;base64,{_b}" title="{_tip}" '
                f'style="width:100%;border-radius:6px;display:block" />',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div title="{_tip}" style="background:{PLACEHOLDER_BG.get(typ,"#333")};'
                f'height:210px;border-radius:8px;display:flex;align-items:center;'
                f'justify-content:center;font-size:3em">{TYPE_EMOJI.get(typ,"?")}</div>',
                unsafe_allow_html=True)
        badge = f"  `×{n_copies}`" if n_copies > 1 else ""
        st.markdown(f"**{(title or '')[:40]}**{badge}")
        sub = artist or platform or (str(year) if year else "")
        line = " · ".join(x for x in [sub, str(year) if (year and sub != str(year)) else "",
                                      human(size)] if x)
        st.caption(line)
        _drv = ", ".join(sorted({d for d, _ in copies}))
        st.caption(f"📀 {_drv}")
        if st.button("🔍 Detalhes / corrigir", key=f"det_{wid}",
                     use_container_width=True):
            show_detail(wid)

if page_items:
    st.divider()
    _page_nav("bottom")

if not rows:
    st.info("Nada corresponde aos filtros. Corre `python mediacat.py scan` "
            "para (re)construir o catálogo.")
