"""Sparse ocean cells produce continuous, correctly bounded water LOD."""

import io
import struct

import numpy as np
import pytest

from asset_convert.lod import terrain_lod as lod
from asset_convert.lod.terrain_nif import build_terrain_nif, tile_solid_mask
from tes5_import.base.writer import pack_record, pack_subrecord, pack_group, pack_tes4_header
from pyffi.formats.nif import NifFormat


def _read(blob):
    data = NifFormat.Data()
    data.read(io.BytesIO(blob))
    return data.roots[0]


@pytest.mark.parametrize('level', [4, 8, 16, 32])
def test_sparse_ocean_preserves_dry_cells_and_explicit_water_heights(level):
    lands = {(0, 0): {'heights': np.full((33, 33), 300, dtype='f4'),
                      'colors': np.full((33, 33, 3), 128, dtype='u1')}}
    water = {(1, 0): (False, None), (2, 0): (True, 150)}
    quads = lod._tile_water_quads(lands, water, 0, 0, level, 25, -2048)
    surfaces = {(x, y): h for x, y, h in quads}
    assert len(surfaces) == level * level - 2
    assert (0, 0) not in surfaces and (1, 0) not in surfaces
    assert surfaces[(2, 0)] == 150 and surfaces[(3, 0)] == 25
    heights, _ = lod._assemble_tile(lands, 0, 0, level)
    root = _read(build_terrain_nif(heights, 0, 0, level, 'Ocean',
        water_quads=quads, solid_mask=tile_solid_mask(lands, 0, 0, level)))
    assert root.children[1].name == b'WATER'
    assert root.children[1].children[0].data.num_triangles == 2 * len(surfaces)


@pytest.mark.parametrize('level', [4, 8, 16, 32])
def test_water_only_tile_has_no_ground_faces_and_keeps_water_slot_and_bounds(level):
    heights, _ = lod._assemble_tile({}, 0, 0, level, -2048)
    quads = lod._tile_water_quads({}, {}, 0, 0, level, 1500, -2048)
    root = _read(build_terrain_nif(heights, 0, 0, level, 'Ocean',
        water_quads=quads, solid_mask=tile_solid_mask({}, 0, 0, level)))
    assert len(root.children) == 2
    assert root.children[0].name == b'land'
    assert root.children[0].data.num_triangles == 0
    assert root.children[1].name == b'WATER'
    geometry = root.children[1].children[0]
    assert geometry.data.num_triangles == 2 * level * level
    assert all(v.z * geometry.scale == pytest.approx(1500) for v in geometry.data.vertices)
    bounds = root.multi_bound.data
    assert bounds.position.z - bounds.extent.z <= -2048
    assert bounds.position.z + bounds.extent.z >= 1500
    if level == 4:
        assert geometry.num_segments == 16
        assert all(s.flags.bsseg_water for s in geometry.segment)


def test_ocean_gaps_are_queued_without_inventing_ground_or_ignoring_selection():
    lands = {(0, 0): {}, (8, 0): {}}
    work = lod._queue_tiles(lands, (0, 0, 8, 0), 'Ocean', None, {}, 0, -2048)
    assert {t[:2] for t in work if t[2] == 4} == {(0, 0), (4, 0), (8, 0)}
    chosen = lod._queue_tiles(lands, (0, 0, 8, 0), 'Ocean', {(5, 0)}, {}, 0, -2048)
    assert {t[:2] for t in chosen if t[2] == 4} == {(4, 0)}
    assert not lod._queue_tiles({}, (0, 0, 3, 3), 'Dry', None, {}, 0, 100)
    assert not lod._queue_tiles({}, (0, 0, 31, 31), 'Dry', None,
        {(x, y): (False, None) for x in range(32) for y in range(32)}, 0, -2048)


def test_world_default_ground_blocks_water_only_where_it_is_above_the_surface():
    water = {(0, 0): (True, 200)}
    assert lod._tile_water_quads({}, water, 0, 0, 4, 0, 100) == [(0, 0, 200)]
    assert not lod._tile_water_quads({}, {}, 0, 0, 4, float('nan'), -2048)


@pytest.mark.parametrize('workers', [1, 2])
def test_generate_world_with_water_cell_and_no_land_records(tmp_path, monkeypatch, workers):
    world = 0x00000800
    sub = lambda tag, value: pack_subrecord(tag, value)
    wrld = pack_record('WRLD', world, 0,
        sub('EDID', b'Ocean\0') + sub('DNAM', struct.pack('<ff', -2048, 35)))
    cell = pack_record('CELL', 0x00000801, 0,
        sub('XCLC', struct.pack('<iiI', 0, 0, 0)) + sub('DATA', b'\2\0'))
    plugin = tmp_path / 'Ocean.esm'
    plugin.write_bytes(pack_tes4_header([]) + pack_group(0, b'WRLD',
        wrld + pack_group(1, struct.pack('<I', world), cell)))
    monkeypatch.setattr(lod, 'worker_count', lambda: workers)
    monkeypatch.setattr(lod, 'LOD_LEVELS', [4])
    monkeypatch.setattr(lod, 'CELL_DIFFUSE_PX', 8)
    monkeypatch.setitem(lod.TEX_SIZE_BY_LEVEL, 4, 32)
    output = tmp_path / 'out'
    assert lod.generate_terrain_lod(plugin, output, 'Ocean')
    btr = output / 'meshes' / 'terrain' / 'Ocean' / 'Ocean.4.0.0.btr'
    root = _read(btr.read_bytes())
    assert root.children[0].data.num_triangles == 0
    assert root.children[1].children[0].data.num_triangles == 32
    assert all(v.z * 4 == pytest.approx(35) for v in root.children[1].children[0].data.vertices)
    assert (output / 'textures' / 'terrain' / 'Ocean' / 'Ocean.4.0.0.dds').is_file()
