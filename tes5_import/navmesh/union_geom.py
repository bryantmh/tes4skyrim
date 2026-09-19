"""Ribbon geometry primitives: the leaf every other union module builds on.

A "strip" is the corridor ribbon around one pathgrid edge -- a dict carrying its
centerline `a`/`b`, unit axis `u`, perpendicular `w`, `half` width, and
optionally an explicit `poly` outline.  Everything here is a pure function of a
strip or a triangle: no mesh state, no shapely at module scope.

See: docs/commentary/tes5_import_navmesh.md#union-geometry-constants
"""

import math

from . import params

#: Heights within this at one point are ONE walkable surface.
SAME_SURFACE_Z = 36.0

#: Two levels are different STOREYS only when at least this far apart.
STOREY_GAP_Z = 120.0

#: How far a corner's ground may be from a surface and still count as ON it.
REACH_TOL = STOREY_GAP_Z

#: Steepest triangle the mesh may carry, as cos(slope); cos(55 deg).
WALL_SLOPE_COS = 0.574

#: A free edge dropping this far is a silhouette over open space, not a join.
FLAP_EDGE_DROP = 40.0

#: Memo of strip identity -> ribbon polygon, cleared once per build.
_RIBBON_CACHE = {}


def _ribbon_polygon(s):
    """The strip's ribbon as a 2D polygon, memoised on strip identity.

    A strip may carry an explicit 'poly' outline instead, used verbatim.

    See: docs/commentary/tes5_import_navmesh.md#ribbon-polygon-memoisation
    """
    cached = _RIBBON_CACHE.get(id(s))
    if cached is not None:
        return cached[1]
    p = _ribbon_polygon_uncached(s)
    _RIBBON_CACHE[id(s)] = (s, p)
    return p

def _ribbon_cache_clear():
    """Drop the memo; call once per build."""
    _RIBBON_CACHE.clear()

def _repair_invalid_outline(poly, s):
    """A self-intersecting outline as a valid polygon covering its centerline.

    Keeps EVERY lobe of the buffer(0) repair and unions in a minimum-width band
    over the centerline, which the ribbon must always contain.

    See: docs/commentary/tes5_import_navmesh.md#invalid-ribbon-outline-repair
    """
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    fixed = poly.buffer(0)
    pieces = []
    if not fixed.is_empty:
        parts = fixed.geoms if hasattr(fixed, 'geoms') else [fixed]
        pieces.extend(g for g in parts
                      if isinstance(g, Polygon) and g.area > 0.0)
    spine = LineString([(s['a'][0], s['a'][1]), (s['b'][0], s['b'][1])])
    pieces.append(spine.buffer(max(params.RIBBON_GROW_MIN_HALF, 1.0),
                               cap_style=2))
    try:
        out = unary_union(pieces)
    except Exception:
        return pieces[-1]
    if not isinstance(out, Polygon) and not hasattr(out, 'geoms'):
        return pieces[-1]
    return out


def _ribbon_polygon_uncached(s):
    """The strip's ribbon polygon, built fresh (see `_ribbon_polygon`)."""
    from shapely.geometry import Polygon

    if s.get('poly') is not None:
        p = Polygon(s['poly'])
        return p if p.is_valid else _repair_invalid_outline(p, s)

    ax, ay = s['a'][0], s['a'][1]
    bx, by = s['b'][0], s['b'][1]
    wx, wy = s['w']
    h = s['half']
    return Polygon([
        (ax + wx * h, ay + wy * h),
        (bx + wx * h, by + wy * h),
        (bx - wx * h, by - wy * h),
        (ax - wx * h, ay - wy * h),
    ])


