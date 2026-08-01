"""Container tag reading, cross-validated against independent implementations.

The MP4 atom walker and the EBML walker are hand-written binary parsers, so
testing them against fixtures I also hand-wrote would only prove my reader
agrees with my writer. Instead:

  * MP4  — `mutagen` (an independent, widely-used library) WRITES the tags and
           this module reads them back. If my understanding of the atom layout
           were wrong, the values would not come out.
  * MKV  — `ebmlite` (an independent EBML implementation) parses the fixture
           first and must report the expected elements; only then is the same
           file handed to this module. That proves the fixture really is valid
           Matroska rather than merely something my own reader accepts.

Both are test-only dependencies. The module under test stays standard library.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from media_catalog.enrich.container import (
    read_mkv_tags, read_mp4_tags, read_video_tags,
)

mutagen_mp4 = pytest.importorskip("mutagen.mp4", reason="test-only oracle")


# ---------------------------------------------------------------- fixtures

def _box(t: str, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + t.encode("latin-1") + payload


def _minimal_mp4() -> bytes:
    """The smallest tree mutagen will open: ftyp + moov(mvhd + one trak)."""
    mvhd = _box("mvhd", b"\x00" * 4 + b"\x00" * 8
                + struct.pack(">II", 1000, 0) + b"\x00" * 80)
    stbl = _box("stbl", _box("stsd", b"\x00" * 4 + struct.pack(">I", 0))
                + _box("stts", b"\x00" * 8) + _box("stsc", b"\x00" * 8)
                + _box("stsz", b"\x00" * 12) + _box("stco", b"\x00" * 8))
    minf = _box("minf", _box("vmhd", b"\x00" * 12)
                + _box("dinf", _box("dref", b"\x00" * 8)) + stbl)
    mdia = _box("mdia", _box("mdhd", b"\x00" * 4 + b"\x00" * 8
                             + struct.pack(">II", 1000, 0) + b"\x00" * 4)
                + _box("hdlr", b"\x00" * 8 + b"vide" + b"\x00" * 13) + minf)
    trak = _box("trak", _box("tkhd", b"\x00" * 4 + b"\x00" * 76) + mdia)
    return (_box("ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
            + _box("moov", mvhd + trak))


def _esize(n: int) -> bytes:
    for length in range(1, 9):
        if n < (1 << (7 * length)) - 1:
            return (n | (1 << (7 * length))).to_bytes(length, "big")
    raise ValueError(n)


def _el(eid: str, payload: bytes) -> bytes:
    return bytes.fromhex(eid) + _esize(len(payload)) + payload


def _simple_tag(name: str, value: str) -> bytes:
    return _el("67C8", _el("45A3", name.encode()) + _el("4487", value.encode()))


@pytest.fixture()
def tagged_mp4(tmp_path) -> Path:
    """An MP4 whose tags were written by mutagen, not by this test."""
    p = tmp_path / "episode.mp4"
    p.write_bytes(_minimal_mp4())
    f = mutagen_mp4.MP4(str(p))
    f["\xa9nam"] = ["O Sopro do Norte"]
    f["tvsh"] = ["Os Maias"]
    f["tvsn"] = [2]
    f["tves"] = [5]
    f["stik"] = [10]                       # 10 = TV show
    f["\xa9day"] = ["2001"]
    f.save()
    return p


@pytest.fixture()
def tagged_mkv(tmp_path) -> Path:
    p = tmp_path / "episode.mkv"
    header = _el("1A45DFA3",
                 _el("4286", b"\x01") + _el("42F7", b"\x01")
                 + _el("4282", b"matroska") + _el("4287", b"\x04")
                 + _el("4285", b"\x02"))
    info = _el("1549A966", _el("7BA9", "Os Maias - O Sopro do Norte".encode()))
    tags = _el("1254C367", _el("7373",
                               _simple_tag("SHOW", "Os Maias")
                               + _simple_tag("SEASON", "2")
                               + _simple_tag("EPISODE", "5")
                               + _simple_tag("DATE_RELEASED", "2001")))
    p.write_bytes(header + _el("18538067", info + tags))
    return p


# ------------------------------------------------------- cross-validation

def test_mp4_tags_written_by_mutagen_are_read_back(tagged_mp4):
    """mutagen produced these bytes; agreement is real evidence, not a tautology."""
    assert read_mp4_tags(tagged_mp4) == {
        "show": "Os Maias",
        "title": "O Sopro do Norte",
        "year": 2001,
        "season": 2,
        "episode": 5,
        "kind": "series",
    }


def test_mp4_movie_kind_is_distinguished_from_tv(tmp_path):
    p = tmp_path / "film.m4v"
    p.write_bytes(_minimal_mp4())
    f = mutagen_mp4.MP4(str(p))
    f["\xa9nam"] = ["Blade Runner"]
    f["stik"] = [9]                        # 9 = movie
    f["\xa9day"] = ["1982"]
    f.save()
    tags = read_video_tags(p)
    assert tags["kind"] == "movie"
    assert tags["title"] == "Blade Runner" and tags["year"] == 1982
    assert "season" not in tags and "episode" not in tags


def test_mkv_fixture_is_valid_matroska_by_an_independent_reader(tagged_mkv):
    """Prove the fixture before trusting what we read out of it."""
    ebmlite = pytest.importorskip("ebmlite", reason="test-only oracle")
    doc = ebmlite.loadSchema("matroska.xml").load(str(tagged_mkv))
    seen: dict[str, str] = {}

    def walk(node):
        for child in node:
            if hasattr(child, "__iter__"):
                walk(child)
            else:
                seen.setdefault(child.name, str(child.value))

    walk(doc)
    assert seen.get("Title") == "Os Maias - O Sopro do Norte"
    assert seen.get("TagName") == "SHOW"


def test_mkv_tags_are_read(tagged_mkv):
    assert read_mkv_tags(tagged_mkv) == {
        "title": "Os Maias - O Sopro do Norte",
        "show": "Os Maias",
        "season": 2,
        "episode": 5,
        "year": 2001,
        "kind": "series",
    }


def test_dispatch_is_by_extension(tagged_mp4, tagged_mkv, tmp_path):
    assert read_video_tags(tagged_mp4)["show"] == "Os Maias"
    assert read_video_tags(tagged_mkv)["show"] == "Os Maias"
    avi = tmp_path / "clip.avi"                 # no reader for this container
    avi.write_bytes(b"RIFF____AVI ")
    assert read_video_tags(avi) == {}


# ------------------------------------------------------------- robustness

@pytest.mark.parametrize("name,blob", [
    ("empty.mp4", b""),
    ("garbage.mp4", b"\xff" * 512),
    ("garbage.mkv", b"\xff" * 512),
    ("zeros.mkv", b"\x00" * 512),
    # a box declaring a size far beyond the file: must not be trusted
    ("liar.mp4", struct.pack(">I", 0xFFFFFF00) + b"moov" + b"\x00" * 64),
    # an EBML element claiming an enormous payload
    ("liar.mkv", bytes.fromhex("18538067") + b"\x08" + b"\xff" * 7 + b"\x00" * 64),
    ("truncated.mp4", _minimal_mp4()[:40]),
])
def test_malformed_files_yield_nothing_and_do_not_hang(tmp_path, name, blob):
    """Sizes come from the file, so a corrupt or hostile one must not be able
    to drive a huge allocation or an unbounded walk."""
    p = tmp_path / name
    p.write_bytes(blob)
    assert read_video_tags(p) == {}


def test_untagged_file_is_simply_empty(tmp_path):
    """The common case for scene releases: a valid container, no metadata."""
    p = tmp_path / "Breaking.Bad.S01E01.mp4"
    p.write_bytes(_minimal_mp4())
    assert read_video_tags(p) == {}


def test_missing_file_is_not_an_error(tmp_path):
    assert read_video_tags(tmp_path / "gone.mkv") == {}


# ---------------------------------------------------------- enrichment pass

def _catalog_with(tmp_path, works):
    from media_catalog import catalog
    conn = catalog.open_catalog(tmp_path / "catalog.db")
    for w in works:
        catalog.upsert_work(conn, w)
    conn.commit()
    return conn


def test_enrichment_fills_title_and_year_from_the_file(tmp_path, tagged_mp4):
    """End to end: a mounted drive, a real tagged file, the work updated."""
    import json

    from media_catalog.enrich.container import enrich_container

    root = tmp_path / "drive"
    (root / "Series" / "show").mkdir(parents=True)
    (root / "Movies").mkdir(parents=True)
    ep = root / "Series" / "show" / "ep.mp4"
    ep.write_bytes(tagged_mp4.read_bytes())
    film = root / "Movies" / "film.mp4"
    film.write_bytes(tagged_mp4.read_bytes())

    conn = _catalog_with(tmp_path, [
        # title empty: the tags are allowed to fill it
        {"type": "series", "title": "", "title_raw": "", "rel_path": "series/x",
         "drive_label": "D", "extra_json": json.dumps(
             {"sample_path": "Series/show/ep.mp4"})},
        {"type": "movie", "title": "", "title_raw": "",
         "rel_path": "Movies/film.mp4", "drive_label": "D"},
    ])
    stats = enrich_container(conn, {"D": str(root)})
    assert stats["tagged"] == 2 and stats["updated"] == 2

    rows = dict(conn.execute("SELECT type, title FROM works").fetchall())
    # a series takes the SHOW name, never the episode's own title
    assert rows["series"] == "Os Maias"
    # a movie takes the title
    assert rows["movie"] == "O Sopro do Norte"
    years = {t: y for t, y in conn.execute("SELECT type, year FROM works")}
    assert years["series"] == 2001 and years["movie"] == 2001


def test_enrichment_never_overwrites_a_user_correction(tmp_path, tagged_mp4):
    from media_catalog.enrich.container import enrich_container

    root = tmp_path / "drive"
    (root / "Movies").mkdir(parents=True)
    (root / "Movies" / "film.mp4").write_bytes(tagged_mp4.read_bytes())

    conn = _catalog_with(tmp_path, [
        {"type": "movie", "title": "O meu titulo", "title_raw": "x",
         "rel_path": "Movies/film.mp4", "drive_label": "D"},
    ])
    # `manual` is set by the app when the user corrects a title — upsert_work
    # deliberately never writes it, so that a re-scan cannot clear the flag.
    conn.execute("UPDATE works SET manual=1")
    conn.commit()

    enrich_container(conn, {"D": str(root)}, force=True)
    assert conn.execute("SELECT title FROM works").fetchone()[0] == "O meu titulo"


def test_offline_drives_are_skipped_not_failed(tmp_path):
    from media_catalog.enrich.container import enrich_container

    conn = _catalog_with(tmp_path, [
        {"type": "movie", "title": "", "title_raw": "x",
         "rel_path": "Movies/film.mp4", "drive_label": "OFFLINE"},
    ])
    stats = enrich_container(conn, {})          # nothing mounted
    assert stats["skipped_offline"] == 1 and stats["updated"] == 0


def test_a_kind_mismatch_is_reported_not_applied(tmp_path, tmp_path_factory):
    """stik says movie but we catalogued a series: counted, never moved."""
    from media_catalog.enrich.container import enrich_container

    root = tmp_path / "drive"
    (root / "Movies").mkdir(parents=True)
    p = root / "Movies" / "film.mp4"
    p.write_bytes(_minimal_mp4())
    f = mutagen_mp4.MP4(str(p))
    f["\xa9nam"] = ["Blade Runner"]
    f["stik"] = [9]                             # movie
    f.save()

    import json
    conn = _catalog_with(tmp_path, [
        {"type": "series", "title": "", "title_raw": "x",
         "rel_path": "series/y", "drive_label": "D",
         "extra_json": json.dumps({"sample_path": "Movies/film.mp4"})},
    ])
    stats = enrich_container(conn, {"D": str(root)})
    assert stats["kind_mismatch"] == 1
    assert conn.execute("SELECT type FROM works").fetchone()[0] == "series"


# ------------------------------------------------------------- CLI wiring

def test_cli_tags_subcommand_reaches_the_enricher(tmp_path, tagged_mp4, monkeypatch):
    """The reader and the pass are useless if nothing invokes them: this drives
    the real `mediacat.py tags` entry point end to end."""
    import importlib.util
    import json
    import sys

    from media_catalog import catalog

    root = tmp_path / "drive"
    (root / "Movies").mkdir(parents=True)
    (root / "Movies" / "film.mp4").write_bytes(tagged_mp4.read_bytes())

    db = tmp_path / "catalog.db"
    conn = catalog.open_catalog(db)
    catalog.upsert_work(conn, {"type": "movie", "title": "", "title_raw": "x",
                               "rel_path": "Movies/film.mp4", "drive_label": "D"})
    conn.commit()
    conn.close()

    spec = importlib.util.spec_from_file_location(
        "mediacat_cli", Path(__file__).resolve().parent.parent / "mediacat.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    # only the mounted-drive lookup is faked; everything below it is real
    monkeypatch.setattr(cli.D, "drive_roots", lambda: {"D": str(root)})
    monkeypatch.setattr(sys, "argv", ["mediacat", "--catalog", str(db), "tags"])
    cli.main()

    conn = catalog.open_catalog(db)
    title = conn.execute("SELECT title FROM works").fetchone()[0]
    assert title == "O Sopro do Norte", "the CLI did not reach the tag reader"


def test_cli_registers_tags_alongside_the_other_passes():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mediacat_cli2", Path(__file__).resolve().parent.parent / "mediacat.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    import contextlib
    import io
    import sys
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        sys.argv = ["mediacat", "--help"]
        cli.main()
    assert "tags" in buf.getvalue()
