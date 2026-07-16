"""Walkable spans -> triangles, meshed directly from the 3D span graph.

This replaces the contour/height-map mesher, which was structurally unable to get
this right.

Why the old approach could not work
-----------------------------------
A contour is a HEIGHT MAP: one Z per (cx,cy) column.  A walkable surface in a
building is not.  A staircase carries an NPC over the room below it, so the same
column holds two walkable surfaces; a two-storey house holds two more.  The old
mesher tried to slice the world into height-map "layers" and contour each one, and
every defect we chased came from the seams between those slices:

  * a staircase peeled into five layers, each contoured on its own, each an island
    joined to the next only at a triangle corner;
  * a layer boundary that fell between two floors let the triangulator bridge them,
    producing a wall of near-vertical triangles "connecting" storey 1 to storey 2;
  * a short pathgrid stub became its own layer and was culled for being small,
    leaving a pathgrid line with no navmesh under it.

Tuning the slicer traded these defects for one another indefinitely.

What this does instead
----------------------
Mesh the span graph directly.  A walkable SPAN — not a column — is the unit:

    node      = (cx, cy, span_index)          two floors in one column are two nodes
    adjacent  = neighbouring columns, span tops within MAX_CLIMB
    geometry  = one quad per span, on the column's four lattice corners

A corner's Z is shared by every span that meets there, so adjacent spans literally
share vertices and the mesh is connected BY CONSTRUCTION.  Nothing has to be
stitched, welded or repaired afterwards.  Two spans a storey apart are never
adjacent, so no triangle can ever bridge them: the cross-floor bug is not fixed
here so much as made unrepresentable.

The raw quad mesh is far too dense (one quad per voxel cell), so it is simplified
afterwards (collapse + flip + smooth, see the Simplification section) — but every
move is local and quality/fidelity-bounded and never changes which spans are
connected, so it cannot reintroduce any of the above.
"""

import math

from . import params


def _corner_key(cx, cy, si):
    return (cx, cy, si)


def build_mesh(hf, region_of=None, doors=None):
    """Walkable spans -> (verts3d, tris).  Connected by construction.

    doors: optional [(x, y, z, rot_z), ...] door thresholds.  Each gets an
    exact oriented quad stamped into the raw mesh (see _stamp_door_quads);
    its corner vertices are pinned through decimation, so the final mesh
    always carries two clean triangles precisely on every door threshold.
    """
    w, h, cs = hf.w, hf.h, hf.cs
    climb = params.MAX_CLIMB

    # Walkable spans per column, as (span_index, top_z).
    walk = {}
    for cy in range(h):
        for cx in range(w):
            col = hf.spans[cy * w + cx]
            ws = [(si, s[1]) for si, s in enumerate(col) if s[2]]
            if ws:
                walk[(cx, cy)] = ws
    if not walk:
        return [], []

    # --- corner vertices -----------------------------------------------------
    # A span occupies the lattice square [cx,cx+1] x [cy,cy+1].  Each of its four
    # corners is shared with the spans of the (up to) four columns touching that
    # corner — but ONLY those whose top is within a step, i.e. the ones an NPC can
    # actually walk between.  Spans on different floors touch the same corner in XY
    # and must NOT share a vertex, so each corner is split into groups of mutually
    # step-reachable spans, one vertex per group.
    #
    # corner_groups[(lx,ly)] = list of [z_sum, n, {span_key: True}]
    corner_groups = {}

    def corner_spans(lx, ly):
        """(span_key, top_z) of every walkable span touching lattice corner."""
        out = []
        for (dx, dy) in ((0, 0), (-1, 0), (0, -1), (-1, -1)):
            key = (lx + dx, ly + dy)
            for (si, z) in walk.get(key, ()):
                out.append(((key[0], key[1], si), z))
        return out

    vert_of = {}          # (lx, ly, group_index) -> vertex index
    span_corner = {}      # (span_key, corner_ix) -> vertex index
    verts = []

    for (cx, cy), ws in walk.items():
        for (si, _z) in ws:
            for ci, (lx, ly) in enumerate(((cx, cy), (cx + 1, cy),
                                           (cx + 1, cy + 1), (cx, cy + 1))):
                if (lx, ly) not in corner_groups:
                    # Build the corner's groups once: cluster the spans touching it
                    # by step-reachability in Z.
                    spans_here = sorted(corner_spans(lx, ly), key=lambda t: t[1])
                    groups = []
                    for (skey, z) in spans_here:
                        if groups and z - groups[-1][-1] <= climb:
                            groups[-1][0].append(skey)
                            groups[-1][1].append(z)
                            groups[-1][-1] = z
                        else:
                            groups.append([[skey], [z], z])
                    corner_groups[(lx, ly)] = groups
                    for gi, g in enumerate(groups):
                        zs = g[1]
                        verts.append((hf.min_x + lx * cs,
                                      hf.min_y + ly * cs,
                                      sum(zs) / len(zs)))
                        vert_of[(lx, ly, gi)] = len(verts) - 1

                skey = (cx, cy, si)
                gi = None
                for k, g in enumerate(corner_groups[(lx, ly)]):
                    if skey in g[0]:
                        gi = k
                        break
                if gi is None:
                    continue
                span_corner[(skey, ci)] = vert_of[(lx, ly, gi)]

    # --- quads ---------------------------------------------------------------
    tris = []
    for (cx, cy), ws in walk.items():
        for (si, _z) in ws:
            skey = (cx, cy, si)
            c = [span_corner.get((skey, i)) for i in range(4)]
            if any(v is None for v in c):
                continue
            a, b, d, e = c            # SW, SE, NE, NW
            if len({a, b, d, e}) < 3:
                continue
            if a != b and b != d and d != a:
                tris.append((a, b, d))
            if a != d and d != e and e != a:
                tris.append((a, d, e))

    verts, tris, pinned = _stamp_door_quads(verts, tris, doors)
    return _decimate(verts, tris, cs, pinned)


