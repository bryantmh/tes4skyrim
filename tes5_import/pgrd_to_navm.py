"""Convert TES4 PGRD (PathGrid) records to TES5 NAVM (NavMesh) records.

=============================================================================
RESEARCH NOTES — TES4 PGRD FORMAT
=============================================================================

Source: external/xEdit/Core/wbDefinitionsTES4.pas

PGRD is a top-level record stored inside a CELL group.  One per cell.

Subrecords (as emitted by tes4_export/record_types/world.py::export_PGRD):
  DATA.PointCount           U16 total point count
  Point[i].X/Y/Z            float position (cell-local for exterior? no —
                            these are already world coords for exteriors and
                            cell-local for interiors, same as the NIF/REFR frame)
  Point[i].Connections      U8 edge count from this point
  Point[i].Edge[j]          S16 neighbour point index (exact graph topology)
  InterCell[i].LocalPoint   U16 point that connects to a neighbouring cell
  InterCell[i].X/Y/Z        float exit point in the neighbour cell
  RefMap[i].Reference       FormID of a placed object near some points
  RefMap[i].Point[j]        point indices near that reference

=============================================================================
RESEARCH NOTES — TES5 NAVM / NVNM FORMAT
=============================================================================

Source: external/xEdit/Core/wbDefinitionsTES5.pas (wbNVNM, lines ~8015-8150)

NAVM is a top-level record living inside the CELL Temporary child group
(group type 9), exactly like REFR/LAND.  BUT the engine only uses a navmesh
for pathfinding if it is *also* indexed in a top-level NAVI (Navmesh Info
Map) record — see navi_builder.py.

NVNM blob layout (all arrays use a U32 count prefix — xEdit "-1" size):
  U32     Version (= 12 for Skyrim SE)
  Pathing Cell:
    U32   CRC Hash of "PathingCell" = 0xA5E9A03C  (constant)
    FormID Parent Worldspace (WRLD, or 0 for interior)
    Parent union — decided by (Parent Worldspace == 0):
       exterior (WS != 0):  S16 Grid Y, S16 Grid X
       interior (WS == 0):  FormID Parent Cell
  U32     Vertex count N;   N × (float X, Y, Z)
  U32     Triangle count T; T × Triangle (16 bytes, see below)
  U32     Edge Link count;  (we emit 0)
  U32     Door Triangle count; (we emit 0)
  U32     Cover Triangle count; (we emit 0)
  U32     NavMeshGrid Divisor G
  float   Max X Distance, Max Y Distance
  float   Min X, Min Y, Min Z, Max X, Max Y, Max Z
  NavMeshGrid: G*G arrays, each  U32 count + count × S16 triangle index

Triangle struct (16 bytes):
  S16 Vertex 0, S16 Vertex 1, S16 Vertex 2
  S16 Edge 0-1 (adjacent tri sharing v0-v1, or -1)
  S16 Edge 1-2, S16 Edge 2-0
  U16 Flags   (0x0200 = Water, 0x0400 = Door, ...)
  U16 Cover Flags (0 for auto-generated)

=============================================================================
ALGORITHM: PGRD → NAVM
=============================================================================

The goal is to APPROXIMATE the Oblivion pathgrid as a Skyrim navmesh so NPCs
can path across the same walkable area.

1. Read PGRD points (X,Y,Z) and the exact edge graph (Point[i].Edge[j]).
2. Build the 2D seed vertex set: every pathgrid node, plus Steiner points
   spaced along every graph edge (so long corridors become a strip of
   triangles instead of a single sliver).
3. Delaunay-triangulate the 2D set (scipy).
4. Assign Z to each vertex:
     - exterior: bilinear sample of the LAND VHGT height field
     - interior: inverse-distance blend of nearby pathgrid-node Z values
       (interiors have no LAND; nodes sit on the floor)
5. Coverage mask: keep only triangles near the pathgrid graph (centroid
   within COVERAGE_RADIUS of a node OR straddling an edge) so the mesh hugs
   the walkable region and does not fill the whole convex hull.
6. Drop degenerate slivers.
7. Carve out static-object footprints: remove triangles whose centroid lands
   inside a placed STAT/CONT/FURN AABB (rotation-aware) so NPCs don't try to
   walk through furniture/architecture.
8. Prune unused vertices, compute edge adjacency, flag water triangles.
9. Serialise NVNM, wrap in a compressed NAVM record.

Returns per-navmesh metadata (centroid, parent) so the caller can build NAVI.
=============================================================================
"""

import math
import struct
import logging

from .text_reader import get_int, get_float, get_str, get_formid
from .writer import pack_subrecord, pack_string_subrecord

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CRC of "PathingCell" — wbCRCValuesEnum, wbDefinitionsFO76.pas:8066.
# Same constant used by every Skyrim navmesh.
_PATHING_CELL_CRC = 0xA5E9A03C
# CRC of "PathingDoor" — used in the Door Triangles array.
_PATHING_DOOR_CRC = 0xE48B73F3

# NVNM version — 12 for Skyrim SE.
_NVNM_VERSION = 12

# Triangle flags (wbDefinitionsTES5.pas wbNVNM triangle flags).
_TRI_FLAG_WATER = 0x0200
_TRI_FLAG_DOOR = 0x0400
_TRI_FLAG_FOUND = 0x0800     # set on every vanilla generated triangle
_TRI_EDGE_LINK = (0x0001, 0x0002, 0x0004)  # per-edge "has Edge Link" bits

# NavMeshGrid divisor is chosen per-navmesh from the bbox span so buckets stay
# roughly one "grid cell" (~512u) wide, matching vanilla (divisor 3..12).
GRID_TARGET_CELL = 600.0
GRID_DIVISOR_MIN = 2
GRID_DIVISOR_MAX = 12

