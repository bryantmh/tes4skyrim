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

The raw quad mesh is far too dense (one quad per voxel cell), so it is decimated
afterwards — but decimation only ever MERGES coplanar neighbours, and never changes
which spans are connected, so it cannot reintroduce any of the above.
"""

import math

from . import params


def _corner_key(cx, cy, si):
    return (cx, cy, si)


def build_mesh(hf, region_of=None):
    """Walkable spans -> (verts3d, tris).  Connected by construction."""
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

    return _decimate(verts, tris)


# ---------------------------------------------------------------------------
# Decimation
# ---------------------------------------------------------------------------

def _decimate(verts, tris):
    """Collapse edges between coplanar triangles to cut the triangle count.

    The raw mesh has a quad per voxel cell, which is one to two orders of magnitude
    more triangles than Skyrim's NVNM wants (and it indexes triangles with an int16).
    Collapsing an edge only ever MERGES neighbours that already form a flat surface,
    so it cannot connect anything that was not already connected, nor separate
    anything that was — every guarantee the span graph gave us survives.
    """
    if not tris:
        return verts, tris

    target_err = params.MAX_SIMPLIFY_ERR

    # Adjacency: vertex -> incident triangles.
    vtris = {}
    for ti, t in enumerate(tris):
        for v in t:
            vtris.setdefault(v, set()).add(ti)

    # Boundary vertices (on an edge used by exactly one triangle) are pinned:
    # moving them would change the outline of the mesh, which is where it meets a
    # wall.  Everything interior is a candidate for collapse.
    edge_count = {}
    for (a, b, c) in tris:
        for e in ((a, b), (b, c), (c, a)):
            k = (min(e), max(e))
            edge_count[k] = edge_count.get(k, 0) + 1
    boundary = set()
    for (a, b), n in edge_count.items():
        if n == 1:
            boundary.add(a)
            boundary.add(b)

    alive = [True] * len(tris)
    vmap = list(range(len(verts)))

    def find(v):
        while vmap[v] != v:
            vmap[v] = vmap[vmap[v]]
            v = vmap[v]
        return v

    def plane_err(v, keep):
        """Max deviation of v's incident triangles' planes at `keep`."""
        pv, pk = verts[v], verts[keep]
        worst = 0.0
        for ti in vtris.get(v, ()):
            if not alive[ti]:
                continue
            a, b, c = (find(x) for x in tris[ti])
            if len({a, b, c}) < 3:
                continue
            va, vb, vc = verts[a], verts[b], verts[c]
            nx = ((vb[1] - va[1]) * (vc[2] - va[2]) -
                  (vb[2] - va[2]) * (vc[1] - va[1]))
            ny = ((vb[2] - va[2]) * (vc[0] - va[0]) -
                  (vb[0] - va[0]) * (vc[2] - va[2]))
            nz = ((vb[0] - va[0]) * (vc[1] - va[1]) -
                  (vb[1] - va[1]) * (vc[0] - va[0]))
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            if ln < 1e-9:
                continue
            d = abs((pk[0] - va[0]) * nx + (pk[1] - va[1]) * ny +
                    (pk[2] - va[2]) * nz) / ln
            worst = max(worst, d)
        return worst

    neighbours = {}
    for (a, b, c) in tris:
        for (x, y) in ((a, b), (b, c), (c, a)):
            neighbours.setdefault(x, set()).add(y)
            neighbours.setdefault(y, set()).add(x)

    max_edge = params.TRI_TARGET_EDGE

    def worst_quality(v, keep):
        """Worst aspect ratio (longest edge / inradius-ish) after collapsing v->keep.

        Planarity alone is not enough to decide a collapse.  A vertex in the middle
        of a flat floor is coplanar with ALL its neighbours, so a purely planar test
        happily drags it right across the room, and the floor degenerates into a fan
        of long thin slivers radiating from whatever vertex survived — useless for
        pathing and visibly wrong.  Bounding the resulting triangles' shape and edge
        length keeps the mesh made of reasonable triangles.
        """
        pk = verts[keep]
        worst = 0.0
        for ti in vtris.get(v, ()):
            if not alive[ti]:
                continue
            idx = [find(x) for x in tris[ti]]
            if keep in idx and v in idx:
                continue                       # this triangle collapses away
            pts = [pk if find(x) == v else verts[find(x)] for x in tris[ti]]
            a, b, c = pts
            e = [math.dist(a, b), math.dist(b, c), math.dist(c, a)]
            longest = max(e)
            if longest > max_edge:
                return 1e9
            s = sum(e) / 2.0
            area2 = s * (s - e[0]) * (s - e[1]) * (s - e[2])
            if area2 <= 1e-9:
                return 1e9                     # degenerate / flipped
            area = math.sqrt(area2)
            # Aspect ratio: longest edge vs the triangle's "thickness".
            worst = max(worst, longest * longest / (4.0 * area))
        return worst

    max_aspect = 6.0

    for v in range(len(verts)):
        if v in boundary or find(v) != v:
            continue
        best = None
        for nb in neighbours.get(v, ()):
            nb = find(nb)
            if nb == v:
                continue
            if plane_err(v, nb) > target_err:
                continue
            q = worst_quality(v, nb)
            if q <= max_aspect and (best is None or q < best[0]):
                best = (q, nb)
        if best is None:
            continue
        keep = best[1]
        vmap[v] = keep
        # The survivor inherits the collapsed vertex's triangles, so a later
        # collapse still sees the full one-ring it has to keep well-shaped.
        vtris.setdefault(keep, set()).update(vtris.get(v, ()))
        for ti in vtris.get(v, ()):
            a, b, c = (find(x) for x in tris[ti])
            if len({a, b, c}) < 3:
                alive[ti] = False

    out_tris = []
    for ti, t in enumerate(tris):
        if not alive[ti]:
            continue
        a, b, c = (find(x) for x in t)
        if len({a, b, c}) < 3:
            continue
        out_tris.append((a, b, c))

    used = sorted({i for t in out_tris for i in t})
    remap = {o: n for n, o in enumerate(used)}
    return ([verts[i] for i in used],
            [(remap[a], remap[b], remap[c]) for (a, b, c) in out_tris])
