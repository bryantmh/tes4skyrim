"""Sheet separation, welding and stitching: turning parts into ONE surface.

A cell's ribbons are grouped into storeys, each storey is triangulated on its
own, and the results are then welded, T-junction-split and stitched at the
pathgrid nodes so the sheets share real edges instead of merely touching.

See: docs/commentary/tes5_import_navmesh.md#sheet-stitching-runs-to-convergence
"""

import math
from itertools import product


from . import params
from .union_geom import (
    STOREY_GAP_Z, WALL_SLOPE_COS, _height_on, _land_of, _tri_area,
    _tri_components, _tri_edges, _tri_span,
)

#: Plan/Z tolerances for sealing a T-junction crack.
TSPLIT_TOL = 2.0
TSPLIT_CRACK_TOL = 6.0
TSPLIT_Z_TOL = 12.0


def _plan_polys(verts, tris, min_area):
    """(polys, geoms, gmap) plan polygons per triangle, tiny ones skipped."""
    from shapely.geometry import Polygon
    polys = [None] * len(tris)
    geoms, gmap = [], []
    for ti, t in enumerate(tris):
        pa, pb, pc = verts[t[0]], verts[t[1]], verts[t[2]]
        try:
            pg = Polygon([(pa[0], pa[1]), (pb[0], pb[1]), (pc[0], pc[1])])
        except Exception:
            continue
        if not pg.is_valid or pg.area < min_area:
            continue
        polys[ti] = pg
        gmap.append(ti)
        geoms.append(pg)
    return polys, geoms, gmap


def _z_at(verts, t, x, y):
    """Height of triangle `t` at (x, y), by barycentric interpolation."""
    (ax, ay, az) = verts[t[0]]
    (bx, by, bz) = verts[t[1]]
    (cx, cy, cz) = verts[t[2]]
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-9:
        return (az + bz + cz) / 3.0
    l0 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d
    l1 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d
    return l0 * az + l1 * bz + (1.0 - l0 - l1) * cz


def _stacked_pairs(verts, tris, polys, geoms, gmap):
    """(-area, ti, tj) for every pair overlapping on the SAME surface.

    Within 40u at the overlap: the tightest gap two REAL storeys ever have is
    STOREY_GAP_Z, so anything closer is a duplicate rather than a floor above.
    """
    import shapely
    tree = shapely.STRtree(geoms)
    left, right = tree.query(geoms, predicate='intersects')
    cand = [(gmap[a], gmap[b]) for a, b in zip(left.tolist(), right.tolist())
            if gmap[a] < gmap[b]
            and not set(tris[gmap[a]]) & set(tris[gmap[b]])]
    if not cand:
        return []
    inters = shapely.intersection([polys[ti] for ti, _tj in cand],
                                  [polys[tj] for _ti, tj in cand])
    pairs = []
    for (ti, tj), inter, area in zip(cand, inters,
                                     shapely.area(inters).tolist()):
        if area <= 4.0:
            continue
        cx, cy = inter.centroid.x, inter.centroid.y
        if abs(_z_at(verts, tris[ti], cx, cy)
               - _z_at(verts, tris[tj], cx, cy)) > 40.0:
            continue
        pairs.append((-area, ti, tj))
    return pairs


def _adjacency_probe(tris, alive):
    """A callable answering "can `ti` be dropped without severing neighbours?"."""
    edge_tris = {}
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            edge_tris.setdefault((a, b) if a < b else (b, a), []).append(ti)

    def neighbours(ti):
        """Live triangles sharing an edge with `ti`."""
        out = []
        for k in range(3):
            a, b = tris[ti][k], tris[ti][(k + 1) % 3]
            for tj in edge_tris.get((a, b) if a < b else (b, a), ()):
                if tj != ti and alive[tj]:
                    out.append(tj)
        return out

    def removable(ti):
        """True when dropping `ti` leaves its neighbours connected."""
        nbrs = neighbours(ti)
        if len(nbrs) <= 1:
            return True
        target = set(nbrs[1:])
        seen = {nbrs[0], ti}
        queue = [nbrs[0]]
        fuel = 128
        while queue and target and fuel:
            fuel -= 1
            cur = queue.pop()
            for tj in neighbours(cur):
                if tj in seen:
                    continue
                seen.add(tj)
                target.discard(tj)
                queue.append(tj)
        return not target

    return removable


