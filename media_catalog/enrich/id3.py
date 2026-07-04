"""ID3 tag reader — pull Artist / Album / Year / Genre (and embedded cover art)
straight from the MP3 files, when the drive is mounted.

media-catalog is normally *index-only* (it reads the drive-xray db and works
fully offline). This is the one enrichment that touches the real files, so it is
an **opportunistic, opt-in pass**: it only enriches albums whose drive is
mounted right now, and silently skips the rest. Nothing here hits the network.

Pure standard library — no `mutagen`. Parses ID3v2.2/2.3/2.4 frames with an
ID3v1 fallback, reading only the file's head (for v2) and last 128 bytes (v1).
"""
from __future__ import annotations

import re
from pathlib import Path

from media_catalog import config

# Winamp / ID3v1 numeric genre table (0–147). TCON frames reference these by
# number, e.g. "(17)" → Rock.
GENRES = [
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient",
    "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "Alternative Rock", "Bass", "Soul", "Punk", "Space", "Meditative",
    "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave",
    "Techno-Industrial", "Electronic", "Pop-Folk", "Eurodance", "Dream",
    "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap",
    "Pop/Funk", "Jungle", "Native US", "Cabaret", "New Wave", "Psychadelic",
    "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal", "Acid Punk",
    "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll", "Hard Rock",
    "Folk", "Folk-Rock", "National Folk", "Swing", "Fast Fusion", "Bebob",
    "Latin", "Revival", "Celtic", "Bluegrass", "Avantgarde", "Gothic Rock",
    "Progressive Rock", "Psychedelic Rock", "Symphonic Rock", "Slow Rock",
    "Big Band", "Chorus", "Easy Listening", "Acoustic", "Humour", "Speech",
    "Chanson", "Opera", "Chamber Music", "Sonata", "Symphony", "Booty Bass",
    "Primus", "Porn Groove", "Satire", "Slow Jam", "Club", "Tango", "Samba",
    "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul", "Freestyle", "Duet",
    "Punk Rock", "Drum Solo", "A Cappella", "Euro-House", "Dance Hall",
    "Goa", "Drum & Bass", "Club-House", "Hardcore", "Terror", "Indie",
    "BritPop", "Negerpunk", "Polsk Punk", "Beat", "Christian Gangsta Rap",
    "Heavy Metal", "Black Metal", "Crossover", "Contemporary Christian",
    "Christian Rock", "Merengue", "Salsa", "Thrash Metal", "Anime", "Jpop",
    "Synthpop",
]

AUDIO_EXT = {".mp3"}


def _synchsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _plain(b: bytes) -> int:
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def _decode_text(data: bytes) -> str:
    """Decode an ID3v2 text-frame payload (leading byte = encoding)."""
    if not data:
        return ""
    enc, payload = data[0], data[1:]
    try:
        if enc == 0:
            s = payload.decode("latin-1")
        elif enc == 1:
            s = payload.decode("utf-16")        # honours the BOM
        elif enc == 2:
            s = payload.decode("utf-16-be")
        else:
            s = payload.decode("utf-8")
    except Exception:
        s = payload.decode("latin-1", "replace")
    return s.split("\x00")[0].strip()


