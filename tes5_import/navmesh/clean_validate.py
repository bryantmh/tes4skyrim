"""Mesh topology and shape queries used by tools, acceptance and the ledge pass.

Edge adjacency and connected components over SHARED EDGES -- what the engine
actually walks -- triangle shape badness, plus the drop-down (ledge) link
search.  Nothing here mutates a mesh.
"""

import math

from . import params


def badness(verts, t):
    """Normalized shape badness; 1.0 = exactly at the contract boundary.

    max(edge_ratio / MAX_EDGE_RATIO, aspect / MAX_TRI_ASPECT): the ratio term
    catches needles (one short edge), the aspect term catches CAPS (all edges
    comparable, near-zero height) which the ratio cannot see.
    """
    p, q, r = verts[t[0]], verts[t[1]], verts[t[2]]
    e = [math.dist(p[:2], q[:2]), math.dist(q[:2], r[:2]),
         math.dist(r[:2], p[:2])]
    lo, hi = min(e), max(e)
    area = abs((q[0] - p[0]) * (r[1] - p[1]) -
               (q[1] - p[1]) * (r[0] - p[0])) * 0.5
    if lo <= 1e-9 or area <= 1e-9:
        return 1e9
    return max((hi / lo) / params.MAX_EDGE_RATIO,
               (hi * hi / (4.0 * area)) / params.MAX_TRI_ASPECT)


def edge_adjacency(tris):
    """(N,3) neighbour-triangle indices over shared edges, -1 for boundary.

    MUST match pgrd_to_navm._compute_adjacency: an edge shared by exactly two
    triangles links them; three or more links none.  This is the connectivity
    the ENGINE sees, distinct from vertex-union-find.
    """
    owners = {}
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = int(t[k]), int(t[(k + 1) % 3])
            key = (a, b) if a < b else (b, a)
            owners.setdefault(key, []).append((ti, k))
    adj = [[-1, -1, -1] for _ in range(len(tris))]
    for ent in owners.values():
        if len(ent) == 2:
            (ti, si), (tj, sj) = ent
            adj[ti][si] = tj
            adj[tj][sj] = ti
    return adj

def _boundary_edges(tris, comp):
    """Open edges (no neighbour) of one component, as (a, b) vertex indices."""
    owners = {}
    for ti in comp:
        t = tris[ti]
        for k in range(3):
            a, b = int(t[k]), int(t[(k + 1) % 3])
            key = (a, b) if a < b else (b, a)
            owners[key] = owners.get(key, 0) + 1
    return [e for e, n in owners.items() if n == 1]

def _centroid(verts, tri):
    """The triangle's 3D centroid."""
    a, b, c = verts[tri[0]], verts[tri[1]], verts[tri[2]]
    return ((a[0] + b[0] + c[0]) / 3.0,
            (a[1] + b[1] + c[1]) / 3.0,
            (a[2] + b[2] + c[2]) / 3.0)

def _edge_tri(tris, ti_set, a, b):
    """The triangle in this component owning boundary edge (a, b)."""
    for ti in ti_set:
        t = tris[ti]
        for k in range(3):
            if {t[k], t[(k + 1) % 3]} == {a, b}:
                return ti
    return None


def _ledge_candidates(verts, bounds_i, bounds_j):
    """Boundary-edge pairs close enough in plan and separated by a legal drop.

    Endpoint pairing is tried both ways round: the two boundaries wind in
    opposite directions, so the matching endpoints are as often swapped as not.
    """
    cands = []
    for (a1, b1) in bounds_i:
        p1, q1 = verts[a1], verts[b1]
        for (a2, b2) in bounds_j:
            p2, q2 = verts[a2], verts[b2]
            dxy = min(
                max(math.hypot(p1[0] - p2[0], p1[1] - p2[1]),
                    math.hypot(q1[0] - q2[0], q1[1] - q2[1])),
                max(math.hypot(p1[0] - q2[0], p1[1] - q2[1]),
                    math.hypot(q1[0] - p2[0], q1[1] - p2[1])))
            if dxy > params.ISLAND_BRIDGE_XY:
                continue
            z1 = 0.5 * (p1[2] + q1[2])
            z2 = 0.5 * (p2[2] + q2[2])
            drop = abs(z1 - z2)
            if not (params.ISLAND_BRIDGE_MIN_DROP <= drop
                    <= params.ISLAND_BRIDGE_MAX_DROP):
                continue
            cands.append((dxy, (a1, b1), (a2, b2), z1, z2, drop))
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    return cands


