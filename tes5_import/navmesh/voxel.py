"""Solid heightfield voxelization + Recast-style span filters.

This is where "is that thing an obstacle?" is actually decided — and the key
point is that it is decided in WORLD SPACE, per voxel column, never per mesh.

Why it cannot be decided per-mesh
---------------------------------
Oblivion collision meshes are ORIGIN-CENTERED, so a ref's PosZ sits at the
object's MIDDLE, not its base:

    lowerclasstable01.nif   collision z [-28.7 .. +28.7]   (a ~57u tall table)
    lowerclassbench01.nif   total collision height 30u     (BELOW the 34u step)

A "short mesh => walk over it" rule would therefore call a dining table
steppable.  Local extents say nothing about how far a thing rises above the
floor it stands on.  Only here, where the floor's Z under each column is known,
is the question answerable.

The governing rule, applied uniformly:

    an object obstructs  <=>  it rises more than MAX_CLIMB above the walkable
                              floor beneath it

Everything falls out of that one rule and Recast's standard span filters:

  * a RUG / PILLOW / low sack is a walkable span a few units above the floor.
    filter_low_hanging_obstacles merges it into the floor: the rug's top simply
    BECOMES the walkable surface.  NPCs walk over it.  (Most Oblivion rugs turn
    out to have no collision at all, so they are free anyway — but the ones that
    do are handled correctly rather than carved around.)
  * a TABLE / BARREL / CRATE rises far more than MAX_CLIMB.  Its sides are
    ledges (filter_ledge_spans), so the floor navmesh stops at them and paths
    route around.
  * a TABLE TOP is itself a walkable span, but it forms a region with no
    pathgrid node, so build.py's seeding drops it.  NPCs never path over tables.
  * STAIRS are a staircase of spans, each within MAX_CLIMB of the next, so they
    connect into a proper ramp.
  * a BRIDGE deck is a walkable span ABOVE the river's terrain span; both exist
    in the same column, and the deck is walkable at deck height.

No size gates, no rug lists, no MIN_EXCLUSION_HEIGHT tuning.
"""

import numpy as np

from . import params


class Heightfield:
    """A column grid of (zmin, zmax, walkable) spans.

    Spans are stored column-major in flat python lists — a column typically
    holds 1-4 spans, so an array-of-structs would waste more time than it saves.
    """

    __slots__ = ('w', 'h', 'cs', 'ch', 'min_x', 'min_y', 'min_z', 'spans')

    def __init__(self, min_x, min_y, min_z, w, h, cs, ch):
        self.w = w
        self.h = h
        self.cs = cs
        self.ch = ch
        self.min_x = min_x
        self.min_y = min_y
        self.min_z = min_z
        self.spans = [[] for _ in range(w * h)]

    def add_span(self, cx, cy, zmin, zmax, walkable):
        """Insert a span, merging only spans that genuinely OVERLAP in Z.

        Two spans that merely TOUCH at a boundary must NOT merge: a wall stands
        ON a floor, so the wall span's bottom equals the floor span's top.  A
        touch-merge fused them into one 400u-tall BLOCKING span that erased the
        floor beneath it (observed: a column reading -260..235 BLOCKING under a
        pathgrid node standing on a floor at -254).  So a wall on a floor stays
        two spans — floor (walkable) and wall (blocking) — and the floor survives.

        When spans do overlap, Recast's rule: same-flag spans union; a walkable
        and a blocking span that overlap resolve to whichever TOP surface is
        higher (a floor laid over a beam is still floor; a solid lip over a floor
        blocks it).  A strictly-below overlap keeps both when only touching.
        """
        if cx < 0 or cy < 0 or cx >= self.w or cy >= self.h:
            return
        col = self.spans[cy * self.w + cx]
        lo, hi, wk = zmin, zmax, walkable

        keep = []
        for s in col:
            # Touching or disjoint in Z (s entirely at/below lo, or at/above hi)
            # -> an independent surface; keep it separate.  Using strict '<=' on
            # the boundary is what stops a wall from swallowing the floor it
            # stands on.
            if s[1] <= lo or s[0] >= hi:
                keep.append(s)
                continue
            # Genuine Z overlap.
            if s[2] == wk:
                lo = min(lo, s[0])
                hi = max(hi, s[1])
            else:
                # Mixed flags: the higher top surface wins the flag; union extent.
                if s[1] > hi:
                    wk = s[2]
                lo = min(lo, s[0])
                hi = max(hi, s[1])

        keep.append([lo, hi, wk])
        keep.sort(key=lambda s: s[0])
        self.spans[cy * self.w + cx] = keep


