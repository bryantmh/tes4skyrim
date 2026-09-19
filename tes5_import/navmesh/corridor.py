"""Phase-1 corridor-ribbon navmesh generation.

THE MODEL, in one line:

    THE PATHGRID IS THE MESH.

Bethesda's pathgrid is the only part of the input that ASSERTS "an actor walks
here".  Instead of re-discovering walkable surface from collision (voxelize /
contour / region-flood) and then fighting to keep the result connected across
the seams that discovery introduces, we build the navmesh DIRECTLY on the
pathgrid: a flat, fixed-width ribbon of triangles centered on every pathgrid
edge.

Ribbons on a dense pathgrid overlap heavily (a node can carry 9 edges, and a
median edge is shorter than two ribbon widths), so they are not simply laid on
top of each other: corridor_union takes the boolean UNION of the ribbon polygons
and retriangulates it, per walkable surface.  The union is coverage-preserving
and non-overlapping by construction (see corridor_union), so the result is a
single connected sheet covering the pathgrid with zero stacked triangles.

The result is deliberately SPARSE — a corridor an actor can follow, not a
room-filling floor.  A completely functional, zero-bad-triangle navmesh that is
a bit narrow beats a dense one that is broken.  Width-grow (fill out to the
walls) is a later phase; this one gets the corridors + doors + links right.

Design principles (see docs/commentary/tes5_import_navmesh.md):
  1. The pathgrid CENTERLINE is sacred — never cut or moved, even where it
     clips a wall.  Only grown width (a later phase) may ever be clipped.
  2. Downward snap follows the pathgrid LINE'S OWN SLOPE.  A pathgrid edge
     A->B already IS the walk ramp (Oblivion places stair nodes at tread
     level).  We sit the ribbon on that straight line and only ever push a
     cross-section DOWN onto collision when the line floats above it — never
     let jagged treads push it up and reintroduce a sawtooth.  Slope stays
     slope.  Phase 1 keeps the corridor FLAT across its width.
  3. Conservative: when unsure, stop.  Doorways are assumed to already have
     pathgrid through them.

Output contract (identical to the old build_navmesh): a manifold (verts, tris)
where every edge is shared by <= 2 triangles — a 3+-shared edge silently
disconnects everything around it under _compute_adjacency.
"""

import math

import numpy as np

from . import corridor_grow, params, world

# Trim node-disc rays at stair nodes so the FLAT disc never rides out over a
# descending flight (see the disc loop in _build_corridor_strips).  Module
# flag so diagnostics can A/B it.
DISC_RAY_TRIM = True


# ---------------------------------------------------------------------------
# Walkable surface sampler (the only collision query Phase 1 needs)
# ---------------------------------------------------------------------------

