import math

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401

from pyffi.formats.nif import NifFormat

from .cms_builder import build_cms_collision

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
# Mesh collision rebuild (strips/packed → vanilla-style MOPP + CMS)
# ---------------------------------------------------------------------------

def _shape_tri_soup(shape):
    """Extract (triangles_hu, sk_material) from a mesh collision shape.

    bhkNiTriStripsShape data is at game-unit scale (÷7 → Oblivion havok,
    ×_HAVOK_SCALE → Skyrim havok).  hkPackedNiTriStripsData is at 1/7
    game-unit scale already (×_HAVOK_SCALE only).  Returns None for
    non-mesh shapes (caller uses the primitive conversion path).
    """
    if isinstance(shape, NifFormat.bhkNiTriStripsShape):
        scale = _HAVOK_SCALE / 7.0
        tris = []
        for sd in shape.strips_data:
            if sd is None:
                continue
            verts = [(v.x * scale, v.y * scale, v.z * scale)
                     for v in sd.vertices]
            tris.extend((verts[a], verts[b], verts[c])
                        for a, b, c in _triangulate_strips(sd))
        if not tris:
            return None
        material = _get_havok_material(shape.material)
        if 0 <= material <= 31:
            material = _OB_TO_SK_MATERIAL.get(material, 3741512247)
        return tris, material

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        data = getattr(shape, 'data', None)
        if data is None or data.num_triangles == 0:
            return None
        verts = [(v.x * _HAVOK_SCALE, v.y * _HAVOK_SCALE, v.z * _HAVOK_SCALE)
                 for v in data.vertices]
        tris = []
        for t in data.triangles:
            a, b, c = t.triangle.v_1, t.triangle.v_2, t.triangle.v_3
            if a == b or b == c or a == c:
                continue
            tris.append((verts[a], verts[b], verts[c]))
        if not tris:
            return None
        material = 3741512247  # stone default
        if shape.num_sub_shapes > 0:
            material = _get_havok_material(shape.sub_shapes[0].material)
            if 0 <= material <= 31:
                material = _OB_TO_SK_MATERIAL.get(material, 3741512247)
        return tris, material

    return None


def _bake_body_transform_into_tris(rb, tris):
    """Fold a bhkRigidBodyT transform into the triangle soup (Skyrim hu).

    Vanilla Skyrim never pairs a transformed rigid body with MOPP/mesh
    collision (0 of 6341 vanilla CMS meshes contain bhkRigidBodyT): the
    engine's CMS/MOPP query path intermittently produces invalid shape keys
    (HK_INVALID_SHAPE_KEY → runaway hit scan → CTD) when one is present —
    every Collision Sentinel CULPRIT was a rotated-root mesh whose wrap
    pass produced bhkRigidBodyT + CMS.  So the body transform is applied to
    the vertices here and the body is demoted to a plain identity
    bhkRigidBody, exactly like vanilla static collision.

    rb.translation must already be in Skyrim havok units (the caller scales
    it before shape conversion).  Returns the transformed triangle list.
    """
    if not isinstance(rb, NifFormat.bhkRigidBodyT):
        return tris
    q = rb.rotation
    R = _m3_from_quat_xyzw(q.x, q.y, q.z, q.w)  # column-vector convention
    t = (rb.translation.x, rb.translation.y, rb.translation.z)

    def xf(v):
        return (
            R[0][0] * v[0] + R[0][1] * v[1] + R[0][2] * v[2] + t[0],
            R[1][0] * v[0] + R[1][1] * v[1] + R[1][2] * v[2] + t[1],
            R[2][0] * v[0] + R[2][1] * v[1] + R[2][2] * v[2] + t[2],
        )

    tris = [(xf(a), xf(b), xf(c)) for a, b, c in tris]
    rb.__class__ = NifFormat.bhkRigidBody
    rb.rotation.x = rb.rotation.y = rb.rotation.z = 0.0
    rb.rotation.w = 1.0
    rb.translation.x = rb.translation.y = rb.translation.z = 0.0
    return tris


def _packed_from_tris(tris, sk_material):
    """Fallback: bare bhkPackedNiTriStripsShape (no MOPP) from a hu soup.

    Only used when the Havok bridge is unavailable or rejects the mesh.
    Packed data vertices are stored at 10× havok units (1/7 game scale).
    """
    vert_index = {}
    verts = []
    idx_tris = []
    for tri in tris:
        idx = []
        for v in tri:
            key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
            i = vert_index.get(key)
            if i is None:
                i = len(verts)
                vert_index[key] = i
                verts.append(key)
            idx.append(i)
        if idx[0] == idx[1] or idx[1] == idx[2] or idx[0] == idx[2]:
            continue
        idx_tris.append(idx)
    if not idx_tris:
        return None

    packed_verts = [(x * 10.0, y * 10.0, z * 10.0) for x, y, z in verts]
    hkdata = NifFormat.hkPackedNiTriStripsData()
    hkdata.num_vertices = len(packed_verts)
    hkdata.vertices.update_size()
    for i, (x, y, z) in enumerate(packed_verts):
        hkdata.vertices[i].x = x
        hkdata.vertices[i].y = y
        hkdata.vertices[i].z = z
    hkdata.num_triangles = len(idx_tris)
    hkdata.triangles.update_size()
    for i, (a, b, c) in enumerate(idx_tris):
        hkdata.triangles[i].triangle.v_1 = a
        hkdata.triangles[i].triangle.v_2 = b
        hkdata.triangles[i].triangle.v_3 = c
        hkdata.triangles[i].welding_info = 0
        nx, ny, nz = _find_normal(packed_verts, a, b, c)
        hkdata.triangles[i].normal.x = nx
        hkdata.triangles[i].normal.y = ny
        hkdata.triangles[i].normal.z = nz

    packed = NifFormat.bhkPackedNiTriStripsShape()
    packed.num_sub_shapes = 1
    packed.sub_shapes.update_size()
    packed.sub_shapes[0].layer = 1  # LAYER_STATIC
    packed.sub_shapes[0].num_vertices = len(packed_verts)
    _set_havok_material(packed.sub_shapes[0].material, sk_material)
    packed.scale.x = 1.0
    packed.scale.y = 1.0
    packed.scale.z = 1.0
    packed.unknown_float_1 = 0.1
    packed.unknown_float_3 = 0.1
    packed.data = hkdata
    return packed