# Half-width of the walkable corridor carved around each pathgrid EDGE (game
# units). Bethesda's CS places pathgrid nodes a short distance INSIDE the walls,
# so a modest buffer reaches the walls without overshooting. Painted-wall
# reference (Anvil FG) shows walls sit just past the outer node ring — a tight
# radius leaves the walls as clean gaps between corridors.
CORRIDOR_HALF_WIDTH = 75.0
# Radius of walkable area around an ISOLATED node (rooms sampled by a lone node).
NODE_RADIUS = 95.0

# Target spacing of the interior Steiner grid that fills each walkable polygon.
FILL_STEP = 128.0
# Spacing of boundary vertices sampled along the walkable polygon edges — must be
# <= FILL_STEP so the boundary is at least as dense as the interior (clean edge).
# Kept fairly fine so small carved obstacle holes get a smooth wrapping border.
BOUNDARY_STEP = 64.0

# Obstacle holes are dilated by this margin so the navmesh keeps a small, clean
# standoff from furniture instead of a jagged single-triangle bite.
OBSTACLE_DILATE = 8.0

# Door choking: the navmesh should neck down to roughly a door's width at each
# doorway (vanilla narrows to a 1-2 triangle strip). We place a jamb rectangle
# just beyond each end of the opening so the walkable region is pinched.
DOOR_WIDTH = 90.0        # clear opening kept walkable at a door
DOOR_JAMB_LEN = 110.0    # length of each jamb block along the wall
DOOR_JAMB_DEPTH = 44.0   # thickness of the jamb across the doorway
DOOR_SLOT_LEN = 130.0    # length of the guaranteed passage slot through a door
# Door-triangle linking: a triangle links to a door if within this distance, and
# triangles centred on the threshold LINE (small offset along facing) are
# preferred by weighting the along-facing offset up.
DOOR_LINK_MAX_DIST = 220.0
DOOR_LINK_ALONG_WEIGHT = 2.0

# Minimum 2D triangle area to keep (drops slivers).
MIN_TRI_AREA = 64.0

# A footprint is treated as FLOOR/shell (never carved) if it contains at least
# this many pathgrid nodes, or covers this fraction of the pathgrid extent.
# Furniture with a single sit/sleep interaction node stays an obstacle.
FLOOR_NODE_COUNT = 3
FLOOR_AREA_FRAC = 0.35

# An obstacle's SMALLER half-extent must be at least this (drops thin decals /
# tiny clutter) and its footprint AREA at least MIN_EXCLUSION_AREA. A bench
# (~129x34) passes; a sack (~24x24) does not.
MIN_EXCLUSION_HALF_EXTENT = 13.0
MIN_EXCLUSION_AREA = 1600.0
# Footprints are shrunk by this margin before carving so the navmesh can still
# hug right up to a wall/object edge (NPCs path to the object surface).
EXCLUSION_MARGIN = 24.0
# A hole is only kept if the object stands tall enough to actually block (skip
# flat rugs, floor decals, low thresholds).
MIN_EXCLUSION_HEIGHT = 40.0


# ---------------------------------------------------------------------------
# LAND height field decoder
# ---------------------------------------------------------------------------

_CELL_SIZE = 4096.0
_LAND_VERTS = 33
_LAND_SPACING = _CELL_SIZE / (_LAND_VERTS - 1)  # 128.0
_VHGT_UNIT = 8.0  # each VHGT delta unit == 8 game units of height

# Exterior Z: beyond this distance from the nearest pathgrid node, a navmesh
# vertex eases from node-height toward the LAND terrain height (open ground far
# from any road follows the terrain; near a road it sits on the road surface).
LAND_BLEND_DIST = 300.0


def _decode_vhgt(vhgt_hex: str):
    """Decode a VHGT hex string into a 33×33 grid of absolute Z (game units).

    grid[row][col]: row 0 = south (min Y), col 0 = west (min X).
    Returns None if the data is missing/short.
    """
    try:
        data = bytes.fromhex(vhgt_hex)
    except ValueError:
        return None
    if len(data) < 4 + _LAND_VERTS * _LAND_VERTS:
        return None

    # VHGT stores an absolute float offset then a 33×33 grid of SIGNED int8
    # gradients. Accumulation is done in RAW units and scaled by _VHGT_UNIT only
    # when reading out: the first column of each row is a delta from the previous
    # row's first column, and within a row each column is a delta from the
    # previous column. (Accumulating in scaled units multiplies the running
    # height by 8 every row → overflow.)
    height_offset = struct.unpack_from('<f', data, 0)[0]
    deltas = data[4:4 + _LAND_VERTS * _LAND_VERTS]

    grid = []
    row_start = height_offset / _VHGT_UNIT  # keep everything in RAW units
    idx = 0
    for _row in range(_LAND_VERTS):
        row_start += struct.unpack_from('<b', deltas, idx)[0]
        col_acc = row_start
        row_vals = [col_acc * _VHGT_UNIT]
        idx += 1
        for _col in range(1, _LAND_VERTS):
            col_acc += struct.unpack_from('<b', deltas, idx)[0]
            row_vals.append(col_acc * _VHGT_UNIT)
            idx += 1
        grid.append(row_vals)
    return grid


def _make_height_sampler(vhgt_hex: str, origin_x: float, origin_y: float):
    """Return f(wx, wy) -> z via bilinear interpolation, or None."""
    grid = _decode_vhgt(vhgt_hex)
    if grid is None:
        return None

    def sample(wx: float, wy: float) -> float:
        lx = (wx - origin_x) / _LAND_SPACING
        ly = (wy - origin_y) / _LAND_SPACING
        lx = max(0.0, min(_LAND_VERTS - 1 - 1e-6, lx))
        ly = max(0.0, min(_LAND_VERTS - 1 - 1e-6, ly))
        col0, row0 = int(lx), int(ly)
        col1 = min(col0 + 1, _LAND_VERTS - 1)
        row1 = min(row0 + 1, _LAND_VERTS - 1)
        fx, fy = lx - col0, ly - row0
        z00 = grid[row0][col0]
        z10 = grid[row0][col1]
        z01 = grid[row1][col0]
        z11 = grid[row1][col1]
        return (z00 * (1 - fx) * (1 - fy) + z10 * fx * (1 - fy) +
                z01 * (1 - fx) * fy + z11 * fx * fy)

    return sample


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _tri_area_2d(ax, ay, bx, by, cx, cy) -> float:
    """Signed area of a 2D triangle (positive = CCW)."""
    return 0.5 * ((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))


