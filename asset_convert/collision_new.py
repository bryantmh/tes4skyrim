"""Oblivion → Skyrim Havok collision conversion.

Extracts collision geometry from Oblivion bhkNiTriStripsShape and converts to
Skyrim-compatible Havok format using bhkPackedNiTriStripsShape + bhkMoppBvTreeShape.
Uses PyFFI's built-in Havok MOPP code generator (update_mopp_welding) instead of
the legacy MOPP_RL.exe, which produces Oblivion-era MOPP data missing the
build_type field.

All shape vertices are scaled by _HAVOK_SCALE (0.1) to convert from Oblivion's
game-unit based Havok coordinates to Skyrim's smaller Havok coordinate system.
"""

import logging
import math
import time

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401

from pyffi.formats.nif import NifFormat

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HAVOK_SCALE = 0.1

# Inertia tensor scale factor: I ∝ m × d².  Collision shapes are scaled by
# _HAVOK_SCALE (0.1), so distances are 10× smaller → inertia scales by 0.01.
_INERTIA_SCALE = _HAVOK_SCALE * _HAVOK_SCALE  # 0.01

# Oblivion bhkNiTriStripsShape stores vertices at 7× Havok unit scale.
_OB_STRIPS_SCALE = 7.0

# Collision object flag presets
_COLL_FLAGS_STATIC = 129     # Standard static collision
_COLL_FLAGS_ANIMATED = 137   # 0x89 = ACTIVE | D_ANIMATED | bit 7

# Phantom/trigger collision types that Skyrim does not support.
# Presence of these causes red-triangle (failed load) or null-deref crashes.
_PHANTOM_COLL_TYPES = frozenset({
    'bhkSPCollisionObject',
    'bhkNPCollisionObject',
    'bhkBlendCollisionObject',
})


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
            a, b, c = pts[i - 2], pts[i - 1], pts[i]
            if a != b and b != c and c != a:
                triangles.append((a, c, b) if flip else (a, b, c))
            flip = not flip
    return triangles


def _find_normal(verts, a, b, c):
    """Return normalised face normal for triangle (a, b, c) in vertex list."""
    va, vb, vc = verts[a], verts[b], verts[c]
    ux, uy, uz = vb[0] - va[0], vb[1] - va[1], vb[2] - va[2]
    vx, vy, vz = vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    mag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if mag > 0:
        nx /= mag
        ny /= mag
        nz /= mag
    return nx, ny, nz


# ---------------------------------------------------------------------------
# Shape conversion
# ---------------------------------------------------------------------------

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

        all_verts = []
        all_triangles = []
        for sd in strips_list:
            offset = len(all_verts)
            inv_scale = 1.0 / _OB_STRIPS_SCALE
            block_verts = [(v.x * inv_scale, v.y * inv_scale, v.z * inv_scale)
                           for v in sd.vertices]
            all_verts.extend(block_verts)
            block_tris = _triangulate_strips(sd)
            all_triangles.extend(
                (a + offset, b + offset, c + offset) for a, b, c in block_tris
            )

        if not all_triangles:
            return None

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
        packed.sub_shapes[0].layer = 1  # LAYER_STATIC
        packed.sub_shapes[0].num_vertices = len(all_verts)
        packed.sub_shapes[0].material = bhk_strips.material
        packed.scale.x = 1.0
        packed.scale.y = 1.0
        packed.scale.z = 1.0
        packed.unknown_float_1 = 0.1
        packed.unknown_float_3 = 0.1
        packed.data = hkdata
        return packed
    except Exception:
        _log.debug("_ni_strips_to_packed failed", exc_info=True)
        return None



# ---------------------------------------------------------------------------
# Rigid body conversion
# ---------------------------------------------------------------------------

def _generate_mopp(packed):
    """Wrap a bhkPackedNiTriStripsShape in bhkMoppBvTreeShape with valid MOPP data.

    Uses PyFFI's built-in Havok Mopper DLL via update_mopp_welding().
    Sets build_type=1 (PC, required for Skyrim SE).
    Returns the bhkMoppBvTreeShape, or None on failure.
    """
    try:
        mopp = NifFormat.bhkMoppBvTreeShape()
        mopp.shape = packed
        mopp.material = packed.sub_shapes[0].material if packed.num_sub_shapes > 0 else 0
        mopp.update_mopp_welding()
        if hasattr(mopp, 'build_type'):
            mopp.build_type = 1
        return mopp
    except Exception:
        _log.debug("_generate_mopp failed", exc_info=True)
        return None


