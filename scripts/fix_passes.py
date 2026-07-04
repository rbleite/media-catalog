#!/usr/bin/env python3
"""Maintenance passes: hide junk, fix HTML entities & dash-serials, re-enrich,
and recover Portuguese movie titles via TMDB pt-PT.

    PYTHONPATH=. python3 scripts/fix_passes.py
"""
import html, re, datetime
from media_catalog import catalog as C, titledb as T, config
from media_catalog.enrich import tmdb, igdb
conn = C.open_catalog(C.DEFAULT_CATALOG)
now = datetime.datetime.now().isoformat(timespec="seconds")

# ── PASS 2: esconder lixo (saves / installers / restos de tags) ──────────
junk_games = conn.execute("""UPDATE works SET hidden=1 WHERE type='game' AND (
    title LIKE '%SAVE0%' OR title LIKE '%AUTOSAVE%' OR title LIKE '%\\_SAV' ESCAPE '\\'
    OR title LIKE '%savedata%' OR upper(title) IN ('INST','NSS','INSTALL','DATA')
    OR length(trim(title))<3)""").rowcount
junk_movies = conn.execute("""UPDATE works SET hidden=1 WHERE type='movie' AND (
    title IN ('Sample','1080','720','480','2160','Sample','sample')
    OR (title GLOB '[0-9]*' AND title NOT GLOB '*[A-Za-z]*' AND length(title)<=4)
    OR title LIKE 'Sample %' OR lower(title) LIKE '%rarbg%')""").rowcount
conn.commit()
print(f"[2] escondidos: {junk_games} jogos + {junk_movies} filmes (lixo)")

# ── PASS 3a: decodificar entidades HTML nos títulos ──────────────────────
fixed_html = 0
for wid, title, typ, prov in conn.execute(
        "SELECT id,title,type,provider FROM works WHERE title LIKE '%&%'").fetchall():
    dec = html.unescape(html.unescape(title or ""))
    if dec != title:
        reset = " , enriched=0, provider=NULL" if (prov or "").endswith("-miss") else ""
        conn.execute(f"UPDATE works SET title=?{reset}, updated_at=? WHERE id=?",
                     (dec, now, wid))
        fixed_html += 1
conn.commit()
print(f"[3a] entidades HTML corrigidas: {fixed_html}")

# ── PASS 3b: serials com traço 'BLES-00174 - Nome' → limpar ──────────────
_SER = re.compile(r"^[A-Z]{4}[-_]?\d{4,5}\s*[-_]?\s*")
ps3map = T.load_map()
fixed_serial = 0
for wid, title, ident in conn.execute(
        "SELECT id,title,identifier FROM works WHERE type='game' AND title GLOB '[A-Z][A-Z][A-Z][A-Z]*[0-9][0-9][0-9][0-9]*'").fetchall():
    m = _SER.match(title or "")
    if not m:
        continue
    rest = title[m.end():].strip(" -_")
    serial = re.sub(r"[-_]", "", title[:m.end()]).strip(" -_").upper()
    if rest:                                   # já tem nome depois do serial
        new = rest
    elif serial in ps3map:                     # serial puro → GameTDB
        new = ps3map[serial]["title"]
    else:
        continue
    conn.execute("UPDATE works SET title=?, identifier=COALESCE(identifier,?),"
                 " enriched=0, provider=NULL, cover_path=NULL, updated_at=? WHERE id=?",
                 (new, serial, now, wid))
    fixed_serial += 1
conn.commit()
print(f"[3b] serials-com-traço limpos: {fixed_serial}")

# ── re-enriquecer os jogos que reset ──────────────────────────────────────
rg = igdb.enrich_games(conn, sleep=0.26)
print(f"[3c] IGDB re-enrich jogos: {rg}")

# ── PASS 1: re-pesquisar filmes falhados em pt-PT ────────────────────────
key = config.get("tmdb_api_key")
misses = conn.execute("SELECT id,title,year FROM works WHERE type='movie'"
                      " AND provider='tmdb-miss' AND COALESCE(hidden,0)=0").fetchall()
pt_matched = 0
for wid, title, year in misses:
    cands = tmdb.search_candidates(title, year, key, lang="pt-PT")
    if not cands:
        cands = tmdb.search_candidates(title, None, key, lang="pt-PT")
    for c in cands:
        if tmdb._acceptable(c, title, year) or (not year and cands.index(c) == 0
                                                and tmdb._norm(c.get("title")) == tmdb._norm(title)):
            tmdb.apply_candidate(conn, wid, c, key)
            pt_matched += 1
            break
    import time; time.sleep(0.03)
conn.commit()
print(f"[1] filmes PT recuperados: {pt_matched} de {len(misses)} falhados")

print("=== COBERTURA FINAL ===")
for typ, tot, cap in conn.execute(
        "SELECT type,count(*),SUM(cover_path IS NOT NULL) FROM works WHERE COALESCE(hidden,0)=0 GROUP BY type"):
    print(f"  {typ}: {cap}/{tot} ({100*cap//tot}%)")
print("DONE")
