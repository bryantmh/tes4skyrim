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

The geometry is built in `tes5_import/navmesh/` from the PATHGRID: a boolean
union of fixed-width corridor ribbons, one per pathgrid edge, sat onto the real
Havok collision (plus LAND terrain outdoors) and stopped at walls.  This module
keeps only the record-level concerns: reading the PGRD, doors, water flags,
adjacency, and the NVNM/NAVM binary packing (validated byte-exact against
Skyrim.esm — do not change it).

Two earlier approaches are worth knowing about, because both failure modes are
easy to reinvent:

  * Buffering the pathgrid into capsules/discs and subtracting the 2D CONVEX
    HULLS of placed objects.  An architecture shell is a HOLLOW BOX, so its hull
    is a solid rectangle over the whole room; such shells had to be classified
    "floor" and never carved, so walls never appeared and rooms came out as
    blobs with holes.
  * VOXELIZING the collision and re-discovering walkable surface (regions →
    contours → triangles).  Correct in principle, but it introduced seams that
    fought connectivity, which is what the corridor model exists to avoid.

See docs/commentary/tes5_import_navmesh.md and tes5_import/navmesh/corridor.py.

Returns per-navmesh metadata (centroid, parent) so the caller can build NAVI.
=============================================================================
"""

import hashlib
import math
import os
import pickle
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

# NVNM Edge Link types (xEdit wbNavmeshEdgeLinkEnum).  A DROP-DOWN is a pair:
# Ledge Down on the upper triangle, Ledge Up on the lower.
_EDGE_LINK_PORTAL = 0
_EDGE_LINK_LEDGE_UP = 1
_EDGE_LINK_LEDGE_DOWN = 2

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

# How far past the cell seam an InterCell exit point may sit and still be
# treated as a genuine cross-cell link.  A valid exit lands in an orthogonally
# adjacent cell, so it is within one cell width of this cell's border; anything
# beyond two cells is CS garbage (see _collect_intercell).
_INTERCELL_MAX_REACH = 2.0 * _CELL_SIZE


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _collect_intercell(rec, points, origin_x, origin_y):
    """Cross-cell pathgrid links from PGRI, as [(local_point, (x, y, z)), ...].

    Each PGRI entry names a LOCAL point in this cell and the world-space EXIT
    point it connects to in a NEIGHBOURING cell.  Oblivion's PGRI is padded with
    uninitialised CS memory: entries whose LocalPoint is out of range, or whose
    exit coordinates are denormalised / absurdly far from this cell, are garbage
    and dropped (InterCell[1] in AnvilExterior: LocalPoint=17292, X=0,
    Y~2.5e-41).  Only a plausible exit — finite, non-denormal, and within
    _INTERCELL_MAX_REACH of the cell origin — survives.

    origin_x/origin_y are this exterior cell's world origin (SW corner).  The
    resulting edges make the corridor ribbon reach across the seam so the
    downstream edge-link pass has real border edges to stitch.
    """
    count = get_int(rec, 'InterCellCount', 0)
    if count <= 0:
        return []
    out = []
    n = len(points)
    for i in range(count):
        lp = rec.get(f'InterCell[{i}].LocalPoint')
        if lp is None:
            continue
        try:
            lp = int(lp)
        except (TypeError, ValueError):
            continue
        if not (0 <= lp < n):
            continue                      # LocalPoint out of range -> garbage
        x = get_float(rec, f'InterCell[{i}].X')
        y = get_float(rec, f'InterCell[{i}].Y')
        z = get_float(rec, f'InterCell[{i}].Z')
        if x is None or y is None or z is None:
            continue
        # Reject denormals / non-finite (CS-uninitialised memory) and exits too
        # far from this cell to be an orthogonal neighbour.
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        if abs(x) < 1e-3 and abs(y) < 1e-3:
            continue                      # (0,0,~0) padding
        if (x < origin_x - _INTERCELL_MAX_REACH or
                x > origin_x + _CELL_SIZE + _INTERCELL_MAX_REACH or
                y < origin_y - _INTERCELL_MAX_REACH or
                y > origin_y + _CELL_SIZE + _INTERCELL_MAX_REACH):
            continue
        out.append((lp, (x, y, z)))
    return out

def _tri_area_2d(ax, ay, bx, by, cx, cy) -> float:
    """Signed area of a 2D triangle (positive = CCW)."""
    return 0.5 * ((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))


def _compute_adjacency(tris: list, verts: list = None) -> list:
    """Return per-edge adjacent-triangle indices, or ``-1`` at a boundary.

    A shared geometric edge is not necessarily a walkable portal. In
    particular, the CK rejects two linked triangles whose surface normals face
    in nearly opposite directions. That topology occurs at folded vertical
    seams in generated corridor geometry. When coordinates are available,
    leave those seams closed instead of advertising an impossible crossing to
    the pathfinder. The optional argument preserves the topology-only helper
    used by older callers and tests.
    """
    edge_map: dict = {}
    for ti, (v0, v1, v2) in enumerate(tris):
        for slot, (va, vb) in enumerate([(v0, v1), (v1, v2), (v2, v0)]):
            key = (min(va, vb), max(va, vb))
            edge_map.setdefault(key, []).append((ti, slot))

    normal_data = None
    if verts is not None:
        # Compute once per triangle, not once per shared edge. Large exterior
        # cells can contain thousands of triangles and each has three edges.
        normal_data = [None] * len(tris)
        for ti, (v0, v1, v2) in enumerate(tris):
            try:
                a, b, c = verts[v0], verts[v1], verts[v2]
            except (IndexError, TypeError):
                continue
            ux, uy, uz = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            vx, vy, vz = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            nx, ny, nz = (uy * vz - uz * vy,
                          uz * vx - ux * vz,
                          ux * vy - uy * vx)
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length > 1e-9:
                normal_data[ti] = (nx, ny, nz, length)

    adj = [[-1, -1, -1] for _ in tris]
    for entries in edge_map.values():
        if len(entries) == 2:
            (ti, si), (tj, sj) = entries
            if normal_data is not None:
                a, b = normal_data[ti], normal_data[tj]
                if (a is not None and b is not None and
                        (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) /
                        (a[3] * b[3]) < -0.9):
                    continue
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


# model_key -> (cx, cy) local-space XY midpoint of the door PANEL (largest mesh
# shape), relative to the model origin (the REFR pivot).  Loaded from the
# door-centres cache built in the SAME pass as collision + mesh bounds
# (asset_convert.collision_extract.scan_mesh_data).  The REFR pivot sits on the
# HINGE, not the opening; the panel midpoint is the point an actor walks
# through, and where the Door Triangle belongs.
_DOOR_CENTROIDS = {}
# model key -> local z of the door mesh's BASE (its bbox z-min).  Added to the
# REFR PosZ this gives the threshold's floor height; see load_door_centroids.
_DOOR_FLOOR_DZ = {}
# model key -> True when the door's threshold runs along the mesh's LOCAL +Y
# (the wider horizontal extent), False when it runs along local +X.  Read per
# model in load_door_centroids; door meshes do not share one convention.
_DOOR_THRESH_LOCAL_Y = {}
# Models whose collision panel is thin in Z: trapdoors, hatches and display
# cases.  They swing about a HORIZONTAL axis, so no vertical-axis threshold
# line exists and they must not receive a door quad.
_DOOR_NO_THRESHOLD = set()
# model key -> real doorway WIDTH in world units, measured off the collision
# panel.  Door panels range from 16u to 764u wide (median 121), so a single
# hardcoded half-width cannot span them: at 90u it left the first 30u of
# impdundoor01's 115u threshold with no mesh, shrinking the Door Triangle to a
# 571-unit scrap that actors could not stand on.
_DOOR_WIDTH = {}
# model key -> (cx, cy) local-space panel centre from the COLLISION PANEL,
# world units.  Preferred over _DOOR_CENTROIDS (the mesh-bbox centres cache):
# verified against placed doors in-world the collision centre is exact to
# ~1.5u, where the mesh-bbox centre was 25-35u off along the threshold on the
# CharacterGen prison gates (cgprisoncellgate01, idgate01) — more than half
# those gates' own 40/63u width, putting the Door Triangle mostly on the jamb.
_DOOR_PANEL_CTR = {}


def door_threshold_axis(model_key):
    """Unit local-space direction the door's threshold line runs along.

    Returns (x, y) in the door mesh's LOCAL frame; the caller rotates it by the
    REFR's RotZ.  Defaults to local +Y, which is the more common door layout.
    """
    if _DOOR_THRESH_LOCAL_Y.get(model_key, True):
        return (0.0, 1.0)
    return (1.0, 0.0)


def load_door_centroids(cache_path, quiet: bool = False) -> int:
    """Load door panel centres/axes/widths for the plugin's door models.

    cache_path: path to door_centers_cache.json (sits beside the bounds cache).
    Keys are mesh_bounds-style ('tes4/...'), matching the door_fids map.

    The authoritative source is door_panel_axis_cache.json (written by
    scan_mesh_data and tools/build_door_axis_cache.py from the COLLISION
    PANEL): axis, doorway width, and — in the 4-element form — the exact panel
    centre.  The legacy door_centers_cache.json (mesh-bbox centres) is only a
    fallback for models the axis cache lacks a centre for; a plugin without it
    still loads fully from the axis cache.
    """
    import json
    _DOOR_CENTROIDS.clear()
    _DOOR_FLOOR_DZ.clear()
    _DOOR_THRESH_LOCAL_Y.clear()
    _DOOR_NO_THRESHOLD.clear()
    _DOOR_WIDTH.clear()
    _DOOR_PANEL_CTR.clear()
    if not cache_path:
        return 0
    base_dir = os.path.dirname(cache_path)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding='utf-8') as fh:
                raw = json.load(fh)
            for k, v in raw.items():
                _DOOR_CENTROIDS[k] = (float(v[0]), float(v[1]))
        except (OSError, ValueError) as exc:
            if not quiet:
                print(f"  Door centres: could not load cache ({exc})")
    # Which LOCAL axis the threshold runs along, its width, and (4-element
    # entries) the exact panel centre, all read from the door's COLLISION
    # PANEL (asset_convert.collision_extract).  The whole-NIF bounding box
    # cannot answer the axis: it includes the door FRAME/arch, which dwarfs
    # the panel and inverts the result -- AnvilDoorMC01's bbox is 98 x 150
    # ("Y wider" -> threshold Y) while its panel is 97.9 x 4.5 -> threshold X.
    # Nine door models were wrong that way, each placing its navmesh door quad
    # 90 degrees out (Anvil's exterior doors among them).
    apath = os.path.join(base_dir, 'door_panel_axis_cache.json')
    if os.path.exists(apath):
        try:
            with open(apath, encoding='utf-8') as fh:
                axes = json.load(fh)
            for k, a in axes.items():
                if isinstance(a, str):              # axis-only cache
                    _DOOR_THRESH_LOCAL_Y[k] = (a == 'Y')
                    continue
                _DOOR_THRESH_LOCAL_Y[k] = (a[0] == 'Y')
                if len(a) > 1 and a[1]:
                    _DOOR_WIDTH[k] = float(a[1])
                if len(a) > 3:
                    _DOOR_PANEL_CTR[k] = (float(a[2]), float(a[3]))
                if len(a) > 4:
                    # Closed slab z-min: better floor drop than the whole-NIF
                    # bounds z-min (which includes the frame reaching below).
                    _DOOR_FLOOR_DZ[k] = float(a[4])
            # A centres-cache model ABSENT from the axis cache is a trapdoor/
            # hatch (thin in Z, no vertical-axis threshold) OR unreadable
            # collision.  Only the former may lose its door marker, and the
            # cache cannot tell them apart, so nothing is dropped here — see
            # the _DOOR_NO_THRESHOLD note in _collect_doors.
            for k in _DOOR_CENTROIDS:
                if k not in axes:
                    _DOOR_NO_THRESHOLD.add(k)
        except (OSError, ValueError):
            pass
    # The REFR pivot sits at the door mesh's local z=0, which for a door is
    # up at the HINGE, not on the floor: impdundoor01's panel runs local z
    # -140.8..+57.7, so PosZ is ~141u above the threshold it stands on.
    # The door-quad Z gate (DOOR_QUAD_ZTOL, 128) then rejected every
    # corridor edge at the real floor height and the door produced NO
    # footprint at all -- measured on CharacterGen's Ambush A doors, off by
    # 140.8 and 144.0.  The mesh-bounds cache already carries each model's
    # local z-min, so read it from there rather than duplicating it here.
    bpath = os.path.join(base_dir, 'mesh_bounds_cache.json')
    if os.path.exists(bpath) and (_DOOR_CENTROIDS or _DOOR_PANEL_CTR):
        try:
            with open(bpath, encoding='utf-8') as fh:
                bounds = json.load(fh)
            for k in set(_DOOR_CENTROIDS) | set(_DOOR_PANEL_CTR):
                if k in _DOOR_FLOOR_DZ:
                    continue            # axis cache carries the slab z-min
                b = bounds.get(k)
                if not b or len(b) < 6:
                    continue
                _DOOR_FLOOR_DZ[k] = float(b[2])
        except (OSError, ValueError):
            pass
    n = len(set(_DOOR_CENTROIDS) | set(_DOOR_PANEL_CTR))
    if not quiet:
        print(f"  Door centres: loaded {n} entries "
              f"({len(_DOOR_PANEL_CTR)} exact panel centres)")
    return n


def _door_threshold(refr, model_key):
    """World-space doorway centre of a door REFR: (x, y, z) or None.

    THE REFR POSITION IS THE MODEL'S PIVOT (the HINGE), NOT THE DOORWAY.  The
    doorway centre is the midpoint of the door mesh's bounding box (see
    load_door_centroids), offset from the pivot and rotated into world space by
    the ref's RotZ.  z stays at the REFR position (only used to pick the
    storey).  Returns None when no midpoint is known (falls back to raw pos).
    """
    if not model_key:
        return None
    # Collision-panel centre first (exact); mesh-bbox centre as fallback.
    c = _DOOR_PANEL_CTR.get(model_key) or _DOOR_CENTROIDS.get(model_key)
    if c is None:
        return None
    scale = get_float(refr, 'XSCL.Scale', 1.0) or 1.0
    rz = get_float(refr, 'RotZ') or 0.0
    cosz, sinz = math.cos(rz), math.sin(rz)
    lx, ly = c[0] * scale, c[1] * scale
    # Bethesda placement applies the TRANSPOSE of the naive rotation (see
    # navmesh/world.py _rot_matrix, verified against the AnvilFG floor shell).
    # The naive CCW form put Arvena's upstairs door centre one FULL door width
    # from the real doorway — only doors rotated 90/270 expose the error
    # (0/180 are sign-invariant), which is why it survived every 0/180 test.
    wx = get_float(refr, 'PosX') + (lx * cosz + ly * sinz)
    wy = get_float(refr, 'PosY') + (-lx * sinz + ly * cosz)
    # Drop z from the pivot (the hinge, up the door leaf) to the panel's base,
    # so the door sits on the storey it actually opens onto.
    wz = get_float(refr, 'PosZ') + _DOOR_FLOOR_DZ.get(model_key, 0.0) * scale
    return (wx, wy, wz)


def _finite_door_point(pt, rot_z):
    """True if a door's threshold point is a usable finite coordinate."""
    from .navmesh.world import _MAX_PLACEMENT
    for v in pt:
        if v is None or not math.isfinite(v) or abs(v) > _MAX_PLACEMENT:
            return False
    return rot_z is None or math.isfinite(rot_z)


