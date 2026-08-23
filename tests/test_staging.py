"""The catalogue is written locally and published once, not continuously.

`config.data_dir()` defaults to `<drive-xray db_dir>/media-catalog` — the same
synced folder that carries the drive indexes. A `mediacat scan` over eight
drives writes for minutes, and doing that in place means minutes of the sync
client re-uploading a database that is still being written.

These cover the mechanism and, more importantly, that no command can be added
without it: the dispatch stages, so forgetting is not possible one command at
a time.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from media_catalog import staging

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def cloud(tmp_path, monkeypatch):
    root = tmp_path / "OneDrive" / "media-catalog"
    root.mkdir(parents=True)
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path / "staging")
    monkeypatch.delenv("MEDIACAT_NO_STAGING", raising=False)
    return root, tmp_path / "staging"


# ── detecting the folder ─────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "OneDrive", "onedrive", "OneDrive - Some Company", "Dropbox",
    "Google Drive", "Mobile Documents", "CloudStorage", "iCloud Drive",
])
def test_the_usual_sync_folders_are_recognised(name):
    assert staging.is_cloud_dir(name)


@pytest.mark.parametrize("name", ["Documents", "tools", "media-catalog", "src"])
def test_ordinary_folders_are_not(name):
    assert not staging.is_cloud_dir(name)


def test_detection_looks_at_every_part_of_the_path(tmp_path):
    assert staging.in_cloud_dir(tmp_path / "Dropbox" / "a" / "b" / "c.db")
    assert not staging.in_cloud_dir(tmp_path / "a" / "b" / "c.db")


# ── the context manager ──────────────────────────────────────────────────────

def test_outside_a_synced_folder_nothing_is_copied(tmp_path):
    db = tmp_path / "catalog.db"
    with staging.staged_catalog(db) as target:
        assert target == db


def test_the_write_lands_locally_and_is_published_at_the_end(cloud):
    root, stage = cloud
    db = root / "catalog.db"
    with staging.staged_catalog(db) as target:
        assert stage in target.parents
        target.write_bytes(b"finished")
        assert not db.exists(), "nothing may appear in the synced folder yet"
    assert db.read_bytes() == b"finished"


def test_a_failure_does_not_publish_a_half_written_catalogue(cloud):
    """The reason there is no try/finally. The other machine reads this
    folder; a database captured mid-write is worse than an old one."""
    root, _stage = cloud
    db = root / "catalog.db"
    db.write_bytes(b"the good one")

    with pytest.raises(RuntimeError):
        with staging.staged_catalog(db) as target:
            target.write_bytes(b"half written")
            raise RuntimeError("interrupted")

    assert db.read_bytes() == b"the good one"


def test_an_interrupted_scan_can_resume(cloud):
    root, stage = cloud
    db = root / "catalog.db"
    with pytest.raises(RuntimeError):
        with staging.staged_catalog(db) as target:
            target.write_bytes(b"six hours of work")
            raise RuntimeError("interrupted")

    again, staged = staging.stage_for_write(db)
    assert staged and again.read_bytes() == b"six hours of work"


def test_the_staging_dir_is_not_tmp():
    """macOS purges /tmp after three days. A staging dir that evaporates turns
    resumption into starting over, without saying so."""
    parts = [p.lower() for p in staging.STAGING_DIR.parts]
    assert "tmp" not in parts and "temp" not in parts


def test_the_opt_out_works(cloud, monkeypatch):
    root, _stage = cloud
    monkeypatch.setenv("MEDIACAT_NO_STAGING", "1")
    db = root / "catalog.db"
    with staging.staged_catalog(db) as target:
        assert target == db


# ── no command can be added without it ───────────────────────────────────────

def _cli_source() -> str:
    return (REPO / "mediacat.py").read_text(encoding="utf-8")


def test_the_dispatch_stages_rather_than_each_command():
    """The whole point of doing it in one place. drive-xray's version of this
    bug was four call sites someone remembered and the rest they did not."""
    src = _cli_source()
    assert "staged_catalog" in src
    assert "handlers[args.cmd](args)" in src


def test_every_command_is_routed_through_the_dispatch():
    """A handler map that has drifted from the subparsers would leave a
    command unstaged — or crash with a KeyError."""
    import re
    src = _cli_source()
    declared = set(re.findall(r'sub\.add_parser\(\s*"([a-z0-9-]+)"', src))
    # not anchored to the line start: the handler map puts several entries
    # on one line, and anchoring silently saw only the first of each
    routed = set(re.findall(r'"([a-z0-9-]+)":\s*cmd_', src))
    assert declared, "no subparsers found — the scan itself is broken"
    assert declared == routed, (
        f"declared but not routed: {sorted(declared - routed)}; "
        f"routed but not declared: {sorted(routed - declared)}")


def test_the_read_only_commands_are_a_known_short_list():
    """Everything not named here stages. If a new READERS entry is added it
    should be a deliberate act, not a way to quietly skip staging."""
    import re
    src = _cli_source()
    m = re.search(r'READERS = \{([^}]*)\}', src)
    assert m, "the READERS set vanished"
    names = set(re.findall(r'"([a-z0-9-]+)"', m.group(1)))
    assert names == {"summary", "export-patch", "export-bundle"}, names


def test_a_real_command_writes_locally_and_publishes(tmp_path, monkeypatch):
    """End to end through the actual CLI, not the internals."""
    root = tmp_path / "Dropbox" / "media-catalog"
    root.mkdir(parents=True)
    db = root / "catalog.db"

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = dict(__import__("os").environ)
    env["MEDIACAT_DATA_DIR"] = str(root)
    # Path.home() reads HOME on POSIX and USERPROFILE on Windows. Setting only
    # HOME leaves the Windows run staging into the real user profile, where
    # this test then looks in the wrong place and reports that staging never
    # happened -- which is exactly how it failed in CI.
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)

    out = subprocess.run(
        [sys.executable, str(REPO / "mediacat.py"),
         "--catalog", str(db), "covers-migrate"],
        capture_output=True, text=True, cwd=str(REPO), env=env)
    assert out.returncode == 0, out.stderr

    assert db.exists(), "the finished catalogue must be published to the folder"

    # Both halves are needed. Asserting only that the file arrived would pass
    # just as well with no staging at all -- writing in place also leaves a
    # catalogue in the folder. The staging dir having been created is what
    # proves the write went somewhere else first.
    stage = tmp_path / "home" / ".media-catalog-staging"
    assert stage.is_dir(), "the write never went through staging"
    assert not (stage / "catalog.db").exists(), \
        "and nothing may be stranded there afterwards"


# ── publishing must not break a connection someone else holds ────────────────
#
# Reported from the running app: after `mediacat covers-migrate`, every
# enrichment died with "attempt to write a readonly database".
#
# finalize() used shutil.move, which replaces the directory entry and gives
# the destination a NEW inode. The Streamlit gallery holds one connection open
# for the life of the session, cached in st.cache_resource, so it was left
# pointing at the old unlinked file — and never recovered, because nothing
# ever rebuilds that cache. The app stayed broken until restarted.
#
# It happened on the very first command this staging ever ran.

def _open_catalog(path):
    from media_catalog import catalog as C
    return C.open_catalog(path)


def test_publishing_leaves_an_open_connection_usable(cloud):
    """The exact failure, as a test. A reader that was already attached must
    still be able to write afterwards."""
    root, stage = cloud
    db = root / "catalog.db"
    _open_catalog(db).close()

    held = _open_catalog(db)                    # the gallery's cached handle
    held.execute("SELECT COUNT(*) FROM works").fetchone()

    staged = stage / "catalog.db"
    stage.mkdir(parents=True, exist_ok=True)
    __import__("shutil").copy2(db, staged)
    staging.finalize(staged, db)

    held.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('probe', '1')")
    held.commit()                               # raised OperationalError before


def test_the_held_connection_sees_what_was_published(cloud):
    """Preserving the file is not enough — the new rows have to be visible
    through the connection that was already open, or the app shows stale data
    and looks broken in a different way."""
    import shutil as _sh
    import sqlite3 as _sq

    root, stage = cloud
    db = root / "catalog.db"
    _open_catalog(db).close()
    held = _open_catalog(db)

    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / "catalog.db"
    _sh.copy2(db, staged)
    w = _sq.connect(staged)
    w.execute("INSERT INTO works (id, type, title, rel_path, drive_label,"
              " updated_at) VALUES (99, 'game', 'Novo', '/x', 'D', '')")
    w.commit()
    w.close()

    staging.finalize(staged, db)
    assert held.execute("SELECT COUNT(*) FROM works WHERE id=99").fetchone()[0] == 1


def test_the_destination_keeps_its_identity(cloud):
    """The mechanism behind both of the above, stated directly: the inode must
    not change. A move gives a new one, which is what stranded the app."""
    import shutil as _sh

    root, stage = cloud
    db = root / "catalog.db"
    _open_catalog(db).close()
    before = db.stat().st_ino

    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / "catalog.db"
    _sh.copy2(db, staged)
    staging.finalize(staged, db)

    assert db.stat().st_ino == before, "publishing replaced the file"


def test_a_first_run_with_no_destination_still_works(cloud):
    """Nothing can be holding a file that does not exist yet, so that path
    stays a plain move — and must keep working."""
    root, stage = cloud
    db = root / "catalog.db"
    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / "catalog.db"
    _open_catalog(staged).close()

    staging.finalize(staged, db)
    assert db.exists() and not staged.exists()


def test_an_unreadable_destination_is_replaced_rather_than_refused(cloud):
    """A truncated sync leaves a file that is not a database. Refusing to
    publish over rubble would strand the finished catalogue in staging."""
    root, stage = cloud
    db = root / "catalog.db"
    db.write_bytes(b"not a database at all" * 30)

    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / "catalog.db"
    _open_catalog(staged).close()

    staging.finalize(staged, db)
    assert _open_catalog(db).execute(
        "SELECT COUNT(*) FROM works").fetchone()[0] == 0


def test_the_staged_copy_is_removed_after_publishing(cloud):
    """Publishing through SQLite left the copy behind, so the staging dir grew
    a stale catalogue on every run — and on Windows the leftover open handle
    made the next attempt fail outright with WinError 32. Caught by CI on
    windows-latest; assertable anywhere, because the leak is the same."""
    root, stage = cloud
    db = root / "catalog.db"
    _open_catalog(db).close()

    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / "catalog.db"
    __import__("shutil").copy2(db, staged)

    staging.finalize(staged, db)
    assert not staged.exists(), "the staged copy was left in the staging dir"
