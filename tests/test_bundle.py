"""Portable enrichment bundles.

Enrichment costs API keys and time. The point of a bundle is that a second
machine gets the whole result -- metadata *and* the cover images -- without
TMDB or IGDB accounts of its own, without a drive-xray index, and without any
cloud folder in between. So the tests here simulate exactly that: export on one
machine, import on another that has nothing.
"""
from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from media_catalog import catalog


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)      # enough to be a real file


@pytest.fixture()
def machine_a(tmp_path):
    """A catalogue that has been enriched, with covers on disk."""
    covers = tmp_path / "a-covers"
    covers.mkdir()
    for name in ("blade.jpg", "maias.jpg"):
        (covers / name).write_bytes(PNG)

    conn = catalog.open_catalog(tmp_path / "a.db")
    catalog.upsert_work(conn, {
        "type": "movie", "title": "Blade Runner", "title_raw": "Blade.Runner.1982",
        "year": 1982, "rel_path": "Movies/blade", "drive_label": "8Tb"})
    catalog.upsert_work(conn, {
        "type": "series", "title": "Os Maias", "title_raw": "Os Maias",
        "rel_path": "series/osmaias", "drive_label": "8Tb"})
    conn.execute("UPDATE works SET enriched=1, genre='Sci-Fi', provider='tmdb',"
                 " cover_path=? WHERE title='Blade Runner'",
                 (str(covers / "blade.jpg"),))
    conn.execute("UPDATE works SET enriched=1, cover_path=?, status='watched'"
                 " WHERE title='Os Maias'", (str(covers / "maias.jpg"),))
    conn.commit()
    return conn, covers


def test_bundle_carries_metadata_and_the_actual_images(machine_a, tmp_path):
    conn, covers = machine_a
    out = tmp_path / "bundle.zip"
    res = catalog.export_bundle(conn, out, covers_dir=covers)

    assert res["works"] == 2 and res["covers"] == 2
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"manifest.json", "works.json",
                "covers/blade.jpg", "covers/maias.jpg"} <= names
        # the images are really in there, not just referenced
        assert z.read("covers/blade.jpg") == PNG


def test_a_bundle_never_carries_api_keys(machine_a, tmp_path):
    """It is meant to be copied around; credentials must not travel with it."""
    conn, covers = machine_a
    out = tmp_path / "bundle.zip"
    catalog.export_bundle(conn, out, covers_dir=covers)
    with zipfile.ZipFile(out) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
        assert not any(n.endswith("secrets.json") for n in z.namelist())
        for leak in (b"tmdb_api_key", b"igdb_client_secret", b"api_key"):
            assert leak not in blob


def test_a_machine_with_nothing_can_browse_after_importing(machine_a, tmp_path):
    """The whole point: no index, no keys, no cloud -- just the bundle."""
    conn_a, covers_a = machine_a
    out = tmp_path / "bundle.zip"
    catalog.export_bundle(conn_a, out, covers_dir=covers_a)

    covers_b = tmp_path / "b-covers"
    conn_b = catalog.open_catalog(tmp_path / "b.db")       # empty catalogue
    assert conn_b.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 0

    res = catalog.import_bundle(conn_b, out, covers_dir=covers_b)
    assert res["created"] == 2 and res["updated"] == 0 and res["covers"] == 2

    rows = dict(conn_b.execute("SELECT title, genre FROM works").fetchall())
    assert rows["Blade Runner"] == "Sci-Fi"
    assert "Os Maias" in rows
    # the cover file itself arrived, and points somewhere real
    cover = conn_b.execute(
        "SELECT cover_path FROM works WHERE title='Blade Runner'").fetchone()[0]
    assert Path(cover).is_file() and Path(cover).read_bytes() == PNG
    # watched/wishlist status travels too
    assert conn_b.execute(
        "SELECT status FROM works WHERE title='Os Maias'").fetchone()[0] == "watched"


def test_importing_updates_titles_this_machine_already_scanned(machine_a, tmp_path):
    conn_a, covers_a = machine_a
    out = tmp_path / "bundle.zip"
    catalog.export_bundle(conn_a, out, covers_dir=covers_a)

    conn_b = catalog.open_catalog(tmp_path / "b.db")
    catalog.upsert_work(conn_b, {           # same drive, scanned locally, raw
        "type": "movie", "title": "Blade.Runner.1982", "title_raw": "x",
        "rel_path": "Movies/blade", "drive_label": "8Tb"})
    conn_b.commit()

    res = catalog.import_bundle(conn_b, out, covers_dir=tmp_path / "b-covers")
    assert res["updated"] == 1 and res["created"] == 1
    assert conn_b.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 2
    assert conn_b.execute(
        "SELECT title FROM works WHERE rel_path='Movies/blade'"
    ).fetchone()[0] == "Blade Runner"


