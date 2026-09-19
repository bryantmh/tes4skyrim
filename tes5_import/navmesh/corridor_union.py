"""Boolean polygon union of the corridor ribbons, then retriangulation.

WHY THIS IS THE RIGHT ALGORITHM

Corridor ribbons overlap wherever pathgrid lines converge.  Cutting them
pairwise (trim the ribbon, weld the seam, patch the junction) is an
approximation that has to get every case right — end-to-end, crossing, wedge,
collinear, dead end — and any case it gets wrong shows up as either lost ground
or stacked sheets.

The union does not approximate.  Each ribbon is a polygon; the geometric union
of the polygons is a region whose area is EXACTLY the measure of the ground the
corridors cover.  Retriangulating that region produces triangles that are
non-overlapping by definition.  So:

    coverage  == 100% by construction (the union contains every ribbon)
    overlap   ==   0% by construction (a triangulation does not self-overlap)

STOREYS — one union PER SHEET, never one flattened union

A single 2D union of every ribbon is WRONG for a multi-storey building, because
the floors overlap in plan view — measured in ChorrolFightersGuild, the three
floors overlap each other by 26-36% of the union's area.  Flattening them and
triangulating once lets a triangle take one corner from the upper floor and
another from the lower: a near-vertical sheet hanging in the stairwell, which is
what rendered as "triangles between floors".  Neither emitting those (mid-air
mesh) nor dropping them (they severed 24 shared edges and split one floor into
7 pieces) is a fix, because the flattened polygon was never the right region.

So the ribbons are partitioned into SHEETS first, and each sheet is unioned and
triangulated on its own:

  1. `_storey_groups` joins ribbons that share a pathgrid NODE where their
     heights agree.  A staircase therefore joins the floor at its foot AND the
     floor at its head, so a whole building comes back as one connected group —
     correct for connectivity, but still not a region we can union.
  2. `_split_plan_overlaps` cuts that group into sheets that do not overlap
     THEMSELVES in plan: two ribbons that overlap in plan and disagree in height
     by more than STOREY_GAP_Z cannot share a sheet.  Ribbons are then assigned
     to the sheet whose height they match BEST (a first-fit scattered one floor
     across several sheets, which overlapped at the same height and duplicated
     ground — 7% of triangles stacked).
  3. Each sheet is unioned, triangulated, and lifted independently, then
     `_weld_sheets` (3D radius weld) and `split_t_junctions` rejoin sheets that
     abut on one floor, so the surface stays connected across a sheet boundary.

HEIGHT — the vertex, not the triangle, owns it

Every output vertex gets its Z from a corridor that covers it, along that
corridor's own centerline, so each triangle sits on the pathgrid line's own
slope (principle 2) and a staircase keeps its rise.

Crucially the height is a property of THE POINT AND ITS STOREY, never of
whichever triangle reached it first.  The original code took a triangle's height
as the MEAN of its three corners' levels and then bound each corner to any vertex
already within SAME_SURFACE_Z of that mean, so a corner's height depended on
triangle order: corner 22 of ICPrisonSewerExit01, carrying a single level at
395.3, minted one vertex at 395.3 for one neighbour and another at 356.2 for the
next.  Those two triangles then shared no EDGE, and the engine cannot walk
between triangles that share only a point — 28 of 582 shared edges were lost and
the mesh fell into 12 components (ICPrisonEntrance01: 28).  Stairs tore worst
because every consecutive triangle on a flight has a different mean.  No value of
SAME_SURFACE_Z fixes that; it is a first-match-wins race, not a tolerance.

`_emit_surfaces` instead keys each vertex on (corner, storey band), a stable
per-corner identity, so two triangles meeting on one surface ALWAYS resolve to
the same vertex and connectivity is structural.

DOORS

corridor_doors.door_footprints runs first, on the raw ribbon union; each door's
flat footprint (the quad bridging its base line to the nearest corridor edge) is
handed back as an `extra_strips` polygon and joins the union as ordinary ground,
and its BASE LINE is passed as a `door_edges` constraint so the retriangulation
forces one large triangle with its long side on the door line — the vanilla
Skyrim door triangle.  The union resolves any overlap with the corridor by
construction — the door coverage is preserved exactly, nothing is deleted.

HEIGHT

Every output vertex gets its Z from a corridor that covers it, along that
corridor's own centerline, so each triangle sits on the pathgrid line's own
slope (principle 2) and a staircase keeps its rise.  Heights are never discarded
and reconstructed — each ribbon already knows its Z everywhere along itself.
"""

import math

import numpy as np
# Bound once at module scope: `_overlap_height_gap` is called ~30k times on a
# large cell, and re-running `import shapely` per call is pure interpreter
# overhead on an already-loaded module.

from . import params

