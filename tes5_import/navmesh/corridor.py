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


import numpy as np

from . import native_loader, corridor_grow, params, world


# ---------------------------------------------------------------------------
# Walkable surface sampler (the only collision query Phase 1 needs)
# ---------------------------------------------------------------------------

def _surface_sampler(walkable):
    """f(x, y, near_z) -> walkable height at (x,y) nearest near_z, or None.

    The returned callable carries a `.layers(x, y)` attribute listing every
    distinct walkable height there, ascending, deduped at 2u.
    """
    if not len(walkable):
        return None
    index = corridor_grow.native_index(walkable)
    surface = index.surface

    def sample(x, y, near_z):
        """The walkable height at (x, y) closest to near_z, or None."""
        return surface(x, y, near_z)

    sample.layers = index.layers
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
# Public entry
# ---------------------------------------------------------------------------

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


def ccw_in_plan(verts, tris):
    """Flip any triangle wound CW in plan; the mesh is a heightfield.

    See: docs/commentary/tes5_import_navmesh.md#winding-must-be-ccw-in-plan
    """
    return [((t[0], t[2], t[1])
             if ((verts[t[1]][0] - verts[t[0]][0])
                 * (verts[t[2]][1] - verts[t[0]][1])
                 - (verts[t[2]][0] - verts[t[0]][0])
                 * (verts[t[1]][1] - verts[t[0]][1])) < 0 else t)
            for t in tris]


def _native_corridor_cell(cell, nodes, edges, lone, doors, obstacles):
    """build_corridors natively, collision gather and obstacle bridge included.

    `cell` is (refr_recs, base_model_by_fid, get_collision, land_rec, origin_x,
    origin_y, door_bases, water_z).
    See: docs/plans/navmesh_native_rewrite.md#stages
    """
    refr_recs, base_model, get_collision, land_rec, origin_x, origin_y, door_bases, water_z = cell
    land = world.land_field(land_rec, origin_x, origin_y)
    clip = (None if land_rec is None
            else (origin_x, origin_y, origin_x + 4096.0, origin_y + 4096.0))
    door_rows = [(x, y, z, r, float(bool(tp)), float(w or 0.0))
                 for (x, y, z, r, tp, w) in (doors or ())]
    obstacle_models = {world.base_fid(r): key for r, key in obstacles or ()}
    return native_loader.native_core().corridor_cell(
        np.asarray([tuple(map(float, n[:3])) for n in nodes], dtype=np.float64).reshape(-1, 3),
        np.asarray(edges, dtype=np.int32).reshape(-1, 2), sorted(lone),
        np.asarray(door_rows, dtype=np.float64).reshape(-1, 6),
        None if land is None else land.grid, 0.0 if land is None else land.ox,
        0.0 if land is None else land.oy, clip,
        *world.placements(refr_recs, base_model or {}, get_collision, door_bases or ()),
        *world.placements([r for r, _k in obstacles or ()], obstacle_models, get_collision),
        water_z)


def build_corridors(refr_recs, base_model_by_fid, get_collision, nodes, edges,
                    land_rec=None, origin_x=0.0, origin_y=0.0, doors=None,
                    door_bases=None, lone=(), water_z=None, obstacles=()):
    """Phase-1 corridor navmesh for one cell: (verts, tris, ledges) lists.

    doors: [(x, y, z, rot_z, is_teleport, width), ...] pivot-corrected door
    centers.  door_bases: low-24 DOOR base FormIDs contributing no collision.
    lone: edgeless nodes that still get a disc (spawn_nodes).  water_z: or None.
    obstacles: (placement, model key) pairs `edges` is bridged across first.
    ledges: [(upper_tri, lower_tri, drop), ...] for NVNM Ledge Up/Down links.
    See: docs/commentary/tes5_import_navmesh.md#ribbon-construction
    """
    cell = (refr_recs, base_model_by_fid, get_collision, land_rec, origin_x, origin_y,
            door_bases, water_z)
    if not nodes:
        return [], [], []
    return _native_corridor_cell(cell, nodes, edges, lone, doors, obstacles)
