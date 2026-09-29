"""Terrain-LOD diffuse takes the VCLR HUE only, as near terrain does.

Community Shaders' LANDSCAPE path divides the interpolated vertex colour by
its largest channel, so near terrain uses VCLR as a tint and never darkens by
it. The LOD used a luminance multiplier around 0.5 that brightened a white
VCLR 1.4x, and filled a LAND with no VCLR at 128. Hue-only matches the near
terrain and a missing VCLR is white (no tint). The mode is a setting:
`multiply`, vanilla's x VCLR/255, is the choice for players without CS.

See: docs/commentary/asset_convert_terrain.md#terrain-lod-vclr-hue
"""

import struct

import numpy as np
import pytest

from asset_convert.lod import terrain_lod
from asset_convert.lod import terrain_lod_textures as tlt
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


# ---------------------------------------------------------------------------
# The tint mode is a setting: 'hue' (default) or vanilla's 'multiply'
# ---------------------------------------------------------------------------

@pytest.fixture
def config(monkeypatch):
    """Stub conversion_config.json; reset the process's resolved mode."""
    import source_paths
    cfg = {}
    monkeypatch.setattr(source_paths, 'load_config', lambda: cfg)
    monkeypatch.setattr(tlt, '_vclr_tint', None)
    return cfg


def test_multiply_darkens_by_vclr_over_255():
    """Vanilla's shader: a grey 64 VCLR scales every channel by 64/255."""
    out = np.full((PX, PX, 3), 100.0, dtype=np.float32)

    got = _apply_vclr_shading(out, _flat((64, 64, 64)), PX, 'multiply')

    assert np.allclose(got, 100.0 * 64 / 255, atol=0.01)


def test_the_default_mode_is_hue(config):
    """No key in the config: hue, as Community Shaders' near terrain."""
    assert tlt.vclr_tint() == 'hue'


def test_the_config_key_selects_multiply(config):
    """`terrainLodVclrTint` is read case-blind."""
    config[tlt.VCLR_TINT_CONFIG_KEY] = ' Multiply '

    assert tlt.vclr_tint() == 'multiply'


def test_an_unknown_config_value_falls_back_to_hue(config, capsys):
    """A typo is warned about, never a crash or a silent third mode."""
    config[tlt.VCLR_TINT_CONFIG_KEY] = 'vanilla'

    assert tlt.vclr_tint() == 'hue'
    assert 'vanilla' in capsys.readouterr().out


def test_an_explicit_mode_beats_the_config(config):
    """The CLI value wins; an invalid one raises."""
    config[tlt.VCLR_TINT_CONFIG_KEY] = 'hue'

    assert tlt.set_vclr_tint('multiply') == 'multiply'
    assert tlt.vclr_tint() == 'multiply'
    with pytest.raises(ValueError):
        tlt.set_vclr_tint('bogus')


def test_composite_cell_follows_the_mode(config, tmp_path):
    """The same grey-VCLR cell is 64/255 as bright under multiply."""
    from PIL import Image
    tex = tmp_path / 'tes4' / 'landscape' / 'grass.dds'
    tex.parent.mkdir(parents=True)
    Image.new('RGB', (8, 8), (200, 200, 200)).save(tex, format='PNG')
    ltex = {7: {'diffuse': 'tes4\\landscape\\grass.dds'}}
    layers = {'base': {q: 7 for q in range(4)}, 'alpha': {}}
    tlt._TEX_CACHE.clear()

    got = {}
    for mode in tlt.VCLR_TINT_MODES:
        tlt.set_vclr_tint(mode)
        got[mode] = tlt.composite_cell(layers, _flat((64, 64, 64)), ltex,
                                       tmp_path, 0, 0, cell_px=PX)
    tlt._TEX_CACHE.clear()

    assert (got['hue'] == 200).all()
    assert (got['multiply'] == int(200 * 64 / 255)).all()


def test_worker_takes_the_parents_mode(config):
    """A spawned worker is handed the resolved mode, not the config's."""
    config[tlt.VCLR_TINT_CONFIG_KEY] = 'hue'

    terrain_lod._worker_init({}, '.', '.', {}, '.', {}, None,
                             vclr_tint='multiply')

    assert tlt.vclr_tint() == 'multiply'


def test_create_lod_passes_the_cli_mode_to_the_terrain_bake(tmp_path):
    """`--vclr-tint` reaches generate_terrain_lod unchanged."""
    from tools.release import create_lod
    seen = {}
    ctx = {'lod_dir': tmp_path, 'out_root': tmp_path, 'export_root': tmp_path,
           'supplier_asset_dirs': lambda n: [], 'supplier_overlay_dirs': lambda n: [],
           'merge_cloud_bank': lambda *a: None, 'generate_lod': lambda **k: True,
           'record_dirs': lambda n: [], 'supplier_record_dirs': lambda n: [],
           'lod_textures_root': lambda d: d,
           'generate_terrain_lod': lambda **k: seen.update(k) or True,
           'vclr_tint': 'multiply'}
    job = ('W', 'A.esm', tmp_path / 'A.esm', [], [], [])
    import asset_convert.game_paths as gp
    ns = gp.current_namespace()
    try:
        assert create_lod._bake_worldspace(job, ctx)
    finally:
        gp.set_namespace(ns)

    assert seen['vclr_tint'] == 'multiply'
