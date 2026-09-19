"""Synthetic ragdoll bodies for Morrowind rigs, which author none.

Morrowind predates Havok, so its skeleton.nif carries no collision at all.
`attach_synthetic_bodies` hangs a capsule body on every bone the creature's
own body mesh SKINS to, written exactly as Oblivion authors one (Havok units,
body frame == the bone's bind world frame), so both consumers -- the hkx
ragdoll and the shipped skeleton.nif -- run their Oblivion path unchanged.

See: docs/commentary/asset_convert_creature.md#morrowind-synthetic-ragdolls
"""

import glob
import os

import numpy as np
from pyffi.formats.nif import NifFormat

from asset_convert.havok.hkx_skeleton import find_skeleton_root
from asset_convert.havok.ragdoll_bone_words import is_loose_bone
from asset_convert.havok.ragdoll_math import (OB_TO_GAME, capsule_inertia,
                                              mat_row_to_quat)

#: Newest NIF version that predates Havok (Morrowind's 4.0.0.2).
_PRE_HAVOK_VERSION = 0x04000002
#: Capsule radius over its length: authored Morroblivion median.
_RADIUS_OF_LENGTH = 0.30
#: Radius band as a fraction of rig extent: authored Morroblivion p10 / p90.
_RADIUS_MIN_OF_RIG = 0.0084
_RADIUS_MAX_OF_RIG = 0.047
#: Source mass per cubic game unit: authored Morroblivion median.
_DENSITY = 0.0066
#: Lightest body as a fraction of the mean, keeping joint mass ratios solvable.
_MASS_MIN_OF_MEAN = 0.1
#: Total SOURCE mass band: vanilla wolf 29 .. dragon 4852, times OB_TO_GAME.
_MASS_TOTAL_BAND = (29.0 * OB_TO_GAME, 4852.0 * OB_TO_GAME)
#: Joint limits in radians: authored Morroblivion medians over 117 joints.
_JOINT_CONE = 0.262
_JOINT_PLANE = 0.407
_JOINT_TWIST = 0.017
#: bhkRigidBody fields every authored Morroblivion ragdoll body carries.
_AUTHORED_BODY = (
    ('collision_response', 1), ('process_contact_callback_delay', 65535),
    ('linear_damping', 0.1), ('angular_damping', 0.05), ('friction', 0.3),
    ('restitution', 0.3), ('max_linear_velocity', 250.0),
    ('max_angular_velocity', 31.4159), ('penetration_depth', 0.15),
    ('motion_system', 6), ('deactivator_type', 2),
    ('solver_deactivation', 2), ('quality_type', 2))
#: Layer 8 is OL_BIPED, the layer of every authored creature limb.
_BIPED_LAYER = 8


def _name(node) -> str:
    """A block's name as text."""
    raw = node.name
    return raw.decode('latin-1') if isinstance(raw, bytes) else str(raw)


def _skinned_bone_names(skeleton_nif_path: str) -> set:
    """Every bone name a body NIF beside the skeleton skins to."""
    names = set()
    folder = os.path.dirname(skeleton_nif_path)
    for path in sorted(glob.glob(os.path.join(folder, '*.nif'))):
        if os.path.basename(path).lower() == 'skeleton.nif':
            continue
        data = NifFormat.Data()
        with open(path, 'rb') as f:
            data.read(f)
        for block in data.blocks:
            if isinstance(block, NifFormat.NiSkinInstance):
                names.update(_name(b) for b in block.bones if b is not None)
    return names


def _walk(root) -> list:
    """[(node, parent index, world R row-convention, world t)] pre-order."""
    out = []

    def visit(node, parent, R_p, t_p):
        """Record `node`'s world transform, then recurse into NiNode children."""
        m = node.rotation
        R = np.array([[m.m_11, m.m_12, m.m_13], [m.m_21, m.m_22, m.m_23],
                      [m.m_31, m.m_32, m.m_33]], dtype=float) @ R_p
        t = np.array([node.translation.x, node.translation.y,
                      node.translation.z], dtype=float) @ R_p + t_p
        index = len(out)
        out.append((node, parent, R, t))
        for child in node.children:
            if isinstance(child, NifFormat.NiNode):
                visit(child, index, R, t)

    visit(root, -1, np.eye(3), np.zeros(3))
    return out


