import io as _io
import math
import os
import subprocess
import time
from pathlib import Path

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401

from pyffi.formats.nif import NifFormat

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ASSET_DIR  = Path(__file__).parent
_MOPP_RL    = str(_ASSET_DIR / 'MOPP_RL.exe')
_MOPP_RL_CWD = str(_ASSET_DIR)
_HAVOK_SCALE = 0.1
_GAME_UNITS_PER_HAVOK = 69.9904  # Skyrim: 1 Havok unit = 69.9904 game units
NIF_FLAGS = 14  # Standard Skyrim NiAVObject flags (SelectiveUpdate bits 1-3)

# ---------------------------------------------------------------------------
# Havok material conversion
# ---------------------------------------------------------------------------
# Oblivion stores materials as a small sequential enum (OblivionHavokMaterial,
# 0-31).  Skyrim stores them as CRC32 hashes of the Creation Kit material name
# (SkyrimHavokMaterial).  Passing the Oblivion int through unmapped leaves the
# engine with an unknown material (no impact sounds/decals, no stair-walk flag).
_OB_TO_SK_MATERIAL = {
    0:  3741512247,  # Stone            → SKY_HAV_MAT_STONE
    1:  3839073443,  # Cloth            → SKY_HAV_MAT_CLOTH
    2:  3106094762,  # Dirt             → SKY_HAV_MAT_DIRT
    3:  3739830338,  # Glass            → SKY_HAV_MAT_GLASS
    4:  1848600814,  # Grass            → SKY_HAV_MAT_GRASS
    5:  1288358971,  # Metal            → SKY_HAV_MAT_SOLID_METAL
    6:  2974920155,  # Organic          → SKY_HAV_MAT_ORGANIC
    7:  591247106,   # Skin             → SKY_HAV_MAT_SKIN
    8:  1024582599,  # Water            → SKY_HAV_MAT_WATER
    9:  500811281,   # Wood             → SKY_HAV_MAT_WOOD
    10: 1570821952,  # Heavy Stone      → SKY_HAV_MAT_HEAVY_STONE
    11: 2229413539,  # Heavy Metal      → SKY_HAV_MAT_HEAVY_METAL
    12: 3070783559,  # Heavy Wood       → SKY_HAV_MAT_HEAVY_WOOD
    13: 3074114406,  # Chain            → SKY_HAV_MAT_MATERIAL_CHAIN
    14: 398949039,   # Snow             → SKY_HAV_MAT_SNOW
    15: 899511101,   # Stone Stairs     → SKY_HAV_MAT_STAIRS_STONE
    16: 1461712277,  # Cloth Stairs     → SKY_HAV_MAT_STAIRS_WOOD (carpeted)
    17: 899511101,   # Dirt Stairs      → SKY_HAV_MAT_STAIRS_STONE
    18: 880200008,   # Glass Stairs     → SKY_HAV_MAT_STAIRS_GLASS
    19: 899511101,   # Grass Stairs     → SKY_HAV_MAT_STAIRS_STONE
    20: 899511101,   # Metal Stairs     → SKY_HAV_MAT_STAIRS_STONE (no metal stairs)
    21: 1461712277,  # Organic Stairs   → SKY_HAV_MAT_STAIRS_WOOD
    22: 1461712277,  # Skin Stairs      → SKY_HAV_MAT_STAIRS_WOOD
    23: 899511101,   # Water Stairs     → SKY_HAV_MAT_STAIRS_STONE
    24: 1461712277,  # Wood Stairs      → SKY_HAV_MAT_STAIRS_WOOD
    25: 899511101,   # Heavy Stone Strs → SKY_HAV_MAT_STAIRS_STONE
    26: 899511101,   # Heavy Metal Strs → SKY_HAV_MAT_STAIRS_STONE
    27: 1461712277,  # Heavy Wood Strs  → SKY_HAV_MAT_STAIRS_WOOD
    28: 899511101,   # Chain Stairs     → SKY_HAV_MAT_STAIRS_STONE
    29: 1560365355,  # Snow Stairs      → SKY_HAV_MAT_STAIRS_SNOW
    30: 1288358971,  # Elevator         → SKY_HAV_MAT_SOLID_METAL
    31: 2974920155,  # Rubber           → SKY_HAV_MAT_ORGANIC
}


def _set_havok_material(hm, value):
    """Set every material item inside a HavokMaterial struct to *value*.

    PyFFI instantiates one enum item per read context (typed as whichever
    variant matched the source version); CRC values are outside the old
    Oblivion enum's range so bypass enum validation when needed.
    """
    for it in getattr(hm, '_items', []):
        if it.__class__.__name__.endswith('HavokMaterial'):
            # NOT set_value(): PyFFI's EnumBase.set_value only logs a warning
            # and returns when the value isn't in its (old, Oblivion-era) enum
            # list.  Skyrim CRC values must be written raw.
            it._value = int(value)


def _get_havok_material(hm):
    """Return the raw material int stored in a HavokMaterial struct."""
    for it in getattr(hm, '_items', []):
        if it.__class__.__name__.endswith('HavokMaterial'):
            return int(it.get_value())
    return 0


def _convert_materials(shape, _seen=None):
    """Recursively map Oblivion havok material enums to Skyrim CRC values.

    Values ≤ 31 are Oblivion enum indices; anything larger is already a
    Skyrim CRC (idempotent — safe to call on partially converted trees).
    """
    if shape is None:
        return
    if _seen is None:
        _seen = set()
    if id(shape) in _seen:
        return
    _seen.add(id(shape))

    hm = getattr(shape, 'material', None)
    if hm is not None and hasattr(hm, '_items'):
        cur = _get_havok_material(hm)
        if 0 <= cur <= 31:
            _set_havok_material(hm, _OB_TO_SK_MATERIAL.get(cur, 3741512247))

    # Recurse into child shapes / sub-shape material carriers
    for attr in ('shape',):
        _convert_materials(getattr(shape, attr, None), _seen)
    for list_attr in ('sub_shapes',):
        subs = getattr(shape, list_attr, None)
        if subs is not None:
            for s in subs:
                _convert_materials(s, _seen)
    data = getattr(shape, 'data', None)
    if data is not None:
        subs = getattr(data, 'sub_shapes', None)
        if subs is not None:
            for s in subs:
                _convert_materials(s, _seen)

