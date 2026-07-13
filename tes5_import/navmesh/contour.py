"""Walkable voxel regions -> contours -> simplified polygons -> triangles.

The back half of the Recast pipeline.  The voxel grid gives a correct but blocky
walkable set; this turns it into a small number of large, clean triangles:

  1. trace the boundary of the walkable column set (marching-squares style),
  2. simplify each contour (Douglas-Peucker) so a 16u staircase along a wall
     becomes a straight edge that follows the actual wall — this is what makes
     the output look like the hand-painted reference instead of a voxel blob,
  3. triangulate the simplified polygon (ear clipping, holes handled by bridging),
  4. lift each 2D vertex back to 3D using the span heights underneath.

Triangle count matters: Skyrim's NVNM indexes triangles with S16, and vanilla
interiors run a few hundred triangles.  Simplification is what keeps us there.
"""

import math

from . import params


# ---------------------------------------------------------------------------
# Contour tracing
# ---------------------------------------------------------------------------

def _walkable_mask(hf):
    """{(cx,cy): top_z} for columns with at least one walkable span.

    A protected (pathgrid) span wins the column; otherwise the highest top.
    """
    out = {}
    for cy in range(hf.h):
        for cx in range(hf.w):
            col = hf.spans[cy * hf.w + cx]
            pro = [s[1] for s in col if s[2] and s[3]]
            if pro:
                out[(cx, cy)] = max(pro)
                continue
            tops = [s[1] for s in col if s[2]]
            if tops:
                out[(cx, cy)] = max(tops)
    return out


def _region_masks(hf, region_of):
    """{(cx,cy): top_z} masks — one per Z-COHERENT LAYER, not one per region.

    Tracing one GLOBAL mask is wrong: disconnected rooms merge into a single
    polygon set and the ear-clipper then fans enormous slivers straight across
    the walls between them.  But one mask per REGION is wrong too, and much more
    damagingly so.

    A mask is a HEIGHT MAP: at most one Z per (cx,cy).  A region, though, is a
    connected walkable SURFACE, and a staircase legitimately connects a house's
    ground floor to its upper floor — so a single region can cover the same XY
    column twice, once per storey.  Forcing that region through one height map
    makes each shared column pick one storey or the other, and the triangulator
    then stitches the two heights together into giant vertical zig-zags spanning
    the whole house (observed: BrumaJGhastasHouse, a region spanning z -236..+19).

    So we peel each region into LAYERS: a layer claims at most one span per
    column, grown by adjacency under the step-height gate, so every layer really
    is a height map.  The staircase and the floor it rises from stay in the same
    layer (each tread is within a step of the next); the storey above is a layer
    of its own.  Each layer is then contoured on its own, which is exactly what
    the 2.5D contour tracer requires.
    """
    climb = params.MAX_CLIMB

    # Walkable spans of each region, indexed by column.
    by_region = {}
    for cy in range(hf.h):
        for cx in range(hf.w):
            col = hf.spans[cy * hf.w + cx]
            for si, s in enumerate(col):
                if not s[2]:
                    continue
                rid = region_of.get((cx, cy, si))
                if rid is None:
                    continue
                by_region.setdefault(rid, {}).setdefault((cx, cy), []).append(s)

    masks = []
    for cols in by_region.values():
        # Remaining (unclaimed) spans per column.
        left = {k: list(v) for k, v in cols.items()}
        while left:
            # Seed the layer at the lowest unclaimed span, so a layer grows along
            # a floor rather than starting halfway up a stair and splitting it.
            seed_key = min(left, key=lambda k: min(s[1] for s in left[k]))
            seed = min(left[seed_key], key=lambda s: s[1])

            mask = {}
            stack = [(seed_key, seed)]
            mask[seed_key] = seed[1]
            left[seed_key].remove(seed)
            if not left[seed_key]:
                del left[seed_key]

            while stack:
                (cx, cy), s = stack.pop()
                for nk in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if nk in mask or nk not in left:
                        continue
                    # The neighbour span closest in Z that is still a step away.
                    cand = None
                    for ns in left[nk]:
                        if abs(ns[1] - s[1]) > climb:
                            continue
                        if cand is None or abs(ns[1] - s[1]) < abs(cand[1] - s[1]):
                            cand = ns
                    if cand is None:
                        continue
                    mask[nk] = cand[1]
                    left[nk].remove(cand)
                    if not left[nk]:
                        del left[nk]
                    stack.append((nk, cand))

            if len(mask) >= params.MIN_REGION_VOXELS:
                masks.append(mask)

    return masks