def _body_bones(walked: list, skinned: set) -> set:
    """Indices that get a body: the skinned bones plus their common ancestor.

    The common ancestor is the trunk every limb hangs off; without a body of
    its own the limbs have no shared ragdoll root.  A bone whose name repeats
    gets none: the engine pairs hkx and NIF bodies by NAME and crashes on one
    it cannot find.  Neither does a bone under a non-unit SCALE, whose frame
    the ragdoll reader cannot invert.
    """
    names = [_name(node) for node, _p, _R, _t in walked]
    scaled = []
    for node, parent, _R, _t in walked:
        scaled.append(abs(float(node.scale) - 1.0) > 1e-3
                      or (parent >= 0 and scaled[parent]))
    fleshy = {i for i, name in enumerate(names)
              if name in skinned and names.count(name) == 1 and not scaled[i]}
    flesh = [0] * len(walked)
    for i in range(len(walked) - 1, -1, -1):
        flesh[i] += i in fleshy
        if walked[i][1] >= 0:
            flesh[walked[i][1]] += flesh[i]
    if flesh[0] < 2:
        return set()
    trunk = max(i for i, n in enumerate(flesh) if n == flesh[0])
    return {trunk} | fleshy


def _segments(walked: list, keep: set) -> dict:
    """{index: bone-local segment end} toward the mean of its body children."""
    ends = {}
    for i, (node, parent, _R, _t) in enumerate(walked):
        if i in keep and parent in keep:
            ends.setdefault(parent, []).append(
                [node.translation.x, node.translation.y, node.translation.z])
    return {i: np.mean(np.asarray(v, dtype=float), axis=0)
            for i, v in ends.items()}


def _make_body(node, R, t, end, extent: float):
    """A bhkBlendCollisionObject on `node`, in Oblivion's authored conventions."""
    r_min, r_max = _RADIUS_MIN_OF_RIG * extent, _RADIUS_MAX_OF_RIG * extent
    length = float(np.linalg.norm(end)) if end is not None else 0.0
    if length < 2.0 * r_min:
        end, length = np.array([0.0, 0.0, 2.0 * r_min]), 2.0 * r_min
    radius = float(np.clip(length * _RADIUS_OF_LENGTH, r_min, r_max))

    shape = NifFormat.bhkCapsuleShape()
    shape.radius = shape.radius_1 = shape.radius_2 = radius / OB_TO_GAME
    (shape.second_point.x, shape.second_point.y,
     shape.second_point.z) = end / OB_TO_GAME

    body = NifFormat.bhkRigidBody()
    body.shape = shape
    for field, value in _AUTHORED_BODY:
        setattr(body, field, value)
    body.havok_col_filter.layer = _BIPED_LAYER
    body.havok_col_filter_copy.layer = _BIPED_LAYER
    body.mass = _DENSITY * (np.pi * radius * radius * length
                            + (4.0 / 3.0) * np.pi * radius ** 3)
    body.center.x, body.center.y, body.center.z = 0.5 * end / OB_TO_GAME
    (body.rotation.x, body.rotation.y, body.rotation.z,
     body.rotation.w) = mat_row_to_quat(R)
    body.translation.x, body.translation.y, body.translation.z = t / OB_TO_GAME

    collision = NifFormat.bhkBlendCollisionObject()
    collision.flags = 9
    collision.unknown_float_1 = collision.unknown_float_2 = 1.0
    collision.body = body
    collision.target = node
    node.collision_object = collision
    return [collision, body, shape]


def _set_vec(vec, values) -> None:
    """Write three floats into a pyffi vector."""
    vec.x, vec.y, vec.z = (float(v) for v in values)