def _compute_adjacency(tris: list) -> list:
    """Per-triangle (e01, e12, e20) adjacent-triangle indices, -1 for boundary."""
    edge_map: dict = {}
    for ti, (v0, v1, v2) in enumerate(tris):
        for slot, (va, vb) in enumerate([(v0, v1), (v1, v2), (v2, v0)]):
            key = (min(va, vb), max(va, vb))
            edge_map.setdefault(key, []).append((ti, slot))

    adj = [[-1, -1, -1] for _ in tris]
    for entries in edge_map.values():
        if len(entries) == 2:
            (ti, si), (tj, sj) = entries
            adj[ti][si] = tj
            adj[tj][sj] = ti
    return [tuple(a) for a in adj]


def _build_navmesh_grid(verts, tris, min_x, min_y, max_x, max_y, divisor):
    """Bucket triangle indices into a divisor×divisor grid by centroid."""
    g = divisor
    span_x = max_x - min_x if max_x > min_x else 1.0
    span_y = max_y - min_y if max_y > min_y else 1.0
    grid = [[] for _ in range(g * g)]
    for ti, (v0, v1, v2) in enumerate(tris):
        cx = (verts[v0][0] + verts[v1][0] + verts[v2][0]) / 3.0
        cy = (verts[v0][1] + verts[v1][1] + verts[v2][1]) / 3.0
        gx = min(max(int((cx - min_x) / span_x * g), 0), g - 1)
        gy = min(max(int((cy - min_y) / span_y * g), 0), g - 1)
        grid[gy * g + gx].append(ti)
    return grid


# ---------------------------------------------------------------------------
# Delaunay triangulation
# ---------------------------------------------------------------------------

def _delaunay_triangulate(points2d: list) -> list:
    """Delaunay over (x,y) tuples -> list of CCW (i,j,k) index triples."""
    try:
        import numpy as np
        from scipy.spatial import Delaunay  # type: ignore
    except ImportError:
        _log.warning("scipy/numpy unavailable — PGRD→NAVM conversion disabled")
        return []
    if len(points2d) < 3:
        return []

    # De-duplicate coincident points (Delaunay is unstable on them).
    tol = 1.0
    keep, seen = [], set()
    for i, (x, y) in enumerate(points2d):
        key = (round(x / tol), round(y / tol))
        if key not in seen:
            seen.add(key)
            keep.append(i)
    if len(keep) < 3:
        return []

    pts = np.array([points2d[i] for i in keep], dtype=np.float64)
    try:
        tri = Delaunay(pts)
    except Exception:
        return []

    result = []
    for simplex in tri.simplices:
        i0, i1, i2 = (keep[simplex[0]], keep[simplex[1]], keep[simplex[2]])
        ax, ay = points2d[i0]
        bx, by = points2d[i1]
        cx, cy = points2d[i2]
        if _tri_area_2d(ax, ay, bx, by, cx, cy) < 0:
            i1, i2 = i2, i1
        result.append((i0, i1, i2))
    return result


# ---------------------------------------------------------------------------
# Walkable region (shapely) — the floor the navmesh must cover
# ---------------------------------------------------------------------------

def _build_walkable_polygon(points, edges, exclusion_polys, door_jambs=None):
    """Build the walkable-floor polygon from the pathgrid, minus obstacles.

    The Oblivion pathgrid only sparsely samples the floor, so we RECONSTRUCT the
    walkable area by buffering:
      - every graph EDGE into a CORRIDOR_HALF_WIDTH-wide capsule, and
      - every node into a NODE_RADIUS disc (covers open rooms sampled by lone
        nodes and the ends of corridors).
    Union → one (or several) clean-edged polygons. Object footprints are then
    subtracted so walls/furniture become clean holes. A small closing buffer
    removes pinholes and rounds sharp notches. Door jamb rectangles are also
    subtracted so the region necks to a door-width passage at each doorway.

    Returns a shapely (Multi)Polygon, or None if shapely is unavailable/empty.
    """
    try:
        from shapely.geometry import LineString, Point, MultiPolygon
        from shapely.ops import unary_union
    except ImportError:
        _log.warning("shapely unavailable — PGRD→NAVM conversion disabled")
        return None

    parts = []
    for (i, j) in edges:
        ax, ay = points[i][0], points[i][1]
        bx, by = points[j][0], points[j][1]
        if abs(ax - bx) < 1e-6 and abs(ay - by) < 1e-6:
            continue
        # cap_style=2 (flat) keeps corridors rectangular; join rounds corners.
        parts.append(LineString([(ax, ay), (bx, by)]).buffer(
            CORRIDOR_HALF_WIDTH, cap_style=2, join_style=1))
    for p in points:
        parts.append(Point(p[0], p[1]).buffer(NODE_RADIUS, quad_segs=6))

    if not parts:
        return None
    region = unary_union(parts)

    # Close pinholes / smooth spurs FIRST (small dilate+erode) so tiny gaps
    # between overlapping node discs vanish — this must happen BEFORE obstacle
    # subtraction or it would refill the carved furniture holes.
    region = region.buffer(16.0, join_style=1).buffer(-16.0, join_style=1)

    # Subtract real obstacles (pillars, crates, furniture) the NPC walks around.
    # Dilate each obstacle a little so the navmesh keeps a clean standoff border
    # instead of grazing the object; smooth the ring with round joins.
    obstacles_real, _floors = _classify_footprints(exclusion_polys, points)
    if obstacles_real:
        obstacles = unary_union(
            [o.buffer(OBSTACLE_DILATE, join_style=1) for o in obstacles_real])
        region = region.difference(obstacles)

    # Choke doorways: subtract the jamb blocks flanking each door so the region
    # necks to ~DOOR_WIDTH there, then UNION a guaranteed door-width passage slot
    # through each door centre so the choke can never fully sever the connection.
    if door_jambs:
        jamb_polys, slot_polys = door_jambs
        if jamb_polys:
            region = region.difference(unary_union(jamb_polys))
        if slot_polys:
            region = region.union(unary_union(slot_polys))

    if region.is_empty:
        return None
    if region.geom_type == 'Polygon':
        return region
    if region.geom_type == 'MultiPolygon':
        return region
    # GeometryCollection etc. — keep only polygonal parts.
    polys = [g for g in getattr(region, 'geoms', []) if g.geom_type == 'Polygon']
    return MultiPolygon(polys) if polys else None


