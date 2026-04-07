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
  Not exported by current exporter; cross-cell edge links are omitted.

PGRL point-to-reference mapping (variable):
  FormID(4) + array of U32 point indices
  Maps an in-cell object reference to the pathgrid points near it.
  Not needed for navmesh conversion.

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

The fundamental structural mismatch:

  PGRD = navigation GRAPH  (nodes + explicit edge list)
  NAVM = navigation MESH   (triangulated surface)

The Bethesda navmesh format requires:
  1. A triangulated polygon mesh covering the walkable surface
  2. Adjacency between every pair of edge-sharing triangles
  3. A spatial index (grid) for fast triangle lookup

Since PGRD edges represent walkable corridors between points, we model each
edge as a rectangular "corridor strip" and triangulate it:

  For each undirected edge (A, B):
    Extrude perpendicular to AB by CORRIDOR_HALF_WIDTH on each side
    This yields a quad (4 vertices), split into 2 triangles
    Average Z of A and B used for the quad's Z

  For isolated points (degree 0) — no triangles generated.
  For very short edges (< MIN_EDGE_LEN) — skip to avoid degenerate triangles.

Vertex deduplication:
  Corridor endpoints are snapped to a 1-unit grid and deduplicated so
  adjacent corridors share vertices at pathgrid nodes, enabling the engine
  to recognise them as connected.

Adjacency computation:
  After building all triangles, compute which triangles share each edge.
  Two triangles are adjacent if they share exactly two vertices (in any order).
  The S16 edge-link fields (Edge 0-1, 1-2, 2-0) reference the adjacent
  triangle's index, or -1 for boundary edges.

NavMeshGrid:
  A G×G spatial grid where G = GRID_DIVISOR (4).  Each grid cell stores the
  indices of triangles whose centroid falls within that cell.

Limitations of this approach:
  - Corridor width is uniform; real pathgrid paths may vary in width
  - Pathgrid points on stairs/slopes produce flat quads at averaged Z
  - No cover flags (set to 0)
  - No cross-cell edge links (PGRI not exported by current exporter)
  - Points with no connections produce no triangles
  - Very complex pathgrids may produce overlapping or degenerate geometry

=============================================================================
BRAINSTORM: Getting to a fully usable navmesh
=============================================================================

The corridor-extrusion approach produces structurally valid NVNM data but is
unusable in practice because: (a) triangle density is too low to represent
real walkable surface, (b) corridor quads overlap at junctions producing
degenerate geometry the engine rejects, (c) there is no height information
(Z is averaged, ignoring slopes), (d) no water / door triangles, and
(e) no inter-cell edge links. Below are ranked ideas for improvement using
every data source available.

DATA SOURCES AVAILABLE
  1. PGRD  — exact node positions + full edge graph (now exported with PGRR)
             PGRI — inter-cell exit point world positions
             PGRL — point-to-reference mappings (links nodes to placed objects)
  2. LAND  — VHGT height field: 33×33 vertex grid per cell, quantised but
             accurate. VNML vertex normals give slope direction. XCLW gives
             water height per cell.
  3. REFR/DOOR — every placed DOOR has XTEL giving target cell + exit position.
             These are the door triangles the engine needs for "found" nav.
  4. REFR/STATIC — placed STATs, FURNs, CONTs define blocking geometry.
             Their NIF collision meshes can be rasterised as exclusion zones.
  5. CELL  — DATA.Flags bit 0 = IsInterior, bit 1 = HasWater. XCLW = water Z.

IDEA 1 — LAND height field → real walkable surface (highest impact)
  The LAND VHGT subrecord encodes a 33×33 height grid (1024 game-unit cell,
  so ~32 units per vertex interval). Decode the delta-encoded heights into
  absolute Z values. Triangulate the grid into 32×32×2 = 2048 triangles.
  Mask out non-walkable triangles using:
    a. Slope threshold: VNML normals with steep Z-component (< ~0.5) → drop
    b. PGRD coverage: only keep triangles within CORRIDOR_HALF_WIDTH of at
       least one pathgrid node. This gives the engine a precise, slope-aware
       walkable surface rather than flat corridor quads.
  VHGT decode: first byte = base height (S8, multiply by 8), subsequent bytes
  are S8 row/column deltas. Full formula documented in xEdit VHGT handler.
  This alone would make exterior navmeshes engine-usable.

