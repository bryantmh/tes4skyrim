"""Phase-2 corridor width-grow: march an actor slab out to walls / floor edge.

Phase 1 lays a fixed-width flat ribbon on every pathgrid edge.  Phase 2 keeps
the centerline sacred (never moved — principle 1) and the ribbon flat on the
centerline plane (principle 2), but replaces the single fixed half-width with a
per-cross-section, per-side GROWN half-width.

From each cross-section center we step outward along the perpendicular.  Growth
stops one step before the FIRST of:

  (a) WALL — a thin vertical slab standing at the trial point intersects the
      blocking soup.  The slab is actor HEIGHT and (roughly) actor WIDTH along
      the edge tangent, but only a THIN sliver deep along the march direction,
      so the ribbon gets right up against the wall / doorway jamb instead of
      stopping an agent-radius short of it (that shortfall was narrowing every
      doorway).
  (b) FLOOR EDGE — the walkable surface under the trial point departs from the
      centerline floor Z by more than MAX_CLIMB, or there is no walkable surface
      there at all.  This is what stops the rail climbing onto a bed / table
      (its top is a step up) and what stops a ground-floor corridor widening
      sideways into the footprint of a different storey (the floor there is a
      whole storey away, so it reads as departed).  It is also what ends the
      march at a cell border: LAND covers exactly one cell, so "no walkable
      surface" stops open terrain growing into the neighbour.
  (c) THE CAP — RIBBON_GROW_MAX_HALF of BUILT floor.  LAND is free, so a rail
      on open terrain runs until real geometry stops it; once it has crossed
      another pathgrid centerline it ends at max(crossing, cap).  A
      terrain-standing rail measures (a) and (b) above the LAND, not above its
      own chord.
      See: docs/commentary/tes5_import_navmesh.md#exterior-coverage-one-code-path

The march measures against FIXED geometry (the same blocking/walkable soups
every time), never against other corridors' already-grown width, so it is
order-independent and the output stays byte-reproducible.  The overlap the
grow creates at junctions and between parallel corridors is resolved by the
boolean union in corridor_union.
"""

import math

import numpy as np

from . import params
from ._native_loader import load_native

_native = load_native('_navgrow_native')


# ---------------------------------------------------------------------------
# Batched native march
# ---------------------------------------------------------------------------

def _native_params(land_from=None, land=None):
    """The tunables the native march needs, mirrored from params.py.

    See: docs/commentary/tes5_import_navmesh.md#grow-is-batched-into-one-native-call
    """
    return {
        'land_from': float(2 ** 31 - 1 if land_from is None else land_from),
        'land_ox': land.ox if land is not None else 0.0,
        'land_oy': land.oy if land is not None else 0.0,
        'step': float(params.RIBBON_GROW_STEP),
        'min_half': float(params.RIBBON_GROW_MIN_HALF),
        'half_width': float(params.RIBBON_HALF_WIDTH),
        'slab_half_w': float(params.RIBBON_GROW_SLAB_HALF_WIDTH),
        'slab_depth': float(params.RIBBON_GROW_SLAB_DEPTH),
        'slab_z_bottom': float(params.RIBBON_GROW_SLAB_Z_BOTTOM),
        'agent_height': float(params.AGENT_HEIGHT),
        'max_climb': float(params.MAX_CLIMB),
        'ztol': float(params.SEED_Z_TOLERANCE),
        'cap': float(params.RIBBON_GROW_MAX_HALF),
        'bisect': float(params.RIBBON_GROW_BISECT),
    }


def grow_batch(blocking, walkable, stations,
               nodes=None, edges=None, node_z=None, land_from=None,
               land=None, on_land=None):
    """Grown half-width for every march station, in ONE native call.

    stations: (N, 9) float64 -- cx, cy, cz, dirx, diry, tanx, tany, lo,
    edge_index (an index into `edges`, or -1 - node_index for a node disc).
    nodes/edges/node_z enable the crossing stop.  `land_from` is where LAND
    starts in `walkable`; `land` (a world.LandField) and the per-station
    `on_land` flags make those rails follow the terrain.
    See: docs/commentary/tes5_import_navmesh.md#grow-is-batched-into-one-native-call
    """
    if not len(stations):
        return np.zeros(0, dtype=np.float64)
    blk = np.ascontiguousarray(blocking, dtype=np.float64).reshape(-1, 3, 3) \
        if len(blocking) else np.zeros((0, 3, 3), dtype=np.float64)
    wlk = (np.ascontiguousarray(walkable, dtype=np.float64).reshape(-1, 3, 3)
           if walkable is not None and len(walkable) else None)
    st = np.ascontiguousarray(stations, dtype=np.float64).reshape(-1, 9)
    nd = eg = nz = None
    if nodes is not None and edges is not None and len(edges):
        nd = np.ascontiguousarray(
            [(float(p[0]), float(p[1])) for p in nodes],
            dtype=np.float64).reshape(-1, 2)
        eg = np.ascontiguousarray(edges, dtype=np.int32).reshape(-1, 2)
        nz = np.ascontiguousarray(node_z, dtype=np.float64).reshape(-1)
    return _native.grow_strips(blk, wlk, st, _native_params(land_from, land),
                               nd, eg, nz,
                               land.grid if land is not None else None,
                               on_land if land is not None else None)


# ---------------------------------------------------------------------------
# Shared multi-bucket triangle index
# ---------------------------------------------------------------------------