# ---------------------------------------------------------------------------
# Triangle extraction from NiTriStripsData
# ---------------------------------------------------------------------------

def _triangulate_strips(strips_data):
    """Convert NiTriStripsData strip indices to a list of (a, b, c) triangles."""
    triangles = []
    for strip in strips_data.points:
        pts = list(strip)
        flip = False
        for i in range(2, len(pts)):
            a, b, c = pts[i-2], pts[i-1], pts[i]
            if a != b and b != c and c != a:
                if not flip:
                    triangles.append((a, b, c))
                else:
                    triangles.append((a, c, b))
            flip = not flip
    return triangles


def _find_normal(verts, a, b, c):
    """Return normalised face normal for triangle (a,b,c) in a vertex list."""
    va, vb, vc = verts[a], verts[b], verts[c]
    ux, uy, uz = vb[0]-va[0], vb[1]-va[1], vb[2]-va[2]
    vx, vy, vz = vc[0]-va[0], vc[1]-va[1], vc[2]-va[2]
    nx = uy*vz - uz*vy
    ny = uz*vx - ux*vz
    nz = ux*vy - uy*vx
    mag = math.sqrt(nx*nx + ny*ny + nz*nz)
    if mag > 0:
        nx /= mag; ny /= mag; nz /= mag
    return nx, ny, nz


# ---------------------------------------------------------------------------
# Shape conversion
# ---------------------------------------------------------------------------

def _remove_stair_risers(verts, triangles):
    """Remove stair-riser triangles from a Havok collision mesh.

    A stair riser is a near-vertical face (|nz| < 0.3) WHERE ALL THREE
    vertices are also shared with at least one upward-facing (floor-like)
    triangle (nz > 0.5).  This correctly distinguishes risers from walls:

    - Riser: connects two tread levels; every vertex borders a floor face
      on one side or the other → ALL vertices are floor-adjacent → REMOVED.
    - Wall: has mid-wall and top vertices that only appear in vertical/ceiling
      faces → at least one vertex is NOT floor-adjacent → KEPT.

    As a safety valve, if the filter would remove more than 50 % of the
    mesh, the original triangle list is returned unchanged (the mesh is not
    a recognisable stair shape).
    """
    if not triangles:
        return triangles

    normals = [_find_normal(verts, a, b, c) for (a, b, c) in triangles]

    # Mark every vertex that borders at least one upward-facing floor triangle.
    floor_verts: set = set()
    for i, (a, b, c) in enumerate(triangles):
        if normals[i][2] > 0.5:
            floor_verts.add(a)
            floor_verts.add(b)
            floor_verts.add(c)

    filtered = []
    removed = 0
    for i, (a, b, c) in enumerate(triangles):
        nz = normals[i][2]
        if (abs(nz) < 0.3
                and a in floor_verts
                and b in floor_verts
                and c in floor_verts):
            removed += 1
            continue
        filtered.append((a, b, c))

    # Safety: if we are removing too large a fraction the mesh is probably
    # not stair-shaped (e.g. a thin slanted wall).  Keep original.
    if removed > len(triangles) * 0.5:
        return triangles

    return filtered


def _build_packed_nif(packed_shape):
    """Wrap a bhkPackedNiTriStripsShape in a minimal BSFadeNode NIF for MOPP_RL."""
    data = NifFormat.Data(version=0x14020007, user_version=12,
                          user_version_2=83)
    fade = NifFormat.BSFadeNode()
    fade.flags = NIF_FLAGS
    mopp_wrap = NifFormat.bhkMoppBvTreeShape()
    mopp_wrap.shape = packed_shape
    rb = NifFormat.bhkRigidBody()
    rb.shape = mopp_wrap
    coll_obj = NifFormat.bhkCollisionObject()
    coll_obj.flags = 129
    coll_obj.target = fade
    coll_obj.body = rb
    fade.collision_object = coll_obj
    data.roots.append(fade)
    data.header.endian_type = NifFormat.EndianType.ENDIANLITTLE
    return data


def _ni_strips_to_packed(bhk_strips):
    """Convert bhkNiTriStripsShape → bhkPackedNiTriStripsShape.

    Combines ALL NiTriStripsData blocks (Oblivion often has multiple per shape)
    and scales vertices by 1/7 (Oblivion stores them at 7× Havok unit scale).
    Returns a bhkPackedNiTriStripsShape, or None on failure.
    """
    try:
        strips_list = list(bhk_strips.strips_data)
        if not strips_list:
            return None

        # Combine vertices and triangles from ALL NiTriStripsData blocks.
        # Oblivion bhkNiTriStripsShape can have multiple data blocks (each is a
        # separate collision piece), but bhkPackedNiTriStripsShape stores them
        # merged with a single sub-shape covering all vertices.
        all_verts = []
        all_triangles = []
        for sd in strips_list:
            offset = len(all_verts)
            block_verts = [(v.x / 7.0, v.y / 7.0, v.z / 7.0) for v in sd.vertices]
            all_verts.extend(block_verts)
            block_tris = _triangulate_strips(sd)
            all_triangles.extend(
                (a + offset, b + offset, c + offset) for a, b, c in block_tris
            )

        if not all_triangles:
            return None

        # NOTE: stair-riser removal disabled pending further testing.
        # all_triangles = _remove_stair_risers(all_verts, all_triangles)
        # if not all_triangles:
        #     return None

        hkdata = NifFormat.hkPackedNiTriStripsData()
        hkdata.num_vertices = len(all_verts)
        hkdata.vertices.update_size()
        for i, (x, y, z) in enumerate(all_verts):
            hkdata.vertices[i].x = x
            hkdata.vertices[i].y = y
            hkdata.vertices[i].z = z

        hkdata.num_triangles = len(all_triangles)
        hkdata.triangles.update_size()
        for i, (a, b, c) in enumerate(all_triangles):
            hkdata.triangles[i].triangle.v_1 = a
            hkdata.triangles[i].triangle.v_2 = b
            hkdata.triangles[i].triangle.v_3 = c
            hkdata.triangles[i].welding_info = 0
            nx, ny, nz = _find_normal(all_verts, a, b, c)
            hkdata.triangles[i].normal.x = nx
            hkdata.triangles[i].normal.y = ny
            hkdata.triangles[i].normal.z = nz

        packed = NifFormat.bhkPackedNiTriStripsShape()
        packed.num_sub_shapes = 1
        packed.sub_shapes.update_size()
        packed.sub_shapes[0].layer = 1       # LAYER_STATIC
        packed.sub_shapes[0].num_vertices = len(all_verts)
        # material is an enum, copy from source
        packed.sub_shapes[0].material = bhk_strips.material
        packed.scale.x = 1.0
        packed.scale.y = 1.0
        packed.scale.z = 1.0
        packed.unknown_float_1 = 0.1
        packed.unknown_float_3 = 0.1
        packed.data = hkdata
        return packed
    except Exception:
        return None