IDEA 2 — Constrained Delaunay triangulation at pathgrid nodes (CDT)
  Instead of extruding corridors, place pathgrid nodes directly as vertices
  and triangulate using CDT with pathgrid edges as constrained segments.
  Add LAND grid vertices as Steiner points within PGRD coverage radius.
  CDT guarantees: (a) no overlapping triangles, (b) edges of the pathgrid
  appear as triangle edges (enabling clean adjacency), (c) Z is sampled from
  LAND height field at each vertex position (bilinear interpolation).
  Python library: scipy.spatial.Delaunay (unconstrained) is available now.
  Full CDT would require shapely or a custom implementation, but even
  unconstrained Delaunay over (PGRD nodes + nearby LAND grid samples) is
  dramatically better than corridor extrusion.

IDEA 3 — Export and use PGRI inter-cell connections
  PGRI gives the world-space XYZ of the exit point in the neighbouring cell
  for every cross-cell edge. The importer can:
    a. Collect all PGRI entries for a cell at export time (not yet done)
    b. At import time, after all NAVMs are built, do a second pass:
       for each PGRI entry, find the NAVM triangle in the neighbouring cell
       whose centroid is closest to the PGRI exit point, and add an Edge Link
       record pointing to it.
  This would make inter-cell navigation work. Without it, NPCs stop at cell
  boundaries. PGRI is already defined in the TES4 format — just not exported.
  Exporter change needed: parse PGRI subrecord (14 bytes per entry:
    U16 local_point_idx, 2×U8 pad, float X, float Y, float Z)
  and emit InterCell[i].LocalPoint=N, InterCell[i].X=, Y=, Z=.

IDEA 4 — REFR DOOR records → door triangle links
  Every placed DOOR REFR has XTEL data: target CELL FormID + exit position.
  In the NAVM, door triangles (the triangle adjacent to a door) must be
  declared in the Door Triangles array with the door reference FormID and
  the CRC "PathingDoor" (0x748C1087 — needs verification from TES5.pas).
  Algorithm:
    a. For each DOOR REFR in the cell, get its world position from DATA.
    b. Find the NAVM triangle whose centroid is nearest to the door position.
    c. Set that triangle's flag bit 10 (0x0400 = Door) and add a Door Triangle
       entry with the REFR FormID and CRC.
  The export already has XTEL data on REFR. What's needed: pass the list of
  door positions into convert_PGRD. The importer pipeline must forward the
  cell's REFR list alongside the PGRD record.

IDEA 5 — Water triangle flags from CELL.XCLW
  If the cell has HasWater (DATA flags bit 1) and a water height (XCLW),
  any navmesh triangle whose Z centroid is below the water height should have
  triangle flag bit 9 (0x0200 = Water) set. This is how the engine decides
  whether the player is in shallow water for movement speed purposes.
  The cell's water height is already exported (XCLW.WaterHeight). Pass it
  into convert_PGRD and set the flag on sub-water triangles.

IDEA 6 — LAND-based slope exclusion + preferred path flags
  Using LAND vertex normals (VNML): compute the surface normal at each
  triangle centroid. If the slope angle exceeds ~46° (normal Z < 0.69),
  mark the triangle with flag bit 4 (0x0010 = No Large Creatures) or exclude
  it entirely. Triangles directly over pathgrid edges could be marked
  flag bit 6 (0x0040 = Preferred) to guide NPC pathfinding along the same
  routes Oblivion used.

IDEA 7 — Static collision mesh rasterisation (exclusion zones)
  Placed STAT/FURN/CONT references have positions + rotations. Their NIF
  files were already converted by nif_converter. The collision meshes in those
  NIFs define impassable volumes. Project these onto the navmesh and delete
  or split any triangle whose centroid falls inside a collision volume.
  This is expensive but would eliminate phantom walkable-through-wall paths.
  A simpler approximation: treat each STAT as a cylinder with radius derived
  from its NIF bounding sphere, and delete navmesh triangles within that radius.

IDEA 8 — Post-process: merge near-duplicate vertices and remove slivers
  After triangulation, run a vertex merge pass (snap to 1-unit grid,
  deduplicate) and remove degenerate triangles (area < 4 square units).
  Then rebuild adjacency. This cures the overlapping corridor quad problem
  without changing the algorithm.