class _TriGrid:
    """Coarse XY bucket index over a triangle soup, with a 3x3-bucket query.

    See: docs/commentary/tes5_import_navmesh.md#trigrid-queries-nine-buckets
    """

    __slots__ = ('B', 'cell', 'minx', 'miny', 'grid',
                 'x0', 'x1', 'y0', 'y1', 'z0', 'z1')

    def __init__(self, tris, cell=128.0):
        """Bucket every triangle by the plan cells its bbox spans."""
        self.B = np.asarray(tris, dtype=float).reshape(-1, 3, 3)
        self.cell = cell
        self.grid = {}
        if not len(self.B):
            self.minx = self.miny = 0.0
            self.x0 = self.x1 = self.y0 = self.y1 = self.z0 = self.z1 = None
            return
        self.x0 = self.B[:, :, 0].min(axis=1)
        self.x1 = self.B[:, :, 0].max(axis=1)
        self.y0 = self.B[:, :, 1].min(axis=1)
        self.y1 = self.B[:, :, 1].max(axis=1)
        self.z0 = self.B[:, :, 2].min(axis=1)
        self.z1 = self.B[:, :, 2].max(axis=1)
        self.minx = float(self.x0.min())
        self.miny = float(self.y0.min())
        for i in range(len(self.B)):
            gx0 = int((self.x0[i] - self.minx) // cell)
            gx1 = int((self.x1[i] - self.minx) // cell)
            gy0 = int((self.y0[i] - self.miny) // cell)
            gy1 = int((self.y1[i] - self.miny) // cell)
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    self.grid.setdefault((gx, gy), []).append(i)

    def candidates(self, x, y):
        """Triangle indices in the bucket at (x, y) and its eight neighbours."""
        if not self.grid:
            return ()
        gx = int((x - self.minx) // self.cell)
        gy = int((y - self.miny) // self.cell)
        out = []
        seen = out.append
        got = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.grid.get((gx + dx, gy + dy), ()):
                    if i not in got:
                        got.add(i)
                        seen(i)
        return out


# ---------------------------------------------------------------------------
# Walkable surface sampler (multi-bucket)
# ---------------------------------------------------------------------------

def walkable_sampler(walkable):
    """f(x, y, near_z) -> walkable Z at (x,y) nearest near_z, or None."""
    tg = _TriGrid(walkable)
    B = tg.B

    def sample(x, y, near_z):
        """Interpolated walkable Z at (x, y) closest to near_z, or None."""
        best = None
        for i in tg.candidates(x, y):
            a, b, c = B[i]
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-6:
                continue
            l0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d
            l1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d
            l2 = 1.0 - l0 - l1
            if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
                continue
            z = l0 * a[2] + l1 * b[2] + l2 * c[2]
            if best is None or abs(z - near_z) < abs(best - near_z):
                best = z
        return best

    return sample


# ---------------------------------------------------------------------------
# Thin vertical slab vs blocking triangle
# ---------------------------------------------------------------------------

def _tri_hits_slab(tri, cx, cy, ux, uy, half_w, tx, ty, depth, z_lo, z_hi):
    """True if triangle `tri` intersects the thin oriented slab.

    See: docs/commentary/tes5_import_navmesh.md#slab-test-is-a-2d-sat
    """
    # Z gate first (cheap): the triangle must reach into the column.
    tzs = (tri[0][2], tri[1][2], tri[2][2])
    if max(tzs) < z_lo or min(tzs) > z_hi:
        return False
    # Project the three vertices into the slab frame.
    px = []
    py = []
    for v in tri:
        ox, oy = v[0] - cx, v[1] - cy
        px.append(ox * tx + oy * ty)      # along tangent
        py.append(ox * ux + oy * uy)      # along march
    # SAT axis 1/2: slab's own axes (rectangle is axis-aligned in this frame).
    if min(px) > half_w or max(px) < -half_w:
        return False
    if min(py) > depth or max(py) < -depth:
        return False
    # SAT axis 3: the triangle's three edge normals.  Rectangle corners:
    rect = ((-half_w, -depth), (half_w, -depth), (half_w, depth), (-half_w, depth))
    tpts = ((px[0], py[0]), (px[1], py[1]), (px[2], py[2]))
    for k in range(3):
        ax, ay = tpts[k]
        bx, by = tpts[(k + 1) % 3]
        nx, ny = -(by - ay), (bx - ax)         # edge normal
        # project triangle
        tproj = [nx * p[0] + ny * p[1] for p in tpts]
        rproj = [nx * p[0] + ny * p[1] for p in rect]
        if min(tproj) > max(rproj) or max(tproj) < min(rproj):
            return False
    return True


def wall_slab_sampler(blocking):
    """Return f(cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth=None) -> True if a wall
    stands in the actor slab there.  (ux,uy)=march dir, (tx,ty)=edge tangent.

    `depth` is the half-extent along the march direction.
    See: docs/commentary/tes5_import_navmesh.md#wall-probe-sweeps-the-interval
    """
    tg = _TriGrid(blocking)
    B = tg.B

    def hit(cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth=None, half_w=None):
        """True if any blocking triangle reaches into the slab.

        `half_w` overrides the actor-width tangent extent for a one-off box.
        """
        if depth is None:
            depth = params.RIBBON_GROW_SLAB_DEPTH
        if half_w is None:
            half_w = params.RIBBON_GROW_SLAB_HALF_WIDTH
        for i in tg.candidates(cx, cy):
            if tg.z1[i] < z_lo or tg.z0[i] > z_hi:
                continue
            if _tri_hits_slab(B[i], cx, cy, ux, uy, half_w, tx, ty,
                              depth, z_lo, z_hi):
                return True
        return False

    return hit
