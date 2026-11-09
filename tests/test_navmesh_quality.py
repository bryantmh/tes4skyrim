"""Regression tests for the navmesh triangle-quality contract.

The native decimate and sliver-cull kernels are called directly.  Each test
encodes a measured failure that must not come back:

* decimation pinching a thin neck (boundary pair joined by an interior edge)
  -- shed vertex-attached scraps on BarrenCave;
* the sliver cull disconnecting what it trims, or removing the sole cover of
  a walked line.
"""

import math

import pytest

pytest.importorskip("numpy")
pytest.importorskip("shapely")

import numpy as np

from tes5_import.navmesh import clean_validate, native_loader, params


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _components(tris):
    """Shared-edge components of a triangle list."""
    return clean_validate.components([list(map(int, t)) for t in tris])


def _cull(verts, tris, pin_xy=()):
    """The native sliver cull at the import's area budget; the kept triangles."""
    nan = float('nan')
    area2 = sum(abs((verts[b][0] - verts[a][0]) * (verts[c][1] - verts[a][1])
                    - (verts[b][1] - verts[a][1]) * (verts[c][0] - verts[a][0]))
                for (a, b, c) in tris)
    samples = np.asarray([(x, y, z, nan, nan) for (x, y, z) in pin_xy],
                         dtype=np.float64).reshape(-1, 5)
    keep = native_loader.native_core().cull_slivers(
        np.asarray(verts, dtype=np.float64), np.zeros(len(verts), dtype=np.uint8),
        np.asarray(tris, dtype=np.int32), np.zeros(len(verts), dtype=np.uint8), samples,
        None, params.CULL_SLIVER_AREA_FRAC * area2)
    return [tuple(tris[i]) for i in keep]


# ---------------------------------------------------------------------------
# decimate: topology guards
# ---------------------------------------------------------------------------

def test_decimate_never_pinches_a_thin_neck():
    """Two boundary vertices joined by an INTERIOR edge may not collapse.

    Regression: collapsing across the neck of a thin peninsula fuses the
    outline at a point; the peninsula then hangs vertex-attached and the
    engine sees a separate component (BarrenCave went [1768, 6, 5, 3]).
    """
    # Two 'lobes' joined by a narrow two-triangle neck whose crossing edge
    # (4, 5) is interior and SHORT — the collapse candidate.
    verts = [
        [0, 0, 0], [120, 0, 0], [120, 100, 0], [0, 100, 0],      # lobe A
        [140, 40, 0], [140, 60, 0],                              # neck
        [260, 0, 0], [260, 100, 0], [380, 0, 0], [380, 100, 0],  # lobe B
    ]
    tris = [
        (0, 1, 2), (0, 2, 3),
        (1, 4, 2), (2, 4, 5),
        (4, 6, 5), (5, 6, 7),
        (6, 8, 7), (7, 8, 9),
    ]
    n0 = len(_components(tris))
    _v2, t2 = native_loader.native_core().decimate(
        [list(map(float, v)) for v in verts], [tuple(t) for t in tris], None, None,
        params.DECIMATE_ROUNDS, True, None, None)
    assert len(_components(t2)) <= n0, "decimation disconnected the mesh"


# ---------------------------------------------------------------------------
# cull_boundary_slivers
# ---------------------------------------------------------------------------

def test_cull_removes_a_boundary_needle_but_keeps_connectivity():
    # A healthy quad with one thin flap atop its boundary edge (3, 2):
    # ratio ~3.3 (> CULL_SLIVER_RATIO), area 250 (< MIN_TRI_AREA).
    verts = [
        [0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0],
        [70, 105, 0],
    ]
    tris = [(0, 1, 2), (0, 2, 3), (3, 2, 4)]
    out = _cull([list(map(float, v)) for v in verts], tris)
    assert (3, 2, 4) not in out, "the needle should be culled"
    assert len(_components(out)) == 1