def trace_contours(mask):
    """Trace CCW boundary loops around a set of grid cells.

    Walks the boundary EDGES between a walkable cell and a non-walkable one, so
    the contour lands on cell corners (integer lattice) and is watertight.
    """
    # Boundary edges, directed so the walkable cell is on the left.
    # Cell (x,y) occupies the square [x,x+1] x [y,y+1] in lattice space.
    edges = {}
    for (x, y) in mask:
        if (x, y - 1) not in mask:                    # south edge: +x
            edges.setdefault((x, y), []).append((x + 1, y))
        if (x + 1, y) not in mask:                    # east edge:  +y
            edges.setdefault((x + 1, y), []).append((x + 1, y + 1))
        if (x, y + 1) not in mask:                    # north edge: -x
            edges.setdefault((x + 1, y + 1), []).append((x, y + 1))
        if (x - 1, y) not in mask:                    # west edge:  -y
            edges.setdefault((x, y + 1), []).append((x, y))

    loops = []
    while edges:
        start = next(iter(edges))
        loop = [start]
        cur = start
        while True:
            outs = edges.get(cur)
            if not outs:
                break
            nxt = outs.pop()
            if not outs:
                del edges[cur]
            if nxt == start:
                break
            loop.append(nxt)
            cur = nxt
            if len(loop) > 200000:                    # runaway guard
                break
        if len(loop) >= 4:
            loops.append(loop)
    return loops


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------

