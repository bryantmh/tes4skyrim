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

The geometry is built in `tes5_import/navmesh/` by VOXELIZING THE REAL HAVOK
COLLISION MESHES of everything placed in the cell (plus LAND terrain outdoors).
This module keeps only the record-level concerns: reading the PGRD, doors,
water flags, adjacency, and the NVNM/NAVM binary packing (validated byte-exact
against Skyrim.esm — do not change it).

The previous approach reconstructed a "walkable floor" by buffering the pathgrid
graph into capsules/discs and subtracting the 2D CONVEX HULLS of placed objects.
That could never work: an architecture shell is a HOLLOW BOX, so its convex hull
is a solid rectangle covering the entire room.  The code was therefore forced to
classify such shells as "floors" and never carve them, which is why walls never
appeared in the output, and why rooms came out as blobs with holes.

Collision geometry answers the question directly — it is what the engine itself
uses to decide what an NPC stands on and what stops them.  See
docs/navmesh_rebuild_plan.md and tes5_import/navmesh/voxel.py.

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

# Door-triangle linking: a triangle links to a door if within this distance, and
# triangles centred on the threshold LINE (small offset along facing) are
# preferred by weighting the along-facing offset up.
DOOR_LINK_MAX_DIST = 220.0
DOOR_LINK_ALONG_WEIGHT = 2.0


_CELL_SIZE = 4096.0


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


# ---------------------------------------------------------------------------
# Walkable region (shapely) — the floor the navmesh must cover
# ---------------------------------------------------------------------------


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

    # ---- Build the navmesh from real COLLISION geometry ----
    # (The old pathgrid-buffering path is gone: it reconstructed the "floor" by
    # buffering the pathgrid graph and carving 2D convex hulls of placed objects,
    # which cannot represent a wall — an architecture shell is a hollow box whose
    # hull is a solid rectangle over the whole room.  We now voxelize the actual
    # Havok collision meshes; see tes5_import/navmesh/.)
    base_objects = []
    doors = []
    if refr_recs:
        base_objects = _collect_base_objects(refr_recs)
        doors = _collect_doors(refr_recs, door_fids)

    from .navmesh import build as navmesh_build
    from asset_convert.collision_extract import get_collision

    verts3d, tris = navmesh_build.build_navmesh(
        refr_recs or [], base_model_by_fid or {}, get_collision,
        points, edges,
        land_rec=land_rec if is_exterior else None,
        origin_x=origin_x, origin_y=origin_y)
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
