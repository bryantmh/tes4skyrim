"""Cleanup + validation for the corridor navmesh.

Two jobs, both mandatory for a mesh the engine can actually walk:

  * DROP DEGENERATE triangles (no XY footprint) — a vertical sliver covers no
    ground, so no actor stands on it, and a coplanar antiparallel pair reads as
    CK OPPOSITE_NORMALS.
  * MAKE MANIFOLD — NVNM adjacency (pgrd_to_navm._compute_adjacency) links an
    edge only when EXACTLY two triangles share it; an edge shared by three or
    more links NONE of them, silently disconnecting everything around it.  The
    ribbon body is manifold by construction, but the door-quad connection can
    lay a triangle over one already there, so this is the backstop.

The corridor model needs NOTHING else: no welding (ribbon vertices are shared
by construction), no stitching, no island cull (there are no stray scraps).
"""

import numpy as np

from . import params
from .clean_decimate import (
    _badness,
    _flip_pass as _flip_pass,
    _mesh_area,
    _pin_verts,
    _split_needles as _split_needles,
    decimate as decimate,
)
from .clean_validate import (
    _boundary_edges as _boundary_edges,
    _centroid as _centroid,
    _resolve_ledges as _resolve_ledges,
    components as components,
    edge_adjacency as edge_adjacency,
    find_ledge_links as find_ledge_links,
)


def _compact(verts, tris):
    """Drop vertices no triangle references; reindex.  Lists in, arrays out."""
    if not tris:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    used = sorted({int(i) for t in tris for i in t})
    remap = {old: new for new, old in enumerate(used)}
    nv = [list(verts[i]) for i in used]
    nt = [(remap[int(a)], remap[int(b)], remap[int(c)]) for (a, b, c) in tris]
    return (np.asarray(nv, dtype=float),
            np.asarray(nt, dtype=np.int32))


def _drop_degenerate(verts, tris):
    """Remove triangles whose XY footprint is below MIN_XY_FOOTPRINT."""
    kept = []
    for (a, b, c) in tris:
        va, vb, vc = verts[a], verts[b], verts[c]
        cross = ((vb[0] - va[0]) * (vc[1] - va[1]) -
                 (vb[1] - va[1]) * (vc[0] - va[0]))
        if abs(cross) * 0.5 >= params.MIN_XY_FOOTPRINT:
            kept.append((a, b, c))
    return kept


_WALKED_CELL = 128.0