def _tri_overlaps_square(ax, ay, bx, by, cx_, cy_, px, py, half):
    """2D separating-axis test: does triangle abc overlap the column square?

    Conservative rasterization.  Without it, any floor triangle smaller than a
    cell — or one that simply misses every column centre — vanishes, leaving
    holes in the floor exactly where a pathgrid node stands.

    This is the hottest function in the whole build (a cave with 27k collision
    triangles calls it 255k times), so it takes unpacked scalars rather than
    tuples and does the cheap bbox rejection before the three edge-axis tests.
    """
    lo = px - half
    hi = px + half
    if ax < lo and bx < lo and cx_ < lo:
        return False
    if ax > hi and bx > hi and cx_ > hi:
        return False
    lo = py - half
    hi = py + half
    if ay < lo and by < lo and cy_ < lo:
        return False
    if ay > hi and by > hi and cy_ > hi:
        return False

    # Edge-normal axes.  For each, project the triangle and the square and look
    # for a gap.  The square's projection is centre +/- (|nx|+|ny|)*half.
    for (ux, uy, vx, vy, wx_, wy_) in ((ax, ay, bx, by, cx_, cy_),
                                       (bx, by, cx_, cy_, ax, ay),
                                       (cx_, cy_, ax, ay, bx, by)):
        nx = -(vy - uy)
        ny = (vx - ux)
        # Triangle: only the third vertex can lie off the edge.
        d0 = nx * (px - ux) + ny * (py - uy)          # square centre
        dw = nx * (wx_ - ux) + ny * (wy_ - uy)        # opposite corner
        r = half * (abs(nx) + abs(ny))
        if dw >= 0.0:
            if d0 + r < 0.0:
                return False
        else:
            if d0 - r > 0.0:
                return False
    return True