# ---------------------------------------------------------------------------
# Rigid body conversion
# ---------------------------------------------------------------------------

def _run_mopp_rl(input_nif_data):
    """Run MOPP_RL.exe on a NifFormat.Data object.

    Writes input to a temp file, runs MOPP_RL.exe, reads and returns the
    resulting NifFormat.Data, or None on failure.
    """
    if not os.path.exists(_MOPP_RL):
        return None
    import uuid
    uid = uuid.uuid4().hex
    temp_dir = os.path.join(_ASSET_DIR.parent, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    tmp_in  = os.path.join(temp_dir, f'mopp_{uid}.nif')
    tmp_out = os.path.join(temp_dir, f'mopp_{uid}_out.nif')
    try:
        buf = _io.BytesIO()
        input_nif_data.write(buf)
        with open(tmp_in, 'wb') as f:
            f.write(buf.getvalue())

        result = subprocess.run(
            [_MOPP_RL, tmp_in, tmp_out],
            capture_output=True,
            cwd=_MOPP_RL_CWD,  # asset_convert/ — where template.nif lives
        )
        if result.returncode != 0 or not os.path.exists(tmp_out):
            return None

        out_data = NifFormat.Data()
        with open(tmp_out, 'rb') as f:
            out_data.inspect(f)
            out_data.read(f)
        return out_data
    except Exception:
        return None
    finally:
        for p in (tmp_in, tmp_out):
            try:
                os.unlink(p)
            except OSError:
                pass


def _convert_rigid_body(rb):
    """Set Skyrim-compatible rigid body flags (in-place).

    Field mapping (PyFFI ↔ newer nif.xml):
      unknown_int_1  → bhkWorldObjCInfo.Unused01     (4 bytes binary padding)
      unknown_int_2  → BroadPhaseType(1B) + Unused02 (3B padding)
      unknown_3_ints → bhkWorldObjCInfoProperty       (Data,Size,CapacityFlags)
      unknown_byte   → bhkEntityCInfo.Unused01         (padding byte)
      unknown_2_shorts → bhkRBCInfo2010.Unused01       (padding)
      havok_col_filter_copy → bhkRBCInfo2010.HavokFilter (copy of entity filter)
      unknown_6_shorts[0:2] → bhkRBCInfo2010.Unused02  (padding)
      unknown_6_shorts[2:4] → bhkRBCInfo2010.UnknownInt1 (MUST be 0!)
      unknown_6_shorts[4:6] → bhkRBCInfo2010.CollisionResponse+ProcessContactDelay
    """
    # Zero padding fields that carried Oblivion-specific data.
    # unknown_int_1 = WorldObjCInfo.Unused01 — padding, zero is safest
    rb.unknown_int_1 = 0
    # unknown_int_2 byte 0 = BroadPhaseType, bytes 1-3 = Unused02 padding.
    # BroadPhaseType 1 = BROAD_PHASE_ENTITY (standard for all shapes).
    rb.unknown_int_2 = 1  # BroadPhaseType=1, padding=0
    # unknown_3_ints = WorldObjCInfoProperty (Data=0, Size=0, CapacityFlags=0x80000000)
    rb.unknown_3_ints[0] = 0
    rb.unknown_3_ints[1] = 0
    rb.unknown_3_ints[2] = -2147483648  # 0x80000000

    # unknown_byte: bhkEntityCInfo.Unused01 — the external NIFConverter
    # sets this to 116, vanilla Skyrim NIFs also show 116.
    rb.unknown_byte = 116
    # Gravity/time factors must be 1.0 or Havok ignores the body
    rb.unknown_time_factor_or_gravity_factor_1 = 1.0
    rb.unknown_time_factor_or_gravity_factor_2 = 1.0
    rb.unknown_int_6 = 196608
    rb.unknown_int_7 = 0
    rb.unknown_int_8 = 0
    rb.unknown_int_81 = 0
    rb.unknown_int_91 = 0
    # unknown_2_shorts: static Skyrim values (per external NIFConverter)
    rb.unknown_2_shorts[0] = 29541
    rb.unknown_2_shorts[1] = 23659
    # unknown_6_shorts: Skyrim-specific values required for correct physics.
    # Elements [2] and [3] map to bhkRBCInfo2010.UnknownInt1 — a 32-bit field
    # that Oblivion values corrupt into invalid pointer 0xFFFF1301.
    # Must be zero.  Elements [0:2] and [4:6] are padding/duplicates.
    rb.unknown_6_shorts[0] = 20704
    rb.unknown_6_shorts[1] = 9444
    rb.unknown_6_shorts[2] = 0       # MUST be 0 — Skyrim interprets as pointer
    rb.unknown_6_shorts[3] = 0       # MUST be 0 — Skyrim interprets as pointer
    rb.unknown_6_shorts[4] = 60417
    rb.unknown_6_shorts[5] = 65535


# ---------------------------------------------------------------------------
# Recursive shape conversion
# ---------------------------------------------------------------------------

def _extract_mopp_result(out_data, root_node):
    """Extract the collision shape from MOPP_RL output and set its target.

    MOPP_RL.exe leaves build_type=0xCD (uninit memory); set it to 1
    (BUILT_WITHOUT_CHUNK_SUBDIVISION) which is what vanilla Skyrim uses.
    """
    result = out_data.roots[0].collision_object.body.shape
    if hasattr(result, 'build_type'):
        result.build_type = 1
    # bhkCompressedMeshShape (inner of bhkMoppBvTreeShape) needs its target set
    inner = getattr(result, 'shape', None)
    if inner is not None and hasattr(inner, 'target'):
        inner.target = root_node
    return result

def _convert_shape(shape, root_node):
    """Recursively convert an Oblivion Havok shape to Skyrim format.

    Scales all geometry/dimensions by _HAVOK_SCALE (0.1).
    bhkNiTriStripsShape is converted to bhkPackedNiTriStripsShape and
    re-MOP'd via MOPP_RL.exe.
    Returns the (possibly replaced) shape.
    """
    if shape is None:
        return None

    if isinstance(shape, NifFormat.bhkBoxShape):
        shape.dimensions.x *= _HAVOK_SCALE
        shape.dimensions.y *= _HAVOK_SCALE
        shape.dimensions.z *= _HAVOK_SCALE
        shape.radius *= _HAVOK_SCALE
        shape.minimum_size = min(shape.dimensions.x, shape.dimensions.y, shape.dimensions.z)
        return shape

    if isinstance(shape, NifFormat.bhkSphereShape):
        shape.radius *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkCapsuleShape):
        shape.radius   *= _HAVOK_SCALE
        shape.radius_1 *= _HAVOK_SCALE
        shape.radius_2 *= _HAVOK_SCALE
        shape.first_point.x  *= _HAVOK_SCALE
        shape.first_point.y  *= _HAVOK_SCALE
        shape.first_point.z  *= _HAVOK_SCALE
        shape.second_point.x *= _HAVOK_SCALE
        shape.second_point.y *= _HAVOK_SCALE
        shape.second_point.z *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkMultiSphereShape):
        for sphere in shape.spheres:
            sphere.center.x *= _HAVOK_SCALE
            sphere.center.y *= _HAVOK_SCALE
            sphere.center.z *= _HAVOK_SCALE
            sphere.radius   *= _HAVOK_SCALE
        return shape

    if isinstance(shape, (NifFormat.bhkConvexTransformShape,
                           NifFormat.bhkTransformShape)):
        shape.transform.m_14 *= _HAVOK_SCALE
        shape.transform.m_24 *= _HAVOK_SCALE
        shape.transform.m_34 *= _HAVOK_SCALE
        shape.shape = _convert_shape(shape.shape, root_node)
        return shape

    if isinstance(shape, NifFormat.bhkConvexVerticesShape):
        for i in range(len(shape.vertices)):
            shape.vertices[i].x *= _HAVOK_SCALE
            shape.vertices[i].y *= _HAVOK_SCALE
            shape.vertices[i].z *= _HAVOK_SCALE
        for i in range(len(shape.normals)):
            shape.normals[i].w *= _HAVOK_SCALE
        shape.radius *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkListShape):
        for i in range(len(shape.sub_shapes)):
            shape.sub_shapes[i] = _convert_shape(shape.sub_shapes[i], root_node)
        return shape

    if isinstance(shape, NifFormat.bhkNiTriStripsShape):
        # Convert strips → packed, then regenerate MOPP
        packed = _ni_strips_to_packed(shape)
        if packed is None:
            return shape  # keep original on failure
        # Try to regenerate MOPP data via MOPP_RL
        tmp_nif = _build_packed_nif(packed)
        out = _run_mopp_rl(tmp_nif)
        if out is not None:
            return _extract_mopp_result(out, root_node)
        # MOPP_RL failed — return packed shape as-is (no MOPP but valid)
        return packed

    if isinstance(shape, NifFormat.bhkMoppBvTreeShape):
        # Convert inner shape, which may already produce a MOPP via MOPP_RL.
        # If conversion gives back a bhkMoppBvTreeShape, use it directly.
        # If it gives back a bhkPackedNiTriStripsShape, regenerate MOPP for it.
        converted = _convert_shape(shape.shape, root_node)
        if isinstance(converted, NifFormat.bhkMoppBvTreeShape):
            return converted
        if isinstance(converted, NifFormat.bhkPackedNiTriStripsShape):
            tmp_nif = _build_packed_nif(converted)
            out = _run_mopp_rl(tmp_nif)
            if out is not None:
                return _extract_mopp_result(out, root_node)
            # MOPP_RL failed — return packed shape directly.
            # Never return the outer bhkMoppBvTreeShape with stale Oblivion MOPP
            # data: Skyrim can't load Oblivion MOPP and will silently drop the
            # collision, while the incompatible blob causes undefined behaviour.
            return converted
        return shape

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        # Already packed — regenerate MOPP
        tmp_nif = _build_packed_nif(shape)
        out = _run_mopp_rl(tmp_nif)
        if out is not None:
            return _extract_mopp_result(out, root_node)
        return shape

    # Unknown shape — return as-is
    return shape


