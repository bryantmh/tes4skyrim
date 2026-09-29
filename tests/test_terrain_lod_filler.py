"""Unpainted terrain-LOD quadrants take the game's own baked LOD image.

A quadrant with no BTXT and no ATXT used to composite the engine's
default.dds, a flat brown-grey. The source game shipped a baked LOD tile for
exactly that ground; its cell crop (the tile is stored SOUTH-UP) now fills the
quadrant, untinted by VCLR because it is already shaded.

See: docs/commentary/asset_convert_terrain.md#terrain-lod-filler
"""

import numpy as np
import pytest
from PIL import Image

from asset_convert import case_paths
from asset_convert.lod import terrain_lod
from asset_convert.lod import terrain_lod_baked as tb
from asset_convert.lod import terrain_lod_textures as tlt

PX = terrain_lod.CELL_DIFFUSE_PX
N = 4


@pytest.fixture(autouse=True)
def _fresh():
    """Empty every per-process cache and counter the compositor keeps."""
    tlt._TEX_CACHE.clear()
    terrain_lod._CELL_IMG_CACHE.clear()
    tb.load_tile_rgb.cache_clear()
    tlt.texture_stats()
    tlt.filler_stats()
    case_paths.invalidate()
    yield
    tb.load_tile_rgb.cache_clear()
    terrain_lod._CELL_IMG_CACHE.clear()


def _south_up_tile(path):
    """A 32-cell tile image, SOUTH-UP, cell (cx, cy) coloured (cx*8, cy*8, 99)."""
    img = np.zeros((32 * N, 32 * N, 3), dtype=np.uint8)
    for cy in range(32):
        for cx in range(32):
            img[cy * N:(cy + 1) * N, cx * N:(cx + 1) * N] = (cx * 8, cy * 8, 99)
    Image.fromarray(img, 'RGB').save(path, format='PNG')
    return str(path)


def _quads(colours):
    """A north-up PX image whose quadrants are {quad: rgb}."""
    img = np.zeros((PX, PX, 3), dtype=np.uint8)
    for quad, (rs, cs) in tlt._quad_blocks(PX).items():
        img[rs, cs] = colours[quad]
    return img


def test_unpainted_quadrant_takes_the_baked_crop_untinted(tmp_path):
    """Quad 2 (TL) is unpainted: baked pixels there, tinted texture elsewhere."""
    tex = tmp_path / 'tes4' / 'landscape' / 'grass.dds'
    tex.parent.mkdir(parents=True)
    Image.new('RGB', (8, 8), (100, 100, 100)).save(tex, format='PNG')
    ltex = {7: {'diffuse': 'tes4\\landscape\\grass.dds'}}
    layers = {'base': {0: 7, 1: 7, 3: 7}, 'alpha': {}}
    baked = _quads({0: (1, 1, 1), 1: (2, 2, 2), 2: (10, 200, 30), 3: (3, 3, 3)})
    red = np.tile(np.array((255, 0, 0), dtype=np.uint8), (33, 33, 1))

    img = tlt.composite_cell(layers, red, ltex, tmp_path, 0, 0, cell_px=PX,
                             baked=baked)

    rs, cs = tlt._quad_blocks(PX)[2]
    assert (img[rs, cs] == (10, 200, 30)).all(), 'baked, and not VCLR-tinted'
    rs, cs = tlt._quad_blocks(PX)[1]
    assert (img[rs, cs] == (100, 0, 0)).all(), 'painted quads keep the tint'
    assert tlt.filler_stats() == {'baked': 1}


def test_without_a_baked_tile_the_default_texture_is_counted(tmp_path):
    """No source: the quadrant is default.dds, and the count says so."""
    layers = {'base': {}, 'alpha': {}}

    tlt.composite_cell(layers, None, {}, tmp_path, 0, 0, cell_px=PX)

    assert tlt.filler_stats() == {'default': 4}


@pytest.mark.parametrize('tile_x, tile_y, baked_tile', [
    (4, 28, (0, 0)), (-4, 0, (-32, 0)), (28, -4, (0, -32))])
def test_tile_atlas_places_every_baked_cell_in_both_axes(tmp_path, tile_x,
                                                         tile_y, baked_tile):
    """E/W and N/S orientation through the whole tile path, negative tiles too."""
    tiles = {baked_tile: _south_up_tile(tmp_path / 'tile.png')}
    level = 4
    heights = np.zeros((level * 32 + 1,) * 2, dtype=np.float32)

    atlas, side = terrain_lod._composite_tile_diffuse(
        {}, tile_x, tile_y, level, {}, [tmp_path], heights, {}, 0.0, tiles)

    assert side == level * PX
    for cy in range(level):
        for cx in range(level):
            lx, ly = tile_x + cx - baked_tile[0], tile_y + cy - baked_tile[1]
            row0 = (level - 1 - cy) * PX
            block = atlas[row0:row0 + PX, cx * PX:(cx + 1) * PX]
            assert (block == (lx * 8, ly * 8, 99)).all(), (cx, cy)


def test_a_fully_painted_cell_never_loads_the_baked_tile(tmp_path):
    """The crop is only read when some quadrant needs it."""
    layers = {'base': {0: 1, 1: 1, 2: 1, 3: 1}, 'alpha': {}}

    assert terrain_lod._baked_crop({(0, 0): str(tmp_path / 'absent.png')},
                                   (3, 3), layers) is None