def _genre_name(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    m = re.match(r"^\((\d+)\)(.*)$", raw)      # "(17)" or "(17)Refinement"
    if m:
        rest = m.group(2).strip()
        n = int(m.group(1))
        return rest or (GENRES[n] if n < len(GENRES) else "")
    if raw.isdigit():
        n = int(raw)
        return GENRES[n] if n < len(GENRES) else ""
    return raw


def _parse_v2(head_body: bytes) -> dict:
    """Return {frame_id: raw_bytes} for one ID3v2 tag (or {} if absent)."""
    if len(head_body) < 10 or head_body[:3] != b"ID3":
        return {}
    ver = head_body[3]
    size = _synchsafe(head_body[6:10])
    body = head_body[10:10 + size]
    frames: dict[str, bytes] = {}
    i = 0
    while i + 6 <= len(body):
        if ver >= 3:                            # v2.3 / v2.4: 4-char id, 4-byte size
            if i + 10 > len(body):
                break
            fid = body[i:i + 4]
            if fid == b"\x00\x00\x00\x00":
                break
            fsize = _synchsafe(body[i + 4:i + 8]) if ver == 4 else _plain(body[i + 4:i + 8])
            i += 10
        else:                                   # v2.2: 3-char id, 3-byte size
            fid = body[i:i + 3]
            if fid == b"\x00\x00\x00":
                break
            fsize = (body[i + 3] << 16) | (body[i + 4] << 8) | body[i + 5]
            i += 6
        if fsize <= 0 or i + fsize > len(body):
            break
        frames[fid.decode("latin-1", "replace")] = body[i:i + fsize]
        i += fsize
    return {"ver": ver, "frames": frames}


def _read_v1(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            f.seek(-128, 2)
            tag = f.read(128)
    except Exception:
        return {}
    if len(tag) < 128 or tag[:3] != b"TAG":
        return {}

    def s(b: bytes) -> str:
        return b.rstrip(b"\x00 ").decode("latin-1", "replace").strip()

    g = tag[127]
    return {
        "artist": s(tag[33:63]),
        "album": s(tag[63:93]),
        "year": s(tag[93:97]),
        "genre": GENRES[g] if g < len(GENRES) else "",
    }


def read_tags(path: Path, head_bytes: int = 1 << 18) -> dict:
    """Read {artist, album, year, genre} from one MP3, ID3v2 first, v1 fallback.
    Only present, non-empty fields are returned."""
    out: dict = {}
    try:
        with open(path, "rb") as f:
            v2 = _parse_v2(f.read(head_bytes))
    except Exception:
        v2 = {}
    if v2:
        fr, ver = v2["frames"], v2["ver"]

        def g(*ids: str) -> str:
            for k in ids:
                if k in fr:
                    t = _decode_text(fr[k])
                    if t:
                        return t
            return ""

        out["artist"] = g("TPE1", "TP1")
        out["album"] = g("TALB", "TAL")
        yr = g("TDRC", "TYER", "TYE", "TDAT", "TORY")
        ym = re.search(r"\d{4}", yr)
        out["year"] = ym.group() if ym else ""
        out["genre"] = _genre_name(g("TCON", "TCO"))

    if not all(out.get(k) for k in ("artist", "album", "year", "genre")):
        for k, v in _read_v1(path).items():
            if v and not out.get(k):
                if k == "year":
                    ym = re.search(r"\d{4}", v)
                    out[k] = ym.group() if ym else out.get(k, "")
                else:
                    out[k] = v
    return {k: v for k, v in out.items() if v}


def extract_cover(path: Path, work_id: int, head_bytes: int = 1 << 21) -> str | None:
    """Pull embedded album art (APIC / PIC frame) into covers/. Returns the path
    written, or None. Reads a larger head since art usually trails the text
    frames."""
    try:
        with open(path, "rb") as f:
            v2 = _parse_v2(f.read(head_bytes))
    except Exception:
        return None
    if not v2:
        return None
    fr, ver = v2["frames"], v2["ver"]
    raw = fr.get("APIC") or fr.get("PIC")
    if not raw or len(raw) < 32:
        return None
    try:
        # APIC: enc(1) mime(\0-term) type(1) desc(\0-term, enc) picture-data
        i = 1
        if ver >= 3:                            # mime is a latin-1 \0-terminated string
            j = raw.index(b"\x00", i)
            mime = raw[i:j].decode("latin-1", "replace").lower()
            i = j + 1
        else:                                   # v2.2 PIC: 3-char image format
            mime = raw[i:i + 3].decode("latin-1", "replace").lower()
            i += 3
        i += 1                                  # picture type byte
        enc = raw[0]
        term = b"\x00\x00" if enc in (1, 2) else b"\x00"
        di = raw.find(term, i)
        i = (di + len(term)) if di != -1 else i
        data = raw[i:]
        if len(data) < 500:
            return None
        ext = "png" if "png" in mime else "jpg"
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        dest = config.COVERS_DIR / f"album_{work_id}.{ext}"
        dest.write_bytes(data)
        return str(dest)
    except Exception:
        return None


def _first_audio(folder: Path) -> Path | None:
    """A representative MP3 in the album folder (shallow, then one level down for
    multi-disc CD1/CD2 layouts)."""
    try:
        entries = sorted(folder.iterdir())
    except Exception:
        return None
    for p in entries:
        if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("._"):
            return p
    for p in entries:
        if p.is_dir():
            hit = _first_audio(p)
            if hit:
                return hit
    return None


def enrich_id3(conn, roots_by_label: dict, force: bool = False,
               progress=None) -> dict:
    """For each album work whose drive is mounted, read ID3 tags off a real MP3
    and fill artist / year / genre (and the album title + cover when missing).

    roots_by_label: {drive_label: absolute_root_path} for drives online now.
    Rows with manual=1 (user-corrected) are never touched. By default only empty
    fields are filled; force=True overwrites all four fields."""
    import datetime
    rows = conn.execute(
        "SELECT id, rel_path, drive_label, title, artist, year, genre,"
        " cover_path, COALESCE(manual,0) FROM works WHERE type='album'").fetchall()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    updated = covers = skipped_offline = 0
    total = len(rows)
    for i, (wid, rel, label, title, artist, year, genre,
            cover, manual) in enumerate(rows):
        root = roots_by_label.get(label)
        if not root:
            skipped_offline += 1
            continue
        if manual:
            continue
        folder = Path(root) / rel.replace("\\", "/")
        if not folder.is_dir():
            skipped_offline += 1
            continue
        mp3 = _first_audio(folder)
        if not mp3:
            continue
        tags = read_tags(mp3)
        sets, vals = [], []
        if tags.get("artist") and (force or not artist):
            sets.append("artist=?"); vals.append(tags["artist"])
        if tags.get("year") and (force or not year):
            try:
                sets.append("year=?"); vals.append(int(tags["year"]))
            except ValueError:
                pass
        if tags.get("genre") and (force or not genre):
            sets.append("genre=?"); vals.append(tags["genre"])
        if tags.get("album") and (force or not title):
            sets.append("title=?"); vals.append(tags["album"])
        if not cover:
            art = extract_cover(mp3, wid)
            if art:
                sets.append("cover_path=?"); vals.append(art)
                covers += 1
        if sets:
            sets.append("provider=COALESCE(provider,'id3')")
            sets.append("updated_at=?"); vals.append(now)
            vals.append(wid)
            conn.execute(f"UPDATE works SET {', '.join(sets)} WHERE id=?", vals)
            updated += 1
        if i % 25 == 0:
            conn.commit()
            if progress:
                progress(i + 1, total, updated, covers)
    conn.commit()
    if progress:
        progress(total, total, updated, covers)
    return {"updated": updated, "covers": covers,
            "skipped_offline": skipped_offline, "total": total}