# ---------------------------------------------------------------------------
# Concave clutter hull decomposition
# ---------------------------------------------------------------------------
# Oblivion clutter ships ONE convex hull per object.  A convex hull fills
# every concavity: a goblet's hull spans rim→base (the thin stem gets a fat
# cylinder of phantom collision), a pitcher's hull fills the handle gap.
# Skyrim's crosshair/activation raycast tests the Havok shape, so the pick
# region extends 2-5× beyond the visible mesh around such features.  Vanilla
# Skyrim authors compound shapes instead (glazedgoblet01 = bhkListShape of a
# cup box + stem box).  We reproduce that: recursively split the VISUAL
# vertices along the axis-aligned cut that minimises total hull volume, and
# emit a bhkListShape of per-piece convex hulls when this removes enough
# phantom volume.
# NOTE: Testing this ingame seemd to make no difference

_DECOMP_MAX_DEPTH = 3          # binary split tree → ≤ 8 pieces
_DECOMP_SPLIT_GAIN = 0.90      # accept a cut only if it removes ≥10% volume
_DECOMP_MIN_PIECE_VERTS = 8
_DECOMP_MAX_HULL_VERTS = 64


def _hull_volume(pts):
    from scipy.spatial import ConvexHull
    try:
        return ConvexHull(pts).volume
    except Exception:
        return None


