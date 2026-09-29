"""Terrain-LOD diffuse takes the VCLR HUE only, as near terrain does.

Community Shaders' LANDSCAPE path divides the interpolated vertex colour by
its largest channel, so near terrain uses VCLR as a tint and never darkens by
it. The LOD used a luminance multiplier around 0.5 that brightened a white
VCLR 1.4x, and filled a LAND with no VCLR at 128. Hue-only matches the near
terrain and a missing VCLR is white (no tint).

See: docs/commentary/asset_convert_terrain.md#terrain-lod-vclr-hue
"""

import struct

import numpy as np

from asset_convert.lod import terrain_lod
from asset_convert.lod.terrain_lod_textures import _apply_vclr_shading

PX = 8


def _flat(rgb):
    """A 33x33 VCLR grid of one colour."""
    return np.tile(np.array(rgb, dtype=np.uint8), (33, 33, 1))


def test_white_vclr_leaves_the_texture_unchanged():
    """White normalises to (1,1,1); the old formula brightened it 1.4x."""
    out = np.full((PX, PX, 3), 100.0, dtype=np.float32)

    got = _apply_vclr_shading(out, _flat((255, 255, 255)), PX)

    assert np.allclose(got, 100.0)


def test_dark_grey_vclr_does_not_darken():
    """Only the hue survives: a uniform grey is no tint at all."""
    out = np.full((PX, PX, 3), 100.0, dtype=np.float32)

    got = _apply_vclr_shading(out, _flat((64, 64, 64)), PX)

    assert np.allclose(got, 100.0)


def test_coloured_vclr_scales_each_channel_by_its_share_of_the_peak():
    """(255,128,128) tints by (1, 128/255, 128/255)."""
    out = np.full((PX, PX, 3), 200.0, dtype=np.float32)

    got = _apply_vclr_shading(out, _flat((255, 128, 128)), PX)

    want = np.array([200.0, 200.0 * 128 / 255, 200.0 * 128 / 255])
    assert np.allclose(got[3, 3], want, atol=0.5)


def test_black_vclr_stays_finite():
    """The 1e-3 floor keeps an all-zero vertex from dividing by zero."""
    out = np.full((PX, PX, 3), 100.0, dtype=np.float32)

    got = _apply_vclr_shading(out, _flat((0, 0, 0)), PX)

    assert np.isfinite(got).all()


def test_land_without_vclr_is_white():
    """A LAND with no VCLR decodes as 255 (no tint), not 128."""
    vhgt = struct.pack('<f', 0.0) + bytes(33 * 33) + bytes(3)
    subs = {'VHGT': vhgt}

    land = terrain_lod._decode_land(b'', lambda _b, tag: subs.get(tag))

    assert land['colors'].shape == (33, 33, 3)
    assert np.all(land['colors'] == 255)