def _rasterize(hf, tris, walkable):
    """Rasterize (N,3,3) world triangles into heightfield columns."""
    if tris is None or len(tris) == 0:
        return
    cs, ch = hf.cs, hf.ch
    inv_cs = 1.0 / cs

    # Per-triangle XY bbox -> column range.
    tx = tris[:, :, 0]
    ty = tris[:, :, 1]
    x0 = np.floor((tx.min(axis=1) - hf.min_x) * inv_cs).astype(np.int32)
    x1 = np.floor((tx.max(axis=1) - hf.min_x) * inv_cs).astype(np.int32)
    y0 = np.floor((ty.min(axis=1) - hf.min_y) * inv_cs).astype(np.int32)
    y1 = np.floor((ty.max(axis=1) - hf.min_y) * inv_cs).astype(np.int32)
    np.clip(x0, 0, hf.w - 1, out=x0)
    np.clip(x1, 0, hf.w - 1, out=x1)
    np.clip(y0, 0, hf.h - 1, out=y0)
    np.clip(y1, 0, hf.h - 1, out=y1)

    zmins = tris[:, :, 2].min(axis=1)
    zmaxs = tris[:, :, 2].max(axis=1)

    half = 0.5 * cs

    tl = tris.tolist()          # python floats: ~2x faster than numpy scalars here

    for i in range(len(tl)):
        a, b, c = tl[i]
        ax, ay, az = a
        bx, by, bz = b
        cx3, cy3, cz3 = c
        ux, uy = bx - ax, by - ay
        vx, vy = cx3 - ax, cy3 - ay
        det = ux * vy - uy * vx
        # A triangle with no XY area is a wall seen edge-on.  It must still
        # block, so stamp its full Z extent across the columns it crosses.
        flat = abs(det) < 1e-9
        inv_det = 0.0 if flat else 1.0 / det

        for cy in range(y0[i], y1[i] + 1):
            wy = hf.min_y + (cy + 0.5) * cs
            for cx in range(x0[i], x1[i] + 1):
                wx = hf.min_x + (cx + 0.5) * cs

                if flat:
                    lo, hi = float(zmins[i]), float(zmaxs[i])
                else:
                    # Barycentric coords of the column centre.
                    px, py = wx - ax, wy - ay
                    s = (px * vy - py * vx) * inv_det
                    t = (ux * py - uy * px) * inv_det

                    if s >= 0.0 and t >= 0.0 and s + t <= 1.0:
                        lo = hi = az + s * (bz - az) + t * (cz3 - az)
                    else:
                        # The centre is outside the triangle, but the triangle
                        # may still cover part of this column.  Sampling only
                        # centres drops every floor triangle smaller than a cell
                        # and leaves holes under pathgrid nodes (observed: 24/52
                        # nodes with NO span at all).  So test real overlap
                        # against the column square, and if they intersect, clamp
                        # the barycentric coords back onto the triangle and
                        # sample there.
                        #
                        # The SAT test is the single hottest thing in the build
                        # (2.2s of 5.9s on an exterior cell), so gate it: only a
                        # column whose centre is within one cell-diagonal of the
                        # triangle can possibly overlap it, and clearly-outside
                        # barycentrics (the common case for the bbox corners of a
                        # big triangle) are rejected with no work.
                        if s < -1.5 or t < -1.5 or s + t > 2.5:
                            continue
                        if not _tri_overlaps_square(ax, ay, bx, by, cx3, cy3,
                                                    wx, wy, half):
                            continue
                        if s < 0.0:
                            s = 0.0
                        elif s > 1.0:
                            s = 1.0
                        if t < 0.0:
                            t = 0.0
                        elif t > 1.0:
                            t = 1.0
                        if s + t > 1.0:
                            k = s + t
                            s /= k
                            t /= k
                        lo = hi = az + s * (bz - az) + t * (cz3 - az)

                if hi - lo < ch:
                    mid = 0.5 * (lo + hi)
                    lo, hi = mid - ch * 0.5, mid + ch * 0.5
                hf.add_span(cx, cy, lo, hi, walkable)


def _rasterize_grid(hf, tris, walkable):
    """Vectorized rasterizer for LARGE, mostly-flat triangles (LAND terrain).

    Terrain is a regular 33x33 grid whose triangles each span many columns; the
    scalar path spends all its time in the per-column SAT test for them.  Here we
    sample the triangle's plane at every column centre in its bbox with numpy and
    keep the ones inside (barycentric >= 0), which is exact for a planar triangle
    and ~20x faster.
    """
    if tris is None or len(tris) == 0:
        return
    cs = hf.cs
    a = tris[:, 0]
    b = tris[:, 1]
    c = tris[:, 2]

    x0 = np.floor((tris[:, :, 0].min(axis=1) - hf.min_x) / cs).astype(np.int64)
    x1 = np.floor((tris[:, :, 0].max(axis=1) - hf.min_x) / cs).astype(np.int64)
    y0 = np.floor((tris[:, :, 1].min(axis=1) - hf.min_y) / cs).astype(np.int64)
    y1 = np.floor((tris[:, :, 1].max(axis=1) - hf.min_y) / cs).astype(np.int64)
    np.clip(x0, 0, hf.w - 1, out=x0)
    np.clip(x1, 0, hf.w - 1, out=x1)
    np.clip(y0, 0, hf.h - 1, out=y0)
    np.clip(y1, 0, hf.h - 1, out=y1)

    ux = b[:, 0] - a[:, 0]
    uy = b[:, 1] - a[:, 1]
    vx = c[:, 0] - a[:, 0]
    vy = c[:, 1] - a[:, 1]
    det = ux * vy - uy * vx

    half_ch = hf.ch * 0.5
    for i in range(len(tris)):
        if abs(det[i]) < 1e-9:
            continue
        gx = np.arange(x0[i], x1[i] + 1)
        gy = np.arange(y0[i], y1[i] + 1)
        if not len(gx) or not len(gy):
            continue
        wx = hf.min_x + (gx + 0.5) * cs
        wy = hf.min_y + (gy + 0.5) * cs
        WX, WY = np.meshgrid(wx, wy)
        px = WX - a[i, 0]
        py = WY - a[i, 1]
        s = (px * vy[i] - py * vx[i]) / det[i]
        t = (ux[i] * py - uy[i] * px) / det[i]
        inside = (s >= -0.02) & (t >= -0.02) & (s + t <= 1.02)
        if not inside.any():
            continue
        z = a[i, 2] + s * (b[i, 2] - a[i, 2]) + t * (c[i, 2] - a[i, 2])
        ii, jj = np.nonzero(inside)
        for k in range(len(ii)):
            cyi = int(gy[ii[k]])
            cxi = int(gx[jj[k]])
            zz = float(z[ii[k], jj[k]])
            hf.add_span(cxi, cyi, zz - half_ch, zz + half_ch, walkable)


