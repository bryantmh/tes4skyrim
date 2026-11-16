"""Frozen navmesh patches: triangles a human owns, kept verbatim over any generator.

A patch is two lists of world-space triangles.  `frozen` are the triangles the
human left, shipped exactly; `voids` are the generated triangles they replaced
or deleted.  Together they are the REGION the human took over: after the
generator runs, every generated triangle overlapping that region on its own
storey is removed, the frozen triangles go in, and whatever part of a removed
triangle lies outside the region is refilled on that triangle's own plane.

When the generator has not moved, the removed triangles are exactly the voids,
nothing needs refilling, and the result is the human's mesh.

See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
"""

import numpy as np
from shapely import STRtree, constrained_delaunay_triangles, get_parts
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from tes5_import.navmesh.corridor import ccw_in_plan

#: Bump when `apply_frozen` builds differently, so every patched cell re-caches.
FROZEN_VERSION = 7

#: Plan area (u^2) below which an overlap or a refill piece is float noise.
AREA_EPS = 1.0

#: Height gap at one plan point inside which two surfaces are the same storey.
STOREY_BAND = 60.0

#: Plan distance inside which a corner IS an existing vertex.
SNAP_TOL = 0.5

#: Height tolerance for a frozen corner reusing a generated vertex.
FROZEN_Z_TOL = 1.0

#: Plan distance and height gap inside which a generated corner is moved onto a patch corner or edge.
WELD_TOL, WELD_Z = 4.0, 20.0

#: Greatest plan distance inside which a corner beside a patch corner is that corner.
CORNER_TOL = 8.0

#: Shortest plan altitude a refill triangle may have; a thinner one is a leftover, not floor.
SLIVER_ALT = 4.0


def plan_area(p):
    """Plan-view area of a triangle given as three points."""
    return abs((p[1][0] - p[0][0]) * (p[2][1] - p[0][1])
               - (p[2][0] - p[0][0]) * (p[1][1] - p[0][1])) * 0.5


def plane_z(p, x, y):
    """Height of triangle `p`'s plane at plan point (x, y)."""
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (q[:3] for q in p)
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-9:
        return (az + bz + cz) / 3.0
    l0 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d
    l1 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d
    return l0 * az + l1 * bz + (1.0 - l0 - l1) * cz


def altitude(p):
    """Shortest plan altitude of a triangle given as three points."""
    longest = max(np.hypot(p[i][0] - p[j][0], p[i][1] - p[j][1]) for i, j in ((0, 1), (1, 2), (2, 0)))
    return 2.0 * plan_area(p) / longest if longest > 1e-9 else 0.0


def _footprint(p):
    """Plan polygon of a triangle given as three points."""
    return Polygon([(q[0], q[1]) for q in p])


def _region(tris_pts):
    """`[(points, footprint)]` for every non-degenerate patch triangle."""
    return [(p, _footprint(p)) for p in tris_pts if plan_area(p) > AREA_EPS]


def covered_by(tris_pts):
    """`f((x, y, z)) -> bool`: does the point lie inside one of `tris_pts`, on its plane?

    A frozen triangle may be split along an edge when it is stitched, so this
    is how its pieces are recognised -- a piece's centroid lies inside it.
    """
    region = _region(list(tris_pts))
    tree = STRtree([foot for _pts, foot in region]) if region else None

    def inside(p):
        """True when `p` is on a frozen triangle's surface (within 1u)."""
        at = Point(p[0], p[1])
        return tree is not None and any(
            region[int(j)][1].covers(at)
            and abs(plane_z(region[int(j)][0], p[0], p[1]) - p[2]) <= FROZEN_Z_TOL
            for j in tree.query(at))
    return inside


