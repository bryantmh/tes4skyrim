r"""Compute a Door footprint at every door, to feed the corridor union.

Vanilla Skyrim marks a door with a triangle whose LONG EDGE runs PARALLEL to the
door line.  We reproduce that: for each door we find the base line (BL-BR, on the
door line) and the FOOTPRINT quad bridging it to the nearest corridor edge:

    BL ----------- BR      BL,BR = the door base, on the door line,
     |             |       DOOR_LINE_HALF either side of the (panel-centered)
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

# How far the nearest corridor edge may be for a door to get a footprint at
# all.  220 stranded real doors: ImperialDungeon01's upper walkway door sits
# 287u from the nearest pathgrid line and got NO Door Triangle, leaving the
# doorway dead in-engine.  The wall walk (_blocked_between) still rejects any
# candidate behind geometry, so a larger reach cannot bridge through a wall.
DOOR_BRIDGE_RADIUS = 384.0
# Fallback half-width, used only when a door model has no measured panel width
# (see pgrd_to_navm._DOOR_WIDTH).  Real widths come from the collision panel.
DOOR_LINE_HALF = 45.0
# A door wider than this is a city gate/portcullis whose full span would
# swallow the room; the base line is capped there.  A MEASURED width is
# otherwise used EXACTLY — widening a narrow gate to a minimum pushed the base
# line into the jambs on both sides (cgprisoncellgate01's old 40u single-bar
# measurement got a 90u base through the wall), and the door triangle must be
# the door's own width, no more and no less.
DOOR_LINE_HALF_MAX = 220.0
# Depth of the door triangle: apex on the perpendicular bisector of the base,
# on the side the pathgrid serves, at half the base length (near-equilateral,
# the vanilla shape) but never shallower than DOOR_MIN_DEPTH.  The triangle's
# shape is a pure function of the door — same width, same depth, same area,
# every build — instead of whatever apex the triangulation could fit.
DOOR_TRI_MIN_DEPTH = 64.0
# How far from each end the bridge collision walk is skipped.  A door REFR is a
# placed mesh standing ON the threshold, so a walk starting at the door hits the
# door panel itself and rejects every candidate; the corridor end is skipped by
# the same amount so the ribbon's own geometry cannot reject it either.  Wider
# than a door panel is thick, far narrower than a room.
DOOR_SELF_CLEARANCE = 48.0
# The footprint must be deep enough to genuinely overlap the corridor it bridges
# to.  The nearest corridor edge usually sits right at the threshold, so the raw
# projection gave 1-20u deep quads — slivers that connect to nothing.
DOOR_MIN_DEPTH = 64.0
# Extra depth pushed PAST the corridor edge so the union certainly merges them.
DOOR_OVERLAP = 32.0


def _sides_disconnected(nodes, pg_edges, dx, dy, dz, fcx, fcy, ztol):
    """True when the pathgrid on the door's two faces is DISCONNECTED.

    The only case that earns a far-side bridge quad.
    See: docs/commentary/tes5_import_navmesh.md#door-far-side-bridge
    """
    if not nodes or not pg_edges:
        return False
    parent = list(range(len(nodes)))

    def find(a):
        """Union-find root of node `a`, path-halving as it climbs."""
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for (i, j) in pg_edges:
        if i < len(nodes) and j < len(nodes):
            parent[find(i)] = find(j)
    best = {}                        # side -> (d2, node)
    for ni, n in enumerate(nodes):
        if abs(n[2] - dz) > ztol:
            continue
        proj = (n[0] - dx) * fcx + (n[1] - dy) * fcy
        if abs(proj) < 1.0:
            continue
        side = 1 if proj > 0 else -1
        d2 = (n[0] - dx) ** 2 + (n[1] - dy) ** 2
        if side not in best or d2 < best[side][0]:
            best[side] = (d2, ni)
    if len(best) < 2:
        return False
    return find(best[1][1]) != find(best[-1][1])


def _mesh_z_index(verts, tris, cell=128.0):
    """Bucket triangle indices by a `cell`-sized plan grid."""
    grid = {}
    for ti, t in enumerate(tris):
        xs = [verts[i][0] for i in t]
        ys = [verts[i][1] for i in t]
        for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
            for gy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                grid.setdefault((gx, gy), []).append(ti)
    return grid


def _mesh_z_near(verts, tris, grid, px, py, near_z, ztol, cell=128.0):
    """Corridor-mesh height at (px, py) nearest near_z, or None.

    See: docs/commentary/tes5_import_navmesh.md#door-mesh-height-probe
    """
    best = None
    for ti in grid.get((int(px // cell), int(py // cell)), ()):
        a, b, c = (verts[i] for i in tris[ti])
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-9:
            continue
        l0 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / d
        l1 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / d
        l2 = 1.0 - l0 - l1
        if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
            continue
        z = l0 * a[2] + l1 * b[2] + l2 * c[2]
        if abs(z - near_z) > ztol:
            continue
        if best is None or abs(z - near_z) < abs(best - near_z):
            best = z
    return best


def _mesh_edges(tris):
    """Every undirected edge of the raw corridor mesh, as sorted index pairs."""
    edges = set()
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            edges.add((a, b) if a < b else (b, a))
    return edges


def _door_half_width(door_w):
    """Half the base line: the measured panel width, capped, else the default."""
    half = 0.5 * door_w if door_w else DOOR_LINE_HALF
    return min(half, DOOR_LINE_HALF_MAX)


def _door_candidates(verts, edges, dx, dy, dz, rz, w_half, ztol):
    """Corridor edges this door could bridge to, nearest first.

    Gated on storey height, DOOR_BRIDGE_RADIUS and the frontal strip.
    See: docs/commentary/tes5_import_navmesh.md#door-candidate-edge-gating
    """
    ttx, tty = math.sin(rz), math.cos(rz)
    strip_half = w_half + params.RIBBON_HALF_WIDTH
    br2 = DOOR_BRIDGE_RADIUS ** 2
    cands = []
    for (a, b) in edges:
        va, vb = verts[a], verts[b]
        if abs(0.5 * (va[2] + vb[2]) - dz) > ztol:
            continue
        mx = 0.5 * (va[0] + vb[0])
        my = 0.5 * (va[1] + vb[1])
        d2 = (mx - dx) ** 2 + (my - dy) ** 2
        if d2 > br2:
            continue
        if abs((mx - dx) * ttx + (my - dy) * tty) > strip_half:
            continue
        cands.append((d2, mx, my, a, b))
    cands.sort(key=lambda c: c[0])
    return cands


def _served_side(nodes, dx, dy, rz):
    """Which face of the door the nearest pathgrid node stands on (+1/-1/0).

    See: docs/commentary/tes5_import_navmesh.md#door-side-comes-from-the-pathgrid
    """
    if not nodes:
        return 0
    ftx, fty = math.cos(rz), -math.sin(rz)
    best = None
    for n in nodes:
        proj = (n[0] - dx) * ftx + (n[1] - dy) * fty
        if abs(proj) < 1.0:
            continue
        d2n = (n[0] - dx) ** 2 + (n[1] - dy) ** 2
        if best is None or d2n < best[0]:
            best = (d2n, proj)
    if best is None:
        return 0
    return 1 if best[1] > 0 else -1


def _clear_by_side(cands, wall_hit, dx, dy, dz, fcx, fcy):
    """Nearest UNBLOCKED candidate on each face, as {side: (d2, a, b, mx, my)}.

    A blocked candidate is skipped and the search continues outward.
    See: docs/commentary/tes5_import_navmesh.md#door-candidate-edge-gating
    """
    best_by_side = {}
    for (d2, mx, my, a, b) in cands:
        side = 1 if (mx - dx) * fcx + (my - dy) * fcy >= 0.0 else -1
        if side in best_by_side:
            continue
        if wall_hit is not None and _blocked_between(
                wall_hit, dx, dy, dz, mx, my):
            continue
        best_by_side[side] = (d2, a, b, mx, my)
        if len(best_by_side) == 2:
            break
    return best_by_side


def _sweep_quad(ctx, side, entry, with_base):
    """The footprint quad sweeping the base line toward `side`.

    See: docs/commentary/tes5_import_navmesh.md#door-footprint-is-a-rectangle
    """
    blx, bly, brx, bry = ctx['base']
    dx, dy, fx, fy = ctx['dx'], ctx['dy'], ctx['fx'], ctx['fy']
    _sd2, sea, seb, semx, semy = entry
    verts = ctx['verts']
    s_z = 0.5 * (verts[sea][2] + verts[seb][2])
    depth = abs((semx - dx) * fx + (semy - dy) * fy)
    depth = float(side) * max(depth + DOOR_OVERLAP, DOOR_MIN_DEPTH,
                              ctx['apex_depth'] + DOOR_OVERLAP)
    sflx, sfly = blx + fx * depth, bly + fy * depth
    sfrx, sfry = brx + fx * depth, bry + fy * depth
    apex = (dx + fx * float(side) * ctx['apex_depth'],
            dy + fy * float(side) * ctx['apex_depth'])
    z_far = _mesh_z_near(verts, ctx['tris'], ctx['zgrid'],
                         0.5 * (sflx + sfrx), 0.5 * (sfly + sfry),
                         s_z, ctx['ztol'])
    return {'base': ((blx, bly), (brx, bry)) if with_base else None,
            'apex': apex if with_base else None,
            'poly': [(blx, bly), (brx, bry), (sfrx, sfry), (sflx, sfly)],
            'z': s_z,
            'z_far': z_far if z_far is not None else s_z}


def _door_quads(door, verts, tris, zgrid, edges, wall_hit, nodes, pg_edges,
                ztol):
    """Every footprint quad for one door: the primary, plus a far side if owed.

    See: docs/commentary/tes5_import_navmesh.md#door-base-line-is-local-y
    """
    dx, dy, dz, rz, is_tp, door_w = door
    w_half = _door_half_width(door_w)
    fcx, fcy = math.cos(rz), -math.sin(rz)
    cands = _door_candidates(verts, edges, dx, dy, dz, rz, w_half, ztol)
    best_by_side = _clear_by_side(cands, wall_hit, dx, dy, dz, fcx, fcy)
    if not best_by_side:
        return []
    want_side = _served_side(nodes, dx, dy, rz)
    primary = want_side if want_side in best_by_side else sorted(best_by_side)[0]

    tx, ty = math.sin(rz), math.cos(rz)
    ctx = {'verts': verts, 'tris': tris, 'zgrid': zgrid, 'ztol': ztol,
           'dx': dx, 'dy': dy, 'fx': ty, 'fy': -tx,
           'apex_depth': max(w_half, DOOR_TRI_MIN_DEPTH),
           'base': (dx + tx * w_half, dy + ty * w_half,
                    dx - tx * w_half, dy - ty * w_half)}

    out = [_sweep_quad(ctx, primary, best_by_side[primary], True)]
    other = -primary
    if (not is_tp and other in best_by_side
            and _sides_disconnected(nodes, pg_edges, dx, dy, dz,
                                    fcx, fcy, ztol)):
        out.append(_sweep_quad(ctx, other, best_by_side[other], False))
    return out


def door_footprints(verts, tris, doors, wall_hit=None, nodes=None,
                    pg_edges=None, unreached=None):
    """Per door, the base line + connecting footprint to feed the union.

    One dict per door with a reachable corridor edge: `base` is the long side
    lying on the door line, `poly` the footprint to union in as ground, `z` its
    height.  A door with no clear corridor edge on its storey within
    DOOR_BRIDGE_RADIUS yields nothing and is appended to `unreached`.
    See: docs/commentary/tes5_import_navmesh.md#door-footprints
    """
    verts = [list(map(float, v)) for v in verts]
    tris = [tuple(map(int, t)) for t in tris]
    if not doors or not tris:
        return []
    zgrid = _mesh_z_index(verts, tris)
    edges = _mesh_edges(tris)
    out = []
    for door in doors:
        quads = _door_quads(door, verts, tris, zgrid, edges, wall_hit,
                            nodes, pg_edges, params.DOOR_QUAD_ZTOL)
        if not quads and unreached is not None:
            unreached.append(door)
        out += quads
    return out


def _d(a, b):
    """Plan-view distance between two points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _blocked_between(wall_hit, x0, y0, z0, x1, y1):
    """True if blocking collision stands between the door and a corridor edge.

    Both ends are skipped by DOOR_SELF_CLEARANCE, the door's own panel included.
    See: docs/commentary/tes5_import_navmesh.md#door-own-collision-is-skipped
    """
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return False
    ux, uy = dx / dist, dy / dist
    tx, ty = -uy, ux
    z_lo = z0 + params.RIBBON_GROW_SLAB_Z_BOTTOM
    z_hi = z0 + params.AGENT_HEIGHT
    step = params.RIBBON_GROW_STEP
    start = DOOR_SELF_CLEARANCE
    end = dist - DOOR_SELF_CLEARANCE
    if end <= start:
        return False                 # too short to contain anything but the door
    q = start
    while q < end:
        seg = min(step, end - q)
        mid = q + 0.5 * seg
        if wall_hit(x0 + ux * mid, y0 + uy * mid, ux, uy, tx, ty, z_lo, z_hi,
                    0.5 * seg + params.RIBBON_GROW_SLAB_DEPTH):
            return True
        q += seg
    return False
