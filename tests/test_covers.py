"""One cover file per distinct image, however many drives hold the album.

Every provider named its cover `<type>_<work_id>.jpg`. Since `works` carries
UNIQUE(drive_label, rel_path), the same album on three drives is three rows —
so it was three downloads and three files holding byte-identical images, in a
folder that is normally cloud-synced.

These tests fix the storage (content addressing), the network (one fetch per
provider source), and the cross-machine breakage that came from recording an
absolute path in `cover_path`.
"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from media_catalog import config, covers

# A minimal valid JPEG and PNG — the store identifies format from the magic
# number, not from any declared type, so the header is what matters.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 900
JPEG2 = b"\xff\xd8\xff\xe0" + b"\x11" * 900
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 900


@pytest.fixture(autouse=True)
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COVERS_DIR", tmp_path / "covers")
    return tmp_path / "covers"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE works (
        id INTEGER PRIMARY KEY, identifier TEXT, cover_path TEXT)""")
    return c


# ── the store ────────────────────────────────────────────────────────────────

def test_the_same_image_is_stored_once(store_dir):
    """The whole point. Three works, one file."""
    names = [covers.store(JPEG) for _ in range(3)]
    assert len(set(names)) == 1
    assert len(list((store_dir / covers.CONTENT_SUBDIR).iterdir())) == 1


def test_different_images_get_different_files(store_dir):
    a, b = covers.store(JPEG), covers.store(JPEG2)
    assert a != b
    assert len(list((store_dir / covers.CONTENT_SUBDIR).iterdir())) == 2


def test_the_name_is_the_hash_of_the_bytes(store_dir):
    name = covers.store(JPEG)
    assert hashlib.sha256(JPEG).hexdigest() in name


def test_the_stored_name_is_relative(store_dir):
    """An absolute path is why covers vanished on the second computer: the
    catalogue recorded /Users/<someone>/... from whichever machine enriched."""
    name = covers.store(JPEG)
    assert not name.startswith("/")
    assert name.startswith(covers.CONTENT_SUBDIR + "/")


def test_the_format_comes_from_the_bytes_not_a_label(store_dir):
    assert covers.store(PNG).endswith(".png")
    assert covers.store(JPEG).endswith(".jpg")


def test_a_404_body_is_not_stored_as_an_image(store_dir):
    """TMDB's series downloader never checked, so an error page was saved with
    a .jpg name and rendered as a broken image."""
    assert covers.store(b"<!DOCTYPE html><html>Not Found</html>" * 40) is None
    assert not (store_dir / covers.CONTENT_SUBDIR).exists()


@pytest.mark.parametrize("junk", [b"", b"tiny", None])
def test_nothing_usable_stores_nothing(junk, store_dir):
    assert covers.store(junk) is None


def test_no_partial_file_is_left_behind(store_dir):
    """It writes to a .part and renames, so a sync client never uploads a
    half-written image under a name that promises a hash of the whole thing."""
    covers.store(JPEG)
    assert not list((store_dir / covers.CONTENT_SUBDIR).glob("*.part"))


# ── resolving ────────────────────────────────────────────────────────────────

def test_a_stored_name_resolves(store_dir):
    name = covers.store(JPEG)
    got = covers.resolve(name)
    assert got is not None and got.read_bytes() == JPEG


def test_a_legacy_bare_filename_still_resolves(store_dir):
    """A catalogue mid-migration holds all three forms at once."""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "album_7.jpg").write_bytes(JPEG)
    assert covers.resolve("album_7.jpg") is not None


def test_a_legacy_absolute_path_from_another_machine_falls_back(store_dir):
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "album_7.jpg").write_bytes(JPEG)
    assert covers.resolve("/Users/someone-else/covers/album_7.jpg") is not None


def test_a_path_that_exists_nowhere_is_none(store_dir):
    assert covers.resolve("/nope/gone.jpg") is None
    assert covers.resolve(None) is None


def test_config_resolve_cover_uses_the_store(store_dir):
    """app.py calls config.resolve_cover, so the wiring has to hold."""
    name = covers.store(JPEG)
    assert config.resolve_cover(name) == str(covers.resolve(name))


# ── one download per provider source ─────────────────────────────────────────

def test_the_same_album_on_three_drives_downloads_once(conn, store_dir):
    """The user's case, stated exactly. Three rows, one network call."""
    calls = []

    def _get():
        calls.append(1)
        return JPEG

    names = [covers.store_from_source(conn, "mbid:abc", _get) for _ in range(3)]
    assert len(calls) == 1, f"downloaded {len(calls)} times"
    assert len(set(names)) == 1


