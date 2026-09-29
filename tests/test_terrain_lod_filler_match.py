"""The baked-LOD filler is colour-matched and feathered into the painted terrain.

Oblivion's bake is bluer and lit differently than our composite, so a filler
quadrant met a painted one with a colour step of ~35 against ~4 between
painted quadrants. A smooth per-cell offset (composite minus bake, fitted on
nearby painted cells) moves the filler to our palette, and each filler block
is feathered to meet its painted neighbours' edge colour. Painted pixels and
the bake's own detail are untouched.

See: docs/commentary/asset_convert_terrain.md#terrain-lod-filler-match
"""

import numpy as np
import pytest
from PIL import Image

from asset_convert import case_paths
from asset_convert.lod import terrain_lod
from asset_convert.lod import terrain_lod_baked as tb
from asset_convert.lod import terrain_lod_textures as tlt

PX = terrain_lod.CELL_DIFFUSE_PX


@pytest.fixture(autouse=True)
def _fresh():
    """Empty every per-process cache and counter the compositor keeps."""
    tlt._TEX_CACHE.clear()
    terrain_lod._CELL_IMG_CACHE.clear()
    tb.load_tile_rgb.cache_clear()
    case_paths.invalidate()
    yield
    tlt.texture_stats()
    tlt.filler_stats()
    tb.load_tile_rgb.cache_clear()
    terrain_lod._CELL_IMG_CACHE.clear()


def test_offsets_follow_nearby_fits_and_fall_back_to_the_worldspace_mean():
    """Near a fitted cell its offset dominates; far away the mean holds."""
    fitted = {(0, 0): np.array([20.0, 0, 0]), (40, 0): np.array([0.0, 0, 0])}

    got = tb.smooth_offsets(fitted, [(0, 0), (1, 0), (20, 20)])

    assert got['cells'][(0, 0)][0] > 19
    assert 10 < got['cells'][(1, 0)][0] < 20
    assert np.allclose(got['cells'][(20, 20)], [10, 0, 0])


def test_neighbouring_offset_images_meet_without_a_step():
    """Cell (0,0)'s east column equals cell (1,0)'s west column, near enough."""
    offsets = {'prior': np.zeros(3),
               'cells': {(x, y): np.array([x * 10.0, y * 5.0, 0])
                         for x in range(-1, 3) for y in range(-1, 2)}}

    west = tb.offset_image(offsets, (0, 0), 32)
    east = tb.offset_image(offsets, (1, 0), 32)

    assert np.abs(west[:, -1] - east[:, 0]).max() < 1.0
    assert np.abs(west[:, 0] - west[:, -1]).max() > 5.0


def test_feather_meets_the_painted_edge_and_keeps_the_filler_detail():
    """Filler right of a painted block: edge colour matches, far side and detail kept."""
    h = 16
    canvas = np.zeros((h, 2 * h, 3), dtype=np.float32)
    canvas[:, :h] = 100.0
    stripes = np.where(np.arange(h) % 2 == 0, 10.0, -10.0)[:, None, None]
    canvas[:, h:] = 160.0 + stripes
    state = np.array([[1, 0]], dtype=np.int8)

    out = tb.feather_filler(canvas, state, h)

    assert np.array_equal(out[:, :h], canvas[:, :h]), 'painted pixels untouched'
    assert abs(out[:, h].mean() - 100.0) < 2.0
    assert abs(out[:, -1].mean() - 160.0) < 2.0
    detail = out[:, h:] - out[:, h:].mean(0, keepdims=True)
    want = (canvas[:, h:] - 160.0)
    assert np.allclose(detail[:, -1], want[:, -1], atol=1.0), 'far edge exact'
    inner = slice(2, h - 2)
    assert (np.abs(detail[inner]) >= 0.75 * np.abs(want[inner])).all(), (
        'at most a quarter of the detail is absorbed, at the seam')


def _solid(path, rgb, size):
    """A solid image file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (size, size), rgb).save(path, format='PNG')
    return str(path)


def test_the_tile_path_matches_and_feathers_the_filler(tmp_path):
    """A painted cell (100) beside baked filler (160): the step at the seam goes."""
    _solid(tmp_path / 'tes4' / 'landscape' / 'grass.dds', (100, 100, 100), 8)
    bake = {(0, 0): _solid(tmp_path / 'bake.png', (160, 160, 160), 128)}
    ltex = {7: {'diffuse': 'tes4\\landscape\\grass.dds'}}
    white = np.full((33, 33, 3), 255, np.uint8)
    lands = {(0, 0): {'heights': np.zeros((33, 33), np.float32), 'colors': white,
                      'layers': {'base': {q: 7 for q in range(4)}, 'alpha': {}}},
             (1, 0): {'heights': np.zeros((33, 33), np.float32), 'colors': white,
                      'layers': {'base': {}, 'alpha': {}}}}
    world = {'lands': lands, 'cell_water': {}, 'default_wh': 0.0}
    offsets = terrain_lod._filler_offsets(world, (ltex, [tmp_path]), bake)
    heights = np.zeros((129, 129), np.float32)

    atlas, _ = terrain_lod._composite_tile_diffuse(
        lands, 0, 0, 4, ltex, [tmp_path], heights, {}, 0.0, bake, offsets)

    rows = slice(3 * PX, 4 * PX)
    painted_edge = atlas[rows, PX - 2:PX].astype(float).mean()
    filler_edge = atlas[rows, PX:PX + 2].astype(float).mean()
    assert abs(painted_edge - 100) < 1.5
    assert abs(filler_edge - painted_edge) < 4, 'no seam'
    assert abs(offsets['prior'][0] + 60) < 1.0, 'bake shifted to our palette'
