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
    """Remove wall-sized steep triangles; KEEP stair risers.

    A triangle steeper than the walkable slope is suspect, but steepness alone is
    the wrong test: a stair riser meshed at CS=16u is legitimately steeper than
    MAX_SLOPE_DEG (a 26u riser over one 16u cell is a 58-degree face, and a whole
    tread-to-tread quad can be vertical), yet it is a single STEP the pathgrid
    walks.  Dropping those was what shredded every staircase into disconnected
    treads — the "holes on stairs" bug: the treads survived, the riser triangles
    between them did not, and the corridor fell apart.

    The discriminator is the triangle's VERTICAL EXTENT.  A single step is at most
    MAX_CLIMB tall (corner averaging on a stamped stair can stretch that to about
    twice), while the cross-floor wall this filter exists to kill spans a whole
    storey (150u+).  The span mesher already makes cross-floor triangles
    unrepresentable — spans a storey apart never share corner vertices — so this
    is a pure backstop, and it must only fire on what cannot possibly be a step:

        drop  <=>  steeper than MAX_SLOPE_DEG  AND  taller than 2.5 * MAX_CLIMB
    """
    if not tris:
        return verts, tris

    cos_lim = math.cos(math.radians(params.MAX_SLOPE_DEG))
    max_step_span = params.MAX_CLIMB * 2.5
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
        if abs(nz) / ln < cos_lim:
            zspan = (max(va[2], vb[2], vc[2]) -
                     min(va[2], vb[2], vc[2]))
            if zspan > max_step_span:
                continue
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
    """Drop navmesh components no pathgrid node stands on, and any island that
    merely SHADOWS a bigger piece.

    An NPC cannot cross between disconnected navmesh pieces, so an unreachable
    island is worse than useless.  A component holding a pathgrid node is kept —
    the designers walk NPCs there — and everything else (a roof corner, a scrap
    over a wall, a shelf top) is removed.

    The vouch test alone is not enough around STAIRCASES: the floor sliver left
    under a flight after the headroom filter, or a stamped ribbon that failed to
    snap onto the treads, sits right next to a stair-base node — vouched — yet
    is a disconnected scrap directly UNDER the real stair mesh.  A disconnected
    island lying in the XY footprint of a bigger kept component, within
    sub-storey Z of its surface, is an artifact by definition: if an NPC could
    genuinely stand there it would have connected.  Two real storeys are never
    caught by this — they are a full storey (250u+) apart.
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

    shadow_dz = params.MAX_CLIMB * 5.0     # sub-storey; two floors are 250u+

    def shadowed(tri_idx, kept_tri_idx):
        """True if most of this island lies under/over kept mesh nearby in Z."""
        hits = 0
        total = 0
        for ti in tri_idx:
            a, b, c = tris[ti]
            cx = (varr[a][0] + varr[b][0] + varr[c][0]) / 3.0
            cy = (varr[a][1] + varr[b][1] + varr[c][1]) / 3.0
            cz = (varr[a][2] + varr[b][2] + varr[c][2]) / 3.0
            total += 1
            for tj in kept_tri_idx:
                p, q, r = tris[tj]
                pa, pb, pc = varr[p], varr[q], varr[r]
                d = ((pb[1] - pc[1]) * (pa[0] - pc[0]) +
                     (pc[0] - pb[0]) * (pa[1] - pc[1]))
                if abs(d) < 1e-9:
                    continue
                l0 = ((pb[1] - pc[1]) * (cx - pc[0]) +
                      (pc[0] - pb[0]) * (cy - pc[1])) / d
                l1 = ((pc[1] - pa[1]) * (cx - pc[0]) +
                      (pa[0] - pc[0]) * (cy - pc[1])) / d
                l2 = 1.0 - l0 - l1
                if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
                    continue
                z = l0 * pa[2] + l1 * pb[2] + l2 * pc[2]
                if abs(z - cz) <= shadow_dz:
                    hits += 1
                    break
        return total and hits / total >= 0.6

    ordered = sorted(comps.values(), key=len, reverse=True)
    kept_comps = [ordered[0]]                             # main component
    for tri_idx in ordered[1:]:
        if not vouched(tri_idx):
            continue
        flat_kept = [ti for comp in kept_comps for ti in comp]
        if shadowed(tri_idx, flat_kept):
            continue
        kept_comps.append(tri_idx)

    keep_tris = [tris[ti] for comp in kept_comps for ti in comp]
    return _compact(verts, keep_tris)


def teleport_door_positions(refr_recs):
    """(x, y) of every teleport-door REFR (XTEL) in the cell.

    A teleport door leads to ANOTHER cell, so the navmesh must end at its
    threshold — exactly as vanilla navmeshes do.  These positions become
    barriers for the pathgrid-reach flood (see region.keep_pathgrid_heights):
    without them, an interior cell's mesh escapes through the open doorway and
    spreads over the decorative street/porch geometry outside the shell.
    """
    out = []
    for refr in refr_recs or ():
        if refr.get('XTEL.Door'):
            try:
                out.append((float(refr.get('PosX')), float(refr.get('PosY'))))
            except (TypeError, ValueError):
                pass
    return out


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
    #
    # But CLAMP the geometry's contribution to a window around the pathgrid
    # (plus the LAND extent for exteriors).  The final mesh can only exist near
    # the pathgrid — region seeding and keep_pathgrid_heights cull every span
    # farther than ~SEED_SNAP*2.5 from a pathgrid sample — so collision far
    # outside the node bbox cannot affect the result.  It CAN, however, blow the
    # grid past MAX_GRID_DIM and coarsen CS by whole octaves: one outlier REFR in
    # FelgageldtCave stretched the bounds to 74k x 122k units, the grid guard
    # pushed CS from 16 to 256, and the entire cave voxelized into mush.
    pad = params.AGENT_RADIUS * 2.0 + params.CS * 2.0
    win = params.PGRD_XY_REACH + params.AGENT_RADIUS * 2.0 + params.CS * 4.0
    lo_x = min(n[0] for n in nodes) - win
    hi_x = max(n[0] for n in nodes) + win
    lo_y = min(n[1] for n in nodes) - win
    hi_y = max(n[1] for n in nodes) + win
    if land_rec is not None:
        lo_x = min(lo_x, origin_x)
        hi_x = max(hi_x, origin_x + 4096.0)
        lo_y = min(lo_y, origin_y)
        hi_y = max(hi_y, origin_y + 4096.0)
    xs = [n[0] for n in nodes]
    ys = [n[1] for n in nodes]
    zs = [n[2] for n in nodes]
    for arr in (walkable, blocking, land_walk):
        if len(arr):
            xs += [max(lo_x, float(arr[:, :, 0].min())),
                   min(hi_x, float(arr[:, :, 0].max()))]
            ys += [max(lo_y, float(arr[:, :, 1].min())),
                   min(hi_y, float(arr[:, :, 1].max()))]
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
    # ceiling.  Drop any span the pathgrid cannot WALK to within PGRD_XY_REACH
    # (geodesic over the span graph).  Teleport doors are flood barriers in
    # interiors: the mesh ends at the threshold instead of escaping through the
    # doorway onto the decorative geometry outside the cell's shell.
    barriers = None
    if land_rec is None:
        tdoors = teleport_door_positions(refr_recs)
        if tdoors:
            barriers = set()
            r_cells = max(1, int(round(params.DOOR_BARRIER_RADIUS / hf.cs)))
            for (dx, dy) in tdoors:
                cx0 = int((dx - hf.min_x) / hf.cs)
                cy0 = int((dy - hf.min_y) / hf.cs)
                for oy in range(-r_cells, r_cells + 1):
                    for ox in range(-r_cells, r_cells + 1):
                        if ox * ox + oy * oy > r_cells * r_cells:
                            continue
                        cx, cy = cx0 + ox, cy0 + oy
                        if 0 <= cx < hf.w and 0 <= cy < hf.h:
                            barriers.add(cy * hf.w + cx)
    if region.keep_pathgrid_heights(hf, nodes, edges, barriers=barriers):
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
