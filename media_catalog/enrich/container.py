"""Video container tag reader — pull show / season / episode / title straight
from the file, when the drive is mounted.

media-catalog is normally index-only: everything it knows comes from parsing
file *names*, which is guesswork. A name says 'Os.Maias.2x05' and we infer; a
container tag says season 2, episode 5 and there is nothing to infer. This is
the second enrichment that touches real files, and follows `id3.py`: an
opportunistic, opt-in pass over works whose drive is mounted right now,
skipping the rest silently. Nothing here hits the network.

Pure standard library — no `mutagen`, no `ffprobe`.

  * MP4 / M4V / MOV — the iTunes-style atoms under moov/udta/meta/ilst:
    stik (media kind), tvsh (show), tvsn (season), tves (episode),
    (c)nam (title), (c)day (year). This is where bought and properly muxed
    files carry real metadata.
  * MKV / WEBM — the EBML Segment>Info>Title element and Segment>Tags
    SimpleTags (TITLE / SEASON / PART_NUMBER / EPISODE).

Honest limitation: most scene .mkv releases carry no tags at all. For those the
file name remains the only signal and the name parser still rules. This adds
certainty where certainty exists; it does not manufacture it.

Everything is bounded: sizes come from the file itself, so every read is capped
and every walk is depth- and iteration-limited. A malformed or hostile file
yields {} rather than a huge allocation.
"""
from __future__ import annotations

import struct
from pathlib import Path

# Reading is capped well below any real header. Metadata lives at the very
# start or the very end of a container, never spread through the payload.
_MAX_BOX = 8 * 1024 * 1024        # largest single box payload we will read
_MAX_ITER = 4096                  # boxes/elements visited before giving up
_MAX_DEPTH = 8

MP4_EXT = {"mp4", "m4v", "mov"}
MKV_EXT = {"mkv", "webm"}
VIDEO_TAG_EXT = MP4_EXT | MKV_EXT

# stik (media kind). 10 is the only one that positively means "TV episode";
# 0 and 9 mean movie. Anything else tells us nothing useful.
_STIK_TV = 10
_STIK_MOVIE = {0, 9}


# --------------------------------------------------------------- MP4 / M4V

def _mp4_boxes(fh, end: int, depth: int = 0):
    """Yield (type, payload_start, payload_end) for boxes in [pos, end)."""
    if depth > _MAX_DEPTH:
        return
    seen = 0
    while fh.tell() + 8 <= end and seen < _MAX_ITER:
        seen += 1
        start = fh.tell()
        head = fh.read(8)
        if len(head) < 8:
            return
        size = struct.unpack(">I", head[:4])[0]
        btype = head[4:8].decode("latin-1")
        if size == 1:                      # 64-bit extended size
            ext = fh.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            body = fh.tell()
        elif size == 0:                    # runs to the end of the file
            body, size = fh.tell(), end - start
        else:
            body = fh.tell()
        stop = start + size
        if size < 8 or stop > end:         # malformed — stop, do not guess
            return
        yield btype, body, stop
        fh.seek(stop)


def _mp4_ilst(fh, start: int, end: int) -> dict:
    """Read the ilst item list into {atom_type: raw payload}."""
    out: dict[str, bytes] = {}
    fh.seek(start)
    for btype, body, stop in _mp4_boxes(fh, end, depth=1):
        fh.seek(body)
        for inner, dbody, dstop in _mp4_boxes(fh, stop, depth=2):
            if inner != "data" or dstop - dbody > _MAX_BOX:
                continue
            fh.seek(dbody)
            blob = fh.read(dstop - dbody)
            if len(blob) >= 8:
                out[btype] = blob            # [4B version/flags][4B locale][payload]
            break
        fh.seek(stop)
    return out


def _find_mp4_ilst(fh, size: int):
    """Walk moov > udta > meta > ilst, returning its (start, end) or None."""
    fh.seek(0)
    for btype, body, stop in _mp4_boxes(fh, size):
        if btype != "moov":
            continue
        fh.seek(body)
        for b2, body2, stop2 in _mp4_boxes(fh, stop, depth=1):
            if b2 != "udta":
                continue
            fh.seek(body2)
            for b3, body3, stop3 in _mp4_boxes(fh, stop2, depth=2):
                if b3 != "meta":
                    continue
                # `meta` is a FullBox: 4 bytes of version/flags before children
                fh.seek(body3 + 4)
                for b4, body4, stop4 in _mp4_boxes(fh, stop3, depth=3):
                    if b4 == "ilst":
                        return body4, stop4
                fh.seek(stop3)
            fh.seek(stop2)
    return None


