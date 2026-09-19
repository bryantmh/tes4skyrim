"""Constrained Delaunay triangulation of one unioned sheet.

Takes a shapely polygon (a storey's unioned ribbons, with door wedges cut out)
and returns a near-equilateral mesh in a single shared vertex space.  Door base
lines survive as exactly one edge each; stairs are refined so no triangle spans
a storey step.

See: docs/commentary/tes5_import_navmesh.md#cdt-is-a-true-constrained-delaunay
"""

import math

import numpy as np

from .union_geom import (
    STOREY_GAP_Z, _has_edge, _segment_cuts, _split_triangle,
)

#: How far off a door base line a corner may sit and still be snapped onto it.
DOOR_SNAP_PERP = 4.0

#: Along-ribbon spacing of steep-ribbon (stair) seeds.
RIBBON_SEED_STEP = 24.0

#: Longest edge a steep (stair) triangle may keep before it is bisected.
STEEP_REFINE_EDGE = 64.0


def _door_edge_on_part(edge, part, tol=2.0):
    """Does this door base line belong to `part`, inside OR on its outline?

    Accepts the edge when its midpoint is within the polygon or within `tol`
    of the FULL boundary, interior rings included.

    See: docs/commentary/tes5_import_navmesh.md#door-edge-must-accept-interior-rings
    """
    from shapely.geometry import Point
    mx = 0.5 * (edge[0][0] + edge[1][0])
    my = 0.5 * (edge[0][1] + edge[1][1])
    p = Point(mx, my)
    try:
        return part.contains(p) or part.boundary.distance(p) <= tol
    except Exception:
        return False

