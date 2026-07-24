"""Boolean polygon union of the corridor ribbons, then retriangulation.

WHY THIS IS THE RIGHT ALGORITHM

Corridor ribbons overlap wherever pathgrid lines converge.  Cutting them
pairwise (trim the ribbon, weld the seam, patch the junction) is an
approximation that has to get every case right — end-to-end, crossing, wedge,
collinear, dead end — and any case it gets wrong shows up as either lost ground
or stacked sheets.

The union does not approximate.  Each ribbon is a polygon; the geometric union
of the polygons is a region whose area is EXACTLY the measure of the ground the
corridors cover.  Retriangulating that region produces triangles that are
non-overlapping by definition.  So:

    coverage  == 100% by construction (the union contains every ribbon)
    overlap   ==   0% by construction (a triangulation does not self-overlap)

STOREYS

The union is a single 2D operation, but a plain flatten would merge floors that
sit on top of each other in plan view (Pinarus's house is two storeys ~268 units
apart).  Storeys are kept apart PER VERTEX instead of per corridor: at each
output point the heights of the corridors covering it are clustered, a cluster
per walkable surface (gaps under STOREY_GAP_Z are one surface — a stair step, a
ramp — a bigger jump is a different floor).  A triangle is emitted once per
surface beneath it, its corners taking that surface's height.  There is no
"assign a corridor to a floor" step, which is what let a staircase — with no
single height — coexist with a 200u floor gap that no global threshold could
satisfy.

DOORS

corridor_doors.door_footprints runs first, on the raw ribbon union; each door's
flat footprint (the quad bridging its base line to the nearest corridor edge) is
handed back as an `extra_strips` polygon and joins the union as ordinary ground,
and its BASE LINE is passed as a `door_edges` constraint so the retriangulation
forces one large triangle with its long side on the door line — the vanilla
Skyrim door triangle.  The union resolves any overlap with the corridor by
construction — the door coverage is preserved exactly, nothing is deleted.

HEIGHT

Every output vertex gets its Z from a corridor that covers it, along that
corridor's own centreline, so each triangle sits on the pathgrid line's own
slope (principle 2) and a staircase keeps its rise.  Heights are never discarded
and reconstructed — each ribbon already knows its Z everywhere along itself.
"""

import math

import numpy as np

from . import params

# Heights within this of each other at one point are treated as the same
# walkable surface when a vertex is placed and when it looks up its level.  Kept
# small so a genuine step between stacked sheets is never fused, but large enough
# to absorb the little disagreement where two ribbons cross on a slope.
SAME_SURFACE_Z = 36.0

# Two levels at one point belong to DIFFERENT storeys only when they are at
# least this far apart.  Anything closer is one walkable surface — a stair step,
# a ramp, two ribbons meeting at a slight angle — and must produce ONE triangle;
# emitting both stacks them (measured: levels 39u apart on a Chorrol stair).
# Real storeys are separated by ~200u or more, so this sits well below them and
# well above any within-surface variation.
STOREY_GAP_Z = 120.0


def _ribbon_polygon(s):
    """The corridor's ribbon as a 2D polygon (a rectangle around its segment).

    A strip may instead carry an explicit 'poly' outline — the door triangles
    do — in which case that shape is used verbatim.
    """
    from shapely.geometry import Polygon

    if s.get('poly') is not None:
        return Polygon(s['poly'])

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


def _height_on(s, px, py):
    """Height of corridor s's surface at (px, py), following its own slope."""
    ax, ay, az = s['a']
    bx, by, bz = s['b']
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((px - ax) * dx +
                                                 (py - ay) * dy) / d2))
    return az + (bz - az) * t


