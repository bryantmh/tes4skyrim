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

# NVNM version — 12 for Skyrim SE.
_NVNM_VERSION = 12

# Triangle flags.
_TRI_FLAG_WATER = 0x0200

# NavMeshGrid divisor (G): the cell bbox is split into a G×G grid of triangle
# buckets. Vanilla uses small values; 4 (interior) / 8 (exterior) is typical.
GRID_DIVISOR_INTERIOR = 4
GRID_DIVISOR_EXTERIOR = 8

# A Delaunay triangle survives the coverage mask if its centroid is within this
# distance of a pathgrid node. 256 = two LAND vertex spacings — wide enough to
# bridge the gaps between nodes without ballooning past the walkable region.
COVERAGE_RADIUS = 256.0

# Spacing between Steiner points inserted along each pathgrid edge (game units).
EDGE_STEINER_SPACING = 128.0

# Minimum 2D triangle area to keep (drops slivers from near-collinear nodes).
MIN_TRI_AREA = 64.0

# A triangle centroid inside an object AABB scaled by this factor is carved out.
# < 1.0 so we only remove triangles well inside a footprint, not grazing edges.
EXCLUSION_SCALE = 0.80

# Objects smaller than this (half-extent, horizontal) never block a triangle —
# clutter, small props, etc. should not punch holes in the navmesh.
MIN_EXCLUSION_HALF_EXTENT = 48.0


# ---------------------------------------------------------------------------
# LAND height field decoder
# ---------------------------------------------------------------------------

_CELL_SIZE = 4096.0
_LAND_VERTS = 33
_LAND_SPACING = _CELL_SIZE / (_LAND_VERTS - 1)  # 128.0
_VHGT_UNIT = 8.0  # each VHGT delta unit == 8 game units of height


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

    height_offset = struct.unpack_from('<f', data, 0)[0]
    deltas = data[4:4 + _LAND_VERTS * _LAND_VERTS]

    grid = []
    row_acc = height_offset
    idx = 0
    for _row in range(_LAND_VERTS):
        col_acc = row_acc
        row_vals = []
        for _col in range(_LAND_VERTS):
            col_acc += struct.unpack_from('<b', deltas, idx)[0]
            row_vals.append(col_acc * _VHGT_UNIT)
            idx += 1
        grid.append(row_vals)
        row_acc = grid[_row][0]  # each row's height chains from its own col 0
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
# Vertex set + Z assignment
# ---------------------------------------------------------------------------

def _build_vertex_set(points: list, edges: list) -> list:
    """PGRD nodes + Steiner points along every edge, deduped to 0.5 units."""
    pts2d = []
    seen = set()

    def add(x, y):
        key = (round(x * 2), round(y * 2))
        if key not in seen:
            seen.add(key)
            pts2d.append((x, y))

    for p in points:
        add(p[0], p[1])

    for (i, j) in edges:
        ax, ay, _ = points[i]
        bx, by, _ = points[j]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        steps = max(1, int(length / EDGE_STEINER_SPACING))
        for s in range(1, steps):
            t = s / steps
            add(ax + dx * t, ay + dy * t)

    return pts2d


def _assign_z(pts2d: list, points: list, height_sampler) -> list:
    """Assign Z to each 2D vertex.

    Exterior (height_sampler present): bilinear LAND sample.
    Interior: inverse-distance-weighted blend of the K nearest node Z values,
    which keeps floor heights smooth across multi-level interiors instead of
    snapping to a single nearest node.
    """
    if height_sampler is not None:
        return [(x, y, height_sampler(x, y)) for (x, y) in pts2d]

    node_xy = [(p[0], p[1]) for p in points]
    node_z = [p[2] for p in points]
    out = []
    K = 4
    for (x, y) in pts2d:
        dists = sorted(((x - px) ** 2 + (y - py) ** 2, k)
                       for k, (px, py) in enumerate(node_xy))[:K]
        # Exact hit on a node.
        if dists and dists[0][0] < 1.0:
            out.append((x, y, node_z[dists[0][1]]))
            continue
        wsum = 0.0
        zsum = 0.0
        for d2, k in dists:
            w = 1.0 / (d2 + 1.0)
            wsum += w
            zsum += w * node_z[k]
        out.append((x, y, zsum / wsum if wsum else 0.0))
    return out


