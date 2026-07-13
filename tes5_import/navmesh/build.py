"""Navmesh build orchestrator: collision geometry + pathgrid -> triangles.

    gather geometry (world.py)
      -> voxelize                    (voxel.py)
      -> STAMP THE PATHGRID          (voxel.stamp_pathgrid)   <-- ground truth
      -> filter (ledge/headroom)     (voxel.py)
      -> regions + pathgrid seed     (region.py)
      -> erode by agent radius       (voxel.py)
      -> contours -> polygons -> triangles (contour.py)
      -> drop steep tris, prune unvouched islands

The pathgrid goes in FIRST, not last.  It used to be applied as a repair pass on
the finished mesh, which meant every stage in between — the ledge filter, the
headroom filter, the region cull, the agent erosion — was free to delete the very
surfaces the pathgrid says an NPC walks, and the repair could only paste ribbons
back on top of the damage.  Staircases lost their mesh that way, and the ribbons
pasted back never conformed to what surrounded them.

Stamped up front as PROTECTED spans, the pathgrid is instead part of the geometry
every later stage reasons about: no filter may un-walk it, no cull may drop it,
erosion may not eat it, and the contourer builds the staircase as an ordinary,
connected part of the mesh because the spans are simply there.

Returns (verts, tris) in world space.  The caller (pgrd_to_navm) keeps ownership
of the NVNM/NAVM binary packing, which is already validated byte-exact against
Skyrim.esm and must not change.
"""

import logging
import math
import time

from . import contour, params, region, voxel, world

_log = logging.getLogger(__name__)


def _drop_steep_triangles(verts, tris, nodes):
    """Remove triangles steeper than a walkable slope.

    A contour can bridge a Z discontinuity — joining a stair top straight down to
    the stair bottom — which reads as a near-vertical face an NPC would "walk" up.

    A triangle the PATHGRID runs across is never dropped, however steep it looks:
    on a staircase the treads are stamped at the pathgrid's own heights, and the
    ramp between two stamped treads is legitimately steep.  Dropping those is what
    left staircases bare, so geometry-derived steepness alone cannot condemn a
    triangle the designers walked an NPC over.
    """
    if not tris:
        return verts, tris

    import numpy as np
    cos_lim = math.cos(math.radians(params.MAX_SLOPE_DEG))
    narr = np.asarray(nodes) if nodes else np.empty((0, 3))
    snap2 = (params.SEED_SNAP * 1.5) ** 2

    def on_pathgrid(va, vb, vc):
        if not len(narr):
            return False
        cx = (va[0] + vb[0] + vc[0]) / 3.0
        cy = (va[1] + vb[1] + vc[1]) / 3.0
        cz = (va[2] + vb[2] + vc[2]) / 3.0
        d2 = (narr[:, 0] - cx) ** 2 + (narr[:, 1] - cy) ** 2
        m = d2 < snap2
        if not m.any():
            return False
        return bool((np.abs(narr[m, 2] - cz) < params.SEED_Z_TOLERANCE).any())

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
        if abs(nz) / ln >= cos_lim or on_pathgrid(va, vb, vc):
            kept.append((a, b, c))
    return _compact(verts, kept)


def _weld(verts, tris):
    """Merge coincident vertices so independently-contoured layers become one mesh.

    Each Z-layer is contoured on its own (it has to be — a contour is a height
    map), which means a staircase layer and the floor layer it rises from produce
    their OWN vertices at the lattice corners they share.  Geometrically the two
    surfaces meet; topologically they are two disconnected components that touch
    at a corner — which is exactly what "the stairs only connect at one triangle
    corner" looks like, and why so many cells came out as islands.

    Welding vertices that coincide in 3D turns those duplicate corners into shared
    ones, so the layers become a single connected surface.  It adds no geometry and
    moves nothing: it only merges points that were already in the same place.

    The Z tolerance is a step height — two layers that meet at a walkable seam are
    at most a step apart there, while a floor and the ceiling below it are a storey
    apart and never weld.
    """
    if not verts:
        return verts, tris
    snap = params.CS * 0.5
    ztol = params.MAX_CLIMB

    buckets = {}
    remap = [0] * len(verts)
    out = []
    for i, v in enumerate(verts):
        kx = int(round(v[0] / snap))
        ky = int(round(v[1] / snap))
        hit = None
        # Check this cell and its neighbours so a pair straddling a bucket edge
        # still welds.
        for bx in (kx - 1, kx, kx + 1):
            for by in (ky - 1, ky, ky + 1):
                for j in buckets.get((bx, by), ()):
                    o = out[j]
                    if (abs(o[0] - v[0]) <= snap and abs(o[1] - v[1]) <= snap
                            and abs(o[2] - v[2]) <= ztol):
                        hit = j
                        break
                if hit is not None:
                    break
            if hit is not None:
                break
        if hit is None:
            hit = len(out)
            out.append(v)
            buckets.setdefault((kx, ky), []).append(hit)
        remap[i] = hit

    new_tris = []
    for (a, b, c) in tris:
        na, nb, nc = remap[a], remap[b], remap[c]
        if na == nb or nb == nc or na == nc:
            continue                      # collapsed to a degenerate sliver
        new_tris.append((na, nb, nc))
    return _compact(out, new_tris)


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
    # never pinch a staircase or a doorway back to a sliver.  Erosion can still
    # split an ordinary region in two, so rebuild regions when it changed
    # anything — contouring stale regions fans triangles across the new gap.
    if voxel.erode_walkable(hf):
        region_of, _regions = region.build_regions(hf)

    verts, tris = contour.build_mesh(hf, region_of)
    if not tris:
        return [], []

    # Each Z-layer was contoured independently, so layers that physically meet
    # (a staircase and its floor) still own separate vertices at the corners they
    # share.  Weld those together or the mesh stays a pile of islands touching at
    # corners — an NPC cannot cross between disconnected navmesh.
    verts, tris = _weld(verts, tris)

    verts, tris = _drop_steep_triangles(verts, tris, nodes)
    if not tris:
        return [], []

    verts, tris = _prune_islands(verts, tris, nodes)

    if time.time() - t0 > budget:
        _log.warning("navmesh cell exceeded %.0fs budget", budget)

    return verts, tris