def _collect_doors(refr_recs, door_fids):
    """Return [(x, y, z, rot_z, ref_fid, is_teleport, width), ...] for doors.

    A door is a REFR whose base is a DOOR (in door_fids) or that has an XTEL
    teleport. is_teleport distinguishes cross-cell doors (XTEL — link two
    navmeshes) from interior-only doors (same cell, just a passage).

    door_fids maps raw low-24 DOOR base FormIDs to normalised model keys
    (legacy plain sets still work — membership only, no centring).  The panel
    centroid corrects the point from the REFR pivot (hinge) to the doorway
    centre (see _door_threshold); z stays at the REFR position.
    """
    if not refr_recs:
        return []
    door_fids = door_fids or {}
    is_map = isinstance(door_fids, dict)
    out = []
    for refr in refr_recs:
        name = refr.get('NAME', '')
        is_teleport = bool(refr.get('XTEL.Door'))
        base = None
        if name:
            try:
                base = int(name, 16) & 0xFFFFFF
            except ValueError:
                base = None
        base_is_door = base is not None and base in door_fids
        if not (is_teleport or base_is_door):
            continue
        ref_fid = get_formid(refr, 'FormID')
        pt = _door_threshold(refr, door_fids.get(base)) if is_map else None
        if pt is None:
            pt = (get_float(refr, 'PosX'), get_float(refr, 'PosY'),
                  get_float(refr, 'PosZ'))
        rot_z = get_float(refr, 'RotZ')
        # A door ref with a garbage/uninitialised position would force its base
        # line into the mesh at an impossible coordinate, stretching the strip
        # extents the native index buckets over (see navmesh/world.py
        # _MAX_PLACEMENT -- Nehrim ships refs with PosY = 8.9e17). Such a door
        # is nowhere near the pathgrid, so it cannot be linked anyway.
        if not _finite_door_point(pt, rot_z):
            continue
        # Normalise the rotation so that, for EVERY door, local +Y is the
        # threshold direction.  Door meshes disagree about which local axis is
        # the wide one (impdundoor01 is wide in Y, icdoorint01 wide in X), so a
        # single convention downstream rotated some doors 90 degrees.  Adding a
        # quarter turn for the X-wide meshes lets every consumer -- the door
        # quad and navmesh_preview alike -- use one rule.
        rz = rot_z if rot_z is not None else 0.0
        mk = door_fids.get(base) if is_map else None
        # A trapdoor/hatch has no vertical-axis threshold, so it gets NO QUAD
        # (width 0 below).  It is still emitted: _build_door_links must give
        # every door a Door Triangle or the doorway goes dead in the engine.
        # Dropping them here deleted the Imperial Prison cell gates — the
        # player's own starting cell door among them — because a shape the
        # extractor could not read (bhkListShape) is indistinguishable from a
        # real trapdoor in the cache.
        if mk is not None and not _DOOR_THRESH_LOCAL_Y.get(mk, True):
            rz += math.pi * 0.5
        scale = get_float(refr, 'XSCL.Scale', 1.0) or 1.0
        width = _DOOR_WIDTH.get(mk, 0.0) * scale if mk is not None else 0.0
        out.append((pt[0], pt[1], pt[2], rz, ref_fid, is_teleport, width))
    return out