def _snapped_ring(coords, lines):
    """The ring with each on-line corner moved to its nearer endpoint."""
    out = []
    for (x, y) in coords:
        for (q0, q1, ux, uy, dl) in lines:
            vx, vy = x - q0[0], y - q0[1]
            if abs(-vx * uy + vy * ux) > DOOR_SNAP_PERP:
                continue
            t = vx * ux + vy * uy
            if not (DOOR_SNAP_PERP < t < dl - DOOR_SNAP_PERP):
                continue
            x, y = (q0 if t < 0.5 * dl else q1)
            break
        if not out or (abs(out[-1][0] - x) > 1e-9
                       or abs(out[-1][1] - y) > 1e-9):
            out.append((x, y))
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _snap_outline_to_door_lines(poly, fixed_edges):
    """Move ring corners lying ON a door base line onto its nearer endpoint.

    Keeps the ring's vertex count and winding; corners move ALONG the line, so
    the covered ground is unchanged to within DOOR_SNAP_PERP.

    See: docs/commentary/tes5_import_navmesh.md#outline-corners-snap-onto-door-lines
    """
    from shapely.geometry import Polygon
    lines = []
    for e in fixed_edges:
        p0, p1 = e[0], e[1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        dl = math.hypot(dx, dy)
        if dl > 1e-9:
            lines.append((p0, p1, dx / dl, dy / dl, dl))
    if not lines:
        return poly
    try:
        shell = _snapped_ring(list(poly.exterior.coords), lines)
        if len(shell) < 3:
            return poly
        holes = [h for h in (_snapped_ring(list(r.coords), lines)
                             for r in poly.interiors) if len(h) >= 3]
        out = Polygon(shell, holes)
        if not out.is_valid:
            out = out.buffer(0)
        if (out.is_empty or not out.is_valid or not isinstance(out, Polygon)
                or abs(out.area - poly.area) > 0.02 * max(poly.area, 1.0)):
            return poly
        return out
    except Exception:
        return poly


def _reserve_door_wedges(poly, fixed_edges):
    """(parts, door_tris) with each door wedge cut out of `poly`.

    See: docs/commentary/tes5_import_navmesh.md#door-wedges-are-cut-not-stitched
    """
    from shapely.geometry import Polygon
    door_tris, reserved = [], []
    for e in (fixed_edges or ()):
        if len(e) < 3 or e[2] is None:
            continue
        p0, p1, apex = e[0], e[1], e[2]
        tri = Polygon([p0, p1, apex])
        if not tri.is_valid or tri.area < 1.0:
            continue
        reserved.append(tri)
        door_z = e[3] if len(e) > 3 and e[3] is not None else None
        entry = (tuple(p0), tuple(p1), tuple(apex))
        door_tris.append(entry if door_z is None
                         else entry + (float(door_z),))
    if not reserved:
        return [poly], door_tris
    try:
        cut = poly
        for r in reserved:
            cut = cut.difference(r)
        if cut.geom_type == 'GeometryCollection':
            from shapely.ops import unary_union
            gs = [g for g in cut.geoms if g.geom_type == 'Polygon']
            cut = unary_union(gs) if gs else cut
    except Exception:
        return [poly], []
    if cut.is_empty:
        return [], door_tris
    if cut.geom_type == 'Polygon':
        return [cut], door_tris
    if cut.geom_type == 'MultiPolygon':
        return [g for g in cut.geoms
                if g.geom_type == 'Polygon' and g.area >= 1.0], door_tris
    return [], door_tris


def _door_line_guard(fixed_edges):
    """Unit-vector records for every door base line, for the densify guard."""
    guard = []
    for e in fixed_edges:
        p0, p1 = e[0], e[1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        dl = math.hypot(dx, dy)
        if dl > 1e-9:
            guard.append((p0, p1, dx / dl, dy / dl, dl))
    return guard


def _runs_along_door_line(guard, x0, y0, x1, y1):
    """True when this boundary segment runs ALONG a door base line."""
    for (q0, _q1, ux, uy, dl) in guard:
        ok = True
        for (px, py) in ((x0, y0), (x1, y1),
                         (0.5 * (x0 + x1), 0.5 * (y0 + y1))):
            vx, vy = px - q0[0], py - q0[1]
            t = vx * ux + vy * uy
            if abs(-vx * uy + vy * ux) > 4.0 or not (-4.0 <= t <= dl + 4.0):
                ok = False
                break
        if ok:
            return True
    return False


def _grid_stations(x0, y0, x1, y1, target_edge):
    """Points where the segment's along-direction coordinate crosses a multiple
    of target_edge, none within a quarter spacing of either end.

    See: docs/commentary/tes5_import_navmesh.md#densify-on-a-world-grid
    """
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return []
    ux, uy = dx / length, dy / length
    s0 = x0 * ux + y0 * uy
    gap = 0.25 * target_edge
    out = []
    k = math.floor(s0 / target_edge) + 1
    while k * target_edge < s0 + length:
        d = k * target_edge - s0
        if gap <= d <= length - gap:
            out.append((x0 + ux * d, y0 + uy * d))
        k += 1
    return out


def _densified(part, guard, target_edge):
    """`part` with its rings resampled at target_edge, door lines exempt.

    See: docs/commentary/tes5_import_navmesh.md#door-base-line-suppresses-densification
    """
    from shapely.geometry import Polygon

    def ring(r):
        """The ring's coords, densified except along door lines."""
        coords = list(r.coords)
        out = []
        for i in range(len(coords) - 1):
            x0, y0 = coords[i]
            x1, y1 = coords[i + 1]
            out.append((x0, y0))
            if _runs_along_door_line(guard, x0, y0, x1, y1):
                continue
            out.extend(_grid_stations(x0, y0, x1, y1, target_edge))
        return out

    shell = ring(part.exterior)
    holes = [h for h in (ring(r) for r in part.interiors) if len(h) >= 3]
    dense = Polygon(shell, holes=holes)
    return dense if dense.is_valid else dense.buffer(0)


def _cdt_part(part, guard, target_edge, pid):
    """The constrained-Delaunay triangles of one part."""
    from shapely import constrained_delaunay_triangles as cdt
    try:
        out = cdt(_densified(part, guard, target_edge))
    except Exception:
        return []
    tris = []
    for g in getattr(out, 'geoms', ()):
        if g.geom_type != 'Polygon':
            continue
        corners = list(g.exterior.coords)[:-1]
        if len(corners) != 3:
            continue
        ia, ib, ic = (pid(x, y) for (x, y) in corners)
        if ia != ib and ib != ic and ia != ic:
            tris.append((ia, ib, ic))
    return tris


def _emit_door_triangles(door_tris, pid, tris_out):
    """Append each door wedge as ordinary mesh; returns its ring edge keys."""
    ring_edges = set()
    for d in door_tris:
        (b0, b1), apex = (d[0], d[1]), d[2]
        ia, ib, ic = pid(*b0), pid(*b1), pid(*apex)
        if ia == ib or ib == ic or ia == ic:
            continue
        cross = ((b1[0] - b0[0]) * (apex[1] - b0[1])
                 - (apex[0] - b0[0]) * (b1[1] - b0[1]))
        tris_out.append((ia, ib, ic) if cross > 0 else (ia, ic, ib))
        for (u, v) in ((ia, ib), (ib, ic), (ia, ic)):
            ring_edges.add((u, v) if u < v else (v, u))
    return ring_edges


def _triangulate(poly, target_edge, fixed_edges=None, steep_seeds=None,
                 land=None, max_edge=None):
    """Triangulate a shapely polygon into UNIFORM, well-shaped triangles.

    Returns (verts2d, tris) in ONE shared vertex space.

    fixed_edges: [(p0, p1, apex), ...] door triangles, cut out and re-added as
    ordinary mesh.  steep_seeds: [(x, y, is_steep), ...] on stair centerlines.
    land: a terrain sheet's LandField; its triangles are bisected past
    `max_edge` and wherever they cut through the land's relief.

    See: docs/commentary/tes5_import_navmesh.md#cdt-is-a-true-constrained-delaunay
    """
    if len(list(poly.exterior.coords)[:-1]) < 3:
        return [], []
    fixed_edges = fixed_edges or []
    if fixed_edges:
        poly = _snap_outline_to_door_lines(poly, fixed_edges)
    parts, door_tris = _reserve_door_wedges(poly, fixed_edges)

    pt_index, pts = {}, []

    def pid(x, y):
        """Index of the mesh vertex at (x, y), deduped at millimetre scale."""
        key = (round(float(x), 3), round(float(y), 3))
        i = pt_index.get(key)
        if i is None:
            i = len(pts)
            pts.append((float(x), float(y)))
            pt_index[key] = i
        return i

    guard = _door_line_guard(fixed_edges)
    tris_out = []
    for part in parts:
        tris_out.extend(_hex_refine(part, pts, pid,
                                    _cdt_part(part, guard, target_edge, pid),
                                    target_edge))
    ring_edges = _emit_door_triangles(door_tris, pid, tris_out)

    verts = [(float(x), float(y)) for (x, y) in pts]
    if not tris_out:
        return _earcut_fallback(poly)
    if land is not None:
        verts, tris_out = _refine_land(verts, tris_out, land, max_edge,
                                       protected=ring_edges)
        tris_out = _flip2d(verts, tris_out)
    steep_pts = [(sx, sy) for (sx, sy, st) in (steep_seeds or ()) if st]
    if steep_pts:
        return _refine_steep(verts, tris_out, steep_pts, protected=ring_edges)
    return verts, tris_out


def _lattice_points(part, spacing):
    """Hex-lattice points inside `part`, eroded so none can mint a sliver.

    Offset rows (the honeycomb dual) at `spacing`, anchored on the part's own
    bounds so the result is deterministic per part.
    """
    try:
        eroded = part.buffer(-0.45 * spacing)
        if eroded.is_empty:
            return []
    except Exception:
        return []
    minx, miny, maxx, maxy = part.bounds
    row_h = spacing * 0.8660254037844386
    cand, row = [], 0
    y = miny + 0.5 * row_h
    while y < maxy:
        x = minx + (0.25 if row % 2 == 0 else 0.75) * spacing
        while x < maxx:
            cand.append((x, y))
            x += spacing
        y += row_h
        row += 1
    if not cand:
        return []
    try:
        import shapely
        hits = shapely.contains_xy(eroded, [c[0] for c in cand],
                                   [c[1] for c in cand])
        return [c for c, k in zip(cand, hits.tolist()) if k]
    except Exception:
        from shapely.geometry import Point
        from shapely.prepared import prep
        ready = prep(eroded)
        return [c for c in cand if ready.contains(Point(c))]


def _containing_triangle(pts, tri_at, alive, T, px, py):
    """Index of the triangle strictly containing (px, py), else None.

    Strictly interior: a point riding an edge would 3-fan into a sliver pair,
    and the lattice loses nothing by skipping it.
    """
    for ti in tri_at:
        if not alive[ti]:
            continue
        a, b, c = T[ti]
        ax, ay = pts[a]
        bx, by = pts[b]
        cx, cy = pts[c]
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-9:
            continue
        l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
        l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
        if l0 >= 0.05 and l1 >= 0.05 and 1.0 - l0 - l1 >= 0.05:
            return ti
    return None


class _Grid:
    """Uniform spatial buckets over triangles and vertices, for _hex_refine."""

    def __init__(self, pts, tris, cell):
        """Bucket every triangle and vertex at `cell` spacing."""
        self.pts, self.tris, self.cell = pts, tris, cell
        self.tri_at, self.vert_at = {}, {}
        for ti in range(len(tris)):
            self.add_tri(ti)
        for i in sorted({i for t in tris for i in t}):
            self.add_vert(i)

    def add_tri(self, ti):
        """Bucket triangle `ti` by its bounding box."""
        xs = [self.pts[i][0] for i in self.tris[ti]]
        ys = [self.pts[i][1] for i in self.tris[ti]]
        for gx in range(int(min(xs) // self.cell), int(max(xs) // self.cell) + 1):
            for gy in range(int(min(ys) // self.cell),
                            int(max(ys) // self.cell) + 1):
                self.tri_at.setdefault((gx, gy), []).append(ti)

    def add_vert(self, i):
        """Bucket vertex `i` by its cell."""
        p = self.pts[i]
        self.vert_at.setdefault((int(p[0] // self.cell),
                                 int(p[1] // self.cell)), []).append(i)

    def crowded(self, px, py, min_d2):
        """True when an existing vertex is within sqrt(min_d2) of (px, py)."""
        gx, gy = int(px // self.cell), int(py // self.cell)
        return any((self.pts[i][0] - px) ** 2 + (self.pts[i][1] - py) ** 2
                   < min_d2
                   for ddx in (-1, 0, 1) for ddy in (-1, 0, 1)
                   for i in self.vert_at.get((gx + ddx, gy + ddy), ()))


def _hex_refine(part, pts, pid, tris, spacing):
    """Insert a hex lattice of interior vertices, then flip to shape.

    See: docs/commentary/tes5_import_navmesh.md#cdt-is-a-true-constrained-delaunay
    """
    if not tris:
        return tris
    cand = _lattice_points(part, spacing)
    if not cand:
        return _flip2d(pts, tris)

    T = [tuple(t) for t in tris]
    alive = [True] * len(T)
    grid = _Grid(pts, T, spacing)
    min_d2 = (0.45 * spacing) ** 2
    for (px, py) in cand:
        if grid.crowded(px, py, min_d2):
            continue
        gx, gy = int(px // spacing), int(py // spacing)
        hit = _containing_triangle(pts, grid.tri_at.get((gx, gy), ()),
                                   alive, T, px, py)
        if hit is None:
            continue
        pi = pid(px, py)
        grid.add_vert(pi)
        a, b, c = T[hit]
        alive[hit] = False
        for nt in ((a, b, pi), (b, c, pi), (c, a, pi)):
            T.append(nt)
            alive.append(True)
            grid.add_tri(len(T) - 1)
    return _flip2d(pts, [T[ti] for ti in range(len(T)) if alive[ti]])


def _flip_gain(pts, t1, t2, key, edge_tris, new_edges):
    """(c, d) when flipping the shared edge `key` improves both triangles.

    Returns None when the flip is illegal (the diagonal already exists, or the
    quad is not convex) or does not strictly improve the worse edge ratio.

    See: docs/commentary/tes5_import_navmesh.md#flip-never-onto-an-existing-diagonal
    """
    a, b = key
    c = next((v for v in t1 if v != a and v != b), None)
    d = next((v for v in t2 if v != a and v != b), None)
    if c is None or d is None or c == d:
        return None
    ckey = (c, d) if c < d else (d, c)
    if ckey in edge_tris or ckey in new_edges:
        return None

    def ratio(x, y, z):
        """Longest/shortest edge of the triangle; 1e9 when degenerate."""
        px, py, pz = pts[x], pts[y], pts[z]
        e = [math.hypot(px[0] - py[0], px[1] - py[1]),
             math.hypot(py[0] - pz[0], py[1] - pz[1]),
             math.hypot(pz[0] - px[0], pz[1] - px[1])]
        lo = min(e)
        return (max(e) / lo) if lo > 1e-9 else 1e9

    def area2(x, y, z):
        """Twice the signed area of the triangle."""
        px, py, pz = pts[x], pts[y], pts[z]
        return ((py[0] - px[0]) * (pz[1] - px[1])
                - (py[1] - px[1]) * (pz[0] - px[0]))

    if max(ratio(c, d, a), ratio(c, d, b)) >= max(ratio(*t1),
                                                  ratio(*t2)) - 1e-9:
        return None
    s_a, s_b = area2(c, d, a), area2(c, d, b)
    if s_a * s_b >= 0 or abs(s_a) <= 1e-6 or abs(s_b) <= 1e-6:
        return None
    return (c, d, s_a, s_b, ckey)


def _flip2d(pts, tris, rounds=4):
    """Ratio-improving diagonal flips on a 2D triangulation.

    Boundary and constraint edges have one owner and are structurally
    unflippable, so the outline, the holes and the door base lines are safe.
    """
    tris = [tuple(t) for t in tris]
    for _ in range(rounds):
        edge_tris = {}
        for ti, t in enumerate(tris):
            for k in range(3):
                a, b = t[k], t[(k + 1) % 3]
                edge_tris.setdefault((a, b) if a < b else (b, a),
                                     []).append(ti)
        done, new_edges, changed = set(), set(), False
        for key in sorted(edge_tris):
            owners = edge_tris[key]
            if len(owners) != 2:
                continue
            ti, tj = owners
            if ti in done or tj in done:
                continue
            got = _flip_gain(pts, tris[ti], tris[tj], key, edge_tris,
                             new_edges)
            if got is None:
                continue
            c, d, s_a, s_b, ckey = got
            tris[ti] = (c, d, key[1]) if s_b > 0 else (d, c, key[1])
            tris[tj] = (c, d, key[0]) if s_a > 0 else (d, c, key[0])
            new_edges.add(ckey)
            done.update((ti, tj))
            changed = True
        if not changed:
            break
    return tris


def _seed_grid(steep_pts, cell):
    """Steep seeds bucketed by grid cell.

    A triangle then tests only the seeds its bbox can contain; the all-pairs
    form was O(tris x seeds) per round and timed out on seed-heavy cells.
    """
    grid = {}
    for (px, py) in steep_pts:
        grid.setdefault((int(px // cell), int(py // cell)), []).append((px, py))
    return grid


def _fan_split(t, split_edges, from_midpoint=False):
    """`t` split at each marked edge, fanned from corners + midpoints.

    Handles one, two or three marked edges in a single conforming pass.
    `from_midpoint` fans from the first midpoint instead of corner 0.
    See: docs/commentary/tes5_import_navmesh.md#land-sheets-bisect-long-edges
    """
    ring, first_mid = [], None
    for k in range(3):
        a, b = t[k], t[(k + 1) % 3]
        ring.append(a)
        m = split_edges.get((a, b) if a < b else (b, a))
        if m is not None:
            first_mid = len(ring) if first_mid is None else first_mid
            ring.append(m)
    if len(ring) == 3:
        return [t]
    if from_midpoint:
        ring = ring[first_mid:] + ring[:first_mid]
    out = []
    for i in range(1, len(ring) - 1):
        tri = (ring[0], ring[i], ring[i + 1])
        if len(set(tri)) == 3:
            out.append(tri)
    return out


def _longest_edge(verts, t):
    """(squared length, a, b) of the triangle's longest edge in plan."""
    best = None
    for k in range(3):
        a, b = t[k], t[(k + 1) % 3]
        pa, pb = verts[a], verts[b]
        d2 = (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2
        if best is None or d2 > best[0]:
            best = (d2, a, b)
    return best


def _has_plan_area(verts, t):
    """False for a triangle whose corners are collinear in plan."""
    (ax, ay), (bx, by), (cx, cy) = verts[t[0]], verts[t[1]], verts[t[2]]
    return abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) >= 2.0


def _carries_seed(verts, t, grid, cell):
    """True when a steep seed lies in or near this triangle."""
    ax, ay = verts[t[0]]
    bx, by = verts[t[1]]
    cx, cy = verts[t[2]]
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-9:
        return False
    for gx in range(int(min(ax, bx, cx) // cell),
                    int(max(ax, bx, cx) // cell) + 1):
        for gy in range(int(min(ay, by, cy) // cell),
                        int(max(ay, by, cy) // cell) + 1):
            for (px, py) in grid.get((gx, gy), ()):
                l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
                l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                if l0 >= -0.02 and l1 >= -0.02 and 1.0 - l0 - l1 >= -0.02:
                    return True
    return False


def _refine_steep(verts, tris, steep_pts, protected=()):
    """Bisect triangles carrying steep centerline seeds until they are fine.

    Longest-edge bisection with the neighbour split at the same midpoint, so
    every split keeps the triangulation conforming.  Edges in `protected`
    (door rings) are never split.

    See: docs/commentary/tes5_import_navmesh.md#steep-refinement-keeps-stairs-alive
    """
    verts = [tuple(v) for v in verts]
    tris = [tuple(t) for t in tris]
    if not steep_pts or not tris:
        return verts, tris
    max_e2 = STEEP_REFINE_EDGE * STEEP_REFINE_EDGE
    cell = STEEP_REFINE_EDGE * 2.0
    grid = _seed_grid(steep_pts, cell)
    for _round in range(6):
        split_edges = {}
        for t in tris:
            if not _carries_seed(verts, t, grid, cell):
                continue
            d2, a, b = _longest_edge(verts, t)
            key = (a, b) if a < b else (b, a)
            if d2 <= max_e2 or key in protected or key in split_edges:
                continue
            pa, pb = verts[a], verts[b]
            split_edges[key] = len(verts)
            verts.append((0.5 * (pa[0] + pb[0]), 0.5 * (pa[1] + pb[1])))
        if not split_edges:
            break
        tris = [x for t in tris for x in _fan_split(t, split_edges)]
    return verts, tris


#: A terrain triangle is split while the LAND leaves one of its edges by more than this.
LAND_RELIEF_TOL = 16.0
#: ...but never below this edge length.
LAND_MIN_EDGE = 96.0


def _cuts_relief(verts, t, land):
    """True when the LAND bulges or dips past LAND_RELIEF_TOL along an edge of `t`."""
    for k in range(3):
        (ax, ay), (bx, by) = verts[t[k]], verts[t[(k + 1) % 3]]
        chord = 0.5 * (land.z(ax, ay) + land.z(bx, by))
        if abs(land.z(0.5 * (ax + bx), 0.5 * (ay + by)) - chord) > LAND_RELIEF_TOL:
            return True
    return False


def _refine_land(verts, tris, land, max_edge, protected=()):
    """Bisect a terrain sheet's triangles: over-long ones, and ones cutting relief.

    Longest-edge bisection fanned from the midpoint, so it stays conforming.
    See: docs/commentary/tes5_import_navmesh.md#land-sheets-bisect-long-edges
    """
    verts = [tuple(v) for v in verts]
    tris = [tuple(t) for t in tris]
    max_e2, min_e2 = max_edge * max_edge, LAND_MIN_EDGE * LAND_MIN_EDGE
    for _round in range(6):
        split_edges = {}
        for t in tris:
            d2, a, b = _longest_edge(verts, t)
            key = (a, b) if a < b else (b, a)
            if (key in protected or key in split_edges or d2 <= min_e2
                    or not _has_plan_area(verts, t)):
                continue
            if d2 <= max_e2 and not _cuts_relief(verts, t, land):
                continue
            pa, pb = verts[a], verts[b]
            split_edges[key] = len(verts)
            verts.append((0.5 * (pa[0] + pb[0]), 0.5 * (pa[1] + pb[1])))
        if not split_edges:
            break
        tris = [x for t in tris for x in _fan_split(t, split_edges, True)]
    return verts, tris


def _split_along(verts, tris, ax, ay, bx, by, vid):
    """(tris, changed) with every triangle the segment crosses re-fanned."""
    out, changed = [], False
    for t in tris:
        pts = [verts[t[0]], verts[t[1]], verts[t[2]]]
        if _has_edge(verts, t, ax, ay, bx, by):
            out.append(t)
            continue
        cuts = _segment_cuts(pts, ax, ay, bx, by)
        if len(cuts) < 2:
            out.append(t)
            continue
        out.extend(_split_triangle(t, pts, cuts, vid, verts))
        changed = True
    return out, changed


def _recover_constraints(verts, tris, segments):
    """Force each segment to appear as a triangle edge.

    Splits any triangle the segment crosses at the crossing points and re-fans
    it, so the result triangulates the SAME area -- nothing dropped, no ground
    invented -- with the segment now running along triangle edges.
    """
    verts = [list(v) for v in verts]
    tris = [tuple(t) for t in tris]
    index = {}
    for i, v in enumerate(verts):
        index.setdefault((round(v[0], 3), round(v[1], 3)), i)

    def vid(x, y):
        """Index of the vertex at (x, y), minting one if needed."""
        key = (round(x, 3), round(y, 3))
        i = index.get(key)
        if i is None:
            i = len(verts)
            verts.append([float(x), float(y)])
            index[key] = i
        return i

    for (p0, p1) in segments:
        ax, ay = float(p0[0]), float(p0[1])
        bx, by = float(p1[0]), float(p1[1])
        if math.hypot(bx - ax, by - ay) < 1e-9:
            continue
        for _round in range(4):
            tris, changed = _split_along(verts, tris, ax, ay, bx, by, vid)
            if not changed:
                break
    return [tuple(v) for v in verts], tris


def _earcut_fallback(poly):
    """Plain earcut of a polygon — used only if Delaunay fails on a piece."""
    import mapbox_earcut as earcut

    rings = [list(poly.exterior.coords)[:-1]]
    for r in poly.interiors:
        rings.append(list(r.coords)[:-1])
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return [], []
    flat = []
    ring_ends = []
    for r in rings:
        for (x, y) in r:
            flat.append([float(x), float(y)])
        ring_ends.append(len(flat))
    arr = np.asarray(flat, dtype=np.float64)
    try:
        idx = earcut.triangulate_float64(arr, np.asarray(ring_ends,
                                                         dtype=np.uint32))
    except Exception:
        return [], []
    verts = [(float(p[0]), float(p[1])) for p in arr]
    tris = [(int(idx[i]), int(idx[i + 1]), int(idx[i + 2]))
            for i in range(0, len(idx) - 2, 3)]
    return verts, tris

def _ribbon_seeds(strips, target_edge):
    """Interior seed points down every ribbon centerline (stairs get more).

    Returns [(x, y, is_steep), ...].  Steep ribbons are sampled finely along
    the centerline and both rails; flat ones get a coarse centerline row that
    the Poisson guard mostly rejects.

    See: docs/commentary/tes5_import_navmesh.md#ribbon-centerline-seeds
    """
    seeds = []
    for s in strips:
        ax, ay, az = s['a']
        bx, by, bz = s['b']
        run = math.hypot(bx - ax, by - ay)
        if run < 1e-3:
            continue
        wx, wy = s['w']
        h = s['half']
        rise = abs(bz - az)
        steep = (s.get('land') is None
                 and rise / run * target_edge > STOREY_GAP_Z * 0.5)
        if steep:
            climb_step = STOREY_GAP_Z * 0.33
            step = max(RIBBON_SEED_STEP, climb_step * run / max(rise, 1e-6))
            offs = (-h * 0.6, 0.0, h * 0.6)
        else:
            step = target_edge * 0.9
            offs = (0.0,)
        n = max(1, int(run / step))
        for k in range(n + 1):
            f = k / n
            cx, cy = ax + (bx - ax) * f, ay + (by - ay) * f
            for off in offs:
                seeds.append((cx + wx * off, cy + wy * off, steep))
    return seeds