def _link_pair(tris, comps, bounds, verts, i, j, out):
    """Append the ledge links between components `i` and `j` to `out`.

    Nearest pairs first, ONE link per triangle, so links spread along the lip
    instead of stacking on one edge.

    See: docs/commentary/tes5_import_navmesh.md#ledge-links-spread-along-the-lip
    """
    cands = _ledge_candidates(verts, bounds[i], bounds[j])
    used, made = set(), 0
    for (_dxy, (a1, b1), (a2, b2), z1, z2, drop) in cands:
        if made >= params.LEDGE_LINKS_PER_PAIR:
            break
        t1 = _edge_tri(tris, comps[i], a1, b1)
        t2 = _edge_tri(tris, comps[j], a2, b2)
        if t1 is None or t2 is None or t1 in used or t2 in used:
            continue
        used.add(t1)
        used.add(t2)
        made += 1
        if z1 >= z2:
            out.append((t1, t2, drop, (a1, b1), tris[t1]))
        else:
            out.append((t2, t1, drop, (a2, b2), tris[t2]))


#: How far short of the floor's real edge a pushed lip stops.
LIP_STANDOFF = 2.0


def _push_lip(verts, lip, hi_tri, reach, moved):
    """Move the lip's two vertices out along the lip's OUTWARD normal to the
    floor's real edge; False if a wall stands in the way (no actor can drop
    over a railing).

    See: docs/commentary/tes5_import_navmesh.md#ledge-lip-is-pushed-to-the-edge
    """
    a, b = lip
    apex = next(k for k in hi_tri if k not in (a, b))
    mx = 0.5 * (verts[a][0] + verts[b][0])
    my = 0.5 * (verts[a][1] + verts[b][1])
    mz = 0.5 * (verts[a][2] + verts[b][2])
    ex, ey = verts[b][0] - verts[a][0], verts[b][1] - verts[a][1]
    run = math.hypot(ex, ey)
    if run < 1e-6:
        return True
    nx, ny = -ey / run, ex / run
    if (verts[apex][0] - mx) * nx + (verts[apex][1] - my) * ny > 0.0:
        nx, ny = -nx, -ny
    dist, wall = reach(mx, my, mz, nx, ny, params.ISLAND_BRIDGE_XY)
    if wall:
        return False
    dist -= LIP_STANDOFF
    for k in (a, b):
        if dist > 0.0 and k not in moved:
            verts[k][0] += nx * dist
            verts[k][1] += ny * dist
            moved.add(k)
    return True


def find_ledge_links(verts, tris, reach=None):
    """Find DROP-DOWNs between components: [(tri_hi, tri_lo, drop), ...].

    Both sides must ALREADY be separate components (stairs and ramps never
    enter); `pgrd_to_navm` writes the links.  With `reach` (see
    `corridor._ledge_reach`) each lip is pushed to the floor's edge, editing
    `verts` in place, and a railing-blocked pair is dropped.

    See: docs/commentary/tes5_import_navmesh.md#ledge-links-spread-along-the-lip
    """
    if len(tris) == 0:
        return []
    tris = [list(map(int, t)) for t in tris]
    comps = components(tris)
    if len(comps) < 2:
        return []
    bounds = [_boundary_edges(tris, c) for c in comps]
    pairs = []
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            _link_pair(tris, comps, bounds, verts, i, j, pairs)
    out, moved = [], set()
    for (hi, lo, drop, lip, hi_tri) in pairs:
        if reach is None or _push_lip(verts, lip, hi_tri, reach, moved):
            out.append((hi, lo, drop))
    return out


def components(tris):
    """List of triangle-index lists connected over EDGE adjacency (engine view)."""
    adj = edge_adjacency(tris)
    n = len(tris)
    seen = [False] * n
    comps = []
    for s in range(n):
        if seen[s]:
            continue
        comp, stack = [], [s]
        seen[s] = True
        while stack:
            x = stack.pop()
            comp.append(x)
            for nb in adj[x]:
                if nb >= 0 and not seen[nb]:
                    seen[nb] = True
                    stack.append(nb)
        comps.append(comp)
    return comps