def build_heightfield(walkable_tris, blocking_tris, bounds, cs=None, ch=None,
                      grid_walkable=None):
    """Rasterize both soups into one Heightfield.

    bounds = (min_x, min_y, min_z, max_x, max_y, max_z) in game units.
    grid_walkable: optional big regular-grid walkable tris (LAND terrain) that
    take the fast vectorized path.
    """
    cs = cs or params.CS
    ch = ch or params.CH
    min_x, min_y, min_z, max_x, max_y, max_z = bounds

    w = int(np.ceil((max_x - min_x) / cs)) + 1
    h = int(np.ceil((max_y - min_y) / cs)) + 1
    # Guard memory on huge exterior cells by coarsening rather than exploding.
    while max(w, h) > params.MAX_GRID_DIM:
        cs *= 2.0
        w = int(np.ceil((max_x - min_x) / cs)) + 1
        h = int(np.ceil((max_y - min_y) / cs)) + 1

    hf = Heightfield(min_x, min_y, min_z, w, h, cs, ch)
    # Blocking first, walkable second: where a walkable surface coincides with a
    # blocking one (a floor abutting a wall base), the walkable flag should win
    # on the top surface, and add_span's top-surface rule gives us that.
    _rasterize(hf, blocking_tris, False)
    _rasterize(hf, walkable_tris, True)
    if grid_walkable is not None and len(grid_walkable):
        _rasterize_grid(hf, grid_walkable, True)
    return hf


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def filter_low_hanging_obstacles(hf):
    """A blocking span within MAX_CLIMB above a walkable one becomes walkable.

    This is what lets an NPC step ONTO a low thing (a rug's lip, a kerb, a low
    plinth) instead of treating it as a wall.
    """
    climb = params.MAX_CLIMB
    for col in hf.spans:
        prev_walk = False
        prev_top = 0.0
        for s in col:
            if (not s[2]) and prev_walk and (s[1] - prev_top) <= climb:
                s[2] = True
            prev_walk = s[2]
            prev_top = s[1]


