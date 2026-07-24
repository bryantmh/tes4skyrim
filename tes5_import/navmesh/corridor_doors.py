r"""Compute a Door footprint at every door, to feed the corridor union.

Vanilla Skyrim marks a door with a triangle whose LONG EDGE runs PARALLEL to the
door line.  We reproduce that: for each door we find the base line (BL-BR, on the
door line) and the FOOTPRINT quad bridging it to the nearest corridor edge:

    BL ----------- BR      BL,BR = the door base, on the door line,
     |             |       DOOR_LINE_HALF either side of the (panel-centred)
     |             |       threshold.  BL-BR is the LONG SIDE handed to the
     |             |       triangulation as a forced edge.
     E0 ----------- E1     E0,E1 = the two ends of the nearest corridor edge,
                           ordered so E0 pairs with BL and E1 with BR.

`door_footprints` returns, per reachable door, the base line and the quad
BL-BR-E1-E0.  corridor.py feeds the quad into the boolean union as ordinary
ground and passes the base line as a union `door_edges` constraint, so the
retriangulation forms ONE large triangle with its long side on the door line —
the union owns and de-overlaps the door geometry, no separate stitching.

Conservative: a door whose nearest corridor edge midpoint is beyond
DOOR_BRIDGE_RADIUS is walled off from the pathgrid and yields nothing.
"""

import math

from . import params

DOOR_BRIDGE_RADIUS = 220.0
DOOR_LINE_HALF = 45.0


def door_footprints(verts, tris, doors):
    """Per door, the base line + connecting footprint to feed the union.

    Returns a list of dicts, one per door that has a reachable corridor edge:

        {'base':  ((blx, bly), (brx, bry)),      # long side, on the door line
         'poly':  [(x, y), ...],                  # footprint to union in as ground
         'z':     storey_z}                        # height of that ground

    The footprint is the quad BL-BR-E1-E0 bridging the door base to the nearest
    corridor edge, handed to the boolean union as an ordinary polygon so the
    union owns its triangles, while the base line is forced to be a triangle
    edge.  This produces the vanilla Skyrim door triangle: one big triangle whose
    long side lies on the door line.

    Conservative: a door whose nearest corridor edge is beyond
    DOOR_BRIDGE_RADIUS is walled off from the pathgrid and yields nothing.
    """
    verts = [list(map(float, v)) for v in verts]
    tris = [tuple(map(int, t)) for t in tris]
    out = []
    if not doors or not tris:
        return out

    ztol = params.DOOR_QUAD_ZTOL
    br2 = DOOR_BRIDGE_RADIUS ** 2

    # Corridor edges once (this reads the RAW ribbon union, unmodified).
    edges = set()
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            edges.add((a, b) if a < b else (b, a))

    for (dx, dy, dz, rz, _is_tp) in doors:
        best = None
        for (a, b) in edges:
            va, vb = verts[a], verts[b]
            mz = 0.5 * (va[2] + vb[2])
            if abs(mz - dz) > ztol:
                continue
            mx = 0.5 * (va[0] + vb[0])
            my = 0.5 * (va[1] + vb[1])
            d2 = (mx - dx) ** 2 + (my - dy) ** 2
            if best is None or d2 < best[0]:
                best = (d2, a, b)
        if best is None or best[0] > br2:
            continue

        _d2, ea, eb = best
        storey_z = 0.5 * (verts[ea][2] + verts[eb][2])
        tx, ty = math.cos(rz), math.sin(rz)
        blx, bly = dx + tx * DOOR_LINE_HALF, dy + ty * DOOR_LINE_HALF
        brx, bry = dx - tx * DOOR_LINE_HALF, dy - ty * DOOR_LINE_HALF

        # Order corridor-edge ends so E0 pairs with BL, E1 with BR (the
        # non-self-crossing pairing) — the quad BL-BR-E1-E0 is then simple.
        va, vb = verts[ea], verts[eb]
        if (_d((blx, bly), va) + _d((brx, bry), vb) <=
                _d((blx, bly), vb) + _d((brx, bry), va)):
            e0, e1 = va, vb
        else:
            e0, e1 = vb, va

        poly = [(blx, bly), (brx, bry), (e1[0], e1[1]), (e0[0], e0[1])]
        out.append({'base': ((blx, bly), (brx, bry)),
                    'poly': poly, 'z': storey_z})
    return out


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])