def _classify_footprints(exclusion_polys, points):
    """Split footprints into (obstacles, floors).

    Only a FLOOR / rug / building-shell should be spared from carving; real
    furniture (beds, benches, tables, crates, barrels) must be carved even
    though it often has ONE interaction node (sleep/sit) sitting on it.

    Distinguishing signal:
      - a FLOOR/shell spans a large area and has MANY pathgrid nodes spread
        across it (the NPC traverses its whole surface), OR is simply huge; while
      - a furniture obstacle has a SMALL footprint with at most one interaction
        node inside it and nodes routed AROUND its perimeter.

    So a footprint is a FLOOR iff it contains >= FLOOR_NODE_COUNT nodes or covers
    > FLOOR_AREA_FRAC of the pathgrid extent. Everything else is an obstacle.
    """
    if not exclusion_polys:
        return [], []
    try:
        from shapely.geometry import Point
        from shapely.prepared import prep
    except ImportError:
        return exclusion_polys, []

    node_xy = [(p[0], p[1]) for p in points]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    grid_area = (max(xs) - min(xs)) * (max(ys) - min(ys)) if len(points) > 1 else 0.0

    obstacles, floors = [], []
    for poly in exclusion_polys:
        pp = prep(poly)
        inside = sum(1 for (x, y) in node_xy if pp.contains(Point(x, y)))
        is_floor = inside >= FLOOR_NODE_COUNT
        if not is_floor and grid_area > 0 and poly.area > FLOOR_AREA_FRAC * grid_area:
            is_floor = True
        (floors if is_floor else obstacles).append(poly)
    return obstacles, floors


def _iter_polys(region):
    """Yield each Polygon in a Polygon/MultiPolygon."""
    if region is None:
        return
    if region.geom_type == 'Polygon':
        yield region
    else:
        for g in region.geoms:
            if g.geom_type == 'Polygon':
                yield g


def _triangulate_region(region):
    """Triangulate a walkable (Multi)Polygon into clean-edged coverage.

    Method (no CDT library needed): sample a dense set of candidate 2D points —
    a regular FILL_STEP grid clipped to the polygon interior, PLUS boundary
    vertices spaced BOUNDARY_STEP along every ring (exterior + holes) — then run
    an unconstrained Delaunay and KEEP only triangles whose centroid lies inside
    the polygon. Because the boundary is sampled at least as densely as the
    interior, the kept triangles tile the polygon with edges that follow the
    real boundary and holes → clean edges, full floor coverage, no slivers off
    the walls.

    Returns (verts2d, tris) with tris as CCW (i,j,k) index triples.
    """
    from shapely.geometry import Point
    from shapely.prepared import prep

    minx, miny, maxx, maxy = region.bounds
    pts = []
    seen = set()

    def add(x, y):
        key = (round(x / 8.0), round(y / 8.0))
        if key not in seen:
            seen.add(key)
            pts.append((x, y))

    # Boundary vertices (clean edges).
    for poly in _iter_polys(region):
        rings = [poly.exterior] + list(poly.interiors)
        for ring in rings:
            length = ring.length
            if length < 1e-6:
                continue
            n = max(2, int(length / BOUNDARY_STEP))
            for s in range(n):
                pt = ring.interpolate(s / n, normalized=True)
                add(pt.x, pt.y)

    # Interior grid, clipped to the polygon.
    prepared = prep(region)
    y = miny + FILL_STEP * 0.5
    row = 0
    while y < maxy:
        # Offset alternate rows for a nicer (less axis-locked) triangulation.
        xoff = (FILL_STEP * 0.5) if (row % 2) else 0.0
        x = minx + xoff + FILL_STEP * 0.5
        while x < maxx:
            if prepared.contains(Point(x, y)):
                add(x, y)
            x += FILL_STEP
        y += FILL_STEP
        row += 1

    if len(pts) < 3:
        return [], []

    tris = _delaunay_triangulate(pts)
    if not tris:
        return [], []

    # Keep triangles whose centroid is inside the walkable region.
    kept = []
    for (a, b, c) in tris:
        cx = (pts[a][0] + pts[b][0] + pts[c][0]) / 3.0
        cy = (pts[a][1] + pts[b][1] + pts[c][1]) / 3.0
        if prepared.contains(Point(cx, cy)):
            kept.append((a, b, c))
    return pts, kept


