"""Regression tests for the hand-correction-driven corridor fixes.

Each test encodes a measured failure from the ImperialDungeon02/03 corrections
(docs/commentary/tes5_import_navmesh.md):

* a pathgrid node in the seam between two tread boxes keeps its own height;
* a steep edge whose line skips treads still gets a profile from the treads
  beside it, and falls back to a clamped chord rather than a sinking one;
* a station blocked on both sides at the centerline takes its neighbours'
  widths instead of collapsing the ribbon to a point;
* outline densification lands opposite rails on the same stations.
"""

import math

import pytest

pytest.importorskip("numpy")

from tes5_import.navmesh import (clean_decimate, clean_validate, corridor,
                                 params, union_cdt)


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


def test_profile_finds_treads_beside_the_line():
    """The line jumps 53u between layers; the treads beside it close the path."""
    regions = [(-1000.0, -1000.0, 1000.0, 1000.0, -1248.0),
               (0.0, 40.0, 30.0, 200.0, -1230.0),
               (30.0, 40.0, 60.0, 200.0, -1213.0),
               (60.0, 40.0, 90.0, 200.0, -1195.0),
               (90.0, -200.0, 400.0, 200.0, -1184.0)]
    sample = _Sampler(regions)
    prof = corridor._surface_profile(sample, (0.0, 0.0, -1248.0),
                                     (200.0, 0.0, -1184.0), half=64.0)
    assert prof is not None
    zs = [p[2] for p in prof]
    assert zs[0] == -1248.0 and zs[-1] == -1184.0
    assert all(b - a <= params.MAX_CLIMB + 1e-6 for a, b in zip(zs, zs[1:]))
    assert all(z >= -1248.0 - 1e-6 for z in zs)


def test_clamped_chord_holds_each_level():
    """No treads at all: each floor is held as far as it runs, one ramp between."""
    regions = [(-1000.0, -1000.0, 80.0, 1000.0, -1248.0),
               (120.0, -1000.0, 1000.0, 1000.0, -1184.0)]
    sample = _Sampler(regions)
    prof = corridor._surface_profile(sample, (0.0, 0.0, -1248.0),
                                     (200.0, 0.0, -1184.0), half=64.0)
    assert prof is not None
    assert all(p[2] == -1248.0 for p in prof if p[0] <= 80.0)
    assert all(p[2] == -1184.0 for p in prof if p[0] >= 120.0)
    assert any(-1248.0 < p[2] < -1184.0 for p in prof)


def test_overlapping_plateaus_keep_the_chord():
    """The lower floor runs under the upper: no honest ramp, the chord stays."""
    regions = [(-1000.0, -1000.0, 1000.0, 1000.0, -1248.0),
               (50.0, -1000.0, 1000.0, 1000.0, -1184.0)]
    sample = _Sampler(regions)
    assert corridor._surface_profile(sample, (0.0, 0.0, -1248.0),
                                     (200.0, 0.0, -1184.0), half=64.0) is None


def test_needle_split_sits_on_the_floor_under_it():
    """A split spanning two levels takes the ground, not the chord.

    ImperialDungeon01's ramp foot: bisecting the -590 to -632 edge put the new
    vertex at the chord's -614.6, 17.6u above the -632 floor there, leaving the
    floor rim and ramp rim as parallel boundaries over the same ground.
    """
    verts = [[0.0, 0.0, -590.0], [100.0, 0.0, -632.0]]
    sample = _Sampler([(-500.0, -500.0, 500.0, 500.0, -632.0),
                       (-500.0, -500.0, 20.0, 500.0, -590.0)])
    mid = clean_decimate._apex_split_point(verts, 0, 1, (60.0, 40.0, -632.0),
                                           100.0, sample)
    assert mid[2] == pytest.approx(-632.0)
    chord = clean_decimate._apex_split_point(verts, 0, 1, (60.0, 40.0, -632.0),
                                             100.0)
    assert chord[2] == pytest.approx(-616.88)


def test_needle_split_keeps_the_chord_without_a_sampler():
    """No oracle: the split is exactly where it always was."""
    verts = [[0.0, 0.0, 0.0], [100.0, 0.0, 40.0]]
    mid = clean_decimate._apex_split_point(verts, 0, 1, (50.0, 10.0, 0.0),
                                           100.0)
    assert mid[2] == pytest.approx(25.6)


