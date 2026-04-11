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
NIF_FLAGS = 14  # Standard Skyrim NiAVObject flags (SelectiveUpdate bits 1-3)

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
        # Inertia tensor: Oblivion stores inertia in "Oblivion Havok" units where
        # 1 game unit = 1/7 Havok unit.  Skyrim Havok is 1 game unit = 1/70 Havok
        # unit, so all lengths scale by _HAVOK_SCALE = 0.1.  However, Skyrim's
        # Havok 2010 runtime normalises inertia by the BODY scale internally, so
        # only one power of _HAVOK_SCALE is needed (not two).
        # Empirical check: applying _HAVOK_SCALE (0.1) produces I/m ratios of
        # 0.017–0.043, closely matching vanilla Skyrim clutter (0.004–0.04).
        # Applying _HAVOK_SCALE^2 (0.01) gives values 10× too small.
        _INERTIA_SCALE = _HAVOK_SCALE  # 0.1
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
    rb.shape = _convert_shape(rb.shape, node)

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

        # 4. broadphaseType=10 for dynamic constrained bodies.
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