def _assign_z(pts2d: list, points: list, height_sampler) -> list:
    """Assign Z to each 2D vertex, placing it on the WALKABLE surface.

    Pathgrid nodes sit on the walkable floor in BOTH interior and exterior cells
    (exterior streets are cobblestone statics placed ~100u above raw terrain, so
    the LAND height field is NOT the walk surface — using it floats the navmesh
    below the road). So Z is taken from the nearest pathgrid nodes, weighted
    steeply toward the closest, which locks each vertex to its own local walkway
    and avoids averaging across floors in multi-level interiors.

    For exteriors, where a vertex is FAR from every node (open fill terrain), we
    blend toward the LAND height so distant ground still follows the terrain.
    """
    node_xy = [(p[0], p[1]) for p in points]
    node_z = [p[2] for p in points]
    try:
        import numpy as np
        from scipy.spatial import cKDTree  # type: ignore
        tree = cKDTree(np.array(node_xy))
        k = min(3, len(node_xy))
        qd, qi = tree.query(np.array(pts2d), k=k)
        if k == 1:
            qd = qd.reshape(-1, 1)
            qi = qi.reshape(-1, 1)
        out = []
        for row in range(len(pts2d)):
            x, y = pts2d[row]
            ds = qd[row]
            ids = qi[row]
            if ds[0] < 1.0:
                out.append((x, y, node_z[ids[0]]))
                continue
            wsum = zsum = 0.0
            for d, ni in zip(ds, ids):
                w = 1.0 / (d ** 4 + 1.0)   # steep: closest node dominates
                wsum += w
                zsum += w * node_z[ni]
            z = zsum / wsum if wsum else node_z[ids[0]]
            # Exterior: past LAND_BLEND_DIST from any node, ease toward terrain.
            if height_sampler is not None and ds[0] > LAND_BLEND_DIST:
                t = min(1.0, (ds[0] - LAND_BLEND_DIST) / LAND_BLEND_DIST)
                z = (1.0 - t) * z + t * height_sampler(x, y)
            out.append((x, y, z))
        return out
    except ImportError:
        pass

    out = []
    for (x, y) in pts2d:
        dists = sorted(((x - px) ** 2 + (y - py) ** 2, k)
                       for k, (px, py) in enumerate(node_xy))[:3]
        if dists and dists[0][0] < 1.0:
            out.append((x, y, node_z[dists[0][1]]))
            continue
        wsum = zsum = 0.0
        for d2, k in dists:
            w = 1.0 / (d2 ** 2 + 1.0)
            wsum += w
            zsum += w * node_z[k]
        out.append((x, y, zsum / wsum if wsum else 0.0))
    return out


def _filter_by_area(verts2d, tris) -> list:
    """Drop triangles below MIN_TRI_AREA (near-collinear slivers)."""
    kept = []
    for tri in tris:
        v0, v1, v2 = tri
        area = abs(_tri_area_2d(verts2d[v0][0], verts2d[v0][1],
                                verts2d[v1][0], verts2d[v1][1],
                                verts2d[v2][0], verts2d[v2][1]))
        if area >= MIN_TRI_AREA:
            kept.append(tri)
    return kept


def _collect_doors(refr_recs, door_fids):
    """Return [(x, y, rot_z, ref_fid, is_teleport), ...] for door refs in cell.

    A door is a REFR whose base is a DOOR (in door_fids) or that has an XTEL
    teleport. is_teleport distinguishes cross-cell doors (XTEL — link two
    navmeshes) from interior-only doors (same cell, just a passage).
    """
    if not refr_recs:
        return []
    door_fids = door_fids or set()
    out = []
    for refr in refr_recs:
        name = refr.get('NAME', '')
        is_teleport = bool(refr.get('XTEL.Door'))
        base_is_door = False
        if name:
            try:
                base_is_door = (int(name, 16) & 0xFFFFFF) in door_fids
            except ValueError:
                base_is_door = False
        if not (is_teleport or base_is_door):
            continue
        ref_fid = get_formid(refr, 'FormID')
        out.append((get_float(refr, 'PosX'), get_float(refr, 'PosY'),
                    get_float(refr, 'RotZ'), ref_fid, is_teleport))
    return out


def _rect(cx, cy, ux, uy, vx, vy, half_u, half_v):
    """Oriented rectangle Polygon centred at (cx,cy), axes (u,v)."""
    from shapely.geometry import Polygon
    c = []
    for su in (-half_u, half_u):
        for sv in (-half_v, half_v):
            c.append((cx + su * ux + sv * vx, cy + su * uy + sv * vy))
    return Polygon([c[0], c[1], c[3], c[2]])


def _door_choke_obstacles(doors):
    """Build (jambs, slots) for door choking.

    A door's opening runs along its local +X; the passage goes THROUGH it along
    local +Y (RotZ rotates both). For each door:
      - jamb rectangles sit just beyond each end of the DOOR_WIDTH opening (along
        ±X) to pinch the walkable region to ~DOOR_WIDTH, and
      - a slot rectangle (DOOR_WIDTH wide, running along ±Y through the door)
        that is UNIONED back in so the choke can never fully sever the passage.

    Returns (jamb_polys, slot_polys), or ([], []) if shapely is unavailable.
    """
    if not doors:
        return [], []
    try:
        import shapely.geometry  # noqa: F401
    except ImportError:
        return [], []

    jambs, slots = [], []
    half = DOOR_WIDTH / 2.0
    for (dx, dy, rot_z, _fid, _tp) in doors:
        cos_z, sin_z = math.cos(rot_z), math.sin(rot_z)
        ux, uy = cos_z, sin_z            # opening axis (local +X)
        vx, vy = -sin_z, cos_z           # passage axis (local +Y)
        for side in (+1.0, -1.0):
            base = half + DOOR_JAMB_LEN / 2.0
            jx = dx + side * base * ux
            jy = dy + side * base * uy
            poly = _rect(jx, jy, ux, uy, vx, vy,
                         DOOR_JAMB_LEN / 2.0, DOOR_JAMB_DEPTH / 2.0)
            if poly.is_valid and poly.area > 1.0:
                jambs.append(poly)
        slot = _rect(dx, dy, ux, uy, vx, vy, half, DOOR_SLOT_LEN / 2.0)
        if slot.is_valid and slot.area > 1.0:
            slots.append(slot)
    return jambs, slots