def _stamp_door_quads(verts, tris, doors):
    """Rebuild the mesh under each door into an exact oriented threshold quad.

    The Door Triangle must be a well-shaped triangle sitting precisely on the
    doorway threshold — the old approach (link whatever decimated triangle
    happened to be nearest the door) produced door triangles of arbitrary
    shape and position.  Here, while the mesh is still at voxel resolution,
    every vertex inside the door's rect (oriented by the door's RotZ) is
    snapped onto the rect's four corners.  Triangles wholly inside the rect
    degenerate and vanish; triangles crossing the rect boundary stretch to
    its corners; and the rect itself is covered by exactly two clean
    triangles whose shared edge crosses the door line.  The corner vertices
    are PINNED through decimation, so the quad survives to the output intact.

    Runs on the raw voxel mesh, where vertices are dense (every cs units), so
    a rect over walkable floor always captures vertices; after decimation the
    triangles are bigger than the rect and there would be nothing to snap.

    Returns (verts, tris, pinned_vertex_ids).
    """
    if not doors:
        return verts, tris, frozenset()
    hw = params.DOOR_QUAD_HALF_WIDTH
    hd = params.DOOR_QUAD_HALF_DEPTH
    ztol = params.DOOR_QUAD_ZTOL

    verts = [list(v) for v in verts]
    pinned = set()
    vmap = {}
    quad_tris = []
    for (dx, dy, dz, rz) in doors:
        tx, ty = math.cos(rz), math.sin(rz)       # threshold (width) axis
        fx, fy = -ty, tx                          # facing (walk-through) axis
        members = ([], [], [], [])                # per corner
        inside = []
        for vi, v in enumerate(verts):
            if vi in pinned or vi in vmap:
                continue                          # already claimed by a door
            ox, oy = v[0] - dx, v[1] - dy
            a = ox * tx + oy * ty
            b = ox * fx + oy * fy
            if abs(a) <= hw and abs(b) <= hd and abs(v[2] - dz) <= ztol:
                ci = {(False, False): 0, (True, False): 1,
                      (True, True): 2, (False, True): 3}[(a >= 0, b >= 0)]
                members[ci].append(vi)
                inside.append(vi)
        if not inside:
            continue                              # no mesh at this door
        z_all = sum(verts[vi][2] for vi in inside) / len(inside)
        cids = []
        for ci, (sa, sb) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
            cxw = dx + sa * hw * tx + sb * hd * fx
            cyw = dy + sa * hw * ty + sb * hd * fy
            ms = members[ci]
            zm = (sum(verts[m][2] for m in ms) / len(ms)) if ms else z_all
            verts.append([cxw, cyw, zm])
            cids.append(len(verts) - 1)
        for ci in range(4):
            for m in members[ci]:
                vmap[m] = cids[ci]
        pinned.update(cids)
        # CCW like the span quads ((t, f) is a right-handed frame).
        quad_tris.append((cids[0], cids[1], cids[2]))
        quad_tris.append((cids[0], cids[2], cids[3]))

    if not vmap:
        return verts, tris, frozenset(pinned)

    out = []
    seen = set()
    for (a, b, c) in tris:
        a = vmap.get(a, a)
        b = vmap.get(b, b)
        c = vmap.get(c, c)
        if len({a, b, c}) < 3:
            continue
        k = (a, b, c) if a < b and a < c else \
            (b, c, a) if b < c else (c, a, b)
        km = tuple(sorted(k))
        if km in seen:
            continue
        seen.add(km)
        out.append((a, b, c))
    for t in quad_tris:
        km = tuple(sorted(t))
        if km not in seen:
            seen.add(km)
            out.append(t)
    return verts, out, frozenset(pinned)


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------
#
# The raw mesh has a quad per voxel cell — one to two orders of magnitude more
# triangles than Skyrim's NVNM wants (it indexes triangles with an int16), all
# grid-aligned, with sawtooth outlines wherever a wall runs diagonally.  It is
# simplified with the three classic constrained-remeshing moves, each of which
# provably preserves what the span graph guaranteed (connectivity, and no
# cross-floor triangles, whose z-spread no move here can increase past the
# existing spread of a one-ring):
#
#   COLLAPSE  merge a vertex into a neighbour.  Bounded by surface fidelity
#             (MAX_SIMPLIFY_ERR to the old planes), triangle shape (MAX_ASPECT),
#             edge length, orientation (no folds) and a link condition (no
#             non-manifold pinches).  Boundary vertices are NOT pinned: a
#             boundary vertex may slide away along its own boundary chain when
#             it deviates less than MAX_SIMPLIFY_ERR from the straight line
#             between its two outline neighbours — this is what turns the 16u
#             voxel sawtooth into clean diagonals (octagon, not saw blade).
#   FLIP      Lawson edge flip between two near-coplanar triangles when it
#             improves the worse of the two shapes.  Geometry does not move at
#             all, so this is free of fidelity risk; it is what dissolves the
#             high-valence fans a greedy collapse leaves behind.
#   SMOOTH    tangential relaxation: move an interior vertex of a locally flat
#             one-ring toward its neighbours' centroid, IN the surface plane.
#             This is what makes triangles come out near-equilateral like the
#             CK's, instead of echoing the voxel grid.
#
# A few rounds of collapse+flip+smooth converge quickly.