# ---------------------------------------------------------------------------
# Triangle filters
# ---------------------------------------------------------------------------

def _filter_by_coverage(verts, tris, points, edges) -> list:
    """Keep triangles whose centroid is near a node or straddles an edge."""
    node_xy = [(p[0], p[1]) for p in points]
    r2 = COVERAGE_RADIUS * COVERAGE_RADIUS
    # Precompute edge midpoints so long edges (whose endpoints are far from a
    # centroid) still count as coverage.
    edge_mids = []
    for (i, j) in edges:
        edge_mids.append(((points[i][0] + points[j][0]) * 0.5,
                          (points[i][1] + points[j][1]) * 0.5))
    kept = []
    for tri in tris:
        v0, v1, v2 = tri
        cx = (verts[v0][0] + verts[v1][0] + verts[v2][0]) / 3.0
        cy = (verts[v0][1] + verts[v1][1] + verts[v2][1]) / 3.0
        near = False
        for (px, py) in node_xy:
            if (cx - px) ** 2 + (cy - py) ** 2 <= r2:
                near = True
                break
        if not near:
            for (px, py) in edge_mids:
                if (cx - px) ** 2 + (cy - py) ** 2 <= r2:
                    near = True
                    break
        if near:
            kept.append(tri)
    return kept


def _filter_by_area(verts, tris) -> list:
    kept = []
    for tri in tris:
        v0, v1, v2 = tri
        area = abs(_tri_area_2d(verts[v0][0], verts[v0][1],
                                verts[v1][0], verts[v1][1],
                                verts[v2][0], verts[v2][1]))
        if area >= MIN_TRI_AREA:
            kept.append(tri)
    return kept


def _filter_by_exclusions(verts, tris, exclusions) -> list:
    """Remove triangles whose centroid falls inside any exclusion AABB."""
    if not exclusions:
        return tris
    kept = []
    for tri in tris:
        v0, v1, v2 = tri
        tx = (verts[v0][0] + verts[v1][0] + verts[v2][0]) / 3.0
        ty = (verts[v0][1] + verts[v1][1] + verts[v2][1]) / 3.0
        tz = (verts[v0][2] + verts[v1][2] + verts[v2][2]) / 3.0
        blocked = False
        for (cx, cy, cz, hx, hy, hz) in exclusions:
            if (abs(tx - cx) <= hx * EXCLUSION_SCALE and
                    abs(ty - cy) <= hy * EXCLUSION_SCALE and
                    abs(tz - cz) <= hz * EXCLUSION_SCALE):
                blocked = True
                break
        if not blocked:
            kept.append(tri)
    return kept


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

def _build_exclusion_zones(refr_recs, base_model_by_fid) -> list:
    """AABB exclusion zones from placed references.

    base_model_by_fid maps a *raw low-24* base-object FormID to its normalised
    'tes4/...nif' model key (built once by the caller from the export, and
    restricted there to blocking base types — STAT/CONT/FURN/ACTI/TREE — so
    doors/lights/markers never carve holes). Bounds come from the mesh_bounds
    cache. Rotation about Z is applied to the AABB so rotated architecture
    footprints stay aligned.

    Returns [(cx, cy, cz, hx, hy, hz), ...] in the same frame as the pathgrid.
    """
    try:
        from .mesh_bounds import get_mesh_obnd
    except ImportError:
        return []

    exclusions = []
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
        # Local half-extents and center.
        lhx = (bx1 - bx0) / 2.0
        lhy = (by1 - by0) / 2.0
        lhz = (bz1 - bz0) / 2.0
        lcx = (bx0 + bx1) / 2.0
        lcy = (by0 + by1) / 2.0
        lcz = (bz0 + bz1) / 2.0

        scale = get_float(refr, 'XSCL.Scale', 1.0) or 1.0
        lhx *= scale
        lhy *= scale
        lhz *= scale
        lcx *= scale
        lcy *= scale
        lcz *= scale

        # Placement.
        rx = get_float(refr, 'PosX')
        ry = get_float(refr, 'PosY')
        rz = get_float(refr, 'PosZ')
        rot_z = get_float(refr, 'RotZ')  # radians

        # Rotate the local center + expand the AABB to enclose the rotated box.
        cos_z, sin_z = math.cos(rot_z), math.sin(rot_z)
        wcx = rx + (lcx * cos_z - lcy * sin_z)
        wcy = ry + (lcx * sin_z + lcy * cos_z)
        wcz = rz + lcz
        # Axis-aligned enclosure of a rotated box: new half-extents.
        whx = abs(lhx * cos_z) + abs(lhy * sin_z)
        why = abs(lhx * sin_z) + abs(lhy * cos_z)
        whz = lhz

        if whx < MIN_EXCLUSION_HALF_EXTENT or why < MIN_EXCLUSION_HALF_EXTENT:
            continue

        exclusions.append((wcx, wcy, wcz, whx, why, whz))

    return exclusions