def _destack(verts, tris):
    """Drop the lower of two triangles stacked on the SAME surface.

    Adjacency-based, not a plan scan: only triangles that actually share
    edges can be duplicates of one another.

    See: docs/commentary/tes5_import_navmesh.md#destack-and-bridge-overlap-details
    """
    if len(tris) < 2:
        return tris
    tris = [tuple(t) for t in tris]
    polys, geoms, gmap = _plan_polys(verts, tris, 4.0)
    if not geoms:
        return tris
    pairs = _stacked_pairs(verts, tris, polys, geoms, gmap)
    if not pairs:
        return tris
    alive = [True] * len(tris)
    removable = _adjacency_probe(tris, alive)
    pairs.sort()
    for (_na, ti, tj) in pairs:
        if not alive[ti] or not alive[tj]:
            continue
        small, big = ((ti, tj) if polys[ti].area <= polys[tj].area
                      else (tj, ti))
        for victim in (small, big):
            if removable(victim):
                alive[victim] = False
                break
    return [t for ti, t in enumerate(tris) if alive[ti]]


#: Bucket size for the walked-pathgrid sample grid.
PG_CELL = 128.0


def _pathgrid_samples(strips):
    """Walked pathgrid points bucketed on a PG_CELL grid."""
    grid = {}
    for s in (strips or ()):
        ax, ay, _az = s['a']
        bx, by, _bz = s['b']
        run = math.hypot(bx - ax, by - ay)
        n = max(1, int(run // 32.0))
        for k in range(n + 1):
            t = k / n
            px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            grid.setdefault((int(px // PG_CELL), int(py // PG_CELL)),
                            []).append((px, py, _height_on(s, px, py)))
    return grid


def _carries_pathgrid(verts, t, pg_grid):
    """True when a walked pathgrid line crosses this triangle."""
    (ax, ay, az) = verts[t[0]]
    (bx, by, bz) = verts[t[1]]
    (cx, cy, cz) = verts[t[2]]
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-9:
        return False
    xs, ys = (ax, bx, cx), (ay, by, cy)
    for gx in range(int(min(xs) // PG_CELL), int(max(xs) // PG_CELL) + 1):
        for gy in range(int(min(ys) // PG_CELL), int(max(ys) // PG_CELL) + 1):
            for (px, py, pz) in pg_grid.get((gx, gy), ()):
                l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
                l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                l2 = 1.0 - l0 - l1
                if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
                    continue
                if abs(l0 * az + l1 * bz + l2 * cz - pz) <= 80.0:
                    return True
    return False


def _steep_triangles(verts, tris, pg_grid, land=None):
    """(cos_slope, index) for every wall-steep triangle, pathgrid ones spared.

    A triangle whose corners all lie on `land` is spared too.
    See: docs/commentary/tes5_import_navmesh.md#land-triangles-are-never-walls
    """
    steep = []
    for ti, t in enumerate(tris):
        pa, pb, pc = verts[t[0]], verts[t[1]], verts[t[2]]
        if land is not None and all(abs(p[2] - land.z(p[0], p[1])) < 0.5
                                    for p in (pa, pb, pc)):
            continue
        area2 = abs((pb[0] - pa[0]) * (pc[1] - pa[1]) -
                    (pb[1] - pa[1]) * (pc[0] - pa[0]))
        ux, uy, uz = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        wx, wy, wz = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        nx3 = uy * wz - uz * wy
        ny3 = uz * wx - ux * wz
        nz3 = ux * wy - uy * wx
        area3 = math.sqrt(nx3 * nx3 + ny3 * ny3 + nz3 * nz3)
        if area3 <= 1e-9 or area2 / area3 >= WALL_SLOPE_COS:
            continue
        if pg_grid and _carries_pathgrid(verts, t, pg_grid):
            continue
        steep.append((area2 / area3, ti))
    return steep


def _drop_walls(verts, tris, strips=None):
    """Remove near-vertical triangles, but only where safe.

    A triangle steeper than WALL_SLOPE_COS is a wall an actor cannot stand
    on.  It is dropped ONLY when its neighbours stay connected without it:
    on jagged cave ground a steep triangle is sometimes the only link
    between two ledges.

    See: docs/commentary/tes5_import_navmesh.md#union-geometry-constants
    """
    if not tris:
        return tris
    pg_grid = _pathgrid_samples(strips)
    steep = _steep_triangles(verts, tris, pg_grid, _land_of(strips or ()))
    if not steep:
        return tris
    alive = [True] * len(tris)
    removable = _adjacency_probe(tris, alive)
    steep.sort()
    for (_cos, ti) in steep:
        if removable(ti):
            alive[ti] = False
    return [t for ti, t in enumerate(tris) if alive[ti]]


def _near_vertices(verts, buckets, cell, nx, ny, r):
    """Vertices within `r` of the node, from the 3x3 bucket neighbourhood.

    The disc reaches one ribbon width past the node's own half-width: two
    sheets can each stop short of the node, and a tighter disc misses the
    neighbour sheet entirely.
    """
    gx, gy = int(nx // cell), int(ny // cell)
    near = []
    for ddx in (-1, 0, 1):
        for ddy in (-1, 0, 1):
            for i in buckets.get((gx + ddx, gy + ddy), ()):
                if math.hypot(verts[i][0] - nx, verts[i][1] - ny) <= r:
                    near.append(i)
    near.sort()
    return near


def _storey_bands(verts, near):
    """`near` split into bands no more than one storey gap apart in Z.

    Banding is on the STOREY gap, not one step: two corridors meeting at a
    node are the same junction even when the sheets left them a step apart.
    """
    near = sorted(near, key=lambda i: (verts[i][2], i))
    bands = [[near[0]]]
    for i in near[1:]:
        if verts[i][2] - verts[bands[-1][-1]][2] <= STOREY_GAP_Z:
            bands[-1].append(i)
        else:
            bands.append([i])
    return bands


def _weld_band(verts, vcomp, remap, band, nx, ny):
    """Weld one vertex per foreign component onto the keeper; True if any did.

    The CLOSEST cross pair is welded, never everything onto the node-nearest
    vertex, and the drag is bounded to a claim seam's width.
    """
    comps_of = {}
    for i in band:
        for cmp in vcomp.get(i, ()):
            comps_of.setdefault(cmp, []).append(i)
    if len(band) < 2 or len(comps_of) < 2:
        return False
    keep = min(band, key=lambda i: (
        math.hypot(verts[i][0] - nx, verts[i][1] - ny), i))
    keep_comps = vcomp.get(keep, set())
    anchor = [i for i in band if vcomp.get(i, set()) & keep_comps]
    welded = False
    for cmp in sorted(comps_of):
        if cmp in keep_comps:
            continue
        best = None
        for i in comps_of[cmp]:
            for j in anchor:
                d2 = ((verts[i][0] - verts[j][0]) ** 2
                      + (verts[i][1] - verts[j][1]) ** 2
                      + (verts[i][2] - verts[j][2]) ** 2)
                if best is None or (d2, i, j) < best:
                    best = (d2, i, j)
        if best is None:
            continue
        _d2, i, j = best
        if _d2 > params.RIBBON_HALF_WIDTH ** 2:
            continue
        if i != j:
            remap[i] = j
            welded = True
    return welded


def _merge_state(verts, tris, resolve, grid_r):
    """(comp-per-tri, vertex -> set-of-comps, xy buckets, cell).

    Rebuilt only when a weld actually happened: doing it per node made
    this the hottest function in the whole navmesh build.

    See: docs/commentary/tes5_import_navmesh.md#pathgrid-node-merge-is-memoised
    """
    comp = _tri_components(tris)
    vcomp = {}
    for ti, t in enumerate(tris):
        for i in t:
            vcomp.setdefault(resolve(i), set()).add(comp[ti])
    cell = max(grid_r, 1.0)
    buckets = {}
    for i in vcomp:
        v = verts[i]
        buckets.setdefault((int(v[0] // cell), int(v[1] // cell)),
                           []).append(i)
    return comp, vcomp, buckets, cell


def _merge_at_pathgrid_nodes(verts, tris, node_pts, node_half):
    """Force corridors meeting at a pathgrid node to share a vertex.

    Welds ONE vertex per foreign component -- the closest -- onto the
    keeper, with the drag bounded to a claim seam's width.  Component state
    is cached and rebuilt only when a weld actually happens.

    See: docs/commentary/tes5_import_navmesh.md#pathgrid-node-merge-is-memoised
    """
    if not tris or not node_pts:
        return verts, tris
    verts = [list(v) for v in verts]
    remap = list(range(len(verts)))

    def resolve(i):
        """Root of vertex `i` in the weld union-find."""
        while remap[i] != i:
            remap[i] = remap[remap[i]]
            i = remap[i]
        return i

    grid_r = max([float(node_half.get(ni, 0.0)) for ni in node_pts]
                 + [params.RIBBON_HALF_WIDTH]) + params.RIBBON_HALF_WIDTH
    cache = {}

    def state():
        """The memoised component/bucket index of the current soup."""
        if 'comp' not in cache:
            cache['comp'] = _merge_state(verts, tris, resolve, grid_r)
        return cache['comp']

    def collapse(soup):
        """`soup` re-indexed through the union-find, dropping collapsed tris."""
        return [t for t in ((resolve(a), resolve(b), resolve(c))
                            for (a, b, c) in soup) if len(set(t)) == 3]

    for ni, (nx, ny) in sorted(node_pts.items()):
        r = (max(float(node_half.get(ni, 0.0)), params.RIBBON_HALF_WIDTH)
             + params.RIBBON_HALF_WIDTH)
        _comp, vcomp, buckets, cell = state()
        near = _near_vertices(verts, buckets, cell, nx, ny, r)
        if len(near) < 2:
            continue
        welded_any = False
        for band in _storey_bands(verts, near):
            if _weld_band(verts, vcomp, remap, band, nx, ny):
                welded_any = True
        if welded_any:
            tris = collapse(tris)
            cache.clear()
    return verts, collapse(tris)


def _fuse_coincident(verts, tris):
    """`tris` with vertices at identical rounded positions merged.

    Passes before this mint midpoints independently on both sides of a seam,
    so two components can touch at IDENTICAL positions under different indices.
    """
    pos, fuse = {}, {}
    for i in sorted({i for t in tris for i in t}):
        key = (round(verts[i][0], 1), round(verts[i][1], 1),
               round(verts[i][2], 1))
        j = pos.get(key)
        if j is None:
            pos[key] = i
        else:
            fuse[i] = j
    if not fuse:
        return tris
    return [t for t in (tuple(fuse.get(k, k) for k in t) for t in tris)
            if len(set(t)) == 3]


def _border_index(tris):
    """(counts, border) edge-use counts and the once-used (border) edges."""
    counts = {}
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    return counts, [e for e, c in counts.items() if c == 1]


def _junction_index(tris, counts):
    """(comp, vcomp, vtris, inc, owner) describing the current soup."""
    comp = _tri_components(tris)
    vcomp, vtris, inc, owner = {}, {}, {}, {}
    for ti, t in enumerate(tris):
        for i in t:
            vcomp.setdefault(i, set()).add(comp[ti])
            vtris.setdefault(i, []).append(ti)
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            if counts.get(key) == 1:
                owner[key] = comp[ti]
                inc.setdefault(a, []).append(key)
                inc.setdefault(b, []).append(key)
    return comp, vcomp, vtris, inc, owner


def _open_fan(verts, tris, state, i, c):
    """Split a closed fan at vertex `i` so component `c` presents a border.

    A component may USE the junction while presenting no BORDER edge: the
    other surface arrives into the MIDDLE of its fan.  A bridge cannot help
    there, so one fan triangle is split to create a border edge instead.
    """
    comp, vtris, counts, owner, replaced, by_comp = state
    cand_t = [ti for ti in vtris.get(i, ())
              if comp[ti] == c and ti not in replaced]
    cand_t.sort(key=lambda x: -_tri_area(verts, tris[x]))
    for ti in cand_t:
        t = tris[ti]
        k = t.index(i)
        p, q = t[(k + 1) % 3], t[(k + 2) % 3]
        okey = (p, q) if p < q else (q, p)
        nb = [tj for tj, tt in enumerate(tris)
              if tj != ti and tj not in replaced and p in tt and q in tt]
        if counts.get(okey, 0) > 1 and not nb:
            continue
        if (abs(verts[p][2] - verts[q][2])
                > max(params.MAX_CLIMB,
                      0.7 * math.hypot(verts[p][0] - verts[q][0],
                                       verts[p][1] - verts[q][1]))):
            continue
        if _tri_area(verts, t) < 4.0 * params.MIN_XY_FOOTPRINT:
            continue
        mid = len(verts)
        verts.append([0.5 * (verts[p][0] + verts[q][0]),
                      0.5 * (verts[p][1] + verts[q][1]),
                      0.5 * (verts[p][2] + verts[q][2])])
        replaced[ti] = [(i, p, mid), (i, mid, q)]
        for tj in nb[:1]:
            tt = tris[tj]
            opp = [x for x in tt if x != p and x != q]
            if len(opp) == 1:
                replaced[tj] = [(opp[0], p, mid), (opp[0], mid, q)]
        key = (i, mid) if i < mid else (mid, i)
        owner[key] = c
        counts[key] = 1
        by_comp.setdefault(c, []).append((i, key))
        counts.pop(okey, None)
        return True
    return False


def _bridge_ok(verts, tris, counts, extra, replaced, tri, allow_overlap):
    """True when this bridge triangle passes the span, slope and mesh guards."""
    if _tri_span(verts, tri) > 160.0:
        return False
    zs = [verts[x][2] for x in tri]
    plan_span = max(math.hypot(
        verts[tri[k]][0] - verts[tri[(k + 1) % 3]][0],
        verts[tri[k]][1] - verts[tri[(k + 1) % 3]][1]) for k in range(3))
    if max(zs) - min(zs) > max(params.MAX_CLIMB, 0.7 * plan_span):
        return False
    if any(counts.get(e, 0) + extra.get(e, 0) >= 2 for e in _tri_edges(tri)):
        return False
    return not _tri_overlaps_mesh(verts, tris, replaced, tri,
                                  eps=(250.0 if allow_overlap else 2.0))


def _try_bridge(verts, tris, counts, extra, replaced, added, pairs,
                allow_overlap):
    """Lay one bridge triangle between two components, if a candidate fits."""
    base_pairs, other_pairs = pairs
    for (i0, k0) in base_pairs:
        a0 = k0[0] if k0[1] == i0 else k0[1]
        for (i1, k1) in other_pairs:
            a1 = k1[0] if k1[1] == i1 else k1[1]
            tri = (a0, i0, a1)
            if len(set(tri)) < 3:
                tri = (a0, i0, i1)
            if len(set(tri)) < 3:
                continue
            if not _bridge_ok(verts, tris, counts, extra, replaced, tri,
                              allow_overlap):
                continue
            for e in _tri_edges(tri):
                extra[e] = extra.get(e, 0) + 1
            added.append(tri)
            return True
    return False


def _halve_long_border(verts, tris, state, c):
    """Split the shortest over-long border edge of component `c` at its midpoint.

    Decimation merges boundary vertices into edges well past the bridge cap, so
    both sides can offer only LONG border edges and every bridge is rejected.
    """
    _comp, vtris, counts, owner, replaced, by_comp = state
    best = None
    for (i, key) in by_comp.get(c, ()):
        far = key[0] if key[1] == i else key[1]
        ln = math.hypot(verts[far][0] - verts[i][0],
                        verts[far][1] - verts[i][1])
        if ln > 80.0 and (best is None or ln < best[0]):
            best = (ln, i, key)
    if best is None:
        return
    _ln, i, key = best
    p, q = key
    owner_t = next((ti for ti in vtris.get(p, ())
                    if ti not in replaced and q in tris[ti]), None)
    if owner_t is None:
        return
    t = tris[owner_t]
    for k in range(3):
        if {t[k], t[(k + 1) % 3]} != {p, q}:
            continue
        cc = t[(k + 2) % 3]
        m = len(verts)
        verts.append([0.5 * (verts[p][0] + verts[q][0]),
                      0.5 * (verts[p][1] + verts[q][1]),
                      0.5 * (verts[p][2] + verts[q][2])])
        replaced[owner_t] = [(t[k], m, cc), (m, t[(k + 1) % 3], cc)]
        counts.pop(key, None)
        for nk in ((min(t[k], m), max(t[k], m)),
                   (min(m, t[(k + 1) % 3]), max(m, t[(k + 1) % 3]))):
            counts[nk] = 1
            owner[nk] = c
        return


def _stitch_round(verts, tris, allow_overlap):
    """One stitch round; returns (tris, changed), changed=None when finished.

    Fuses coincident vertices, then for every junction either bridges the
    components, opens a closed fan, or halves an over-long border edge.
    """
    tris = _fuse_coincident(verts, tris)
    counts, border = _border_index(tris)
    if not border:
        return tris, None
    comp, vcomp, vtris, inc, owner = _junction_index(tris, counts)
    added, extra, replaced = [], {}, {}
    for i0v in sorted(i for i, cs in vcomp.items() if len(cs) > 1):
        by_comp = {}
        for key in inc.get(i0v, ()):
            by_comp.setdefault(owner[key], []).append((i0v, key))
        state = (comp, vtris, counts, owner, replaced, by_comp)
        for c in sorted(vcomp.get(i0v, ())):
            if c not in by_comp:
                _open_fan(verts, tris, state, i0v, c)
        if len(by_comp) < 2:
            continue
        order = sorted(by_comp)
        base_c = order[0]
        for other_c in order[1:]:
            if _try_bridge(verts, tris, counts, extra, replaced, added,
                           (by_comp[base_c], by_comp[other_c]), allow_overlap):
                continue
            for c in (base_c, other_c):
                _halve_long_border(verts, tris, state, c)
    if not added and not replaced:
        return tris, False
    out = []
    for ti, t in enumerate(tris):
        out.extend(replaced.get(ti, [t]))
    return [t for t in out + added if len(set(t)) == 3], True


def stitch_shared_nodes(verts, tris, stitch_nodes):
    """Give sheets meeting at a node REAL shared edges, not mere contact.

    Runs to convergence: each round either bridges a junction, opens a fan,
    or halves an over-long border edge.  Bridges are guarded on slope,
    manifoldness and overlap.

    See: docs/commentary/tes5_import_navmesh.md#sheet-stitching-runs-to-convergence
    """
    if not tris:
        return tris
    allow_overlap = False
    for _round in range(30):
        if _round >= 24:
            allow_overlap = True
        tris, changed = _stitch_round(verts, tris, allow_overlap)
        if changed is None:
            break
        if not changed:
            if allow_overlap:
                break
            allow_overlap = True
            continue
        allow_overlap = False
    return tris


def _tri_overlaps_mesh(verts, tris, replaced, cand, eps=2.0):
    """True when the candidate triangle overlaps existing mesh in plan.

    A triangle fan-opened this round still counts: its halves cover exactly
    the parent's footprint.

    See: docs/commentary/tes5_import_navmesh.md#destack-and-bridge-overlap-details
    """
    from shapely.geometry import Polygon as _OvP
    pa, pb, pc = verts[cand[0]], verts[cand[1]], verts[cand[2]]
    try:
        cp = _OvP([(pa[0], pa[1]), (pb[0], pb[1]), (pc[0], pc[1])])
    except Exception:
        return False
    if not cp.is_valid or cp.area <= eps:
        return False
    minx = min(pa[0], pb[0], pc[0]) - 1.0
    maxx = max(pa[0], pb[0], pc[0]) + 1.0
    miny = min(pa[1], pb[1], pc[1]) - 1.0
    maxy = max(pa[1], pb[1], pc[1]) + 1.0
    zlo = min(pa[2], pb[2], pc[2]) - 40.0
    zhi = max(pa[2], pb[2], pc[2]) + 40.0
    for ti, t in enumerate(tris):
        qa, qb, qc = verts[t[0]], verts[t[1]], verts[t[2]]
        if (max(qa[0], qb[0], qc[0]) < minx or min(qa[0], qb[0], qc[0]) > maxx
                or max(qa[1], qb[1], qc[1]) < miny
                or min(qa[1], qb[1], qc[1]) > maxy
                or max(qa[2], qb[2], qc[2]) < zlo
                or min(qa[2], qb[2], qc[2]) > zhi):
            continue
        try:
            tp = _OvP([(qa[0], qa[1]), (qb[0], qb[1]), (qc[0], qc[1])])
            if tp.is_valid and cp.intersection(tp).area > eps:
                return True
        except Exception:
            continue
    return False

#: Radius within which two sheet vertices fuse, and the same-emission XY cap.
WELD_R = 16.0
SAME_PART_WELD_XY = 4.0


def _weld_match(grid, v, isrc, gpos, has_src):
    """(rep index, risky) for the first vertex within WELD_R, else (None, False).

    `risky` marks a SAME-emission fuse that moves the vertex sideways past
    SAME_PART_WELD_XY -- provisional until proven not to overlap.  Vertices of
    DIFFERENT emissions on the same plan spot fuse across one step of height.
    See: docs/commentary/tes5_import_navmesh.md#cross-sheet-weld-spans-one-step
    """
    gx, gy, gz = gpos
    reach = int(params.MAX_CLIMB // WELD_R) + 1
    for (ddx, ddy, ddz) in product((-1, 0, 1), (-1, 0, 1),
                                   range(-reach, reach + 1)):
        for (j, p, jsrc) in grid.get((gx + ddx, gy + ddy,
                                      gz + ddz), ()):
            dxy2 = (p[0] - v[0]) ** 2 + (p[1] - v[1]) ** 2
            if (has_src and isrc != jsrc
                    and dxy2 <= SAME_PART_WELD_XY ** 2
                    and abs(p[2] - v[2]) <= params.MAX_CLIMB):
                return j, False
            d2 = dxy2 + (p[2] - v[2]) ** 2
            if d2 > WELD_R * WELD_R:
                continue
            risky = False
            if has_src and isrc == jsrc:
                dxy2 = ((p[0] - v[0]) ** 2 + (p[1] - v[1]) ** 2)
                risky = dxy2 > SAME_PART_WELD_XY ** 2
            return j, risky
    return None, False


def _weld_pass(verts, src):
    """(out, remap, provisional) for a 3D radius weld of `verts`.

    A same-emission fuse that moves a vertex sideways more than
    SAME_PART_WELD_XY is recorded as PROVISIONAL for the caller to verify.
    """
    cell = WELD_R
    grid, out, provisional = {}, [], {}
    remap = [0] * len(verts)
    for i, v in enumerate(verts):
        gx, gy, gz = (int(v[0] // cell), int(v[1] // cell), int(v[2] // cell))
        isrc = src[i] if src is not None else None
        got, risky = _weld_match(grid, v, isrc, (gx, gy, gz),
                                 src is not None)
        if got is None:
            got = len(out)
            out.append([float(v[0]), float(v[1]), float(v[2])])
            grid.setdefault((gx, gy, gz), []).append((got, out[got], isrc))
        elif risky:
            provisional.setdefault(got, []).append(i)
        remap[i] = got
    return out, remap, provisional


def _weld_tris(tris, remap):
    """`tris` re-indexed through `remap`, dropping any that collapsed."""
    welded = []
    for (a, b, c) in tris:
        a2, b2, c2 = remap[a], remap[b], remap[c]
        if a2 != b2 and b2 != c2 and a2 != c2:
            welded.append((a2, b2, c2))
    return welded


def _welded_polys(out, welded):
    """(polys, zranges, geoms, gmap) plan polygons for the welded soup."""
    from shapely.geometry import Polygon
    polys = [None] * len(welded)
    zr = [None] * len(welded)
    geoms, gmap = [], []
    for ti, t in enumerate(welded):
        pa, pb, pc = out[t[0]], out[t[1]], out[t[2]]
        try:
            pg = Polygon([(pa[0], pa[1]), (pb[0], pb[1]),
                          (pc[0], pc[1])])
        except Exception:
            continue
        if not pg.is_valid or pg.area <= 1e-6:
            continue
        polys[ti] = pg
        zr[ti] = (min(pa[2], pb[2], pc[2]), max(pa[2], pb[2], pc[2]))
        gmap.append(ti)
        geoms.append(pg)
    return polys, zr, geoms, gmap


def _overlap_probe(out, welded, provisional):
    """A callable answering "does triangle `ti` now overlap other mesh?".

    Suspects are tested against an STRtree of the whole soup; a per-call scan
    was 6 of Moranda02's 14 seconds.  The tree query is a BOX filter, so the
    cheap predicate runs before any clip.
    """
    from shapely import STRtree, intersects
    polys, zr, geoms, gmap = _welded_polys(out, welded)
    tree = STRtree(geoms) if geoms else None
    checked = {}

    def probe(ti):
        """True when triangle `ti` overlaps other mesh in plan."""
        got = checked.get(ti)
        if got is not None:
            return got
        cp = polys[ti]
        res = False
        if cp is not None and cp.area > 2.0 and tree is not None:
            zlo, zhi = zr[ti]
            cand = []
            for gi in tree.query(cp).tolist():
                tj = gmap[gi]
                if tj == ti or zr[tj][0] > zhi + 40.0 or zr[tj][1] < zlo - 40.0:
                    continue
                cand.append(tj)
            if cand:
                try:
                    hits = intersects(cp, [polys[tj] for tj in cand])
                except Exception:
                    hits = None
                if hits is not None:
                    cand = [tj for tj, h in zip(cand, hits.tolist()) if h]
            for tj in cand:
                try:
                    if cp.intersection(polys[tj]).area > 2.0:
                        res = True
                        break
                except Exception:
                    continue
        checked[ti] = res
        return res

    return probe


def _revert_bad_welds(verts, out, remap, welded, provisional):
    """Undo any provisional weld whose triangles now overlap; True if reverted."""
    vtris = {}
    for ti, t in enumerate(welded):
        for k in t:
            if k in provisional:
                vtris.setdefault(k, []).append(ti)
    probe = _overlap_probe(out, welded, provisional)
    bad = set()
    for rep in sorted(provisional):
        for ti in vtris.get(rep, ()):
            if probe(ti):
                bad.add(rep)
                break
    if not bad:
        return False
    for rep in sorted(bad):
        for i in provisional[rep]:
            ni = len(out)
            out.append([float(verts[i][0]), float(verts[i][1]),
                        float(verts[i][2])])
            remap[i] = ni
    return True


def _weld_sheets(verts, tris, src=None):
    """Fuse independently-triangulated sheets by 3D radius.

    Distance-based, never grid-snapped.  A same-emission fuse that moves a
    vertex sideways is provisional and is reverted if its triangles then
    overlap other mesh.

    See: docs/commentary/tes5_import_navmesh.md#sheet-weld-is-distance-based
    """
    if not verts:
        return verts, tris
    out, remap, provisional = _weld_pass(verts, src)
    welded = _weld_tris(tris, remap)
    if provisional and _revert_bad_welds(verts, out, remap, welded,
                                         provisional):
        welded = _weld_tris(tris, remap)
    return out, welded


def _hanging_on_edge(verts, grid, counts, bverts, edge, cell):
    """Vertices lying ON this border edge in plan, ordered along it.

    A boundary vertex gets the wider TSPLIT_CRACK_TOL: a crack has boundary on
    BOTH sides, so such a vertex is the far lip and splitting seals it.  An
    interior vertex that close is dense healthy mesh.
    """
    a, b = edge
    pa, pb = verts[a], verts[b]
    dx, dy, dz = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
    span2 = dx * dx + dy * dy
    if span2 < 1e-9:
        return []
    hits = []
    for gx in range(int(min(pa[0], pb[0]) // cell),
                    int(max(pa[0], pb[0]) // cell) + 1):
        for gy in range(int(min(pa[1], pb[1]) // cell),
                        int(max(pa[1], pb[1]) // cell) + 1):
            for i in grid.get((gx, gy), ()):
                if i == a or i == b:
                    continue
                p = verts[i]
                s = ((p[0] - pa[0]) * dx + (p[1] - pa[1]) * dy) / span2
                if not (0.02 < s < 0.98):
                    continue
                qx, qy, qz = pa[0] + dx * s, pa[1] + dy * s, pa[2] + dz * s
                r = TSPLIT_CRACK_TOL if i in bverts else TSPLIT_TOL
                if ((p[0] - qx) ** 2 + (p[1] - qy) ** 2 > r * r
                        or abs(p[2] - qz) > TSPLIT_Z_TOL):
                    continue
                ka = (a, i) if a < i else (i, a)
                kb = (i, b) if i < b else (b, i)
                if counts.get(ka, 0) <= 1 and counts.get(kb, 0) <= 1:
                    hits.append((s, i))
    hits.sort()
    return [i for (_s, i) in hits]


def _plan_splits(verts, tris):
    """{border edge: [vertices on it]} for every edge worth splitting."""
    counts = {}
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    border = {e for e, c in counts.items() if c == 1}
    if not border:
        return None
    bverts = {i for e in border for i in e}
    cell = 64.0
    grid = {}
    for i in {i for t in tris for i in t}:
        grid.setdefault((int(verts[i][0] // cell),
                         int(verts[i][1] // cell)), []).append(i)
    splits = {}
    for edge in border:
        got = _hanging_on_edge(verts, grid, counts, bverts, edge, cell)
        if got:
            splits[edge] = got
    return splits


def _apply_splits(tris, splits):
    """(tris, changed) with each split edge fanned from its opposite corner.

    The fan's new edges must not give any edge a 3rd owner, or make-manifold
    rips the extras out and deletes real coverage.
    """
    out, changed = [], False
    for t in tris:
        fan = None
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            ins = splits.get(key)
            if not ins:
                continue
            chain = list(ins) if (a, b) == key else list(reversed(ins))
            opp = t[(k + 2) % 3]
            seq = [a] + chain + [b]
            fan = [(seq[m], seq[m + 1], opp) for m in range(len(seq) - 1)]
            break
        if fan:
            out.extend(fan)
            changed = True
        else:
            out.append(t)
    return [t for t in out if len(set(t)) == 3], changed


def split_t_junctions(verts, tris):
    """Split a border edge another sheet's vertex lies on, sealing cracks.

    Projects in PLAN with a separate Z window, so a stair fold whose vertex
    sits on the edge in plan but off in Z is still sealed.

    See: docs/commentary/tes5_import_navmesh.md#t-junction-split-projects-in-plan
    """
    for _round in range(3):
        splits = _plan_splits(verts, tris)
        if not splits:
            break
        tris, changed = _apply_splits(tris, splits)
        if not changed:
            break
    return tris