def _build_door_links(verts, tris, doors):
    """Return [(triangle_index, door_ref_fid), ...], one per door.

    The mesh generator forces every door's base line in as a triangle edge
    (corridor_doors + build_union_mesh's door_edges), so the door triangle is
    normally simply the
    triangle CONTAINING the door position at the door's height — precise by
    construction.  When no triangle contains the point (the quad was culled or
    there is no mesh at the door), fall back to the nearest triangle centred
    on the threshold line.  door_ref_fid is the (remapped) REFR FormID the
    engine walks through.
    """
    if not doors or not tris:
        return []

    cents = []
    for (a, b, c) in tris:
        cents.append(((verts[a][0] + verts[b][0] + verts[c][0]) / 3.0,
                      (verts[a][1] + verts[b][1] + verts[c][1]) / 3.0))

    def _containing(dx, dy, dz):
        """Triangle containing (dx,dy) nearest dz, or None.

        The door point lies ON the threshold, which after constraint recovery is
        a shared triangle EDGE — so two or more triangles legitimately contain
        it and the choice is a tie.  Breaking that tie by triangle INDEX picked
        an arbitrary one: on the CharacterGen assassins' cell door the same
        geometry yielded either a 1,586-unit triangle or a 572-unit sliver
        depending only on iteration order, and the sliver is too narrow for an
        actor to stand on (vanilla door triangles: min 992, median 9,614).
        Prefer the LARGEST containing triangle, which is both standable and
        deterministic.
        """
        best = None            # ((z-band, -area, ti), ti)
        for ti, (a, b, c) in enumerate(tris):
            va, vb, vc = verts[a], verts[b], verts[c]
            d = ((vb[1] - vc[1]) * (va[0] - vc[0]) +
                 (vc[0] - vb[0]) * (va[1] - vc[1]))
            if abs(d) < 1e-9:
                continue
            l0 = ((vb[1] - vc[1]) * (dx - vc[0]) +
                  (vc[0] - vb[0]) * (dy - vc[1])) / d
            l1 = ((vc[1] - va[1]) * (dx - vc[0]) +
                  (va[0] - vc[0]) * (dy - vc[1])) / d
            l2 = 1.0 - l0 - l1
            if l0 < -0.01 or l1 < -0.01 or l2 < -0.01:
                continue
            z = l0 * va[2] + l1 * vb[2] + l2 * vc[2]
            dzz = abs(z - dz)
            area = 0.5 * abs(d)
            # Heights within a step are "the same floor": rank those by AREA so
            # a sliver never wins over the real door triangle beside it.
            key = (round(dzz / 32.0), -area, ti)
            if dzz <= 128.0 and (best is None or key < best[0]):
                best = (key, ti)
        return best[1] if best else None

    links = []
    for (dx, dy, dz, rot_z, ref_fid, _is_tp, _w) in doors:
        if not ref_fid:
            continue
        best_ti = _containing(dx, dy, dz)
        if best_ti is None:
            # Door facing axis.  _collect_doors normalises RotZ so the
            # THRESHOLD is (sin rz, cos rz) under the transpose placement
            # convention; the facing is the perpendicular (cos rz, -sin rz).
            fx, fy = math.cos(rot_z), -math.sin(rot_z)
            best_cost = None
            for ti, (cx, cy) in enumerate(cents):
                ox, oy = cx - dx, cy - dy
                dist2 = ox * ox + oy * oy
                if dist2 > (DOOR_LINK_MAX_DIST ** 2):
                    continue
                # Prefer triangles centred near the threshold LINE (small
                # |offset along facing|) and close to the door point.
                along = abs(ox * fx + oy * fy)
                cost = dist2 + (along * DOOR_LINK_ALONG_WEIGHT) ** 2
                if best_cost is None or cost < best_cost:
                    best_cost, best_ti = cost, ti
        if best_ti is not None:
            links.append((best_ti, ref_fid))
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