from .union_mesh import (
    _destack as _destack,
    _drop_walls as _drop_walls,
    _merge_at_pathgrid_nodes as _merge_at_pathgrid_nodes,
    split_t_junctions as split_t_junctions,
    stitch_shared_nodes as stitch_shared_nodes,
    _tri_overlaps_mesh as _tri_overlaps_mesh,
    _weld_sheets as _weld_sheets,
)
from .union_sheets import (
    _overlap_height_gap as _overlap_height_gap,
    _same_surface_region as _same_surface_region,
    _split_plan_overlaps as _split_plan_overlaps,
    _storey_groups as _storey_groups,
    _subs_same_floor as _subs_same_floor,
)
from .union_cdt import (
    DOOR_SNAP_PERP as DOOR_SNAP_PERP,
    RIBBON_SEED_STEP as RIBBON_SEED_STEP,
    STEEP_REFINE_EDGE as STEEP_REFINE_EDGE,
    _door_edge_on_part as _door_edge_on_part,
    _earcut_fallback as _earcut_fallback,
    _flip2d as _flip2d,
    _hex_refine as _hex_refine,
    _recover_constraints as _recover_constraints,
    _refine_steep as _refine_steep,
    _ribbon_seeds as _ribbon_seeds,
    _snap_outline_to_door_lines as _snap_outline_to_door_lines,
    _triangulate as _triangulate,
)
from .union_geom import (
    FLAP_EDGE_DROP as FLAP_EDGE_DROP,
    REACH_TOL as REACH_TOL,
    SAME_SURFACE_Z as SAME_SURFACE_Z,
    STOREY_GAP_Z as STOREY_GAP_Z,
    WALL_SLOPE_COS as WALL_SLOPE_COS,
    _RIBBON_CACHE as _RIBBON_CACHE,
    _clip_strip_near as _clip_strip_near,
    _distance_to as _distance_to,
    _has_edge as _has_edge,
    _height_on as _height_on,
    _land_of as _land_of,
    _near as _near,
    _on_segment as _on_segment,
    _point_in_poly as _point_in_poly,
    _poly_strip as _poly_strip,
    _ribbon_cache_clear as _ribbon_cache_clear,
    _ribbon_polygon as _ribbon_polygon,
    _ribbon_polygon_uncached as _ribbon_polygon_uncached,
    _seg_dist as _seg_dist,
    _seg_intersect as _seg_intersect,
    _segment_cuts as _segment_cuts,
    _split_triangle as _split_triangle,
    _tri_area as _tri_area,
    _tri_components as _tri_components,
    _tri_edges as _tri_edges,
    _tri_span as _tri_span,
)


# A sheet may only claim a door when it holds at least this much of the door
# triangle.  The base line sits on the union boundary, so every sheet meeting
# the threshold passes the on-outline test; without this the first one iterated
# won and cut a wedge that was almost entirely outside itself.
DOOR_CLAIM_MIN_FRAC = 0.5


def _node_geometry(strips):
    """(node_pts, node_half): each pathgrid node's XY and widest ribbon half.

    See: docs/commentary/tes5_import_navmesh.md#shared-node-points-are-seeded
    """
    node_pts, node_half = {}, {}
    for s in strips:
        (i, j) = s.get('edge', (-1, -1))
        if i < 0:
            continue
        node_pts.setdefault(i, (s['na'][0], s['na'][1]))
        node_half[i] = max(node_half.get(i, 0.0), float(s['half']))
        if j != i:
            node_pts.setdefault(j, (s['nb'][0], s['nb'][1]))
            node_half[j] = max(node_half.get(j, 0.0), float(s['half']))
    return node_pts, node_half


def _sheet_node_sets(sheets):
    """(node_sheets, node_owner): which sheets touch each node, and its owner.

    A node is owned by the FIRST sheet reaching it, exclusively.
    See: docs/commentary/tes5_import_navmesh.md#junction-union-is-exclusive
    """
    node_sheets, node_owner = {}, {}
    for gi, group in enumerate(sheets):
        for s in group:
            (i, j) = s.get('edge', (-1, -1))
            if i < 0:
                continue
            node_sheets.setdefault(i, set()).add(gi)
            node_sheets.setdefault(j, set()).add(gi)
            for nd in ((i,) if j == i else (i, j)):
                node_owner.setdefault(nd, gi)
    return node_sheets, node_owner


def _junction_transfers(strips, sheets, node_pts, node_half, node_owner):
    """(extra, strips, drop) per sheet for every cross-sheet pathgrid junction.

    See: docs/commentary/tes5_import_navmesh.md#junction-union-is-exclusive
    """
    extra, donated, drop = {}, {}, {}
    if not node_pts:
        return extra, donated, drop
    from shapely.geometry import Point as _Point
    sheet_of = {}
    for gi, group in enumerate(sheets):
        for s in group:
            sheet_of[s.get('edge', (-1, -1))] = gi
    for s in strips:
        (i, j) = s.get('edge', (-1, -1))
        if i < 0:
            continue
        gi = sheet_of.get((i, j))
        if gi is None:
            continue
        for nd in ((i,) if j == i else (i, j)):
            own = node_owner.get(nd)
            if own is None or own == gi or nd not in node_pts:
                continue
            nx, ny = node_pts[nd]
            r = max(float(node_half.get(nd, 0.0)), params.RIBBON_HALF_WIDTH)
            try:
                piece = _ribbon_polygon(s).intersection(
                    _Point(nx, ny).buffer(r))
            except Exception:
                continue
            if piece.is_empty or piece.area < 1.0:
                continue
            extra.setdefault(own, []).append(piece)
            donated.setdefault(own, []).append(
                _clip_strip_near(s, nx, ny, r, piece))
            drop.setdefault(gi, []).append(piece)
    return extra, donated, drop