def filter_ledge_spans(hf):
    """Un-walk spans at the lip of a drop taller than MAX_CLIMB.

    This is what keeps the navmesh OFF the tops of walls and away from the edge
    of a balcony — and what makes a table's sides a barrier rather than a ramp.
    """
    climb = params.MAX_CLIMB
    height = params.AGENT_HEIGHT
    w, h = hf.w, hf.h
    to_clear = []

    for cy in range(h):
        for cx in range(w):
            col = hf.spans[cy * w + cx]
            for si, s in enumerate(col):
                if not s[2]:
                    continue
                top = s[1]
                # Headroom above this span (to the next span's floor).
                s_top_clear = (col[si + 1][0] - top) if si + 1 < len(col) else 1e9

                min_drop = 1e9
                max_drop = -1e9
                for (dx, dy) in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = cx + dx, cy + dy
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        # Off-grid: treat as a big drop so we don't run the mesh
                        # off the edge of the world.
                        min_drop = min(min_drop, -height)
                        continue
                    ncol = hf.spans[ny * w + nx]
                    if not ncol:
                        min_drop = min(min_drop, -height)
                        continue
                    found = False
                    for ni, ns in enumerate(ncol):
                        n_top_clear = (ncol[ni + 1][0] - ns[1]) if ni + 1 < len(ncol) else 1e9
                        # Only a neighbour we could actually stand in counts.
                        if min(s_top_clear, n_top_clear) < height:
                            continue
                        d = ns[1] - top
                        if abs(d) <= climb:
                            min_drop = min(min_drop, d)
                            max_drop = max(max_drop, d)
                            found = True
                    if not found:
                        # No reachable neighbour surface -> a drop.
                        best = None
                        for ns in ncol:
                            d = ns[1] - top
                            if best is None or abs(d) < abs(best):
                                best = d
                        min_drop = min(min_drop, best if best is not None else -height)

                # A span whose neighbours differ by more than a step is a ledge.
                if min_drop < -climb or (max_drop - min_drop) > climb:
                    to_clear.append(s)

    for s in to_clear:
        s[2] = False


def filter_walkable_low_height_spans(hf):
    """Un-walk any span without AGENT_HEIGHT of clearance above it."""
    height = params.AGENT_HEIGHT
    for col in hf.spans:
        for i, s in enumerate(col):
            if not s[2]:
                continue
            ceil_z = col[i + 1][0] if i + 1 < len(col) else 1e9
            if ceil_z - s[1] < height:
                s[2] = False


def erode_walkable(hf, radius=None):
    """Erode the walkable set by the agent radius, in voxels.

    Correct standoff from walls by construction — this replaces the old
    hand-tuned EXCLUSION_MARGIN, which shrank each obstacle footprint by a fudge
    factor and hoped.
    """
    radius = params.AGENT_RADIUS if radius is None else radius
    r = int(np.ceil(radius / hf.cs))
    if r <= 0:
        return
    w, h = hf.w, hf.h

    # Distance-to-nonwalkable via a two-pass chamfer over the column grid, using
    # the TOP walkable span per column.  Column-level (not span-level) erosion is
    # what Recast does too, and it is what keeps this fast.
    INF = 1 << 20
    dist = np.full(w * h, INF, dtype=np.int32)
    for i, col in enumerate(hf.spans):
        if not any(s[2] for s in col):
            dist[i] = 0
    d = dist.reshape(h, w)
    for y in range(h):
        for x in range(w):
            v = d[y, x]
            if x > 0:
                v = min(v, d[y, x - 1] + 1)
            if y > 0:
                v = min(v, d[y - 1, x] + 1)
            d[y, x] = v
    for y in range(h - 1, -1, -1):
        for x in range(w - 1, -1, -1):
            v = d[y, x]
            if x + 1 < w:
                v = min(v, d[y, x + 1] + 1)
            if y + 1 < h:
                v = min(v, d[y + 1, x] + 1)
            d[y, x] = v

    changed = 0
    for i, col in enumerate(hf.spans):
        if d.flat[i] <= r:
            for s in col:
                if s[2]:
                    s[2] = False
                    changed += 1
    return changed


def apply_filters(hf):
    """Run the standard filter chain, in Recast's order."""
    filter_low_hanging_obstacles(hf)
    filter_ledge_spans(hf)
    filter_walkable_low_height_spans(hf)


def walkable_columns(hf):
    """{(cx,cy): [span, ...]} of walkable spans, for region growing."""
    out = {}
    for cy in range(hf.h):
        for cx in range(hf.w):
            col = hf.spans[cy * hf.w + cx]
            ws = [s for s in col if s[2]]
            if ws:
                out[(cx, cy)] = ws
    return out