def _build_door_links(verts, tris, doors):
    """Return [(triangle_index, door_ref_fid), ...], one per door.

    The door triangle is the walkable triangle that sits ON the doorway
    threshold: we pick the triangle whose centroid is closest to the door
    position but that also STRADDLES the door line (small perpendicular offset
    along the door's facing axis), so the link lands on the choked passage strip
    rather than an adjacent room triangle. door_ref_fid is the (remapped) REFR
    FormID the engine walks through.
    """
    if not doors or not tris:
        return []

    cents = []
    for (a, b, c) in tris:
        cents.append(((verts[a][0] + verts[b][0] + verts[c][0]) / 3.0,
                      (verts[a][1] + verts[b][1] + verts[c][1]) / 3.0))

    used_tris = set()
    links = []
    for (dx, dy, rot_z, ref_fid, _is_tp) in doors:
        if not ref_fid:
            continue
        # Door facing axis (local +Y): the threshold line is perpendicular.
        fx, fy = -math.sin(rot_z), math.cos(rot_z)
        best_ti, best_cost = None, None
        for ti, (cx, cy) in enumerate(cents):
            if ti in used_tris:
                continue
            ox, oy = cx - dx, cy - dy
            dist2 = ox * ox + oy * oy
            if dist2 > (DOOR_LINK_MAX_DIST ** 2):
                continue
            # Prefer triangles centred near the threshold LINE (small |offset
            # along facing|) and close to the door point.
            along = abs(ox * fx + oy * fy)
            cost = dist2 + (along * DOOR_LINK_ALONG_WEIGHT) ** 2
            if best_cost is None or cost < best_cost:
                best_cost, best_ti = cost, ti
        if best_ti is not None:
            links.append((best_ti, ref_fid))
            used_tris.add(best_ti)
    return links


def _prune_unused_verts(verts, tris):
    used = set()
    for tri in tris:
        used.update(tri)
    old_to_new = {}
    new_verts = []
    for old in sorted(used):
        old_to_new[old] = len(new_verts)
        new_verts.append(verts[old])
    new_tris = [(old_to_new[a], old_to_new[b], old_to_new[c])
                for (a, b, c) in tris]
    return new_verts, new_tris


def _compute_water_flags(verts, tris, water_z) -> list:
    flags = []
    for tri in tris:
        v0, v1, v2 = tri
        cz = (verts[v0][2] + verts[v1][2] + verts[v2][2]) / 3.0
        flags.append(_TRI_FLAG_WATER if cz < water_z else 0)
    return flags


# ---------------------------------------------------------------------------
# Exclusion zones from placed references + base-object mesh bounds
# ---------------------------------------------------------------------------

def _build_exclusion_zones(refr_recs, base_model_by_fid):
    """Obstacle footprints (oriented shapely polygons) from placed refs.

    base_model_by_fid maps a *raw low-24* base-object FormID to its normalised
    'tes4/...nif' model key (built by the caller from the export, restricted to
    blocking base types — STAT/CONT/FURN/ACTI/TREE — so doors/lights/markers
    never carve holes).

    Each obstacle uses the mesh's real 2D SILHOUETTE (convex-hull footprint from
    the mesh_footprints cache) rotated by RotZ, scaled, and placed — so an angled
    wall, an L-shaped building, or a round well carves a hole that matches its
    actual shape rather than a fat AABB. The AABB (mesh_bounds) is used only for
    the fast height/size gates and as a fallback when a hull is unavailable. The
    footprint is shrunk by EXCLUSION_MARGIN so the navmesh can still hug the
    object surface; flat objects (rugs/decals) and tiny clutter are skipped.

    Returns a list of shapely Polygons in the pathgrid frame.
    """
    try:
        from .mesh_bounds import get_mesh_obnd
        from .mesh_footprints import get_mesh_footprint
        from shapely.geometry import Polygon
    except ImportError:
        return []

    polys = []
    for refr in refr_recs:
        name = refr.get('NAME')
        if not name:
            continue
        try:
            base_low = int(name, 16) & 0x00FFFFFF
        except ValueError:
            continue
        model_key = base_model_by_fid.get(base_low)
        if not model_key:
            continue
        bounds = get_mesh_obnd(model_key)
        if bounds is None:
            continue

        bx0, by0, bz0, bx1, by1, bz1 = bounds
        scale = get_float(refr, 'XSCL.Scale', 1.0) or 1.0

        # Gates (fast, from the AABB): skip flat objects and tiny clutter.
        if (bz1 - bz0) * scale < MIN_EXCLUSION_HEIGHT:
            continue
        lhx = (bx1 - bx0) / 2.0 * scale
        lhy = (by1 - by0) / 2.0 * scale
        if min(lhx, lhy) < MIN_EXCLUSION_HALF_EXTENT:
            continue
        if (2 * lhx) * (2 * lhy) < MIN_EXCLUSION_AREA:
            continue

        rx = get_float(refr, 'PosX')
        ry = get_float(refr, 'PosY')
        rot_z = get_float(refr, 'RotZ')  # radians
        cos_z, sin_z = math.cos(rot_z), math.sin(rot_z)

        # Prefer the real silhouette hull; fall back to the AABB rectangle.
        hull = get_mesh_footprint(model_key)
        if hull and len(hull) >= 3:
            local = [(hx * scale, hy * scale) for (hx, hy) in hull]
        else:
            lcx = (bx0 + bx1) / 2.0 * scale
            lcy = (by0 + by1) / 2.0 * scale
            local = [(lcx - lhx, lcy - lhy), (lcx + lhx, lcy - lhy),
                     (lcx + lhx, lcy + lhy), (lcx - lhx, lcy + lhy)]

        corners = []
        for (ox, oy) in local:
            wx = rx + (ox * cos_z - oy * sin_z)
            wy = ry + (ox * sin_z + oy * cos_z)
            corners.append((wx, wy))

        poly = Polygon(corners)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area < 1.0:
            continue
        # Shrink so the navmesh can hug the surface; skip if it vanishes.
        poly = poly.buffer(-EXCLUSION_MARGIN, join_style=2)
        if poly.is_empty or poly.geom_type != 'Polygon':
            continue
        polys.append(poly)

    return polys