def _distance_to(s, px, py):
    """Distance from (px, py) to the strip's centreline.

    For a strip with an explicit outline (a door triangle) the distance is 0
    inside that outline, so it only ever claims the ground it actually covers —
    a centreline measure would let it claim well outside its own shape.
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
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((px - a[0]) * dx +
                                                 (py - a[1]) * dy) / d2))
    return math.hypot(px - (a[0] + dx * t), py - (a[1] + dy * t))


def _triangulate(poly, target_edge, fixed_edges=None, steep_seeds=None):
    """Triangulate a shapely polygon into UNIFORM, well-shaped triangles.

    Returns (verts2d, tris).  The old approach earcut'd the polygon after
    cutting it on an 8u grid, which produced a mesh full of needles and tiny
    slivers along every boundary (20% of triangles had an edge ratio > 3, some
    > 400).  Vanilla Skyrim navmeshes are near-uniform ~target_edge triangles,
    so we reproduce that:

      1. Sample interior Steiner points on a hex lattice at `target_edge`
         spacing — a hex lattice, not a square grid, so the Delaunay of the
         points is near-equilateral (the Voronoi-dual pattern the author asked
         for) instead of right-isoceles.
      2. Densify the boundary rings at the same spacing so boundary triangles
         are the same scale as interior ones.
      3. Delaunay-triangulate the whole point set, then keep only triangles
         whose CENTROID lies inside the polygon — this honours the outline and
         every hole exactly (a ring of corridors around an obstacle keeps its
         hole) without a constrained triangulator.

    `fixed_edges` is a list of (p0, p1) 2D segments that MUST appear as a
    triangle edge — the door base lines.  Their endpoints are inserted as
    Steiner points and no interior sample is placed near the segment, so the
    Delaunay naturally forms a triangle with that long edge (Skyrim's door
    triangle).

    `steep_seeds` is a list of (x, y) points along STEEP ribbon centrelines
    (stairs, ramps).  A uniform target_edge triangle on a staircase climbs more
    than one storey gap across its corners and is dropped by the per-surface
    emission — the whole stair vanishes.  These seeds are forced in at a fine
    spacing so the stair keeps short, gently-climbing triangles that survive.
    """
    from shapely.geometry import Point
    from shapely.prepared import prep

    ext = list(poly.exterior.coords)[:-1]
    if len(ext) < 3:
        return [], []

    # A coarse spatial hash of accepted points, so a candidate can be rejected
    # when it crowds an existing one — this Poisson-disk guard is what keeps the
    # Delaunay well-shaped: a boundary sample landing a few units from a lattice
    # point (or two boundary rings nearly touching) is exactly what breeds the
    # sliver needles, so we simply never place the second point.
    bin_size = max(1.0, target_edge * 0.5)
    hash_bins = {}

    def _too_close(x, y, r2):
        gx, gy = int(x // bin_size), int(y // bin_size)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for (ex, ey) in hash_bins.get((gx + ddx, gy + ddy), ()):
                    if (ex - x) ** 2 + (ey - y) ** 2 < r2:
                        return True
        return False

    pts = []
    # Even a FORCED point (an outline corner, a door endpoint) is dropped when
    # it sits within this of an existing one: two union-outline corners landing
    # ~1u apart are the same corner and only breed a 1u-short needle.  Well
    # below the 40u ribbon width, so no real feature is welded away.
    weld2 = 3.0 ** 2

    def add(x, y, min_dist=0.0, force=False):
        x, y = float(x), float(y)
        if _too_close(x, y, weld2):
            return
        if not force and min_dist > 0.0 and _too_close(x, y, min_dist * min_dist):
            return
        hash_bins.setdefault((int(x // bin_size), int(y // bin_size)),
                             []).append((x, y))
        pts.append((x, y))

    # 1. boundary vertices + densified boundary samples.  Corners are FORCED
    #    (they define the outline); interpolated samples yield to spacing so a
    #    short edge does not seed a cluster of near-coincident points.
    for ring in [poly.exterior] + list(poly.interiors):
        coords = list(ring.coords)
        for i in range(len(coords) - 1):
            x0, y0 = coords[i]
            x1, y1 = coords[i + 1]
            add(x0, y0, force=True)
            seg = math.hypot(x1 - x0, y1 - y0)
            n = int(seg // target_edge)
            for s in range(1, n + 1):
                f = s / (n + 1)
                add(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f,
                    min_dist=target_edge * 0.5)

    # 2. door-base endpoints, forced (they must survive to make the door edge)
    fixed_edges = fixed_edges or []
    fixed_pts = []
    for (p0, p1) in fixed_edges:
        add(p0[0], p0[1], force=True)
        add(p1[0], p1[1], force=True)
        fixed_pts.append((p0, p1))

    # 3. ribbon centreline seeds.  Steep (stair) seeds are FORCED in at their
    #    fine spacing; flat centreline seeds YIELD to the Poisson spacing, so
    #    they only survive where a corridor is too narrow for the hex lattice
    #    (guaranteeing its bridge triangles stay inside) and are thinned out in
    #    open rooms that the lattice already fills.
    pp = prep(poly)
    for (sx, sy, steep) in (steep_seeds or ()):
        if not pp.contains(Point(sx, sy)):
            continue
        if steep:
            add(sx, sy, force=True)
        else:
            add(sx, sy, min_dist=target_edge * 0.6)

    # 4. interior hex lattice at target_edge spacing.  A lattice point yields to
    #    the Poisson-disk spacing (never crowd a boundary, door, or steep seed),
    #    and to the door keep-out (leave the door triangle clean and large).
    minx, miny, maxx, maxy = poly.bounds
    dy = target_edge * math.sqrt(3.0) / 2.0
    row = 0
    y = miny + dy * 0.5
    keepout2 = (target_edge * 0.75) ** 2
    while y < maxy:
        off = 0.0 if (row % 2 == 0) else target_edge * 0.5
        x = minx + off + target_edge * 0.25
        while x < maxx:
            if pp.contains(Point(x, y)):
                near_fixed = any(_seg_dist2(x, y, p0, p1) < keepout2
                                 for (p0, p1) in fixed_pts)
                if not near_fixed:
                    add(x, y, min_dist=target_edge * 0.6)
            x += target_edge
        y += dy
        row += 1

    if len(pts) < 3:
        return [], []

    from scipy.spatial import Delaunay
    from shapely.geometry import Polygon as _Poly
    arr = np.asarray(pts, dtype=np.float64)
    try:
        dt = Delaunay(arr)
    except Exception:
        return _earcut_fallback(poly)

    # Keep a triangle when the MAJORITY of its area lies inside the union.  A
    # plain centroid-in-poly test dropped fringe bridge triangles whose centroid
    # fell a hair outside a concave notch, which both lost that ground and
    # severed a corner of the surface into a point-touching speck (Chorrol's top
    # floor and basement each shed a 3-triangle scrap the main mesh does NOT
    # cover, so it could not simply be deleted).  The area test keeps a triangle
    # that is mostly walkable ground and only discards one that mostly pokes past
    # the boundary — preserving coverage and connectivity together.
    tris = []
    for (a, b, c) in dt.simplices:
        pa, pb, pc = arr[a], arr[b], arr[c]
        cx = (pa[0] + pb[0] + pc[0]) / 3.0
        cy = (pa[1] + pb[1] + pc[1]) / 3.0
        if pp.contains(Point(cx, cy)):
            tris.append((int(a), int(b), int(c)))
            continue
        # centroid outside — keep only if most of the triangle is inside
        tri = _Poly([(pa[0], pa[1]), (pb[0], pb[1]), (pc[0], pc[1])])
        ta = tri.area
        if ta < 1e-6:
            continue
        try:
            inside = tri.intersection(poly).area
        except Exception:
            inside = 0.0
        if inside >= 0.5 * ta:
            tris.append((int(a), int(b), int(c)))
    verts = [(float(p[0]), float(p[1])) for p in arr]
    if not tris:
        return _earcut_fallback(poly)
    return verts, tris


def _seg_dist2(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((px - a[0]) * dx +
                                                 (py - a[1]) * dy) / d2))
    ddx = px - (a[0] + dx * t)
    ddy = py - (a[1] + dy * t)
    return ddx * ddx + ddy * ddy


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


def _polygons_of(geom):
    from shapely.geometry import Polygon, MultiPolygon
    if isinstance(geom, Polygon):
        return [geom] if geom.area > 1e-6 else []
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if g.area > 1e-6]
    if hasattr(geom, 'geoms'):
        out = []
        for g in geom.geoms:
            out.extend(_polygons_of(g))
        return out
    return []


def _ribbon_seeds(strips, target_edge):
    """Interior seed points down every ribbon centreline (stairs get more).

    Two jobs:

      * CONNECTIVITY.  A corridor is only ~one ribbon wide (80u); at a 128u
        target edge it gets no interior hex-lattice row, so a bend in it is
        triangulated by long triangles whose centroids fall outside the bend and
        are culled — silently snapping the corridor into disconnected pieces
        (ChorrolFightersGuild fell into 10 components).  A row of centreline
        points down every ribbon guarantees a triangle chain that stays inside.

      * STAIRS.  A ribbon that climbs more than half a storey gap over a
        target_edge run is a stair: one uniform triangle on it would span more
        than STOREY_GAP_Z across its corners and be dropped by the per-surface
        emission, and the whole stair vanishes (Pinarus's two floors, 268u
        apart, on a single 2-node edge).  Steep ribbons are sampled MUCH finer,
        along the centreline and both rails, so the stair keeps short,
        full-width, gently-climbing triangles.

    On flat open ground the Poisson guard rejects most of these in favour of the
    coarse hex lattice, so rooms stay large-triangled.
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
        steep = rise / run * target_edge > STOREY_GAP_Z * 0.5
        if steep:
            # spacing so the climb per step is ~a third of the storey gap
            climb_step = STOREY_GAP_Z * 0.33
            step = max(RIBBON_SEED_STEP, climb_step * run / max(rise, 1e-6))
            offs = (-h * 0.6, 0.0, h * 0.6)
        else:
            step = target_edge * 0.9         # ~one triangle per along-corridor
            offs = (0.0,)                    # centreline only; Poisson thins it
        n = max(1, int(run / step))
        for k in range(n + 1):
            f = k / n
            cx, cy = ax + (bx - ax) * f, ay + (by - ay) * f
            for off in offs:
                seeds.append((cx + wx * off, cy + wy * off, steep))
    return seeds


