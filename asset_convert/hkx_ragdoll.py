"""Ragdoll stage for creature skeleton.hkx.

Converts the Oblivion skeleton.nif ragdoll (bhkBlendCollisionObject rigid
bodies + bhkRagdollConstraint/bhkLimitedHingeConstraint on the bone nodes)
into the vanilla Skyrim skeleton.hkx ragdoll anatomy (layouts mirrored from
the hkxcmd XML dump of the vanilla deer skeleton.hkx):

  hkaSkeleton (ragdoll)     bones "Ragdoll_<bone>", subset of the anim
                            skeleton (bones that carry rigid bodies),
                            parent-before-child
  2x hkaSkeletonMapper      anim→ragdoll and ragdoll→anim (identity
                            aFromBTransform: our ragdoll bone frames are
                            DEFINED to coincide with the anim bone frames —
                            body translation offsets are folded into the
                            shape vertices / COM instead)
  hkpPhysicsData/System     shared hkpRigidBody set + one
                            hkpConstraintInstance per joint
  hkaRagdollInstance        second constraint-instance set (vanilla
                            duplicates the constraint data per owner)

Unit/convention notes (all verified against the vanilla deer dump):
  - skeleton.hkx works in GAME units (capsule radius ~24), NOT Havok metres.
    Oblivion nif bhk data is in Oblivion Havok units (game/7) → ×7.
  - Inertia scales by 7² = 49; hkpMotion stores inertiaAndMassInv =
    (1/I, 1/I, 1/I, 1/mass).
  - hkTransform XML prints the ROW-convention rotation matrix rows (same
    convention as NIF matrices) + translation; hkQuaternions equal
    _mat33_to_quat_xyzw of the NIF matrix.
  - Constraint transformA/B rows = (twist, plane, twist×plane) for ragdoll
    joints, (axle, perp1, perp2) for hinges, expressed in each entity's
    local frame; translation = pivot.
  - Constraint entities order = (child body, parent body) — the nif stores
    the constraint on the child body with entities[0] = itself.
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asset_convert import pyffi_monkey_patch  # noqa: F401
from asset_convert.hkx_xml import fmt_vec
from asset_convert import hkx_xml
from pyffi.formats.nif import NifFormat

hkx_xml.SIGNATURES.update({
    'hkaSkeletonMapper': '0x12df42a5',
    'hkpCapsuleShape': '0xdd0b1fd3',
    'hkpRigidBody': '0x75f8d805',
    'hkpRagdollConstraintData': '0x8fb5dd29',
    'hkpLimitedHingeConstraintData': '0x7c15bb6b',
    'hkpConstraintInstance': '0x34eba5f',
    'hkpPositionConstraintMotor': '0x748fb303',
    'hkaRagdollInstance': '0x154948e8',
    'hkpPhysicsSystem': '0xff724c17',
    'hkpPhysicsData': '0xc2a461e4',
    'hkMemoryResourceContainer': '0x4762f92a',
})

_OB_TO_GAME = 7.0          # Oblivion Havok units → game units
_HUGE = '18446726481523507000.000000'
_MAX_IMPULSE = '340282001837565600000000000000000000000.000000'


# ---------------------------------------------------------------------------
# Extraction from the Oblivion skeleton.nif
# ---------------------------------------------------------------------------

def _quat_to_mat_row(q):
    """xyzw quat → row-convention 3x3 (inverse of _mat33_to_quat_xyzw)."""
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
        [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
        [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)],
    ])


def _mat_row_to_quat(m):
    """Row-convention 3x3 → xyzw quat (Shepperd)."""
    m00, m01, m02 = m[0]
    m10, m11, m12 = m[1]
    m20, m21, m22 = m[2]
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m12 - m21) / s
        y = (m20 - m02) / s
        z = (m01 - m10) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m12 - m21) / s
        x = 0.25 * s
        y = (m10 + m01) / s
        z = (m20 + m02) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m20 - m02) / s
        x = (m10 + m01) / s
        y = 0.25 * s
        z = (m21 + m12) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m01 - m10) / s
        x = (m20 + m02) / s
        y = (m21 + m12) / s
        z = 0.25 * s
    n = math.sqrt(w * w + x * x + y * y + z * z)
    return (x / n, y / n, z / n, w / n)


def _bone_worlds(bones):
    """World (rotation 3x3 row-convention, translation vec3) per anim bone."""
    worlds = []
    for b in bones:
        R = _quat_to_mat_row(b.quat_xyzw) * b.scale
        t = np.array(b.translation, dtype=float)
        if b.parent < 0:
            worlds.append((R, t))
        else:
            Rp, tp = worlds[b.parent]
            worlds.append((R @ Rp, t @ Rp + tp))
    return worlds


def _v4(v, scale=1.0):
    return np.array([v.x * scale, v.y * scale, v.z * scale], dtype=float)


class RagdollPart:
    def __init__(self):
        self.anim_index = -1
        self.parent = -1            # ragdoll part index
        self.name = ''
        self.mass = 1.0
        self.inertia = 1.0          # max diagonal, game units
        self.com = np.zeros(3)      # bone-local, game units
        self.shape = None           # (radius, vA, vB) capsule, bone-local
        self.constraint = None      # (kind, descriptor dict) joining to parent


def _capsule_from_shape(shape, offset):
    """Any Oblivion bhk shape → (radius, vA, vB) capsule in bone-local game
    units (offset = folded body translation)."""
    name = shape.__class__.__name__
    if name == 'bhkCapsuleShape':
        r = float(shape.radius) * _OB_TO_GAME
        return (r, _v4(shape.first_point, _OB_TO_GAME) + offset,
                _v4(shape.second_point, _OB_TO_GAME) + offset)
    if name == 'bhkSphereShape':
        r = float(shape.radius) * _OB_TO_GAME
        eps = np.array([0.0, 0.0, max(0.1, r * 0.05)])
        return (r, offset - eps, offset + eps)
    if name == 'bhkBoxShape':
        d = _v4(shape.dimensions, _OB_TO_GAME)     # half extents
        axis = int(np.argmax(d))
        seg = np.zeros(3)
        seg[axis] = d[axis]
        r = float(np.median(np.delete(d, axis)))
        return (max(r, 0.5), offset - seg, offset + seg)
    if name in ('bhkTransformShape', 'bhkConvexTransformShape'):
        m = shape.transform
        sub = _capsule_from_shape(shape.shape, np.zeros(3))
        if sub is None:
            return None
        R = np.array([[m.m_11, m.m_12, m.m_13],
                      [m.m_21, m.m_22, m.m_23],
                      [m.m_31, m.m_32, m.m_33]])
        t = np.array([m.m_14, m.m_24, m.m_34]) * _OB_TO_GAME
        r, va, vb = sub
        # PyFFI m_ij is the transpose of the engine's column matrix →
        # row-convention: v' = v @ R.T ... use both orders? m_i4 column is
        # translation; rotate row-style like collision.py does.
        return (r, va @ R.T + t + offset, vb @ R.T + t + offset)
    if name == 'bhkListShape':
        for sub in shape.sub_shapes:
            got = _capsule_from_shape(sub, offset)
            if got is not None:
                return got
    return None


def _descriptor(constraint):
    """(kind, descriptor) from a bhk constraint block; malleables demote to
    their inner type. Returns (None, None) for unsupported kinds."""
    cname = constraint.__class__.__name__
    if cname == 'bhkRagdollConstraint':
        return 'ragdoll', constraint.ragdoll
    if cname == 'bhkLimitedHingeConstraint':
        return 'hinge', constraint.limited_hinge
    if cname == 'bhkHingeConstraint':
        return 'plain_hinge', constraint.hinge
    if cname == 'bhkMalleableConstraint':
        sub = constraint.sub_constraint     # PyFFI 2.2.3 SubConstraint
        t = int(sub.type)
        if t == 7:      # ragdoll
            return 'ragdoll', sub.ragdoll
        if t == 2:      # limited hinge
            return 'hinge', sub.limited_hinge
        if t == 1:
            return 'plain_hinge', sub.hinge
    return None, None


def extract_ragdoll(skeleton_nif_path: str, bones: list):
    """Parse the Oblivion skeleton.nif into RagdollPart list (parent-before-
    child, constraints attached), or None when the skeleton has no ragdoll."""
    data = NifFormat.Data()
    with open(skeleton_nif_path, 'rb') as f:
        data.read(f)

    bone_index = {b.name: i for i, b in enumerate(bones)}
    body_by_bone = {}       # anim bone index -> (rigid_body, node)
    body_id_to_bone = {}    # id(rigid_body) -> anim bone index
    for root in data.roots:
        if not isinstance(root, NifFormat.NiNode):
            continue
        for node in root.tree():
            if not isinstance(node, NifFormat.NiNode):
                continue
            co = getattr(node, 'collision_object', None)
            body = getattr(co, 'body', None) if co is not None else None
            if body is None:
                continue
            name = bytes(node.name).decode('latin-1').rstrip('\x00')
            idx = bone_index.get(name)
            if idx is None:
                continue
            body_by_bone[idx] = (body, node)
            body_id_to_bone[id(body)] = idx

    if len(body_by_bone) < 2:
        return None

    # ragdoll part order: anim-skeleton DFS order restricted to body bones
    part_of_bone = {}
    parts = []
    for idx in sorted(body_by_bone):
        body, node = body_by_bone[idx]
        p = RagdollPart()
        p.anim_index = idx
        p.name = 'Ragdoll_' + bones[idx].name
        # nearest ancestor with a body
        a = bones[idx].parent
        while a >= 0 and a not in part_of_bone:
            a = bones[a].parent
        p.parent = part_of_bone.get(a, -1)

        offset = _v4(body.translation, _OB_TO_GAME)
        p.mass = float(body.mass) if body.mass > 0 else 1.0
        inertia = max(body.inertia.m_11, body.inertia.m_22,
                      body.inertia.m_33) * (_OB_TO_GAME ** 2)
        p.com = _v4(body.center, _OB_TO_GAME) + offset
        p.shape = _capsule_from_shape(body.shape, offset)
        if p.shape is None:
            r = max(1.0, float(np.linalg.norm(p.com)))
            p.shape = (r, p.com - [0, 0, 0.5], p.com + [0, 0, 0.5])
        if inertia <= 0:
            r_bs = max(np.linalg.norm(p.shape[1]),
                       np.linalg.norm(p.shape[2])) + p.shape[0]
            inertia = 0.4 * p.mass * r_bs * r_bs
        p.inertia = inertia

        # constraint joining this body to its parent body
        for con in getattr(body, 'constraints', []):
            kind, d = _descriptor(con)
            if kind is None:
                continue
            ents = [body_id_to_bone.get(id(e)) for e in con.entities]
            if len(ents) == 2 and ents[0] == idx and ents[1] is not None:
                p.constraint = (kind, d, offset,
                                _v4(body_by_bone[ents[1]][0].translation,
                                    _OB_TO_GAME))
                break
        part_of_bone[idx] = len(parts)
        parts.append(p)

    return parts


# ---------------------------------------------------------------------------
# XML emission
# ---------------------------------------------------------------------------

def _fmt_transform_rows(rows, t):
    return (fmt_vec(*rows[0]) + fmt_vec(*rows[1]) + fmt_vec(*rows[2])
            + fmt_vec(*t))


def _basis_rows(axis1, axis2):
    """Orthonormal (axis1, axis2', axis1×axis2') rows from two descriptor
    axes (Gram-Schmidt on axis2)."""
    a = np.asarray(axis1, dtype=float)
    a = a / (np.linalg.norm(a) or 1.0)
    b = np.asarray(axis2, dtype=float)
    b = b - a * float(a.dot(b))
    n = np.linalg.norm(b)
    if n < 1e-6:
        b = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.9 \
            else np.array([1.0, 0.0, 0.0])
        b = b - a * float(a.dot(b))
        n = np.linalg.norm(b)
    b = b / n
    return [a, b, np.cross(a, b)]


def _add_rigid_body(pf, part, world_R, world_t):
    """hkpCapsuleShape + hkpRigidBody pair; returns the body object."""
    shape = pf.add('hkpCapsuleShape')
    r, va, vb = part.shape
    shape.param('userData', 0)
    shape.param('radius', f'{r:.6f}')
    shape.param('vertexA', fmt_vec(va[0], va[1], va[2], r))
    shape.param('vertexB', fmt_vec(vb[0], vb[1], vb[2], r))

    com_w = part.com @ world_R + world_t
    quat = _mat_row_to_quat(world_R)
    r_obj = max(np.linalg.norm(va), np.linalg.norm(vb)) + r

    body = pf.add('hkpRigidBody')
    body.param('userData', 0)
    body.param_raw('collidable', f'''<hkobject>
\t<hkparam name="shape">{shape.ref}</hkparam>
\t<hkparam name="shapeKey">4294967295</hkparam>
\t<hkparam name="forceCollideOntoPpu">0</hkparam>
\t<hkparam name="broadPhaseHandle">
\t\t<hkobject>
\t\t\t<hkparam name="type">1</hkparam>
\t\t\t<hkparam name="objectQualityType">4</hkparam>
\t\t\t<hkparam name="collisionFilterInfo">0</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="allowedPenetrationDepth">0.100000</hkparam>
</hkobject>''')
    body.param_raw('multiThreadCheck', '<hkobject>\n</hkobject>')
    body.param('name', part.name)
    body.param_raw('properties', '', numelements=0)
    body.param_raw('material', '''<hkobject>
\t<hkparam name="responseType">RESPONSE_SIMPLE_CONTACT</hkparam>
\t<hkparam name="rollingFrictionMultiplier">0.000000</hkparam>
\t<hkparam name="friction">0.300000</hkparam>
\t<hkparam name="restitution">0.800000</hkparam>
</hkobject>''')
    body.param('damageMultiplier', '1.000000')
    body.param('storageIndex', 65535)
    body.param('contactPointCallbackDelay', 65535)
    body.param('autoRemoveLevel', 0)
    body.param('numShapeKeysInContactPointProperties', 0)
    body.param('responseModifierFlags', 0)
    body.param('uid', 4294967295)
    body.param_raw('spuCollisionCallback', '''<hkobject>
\t<hkparam name="eventFilter">3</hkparam>
\t<hkparam name="userFilter">1</hkparam>
</hkobject>''')
    inv_i = 1.0 / part.inertia
    inv_m = 1.0 / part.mass
    body.param_raw('motion', f'''<hkobject>
\t<hkparam name="type">MOTION_SPHERE_INERTIA</hkparam>
\t<hkparam name="deactivationIntegrateCounter">15</hkparam>
\t<hkparam name="deactivationNumInactiveFrames">49152 49152</hkparam>
\t<hkparam name="motionState">
\t\t<hkobject>
\t\t\t<hkparam name="transform">{_fmt_transform_rows(world_R, world_t)}</hkparam>
\t\t\t<hkparam name="sweptTransform">
\t\t\t\t<hkobject>
\t\t\t\t\t<hkparam name="centerOfMass0">{fmt_vec(com_w[0], com_w[1], com_w[2], 0.0)}</hkparam>
\t\t\t\t\t<hkparam name="centerOfMass1">{fmt_vec(com_w[0], com_w[1], com_w[2], 0.0)}</hkparam>
\t\t\t\t\t<hkparam name="rotation0">{fmt_vec(*quat)}</hkparam>
\t\t\t\t\t<hkparam name="rotation1">{fmt_vec(*quat)}</hkparam>
\t\t\t\t\t<hkparam name="centerOfMassLocal">{fmt_vec(part.com[0], part.com[1], part.com[2], 0.0)}</hkparam>
\t\t\t\t</hkobject>
\t\t\t</hkparam>
\t\t\t<hkparam name="deltaAngle">(0.000000 0.000000 0.000000 0.000000)</hkparam>
\t\t\t<hkparam name="objectRadius">{r_obj:.6f}</hkparam>
\t\t\t<hkparam name="linearDamping">0.000000</hkparam>
\t\t\t<hkparam name="angularDamping">0.049805</hkparam>
\t\t\t<hkparam name="timeFactor">1.000000</hkparam>
\t\t\t<hkparam name="maxLinearVelocity">127</hkparam>
\t\t\t<hkparam name="maxAngularVelocity">127</hkparam>
\t\t\t<hkparam name="deactivationClass">2</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="inertiaAndMassInv">{fmt_vec(inv_i, inv_i, inv_i, inv_m)}</hkparam>
\t<hkparam name="linearVelocity">(0.000000 0.000000 0.000000 0.000000)</hkparam>
\t<hkparam name="angularVelocity">(0.000000 0.000000 0.000000 0.000000)</hkparam>
\t<hkparam name="deactivationRefPosition">(0.000000 0.000000 0.000000 0.000000) (0.000000 0.000000 0.000000 0.000000)</hkparam>
\t<hkparam name="deactivationRefOrientation">0 0</hkparam>
\t<hkparam name="savedMotion">null</hkparam>
\t<hkparam name="savedQualityTypeIndex">0</hkparam>
\t<hkparam name="gravityFactor">1.000000</hkparam>
</hkobject>''')
    body.param('localFrame', 'null')
    body.param('npData', 0)
    return body


def _add_ragdoll_constraint_data(pf, d, offset_a, offset_b, motor_ref):
    """hkpRagdollConstraintData from an Oblivion RagdollDescriptor."""
    rows_a = _basis_rows(_v4(d.twist_a), _v4(d.plane_a))
    rows_b = _basis_rows(_v4(d.twist_b), _v4(d.plane_b))
    piv_a = _v4(d.pivot_a, _OB_TO_GAME) + offset_a
    piv_b = _v4(d.pivot_b, _OB_TO_GAME) + offset_b
    cone = float(d.cone_max_angle)
    tgt = (fmt_vec(*rows_b[0]) + fmt_vec(*rows_b[1]) + fmt_vec(*rows_b[2]))

    data = pf.add('hkpRagdollConstraintData')
    data.param('userData', 0)
    data.param_raw('atoms', f'''<hkobject>
\t<hkparam name="transforms">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_SET_LOCAL_TRANSFORMS</hkparam>
\t\t\t<hkparam name="transformA">{_fmt_transform_rows(rows_a, piv_a)}</hkparam>
\t\t\t<hkparam name="transformB">{_fmt_transform_rows(rows_b, piv_b)}</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="setupStabilization">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_SETUP_STABILIZATION</hkparam>
\t\t\t<hkparam name="enabled">false</hkparam>
\t\t\t<hkparam name="maxAngle">{_HUGE}</hkparam>
\t\t\t<hkparam name="padding">0 0 0 0 0 0 0 0</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="ragdollMotors">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_RAGDOLL_MOTOR</hkparam>
\t\t\t<hkparam name="isEnabled">false</hkparam>
\t\t\t<hkparam name="initializedOffset">96</hkparam>
\t\t\t<hkparam name="previousTargetAnglesOffset">100</hkparam>
\t\t\t<hkparam name="target_bRca">{tgt}</hkparam>
\t\t\t<hkparam name="motors">{motor_ref} {motor_ref} {motor_ref}</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="angFriction">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_ANG_FRICTION</hkparam>
\t\t\t<hkparam name="isEnabled">1</hkparam>
\t\t\t<hkparam name="firstFrictionAxis">0</hkparam>
\t\t\t<hkparam name="numFrictionAxes">3</hkparam>
\t\t\t<hkparam name="maxFrictionTorque">{float(d.max_friction):.6f}</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="twistLimit">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_TWIST_LIMIT</hkparam>
\t\t\t<hkparam name="isEnabled">1</hkparam>
\t\t\t<hkparam name="twistAxis">0</hkparam>
\t\t\t<hkparam name="refAxis">1</hkparam>
\t\t\t<hkparam name="minAngle">{float(d.twist_min_angle):.6f}</hkparam>
\t\t\t<hkparam name="maxAngle">{float(d.twist_max_angle):.6f}</hkparam>
\t\t\t<hkparam name="angularLimitsTauFactor">0.800000</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="coneLimit">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_CONE_LIMIT</hkparam>
\t\t\t<hkparam name="isEnabled">1</hkparam>
\t\t\t<hkparam name="twistAxisInA">0</hkparam>
\t\t\t<hkparam name="refAxisInB">0</hkparam>
\t\t\t<hkparam name="angleMeasurementMode">ZERO_WHEN_VECTORS_ALIGNED</hkparam>
\t\t\t<hkparam name="memOffsetToAngleOffset">56</hkparam>
\t\t\t<hkparam name="minAngle">-100.000000</hkparam>
\t\t\t<hkparam name="maxAngle">{cone:.6f}</hkparam>
\t\t\t<hkparam name="angularLimitsTauFactor">0.800000</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="planesLimit">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_CONE_LIMIT</hkparam>
\t\t\t<hkparam name="isEnabled">1</hkparam>
\t\t\t<hkparam name="twistAxisInA">0</hkparam>
\t\t\t<hkparam name="refAxisInB">1</hkparam>
\t\t\t<hkparam name="angleMeasurementMode">ZERO_WHEN_VECTORS_PERPENDICULAR</hkparam>
\t\t\t<hkparam name="memOffsetToAngleOffset">0</hkparam>
\t\t\t<hkparam name="minAngle">{float(d.plane_min_angle):.6f}</hkparam>
\t\t\t<hkparam name="maxAngle">{float(d.plane_max_angle):.6f}</hkparam>
\t\t\t<hkparam name="angularLimitsTauFactor">0.800000</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="ballSocket">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_BALL_SOCKET</hkparam>
\t\t\t<hkparam name="solvingMethod">METHOD_OLD</hkparam>
\t\t\t<hkparam name="bodiesToNotify">0</hkparam>
\t\t\t<hkparam name="velocityStabilizationFactor">48</hkparam>
\t\t\t<hkparam name="maxImpulse">{_MAX_IMPULSE}</hkparam>
\t\t\t<hkparam name="inertiaStabilizationFactor">0.000000</hkparam>
\t\t</hkobject>
\t</hkparam>
</hkobject>''')
    return data


def _add_hinge_constraint_data(pf, kind, d, offset_a, offset_b):
    """hkpLimitedHingeConstraintData from an Oblivion (Limited)Hinge
    descriptor. Plain hinges get wide limits."""
    axle_a = _v4(d.axle_a)
    perp_a = _v4(getattr(d, 'perp_2_axle_in_a_1', None)) \
        if getattr(d, 'perp_2_axle_in_a_1', None) is not None else None
    rows_a = _basis_rows(axle_a, perp_a) if perp_a is not None \
        else _basis_rows(axle_a, np.array([0.0, 0.0, 1.0]))
    rows_b = _basis_rows(_v4(d.axle_b), rows_a[1])
    piv_a = _v4(d.pivot_a, _OB_TO_GAME) + offset_a
    piv_b = _v4(d.pivot_b, _OB_TO_GAME) + offset_b
    if kind == 'hinge':
        min_a, max_a = float(d.min_angle), float(d.max_angle)
        friction = float(d.max_friction)
    else:
        min_a, max_a = -math.pi, math.pi
        friction = 0.0

    data = pf.add('hkpLimitedHingeConstraintData')
    data.param('userData', 0)
    data.param_raw('atoms', f'''<hkobject>
\t<hkparam name="transforms">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_SET_LOCAL_TRANSFORMS</hkparam>
\t\t\t<hkparam name="transformA">{_fmt_transform_rows(rows_a, piv_a)}</hkparam>
\t\t\t<hkparam name="transformB">{_fmt_transform_rows(rows_b, piv_b)}</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="setupStabilization">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_SETUP_STABILIZATION</hkparam>
\t\t\t<hkparam name="enabled">false</hkparam>
\t\t\t<hkparam name="maxAngle">{_HUGE}</hkparam>
\t\t\t<hkparam name="padding">0 0 0 0 0 0 0 0</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="angMotor">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_ANG_MOTOR</hkparam>
\t\t\t<hkparam name="isEnabled">false</hkparam>
\t\t\t<hkparam name="motorAxis">0</hkparam>
\t\t\t<hkparam name="initializedOffset">64</hkparam>
\t\t\t<hkparam name="previousTargetAngleOffset">68</hkparam>
\t\t\t<hkparam name="correspondingAngLimitSolverResultOffset">16</hkparam>
\t\t\t<hkparam name="targetAngle">0.000000</hkparam>
\t\t\t<hkparam name="motor">null</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="angFriction">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_ANG_FRICTION</hkparam>
\t\t\t<hkparam name="isEnabled">1</hkparam>
\t\t\t<hkparam name="firstFrictionAxis">0</hkparam>
\t\t\t<hkparam name="numFrictionAxes">1</hkparam>
\t\t\t<hkparam name="maxFrictionTorque">{friction:.6f}</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="angLimit">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_ANG_LIMIT</hkparam>
\t\t\t<hkparam name="isEnabled">1</hkparam>
\t\t\t<hkparam name="limitAxis">0</hkparam>
\t\t\t<hkparam name="minAngle">{min_a:.6f}</hkparam>
\t\t\t<hkparam name="maxAngle">{max_a:.6f}</hkparam>
\t\t\t<hkparam name="angularLimitsTauFactor">1.000000</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="2dAng">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_2D_ANG</hkparam>
\t\t\t<hkparam name="freeRotationAxis">0</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="ballSocket">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_BALL_SOCKET</hkparam>
\t\t\t<hkparam name="solvingMethod">METHOD_OLD</hkparam>
\t\t\t<hkparam name="bodiesToNotify">0</hkparam>
\t\t\t<hkparam name="velocityStabilizationFactor">48</hkparam>
\t\t\t<hkparam name="maxImpulse">{_MAX_IMPULSE}</hkparam>
\t\t\t<hkparam name="inertiaStabilizationFactor">0.000000</hkparam>
\t\t</hkobject>
\t</hkparam>
</hkobject>''')
    return data


def _add_constraint_instance(pf, data_ref, child_body_ref, parent_body_ref,
                             name):
    inst = pf.add('hkpConstraintInstance')
    inst.param('data', data_ref)
    inst.param('constraintModifiers', 'null')
    inst.param_raw('entities', f'{child_body_ref} {parent_body_ref}')
    inst.param('priority', 'PRIORITY_PSI')
    inst.param('wantRuntime', 'true')
    inst.param('destructionRemapInfo', 'ON_DESTRUCTION_REMAP')
    inst.param('name', name)
    inst.param('userData', 0)
    return inst


def emit_ragdoll(pf, bones, parts, anim_skel_ref):
    """Emit the full ragdoll object set; returns the extra namedVariants."""
    worlds = _bone_worlds(bones)

    # ragdoll hkaSkeleton — reference pose relative to the ragdoll parent
    rskel = pf.add('hkaSkeleton')
    rskel.param('name', parts[0].name)
    rskel.param_array('parentIndices', [p.parent for p in parts])
    rskel.param_structs('bones', [
        [('name', p.name), ('lockTranslation', p.parent != -1)]
        for p in parts])
    pose_lines = []
    for p in parts:
        R, t = worlds[p.anim_index]
        if p.parent < 0:
            lt, lq = t, _mat_row_to_quat(R)
        else:
            Rp, tp = worlds[parts[p.parent].anim_index]
            inv = Rp.T
            lt = (t - tp) @ inv
            lq = _mat_row_to_quat(R @ inv)
        pose_lines.append(fmt_vec(*lt) + fmt_vec(*lq)
                          + fmt_vec(1.0, 1.0, 1.0))
    rskel.param_raw('referencePose', '\n'.join(pose_lines),
                    numelements=len(parts))
    rskel.param_array('referenceFloats', [])
    rskel.param_raw('floatSlots', '', numelements=0)
    rskel.param_raw('localFrames', '', numelements=0)

    # mappers (identity aFromB — ragdoll frames coincide with anim frames)
    ident = ('(0.000000 0.000000 0.000000)'
             '(0.000000 0.000000 0.000000 1.000000)'
             '(1.000000 1.000000 1.000000)')

    def _mapper(a_ref, b_ref, pairs, unmapped):
        m = pf.add('hkaSkeletonMapper')
        rows = '\n'.join(
            f'<hkobject>\n\t<hkparam name="boneA">{a}</hkparam>\n'
            f'\t<hkparam name="boneB">{b}</hkparam>\n'
            f'\t<hkparam name="aFromBTransform">{ident}</hkparam>\n'
            f'</hkobject>' for a, b in pairs)
        unmapped_s = ' '.join(str(u) for u in unmapped)
        m.param_raw('mapping', f'''<hkobject>
\t<hkparam name="skeletonA">{a_ref}</hkparam>
\t<hkparam name="skeletonB">{b_ref}</hkparam>
\t<hkparam name="simpleMappings" numelements="{len(pairs)}">
{rows}
\t</hkparam>
\t<hkparam name="chainMappings" numelements="0"></hkparam>
\t<hkparam name="unmappedBones" numelements="{len(unmapped)}">
\t\t{unmapped_s}
\t</hkparam>
\t<hkparam name="extractedMotionMapping">{ident}</hkparam>
\t<hkparam name="keepUnmappedLocal">true</hkparam>
\t<hkparam name="mappingType">HK_RAGDOLL_MAPPING</hkparam>
</hkobject>''')
        return m

    mapped_anim = {p.anim_index for p in parts}
    unmapped_anim = [i for i in range(len(bones)) if i not in mapped_anim]
    map_r2a = _mapper(rskel.ref, anim_skel_ref,
                      [(ri, p.anim_index) for ri, p in enumerate(parts)],
                      [])
    map_a2r = _mapper(anim_skel_ref, rskel.ref,
                      [(p.anim_index, ri) for ri, p in enumerate(parts)],
                      unmapped_anim)

    motor = pf.add('hkpPositionConstraintMotor')
    motor.param('minForce', '-1000000.000000')
    motor.param('maxForce', '1000000.000000')
    motor.param('tau', '0.800000')
    motor.param('damping', '1.000000')
    motor.param('proportionalRecoveryVelocity', '2.000000')
    motor.param('constantRecoveryVelocity', '1.000000')

    bodies = [_add_rigid_body(pf, p, *worlds[p.anim_index]) for p in parts]

    def _constraints():
        insts = []
        for ri, p in enumerate(parts):
            if p.constraint is None or p.parent < 0:
                continue
            kind, d, off_a, off_b = p.constraint
            if kind == 'ragdoll':
                data = _add_ragdoll_constraint_data(pf, d, off_a, off_b,
                                                    motor.ref)
            else:
                data = _add_hinge_constraint_data(pf, kind, d, off_a, off_b)
            insts.append(_add_constraint_instance(
                pf, data.ref, bodies[ri].ref, bodies[p.parent].ref, p.name))
        return insts

    # vanilla duplicates the constraint graph: one instance set for the
    # ragdoll instance, one for the physics system (bodies are shared)
    con_ragdoll = _constraints()
    con_system = _constraints()

    ragdoll = pf.add('hkaRagdollInstance')
    ragdoll.param_array('rigidBodies', [b.ref for b in bodies])
    ragdoll.param_array('constraints', [c.ref for c in con_ragdoll])
    ragdoll.param_array('boneToRigidBodyMap', list(range(len(parts))))
    ragdoll.param('skeleton', rskel.ref)

    system = pf.add('hkpPhysicsSystem')
    system.param_array('rigidBodies', [b.ref for b in bodies])
    system.param_array('constraints', [c.ref for c in con_system])
    system.param_array('actions', [])
    system.param_array('phantoms', [])
    system.param('name', 'Default Physics System')
    system.param('userData', 0)
    system.param('active', True)

    pdata = pf.add('hkpPhysicsData')
    pdata.param('worldCinfo', 'null')
    pdata.param_array('systems', [system.ref])

    resource = pf.add('hkMemoryResourceContainer')
    resource.param('name', '')
    resource.param_array('resourceHandles', [])
    resource.param_array('children', [])

    return rskel, [
        [('name', 'Resource Data'),
         ('className', 'hkMemoryResourceContainer'),
         ('variant', resource.ref)],
        [('name', 'Physics Data'), ('className', 'hkpPhysicsData'),
         ('variant', pdata.ref)],
        [('name', 'RagdollInstance'), ('className', 'hkaRagdollInstance'),
         ('variant', ragdoll.ref)],
        [('name', 'SkeletonMapper'), ('className', 'hkaSkeletonMapper'),
         ('variant', map_r2a.ref)],
        [('name', 'SkeletonMapper'), ('className', 'hkaSkeletonMapper'),
         ('variant', map_a2r.ref)],
    ]
