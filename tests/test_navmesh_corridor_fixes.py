"""Regression tests for the hand-correction-driven corridor fixes.

Each test encodes a measured failure from the ImperialDungeon02/03 corrections
(docs/commentary/tes5_import_navmesh.md):

* a pathgrid node in the seam between two tread boxes keeps its own height;
* a ledge lip is pushed out to the floor's edge, and a railing drops the link.
"""

import pytest

pytest.importorskip("numpy")

from tes5_import.navmesh import clean_validate, corridor, params


class _Sampler(object):
    """A walkable sampler over axis-aligned plateaus `(x0, y0, x1, y1, z)`."""

    def __init__(self, regions):
        """Keep the plateau list."""
        self.regions = regions

    def _layers(self, x, y):
        """Every plateau height covering (x, y), ascending."""
        out = []
        for (x0, y0, x1, y1, z) in self.regions:
            if x0 <= x <= x1 and y0 <= y <= y1:
                out.append(z)
        return sorted(out)

    def __call__(self, x, y, near_z):
        """The covering height nearest near_z, or None."""
        zs = self._layers(x, y)
        return min(zs, key=lambda z: abs(z - near_z)) if zs else None

    @property
    def layers(self):
        """The `.layers(x, y)` attribute the corridor sampler exposes."""
        return self._layers


def test_node_in_tread_seam_keeps_its_height():
    """Directly under the node only the floor beneath the stairs is walkable."""
    treads = [(0.0, 0.0, 95.0, 200.0, -1195.0), (105.0, 0.0, 200.0, 200.0, -1213.0),
              (-500.0, -500.0, 500.0, 500.0, -1248.0)]
    sample = _Sampler(treads)
    assert corridor._snap_node_z(sample, 100.0, 100.0, -1200.0) == -1195.0


def test_node_far_from_any_surface_still_clamps():
    """No surface within the radii either: the old clamp rule is unchanged."""
    sample = _Sampler([(-500.0, -500.0, 500.0, 500.0, -400.0)])
    assert corridor._snap_node_z(sample, 0.0, 0.0, 0.0) == -params.SEED_SNAP


def _two_storeys():
    """An upper slab whose lip is 30u short of its edge, a lower slab below."""
    verts = [[0.0, 0.0, 192.0], [100.0, 0.0, 192.0], [100.0, 70.0, 192.0],
             [0.0, 70.0, 192.0],
             [0.0, 100.0, 0.0], [100.0, 100.0, 0.0], [100.0, 200.0, 0.0],
             [0.0, 200.0, 0.0]]
    tris = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)]
    return verts, tris


def test_lip_pushed_to_floor_edge():
    """The lip (y=70) moves out to where the reach says the floor ends."""
    verts, tris = _two_storeys()

    def reach(x, y, z, dx, dy, limit):
        """Floor runs 30u further, no wall."""
        return 30.0, False

    links = clean_validate.find_ledge_links(verts, tris, reach=reach)
    assert links and links[0][0] in (0, 1) and links[0][1] in (2, 3)
    assert verts[2][1] == pytest.approx(70.0 + 30.0 - clean_validate.LIP_STANDOFF)
    assert verts[3][1] == pytest.approx(70.0 + 30.0 - clean_validate.LIP_STANDOFF)
    assert verts[0][1] == 0.0 and verts[1][1] == 0.0


def test_railing_blocked_pair_is_dropped():
    """A wall before the edge means no actor can drop there: no link."""
    verts, tris = _two_storeys()

    def reach(x, y, z, dx, dy, limit):
        """A railing 12u out."""
        return 12.0, True

    assert clean_validate.find_ledge_links(verts, tris, reach=reach) == []
    assert verts[2][1] == 70.0


def test_no_reach_keeps_old_behaviour():
    """Without a reach oracle the pair is linked and nothing moves."""
    verts, tris = _two_storeys()
    links = clean_validate.find_ledge_links(verts, tris)
    assert len(links) == 1 and verts[2][1] == 70.0