def _clip_strip_near(s, nx, ny, r, piece):
    """A copy of strip `s` truncated to within `r` of (nx, ny).

    Used when a ribbon donates ground at a junction: the owning sheet needs
    the arriving corridor's HEIGHT over the donated disc and nothing beyond.
    The centerline is cut at the node's own projection and the footprint
    becomes the donated piece, so the strip can never answer a level lookup
    outside the ground that changed hands.

    See: docs/commentary/tes5_import_navmesh.md#clip-strip-cuts-at-the-node-projection
    """
    ax, ay, az = s['a']
    bx, by, bz = s['b']
    run = math.hypot(bx - ax, by - ay)
    out = dict(s)
    if run > 1e-6:
        t_node = ((nx - ax) * (bx - ax) + (ny - ay) * (by - ay)) / (run * run)
        t_node = max(0.0, min(1.0, t_node))
        da = math.hypot(ax - nx, ay - ny)
        db = math.hypot(bx - nx, by - ny)
        if da <= db:
            f = min(1.0, t_node + r / run)
            out['b'] = (ax + (bx - ax) * f, ay + (by - ay) * f,
                        az + (bz - az) * f)
        else:
            f = max(0.0, t_node - r / run)
            out['a'] = (ax + (bx - ax) * f, ay + (by - ay) * f,
                        az + (bz - az) * f)
    try:
        best = None
        for g in getattr(piece, 'geoms', (piece,)):
            if g.geom_type == 'Polygon' and (best is None
                                             or g.area > best.area):
                best = g
        if best is not None:
            out['poly'] = list(best.exterior.coords)
    except Exception:
        pass
    return out

def _poly_strip(poly2d, z):
    """A flat footprint polygon at a fixed height, as a strip for the union.

    The door footprint (base line bridged to the corridor edge) is handed in
    this way: it contributes its outline to the union and a constant height z to
    the level lookup, so the door ground knows how high it sits.  Its axis runs
    along the first polygon edge (only used to give the height lookup a gradient,
    which is flat here anyway).
    """
    a = (float(poly2d[0][0]), float(poly2d[0][1]), float(z))
    b = (float(poly2d[1][0]), float(poly2d[1][1]), float(z))
    length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    return {
        'edge': (-1, -1),
        'na': a, 'nb': b, 'a': a, 'b': b,
        'u': (ux, uy), 'w': (-uy, ux),
        'half': 0.5 * length, 'len': length,
        'poly': [(float(p[0]), float(p[1])) for p in poly2d],
    }

def _land_of(strips):
    """The LandField the group's terrain-standing strips follow, or None."""
    return next((s['land'] for s in strips if s.get('land') is not None), None)


def _height_on(s, px, py):
    """Height of strip `s` at (px, py), following its own slope.

    A STEEP strip may carry a 'prof' polyline whose interior follows the real
    treads; otherwise the straight A->B line is used.  Must stay IDENTICAL to
    the native mirror in grow.cpp (py_levels_at).

    See: docs/commentary/tes5_import_navmesh.md#height-follows-the-pathgrid-line
    """
    prof = s.get('prof')
    if prof and len(prof) >= 2:
        best_d2 = None
        best_z = prof[0][2]
        for k in range(len(prof) - 1):
            qax, qay, qaz = prof[k]
            qbx, qby, qbz = prof[k + 1]
            dx, dy = qbx - qax, qby - qay
            d2 = dx * dx + dy * dy
            t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, (
                (px - qax) * dx + (py - qay) * dy) / d2))
            cx, cy = qax + dx * t, qay + dy * t
            dd = (px - cx) ** 2 + (py - cy) ** 2
            if best_d2 is None or dd < best_d2:
                best_d2 = dd
                best_z = qaz + (qbz - qaz) * t
        return best_z
    ax, ay, az = s['a']
    bx, by, bz = s['b']
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((px - ax) * dx +
                                                 (py - ay) * dy) / d2))
    if s.get('land') is not None:
        return s['land'].z(px, py)
    return az + (bz - az) * t

def _distance_to(s, px, py):
    """Distance from (px, py) to the strip's centerline.

    For a strip with an explicit outline (a door triangle) the distance is 0
    inside that outline, so it only ever claims the ground it actually covers —
    a centerline measure would let it claim well outside its own shape.
    """
    if s.get('poly') is not None:
        if _point_in_poly(px, py, s['poly']):
            return 0.0
        return min(_seg_dist(px, py, s['poly'][i],
                             s['poly'][(i + 1) % len(s['poly'])])
                   for i in range(len(s['poly'])))

    ax, ay = s['a'][0], s['a'][1]
    bx, by = s['b'][0], s['b'][1]
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((px - ax) * dx +
                                                 (py - ay) * dy) / d2))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))

def _point_in_poly(px, py, poly):
    """True when (px, py) is inside the polygon ring (ray cast)."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xin = x1 + (py - y1) * (x2 - x1) / ((y2 - y1) or 1e-12)
            if px < xin:
                inside = not inside
    return inside

def _seg_dist(px, py, a, b):
    """Distance from (px, py) to segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((px - a[0]) * dx +
                                                 (py - a[1]) * dy) / d2))
    return math.hypot(px - (a[0] + dx * t), py - (a[1] + dy * t))