def _convert_rigid_body(rb):
    """Set Skyrim-compatible rigid body flags (in-place).

    Values match the proven working conversion from commit a4415e4.
    Only fields with KNOWN correct Skyrim values are set.
    Fields like unknown_int_1, unknown_int_2, unknown_3_ints are left at
    their default values (they are runtime padding that Skyrim initialises).
    """
    rb.unknown_byte = 116
    # Gravity/time factors: 1.0 or Havok ignores the body
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
    # unknown_6_shorts: Skyrim-specific values.
    # [2:4] = pointer field — MUST be 0 or Skyrim crashes
    # [0:2] and [4:6] = padding/response values from working conversion
    rb.unknown_6_shorts[0] = 20704
    rb.unknown_6_shorts[1] = 9444
    rb.unknown_6_shorts[2] = 0  # MUST be 0 — Skyrim interprets as pointer
    rb.unknown_6_shorts[3] = 0  # MUST be 0 — Skyrim interprets as pointer
    rb.unknown_6_shorts[4] = 60417
    rb.unknown_6_shorts[5] = 65535


# ---------------------------------------------------------------------------
# Recursive shape conversion
# ---------------------------------------------------------------------------

def _convert_shape(shape, root_node):
    """Recursively convert an Oblivion Havok shape to Skyrim format.

    Scales all geometry/dimensions by _HAVOK_SCALE (0.1).
    bhkNiTriStripsShape is converted to bhkPackedNiTriStripsShape + MOPP.
    Returns the (possibly replaced) shape.
    """
    if shape is None:
        return None

    if isinstance(shape, NifFormat.bhkBoxShape):
        shape.dimensions.x *= _HAVOK_SCALE
        shape.dimensions.y *= _HAVOK_SCALE
        shape.dimensions.z *= _HAVOK_SCALE
        shape.radius *= _HAVOK_SCALE
        shape.minimum_size = min(shape.dimensions.x, shape.dimensions.y,
                                 shape.dimensions.z)
        return shape

    if isinstance(shape, NifFormat.bhkSphereShape):
        shape.radius *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkCapsuleShape):
        shape.radius *= _HAVOK_SCALE
        shape.radius_1 *= _HAVOK_SCALE
        shape.radius_2 *= _HAVOK_SCALE
        shape.first_point.x *= _HAVOK_SCALE
        shape.first_point.y *= _HAVOK_SCALE
        shape.first_point.z *= _HAVOK_SCALE
        shape.second_point.x *= _HAVOK_SCALE
        shape.second_point.y *= _HAVOK_SCALE
        shape.second_point.z *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkMultiSphereShape):
        for sphere in shape.spheres:
            sphere.center.x *= _HAVOK_SCALE
            sphere.center.y *= _HAVOK_SCALE
            sphere.center.z *= _HAVOK_SCALE
            sphere.radius *= _HAVOK_SCALE
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
        # Convert strips → packed, then generate MOPP wrapping.
        packed = _ni_strips_to_packed(shape)
        if packed is None:
            return shape  # keep original on failure
        mopp = _generate_mopp(packed)
        if mopp is not None:
            return mopp
        # MOPP generation failed — return bare packed (no collision but no crash)
        return packed

    if isinstance(shape, NifFormat.bhkMoppBvTreeShape):
        # Convert the inner shape first.
        converted = _convert_shape(shape.shape, root_node)
        if isinstance(converted, NifFormat.bhkMoppBvTreeShape):
            # Inner conversion already produced a MOPP — use it directly.
            return converted
        if isinstance(converted, NifFormat.bhkPackedNiTriStripsShape):
            # Regenerate MOPP for the converted packed shape.
            mopp = _generate_mopp(converted)
            if mopp is not None:
                return mopp
            # MOPP failed — return bare packed. NEVER return the old
            # bhkMoppBvTreeShape with stale Oblivion MOPP data.
            return converted
        # Unknown inner shape — keep the MOPP wrapper but scale origin.
        shape.shape = converted
        shape.origin.x *= _HAVOK_SCALE
        shape.origin.y *= _HAVOK_SCALE
        shape.origin.z *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        # Already packed — generate MOPP for it.
        mopp = _generate_mopp(shape)
        if mopp is not None:
            return mopp
        return shape

    # Unknown shape — return as-is
    return shape