def _newest(frozen):
    """`frozen` without any triangle a LATER one overlaps on its storey.

    A re-pin appends its triangles, so where an edit re-cut an older patch
    triangle the later pieces are the mesh the human last saw.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    region = _region(frozen)
    tree = STRtree([foot for _pts, foot in region])
    stale = set()
    for i, (pts, foot) in enumerate(region):
        for j in tree.query(foot):
            hit = foot.intersection(region[int(j)][1]) if j > i else None
            if hit is not None and hit.area > AREA_EPS and _storey_gap(pts, region[int(j)][0], hit) <= STOREY_BAND:
                stale.add(id(pts))
    return [pts for pts in frozen if id(pts) not in stale]


def _owners(corners, region, tree):
    """Footprints of the patch triangles that claim this generated triangle.

    A claim needs real plan overlap AND the same storey at the overlap, so a
    patch on one floor never removes the floor above or below it.
    """
    if plan_area(corners) <= AREA_EPS:
        return []
    poly = _footprint(corners)
    out = []
    for j in tree.query(poly):
        pts, foot = region[int(j)]
        hit = poly.intersection(foot)
        if hit.area <= AREA_EPS:
            continue
        if _storey_gap(corners, pts, hit) <= STOREY_BAND:
            out.append(foot)
    return out


def _storey_gap(a, b, overlap):
    """Least height gap of `a` and `b` over their overlap's corners and an inner point."""
    c = overlap.representative_point()
    pts = [(c.x, c.y)] + [xy for p in get_parts(overlap) if p.geom_type == 'Polygon'
                          for xy in p.exterior.coords[:-1]]
    return min(abs(plane_z(a, x, y) - plane_z(b, x, y)) for x, y in pts)


def _groups(tris, removed):
    """The removed triangle indices split into edge-connected groups."""
    parent = {i: i for i in removed}

    def root(i):
        """Union-find root of `i`, compressing the path."""
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner = {}
    for i in removed:
        for k in range(3):
            a, b = tris[i][k], tris[i][(k + 1) % 3]
            e = (a, b) if a < b else (b, a)
            if e in owner:
                parent[root(i)] = root(owner[e])
            else:
                owner[e] = i
    out = {}
    for i in removed:
        out.setdefault(root(i), []).append(i)
    return list(out.values())


def _height_at(sheet, x, y):
    """Height of the removed floor at (x, y): the plane of the triangle holding it."""
    at = Point(x, y)
    best, best_d = sheet[0], None
    for pts in sheet:
        d = _footprint(pts).distance(at)
        if best_d is None or d < best_d:
            best, best_d = pts, d
        if d == 0.0:
            break
    return plane_z(best, x, y)


def _refill(sheet, claims):
    """Triangles covering the part of one removed sheet no patch claims.

    The sheet is refilled as ONE polygon, so its vertices are only the
    generator's own and the patch's corners -- never a crossing on a frozen
    edge -- and each corner sits on the removed floor's own plane.
    """
    rest = unary_union([_footprint(p) for p in sheet]).difference(
        unary_union(claims))
    if rest.area <= AREA_EPS:
        return []
    out = []
    for part in get_parts(rest):
        if part.geom_type != 'Polygon' or part.area <= AREA_EPS:
            continue
        for tri in get_parts(constrained_delaunay_triangles(part)):
            pts = [(x, y, _height_at(sheet, x, y))
                   for (x, y) in list(tri.exterior.coords)[:3]]
            if plan_area(pts) > 0.05:
                out.append(pts)
    return out