def _near(p, x, y):
    """True when point `p` is within 1e-6 of (x, y) in plan."""
    return abs(p[0] - x) < 1e-6 and abs(p[1] - y) < 1e-6

def _has_edge(verts, t, ax, ay, bx, by):
    """True if the triangle already has an edge lying along the segment."""
    for k in range(3):
        p = verts[t[k]]
        q = verts[t[(k + 1) % 3]]
        if (_near(p, ax, ay) and _near(q, bx, by)) or \
                (_near(p, bx, by) and _near(q, ax, ay)):
            return True
    return False

def _segment_cuts(pts, ax, ay, bx, by):
    """Points where the segment crosses this triangle's edges (deduped)."""
    cuts = []
    for k in range(3):
        p, q = pts[k], pts[(k + 1) % 3]
        hit = _seg_intersect(p[0], p[1], q[0], q[1], ax, ay, bx, by)
        if hit is None:
            continue
        if not any(abs(hit[0] - c[0]) < 1e-6 and abs(hit[1] - c[1]) < 1e-6
                   for c in cuts):
            cuts.append(hit)
    return cuts

def _seg_intersect(x1, y1, x2, y2, x3, y3, x4, y4):
    """The crossing point of two segments, or None if they do not."""
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if t < -1e-9 or t > 1 + 1e-9 or u < -1e-9 or u > 1 + 1e-9:
        return None
    return (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)

def _split_triangle(t, pts, cuts, vid, verts):
    """Re-triangulate one triangle around the two points the segment cuts."""
    c0 = vid(cuts[0][0], cuts[0][1])
    c1 = vid(cuts[1][0], cuts[1][1])
    if c0 == c1:
        return [t]
    ring = []
    for k in range(3):
        ring.append(t[k])
        p, q = pts[k], pts[(k + 1) % 3]
        on = [c for c in (c0, c1)
              if _on_segment(verts[c], p, q) and
              not _near(verts[c], p[0], p[1]) and
              not _near(verts[c], q[0], q[1])]
        on.sort(key=lambda c: (verts[c][0] - p[0]) ** 2 +
                (verts[c][1] - p[1]) ** 2)
        ring.extend(on)
    ring = [v for i, v in enumerate(ring) if v not in ring[:i]]
    if len(ring) < 3:
        return [t]
    out = []
    for i in range(1, len(ring) - 1):
        tri = (ring[0], ring[i], ring[i + 1])
        if len(set(tri)) == 3:
            out.append(tri)
    return out or [t]

def _on_segment(c, p, q):
    """True when point `c` lies on segment p-q."""
    cross = ((q[0] - p[0]) * (c[1] - p[1]) - (q[1] - p[1]) * (c[0] - p[0]))
    if abs(cross) > 1e-6:
        return False
    dot = (c[0] - p[0]) * (q[0] - p[0]) + (c[1] - p[1]) * (q[1] - p[1])
    if dot < -1e-9:
        return False
    return dot <= (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + 1e-9

def _tri_edges(tri):
    """The triangle's three edges as sorted (lo, hi) keys."""
    return [(tri[k], tri[(k + 1) % 3]) if tri[k] < tri[(k + 1) % 3]
            else (tri[(k + 1) % 3], tri[k]) for k in range(3)]

def _tri_area(verts, tri):
    """XY-projected area of a triangle."""
    a, b, c = verts[tri[0]], verts[tri[1]], verts[tri[2]]
    return abs((b[0] - a[0]) * (c[1] - a[1]) -
               (b[1] - a[1]) * (c[0] - a[0])) * 0.5

def _tri_span(verts, tri):
    """Longest edge length of a triangle (3D)."""
    best = 0.0
    for k in range(3):
        p = verts[tri[k]]
        q = verts[tri[(k + 1) % 3]]
        d = math.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 +
                      (p[2] - q[2]) ** 2)
        best = max(best, d)
    return best

def _tri_components(tris):
    """Component id per triangle, over SHARED EDGES (what the engine walks)."""
    edges = {}
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            edges.setdefault((a, b) if a < b else (b, a), []).append(ti)
    parent = list(range(len(tris)))

    def find(x):
        """Root of x, path-compressed."""
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ts in edges.values():
        for i in range(1, len(ts)):
            ra, rb = find(ts[0]), find(ts[i])
            if ra != rb:
                parent[ra] = rb
    return [find(i) for i in range(len(tris))]
