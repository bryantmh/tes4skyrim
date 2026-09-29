"""A later plugin's no-LAND fill never replaces an earlier plugin's relief.

The editor bakes a LOD cell with no LAND as a flat plane at the worldspace's
default land height. Frostcrag Reborn ships its own 60.00.32 mesh with 96
horizon cells flat at that height (and 32 more ramping down to it) where
Oblivion.esm's mesh has 18k-46k mountains; "last source wins" put a 44,416
unit cliff at the y=64 tile seam. The fill height comes from the meshes (the
height holding the most flat area), never a constant.

See: docs/commentary/asset_convert_terrain.md#baked-lod-fill
"""

import numpy as np

from asset_convert.lod import terrain_lod_baked as tb

FILL = -300.0     # deliberately not Oblivion's value: nothing may assume one


def _grid(z):
    """A whole-tile raster at height `z`."""
    return np.full((tb.TILE_SAMPLES, tb.TILE_SAMPLES), z, dtype=np.float32)


def _relief():
    """A whole-tile raster with relief everywhere (a slope of 2 units per sample)."""
    ys, xs = np.mgrid[0:tb.TILE_SAMPLES, 0:tb.TILE_SAMPLES]
    return (1000.0 + 2.0 * xs + 3.0 * ys).astype(np.float32)


def test_a_later_fill_block_loses_to_an_earlier_source_with_relief():
    """Flat-fill and ramp-to-fill cells fall back; the later plugin keeps the rest."""
    base = _relief()
    patch = _relief() + 5.0
    patch[0:33, 0:33] = FILL                      # cell (0,0): perfectly flat fill
    patch[0:33, 32:65] = FILL                     # cell (1,0): ramp into the fill
    patch[0:4, 32:65] = base[0:4, 32:65] + 5.0

    cells, used = tb.tile_cells((0, 0), [('Base', base), ('Mod', patch)], FILL)

    assert np.array_equal(cells[(0, 0)], tb.cell_block(base, 0, 0))
    assert np.array_equal(cells[(1, 0)], tb.cell_block(base, 1, 0))
    assert np.array_equal(cells[(2, 0)], tb.cell_block(patch, 2, 0))
    assert used == {'Base': 2, 'Mod': 32 * 32 - 2}


def test_fill_everywhere_keeps_the_last_source_and_no_fill_height_is_the_old_rule():
    """Fill in both sources: the last wins. Without a fill height, the last wins too."""
    cells, _u = tb.tile_cells((0, 0), [('Base', _grid(FILL)), ('Mod', _grid(FILL))],
                              FILL)
    assert np.all(cells[(0, 0)] == FILL)

    patch = _grid(FILL)
    cells, used = tb.tile_cells((0, 0), [('Base', _relief()), ('Mod', patch)])
    assert np.all(cells[(0, 0)] == FILL) and used == {'Mod': 1024}


def test_a_flat_block_at_another_height_is_authored_and_kept():
    """A later plugin's flat sea floor below the fill height is its choice, not fill."""
    cells, used = tb.tile_cells((0, 0), [('Base', _relief()), ('Mod', _grid(-9000))],
                                FILL)

    assert np.all(cells[(5, 5)] == -9000) and used == {'Mod': 1024}


def _quad(x0, y0, x1, y1, z0, z1=None):
    """(verts, tris) of one quad from (x0,y0) to (x1,y1), z0 west, z1 east."""
    z1 = z0 if z1 is None else z1
    verts = np.array([[x0, y0, z0], [x1, y0, z1], [x0, y1, z0], [x1, y1, z1]],
                     dtype=np.float64)
    return verts, np.array([[0, 1, 2], [1, 3, 2]])


def test_the_fill_height_is_the_height_holding_the_most_flat_area():
    """Area, not triangle count: one big flat quad beats many small ones; slopes count 0."""
    big = _quad(0, 0, 40000, 40000, FILL)
    small = [_quad(i * 10, 0, i * 10 + 10, 10, 50.0) for i in range(50)]
    slope = _quad(0, 0, 90000, 90000, 0.0, 9000.0)

    assert tb.fill_height([big] + small + [slope]) == FILL
    assert tb.fill_height([slope]) is None
    assert tb.fill_height([]) is None


def test_baked_heights_derives_the_fill_from_every_mesh_before_choosing(monkeypatch):
    """The fill plane lives in the base mesh; the mod's fill cell still falls back."""
    span = tb.TILE_CELLS * tb.CELL_UNITS
    base_ring = _quad(-span, -span, 0, 0, FILL)            # past-the-map fill, other tile
    base = (np.concatenate([_quad(0, 0, span, span, 1000.0, 9000.0)[0],
                            base_ring[0]]),
            np.array([[0, 1, 2], [1, 3, 2], [4, 5, 6], [5, 7, 6]]))
    mod = _quad(0, 0, span, span, FILL)
    meshes = {'base.nif': base, 'mod.nif': mod}
    monkeypatch.setattr(tb, 'mesh_triangles', lambda p: meshes[p])
    logged = []

    out = tb.baked_heights({(0, 0): [('Base', 'base.nif'), ('Mod', 'mod.nif')]},
                           log=logged.append)

    assert np.ptp(out[(3, 3)]) > 100, 'relief, not the flat fill'
    assert any('fill height: -300.0' in line for line in logged)