class _VertexPool(object):
    """Vertices bucketed in plan, so a corner can find the vertex it already is."""

    def __init__(self, verts):
        """Index every existing vertex; none is frozen yet."""
        self.verts = [tuple(float(c) for c in v[:3]) for v in verts]
        self.grid = {}
        self.frozen = set()
        for i, v in enumerate(self.verts):
            self._index(i, v)

    def _index(self, i, v):
        """File vertex `i` under its 1u plan bucket."""
        self.grid.setdefault((int(v[0] // 1.0), int(v[1] // 1.0)), []).append(i)

    def find(self, p, dz, among=None):
        """Index of the nearest vertex within SNAP_TOL in plan and `dz` in height, else None."""
        best, best_d = None, None
        gx, gy = int(p[0] // 1.0), int(p[1] // 1.0)
        for bx in (gx - 1, gx, gx + 1):
            for by in (gy - 1, gy, gy + 1):
                for i in self.grid.get((bx, by), ()):
                    if among is not None and i not in among:
                        continue
                    v = self.verts[i]
                    plan = (v[0] - p[0]) ** 2 + (v[1] - p[1]) ** 2
                    if plan > SNAP_TOL ** 2 or abs(v[2] - p[2]) > dz:
                        continue
                    d = plan + (v[2] - p[2]) ** 2
                    if best_d is None or d < best_d:
                        best, best_d = i, d
        return best

    def add(self, p):
        """Append a new vertex and return its index."""
        self.verts.append(tuple(float(c) for c in p[:3]))
        i = len(self.verts) - 1
        self._index(i, self.verts[i])
        return i

    def frozen_corner(self, p):
        """Index for a frozen corner: the generated vertex under it on its storey, moved onto it.

        The pinned position wins, so a patch stays joined to the floor beside
        it even where the generator has since moved that vertex.
        See: docs/commentary/tes5_import_navmesh.md#frozen-corner-takes-the-vertex
        """
        i = self.find(p, FROZEN_Z_TOL)
        if i is None:
            i = self.find(p, STOREY_BAND)
            if i is None or i in self.frozen:
                i = self.add(p)
            self.verts[i] = tuple(float(c) for c in p[:3])
        self.frozen.add(i)
        return i

    def refill_corner(self, p):
        """Index for a refill corner, preferring a frozen vertex so the patch stays exact."""
        i = self.find(p, STOREY_BAND, self.frozen)
        if i is None:
            i = self.find(p, STOREY_BAND)
        return self.add(p) if i is None else i


def _on_segment(p, a, b):
    """The point of segment ab nearest `p` in plan, at the segment's own height."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    span2 = dx * dx + dy * dy
    s = min(1.0, max(0.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / span2)) if span2 > 1e-9 else 0.0
    return (a[0] + s * dx, a[1] + s * dy, a[2] + s * (b[2] - a[2]))


def corner_reach(tris_pts):
    """`{corner: plan distance}` inside which another corner IS that patch corner.

    CORNER_TOL, but never over a quarter of the shortest patch edge at the
    corner, so two real corners of a small patch stay two.
    """
    out = {}
    for t in tris_pts:
        for k in range(3):
            a, b = tuple(t[k]), tuple(t[(k + 1) % 3])
            reach = min(CORNER_TOL, 0.25 * np.hypot(b[0] - a[0], b[1] - a[1]))
            out[a], out[b] = min(out.get(a, reach), reach), min(out.get(b, reach), reach)
    return out


def _weld_target(p, reach, corner_tree, edges, edge_tree):
    """Where generated corner `p` belongs on the patch: a corner in reach, else a point of an edge; or None."""
    corners = list(reach)
    near = Point(p[0], p[1]).buffer(CORNER_TOL)
    hits = [(np.hypot(c[0] - p[0], c[1] - p[1]), c) for c in (corners[int(j)] for j in corner_tree.query(near))
            if abs(c[2] - p[2]) <= WELD_Z]
    hits = [(d, c) for d, c in hits if d <= reach[c]]
    if hits:
        return min(hits)[1]
    hits = [(np.hypot(q[0] - p[0], q[1] - p[1]), q)
            for q in (_on_segment(p, *edges[int(j)]) for j in edge_tree.query(near)) if abs(q[2] - p[2]) <= WELD_Z]
    hits = [(d, q) for d, q in hits if d <= WELD_TOL]
    return min(hits)[1] if hits else None


def _welded(verts, tris, region):
    """`(verts, tris)` with each generated corner near the patch boundary moved onto it.

    A generator that has shifted a few units leaves its edges just beside the
    patch's; the strip between them refills as slivers. Moving the generated
    corner onto the patch corner or edge closes the strip. Corners landing on
    one position share one index.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    reach = corner_reach(region)
    edges = [(tuple(t[k]), tuple(t[(k + 1) % 3])) for t in region for k in range(3)]
    corner_tree = STRtree([Point(c[0], c[1]) for c in reach])
    edge_tree = STRtree([LineString([a[:2], b[:2]]) for a, b in edges])
    out, moved = list(verts), set()
    for i in sorted({k for t in tris for k in t[:3]}):
        q = _weld_target(out[i], reach, corner_tree, edges, edge_tree)
        if q is not None and q != out[i]:
            out[i] = tuple(float(c) for c in q)
            moved.add(i)
    first = {}
    for i, v in enumerate(out):
        if i not in moved:
            first.setdefault(v, i)
    remap = {i: first.setdefault(out[i], i) for i in sorted(moved)}
    return out, [tuple(remap.get(k, k) for k in t[:3]) + tuple(t[3:]) for t in tris]


def _collapsed(verts, tris, before):
    """Indices of triangles the weld flattened to a line or a point."""
    return [i for i, t in enumerate(tris)
            if len(set(t[:3])) < 3 or (plan_area([verts[k] for k in t[:3]]) <= AREA_EPS
                                       < plan_area([before[k] for k in t[:3]]))]


def _place(tris_pts, corner):
    """Index triples for `tris_pts`, dropping any whose corners collapse."""
    out = []
    for pts in tris_pts:
        t = tuple(corner(p) for p in pts)
        if len(set(t)) == 3:
            out.append(t)
    return out


def drop_unused_verts(verts, tris):
    """(verts, tris) with vertices no triangle uses removed and indices compacted."""
    used = sorted({v for t in tris for v in t[:3]})
    vmap = {old: new for new, old in enumerate(used)}
    return ([verts[v] for v in used],
            [tuple(vmap[v] for v in t[:3]) + tuple(t[3:]) for t in tris])


def _snapped(pool, tris_pts):
    """Patch triangles with each corner moved onto the generated vertex it already is.

    Stored corners are rounded to 0.01u; snapping restores the generator's
    exact value, so an unmoved generator yields exact overlaps and no slivers.
    """
    out = []
    for pts in tris_pts:
        ids = [pool.find(p, FROZEN_Z_TOL) for p in pts]
        out.append(tuple(p if i is None else pool.verts[i]
                         for p, i in zip(pts, ids)))
    return out


def _claims(pool, tris, frozen, voids):
    """`{generated triangle index: [claiming patch footprints]}` for every claimed one.

    `frozen` and `voids` are already snapped.  A generated triangle that IS a
    void -- same corners once snapped the same way -- is claimed whatever its
    size: a sliver a weld collapsed is too thin for the overlap test.
    """
    region = _region(frozen + voids)
    tree = STRtree([foot for _pts, foot in region])
    void_keys = {frozenset(pts) for pts in voids}
    out = {}
    for i, t in enumerate(tris):
        corners = [pool.verts[k] for k in t[:3]]
        owners = _owners(corners, region, tree)
        if not owners and frozenset(_snapped(pool, [corners])[0]) in void_keys:
            owners = [_footprint(corners)]
        if owners:
            out[i] = owners
    return out


def _edge_counts(tris):
    """`{frozenset(a, b): owners}` over every triangle edge."""
    out = {}
    for t in tris:
        for k in range(3):
            e = frozenset((t[k], t[(k + 1) % 3]))
            out[e] = out.get(e, 0) + 1
    return out


def _hanging(verts, a, b, movable):
    """`[(s, v, (x, y, z))]` movable vertices within SNAP_TOL of edge ab, ordered along it.

    The position given is the point ON the edge, at the edge's own height.
    """
    pa, pb = verts[a], verts[b]
    dx, dy, dz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
    span2 = dx * dx + dy * dy
    hits = []
    for v in (movable if span2 > 1e-9 else ()):
        p = verts[v]
        s = ((p[0] - pa[0]) * dx + (p[1] - pa[1]) * dy) / span2
        q = (pa[0] + s * dx, pa[1] + s * dy, pa[2] + s * dz)
        if (1e-6 < s < 1 - 1e-6 and abs(p[2] - q[2]) <= STOREY_BAND
                and (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= SNAP_TOL ** 2):
            hits.append((s, v, q))
    return sorted(hits)


def _split_open_edge(verts, t, counts, movable):
    """Frozen triangle `t` fanned at the refill vertices on one of its open edges, else `[t]`."""
    for k in range(3):
        a, b, opp = t[k], t[(k + 1) % 3], t[(k + 2) % 3]
        if counts.get(frozenset((a, b))) != 1:
            continue
        hits = _hanging(verts, a, b, movable)
        if hits:
            for _s, v, q in hits:
                verts[v] = q
            seq = [a] + [v for _s, v, _q in hits] + [b]
            return [(seq[m], seq[m + 1], opp) for m in range(len(seq) - 1)]
    return [t]


def _apex(verts, t):
    """`(apex, a, b)` of triangle `t`: the corner opposite its longest plan edge, then that edge."""
    spans = [(np.hypot(verts[t[k]][0] - verts[t[(k + 1) % 3]][0], verts[t[k]][1] - verts[t[(k + 1) % 3]][1]), k)
             for k in range(3)]
    k = max(spans)[1]
    return t[(k + 2) % 3], t[k], t[(k + 1) % 3]


def _on_frozen_corners(verts, frozen_t, kept_t, refill_t):
    """`refill_t` with each refill-only corner within a frozen corner's reach replaced by it.

    Where a generated edge crosses a frozen edge just beside its end, the
    refill leaves a corner there; fanning the frozen triangle at it would cut
    a sliver off the patch.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    fixed = {v for t in kept_t + frozen_t for v in t}
    reach = corner_reach([[verts[k] for k in t] for t in frozen_t])
    anchors = sorted({v for t in frozen_t for v in t})
    remap = {}
    for v in sorted({k for t in refill_t for k in t} - fixed):
        near = [(np.hypot(verts[f][0] - verts[v][0], verts[f][1] - verts[v][1]), f) for f in anchors
                if abs(verts[f][2] - verts[v][2]) <= WELD_Z]
        if near and min(near)[0] <= reach[tuple(verts[min(near)[1]])]:
            remap[v] = min(near)[1]
    out = [tuple(remap.get(k, k) for k in t) for t in refill_t]
    return [t for t in out if len(set(t)) == 3]


def _dissolved(verts, kept_t, refill_t):
    """`(kept_t, refill_t)` without refill slivers: each thinner than SLIVER_ALT is removed.

    A flat sliver's long edge belongs to a neighbour, which is split at the
    sliver's apex so the two sides still meet corner to corner; the new half
    joins the refill, so kept indices hold. A sliver climbing a wall (its apex
    more than WELD_Z off its long edge) is the leftover of a triangle the
    human removed and is only dropped.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    kept_t, refill_t = list(kept_t), list(refill_t)
    for _round in range(3):
        thin = [t for t in refill_t if altitude([verts[k] for k in t]) < SLIVER_ALT]
        if not thin:
            break
        for t in (t for t in thin if t in refill_t):
            refill_t.remove(t)
            c, a, b = _apex(verts, t)
            if abs(verts[c][2] - _on_segment(verts[c], verts[a], verts[b])[2]) > WELD_Z:
                continue
            for pool in (kept_t, refill_t):
                hit = next((n for n, o in enumerate(pool) if a in o and b in o and c not in o), None)
                if hit is not None:
                    other = pool[hit]
                    pool[hit] = tuple(c if k == b else k for k in other)
                    refill_t.append(tuple(c if k == a else k for k in other))
                    break
    return kept_t, refill_t


def _stitch(verts, frozen_t, kept_t, refill_t):
    """Frozen triangles with each open edge split where a refill vertex lies on it.

    The refill cuts the patch out of the removed floor, so where the new
    generator's floor edge crosses a frozen edge it leaves a vertex part-way
    along it -- a T-junction the engine will not link across.  Only vertices
    the refill alone uses are moved, onto the edge at its own height, so
    neither the frozen surface nor any kept generated triangle moves.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    used = {v for t in kept_t + frozen_t for v in t}
    movable = sorted({v for t in refill_t for v in t} - used)
    for _round in range(3):
        counts = _edge_counts(kept_t + frozen_t + refill_t)
        out = [p for t in frozen_t
               for p in _split_open_edge(verts, t, counts, movable)]
        if len(out) == len(frozen_t):
            return out
        frozen_t = out
    return frozen_t


def _open_edges(tris):
    """`{triangle index: [(a, b)]}` of every edge no other triangle shares."""
    counts = _edge_counts(tris)
    out = {}
    for i, t in enumerate(tris):
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            if counts.get(frozenset((a, b))) == 1:
                out.setdefault(i, []).append((a, b))
    return out


def _on_edge(verts, a, b, opp, used):
    """Vertices of `used` lying inside edge ab (SNAP_TOL in plan, WELD_Z in height), ordered along it;
    a vertex standing on `opp` is not one, as fanning at it would make a flat triangle."""
    pa, pb, po = verts[a], verts[b], verts[opp]
    dx, dy, dz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
    span2 = dx * dx + dy * dy
    if span2 <= 1e-9:
        return []
    p = used[1]
    s = ((p[:, 0] - pa[0]) * dx + (p[:, 1] - pa[1]) * dy) / span2
    off = np.abs((p[:, 0] - pa[0]) * dy - (p[:, 1] - pa[1]) * dx) / np.sqrt(span2)
    gap = np.abs(p[:, 2] - (pa[2] + s * dz))
    clear = np.hypot(p[:, 0] - po[0], p[:, 1] - po[1]) > SNAP_TOL
    hit = (s > 1e-6) & (s < 1 - 1e-6) & (off <= SNAP_TOL) & (gap <= WELD_Z) & clear
    return [int(used[0][i]) for i in np.argsort(s) if hit[i] and used[0][i] not in (a, b)]


def close_t_junctions(verts, tris, first=0):
    """`tris` with every open edge that a vertex of `tris[first:]` (the patch) lies on fanned at it; new
    pieces appended.

    A vertex part-way along another triangle's edge leaves the two sides
    unlinked, and the engine does not path across. Nothing moves; the
    triangle keeps its index as the first piece, so ledge indices hold.
    Only the patch's vertices fan: the generator's own mesh elsewhere is not
    the patch's business (fanning it laid a triangle over others, 2026-11-15).
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    out = [tuple(t) for t in tris]
    ids = np.array(sorted({v for t in out[first:] for v in t}), dtype=int)
    used = (ids, np.asarray([verts[v] for v in ids], dtype=float).reshape(-1, 3))
    counts = _edge_counts(out)
    for i in range(len(out)):
        for k in range(3):
            a, b, opp = out[i][k], out[i][(k + 1) % 3], out[i][(k + 2) % 3]
            mid = _on_edge(verts, a, b, opp, used) if counts.get(frozenset((a, b))) == 1 else []
            if mid:
                seq = [a] + mid + [b]
                pieces = [(seq[m], seq[m + 1], opp) for m in range(len(seq) - 1)]
                out[i] = pieces[0]
                out += pieces[1:]
                break
    return out


def centroid(pts):
    """Centroid of a triangle given as three points."""
    return tuple(sum(p[k] for p in pts) / 3.0 for k in range(3))


def _lip_distance(verts, lips, toward):
    """Plan distance² from `toward` to the nearest midpoint of `lips`."""
    return min((0.5 * (verts[a][0] + verts[b][0]) - toward[0]) ** 2
               + (0.5 * (verts[a][1] + verts[b][1]) - toward[1]) ** 2
               for a, b in lips)


def _heir(verts, out, first, lips, old, toward):
    """Index of the new triangle taking over removed triangle `old`'s ledge, or None.

    Of the patch and refill triangles (`out[first:]`) overlapping `old` on its
    storey with an open edge, the one whose open edge lies nearest `toward`
    wins -- the rule `from_pgrd._open_edge_towards` picks the edge by.
    """
    foot = _footprint(old)
    best = None
    for i in range(first, len(out)):
        pts = [verts[k] for k in out[i]]
        hit = foot.intersection(_footprint(pts)) if i in lips else None
        if hit is None or hit.area <= AREA_EPS:
            continue
        c = hit.representative_point()
        if abs(plane_z(old, c.x, c.y) - plane_z(pts, c.x, c.y)) > STOREY_BAND:
            continue
        d = _lip_distance(verts, lips[i], toward)
        if best is None or d < best[0]:
            best = (d, i)
    return None if best is None else best[1]


def _carry_ledges(verts, out, first, keep, before, ledges, links=()):
    """Ledge pairs renumbered into `out`; a removed side moves to its heir.

    `before` is each ledge triangle's corners as the generator built them.
    `links` are pinned `(upper, lower)` triangles, each resolved to the
    triangle now over it the same way.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    lips = _open_edges(out)
    rows = [(tuple(keep[i] if i in keep
                   else _heir(verts, out, first, lips, before[i], centroid(before[j]))
                   for i, j in ((u, l), (l, u))), tuple(rest))
            for (u, l, *rest) in ledges or ()]
    rows += [((_heir(verts, out, 0, lips, up, centroid(down)),
               _heir(verts, out, 0, lips, down, centroid(up))),
              (centroid(up)[2] - centroid(down)[2],))
             for up, down in links]
    carried, seen = [], set()
    for ends, rest in rows:
        if None not in ends and ends[0] != ends[1] and ends not in seen:
            seen.add(ends)
            carried.append(ends + rest)
    return carried


def apply_frozen(verts, tris, ledges, frozen, voids, links=()):
    """(verts, tris, ledges) with each frozen patch and its pinned links put back.

    A ledge whose triangle the patch replaced moves to the new triangle over it.
    `links` are `(upper, lower)` pairs of triangles given as three points.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    if not tris or not (frozen or voids):
        return verts, tris, ledges
    pool = _VertexPool(verts)
    frozen, voids = _newest(_snapped(pool, frozen)), _snapped(pool, voids)
    welded, tris = _welded(pool.verts, tris, frozen + voids)
    flat = _collapsed(welded, tris, pool.verts)
    pool = _VertexPool(welded)
    claimed = _claims(pool, tris, frozen, voids)
    claimed.update({i: [] for i in flat if i not in claimed})
    refill = []
    for group in _groups(tris, claimed):
        sheet = [[pool.verts[k] for k in tris[i][:3]] for i in group]
        refill += _refill(sheet, [f for i in group for f in claimed[i]])
    keep = {i: n for n, i in enumerate(
        i for i in range(len(tris)) if i not in claimed)}
    kept_t = [tuple(int(k) for k in tris[i][:3]) for i in keep]
    before = {i: [pool.verts[k] for k in tris[i][:3]]
              for (u, l, *_r) in ledges or () for i in (u, l)}
    frozen_t = ccw_in_plan(pool.verts, _place(frozen, pool.frozen_corner))
    refill_t = ccw_in_plan(pool.verts, _place(refill, pool.refill_corner))
    refill_t = _on_frozen_corners(pool.verts, frozen_t, kept_t, refill_t)
    frozen_t = _stitch(pool.verts, frozen_t, kept_t, refill_t)
    kept_t, refill_t = _dissolved(pool.verts, kept_t, refill_t)
    out = kept_t + frozen_t + refill_t
    ledges = _carry_ledges(pool.verts, out, len(kept_t), keep, before, ledges,
                           links)
    new_verts, new_tris = drop_unused_verts(pool.verts, close_t_junctions(pool.verts, out, len(kept_t)))
    return new_verts, new_tris, ledges