def _atom_text(blob: bytes) -> str:
    return blob[8:].decode("utf-8", "replace").strip("\x00").strip()


def _atom_int(blob: bytes) -> int | None:
    payload = blob[8:]
    if not payload or len(payload) > 8:
        return None
    return int.from_bytes(payload, "big")


def read_mp4_tags(path: Path) -> dict:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            found = _find_mp4_ilst(fh, size)
            if not found:
                return {}
            items = _mp4_ilst(fh, *found)
    except (OSError, ValueError, struct.error):
        return {}

    tags: dict = {}
    if b := items.get("tvsh"):
        tags["show"] = _atom_text(b)
    if b := items.get("\xa9nam"):
        tags["title"] = _atom_text(b)
    if b := items.get("\xa9day"):
        year = _atom_text(b)[:4]
        if year.isdigit():
            tags["year"] = int(year)
    for atom, key in (("tvsn", "season"), ("tves", "episode")):
        if b := items.get(atom):
            # season/episode are stored as binary ints, but some muxers write
            # them as text — accept either rather than dropping the value
            v = _atom_int(b)
            if v is None:
                txt = _atom_text(b)
                v = int(txt) if txt.isdigit() else None
            if v is not None:
                tags[key] = v
    if b := items.get("stik"):
        kind = _atom_int(b)
        if kind == _STIK_TV:
            tags["kind"] = "series"
        elif kind in _STIK_MOVIE:
            tags["kind"] = "movie"
    # a show name with a season/episode is a series even without stik
    if "kind" not in tags and tags.get("show"):
        tags["kind"] = "series"
    return {k: v for k, v in tags.items() if v not in ("", None)}


# --------------------------------------------------------------- MKV / WEBM

_EBML_SEGMENT = 0x18538067
_EBML_INFO = 0x1549A966
_EBML_TITLE = 0x7BA9
_EBML_TAGS = 0x1254C367
_EBML_TAG = 0x7373
_EBML_SIMPLE = 0x67C8
_EBML_TAGNAME = 0x45A3
_EBML_TAGSTRING = 0x4487
# containers we descend into; everything else is skipped wholesale
_EBML_PARENTS = {_EBML_SEGMENT, _EBML_INFO, _EBML_TAGS, _EBML_TAG, _EBML_SIMPLE}


def _vint(fh, keep_marker: bool):
    """Read an EBML variable-length integer. IDs keep the length marker, sizes
    strip it. Returns (value, ok)."""
    first = fh.read(1)
    if not first:
        return 0, False
    b = first[0]
    if b == 0:
        return 0, False
    length = 8 - b.bit_length() + 1
    rest = fh.read(length - 1)
    if len(rest) != length - 1:
        return 0, False
    if keep_marker:
        return int.from_bytes(first + rest, "big"), True
    val = b & ((1 << (8 - length)) - 1)
    for c in rest:
        val = (val << 8) | c
    return val, True


def _ebml_walk(fh, end: int, depth: int = 0):
    """Yield (element_id, payload_start, payload_end), descending into the
    container elements we care about."""
    if depth > _MAX_DEPTH:
        return
    seen = 0
    while fh.tell() < end and seen < _MAX_ITER:
        seen += 1
        eid, ok = _vint(fh, keep_marker=True)
        if not ok:
            return
        size, ok = _vint(fh, keep_marker=False)
        if not ok:
            return
        body = fh.tell()
        # unknown-size elements (all bits set) run to the end of the parent
        stop = end if size >= (1 << 56) - 1 else body + size
        if stop > end or stop < body:
            return
        yield eid, body, stop
        if eid in _EBML_PARENTS:
            fh.seek(body)
            yield from _ebml_walk(fh, stop, depth + 1)
        fh.seek(stop)


def read_mkv_tags(path: Path) -> dict:
    tags: dict = {}
    simple: dict[str, str] = {}
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            pending_name = None
            for eid, body, stop in _ebml_walk(fh, size):
                span = stop - body
                if span > _MAX_BOX:
                    continue
                if eid == _EBML_TITLE:
                    fh.seek(body)
                    tags["title"] = fh.read(span).decode(
                        "utf-8", "replace").strip("\x00").strip()
                elif eid == _EBML_TAGNAME:
                    fh.seek(body)
                    pending_name = fh.read(span).decode(
                        "utf-8", "replace").strip("\x00").strip().upper()
                elif eid == _EBML_TAGSTRING and pending_name:
                    fh.seek(body)
                    simple[pending_name] = fh.read(span).decode(
                        "utf-8", "replace").strip("\x00").strip()
                    pending_name = None
    except (OSError, ValueError, struct.error):
        return {}

    if v := simple.get("TITLE"):
        tags.setdefault("title", v)
    for key, names in (("season", ("SEASON", "PART_NUMBER", "SEASON_NUMBER")),
                       ("episode", ("EPISODE", "EPISODE_NUMBER", "PART"))):
        for n in names:
            v = simple.get(n, "")
            if v.isdigit():
                tags[key] = int(v)
                break
    if v := simple.get("DATE_RELEASED", simple.get("DATE", "")):
        if v[:4].isdigit():
            tags["year"] = int(v[:4])
    if v := simple.get("SHOW", simple.get("COLLECTION", "")):
        tags["show"] = v
    if tags.get("show") or ("season" in tags and "episode" in tags):
        tags["kind"] = "series"
    return {k: v for k, v in tags.items() if v not in ("", None)}