# Along-ribbon spacing of steep-ribbon (stair) seeds.  RIBBON_STEP-scale so a
# stair keeps the fine cross-sections the old 8u grid gave it.
RIBBON_SEED_STEP = 24.0


def build_union_mesh(strips, extra_strips=None, door_edges=None):
    """Union the corridor ribbons per storey and retriangulate.

    Returns (verts, tris) with 3D vertices.  Coverage is the exact union of the
    ribbons and the triangles do not overlap — both by construction.

    extra_strips: door FOOTPRINT strips (from corridor_doors.door_footprints via
    _poly_strip) that join the union as ordinary ground — the flat connection
    quad from each door base to the nearest corridor edge.  Their COVERAGE is
    preserved exactly; the union resolves any overlap with the corridor.

    door_edges: [((x0,y0), (x1,y1)), ...] the door BASE lines.  Each is forced
    to appear as a triangle edge in the retriangulation, so every door gets one
    large triangle with its long side on the door line — the vanilla Skyrim door
    triangle — instead of whatever the generic mesh happens to lay there.
    """
    from shapely.geometry import Polygon, MultiPolygon
    from shapely.ops import unary_union

    if not strips:
        return [], []

    # Door footprints participate as ordinary geometry: they contribute their
    # polygon to the union AND their (flat) height to the level lookup, so a
    # vertex standing on door-only ground still knows how high it is.
    strips = list(strips) + list(extra_strips or ())

    verts = []
    tris = []

    # ONE 2D union of every ribbon, retriangulated once.  No storey buckets: a
    # staircase has no single height, so any attempt to assign corridors to
    # floors forces one Z threshold to be both loose enough for a stair's slope
    # and tight enough for a 200u floor gap — which no value satisfies.
    polys = [p for p in (_ribbon_polygon(s) for s in strips)
             if p.is_valid and not p.is_empty]
    if not polys:
        return [], []
    merged = unary_union(polys)
    if merged.is_empty:
        return [], []
    parts = (list(merged.geoms) if isinstance(merged, MultiPolygon)
             else [merged])

    # Steep-ribbon centreline seeds, computed once for all parts.  A ribbon is
    # "steep" when a target_edge-long triangle laid on it would climb more than
    # half a storey gap — a stair.  Such a triangle, spanning >STOREY_GAP_Z
    # across its corners, is split apart by the per-surface emission and the
    # whole stair vanishes (Pinarus's two floors, 268u apart, joined by a single
    # 2-node stair edge).  We seed the stair centreline finely so its triangles
    # stay short and climb little.
    steep_seeds = _ribbon_seeds(strips, params.TRI_TARGET_EDGE)

    # Each output vertex is emitted ONCE PER SURFACE that covers it: where two
    # storeys stack, the same (x, y) yields one vertex per storey, at each
    # storey's own height.  Surfaces are found by clustering the heights of the
    # corridors covering that point — heights within SAME_SURFACE_Z of each
    # other are one surface, a bigger jump is a different storey.  This is the
    # local, per-point version of the test; nothing is classified globally.
    door_edges = door_edges or []
    for part in parts:
        if not isinstance(part, Polygon) or part.area < 1.0:
            continue
        # unary_union of the ribbon rectangles leaves tiny notches where two
        # rectangle corners land ~1u apart; those breed 1u-short needles.  The
        # weld inside _triangulate collapses near-coincident boundary points, so
        # no explicit simplify (which risked pinching a thin connection into two
        # components) is needed.
        # door base lines whose midpoint falls in this part
        fixed = [e for e in door_edges
                 if _point_in_poly(0.5 * (e[0][0] + e[1][0]),
                                   0.5 * (e[0][1] + e[1][1]),
                                   list(part.exterior.coords)[:-1])]
        v2, t2 = _triangulate(part, params.TRI_TARGET_EDGE, fixed_edges=fixed,
                              steep_seeds=steep_seeds)
        if not t2:
            continue

        # per 2D vertex: the list of surface heights there
        levels = [_levels_at(strips, x, y) for (x, y) in v2]
        vid = [[] for _ in v2]              # per corner: list of (height, id)

        def vertex_at(k, z):
            """Vertex id for 2D point k on the surface at height z.

            A corner that carries no level for this surface still gets a vertex,
            at the surface's own height.  NEVER return None: skipping the
            triangle instead would DROP ground that the union says is covered,
            which is exactly the defect this whole module exists to prevent.

            Two triangles that meet on ONE surface must resolve their shared
            corner to the SAME vertex, or they share no edge and the surface
            splinters into disconnected fragments (ChorrolFightersGuild's top
            floor broke into a 108-triangle piece plus specks where neighbours
            landed at 132.0 vs 132.7).  So a request within SAME_SURFACE_Z of a
            vertex already emitted at this corner REUSES it, rather than keying
            on an exact height.
            """
            lv = levels[k]
            zz = z
            if lv:
                near = min(lv, key=lambda t: abs(t - z))
                if abs(near - z) <= SAME_SURFACE_Z:
                    zz = near
            for (hz, gid) in vid[k]:
                if abs(hz - zz) <= SAME_SURFACE_Z:
                    return gid
            gid = len(verts)
            verts.append([float(v2[k][0]), float(v2[k][1]), zz])
            vid[k].append((zz, gid))
            return gid

        # A triangle is emitted on a surface when all three corners lie on THAT
        # surface — judged against the surface itself, not against each other.
        #
        # The surface a triangle belongs to is the corridor covering its centre;
        # a corner is on it when its level matches that corridor's own height
        # there.  On a staircase every corner then matches the sloping stair
        # surface however much the triangle climbs, while a corner belonging to
        # the floor underneath is hundreds of units away and never matches.
        #
        # Comparing the three corners to EACH OTHER instead (max minus min
        # inside one band) drops a triangle whenever the stair's rise across it
        # exceeds the tolerance — that punched a hole through the middle of the
        # Guild's stairway.
        # Emit the triangle once per SURFACE it exists on.  The surfaces are
        # enumerated from all three corners' levels (not just corner a's): a
        # storey that corner a happens not to carry — because the ribbon there
        # belongs only to the other floor — would otherwise never be emitted,
        # which left 490 of 600 sampled gaps at points where both storeys were
        # correctly detected.
        # EVERY triangle of the union is emitted on EVERY surface beneath it.
        # Nothing is ever skipped: the union is the ground the corridors cover,
        # so dropping one of its triangles is lost coverage by definition.  A
        # corner missing a level for a surface is given one (see vertex_at),
        # rather than the triangle being discarded.
        for (a, b, c) in t2:
            zs = sorted(set(levels[a]) | set(levels[b]) | set(levels[c]))
            if not zs:
                continue                    # no corridor covers this at all
            # Cluster with the STOREY gap, not the same-surface tolerance: two
            # levels a stair-step apart (measured 39u on a Chorrol stair) are
            # the same walkable surface and must yield ONE triangle, or the two
            # land on top of each other.  Genuinely stacked storeys are hundreds
            # of units apart and still separate cleanly.
            surfaces = [[zs[0]]]
            for z in zs[1:]:
                if z - surfaces[-1][-1] <= STOREY_GAP_Z:
                    surfaces[-1].append(z)
                else:
                    surfaces.append([z])
            for grp in surfaces:
                z = sum(grp) / len(grp)
                tris.append((vertex_at(a, z), vertex_at(b, z),
                             vertex_at(c, z)))

    return verts, tris


def _levels_at(strips, px, py):
    """Distinct surface heights at (px, py): one per storey covering it.

    The heights of every corridor whose ribbon covers the point are clustered;
    a gap larger than SAME_SURFACE_Z starts a new surface.  A stair ribbon and
    the floor it meets fall in one cluster (they differ by a few units there),
    while the floor it flies over is hundreds away and forms its own.
    """
    zs = []
    for s in strips:
        if _distance_to(s, px, py) <= s['half'] + 1e-6:
            zs.append(_height_on(s, px, py))
    if not zs:
        return []
    zs.sort()
    out = [[zs[0]]]
    for z in zs[1:]:
        if z - out[-1][-1] <= SAME_SURFACE_Z:
            out[-1].append(z)
        else:
            out.append([z])
    return [sum(g) / len(g) for g in out]


def _closest_level(levels, z):
    """Index of the level nearest z, or None when none is within tolerance."""
    best = None
    for i, lz in enumerate(levels):
        d = abs(lz - z)
        if d <= SAME_SURFACE_Z and (best is None or d < best[0]):
            best = (d, i)
    return best[1] if best else None