def _height_grid(walkable, cell=128.0):
    """(triangles, grid, minx, miny) bucketing a walkable soup by plan cell."""
    W = np.asarray(walkable, dtype=float).reshape(-1, 3, 3)
    if not len(W):
        return None, None, 0.0, 0.0
    minx = float(W[:, :, 0].min())
    miny = float(W[:, :, 1].min())
    grid = {}
    for i, tri in enumerate(W):
        gx0 = int((tri[:, 0].min() - minx) // cell)
        gx1 = int((tri[:, 0].max() - minx) // cell)
        gy0 = int((tri[:, 1].min() - miny) // cell)
        gy1 = int((tri[:, 1].max() - miny) // cell)
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                grid.setdefault((gx, gy), []).append(i)
    return W, grid, minx, miny


def _bucket_heights(W, grid, minx, miny, cell, x, y):
    """Every walkable height at (x, y) from the triangles in its bucket."""
    out = []
    for i in grid.get((int((x - minx) // cell), int((y - miny) // cell)), ()):
        a, b, c = W[i]
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-6:
            continue
        l0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d
        l1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d
        l2 = 1.0 - l0 - l1
        if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
            continue
        out.append(l0 * a[2] + l1 * b[2] + l2 * c[2])
    return out


def _surface_sampler(walkable):
    """f(x, y, near_z) -> walkable height at (x,y) nearest near_z, or None.

    The returned callable carries a `.layers(x, y)` attribute listing every
    distinct walkable height there, ascending, deduped at 2u.
    """
    cell = 128.0
    W, grid, minx, miny = _height_grid(walkable, cell)
    if W is None:
        return None

    def sample(x, y, near_z):
        """The walkable height at (x, y) closest to near_z, or None."""
        best = None
        for z in _bucket_heights(W, grid, minx, miny, cell, x, y):
            if best is None or abs(z - near_z) < abs(best - near_z):
                best = z
        return best

    def layers(x, y):
        """Every distinct walkable height at (x, y), ascending (2u dedupe)."""
        out = []
        for z in sorted(_bucket_heights(W, grid, minx, miny, cell, x, y)):
            if not out or z - out[-1] > 2.0:
                out.append(z)
        return out

    sample.layers = layers
    return sample


#: Plan radii also sampled when snapping a node's Z (a node in a seam keeps its neighbours' height).
NODE_SNAP_RADII = (8.0, 16.0)


def _snap_node_z(sample, x, y, z):
    """Node Z snapped DOWN onto walkable collision (principle 2).

    The pathgrid hovers above the walked surface, and the navmesh must sit ON
    it.  Snap toward the surface only within a plausible window; never teleport
    to a distant floor, never rise onto an object standing on the floor.  The
    surface nearest the node's OWN height within NODE_SNAP_RADII wins.
    See: docs/commentary/tes5_import_navmesh.md#node-z-snaps-down-onto-collision
    """
    if sample is None:
        return z
    s = sample(x, y, z)
    for r in NODE_SNAP_RADII:
        for dx, dy in ((r, 0.0), (-r, 0.0), (0.0, r), (0.0, -r)):
            c = sample(x + dx, y + dy, z)
            if c is not None and (s is None or abs(c - z) < abs(s - z)):
                s = c
    if s is None:
        return z
    if s <= z + params.SEED_Z_TOLERANCE and s >= z - params.SEED_SNAP:
        return s
    if s < z:
        return z - params.SEED_SNAP
    return z


# ---------------------------------------------------------------------------
# Ribbon generation
# ---------------------------------------------------------------------------

def _edge_frame(nodes, node_z, i, j):
    """((ax,ay,az), (bx,by,bz), (ux,uy), length) for an edge, or None."""
    if i >= len(nodes) or j >= len(nodes) or i == j:
        return None
    ax, ay = nodes[i][0], nodes[i][1]
    bx, by = nodes[j][0], nodes[j][1]
    length = math.hypot(bx - ax, by - ay)
    if length < 1e-4:
        return None
    return ((ax, ay, node_z[i]), (bx, by, node_z[j]),
            ((bx - ax) / length, (by - ay) / length), length)


def _extended_ends(got, degree, i, j):
    """(pa, pb): the edge's ends pushed out by `RIBBON_END_EXTEND` at dead ends.

    See: docs/commentary/tes5_import_navmesh.md#only-dead-ends-extend
    """
    (ax, ay, az), (bx, by, bz), (ux, uy), length = got
    ext = params.RIBBON_END_EXTEND
    dz = bz - az
    ea = ext if degree.get(i, 0) <= 1 else 0.0
    eb = ext if degree.get(j, 0) <= 1 else 0.0
    return ((ax - ux * ea, ay - uy * ea, az - dz * (ea / length)),
            (bx + ux * eb, by + uy * eb, bz + dz * (eb / length)))


def _is_steep(pa, pb):
    """True for a stair/ramp edge: rise over run above RIBBON_GROW_MAX_SLOPE."""
    run = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
    return abs(pb[2] - pa[2]) / max(run, 1e-6) > params.RIBBON_GROW_MAX_SLOPE


def _prof_at(prof, pa, pb, x, y):
    """(height, |slope|) of a profile at plan point (x, y).

    The parameter runs along the pa->pb chord the profile was built on and
    is clamped, so beyond either end the end height carries on flat.
    """
    ax, ay = pa[0], pa[1]
    dx, dy = pb[0] - ax, pb[1] - ay
    d2 = dx * dx + dy * dy
    t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((x - ax) * dx
                                                 + (y - ay) * dy) / d2))
    f = t * (len(prof) - 1)
    k = min(len(prof) - 2, max(0, int(f)))
    z0, z1 = prof[k][2], prof[k + 1][2]
    run = math.dist(prof[k][:2], prof[k + 1][:2]) or 1.0
    return z0 + (z1 - z0) * (f - k), abs(z1 - z0) / run


def _edge_station_rows(pa, pb, frame, k, prof, ei, steep):
    """The 2(k+1) march rows of one edge: each station, both perpendiculars.

    A station's height and soft floor follow the profile where there is one:
    on the flight (local slope above RIBBON_GROW_MAX_SLOPE) the floor is the
    stair half-width, on the approaches it is the corridor ramp.
    See: docs/commentary/tes5_import_navmesh.md#steep-edges-are-grown
    """
    (ux, uy), (wx, wy), total = frame
    ramp = params.RIBBON_HALF_WIDTH
    lo0, lo1 = params.RIBBON_HALF_WIDTH, params.RIBBON_GROW_MIN_HALF
    rows = []
    for s in range(k + 1):
        t = s / k
        cxs = pa[0] + (pb[0] - pa[0]) * t
        cys = pa[1] + (pb[1] - pa[1]) * t
        czs = pa[2] + (pb[2] - pa[2]) * t
        flight = steep
        if prof:
            czs, slope = _prof_at(prof, pa, pb, cxs, cys)
            flight = slope > params.RIBBON_GROW_MAX_SLOPE
        d_end = min(t, 1.0 - t) * total
        frac = min(1.0, d_end / ramp) if ramp > 1e-6 else 1.0
        floor_h = (params.RIBBON_STAIR_HALF_WIDTH if flight
                   else lo0 + (lo1 - lo0) * frac)
        rows.append((cxs, cys, czs, wx, wy, ux, uy, floor_h, ei))
        rows.append((cxs, cys, czs, -wx, -wy, ux, uy, floor_h, ei))
    return rows


def _edge_march_rows(nodes, edges, node_z, degree, profiles, flat=()):
    """(station rows, plan entries) for every edge, steep ones included.

    `profiles` maps each steep edge to its tread profile (or None).
    See: docs/commentary/tes5_import_navmesh.md#steep-edges-are-grown
    """
    edge_index = {(i, j): e for e, (i, j) in enumerate(edges)}
    rows, plan = [], []
    for (i, j) in edges:
        got = _edge_frame(nodes, node_z, i, j)
        if got is None:
            continue
        (ux, uy), length = got[2], got[3]
        pa, pb = _extended_ends(got, degree, i, j)
        total = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
        k = max(1, int(round(total / params.RIBBON_STEP)))
        ei = edge_index.get((i, j), -1)
        base = len(rows)
        rows += _edge_station_rows(pa, pb, ((ux, uy), (-uy, ux), total), k,
                                   profiles.get((i, j)), ei,
                                   (i, j) not in flat and _is_steep(pa, pb))
        plan.append(('edge', (i, j), pa, pb, (ux, uy), (-uy, ux),
                     length, k, base))
    return rows, plan


def _bridge_blocked_stations(widths, plan):
    """Give a station blocked on BOTH sides the widths of its neighbours.

    See: docs/commentary/tes5_import_navmesh.md#blocked-stations-are-bridged
    """
    for entry in plan:
        if entry[0] != 'edge':
            continue
        k, base = entry[7], entry[8]
        open_s = [s for s in range(k + 1)
                  if widths[base + 2 * s] > 0.0 or widths[base + 2 * s + 1] > 0.0]
        if not open_s or len(open_s) == k + 1:
            continue
        for s in range(k + 1):
            if widths[base + 2 * s] > 0.0 or widths[base + 2 * s + 1] > 0.0:
                continue
            before = max((o for o in open_s if o < s), default=None)
            after = min((o for o in open_s if o > s), default=None)
            if before is None or after is None:
                src = after if before is None else before
                widths[base + 2 * s] = widths[base + 2 * src]
                widths[base + 2 * s + 1] = widths[base + 2 * src + 1]
                continue
            f = (s - before) / float(after - before)
            for side in (0, 1):
                w0 = widths[base + 2 * before + side]
                w1 = widths[base + 2 * after + side]
                widths[base + 2 * s + side] = w0 + (w1 - w0) * f


#: Stations either side whose shortest rail bounds a rail past the cap (8u apart).
FAR_WIDTH_WINDOW = 8


def _eroded(w, win, wrap):
    """Running minimum of `w` over +-`win` samples; `wrap` closes the ring."""
    pad = np.pad(w, win, mode='wrap' if wrap else 'edge')
    return np.lib.stride_tricks.sliding_window_view(
        pad, 2 * win + 1).min(axis=1)


def _erode_far_widths(widths, plan):
    """Past the cap, hold each rail and disc ray to its shortest neighbour.

    See: docs/commentary/tes5_import_navmesh.md#far-widths-are-eroded
    """
    cap = params.RIBBON_GROW_MAX_HALF
    nrays = params.RIBBON_GROW_DISC_RAYS
    for entry in plan:
        base = entry[-1]
        if entry[0] == 'disc':
            runs = [(slice(base, base + nrays), 1, True)]
        else:
            end = base + 2 * (entry[7] + 1)
            runs = [(slice(base + side, end, 2), FAR_WIDTH_WINDOW, False)
                    for side in (0, 1)]
        for sl, win, wrap in runs:
            w = np.asarray(widths[sl], dtype=np.float64)
            widths[sl] = np.minimum(w, np.maximum(cap, _eroded(w, win, wrap)))


#: Disc station column-9 encoding: `_DISC_STATION - node_index` (always < 0).
_DISC_STATION = -1


def _disc_march_rows(nodes, node_z, degree, rows):
    """Append each node's radial fan to `rows`; returns the disc plan entries.

    A disc's column-9 value encodes its NODE, not an edge: its rays start ON
    the centerlines meeting there, which the crossing stop must not count.
    See: docs/commentary/tes5_import_navmesh.md#stair-nodes-get-discs-too
    """
    nrays = params.RIBBON_GROW_DISC_RAYS
    plan = []
    for ni in sorted(degree):
        if ni >= len(nodes):
            continue
        nx, ny = nodes[ni][0], nodes[ni][1]
        nz = node_z[ni]
        base = len(rows)
        for kk in range(nrays):
            ang = 2.0 * math.pi * kk / nrays
            ddx, ddy = math.cos(ang), math.sin(ang)
            rows.append((nx, ny, nz, ddx, ddy, -ddy, ddx, 0.0,
                         _DISC_STATION - ni))
        plan.append(('disc', ni, nx, ny, nz, base))
    return plan


def _stands_on_land(land, nodes, node_z, i, j):
    """True when ribbon (i, j) -- or node disc (i, i) -- follows the LAND.

    Each node must have snapped onto the terrain itself or lie in a
    neighbouring cell.  Such an edge is never a staircase, however steep.
    See: docs/commentary/tes5_import_navmesh.md#terrain-standing-ribbons-follow-the-land
    """
    if land is None:
        return False
    return all(not land.covers(nodes[n][0], nodes[n][1])
               or abs(node_z[n] - land.z(nodes[n][0], nodes[n][1]))
               <= NODE_SNAP_RADII[0]
               for n in (i, j))


def _plan_stations(nodes, edges, node_z, degree, grow, profiles, land=None,
                   flat=()):
    """Every march station the grow needs, as a plan the native batch consumes.

    Returns (stations, plan, on_land): an (N, 9) float64 array of
    (cx, cy, cz, dirx, diry, tanx, tany, lo, edge_index), the reassembly plan
    (`edge` and `disc` entries) and the per-station terrain-following flags.
    See: docs/commentary/tes5_import_navmesh.md#stations-are-planned-then-marched
    """
    if not grow:
        return np.zeros((0, 9), dtype=np.float64), [], np.zeros(0)
    rows, plan = _edge_march_rows(nodes, edges, node_z, degree, profiles,
                                  flat)
    plan.extend(_disc_march_rows(nodes, node_z, degree, rows))
    st = (np.asarray(rows, dtype=np.float64) if rows
          else np.zeros((0, 9), dtype=np.float64))
    on_land = np.zeros(len(st), dtype=np.float64)
    for at, entry in enumerate(plan):
        i, j = entry[1] if entry[0] == 'edge' else (entry[1], entry[1])
        end = plan[at + 1][-1] if at + 1 < len(plan) else len(st)
        if _stands_on_land(land, nodes, node_z, i, j):
            on_land[entry[-1]:end] = 1.0
            for row in st[entry[-1]:end]:
                row[2] = land.z(row[0], row[1])
    return st, plan, on_land


def _profile_stations(sample, pa, pb, n, half):
    """Per-station (x, y, candidate heights) along a steep edge's centerline.

    Candidates come from the whole CROSS-SECTION (centerline and +-half/2,
    +-half across it), so a line that clips a flight's corner still sees
    every tread.
    See: docs/commentary/tes5_import_navmesh.md#profile-samples-the-cross-section
    """
    layers = sample.layers
    ax, ay, az = pa
    bx, by, bz = pb
    run = math.hypot(bx - ax, by - ay) or 1.0
    wx, wy = -(by - ay) / run, (bx - ax) / run
    lo = min(az, bz) - params.MAX_CLIMB
    hi = max(az, bz) + params.MAX_CLIMB
    stations = []
    for s in range(n + 1):
        t = s / n
        x = ax + (bx - ax) * t
        y = ay + (by - ay) * t
        cand = set()
        for off in (0.0, 0.5 * half, -0.5 * half, half, -half):
            cand.update(round(z, 1) for z in layers(x + wx * off, y + wy * off)
                        if lo <= z <= hi)
        stations.append((x, y, sorted(cand) or [az + (bz - az) * t]))
    return stations


def _clamped_chord(stations, az, bz, n):
    """Each node's level held while its floor continues, one ramp between.

    None when the two plateaus overlap (the lower floor runs under the
    upper), where a chord is the only honest answer.
    See: docs/commentary/tes5_import_navmesh.md#profile-samples-the-cross-section
    """
    step = params.MAX_CLIMB

    def holds(s, z):
        """True while some layer at station s is within a step of z."""
        return any(abs(c - z) <= step for c in stations[s][2])

    ka = 0
    while ka + 1 <= n and holds(ka + 1, az):
        ka += 1
    kb = n
    while kb - 1 >= 0 and holds(kb - 1, bz):
        kb -= 1
    if ka >= kb:
        return None
    pts = []
    for s in range(n + 1):
        f = max(0.0, min(1.0, (s - ka) / float(kb - ka)))
        pts.append((stations[s][0], stations[s][1], az + (bz - az) * f))
    return pts


def _cheapest_layer_path(stations, az, n):
    """(costs, backpointers) for the one-step-per-station layer walk."""
    inf = float('inf')
    step = params.MAX_CLIMB
    costs = [[(abs(z - az) if abs(z - az) <= step else inf)
              for z in stations[0][2]]]
    back = []
    for s in range(1, n + 1):
        cand = stations[s][2]
        prev_c, prev_z = costs[-1], stations[s - 1][2]
        row = [inf] * len(cand)
        bk = [0] * len(cand)
        for i, z in enumerate(cand):
            for j, zp in enumerate(prev_z):
                if prev_c[j] == inf or abs(z - zp) > step:
                    continue
                c = prev_c[j] + abs(z - zp)
                if c < row[i]:
                    row[i], bk[i] = c, j
        costs.append(row)
        back.append(bk)
    return costs, back


def _surface_profile(sample, pa, pb, half=None):
    """Height profile along a STEEP edge, following the real walkable surface.

    Returns None (caller keeps the chord) only when no layer path reaches the
    far node's height AND the clamped-chord fallback has nothing to hold.
    See: docs/commentary/tes5_import_navmesh.md#steep-heights-follow-the-treads
    """
    if getattr(sample, 'layers', None) is None:
        return None
    ax, ay, az = pa
    bx, by, bz = pb
    run = math.hypot(bx - ax, by - ay)
    if run < 32.0:
        return None
    n = max(2, int(run // 16.0))
    stations = _profile_stations(
        sample, pa, pb, n,
        params.RIBBON_STAIR_HALF_WIDTH if half is None else half)
    costs, back = _cheapest_layer_path(stations, az, n)

    inf = float('inf')
    best = None
    for i, z in enumerate(stations[n][2]):
        if costs[n][i] == inf or abs(z - bz) > params.MAX_CLIMB:
            continue
        key = (abs(z - bz), costs[n][i], i)
        if best is None or key < best:
            best = key
    if best is None:
        return _clamped_chord(stations, az, bz, n)
    idx = best[2]
    zs = [0.0] * (n + 1)
    for s in range(n, -1, -1):
        zs[s] = stations[s][2][idx]
        if s > 0:
            idx = back[s - 1][idx]
    pts = [(stations[s][0], stations[s][1], zs[s]) for s in range(n + 1)]
    pts[0] = (ax, ay, az)
    pts[-1] = (bx, by, bz)
    return pts


def _node_degrees(edges):
    """How many edges touch each node."""
    degree = {}
    for (i, j) in edges:
        degree[i] = degree.get(i, 0) + 1
        degree[j] = degree.get(j, 0) + 1
    return degree


def _steep_counts(nodes, edges, node_z, flat=()):
    """How many STEEP runs touch each node; `flat` edges never count."""
    steep_count = {}
    for (i, j) in edges:
        got = _edge_frame(nodes, node_z, i, j)
        if got is None or (i, j) in flat:
            continue
        (_a, _b, _u, run) = got
        if abs(node_z[j] - node_z[i]) / run > params.RIBBON_GROW_MAX_SLOPE:
            steep_count[i] = steep_count.get(i, 0) + 1
            steep_count[j] = steep_count.get(j, 0) + 1
    return steep_count


def _ungrown_strip(strip, steep, prof):
    """Finish a strip the march never planned: fixed width, real tread heights.

    See: docs/commentary/tes5_import_navmesh.md#a-steep-ribbon-is-never-grown
    """
    strip['half'] = (params.RIBBON_STAIR_HALF_WIDTH if steep
                     else params.RIBBON_HALF_WIDTH)
    if prof:
        strip['prof'] = prof
    return strip


def _steep_profiles(nodes, edges, node_z, degree, sample, flat=()):
    """{(i, j): tread profile or None} for every steep edge not in `flat`.

    See: docs/commentary/tes5_import_navmesh.md#profile-samples-the-cross-section
    """
    out = {}
    for (i, j) in edges:
        got = _edge_frame(nodes, node_z, i, j)
        if got is None or (i, j) in flat:
            continue
        pa, pb = _extended_ends(got, degree, i, j)
        if _is_steep(pa, pb):
            out[(i, j)] = (_surface_profile(sample, pa, pb)
                           if sample is not None else None)
    return out


def _on_flight(prof, pa, pb, x, y):
    """True where the profile climbs faster than a corridor may be grown."""
    if not prof:
        return _is_steep(pa, pb)
    return _prof_at(prof, pa, pb, x, y)[1] > params.RIBBON_GROW_MAX_SLOPE


def _grown_outline(strip, entry, widths, w, prof):
    """Finish a grown strip: simplified rails closed into an explicit outline.

    Stations on the FLIGHT keep the fixed stair half-width; only the flat
    approaches take their marched widths.
    See: docs/commentary/tes5_import_navmesh.md#steep-edges-are-grown
    """
    wx, wy = w
    _, _, ppa, ppb, _u, _w, _len, k, base = entry
    left, right = [], []
    max_h = params.RIBBON_HALF_WIDTH
    for s in range(k + 1):
        t = s / k
        cxs = ppa[0] + (ppb[0] - ppa[0]) * t
        cys = ppa[1] + (ppb[1] - ppa[1]) * t
        hl = float(widths[base + 2 * s])
        hr = float(widths[base + 2 * s + 1])
        if strip.get('land') is None and _on_flight(prof, ppa, ppb, cxs, cys):
            hl = hr = params.RIBBON_STAIR_HALF_WIDTH
        left.append((cxs + wx * hl, cys + wy * hl))
        right.append((cxs - wx * hr, cys - wy * hr))
        max_h = max(max_h, hl, hr)
    left = _simplify(left, params.RIBBON_RAIL_SIMPLIFY)
    right = _simplify(right, params.RIBBON_RAIL_SIMPLIFY)
    strip['poly'] = left + right[::-1]
    strip['half'] = max_h
    return strip


def _edge_strip(nodes, node_z, i, j, degree, grown_edges, widths, profiles,
                land=None):
    """The ribbon for one pathgrid edge, or None if the edge is unusable.

    See: docs/commentary/tes5_import_navmesh.md#only-dead-ends-extend
    """
    got = _edge_frame(nodes, node_z, i, j)
    if got is None:
        return None
    (ax, ay, az), (bx, by, bz), (ux, uy), length = got
    pa, pb = _extended_ends(got, degree, i, j)
    strip = {
        'edge': (i, j),
        'na': (ax, ay, az), 'nb': (bx, by, bz),
        'a': pa, 'b': pb,
        'u': (ux, uy), 'w': (-uy, ux), 'len': length,
    }
    if land is not None:
        strip['land'] = land
    steep = land is None and _is_steep(pa, pb)
    prof = profiles.get((i, j)) if steep else None
    entry = grown_edges.get((i, j)) if widths is not None else None
    if entry is None:
        return _ungrown_strip(strip, steep, prof)
    strip = _grown_outline(strip, entry, widths, (-uy, ux), prof)
    if prof:
        strip['prof'] = prof
    return strip


def _trim_disc_ray(layers, nx, ny, nz, ddx, ddy, d):
    """Shorten a disc ray where the real surface leaves the node's level.

    See: docs/commentary/tes5_import_navmesh.md#disc-rays-are-trimmed-at-stairs
    """
    zcur = nz
    good = params.RIBBON_HALF_WIDTH
    dd = good
    while dd < d - 1e-6:
        dd = min(d, dd + 8.0)
        cand = [z for z in layers(nx + ddx * dd, ny + ddy * dd)
                if abs(z - zcur) <= params.MAX_CLIMB]
        if not cand:
            good = dd
            continue
        zc = min(cand, key=lambda z: abs(z - zcur))
        if abs(zc - nz) > params.MAX_CLIMB:
            break
        zcur = zc
        good = dd
    return good


def _node_reach(plan, widths):
    """{node: largest half-width any incident ribbon reached at that node}.

    See: docs/commentary/tes5_import_navmesh.md#disc-radius-capped-by-its-ribbons
    """
    reach = {}
    for entry in plan:
        if entry[0] != 'edge':
            continue
        (i, j), k, base = entry[1], entry[7], entry[8]
        for node, s in ((i, 0), (j, k)):
            w = max(float(widths[base + 2 * s]), float(widths[base + 2 * s + 1]))
            reach[node] = max(reach.get(node, 0.0), w)
    return reach


#: A disc ray runs past its ribbons' reach only over ground this close to the node's level.
DISC_LEVEL_TOL = 2.0


def _level_reach(layers, nx, ny, nz, ddx, ddy, start, d):
    """Largest distance in [start, d] up to which the ground stays AT level nz.

    See: docs/commentary/tes5_import_navmesh.md#disc-radius-capped-by-its-ribbons
    """
    good = start
    dd = start
    while dd < d - 1e-6:
        dd = min(d, dd + 8.0)
        if not any(abs(z - nz) <= DISC_LEVEL_TOL
                   for z in layers(nx + ddx * dd, ny + ddy * dd)):
            break
        good = dd
    return good


def _disc_strip(entry, widths, layers, steep_strips, trim, cap):
    """The node-disc ribbon for one plan entry, or None if it degenerates.

    A ray is clamped to `cap`, the reach of the node's own ribbons, and runs
    beyond it only over ground at the node's level.
    See: docs/commentary/tes5_import_navmesh.md#disc-radius-capped-by-its-ribbons
    """
    _, ni, nx, ny, nz, base = entry
    nrays = params.RIBBON_GROW_DISC_RAYS
    disc = []
    for kk in range(nrays):
        ang = 2.0 * math.pi * kk / nrays
        ddx, ddy = math.cos(ang), math.sin(ang)
        d = float(widths[base + kk])
        if d > cap:
            d = (_level_reach(layers, nx, ny, nz, ddx, ddy, cap, d)
                 if layers is not None else cap)
        if trim and d > params.RIBBON_HALF_WIDTH:
            d = _trim_disc_ray(layers, nx, ny, nz, ddx, ddy, d)
        disc.append((nx + ddx * d, ny + ddy * d))
    disc = _simplify(disc, params.RIBBON_RAIL_SIMPLIFY)
    if len(disc) < 3:
        return None
    disc = _clip_flat_poly_off_level(disc, nx, ny, nz, steep_strips)
    if len(disc) < 3:
        return None
    rmax = max(math.hypot(px - nx, py - ny) for (px, py) in disc)
    return {
        'edge': (ni, ni),
        'na': (nx, ny, nz), 'nb': (nx, ny, nz),
        'a': (nx, ny, nz), 'b': (nx, ny, nz),
        'u': (1.0, 0.0), 'w': (0.0, 1.0),
        'len': max(rmax, 1.0), 'half': max(rmax, 1.0),
        'poly': disc,
    }


def _steep_strips(strips):
    """Every ribbon steeper than RIBBON_GROW_MAX_SLOPE."""
    out = []
    for s in strips:
        if s.get('len', 0.0) < 1e-6 or s.get('land') is not None:
            continue
        if (abs(s['nb'][2] - s['na'][2]) / s['len']
                > params.RIBBON_GROW_MAX_SLOPE):
            out.append(s)
    return out


def _build_corridor_strips(nodes, edges, node_z, wall_hit=None,
                           walk_probe=None, field=None,
                           blocking=None, walkable=None, sample=None,
                           land_from=None, land=None):
    """One corridor ribbon per pathgrid edge, plus a disc at every node.

    Each strip carries its centerline ends (after dead-end extension), the
    along/perpendicular units, a MAX half-width for level lookups, and in
    Phase 2 an explicit grown outline.  They are NOT yet a shared mesh --
    corridor_union takes their boolean union and retriangulates it.
    See: docs/commentary/tes5_import_navmesh.md#ribbon-construction
    """
    grow = params.RIBBON_GROW and blocking is not None
    degree = _node_degrees(edges)
    flat = {e for e in edges if _stands_on_land(land, nodes, node_z, *e)}
    steep_count = _steep_counts(nodes, edges, node_z, flat)
    profiles = _steep_profiles(nodes, edges, node_z, degree, sample, flat)

    stations, plan, on_land = _plan_stations(nodes, edges, node_z, degree,
                                             grow, profiles, land, flat)
    widths = None
    if len(stations):
        widths = corridor_grow.grow_batch(blocking, walkable, stations,
                                          nodes, edges, node_z, land_from,
                                          land, on_land)
        _bridge_blocked_stations(widths, plan)
        _erode_far_widths(widths, plan)
    grown_edges = {p[1]: p for p in plan if p[0] == 'edge'}

    strips = []
    for (i, j) in edges:
        strip = _edge_strip(nodes, node_z, i, j, degree, grown_edges,
                            widths, profiles, land if (i, j) in flat else None)
        if strip is not None:
            strips.append(strip)

    if widths is None:
        return strips
    steep = _steep_strips(strips)
    layers = getattr(sample, 'layers', None) if sample is not None else None
    reach = _node_reach(plan, widths)
    for entry in plan:
        if entry[0] != 'disc':
            continue
        trim = (DISC_RAY_TRIM and layers is not None
                and steep_count.get(entry[1], 0) >= 1)
        cap = max(params.RIBBON_HALF_WIDTH, reach.get(entry[1], 0.0))
        disc = _disc_strip(entry, widths, layers, steep, trim, cap)
        if disc is not None:
            if _stands_on_land(land, nodes, node_z, entry[1], entry[1]):
                disc['land'] = land
            strips.append(disc)
    return strips


def _strip_z_at(strip, az, bz, t):
    """Height along a strip at parameter t, following its profile if it has one."""
    prof = strip.get('prof')
    if not prof:
        return az + (bz - az) * t
    f = t * (len(prof) - 1)
    k = min(len(prof) - 2, max(0, int(f)))
    return prof[k][2] + (prof[k + 1][2] - prof[k][2]) * (f - k)


def _off_level_mask(strip, az, bz, nz, ax, ay, bx, by, n):
    """Per-station True where the strip has left the flat surface's level."""
    if callable(nz):
        return [abs(_strip_z_at(strip, az, bz, k / n)
                    - nz(ax + (bx - ax) * (k / n),
                         ay + (by - ay) * (k / n))) > params.MAX_CLIMB
                for k in range(n + 1)]
    return [abs(_strip_z_at(strip, az, bz, k / n) - nz) > params.MAX_CLIMB
            for k in range(n + 1)]


def _anchor_stations(mask, ap, ax, ay, dx, dy, n):
    """On-level stations lying INSIDE the polygon, which anchor the flight.

    See: docs/commentary/tes5_import_navmesh.md#flat-polys-are-clipped-off-level
    """
    from shapely.geometry import Point
    anchored = set()
    for k in range(n + 1):
        if mask[k]:
            continue
        try:
            if ap.contains(Point(ax + dx * (k / n), ay + dy * (k / n))):
                anchored.add(k)
        except Exception:
            pass
    return anchored


def _cut_quads(strip, mask, anchored, frame, half, n):
    """The quads to subtract: off-level runs contiguous with an anchored mouth."""
    (ax, ay), (ux, uy), (wx, wy), run = frame
    hits = []
    k = 0
    while k <= n:
        if not mask[k]:
            k += 1
            continue
        k2 = k
        while k2 + 1 <= n and mask[k2 + 1]:
            k2 += 1
        if (k - 1) in anchored or (k2 + 1) in anchored:
            d0, d1 = run * k / n, run * k2 / n
            if d1 - d0 > 1.0:
                hits.append((
                    (ax + ux * d0 + wx * half, ay + uy * d0 + wy * half),
                    (ax + ux * d0 - wx * half, ay + uy * d0 - wy * half),
                    (ax + ux * d1 - wx * half, ay + uy * d1 - wy * half),
                    (ax + ux * d1 + wx * half, ay + uy * d1 + wy * half)))
        k = k2 + 1
    return hits


def _strip_cut_quads(strip, disc, nx, ny, nz, rmax, anchor):
    """Quads this steep strip contributes to the subtraction, possibly empty."""
    ax, ay, az = strip['a']
    bx, by, bz = strip['b']
    run = math.hypot(bx - ax, by - ay)
    if run < 1e-6:
        return []
    half = float(strip.get('half', params.RIBBON_STAIR_HALF_WIDTH))
    dx, dy = bx - ax, by - ay
    t0 = max(0.0, min(1.0, ((nx - ax) * dx + (ny - ay) * dy) / (run * run)))
    if math.hypot(nx - (ax + dx * t0),
                  ny - (ay + dy * t0)) > rmax + half + 8.0:
        return []
    ap = anchor()
    if ap is None:
        return []
    n = max(2, int(run // 8.0))
    mask = _off_level_mask(strip, az, bz, nz, ax, ay, bx, by, n)
    anchored = _anchor_stations(mask, ap, ax, ay, dx, dy, n)
    if not anchored:
        return []
    frame = ((ax, ay), (dx / run, dy / run), (-dy / run, dx / run), run)
    return _cut_quads(strip, mask, anchored, frame, half, n)


def _subtract_quads(disc, nx, ny, hits):
    """Cut `hits` out of the polygon, keeping the piece the node stands on."""
    try:
        from shapely.geometry import Point, Polygon
        from shapely.ops import unary_union
        dp = Polygon(disc)
        if not dp.is_valid:
            dp = dp.buffer(0)
        cut = dp.difference(unary_union([Polygon(q) for q in hits]))
        if cut.is_empty:
            return disc
        pieces = list(cut.geoms) if hasattr(cut, 'geoms') else [cut]
        pieces = [g for g in pieces if g.geom_type == 'Polygon' and g.area > 1.0]
        if not pieces:
            return disc
        best = min(pieces, key=lambda g: g.distance(Point(nx, ny)))
        ring = list(best.exterior.coords)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        return ring
    except Exception:
        return disc


def _clip_flat_poly_off_level(disc, nx, ny, nz, steep_strips):
    """Remove from a FLAT polygon the ground where a steep ribbon that MEETS it
    has LEFT the polygon's level by more than a step.

    (nx, ny) anchors which piece survives a split; `nz` is the flat surface's
    height, either a constant (node disc) or a callable (sloped door quad).
    See: docs/commentary/tes5_import_navmesh.md#flat-polys-are-clipped-off-level
    """
    cache = []

    def anchor():
        """The buffered polygon, built lazily -- most callers never need it."""
        if not cache:
            try:
                from shapely.geometry import Polygon
                ap = Polygon(disc)
                if not ap.is_valid:
                    ap = ap.buffer(0)
                cache.append(ap.buffer(8.0))
            except Exception:
                cache.append(None)
        return cache[0]

    rmax = max(math.hypot(px - nx, py - ny) for (px, py) in disc)
    hits = []
    for s in steep_strips:
        hits.extend(_strip_cut_quads(s, disc, nx, ny, nz, rmax, anchor))
    if not hits:
        return disc
    return _subtract_quads(disc, nx, ny, hits)


def _simplify(pts, tol):
    """Douglas-Peucker on a polyline, keeping both endpoints."""
    if tol <= 0.0 or len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        ax, ay = pts[i0]
        bx, by = pts[i1]
        dx, dy = bx - ax, by - ay
        d2 = dx * dx + dy * dy
        worst = -1.0
        wi = -1
        for m in range(i0 + 1, i1):
            px, py = pts[m]
            if d2 < 1e-12:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / d2))
                d = math.hypot(px - (ax + dx * t), py - (ay + dy * t))
            if d > worst:
                worst, wi = d, m
        if worst > tol:
            keep[wi] = True
            stack.append((i0, wi))
            stack.append((wi, i1))
    return [p for p, k in zip(pts, keep) if k]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def _cell_geometry(refr_recs, base_model_by_fid, get_collision, land_rec,
                   origin_x, origin_y, door_bases):
    """(walkable, blocking, land_from): LAND is walkable[land_from:], or no rows."""
    walkable, blocking, land_walk = world.gather_cell_geometry(
        refr_recs or [], base_model_by_fid or {}, get_collision,
        land_rec=land_rec, origin_x=origin_x, origin_y=origin_y,
        split_land=True, skip_bases=door_bases)
    land_from = len(walkable)
    if land_walk is not None and len(land_walk):
        walkable = (np.concatenate([walkable, land_walk])
                    if land_from else land_walk)
    return walkable, blocking, land_from


def _quad_height_fn(poly, zb, zf, sweep, bm, fm):
    """A callable giving the door quad's ramped height at any (x, y)."""
    bmx, bmy = bm
    fmx, fmy = fm

    def _qz(px, py):
        """Height of the ramping quad at this point."""
        if sweep < 1e-6:
            return zb
        t = (((px - bmx) * (fmx - bmx) + (py - bmy) * (fmy - bmy))
             / (sweep * sweep))
        return zb + (zf - zb) * max(0.0, min(1.0, t))

    return _qz


def _ramped_strip(ps, zb, zf, sweep, bm, fm):
    """Point a door strip's height axis down its ramp, when it has one."""
    if abs(zf - zb) <= 1.0 or sweep <= 1e-6:
        return ps
    bmx, bmy = bm
    fmx, fmy = fm
    ux_, uy_ = (fmx - bmx) / sweep, (fmy - bmy) / sweep
    ps['a'] = (bmx, bmy, zb)
    ps['b'] = (fmx, fmy, zf)
    ps['na'], ps['nb'] = ps['a'], ps['b']
    ps['u'] = (ux_, uy_)
    ps['w'] = (-uy_, ux_)
    ps['len'] = sweep
    ps['half'] = max(float(ps['half']), sweep) + 8.0
    return ps


def _door_quad_strip(fp, steep_list):
    """(strip, base entry, pins) for one door footprint, or None if degenerate.

    See: docs/commentary/tes5_import_navmesh.md#the-door-quad-ramps-and-is-clipped
    """
    from . import corridor_union
    poly = fp['poly']
    zb = float(fp['z'])
    zf = float(fp.get('z_far', fp['z']))
    bmx = 0.5 * (poly[0][0] + poly[1][0])
    bmy = 0.5 * (poly[0][1] + poly[1][1])
    fmx = 0.5 * (poly[2][0] + poly[3][0])
    fmy = 0.5 * (poly[2][1] + poly[3][1])
    sweep = math.hypot(fmx - bmx, fmy - bmy)
    if abs(zf - zb) > 0.5 * max(sweep, 1.0):
        zf = zb
    qz = _quad_height_fn(poly, zb, zf, sweep, (bmx, bmy), (fmx, fmy))

    if steep_list and len(poly) >= 3:
        if fp['base'] is not None:
            ax_ = 0.5 * (fp['base'][0][0] + fp['base'][1][0])
            ay_ = 0.5 * (fp['base'][0][1] + fp['base'][1][1])
        else:
            ax_ = sum(p[0] for p in poly) / len(poly)
            ay_ = sum(p[1] for p in poly) / len(poly)
        poly = _clip_flat_poly_off_level(poly, ax_, ay_, qz, steep_list)
    if len(poly) < 3:
        return None

    ps = _ramped_strip(corridor_union._poly_strip(poly, zb),
                       zb, zf, sweep, (bmx, bmy), (fmx, fmy))
    if fp['base'] is None:
        return ps, None, []
    (b0, b1), apex, fz = fp['base'], fp['apex'], fp['z']
    pins = [(b0[0], b0[1], fz), (b1[0], b1[1], fz),
            (0.5 * (b0[0] + b1[0]), 0.5 * (b0[1] + b1[1]), fz),
            (apex[0], apex[1], fz)]
    return ps, (b0, b1, apex, fp['z']), pins


def _door_geometry(corridors, door_list, nodes, edges, wall_hit, cell_clip):
    """(door strips, door base edges, wedge pins) from a probe union.

    See: docs/commentary/tes5_import_navmesh.md#door-mesh-stays-in-the-union
    """
    from . import corridor_doors, corridor_union
    strips, edges_out, pins = [], [], []
    if not door_list:
        return strips, edges_out, pins
    rv, rt = corridor_union.build_union_mesh(corridors, cell_bounds=cell_clip,
                                             wall_cut=None, probe_only=True)
    if not rt:
        return strips, edges_out, pins
    steep_list = _steep_strips(corridors)
    for fp in corridor_doors.door_footprints(rv, rt, door_list,
                                             wall_hit=wall_hit, nodes=nodes,
                                             pg_edges=edges):
        got = _door_quad_strip(fp, steep_list)
        if got is None:
            continue
        ps, edge, quad_pins = got
        strips.append(ps)
        if edge is not None:
            edges_out.append(edge)
            pins.extend(quad_pins)
    return strips, edges_out, pins


def _centerline_samples(nodes, edges, node_z):
    """(x, y, z, ux, uy) along every pathgrid edge, at RIBBON_STEP spacing.

    See: docs/commentary/tes5_import_navmesh.md#every-centerline-is-sampled
    """
    out = []
    for (i, j) in edges:
        got = _edge_frame(nodes, node_z, i, j)
        if got is None:
            continue
        (_a, _b, (ux_, uy_), run) = got
        steps = max(2, int(run // params.RIBBON_STEP))
        for s in range(steps + 1):
            f = s / steps
            out.append((nodes[i][0] + (nodes[j][0] - nodes[i][0]) * f,
                        nodes[i][1] + (nodes[j][1] - nodes[i][1]) * f,
                        node_z[i] + (node_z[j] - node_z[i]) * f, ux_, uy_))
    return out


def _tri_carries_door(verts, tri, door_xy):
    """Does a door threshold stand on this triangle, within a storey?"""
    a, b, c = (verts[i] for i in tri)
    d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(d) < 1e-9:
        return False
    for (px, py, pz) in door_xy:
        l0 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / d
        l1 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / d
        l2 = 1.0 - l0 - l1
        if (l0 >= -0.05 and l1 >= -0.05 and l2 >= -0.05
                and abs(l0 * a[2] + l1 * b[2] + l2 * c[2] - pz) <= 128.0):
            return True
    return False


def _drop_attach_scraps(verts, tris, door_xy):
    """Drop 1-2 triangle specks the door attach orphaned, keeping door ground.

    See: docs/commentary/tes5_import_navmesh.md#attach-era-scraps-are-dropped
    """
    from . import corridor_clean
    comps = corridor_clean.components([list(map(int, t)) for t in tris])
    if len(comps) <= 1:
        return tris
    drop = set()
    for comp in comps:
        if len(comp) > 2:
            continue
        if not any(_tri_carries_door(verts, tris[ti], door_xy)
                   for ti in comp):
            drop.update(comp)
    if not drop:
        return tris
    return [t for ti, t in enumerate(tris) if ti not in drop]


def _ccw_in_plan(verts, tris):
    """Flip any triangle wound CW in plan; the mesh is a heightfield.

    See: docs/commentary/tes5_import_navmesh.md#winding-must-be-ccw-in-plan
    """
    return [((t[0], t[2], t[1])
             if ((verts[t[1]][0] - verts[t[0]][0])
                 * (verts[t[2]][1] - verts[t[0]][1])
                 - (verts[t[2]][0] - verts[t[0]][0])
                 * (verts[t[1]][1] - verts[t[0]][1])) < 0 else t)
            for t in tris]


def _lazy_wall_hit(blocking):
    """A wall-slab sampler that indexes the blocking soup on first use.

    See: docs/commentary/tes5_import_navmesh.md#the-wall-sampler-is-lazy
    """
    cache = []

    def wall_hit(*a, **kw):
        """True if a wall stands in the actor slab; builds the index once."""
        if not cache:
            cache.append(corridor_grow.wall_slab_sampler(blocking))
        return cache[0](*a, **kw)

    return wall_hit


def _outline_ground_ok(blocking, walkable):
    """f(a, v, b) -> True when the notch a-v-b may be cut straight along a-b.

    See: docs/commentary/tes5_import_navmesh.md#concave-notches-straightened-against-collision
    """
    wall_hit = _lazy_wall_hit(blocking)
    walk = corridor_grow.walkable_sampler(walkable)

    def ok(a, v, b):
        """No wall in the actor band over the notch, floor all along the chord."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        run = math.hypot(dx, dy)
        if run < 1e-6:
            return False
        tx, ty = dx / run, dy / run
        off = (v[0] - a[0]) * -ty + (v[1] - a[1]) * tx
        ux, uy = (-ty, tx) if off >= 0 else (ty, -tx)
        depth = 0.5 * abs(off) + 1.0
        cx = 0.5 * (a[0] + b[0]) + ux * depth
        cy = 0.5 * (a[1] + b[1]) + uy * depth
        z_lo = min(a[2], b[2], v[2]) + params.RIBBON_GROW_SLAB_Z_BOTTOM
        z_hi = max(a[2], b[2], v[2]) + params.AGENT_HEIGHT
        if wall_hit(cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth,
                    half_w=0.5 * run):
            return False
        n = max(1, int(run // params.RIBBON_GROW_STEP))
        for s in range(n + 1):
            t = s / n
            z = a[2] + (b[2] - a[2]) * t
            got = walk(a[0] + dx * t, a[1] + dy * t, z)
            if got is None or abs(got - z) > params.MAX_CLIMB:
                return False
        return True

    return ok


def _land_slit_ok(blocking, land):
    """f(polygon) -> True when no wall stands anywhere along a terrain gap.

    See: docs/commentary/tes5_import_navmesh.md#land-slits-are-closed
    """
    if land is None:
        return None
    wall_hit = _lazy_wall_hit(blocking)

    def ok(piece):
        """Probe the gap's outline every RIBBON_GROW_STEP at actor height."""
        ring = piece.exterior
        n = max(1, int(ring.length // params.RIBBON_GROW_STEP))
        for k in range(n):
            pt = ring.interpolate(k / n, normalized=True)
            z = land.z(pt.x, pt.y)
            if wall_hit(pt.x, pt.y, 1.0, 0.0, 0.0, 1.0,
                        z + params.RIBBON_GROW_SLAB_Z_BOTTOM,
                        z + params.AGENT_HEIGHT, params.RIBBON_GROW_STEP,
                        half_w=params.RIBBON_GROW_STEP):
                return False
        return True

    return ok


def _ledge_reach(blocking, walkable):
    """f(x, y, z, dx, dy, limit) -> (floor run, wall_first) from a ledge lip.

    Marches along (dx, dy) with the width-grow's wall slab and floor test:
    the run is how far the floor at z continues; wall_first is True when a
    wall taller than a step stands in the way before the floor ends.
    See: docs/commentary/tes5_import_navmesh.md#ledge-lip-is-pushed-to-the-edge
    """
    wall_hit = _lazy_wall_hit(blocking)
    walk = corridor_grow.walkable_sampler(walkable)

    def reach(x, y, z, dx, dy, limit):
        """Floor run and wall verdict along one direction from (x, y, z)."""
        step = LIP_MARCH_STEP
        d = 0.0
        while d < limit:
            nd = d + step
            mid = 0.5 * (d + nd)
            if wall_hit(x + dx * mid, y + dy * mid, dx, dy, -dy, dx,
                        z + params.RIBBON_GROW_SLAB_Z_BOTTOM,
                        z + params.AGENT_HEIGHT,
                        0.5 * step + params.RIBBON_GROW_SLAB_DEPTH):
                return d, True
            s = walk(x + dx * nd, y + dy * nd, z)
            if s is None or abs(s - z) > params.MAX_CLIMB:
                return d, False
            d = nd
        return d, False

    return reach


#: March step when pushing a ledge lip out to the floor's edge.
LIP_MARCH_STEP = 4.0


def build_corridors(refr_recs, base_model_by_fid, get_collision, nodes, edges,
                    land_rec=None, origin_x=0.0, origin_y=0.0, doors=None,
                    door_bases=None):
    """Phase-1 corridor navmesh for one cell: (verts, tris, ledges) lists.

    doors: [(x, y, z, rot_z, is_teleport, width), ...] pivot-corrected door
    centers.  door_bases: low-24 DOOR base FormIDs contributing no collision.
    ledges: [(upper_tri, lower_tri, drop), ...] for NVNM Ledge Up/Down links.
    See: docs/commentary/tes5_import_navmesh.md#ribbon-construction
    """
    if not nodes or not edges:
        return [], [], []
    from . import corridor_clean, corridor_union

    walkable, blocking, land_from = _cell_geometry(
        refr_recs, base_model_by_fid, get_collision, land_rec,
        origin_x, origin_y, door_bases)
    sample = _surface_sampler(walkable)
    node_z = [_snap_node_z(sample, nodes[i][0], nodes[i][1], nodes[i][2])
              for i in range(len(nodes))]

    corridors = _build_corridor_strips(nodes, edges, node_z,
                                       blocking=blocking, walkable=walkable,
                                       sample=sample, land_from=land_from,
                                       land=world.land_field(
                                           land_rec, origin_x, origin_y))
    cell_clip = None
    if land_rec is not None:
        cell_clip = (origin_x, origin_y, origin_x + 4096.0, origin_y + 4096.0)

    door_list = list(doors or ())
    door_strips, door_edges, door_pins = _door_geometry(
        corridors, door_list, nodes, edges, _lazy_wall_hit(blocking),
        cell_clip)

    verts, tris = corridor_union.build_union_mesh(
        corridors, extra_strips=door_strips, door_edges=door_edges,
        cell_bounds=cell_clip, wall_cut=None,
        slit_ok=_land_slit_ok(blocking, world.land_field(
            land_rec, origin_x, origin_y)))
    if not tris:
        return [], [], []

    door_xy = [(x, y, z) for (x, y, z, r, tp, w) in door_list]
    pin_xy = (list(door_xy) + door_pins
              + _centerline_samples(nodes, edges, node_z))
    verts, tris, ledge_marks = corridor_clean.finalize(
        verts, tris, cs=(params.CS_EXTERIOR if land_rec is not None
                         else params.CS),
        doors=door_xy, cell_bounds=cell_clip, pin_xy=pin_xy,
        door_pins=door_pins,
        node_pins=[(nodes[i][0], nodes[i][1]) for i in range(len(nodes))],
        ground_ok=_outline_ground_ok(blocking, walkable),
        ledge_reach=_ledge_reach(blocking, walkable), surface=sample)

    verts = [tuple(float(c) for c in v) for v in verts]
    tris = [tuple(int(i) for i in t) for t in tris]
    tris = _drop_attach_scraps(verts, tris, door_xy)
    tris = corridor_clean._drop_degenerate_guarded(verts, tris)
    tris = _ccw_in_plan(verts, tris)

    ledges = corridor_clean._resolve_ledges(verts, tris, ledge_marks)
    return (verts, tris,
            [(int(a), int(b), float(d)) for (a, b, d) in ledges])