# ---------------------------------------------------------------------------
# NVNM serialiser
# ---------------------------------------------------------------------------

def _choose_divisor(span_x, span_y):
    """Pick a NavMeshGrid divisor so buckets are ~GRID_TARGET_CELL wide."""
    span = max(span_x, span_y)
    g = int(round(span / GRID_TARGET_CELL))
    return max(GRID_DIVISOR_MIN, min(GRID_DIVISOR_MAX, g))


def _pack_nvnm(verts, tris, adj, tri_flags,
               wrld_fid, cell_fid, grid_x, grid_y, is_exterior,
               door_tris=None) -> bytes:
    """Serialise an NVNM blob.

    door_tris: list of (triangle_index, door_ref_fid) — emitted as Door
    Triangles and flagged with _TRI_FLAG_DOOR on the referenced triangle.
    """
    door_tris = door_tris or []
    door_by_tri = {ti: fid for (ti, fid) in door_tris}

    buf = bytearray()
    buf += struct.pack('<I', _NVNM_VERSION)
    buf += struct.pack('<I', _PATHING_CELL_CRC)
    buf += struct.pack('<I', wrld_fid)
    if is_exterior:
        buf += struct.pack('<hh', grid_y, grid_x)   # Grid Y, then Grid X
    else:
        buf += struct.pack('<I', cell_fid)

    # Vertices
    buf += struct.pack('<I', len(verts))
    for x, y, z in verts:
        buf += struct.pack('<fff', x, y, z)

    # Triangles — base "Found" flag on every tri (matches vanilla), plus water
    # / door bits. No per-edge link bits: we emit no Edge Links.
    buf += struct.pack('<I', len(tris))
    for ti, (v0, v1, v2) in enumerate(tris):
        e01, e12, e20 = adj[ti]
        flags = _TRI_FLAG_FOUND
        flags |= (tri_flags[ti] if ti < len(tri_flags) else 0)
        if ti in door_by_tri:
            flags |= _TRI_FLAG_DOOR
        buf += struct.pack('<6h2H', v0, v1, v2, e01, e12, e20, flags, 0)

    # Edge Links — none (cross-cell links can't be resolved from PGRD alone).
    buf += struct.pack('<I', 0)

    # Door Triangles: sorted by (triangle, door) per xEdit wbStructSK([0,2]).
    sorted_doors = sorted(door_tris, key=lambda d: (d[0], d[1]))
    buf += struct.pack('<I', len(sorted_doors))
    for (ti, fid) in sorted_doors:
        buf += struct.pack('<hI I', ti, _PATHING_DOOR_CRC, fid)

    # Cover Triangles — none.
    buf += struct.pack('<I', 0)

    # Bounding box
    if verts:
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        zs = [v[2] for v in verts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)
    else:
        min_x = min_y = min_z = max_x = max_y = max_z = 0.0

    span_x = max_x - min_x if max_x > min_x else 1.0
    span_y = max_y - min_y if max_y > min_y else 1.0
    divisor = _choose_divisor(span_x, span_y)

    buf += struct.pack('<I', divisor)
    buf += struct.pack('<f', span_x / divisor)   # Max X Distance (bucket width)
    buf += struct.pack('<f', span_y / divisor)   # Max Y Distance
    buf += struct.pack('<ffffff', min_x, min_y, min_z, max_x, max_y, max_z)

    grid = _build_navmesh_grid(verts, tris, min_x, min_y, max_x, max_y, divisor)
    for cell_tris in grid:
        buf += struct.pack('<I', len(cell_tris))
        for ti in cell_tris:
            buf += struct.pack('<h', ti)

    return bytes(buf)


def _pack_navm_record(form_id: int, subrecords: bytes) -> bytes:
    """Pack a compressed NAVM record (Compressed flag 0x00040000)."""
    import zlib
    uncompressed_size = len(subrecords)
    compressed = zlib.compress(subrecords, 6)
    payload = struct.pack('<I', uncompressed_size) + compressed

    flags = 0x00040000  # Compressed
    header = struct.pack('<4sIIIIHH',
                         b'NAVM', len(payload), flags, form_id,
                         0,   # vcs1
                         44,  # FORM_VERSION_SSE
                         0)   # vcs2
    return header + payload


# ---------------------------------------------------------------------------
# Public converter
# ---------------------------------------------------------------------------