def _sheet_coverage(group, gi, ctx):
    """This sheet's own polygon after junction transfers, clip and claims.

    See: docs/commentary/tes5_import_navmesh.md#sheets-claim-ground-exclusively
    """
    from shapely.geometry import box
    from shapely.ops import unary_union
    gpolys = [p for p in (_ribbon_polygon(s) for s in group)
              if p.is_valid and not p.is_empty]
    gpolys.extend(ctx['junction_extra'].get(gi, ()))
    if not gpolys:
        return None
    gmerged = unary_union(gpolys)
    drop = ctx['junction_drop'].get(gi)
    if drop:
        try:
            cut = gmerged.difference(unary_union(drop))
            if not cut.is_empty:
                gmerged = cut
        except Exception:
            pass
    if ctx['cell_bounds'] is not None:
        minx, miny, maxx, maxy = ctx['cell_bounds']
        gmerged = gmerged.intersection(box(minx, miny, maxx, maxy))
    for (prev_poly, prev_group) in ctx['claimed']:
        if gmerged.is_empty:
            break
        try:
            shared_area = gmerged.intersection(prev_poly)
        except Exception:
            continue
        if shared_area.is_empty or shared_area.area < 1.0:
            continue
        dup = _same_surface_region(group, prev_group, shared_area)
        if dup is None or dup.is_empty:
            continue
        try:
            trimmed = gmerged.difference(dup)
        except Exception:
            continue
        if not trimmed.is_empty:
            gmerged = trimmed
    return None if gmerged.is_empty else gmerged


#: A terrain sheet's triangle edges are bisected down to this many target edges.
LAND_MAX_EDGE_RATIO = 2.5

#: Half the widest gap between terrain ribbons that is closed up.
LAND_SLIT_HALF = 16.0


def _target_edge(group):
    """Triangle target edge for a sheet: terrain sheets mesh coarser."""
    return (params.LAND_TRI_TARGET_EDGE if _land_of(group) is not None
            else params.TRI_TARGET_EDGE)


def _close_land_slits(gmerged, group, slit_ok):
    """`gmerged` with the wall-free hairline gaps between terrain ribbons filled.

    See: docs/commentary/tes5_import_navmesh.md#land-slits-are-closed
    """
    if slit_ok is None or _land_of(group) is None:
        return gmerged
    from shapely.ops import unary_union
    closed = gmerged.buffer(LAND_SLIT_HALF, join_style=2).buffer(
        -LAND_SLIT_HALF, join_style=2)
    fill = closed.difference(gmerged)
    pieces = list(getattr(fill, 'geoms', [fill]))
    keep = [p for p in pieces if not p.is_empty and p.area > 1.0 and slit_ok(p)]
    return unary_union([gmerged] + keep) if keep else gmerged


def _sheet_parts(gmerged, wall_cut):
    """The sheet's polygon list after the wall cut, or an empty list."""
    from shapely.geometry import Polygon
    if wall_cut is not None:
        try:
            gcut = gmerged.difference(wall_cut)
            if not gcut.is_empty:
                gmerged = gcut
        except Exception:
            pass
    if gmerged.is_empty:
        return []
    if hasattr(gmerged, 'geoms'):
        return [g for g in gmerged.geoms if isinstance(g, Polygon)]
    return [gmerged] if isinstance(gmerged, Polygon) else []


def _wedge_fits_part(edge, part):
    """True if this part holds enough of the door's wedge to own the claim.

    See: docs/commentary/tes5_import_navmesh.md#door-claim-is-single-and-gated
    """
    if len(edge) <= 2 or edge[2] is None:
        return True
    try:
        from shapely.geometry import Polygon as _DCP
        wedge = _DCP([edge[0], edge[1], edge[2]])
        if (wedge.is_valid and wedge.area > 1.0
                and part.intersection(wedge).area
                < DOOR_CLAIM_MIN_FRAC * wedge.area):
            return False
    except Exception:
        pass
    return True


def _storey_holds_door(edge, group):
    """True if this sheet has a surface at the door's own height under it.

    See: docs/commentary/tes5_import_navmesh.md#door-claim-is-single-and-gated
    """
    if len(edge) <= 3 or edge[3] is None:
        return True
    mx = 0.5 * (edge[0][0] + edge[1][0])
    my = 0.5 * (edge[0][1] + edge[1][1])
    lv = _levels_at(group, mx, my)
    return not lv or any(abs(q - edge[3]) <= STOREY_GAP_Z for q in lv)


def _claim_door_edges(part, group, door_edges, claimed_ids):
    """The door base lines this part owns, consuming each claim exactly once.

    See: docs/commentary/tes5_import_navmesh.md#door-claim-is-single-and-gated
    """
    fixed = []
    for ei, e in enumerate(door_edges):
        if ei in claimed_ids or not _door_edge_on_part(e, part):
            continue
        if not _wedge_fits_part(e, part) or not _storey_holds_door(e, group):
            continue
        claimed_ids.add(ei)
        fixed.append(e)
    return fixed


