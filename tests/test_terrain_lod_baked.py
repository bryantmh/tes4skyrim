"""The source game's baked distant-land LOD: discovery, world-space raster, crops.

Oblivion's LOD meshes come in two frames: tile space with the tile origin as
the node translation, and (UOP's 60.64.00) world space with no translation.
Both must raster to the same heights. Plugins ship them as
`meshes\\Landscape\\LOD\\...NIF`, so discovery ignores case. The baked tile
textures are stored SOUTH-UP.

See: docs/commentary/asset_convert_terrain.md#baked-lod-sources
"""

import numpy as np
import pytest

from asset_convert import case_paths
from asset_convert.lod import terrain_lod_baked as tb


@pytest.fixture
def nif():
    """The patched NifFormat."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    return NifFormat


def _plane(x, y):
    """The fixture surface: z = 0.01x + 0.02y + 7."""
    return 0.01 * x + 0.02 * y + 7.0


def _scene(nif, corners, translation):
    """A pyffi scene of one strip quad over `corners` (x0, y0, x1, y1), translated."""
    shape = nif.NiTriStrips()
    data = nif.NiTriStripsData()
    shape.data = data
    x0, y0, x1, y1 = corners
    tx, ty = translation
    pts = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    data.num_vertices = 4
    data.has_vertices = True
    data.vertices.update_size()
    for v, (x, y) in zip(data.vertices, pts):
        v.x, v.y, v.z = x, y, _plane(x + tx, y + ty)
    data.set_strips([[0, 1, 2, 3]])
    shape.translation.x, shape.translation.y = tx, ty
    scene = nif.Data()
    scene.roots = [shape]
    return scene


def test_tile_frame_and_world_frame_meshes_raster_identically(nif):
    """T = tile origin with local verts, or T = 0 with world verts: same heights."""
    tile = (64, 0)
    ox, oy = tile[0] * 4096, tile[1] * 4096
    span = 32 * 4096
    local = _scene(nif, (0, 0, span, span), (ox, oy))
    world = _scene(nif, (ox, oy, ox + span, oy + span), (0, 0))

    a = tb.raster_world(*tb._world_triangles(local), *tile)
    b = tb.raster_world(*tb._world_triangles(world), *tile)

    assert a.shape == (1025, 1025) and not np.isnan(a).any()
    assert np.allclose(a, b, atol=1e-3)
    ys, xs = np.mgrid[0:1025, 0:1025]
    want = _plane(ox + xs * 128.0, oy + ys * 128.0)
    assert np.allclose(a, want, atol=1e-2), 'row 0 is south, column 0 is west'


def test_a_mesh_covering_part_of_a_tile_leaves_the_rest_nan(nif):
    """Only the covered cells get heights; a world-frame patch lands where it is."""
    tile = (0, 0)
    scene = _scene(nif, (4096, 8192, 3 * 4096, 4 * 4096), (0, 0))

    grid = tb.raster_world(*tb._world_triangles(scene), *tile)

    assert not np.isnan(tb.cell_block(grid, 1, 2)).any()
    assert not np.isnan(tb.cell_block(grid, 2, 3)).any()
    assert np.isnan(tb.cell_block(grid, 0, 0)).all()
    assert np.isnan(tb.cell_block(grid, 3, 2)[:, 1:]).all()


def test_each_cell_takes_the_last_source_that_covers_it():
    """A later plugin wins only the cells it covers fully; the rest fall back."""
    base = np.full((1025, 1025), 1.0, dtype=np.float32)
    patch = np.full((1025, 1025), np.nan, dtype=np.float32)
    patch[0:33, 0:65] = 2.0
    patch[0:33, 65] = 2.0

    cells, used = tb.tile_cells((0, 0), [('Oblivion.esm', base), ('UOP', patch)])

    assert cells[(0, 0)][0, 0] == 2.0 and cells[(1, 0)][0, 0] == 2.0
    assert cells[(2, 0)][0, 0] == 1.0, 'partial coverage does not win a cell'
    assert used == {'UOP': 2, 'Oblivion.esm': 32 * 32 - 2}


def _export(root, name, files):
    """A plain export record dir with Tamriel as FormID 3C and the given files."""
    d = root / name
    d.mkdir(parents=True)
    (d / 'WRLD.txt').write_text('FormID=0000003C\nEditorID=Tamriel\n',
                                encoding='utf-8')
    for rel in files:
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x')
    return d


def test_discovery_ignores_case_filters_the_worldspace_and_keeps_load_order(tmp_path):
    """Mixed-case folders are found; `_fn` and other worldspaces are not."""
    case_paths.invalidate()
    obl = _export(tmp_path, 'Oblivion.esm', [
        'meshes/landscape/lod/60.64.00.32.nif',
        'textures/landscapelod/generated/60.00.00.32.dds',
        'textures/landscapelod/generated/60.00.00.32_fn.dds',
        'textures/landscapelod/generated/99.00.00.32.dds'])
    uop = _export(tmp_path, 'UOP', [
        'meshes/Landscape/LOD/60.64.00.32.NIF',
        'Textures/LandscapeLOD/Generated/60.00.00.32.DDS'])

    meshes = tb.lod_meshes([obl, uop], 'TES4Tamriel')
    textures = tb.baked_textures([obl, uop], 'TES4Tamriel')

    assert [label for label, _p in meshes[(64, 0)]] == ['Oblivion.esm', 'UOP']
    assert meshes[(64, 0)][1][1].name == '60.64.00.32.NIF'
    assert set(textures) == {(0, 0)}
    assert textures[(0, 0)].name == '60.00.00.32.DDS', 'the last plugin wins'


def test_cell_crop_reads_the_south_up_tile_in_both_axes():
    """Per-cell colours (cx, cy) in a south-up tile come back where they belong."""
    n = 4
    tile = np.zeros((32 * n, 32 * n, 3), dtype=np.uint8)
    for cy in range(32):
        for cx in range(32):
            tile[cy * n:(cy + 1) * n, cx * n:(cx + 1) * n] = (cx * 8, cy * 8, 99)

    for cx, cy in ((3, 30), (30, 3), (0, 31), (31, 0)):
        crop = tb.cell_crop(tile, cx, cy, 16)
        assert crop.shape == (16, 16, 3)
        assert (crop == (cx * 8, cy * 8, 99)).all(), (cx, cy)


def test_a_renamed_worldspace_is_found_under_its_plugin_chain_s_name(tmp_path):
    """Arktwend's WrldMorrowind converts to WrldArktwend: by plugin name or by header master.

    The lookup must use the export's own chain, not the process's active one
    (which, in a LOD run, only renames Tamriel).
    """
    own = tmp_path / 'Arktwend_English.esm'
    own.mkdir()
    (own / 'WRLD.txt').write_text('FormID=0000003C\nEditorID=WrldMorrowind\n',
                                  encoding='utf-8')
    patch = tmp_path / 'SomePatch.esp'
    patch.mkdir()
    (patch / 'WRLD.txt').write_text('FormID=0000003C\nEditorID=WrldMorrowind\n',
                                    encoding='utf-8')
    (patch / '_HEADER.txt').write_text('Master[0]=Arktwend_English.esm\n',
                                       encoding='utf-8')
    other = tmp_path / 'Morrowind.esm'
    other.mkdir()
    (other / 'WRLD.txt').write_text('FormID=0000003C\nEditorID=WrldMorrowind\n',
                                    encoding='utf-8')

    assert tb.worldspace_fids(own, 'WrldArktwend') == {0x3C}
    assert tb.worldspace_fids(patch, 'WrldArktwend') == {0x3C}
    assert tb.worldspace_fids(own, 'WrldMorrowind') == {0x3C}, 'source name too'
    assert tb.worldspace_fids(other, 'WrldArktwend') == set(), 'not renamed there'