def _rebuild_mesh_collision(rb, target_node):
    """Rebuild strips/packed mesh collision as vanilla MOPP+CMS (in place).

    Handles rb.shape being bhkNiTriStripsShape, bhkPackedNiTriStripsShape,
    or a stale Oblivion bhkMoppBvTreeShape wrapping either.  Bakes any
    bhkRigidBodyT transform into the geometry (body becomes plain identity
    bhkRigidBody).  Returns True when handled; False → caller uses the
    primitive-shape conversion path.
    """
    shape = rb.shape
    inner = shape.shape if isinstance(shape, NifFormat.bhkMoppBvTreeShape) else shape
    soup = _shape_tri_soup(inner)
    if soup is None:
        return False
    tris, sk_material = soup
    tris = [t for t in tris
            if all(math.isfinite(c) for v in t for c in v)]
    if not tris:
        return False
    tris = _bake_body_transform_into_tris(rb, tris)
    mopp = build_cms_collision(tris, sk_material, NifFormat)
    if mopp is not None:
        mopp.shape.target = target_node
        rb.shape = mopp
        return True
    packed = _packed_from_tris(tris, sk_material)
    if packed is not None:
        rb.shape = packed
        return True
    return False


def demote_t_body_on_mesh_collision(data):
    """Demote bhkRigidBodyT bodies that own MOPP/CMS collision (in-place).

    For pre-made Skyrim-format assets (the Skyblivion speedtree pack pairs
    bhkRigidBodyT with bhkCompressedMeshShape — a combination vanilla Skyrim
    never ships, 0 of 6341 vanilla CMS meshes, and the engine path that
    intermittently produces invalid shape keys / CTDs).

    Pure-translation bodies (the speedtree case): the CMS chunk translations,
    big verts, bounds and the MOPP origin are shifted by t — MOPP bytecode is
    origin-relative, so no recompile is needed.  Rotated bodies fall back to
    a full decode + rebuild through the Havok bridge.  Returns the number of
    bodies demoted.
    """
    from .cms import decode_cms

    n = 0
    for blk in list(data.blocks):
        if blk.__class__ is not NifFormat.bhkRigidBodyT:
            continue
        mopp = blk.shape
        if not isinstance(mopp, NifFormat.bhkMoppBvTreeShape):
            continue
        cms = getattr(mopp, 'shape', None)
        cms_data = getattr(cms, 'data', None)
        if cms_data is None or type(cms_data).__name__ != 'bhkCompressedMeshShapeData':
            continue

        q = blk.rotation
        t = (blk.translation.x, blk.translation.y, blk.translation.z)
        rot_identity = max(abs(q.x), abs(q.y), abs(q.z),
                           abs(abs(q.w) - 1.0)) < 1e-5
        xforms_identity = all(
            max(abs(x.rotation.x), abs(x.rotation.y), abs(x.rotation.z),
                abs(abs(x.rotation.w) - 1.0)) < 1e-5
            for x in cms_data.chunk_transforms
        )

        if rot_identity and xforms_identity:
            for ch in cms_data.chunks:
                ch.translation.x += t[0]
                ch.translation.y += t[1]
                ch.translation.z += t[2]
            for bv in cms_data.big_verts:
                bv.x += t[0]
                bv.y += t[1]
                bv.z += t[2]
            for bound in (cms_data.bounds_min, cms_data.bounds_max):
                bound.x += t[0]
                bound.y += t[1]
                bound.z += t[2]
            mopp.origin.x += t[0]
            mopp.origin.y += t[1]
            mopp.origin.z += t[2]
        else:
            # Rotated body — rebuild the whole chain over transformed tris.
            R = _m3_from_quat_xyzw(q.x, q.y, q.z, q.w)
            tris = [
                tuple(
                    tuple(sum(R[i][k] * v[k] for k in range(3)) + t[i]
                          for i in range(3))
                    for v in tri
                )
                for _key, tri in decode_cms(cms_data)
            ]
            material = 3741512247
            if cms_data.num_materials > 0:
                material = int(cms_data.chunk_materials[0].material)
            new_mopp = build_cms_collision(tris, material, NifFormat)
            if new_mopp is None:
                continue  # keep the T body rather than lose collision
            new_mopp.shape.target = cms.target
            blk.shape = new_mopp

        blk.rotation.x = blk.rotation.y = blk.rotation.z = 0.0
        blk.rotation.w = 1.0
        blk.translation.x = blk.translation.y = blk.translation.z = 0.0
        blk.__class__ = NifFormat.bhkRigidBody
        n += 1
    return n


# ---------------------------------------------------------------------------
# Rigid body conversion
# ---------------------------------------------------------------------------

# Oblivion layer → Skyrim layer for the values whose meaning diverges.
# Layers 0-18 are identical in both enums (STATIC..PORTAL) and pass through.
# 19+ diverge: Oblivion 19-31 are stairs/pick layers that moved, 32 is
# OL_OTHER, and 33-56 are per-bone ragdoll layers (OL_HEAD..OL_WING) that
# Oblivion authors also used on constrained world props (cellchain01 anchor
# = OL_L_FOOT).  Skyrim reads those raw values as pick/zone layers with NO
# physical collision (42 = PATHPICK), so touching the object does nothing.
# Vanilla Skyrim constrained props (trapmace01 links) use 10 = PROPS.
_OB_TO_SKY_LAYER: dict[int, int] = {
    19: 31,   # OL_STAIRS          → SKYL_STAIRHELPER
    20: 30,   # OL_CHAR_CONTROLLER → SKYL_CHARCONTROLLER
    21: 34,   # OL_AVOID_BOX       → SKYL_AVOIDBOX
    22: 1,    # OL_UNKNOWN1        → STATIC (no equivalent)
    23: 1,    # OL_UNKNOWN2        → STATIC (no equivalent)
    24: 39,   # OL_CAMERA_PICK     → SKYL_CAMERAPICK
    25: 40,   # OL_ITEM_PICK       → SKYL_ITEMPICK
    26: 41,   # OL_LINE_OF_SIGHT   → SKYL_LINEOFSIGHT
    27: 42,   # OL_PATH_PICK       → SKYL_PATHPICK
    28: 43,   # OL_CUSTOM_PICK_1   → SKYL_CUSTOMPICK1
    29: 44,   # OL_CUSTOM_PICK_2   → SKYL_CUSTOMPICK2
    30: 45,   # OL_SPELL_EXPLOSION → SKYL_SPELLEXPLOSION
    31: 46,   # OL_DROPPING_PICK   → SKYL_DROPPINGPICK
    32: 4,    # OL_OTHER           → SKYL_CLUTTER
}
for _l in range(33, 58):   # OL_HEAD..OL_NULL (ragdoll bone layers)
    _OB_TO_SKY_LAYER[_l] = 10  # SKYL_PROPS (vanilla constrained-prop layer)