def _perp_dist(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    if d2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / d2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _dp(points, eps):
    """Douglas-Peucker on an open polyline."""
    if len(points) < 3:
        return list(points)
    stack = [(0, len(points) - 1)]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        worst, wi = -1.0, -1
        for k in range(i + 1, j):
            d = _perp_dist(points[k], points[i], points[j])
            if d > worst:
                worst, wi = d, k
        if worst > eps:
            keep[wi] = True
            stack.append((i, wi))
            stack.append((wi, j))
    return [p for p, k in zip(points, keep) if k]


def _deburr(loop, passes=2):
    """Remove single-cell teeth from a raw voxel contour before simplification.

    A contour traced on the voxel lattice has axis-aligned 1-cell spikes (a
    column that pokes out or in by one cell), which Douglas-Peucker preserves as
    saw teeth if they exceed its tolerance.  Chaikin-style corner cutting rounds
    those: replace each vertex by points 1/4 and 3/4 toward its neighbours, then
    collinear-merge.  Two passes turn a 90-deg jag into an octagon-like bevel
    without pulling the boundary off the walls (cuts stay inside the ring).
    """
    if len(loop) < 4:
        return loop
    for _ in range(passes):
        n = len(loop)
        out = []
        for i in range(n):
            ax, ay = loop[i]
            bx, by = loop[(i + 1) % n]
            out.append((ax * 0.75 + bx * 0.25, ay * 0.75 + by * 0.25))
            out.append((ax * 0.25 + bx * 0.75, ay * 0.25 + by * 0.75))
        loop = out
    return loop


def simplify_loop(loop, eps):
    """Douglas-Peucker on a CLOSED loop.

    Anchor on the two extreme points first: a closed ring has no natural
    endpoints, and simplifying from an arbitrary start can shave a real corner.
    """
    n = len(loop)
    if n < 4:
        return list(loop)
    # Farthest-apart pair (approximated from the bbox extremes) as anchors.
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    i0 = min(range(n), key=lambda i: (xs[i], ys[i]))
    i1 = max(range(n), key=lambda i: (xs[i], ys[i]))
    if i0 > i1:
        i0, i1 = i1, i0
    a = _dp(loop[i0:i1 + 1], eps)
    b = _dp(loop[i1:] + loop[:i0 + 1], eps)
    out = a[:-1] + b[:-1]
    return out if len(out) >= 3 else list(loop)


def _ring_contains_pt(ring, pt):
    """Even-odd point-in-polygon: is pt strictly inside ring?

    Used to decide which outer contour a hole belongs to.  A bounding-box test is
    NOT containment — two walkable strips either side of an obstacle have
    overlapping boxes — and mis-assigning a hole makes the triangulator bridge
    across the obstacle to reach it.
    """
    x, y = pt[0], pt[1]
    inside = False
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xint = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < xint:
                inside = not inside
    return inside


def _signed_area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


# ---------------------------------------------------------------------------
# Triangulation (ear clipping, with hole bridging)
# ---------------------------------------------------------------------------

def _point_in_tri(p, a, b, c):
    d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
    d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1])
    d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1])
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def _bridge_holes(outer, holes):
    """Merge holes into the outer ring by cutting a bridge to each.

    Simple and adequate here: for each hole take its rightmost vertex and join it
    to a visible outer vertex, splicing the hole (reversed) into the ring.
    """
    ring = list(outer)
    for hole in sorted(holes, key=lambda h: -max(p[0] for p in h)):
        if len(hole) < 3:
            continue
        hi = max(range(len(hole)), key=lambda i: hole[i][0])
        hx, hy = hole[hi]
        # Nearest outer vertex to the right of the hole's rightmost point.
        best, bi = None, None
        for i, (ox, oy) in enumerate(ring):
            if ox < hx:
                continue
            d = (ox - hx) ** 2 + (oy - hy) ** 2
            if best is None or d < best:
                best, bi = d, i
        if bi is None:
            bi = min(range(len(ring)),
                     key=lambda i: (ring[i][0] - hx) ** 2 + (ring[i][1] - hy) ** 2)
        rot = hole[hi:] + hole[:hi]
        ring = (ring[:bi + 1] + rot + [rot[0]] + ring[bi:])
    return ring