def _mesh_one_part(part, group, gseeds, door_edges, claimed_ids):
    """Triangulate one part of one sheet into (verts, tris), or ([], []).

    See: docs/commentary/tes5_import_navmesh.md#door-apex-inherits-base-levels
    """
    fixed = _claim_door_edges(part, group, door_edges, claimed_ids)
    land = _land_of(group)
    target = _target_edge(group)
    v2, t2 = _triangulate(part, target, fixed_edges=fixed, steep_seeds=gseeds,
                          land=land, max_edge=LAND_MAX_EDGE_RATIO * target)
    if not t2:
        return [], []
    levels = _levels_batch(group, v2)
    _apply_door_apex_levels(v2, levels, fixed)
    return _emit_surfaces(v2, t2, levels, land)


def _mesh_sheets(sheets, ctx, door_edges):
    """Triangulate every sheet; returns (verts, tris, vert_src).

    See: docs/commentary/tes5_import_navmesh.md#union-has-no-storey-buckets
    """
    from shapely.geometry import Polygon
    verts, tris, vert_src = [], [], []
    claimed_ids = set()
    for gi, group in enumerate(sheets):
        gmerged = _sheet_coverage(group, gi, ctx)
        if gmerged is None:
            continue
        gmerged = _close_land_slits(gmerged, group, ctx['slit_ok'])
        ctx['claimed'].append((gmerged, group))
        parts = _sheet_parts(gmerged, ctx['wall_cut'])
        group = list(group) + ctx['junction_strips'].get(gi, [])
        gseeds = _ribbon_seeds(group, _target_edge(group))
        for part in parts:
            if not isinstance(part, Polygon) or part.area < 1.0:
                continue
            v3, t3 = _mesh_one_part(part, group, gseeds, door_edges,
                                    claimed_ids)
            base = len(verts)
            verts.extend(v3)
            vert_src.extend([base] * len(v3))
            tris.extend((a + base, b + base, c + base) for (a, b, c) in t3)
    return verts, tris, vert_src


def _merged_coverage(strips, cell_bounds, wall_cut):
    """The flattened union of every ribbon, clipped and wall-cut, or None.

    See: docs/commentary/tes5_import_navmesh.md#union-inputs-and-clipping
    """
    from shapely.geometry import box
    from shapely.ops import unary_union
    polys = [p for p in (_ribbon_polygon(s) for s in strips)
             if p.is_valid and not p.is_empty]
    if not polys:
        return None
    merged = unary_union(polys)
    if merged.is_empty:
        return None
    if cell_bounds is not None:
        minx, miny, maxx, maxy = cell_bounds
        merged = merged.intersection(box(minx, miny, maxx, maxy))
        if merged.is_empty:
            return None
    if wall_cut is not None:
        try:
            cut = merged.difference(wall_cut)
            if not cut.is_empty:
                merged = cut
        except Exception:
            pass
    return merged


def _finish_union(verts, tris, strips, node_pts, node_half, stitch_nodes,
                  probe_only):
    """Weld, merge, stitch and re-zip the sheet meshes into one surface.

    See: docs/commentary/tes5_import_navmesh.md#t-junctions-are-split-three-times
    """
    tris = split_t_junctions(verts, tris)
    verts, tris = _merge_at_pathgrid_nodes(verts, tris, node_pts, node_half)
    tris = _destack(verts, tris)
    tris = stitch_shared_nodes(verts, tris, stitch_nodes)
    tris = split_t_junctions(verts, tris)
    tris = _drop_walls(verts, tris, strips)
    tris = split_t_junctions(verts, tris)
    if probe_only:
        return verts, tris
    tris = _fill_boundary_notches(verts, tris, strips)
    return verts, _drop_point_attached(tris)


def build_union_mesh(strips, extra_strips=None, door_edges=None,
                     cell_bounds=None, wall_cut=None, probe_only=False,
                     slit_ok=None):
    """Union the corridor ribbons per storey and retriangulate.

    Returns (verts, tris) with 3D vertices.  Coverage is the exact union of the
    ribbons and the triangles do not overlap, both by construction.
    `extra_strips` are door footprint strips joining as ordinary ground,
    `door_edges` the door base lines forced to be triangle edges, `cell_bounds`
    the exterior rectangle the coverage is clipped to.
    See: docs/commentary/tes5_import_navmesh.md#union-mesh-driver
    """
    _ribbon_cache_clear()
    if not strips:
        return [], []
    strips = list(strips) + list(extra_strips or ())
    if _merged_coverage(strips, cell_bounds, wall_cut) is None:
        return [], []

    sheets = _split_plan_overlaps(_storey_groups(strips))
    node_pts, node_half = _node_geometry(strips)
    node_sheets, node_owner = _sheet_node_sets(sheets)
    junction_extra, junction_strips, junction_drop = _junction_transfers(
        strips, sheets, node_pts, node_half, node_owner)

    ctx = {'junction_extra': junction_extra, 'junction_strips': junction_strips,
           'junction_drop': junction_drop, 'cell_bounds': cell_bounds,
           'wall_cut': wall_cut, 'claimed': [], 'slit_ok': slit_ok}
    verts, tris, vert_src = _mesh_sheets(sheets, ctx, door_edges or [])

    stitch_nodes = [(node_pts[i][0], node_pts[i][1])
                    for i, gset in node_sheets.items()
                    if len(gset) >= 2 and i in node_pts]
    verts, tris = _weld_sheets(verts, tris, src=vert_src)
    return _finish_union(verts, tris, strips, node_pts, node_half,
                         stitch_nodes, probe_only)