def _make_joint(child_body, parent_body, child, parent):
    """A bhkRagdollConstraint on `child_body`, pivoting at the child's joint.

    `child` / `parent` are their `_walk` rows.  The twist axis runs down the
    child's own capsule and both frames coincide at bind, so the limits open
    symmetrically around the rig's rest pose.
    """
    _node, _p, R_c, t_c = child
    _node, _p, R_p, t_p = parent
    seg = child_body.shape.second_point
    twist = np.array([seg.x, seg.y, seg.z], dtype=float)
    twist /= np.linalg.norm(twist)
    plane = np.cross(twist, np.eye(3)[int(np.argmin(np.abs(twist)))])
    plane /= np.linalg.norm(plane)
    to_parent = R_c @ R_p.T

    con = NifFormat.bhkRagdollConstraint()
    con.num_entities = 2
    con.entities.update_size()
    con.entities[0], con.entities[1] = child_body, parent_body
    con.priority = 1
    d = con.ragdoll
    _set_vec(d.twist_a, twist)
    _set_vec(d.plane_a, plane)
    _set_vec(d.twist_b, twist @ to_parent)
    _set_vec(d.plane_b, plane @ to_parent)
    _set_vec(d.pivot_b, (t_c - t_p) @ R_p.T / OB_TO_GAME)
    d.cone_max_angle = _JOINT_CONE
    d.plane_min_angle, d.plane_max_angle = -_JOINT_PLANE, _JOINT_PLANE
    d.twist_min_angle, d.twist_max_angle = -_JOINT_TWIST, _JOINT_TWIST
    child_body.num_constraints = 1
    child_body.constraints.update_size()
    child_body.constraints[0] = con
    return con


def _joint_parent(walked: list, body_of: dict, index: int) -> int:
    """The body `index` joins to, or -1 for the trunk.

    Authored rigs join an arm to the SPINE even where the clavicle hangs off
    the neck, so a living creature's arms stay on its animation while only
    the neck/head/tail chain hangs loose.  A body outside such a chain
    therefore skips ancestors inside one.
    """
    def loose(i):
        """Whether bone `i` is a loose-while-alive chain link."""
        return is_loose_bone(_name(walked[i][0]))

    parent, fallback = walked[index][1], -1
    while parent >= 0:
        if parent in body_of:
            if loose(index) or not loose(parent):
                return parent
            fallback = parent if fallback < 0 else fallback
        parent = walked[parent][1]
    return fallback


def attach_synthetic_bodies(data, skeleton_nif_path: str) -> int:
    """Give a pre-Havok rig its ragdoll bodies; returns the count attached.

    A no-op on any NIF newer than Morrowind's, on a rig that already carries
    collision, and on one whose folder holds no skinned body mesh.
    """
    if not 0 < getattr(data, 'version', 0) <= _PRE_HAVOK_VERSION:
        return 0
    try:
        walked = _walk(find_skeleton_root(data))
    except ValueError:
        return 0
    if any(getattr(n, 'collision_object', None) is not None
           for n, _p, _R, _t in walked):
        return 0
    keep = _body_bones(walked, _skinned_bone_names(skeleton_nif_path))
    if not keep:
        return 0

    worlds = np.asarray([walked[i][3] for i in keep])
    extent = max(1.0, float(np.linalg.norm(worlds.max(0) - worlds.min(0))))
    ends = _segments(walked, keep)
    body_of = {}
    for i in sorted(keep):
        node, _parent, R, t = walked[i]
        blocks = _make_body(node, R, t, ends.get(i), extent)
        parent = _joint_parent(walked, body_of, i)
        body_of[i] = blocks[1]
        if parent >= 0:
            blocks.append(_make_joint(blocks[1], body_of[parent],
                                      walked[i], walked[parent]))
        data.blocks.extend(blocks)
    _normalize_mass(list(body_of.values()))
    return len(body_of)


def _normalize_mass(bodies: list) -> None:
    """Clamp the total into the vanilla band, floor each body, set inertia.

    Volume follows authoring scale cubed and these rigs are authored at
    scales spanning 400x, so the raw total says nothing about creature size.
    """
    total = sum(float(b.mass) for b in bodies)
    factor = float(np.clip(total, *_MASS_TOTAL_BAND)) / total
    floor = _MASS_MIN_OF_MEAN * factor * total / len(bodies)
    for body in bodies:
        body.mass = max(float(body.mass) * factor, floor)
        shape = body.shape
        end = (shape.second_point.x, shape.second_point.y,
               shape.second_point.z)
        (body.inertia.m_11, body.inertia.m_22,
         body.inertia.m_33) = capsule_inertia(
            (shape.radius, (0.0, 0.0, 0.0), end), body.mass)
