"""Phase-1 corridor-ribbon navmesh generation.

THE MODEL, in one line:

    THE PATHGRID IS THE MESH.

Bethesda's pathgrid is the only part of the input that ASSERTS "an actor walks
here".  Instead of re-discovering walkable surface from collision (voxelize /
contour / region-flood) and then fighting to keep the result connected across
the seams that discovery introduces, we build the navmesh DIRECTLY on the
pathgrid: a flat, fixed-width ribbon of triangles centred on every pathgrid
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

Design principles (see docs/navmesh_corridor_redesign.md):
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

from . import params, world


# ---------------------------------------------------------------------------
# Walkable surface sampler (the only collision query Phase 1 needs)
# ---------------------------------------------------------------------------

def _surface_sampler(walkable):
    """f(x, y, near_z) -> walkable-collision height at (x,y) nearest near_z, or
    None.  Point-in-triangle over the walkable soup, bucketed into a coarse XY
    grid so each query only tests nearby triangles.
    """
    W = np.asarray(walkable, dtype=float).reshape(-1, 3, 3)
    if not len(W):
        return None
    cell = 128.0
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

    def sample(x, y, near_z):
        gx = int((x - minx) // cell)
        gy = int((y - miny) // cell)
        best = None
        for i in grid.get((gx, gy), ()):
            a, b, c = W[i]
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-6:
                continue
            l0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d
            l1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d
            l2 = 1.0 - l0 - l1
            if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
                continue
            z = l0 * a[2] + l1 * b[2] + l2 * c[2]
            if best is None or abs(z - near_z) < abs(best - near_z):
                best = z
        return best

    return sample


def _snap_node_z(sample, x, y, z):
    """Node Z snapped DOWN onto walkable collision (principle 2).

    The pathgrid hovers above the walked surface, and the navmesh must sit ON
    it.  Snap toward the surface only within a plausible window; never teleport
    to a distant floor, never rise onto an object standing on the floor.
    """
    if sample is None:
        return z
    s = sample(x, y, z)
    if s is None:
        return z                                   # trust the pathgrid
    if s <= z + params.SEED_Z_TOLERANCE and s >= z - params.SEED_SNAP:
        return s                                   # within window: sit on it
    if s < z:
        return z - params.SEED_SNAP                # far below: clamp the drop
    return z                                       # surface above node: stay


# ---------------------------------------------------------------------------
# Ribbon generation
# ---------------------------------------------------------------------------

def _build_corridor_strips(nodes, edges, node_z):
    """One corridor per pathgrid edge.  Returns a list of dicts, each:

        {'edge': (i, j),
         'a': (ax, ay, az), 'b': (bx, by, bz),   # centerline ends (extended)
         'u': (ux, uy), 'w': (wx, wy),           # along / perpendicular units
         'half': half}

    'a'/'b' are the centerline endpoints AFTER dead-end extension, carrying the
    line's own slope (principle 2).  The corridor is the flat rectangle of
    half-width `half` about the segment a->b.  Corridors are NOT yet a shared
    mesh — corridor_union takes their boolean union and retriangulates it;
    keeping them as parametric strips lets it recover each vertex's height.
    """
    half = params.RIBBON_HALF_WIDTH
    ext = params.RIBBON_END_EXTEND

    # Degree of every node, so only DEAD ENDS get the end extension.  Extending
    # past a node that another corridor also uses puts this corridor's stub
    # entirely inside that corridor — guaranteed double coverage at every
    # junction, and the dominant residual overlap (collinear pairs sharing a
    # node overlapped for 22 triangles each).  At a dead end there is no other
    # corridor, so the stub is the only thing reaching the wall or door ahead
    # and it costs nothing.
    degree = {}
    for (i, j) in edges:
        degree[i] = degree.get(i, 0) + 1
        degree[j] = degree.get(j, 0) + 1

    strips = []
    for (i, j) in edges:
        if i >= len(nodes) or j >= len(nodes) or i == j:
            continue
        ax, ay = nodes[i][0], nodes[i][1]
        bx, by = nodes[j][0], nodes[j][1]
        az, bz = node_z[i], node_z[j]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-4:
            continue
        ux, uy = dx / length, dy / length
        dz = bz - az
        # Ribbons run node to node; at a DEAD END the ribbon extends past the
        # node, since nothing else reaches the wall or door ahead.  Overlap
        # between ribbons is resolved by the union, so a ribbon never needs to
        # stop short of a junction.
        ea = ext if degree.get(i, 0) <= 1 else 0.0
        eb = ext if degree.get(j, 0) <= 1 else 0.0
        strips.append({
            'edge': (i, j),
            # true node centreline points (where the node anchor is)
            'na': (ax, ay, az), 'nb': (bx, by, bz),
            # endpoints the quad actually reaches
            'a': (ax - ux * ea, ay - uy * ea, az - dz * (ea / length)),
            'b': (bx + ux * eb, by + uy * eb, bz + dz * (eb / length)),
            'u': (ux, uy), 'w': (-uy, ux), 'half': half, 'len': length,
        })
    return strips


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def build_corridors(refr_recs, base_model_by_fid, get_collision, nodes, edges,
                    land_rec=None, origin_x=0.0, origin_y=0.0, doors=None):
    """Phase-1 corridor navmesh for one cell.  Returns (verts, tris) lists.

    doors: [(x, y, z, rot_z, is_teleport), ...] pivot-corrected door centres.
    """
    if not nodes or not edges:
        return [], []

    walkable, blocking, land_walk = world.gather_cell_geometry(
        refr_recs or [], base_model_by_fid or {}, get_collision,
        land_rec=land_rec, origin_x=origin_x, origin_y=origin_y,
        split_land=True)
    if land_walk is not None and len(land_walk):
        walkable = (np.concatenate([walkable, land_walk])
                    if len(walkable) else land_walk)

    sample = _surface_sampler(walkable)

    # Node heights: snap each node down onto walkable collision.
    node_z = [_snap_node_z(sample, nodes[i][0], nodes[i][1], nodes[i][2])
              for i in range(len(nodes))]

    from . import corridor_doors, corridor_clean, corridor_union

    # One corridor (a rectangular ribbon on the pathgrid line's own slope) per
    # edge, then a BOOLEAN UNION of those ribbons per storey, retriangulated.
    #
    # The union is coverage-preserving by construction: its area is exactly the
    # ground the ribbons cover, and a triangulation of it cannot self-overlap.
    # Cutting the ribbons pairwise instead (trim, weld, patch the junction) is an
    # approximation that has to handle every configuration — end-to-end,
    # crossing, wedge, collinear — and every case it got wrong appeared as lost
    # ground or stacked sheets.
    #
    # Storeys are grouped by SHARED NODES with agreeing heights, so a staircase
    # stays one storey with the floors it joins while two floors stacked in plan
    # view are unioned separately and never flattened together.
    corridors = _build_corridor_strips(nodes, edges, node_z)

    # Exterior meshes are clipped to their own cell rectangle so a cross-seam
    # ribbon (built from a PGRI InterCell link, which reaches into the neighbour
    # cell) stops exactly at the boundary plane — leaving a border edge on the
    # seam for build_edge_links to stitch, without importing neighbour geometry.
    cell_clip = None
    if land_rec is not None:
        cell_clip = (origin_x, origin_y, origin_x + 4096.0, origin_y + 4096.0)

    # Doors are computed FIRST, on the raw ribbon union: each door's footprint
    # (the flat quad bridging its base line to the nearest corridor edge) is fed
    # into the union as ordinary ground, and its BASE LINE is forced to be a
    # triangle edge.  The union then resolves any overlap by construction —
    # nothing is deleted — and every door comes out as ONE large triangle whose
    # long side sits on the door line, matching vanilla Skyrim.
    door_list = [(x, y, z, r, tp) for (x, y, z, r, tp) in (doors or ())]
    door_strips = []
    door_edges = []
    if door_list:
        rv, rt = corridor_union.build_union_mesh(corridors, cell_bounds=cell_clip)
        if rt:
            for fp in corridor_doors.door_footprints(rv, rt, door_list):
                door_strips.append(corridor_union._poly_strip(fp['poly'],
                                                              fp['z']))
                door_edges.append(fp['base'])

    verts, tris = corridor_union.build_union_mesh(
        corridors, extra_strips=door_strips, door_edges=door_edges,
        cell_bounds=cell_clip)
    if not tris:
        return [], []

    cs = params.CS_EXTERIOR if land_rec is not None else params.CS
    # For dropping unreachable fringe scraps, a component is KEPT when it can
    # reach another cell — via a door, or (exterior) by touching the cell
    # border where a worldspace edge-link continues it.  Pass the door centres
    # and, for an exterior cell, its world-space bounds.
    door_xy = [(x, y, z) for (x, y, z, r, tp) in door_list]
    cell_bounds = None
    if land_rec is not None:
        cell_bounds = (origin_x, origin_y, origin_x + 4096.0, origin_y + 4096.0)
    verts, tris = corridor_clean.finalize(verts, tris, cs=cs,
                                          doors=door_xy, cell_bounds=cell_bounds)

    return ([tuple(float(c) for c in v) for v in verts],
            [tuple(int(i) for i in t) for t in tris])
