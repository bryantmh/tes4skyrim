"""Whether an actor's body gets along a planned path: steps it against the cell's real collision.

The route planner trusts every triangle; what stops a walker is the world. Each step along the path must
have floor under it, climb no more than a step at once or the slope limit for longer, and meet no
collision ahead of the body between knee and head height.
See: docs/reference/navmesh_engine_contracts.md#clearance-is-for-objects-not-walls
"""

import math

import numpy as np

from tools.navmesh.engine_check import FLOOR_BELOW, Solid, ray_triangles
from tools.navmesh.index import NavIndex

#: Skyrim's exterior cell width.
CELL = 4096.0
#: Ground this far above the navmesh still carries the walker (measured: 34 on a walked Arktwend road).
WALK_ABOVE = 64.0
#: Distance between body tests along the path.
STEP = 12.0
#: Max walkable slope of bhkCharacterController (SSE 1.6.1170 constructor 0xe9dcd0, 47 degrees).
MAX_SLOPE_DEG = 47.0
#: Consecutive steps steeper than the slope limit that stop a walker (a single stair step does not).
STEEP_STEPS = 3
#: Highest single rise the walker steps onto; calibrated on logged walks (a 34 plank edge is walked).
STEP_UP = 40.0
#: A hit stops the walker when its surface faces the walking direction at least this squarely (cos).
HEAD_ON = 0.9
#: Heights above the floor the forward probes run at: above a step, waist, head.
PROBE_HEIGHTS = (STEP_UP + 6.0, 64.0, 110.0)
#: Lateral offsets of the parallel forward probes, as fractions of the body radius: center and both sides.
PROBE_SIDES = (0.0, -0.9, 0.9)
DOWN = np.array([[0.0, 0.0, -1.0]])
UP = np.array([[0.0, 0.0, 1.0]])
#: Height of the walking body (CK default path request height 128, CK 0x286e450).
BODY_HEIGHT = 128.0


class Collision(object):
    """Collision of an export's cells, by exterior grid square or interior name, built on first use."""

    def __init__(self, export):
        """Index the export's exterior cells by grid and its interiors by name."""
        self.idx = NavIndex(export)
        self.grid, self.names, self.cache = {}, {}, {}
        for rec in self.idx.cells:
            if rec.get('XCLC.X') not in (None, ''):
                self.grid[(int(rec['XCLC.X']), int(rec['XCLC.Y']))] = rec.get('FormID')
            elif rec.get('FULL'):
                self.names[rec['FULL'].lower()] = rec.get('FormID')

    def soup(self, fid):
        """(walkable, blocking) collision triangles of one export cell, cached."""
        if fid not in self.cache:
            ctx = self.idx.cell(fid) if fid else None
            self.cache[fid] = ctx.collision()[:2] if ctx else (None, None)
        return self.cache[fid]

    def solid(self, x, y, interior=None):
        """A Solid over the interior, or the exterior square under (x, y) and its eight neighbours."""
        key = ('i', interior) if interior else ('x', int(x // CELL), int(y // CELL))
        if key not in self.cache:
            if interior:
                fids = [self.names.get(interior.lower())]
            else:
                fids = [self.grid.get((key[1] + dx, key[2] + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            self.cache[key] = Solid([s for f in fids if f for s in self.soup(f)])
        return self.cache[key]


def floor_z(solid, x, y, z, radius=0.0):
    """Highest surface near z under the body's footprint (center and four points at half radius), else None.

    The body rests on whatever is under it: a gap between planks narrower than the body carries it.
    """
    best = None
    r = radius / 2.0
    for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r)):
        hit = solid.cast((x + dx, y + dy, z + WALK_ABOVE), DOWN, WALK_ABOVE + FLOOR_BELOW)[0]
        if hit is not None:
            got = z + WALK_ABOVE - hit[0]
            best = got if best is None else max(best, got)
    return best


def facing_hit(solid, origin, fwd, length):
    """Plan dot of walking direction and the first surface hit within `length` (-1 = head-on), else None."""
    end = origin + fwd * length
    cand = solid.near(min(origin[0], end[0]), min(origin[1], end[1]), max(origin[0], end[0]),
                      max(origin[1], end[1]))
    if not len(cand):
        return None
    t, hit = ray_triangles(origin, fwd[None], solid.tri[cand])
    ok = hit[0] & (t[0] <= length)
    if not ok.any():
        return None
    n = solid.n[cand[int(np.argmin(np.where(ok, t[0], np.inf)))]]
    flat = np.array([n[0], n[1], 0.0])
    size = float(np.linalg.norm(flat))
    return -abs(float(np.dot(flat / size, fwd))) if size > 1e-6 else 0.0


def blocked_ahead(solid, at, direction, radius):
    """True when a surface faces the walker head-on within one step plus the radius, across its width."""
    fwd = np.array([direction[0], direction[1], 0.0])
    fwd /= max(float(np.linalg.norm(fwd)), 1e-9)
    side = np.array([-fwd[1], fwd[0], 0.0])
    for h in PROBE_HEIGHTS:
        for f in PROBE_SIDES:
            o = np.array([at[0], at[1], at[2] + h]) + side * radius * f
            got = facing_hit(solid, o, fwd, radius + STEP)
            if got is not None and got <= -HEAD_ON:
                return True
    return False


def low_ceiling(solid, at, radius):
    """True when collision over the body's footprint sits below the body's height above a step."""
    r = radius / 2.0
    for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r)):
        if solid.cast((at[0] + dx, at[1] + dy, at[2] + STEP_UP), UP, BODY_HEIGHT - STEP_UP)[0] is not None:
            return True
    return False


def samples(path):
    """Points every STEP along a polyline, each with its walking direction."""
    for p, q in zip(path, path[1:]):
        d = np.asarray(q, float) - np.asarray(p, float)
        n = max(1, int(np.linalg.norm(d[:2]) // STEP))
        for i in range(n):
            yield np.asarray(p, float) + d * (i / n), d


def walk_body(collision, path, radius, surface, interior=None):
    """`(None, None)` when the body gets to the path's end, else `(point, reason)` where it stops.

    `surface(point)` is the navmesh height under a point: the walker follows the mesh, so the floor is
    looked for there, not on the straight line between path corners.
    """
    max_rise = STEP * math.tan(math.radians(MAX_SLOPE_DEG))
    last, steep = None, 0
    goal = np.asarray(path[-1], float)
    for at, d in samples(path):
        if float(np.linalg.norm((goal - at)[:2])) <= radius + STEP:
            break
        solid = collision.solid(at[0], at[1], interior)
        at[2] = surface(at)
        z = floor_z(solid, at[0], at[1], at[2], radius)
        if z is None:
            return at, 'no floor'
        here = np.array([at[0], at[1], z])
        rise = z - last[2] if last is not None else 0.0
        steep = steep + 1 if rise > max_rise else 0
        if rise > STEP_UP or steep >= STEEP_STEPS:
            return here, 'climb %.0f over %.0f' % (rise, STEP)
        if blocked_ahead(solid, here, d, radius):
            return here, 'collision ahead'
        if low_ceiling(solid, here, radius):
            return here, 'ceiling below body height'
        last = here
    return None, None