def test_blocked_stations_take_neighbour_widths():
    """Six stations zeroed on both sides come back interpolated."""
    k = 9
    widths = [30.0, 32.0] * (k + 1)
    for s in range(3, 7):
        widths[2 * s] = widths[2 * s + 1] = 0.0
    widths[2 * 7], widths[2 * 7 + 1] = 40.0, 40.0
    plan = [('edge', (0, 1), None, None, None, None, 0.0, k, 0)]
    corridor._bridge_blocked_stations(widths, plan)
    assert widths[2 * 5] == pytest.approx(30.0 + 10.0 * (5 - 2) / 5.0)
    assert widths[2 * 5 + 1] == pytest.approx(32.0 + 8.0 * (5 - 2) / 5.0)
    assert all(w > 0.0 for w in widths)


def test_one_sided_blocking_is_left_alone():
    """A wall close on one side only is a real wall; nothing is bridged."""
    widths = [30.0, 0.0, 30.0, 0.0, 30.0, 0.0]
    plan = [('edge', (0, 1), None, None, None, None, 0.0, 2, 0)]
    corridor._bridge_blocked_stations(widths, plan)
    assert widths == [30.0, 0.0, 30.0, 0.0, 30.0, 0.0]


def test_grid_stations_align_opposite_rails():
    """Two parallel rails hit the same along-direction stations."""
    top = union_cdt._grid_stations(10.0, 100.0, 500.0, 100.0, 128.0)
    bottom = union_cdt._grid_stations(500.0, 0.0, 10.0, 0.0, 128.0)
    assert [round(p[0]) for p in top] == [128, 256, 384]
    assert sorted(round(p[0]) for p in bottom) == [128, 256, 384]


def test_grid_stations_keep_clear_of_segment_ends():
    """A station within a quarter spacing of an end would only mint a sliver."""
    pts = union_cdt._grid_stations(120.0, 0.0, 270.0, 0.0, 128.0)
    assert [round(p[0]) for p in pts] == []
    pts = union_cdt._grid_stations(90.0, 0.0, 300.0, 0.0, 128.0)
    assert [round(p[0]) for p in pts] == [128, 256]
    assert all(math.isfinite(p[1]) for p in pts)


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


def test_disc_ray_runs_on_only_over_level_ground():
    """Past its ribbons' reach a disc continues over ground AT its level and
    stops where the floor is a step down."""
    def layers(x, y):
        """Floor at 0 to x=100, then a floor 27u lower."""
        return [0.0] if x <= 100.0 else [-27.0]

    assert corridor._level_reach(layers, 0.0, 0.0, 0.0, 1.0, 0.0, 40.0, 160.0) == 96.0
    assert corridor._level_reach(layers, 0.0, 0.0, 0.0, 1.0, 0.0, 40.0, 80.0) == 80.0

def _land_group():
    """A sheet group the slit-closer treats as terrain."""
    return [{'land': object()}]


def test_land_slit_repairs_an_invalid_buffer_ring():
    """An invalid buffered ring is repaired, not thrown out of.

    The mitre round-trip self-intersects on spiky terrain outlines; GEOS then
    throws out of `difference` and the whole CELL loses its navmesh -- measured
    as 13 Oblivion exteriors and 1 TR_Mainland cell with no navmesh at all.

    See: docs/commentary/tes5_import_navmesh.md#land-slit-buffer-must-be-repaired
    """
    from shapely.geometry import Polygon
    from tes5_import.navmesh import corridor_union as cu

    bowtie = Polygon([(0, 0), (100, 100), (100, 0), (0, 100)])
    assert not bowtie.is_valid
    calls = []
    out = cu._close_land_slits(bowtie, _land_group(),
                               lambda p: calls.append(p) or True)
    assert out is not None and not out.is_empty


def test_land_slit_failure_keeps_the_sheet():
    """Any GEOS failure in the fill returns the sheet, never propagates.

    Covers the cell whose polygons are both VALID and still fail inside GEOS's
    overlay (Oblivion 01006879), where repairing the ring does not help.
    """
    from shapely.errors import GEOSException
    from shapely.geometry import Polygon
    from tes5_import.navmesh import corridor_union as cu

    def boom(_p):
        """Stand in for a shapely call that throws inside the fill."""
        raise GEOSException('overlay failed')

    poly = Polygon([(0, 0), (400, 0), (400, 90), (0, 90)])
    assert cu._close_land_slits(poly, _land_group(), boom) is poly


def test_land_slit_skips_non_terrain_sheets():
    """A sheet with no LAND is returned untouched."""
    from shapely.geometry import Polygon
    from tes5_import.navmesh import corridor_union as cu
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert cu._close_land_slits(poly, [{}], lambda _p: True) is poly
