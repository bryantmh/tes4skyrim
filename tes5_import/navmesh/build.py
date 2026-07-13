"""Navmesh build orchestrator: collision geometry + pathgrid -> triangles.

    gather geometry (world.py)
      -> voxelize                    (voxel.py)
      -> STAMP THE PATHGRID          (voxel.stamp_pathgrid)   <-- ground truth
      -> filter (ledge/headroom)     (voxel.py)
      -> regions + pathgrid seed     (region.py)
      -> erode by agent radius       (voxel.py)
      -> mesh the span graph         (spanmesh.py)
      -> drop steep tris, prune unvouched islands

Two decisions carry this design.

THE PATHGRID GOES IN FIRST, not last.  It used to be a repair pass on the finished
mesh, which meant every stage in between — the ledge filter, the headroom filter,
the region cull, the agent erosion — was free to delete the very surfaces the
pathgrid says an NPC walks, and the repair could only paste ribbons back over the
damage.  Staircases lost their mesh that way.  Stamped up front as PROTECTED spans
it is instead part of the geometry every later stage reasons about: no filter may
un-walk it, no cull may drop it, and erosion may not eat it.

THE MESH IS BUILT FROM THE SPAN GRAPH, not from contours.  A contour is a height
map (one Z per column) and a building is not: a staircase carries an NPC over the
room below it, and a house stacks two storeys in the same columns.  Meshing spans
directly makes adjacency — and therefore connectivity — structural, so staircases
cannot fragment and no triangle can bridge two floors.  See spanmesh.py.

Returns (verts, tris) in world space.  The caller (pgrd_to_navm) keeps ownership
of the NVNM/NAVM binary packing, which is already validated byte-exact against
Skyrim.esm and must not change.
"""

import logging
import math
import time

from . import params, region, spanmesh, voxel, world

_log = logging.getLogger(__name__)


def _drop_steep_triangles(verts, tris, nodes):
    """Remove triangles too steep for an NPC to walk.

    A contour can bridge a Z discontinuity — most damagingly, it can join a
    ground-floor surface straight up to the storey above, producing a wall of
    near-vertical triangles that "connect" the two floors.  No NPC walks up an
    80-degree face, so any triangle steeper than the walkable slope is an artefact
    and goes, WITHOUT EXCEPTION.

    There used to be an exception — a steep triangle was kept if the pathgrid ran
    near it, on the theory that a stamped staircase is legitimately steep.  That
    is what let the cross-floor triangles survive: a vertical triangle spanning
    two storeys has its centroid halfway up, which lands right beside the stair
    pathgrid, so it was exempted and kept.  A real staircase ramp is nowhere near
    vertical (Oblivion's are ~30 degrees), so it does not need the exception and
    nothing else deserves it.  MAX_SLOPE_DEG is the single ceiling.
    """
    if not tris:
        return verts, tris

    cos_lim = math.cos(math.radians(params.MAX_SLOPE_DEG))
    kept = []
    for (a, b, c) in tris:
        va, vb, vc = verts[a], verts[b], verts[c]
        ux, uy, uz = vb[0] - va[0], vb[1] - va[1], vb[2] - va[2]
        wx, wy, wz = vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln < 1e-9:
            continue
        if abs(nz) / ln >= cos_lim:
            kept.append((a, b, c))
    return _compact(verts, kept)


def _compact(verts, tris):
    """Drop vertices no triangle references; reindex."""
    used = sorted({i for t in tris for i in t})
    remap = {old: new for new, old in enumerate(used)}
    new_verts = [verts[i] for i in used]
    new_tris = [(remap[a], remap[b], remap[c]) for (a, b, c) in tris]
    return new_verts, new_tris


def _tri_components(tris, nverts):
    parent = list(range(nverts))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for (a, b, c) in tris:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[rc] = ra
    comps = {}
    for ti, (a, _b, _c) in enumerate(tris):
        comps.setdefault(find(a), []).append(ti)
    return comps