def test_different_sources_still_download(conn, store_dir):
    calls = []

    def make(data):
        def _get():
            calls.append(1)
            return data
        return _get

    covers.store_from_source(conn, "mbid:a", make(JPEG))
    covers.store_from_source(conn, "mbid:b", make(JPEG2))
    assert len(calls) == 2


def test_two_sources_serving_the_same_image_still_share_one_file(conn,
                                                                 store_dir):
    """Deezer and iTunes can return the same artwork. The download happens
    twice — different sources — but the store keeps one file."""
    covers.store_from_source(conn, "deezer:x", lambda: JPEG)
    covers.store_from_source(conn, "itunes:y", lambda: JPEG)
    assert len(list((store_dir / covers.CONTENT_SUBDIR).iterdir())) == 1


def test_a_remembered_source_whose_file_vanished_is_refetched(conn, store_dir):
    """The row is worthless if the store was pruned, or the catalogue arrived
    from a machine whose covers did not come with it. Trusting it blindly
    would leave the work with no cover at all."""
    name = covers.store_from_source(conn, "mbid:abc", lambda: JPEG)
    covers.resolve(name).unlink()

    calls = []

    def _get():
        calls.append(1)
        return JPEG

    again = covers.store_from_source(conn, "mbid:abc", _get)
    assert calls, "it trusted a row pointing at a file that is gone"
    assert again == name


def test_a_failed_download_is_not_remembered(conn, store_dir):
    """Caching a failure would make one network blip permanent."""
    covers.store_from_source(conn, "mbid:abc", lambda: None)
    calls = []

    def _get():
        calls.append(1)
        return JPEG

    assert covers.store_from_source(conn, "mbid:abc", _get) is not None
    assert calls, "a failure was cached as if it were an answer"


def test_no_source_still_downloads(conn, store_dir):
    assert covers.store_from_source(conn, "", lambda: JPEG) is not None


# ── migrating what is already on disk ────────────────────────────────────────

def test_migration_collapses_identical_covers(conn, store_dir):
    """The state a real catalogue is in today: the same album on three drives,
    three files, identical bytes."""
    store_dir.mkdir(parents=True, exist_ok=True)
    for wid in (1, 2, 3):
        (store_dir / f"album_{wid}.jpg").write_bytes(JPEG)
        conn.execute("INSERT INTO works (id, identifier, cover_path)"
                     " VALUES (?,?,?)",
                     (wid, "mbid:abc", f"/old/machine/covers/album_{wid}.jpg"))
    conn.commit()

    stats = covers.migrate_legacy(conn)

    assert stats["rows_updated"] == 3
    assert len(list((store_dir / covers.CONTENT_SUBDIR).iterdir())) == 1, \
        "three identical covers must become one file"

    paths = [r[0] for r in conn.execute("SELECT cover_path FROM works")]
    assert len(set(paths)) == 1, "and all three rows must point at it"
    assert all(covers.resolve(p) for p in paths), "and it must be readable"


def test_migration_keeps_distinct_covers_distinct(conn, store_dir):
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "album_1.jpg").write_bytes(JPEG)
    (store_dir / "album_2.jpg").write_bytes(JPEG2)
    conn.execute("INSERT INTO works VALUES (1, 'a', 'album_1.jpg')")
    conn.execute("INSERT INTO works VALUES (2, 'b', 'album_2.jpg')")
    conn.commit()

    covers.migrate_legacy(conn)
    paths = [r[0] for r in conn.execute("SELECT cover_path FROM works")]
    assert len(set(paths)) == 2


def test_migration_does_not_delete_the_originals(conn, store_dir):
    """It copies. An interrupted run must not be able to lose a cover that no
    row points at yet."""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "album_1.jpg").write_bytes(JPEG)
    conn.execute("INSERT INTO works VALUES (1, 'a', 'album_1.jpg')")
    conn.commit()
    covers.migrate_legacy(conn)
    assert (store_dir / "album_1.jpg").exists()


def test_migration_runs_twice_without_damage(conn, store_dir):
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "album_1.jpg").write_bytes(JPEG)
    conn.execute("INSERT INTO works VALUES (1, 'a', 'album_1.jpg')")
    conn.commit()

    covers.migrate_legacy(conn)
    first = conn.execute("SELECT cover_path FROM works").fetchone()[0]
    covers.migrate_legacy(conn)
    assert conn.execute("SELECT cover_path FROM works").fetchone()[0] == first


def test_migration_on_an_empty_store_is_harmless(conn):
    assert covers.migrate_legacy(conn)["scanned"] == 0
