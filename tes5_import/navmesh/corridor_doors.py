r"""Put a Door Triangle at every door, bridged onto the nearest corridor EDGE.

Vanilla Skyrim marks a door with a triangle whose LONG EDGE runs PARALLEL to
the door line.  We reproduce that and connect it to the corridor by bridging the
door base to the NEAREST corridor edge — a rectangle split into three triangles:

    BL ----------- BR      BL,BR = the door base, on the door line,
     |\           /|       DOOR_LINE_HALF either side of the (panel-centred)
     | \         / |       threshold.  BL-BR is the LONG SIDE of the centre
     |  \       /  |       triangle that _build_door_links flags.
     E0 ----------- E1     E0,E1 = the two ends of the nearest corridor edge,
                           ordered so E0 pairs with BL and E1 with BR.

  * centre = (BL, BR, E1)  — long side BL-BR on the door line
  * (BL, E1, E0)           — shares the corridor edge E0-E1
  * (BL, E0, ... )         handled by the two triangles above filling the quad

Concretely the quad BL-BR-E1-E0 is split into two triangles sharing the corridor
edge E0-E1, plus we keep the door base as the long side of the centre triangle.

Conservative: a door whose nearest corridor edge midpoint is beyond
DOOR_BRIDGE_RADIUS is walled off from the pathgrid and gets no Door Triangle.
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
    corridor edge — the SAME connection carve_doors makes, but handed to the
    boolean union as an ordinary polygon so the union owns its triangles and the
    base line is forced to be a triangle edge.  This produces the vanilla Skyrim
    door triangle: one big triangle whose long side lies on the door line.

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


def carve_doors(verts, tris, doors, sample=None):
    """Bridge each door's threshold base onto the nearest corridor edge."""
    verts = [list(map(float, v)) for v in verts]
    tris = [tuple(map(int, t)) for t in tris]
    if not doors or not tris:
        return verts, tris

    ztol = params.DOOR_QUAD_ZTOL
    br2 = DOOR_BRIDGE_RADIUS ** 2

    for (dx, dy, dz, rz, _is_tp) in doors:
        # Edge -> owning triangle indices, rebuilt each door (a prior door may
        # have split a triangle another door then bridges onto).
        owners = {}
        for ti, t in enumerate(tris):
            for k in range(3):
                a, b = t[k], t[(k + 1) % 3]
                key = (a, b) if a < b else (b, a)
                owners.setdefault(key, []).append(ti)

        # Nearest corridor edge to the door (XY distance from door centre to the
        # edge midpoint), within the Z window so we never attach to a storey
        # above/below.
        best = None
        for (a, b) in owners:
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
            continue                                  # walled off — skip

        _d2, ea, eb = best
        storey_z = 0.5 * (verts[ea][2] + verts[eb][2])

        # door base on the door line, at the corridor storey height.
        tx, ty = math.cos(rz), math.sin(rz)
        bl = len(verts)
        verts.append([dx + tx * DOOR_LINE_HALF, dy + ty * DOOR_LINE_HALF,
                      storey_z])
        br = len(verts)
        verts.append([dx - tx * DOOR_LINE_HALF, dy - ty * DOOR_LINE_HALF,
                      storey_z])

        # Order the corridor edge ends so E0 pairs with BL and E1 with BR — the
        # pairing that does NOT self-cross (the shorter total of the two link
        # diagonals wins).
        if (_d(verts[bl], verts[ea]) + _d(verts[br], verts[eb]) <=
                _d(verts[bl], verts[eb]) + _d(verts[br], verts[ea])):
            e0, e1 = ea, eb
        else:
            e0, e1 = eb, ea

        # Split the corridor edge E0-E1 at its midpoint M and fan THREE triangles
        # from M to the door base:
        #     E0 --- M --- E1
        #       \    |    /
        #        \   |   /
        #     BL --------- BR   (door line)
        # To keep the mesh manifold, the corridor triangle(s) that own edge
        # E0-E1 are re-split through M too, so M is a shared vertex, not a
        # T-junction.
        mid = len(verts)
        verts.append([0.5 * (verts[e0][0] + verts[e1][0]),
                      0.5 * (verts[e0][1] + verts[e1][1]), storey_z])

        key = (e0, e1) if e0 < e1 else (e1, e0)
        for ti in owners.get(key, []):
            t = tris[ti]
            apex = next((v for v in t if v != e0 and v != e1), None)
            if apex is None:
                continue
            # replace the owning triangle with its two halves through M
            tris[ti] = (apex, e0, mid)
            _add(tris, verts, apex, mid, e1)

        # The three door triangles fanning from M (matches the reference: a
        # centre triangle with its long side on the door line, flanked above and
        # below).
        _add(tris, verts, mid, e0, bl)     # top flank
        _add(tris, verts, mid, bl, br)     # centre: long side BL-BR on the door
        _add(tris, verts, mid, br, e1)     # bottom flank

    return verts, tris


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _add(tris, verts, a, b, c):
    """Append triangle (a,b,c) if it is non-degenerate in XY."""
    if len({a, b, c}) < 3:
        return
    va, vb, vc = verts[a], verts[b], verts[c]
    area = abs((vb[0] - va[0]) * (vc[1] - va[1]) -
               (vb[1] - va[1]) * (vc[0] - va[0])) * 0.5
    if area >= params.MIN_XY_FOOTPRINT:
        tris.append((a, b, c))