def _tri_shape(pa, pb, pc):
    """(aspect, longest_edge, normal) — aspect is 1e9 for degenerate."""
    e0 = math.dist(pa, pb)
    e1 = math.dist(pb, pc)
    e2 = math.dist(pc, pa)
    longest = max(e0, e1, e2)
    ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
    wx, wy, wz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
    nx = uy * wz - uz * wy
    ny = uz * wx - ux * wz
    nz = ux * wy - uy * wx
    area = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
    if area <= 1e-6:
        return 1e9, longest, (0.0, 0.0, 0.0)
    return longest * longest / (4.0 * area), longest, (nx, ny, nz)


def _seg_dist(p, a, b):
    """Distance from point p to segment ab (3D)."""
    abx, aby, abz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    apx, apy, apz = p[0] - a[0], p[1] - a[1], p[2] - a[2]
    denom = abx * abx + aby * aby + abz * abz
    t = 0.0 if denom < 1e-12 else max(0.0, min(1.0, (
        apx * abx + apy * aby + apz * abz) / denom))
    dx = apx - t * abx
    dy = apy - t * aby
    dz = apz - t * abz
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _decimate(verts, tris, cs, pinned=frozenset()):
    if not tris:
        return verts, tris

    verts = [list(v) for v in verts]
    tris = [tuple(t) for t in tris]
    # Edge budget scales with the voxel size: exteriors (CS 32) are smooth
    # terrain and take triangles twice the interior scale without losing shape.
    max_edge = params.TRI_TARGET_EDGE * (cs / params.CS)
    # Outline error a boundary vertex has already absorbed, so repeated chain
    # collapses cannot walk the outline arbitrarily far from where it started.
    acc = {}

    for _ in range(params.SIMPLIFY_PASSES):
        verts, tris, n_col = _collapse_pass(verts, tris, acc, max_edge, pinned)
        n_flip = _flip_pass(verts, tris, max_edge)
        n_smooth = _smooth_pass(verts, tris, max_edge, pinned)
        if n_col + n_flip + n_smooth == 0:
            break
    _flip_pass(verts, tris, max_edge)

    used = sorted({i for t in tris for i in t})
    remap = {o: n for n, o in enumerate(used)}
    return ([tuple(verts[i]) for i in used],
            [(remap[a], remap[b], remap[c]) for (a, b, c) in tris])