def _open_edge_towards(verts, tris, adj, ti, other_ti):
    """Slot (0..2) of ti's border edge facing triangle other_ti, or None.

    A drop-down is stepped off an OPEN edge — one with no neighbour, i.e. the
    lip.  Of those, the one whose midpoint is closest to the other side is the
    edge the actor actually uses.
    """
    tri = tris[ti]
    ox = sum(verts[i][0] for i in tris[other_ti]) / 3.0
    oy = sum(verts[i][1] for i in tris[other_ti]) / 3.0
    best = None
    for slot in range(3):
        if adj[ti][slot] >= 0:
            continue                    # has a neighbour: not a lip
        a = verts[tri[slot]]
        b = verts[tri[(slot + 1) % 3]]
        mx, my = 0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1])
        d = (mx - ox) ** 2 + (my - oy) ** 2
        if best is None or d < best[0]:
            best = (d, slot)
    return best[1] if best else None


def _resolve_ledge_links(verts, tris, adj, ledges, navm_fid):
    """Resolve each ledge pair to the open edge an actor steps off.

    Returns (link_bit, link_slot, edge_links). A pair is committed only when
    BOTH directions resolve an open edge: a one-sided link is what the CK
    flags as "Bad portal navmesh ID/triangle index", so the lip is left
    un-linked instead.

    See: docs/commentary/tes5_import_navmesh.md#ledge-links
    """
    link_bit = {}
    link_slot = {}
    edge_links = []
    for (hi, lo, _drop) in ledges:
        if not (0 <= hi < len(tris) and 0 <= lo < len(tris)):
            continue
        pending = []
        for (ti, other, typ) in ((hi, lo, _EDGE_LINK_LEDGE_DOWN),
                                 (lo, hi, _EDGE_LINK_LEDGE_UP)):
            slot = _open_edge_towards(verts, tris, adj, ti, other)
            if slot is None or (ti, slot) in link_slot:
                pending = None
                break
            pending.append((ti, other, typ, slot))
        if not pending:
            continue
        for (ti, other, typ, slot) in pending:
            link_bit[ti] = link_bit.get(ti, 0) | _TRI_EDGE_LINK[slot]
            link_slot[(ti, slot)] = len(edge_links)
            edge_links.append((typ, navm_fid, other))
    return link_bit, link_slot, edge_links


def _pack_bounds_and_grid(verts, tris) -> bytes:
    """The NVNM tail: bounding box, bucket size, and the spatial lookup grid."""
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

    out = bytearray()
    out += struct.pack('<I', divisor)
    out += struct.pack('<f', span_x / divisor)
    out += struct.pack('<f', span_y / divisor)
    out += struct.pack('<ffffff', min_x, min_y, min_z, max_x, max_y, max_z)

    grid = _build_navmesh_grid(verts, tris, min_x, min_y, max_x, max_y, divisor)
    for cell_tris in grid:
        out += struct.pack('<I', len(cell_tris))
        for ti in cell_tris:
            out += struct.pack('<h', ti)
    return bytes(out)