def test_cull_spares_the_sole_cover_of_a_walked_line():
    """A sliver that is the ONLY cover of a pathgrid sample must survive.

    (The blanket rule — any sample inside the triangle protects it — was
    refined: a sample that stays covered by neighbours after the cull no
    longer blocks it.  So the fixture's sample sits well past the shared
    edge, where only the sliver itself covers it.)
    """
    verts = [
        [0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0],
        [70, 130, 0],
    ]
    tris = [(0, 1, 2), (0, 2, 3), (3, 2, 4)]
    out = _cull([list(map(float, v)) for v in verts], tris, [(70.0, 115.0, 0.0)])
    assert (3, 2, 4) in out, "the sole cover of the walked line must survive"


# ---------------------------------------------------------------------------
# The shape contract constant
# ---------------------------------------------------------------------------

def test_edge_ratio_contract_is_two():
    """The author-set contract: long side <= 2x the short side."""
    assert params.MAX_EDGE_RATIO == 2.0
    assert params.CULL_SLIVER_RATIO == 2.0
    assert params.MIN_TRI_AREA > 0


# ---------------------------------------------------------------------------
# METRIC regression tests.  These guard the DIAGNOSTICS, not the generator:
# ---------------------------------------------------------------------------

class _FakeCell(object):
    def __init__(self, nodes, edges, doors=()):
        self.nodes = nodes
        self.edges = edges
        self.doors = list(doors)

    def walked_samples(self, step=16.0):
        for (a, b) in self.edges:
            pa, pb = self.nodes[a], self.nodes[b]
            n = max(2, int(math.dist(pa[:2], pb[:2]) / step) + 1)
            for i in range(n + 1):
                f = i / n
                yield (pa[0] + (pb[0] - pa[0]) * f,
                       pa[1] + (pb[1] - pa[1]) * f,
                       pa[2] + (pb[2] - pa[2]) * f)


def _strip_with_hole():
    """Two coplanar plates 64u apart: a walked line crosses the void."""
    verts = [(0.0, -100.0, 0.0), (200.0, -100.0, 0.0),
             (200.0, -32.0, 0.0), (0.0, -32.0, 0.0),
             (0.0, 32.0, 0.0), (200.0, 32.0, 0.0),
             (200.0, 100.0, 0.0), (0.0, 100.0, 0.0)]
    tris = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)]
    return verts, tris


def test_walked_gap_reports_the_contiguous_run_not_a_sample_count():
    from tools.navmesh import metrics
    verts, tris = _strip_with_hole()
    cell = _FakeCell([(100.0, -100.0, 0.0), (100.0, 100.0, 0.0)], [(0, 1)])
    surf = metrics.Surface(verts, tris)
    gaps = metrics.walked_gaps(surf, cell)
    assert gaps, 'a walked line crossing a 64u void must report a gap'
    # The run is what matters: it is far longer than one 16u sample.
    assert gaps[0][0] >= 48.0, gaps[0]


def test_door_gap_is_reported_separately_from_ordinary_misses():
    from tools.navmesh import metrics
    verts, tris = _strip_with_hole()
    # A door sits exactly on the void: this is the unwalkable case.
    cell = _FakeCell([(100.0, -100.0, 0.0), (100.0, 100.0, 0.0)], [(0, 1)],
                     doors=[(100.0, 0.0, 0.0, 0.0, 'D1', False, 96.0)])
    surf = metrics.Surface(verts, tris)
    assert metrics.door_gaps(surf, cell), \
        'a gap inside a doorway must be flagged as a door gap'


def test_no_door_gap_when_the_doorway_is_meshed():
    from tools.navmesh import metrics
    verts = [(0.0, -100.0, 0.0), (200.0, -100.0, 0.0),
             (200.0, 100.0, 0.0), (0.0, 100.0, 0.0)]
    tris = [(0, 1, 2), (0, 2, 3)]
    cell = _FakeCell([(100.0, -100.0, 0.0), (100.0, 100.0, 0.0)], [(0, 1)],
                     doors=[(100.0, 0.0, 0.0, 0.0, 'D1', False, 96.0)])
    surf = metrics.Surface(verts, tris)
    assert not metrics.door_gaps(surf, cell)