def _recursive_hull_split(pts, depth):
    """Split point cloud into pieces whose hulls waste less volume.

    Returns a list of point arrays (≥1 entries).  Points near the cut plane
    are shared by both halves so piece hulls overlap slightly (no gaps).
    """
    import numpy as np
    vol = _hull_volume(pts)
    if vol is None or vol <= 0 or depth <= 0:
        return [pts]

    best = None
    for axis in range(3):
        lo, hi = pts[:, axis].min(), pts[:, axis].max()
        extent = hi - lo
        if extent * _GAME_UNITS_PER_HAVOK < 3.0:  # too thin to split
            continue
        eps = 0.02 * extent
        for frac in (0.3, 0.4, 0.5, 0.6, 0.7):
            cut = lo + frac * extent
            coords = pts[:, axis]
            # Each half must reach past the first vertex "ring" on the far
            # side of the cut, otherwise sparse vertex rows leave an unfilled
            # band of collision between the two piece hulls.
            above = coords[coords > cut]
            below = coords[coords < cut]
            reach_a = (above.min() if len(above) else cut) + eps
            reach_b = (below.max() if len(below) else cut) - eps
            a = pts[coords <= reach_a]
            b = pts[coords >= reach_b]
            if len(a) < _DECOMP_MIN_PIECE_VERTS or len(b) < _DECOMP_MIN_PIECE_VERTS:
                continue
            va = _hull_volume(a)
            vb = _hull_volume(b)
            if va is None or vb is None:
                continue
            if best is None or va + vb < best[0]:
                best = (va + vb, a, b)

    if best is None or best[0] > vol * _DECOMP_SPLIT_GAIN:
        return [pts]
    return (_recursive_hull_split(best[1], depth - 1)
            + _recursive_hull_split(best[2], depth - 1))


def _build_piece_convex_shape(pts, radius, sk_material):
    """Build a bhkConvexVerticesShape from a piece point cloud (Havok units)."""
    import numpy as np
    from scipy.spatial import ConvexHull

    hull = None
    hull_pts = None
    # Quantise to a grid to keep hull vertex counts in the vanilla range
    # (grid steps in Havok units: 0.28 / 0.56 / 1.05 game units).
    for grid in (0.004, 0.008, 0.015):
        q = np.unique(np.round(pts / grid) * grid, axis=0)
        if len(q) < 4:
            continue
        try:
            h = ConvexHull(q)
        except Exception:
            continue
        hull, hull_pts = h, q[h.vertices]
        if len(hull_pts) <= _DECOMP_MAX_HULL_VERTS:
            break
    if hull is None or len(hull_pts) < 4:
        return None

    # scipy facet equations are triangulated → dedupe coplanar planes.
    # Equation convention: n·x + d <= 0 inside (n outward unit normal).
    eqs = np.unique(np.round(hull.equations, 5), axis=0)

    shape = NifFormat.bhkConvexVerticesShape()
    _set_havok_material(shape.material, sk_material)
    shape.radius = radius
    shape.num_vertices = len(hull_pts)
    shape.vertices.update_size()
    for i, (x, y, z) in enumerate(hull_pts):
        shape.vertices[i].x = float(x)
        shape.vertices[i].y = float(y)
        shape.vertices[i].z = float(z)
        shape.vertices[i].w = 0.0
    shape.num_normals = len(eqs)
    shape.normals.update_size()
    for i, eq in enumerate(eqs):
        shape.normals[i].x = float(eq[0])
        shape.normals[i].y = float(eq[1])
        shape.normals[i].z = float(eq[2])
        # Face plane sits at n·x = -w.  Vanilla stores planes pushed out by
        # the convex radius (face dist = vertex dist + radius).
        shape.normals[i].w = float(eq[3]) - radius
    return shape


