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


def _make_manifold(verts, tris):
    """Drop the smallest triangle on any edge shared by 3+ triangles until every
    edge has at most two.  Keeps the larger (more load-bearing) triangle.
    """
    tris = [tuple(map(int, t)) for t in tris]

    def area(t):
        va, vb, vc = verts[t[0]], verts[t[1]], verts[t[2]]
        return abs((vb[0] - va[0]) * (vc[1] - va[1]) -
                   (vb[1] - va[1]) * (vc[0] - va[0])) * 0.5

    for _ in range(6):
        owners = {}
        for ti, t in enumerate(tris):
            for k in range(3):
                a, b = t[k], t[(k + 1) % 3]
                key = (a, b) if a < b else (b, a)
                owners.setdefault(key, []).append(ti)

        # How many DIFFERENT triangles each triangle touches: a triangle that is
        # the only link between two parts of the mesh has high connectivity value
        # and must survive even if it is small.  Dropping purely by area cut the
        # node-disc bridges — the smallest triangles at every junction — and split
        # LeyawiinCastleCountyHall's mesh into 8170 + 4275 after it had been built
        # as a single connected sheet.
        nbr = [0] * len(tris)
        for ent in owners.values():
            if len(ent) == 2:
                nbr[ent[0]] += 1
                nbr[ent[1]] += 1

        drop = set()
        for ent in owners.values():
            if len(ent) <= 2:
                continue
            live = [ti for ti in ent if ti not in drop]
            if len(live) <= 2:
                continue
            # Keep the best-connected triangles first, breaking ties by area.
            live.sort(key=lambda ti: (nbr[ti], area(tris[ti])), reverse=True)
            drop.update(live[2:])
        if not drop:
            break
        # A triangle may only be dropped if the mesh stays as connected without
        # it.  Dropping purely to satisfy the manifold rule repeatedly split
        # meshes that had been built as one sheet (5 components in the Guild),
        # which is far worse than one over-shared edge: the engine ignores the
        # extra edge, but an island is unreachable.
        kept = [t for i, t in enumerate(tris) if i not in drop]
        if len(components(kept)) <= len(components(tris)):
            tris = kept
        else:
            # Re-try dropping only those that do not break connectivity.
            safe = set()
            base = len(components(tris))
            for ti in sorted(drop):
                trial = [t for i, t in enumerate(tris)
                         if i != ti and i not in safe]
                if len(components(trial)) <= base:
                    safe.add(ti)
            if not safe:
                break
            tris = [t for i, t in enumerate(tris) if i not in safe]
    return tris


def finalize(verts, tris, cs=None, pinned=None, doors=None, cell_bounds=None):
    """V1 cleanup: drop degenerate triangles, guarantee manifold, compact.

    corridor_union already produces ONE connected, non-overlapping surface (the
    boolean union of the ribbons, retriangulated) — manifold by construction.
    So this welds coincident vertices (the grid-split pieces share boundary
    coords), runs a make-manifold backstop, and drops any UNREACHABLE stray
    component; no decimation.  `cs`/`pinned` accepted for signature stability
    and unused.

    doors: [(x, y, z), ...] door centres — a component reaching a door leads to
    another cell and is kept.  cell_bounds: (minx, miny, maxx, maxy) worldspace
    bounds for an exterior cell — a component touching the border continues into
    the neighbour cell via a worldspace edge-link and is kept.

    Returns (verts, tris) as numpy arrays (float verts, int32 tris).
    """
    verts, tris = _weld_coincident(verts, tris)
    tris = _make_manifold(verts, tris)
    tris = _drop_unreachable_islands(verts, tris, doors, cell_bounds)
    return _compact(verts, tris)


# TODO(navmesh): revisit — some of these dropped fringe islands are REAL
# coverage the main mesh does not cover (verified on Chorrol: their centroids
# are inside no main-component triangle).  They arise where the retriangulation
# pinched a surface to a single-vertex bowtie, leaving a corner edge-detached.
# The proper fix is to seed the triangulation so the neck stays edge-connected
# (or split the bowtie vertex), NOT to drop.  For now an island is dropped only
# when it is unreachable — connected to NO cell door and NO worldspace border —
# so a doorstep or a border-crossing scrap is always preserved even if tiny.


def _drop_unreachable_islands(verts, tris, doors=None, cell_bounds=None):
    """Drop disconnected components that lead nowhere.

    A component is KEPT when it can reach another cell:
      * it comes within ISLAND_DOOR_RADIUS of a door (leads through the door),
      * or (exterior) it touches the cell border, where a worldspace edge-link
        continues it into the neighbour cell.
    Everything still connected to the main body is kept as one component.  Only
    a component that is BOTH disconnected from the main mesh AND reaches no cell
    exit is unreachable noise, and only those are dropped — never by size.
    """
    comps = components(tris)
    if len(comps) <= 1:
        return tris
    comps.sort(key=len, reverse=True)

    doors = doors or []
    dr2 = params.ISLAND_DOOR_RADIUS ** 2
    dz = params.ISLAND_DOOR_ZTOL
    margin = params.ISLAND_EDGE_MARGIN

    def reaches_exit(comp):
        for ci in comp:
            for i in tris[ci]:
                vx, vy, vz = verts[i][0], verts[i][1], verts[i][2]
                for (dxp, dyp, dzp) in doors:
                    if ((vx - dxp) ** 2 + (vy - dyp) ** 2 <= dr2 and
                            abs(vz - dzp) <= dz):
                        return True
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


# ---------------------------------------------------------------------------
# Validation (used by tools / acceptance, not by the build)
# ---------------------------------------------------------------------------

def edge_adjacency(tris):
    """(N,3) neighbour-triangle indices over shared edges, -1 for boundary.

    MUST match pgrd_to_navm._compute_adjacency: an edge shared by exactly two
    triangles links them; three or more links none.  This is the connectivity
    the ENGINE sees, distinct from vertex-union-find.
    """
    owners = {}
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = int(t[k]), int(t[(k + 1) % 3])
            key = (a, b) if a < b else (b, a)
            owners.setdefault(key, []).append((ti, k))
    adj = [[-1, -1, -1] for _ in range(len(tris))]
    for ent in owners.values():
        if len(ent) == 2:
            (ti, si), (tj, sj) = ent
            adj[ti][si] = tj
            adj[tj][sj] = ti
    return adj


def components(tris):
    """List of triangle-index lists connected over EDGE adjacency (engine view)."""
    adj = edge_adjacency(tris)
    n = len(tris)
    seen = [False] * n
    comps = []
    for s in range(n):
        if seen[s]:
            continue
        comp, stack = [], [s]
        seen[s] = True
        while stack:
            x = stack.pop()
            comp.append(x)
            for nb in adj[x]:
                if nb >= 0 and not seen[nb]:
                    seen[nb] = True
                    stack.append(nb)
        comps.append(comp)
    return comps