# ---------------------------------------------------------------------------
# Full collision conversion per-node
# ---------------------------------------------------------------------------

def convert_collision(node, actual_root=None):
    """Convert collision on a NiNode from Oblivion to Skyrim Havok format.

    Modifies node.collision_object in-place.
    actual_root: the NIF's top-level root node, used as target for
    bhkCompressedMeshShape so Skyrim reads the correct world transform.
    """
    if not hasattr(node, 'collision_object') or node.collision_object is None:
        return

    # Strip unsupported phantom/trigger collision types
    if node.collision_object.__class__.__name__ in _PHANTOM_COLL_TYPES:
        node.collision_object = None
        return

    coll_obj = node.collision_object
    coll_obj.flags = _COLL_FLAGS_STATIC

    rb = coll_obj.body if hasattr(coll_obj, 'body') else None
    if rb is None:
        return

    if isinstance(rb, NifFormat.bhkSimpleShapePhantom):
        rb.shape = _convert_shape(rb.shape, node)
        return

    # Scale rigid body translation
    if isinstance(rb, NifFormat.bhkRigidBodyT):
        rb.translation.x *= _HAVOK_SCALE
        rb.translation.y *= _HAVOK_SCALE
        rb.translation.z *= _HAVOK_SCALE
    rb.center.x *= _HAVOK_SCALE
    rb.center.y *= _HAVOK_SCALE
    rb.center.z *= _HAVOK_SCALE

    _convert_rigid_body(rb)

    if rb.mass == 0:
        if rb.motion_system == 6:  # Keyframed (animated doors etc.)
            # Preserve keyframed motion; only set the physics coefficients.
            rb.deactivator_type = 1
            rb.quality_type = 3     # MO_QUAL_KEYFRAMED
            rb.solver_deactivation = 1
        else:
            # Static object — quality_type=0 (MO_QUAL_INVALID = auto-detect)
            # matches ALL vanilla Skyrim architecture NIFs.
            rb.motion_system = 5    # SYS_BOX_STABILIZED
            rb.deactivator_type = 1
            rb.quality_type = 0     # MO_QUAL_INVALID (auto-detect)
            rb.solver_deactivation = 1
        rb.friction = 0.50
        rb.restitution = 0.40
        rb.linear_damping = 0.0996
        rb.angular_damping = 0.0498
        rb.max_linear_velocity = 104.4
        rb.max_angular_velocity = 31.57
    else:
        # Dynamic/clutter — keep Oblivion mass as-is (already in Skyrim range).
        # Scale inertia tensor by _HAVOK_SCALE² = 0.01.
        rb.inertia.m_11 *= _INERTIA_SCALE
        rb.inertia.m_12 *= _INERTIA_SCALE
        rb.inertia.m_13 *= _INERTIA_SCALE
        rb.inertia.m_21 *= _INERTIA_SCALE
        rb.inertia.m_22 *= _INERTIA_SCALE
        rb.inertia.m_23 *= _INERTIA_SCALE
        rb.inertia.m_31 *= _INERTIA_SCALE
        rb.inertia.m_32 *= _INERTIA_SCALE
        rb.inertia.m_33 *= _INERTIA_SCALE
        rb.motion_system = 3    # MO_SYS_SPHERE_INERTIA
        rb.quality_type = 4     # MO_QUAL_MOVING
        rb.deactivator_type = 1
        rb.solver_deactivation = 2
        rb.rolling_friction_multiplier = 0
        rb.linear_damping = 0.0996
        rb.angular_damping = 0.0498
        rb.friction = 0.50
        rb.restitution = 0.40
        rb.max_linear_velocity = 104.4
        rb.max_angular_velocity = 31.57

    shape_target = actual_root if actual_root is not None else node
    rb.shape = _convert_shape(rb.shape, shape_target)

    # Scale constraint pivots by _HAVOK_SCALE.
    # Oblivion constraints store pivots in game-unit Havok scale;
    # Skyrim expects them in the smaller 0.1× Havok coordinate system.
    for i in range(rb.num_constraints):
        _scale_constraint(rb.constraints[i])