def test_a_local_correction_is_never_overwritten(machine_a, tmp_path):
    """Someone fixed a title by hand here; an import must not undo that."""
    conn_a, covers_a = machine_a
    out = tmp_path / "bundle.zip"
    catalog.export_bundle(conn_a, out, covers_dir=covers_a)

    conn_b = catalog.open_catalog(tmp_path / "b.db")
    catalog.upsert_work(conn_b, {
        "type": "movie", "title": "O meu titulo", "title_raw": "x",
        "rel_path": "Movies/blade", "drive_label": "8Tb"})
    conn_b.execute("UPDATE works SET manual=1")
    conn_b.commit()

    res = catalog.import_bundle(conn_b, out, covers_dir=tmp_path / "b-covers")
    assert res["skipped_manual"] == 1
    assert conn_b.execute(
        "SELECT title FROM works WHERE rel_path='Movies/blade'"
    ).fetchone()[0] == "O meu titulo"


def test_reimporting_is_safe(machine_a, tmp_path):
    conn_a, covers_a = machine_a
    out = tmp_path / "bundle.zip"
    catalog.export_bundle(conn_a, out, covers_dir=covers_a)
    covers_b = tmp_path / "b-covers"
    conn_b = catalog.open_catalog(tmp_path / "b.db")

    first = catalog.import_bundle(conn_b, out, covers_dir=covers_b)
    second = catalog.import_bundle(conn_b, out, covers_dir=covers_b)
    assert first["created"] == 2
    assert second["created"] == 0 and second["updated"] == 2
    assert second["covers"] == 0            # already on disk, not rewritten
    assert conn_b.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 2


def test_a_cover_recorded_but_missing_on_disk_is_not_claimed(tmp_path):
    """A dangling cover_path must not produce a bundle that promises an image
    it does not contain."""
    covers = tmp_path / "covers"
    covers.mkdir()
    conn = catalog.open_catalog(tmp_path / "a.db")
    catalog.upsert_work(conn, {"type": "movie", "title": "Ghost", "title_raw": "x",
                               "rel_path": "Movies/ghost", "drive_label": "D"})
    conn.execute("UPDATE works SET enriched=1, cover_path=?",
                 (str(covers / "not-there.jpg"),))
    conn.commit()

    out = tmp_path / "b.zip"
    res = catalog.export_bundle(conn, out, covers_dir=covers)
    assert res["works"] == 1 and res["covers"] == 0
    with zipfile.ZipFile(out) as z:
        assert not [n for n in z.namelist() if n.startswith("covers/")]
        assert "cover" not in json.loads(z.read("works.json"))[0]


def test_a_hostile_archive_cannot_write_outside_the_covers_folder(tmp_path):
    """Bundles get emailed and copied around, so a crafted one must not be able
    to drop a file anywhere it likes."""
    out = tmp_path / "evil.zip"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("works.json", "[]")
        z.writestr("covers/../../escaped.txt", "pwned")

    covers_b = tmp_path / "b-covers"
    conn_b = catalog.open_catalog(tmp_path / "b.db")
    catalog.import_bundle(conn_b, out, covers_dir=covers_b)

    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert (covers_b / "escaped.txt").is_file()   # flattened into the folder


def test_a_file_that_is_not_a_bundle_is_refused(tmp_path):
    junk = tmp_path / "holiday.zip"
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("photo.jpg", "not a catalogue")
    conn = catalog.open_catalog(tmp_path / "b.db")
    with pytest.raises(ValueError, match="not a media-catalog bundle"):
        catalog.import_bundle(conn, junk, covers_dir=tmp_path / "c")


def test_the_cli_round_trips_a_bundle_between_two_catalogues(tmp_path, monkeypatch):
    """Drives the real entry points, the way someone actually moves a
    collection between computers."""
    import importlib.util
    import sys

    covers_a = tmp_path / "a-covers"; covers_a.mkdir()
    (covers_a / "blade.jpg").write_bytes(PNG)
    db_a = tmp_path / "a.db"
    conn = catalog.open_catalog(db_a)
    catalog.upsert_work(conn, {"type": "movie", "title": "Blade Runner",
                               "title_raw": "x", "year": 1982,
                               "rel_path": "Movies/blade", "drive_label": "8Tb"})
    conn.execute("UPDATE works SET enriched=1, cover_path=?",
                 (str(covers_a / "blade.jpg"),))
    conn.commit(); conn.close()

    spec = importlib.util.spec_from_file_location(
        "mc_bundle_cli", Path(__file__).resolve().parent.parent / "mediacat.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    monkeypatch.setattr(catalog._config, "COVERS_DIR", covers_a)
    out = tmp_path / "bundle.zip"
    monkeypatch.setattr(sys, "argv",
                        ["mediacat", "--catalog", str(db_a), "export-bundle", str(out)])
    cli.main()
    assert out.is_file()

    # a second machine: different catalogue, different covers folder, no keys
    covers_b = tmp_path / "b-covers"
    monkeypatch.setattr(catalog._config, "COVERS_DIR", covers_b)
    db_b = tmp_path / "b.db"
    monkeypatch.setattr(sys, "argv",
                        ["mediacat", "--catalog", str(db_b), "import-bundle", str(out)])
    cli.main()

    conn_b = catalog.open_catalog(db_b)
    title, cover = conn_b.execute(
        "SELECT title, cover_path FROM works").fetchone()
    assert title == "Blade Runner"
    assert Path(cover).is_file() and Path(cover).read_bytes() == PNG
