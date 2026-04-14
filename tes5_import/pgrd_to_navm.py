"""Convert TES4 PGRD (PathGrid) records to TES5 NAVM (NavMesh) records.

=============================================================================
RESEARCH NOTES — TES4 PGRD FORMAT
=============================================================================

Source: external/xEdit/Core/wbDefinitionsTES4.pas lines 3973-4009

PGRD is a top-level record stored inside a CELL group.  One per cell.

Subrecords:
  DATA  2 bytes  U16  — total point count
  PGRP  variable — array of 16-byte point structs (see below)
  PGAG  variable — bit array, (PointCount+7)//8 bytes; purpose unknown
  PGRR  variable — point-to-point connection index arrays (S16 each)
  PGRI  variable — inter-cell connections (14 bytes each, see below)
  PGRL  variable — point-to-reference mappings (FormID + U32 indices)

PGRP point struct (16 bytes):
  offset 0  float  X
  offset 4  float  Y
  offset 8  float  Z  (even Z = red/orange coloured in editor, odd = blue)
  offset 12 U8     Connection count (number of edges from this point)
  offset 13 3×U8   Padding (unused)

PGRR layout (xEdit comment, wbDefinitionsTES4.pas:3982-3987):
  A flat array of S16 target-point indices.  The connection count field in
  each PGRP entry tells how many consecutive S16 entries in PGRR belong to
  that point.  Example: if points 0..3 have counts 2,5,2,4 then PGRR[0..1]
  are point-0's neighbours, PGRR[2..6] are point-1's, etc.

  NOTE: The exporter (tes4_export/record_types/world.py) exports PGRR target
  indices as Point[i].Edge[j] lines.  Each entry is a signed S16 neighbour
  index.  The importer reads these directly to reconstruct the exact graph
  topology.  For older exports that lack this data, the importer falls back to
  nearest-neighbour approximation using per-point connection counts.

PGRI inter-cell connection (14 bytes per entry):
  offset 0  U16    Point index (local point that connects to another cell)
  offset 2  2×U8   Padding
  offset 4  float  X of the exit point (world coords in the neighbouring cell)
  offset 8  float  Y
  offset 12 float  Z
  Exported as InterCell[i].LocalPoint, InterCell[i].X/Y/Z.

PGRL point-to-reference mapping (variable):
  FormID(4) + array of U32 point indices
  Maps an in-cell object reference to the pathgrid points near it.
  Exported as RefMap[i].Reference, RefMap[i].Point[j].

=============================================================================
RESEARCH NOTES — TES5 NAVM FORMAT
=============================================================================

Source: external/xEdit/Core/wbDefinitionsTES5.pas lines 8015-8150

NAVM is a top-level record.  One NAVM per cell (interior or exterior).

Record flags of interest:
  bit 26  0x04000000  AutoGen   — set on CK-generated navmeshes
  bit 31  0x80000000  NavmeshGenCell — set on auto-generated cell navmeshes

Subrecords:
  EDID  string   Editor ID (optional)
  NVNM  binary   All geometry in one blob (see below)
  ONAM  FormID[] Associated base-object FormIDs (CONT/FURN/TREE/STAT)
  PNAM  U16[]    Preferred connector vertex indices
  NNAM  U16[]    Non-connector vertex indices

NVNM blob layout (full / non-compressed path, wbDefinitionsTES5.pas:8015-8136):

  Offset  Size  Type     Field
  ------  ----  ----     -----
  0       4     U32      Version — always 12 for Skyrim SE
  4       4     U32      CRC Hash of "PathingCell" — 0x8B8F1C87 (constant)
  8       4     FormID   Parent Worldspace (WRLD FormID, or 0 for interior)
  12      4     varies   Parent union (see below)

  Parent union — discriminated by whether Worldspace == 0:
    Worldspace != 0  (exterior):  S16 Grid-Y, S16 Grid-X  (4 bytes total)
    Worldspace == 0  (interior):  FormID of parent CELL    (4 bytes total)

  After the 16-byte header:

  16      4     U32      Vertex count (N)
  20      N*12  float×3  Vertices: X, Y, Z (little-endian float each)

  20+N*12  4    U32      Triangle count (T)
  24+N*12  T*16 struct   Triangles (see below)

  After triangles:
    4     U32      Edge link count (E)
    E*12  struct   Edge links (see below)

    4     U32      Door triangle count (D)
    D*12  struct   Door triangles (Triangle(S16), CRC(U32), DoorRef(FormID))

    4     U32      Cover triangle count (C)
    C*2   S16[]    Cover triangle indices

    4     U32      NavMeshGrid Divisor (G, typically 4 or 8)
    4     float    Max X distance
    4     float    Max Y distance
    4     float    Min X
    4     float    Min Y
    4     float    Min Z
    4     float    Max X
    4     float    Max Y
    4     float    Max Z

    Then NavMeshGrid: G×G arrays, each a U16-count-prefixed list of triangle
    indices that overlap that grid cell.  Total G^2 sub-arrays.
    Each sub-array: U16 count, then count × S16 triangle indices.

NVNM Triangle struct (16 bytes per triangle):
  offset 0   S16  Vertex 0 index
  offset 2   S16  Vertex 1 index
  offset 4   S16  Vertex 2 index
  offset 6   S16  Edge 0-1: index of adjacent triangle sharing edge v0-v1,
                            or -1 (0xFFFF) if no adjacent triangle
  offset 8   S16  Edge 1-2: adjacent triangle sharing edge v1-v2, or -1
  offset 10  S16  Edge 2-0: adjacent triangle sharing edge v2-v0, or -1
  offset 12  U16  Triangle flags (see below)
  offset 14  U16  Cover flags (see below)

Triangle flags (U16 at offset 12):
  bit 0  0x0001  Edge 0-1 link is external (to another navmesh)
  bit 1  0x0002  Edge 1-2 link is external
  bit 2  0x0004  Edge 2-0 link is external
  bit 3  0x0008  Deleted (CK Fixes extension)
  bit 4  0x0010  No Large Creatures
  bit 5  0x0020  Overlapping
  bit 6  0x0040  Preferred
  bit 9  0x0200  Water
  bit 10 0x0400  Door
  bit 11 0x0800  Found

Cover flags (U16 at offset 14) — complex encoding (xEdit comment 8060-8078):
  Bits 0-3  4-bit cover-height enum for edge 0-1
  Bits 4-5  Left/Right flags for edge 0-1 cover
  Bits 6-9  4-bit cover-height enum for edge 1-2
  Bits 10-11 Left/Right flags for edge 1-2 cover
  Bits 12-15 Reserved
  Set to 0 for auto-generated navmeshes.

NVNM Edge Link struct (12 bytes):
  offset 0  U32      Type: 0=Portal, 1=LedgeUp, 2=LedgeDown, 3=Enable/Disable
  offset 4  FormID   Target NAVM record
  offset 8  S16      Triangle index in target navmesh
  offset 10 2×U8     Padding

CRC "PathingCell" constant — 0x8B8F1C87
  Computed by Bethesda's CRC32 variant over the string "PathingCell".
  All generated navmeshes use this same constant (it is the cell-type hash,
  not a record-specific hash). Verified from vanilla Skyrim.esm navmesh data.

NavMeshGrid divisor:
  Divides the cell bounding box into a G×G grid.  Typical value is 4 for
  interior cells and 8 for exterior cells. The divisor also serves as G
  (grid size in cells per dimension), so grid has G^2 cells total.

=============================================================================
ALGORITHM: PGRD → NAVM CONVERSION
=============================================================================

Approach: Delaunay triangulation over PGRD nodes + LAND height sampling

1. Read PGRD points and edge graph.
2. Decode LAND VHGT height field (33×33 grid, 128-unit spacing) and build a
   bilinear Z-sampler for the cell.
3. Insert PGRD nodes as seed vertices.  Add extra Steiner points along each
   PGRD edge at ~64-unit intervals, and on a coarser grid within the
   PGRD coverage radius, to improve mesh density.
4. Run scipy.spatial.Delaunay on the 2D (X,Y) vertex set.
5. Keep only triangles whose centroid is within COVERAGE_RADIUS of at least
   one PGRD node (discards triangles far from any walkable path).
6. Assign Z to each vertex by bilinear interpolation from the LAND height grid.
   If no LAND data is available, fall back to the nearest PGRD point's Z.
7. Remove degenerate triangles (area < MIN_TRI_AREA).
8. Remove triangles whose centroid falls inside a static object's bounding box
   from the mesh_bounds cache (exclusion zones).
9. Compute triangle adjacency from shared edges.
10. Flag water triangles (centroid Z < water_z).
11. Serialise NVNM blob and build NAVM record.

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

# CRC constant in the NVNM header — verified from Skyrim.esm NAVM records
_PATHING_CELL_CRC = 0xA5E9A03C

# NVNM version — always 12 for Skyrim SE
_NVNM_VERSION = 12

# NavMeshGrid divisor (G): grid is G×G cells. 4 for interior, 8 for exterior.
GRID_DIVISOR_INTERIOR = 4
GRID_DIVISOR_EXTERIOR = 8

# How far from any PGRD node a Delaunay triangle centroid may be to survive
# the coverage mask.  128 units = one LAND grid vertex spacing.
COVERAGE_RADIUS = 192.0

# Spacing between Steiner points added along PGRD edges (game units).
EDGE_STEINER_SPACING = 64.0

# Minimum triangle area to keep (avoids degenerate slivers).
MIN_TRI_AREA = 16.0

# Exclusion: triangle is blocked if its centroid is within this factor of
# an object's half-extent in each axis (1.0 = exactly inside the AABB).
EXCLUSION_SCALE = 0.85


# ---------------------------------------------------------------------------
# LAND height field decoder
# ---------------------------------------------------------------------------

# Exterior cell size in game units
_CELL_SIZE = 4096.0
# LAND grid is 33×33 vertices; spacing = 4096/32 = 128 units
_LAND_VERTS = 33
_LAND_SPACING = _CELL_SIZE / (_LAND_VERTS - 1)  # 128.0

# VHGT multiplier: each delta unit = 8 game units of height
_VHGT_UNIT = 8.0


def _decode_vhgt(vhgt_hex: str) -> list:
    """Decode a VHGT hex string into a 33×33 list of absolute Z values.

    VHGT format:
      bytes 0-3:  float  HeightOffset (base height for the cell, in VHGT units)
      bytes 4-4+33*33: S8 delta array, row-major.
        row_acc starts at HeightOffset.  col_acc starts at row_acc.
        Each byte adds to col_acc; at end of each row, row_acc += last_col_acc.
      bytes 4+33*33 .. end: 3 pad bytes (ignored)

    Absolute height = accumulated_value * _VHGT_UNIT
    """
    try:
        data = bytes.fromhex(vhgt_hex)
    except ValueError:
        return []

    if len(data) < 4 + _LAND_VERTS * _LAND_VERTS:
        return []

    height_offset = struct.unpack_from('<f', data, 0)[0]
    deltas = data[4:4 + _LAND_VERTS * _LAND_VERTS]

    grid = []
    row_acc = height_offset
    idx = 0
    for _row in range(_LAND_VERTS):
        col_acc = row_acc
        row_vals = []
        for _col in range(_LAND_VERTS):
            delta = struct.unpack_from('<b', deltas, idx)[0]
            col_acc += delta
            row_vals.append(col_acc * _VHGT_UNIT)
            idx += 1
        grid.append(row_vals)
        # End of row: row_acc accumulates the last col_acc
        row_acc = col_acc

    return grid  # grid[row][col], row=0 is south, col=0 is west


def _make_height_sampler(vhgt_hex: str, cell_origin_x: float, cell_origin_y: float):
    """Return a callable f(x, y) -> z using bilinear interpolation of the LAND grid.

    cell_origin_x/y: world-space coordinates of the SW corner of the cell
    (i.e. grid_x * 4096 and grid_y * 4096 for exterior cells).

    Returns None if VHGT data is missing or invalid.
    """
    grid = _decode_vhgt(vhgt_hex)
    if not grid:
        return None

    def sample(wx: float, wy: float) -> float:
        # Convert world coords to grid coords (0..32 range)
        lx = (wx - cell_origin_x) / _LAND_SPACING
        ly = (wy - cell_origin_y) / _LAND_SPACING
        # Clamp to valid grid range
        lx = max(0.0, min(_LAND_VERTS - 1 - 1e-6, lx))
        ly = max(0.0, min(_LAND_VERTS - 1 - 1e-6, ly))
        col0 = int(lx)
        row0 = int(ly)
        col1 = min(col0 + 1, _LAND_VERTS - 1)
        row1 = min(row0 + 1, _LAND_VERTS - 1)
        fx = lx - col0
        fy = ly - row0
        z00 = grid[row0][col0]
        z10 = grid[row0][col1]
        z01 = grid[row1][col0]
        z11 = grid[row1][col1]
        return (z00 * (1 - fx) * (1 - fy) +
                z10 * fx * (1 - fy) +
                z01 * (1 - fx) * fy +
                z11 * fx * fy)

    return sample


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _tri_area_2d(ax, ay, bx, by, cx, cy) -> float:
    """Signed area of a 2D triangle (positive = CCW)."""
    return 0.5 * ((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))


def _compute_adjacency(tris: list) -> list:
    """For each triangle, find the indices of adjacent triangles per edge.

    Returns a list of (e01, e12, e20) per triangle, where each value is
    the index of the adjacent triangle or -1 for a boundary edge.
    """
    edge_map: dict = {}
    for ti, (v0, v1, v2) in enumerate(tris):
        for slot, (va, vb) in enumerate([(v0, v1), (v1, v2), (v2, v0)]):
            key = (min(va, vb), max(va, vb))
            edge_map.setdefault(key, []).append((ti, slot))

    adj = [[-1, -1, -1] for _ in tris]
    for entries in edge_map.values():
        if len(entries) == 2:
            (ti, slot_i), (tj, slot_j) = entries
            adj[ti][slot_i] = tj
            adj[tj][slot_j] = ti
    return [tuple(a) for a in adj]


def _build_navmesh_grid(verts: list, tris: list, min_x: float, min_y: float,
                        max_x: float, max_y: float, divisor: int) -> list:
    """Build a G×G grid of triangle index lists for the NavMeshGrid."""
    g = divisor
    span_x = max_x - min_x if max_x > min_x else 1.0
    span_y = max_y - min_y if max_y > min_y else 1.0

    grid = [[] for _ in range(g * g)]
    for ti, (v0, v1, v2) in enumerate(tris):
        cx = (verts[v0][0] + verts[v1][0] + verts[v2][0]) / 3.0
        cy = (verts[v0][1] + verts[v1][1] + verts[v2][1]) / 3.0
        gx = min(int((cx - min_x) / span_x * g), g - 1)
        gy = min(int((cy - min_y) / span_y * g), g - 1)
        gx = max(gx, 0)
        gy = max(gy, 0)
        grid[gy * g + gx].append(ti)

    return grid


# ---------------------------------------------------------------------------
# Delaunay triangulation
# ---------------------------------------------------------------------------

def _delaunay_triangulate(points2d: list) -> list:
    """Run scipy Delaunay on a list of (x, y) tuples.

    Returns a list of (i, j, k) index triples (CCW winding), or [] on failure.
    """
    try:
        import numpy as np
        from scipy.spatial import Delaunay  # type: ignore
    except ImportError:
        return []

    if len(points2d) < 3:
        return []

    pts = np.array(points2d, dtype=np.float64)

    # Remove near-duplicate points to avoid degenerate Delaunay input
    # (scipy Delaunay can hang or produce garbage with coincident points)
    tol = 0.5
    keep = []
    seen_keys: set = set()
    for i, (x, y) in enumerate(points2d):
        key = (round(x / tol), round(y / tol))
        if key not in seen_keys:
            seen_keys.add(key)
            keep.append(i)

    if len(keep) < 3:
        return []

    pts_unique = pts[keep]
    try:
        tri = Delaunay(pts_unique)
    except Exception:
        return []

    # Remap indices back to the original point list
    idx_map = {new: old for new, old in enumerate(keep)}
    result = []
    for simplex in tri.simplices:
        i0 = idx_map[simplex[0]]
        i1 = idx_map[simplex[1]]
        i2 = idx_map[simplex[2]]
        # Ensure CCW winding
        ax, ay = points2d[i0]
        bx, by = points2d[i1]
        cx, cy = points2d[i2]
        area = _tri_area_2d(ax, ay, bx, by, cx, cy)
        if area < 0:
            i1, i2 = i2, i1
        result.append((i0, i1, i2))

    return result


# ---------------------------------------------------------------------------
# NVNM serialiser
# ---------------------------------------------------------------------------

def _pack_nvnm(verts: list, tris: list, adj: list, tri_flags: list,
               wrld_fid: int, cell_fid: int,
               grid_x: int, grid_y: int,
               is_exterior: bool) -> bytes:
    """Serialise all navmesh geometry into the NVNM binary blob."""
    buf = bytearray()

    buf += struct.pack('<I', _NVNM_VERSION)
    buf += struct.pack('<I', _PATHING_CELL_CRC)
    buf += struct.pack('<I', wrld_fid)

    if wrld_fid == 0:
        buf += struct.pack('<I', cell_fid)
    else:
        buf += struct.pack('<hh', grid_y, grid_x)

    # Vertices
    buf += struct.pack('<I', len(verts))
    for x, y, z in verts:
        buf += struct.pack('<fff', x, y, z)

    # Triangles (16 bytes each)
    buf += struct.pack('<I', len(tris))
    for ti, (v0, v1, v2) in enumerate(tris):
        e01, e12, e20 = adj[ti]
        # Bits 0-2 mean "edge is EXTERNAL (inter-navmesh)" — must be 0 for
        # internal same-navmesh adjacency.  We produce no edge links, so all
        # adjacency is internal and these bits stay clear.
        flags = tri_flags[ti] if ti < len(tri_flags) else 0
        cover = 0
        buf += struct.pack('<hhhhhh HH',
                           v0, v1, v2,
                           e01 if e01 != -1 else -1,
                           e12 if e12 != -1 else -1,
                           e20 if e20 != -1 else -1,
                           flags, cover)

    # Edge Links — none
    buf += struct.pack('<I', 0)

    # Door Triangles — none
    buf += struct.pack('<I', 0)

    # Cover Triangles — none
    buf += struct.pack('<I', 0)

    # Bounding box
    if verts:
        all_x = [v[0] for v in verts]
        all_y = [v[1] for v in verts]
        all_z = [v[2] for v in verts]
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        min_z, max_z = min(all_z), max(all_z)
    else:
        min_x = min_y = min_z = max_x = max_y = max_z = 0.0

    span_x = max_x - min_x if max_x > min_x else 1.0
    span_y = max_y - min_y if max_y > min_y else 1.0

    divisor = GRID_DIVISOR_EXTERIOR if is_exterior else GRID_DIVISOR_INTERIOR
    buf += struct.pack('<I', divisor)
    buf += struct.pack('<f', span_x / divisor)
    buf += struct.pack('<f', span_y / divisor)
    buf += struct.pack('<ffffff', min_x, min_y, min_z, max_x, max_y, max_z)

    grid = _build_navmesh_grid(verts, tris, min_x, min_y, max_x, max_y, divisor)
    for cell_tris in grid:
        buf += struct.pack('<I', len(cell_tris))  # U32 count prefix (not U16)
        for ti in cell_tris:
            buf += struct.pack('<h', ti)

    return bytes(buf)


# ---------------------------------------------------------------------------
# Core conversion logic
# ---------------------------------------------------------------------------

def _build_vertex_set(points: list, edges: list) -> list:
    """Build the full vertex list for Delaunay input.

    Includes:
    - All PGRD nodes
    - Steiner points along each PGRD edge at EDGE_STEINER_SPACING intervals
    """
    pts2d = []
    seen_keys: set = set()

    def add(x: float, y: float):
        key = (round(x * 2), round(y * 2))  # 0.5-unit dedup
        if key not in seen_keys:
            seen_keys.add(key)
            pts2d.append((x, y))

    # Seed: all PGRD nodes
    for (x, y, *_) in points:
        add(x, y)

    # Steiner points along each edge
    for (i, j) in edges:
        ax, ay, _ = points[i]
        bx, by, _ = points[j]
        dx = bx - ax
        dy = by - ay
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1e-6:
            continue
        steps = max(1, int(length / EDGE_STEINER_SPACING))
        for s in range(1, steps):
            t = s / steps
            add(ax + dx * t, ay + dy * t)

    return pts2d


def _assign_z(pts2d: list, points: list, height_sampler) -> list:
    """Assign Z to each 2D point.

    Priority:
    1. height_sampler bilinear interpolation from LAND VHGT (if available)
    2. Nearest PGRD node Z (fallback)
    """
    if height_sampler is not None:
        return [(x, y, height_sampler(x, y)) for (x, y) in pts2d]

    # Fallback: nearest PGRD node Z
    pgrd_xy = [(p[0], p[1]) for p in points]
    pgrd_z = [p[2] for p in points]
    result = []
    for (x, y) in pts2d:
        best_d2 = 1e30
        best_z = 0.0
        for k, (px, py) in enumerate(pgrd_xy):
            d2 = (x - px) ** 2 + (y - py) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_z = pgrd_z[k]
        result.append((x, y, best_z))
    return result


def _filter_by_coverage(verts: list, tris: list, points: list) -> list:
    """Keep only triangles whose centroid is within COVERAGE_RADIUS of any PGRD node.

    Returns the filtered triangle list.
    """
    pgrd_xy = [(p[0], p[1]) for p in points]
    r2 = COVERAGE_RADIUS * COVERAGE_RADIUS
    kept = []
    for tri in tris:
        v0, v1, v2 = tri
        cx = (verts[v0][0] + verts[v1][0] + verts[v2][0]) / 3.0
        cy = (verts[v0][1] + verts[v1][1] + verts[v2][1]) / 3.0
        for (px, py) in pgrd_xy:
            if (cx - px) ** 2 + (cy - py) ** 2 <= r2:
                kept.append(tri)
                break
    return kept


def _filter_by_area(verts: list, tris: list) -> list:
    """Remove degenerate triangles (2D area < MIN_TRI_AREA)."""
    kept = []
    for tri in tris:
        v0, v1, v2 = tri
        ax, ay = verts[v0][0], verts[v0][1]
        bx, by = verts[v1][0], verts[v1][1]
        cx, cy = verts[v2][0], verts[v2][1]
        area = abs(_tri_area_2d(ax, ay, bx, by, cx, cy))
        if area >= MIN_TRI_AREA:
            kept.append(tri)
    return kept


def _filter_by_exclusions(verts: list, tris: list, exclusions: list) -> list:
    """Remove triangles whose centroid falls inside any exclusion AABB.

    exclusions: list of (cx, cy, cz, hx, hy, hz) — center + half-extents in XYZ.
    """
    if not exclusions:
        return tris
    kept = []
    for tri in tris:
        v0, v1, v2 = tri
        tx = (verts[v0][0] + verts[v1][0] + verts[v2][0]) / 3.0
        ty = (verts[v0][1] + verts[v1][1] + verts[v2][1]) / 3.0
        tz = (verts[v0][2] + verts[v1][2] + verts[v2][2]) / 3.0
        blocked = False
        for (ecx, ecy, ecz, ehx, ehy, ehz) in exclusions:
            if (abs(tx - ecx) <= ehx * EXCLUSION_SCALE and
                    abs(ty - ecy) <= ehy * EXCLUSION_SCALE and
                    abs(tz - ecz) <= ehz * EXCLUSION_SCALE):
                blocked = True
                break
        if not blocked:
            kept.append(tri)
    return kept


def _prune_unused_verts(verts: list, tris: list) -> tuple:
    """Remove vertices not referenced by any triangle; remap triangle indices."""
    used = set()
    for tri in tris:
        used.update(tri)
    old_to_new = {}
    new_verts = []
    for old_idx in sorted(used):
        old_to_new[old_idx] = len(new_verts)
        new_verts.append(verts[old_idx])
    new_tris = [(old_to_new[v0], old_to_new[v1], old_to_new[v2]) for (v0, v1, v2) in tris]
    return new_verts, new_tris


def _compute_water_flags(verts: list, tris: list, water_z: float) -> list:
    """Return per-triangle flags with Water bit (0x0200) set where centroid Z < water_z."""
    flags = []
    for tri in tris:
        v0, v1, v2 = tri
        cz = (verts[v0][2] + verts[v1][2] + verts[v2][2]) / 3.0
        flags.append(0x0200 if cz < water_z else 0)
    return flags


# ---------------------------------------------------------------------------
# Exclusion zone builder from REFR records + mesh bounds
# ---------------------------------------------------------------------------

def _build_exclusion_zones(refr_recs: list) -> list:
    """Build AABB exclusion zones from placed STATIC/FURN/CONT references.

    Uses the mesh_bounds cache loaded by the import pipeline.  Only records
    with a known NIF model and cached bounds are used.

    Returns a list of (cx, cy, cz, hx, hy, hz) tuples in world space.
    """
    try:
        from .mesh_bounds import get_mesh_obnd
    except ImportError:
        return []

    exclusions = []
    # Only exclude large static objects — skip actors, doors, lights, etc.
    _EXCLUDED_BASE_TYPES = {'DOOR', 'LIGH', 'ACTI', 'NPC_', 'CREA'}

    for refr in refr_recs:
        base_sig = get_str(refr, 'BaseType') or ''
        if base_sig in _EXCLUDED_BASE_TYPES:
            continue

        model = get_str(refr, 'Model.MODL') or get_str(refr, 'MODL')
        if not model:
            continue

        # Normalise path the same way mesh_bounds does
        norm = model.lower().replace('\\', '/')
        if not norm.startswith('tes4/'):
            norm = 'tes4/' + norm.lstrip('/')
        if not norm.endswith('.nif'):
            norm += '.nif' if '.' not in norm.split('/')[-1] else ''

        bounds = get_mesh_obnd(norm)
        if bounds is None:
            continue

        bx0, by0, bz0, bx1, by1, bz1 = bounds
        # Half-extents
        hx = (bx1 - bx0) / 2.0
        hy = (by1 - by0) / 2.0
        hz = (bz1 - bz0) / 2.0
        # Skip tiny objects (less than 32 units in any horizontal axis)
        if hx < 32 or hy < 32:
            continue

        # World position from REFR DATA
        rx = get_float(refr, 'DATA.PosX') or 0.0
        ry = get_float(refr, 'DATA.PosY') or 0.0
        rz = get_float(refr, 'DATA.PosZ') or 0.0

        # AABB center in world space (bounds are model-local; ignore rotation for now)
        local_cx = (bx0 + bx1) / 2.0
        local_cy = (by0 + by1) / 2.0
        local_cz = (bz0 + bz1) / 2.0

        exclusions.append((rx + local_cx, ry + local_cy, rz + local_cz, hx, hy, hz))

    return exclusions


# ---------------------------------------------------------------------------
# NAVM record packer
# ---------------------------------------------------------------------------

def _pack_navm_record(form_id: int, subrecords: bytes) -> bytes:
    """Pack a NAVM record with the Compressed flag (0x00040000) set.

    When the Compressed flag is set the record data layout is:
      4 bytes  U32  uncompressed data size
      N bytes       zlib-compressed data (deflate, level 6)

    The record header dataSize field holds the compressed size + 4
    (the 4-byte uncompressed-size prefix is counted).
    """
    import zlib
    uncompressed_size = len(subrecords)
    compressed = zlib.compress(subrecords, 6)
    payload = struct.pack('<I', uncompressed_size) + compressed

    flags = 0x00040000  # Compressed
    sig_bytes = b'NAVM'
    # TES5 record header: sig(4) + dataSize(4) + flags(4) + formID(4) + vcs1(4) + formVersion(2) + vcs2(2)
    header = struct.pack('<4sIIIIHH',
                         sig_bytes,
                         len(payload),
                         flags,
                         form_id,
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
                 refr_recs: list = None) -> tuple:
    """Convert a TES4 PGRD (PathGrid) record to a TES5 NAVM (NavMesh) record.

    Uses Delaunay triangulation over pathgrid nodes and LAND height data to
    produce a walkable navmesh surface.

    Args:
        rec:       Parsed PGRD record dict (from text_reader).
        writer:    ESM writer (must supply alloc_formid()).
        land_rec:  LAND record for this cell (provides VHGT height field).
        cell_rec:  CELL record (provides water height, interior flag, grid coords).
        refr_recs: List of REFR records in this cell (for exclusion zones).

    Returns:
        (navm_bytes, navm_formid) on success, or (None, 0) if conversion fails.
    """
    if writer is None:
        return None, 0

    point_count = get_int(rec, 'DATA.PointCount')
    if point_count is None or point_count < 2:
        return None, 0

    # ---- Read point positions and connection degrees ----
    points = []
    degrees = []
    for i in range(point_count):
        x = get_float(rec, f'Point[{i}].X') or 0.0
        y = get_float(rec, f'Point[{i}].Y') or 0.0
        z = get_float(rec, f'Point[{i}].Z') or 0.0
        conn = get_int(rec, f'Point[{i}].Connections') or 0
        points.append((x, y, z))
        degrees.append(conn)

    if len(points) < 2:
        return None, 0

    # ---- Build edge list from exported PGRR data ----
    edges = []
    has_edge_data = get_int(rec, 'Point[0].Edge[0]') is not None

    if has_edge_data:
        seen_edges: set = set()
        for i in range(len(points)):
            deg = degrees[i]
            for j in range(deg):
                target = get_int(rec, f'Point[{i}].Edge[{j}]')
                if target is None:
                    break
                if 0 <= target < len(points) and target != i:
                    key = (min(i, target), max(i, target))
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append(key)
    else:
        # Fallback: nearest-neighbour edges when PGRR was not exported
        def _dist2(a, b):
            return (a[0]-b[0])**2 + (a[1]-b[1])**2

        seen_edges = set()
        for i in range(len(points)):
            deg = degrees[i]
            if deg == 0:
                continue
            others = sorted((_dist2(points[i], points[j]), j)
                            for j in range(len(points)) if j != i)
            added = 0
            for _, j in others:
                if added >= deg:
                    break
                key = (min(i, j), max(i, j))
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append(key)
                added += 1

    # ---- Resolve parent cell / worldspace info ----
    cell_fid = get_formid(rec, 'ParentCELL') or 0
    wrld_fid = get_formid(rec, 'ParentWRLD') or 0
    grid_x = get_int(rec, 'GridX') or 0
    grid_y = get_int(rec, 'GridY') or 0
    is_exterior = wrld_fid != 0

    # ---- Determine cell origin for LAND height sampling ----
    # Exterior cells: origin = (grid_x * 4096, grid_y * 4096)
    # Interior cells: no meaningful LAND; use fallback Z
    cell_origin_x = 0.0
    cell_origin_y = 0.0
    if is_exterior:
        if cell_rec is not None:
            cg_x = get_int(cell_rec, 'XCLC.X') or grid_x
            cg_y = get_int(cell_rec, 'XCLC.Y') or grid_y
        else:
            cg_x, cg_y = grid_x, grid_y
        cell_origin_x = cg_x * _CELL_SIZE
        cell_origin_y = cg_y * _CELL_SIZE

    # ---- Build height sampler from LAND VHGT ----
    height_sampler = None
    if land_rec is not None and is_exterior:
        vhgt_hex = get_str(land_rec, 'VHGT')
        if vhgt_hex:
            height_sampler = _make_height_sampler(vhgt_hex, cell_origin_x, cell_origin_y)

    # ---- Water height ----
    water_z = None
    if cell_rec is not None:
        cell_flags = get_int(cell_rec, 'DATA.Flags') or 0
        has_water = bool(cell_flags & 0x02)
        if has_water:
            wz = get_float(cell_rec, 'XCLW.WaterHeight')
            if wz is not None:
                water_z = wz

    # ---- Build 2D vertex set (PGRD nodes + Steiner points) ----
    pts2d = _build_vertex_set(points, edges)

    if len(pts2d) < 3:
        _log.debug("PGRD FormID %s: fewer than 3 unique 2D points", rec.get('FormID', '?'))
        return None, 0

    # ---- Delaunay triangulation ----
    tris = _delaunay_triangulate(pts2d)
    if not tris:
        _log.debug("PGRD FormID %s: Delaunay produced no triangles", rec.get('FormID', '?'))
        return None, 0

    # ---- Assign Z to all 2D vertices ----
    verts3d = _assign_z(pts2d, points, height_sampler)

    # ---- Coverage masking: keep only triangles near a PGRD node ----
    tris = _filter_by_coverage(verts3d, tris, points)
    if not tris:
        _log.debug("PGRD FormID %s: no triangles survived coverage mask", rec.get('FormID', '?'))
        return None, 0

    # ---- Remove degenerate triangles ----
    tris = _filter_by_area(verts3d, tris)
    if not tris:
        return None, 0

    # ---- Static object exclusion zones ----
    if refr_recs:
        exclusions = _build_exclusion_zones(refr_recs)
        if exclusions:
            tris = _filter_by_exclusions(verts3d, tris, exclusions)

    if not tris:
        return None, 0

    # ---- Prune unreferenced vertices ----
    verts3d, tris = _prune_unused_verts(verts3d, tris)

    # ---- Per-triangle flags (water) ----
    tri_flags = []
    if water_z is not None:
        tri_flags = _compute_water_flags(verts3d, tris, water_z)
    else:
        tri_flags = [0] * len(tris)

    # ---- Triangle adjacency ----
    adj = _compute_adjacency(tris)

    # ---- Serialise NVNM blob ----
    nvnm_bytes = _pack_nvnm(verts3d, tris, adj, tri_flags,
                             wrld_fid, cell_fid, grid_x, grid_y, is_exterior)

    # ---- Build NAVM record ----
    navm_fid = writer.alloc_formid()
    subs = b''

    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', f'TES4Navm{edid}')

    subs += pack_subrecord('NVNM', nvnm_bytes)

    navm_bytes_out = _pack_navm_record(navm_fid, subs)
    return navm_bytes_out, navm_fid