IDEA 9 — PGRL node-to-reference links → ONAM population
  PGRL maps pathgrid nodes to nearby placed references (REFR FormIDs).
  NAVM.ONAM lists associated base-object FormIDs. By reading PGRL, we know
  which references are spatially associated with pathgrid nodes, and we can
  walk from REFR FormID → NAME (base object FormID) → populate ONAM.
  This is cosmetic (ONAM is used by the CK for display), but having it
  populated correctly makes the navmesh look right in xEdit / CK.

IMPLEMENTATION ORDER (recommended):
  Step 1: Export PGRI (cross-cell exits) — exporter change, no algo change
  Step 2: Pass CELL water height + door REFRs into convert_PGRD — plumbing
  Step 3: LAND height decode + bilinear Z sampling per vertex — replaces flat Z
  Step 4: Unconstrained Delaunay at PGRD nodes (scipy) — replaces corridors
  Step 5: Water and door triangle flags — straightforward once Step 2 done
  Step 6: Inter-cell edge links (Phase 2 pass after all NAVMs built)
  Step 7: Slope exclusion via LAND VNML

=============================================================================
"""

import math
import struct
import logging

from .text_reader import get_int, get_float, get_str, get_formid
from .writer import pack_record, pack_subrecord, pack_string_subrecord

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CRC32 of "PathingCell" (Bethesda variant, verified from Skyrim.esm)
_PATHING_CELL_CRC = 0x8B8F1C87

# NVNM version — always 12 for Skyrim SE
_NVNM_VERSION = 12

# Half-width of the walkable corridor generated per pathgrid edge (game units)
# Oblivion pathgrid edges typically represent ~128 unit wide walkways
CORRIDOR_HALF_WIDTH = 64.0

# Minimum edge length below which we skip corridor generation (avoids degenerate tris)
MIN_EDGE_LEN = 8.0

# NavMeshGrid divisor (G): grid is G×G cells. 4 is standard for interior cells.
GRID_DIVISOR = 4

# Vertex snap grid (1 unit) for deduplication at corridor junctions
VERT_SNAP = 1.0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _snap(v: float) -> float:
    """Snap coordinate to VERT_SNAP grid."""
    return round(v / VERT_SNAP) * VERT_SNAP


def _perp2d(dx: float, dy: float, length: float) -> tuple:
    """Return unit perpendicular (rotated 90° CCW) scaled to half-width."""
    if length < 1e-9:
        return 0.0, 0.0
    nx = -dy / length
    ny = dx / length
    return nx * CORRIDOR_HALF_WIDTH, ny * CORRIDOR_HALF_WIDTH


def _build_corridors(points: list, adjacency: dict) -> tuple:
    """Build triangles from pathgrid edges as corridor quads.

    points: list of (x, y, z)
    adjacency: dict { i: set of j } — undirected edges (only i<j stored)

    Returns:
        verts: list of (x, y, z) — deduplicated vertices
        tris:  list of (v0, v1, v2) — triangle vertex indices
    """
    vert_map: dict = {}   # (sx, sy, sz) → index
    verts: list = []
    tris: list = []

    def get_vert(x: float, y: float, z: float) -> int:
        key = (_snap(x), _snap(y), _snap(z))
        if key not in vert_map:
            vert_map[key] = len(verts)
            verts.append((x, y, z))
        return vert_map[key]

    for i, neighbours in adjacency.items():
        ax, ay, az = points[i]
        for j in neighbours:
            bx, by, bz = points[j]
            dx = bx - ax
            dy = by - ay
            edge_len = math.sqrt(dx * dx + dy * dy)
            if edge_len < MIN_EDGE_LEN:
                continue
            px, py = _perp2d(dx, dy, edge_len)
            # Average Z for the corridor (flat approximation)
            za = az
            zb = bz
            # Four corners of the corridor quad:
            #  A_left, A_right (at point A)
            #  B_left, B_right (at point B)
            al = get_vert(ax + px, ay + py, za)
            ar = get_vert(ax - px, ay - py, za)
            bl = get_vert(bx + px, by + py, zb)
            br = get_vert(bx - px, by - py, zb)
            # Split quad into 2 CCW triangles viewed from above:
            #  tri0: al, bl, ar
            #  tri1: bl, br, ar
            tris.append((al, bl, ar))
            tris.append((bl, br, ar))

    return verts, tris


def _compute_adjacency(tris: list) -> list:
    """For each triangle, find the indices of adjacent triangles per edge.

    Returns a list of (e01, e12, e20) per triangle, where each value is
    the index of the adjacent triangle or -1 for a boundary edge.

    An edge is shared when two triangles contain the same two vertex indices
    (in any order).
    """
    # Build edge → list of (tri_index, edge_slot) mapping
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
    """Build a G×G grid of triangle index lists for the NavMeshGrid.

    Each cell in the grid contains the indices of triangles whose centroid
    falls within that cell's bounding rectangle.

    Returns a list of G^2 lists, row-major (Y-major), each containing S16
    triangle indices.
    """
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
# NVNM serialiser
# ---------------------------------------------------------------------------

def _pack_nvnm(verts: list, tris: list, adj: list,
               wrld_fid: int, cell_fid: int,
               grid_x: int, grid_y: int) -> bytes:
    """Serialise all navmesh geometry into the NVNM binary blob.

    verts:    list of (x, y, z) floats
    tris:     list of (v0, v1, v2) int indices
    adj:      list of (e01, e12, e20) adjacency indices (-1 = none)
    wrld_fid: parent worldspace FormID (0 for interior)
    cell_fid: parent CELL FormID (used for interior; ignored for exterior)
    grid_x/y: exterior grid coordinates (used only when wrld_fid != 0)
    """
    buf = bytearray()

    # Version
    buf += struct.pack('<I', _NVNM_VERSION)

    # Pathing Cell header: CRC + Parent Worldspace + Parent union
    buf += struct.pack('<I', _PATHING_CELL_CRC)
    buf += struct.pack('<I', wrld_fid)

    if wrld_fid == 0:
        # Interior: parent is CELL FormID
        buf += struct.pack('<I', cell_fid)
    else:
        # Exterior: parent is {GridY(S16), GridX(S16)}
        buf += struct.pack('<hh', grid_y, grid_x)

    # Vertices
    buf += struct.pack('<I', len(verts))
    for x, y, z in verts:
        buf += struct.pack('<fff', x, y, z)

    # Triangles (16 bytes each)
    buf += struct.pack('<I', len(tris))
    for ti, (v0, v1, v2) in enumerate(tris):
        e01, e12, e20 = adj[ti]
        # Triangle flags: set bits 0-2 when corresponding edge has an
        # internal (same-navmesh) adjacency; external links not set here.
        flags = 0
        if e01 != -1:
            flags |= 0x0001
        if e12 != -1:
            flags |= 0x0002
        if e20 != -1:
            flags |= 0x0004
        cover = 0  # no cover data for converted navmeshes
        buf += struct.pack('<hhhhhh HH',
                           v0, v1, v2,
                           e01 if e01 != -1 else -1,
                           e12 if e12 != -1 else -1,
                           e20 if e20 != -1 else -1,
                           flags, cover)

    # Edge Links — none (inter-cell links not available from export data)
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

    # NavMeshGrid Divisor + max distances + bounds
    buf += struct.pack('<I', GRID_DIVISOR)
    buf += struct.pack('<f', span_x / GRID_DIVISOR)
    buf += struct.pack('<f', span_y / GRID_DIVISOR)
    buf += struct.pack('<ffffff', min_x, min_y, min_z, max_x, max_y, max_z)

    # NavMeshGrid: GRID_DIVISOR^2 cells, each U16-count-prefixed S16 list
    grid = _build_navmesh_grid(verts, tris, min_x, min_y, max_x, max_y,
                               GRID_DIVISOR)
    for cell_tris in grid:
        buf += struct.pack('<H', len(cell_tris))
        for ti in cell_tris:
            buf += struct.pack('<h', ti)

    return bytes(buf)


# ---------------------------------------------------------------------------
# Public converter
# ---------------------------------------------------------------------------

def convert_PGRD(rec: dict, writer=None) -> tuple:
    """Convert a TES4 PGRD (PathGrid) record to a TES5 NAVM (NavMesh) record.

    Uses the pathgrid point positions and per-point connection counts to
    reconstruct a walkable navmesh.  Each undirected edge in the pathgrid is
    extruded into a rectangular corridor strip and triangulated.  Triangle
    adjacency is computed from shared vertices.

    Args:
        rec:    Parsed PGRD record dict (from text_reader).
        writer: ESM writer (must supply alloc_formid()).

    Returns:
        (navm_bytes, navm_formid) on success, or (None, 0) if conversion fails
        (fewer than 3 points, writer missing, or no triangles produced).

    Limitations:
        - Corridor width is uniform (CORRIDOR_HALF_WIDTH game units).
        - Slope / Z variation on edges is modelled as a flat quad at averaged Z.
        - Cross-cell edge links (PGRI) are not produced because the current
          PGRD exporter does not export them.
        - Triangle cover flags are all zero.
    """
    if writer is None:
        return None, 0

    point_count = get_int(rec, 'DATA.PointCount')
    if point_count < 2:
        return None, 0

    # ---- Read point positions and connection degrees ----
    points = []
    degrees = []
    for i in range(point_count):
        x = get_float(rec, f'Point[{i}].X')
        y = get_float(rec, f'Point[{i}].Y')
        z = get_float(rec, f'Point[{i}].Z')
        conn = get_int(rec, f'Point[{i}].Connections')
        points.append((x, y, z))
        degrees.append(conn)

    if len(points) < 2:
        return None, 0

    # ---- Build adjacency from exported PGRR edge data ----
    # The exporter now exports Point[i].Edge[j] = target index from PGRR.
    # PGRR is a flat S16 array where point i owns degrees[i] consecutive entries.
    # We read those explicitly, then fall back to nearest-neighbour only when
    # no edge data is present (older exports or missing PGRR subrecord).
    adjacency: dict = {i: set() for i in range(len(points))}

    has_edge_data = get_int(rec, 'Point[0].Edge[0]') is not None

    if has_edge_data:
        for i in range(len(points)):
            deg = degrees[i]
            for j in range(deg):
                target = get_int(rec, f'Point[{i}].Edge[{j}]')
                if target is None:
                    break
                if 0 <= target < len(points) and target != i:
                    # Store undirected edge with canonical ordering
                    a, b = (i, target) if i < target else (target, i)
                    adjacency[a].add(b)
    else:
        # Fallback: nearest-neighbour approximation when PGRR was not exported.
        # Each point connects to its `degree` spatially nearest neighbours.
        def dist2(a, b):
            return (a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2

        for i in range(len(points)):
            deg = degrees[i]
            if deg == 0:
                continue
            others = sorted(
                [(dist2(points[i], points[j]), j) for j in range(len(points)) if j != i]
            )
            added = 0
            for _, j in others:
                if added >= deg:
                    break
                a, b = (i, j) if i < j else (j, i)
                adjacency[a].add(b)
                added += 1

    # ---- Build corridor triangles ----
    verts, tris = _build_corridors(points, adjacency)

    if not tris:
        _log.debug("PGRD FormID %s produced no triangles", rec.get('FormID', '?'))
        return None, 0

    # ---- Compute triangle adjacency ----
    adj = _compute_adjacency(tris)

    # ---- Resolve parent cell / worldspace ----
    cell_fid = get_formid(rec, 'ParentCELL') or 0
    wrld_fid = get_formid(rec, 'ParentWRLD') or 0

    # Exterior grid coordinates — derived from cell FormID if not available
    # from the record (ParentWRLD gives the worldspace, ParentCELL gives grid)
    grid_x = get_int(rec, 'GridX') or 0
    grid_y = get_int(rec, 'GridY') or 0

    # ---- Serialise NVNM blob ----
    nvnm_bytes = _pack_nvnm(verts, tris, adj, wrld_fid, cell_fid, grid_x, grid_y)

    # ---- Build NAVM record ----
    navm_fid = writer.alloc_formid()
    subs = b''

    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', f'TES4Navm{edid}')

    subs += pack_subrecord('NVNM', nvnm_bytes)

    # Record flag 0x04000000 = AutoGen (marks this as a generated navmesh)
    navm_bytes_out = pack_record('NAVM', navm_fid, 0x04000000, subs)
    return navm_bytes_out, navm_fid