# ---------------------------------------------------------------------------
# NVNM serialiser
# ---------------------------------------------------------------------------

def _pack_nvnm(verts, tris, adj, tri_flags,
               wrld_fid, cell_fid, grid_x, grid_y, is_exterior) -> bytes:
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

    # Triangles
    buf += struct.pack('<I', len(tris))
    for ti, (v0, v1, v2) in enumerate(tris):
        e01, e12, e20 = adj[ti]
        flags = tri_flags[ti] if ti < len(tri_flags) else 0
        buf += struct.pack('<6h2H', v0, v1, v2, e01, e12, e20, flags, 0)

    # Edge Links / Door Triangles / Cover Triangles — none.
    buf += struct.pack('<III', 0, 0, 0)

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
    divisor = GRID_DIVISOR_EXTERIOR if is_exterior else GRID_DIVISOR_INTERIOR

    buf += struct.pack('<I', divisor)
    buf += struct.pack('<f', span_x / divisor)   # Max X Distance (cell width)
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
                 base_model_by_fid: dict = None) -> tuple:
    """Convert one TES4 PGRD to a TES5 NAVM record.

    Args:
        rec:                Parsed PGRD record dict.
        writer:             PluginWriter (supplies alloc_formid()).
        land_rec:           LAND record for the cell (VHGT height field).
        cell_rec:           CELL record (water height, grid coords).
        refr_recs:          REFR records in this cell (exclusion footprints).
        base_model_by_fid:  {raw_low_base_fid: 'tes4/...nif'} for footprints.

    Returns:
        (navm_bytes, meta) where meta is a dict
        {fid, wrld_fid, cell_fid, grid_x, grid_y, is_exterior, center,
         base_objects} — or (None, None) on failure.
    """
    if writer is None:
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

    # ---- Triangulate ----
    pts2d = _build_vertex_set(points, edges)
    if len(pts2d) < 3:
        return None, None
    tris = _delaunay_triangulate(pts2d)
    if not tris:
        return None, None

    verts3d = _assign_z(pts2d, points, height_sampler)

    tris = _filter_by_coverage(verts3d, tris, points, edges)
    if not tris:
        return None, None
    tris = _filter_by_area(verts3d, tris)
    if not tris:
        return None, None

    base_objects = []
    if refr_recs:
        exclusions = _build_exclusion_zones(refr_recs, base_model_by_fid or {})
        if exclusions:
            tris = _filter_by_exclusions(verts3d, tris, exclusions)
        # Base objects for ONAM: the CONT/FURN/TREE/STAT bases placed here.
        base_objects = _collect_base_objects(refr_recs)
    if not tris:
        return None, None

    verts3d, tris = _prune_unused_verts(verts3d, tris)
    if len(verts3d) < 3 or not tris:
        return None, None

    tri_flags = (_compute_water_flags(verts3d, tris, water_z)
                 if water_z is not None else [0] * len(tris))
    adj = _compute_adjacency(tris)

    nvnm = _pack_nvnm(verts3d, tris, adj, tri_flags,
                      wrld_fid, cell_fid, grid_x, grid_y, is_exterior)

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