def _scale_constraint(constraint):
    """Scale Havok constraint pivots/axes by _HAVOK_SCALE."""
    if constraint is None:
        return

    if isinstance(constraint, NifFormat.bhkLimitedHingeConstraint):
        desc = constraint.limited_hinge
        desc.pivot_a.x *= _HAVOK_SCALE
        desc.pivot_a.y *= _HAVOK_SCALE
        desc.pivot_a.z *= _HAVOK_SCALE
        desc.pivot_b.x *= _HAVOK_SCALE
        desc.pivot_b.y *= _HAVOK_SCALE
        desc.pivot_b.z *= _HAVOK_SCALE

    elif isinstance(constraint, NifFormat.bhkHingeConstraint):
        desc = constraint.hinge
        desc.pivot_a.x *= _HAVOK_SCALE
        desc.pivot_a.y *= _HAVOK_SCALE
        desc.pivot_a.z *= _HAVOK_SCALE
        desc.pivot_b.x *= _HAVOK_SCALE
        desc.pivot_b.y *= _HAVOK_SCALE
        desc.pivot_b.z *= _HAVOK_SCALE

    elif isinstance(constraint, NifFormat.bhkRagdollConstraint):
        desc = constraint.ragdoll
        desc.pivot_a.x *= _HAVOK_SCALE
        desc.pivot_a.y *= _HAVOK_SCALE
        desc.pivot_a.z *= _HAVOK_SCALE
        desc.pivot_b.x *= _HAVOK_SCALE
        desc.pivot_b.y *= _HAVOK_SCALE
        desc.pivot_b.z *= _HAVOK_SCALE

    elif isinstance(constraint, NifFormat.bhkMalleableConstraint):
        desc = constraint.malleable
        if hasattr(desc, 'limited_hinge'):
            h = desc.limited_hinge
            h.pivot_a.x *= _HAVOK_SCALE
            h.pivot_a.y *= _HAVOK_SCALE
            h.pivot_a.z *= _HAVOK_SCALE
            h.pivot_b.x *= _HAVOK_SCALE
            h.pivot_b.y *= _HAVOK_SCALE
            h.pivot_b.z *= _HAVOK_SCALE


def convert_all_collisions(node, actual_root=None):
    """Recursively convert collision objects on every node in the tree.

    Skyrim requires ALL bhkCollisionObject instances to use Skyrim Havok format.
    Unconverted child collisions (e.g. animated display-case lids) retain
    Oblivion-format fields that crash Skyrim.
    """
    if node is None or not isinstance(node, NifFormat.NiNode):
        return
    if actual_root is None:
        actual_root = node
    convert_collision(node, actual_root)
    if hasattr(node, 'children'):
        for child in node.children:
            convert_all_collisions(child, actual_root)


# ---------------------------------------------------------------------------
# Collision hoisting (child → root)
# ---------------------------------------------------------------------------

def offset_collision_shape_verts(co, ox, oy, oz):
    """Add (ox, oy, oz) game-unit offset to collision shape vertices.

    In Oblivion bhkNiTriStripsShape the vertices are stored at game-unit scale
    (×7 Havok units).  Adding the game-unit offset bakes a child NiNode's
    world-space translation into the shape so collision stays correct after
    hoisting to the root node.

    Traverses: bhkCollisionObject → body → shape → (bhkMopp →) bhkNiTriStripsShape
    """
    rb = getattr(co, 'body', None)
    if rb is None:
        return
    shape = getattr(rb, 'shape', None)
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
    """Find collision on a descendant NiNode and move it to root.

    Skyrim requires bhkCollisionObject on the root BSFadeNode.
    Bakes child NiNode translation into collision vertices when needed.
    Returns True if collision was hoisted.
    """
    def _find_and_clear(node):
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
        if ox != 0.0 or oy != 0.0 or oz != 0.0:
            offset_collision_shape_verts(co, ox, oy, oz)
        return True
    return False


def remove_empty_collision_nodes(root):
    """Remove empty NiNode children left after collision hoisting."""
    if not hasattr(root, 'children') or not isinstance(root, NifFormat.NiNode):
        return
    keep = []
    for child in root.children:
        if child is None:
            continue
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