def triangulate(outer, holes=None):
    """Triangulate a simple polygon (CCW) with optional holes -> (verts, tris).

    Uses mapbox_earcut, which handles holes natively and is robust on the large
    concave room polygons we produce.  The hand-rolled ear clipper below is kept
    only as a fallback: it bails out when it can't find a valid ear (which a
    bridged hole can easily cause), and that silently dropped ~40% of the floor
    area on the Anvil FG main room.
    """
    if len(outer) < 3:
        return [], []
    if _signed_area(outer) < 0:
        outer = outer[::-1]
    hs = []
    for h in (holes or ()):
        if len(h) >= 3:
            hs.append(h[::-1] if _signed_area(h) > 0 else h)

    try:
        import numpy as np
        import mapbox_earcut as earcut

        verts = list(outer)
        rings = [len(verts)]
        for h in hs:
            verts.extend(h)
            rings.append(len(verts))
        arr = np.asarray(verts, dtype=np.float64).reshape(-1, 2)
        idx = earcut.triangulate_float64(arr, np.asarray(rings, dtype=np.uint32))
        tris = []
        for i in range(0, len(idx) - 2, 3):
            a, b, c = int(idx[i]), int(idx[i + 1]), int(idx[i + 2])
            if a == b or b == c or a == c:
                continue
            tris.append((a, b, c))
        if tris:
            return verts, tris
    except Exception:
        pass

    ring = _bridge_holes(outer, hs) if hs else list(outer)

    verts = list(ring)
    idx = list(range(len(verts)))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < 20000:
        guard += 1
        # Clip the BEST ear (largest smallest-angle), not the first valid one.
        # First-found ear clipping walks around the ring shaving one sliver after
        # another, producing exactly the long thin triangles that spanned whole
        # rooms in the first render.  Picking the fattest ear each time yields
        # well-shaped triangles and far fewer of them.
        best_k, best_score = None, -1.0
        n = len(idx)
        for k in range(n):
            i0 = idx[(k - 1) % n]
            i1 = idx[k]
            i2 = idx[(k + 1) % n]
            a, b, c = verts[i0], verts[i1], verts[i2]
            cross = ((b[0] - a[0]) * (c[1] - a[1]) -
                     (c[0] - a[0]) * (b[1] - a[1]))
            if cross <= 1e-9:
                continue                      # reflex or degenerate
            bad = False
            for m in idx:
                if m in (i0, i1, i2):
                    continue
                if _point_in_tri(verts[m], a, b, c):
                    bad = True
                    break
            if bad:
                continue
            # Score = 2*area / (longest edge)^2  — a fat triangle scores high,
            # a sliver scores ~0.
            e0 = (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
            e1 = (b[0] - c[0]) ** 2 + (b[1] - c[1]) ** 2
            e2 = (c[0] - a[0]) ** 2 + (c[1] - a[1]) ** 2
            longest = max(e0, e1, e2)
            score = cross / longest if longest > 1e-12 else 0.0
            if score > best_score:
                best_score, best_k = score, k
        if best_k is None:
            break
        k = best_k
        n = len(idx)
        tris.append((idx[(k - 1) % n], idx[k], idx[(k + 1) % n]))
        idx.pop(k)
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return verts, tris


# ---------------------------------------------------------------------------
# Public: region set -> 3D mesh
# ---------------------------------------------------------------------------

def _refine_slivers(verts, tris):
    """Flip shared edges to remove sliver triangles (Delaunay-style refinement).

    earcut is robust but fans triangles from a few vertices, so ~45% of its
    output on a big room polygon is slivers (aspect < 0.15).  Flipping the
    shared edge of two adjacent triangles when it improves the worst aspect
    ratio converts those fans into well-shaped triangles, without moving any
    vertex or changing the covered area.
    """
    def aspect(a, b, c):
        ar = abs((b[0] - a[0]) * (c[1] - a[1]) -
                 (c[0] - a[0]) * (b[1] - a[1]))
        e = max((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2,
                (b[0] - c[0]) ** 2 + (b[1] - c[1]) ** 2,
                (c[0] - a[0]) ** 2 + (c[1] - a[1]) ** 2)
        return (ar / e) if e > 1e-12 else 0.0

    tris = [list(t) for t in tris]
    for _sweep in range(4):
        # Map each undirected edge -> the (at most two) triangles using it.
        edge = {}
        for ti, (a, b, c) in enumerate(tris):
            for (u, v) in ((a, b), (b, c), (c, a)):
                edge.setdefault((min(u, v), max(u, v)), []).append(ti)

        flipped = 0
        done = set()
        for (u, v), ts in edge.items():
            if len(ts) != 2:
                continue
            t0, t1 = ts
            if t0 in done or t1 in done:
                continue
            # Opposite corners.
            o0 = next((x for x in tris[t0] if x not in (u, v)), None)
            o1 = next((x for x in tris[t1] if x not in (u, v)), None)
            if o0 is None or o1 is None or o0 == o1:
                continue
            pu, pv, p0, p1 = verts[u], verts[v], verts[o0], verts[o1]

            cur = min(aspect(pu, pv, p0), aspect(pu, pv, p1))
            new = min(aspect(p0, p1, pu), aspect(p0, p1, pv))
            if new <= cur * 1.05:
                continue
            # The flipped pair must stay non-degenerate and keep the same
            # orientation (convex quad), or we would fold the mesh over itself.
            def ccw(a, b, c):
                return ((b[0] - a[0]) * (c[1] - a[1]) -
                        (c[0] - a[0]) * (b[1] - a[1]))
            if ccw(p0, p1, pu) <= 0 or ccw(p1, p0, pv) <= 0:
                continue

            tris[t0] = [o0, o1, u]
            tris[t1] = [o1, o0, v]
            done.add(t0)
            done.add(t1)
            flipped += 1
        if not flipped:
            break

    out = []
    for (a, b, c) in tris:
        pa, pb, pc = verts[a], verts[b], verts[c]
        if abs((pb[0] - pa[0]) * (pc[1] - pa[1]) -
               (pc[0] - pa[0]) * (pb[1] - pa[1])) < 1e-6:
            continue
        out.append((a, b, c))
    return out


def _point_in_polygon(px, py, ring):
    """Even-odd point-in-polygon test."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > py) != (yj > py)) and \
           (px < (xj - xi) * (py - yi) / (yj - yi + 1e-30) + xi):
            inside = not inside
        j = i
    return inside


def triangulate_uniform(outer, holes, step):
    """Triangulate with interior Steiner points for UNIFORM triangle sizing.

    earcut is correct but fans triangles from a few vertices, giving long thin
    slivers.  A navmesh wants triangles of similar size, so we seed a regular
    grid of interior points (spacing `step`) plus densified boundary points,
    Delaunay the lot, and keep the triangles whose centroid is inside the
    polygon (and outside every hole).  This is the classic Steiner-point mesh and
    yields evenly-sized triangles that still respect walls and holes.
    """
    try:
        import numpy as np
        from scipy.spatial import Delaunay
    except ImportError:
        return triangulate(outer, holes)

    holes = holes or []
    pts = []

    def densify(ring):
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            pts.append((ax, ay))
            seg = math.hypot(bx - ax, by - ay)
            k = int(seg / step)
            for s in range(1, k):
                t = s / k
                pts.append((ax + (bx - ax) * t, ay + (by - ay) * t))

    densify(outer)
    for h in holes:
        densify(h)

    # Vectorized even-odd ray cast over a whole batch of candidate points at
    # once — the pure-python per-point _point_in_polygon was the exterior
    # hotspot (0.3s+ on a big cell).
    def _ring_contains(cand, ring):
        rx = np.asarray([p[0] for p in ring], dtype=np.float64)
        ry = np.asarray([p[1] for p in ring], dtype=np.float64)
        rxn = np.roll(rx, -1)
        ryn = np.roll(ry, -1)
        px = cand[:, 0][:, None]
        py = cand[:, 1][:, None]
        cond = ((ry[None, :] > py) != (ryn[None, :] > py))
        xint = (rxn - rx)[None, :] * (py - ry[None, :]) / \
               (ryn - ry + 1e-30)[None, :] + rx[None, :]
        return (np.logical_and(cond, px < xint).sum(axis=1) % 2).astype(bool)

    def inside_mask(cand):
        m = _ring_contains(cand, outer)
        for h in holes:
            m &= ~_ring_contains(cand, h)
        return m

    ox0 = min(p[0] for p in outer)
    ox1 = max(p[0] for p in outer)
    oy0 = min(p[1] for p in outer)
    oy1 = max(p[1] for p in outer)
    # Interior grid, alternate rows offset so points don't fall on boundary.
    gx = np.arange(ox0 + step * 0.5, ox1, step)
    gy = np.arange(oy0 + step * 0.5, oy1, step)
    if len(gx) and len(gy):
        GX, GY = np.meshgrid(gx, gy)
        GX = GX.copy()
        GX[1::2] += step * 0.5
        cand = np.column_stack([GX.ravel(), GY.ravel()])
        keep = inside_mask(cand)
        pts.extend(map(tuple, cand[keep]))

    if len(pts) < 3:
        return triangulate(outer, holes)

    arr = np.asarray(pts, dtype=np.float64)
    try:
        dt = Delaunay(arr)
    except Exception:
        return triangulate(outer, holes)

    simp = dt.simplices
    cent = arr[simp].mean(axis=1)
    keep = inside_mask(cent)

    verts = [tuple(p) for p in pts]
    tris = []
    for si in np.nonzero(keep)[0]:
        a, b, c = (int(x) for x in simp[si])
        if ((arr[b][0] - arr[a][0]) * (arr[c][1] - arr[a][1]) -
                (arr[c][0] - arr[a][0]) * (arr[b][1] - arr[a][1])) < 0:
            b, c = c, b
        tris.append((a, b, c))
    return verts, tris


def _mesh_mask(hf, mask, all_verts, all_tris):
    """Contour + triangulate ONE region's mask, appending into the mesh lists."""
    loops = trace_contours(mask)
    if not loops:
        return

    eps = params.MAX_SIMPLIFY_ERR / hf.cs        # lattice units
    min_area = params.MIN_REGION_VOXELS          # in lattice cells

    outers, holes = [], []
    for loop in loops:
        # Deburr single-cell voxel teeth, then Douglas-Peucker.  Deburring first
        # rounds 90-deg lattice jags so the simplified ring reads as an octagon
        # around obstacles instead of a saw blade.
        s = simplify_loop(_deburr(loop), eps)
        if len(s) < 3:
            continue
        area = _signed_area(s)
        if abs(area) < min_area:
            continue
        (outers if area > 0 else holes).append(s)
    if not outers:
        return

    def corner_z(lx, ly):
        best = None
        for (dx, dy) in ((0, 0), (-1, 0), (0, -1), (-1, -1)):
            z = mask.get((lx + dx, ly + dy))
            if z is not None and (best is None or z > best):
                best = z
        return best

    for outer in outers:
        # Assign each hole to the outer ring that ACTUALLY contains it.  A
        # bounding-box test (what this used to do) is not containment: two
        # walkable strips either side of a bed give two separate outer rings whose
        # boxes overlap, so the bed's hole got attached to a ring it does not lie
        # inside, and the triangulator then bridged to it — drawing a triangle
        # straight across the bed to join two parallel spans.
        mine = [h for h in holes if _ring_contains_pt(outer, h[0])]

        # Uniform Steiner-point triangulation for even triangle sizing (the
        # navmesh should not contain long thin triangles); step is in lattice
        # units.  Exteriors (coarser voxel grid) use a much larger target edge:
        # open terrain has no fine detail and a dense grid there explodes both
        # the triangle count and the build time for no benefit.  Tiny polygons
        # fall back to a plain fan.
        target = (params.TRI_TARGET_EDGE_EXTERIOR if hf.cs > params.CS
                  else params.TRI_TARGET_EDGE)
        step = max(2.0, target / hf.cs)
        verts2d, tris = triangulate_uniform(outer, mine, step)
        if not tris:
            verts2d, tris = triangulate(outer, mine)
        if not tris:
            continue
        tris = _refine_slivers(verts2d, tris)
        if not tris:
            continue

        base = len(all_verts)
        for (lx, ly) in verts2d:
            z = corner_z(int(round(lx)), int(round(ly)))
            if z is None:
                # Simplification can pull a corner just off the mask; snap to the
                # nearest known column rather than emitting a bogus height.
                bd = None
                z = 0.0
                for (mx, my), mz in mask.items():
                    d = (mx - lx) ** 2 + (my - ly) ** 2
                    if bd is None or d < bd:
                        bd, z = d, mz
            all_verts.append((hf.min_x + lx * hf.cs,
                              hf.min_y + ly * hf.cs, z))
        for (i, j, k) in tris:
            all_tris.append((base + i, base + j, base + k))


def build_mesh(hf, region_of=None):
    """Walkable columns -> (verts3d, tris).

    Contours are traced per Z-COHERENT LAYER when region_of is supplied (see
    _region_masks).  Doing it on one global mask merges disconnected rooms into a
    single polygon set and the triangulator fans huge slivers across the walls
    between them; doing it per region is just as broken once a staircase makes one
    region cover two storeys of the same columns.

    Vertex Z comes from the walkable span tops beneath each lattice corner, so
    the mesh sits on the floor it was derived from and stairs stay sloped.
    """
    all_verts, all_tris = [], []
    if region_of:
        for mask in _region_masks(hf, region_of):
            _mesh_mask(hf, mask, all_verts, all_tris)
    else:
        mask = _walkable_mask(hf)
        if mask:
            _mesh_mask(hf, mask, all_verts, all_tris)
    return all_verts, all_tris