def _collect_visual_vertices(node):
    """Gather all visual mesh vertices under *node* in node-frame game units."""
    import numpy as np
    out = []

    def walk(n, M):
        if n is None:
            return
        L = np.eye(4)
        if hasattr(n, 'translation') and hasattr(n.translation, 'x'):
            r = n.rotation
            L[0, :3] = [r.m_11, r.m_12, r.m_13]
            L[1, :3] = [r.m_21, r.m_22, r.m_23]
            L[2, :3] = [r.m_31, r.m_32, r.m_33]
            L[:3, :3] *= n.scale
            L[3, :3] = [n.translation.x, n.translation.y, n.translation.z]
        M2 = L @ M
        if isinstance(n, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            d = n.data
            if d is not None and getattr(d, 'num_vertices', 0) > 0:
                verts = np.array([[v.x, v.y, v.z] for v in d.vertices])
                out.append(verts @ M2[:3, :3] + M2[3, :3])
        elif isinstance(n, NifFormat.NiNode):
            for c in n.children:
                walk(c, M2)

    if isinstance(node, NifFormat.NiNode):
        for c in node.children:
            walk(c, np.eye(4))
    if not out:
        return None
    return np.vstack(out)


def _decompose_clutter_hull(node, hull_shape):
    """Replace a concave-filling single convex hull with a bhkListShape of
    tighter per-piece hulls rebuilt from the visual geometry.

    Returns the new bhkListShape, or None to keep the original shape.
    Only called for dynamic (mass>0) plain bhkRigidBody clutter, where the
    shape frame equals the node frame.
    """
    try:
        import numpy as np
        from scipy.spatial import ConvexHull  # noqa: F401 — availability check
    except ImportError:
        return None

    pts = _collect_visual_vertices(node)
    if pts is None or len(pts) < 24 or len(pts) > 60000:
        return None
    pts_hk = pts / _GAME_UNITS_PER_HAVOK

    # Frame/coverage sanity: the existing (already scaled) hull must roughly
    # match the visual AABB, otherwise the collision was authored to cover
    # something else (or sits in a different frame) — keep it.
    hull_pts = np.array([[v.x, v.y, v.z] for v in hull_shape.vertices])
    if len(hull_pts) < 4:
        return None
    for axis in range(3):
        v_lo, v_hi = pts_hk[:, axis].min(), pts_hk[:, axis].max()
        h_lo, h_hi = hull_pts[:, axis].min(), hull_pts[:, axis].max()
        v_ext, h_ext = v_hi - v_lo, h_hi - h_lo
        max_ext = max(v_ext, h_ext, 1e-4)
        if abs((v_lo + v_hi) - (h_lo + h_hi)) / 2 > 0.35 * max_ext:
            return None
        if not (0.6 <= (v_ext + 1e-4) / (h_ext + 1e-4) <= 1.67):
            return None

    single_vol = _hull_volume(pts_hk)
    if single_vol is None or single_vol <= 0:
        return None

    pieces = _recursive_hull_split(pts_hk, _DECOMP_MAX_DEPTH)
    if len(pieces) < 2:
        return None

    radius = max(hull_shape.radius, 0.005)
    sk_material = _get_havok_material(hull_shape.material)
    piece_shapes = []
    for piece in pieces:
        s = _build_piece_convex_shape(piece, radius, sk_material)
        if s is None:
            return None
        piece_shapes.append(s)

    list_shape = NifFormat.bhkListShape()
    _set_havok_material(list_shape.material, sk_material)
    list_shape.num_sub_shapes = len(piece_shapes)
    list_shape.sub_shapes.update_size()
    for i, s in enumerate(piece_shapes):
        list_shape.sub_shapes[i] = s
    list_shape.num_unknown_ints = len(piece_shapes)
    list_shape.unknown_ints.update_size()
    for i in range(len(piece_shapes)):
        list_shape.unknown_ints[i] = 0
    return list_shape


# ---------------------------------------------------------------------------
# Full collision conversion per-node
# ---------------------------------------------------------------------------

def _convert_collision(node, actual_root=None):
    """Convert all collision on a NiNode from Oblivion to Skyrim Havok format.

    Modifies node.collision_object in-place.
    actual_root: the NIF's top-level root node, used as target for
    bhkCompressedMeshShape so Skyrim reads the correct world transform.
    """
    if not hasattr(node, 'collision_object') or node.collision_object is None:
        return

    # bhkSPCollisionObject / bhkNPCollisionObject / bhkBlendCollisionObject are
    # Oblivion phantom/trigger-volume types (fire damage spheres, etc.).  Skyrim
    # does not support these block types; the engine reads garbage or null-derefs
    # when it encounters them, causing a red-triangle (failed load).  Strip them.
    _PHANTOM_COLL_TYPES = frozenset({
        'bhkSPCollisionObject', 'bhkNPCollisionObject', 'bhkBlendCollisionObject'
    })
    if node.collision_object.__class__.__name__ in _PHANTOM_COLL_TYPES:
        node.collision_object = None
        return

    coll_obj = node.collision_object
    # Default: standard Skyrim collision flags.  Animated collision (keyframed)
    # has flags overridden below after rigid body analysis.
    coll_obj.flags = 129

    rb = coll_obj.body if hasattr(coll_obj, 'body') else None
    if rb is None:
        return

    if isinstance(rb, NifFormat.bhkSimpleShapePhantom):
        rb.shape = _convert_shape(rb.shape, node)
        _convert_materials(rb.shape)
        return

    # Scale rigid body translation.
    # bhkRigidBodyT uses translation for Havok body offset; scale it.
    # bhkRigidBody (non-T) relies on the NiNode world transform for placement;
    # its translation field is not used by Skyrim and should be zeroed to avoid
    # any residual Oblivion value shifting the physics body.
    if isinstance(rb, NifFormat.bhkRigidBodyT):
        rb.translation.x *= _HAVOK_SCALE
        rb.translation.y *= _HAVOK_SCALE
        rb.translation.z *= _HAVOK_SCALE
    else:
        rb.translation.x = 0.0
        rb.translation.y = 0.0
        rb.translation.z = 0.0
    rb.center.x *= _HAVOK_SCALE
    rb.center.y *= _HAVOK_SCALE
    rb.center.z *= _HAVOK_SCALE

    _convert_rigid_body(rb)

    if rb.mass == 0:
        if rb.motion_system == 6:  # Keyframed (animated lid/door etc.)
            # Skyrim animated doors/activators: the collision body follows the
            # NiNode animation exactly (keyframed).  Values sourced from vanilla
            # Skyrim farmhouseanimdoor01.nif.
            coll_obj.flags      = 137  # 0x89 = ACTIVE | D_ANIMATED | bit 7
            rb.motion_system    = 4    # MO_SYS_KEYFRAMED
            rb.deactivator_type = 1
            rb.quality_type     = 1    # MO_QUAL_FIXED (position is deterministic)
            rb.solver_deactivation = 1
            rb.unknown_byte     = 10   # Skyrim broadphase type for animated
            # Set bit 7 (0x80) on the animated NiNode — tells Skyrim to
            # synchronise the node's transform updates with physics.
            if hasattr(node, 'flags'):
                node.flags = NIF_FLAGS | 0x80  # 0x008E = 142
        else:
            # Static object — vanilla Skyrim static NIFs (farmhouse01.nif etc.)
            # use quality_type=0 (MO_QUAL_INVALID = auto-detect), not 1.
            # The working pre-refactor code also used 0.
            rb.motion_system    = 5  # SYS_BOX_STABILIZED
            rb.deactivator_type = 1
            rb.quality_type     = 0  # MO_QUAL_INVALID (auto-detect, vanilla standard)
            rb.solver_deactivation = 1
        rb.friction         = 0.50
        rb.restitution      = 0.40
        rb.linear_damping   = 0.0996
        rb.angular_damping  = 0.0498
        rb.max_linear_velocity  = 104.4
        rb.max_angular_velocity = 31.57
    else:
        # Dynamic/clutter objects.
        #
        # Mass: keep Oblivion mass as-is.  Oblivion masses (0.1–35) are in the
        # same SI-kilogram range as Skyrim clutter (0.2–100) — no scaling needed.
        # Skyrim designers set masses independently; there is no consistent
        # object-to-object multiplier between the two games.
        #
        # Inertia tensor: inertia ∝ mass × length², and lengths scale by
        # _HAVOK_SCALE (0.1) going from Oblivion Havok units (game/7) to Skyrim
        # Havok units (game/70) — so inertia scales by _HAVOK_SCALE² = 0.01.
        # Verified against vanilla: silverjug01 (mass 0.8, r≈0.19 hk, h≈0.6 hk)
        # stores I_x=0.031 = m(3r²+h²)/12 exactly (SI physics in Havok metres).
        # Scaling by only 0.1 leaves inertia ~10× too large → objects resist
        # rotation, feel sluggish/heavy when grabbed or knocked.
        _INERTIA_SCALE = _HAVOK_SCALE ** 2  # 0.01
        rb.inertia.m_11 *= _INERTIA_SCALE
        rb.inertia.m_12 *= _INERTIA_SCALE
        rb.inertia.m_13 *= _INERTIA_SCALE
        rb.inertia.m_21 *= _INERTIA_SCALE
        rb.inertia.m_22 *= _INERTIA_SCALE
        rb.inertia.m_23 *= _INERTIA_SCALE
        rb.inertia.m_31 *= _INERTIA_SCALE
        rb.inertia.m_32 *= _INERTIA_SCALE
        rb.inertia.m_33 *= _INERTIA_SCALE
        # motion_system: preserve SPHERE (2) for round objects; map all others
        # (Oblivion used BOX=4) to SPHERE_INERTIA (3), which Skyrim uses for
        # asymmetric clutter (keys, bottles, boxes, etc.).
        if rb.motion_system == 2:
            pass  # keep SPHERE
        else:
            rb.motion_system = 3  # MO_SYS_SPHERE_INERTIA
        rb.quality_type    = 4  # MO_QUAL_MOVING
        rb.deactivator_type = 1
        rb.solver_deactivation = 2
        rb.rolling_friction_multiplier = 0
        rb.linear_damping  = 0.0996
        rb.angular_damping = 0.0498
        rb.friction        = 0.50
        rb.restitution     = 0.40
        rb.max_linear_velocity  = 104.4
        rb.max_angular_velocity = 31.57

    # bhkCompressedMeshShape.target must point to the specific NiNode that
    # owns the collision object (i.e. `node` itself).  Using actual_root
    # (BSFadeNode with identity transform) is wrong when collision is on an
    # inner NiNode that carries the rotation — Havok would position the shape
    # at the origin instead of the correct rotated world position.
    rb.shape = _convert_shape(rb.shape, actual_root if actual_root is not None else node)
    _convert_materials(rb.shape)

    # Dynamic clutter with a single full-object convex hull: rebuild concave
    # objects (goblets, pitchers, ewers…) as a compound of tighter hulls so
    # the activation raycast and contacts match the visible mesh.
    # bhkRigidBodyT excluded — its shape frame is offset from the node frame.
    if (rb.mass > 0 and rb.__class__ is NifFormat.bhkRigidBody
            and isinstance(rb.shape, NifFormat.bhkConvexVerticesShape)):
        decomposed = _decompose_clutter_hull(node, rb.shape)
        if decomposed is not None:
            rb.shape = decomposed

def convert_all_collisions(node, actual_root=None):
    """Recursively convert collision objects on every node in the entire tree.

    Skyrim requires ALL bhkCollisionObject instances in a NIF to use Skyrim
    Havok format.  Our main conversion only processes the root node's collision,
    but objects like animated display cases have additional collision objects on
    child NiNodes (e.g. the moving lid).  These child collisions also contain
    Oblivion-format unknown_6_shorts values that cause a crash when Skyrim reads
    them as pointers.  This function walks the full tree to convert every one.

    actual_root: the NIF's top-level root node (BSFadeNode).  Passed through
    to _convert_collision → _convert_shape → _extract_mopp_result so that
    bhkCompressedMeshShape.target always points to the root, not an inner wrapper.
    """
    if node is None or not isinstance(node, NifFormat.NiNode):
        return
    if actual_root is None:
        actual_root = node
    _convert_collision(node, actual_root)
    if hasattr(node, 'children'):
        for child in node.children:
            convert_all_collisions(child, actual_root)



def scale_constraint_pivots(data):
    """Fix Havok constraint data for Oblivion → Skyrim conversion.

    Two things need fixing for swinging signs (bhkLimitedHingeConstraint):

    1. Pivot scale: pivot_a/pivot_b are in Oblivion Havok-space positions and
       must be scaled by _HAVOK_SCALE (0.1).  Axis vectors are unit vectors
       and must NOT be scaled.

    2. Missing perp_2_axle_in_b_1: Oblivion's LimitedHingeDescriptor does not
       have perp_2_axle_in_b_1; Skyrim does.  Leaving it zero causes the sign
       to spawn at a wrong tilt.  Derived as: perp_b2 × axle_b (normalised).

    3. broadphaseType=10 for dynamic constrained bodies.

    bhkRigidBodyT is kept as-is: Skyrim uses bhkRigidBodyT for constrained
    sign bodies (confirmed in vanilla signfourshieldstavern01.nif).  The T
    offset is body-local relative to the owning NiNode, which is already
    correct after _convert_collision scales it by _HAVOK_SCALE.
    """
    for block in data.blocks:
        if not isinstance(block, NifFormat.bhkLimitedHingeConstraint):
            continue
        d = block.limited_hinge

        # 1. Scale pivot positions (xyz only; w is unused padding).
        for pivot_attr in ('pivot_a', 'pivot_b'):
            pivot = getattr(d, pivot_attr, None)
            if pivot is not None:
                pivot.x *= _HAVOK_SCALE
                pivot.y *= _HAVOK_SCALE
                pivot.z *= _HAVOK_SCALE

        # 2. Compute missing perp_2_axle_in_b_1 for Skyrim format.
        #    axle_b, perp_2_axle_in_b_1, perp_2_axle_in_b_2 form a right-handed
        #    orthonormal frame.  Recover perp_2_axle_in_b_1 = perp_b2 × axle_b.
        #    Vanilla Skyrim stores w=-1 on perp_2_axle_in_a_1 and perp_2_axle_in_b_1.
        perp_b1 = getattr(d, 'perp_2_axle_in_b_1', None)
        if perp_b1 is not None:
            axle_b  = d.axle_b
            perp_b2 = d.perp_2_axle_in_b_2
            cx = perp_b2.y * axle_b.z - perp_b2.z * axle_b.y
            cy = perp_b2.z * axle_b.x - perp_b2.x * axle_b.z
            cz = perp_b2.x * axle_b.y - perp_b2.y * axle_b.x
            mag = math.sqrt(cx*cx + cy*cy + cz*cz)
            if mag > 1e-6:
                cx /= mag; cy /= mag; cz /= mag
            perp_b1.x = cx
            perp_b1.y = cy
            perp_b1.z = cz
            perp_b1.w = -1.0

        perp_a1 = getattr(d, 'perp_2_axle_in_a_1', None)
        if perp_a1 is not None:
            perp_a1.w = -1.0

        # 3. Clamp max_friction to Skyrim range.
        #    Oblivion stores max_friction=3.0; Skyrim signs use 0.01.
        #    At 3.0 the hinge has enough rotational friction to lock the sign
        #    at any angle against gravity, so it stops at a wrong tilt instead
        #    of swinging freely back to vertical.
        if d.max_friction > 0.5:
            d.max_friction = 0.01

        # 4. Re-scale inertia for dynamic constrained bodies.
        #    _convert_collision already applied _HAVOK_SCALE (0.1) to inertia.
        #    Inertia has units mass*length^2, so it requires _HAVOK_SCALE^2 = 0.01 total.
        #    Apply the missing second factor of 0.1 here.
        #    (General clutter inertia stays at *0.1 since Oblivion clutter was authored
        #    with smaller values and the result is already in the correct Skyrim range.)
        for e in block.entities:
            if e is not None and e.mass > 0.0:
                I = e.inertia
                I.m_11 *= _HAVOK_SCALE
                I.m_12 *= _HAVOK_SCALE
                I.m_13 *= _HAVOK_SCALE
                I.m_21 *= _HAVOK_SCALE
                I.m_22 *= _HAVOK_SCALE
                I.m_23 *= _HAVOK_SCALE
                I.m_31 *= _HAVOK_SCALE
                I.m_32 *= _HAVOK_SCALE
                I.m_33 *= _HAVOK_SCALE

        # 5. broadphaseType=10 for dynamic constrained bodies.
        for e in block.entities:
            if e is not None and e.mass > 0.0:
                e.unknown_byte = 10


# ---------------------------------------------------------------------------
# Collision hoisting (child → root)
# ---------------------------------------------------------------------------

def _offset_collision_shape_verts(co, ox, oy, oz):
    """Add (ox, oy, oz) game-unit offset to all collision shape vertices.

    In Oblivion bhkNiTriStripsShape the vertices are stored at game-unit scale
    (×7 Havok units).  Adding the game-unit offset here bakes a child NiNode's
    world-space translation into the shape so the collision stays in the correct
    position after the node is hoisted to the root.

    Traverses: bhkCollisionObject → body → shape → (bhkMoppBvTreeShape →) bhkNiTriStripsShape
    """
    rb = getattr(co, 'body', None)
    if rb is None:
        return
    shape = getattr(rb, 'shape', None)
    # Unwrap bhkMoppBvTreeShape to get at the inner shape
    if shape is not None and isinstance(shape, NifFormat.bhkMoppBvTreeShape):
        shape = shape.shape
    if shape is None or not isinstance(shape, NifFormat.bhkNiTriStripsShape):
        return
    for sd in shape.strips_data:
        if sd is None:
            continue
        for v in sd.vertices:
            v.x += ox
            v.y += oy
            v.z += oz


def hoist_collision(root):
    """Find a collision object on any descendant NiNode and move it to root.

    Skyrim requires bhkCollisionObject to be on the root BSFadeNode.
    Oblivion meshes sometimes put it on a child NiNode (e.g. 'CollisionXxx').
    We take the first one found, assign it to root, and null it on the child.

    If the source child NiNode has a non-zero translation, that offset is baked
    into the collision shape vertices so the collision stays in the correct world
    position after being moved to the root (which is at the world origin).

    Returns True if a collision was hoisted.
    """
    def _find_and_clear(node):
        """Return (collision_object, child_node_translation_xyz) or None."""
        if not isinstance(node, NifFormat.NiNode):
            return None
        for child in node.children:
            if child is None:
                continue
            if (hasattr(child, 'collision_object') and
                    child.collision_object is not None):
                co = child.collision_object
                child.collision_object = None
                t = child.translation
                return co, (t.x, t.y, t.z)
            result = _find_and_clear(child)
            if result is not None:
                return result
        return None

    found = _find_and_clear(root)
    if found is not None:
        co, (ox, oy, oz) = found
        root.collision_object = co
        co.target = root
        # If the source NiNode was not at the origin, bake its world-space
        # translation into the bhkNiTriStripsShape vertices so the collision
        # lands in the correct position after being placed on the root node.
        if ox != 0.0 or oy != 0.0 or oz != 0.0:
            _offset_collision_shape_verts(co, ox, oy, oz)
        return True
    return False


def remove_empty_collision_nodes(root):
    """Remove empty NiNode children that were collision containers.

    After hoisting collision to root, the original NiNode child (e.g.
    'Collision045') is left empty: no children, no collision_object.  Skyrim
    processes every child of BSFadeNode and an unexpected empty NiNode can
    trigger crashes.  This function compacts the children array in-place.
    """
    if not hasattr(root, 'children') or not isinstance(root, NifFormat.NiNode):
        return
    keep = []
    for child in root.children:
        if child is None:
            continue
        # Remove bare NiNodes with no children and no collision
        if (type(child).__name__ in ('NiNode',) and
                child.num_children == 0 and
                getattr(child, 'collision_object', None) is None):
            continue
        keep.append(child)
    if len(keep) < root.num_children:
        root.num_children = len(keep)
        root.children.update_size()
        for i, c in enumerate(keep):
            root.children[i] = c