def _collapse_pass(verts, tris, acc, max_edge, pinned=frozenset()):
    """One sweep of quality-bounded edge collapses, shortest edges first.

    Interior vertices collapse into whichever neighbour yields the best-shaped
    result.  A boundary vertex with exactly two outline neighbours may collapse
    into one of them when it sits within MAX_SIMPLIFY_ERR of the straight line
    between them (sawtooth removal); its deviation is charged against `acc` on
    the survivor so chains of collapses stay within the same total budget.

    A PINNED vertex (door-quad corner) never moves or collapses away, though
    others may collapse INTO it.
    """
    target_err = params.MAX_SIMPLIFY_ERR
    max_aspect = params.MAX_ASPECT

    vtris = {}
    for ti, t in enumerate(tris):
        for v in t:
            vtris.setdefault(v, set()).add(ti)

    neighbours = {}
    edge_count = {}
    for (a, b, c) in tris:
        for (x, y) in ((a, b), (b, c), (c, a)):
            neighbours.setdefault(x, set()).add(y)
            neighbours.setdefault(y, set()).add(x)
            k = (x, y) if x < y else (y, x)
            edge_count[k] = edge_count.get(k, 0) + 1

    # bnbrs[v] = v's neighbours along boundary edges.  Maintained through
    # collapses so a later tooth in the same sawtooth still sees the true chain.
    bnbrs = {}
    for (a, b), n in edge_count.items():
        if n == 1:
            bnbrs.setdefault(a, set()).add(b)
            bnbrs.setdefault(b, set()).add(a)

    alive = [True] * len(tris)
    vmap = list(range(len(verts)))

    def find(v):
        while vmap[v] != v:
            vmap[v] = vmap[vmap[v]]
            v = vmap[v]
        return v

    def vertex_planes(v):
        """(anchor point, unit normal) of v's incident alive triangles.

        Computed ONCE per vertex and evaluated against each collapse
        candidate — the old per-candidate recomputation (a full _tri_shape
        per incident triangle per candidate) was the hottest code in the
        entire build.
        """
        planes = []
        for ti in vtris.get(v, ()):
            if not alive[ti]:
                continue
            a, b, c = (find(x) for x in tris[ti])
            if len({a, b, c}) < 3:
                continue
            va, vb, vc = verts[a], verts[b], verts[c]
            ux, uy, uz = vb[0] - va[0], vb[1] - va[1], vb[2] - va[2]
            wx, wy, wz = vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]
            nx = uy * wz - uz * wy
            ny = uz * wx - ux * wz
            nz = ux * wy - uy * wx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            if ln < 1e-9:
                continue
            planes.append((va[0], va[1], va[2], nx / ln, ny / ln, nz / ln))
        return planes

    def plane_dev(planes, keep):
        """Max deviation of `keep` from the given planes (early-out past
        target_err — callers only compare against it)."""
        pk = verts[keep]
        worst = 0.0
        for (ax, ay, az, nx, ny, nz) in planes:
            d = (pk[0] - ax) * nx + (pk[1] - ay) * ny + (pk[2] - az) * nz
            if d < 0.0:
                d = -d
            if d > worst:
                worst = d
                if worst > target_err:
                    return worst
        return worst

    def collapse_quality(v, keep):
        """Worst shape after collapsing v->keep, or None if any check fails.

        Planarity alone is not enough: a vertex in the middle of a flat floor
        is coplanar with ALL its neighbours, so a purely planar test drags it
        clear across the room and the floor degenerates into sliver fans.
        Shape, edge length AND orientation (no folded triangles — a fold has
        healthy area and passes a bare degeneracy test) are all bounded.
        """
        pk = verts[keep]
        worst = 0.0
        for ti in vtris.get(v, ()):
            if not alive[ti]:
                continue
            idx = [find(x) for x in tris[ti]]
            if v not in idx:
                continue
            if keep in idx:
                continue                       # this triangle collapses away
            old = [verts[x] for x in idx]
            new = [pk if x == v else verts[x] for x in idx]
            q, longest, nn = _tri_shape(*new)
            if q > max_aspect or longest > max_edge:
                return None
            _oq, _oe, on = _tri_shape(*old)
            if nn[0] * on[0] + nn[1] * on[1] + nn[2] * on[2] <= 0.0:
                return None                    # collapse would fold the mesh
            worst = max(worst, q)
        return worst

    def link_ok(v, keep):
        """Edge-collapse link condition: the only neighbours v and keep share
        are the opposite corners of the triangles dying with the edge.  Anything
        else would pinch the mesh into a non-manifold configuration (duplicate
        edges), which corrupts NVNM edge adjacency."""
        opp = set()
        for ti in vtris.get(v, ()):
            if not alive[ti]:
                continue
            idx = {find(x) for x in tris[ti]}
            if v in idx and keep in idx:
                opp.update(idx - {v, keep})
        shared = {find(n) for n in neighbours.get(v, ())} & \
                 {find(n) for n in neighbours.get(keep, ())}
        shared.discard(v)
        shared.discard(keep)
        return shared == opp

    def do_collapse(v, keep):
        vmap[v] = keep
        vtris.setdefault(keep, set()).update(vtris.get(v, ()))
        for ti in vtris.get(v, ()):
            a, b, c = (find(x) for x in tris[ti])
            if len({a, b, c}) < 3:
                alive[ti] = False
        nv = neighbours.pop(v, set())
        nk = neighbours.setdefault(keep, set())
        for n in nv:
            neighbours.get(n, set()).discard(v)
            if n != keep:
                neighbours.get(n, set()).add(keep)
                nk.add(n)
        nk.discard(keep)
        nk.discard(v)

    # Shortest incident edge first: coarsening eats the dense voxel-scale mesh
    # from the bottom up, which is what drives edge lengths toward uniform.
    order = []
    for v, nbs in neighbours.items():
        pv = verts[v]
        shortest = min(math.dist(pv, verts[n]) for n in nbs)
        order.append((shortest, v))
    order.sort()

    collapsed = 0
    for (_d, v) in order:
        if v in pinned:
            continue
        if find(v) != v:
            continue
        bn = bnbrs.get(v)
        if bn is not None:
            # Boundary vertex: only a plain 2-neighbour chain vertex may move,
            # only into a chain neighbour, and only when the outline barely
            # changes.  Corners of doorways/junctions (degree != 2) stay put.
            if len(bn) != 2:
                continue
            a, b = (find(x) for x in bn)
            if a == v or b == v or a == b:
                continue
            dev = _seg_dist(verts[v], verts[a], verts[b]) + acc.get(v, 0.0)
            if dev > target_err:
                continue
            planes = vertex_planes(v)
            best = None
            for keep in (a, b):
                if plane_dev(planes, keep) > target_err:
                    continue
                if not link_ok(v, keep):
                    continue
                q = collapse_quality(v, keep)
                if q is not None and (best is None or q < best[0]):
                    best = (q, keep)
            if best is None:
                continue
            keep = best[1]
            do_collapse(v, keep)
            acc[keep] = max(acc.get(keep, 0.0), dev)
            other = b if keep == a else a
            bnbrs.pop(v, None)
            bnbrs[keep] = (bnbrs.get(keep, set()) - {v}) | {other}
            bnbrs[other] = (bnbrs.get(other, set()) - {v}) | {keep}
            collapsed += 1
        else:
            planes = vertex_planes(v)
            best = None
            for nb in neighbours.get(v, ()):
                nb = find(nb)
                if nb == v:
                    continue
                if plane_dev(planes, nb) > target_err:
                    continue
                if not link_ok(v, nb):
                    continue
                q = collapse_quality(v, nb)
                if q is not None and (best is None or q < best[0]):
                    best = (q, nb)
            if best is not None:
                do_collapse(v, best[1])
                collapsed += 1

    out = []
    for ti, t in enumerate(tris):
        if not alive[ti]:
            continue
        a, b, c = (find(x) for x in t)
        if len({a, b, c}) < 3:
            continue
        out.append((a, b, c))
    return verts, out, collapsed


