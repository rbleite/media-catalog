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
