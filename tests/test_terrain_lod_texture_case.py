"""Terrain LOD finds a land texture whose file is spelled in another case.

A TXST spells `tes4\\Landscape\\TerrainHDRock01.dds` in its author's case while
the converted tree ships `tes4/landscape/terrainhdrock01.dds`. On a
case-sensitive filesystem the exact join missed, and the bake composited the
neutral-grey fallback in place of the texture. On a filesystem that ignores
case these tests pass with or without the case-blind lookup.

See: docs/commentary/asset_convert_texture.md#case-blind-lookups
"""

import numpy as np
import pytest
from PIL import Image

from asset_convert.lod import terrain_lod_textures as tlt

RED = (200, 10, 10)
GREEN = (10, 200, 10)
GREY = (128, 128, 128)
RECORD_PATH = 'textures\\tes4\\Landscape\\TerrainHDRock01.dds'


@pytest.fixture(autouse=True)
def _empty_cache():
    """Each test reads the files it wrote, not an earlier test's tile."""
    tlt._TEX_CACHE.clear()
    yield
    tlt._TEX_CACHE.clear()


def _write(root, rel, rgb):
    """A 4x4 image of one color at `root/rel` (forward slashes)."""
    path = root.joinpath(*rel.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (4, 4), rgb).save(path, format='PNG')
    return path


def _color(tile):
    """The tile's single color as an (r, g, b) tuple."""
    assert (tile == tile[0, 0]).all()
    return tuple(int(c) for c in tile[0, 0])


def test_a_lowercase_file_is_found_for_a_mixed_case_record(tmp_path):
    """The record's spelling misses on disk; the lowercase file is used."""
    _write(tmp_path, 'tes4/landscape/terrainhdrock01.dds', RED)
    tile = tlt.load_texture_rgb(RECORD_PATH, tmp_path, size=4)
    assert _color(tile) == RED


def test_an_exactly_spelled_file_is_found(tmp_path):
    """A file spelled as the record spells it is found as before."""
    _write(tmp_path, 'tes4/Landscape/TerrainHDRock01.dds', GREEN)
    tile = tlt.load_texture_rgb(RECORD_PATH, tmp_path, size=4)
    assert _color(tile) == GREEN


def test_an_accented_name_still_finds_its_ascii_file(tmp_path):
    """`win_join`'s ASCII fallback still answers before the case-blind one."""
    _write(tmp_path, 'tes4/Landscape/TerrainGras01.dds', GREEN)
    accented = 'tes4\\Landscape\\TerrainGräs01.dds'
    tile = tlt.load_texture_rgb(accented, tmp_path, size=4)
    assert _color(tile) == GREEN


def test_a_texture_in_no_root_is_neutral_grey(tmp_path):
    """No spelling of the file anywhere still gives the grey fallback."""
    _write(tmp_path, 'tes4/landscape/terrainhddirt01.dds', RED)
    tile = tlt.load_texture_rgb(RECORD_PATH, tmp_path, size=4)
    assert _color(tile) == GREY
    assert tile.dtype == np.uint8


def test_the_first_root_with_any_spelling_wins(tmp_path):
    """Roots keep their order: own tree, any case, before a master's."""
    own, master = tmp_path / 'own', tmp_path / 'master'
    _write(own, 'tes4/landscape/terrainhdrock01.dds', RED)
    _write(master, 'tes4/Landscape/TerrainHDRock01.dds', GREEN)
    tile = tlt.load_texture_rgb(RECORD_PATH, [own, master], size=4)
    assert _color(tile) == RED


def test_a_master_root_is_searched_ignoring_case(tmp_path):
    """A texture only a master ships, in another case, is found there."""
    own, master = tmp_path / 'own', tmp_path / 'master'
    own.mkdir()
    _write(master, 'tes4/landscape/terrainhdrock01.dds', GREEN)
    tile = tlt.load_texture_rgb(RECORD_PATH, [own, master], size=4)
    assert _color(tile) == GREEN
