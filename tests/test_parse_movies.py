"""Movie name parsing.

These names are the messy reality of a 20-year-old collection: scene releases,
Portuguese titles with accents, titles that are themselves years or contain
digits. The cases here all pass today — they are pinned so that a future fix
to one convention cannot quietly break another.
"""
from __future__ import annotations

import pytest

from media_catalog.discover import _clean_movie_title


@pytest.mark.parametrize("raw,title,year", [
    # scene releases: strip resolution, source, codec and release group
    ("Blade.Runner.1982.1080p.BluRay.x264-LOL", "Blade Runner", 1982),
    ("The.Matrix.1999.REMASTERED.1080p.BluRay.x264", "The Matrix", 1999),
    # accents and punctuation must survive untouched
    ("O Senhor dos Anéis - A Irmandade do Anel (2001)",
     "O Senhor dos Anéis - A Irmandade do Anel", 2001),
    ("Amélie", "Amélie", None),
    ("WALL·E (2008)", "WALL·E", 2008),
    # digits inside the title must not be eaten as the year
    ("Se7en.1995", "Se7en", 1995),
    ("Blade Runner 2049 (2017)", "Blade Runner 2049", 2017),
    # a title that IS a year, followed by the real year
    ("2012.2009.1080p", "2012", 2009),
    # hyphens belong to the title, not to a release group
    ("Spider-Man.Into.the.Spider-Verse.2018.720p",
     "Spider-Man Into the Spider-Verse", 2018),
])
def test_movie_titles_and_years(raw, title, year):
    assert _clean_movie_title(raw) == (title, year)