def _flip_pass(verts, tris, max_edge):
    """Lawson edge flips between near-coplanar triangle pairs.

    Flipping the shared edge of two triangles never moves a vertex, so it costs
    nothing in fidelity — but it is the move that actually dissolves sliver fans:
    a collapse-only simplifier leaves high-valence vertices whose surrounding
    triangles are long and thin, and flipping redistributes them toward the
    equilateral.  A flip is taken only when it strictly improves the worse of
    the two shapes, which guarantees termination.

    Restricted to near-coplanar pairs so a stair riser/tread pair is never
    flattened, and to interior manifold edges so the outline cannot change.
    """
    cos_flat = math.cos(math.radians(10.0))
    max_aspect_new = params.MAX_ASPECT * 2.0   # a flip must never CREATE junk

    edge_tris = {}
    for ti, (a, b, c) in enumerate(tris):
        for (x, y) in ((a, b), (b, c), (c, a)):
            k = (x, y) if x < y else (y, x)
            edge_tris.setdefault(k, []).append(ti)
    all_edges = set(edge_tris)

    stack = [k for k, ts in edge_tris.items() if len(ts) == 2]
    flips = 0
    guard = 8 * len(tris)

    while stack and guard > 0:
        guard -= 1
        k = stack.pop()
        ts = edge_tris.get(k)
        if ts is None or len(ts) != 2:
            continue
        ti, tj = ts
        t1, t2 = tris[ti], tris[tj]
        u, v = k
        if u not in t1 or v not in t1 or u not in t2 or v not in t2:
            continue                            # stale entry
        p = next(x for x in t1 if x != u and x != v)
        q = next(x for x in t2 if x != u and x != v)
        if p == q:
            continue
        pq = (p, q) if p < q else (q, p)
        if pq in all_edges:
            continue                            # flip would duplicate an edge

        # Orient so t1 traverses u->v (consistent winding by construction).
        i1 = t1.index(u)
        if t1[(i1 + 1) % 3] != v:
            u, v = v, u
            i1 = t1.index(u)
            if t1[(i1 + 1) % 3] != v:
                continue
        i2 = t2.index(v)
        if t2[(i2 + 1) % 3] != u:
            continue                            # inconsistent winding: leave it

        pu, pv, pp, pq_ = verts[u], verts[v], verts[p], verts[q]
        q1, _e1, n1 = _tri_shape(pu, pv, pp)
        q2, _e2, n2 = _tri_shape(pv, pu, pq_)
        l1 = math.sqrt(n1[0] ** 2 + n1[1] ** 2 + n1[2] ** 2)
        l2 = math.sqrt(n2[0] ** 2 + n2[1] ** 2 + n2[2] ** 2)
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        if (n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]) / (l1 * l2) < cos_flat:
            continue                            # a real crease (stair riser)

        nq1, le1, nn1 = _tri_shape(pu, pq_, pp)      # (u, q, p)
        nq2, le2, nn2 = _tri_shape(pv, pp, pq_)      # (v, p, q)
        if max(nq1, nq2) >= max(q1, q2) - 1e-6:
            continue                            # not an improvement
        if max(nq1, nq2) > max_aspect_new or max(le1, le2) > max_edge:
            continue
        # Both new triangles must face the same way as the old pair.
        for nn in (nn1, nn2):
            if nn[0] * n1[0] + nn[1] * n1[1] + nn[2] * n1[2] <= 0.0:
                break
        else:
            tris[ti] = (u, q, p)
            tris[tj] = (v, p, q)
            del edge_tris[k]
            all_edges.discard(k)
            edge_tris[pq] = [ti, tj]
            all_edges.add(pq)
            # Two outer edges change owner: (v,p) moves t1->tj, (u,q) t2->ti.
            for (ex, ey, old_t, new_t) in ((v, p, ti, tj), (u, q, tj, ti)):
                ek = (ex, ey) if ex < ey else (ey, ex)
                ets = edge_tris.get(ek)
                if ets is not None:
                    edge_tris[ek] = [new_t if t == old_t else t for t in ets]
            for (ex, ey) in ((u, p), (v, q), (v, p), (u, q)):
                ek = (ex, ey) if ex < ey else (ey, ex)
                if len(edge_tris.get(ek, ())) == 2:
                    stack.append(ek)
            flips += 1
    return flips


