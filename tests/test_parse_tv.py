"""TV episode parsing.

parse_tv deliberately has two strengths. `strong=True` accepts only
self-identifying patterns and is used OUTSIDE a Series/ root, where a false
match would drag lab and camera files ('5Mm 63X', '18Dpf …') into the catalogue
as episodes. `strong=False` also trusts weaker forms, and is only used inside a
dedicated Series/ root. Both halves are pinned here: what must parse, and just
as importantly what must not.
"""
from __future__ import annotations

import pytest

from media_catalog.discover import parse_tv


@pytest.mark.parametrize("raw,show,season,ep", [
    ("Breaking.Bad.S01E01.720p.HDTV-FQM", "Breaking Bad", 1, 1),
    ("The.Office.US.S09E23.HDTV", "The Office US", 9, 23),
    ("Alias.S1E1", "Alias", 1, 1),
    ("24.S01E01", "24", 1, 1),                      # the show is a number
    ("Doctor.Who.2005.S01E01", "Doctor Who 2005", 1, 1),
    ("Friends.S10E17-E18", "Friends", 10, 17),      # double episode -> first
    # the NxNN convention, common on older and European releases
    ("Game of Thrones - 1x01", "Game of Thrones", 1, 1),
    ("Os.Maias.2x05.mkv", "Os Maias", 2, 5),
    ("Dexter 3x12 HDTV", "Dexter", 3, 12),
    ("Sopranos.1x1", "Sopranos", 1, 1),             # single-digit episode
    ("Lost - 6x17-6x18", "Lost", 6, 17),            # double episode -> first
    ("Alias 2x03 720p x264", "Alias", 2, 3),
])
def test_episodes_parse(raw, show, season, ep):
    assert parse_tv(raw) == (show, season, ep)


@pytest.mark.parametrize("raw,show,season", [
    ("Californication Season 5 COMPLETE", "Californication", 5),
    ("Homeland.S01", "Homeland", 1),                # weak: season pack
])
def test_season_packs_have_no_episode(raw, show, season):
    assert parse_tv(raw) == (show, season, None)


@pytest.mark.parametrize("raw", [
    "5Mm 63X",                      # microscope capture
    "18Dpf larvae 40x",             # ditto
    "Blade.Runner.1982.1080p.BluRay.x264-LOL",       # a movie
    "Holiday video 1920x1080",      # a resolution is not season x episode
    "timelapse 1280x720.mkv",
])
def test_non_episodes_are_refused_outside_a_series_root(raw):
    """strong=True is what runs outside Series/ — it must not false-match."""
    assert parse_tv(raw, strong=True) is None


@pytest.mark.parametrize("raw", [
    # resolutions
    "Holiday video 1920x1080",
    "timelapse 1280x720.mkv",
    "render 3840x2160",
    # print and canvas sizes share the NxNN shape — the episode marker is only
    # trusted where one actually sits: at the end, or before a source tag
    "photo 4x6 print",
    "IMG 20x30 canvas",
    "quadro 30x40 tela",
])
def test_dimensions_are_never_episodes_even_inside_a_series_root(raw):
    assert parse_tv(raw) is None