def _pack_triangles(tris, adj, tri_flags, door_by_tri, link_bit, link_slot):
    """The triangle array: verts, neighbours and flags.

    Every triangle carries the base Found flag, as vanilla does. Where an edge
    carries a link its neighbour field becomes the INDEX into the Edge Links
    array (xEdit wbEdgeToStr), not a triangle.
    """
    out = bytearray()
    out += struct.pack('<I', len(tris))
    for ti, (v0, v1, v2) in enumerate(tris):
        edges3 = list(adj[ti])
        for slot in range(3):
            li = link_slot.get((ti, slot))
            if li is not None:
                edges3[slot] = li
        flags = _TRI_FLAG_FOUND | link_bit.get(ti, 0)
        flags |= (tri_flags[ti] if ti < len(tri_flags) else 0)
        if ti in door_by_tri:
            flags |= _TRI_FLAG_DOOR
        out += struct.pack('<6h2H', v0, v1, v2,
                           edges3[0], edges3[1], edges3[2], flags, 0)
    return bytes(out)


def pack_nvnm(verts, tris, adj, tri_flags,
               wrld_fid, cell_fid, grid_x, grid_y, is_exterior,
               door_tris=None, ledges=None, navm_fid=0) -> bytes:
    """Serialise an NVNM blob.

    door_tris: (triangle_index, door_ref_fid) pairs, flagged on the triangle.
    ledges:    (upper_tri, lower_tri, drop) DROP-DOWNs, emitted as Edge Links
               naming THIS navmesh since both triangles are in it.

    See: docs/commentary/tes5_import_navmesh.md#ledge-links
    """
    door_tris = door_tris or []
    ledges = ledges or []
    door_by_tri = {ti: fid for (ti, fid) in door_tris}

    buf = bytearray()
    buf += struct.pack('<I', _NVNM_VERSION)
    buf += struct.pack('<I', _PATHING_CELL_CRC)
    buf += struct.pack('<I', wrld_fid)
    if is_exterior:
        buf += struct.pack('<hh', grid_y, grid_x)
    else:
        buf += struct.pack('<I', cell_fid)

    buf += struct.pack('<I', len(verts))
    for x, y, z in verts:
        buf += struct.pack('<fff', x, y, z)

    link_bit, link_slot, edge_links = _resolve_ledge_links(
        verts, tris, adj, ledges, navm_fid)

    buf += _pack_triangles(tris, adj, tri_flags, door_by_tri,
                           link_bit, link_slot)

    # Edge Links.  Cross-cell portals are added later by build_edge_links;
    # what we can resolve from the PGRD alone is the DROP-DOWN pair, which is
    # a link within THIS navmesh (vanilla does the same — 0008FFE1 links to
    # itself).
    buf += struct.pack('<I', len(edge_links))
    for (typ, nav, ti) in edge_links:
        buf += struct.pack('<IIh', typ, nav, ti)

    # Door Triangles: sorted by (triangle, door) per xEdit wbStructSK([0,2]).
    sorted_doors = sorted(door_tris, key=lambda d: (d[0], d[1]))
    buf += struct.pack('<I', len(sorted_doors))
    for (ti, fid) in sorted_doors:
        buf += struct.pack('<hI I', ti, _PATHING_DOOR_CRC, fid)

    # Cover Triangles — none.
    buf += struct.pack('<I', 0)

    buf += _pack_bounds_and_grid(verts, tris)
    return bytes(buf)


def pack_navm_record(form_id: int, subrecords: bytes) -> bytes:
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
# Geometry cache
# ---------------------------------------------------------------------------
#
# Building a cell's navmesh geometry (ribbons -> union -> triangulate -> clean)
# costs seconds; packing it into an NVNM costs milliseconds.  The geometry
# depends ONLY on inputs that rarely change between imports — the pathgrid,
# the placed REFRs, the LAND heights, the collision of the meshes the cell
# places, and the generator code itself — so (verts, tris) is cached to disk
# keyed by a hash of exactly those inputs.  Any edit to the navmesh sources,
# params included, changes the tag and self-invalidates every entry; there is
# no version constant to forget to bump.  FormID-dependent work (NVNM parent,
# door links, ONAM, water flags) is recomputed every run, so load-order changes
# cannot be baked in.
#
# Collision enters PER MESH, not as one whole-file hash (see _geom_hash v4).
# The coarse version made every entry share one fate: replacing a single mesh
# invalidated all ~8,200 Oblivion entries.  Per-mesh digests confine the miss to
# the cells that actually place the changed mesh, which is what makes a
# downloaded cache (tools/navmesh_cache.py) still mostly useful to a user who
# has swapped in a few meshes of their own.

def _geom_hash(tag, points, edges, refr_recs, base_model_by_fid, doors,
               land_rec, origin_x, origin_y):
    """Hash of everything the geometry build consumes."""
    from asset_convert.collision_extract import collision_digest
    h = hashlib.sha1()
    # Bump when the CACHED PAYLOAD's shape changes, not just its inputs: the
    # entry now carries ledge links too, and an older entry would silently
    # restore geometry with no drop-downs.
    # v3: analytic door wedges (exact width/centre/apex side) changed the
    # geometry for every cell with a door without changing the inputs.
    # v4: per-mesh collision digests replaced the whole-file collision hash that
    # used to ride in via `tag`.  One replaced mesh previously invalidated EVERY
    # entry (~8,200 for Oblivion) and forced a full regeneration; now only the
    # cells that actually place that mesh miss.  This is also what lets a
    # published cache survive a user's own mesh edits — see
    # collision_extract.collision_digest and tools/navmesh_cache.py.
    h.update(b'geom-v4-permesh-collision')
    h.update(repr((tag, origin_x, origin_y)).encode())
    h.update(repr(points).encode())
    h.update(repr(edges).encode())
    base_model_by_fid = base_model_by_fid or {}
    # Digest each DISTINCT model once: a cell can place hundreds of REFRs that
    # share a handful of models, and collision_digest memoises per key anyway.
    seen_models = {}
    for refr in refr_recs or ():
        name = refr.get('NAME', '')
        try:
            key = base_model_by_fid.get(int(name, 16) & 0xFFFFFF, '')
        except ValueError:
            key = ''
        if key and key not in seen_models:
            seen_models[key] = collision_digest(key)
        h.update(('%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' % (
            name, key,
            refr.get('PosX'), refr.get('PosY'), refr.get('PosZ'),
            refr.get('RotX'), refr.get('RotY'), refr.get('RotZ'),
            refr.get('XSCL.Scale'), bool(refr.get('XTEL.Door')))).encode())
    # Sorted so REFR order cannot perturb the hash (it already contributes above).
    for key in sorted(seen_models):
        h.update(('C|%s|%s\n' % (key, seen_models[key])).encode())
    for (x, y, z, r, _fid, tp, w) in doors or ():
        # Width participates: it sizes the door quad, so a changed panel
        # measurement must invalidate the cached geometry.
        h.update(repr((x, y, z, r, tp, w)).encode())
    if land_rec is not None:
        h.update((get_str(land_rec, 'VHGT') or '').encode())
    return h.hexdigest()