def _remap_world_filter(rb):
    """Convert the Oblivion collision filter of a world-object body (in-place).

    Remaps diverging layer values (see _OB_TO_SKY_LAYER) and zeroes the
    flags/part byte and group: bits 0-4 are biped part numbers (meaningless
    off the BIPED layers) and bit 7 is Skyrim's "Linked Group" flag —
    Oblivion chains ship 0x80|part here, vanilla Skyrim constrained props
    ship 0 and rely on the engine's per-reference group assignment.
    Creature-skeleton blend bodies do NOT go through this (their layer is
    forced to 8 BIPED in _convert_blend_collision and part numbers matter).
    """
    for hf in (getattr(rb, 'havok_col_filter', None),
               getattr(rb, 'havok_col_filter_copy', None)):
        if hf is None:
            continue
        hf.layer = _OB_TO_SKY_LAYER.get(int(hf.layer), int(hf.layer))
        hf.flags_and_part_number = 0
        hf.unknown_short = 0   # Group


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

def _expand_multisphere(ms):
    """Expand bhkMultiSphereShape into per-sphere bhkConvexTransformShape-
    wrapped bhkSphereShapes.

    hkpMultiSphereShape is deprecated in Skyrim's Havok generation: 0 of
    17,216 vanilla meshes ship the block, and files that do (Oblivion's
    alchemy apparatus clutter) crash SSE at cell load with no crash log.
    Vanilla expresses the same thing as ConvexTransform+Sphere children in a
    list shape (e.g. clutter\\kitchen\\woodenladle01.nif).

    Sphere data arrives in Oblivion Havok units — the ×0.1 rescale happens
    here.  Returns a single wrapper for 1 sphere, a bhkListShape for several,
    or None for an empty multisphere.
    """
    mat = _get_havok_material(ms.material)
    wrappers = []
    for s in ms.spheres:
        sph = NifFormat.bhkSphereShape()
        _set_havok_material(sph.material, mat)
        sph.radius = s.radius * _HAVOK_SCALE

        cts = NifFormat.bhkConvexTransformShape()
        _set_havok_material(cts.material, mat)
        cts.unknown_float_1 = sph.radius
        for i in range(8):
            cts.unknown_8_bytes[i] = 0
        t = cts.transform
        # Identity rotation, translation in the 4th column, 4th row all
        # zeros (incl. m_44) — matches vanilla bhkConvexTransformShape.
        t.m_11 = 1.0; t.m_12 = 0.0; t.m_13 = 0.0; t.m_14 = s.center.x * _HAVOK_SCALE
        t.m_21 = 0.0; t.m_22 = 1.0; t.m_23 = 0.0; t.m_24 = s.center.y * _HAVOK_SCALE
        t.m_31 = 0.0; t.m_32 = 0.0; t.m_33 = 1.0; t.m_34 = s.center.z * _HAVOK_SCALE
        t.m_41 = 0.0; t.m_42 = 0.0; t.m_43 = 0.0; t.m_44 = 0.0
        cts.shape = sph
        wrappers.append(cts)

    if not wrappers:
        return None
    if len(wrappers) == 1:
        return wrappers[0]
    ls = NifFormat.bhkListShape()
    _set_havok_material(ls.material, mat)
    ls.num_sub_shapes = len(wrappers)
    ls.sub_shapes.update_size()
    for i, w in enumerate(wrappers):
        ls.sub_shapes[i] = w
    ls.num_unknown_ints = len(wrappers)
    ls.unknown_ints.update_size()
    for i in range(len(wrappers)):
        ls.unknown_ints[i] = 0
    return ls