def convert_PGRD(rec: dict, writer=None,
                 land_rec: dict = None,
                 cell_rec: dict = None,
                 refr_recs: list = None,
                 base_model_by_fid: dict = None,
                 door_fids: set = None,
                 navm_fid: int = None) -> tuple:
    """Convert one TES4 PGRD to a TES5 NAVM record.

    Args:
        rec:                Parsed PGRD record dict.
        writer:             PluginWriter (supplies alloc_formid()).
        land_rec:           LAND record for the cell (VHGT height field).
        cell_rec:           CELL record (water height, grid coords).
        refr_recs:          REFR records in this cell (exclusion footprints).
        base_model_by_fid:  {raw_low_base_fid: 'tes4/...nif'} for footprints.
        door_fids:          set of raw low-24 DOOR base FormIDs (for door links).
        navm_fid:           Pre-allocated NAVM FormID.  When given, the writer is
                            not touched for allocation — this lets callers assign
                            FormIDs deterministically before farming the (heavy,
                            scipy-bound) geometry work out to a thread pool.

    Returns:
        (navm_bytes, meta) where meta is a dict
        {fid, wrld_fid, cell_fid, grid_x, grid_y, is_exterior, center,
         base_objects} — or (None, None) on failure.
    """
    if writer is None and navm_fid is None:
        return None, None

    point_count = get_int(rec, 'DATA.PointCount', 0)
    if point_count < 2:
        return None, None

    # ---- Points + degrees ----
    points, degrees = [], []
    for i in range(point_count):
        if rec.get(f'Point[{i}].X') is None:
            break
        points.append((get_float(rec, f'Point[{i}].X'),
                       get_float(rec, f'Point[{i}].Y'),
                       get_float(rec, f'Point[{i}].Z')))
        degrees.append(get_int(rec, f'Point[{i}].Connections', 0))
    if len(points) < 2:
        return None, None

    n = len(points)

    # ---- Edges from exported PGRR topology ----
    edges = []
    seen_edges = set()
    if rec.get('Point[0].Edge[0]') is not None:
        for i in range(n):
            for j in range(degrees[i]):
                tgt = rec.get(f'Point[{i}].Edge[{j}]')
                if tgt is None:
                    break
                try:
                    t = int(tgt)
                except ValueError:
                    continue
                if 0 <= t < n and t != i:
                    key = (min(i, t), max(i, t))
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append(key)
    else:
        # Fallback: nearest-neighbour edges by connection degree.
        for i in range(n):
            if degrees[i] == 0:
                continue
            others = sorted(((points[i][0] - points[j][0]) ** 2 +
                             (points[i][1] - points[j][1]) ** 2, j)
                            for j in range(n) if j != i)
            for _, j in others[:degrees[i]]:
                key = (min(i, j), max(i, j))
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append(key)

    # ---- Parent cell / worldspace ----
    cell_fid = get_formid(rec, 'ParentCELL')
    wrld_fid = get_formid(rec, 'ParentWRLD')
    is_exterior = wrld_fid != 0

    grid_x = grid_y = 0
    origin_x = origin_y = 0.0
    if is_exterior:
        if cell_rec is not None:
            grid_x = get_int(cell_rec, 'XCLC.X', 0)
            grid_y = get_int(cell_rec, 'XCLC.Y', 0)
        origin_x = grid_x * _CELL_SIZE
        origin_y = grid_y * _CELL_SIZE

    # ---- Height sampler (exterior LAND) ----
    height_sampler = None
    if land_rec is not None and is_exterior:
        vhgt_hex = get_str(land_rec, 'VHGT')
        if vhgt_hex:
            height_sampler = _make_height_sampler(vhgt_hex, origin_x, origin_y)

    # ---- Water height ----
    water_z = None
    if cell_rec is not None:
        if get_int(cell_rec, 'DATA.Flags', 0) & 0x02:  # HasWater
            wz = cell_rec.get('XCLW.WaterHeight')
            if wz is not None:
                try:
                    water_z = float(wz)
                except ValueError:
                    water_z = None

    # ---- Build the walkable region (floor coverage) ----
    exclusion_polys = []
    base_objects = []
    doors = []
    if refr_recs:
        exclusion_polys = _build_exclusion_zones(refr_recs, base_model_by_fid or {})
        base_objects = _collect_base_objects(refr_recs)
        doors = _collect_doors(refr_recs, door_fids)

    door_jambs = _door_choke_obstacles(doors)
    region = _build_walkable_polygon(points, edges, exclusion_polys, door_jambs)
    if region is None:
        return None, None

    pts2d, tris = _triangulate_region(region)
    if len(pts2d) < 3 or not tris:
        return None, None

    tris = _filter_by_area(
        [(x, y) for (x, y) in pts2d], tris)  # drop 2D slivers
    if not tris:
        return None, None

    verts3d = _assign_z(pts2d, points, height_sampler)
    verts3d, tris = _prune_unused_verts(verts3d, tris)
    if len(verts3d) < 3 or not tris:
        return None, None

    tri_flags = (_compute_water_flags(verts3d, tris, water_z)
                 if water_z is not None else [0] * len(tris))
    adj = _compute_adjacency(tris)

    # ---- Door triangles: link the navmesh tri at each door threshold ----
    door_tris = _build_door_links(verts3d, tris, doors)

    nvnm = _pack_nvnm(verts3d, tris, adj, tri_flags,
                      wrld_fid, cell_fid, grid_x, grid_y, is_exterior,
                      door_tris=door_tris)

    if navm_fid is None:
        navm_fid = writer.alloc_formid()
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', f'TES4Navm{edid}')
    subs += pack_subrecord('NVNM', nvnm)
    if base_objects:
        onam = b''.join(struct.pack('<I', b) for b in base_objects)
        subs += pack_subrecord('ONAM', onam)

    navm_bytes = _pack_navm_record(navm_fid, subs)

    # Centroid for the NAVI NVMI entry.
    cx = sum(v[0] for v in verts3d) / len(verts3d)
    cy = sum(v[1] for v in verts3d) / len(verts3d)
    cz = sum(v[2] for v in verts3d) / len(verts3d)

    meta = {
        'fid': navm_fid,
        'wrld_fid': wrld_fid,
        'cell_fid': cell_fid,
        'grid_x': grid_x,
        'grid_y': grid_y,
        'is_exterior': is_exterior,
        'center': (cx, cy, cz),
        'base_objects': base_objects,
    }
    return navm_bytes, meta


def _collect_base_objects(refr_recs) -> list:
    """Distinct remapped base-object FormIDs (for NAVM ONAM). CONT/FURN/TREE/STAT.

    We don't know each base's type here without a lookup, so we register every
    distinct base FormID; the engine tolerates NULL-typed entries and this
    keeps door/furniture linkage intact for the navmesh info map.
    """
    seen = set()
    out = []
    for refr in refr_recs:
        name = refr.get('NAME')
        if not name:
            continue
        fid = get_formid(refr, 'NAME')
        if fid and fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out