def _smooth_pass(verts, tris, max_edge, pinned=frozenset()):
    """Tangential relaxation of interior vertices on locally flat surface.

    Moves a vertex toward the centroid of its neighbours, projected back into
    the local surface plane, so a flat floor keeps its exact height while the
    triangulation relaxes off the voxel grid toward the equilateral.  Vertices
    on a crease (stairs) or on the outline are left exactly where they are.
    """
    cos_flat = math.cos(math.radians(5.0))
    max_aspect = params.MAX_ASPECT

    vtris = {}
    neighbours = {}
    edge_count = {}
    for ti, (a, b, c) in enumerate(tris):
        for v in (a, b, c):
            vtris.setdefault(v, set()).add(ti)
        for (x, y) in ((a, b), (b, c), (c, a)):
            neighbours.setdefault(x, set()).add(y)
            neighbours.setdefault(y, set()).add(x)
            k = (x, y) if x < y else (y, x)
            edge_count[k] = edge_count.get(k, 0) + 1
    boundary = set()
    for (a, b), n in edge_count.items():
        if n == 1:
            boundary.add(a)
            boundary.add(b)

    moved = 0
    for v, nbs in neighbours.items():
        if v in boundary or v in pinned:
            continue
        ring = [tris[ti] for ti in vtris.get(v, ())]
        if not ring:
            continue
        # Average normal; bail if the one-ring is not genuinely flat.
        ax = ay = az = 0.0
        norms = []
        for t in ring:
            _q, _e, n = _tri_shape(verts[t[0]], verts[t[1]], verts[t[2]])
            ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
            if ln < 1e-9:
                norms = None
                break
            n = (n[0] / ln, n[1] / ln, n[2] / ln)
            norms.append(n)
            ax += n[0]
            ay += n[1]
            az += n[2]
        if not norms:
            continue
        ln = math.sqrt(ax * ax + ay * ay + az * az)
        if ln < 1e-9:
            continue
        ax, ay, az = ax / ln, ay / ln, az / ln
        if any(n[0] * ax + n[1] * ay + n[2] * az < cos_flat for n in norms):
            continue

        pv = verts[v]
        cx = sum(verts[n][0] for n in nbs) / len(nbs)
        cy = sum(verts[n][1] for n in nbs) / len(nbs)
        cz = sum(verts[n][2] for n in nbs) / len(nbs)
        dx, dy, dz = cx - pv[0], cy - pv[1], cz - pv[2]
        dn = dx * ax + dy * ay + dz * az
        dx, dy, dz = dx - dn * ax, dy - dn * ay, dz - dn * az   # tangential only
        dx, dy, dz = dx * 0.5, dy * 0.5, dz * 0.5
        if dx * dx + dy * dy + dz * dz < 1.0:
            continue
        new = (pv[0] + dx, pv[1] + dy, pv[2] + dz)

        ok = True
        for t in ring:
            pts = [new if x == v else verts[x] for x in t]
            q, longest, nn = _tri_shape(*pts)
            if (q > max_aspect or longest > max_edge or
                    nn[0] * ax + nn[1] * ay + nn[2] * az <= 0.0):
                ok = False
                break
        if ok:
            pv[0], pv[1], pv[2] = new
            moved += 1
    return moved