# --------------------------------------------------------------- public API

def read_video_tags(path: Path) -> dict:
    """Embedded tags for one video file, or {} when it has none we can read.

    Keys, all optional: kind ('series'|'movie'), show, season, episode,
    title, year.
    """
    ext = path.suffix.lstrip(".").lower()
    if ext in MP4_EXT:
        return read_mp4_tags(path)
    if ext in MKV_EXT:
        return read_mkv_tags(path)
    return {}


# --------------------------------------------------------------- enrichment

def _first_video(folder: Path) -> Path | None:
    """A video file to read tags from — the folder itself, or the largest file
    inside it (the feature, not a sample or trailer). Only one level deep."""
    try:
        if folder.is_file():
            return folder if folder.suffix.lstrip(".").lower() in VIDEO_TAG_EXT \
                else None
        cands = [f for f in folder.iterdir()
                 if f.is_file() and f.suffix.lstrip(".").lower() in VIDEO_TAG_EXT]
    except OSError:
        return None
    return max(cands, key=lambda f: f.stat().st_size) if cands else None


def enrich_container(conn, roots_by_label: dict, force: bool = False,
                     progress=None) -> dict:
    """Fill title / year for movies and series from tags embedded in the file,
    for works whose drive is mounted right now.

    roots_by_label: {drive_label: absolute_root_path} for drives online now.
    Rows with manual=1 (user-corrected) are never touched. By default only empty
    fields are filled; force=True overwrites from the tags.

    A tag disagreeing with how the work was classified (stik says movie, we
    catalogued a series) is *counted and returned*, never applied — moving a
    work between galleries is the user's call, not a side effect of a scan.
    """
    import datetime
    import json as _json
    rows = conn.execute(
        "SELECT id, type, rel_path, drive_label, title, year, extra_json,"
        " COALESCE(manual,0) FROM works WHERE type IN ('movie','series')"
    ).fetchall()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    updated = tagged = kind_mismatch = skipped_offline = no_tags = 0
    total = len(rows)

    for i, (wid, wtype, rel, label, title, year, extra, manual) in enumerate(rows):
        root = roots_by_label.get(label)
        if not root:
            skipped_offline += 1
            continue
        if manual:
            continue
        # A series' rel_path is a synthetic key, so discovery records a real
        # episode in extra_json; movies point at the file or folder directly.
        target = None
        if wtype == "series":
            try:
                sample = (_json.loads(extra or "{}") or {}).get("sample_path")
            except ValueError:
                sample = None
            if sample:
                target = Path(root) / sample.replace("\\", "/")
        else:
            target = Path(root) / rel.replace("\\", "/")
        if target is None:
            continue
        video = _first_video(target)
        if video is None:
            skipped_offline += 1
            continue

        tags = read_video_tags(video)
        if not tags:
            no_tags += 1
            continue
        tagged += 1
        if tags.get("kind") and tags["kind"] != wtype:
            kind_mismatch += 1

        # For a series the show name is the work's title; the episode title is
        # about one file and must not overwrite it.
        new_title = tags.get("show") if wtype == "series" else tags.get("title")
        sets, vals = [], []
        if new_title and (force or not title):
            sets.append("title=?"); vals.append(new_title)
        if tags.get("year") and (force or not year):
            sets.append("year=?"); vals.append(int(tags["year"]))
        if sets:
            sets.append("provider=COALESCE(provider,'container')")
            sets.append("updated_at=?"); vals.append(now)
            vals.append(wid)
            conn.execute(f"UPDATE works SET {', '.join(sets)} WHERE id=?", vals)
            updated += 1
        if i % 25 == 0:
            conn.commit()
            if progress:
                progress(i + 1, total, updated, tagged)
    conn.commit()
    if progress:
        progress(total, total, updated, tagged)
    return {"updated": updated, "tagged": tagged, "no_tags": no_tags,
            "kind_mismatch": kind_mismatch, "skipped_offline": skipped_offline,
            "total": total}
