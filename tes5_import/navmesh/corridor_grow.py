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

import numpy as np

from . import params
from .native_loader import load_native

_native = load_native('_navgrow_native')


# ---------------------------------------------------------------------------
# Batched native march
# ---------------------------------------------------------------------------

def native_index(tris):
    """A native TriIndex over a triangle soup, 9 floats per triangle."""
    soup = np.ascontiguousarray(tris, dtype=np.float64).reshape(-1, 9)
    return _native.TriIndex(soup)


# ---------------------------------------------------------------------------
# Native samplers
# ---------------------------------------------------------------------------

def walkable_sampler(walkable):
    """f(x, y, near_z) -> walkable Z at (x,y) nearest near_z, or None."""
    return native_index(walkable).walk


def wall_slab_sampler(blocking):
    """Return f(cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth=None) -> True if a wall
    stands in the actor slab there.  (ux,uy)=march dir, (tx,ty)=edge tangent.

    `depth` is the half-extent along the march direction.
    See: docs/commentary/tes5_import_navmesh.md#wall-probe-sweeps-the-interval
    """
    wall = native_index(blocking).wall

    def hit(cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth=None, half_w=None):
        """True if any blocking triangle reaches into the slab."""
        return wall(cx, cy, ux, uy, tx, ty, z_lo, z_hi,
                    params.RIBBON_GROW_SLAB_DEPTH if depth is None else depth,
                    params.RIBBON_GROW_SLAB_HALF_WIDTH if half_w is None
                    else half_w)

    return hit
