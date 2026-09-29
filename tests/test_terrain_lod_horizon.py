"""Terrain LOD past the LAND records comes from the source game's baked LOD meshes.

Oblivion's horizon mountains exist only in its shipped LOD meshes; the LOD
tiles over them were never queued (no LAND), so the distance showed nothing.
A full run now adds a synthetic cell for every baked-mesh cell with no LAND:
heights feathered to meet real LAND exactly, LAND winning every shared vertex,
water where it dips below the worldspace water AND connects to LAND whose own
water shows, and never over a cell an overlay deleted.

See: docs/commentary/asset_convert_terrain.md#no-terrain-without-an-authored-source
"""

import numpy as np

from asset_convert.lod import terrain_lod
from asset_convert.lod import terrain_lod_baked as tb


def _flat(z):
    """A 33x33 height patch at `z`."""
    return np.full((33, 33), float(z), dtype=np.float32)


def _land(z):
    """A LAND cell dict of flat heights `z`, painted in quadrant 0."""
    return {'heights': _flat(z), 'colors': np.full((33, 33, 3), 200, np.uint8),
            'layers': {'base': {0: 1}, 'alpha': {}}}


def test_feather_meets_the_land_edge_and_fades_to_the_far_edge():
    """West edge becomes LAND's exactly; the east edge is untouched."""
    west = np.linspace(100, 200, 33).astype(np.float32)

    got = tb.feather_edges(_flat(0), west=west)

    assert np.array_equal(got[:, 0], west)
    assert np.all(got[:, -1] == 0)
    assert np.allclose(got[:, 16], west / 2, atol=1e-3)


def test_feather_keeps_a_corner_two_land_neighbours_agree_on():
    """West then south: the shared SW corner stays the value both LANDs hold."""
    west = np.full(33, 50.0, dtype=np.float32)
    south = np.full(33, 50.0, dtype=np.float32)
    south[-1] = 80.0

    got = tb.feather_edges(_flat(0), west=west, south=south)

    assert got[0, 0] == 50.0
    assert np.array_equal(got[0, :], south)
    assert np.allclose(got[:, 0], west, atol=1e-4)


def _world(**extra):
    """A world with one LAND cell at (0, 0), no water, default water height 0."""
    world = {'lands': {(0, 0): _land(50)}, 'cell_water': {}, 'default_wh': 0.0,
             'deleted': set()}
    world.update(extra)
    return world


def test_synthesize_fills_only_landless_cells_not_deleted_ones():
    """LAND and overlay-deleted cells are never replaced by baked heights."""
    world = _world(deleted={(3, 0)})
    heights = {k: _flat(10) for k in ((0, 0), (1, 0), (2, 0), (3, 0))}

    keys = tb.synthesize(world, heights)

    assert keys == {(1, 0), (2, 0)}
    lands = world['lands']
    assert lands[(0, 0)]['heights'][0, 0] == 50, 'LAND untouched'
    assert (3, 0) not in lands
    assert np.all(lands[(1, 0)]['heights'][:, 0] == 50), 'meets LAND'
    assert np.all(lands[(1, 0)]['heights'][:, -1] == 10), 'no feather to synthetic'
    assert np.all(lands[(2, 0)]['colors'] == 255)
    assert lands[(2, 0)]['layers'] == {'base': {}, 'alpha': {}}


def _wet_land(z):
    """A LAND cell dict whose terrain dips to `z` at one vertex (below water at 0)."""
    land = _land(50)
    land['heights'][10, 10] = z
    return land


def test_synthetic_cells_below_the_water_connected_to_land_water_get_it():
    """A below-water chain reaching LAND that shows water is wet; above-water is not."""
    world = _world(cell_water={(0, 0): (True, None)})
    world['lands'][(0, 0)] = _wet_land(-100)
    heights = {(1, 0): _flat(-200), (2, 0): _flat(-200), (3, 0): _flat(-200),
               (1, 3): _flat(10)}

    tb.synthesize(world, heights)

    for key in ((1, 0), (2, 0), (3, 0)):
        assert world['cell_water'][key] == (True, None), key
    assert (1, 3) not in world['cell_water'], 'wholly above the water'