def _geom_cache_load(path, want_hash):
    """(verts, tris) from a cache file, or None on any mismatch/problem."""
    try:
        with open(path, 'rb') as fh:
            stored = pickle.load(fh)
        if stored.get('hash') != want_hash:
            return None
        verts = [tuple(v) for v in stored['verts'].tolist()]
        tris = [tuple(t) for t in stored['tris'].tolist()]
        ledges = [tuple(l) for l in stored.get('ledges', ())]
        return verts, tris, ledges
    except Exception:
        return None


def geom_quantize(verts):
    """Verts as the cache stores them: float32, so fresh and cached compare equal.

    See: docs/commentary/tes5_import_navmesh.md#verifying-a-cache-against-fresh-geometry
    """
    import numpy as np
    if not verts:
        return []
    return [tuple(v) for v in np.asarray(verts, dtype=np.float32).tolist()]


def _orient_quantized_triangles_up(verts, tris):
    """Wind triangles using the float32 coordinates NVNM actually stores.

    Rounding a nearly-collinear plan triangle can change its signed XY area.
    Plan-degenerate neighbours are oriented as a manifold so they traverse a
    shared edge in opposite directions rather than acquiring opposite normals.
    """
    out = []
    plan_area = []
    for tri in tris:
        a, b, c = (int(tri[0]), int(tri[1]), int(tri[2]))
        pa, pb, pc = verts[a], verts[b], verts[c]
        signed = ((pb[0] - pa[0]) * (pc[1] - pa[1])
                  - (pc[0] - pa[0]) * (pb[1] - pa[1]))
        if signed < 0.0:
            out.append((a, c, b))
            signed = -signed
        else:
            out.append((a, b, c))
        plan_area.append(signed)

    owners = {}
    for ti, tri in enumerate(out):
        for k in range(3):
            a, b = tri[k], tri[(k + 1) % 3]
            owners.setdefault((a, b) if a < b else (b, a), []).append(
                (ti, a, b))
    graph = [[] for _ in out]
    for edge_owners in owners.values():
        if len(edge_owners) != 2:
            continue
        (i, ia, ib), (j, ja, jb) = edge_owners
        parity = int(ia == ja and ib == jb)
        graph[i].append((j, parity))
        graph[j].append((i, parity))

    vertical = {i for i, area in enumerate(plan_area) if area <= 1e-6}
    state = {i: 0 for i in range(len(out)) if i not in vertical}
    queue = list(state)
    while queue:
        i = queue.pop()
        for j, parity in graph[i]:
            if j not in vertical or j in state:
                continue
            state[j] = state[i] ^ parity
            queue.append(j)
    for root in sorted(vertical):
        if root in state:
            continue
        state[root] = 0
        queue = [root]
        while queue:
            i = queue.pop()
            for j, parity in graph[i]:
                if j not in vertical or j in state:
                    continue
                state[j] = state[i] ^ parity
                queue.append(j)
    for i in vertical:
        if state.get(i, 0):
            a, b, c = out[i]
            out[i] = (a, c, b)
    return out


def _dedupe_indexed_triangles(tris, ledges=()):
    """Drop repeated faces and remap ledge endpoints to the kept triangle.

    Collision-backed sheets can contribute the same indexed face more than
    once. Because all three undirected edges are then shared by exactly those
    copies, adjacency links every edge of one face to the other. The CK rejects
    those duplicate edge targets. Identity deliberately uses vertex indices so
    independently authored overlapping floors remain distinct.
    """
    kept = []
    old_to_new = []
    by_face = {}
    for tri in tris:
        normalized = tuple(int(v) for v in tri)
        key = tuple(sorted(normalized))
        target = by_face.get(key)
        if target is None:
            target = len(kept)
            by_face[key] = target
            kept.append(normalized)
        old_to_new.append(target)

    if len(kept) == len(tris):
        return kept, [tuple(link) for link in (ledges or ())]

    remapped_ledges = []
    seen_ledges = set()
    for link in ledges or ():
        hi, lo, *tail = link
        hi = old_to_new[int(hi)]
        lo = old_to_new[int(lo)]
        if hi == lo:
            continue
        remapped = (hi, lo, *tail)
        if remapped not in seen_ledges:
            seen_ledges.add(remapped)
            remapped_ledges.append(remapped)
    return kept, remapped_ledges


def _normalize_geometry(verts, tris, ledges=()):
    """Return the canonical geometry used by cold builds and cache hits."""
    verts = geom_quantize(verts)
    tris, ledges = _dedupe_indexed_triangles(tris, ledges)
    tris = _orient_quantized_triangles_up(verts, tris)
    return verts, tris, ledges


def geom_equal(a, b):
    """Do two (verts, tris, ledges) triples describe the same walking surface?"""
    av, at, al = a
    bv, bt, bl = b
    if len(av) != len(bv) or len(at) != len(bt):
        return False
    if geom_quantize(av) != geom_quantize(bv):
        return False
    if [tuple(t) for t in at] != [tuple(t) for t in bt]:
        return False
    return sorted(tuple(x) for x in al) == sorted(tuple(x) for x in bl)