# T-junction split tolerances (module-level so diagnostics can A/B them).
# TSPLIT_TOL: plan radius for INTERIOR hanging vertices.  TSPLIT_CRACK_TOL:
# plan radius when the hit vertex is itself on the boundary (crack zipper).
# TSPLIT_Z_TOL: the z window — 12u seals the measured 3-8u stair-fold cracks;
# MAX_CLIMB grabbed genuine fold vertices and minted overlaps.
TSPLIT_TOL = 2.0
TSPLIT_CRACK_TOL = 6.0
TSPLIT_Z_TOL = 12.0




def _apply_door_apex_levels(v2, levels, door_edges):
    """Give each reserved door triangle's APEX the levels of its base line.

    The apex sits inside the reserved hole, so nothing covers it and it has no
    level of its own; without this it is dropped by _emit_surfaces along with
    the whole door triangle.
    """
    if not door_edges:
        return
    idx = {}
    for i, p in enumerate(v2):
        idx.setdefault((round(p[0], 3), round(p[1], 3)), i)
    for e in door_edges:
        p0, p1 = e[0], e[1]
        door_z = e[3] if len(e) > 3 and e[3] is not None else None
        i0 = idx.get((round(p0[0], 3), round(p0[1], 3)))
        i1 = idx.get((round(p1[0], 3), round(p1[1], 3)))
        if i0 is None or i1 is None:
            continue
        # A doorway can be WIDER than the corridor ribbon crossing it, so a
        # base endpoint may stand on ground no strip covers (Arvena's
        # upstairs door: base corners at x=-368/-272, the crossing ribbon only
        # spans -360..-280) — it then has no level, is dropped by
        # _emit_surfaces, and the whole door triangle goes with it.  The door
        # knows its own storey; seed the endpoints with it.
        if door_z is not None:
            for ii in (i0, i1):
                if not any(abs(q - door_z) <= SAME_SURFACE_Z
                           for q in levels[ii]):
                    levels[ii] = sorted(set(list(levels[ii])
                                            + [float(door_z)]))
        base_lv = sorted(set(list(levels[i0]) + list(levels[i1])))
        if not base_lv:
            continue
        # The corners the door introduces must land on the DOOR's storey, not
        # on every storey the base endpoints touch (a base endpoint shared
        # with a stacked floor carries both heights).
        lv_door = [q for q in base_lv
                   if door_z is None or abs(q - door_z) <= STOREY_GAP_Z]
        lv_door = lv_door or base_lv
        # The apex is known exactly (the analytic wedge corner) — give it the
        # door's levels directly when nothing covers it.
        reach = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if len(e) > 2 and e[2] is not None:
            ia = idx.get((round(e[2][0], 3), round(e[2][1], 3)))
            if ia is not None and not levels[ia]:
                levels[ia] = list(lv_door)
            reach = max(reach, math.hypot(
                e[2][0] - 0.5 * (p0[0] + p1[0]),
                e[2][1] - 0.5 * (p0[1] + p1[1])) + 4.0)
        # Any other level-less vertex near this base line was introduced by
        # the reservation (a hole-outline corner) and stands on the same
        # ground as the base.
        mx = 0.5 * (p0[0] + p1[0])
        my = 0.5 * (p0[1] + p1[1])
        for i, p in enumerate(v2):
            if levels[i]:
                continue
            if math.hypot(p[0] - mx, p[1] - my) <= reach:
                levels[i] = list(lv_door)


def _storeys_of(lv):
    """Cluster a sorted level list into storey bands on STOREY_GAP_Z.

    See: docs/commentary/tes5_import_navmesh.md#levels-recluster-on-the-storey-gap
    """
    if not lv:
        return []
    out = [[lv[0]]]
    for z in lv[1:]:
        if z - out[-1][-1] <= STOREY_GAP_Z:
            out[-1].append(z)
        else:
            out.append([z])
    return out


def _reaches(bands, z):
    """Does a corner with these bands have ground on the surface at z?

    See: docs/commentary/tes5_import_navmesh.md#reach-tolerance-is-one-step
    """
    for (lo, hi) in bands:
        if lo - REACH_TOL <= z <= hi + REACH_TOL:
            return True
    return False


def _triangle_surfaces(reps_all, tri):
    """The storey heights every corner of `tri` has ground on.

    See: docs/commentary/tes5_import_navmesh.md#every-corner-proposes-its-own-storeys
    """
    present = [x for x in (reps_all[tri[0]], reps_all[tri[1]],
                           reps_all[tri[2]]) if x]
    if not present:
        return ()
    proposals = sorted({z for bands in present for (lo, hi) in bands
                        for z in (lo, hi)})
    surfaces = []
    for z in proposals:
        if not all(_reaches(bands, z) for bands in present):
            continue
        if not surfaces or z - surfaces[-1] > STOREY_GAP_Z:
            surfaces.append(z)
    return tuple(surfaces)


