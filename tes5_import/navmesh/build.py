"""Navmesh build orchestrator: collision geometry -> triangles.

    gather geometry (world.py)
      -> voxelize + filter        (voxel.py)
      -> regions + pathgrid seed  (region.py)
      -> floor synthesis for missing shells
      -> erode by agent radius
      -> contours -> polygons -> triangles (contour.py)
      -> connectivity repair along PGRD edges

Returns (verts, tris) in world space.  The caller (pgrd_to_navm) keeps ownership
of the NVNM/NAVM binary packing, which is already validated byte-exact against
Skyrim.esm and must not change.
"""

import logging
import time

from . import contour, params, region, voxel, world

_log = logging.getLogger(__name__)


def _components(tris, nverts):
    """Connected-component id per vertex (union-find over triangle edges)."""
    parent = list(range(nverts))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for (a, b, c) in tris:
        union(a, b)
        union(b, c)
    return [find(i) for i in range(nverts)]


def _repair_connectivity(hf, nodes, edges, verts, tris):
    """Ensure PGRD-connected nodes end up in one navmesh component.

    A pathgrid edge is an authored assertion that an NPC walks from A to B.  If
    voxelization severed that (a doorway narrower than the eroded agent, a
    missing plank), carve a walkable corridor along the edge and report that a
    rebuild is needed.  The corridor follows a real pathgrid edge, so by
    construction it does not cut through a wall.

    Returns True if anything was carved (caller re-meshes).
    """
    if not tris or not edges:
        return False

    comp = _components(tris, len(verts))

    # Map each node to the component of its nearest navmesh vertex.
    def node_comp(n):
        best, bi = None, None
        for i, v in enumerate(verts):
            d = (v[0] - n[0]) ** 2 + (v[1] - n[1]) ** 2
            if d > (params.SEED_SNAP * 4.0) ** 2:
                continue
            if abs(v[2] - n[2]) > params.SEED_Z_TOLERANCE:
                continue
            if best is None or d < best:
                best, bi = d, i
        return comp[bi] if bi is not None else None

    ncomp = [node_comp(n) for n in nodes]

    carved = 0
    half = params.REPAIR_WIDTH * 0.5
    for (i, j) in edges:
        ci, cj = ncomp[i], ncomp[j]
        if ci is None or cj is None or ci == cj:
            continue
        # Severed: carve a corridor of walkable spans along the edge.
        a, b = nodes[i], nodes[j]
        steps = int(max(abs(a[0] - b[0]), abs(a[1] - b[1])) / (hf.cs * 0.5)) + 2
        r = max(1, int(round(half / hf.cs)))
        for k in range(steps + 1):
            t = k / steps
            wx = a[0] + (b[0] - a[0]) * t
            wy = a[1] + (b[1] - a[1]) * t
            wz = a[2] + (b[2] - a[2]) * t
            cx0 = int((wx - hf.min_x) / hf.cs)
            cy0 = int((wy - hf.min_y) / hf.cs)
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    cx, cy = cx0 + dx, cy0 + dy
                    if cx < 0 or cy < 0 or cx >= hf.w or cy >= hf.h:
                        continue
                    col = hf.spans[cy * hf.w + cx]
                    hit = None
                    for s in col:
                        if abs(s[1] - wz) <= params.MAX_CLIMB:
                            hit = s
                            break
                    if hit is not None:
                        if not hit[2]:
                            hit[2] = True
                            carved += 1
                    else:
                        hf.add_span(cx, cy, wz - hf.ch, wz, True)
                        carved += 1
    return carved > 0


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
    if len(walkable) == 0 and len(blocking) == 0 and len(land_walk) == 0:
        return [], []

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
    voxel.apply_filters(hf)

    # Regions + pathgrid seeding: keep only surfaces the designers vouched for.
    region_of, regions = region.build_regions(hf)
    seeded = region.seed_regions(hf, region_of, regions, nodes)

    # Some rooms' floor shells live in a neighbouring cell; the pathgrid still
    # asserts NPCs walk there, so lay down floor under those orphaned nodes.
    missing = region.unseeded_nodes(hf, region_of, nodes, seeded)
    if missing and region.synthesize_floor(hf, nodes, edges, missing):
        region_of, regions = region.build_regions(hf)
        seeded = region.seed_regions(hf, region_of, regions, nodes)

    region.keep_regions(hf, region_of, seeded)

    # Standoff from walls.  After seeding, so erosion cannot orphan a region.
    # Erosion can split a region in two (a corridor pinched at a doorway), so
    # regions must be recomputed before contouring when it changed anything —
    # contouring stale regions would fan triangles across the new gap.
    if voxel.erode_walkable(hf):
        region_of, _regions = region.build_regions(hf)
    verts, tris = contour.build_mesh(hf, region_of)
    if not tris:
        return [], []

    # Guarantee PGRD-connected nodes share a navmesh component.
    if time.time() - t0 < budget:
        if _repair_connectivity(hf, nodes, edges, verts, tris):
            region_of, _regions = region.build_regions(hf)
            verts, tris = contour.build_mesh(hf, region_of)

    if time.time() - t0 > budget:
        _log.warning("navmesh cell exceeded %.0fs budget", budget)

    return verts, tris