def _pathgrid_nodes(rec):
    """(points, degrees) for a PGRD, or (None, None) when it has no usable graph."""
    point_count = get_int(rec, 'DATA.PointCount', 0)
    intercell = get_int(rec, 'InterCellCount', 0)
    if point_count < 1 or (point_count < 2 and intercell <= 0):
        return None, None
    points, degrees = [], []
    for i in range(point_count):
        if rec.get('Point[%d].X' % i) is None:
            break
        points.append((get_float(rec, 'Point[%d].X' % i),
                       get_float(rec, 'Point[%d].Y' % i),
                       get_float(rec, 'Point[%d].Z' % i)))
        degrees.append(get_int(rec, 'Point[%d].Connections' % i, 0))
    if not points or (len(points) < 2 and intercell <= 0):
        return None, None
    return points, degrees


def _explicit_edges(rec, n, degrees, edges, seen):
    """Edges from the exported PGRR topology, appended in record order."""
    for i in range(n):
        for j in range(degrees[i]):
            tgt = rec.get('Point[%d].Edge[%d]' % (i, j))
            if tgt is None:
                break
            try:
                t = int(tgt)
            except ValueError:
                continue
            if 0 <= t < n and t != i:
                key = (min(i, t), max(i, t))
                if key not in seen:
                    seen.add(key)
                    edges.append(key)


def _nearest_edges(points, n, degrees, edges, seen):
    """Fallback topology: join each node to its `Connections` nearest others."""
    for i in range(n):
        if degrees[i] == 0:
            continue
        others = sorted(((points[i][0] - points[j][0]) ** 2 +
                         (points[i][1] - points[j][1]) ** 2, j)
                        for j in range(n) if j != i)
        for _d, j in others[:degrees[i]]:
            key = (min(i, j), max(i, j))
            if key not in seen:
                seen.add(key)
                edges.append(key)


def _cell_graph(rec, cell_rec):
    """(points, edges, origin_x, origin_y, is_exterior) for one PGRD.

    The pathgrid graph exactly as convert_PGRD derives it, including the
    synthetic PGRI exit nodes.  Both callers MUST share this: the value feeds
    the cache hash, so any divergence silently invalidates every entry.
    """
    points, degrees = _pathgrid_nodes(rec)
    if points is None:
        return None, None, 0.0, 0.0, False
    n = len(points)
    edges, seen = [], set()
    if rec.get('Point[0].Edge[0]') is not None:
        _explicit_edges(rec, n, degrees, edges, seen)
    else:
        _nearest_edges(points, n, degrees, edges, seen)

    is_exterior = get_formid(rec, 'ParentWRLD') != 0
    origin_x = origin_y = 0.0
    if is_exterior:
        gx = get_int(cell_rec, 'XCLC.X', 0) if cell_rec is not None else 0
        gy = get_int(cell_rec, 'XCLC.Y', 0) if cell_rec is not None else 0
        origin_x, origin_y = gx * _CELL_SIZE, gy * _CELL_SIZE
        for (lp, exit_xy) in _collect_intercell(rec, points, origin_x, origin_y):
            new_idx = len(points)
            points.append(exit_xy)
            key = (min(lp, new_idx), max(lp, new_idx))
            if key not in seen:
                seen.add(key)
                edges.append(key)
    return points, edges, origin_x, origin_y, is_exterior


def cell_geom_key(rec, land_rec, cell_rec, refr_recs, base_model_by_fid,
                  door_fids, geom_cache, extra_door_refrs=None):
    """This cell's cache hash, derived from its INPUTS with no geometry build.

    convert_PGRD computes the same value before deciding whether to build, so
    re-keying a cache needs only this -- not a mesh.  Building one to learn a
    hash costs ~3.8s per cell (8.7 hours for Oblivion) to recompute something
    that depends on no geometry at all.

    Returns None when the cell has no navmesh job or no cache is configured.

    See: docs/commentary/tes5_import_navmesh.md#verifying-a-cache-against-fresh-geometry
    """
    if geom_cache is None:
        return None
    points, edges, origin_x, origin_y, is_exterior = _cell_graph(rec, cell_rec)
    if points is None:
        return None
    doors = []
    if refr_recs:
        doors = _collect_doors(refr_recs, door_fids)
    if extra_door_refrs:
        doors += _collect_doors(extra_door_refrs, door_fids)
    return _geom_hash(geom_cache[1], points, edges, refr_recs,
                      base_model_by_fid, doors,
                      land_rec if is_exterior else None, origin_x, origin_y)


def cached_geometry(geom_cache, cell_fid, pgrd_fid):
    """Canonical cached geometry for one cell, ignoring its hash, or None.

    Reads the payload WITHOUT checking the tag-bearing hash, which is what makes
    an adopt/verify pass able to compare geometry across a tag change. Legacy
    payloads are normalized exactly as a normal cache hit is before comparison.
    """
    if not geom_cache:
        return None
    path = os.path.join(geom_cache[0], '%08X_%08X.pkl' % (cell_fid, pgrd_fid))
    try:
        with open(path, 'rb') as fh:
            stored = pickle.load(fh)
        return _normalize_geometry(
            [tuple(v) for v in stored['verts'].tolist()],
            [tuple(t) for t in stored['tris'].tolist()],
            [tuple(l) for l in stored.get('ledges', ())])
    except Exception:
        return None


