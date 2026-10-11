"""The gallery's connection must survive the catalogue file being replaced.

Reported from the running app, with the publish fix (#13) already in place:
"Enriquecer séries" died with "attempt to write a readonly database".

#13 stopped OUR staging from replacing the file. It cannot stop anyone else.
The catalogue lives in a synced folder; when the sync client brings down the
copy another computer wrote, it swaps a new file in under the same name. The
app's connection, cached for the life of the process, stays on the old one,
and every write fails until the app is restarted.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from media_catalog import catalog as C

REPO = Path(__file__).resolve().parent.parent


def _sync_client_replaces(db: Path) -> None:
    """What a sync client does with a download: write beside, then swap in."""
    incoming = db.with_name(db.name + ".download")
    shutil.copy2(db, incoming)
    os.replace(incoming, db)


@pytest.mark.skipif(os.name == "nt",
                    reason="Windows will not replace a file that is open")
def test_the_failure_as_reported(tmp_path):
    """The mechanism, so the fix below is fixing something real."""
    db = tmp_path / "catalog.db"
    held = C.open_catalog(db)
    _sync_client_replaces(db)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        held.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('p', '1')")
        held.commit()


def test_an_untouched_file_is_not_reported_as_replaced(tmp_path):
    db = tmp_path / "catalog.db"
    C.open_catalog(db).close()
    ident = C.file_identity(db)
    # writing through SQLite modifies the file in place: same identity
    c = C.open_catalog(db)
    c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('p', '1')")
    c.commit()
    c.close()
    assert not C.replaced_since(ident, db)


def test_a_replaced_file_is_reported(tmp_path):
    db = tmp_path / "catalog.db"
    C.open_catalog(db).close()
    ident = C.file_identity(db)
    _sync_client_replaces(db)
    assert C.replaced_since(ident, db)


def test_a_file_missing_for_a_moment_is_not_a_reason_to_reopen(tmp_path):
    """Mid-swap the name can briefly point at nothing. Reopening then would
    CREATE an empty catalogue in its place."""
    db = tmp_path / "catalog.db"
    C.open_catalog(db).close()
    ident = C.file_identity(db)
    db.unlink()
    assert not C.replaced_since(ident, db)


@pytest.mark.skipif(os.name == "nt",
                    reason="Windows will not replace a file that is open")
def test_reopening_after_a_replacement_writes_again(tmp_path):
    """The recovery the app performs, end to end outside Streamlit."""
    db = tmp_path / "catalog.db"
    conn = C.open_catalog(db)
    ident = C.file_identity(db)
    _sync_client_replaces(db)

    if C.replaced_since(ident, db):
        conn.close()
        conn = C.open_catalog(db)

    conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('p', '1')")
    conn.commit()
    assert sqlite3.connect(db).execute(
        "SELECT v FROM meta WHERE k='p'").fetchone() == ("1",)


def test_the_app_checks_on_every_rerun():
    """The check is worthless inside the cached function -- that only runs
    once. It has to sit at module level, after the cached call, and clear the
    cache when it fires."""
    src = (REPO / "app.py").read_text(encoding="utf-8")
    call = src.index("conn, _cat_identity = _conn()")
    check = src.index("C.replaced_since(_cat_identity, C.DEFAULT_CATALOG)")
    assert call < check
    assert "_conn.clear()" in src[check:check + 400]
    assert src[:call].count("def _conn(") == 1
    assert "replaced_since" not in src[src.index("def _conn("):call]