def _prune_islands(verts, tris, nodes):
    """Drop navmesh components no pathgrid node stands on.

    An NPC cannot cross between disconnected navmesh pieces, so an unreachable
    island is worse than useless.  A component holding a pathgrid node is kept —
    the designers walk NPCs there — and everything else (a roof corner, a scrap
    over a wall, a shelf top) is removed.
    """
    if not tris:
        return verts, tris
    comps = _tri_components(tris, len(verts))
    if len(comps) <= 1:
        return _compact(verts, tris)

    import numpy as np
    varr = np.asarray(verts)
    snap2 = (params.SEED_SNAP * 1.5) ** 2
    ztol = params.SEED_Z_TOLERANCE
    node_arr = np.asarray(nodes) if nodes else np.empty((0, 3))

    def vouched(tri_idx):
        vids = {i for ti in tri_idx for i in tris[ti]}
        vsub = varr[list(vids)]
        for (nx, ny, nz) in node_arr:
            d2 = (vsub[:, 0] - nx) ** 2 + (vsub[:, 1] - ny) ** 2
            m = d2 < snap2
            if m.any() and (np.abs(vsub[m, 2] - nz) < ztol).any():
                return True
        return False

    ordered = sorted(comps.values(), key=len, reverse=True)
    keep_tris = [tris[ti] for ti in ordered[0]]           # main component
    for tri_idx in ordered[1:]:
        if vouched(tri_idx):
            keep_tris.extend(tris[ti] for ti in tri_idx)

    return _compact(verts, keep_tris)


def build_navmesh(refr_recs, base_model_by_fid, get_collision, nodes, edges,
                  land_rec=None, origin_x=0.0, origin_y=0.0, budget=None):
    """Build a navmesh for one cell.  Returns (verts3d, tris) or ([], [])."""
    if not nodes:
        return [], []
    t0 = time.time()
    budget = params.CELL_TIME_BUDGET if budget is None else budget

    walkable, blocking, land_walk = world.gather_cell_geometry(
        refr_recs, base_model_by_fid, get_collision,
        land_rec=land_rec, origin_x=origin_x, origin_y=origin_y,
        split_land=True)

    # Bounds: cover the geometry AND the pathgrid (a node can sit just outside
    # the collision, e.g. in a doorway) with room for the agent-radius erosion.
    pad = params.AGENT_RADIUS * 2.0 + params.CS * 2.0
    xs = [n[0] for n in nodes]
    ys = [n[1] for n in nodes]
    zs = [n[2] for n in nodes]
    for arr in (walkable, blocking, land_walk):
        if len(arr):
            xs += [float(arr[:, :, 0].min()), float(arr[:, :, 0].max())]
            ys += [float(arr[:, :, 1].min()), float(arr[:, :, 1].max())]
            zs += [float(arr[:, :, 2].min()), float(arr[:, :, 2].max())]
    bounds = (min(xs) - pad, min(ys) - pad, min(zs) - pad,
              max(xs) + pad, max(ys) + pad, max(zs) + pad)

    # Exteriors are a whole 4096u cell with no doorway-scale detail, so they use
    # a coarser grid; every pass here is O(columns) and 16u would quadruple the
    # work for no gain.
    cs = params.CS_EXTERIOR if land_rec is not None else params.CS
    hf = voxel.build_heightfield(walkable, blocking, bounds, cs=cs,
                                 grid_walkable=land_walk)

    # THE PATHGRID GOES IN HERE — before any filter, cull or erosion can touch
    # it.  Every later stage treats these spans as immovable, so a staircase or a
    # doorway the designers walked an NPC through survives to the contourer as
    # ordinary, connected walkable surface instead of being repaired back on
    # afterwards.
    voxel.stamp_pathgrid(hf, nodes, edges)

    voxel.apply_filters(hf)

    # Regions + pathgrid seeding: keep only surfaces the designers vouched for.
    region_of, regions = region.build_regions(hf)
    seeded = region.seed_regions(hf, region_of, regions, nodes)
    region.keep_regions(hf, region_of, seeded)

    # A staircase climb-connects a room's floor to the CEILING of the room beneath
    # it (the treads wrap over that room), so the flood-fill merges floor + stairs
    # + ceiling into one region and keep_regions alone would paint navmesh on the
    # ceiling.  Drop any span no pathgrid sample vouches for at its height.  This
    # also rescues a storey whose region no node happened to seed, which is why
    # two-storey houses get all their floors.
    if region.keep_pathgrid_heights(hf, nodes, edges):
        region_of, regions = region.build_regions(hf)

    # Standoff from walls.  Protected (pathgrid) columns are exempt, so this can
    # never pinch a staircase or a doorway back to a sliver.
    voxel.erode_walkable(hf)

    # Mesh the SPAN GRAPH directly (see spanmesh).  Adjacent spans share corner
    # vertices, so the mesh is connected by construction — no layers to seam
    # together, no islands to weld, and two spans a storey apart are never
    # adjacent, so no triangle can bridge two floors.
    verts, tris = spanmesh.build_mesh(hf)
    if not tris:
        return [], []

    verts, tris = _drop_steep_triangles(verts, tris, nodes)
    if not tris:
        return [], []

    verts, tris = _prune_islands(verts, tris, nodes)

    if time.time() - t0 > budget:
        _log.warning("navmesh cell exceeded %.0fs budget", budget)

    return verts, tris