def _geom_cache_store(path, geom_hash, verts, tris, ledges=()):
    import numpy as np
    try:
        tmp = path + '.tmp%d' % os.getpid()
        with open(tmp, 'wb') as fh:
            pickle.dump({'hash': geom_hash,
                         'verts': np.asarray(verts, dtype=np.float32),
                         'tris': np.asarray(tris, dtype=np.int32),
                         'ledges': [tuple(l) for l in (ledges or ())]},
                        fh, pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception:
        pass                      # a failed cache write must never fail the cell


# ---------------------------------------------------------------------------
# Public converter
# ---------------------------------------------------------------------------

def convert_PGRD(rec: dict, writer=None,
                 land_rec: dict = None,
                 cell_rec: dict = None,
                 refr_recs: list = None,
                 base_model_by_fid: dict = None,
                 door_fids: set = None,
                 navm_fid: int = None,
                 geom_cache: tuple = None,
                 extra_door_refrs: list = None) -> tuple:
    """Convert one TES4 PGRD to a TES5 NAVM record.

    Args:
        rec:                Parsed PGRD record dict.
        writer:             PluginWriter (supplies derive_formid()).
        land_rec:           LAND record for the cell (VHGT height field).
        cell_rec:           CELL record (water height, grid coords).
        refr_recs:          REFR records in this cell (exclusion footprints).
        base_model_by_fid:  {raw_low_base_fid: 'tes4/...nif'} for footprints.
        door_fids:          set of raw low-24 DOOR base FormIDs (for door links).
        navm_fid:           Pre-allocated NAVM FormID; skips writer allocation.
        geom_cache:         (cache_dir, tag) enabling the on-disk geometry cache.
        extra_door_refrs:   Door REFRs standing here but parented elsewhere.

    Returns:
        (navm_bytes, meta) where meta is a dict
        {fid, wrld_fid, cell_fid, grid_x, grid_y, is_exterior, center,
         base_objects, geom_cached, geometry} — or (None, None) on failure.

    See: docs/commentary/tes5_import_navmesh.md#convert-pgrd-arguments
    """
    if writer is None and navm_fid is None:
        return None, None

    point_count = get_int(rec, 'DATA.PointCount', 0)
    if point_count < 1:
        return None, None
    if point_count < 2 and get_int(rec, 'InterCellCount', 0) <= 0:
        # A single node with no in-cell edge AND no PGRI link can never make
        # a ribbon.  A single node WITH PGRI links can: the synthetic exit
        # edges (below) lay cross-seam stubs — 53 exterior cells shipped with
        # no navmesh at all under the old `< 2` gate, leaving holes in the
        # cross-cell network exactly where a road crosses a cell corner.
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
    if not points:
        return None, None
    if len(points) < 2 and get_int(rec, 'InterCellCount', 0) <= 0:
        return None, None       # same PGRI exception as the gate above

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

    # ---- Cross-cell (InterCell / PGRI) edges ----
    # Exterior cell meshes are otherwise islands: the corridor ribbons stop at
    # the last node INSIDE the cell, never reaching the seam, so the edge-link
    # pass finds no border edges to stitch.  Each PGRI entry gives a local node
    # and an exit point in the neighbour cell; adding a synthetic node at the
    # exit and an edge to it lays a ribbon that crosses the boundary plane.  The
    # ribbon is CLIPPED back to this cell in corridor_union, leaving a border
    # edge exactly on the seam that build_edge_links can match.
    if is_exterior:
        for (lp, exit_xy) in _collect_intercell(rec, points, origin_x, origin_y):
            new_idx = len(points)
            points.append(exit_xy)
            key = (min(lp, new_idx), max(lp, new_idx))
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(key)
        n = len(points)

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
    if extra_door_refrs:
        doors += _collect_doors(extra_door_refrs, door_fids)

    geom_cached = False
    cache_path = geom_hash = None
    verts3d = tris = None
    if geom_cache is not None:
        cache_dir, tag = geom_cache
        geom_hash = _geom_hash(tag, points, edges, refr_recs,
                               base_model_by_fid, doors,
                               land_rec if is_exterior else None,
                               origin_x, origin_y)
        cache_path = os.path.join(
            cache_dir, '%08X_%08X.pkl' % (cell_fid, get_formid(rec, 'FormID')))
        cached = _geom_cache_load(cache_path, geom_hash)
        if cached is not None:
            verts3d, tris, ledges = cached
            geom_cached = True

    if verts3d is None:
        from .navmesh import build as navmesh_build
        from asset_convert.collision_extract import get_collision

        ledges = []
        verts3d, tris = navmesh_build.build_navmesh(
            refr_recs or [], base_model_by_fid or {}, get_collision,
            points, edges,
            land_rec=land_rec if is_exterior else None,
            origin_x=origin_x, origin_y=origin_y,
            doors=[(x, y, z, r, tp, w)
                   for (x, y, z, r, _f, tp, w) in doors],
            ledges_out=ledges,
            door_bases=(set(door_fids.keys())
                        if isinstance(door_fids, dict)
                        else set(door_fids or ())))
        if verts3d:
            verts3d, tris, ledges = _normalize_geometry(
                verts3d, tris, ledges)
        if cache_path is not None:
            _geom_cache_store(cache_path, geom_hash, verts3d, tris, ledges)
    elif verts3d:
        # A matching cache can still predate canonical winding/face cleanup.
        verts3d, tris, ledges = _normalize_geometry(verts3d, tris, ledges)
    if len(verts3d) < 3 or not tris:
        return None, None

    tri_flags = (_compute_water_flags(verts3d, tris, water_z)
                 if water_z is not None else [0] * len(tris))
    adj = _compute_adjacency(tris, verts3d)

    # ---- Door triangles: link the navmesh tri at each door threshold ----
    door_tris = _build_door_links(verts3d, tris, doors)

    # The ledge links name THIS navmesh, so its FormID must exist first.
    if navm_fid is None:
        # Same key as the bulk pre-pass (_precompute_navmeshes) so the serial
        # fallback and the parallel path agree on a cell's navmesh id.
        navm_fid = writer.derive_formid(
            'NAVM', (cell_fid, get_formid(rec, 'FormID')))

    nvnm = pack_nvnm(verts3d, tris, adj, tri_flags,
                      wrld_fid, cell_fid, grid_x, grid_y, is_exterior,
                      door_tris=door_tris, ledges=ledges, navm_fid=navm_fid)
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', f'TES4Navm{edid}')
    subs += pack_subrecord('NVNM', nvnm)
    if base_objects:
        onam = b''.join(struct.pack('<I', b) for b in base_objects)
        subs += pack_subrecord('ONAM', onam)

    navm_bytes = pack_navm_record(navm_fid, subs)

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
        'geom_cached': geom_cached,
        'geometry': (verts3d, tris, ledges),
        'geom_hash': geom_hash,
        # NVMI Door Links mirror the mesh's own door triangles EXACTLY —
        # verified on all 15,462 vanilla NVMI entries (the engine joins the two
        # sides of a load door through the door refs' XTEL pairing, so each
        # mesh lists only its own doors).
        'door_refs': sorted({fid for (_t, fid) in door_tris}),
        # The REFR side of the same link: door ref FormID -> (NAVM, triangle),
        # which convert_REFR emits as XNDP.  NVNM/NVMI alone are not enough —
        # they tell the navmesh which triangle is a door, but the engine builds
        # its PathingDoor from the DOOR REFERENCE, and that lookup goes through
        # XNDP.  1,705 of 1,722 vanilla teleport-door REFRs carry it.
        'door_xndp': {fid: (navm_fid, ti) for (ti, fid) in door_tris},
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