def _convert_shape(shape, root_node):
    """Recursively convert an Oblivion Havok shape to Skyrim format.

    Scales all geometry/dimensions by _HAVOK_SCALE (0.1).  Top-level mesh
    collision (strips/packed/MOPP) is rebuilt in _rebuild_mesh_collision
    before this runs; the mesh branches here only serve nested occurrences
    (e.g. a strips shape inside a bhkListShape) and produce a bare packed
    shape without MOPP.
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
        return _expand_multisphere(shape)

    # bhkConvexSweepShape: early-Oblivion (10.0.1.0) wrapper for a swept
    # convex shape (handscythe01, oar01).  Skyrim never ships it — unwrap to
    # the inner shape, which then converts normally.
    if shape.__class__.__name__ == 'bhkConvexSweepShape':
        return _convert_shape(shape.shape, root_node)

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
        # Convert children; flatten any nested bhkListShape produced by child
        # conversion (e.g. multisphere expansion) — a list shape carries no
        # transform of its own so flattening is semantics-preserving, and
        # vanilla never nests list shapes.
        children = []
        for i in range(len(shape.sub_shapes)):
            c = _convert_shape(shape.sub_shapes[i], root_node)
            if isinstance(c, NifFormat.bhkListShape):
                children.extend(list(c.sub_shapes))
            elif c is not None:
                children.append(c)
        if len(children) != shape.num_sub_shapes:
            shape.num_sub_shapes = len(children)
            shape.sub_shapes.update_size()
            shape.num_unknown_ints = len(children)
            shape.unknown_ints.update_size()
            for i in range(len(children)):
                shape.unknown_ints[i] = 0
        for i, c in enumerate(children):
            shape.sub_shapes[i] = c
        return shape

    if isinstance(shape, NifFormat.bhkNiTriStripsShape):
        # Nested strips (inside a list shape) → bare packed triangle shape.
        packed = _ni_strips_to_packed(shape)
        return packed if packed is not None else shape

    if isinstance(shape, NifFormat.bhkMoppBvTreeShape):
        # Never keep the outer bhkMoppBvTreeShape with stale Oblivion MOPP
        # data: Skyrim can't load Oblivion MOPP and will silently drop the
        # collision, while the incompatible blob causes undefined behaviour.
        return _convert_shape(shape.shape, root_node)

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
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

def _node_is_animated(node, actual_root):
    """True if this node's transform is driven by animation in this NIF.

    Sources checked (walking from the file root):
      - NiControllerSequence controlled blocks (node-name entries),
      - NiMultiTargetTransformController extra targets,
      - a NiTransformController/NiKeyframeController attached to the node.
    Used to decide whether an Oblivion MO_SYS_KEYFRAMED body stays keyframed
    in Skyrim (gate leaves, animated lids) or is an unyielding anchor/held
    trap part instead (see the motion-system comment in _convert_collision).
    """
    root = actual_root if actual_root is not None else node

    def _name_of(b):
        nm = getattr(b, 'name', b'')
        return nm.decode('latin-1') if isinstance(nm, (bytes, bytearray)) else str(nm)

    names = set()

    def _walk(n):
        if not isinstance(n, NifFormat.NiAVObject):
            return
        ctrl = getattr(n, 'controller', None)
        while ctrl is not None:
            cls = ctrl.__class__.__name__
            if cls == 'NiControllerManager':
                for seq in getattr(ctrl, 'controller_sequences', []) or []:
                    if seq is None:
                        continue
                    for cb in getattr(seq, 'controlled_blocks', []) or []:
                        try:
                            nm = cb.get_node_name()
                        except Exception:
                            nm = None
                        if nm:
                            names.add(nm.decode('latin-1')
                                      if isinstance(nm, (bytes, bytearray)) else str(nm))
            elif cls == 'NiMultiTargetTransformController':
                for t in getattr(ctrl, 'extra_targets', []) or []:
                    if t is not None:
                        names.add(_name_of(t))
            elif 'TransformController' in cls or 'KeyframeController' in cls:
                names.add(_name_of(n))
            ctrl = getattr(ctrl, 'next_controller', None)
        for c in getattr(n, 'children', []) or []:
            if c is not None:
                _walk(c)

    _walk(root)
    return _name_of(node) in names


def _convert_blend_collision(node, coll_obj):
    """Convert a bhkBlendCollisionObject on a creature-skeleton bone.

    Vanilla Skyrim creature skeletons KEEP blend collision objects (dog:
    flags=137, plain bhkRigidBody with a NON-zero bone-relative translation
    in Havok units, capsule shapes, motion_system=4 KEYFRAMED,
    quality_type=1 FIXED, layer=8 BIPED).  Shape/material/2010-format fixups
    are shared with the standard path.  Inertia gets the full ×0.01
    (mass·length², lengths scale ×0.1) here, same as the standard dynamic
    path in _convert_collision.
    """
    coll_obj.flags = 137
    rb = getattr(coll_obj, 'body', None)
    if rb is None:
        return
    # Blend bodies USE their translation (bone-relative placement) — scale,
    # never zero, even for plain bhkRigidBody.
    rb.translation.x *= _HAVOK_SCALE
    rb.translation.y *= _HAVOK_SCALE
    rb.translation.z *= _HAVOK_SCALE
    rb.center.x *= _HAVOK_SCALE
    rb.center.y *= _HAVOK_SCALE
    rb.center.z *= _HAVOK_SCALE
    _convert_rigid_body(rb)
    for attr in ('m_11', 'm_12', 'm_13', 'm_21', 'm_22', 'm_23',
                 'm_31', 'm_32', 'm_33'):
        setattr(rb.inertia, attr,
                getattr(rb.inertia, attr) * _HAVOK_SCALE * _HAVOK_SCALE)
    rb.motion_system = 4        # MO_SYS_KEYFRAMED (bone follows animation)
    rb.quality_type = 1         # MO_QUAL_FIXED
    rb.deactivator_type = 1
    rb.solver_deactivation = 1
    rb.havok_col_filter.layer = 8   # SKYL_BIPED
    rb.shape = _convert_shape(rb.shape, node)
    _convert_materials(rb.shape)


def _convert_collision(node, actual_root=None, keep_blend=False):
    """Convert all collision on a NiNode from Oblivion to Skyrim Havok format.

    Modifies node.collision_object in-place.
    actual_root: the NIF's top-level root node, used as target for
    bhkCompressedMeshShape so Skyrim reads the correct world transform.
    keep_blend: creature skeletons — convert bhkBlendCollisionObject
    (ragdoll bone collision, fully supported by Skyrim) instead of
    stripping it.
    """
    if not hasattr(node, 'collision_object') or node.collision_object is None:
        return

    # bhkBlendCollisionObject is stripped on world objects, but on creature
    # skeletons (keep_blend) it is the vanilla ragdoll-bone type.
    cls_name = node.collision_object.__class__.__name__
    if cls_name == 'bhkBlendCollisionObject':
        if keep_blend:
            _convert_blend_collision(node, node.collision_object)
        else:
            node.collision_object = None
        return
    if cls_name == 'bhkSPCollisionObject':
        # Trigger-volume phantom (tripwire triggers, gas/fire damage zones).
        # Skyrim fully supports bhkSPCollisionObject + bhkSimpleShapePhantom —
        # vanilla ships 31 of them under meshes/traps alone (traptripwire01,
        # pressure plates, bear trap...), always with collision-object
        # flags=129 and layer 12 (TRIGGER, same enum value as Oblivion).
        # Convert the inner shape (×0.1 scale + material remap) and keep it.
        body = getattr(node.collision_object, 'body', None)
        if isinstance(body, NifFormat.bhkSimpleShapePhantom):
            node.collision_object.flags = 129
            _remap_world_filter(body)
            body.shape = _convert_shape(body.shape, node)
            _convert_materials(body.shape)
        else:
            node.collision_object = None
        return
    if cls_name == 'bhkNPCollisionObject':
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
        _remap_world_filter(rb)
        rb.shape = _convert_shape(rb.shape, node)
        _convert_materials(rb.shape)
        return

    # Scale rigid body translation.
    # bhkRigidBodyT uses translation/rotation for the Havok body offset; scale
    # the translation and keep the rotation.
    # bhkRigidBody (non-T): OBLIVION ignores both fields, so its files carry
    # arbitrary leftover values there.  SKYRIM APPLIES BOTH EVEN ON NON-T
    # BODIES — proven by vanilla trapmace01.nif Base01: node rotated +0.5°
    # about X, body rotation = the exact inverse quaternion (-0.0044,0,0,1)
    # so its root-space MOPP stays aligned; every other vanilla non-T body is
    # exactly identity/zero, unlike the genuinely-garbage padding fields.
    # Leftover Oblivion rotations (up to ~115° on chain links) rotated every
    # constraint frame and collision shape out from under the solver: chains/
    # swinging traps acted welded solid, and ordinary clutter collision sat
    # askew from the visual mesh ("havok interactions feel weird").
    if isinstance(rb, NifFormat.bhkRigidBodyT):
        rb.translation.x *= _HAVOK_SCALE
        rb.translation.y *= _HAVOK_SCALE
        rb.translation.z *= _HAVOK_SCALE
    else:
        rb.translation.x = 0.0
        rb.translation.y = 0.0
        rb.translation.z = 0.0
        rb.translation.w = 0.0
        rb.rotation.x = 0.0
        rb.rotation.y = 0.0
        rb.rotation.z = 0.0
        rb.rotation.w = 1.0
    rb.center.x *= _HAVOK_SCALE
    rb.center.y *= _HAVOK_SCALE
    rb.center.z *= _HAVOK_SCALE

    _convert_rigid_body(rb)
    _remap_world_filter(rb)

    # Penetration depth is a LENGTH (max allowed overlap): Oblivion ships
    # 0.15 in its Havok units; vanilla Skyrim bodies carry ~0.005-0.012.
    # Unscaled it lets contacts sink an entire chain-link deep.
    rb.penetration_depth *= _HAVOK_SCALE

    # Oblivion MO_SYS_KEYFRAMED (6) semantics are context-dependent.  Three
    # cases, discriminated per body (vanilla Skyrim census):
    #  1. Node driven by animation (gate leaves targeted by Open/Close
    #     sequences, animated display-case lids) → Skyrim KEYFRAMED, like
    #     vanilla farmhouseanimdoor01.  Keyframed is ONLY valid for animated
    #     nodes: a keyframed body with anim flags (137/142) on a non-animated
    #     object flips the engine into the baked/anim-static path and the
    #     whole compound acts welded solid.
    #  2. mass>0 AND owns a constraint (mace-trap chain links: Oblivion holds
    #     whole traps keyframed until the trap script enables havok) →
    #     DYNAMIC, like vanilla trapmace01's links (ms=3, quality 4).
    #  3. Everything else (constrained-island anchors: cellchain01 root,
    #     cellChainMiddle, mass=100 "Unyielding"; unyielding props) →
    #     STATIC with mass 0.  Vanilla chain/noose/trap anchors are ALWAYS
    #     static mass-0 bodies (NooseRopePiece01 root, trapmace Base01),
    #     never keyframed.
    keyframed_body = rb.motion_system == 6 and _node_is_animated(node, actual_root)
    if rb.motion_system == 6 and not keyframed_body:
        if rb.mass > 0 and rb.num_constraints > 0:
            pass                    # case 2: falls into the dynamic branch
        else:
            rb.mass = 0.0           # case 3: falls into the static branch
    if keyframed_body:
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
        rb.friction         = 0.50
        rb.restitution      = 0.40
        rb.linear_damping   = 0.0996
        rb.angular_damping  = 0.0498
        rb.max_linear_velocity  = 104.4
        rb.max_angular_velocity = 31.57
        # Scripts can switch keyframed trap bodies to dynamic at runtime
        # (swinging traps activate that way), so inertia must be in Skyrim
        # units even though keyframed motion ignores it.  ×0.01, same as the
        # dynamic branch.
        _s2 = _HAVOK_SCALE * _HAVOK_SCALE
        for _attr in ('m_11', 'm_12', 'm_13', 'm_21', 'm_22', 'm_23',
                      'm_31', 'm_32', 'm_33'):
            setattr(rb.inertia, _attr, getattr(rb.inertia, _attr) * _s2)
    elif rb.mass == 0:
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

    # Mesh collision (strips/packed, possibly under a stale Oblivion MOPP) is
    # rebuilt from scratch as vanilla-style MOPP + bhkCompressedMeshShape with
    # any bhkRigidBodyT transform baked into the geometry (plain identity
    # body, like all 6341 vanilla CMS meshes).  The CMS target is the root
    # BSFadeNode — static collision must live on the root.
    target_node = actual_root if actual_root is not None else node
    if not _rebuild_mesh_collision(rb, target_node):
        rb.shape = _convert_shape(rb.shape, target_node)
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

def convert_all_collisions(node, actual_root=None, keep_blend=False):
    """Recursively convert collision objects on every node in the entire tree.

    Skyrim requires ALL bhkCollisionObject instances in a NIF to use Skyrim
    Havok format.  Our main conversion only processes the root node's collision,
    but objects like animated display cases have additional collision objects on
    child NiNodes (e.g. the moving lid).  These child collisions also contain
    Oblivion-format unknown_6_shorts values that cause a crash when Skyrim reads
    them as pointers.  This function walks the full tree to convert every one.

    actual_root: the NIF's top-level root node (BSFadeNode).  Passed through
    to _convert_collision → _rebuild_mesh_collision so that
    bhkCompressedMeshShape.target always points to the root, not an inner wrapper.
    keep_blend: creature skeleton mode — see _convert_collision.
    """
    if node is None or not isinstance(node, NifFormat.NiNode):
        return
    if actual_root is None:
        actual_root = node
    _convert_collision(node, actual_root, keep_blend=keep_blend)
    if hasattr(node, 'children'):
        for child in node.children:
            convert_all_collisions(child, actual_root, keep_blend=keep_blend)



def _vec_cross(a, b):
    """Cross product of two PyFFI Vector4s (xyz), returned as a tuple."""
    return (a.y * b.z - a.z * b.y,
            a.z * b.x - a.x * b.z,
            a.x * b.y - a.y * b.x)


def _vec_set_unit(dst, xyz, w=0.0):
    """Normalise xyz and store into a PyFFI Vector4."""
    x, y, z = xyz
    mag = math.sqrt(x * x + y * y + z * z)
    if mag > 1e-6:
        x /= mag; y /= mag; z /= mag
    dst.x = x
    dst.y = y
    dst.z = z
    dst.w = w


def _copy_struct(src, dst):
    """Copy a PyFFI compound field-by-field (Vector4s by component)."""
    done = set()
    for a in dst._attrs:
        name = a.name
        if name in done:
            continue
        done.add(name)
        try:
            sv = getattr(src, name)
            dv = getattr(dst, name)
        except Exception:
            continue
        if hasattr(dv, 'x') and hasattr(dv, 'w'):
            dv.x = sv.x; dv.y = sv.y; dv.z = sv.z; dv.w = sv.w
        elif hasattr(dv, '_attrs'):
            _copy_struct(sv, dv)
        elif isinstance(dv, (int, float, bool)):
            try:
                setattr(dst, name, sv)
            except Exception:
                pass


# SubConstraint.type (hkConstraintType) → (plain constraint block class name,
# descriptor attribute name on both SubConstraint and the plain block).
_MALLEABLE_INNER = {
    0: ('bhkBallAndSocketConstraint', 'ball_and_socket'),
    1: ('bhkHingeConstraint', 'hinge'),
    2: ('bhkLimitedHingeConstraint', 'limited_hinge'),
    6: ('bhkPrismaticConstraint', 'prismatic'),
    7: ('bhkRagdollConstraint', 'ragdoll'),
    8: ('bhkStiffSpringConstraint', 'stiff_spring'),
}


def _demote_malleable_constraints(data):
    """Replace every bhkMalleableConstraint with a plain constraint of its inner type.

    Vanilla Skyrim ships ZERO bhkMalleableConstraint meshes (0 of 17,216 —
    binary block-type grep), so the engine path for them is untested; the
    inner descriptor as a plain constraint is the vanilla-conformant form.
    The malleable strength/tau/damping wrapper data is dropped.

    Returns the list of newly created constraint blocks (they are referenced
    from the rigid bodies but not yet present in data.blocks).
    """
    new_blocks = []
    replacements = {}
    for block in data.blocks:
        if not isinstance(block, NifFormat.bhkMalleableConstraint):
            continue
        sub = block.sub_constraint
        inner = _MALLEABLE_INNER.get(sub.type)
        if inner is None:
            continue
        cls_name, desc_attr = inner
        new_block = getattr(NifFormat, cls_name)()
        # bhkConstraint header: entities + priority come from the outer block
        # (SubConstraint's own entity list is "usually NONE").
        new_block.num_entities = block.num_entities
        new_block.entities.update_size()
        for i in range(block.num_entities):
            new_block.entities[i] = block.entities[i]
        new_block.priority = block.priority
        _copy_struct(getattr(sub, desc_attr), getattr(new_block, desc_attr))
        replacements[block] = new_block
        new_blocks.append(new_block)

    if replacements:
        # Swap references in every rigid body's constraints array.
        for block in data.blocks:
            constraints = getattr(block, 'constraints', None)
            if constraints is None:
                continue
            for i, c in enumerate(constraints):
                if c in replacements:
                    constraints[i] = replacements[c]
    return new_blocks


def _fix_limited_hinge(d):
    """Skyrim-format fixes for a LimitedHingeDescriptor (pivots already scaled).

    1. Missing perp_2_axle_in_b_1: Oblivion's LimitedHingeDescriptor does not
       have perp_2_axle_in_b_1; Skyrim does.  Leaving it zero causes the sign
       to spawn at a wrong tilt.  Derived as: perp_b2 × axle_b (normalised).
       Vanilla Skyrim stores w=-1 on perp_2_axle_in_a_1 and perp_2_axle_in_b_1.

    2. Clamp max_friction to Skyrim range.
       Oblivion stores max_friction=3.0; Skyrim signs use 0.01.
       At 3.0 the hinge has enough rotational friction to lock the sign
       at any angle against gravity, so it stops at a wrong tilt instead
       of swinging freely back to vertical.
    """
    perp_b1 = getattr(d, 'perp_2_axle_in_b_1', None)
    if perp_b1 is not None:
        _vec_set_unit(perp_b1, _vec_cross(d.perp_2_axle_in_b_2, d.axle_b), w=-1.0)

    perp_a1 = getattr(d, 'perp_2_axle_in_a_1', None)
    if perp_a1 is not None:
        perp_a1.w = -1.0

    if d.max_friction > 0.5:
        d.max_friction = 0.01


def _fix_ragdoll(d):
    """Derive the Skyrim-only RagdollDescriptor motor axes and clamp friction.

    In the Skyrim (Havok 2010) layout twist/plane/motor are the three columns
    of an orthonormal basis — motor = twist × plane (verified on vanilla
    desecratedimperial.nif: twist=(1,0,0), plane=(0,1,0), motor=(0,0,1)).
    Oblivion's layout has no motor fields, so PyFFI leaves them zero, which
    ships a singular constraint basis.

    max_friction: Oblivion chain/trap ragdoll constraints store 10.0; at that
    value the joint has enough rotational friction to lock solid — chains and
    swinging traps LOOK fine but never move when touched.  Vanilla Skyrim prop
    ragdoll constraints use 0.01 (desecratedimperial.nif), the same value the
    limited-hinge clamp already uses (the tavern-sign fix).
    """
    if d.max_friction > 0.5:
        d.max_friction = 0.01
    for twist_name, plane_name, motor_name in (('twist_a', 'plane_a', 'motor_a'),
                                               ('twist_b', 'plane_b', 'motor_b')):
        motor = getattr(d, motor_name, None)
        if motor is None:
            continue
        if math.sqrt(motor.x ** 2 + motor.y ** 2 + motor.z ** 2) > 1e-6:
            continue  # already populated
        _vec_set_unit(motor, _vec_cross(getattr(d, twist_name),
                                        getattr(d, plane_name)))


def _fix_hinge(d):
    """Derive the Skyrim-only HingeDescriptor fields.

    Oblivion stores only pivot_a, perp_a1, perp_a2, pivot_b, axle_b.  Skyrim
    additionally needs axle_a and perp_2_axle_in_b_1/2; left zero the hinge
    axis is degenerate.  Frame convention (per nif.xml): perp2 = axle × perp1,
    so axle_a = perp_a1 × perp_a2.  For the B side only axle_b is known; any
    orthonormal complement works because a plain hinge has no angle limits —
    build perp_b1 by Gram-Schmidt from perp_a1, then perp_b2 = axle_b × perp_b1.
    """
    axle_a = getattr(d, 'axle_a', None)
    if axle_a is not None:
        L = math.sqrt(axle_a.x ** 2 + axle_a.y ** 2 + axle_a.z ** 2)
        if L < 1e-6:
            _vec_set_unit(axle_a, _vec_cross(d.perp_2_axle_in_a_1,
                                             d.perp_2_axle_in_a_2))

    perp_b1 = getattr(d, 'perp_2_axle_in_b_1', None)
    perp_b2 = getattr(d, 'perp_2_axle_in_b_2', None)
    if perp_b1 is None or perp_b2 is None:
        return
    L1 = math.sqrt(perp_b1.x ** 2 + perp_b1.y ** 2 + perp_b1.z ** 2)
    L2 = math.sqrt(perp_b2.x ** 2 + perp_b2.y ** 2 + perp_b2.z ** 2)
    if L1 > 1e-6 and L2 > 1e-6:
        return  # already populated
    ab = d.axle_b
    # Reference vector not parallel to axle_b
    ref = d.perp_2_axle_in_a_1
    rx, ry, rz = ref.x, ref.y, ref.z
    dot = rx * ab.x + ry * ab.y + rz * ab.z
    px, py, pz = rx - dot * ab.x, ry - dot * ab.y, rz - dot * ab.z
    if px * px + py * py + pz * pz < 1e-9:
        # perp_a1 parallel to axle_b — fall back to whichever world axis isn't
        for rx, ry, rz in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
            dot = rx * ab.x + ry * ab.y + rz * ab.z
            px, py, pz = rx - dot * ab.x, ry - dot * ab.y, rz - dot * ab.z
            if px * px + py * py + pz * pz > 1e-9:
                break
    _vec_set_unit(perp_b1, (px, py, pz))
    _vec_set_unit(perp_b2, _vec_cross(ab, perp_b1))


def _fix_prismatic(d):
    """Best-effort Skyrim fields for a PrismaticDescriptor.

    Oblivion stores pivot_a, pivot_b, sliding_b, plane_b (+ a rotation).
    Skyrim additionally wants sliding_a/plane_a (the same axes expressed in
    body A's frame).  Without the body world transforms at this point, copy
    the B-frame axes — constrained prop pairs sit at near-identity relative
    rotation in practice.  Sliding distances are lengths → scale by 0.1.
    NOTE: vanilla Skyrim ships zero bhkPrismaticConstraint meshes, so this
    path is inherently untested by Bethesda.
    """
    for src_name, dst_name in (('sliding_b', 'sliding_a'), ('plane_b', 'plane_a')):
        src = getattr(d, src_name, None)
        dst = getattr(d, dst_name, None)
        if src is None or dst is None:
            continue
        if math.sqrt(dst.x ** 2 + dst.y ** 2 + dst.z ** 2) < 1e-6:
            dst.x = src.x; dst.y = src.y; dst.z = src.z; dst.w = src.w
    for attr in ('min_distance', 'max_distance'):
        v = getattr(d, attr, None)
        if v is not None:
            setattr(d, attr, v * _HAVOK_SCALE)


def _constraint_descriptors(block):
    """Yield (kind, descriptor) for a plain bhkConstraint block."""
    for kind in ('limited_hinge', 'ragdoll', 'hinge', 'prismatic',
                 'stiff_spring', 'ball_and_socket'):
        d = getattr(block, kind, None)
        if d is not None:
            yield kind, d


def scale_constraint_pivots(data):
    """Fix Havok constraint data for Oblivion → Skyrim conversion.

    Applies to EVERY constraint descriptor type (limited hinge, ragdoll,
    hinge, prismatic, stiff spring, ball-and-socket):

    1. Malleable demotion: bhkMalleableConstraint (never shipped by vanilla
       Skyrim) is replaced by a plain constraint of its inner type.
    2. Pivot scale: pivot_a/pivot_b are Oblivion Havok-space positions and
       must be scaled by _HAVOK_SCALE (0.1).  Axis vectors are unit vectors
       and must NOT be scaled.  Stiff-spring length and prismatic sliding
       distances are lengths and scale too.
    3. Skyrim-only fields Oblivion has no source for are derived
       (limited hinge perp_b1, hinge axle_a/perp_b1/perp_b2).
    4. broadphaseType=10 for dynamic constrained bodies.  (Inertia is NOT
       rescaled here — _convert_collision and _convert_blend_collision both
       already apply the full ×0.01; an extra ×0.1 here left every
       constrained body's inertia 10× too small.)

    bhkRigidBodyT is kept as-is: Skyrim uses bhkRigidBodyT for constrained
    sign bodies (confirmed in vanilla signfourshieldstavern01.nif).  The T
    offset is body-local relative to the owning NiNode, which is already
    correct after _convert_collision scales it by _HAVOK_SCALE.
    """
    constraint_blocks = [b for b in data.blocks
                         if isinstance(b, NifFormat.bhkConstraint)]
    constraint_blocks += _demote_malleable_constraints(data)

    for block in constraint_blocks:
        if isinstance(block, NifFormat.bhkMalleableConstraint):
            continue  # replaced by its demoted inner constraint
        for kind, d in _constraint_descriptors(block):
            # Scale pivot positions (xyz only; w is unused padding).
            for pivot_attr in ('pivot_a', 'pivot_b'):
                pivot = getattr(d, pivot_attr, None)
                if pivot is not None:
                    pivot.x *= _HAVOK_SCALE
                    pivot.y *= _HAVOK_SCALE
                    pivot.z *= _HAVOK_SCALE
            if kind == 'limited_hinge':
                _fix_limited_hinge(d)
            elif kind == 'ragdoll':
                _fix_ragdoll(d)
            elif kind == 'hinge':
                _fix_hinge(d)
            elif kind == 'prismatic':
                _fix_prismatic(d)
            elif kind == 'stiff_spring':
                length = getattr(d, 'length', None)
                if length is not None:
                    d.length = length * _HAVOK_SCALE

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


_OB_GAME_UNITS_PER_HAVOK = 7.0  # Oblivion: 1 Havok unit = 7 game units


def _m3_from_quat_xyzw(x, y, z, w):
    """Unit quaternion (x,y,z,w) → 3x3 column-vector rotation matrix.

    Same formula as NifSkope's Matrix::fromQuat, which is how the engine
    interprets bhkRigidBodyT.rotation.
    """
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z),     2 * (x * z + w * y)],
        [2 * (x * y + w * z),     1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y),     2 * (y * z + w * x),     1 - 2 * (x * x + y * y)],
    ]


def _quat_xyzw_from_m3(m):
    """3x3 column-vector rotation matrix → unit quaternion (x,y,z,w).

    Shoemake branches handle 180° rotations (trace = -1, w = 0), which are
    common on Oblivion architecture roots.
    """
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    return x, y, z, w


def bake_node_transform_into_body(coll_obj, node, extra_z=0.0):
    """Compose a node's local transform L=(R,T,s) into its collision body.

    extra_z: additional model-space z translation carried by the wrapper but
    not present on the node itself (the furniture origin shift — the importer
    lowers the REFRs by the same amount, so the collision must rise with the
    geometry or it ends up sunk by the shift).

    Used when the root's transform is about to be zeroed (rotation-wrap pass):
    the engine places a root collision body at REFR ∘ bodyT, so the vanishing
    root transform must be absorbed into bodyT or the collision ends up
    rotated/offset relative to the mesh (stackhallentrance01: 90° off).
    Collision must stay on the ROOT node — attaching it to the inner wrapper
    aligns it too, but intermittently crashes hkpCollisionDispatcher.

    NIF matrices act on row vectors under PyFFI's m_ij naming; the engine
    (and NifSkope) reads the same file bytes as a column-vector matrix, so the
    column matrix is the m_ij-named transpose.  bhkRigidBody(T) translation is
    in Oblivion Havok units at this stage (game / 7); the Skyrim rescale
    (× _HAVOK_SCALE) happens later in _convert_collision.

    bhkRigidBody (non-T) carries no transform of its own, so it is promoted to
    bhkRigidBodyT (identical field layout — PyFFI class swap) to hold L.
    Returns True if the body was modified.
    """
    body = getattr(coll_obj, 'body', None)
    if body is None or not isinstance(body, NifFormat.bhkRigidBody):
        return False

    r = node.rotation
    R = [[r.m_11, r.m_21, r.m_31],
         [r.m_12, r.m_22, r.m_32],
         [r.m_13, r.m_23, r.m_33]]  # column-vector convention
    s = node.scale
    T = (node.translation.x / _OB_GAME_UNITS_PER_HAVOK,
         node.translation.y / _OB_GAME_UNITS_PER_HAVOK,
         (node.translation.z + extra_z) / _OB_GAME_UNITS_PER_HAVOK)

    if isinstance(body, NifFormat.bhkRigidBodyT):
        q = body.rotation
        M_old = _m3_from_quat_xyzw(q.x, q.y, q.z, q.w)
        t_old = (body.translation.x, body.translation.y, body.translation.z)
    else:
        body.__class__ = NifFormat.bhkRigidBodyT
        M_old = _m3_from_quat_xyzw(0.0, 0.0, 0.0, 1.0)
        t_old = (0.0, 0.0, 0.0)

    # bodyT' = L ∘ bodyT:  M' = R·M,  t' = R·(t·s) + T
    M_new = [[sum(R[i][k] * M_old[k][j] for k in range(3)) for j in range(3)]
             for i in range(3)]
    t_new = [sum(R[i][k] * t_old[k] * s for k in range(3)) + T[i]
             for i in range(3)]
    x, y, z, w = _quat_xyzw_from_m3(M_new)

    body.rotation.x = x
    body.rotation.y = y
    body.rotation.z = z
    body.rotation.w = w
    body.translation.x = t_new[0]
    body.translation.y = t_new[1]
    body.translation.z = t_new[2]
    return True


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


def _collect_psys_referenced_nodes(root):
    """Return the set of id()s of nodes referenced by particle-system modifiers
    (NiPSysGravityModifier.gravity_object, *Emitter.emitter_object).

    These are empty marker NiNodes (e.g. 'Gravity', 'SparkGravity') that the
    particle physics point at; removing them dangles the reference and breaks
    the simulation (invisible particles)."""
    refs = set()
    for block in getattr(root, 'tree', lambda: [])():
        tn = type(block).__name__
        if tn == 'NiPSysGravityModifier':
            go = getattr(block, 'gravity_object', None)
            if go is not None:
                refs.add(id(go))
        elif tn.endswith('Emitter') and 'Ctlr' not in tn:
            eo = getattr(block, 'emitter_object', None)
            if eo is not None:
                refs.add(id(eo))
    return refs


def remove_empty_collision_nodes(root):
    """Remove empty NiNode children that were collision containers.

    After hoisting collision to root, the original NiNode child (e.g.
    'Collision045') is left empty: no children, no collision_object.  Skyrim
    processes every child of BSFadeNode and an unexpected empty NiNode can
    trigger crashes.  This function compacts the children array in-place.

    Nodes referenced by particle-system modifiers (Gravity/emitter objects)
    are PRESERVED even when empty — dropping them dangles the reference and the
    particle system stops rendering.
    """
    if not hasattr(root, 'children') or not isinstance(root, NifFormat.NiNode):
        return
    protected = _collect_psys_referenced_nodes(root)
    keep = []
    for child in root.children:
        if child is None:
            continue
        # Remove bare NiNodes with no children and no collision — unless a
        # particle modifier references them.
        if (type(child).__name__ in ('NiNode',) and
                child.num_children == 0 and
                getattr(child, 'collision_object', None) is None and
                id(child) not in protected):
            continue
        keep.append(child)
    if len(keep) < root.num_children:
        root.num_children = len(keep)
        root.children.update_size()
        for i, c in enumerate(keep):
            root.children[i] = c
