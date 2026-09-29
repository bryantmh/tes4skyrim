"""Synthetic horizon cells meet each other, and every corner, without a crack.

Neighbouring baked LOD meshes disagree on their shared 32-cell border
(Oblivion Tamriel: 583 of 989 synthetic|synthetic edges on those seams, up to
1,968 units), and the old per-edge feather moved a cell's corner without
telling its synthetic neighbour (corners off by up to 7,592). Every synthetic
edge now targets the mean of both raw edges, every corner one shared value
(LAND there wins), and a Coons blend bends each cell to meet all four.

See: docs/commentary/asset_convert_terrain.md#synthetic-seam-stitch
"""

import numpy as np

from asset_convert.lod import terrain_lod_baked as tb


def _flat(z):
    """A 33x33 height patch at `z`."""
    return np.full((33, 33), float(z), dtype=np.float32)


def _slope(x, y):
    """Cell (x, y) of one continuous plane, so every shared edge agrees."""
    ys, xs = np.mgrid[0:33, 0:33]
    return (7.0 * (x * 32 + xs) + 3.0 * (y * 32 + ys)).astype(np.float32)


def _land(h):
    """A LAND cell dict with heights `h`."""
    return {'heights': np.asarray(h, dtype=np.float32),
            'colors': np.full((33, 33, 3), 200, np.uint8),
            'layers': {'base': {0: 1}, 'alpha': {}}}


def _world(lands):
    """A dry world holding `lands`."""
    return {'lands': dict(lands), 'cell_water': {}, 'default_wh': 0.0,
            'deleted': set()}


def test_two_synthetic_cells_meet_on_their_mean_edge():
    """0 west of 100: both sides of the seam become 50; the far edges stay put."""
    world = _world({})

    tb.synthesize(world, {(31, 0): _flat(0), (32, 0): _flat(100)})

    west, east = world['lands'][(31, 0)]['heights'], world['lands'][(32, 0)]['heights']
    assert np.array_equal(west[:, -1], east[:, 0])
    assert np.allclose(west[:, -1], 50.0)
    assert np.all(west[:, 0] == 0.0) and np.all(east[:, -1] == 100.0)


def test_cells_that_already_agree_are_left_bit_identical():
    """The control: a continuous surface split into cells is not touched."""
    heights = {(x, y): _slope(x, y) for x in range(3) for y in range(3)}
    world = _world({})

    tb.synthesize(world, {k: v.copy() for k, v in heights.items()})

    for key, want in heights.items():
        assert np.array_equal(world['lands'][key]['heights'], want), key


def test_a_corner_shared_only_diagonally_with_land_takes_the_land_height():
    """LAND at (0,0); synthetic (1,0), (0,1), (1,1): all four meet at LAND's NE corner."""
    land = _flat(500)
    world = _world({(0, 0): _land(land)})
    heights = {(1, 0): _flat(0), (0, 1): _flat(40), (1, 1): _flat(-300)}

    tb.synthesize(world, heights)

    got = world['lands']
    assert got[(1, 1)]['heights'][0, 0] == 500.0
    assert np.array_equal(got[(1, 0)]['heights'][:, 0], land[:, -1]), 'LAND edge exact'
    assert np.array_equal(got[(0, 1)]['heights'][0, :], land[-1, :]), 'LAND edge exact'
    assert np.array_equal(got[(1, 0)]['heights'][-1, :], got[(1, 1)]['heights'][0, :])
    assert np.array_equal(got[(0, 1)]['heights'][:, -1], got[(1, 1)]['heights'][:, 0])


def test_the_fit_meets_all_four_targets_and_blends_between():
    """Coons: every edge exact, the middle a smooth blend, the free shape kept."""
    h = _slope(0, 0)
    targets = {'west': h[:, 0] + 100.0, 'south': h[0, :] + np.linspace(100, 0, 33)}

    got = tb.fit_edges(h, targets)

    assert np.array_equal(got[:, 0], targets['west'].astype(np.float32))
    assert np.array_equal(got[0, :], targets['south'].astype(np.float32))
    assert np.allclose(got[:, -1], h[:, -1]), 'east free, both its corners unmoved'
    assert np.allclose(got[-1, :] - h[-1, :], np.linspace(100, 0, 33), atol=1e-3)
    assert 0 < got[16, 16] - h[16, 16] < 100