def test_a_sunken_plain_not_connected_to_any_shown_water_stays_dry():
    """Pale Pass's case: LAND flagged with water but all above it; the fill plain is dry."""
    world = _world(cell_water={(0, 0): (True, None)})
    heights = {(1, 0): _flat(-512), (2, 0): _flat(-512)}

    tb.synthesize(world, heights)

    assert (1, 0) not in world['cell_water'] and (2, 0) not in world['cell_water']


def test_a_basin_cut_off_by_higher_ground_stays_dry():
    """(3,0) is below the water but (2,0), above it, separates it from the sea."""
    world = _world(cell_water={(0, 0): (True, None)})
    world['lands'][(0, 0)] = _land(-100)
    heights = {(1, 0): _flat(-200), (2, 0): _flat(300), (3, 0): _flat(-200)}

    tb.synthesize(world, heights)

    assert world['cell_water'][(1, 0)] == (True, None)
    assert (3, 0) not in world['cell_water']
    assert (2, 0) not in world['cell_water']


def test_land_wins_every_vertex_it_shares_with_a_synthetic_cell():
    """A synthetic cell written after LAND must not overwrite the shared column."""
    lands = {(0, 0): _land(50), (1, 0): _land(999)}

    heights, _c = terrain_lod._assemble_tile(lands, 0, 0, 4,
                                             synthetic=frozenset({(1, 0)}))

    assert np.all(heights[0:33, 32] == 50)
    assert np.all(heights[0:33, 33:65] == 999)


def test_a_full_run_queues_the_tile_that_holds_only_baked_cells(monkeypatch):
    """LAND at (0,0) plus baked heights at (40,1): the LOD32 tile at 32,0 bakes."""
    monkeypatch.setattr(tb, 'lod_meshes', lambda dirs, edid: {'m': 1})
    monkeypatch.setattr(tb, 'baked_heights',
                        lambda meshes, log=print: {(40, 1): _flat(5)})
    world = _world()

    keys = terrain_lod._horizon_cells(world, ['export'], 'W', None)

    assert keys == {(40, 1)}
    work = terrain_lod._queue_tiles(world['lands'], (0, 0, 40, 1), 'W', None)
    assert (32, 0, 32, 'W') in work and (0, 0, 32, 'W') in work


def test_a_partial_run_synthesizes_nothing(monkeypatch):
    """`only_cells` runs keep their tiles as a full run left them."""
    monkeypatch.setattr(tb, 'baked_heights', lambda *a, **k: 1 / 0)
    world = _world()

    assert terrain_lod._horizon_cells(world, ['export'], 'W', {(0, 0)}) == set()
    assert list(world['lands']) == [(0, 0)]


class _Rec:
    """A LAND record stand-in with no VHGT."""

    body = b''

    def sub(self, _tag):
        """No subrecord of any kind."""
        return None


def test_an_overlay_deleting_a_cell_records_it():
    """A VHGT-less overlay LAND erases the cell and marks it deleted."""
    lands, deleted = {(4, 4): _land(1)}, set()

    terrain_lod._take_land(_Rec(), (4, 4), lands, False, False, None, deleted)

    assert (4, 4) not in lands and deleted == {(4, 4)}


def test_a_max_cell_on_a_tile_boundary_still_gets_its_tile():
    """Bounds are inclusive: a cell at x = 32 needs the tile starting at 32."""
    lands = {(0, 0): _land(1), (32, 32): _land(1), (4, 0): _land(1)}

    work = terrain_lod._queue_tiles(lands, (0, 0, 32, 32), 'W', None)

    assert (32, 32, 32, 'W') in work
    assert (4, 0, 4, 'W') in work and (32, 32, 4, 'W') in work
    assert (0, 0, 32, 'W') in work