def _bare_corner_clusters(t2, levels, tri_surfaces):
    """Storey clusters for corners that carry no level of their own.

    See: docs/commentary/tes5_import_navmesh.md#level-less-corners-cluster-their-own-surfaces
    """
    bare = {}
    for ti, (a, b, c) in enumerate(t2):
        for z in tri_surfaces[ti]:
            for k in (a, b, c):
                if not levels[k]:
                    bare.setdefault(k, []).append(z)
    return {k: _storeys_of(sorted(zs)) for k, zs in bare.items()}


def _slot_of(corner_bands, bare_clusters, k, z):
    """Which STOREY band of corner k this triangle's surface belongs to.

    See: docs/commentary/tes5_import_navmesh.md#vertex-key-is-corner-and-slot
    """
    bands = corner_bands[k] or bare_clusters.get(k)
    if not bands:
        return 0
    best_i, best_d = 0, None
    for i, band in enumerate(bands):
        for x in band:
            d = x - z if x >= z else z - x
            if best_d is None or d < best_d:
                best_d, best_i = d, i
    return best_i


def _emit_vertex(state, k, fallback):
    """The single vertex for corner k on storey k[1], minting it if new.

    See: docs/commentary/tes5_import_navmesh.md#vertex-height-is-the-band-median
    """
    got = state['vid'].get(k)
    if got is not None:
        return got
    bands = (state['corner_bands'][k[0]]
             or state['bare_clusters'].get(k[0]) or ())
    if 0 <= k[1] < len(bands):
        band = sorted(bands[k[1]])
        zz = band[len(band) // 2]
    else:
        zz = fallback
    verts = state['verts']
    got = len(verts)
    verts.append([float(state['v2'][k[0]][0]), float(state['v2'][k[0]][1]),
                  float(zz)])
    state['vid'][k] = got
    return got


def _emit_triangles(state, keyed):
    """Lift every keyed triangle, dropping duplicate windings and slivers.

    See: docs/commentary/tes5_import_navmesh.md#emission-drops-duplicates-and-slivers
    """
    verts = state['verts']
    tris, seen = [], set()
    for (ka, kb, kc, z) in keyed:
        ia = _emit_vertex(state, ka, z)
        ib = _emit_vertex(state, kb, z)
        ic = _emit_vertex(state, kc, z)
        if ia == ib or ib == ic or ia == ic:
            continue
        key = tuple(sorted((ia, ib, ic)))
        if key in seen:
            continue
        pa, pb, pc = verts[ia], verts[ib], verts[ic]
        area2 = abs((pb[0] - pa[0]) * (pc[1] - pa[1]) -
                    (pb[1] - pa[1]) * (pc[0] - pa[0]))
        if area2 * 0.5 < params.MIN_XY_FOOTPRINT:
            continue
        seen.add(key)
        tris.append((ia, ib, ic))
    return tris


def _emit_surfaces(v2, t2, levels, land=None):
    """Lift a 2D triangulation onto its walkable surfaces, WITHOUT tearing.

    Every 2D triangle is emitted once per surface beneath it; a triangle whose
    corners share no storey is not emitted at all.  Corners are compared in
    heights ABOVE `land`, so a hillside is one surface.
    See: docs/commentary/tes5_import_navmesh.md#surface-emission
    """
    ground = [land.z(p[0], p[1]) if land is not None else 0.0 for p in v2]
    levels = [[z - g for z in lv] for lv, g in zip(levels, ground)]
    corner_bands = [_storeys_of(sorted(lv)) for lv in levels]
    reps_all = [[(min(b), max(b)) for b in bands] for bands in corner_bands]
    tri_surfaces = [_triangle_surfaces(reps_all, tri) for tri in t2]
    bare_clusters = _bare_corner_clusters(t2, levels, tri_surfaces)

    keyed = []
    for ti, (a, b, c) in enumerate(t2):
        for z in tri_surfaces[ti]:
            keyed.append(((a, _slot_of(corner_bands, bare_clusters, a, z)),
                          (b, _slot_of(corner_bands, bare_clusters, b, z)),
                          (c, _slot_of(corner_bands, bare_clusters, c, z)), z))

    state = {'v2': v2, 'corner_bands': corner_bands,
             'bare_clusters': bare_clusters, 'vid': {}, 'verts': []}
    tris = _emit_triangles(state, keyed)
    for v in state['verts']:
        v[2] += land.z(v[0], v[1]) if land is not None else 0.0
    return state['verts'], tris

def _walked_line_index(strips, cellsz):
    """Bucket sampled points along every ribbon centerline by plan cell."""
    lines = {}
    for s in strips:
        ax, ay, az = s['a'][0], s['a'][1], s['a'][2]
        bx, by, bz = s['b'][0], s['b'][1], s['b'][2]
        n = max(1, int(math.hypot(bx - ax, by - ay) / cellsz) + 1)
        for i in range(n + 1):
            f = i / n
            x, y = ax + (bx - ax) * f, ay + (by - ay) * f
            lines.setdefault((int(x // cellsz), int(y // cellsz)),
                             []).append((x, y, az + (bz - az) * f))
    return lines


def _near_line(lines, cellsz, x, y, z):
    """True if a walked pathgrid line passes within cellsz of (x, y, z)."""
    gx, gy = int(x // cellsz), int(y // cellsz)
    for ddx in (-1, 0, 1):
        for ddy in (-1, 0, 1):
            for (px, py, pz) in lines.get((gx + ddx, gy + ddy), ()):
                if ((px - x) ** 2 + (py - y) ** 2 <= cellsz * cellsz
                        and abs(pz - z) <= STOREY_GAP_Z):
                    return True
    return False


def _plan_index(verts, tris):
    """(STRtree, triangle ids, polygons) over the mesh's plan footprints."""
    from shapely import STRtree
    from shapely.geometry import Polygon
    geoms, gmap = [], []
    for ti, t in enumerate(tris):
        pa, pb, pc = verts[t[0]], verts[t[1]], verts[t[2]]
        try:
            pg = Polygon([(pa[0], pa[1]), (pb[0], pb[1]), (pc[0], pc[1])])
        except Exception:
            continue
        if pg.is_valid and pg.area > 1.0:
            geoms.append(pg)
            gmap.append(ti)
    return (STRtree(geoms) if geoms else None), gmap, geoms


def _overlaps_existing(verts, tris, tri, index):
    """True if `tri` would stack on ground already meshed at its height.

    See: docs/commentary/tes5_import_navmesh.md#notch-fill-must-not-stack
    """
    from shapely.geometry import Polygon
    tree, gmap, polys = index
    if tree is None:
        return False
    pa, pb, pc = verts[tri[0]], verts[tri[1]], verts[tri[2]]
    try:
        cand = Polygon([(pa[0], pa[1]), (pb[0], pb[1]), (pc[0], pc[1])])
    except Exception:
        return True
    if not cand.is_valid or cand.area <= 1.0:
        return True
    zlo = min(pa[2], pb[2], pc[2])
    zhi = max(pa[2], pb[2], pc[2])
    for gi in tree.query(cand).tolist():
        ti = gmap[gi]
        if set(tris[ti]) & set(tri):
            continue
        tz = [verts[i][2] for i in tris[ti]]
        if min(tz) > zhi + STOREY_GAP_Z or max(tz) < zlo - STOREY_GAP_Z:
            continue
        try:
            if cand.intersection(polys[gi]).area > 4.0:
                return True
        except Exception:
            return True
    return False


def _border_edge_counts(tris):
    """(edge use counts, the singly-owned border edges)."""
    counts = {}
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    return counts, [e for e, c in counts.items() if c == 1]


def _notch_triangle(verts, counts, apex, ends, lines, cellsz):
    """The wound fill triangle for this apex, or None if it is not a notch.

    See: docs/commentary/tes5_import_navmesh.md#notch-fill-has-four-gates
    """
    p, q = ends
    va, vp, vq = verts[apex], verts[p], verts[q]
    gap = math.dist(vp[:2], vq[:2])
    if gap < 1e-6 or gap > NOTCH_MAX_MOUTH:
        return None
    side = min(math.dist(va[:2], vp[:2]), math.dist(va[:2], vq[:2]))
    if side < 1e-6 or side < gap * NOTCH_MIN_DEPTH_RATIO:
        return None
    if counts.get((min(p, q), max(p, q)), 0) >= 1:
        return None
    if not _near_line(lines, cellsz, (va[0] + vp[0] + vq[0]) / 3.0,
                      (va[1] + vp[1] + vq[1]) / 3.0,
                      (va[2] + vp[2] + vq[2]) / 3.0):
        return None
    cross = ((vp[0] - va[0]) * (vq[1] - va[1])
             - (vq[0] - va[0]) * (vp[1] - va[1]))
    if abs(cross) < 1.0:
        return None
    return (apex, p, q) if cross > 0 else (apex, q, p)


def _notch_round(verts, tris, lines, cellsz):
    """One pass of notch filling; returns the triangles it added."""
    counts, border = _border_edge_counts(tris)
    if not border:
        return []
    index = _plan_index(verts, tris)
    at = {}
    for (a, b) in border:
        at.setdefault(a, []).append(b)
        at.setdefault(b, []).append(a)
    added, used = [], set()
    for apex, ends in at.items():
        if len(ends) != 2 or apex in used:
            continue
        if ends[0] in used or ends[1] in used:
            continue
        tri = _notch_triangle(verts, counts, apex, ends, lines, cellsz)
        if tri is None or _overlaps_existing(verts, tris, tri, index):
            continue
        added.append(tri)
        used.update(tri)
        key = (min(ends), max(ends))
        counts[key] = counts.get(key, 0) + 1
    return added


def _fill_boundary_notches(verts, tris, strips):
    """Close narrow V-shaped notches bitten out of the walkable surface.

    See: docs/commentary/tes5_import_navmesh.md#notch-fill-and-levels
    """
    if not strips:
        return tris
    tris = [tuple(t) for t in tris]
    cellsz = NOTCH_NEAR_LINE
    lines = _walked_line_index(strips, cellsz)
    for _round in range(2):
        added = _notch_round(verts, tris, lines, cellsz)
        if not added:
            break
        tris.extend(added)
    return tris

# A notch mouth wider than this is a room corner, not a bite out of the mesh.
NOTCH_MAX_MOUTH = 160.0
# ...and it must be DEEP next to that mouth (a slim V, not a shallow wedge).
# NOTE the direction: side >= mouth * ratio.  The first version had this
# backwards (mouth > side * ratio) and so never fired on the very notches it
# was written for — the stair V-cracks, whose mouth is 15u against 65u sides.
NOTCH_MIN_DEPTH_RATIO = 1.2
# How close the fill must be to a walked pathgrid line to be justified.  64
# was under half a ribbon width, so a notch bitten out of the SIDE of a
# corridor (which is exactly where they appear) fell outside it and was never
# filled; 192 covers the full ribbon plus its grow margin.
NOTCH_NEAR_LINE = 192.0


def _drop_point_attached(tris):
    """Drop triangles that touch the rest of the mesh at a single VERTEX only.

    Iterated: removing one can leave its neighbour edge-isolated in turn.
    See: docs/commentary/tes5_import_navmesh.md#point-attached-cannot-be-walked-onto
    """
    tris = [tuple(t) for t in tris]
    for _round in range(4):
        counts = {}
        for t in tris:
            for k in range(3):
                a, b = t[k], t[(k + 1) % 3]
                key = (a, b) if a < b else (b, a)
                counts[key] = counts.get(key, 0) + 1
        keep = []
        for t in tris:
            shared = False
            for k in range(3):
                a, b = t[k], t[(k + 1) % 3]
                key = (a, b) if a < b else (b, a)
                if counts.get(key, 0) >= 2:
                    shared = True
                    break
            if shared:
                keep.append(t)
        if len(keep) == len(tris):
            break
        tris = keep
    return tris


def _levels_batch(strips, points):
    """_levels_at for MANY points at once, natively.  Returns a list of lists.

    See: docs/commentary/tes5_import_navmesh.md#levels-are-batched
    """
    from ._native_loader import load_native
    native = load_native('_navgrow_native')

    rows = []
    poly = []
    prof = []
    land = _land_of(strips)
    for s_ in strips:
        p = s_.get('poly')
        off, n = 0, 0
        if p is not None:
            off, n = len(poly), len(p)
            poly.extend(p)
        pr = s_.get('prof')
        proff, prn = 0, 0
        if pr is not None and len(pr) >= 2:
            proff, prn = len(prof), len(pr)
            prof.extend(pr)
        a_, b_ = s_['a'], s_['b']
        rows.append((a_[0], a_[1], a_[2], b_[0], b_[1], b_[2],
                     float(s_['half']), float(off), float(n),
                     float(proff), float(prn),
                     float(s_.get('land') is not None)))
    sarr = (np.asarray(rows, dtype=np.float64) if rows
            else np.zeros((0, 12), dtype=np.float64))
    parr = (np.asarray(poly, dtype=np.float64).reshape(-1, 2) if poly
            else np.zeros((0, 2), dtype=np.float64))
    farr = (np.asarray(prof, dtype=np.float64).reshape(-1, 3) if prof
            else np.zeros((0, 3), dtype=np.float64))
    qarr = (np.asarray(points, dtype=np.float64).reshape(-1, 2) if len(points)
            else np.zeros((0, 2), dtype=np.float64))
    if land is None:
        return native.levels_at(sarr, parr, qarr, float(SAME_SURFACE_Z), farr)
    return native.levels_at(sarr, parr, qarr, float(SAME_SURFACE_Z), farr,
                            land.grid, land.ox, land.oy)


def _levels_at(strips, px, py):
    """Distinct surface heights at (px, py): one per storey covering it.

    The heights of every corridor whose ribbon covers the point are clustered;
    a gap larger than SAME_SURFACE_Z starts a new surface.  A stair ribbon and
    the floor it meets fall in one cluster (they differ by a few units there),
    while the floor it flies over is hundreds away and forms its own.

    A poly strip owns exactly its OUTLINE, so admission is containment; `half`
    is a radius only for a fixed-width rectangle.
    See: docs/commentary/tes5_import_navmesh.md#poly-strips-admit-by-containment
    """
    zs = []
    for s in strips:
        if s.get('poly') is not None:
            hit = _distance_to(s, px, py) <= 1e-6      # 0 == inside the outline
        else:
            hit = _distance_to(s, px, py) <= s['half'] + 1e-6
        if hit:
            zs.append(_height_on(s, px, py))
    if not zs:
        return []
    zs.sort()
    out = [[zs[0]]]
    for z in zs[1:]:
        if z - out[-1][-1] <= SAME_SURFACE_Z:
            out[-1].append(z)
        else:
            out.append([z])
    return [sum(g) / len(g) for g in out]