def _walked_lookup(pin_xy):
    """Bucket walked pathgrid samples for point-in-triangle tests."""
    grid = {}
    for p in (pin_xy or ()):
        grid.setdefault((int(p[0] // _WALKED_CELL), int(p[1] // _WALKED_CELL)),
                        []).append((p[0], p[1], p[2] if len(p) > 2 else None))
    return grid


def _covers_any_sample(verts, t, grid):
    """Does triangle t contain a walked sample, at its own height?"""
    if not grid:
        return False
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (verts[i][:3] for i in t)
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-9:
        return False
    xs = (ax, bx, cx)
    ys = (ay, by, cy)
    for gx in range(int(min(xs) // _WALKED_CELL),
                    int(max(xs) // _WALKED_CELL) + 1):
        for gy in range(int(min(ys) // _WALKED_CELL),
                        int(max(ys) // _WALKED_CELL) + 1):
            for (px, py, pz) in grid.get((gx, gy), ()):
                l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
                l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                l2 = 1.0 - l0 - l1
                if l0 < -0.01 or l1 < -0.01 or l2 < -0.01:
                    continue
                if pz is None or abs(l0 * az + l1 * bz + l2 * cz
                                     - pz) <= params.MAX_CLIMB + 8.0:
                    return True
    return False


def _plan_area(verts, t):
    """Plan-view area of a triangle."""
    va, vb, vc = verts[t[0]], verts[t[1]], verts[t[2]]
    return abs((vb[0] - va[0]) * (vc[1] - va[1]) -
               (vb[1] - va[1]) * (vc[0] - va[0])) * 0.5


def _neighbour_counts(tris, owners):
    """How many DIFFERENT triangles each triangle shares an edge with."""
    nbr = [0] * len(tris)
    for ent in owners.values():
        if len(ent) == 2:
            nbr[ent[0]] += 1
            nbr[ent[1]] += 1
    return nbr


def _overshare_drops(verts, tris, owners, walked):
    """Triangles to shed so no edge keeps more than two owners.

    Ranked by walked line, then connectivity, then area.
    See: docs/commentary/tes5_import_navmesh.md#a-walked-line-outranks-size
    """
    nbr = _neighbour_counts(tris, owners)
    drop = set()
    for ent in owners.values():
        if len(ent) <= 2:
            continue
        live = [ti for ti in ent if ti not in drop]
        if len(live) <= 2:
            continue
        live.sort(key=lambda ti: (_covers_any_sample(verts, tris[ti], walked),
                                  nbr[ti], _plan_area(verts, tris[ti])),
                  reverse=True)
        drop.update(live[2:])
    return drop


def _drops_that_keep_connectivity(tris, drop):
    """The subset of `drop` that can go without adding a component."""
    safe = set()
    base = len(components(tris))
    for ti in sorted(drop):
        trial = [t for i, t in enumerate(tris) if i != ti and i not in safe]
        if len(components(trial)) <= base:
            safe.add(ti)
    return safe


def _make_manifold(verts, tris, pin_xy=None):
    """Shed triangles until no edge has more than two owners.

    `pin_xy` is the walked pathgrid samples, which outrank every other
    candidate on an edge.  A drop that would add a component is refused.
    See: docs/commentary/tes5_import_navmesh.md#a-walked-line-outranks-size
    """
    tris = [tuple(map(int, t)) for t in tris]
    walked = _walked_lookup(pin_xy)
    for _ in range(6):
        owners = _edge_owners(tris)
        drop = _overshare_drops(verts, tris, owners, walked)
        if not drop:
            break
        kept = [t for i, t in enumerate(tris) if i not in drop]
        if len(components(kept)) <= len(components(tris)):
            tris = kept
            continue
        safe = _drops_that_keep_connectivity(tris, drop)
        if not safe:
            break
        tris = [t for i, t in enumerate(tris) if i not in safe]
    return tris



def apply_welds(verts, tris, welds, tol):
    """Fuse each weld pair's nearest vertices into ONE index.

    A crack closes only when two triangles share a vertex index, so a hand
    weld cannot be expressed as a position to protect -- it is re-found here
    by matching each stored endpoint to the nearest generated vertex within
    `tol` and rewriting every triangle that referenced the first.

    See: docs/commentary/tes5_import_navmesh.md#weld-pins
    """
    if not welds or not verts:
        return verts, tris
    verts = [list(v) for v in verts]
    remap = {}
    for (src, dst) in welds:
        i = _nearest_vert(verts, src, tol)
        j = _nearest_vert(verts, dst, tol)
        if i is None or j is None or i == j:
            continue
        j = remap.get(j, j)
        remap[i] = j
        verts[i] = list(verts[j])
    if not remap:
        return verts, tris
    out = []
    for t in tris:
        a, b, c = (remap.get(int(k), int(k)) for k in t[:3])
        if a != b and b != c and a != c:
            out.append((a, b, c))
    return verts, out


def _nearest_vert(verts, pt, tol):
    """Index of the vertex nearest `pt` within `tol`, else None."""
    best = None
    best_d = tol * tol
    for i, v in enumerate(verts):
        d = ((v[0] - pt[0]) ** 2 + (v[1] - pt[1]) ** 2 + (v[2] - pt[2]) ** 2)
        if d <= best_d:
            best_d = d
            best = i
    return best


def finalize(verts, tris, cs=None, pinned=None, doors=None, cell_bounds=None,
             pin_xy=None, door_pins=None, node_pins=None, ground_ok=None,
             ledge_reach=None, surface=None, welds=None, weld_tol=8.0):
    """V1 cleanup: weld, guarantee manifold, drop stray islands, compact.

    Returns (verts, tris) as numpy arrays, plus ledges as MARKS (centroids),
    which the caller resolves with `_resolve_ledges` LAST because later passes
    shift indices.  Oracles: `ground_ok` straightens concave notches,
    `ledge_reach` pushes lips, `surface` heights a needle split.  `welds` are
    hand-recorded cracks, closed before anything reads adjacency.
    See: docs/commentary/tes5_import_navmesh.md#finalize-is-a-backstop
    See: docs/commentary/tes5_import_navmesh.md#weld-pins
    """
    verts, tris = _weld_coincident(verts, tris)
    verts, tris = apply_welds(verts, tris, welds, weld_tol)
    tris = _make_manifold(verts, tris, pin_xy=pin_xy)
    # Plan-degenerate triangles (three corners collinear in plan) are walls or
    # zero-width seam slivers — no actor can stand on them, and the CK flags
    # the coplanar-antiparallel case as OPPOSITE_NORMALS.  Drop them unless
    # removal would disconnect the mesh (a degenerate connector is still a
    # working NVNM adjacency, so severing it silently strands a region).
    tris = _drop_degenerate_guarded(verts, tris)
    # Only DOORS pin the decimator: a collapse at a threshold kills the Door
    # Triangle.  (pin_xy carries every pathgrid sample and is used by the island
    # pass below; pinning all of it would disable decimation everywhere.)
    # door_pins carries the reserved wedges' RING points (base corners, base
    # midpoint, apex) — decimation collapsing those left the attach nothing to
    # snap the door triangle to where the doorway outreaches its ribbon.
    _pins = ([(d[0], d[1], params.DECIMATE_PIN_CENTER_RADIUS)
              for d in (doors or ())]
             + [(p[0], p[1], params.DECIMATE_PIN_RADIUS)
                for p in (door_pins or ())]
             + [(n[0], n[1], params.DECIMATE_PIN_NODE_RADIUS)
                for n in (node_pins or ())])
    verts, tris = decimate(verts, tris, pinned_xy=_pins,
                           seam_bounds=cell_bounds, ground_ok=ground_ok,
                           surface=surface)
    # The "little bits around the outside": whatever badly-shaped small
    # triangles remain after collapses and flips sit where the outline simply
    # does not admit a good triangle — remove them rather than ship needles.
    # Culling exposes a NEW boundary ring whose triangles were shaped for the
    # old one, so re-smooth (collapse + flip) and cull once more with a small
    # residual budget; one interleave converges (measured), more just churns.
    tris = cull_boundary_slivers(verts, tris, pinned_xy=_pins, pin_xy=pin_xy,
                                 seam_bounds=cell_bounds,
                                 budget_frac=params.CULL_SLIVER_AREA_FRAC)
    # The second round only RE-SMOOTHS the freshly exposed boundary ring:
    # two collapse/flip rounds, no splits (a full second decimate measured
    # ~10% of a dense cell's whole build for marginal further gain).
    verts, tris = decimate(verts, tris, pinned_xy=_pins,
                           seam_bounds=cell_bounds, rounds=2,
                           allow_split=False, ground_ok=ground_ok)
    tris = cull_boundary_slivers(verts, tris, pinned_xy=_pins, pin_xy=pin_xy,
                                 seam_bounds=cell_bounds,
                                 budget_frac=params.CULL_SLIVER_AREA_FRAC
                                 * 0.4)
    # Drop OPEN FLAPS — triangles hanging off a floor's rim into a stairwell.
    # This runs here, after decimation, because that is where they become
    # visible: emission fans a whole floor rim back to one stair vertex below,
    # and while every spoke of that fan is present each shares its long edges
    # with the next, so nothing upstream sees a free edge.  Decimation merges
    # the spokes into one triangle and the fan's two outer edges become the
    # silhouette — which is exactly what makes the flap identifiable.  It runs
    # BEFORE find_ledge_links so a flap is never mistaken for a mezzanine an
    # actor is meant to drop off.
    tris = cull_open_flaps(verts, tris, pin_xy)
    ledge_pairs = find_ledge_links(verts, tris, reach=ledge_reach)
    # Identify each ledge triangle by its CENTROID, not its index: the passes
    # below drop and reorder both triangles and vertices, so an index captured
    # now is meaningless afterwards.  The centroid survives all of them.
    ledge_marks = [(_centroid(verts, tris[hi]), _centroid(verts, tris[lo]),
                    drop) for (hi, lo, drop) in ledge_pairs]
    tris = _make_manifold(verts, tris, pin_xy=pin_xy)
    verts, tris = _repair_junctions_after_cleanup(verts, tris)
    tris = _make_manifold(verts, tris, pin_xy=pin_xy)
    tris = _drop_unreachable_islands(verts, tris, doors, cell_bounds, pin_xy)
    verts, tris = _compact(verts, tris)
    return verts, tris, ledge_marks


def _repair_junctions_after_cleanup(verts, tris):
    """(verts, tris) with contact decimation created turned into shared edges.

    A collapse target is another vertex's position, so two components' rims can
    end up coincident under different indices; a needle split mints a vertex ON
    a border edge, leaving the sheet across it a parallel rim over the same
    ground.  Neither is visible upstream, where both passes already ran.
    See: docs/commentary/tes5_import_navmesh.md#split-at-the-apex-projection
    """
    from .corridor_union import split_t_junctions, stitch_shared_nodes
    verts = [list(map(float, v)) for v in verts]
    tris = stitch_shared_nodes(verts, [tuple(t) for t in tris], [])
    return verts, split_t_junctions(verts, tris)


#: How far inside a triangle a walked sample must fall to count as crossing it.
PG_INSIDE_FRAC = 0.05

#: Plan bucket size for the walked-sample containment tests.
PG_CELL = 128.0


def _edge_owners(tris):
    """edge -> the triangle indices using it."""
    edge_tris = {}
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            edge_tris.setdefault((a, b) if a < b else (b, a), []).append(ti)
    return edge_tris


def _live_neighbours(tris, edge_tris, alive, ti):
    """Live triangles sharing an edge with ti."""
    out = []
    t = tris[ti]
    for k in range(3):
        a, b = t[k], t[(k + 1) % 3]
        for tj in edge_tris.get((a, b) if a < b else (b, a), ()):
            if tj != ti and alive[tj]:
                out.append(tj)
    return out


def _neighbours_stay_linked(tris, edge_tris, alive, ti, fuel=128):
    """True if ti's neighbours remain mutually reachable without it."""
    nbrs = _live_neighbours(tris, edge_tris, alive, ti)
    if len(nbrs) <= 1:
        return True
    target = set(nbrs[1:])
    seen = {nbrs[0], ti}
    queue = [nbrs[0]]
    budget = fuel
    while queue and target and budget:
        budget -= 1
        cur = queue.pop()
        for tj in _live_neighbours(tris, edge_tris, alive, cur):
            if tj in seen:
                continue
            seen.add(tj)
            target.discard(tj)
            queue.append(tj)
    return not target


def _carries_pathgrid(verts, pg, t):
    """Does a walked sample land INSIDE this triangle, off its corners?

    See: docs/commentary/tes5_import_navmesh.md#a-sample-must-fall-strictly-inside
    """
    (ax, ay) = verts[t[0]][0], verts[t[0]][1]
    (bx, by) = verts[t[1]][0], verts[t[1]][1]
    (cx, cy) = verts[t[2]][0], verts[t[2]][1]
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-9:
        return False
    xs, ys = (ax, bx, cx), (ay, by, cy)
    for gx in range(int(min(xs) // PG_CELL), int(max(xs) // PG_CELL) + 1):
        for gy in range(int(min(ys) // PG_CELL), int(max(ys) // PG_CELL) + 1):
            for (px, py) in pg.get((gx, gy), ()):
                l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
                l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                if min(l0, l1, 1.0 - l0 - l1) >= PG_INSIDE_FRAC:
                    return True
    return False


def _flap_candidates(verts, tris, edge_tris, pg):
    """Triangles with two plunging free edges that no walked sample crosses.

    See: docs/commentary/tes5_import_navmesh.md#a-flap-is-topological-not-steep
    """
    from .corridor_union import FLAP_EDGE_DROP
    cands = []
    for ti, t in enumerate(tris):
        free = 0
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            if len(edge_tris.get(key, ())) != 1:
                continue
            if abs(verts[a][2] - verts[b][2]) >= FLAP_EDGE_DROP:
                free += 1
        if free >= 2 and not (pg and _carries_pathgrid(verts, pg, t)):
            cands.append(ti)
    return cands


def cull_open_flaps(verts, tris, pin_xy=None):
    """Drop triangles that hang off a floor's rim into open space.

    A flap is dropped only when its neighbours stay mutually reachable, so
    this can never sever a mesh.
    See: docs/commentary/tes5_import_navmesh.md#a-flap-is-topological-not-steep
    """
    if not tris:
        return tris
    pg = {}
    for p in (pin_xy or ()):
        pg.setdefault((int(p[0] // PG_CELL), int(p[1] // PG_CELL)),
                      []).append((p[0], p[1]))

    edge_tris = _edge_owners(tris)
    cands = _flap_candidates(verts, tris, edge_tris, pg)
    if not cands:
        return tris

    alive = [True] * len(tris)
    for ti in cands:
        if _neighbours_stay_linked(tris, edge_tris, alive, ti):
            alive[ti] = False
    return [t for ti, t in enumerate(tris) if alive[ti]]

def _drop_degenerate_guarded(verts, tris):
    """Drop plan-degenerate triangles, but never disconnect the mesh.

    A candidate that is the only route between its neighbours is kept, and
    candidate indices are re-derived after every removal.
    See: docs/commentary/tes5_import_navmesh.md#no-shape-cull-among-degenerates
    """
    tris = [tuple(map(int, t)) for t in tris]

    def _deg(t):
        """True if this triangle's plan footprint is below MIN_XY_FOOTPRINT."""
        va, vb, vc = verts[t[0]], verts[t[1]], verts[t[2]]
        cross = ((vb[0] - va[0]) * (vc[1] - va[1]) -
                 (vb[1] - va[1]) * (vc[0] - va[0]))
        return abs(cross) * 0.5 < params.MIN_XY_FOOTPRINT

    if not any(_deg(t) for t in tris):
        return tris
    edge_tris = _edge_owners(tris)
    alive = [True] * len(tris)
    for ti in range(len(tris)):
        if _deg(tris[ti]) and _neighbours_stay_linked(
                tris, edge_tris, alive, ti, fuel=256):
            alive[ti] = False
    return [t for ti, t in enumerate(tris) if alive[ti]]


def _bucket_pathgrid(pin_xy, cell):
    """Bucket walked-line samples by plan cell as (x, y, z-or-None, dir)."""
    pgrid = {}
    for p in (pin_xy or ()):
        px, py = p[0], p[1]
        pz = p[2] if len(p) > 2 else None
        pu = (p[3], p[4]) if len(p) > 4 else None
        pgrid.setdefault((int(px // cell), int(py // cell)),
                         []).append((px, py, pz, pu))
    return pgrid


def _bary_z(verts, tri, x, y):
    """Interpolated Z of `tri` at (x, y), or None if the point is outside."""
    (ax, ay, az) = verts[tri[0]]
    (bx, by, bz) = verts[tri[1]]
    (cx, cy, cz) = verts[tri[2]]
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-9:
        return None
    l0 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d
    l1 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d
    l2 = 1.0 - l0 - l1
    if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
        return None
    return l0 * az + l1 * bz + l2 * cz


def _bucket_tris(verts, tris, cell):
    """Bucket triangle indices by the plan cells their bboxes span."""
    grid = {}
    for tj, tt in enumerate(tris):
        xs = [verts[i][0] for i in tt]
        ys = [verts[i][1] for i in tt]
        for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
            for gy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                grid.setdefault((gx, gy), []).append(tj)
    return grid


def _edge_use(tris):
    """(edge -> owner count, edge -> triangle indices)."""
    counts, edge_tris = {}, {}
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
            edge_tris.setdefault(key, []).append(ti)
    return counts, edge_tris


class _Cull:
    """One sliver-cull round's mesh state and its live coverage queries."""

    PCELL = 128.0
    TCELL = 128.0

    def __init__(self, verts, tris, pin, pgrid, on_seam):
        """Index edges and plan buckets for this round's triangle list."""
        self.verts = verts
        self.tris = tris
        self.pin = pin
        self.pgrid = pgrid
        self.on_seam = on_seam
        self.counts, self.edge_tris = _edge_use(tris)
        self.alive = [True] * len(tris)
        self.tgrid = _bucket_tris(verts, tris, self.TCELL)

    def neighbours(self, ti):
        """Live triangles sharing an edge with ti."""
        out = []
        for k in range(3):
            a, b = self.tris[ti][k], self.tris[ti][(k + 1) % 3]
            for tj in self.edge_tris.get((a, b) if a < b else (b, a), ()):
                if tj != ti and self.alive[tj]:
                    out.append(tj)
        return out

    def boundary_edges(self, t):
        """How many of this triangle's edges are singly owned."""
        return sum(1 for k in range(3)
                   if self.counts.get((min(t[k], t[(k + 1) % 3]),
                                       max(t[k], t[(k + 1) % 3]))) == 1)

    def on_seam_edge(self, t):
        """True if a border edge of t lies on the exterior cell seam."""
        return any(self.counts.get((min(t[k], t[(k + 1) % 3]),
                                    max(t[k], t[(k + 1) % 3]))) == 1
                   and self.on_seam(t[k]) and self.on_seam(t[(k + 1) % 3])
                   for k in range(3))

    def samples_in(self, t):
        """Walked-line samples inside triangle t, as (x, y, z-or-None)."""
        xs = [self.verts[i][0] for i in t]
        ys = [self.verts[i][1] for i in t]
        (ax, ay), (bx, by), (cx, cy) = ((self.verts[i][0], self.verts[i][1])
                                        for i in t)
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-9:
            return []
        out = []
        for gx in range(int(min(xs) // self.PCELL),
                        int(max(xs) // self.PCELL) + 1):
            for gy in range(int(min(ys) // self.PCELL),
                            int(max(ys) // self.PCELL) + 1):
                for (px, py, pz, _pu) in self.pgrid.get((gx, gy), ()):
                    l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
                    l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                    if l0 >= -0.02 and l1 >= -0.02 and (1 - l0 - l1) >= -0.02:
                        out.append((px, py, pz))
        return out

    def z_at(self, tj, x, y):
        """Interpolated height of live triangle tj at (x, y), or None."""
        return _bary_z(self.verts, self.tris[tj], x, y)

    def samples_still_covered(self, ti, samples):
        """Would every walked sample in ti stay covered without it?

        See: docs/commentary/tes5_import_navmesh.md#the-walked-line-is-sacrosanct
        """
        for (px, py, pz) in samples:
            zref = pz if pz is not None else self.z_at(ti, px, py)
            ok = False
            for tj in self.tgrid.get((int(px // self.TCELL),
                                      int(py // self.TCELL)), ()):
                if tj == ti or not self.alive[tj]:
                    continue
                z = self.z_at(tj, px, py)
                if z is not None and (zref is None or abs(z - zref)
                                      <= params.MAX_CLIMB + 8.0):
                    ok = True
                    break
            if not ok:
                return False
        return True

    def cover_z(self, x, y, zc, skip=None):
        """Height of the live triangle covering (x, y) nearest zc, or None."""
        best = None
        for tj in self.tgrid.get((int(x // self.TCELL),
                                  int(y // self.TCELL)), ()):
            if tj == skip or not self.alive[tj]:
                continue
            z = self.z_at(tj, x, y)
            if z is not None and abs(z - zc) <= 40.0:
                if best is None or abs(z - zc) < abs(best - zc):
                    best = z
        return best

    def width_at(self, px, py, pz, pu):
        """Continuous covered cross-width at a directed line sample."""
        wx_, wy_ = -pu[1], pu[0]
        width = 4.0
        for sgn in (1, -1):
            zc = pz
            for s in range(1, 16):
                z = self.cover_z(px + sgn * wx_ * s * 4.0,
                                 py + sgn * wy_ * s * 4.0, zc)
                if z is None:
                    break
                zc = z
                width += 4.0
        return width

    def narrows_corridor(self, t):
        """Would culling t squeeze a walked line under the width contract?

        See: docs/commentary/tes5_import_navmesh.md#corridor-width-is-a-contract
        """
        r = 44.0
        pts = [self.verts[i] for i in t]
        cx = sum(p[0] for p in pts) / 3.0
        cy = sum(p[1] for p in pts) / 3.0
        cz = sum(p[2] for p in pts) / 3.0
        for (px_, py_, pz_) in (pts + [(cx, cy, cz)]):
            if self._tight_near(px_, py_, pz_, r):
                return True
        return False

    def _tight_near(self, px_, py_, pz_, r):
        """Is any directed sample within r of this point already tight?"""
        for gx in range(int((px_ - r) // self.PCELL),
                        int((px_ + r) // self.PCELL) + 1):
            for gy in range(int((py_ - r) // self.PCELL),
                            int((py_ + r) // self.PCELL) + 1):
                for (qx, qy, qz, qu) in self.pgrid.get((gx, gy), ()):
                    if qu is None:
                        continue
                    if ((qx - px_) ** 2 + (qy - py_) ** 2 > r * r
                            or (qz is not None and abs(qz - pz_) > 60.0)):
                        continue
                    if self.width_at(qx, qy,
                                     qz if qz is not None else pz_,
                                     qu) < 56.0:
                        return True
        return False

    def bridges(self, ti):
        """True if removing ti would leave its neighbours mutually unreachable.

        See: docs/commentary/tes5_import_navmesh.md#a-cull-can-never-disconnect
        """
        nbrs = self.neighbours(ti)
        if len(nbrs) <= 1:
            return False
        target = set(nbrs[1:])
        seen_t = {nbrs[0], ti}
        queue = [nbrs[0]]
        fuel = 128
        while queue and target and fuel:
            fuel -= 1
            cur = queue.pop()
            for tj in self.neighbours(cur):
                if tj in seen_t:
                    continue
                seen_t.add(tj)
                target.discard(tj)
                queue.append(tj)
        return bool(target)

    def survivors(self):
        """The triangles still alive after this round."""
        return [t for ti, t in enumerate(self.tris) if self.alive[ti]]


def _sliver_candidates(st):
    """Cullable fringe triangles, worst shape first, with their samples.

    See: docs/commentary/tes5_import_navmesh.md#fringe-needs-two-boundary-edges
    """
    cands = []
    for ti, t in enumerate(st.tris):
        if st.boundary_edges(t) < 2:
            continue
        ratio = _badness(st.verts, t)
        p, q, r = st.verts[t[0]], st.verts[t[1]], st.verts[t[2]]
        area = abs((q[0] - p[0]) * (r[1] - p[1])
                   - (q[1] - p[1]) * (r[0] - p[0])) * 0.5
        if not ((ratio > 1.0 and area < params.CULL_SLIVER_MAX_AREA)
                or area < params.MIN_TRI_AREA):
            continue
        if any(v in st.pin for v in t) or st.on_seam_edge(t):
            continue
        cands.append((-ratio, ti, area, st.samples_in(t)))
    cands.sort(key=lambda c: (c[0], c[1]))
    return cands


def _cull_round(verts, tris, pin, pgrid, on_seam, removed_area, budget):
    """One cull pass; returns (tris, area removed, whether anything went)."""
    st = _Cull(verts, tris, pin, pgrid, on_seam)
    changed = False
    for (_r, ti, area, samples) in _sliver_candidates(st):
        if not st.alive[ti] or removed_area + area > budget:
            continue
        if samples and not st.samples_still_covered(ti, samples):
            continue
        if st.narrows_corridor(st.tris[ti]) or st.bridges(ti):
            continue
        st.alive[ti] = False
        removed_area += area
        changed = True
    return st.survivors(), removed_area, changed


def cull_boundary_slivers(verts, tris, pinned_xy=None, pin_xy=None,
                          seam_bounds=None, budget_frac=None):
    """Remove small, badly-shaped triangles on the OUTLINE.

    A candidate must have two boundary edges, touch no door pin or cell seam,
    keep every walked sample covered, keep the corridor wide enough, bridge
    nothing, and fit the area budget.
    See: docs/commentary/tes5_import_navmesh.md#boundary-sliver-cull
    """
    if not tris:
        return tris
    tris = [tuple(t) for t in tris]
    budget = ((budget_frac if budget_frac is not None
               else params.CULL_SLIVER_AREA_FRAC) * _mesh_area(verts, tris))
    pin = _pin_verts(verts, pinned_xy)
    pgrid = _bucket_pathgrid(pin_xy, _Cull.PCELL)

    def on_seam(vi):
        """True if this vertex sits on the exterior cell seam rectangle."""
        if seam_bounds is None:
            return False
        x, y = verts[vi][0], verts[vi][1]
        minx, miny, maxx, maxy = seam_bounds
        return (abs(x - minx) <= 0.5 or abs(x - maxx) <= 0.5
                or abs(y - miny) <= 0.5 or abs(y - maxy) <= 0.5)

    removed_area = 0.0
    for _round in range(3):
        tris, removed_area, changed = _cull_round(
            verts, tris, pin, pgrid, on_seam, removed_area, budget)
        if not changed:
            break
    return tris

def _drop_unreachable_islands(verts, tris, doors=None, cell_bounds=None,
                              pin_xy=None):
    """Drop disconnected components that lead nowhere.

    A component reaching a door, the exterior cell border, or a pathgrid line
    is KEPT.  Nothing is ever dropped for being small.
    See: docs/commentary/tes5_import_navmesh.md#an-island-is-dropped-only-if-unreachable
    """
    comps = components(tris)
    if len(comps) <= 1:
        return tris
    comps.sort(key=len, reverse=True)

    doors = doors or []
    dr2 = params.ISLAND_DOOR_RADIUS ** 2
    dz = params.ISLAND_DOOR_ZTOL
    margin = params.ISLAND_EDGE_MARGIN

    pins = list(pin_xy or ())
    pr2 = params.ISLAND_PGRD_RADIUS ** 2

    def reaches_exit(comp):
        """True if any vertex of this component reaches a way out of the cell."""
        for ci in comp:
            for i in tris[ci]:
                vx, vy, vz = verts[i][0], verts[i][1], verts[i][2]
                for (dxp, dyp, dzp) in doors:
                    if ((vx - dxp) ** 2 + (vy - dyp) ** 2 <= dr2 and
                            abs(vz - dzp) <= dz):
                        return True
                for p in pins:
                    if ((vx - p[0]) ** 2 + (vy - p[1]) ** 2 <= pr2 and
                            (len(p) < 3 or abs(vz - p[2]) <= dz)):
                        return True        # carries a pathgrid line
                if cell_bounds is not None:
                    minx, miny, maxx, maxy = cell_bounds
                    if (vx - minx <= margin or maxx - vx <= margin or
                            vy - miny <= margin or maxy - vy <= margin):
                        return True
        return False

    keep = set(comps[0])                     # the main body always stays
    for c in comps[1:]:
        if reaches_exit(c):
            keep.update(c)
    return [t for i, t in enumerate(tris) if i in keep]


def _weld_coincident(verts, tris):
    """Fuse vertices at the SAME rounded coordinate to one index.

    The node stitch snaps the coincident cross-section rails of corridors
    meeting at a node onto identical coordinates; this fuses those into shared
    indices so the corridors share EDGES.  Only exact (rounded) coincidence is
    fused — this never pulls distinct geometry together.
    """
    key_to_vid = {}
    remap = [0] * len(verts)
    out_verts = []
    for i, v in enumerate(verts):
        k = (round(v[0], 1), round(v[1], 1), round(v[2], 1))
        vi = key_to_vid.get(k)
        if vi is None:
            vi = len(out_verts)
            out_verts.append([v[0], v[1], v[2]])
            key_to_vid[k] = vi
        remap[i] = vi
    out_tris = []
    for (a, b, c) in tris:
        a, b, c = remap[a], remap[b], remap[c]
        if a != b and b != c and a != c:
            out_tris.append((a, b, c))
    return out_verts, out_tris
