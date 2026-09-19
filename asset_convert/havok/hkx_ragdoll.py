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
    mat33_to_quat_xyzw of the NIF matrix.
  - Constraint transformA/B rows = (twist, plane, twist×plane) for ragdoll
    joints, (axle, perp1, perp2) for hinges, expressed in each entity's
    local frame; translation = pivot.
  - Constraint entities order = (child body, parent body) — the nif stores
    the constraint on the child body with entities[0] = itself.
  - THE BODY FRAME IS NOT THE BONE FRAME (2026-07-16, the mangled-ragdoll
    root cause): a blend-collision bhkRigidBody's rotation/translation hold
    the body's BIND-POSE WORLD transform (translation×7 == the bone's world
    position on every Oblivion skeleton; verified dog 26/26 — vanilla Skyrim
    skeleton.nif blend bodies use the same convention in metre units).
    Capsule vertices, COM, and constraint pivots/axes are authored in that
    body-local frame, so converting them to our bone-local ragdoll frames
    needs the full bone-from-body transform (R_body_world @ R_bone_world^T
    row-convention + the world offset), NOT translation-as-offset.  The old
    "fold body.translation in as an offset" displaced every capsule by the
    bone's world position and dropped the rotation entirely.
  - Vanilla creature ragdoll constraints have maxFrictionTorque 0.0 across
    the board (dog census) — Oblivion descriptor frictions (≈10) freeze
    joints into distorted poses in Skyrim's solver.  Synthetic rock joints
    keep 10.0 (vanilla atronachstorm census).
"""

import math
import os
import sys

import numpy as np

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert.havok.ragdoll_math import (OB_TO_GAME, bone_worlds,
                                              capsule_inertia,
                                              mat_row_to_quat,
                                              quat_to_mat_row, unit, v4)
from asset_convert.havok.hkx_xml import fmt_vec
from asset_convert.havok import hkx_xml
from asset_convert.havok.ragdoll_bone_words import (AXIAL_WORDS, bone_words,
                                                    is_loose_bone)
from asset_convert.havok.hkx_ragdoll_morrowind import attach_synthetic_bodies
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
    'hkMemoryResourceHandle': '0xbffac086',
    'hkpShapeInfo': '0xea7f1d08',
})

# Oblivion's authored ragdoll MASSES have to be divided by the same 7 (2026-08-08
# "dead creatures weigh a million pounds — I can only move limbs a little" report).
#
# Oblivion tunes mass against OBLIVION-scale lengths; we multiply every length by
# `OB_TO_GAME`, and Havok's rotational inertia goes as mass * length^2, so
# carrying the mass through unchanged inflates the resistance-to-rotation by 49x
# relative to what the animators actually tuned.  Dividing mass by 7 restores
# Oblivion's own feel while preserving each rig's RELATIVE proportions, so a
# heavy creature stays heavy (a Skyrim dragon is immovable and should be) and a
# rat becomes flickable.
#
# Landing check against matched vanilla creatures (our dog, mass total / max
# principal inertia):  carried through 262 / 4548; /7 -> 37.4 / 650.  Vanilla
# wolf is 29 / 426 and vanilla dog 74 / 819, so /7 sits between them.  A single
# divisor is deliberate: vanilla per-body masses are hand-authored round numbers
# with no volume/density law (dog density varies 46x across its own bodies, and
# vanilla totals span wolf 29 -> dragon 4852), so there is nothing physical to
# derive — only the unit scale.
_OB_MASS_DIV = OB_TO_GAME
_HUGE = '18446726481523507000.000000'
_MAX_IMPULSE = '340282001837565600000000000000000000000.000000'


# ---------------------------------------------------------------------------
# Extraction from the Oblivion skeleton.nif
# ---------------------------------------------------------------------------

class RagdollPart:
    def __init__(self):
        self.anim_index = -1
        self.parent = -1            # ragdoll part index
        self.name = ''
        self.mass = 1.0
        self.inertia = (1.0, 1.0, 1.0)  # tensor diagonal, game units
        self.com = np.zeros(3)      # bone-local, game units
        self.shape = None           # (radius, vA, vB) capsule, bone-local
        self.constraint = None      # (kind, descriptor dict) joining to parent


def _capsule_from_shape(shape):
    """Any Oblivion bhk shape → (radius, vA, vB) capsule in BODY-local game
    units (the caller maps body space → bone space via the part's
    bone-from-body transform)."""
    name = shape.__class__.__name__
    if name == 'bhkCapsuleShape':
        r = float(shape.radius) * OB_TO_GAME
        return (r, v4(shape.first_point, OB_TO_GAME),
                v4(shape.second_point, OB_TO_GAME))
    if name == 'bhkSphereShape':
        r = float(shape.radius) * OB_TO_GAME
        eps = np.array([0.0, 0.0, max(0.1, r * 0.05)])
        return (r, -eps, eps)
    if name == 'bhkBoxShape':
        d = v4(shape.dimensions, OB_TO_GAME)     # half extents
        axis = int(np.argmax(d))
        seg = np.zeros(3)
        seg[axis] = d[axis]
        r = float(np.median(np.delete(d, axis)))
        return (max(r, 0.5), -seg, seg)
    if name in ('bhkTransformShape', 'bhkConvexTransformShape'):
        m = shape.transform
        sub = _capsule_from_shape(shape.shape)
        if sub is None:
            return None
        R = np.array([[m.m_11, m.m_12, m.m_13],
                      [m.m_21, m.m_22, m.m_23],
                      [m.m_31, m.m_32, m.m_33]])
        t = np.array([m.m_14, m.m_24, m.m_34]) * OB_TO_GAME
        r, va, vb = sub
        # PyFFI m_ij is the transpose of the engine's column matrix →
        # row-convention: v' = v @ R.T ... use both orders? m_i4 column is
        # translation; rotate row-style like collision.py does.
        return (r, va @ R.T + t, vb @ R.T + t)
    if name == 'bhkListShape':
        for sub in shape.sub_shapes:
            got = _capsule_from_shape(sub)
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
        return 'hinge', constraint.hinge
    if cname == 'bhkMalleableConstraint':
        sub = constraint.sub_constraint     # PyFFI 2.2.3 SubConstraint
        t = int(sub.type)
        if t == 7:      # ragdoll
            return 'ragdoll', sub.ragdoll
        if t == 2:      # limited hinge
            return 'hinge', sub.limited_hinge
        if t == 1:
            return 'hinge', sub.hinge
    return None, None


# --- bind-pose limit legalization ------------------------------------------
# Oblivion authors many joints whose limit window EXCLUDES the bind pose
_BIND_EPS = 0.009        # ~0.5 deg margin inside the widened boundary


def _bind_twist(axis, ref_a, ref_b):
    """Signed rotation of ref_a relative to ref_b about axis (world)."""
    axis = np.asarray(axis, dtype=float)
    axis = axis / (np.linalg.norm(axis) or 1.0)

    def _proj(v):
        p = v - axis * float(np.dot(v, axis))
        n = np.linalg.norm(p)
        return p / n if n else p

    pa, pb = _proj(np.asarray(ref_a, float)), _proj(np.asarray(ref_b, float))
    return math.atan2(float(np.dot(np.cross(pb, pa), axis)),
                      float(np.dot(pa, pb)))


def _world_angle(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a = a / (np.linalg.norm(a) or 1.0)
    b = b / (np.linalg.norm(b) or 1.0)
    return math.acos(max(-1.0, min(1.0, float(np.dot(a, b)))))


def _legalize_limits(kind, info, R_cw, R_pw):
    """Widen the constraint's limit windows so the bind pose is inside them
    (see block comment above).  rows/limits are mutated in place."""
    wa = [np.asarray(r, float) @ R_cw for r in info['rows_a']]
    wb = [np.asarray(r, float) @ R_pw for r in info['rows_b']]
    if kind == 'ragdoll':
        cone = _world_angle(wa[0], wb[0])
        dot = float(np.dot(wa[0] / (np.linalg.norm(wa[0]) or 1.0),
                           wb[1] / (np.linalg.norm(wb[1]) or 1.0)))
        plane = math.asin(max(-1.0, min(1.0, dot)))
        twist = _bind_twist(wa[0] + wb[0], wa[1], wb[1])
        info['cone'] = max(info['cone'], cone + _BIND_EPS)
        info['plane_min'] = min(info['plane_min'], plane - _BIND_EPS)
        info['plane_max'] = max(info['plane_max'], plane + _BIND_EPS)
        info['twist_min'] = min(info['twist_min'], twist - _BIND_EPS)
        info['twist_max'] = max(info['twist_max'], twist + _BIND_EPS)
    else:
        ang = _bind_twist(wa[0] + wb[0], wa[1], wb[1])
        info['min'] = min(info['min'], ang - _BIND_EPS)
        info['max'] = max(info['max'], ang + _BIND_EPS)


# --- vanilla rock-joint template (atronachstorm skeleton.nif census: every
# free orbiting rock is ragdoll-constrained to its nearest body-carrying
# ancestor with exactly these limits) ---
_SYNTH_CONE = 0.872665          # 50 deg
_SYNTH_PLANE = 1.570796         # +/- 90 deg
_SYNTH_TWIST = 0.087266         # +/- 5 deg
# Friction 0, like EVERY vanilla creature ragdoll joint (dog census: 42/42
# `maxFrictionTorque` = 0.000000).  The Oblivion-authored ~10 was carried onto
# synthetic joints and is the 2026-08-08 "corpse never falls over" cause:
# `maxFrictionTorque` is an angular friction torque on the joint, so a joint
# carrying 10 resists rotation hard enough that the chain through it cannot
# collapse under gravity.
#
# The 4 creatures whose Oblivion rig ships bodies with NO authored constraint
# are exactly the 4 that never fell over (user-confirmed 4/4): skeleton and
# shambles (3 synthetic joints each, on `Head` + both `UpperArm`),
# mehrunesdagon (22 of 23 — its whole spine and both legs), stormatronach
# (53, its rock shell).  Every other creature has exactly one unconstrained
# body — the ragdoll ROOT, which needs no joint — and every one of those
# falls.  The tell was the storm atronach: its ROCKS (the synthetic-jointed
# bodies) tumbled correctly while its BODY stayed rigid, and its mainline
# spine/leg joints are authored, friction 0 — the frozen links are the ones
# our template gave friction to.
#
# Nothing else distinguished the two groups: free-at-death sets are IDENTICAL
# between skeleton and zombie (`Head`, `L Hand`, `R Hand`; 17 parts each) yet
# zombie falls, and joint limits, mass ratios, keyframe percentages,
# SPHERE_INERTIA counts and load-path blocking all fail to separate them.
_SYNTH_FRICTION = 0.0


def decode_name(node):
    return bytes(node.name).decode('latin-1').rstrip('\x00')


# Oblivion collision-TOGGLE proxies, which are NOT ragdoll limbs.
#
# Morroblivion's alit hangs a CollisionNode -> EnableCollisions pair off each
# of its 12 bones: the CollisionNode body is a 95%-scale DUPLICATE of the
# bone's own capsule at mass 0.0001 (the bone's real mass is 5..30), and the
# EnableCollisions body is a bhkSphereShape of radius 0.0.  They are a shadow
# copy of each bone's collision that Oblivion could switch on and off; the
# alit is ONE creature with 12 limbs, not a 36-part assembly, and no vanilla
# Skyrim creature ships anything like them (census of all 35 LE
# skeleton.hkx in references/Skyrim Animations).  Alit is the only rig of 90
# across Oblivion/Nehrim/Morrowind_ob that has them.
#
# Keyed on physical content (no volume, or a mass three orders below any
# authored limb), never on the node NAME.  Evaluated on the SOURCE (Oblivion
# units) in both consumers: extract_ragdoll reads the source NIF, and
# nif_converter strips them BEFORE collision conversion rescales mass/radius.
_MARKER_MAX_MASS = 0.01


def is_marker_body(node, body):
    """True when this collision body is an Oblivion collision-toggle proxy
    rather than a real ragdoll limb (see _MARKER_MAX_MASS)."""
    shape = getattr(body, 'shape', None)
    if shape is None:
        return True
    radius = getattr(shape, 'radius', None)
    if radius is not None and float(radius) <= 0.01:
        return True     # zero-volume marker (alit's EnableCollisions)
    return float(getattr(body, 'mass', 0.0)) <= _MARKER_MAX_MASS


def plan_ragdoll_tree(data, exclude_markers=True):
    """Plan the single constrained tree over EVERY collision body in a
    creature skeleton.nif (works on Oblivion source or mid-conversion data;
    `exclude_markers` applies is_marker_body, whose thresholds are SOURCE
    units -- pass False on converted data, where the markers are already
    physically stripped and every surviving body is real).

    THE ENGINE CONTRACT (SkyrimSE ragdoll attach at actor Load3D, Address
    Library id 63792 = GOG/AE exe 0xb33f70, read 2026-08-28 for the
    Morroblivion alit crash): the engine walks the skeleton.NIF in pre-order
    DFS, collecting every blend body into list A and EVERY constraint of every
    body into list B; then for each hkx ragdoll body i it finds the NIF body
    j with the same bone name and overwrites hkx constraint[i-1] with NIF
    constraint B[j-1] -- no bounds check, no entity check.  Hence:
      * the FIRST body in DFS order is the ragdoll root: index 0 in the hkx,
        0 constraints in the NIF;
      * every other body carries EXACTLY ONE constraint in the NIF -- its
        joint to an EARLIER body -- and sits at the same index in the hkx;
      * an hkx root that is not the NIF's first body reads B[-1]
        (uninitialized stack) -> EXCEPTION_ACCESS_VIOLATION in
        hkpConstraintUtils::convertToPowered (the alit crash: Spine and Neck
        constrain EACH OTHER and nothing constrains Bip01 NonAccum, the first
        body, so the old cycle-breaker made Spine the root); a body with 2
        constraints shifts every later slot (mudcrab); a first body
        constrained to a LATER body put the root second (landdreugh).
    Bodies outside one connected tree also crash (2026-07-09 Storm Atronach:
    an uninitialized hkpPositionConstraintMotor) -- vanilla atronachstorm
    constrains all 26 orbiting rocks to their parent bones.

    Per body, in DFS order, the parent joint is: (1) its own authored
    constraint to an EARLIER body, else (2) an earlier body's authored
    constraint that names it -- the same joint with its ends swapped, else
    (3) a synthetic vanilla-template joint to its nearest body-carrying NIF
    ancestor (fallback: the root).  Every other authored constraint is
    dropped by the NIF side (collision.enforce_ragdoll_tree), which rebuilds
    each body's list from this same plan so both files agree.

    Returns None when there are fewer than 2 bodies, else a dict:
      body_nodes  [NiNode] every body-carrying node under the bone root,
                  pre-order DFS == the hkx part order
      edges       {id(child): parent NiNode} authored joints (cases 1 and 2)
      edge_con    {id(child): (constraint block, reversed)} the block behind
                  each authored edge; reversed=True means it was authored on
                  the parent with the child as entity B
      synthetic   [(child NiNode, parent NiNode)] joints to ADD (case 3)
      worlds      {id(node): (R 3x3 row-conv, t vec3)} world transforms in
                  game units
      root        body_nodes[0]
      node_of_id  {id(node): NiNode}
      bone_order  {id(node): DFS index over ALL NiNodes} == the anim bone index
    """
    from asset_convert.havok.hkx_skeleton import find_skeleton_root
    try:
        skel_root = find_skeleton_root(data)
    except ValueError:
        return None

    body_nodes = []
    node_parent = {}
    worlds = {}
    # DFS visit order == hkx_skeleton.collect_bones' anim bone index: both
    # walk this same tree, from the same find_skeleton_root(), taking
    # NiNode children in order.  This is the ONLY stable identity for a node
    # across the two parses (extract_ragdoll re-reads the NIF, so id() does
    # not carry), and unlike the node NAME it is unique — see anim_idx.
    bone_order = {}

    def _local(node):
        m = node.rotation
        R = np.array([[m.m_11, m.m_12, m.m_13],
                      [m.m_21, m.m_22, m.m_23],
                      [m.m_31, m.m_32, m.m_33]], dtype=float) \
            * float(node.scale)
        t = np.array([node.translation.x, node.translation.y,
                      node.translation.z], dtype=float)
        return R, t

    def visit(node, parent, R_p, t_p):
        R_l, t_l = _local(node)
        R_w = R_l @ R_p
        t_w = t_l @ R_p + t_p
        worlds[id(node)] = (R_w, t_w)
        node_parent[id(node)] = parent
        bone_order[id(node)] = len(bone_order)
        co = getattr(node, 'collision_object', None)
        if (co is not None and getattr(co, 'body', None) is not None
                and not (exclude_markers and is_marker_body(node, co.body))):
            body_nodes.append(node)
        for child in node.children:
            if isinstance(child, NifFormat.NiNode):
                visit(child, node, R_w, t_w)

    visit(skel_root, None, np.eye(3), np.zeros(3))

    if len(body_nodes) < 2:
        return None

    dfs_index = {id(n): i for i, n in enumerate(body_nodes)}
    body_of = {id(n): n.collision_object.body for n in body_nodes}
    node_of_body = {id(b): nid for nid, b in body_of.items()}
    node_of_id = {id(n): n for n in body_nodes}

    # every convertible authored joint whose two entities are both ragdoll
    # bodies, as (holder, other, block), holders in DFS order
    authored = []
    for n in body_nodes:
        body = body_of[id(n)]
        for con in getattr(body, 'constraints', []):
            kind, _d = _descriptor(con)
            if kind is None:
                continue
            ents = list(con.entities)
            if (len(ents) == 2 and ents[0] is body
                    and id(ents[1]) in node_of_body):
                authored.append((id(n), node_of_body[id(ents[1])], con))

    root = body_nodes[0]
    edges = {}
    edge_con = {}
    synthetic = []
    for n in body_nodes[1:]:
        nid = id(n)
        # (1) the body's own joint to an EARLIER body
        pick = next(((pid, con) for hid, pid, con in authored
                     if hid == nid and dfs_index[pid] < dfs_index[nid]), None)
        if pick is not None:
            edges[nid] = node_of_id[pick[0]]
            edge_con[nid] = (pick[1], False)
            continue
        # (2) an EARLIER body's joint that names this body: same joint,
        #     ends swapped
        pick = next(((hid, con) for hid, pid, con in authored
                     if pid == nid and dfs_index[hid] < dfs_index[nid]), None)
        if pick is not None:
            edges[nid] = node_of_id[pick[0]]
            edge_con[nid] = (pick[1], True)
            continue
        # (3) synthesize: nearest body-carrying NIF ancestor (always earlier
        #     in pre-order), else the root
        target = None
        anc = node_parent.get(nid)
        while anc is not None:
            if id(anc) in body_of:
                target = anc
                break
            anc = node_parent.get(id(anc))
        synthetic.append((n, target if target is not None else root))

    return {'body_nodes': body_nodes, 'edges': edges, 'edge_con': edge_con,
            'synthetic': synthetic, 'worlds': worlds, 'root': root,
            'node_of_id': node_of_id, 'bone_order': bone_order}


def _joint_info(kind, d, cid, pid, _to_bone):
    """Bone-space game-unit info dict for an authored joint descriptor with
    entity A = node cid and entity B = node pid."""
    if kind == 'ragdoll':
        return {
            'rows_a': _basis_rows(_to_bone(cid, v4(d.twist_a), 0),
                                  _to_bone(cid, v4(d.plane_a), 0)),
            'rows_b': _basis_rows(_to_bone(pid, v4(d.twist_b), 0),
                                  _to_bone(pid, v4(d.plane_b), 0)),
            'piv_a': _to_bone(cid, v4(d.pivot_a, OB_TO_GAME), 1),
            'piv_b': _to_bone(pid, v4(d.pivot_b, OB_TO_GAME), 1),
            'cone': float(d.cone_max_angle),
            'plane_min': float(d.plane_min_angle),
            'plane_max': float(d.plane_max_angle),
            'twist_min': float(d.twist_min_angle),
            'twist_max': float(d.twist_max_angle),
            'friction': 0.0,
        }
    axle_a = _to_bone(cid, v4(d.axle_a), 0)
    perp_a = getattr(d, 'perp_2_axle_in_a_1', None)
    rows_a = (_basis_rows(axle_a, _to_bone(cid, v4(perp_a), 0))
              if perp_a is not None
              else _basis_rows(axle_a, np.array([0.0, 0.0, 1.0])))
    axle_b = _to_bone(pid, v4(d.axle_b), 0)
    p2b = getattr(d, 'perp_2_axle_in_b_2', None)
    if p2b is not None:
        p1b = np.cross(unit(v4(p2b)), unit(v4(d.axle_b)))
        rows_b = _basis_rows(axle_b, _to_bone(pid, p1b, 0))
    else:
        rows_b = _basis_rows(axle_b, np.array([0.0, 0.0, 1.0]))
    if kind == 'hinge':
        min_a, max_a = float(d.min_angle), float(d.max_angle)
    else:
        min_a, max_a = -math.pi, math.pi
    return {
        'rows_a': rows_a, 'rows_b': rows_b,
        'piv_a': _to_bone(cid, v4(d.pivot_a, OB_TO_GAME), 1),
        'piv_b': _to_bone(pid, v4(d.pivot_b, OB_TO_GAME), 1),
        'min': min_a, 'max': max_a,
        'friction': 0.0,
    }


def swap_joint_ends(kind, info):
    """The same joint seen from the other entity: frames and pivots swap,
    and the relative-rotation limits negate (the inverse of a rotation by
    theta about a shared axis is -theta; the cone is symmetric)."""
    out = dict(info)
    out['rows_a'], out['rows_b'] = info['rows_b'], info['rows_a']
    out['piv_a'], out['piv_b'] = info['piv_b'], info['piv_a']
    if kind == 'ragdoll':
        out['plane_min'], out['plane_max'] = -info['plane_max'], -info['plane_min']
        out['twist_min'], out['twist_max'] = -info['twist_max'], -info['twist_min']
    else:
        out['min'], out['max'] = -info['max'], -info['min']
    return out


def _read_rig(skeleton_nif_path: str):
    """The parsed skeleton.nif, with ragdoll bodies synthesized when the rig
    authors none -- a Morrowind (NIF 4.0.0.2) skeleton carries no Havok at all.

    See: docs/commentary/asset_convert_creature.md#morrowind-synthetic-ragdolls
    """
    data = NifFormat.Data()
    with open(skeleton_nif_path, 'rb') as f:
        data.read(f)
    attach_synthetic_bodies(data, skeleton_nif_path)
    return data


def extract_ragdoll(skeleton_nif_path: str, bones: list):
    """Parse the Oblivion skeleton.nif into RagdollPart list (parent-before-
    child, constraints attached), or None when the skeleton has no ragdoll.

    EVERY blend-collision body becomes a ragdoll part of one connected
    constrained tree; unconstrained bodies (atronach rocks, detached
    skeleton-creature clusters) get synthetic vanilla-template joints to
    their nearest body-carrying ancestor (see plan_ragdoll_tree)."""
    plan = plan_ragdoll_tree(_read_rig(skeleton_nif_path))
    if plan is None:
        return None

    from asset_convert.havok.hkx_skeleton import BONE_RENAMES
    # Resolve a body's NiNode to its anim bone by TREE POSITION, not by name.
    # Both walks are the same pre-order DFS over the same NiNode tree, so the
    # positional key is exact even when a rig repeats a bone name (Oblivion
    # rigs do: Morroblivion's alit names all 24 of its collision proxies
    # CollisionNode/EnableCollisions).  The name lookup stays as a fallback
    # for a rig whose two walks disagree — a shape we have not seen.
    bone_index = {b.name: i for i, b in enumerate(bones)}
    bone_order = plan['bone_order']

    def anim_idx(node):
        name = decode_name(node)
        name = BONE_RENAMES.get(name, name)
        idx = bone_order.get(id(node))
        # Trust the positional key only when it lands on the bone the node
        # actually names; otherwise the two walks disagree (a rig shape we
        # have not seen) and the name lookup is the safer answer.
        if idx is not None and idx < len(bones) and bones[idx].name == name:
            return idx
        return bone_index.get(name)

    if any(anim_idx(n) is None for n in plan['body_nodes']):
        return None     # body outside the anim skeleton — no usable ragdoll

    body_of = {id(n): n.collision_object.body for n in plan['body_nodes']}

    # bone-from-body transform per body node: the body's rotation/translation
    # are its BIND WORLD transform (see module docstring) while our ragdoll
    # bone frames are the anim bone frames — row convention
    # v_bone = v_body @ R_delta + t_delta.
    xf_of = {}
    for n in plan['body_nodes']:
        body = body_of[id(n)]
        q = body.rotation
        R_bw = quat_to_mat_row((q.x, q.y, q.z, q.w))
        t_bw = v4(body.translation, OB_TO_GAME)
        R_bone, t_bone = plan['worlds'][id(n)]
        R_delta = R_bw @ R_bone.T
        t_delta = (t_bw - t_bone) @ R_bone.T
        xf_of[id(n)] = (R_delta, t_delta)

    def _to_bone(nid, v, is_point):
        R_delta, t_delta = xf_of[nid]
        out = np.asarray(v, dtype=float) @ R_delta
        return out + t_delta if is_point else out

    # per-child constraint info: real descriptors for planned edges,
    # synthetic vanilla-template ragdoll joints for the augmentation.
    # Everything is normalized here into bone-space game-unit dicts so the
    # XML emitters do no frame math.  Converted joints get friction 0.0
    # (vanilla creature census); synthetic rock joints keep the vanilla
    # atronach value.
    parent_of = {}          # id(child node) -> parent NiNode
    con_of = {}             # id(child node) -> (kind, info dict)
    for n in plan['body_nodes']:
        pick = plan['edge_con'].get(id(n))
        if pick is None:
            continue
        con, reversed_ = pick
        pnode = plan['edges'][id(n)]
        kind, d = _descriptor(con)
        cid, pid = id(n), id(pnode)
        if reversed_:
            # authored on the parent with (A=parent, B=child): build it as
            # authored, then swap the ends so A is the child (the hkx and
            # vanilla convention, see the module docstring)
            info = swap_joint_ends(kind, _joint_info(kind, d, pid, cid, _to_bone))
        else:
            info = _joint_info(kind, d, cid, pid, _to_bone)
        _legalize_limits(kind, info, plan['worlds'][cid][0],
                         plan['worlds'][pid][0])
        parent_of[id(n)] = pnode
        con_of[id(n)] = (kind, info)

    for child, pnode in plan['synthetic']:
        body = body_of[id(child)]
        cid, pid = id(child), id(pnode)
        R_cw, t_cw = plan['worlds'][cid]
        R_pw, t_pw = plan['worlds'][pid]
        # pivot at the child body COM, expressed in each bone's frame
        com_child = _to_bone(cid, v4(body.center, OB_TO_GAME), 1)
        com_w = com_child @ R_cw + t_cw
        piv_parent = (com_w - t_pw) @ R_pw.T
        R_rel = R_cw @ R_pw.T           # child-frame vec -> parent frame
        parent_of[id(child)] = pnode
        con_of[id(child)] = ('ragdoll', {
            'rows_a': _basis_rows(np.array([1.0, 0.0, 0.0]),
                                  np.array([0.0, 1.0, 0.0])),
            'rows_b': _basis_rows(unit(R_rel[0]), unit(R_rel[1])),
            'piv_a': com_child,
            'piv_b': piv_parent,
            'cone': _SYNTH_CONE,
            'plane_min': -_SYNTH_PLANE, 'plane_max': _SYNTH_PLANE,
            'twist_min': -_SYNTH_TWIST, 'twist_max': _SYNTH_TWIST,
            'friction': _SYNTH_FRICTION,
        })

    # part order == the NIF's pre-order DFS (the engine contract, see
    # plan_ragdoll_tree).  Every parent is earlier by construction.
    node_of_id = plan['node_of_id']
    order = [id(n) for n in plan['body_nodes']]

    part_of_node = {}
    parts = []
    used_names = {}
    for nid in order:
        node = node_of_id[nid]
        body = body_of[nid]
        idx = anim_idx(node)
        p = RagdollPart()
        p.anim_index = idx
        # Ragdoll bone names index the hkaSkeletonMapper and the resource
        # container tree, so they must be UNIQUE even when the source rig
        # reuses a bone name (alit's 12 'CollisionNode' proxies).  Suffix
        # only the repeats, so every rig that already had unique names keeps
        # byte-identical output.
        base = 'Ragdoll_' + bones[idx].name
        n = used_names.get(base, 0)
        used_names[base] = n + 1
        p.name = base if n == 0 else f'{base}_{n}'
        pnode = parent_of.get(nid)
        if pnode is not None and id(pnode) not in part_of_node:
            raise ValueError(
                f'ragdoll part {p.name!r} has parent {decode_name(pnode)!r} '
                f'AFTER it in NIF order -- plan_ragdoll_tree must only pick '
                f'earlier bodies')
        p.parent = part_of_node[id(pnode)] if pnode is not None else -1
        p.constraint = con_of.get(nid)

        # /_OB_MASS_DIV: Oblivion mass is authored against Oblivion-scale
        # lengths (see the constant) -- carrying it through unconverted while
        # scaling lengths x7 made every corpse feel immovable.
        p.mass = (float(body.mass) / _OB_MASS_DIV if body.mass > 0
                  else 1.0 / _OB_MASS_DIV)
        p.com = _to_bone(nid, v4(body.center, OB_TO_GAME), 1)
        shape = _capsule_from_shape(body.shape)
        if shape is not None:
            r, va, vb = shape
            p.shape = (r, _to_bone(nid, va, 1), _to_bone(nid, vb, 1))
        else:
            r = max(1.0, float(np.linalg.norm(p.com)))
            p.shape = (r, p.com - [0, 0, 0.5], p.com + [0, 0, 0.5])
        # Inertia is COMPUTED FROM THE CAPSULE, not carried from Oblivion
        # (2026-08-08, the rigid-ragdoll root cause).  Oblivion's authored
        # inertia diagonals are wildly ill-conditioned — our census of the
        # carried-through tensors hit 32x anisotropy on the forearm, 20x on
        # the thigh, vs vanilla Skyrim's WORST body at 6.6x — and a badly
        # ill-conditioned inertia on a constrained ragdoll body makes Havok's
        # joint solver diverge: the whole limp ragdoll stays RIGID, and the
        # destabilised island snaps to a fallback transform / drops out of
        # collision (teleport, fall-through-floor, can't be dragged, attached
        # parts lose their hitbox — every symptom, all on the frame physics
        # takes over).  Vanilla recomputes each body's tensor from its
        # capsule; so do we, giving the well-conditioned solid-capsule tensor
        # (analytic cylinder+hemisphere-caps approximation about the COM).
        # Computed for ALL parts after _widen_root_hub below, so the root hub's
        # tensor matches its final radius.

        part_of_node[nid] = len(parts)
        parts.append(p)

    for p in parts:
        p.inertia = capsule_inertia(p.shape, p.mass)
    return parts


# See: docs/commentary/asset_convert_creature.md#ragdoll-root-bone1-dead-end
# ---------------------------------------------------------------------------



def _keyframe_bone_sets(parts):
    """The three vanilla `hkbBoneIndexArray` sets, in ragdoll indices.

    Returns (keyframe_full, keyframe_lower, contact).  **Never `range(n)`** —
    that was the 2026-08-08 root cause of the whole broken-corpse cluster
    (rigid limbs, teleport on death, sinking through the floor, dead pick
    geometry on the attached parts).  `hkbKeyframeBonesModifier` PINS each
    listed ragdoll body to the animation pose, and a pinned body is
    immovable by the solver and generates no contacts, so keyframing every
    body left nothing for gravity to act on and nothing for
    `BSRagdollContactListenerModifier` to fire on.

    Vanilla dogbehavior (22-body dog ragdoll) is the model:

    * `KeyframeFullRagdoll` — death state 3 `AnimateToRagdoll`, 18/22 bones:
      everything EXCEPT the deepest limb LEAVES (LBackLegToe, RFrontLeg2,
      L/RFrontLegPalm).  The extremities are already free the frame the
      ragdoll enters the world, so gravity gets a purchase and the corpse
      starts folding; the `Ragdoll` clip trigger then releases the rest.
    * `KeyframeLowerBody` — the LIVE root state, 17/22 bones: everything
      EXCEPT the tail chain, neck and head, which hang free so a living
      creature's tail wags and its head bobs under physics.
    * `CollisionListener.bones` — 8/22: the bones that actually touch the
      world (limb ROOTS, spine, neck, head), never the toe/palm tips.  These
      are what convert floor contact into the `Ragdoll` release event.
    """
    n = len(parts)
    children = {}
    for i, p in enumerate(parts):
        if p.parent >= 0:
            children.setdefault(p.parent, []).append(i)
    leaves = [i for i in range(n) if i not in children]

    def _depth(i):
        d = 0
        while parts[i].parent >= 0:
            i = parts[i].parent
            d += 1
        return d

    # --- KeyframeFullRagdoll: drop the deepest limb leaves.  Vanilla drops 4
    # of 22 (18%); scale that ratio, always at least one leaf, and never so
    # many that the trunk itself comes loose.
    n_free = max(1, round(n * 4.0 / 22.0))
    free_at_death = sorted(leaves, key=_depth, reverse=True)[:n_free]
    kf_full = [i for i in range(n) if i not in set(free_at_death)]

    # --- KeyframeLowerBody: drop the tail/neck/head chains entirely (a bone
    # is loose if it OR any ancestor is named as a loose chain, so the whole
    # sub-chain below Tail1 or Neck comes free, matching vanilla).
    def _loose(i):
        j = i
        while j >= 0:
            if is_loose_bone(parts[j].name):
                return True
            j = parts[j].parent
        return False

    kf_lower = [i for i in range(n) if not _loose(i)]
    if not kf_lower:                       # all-loose rig (snake, tentacle)
        kf_lower = list(kf_full)

    # --- CollisionListener: only the bones that actually reach the world.
    # Vanilla dog is 8 of 22 (36%) — the four limb ROOTS (the first body of
    # each chain hanging off the trunk) plus the trunk's own spine/neck/head
    # links.  Deeper limb bodies and every leaf are excluded: a toe tip
    # scuffing the floor must not fire the `Ragdoll` release.
    # The TRUNK is the axial chain — the root plus every body whose name is a
    # spine/pelvis/neck/head link.  Walking "the single child that has
    # children" instead stops at the first spine node that also carries a
    # leg, which left the front limbs out of the contact set entirely.
    trunk = {0} | {i for i in range(n)
                   if bone_words(parts[i].name) & AXIAL_WORDS}
    # Limb roots: the first body of each chain hanging off the trunk that is
    # itself not a leaf (a lone leaf hanging off the spine is a fin/ear, not
    # a leg).
    limb_roots = {i for i in range(n)
                  if parts[i].parent in trunk and i not in trunk
                  and i in children}
    # The TAIL is excluded from contacts even though it is axial: vanilla's
    # 8-bone dog set is the four limb roots plus Spine1/Spine3/Neck2/Head,
    # with all three tail links absent.  A dragging tail must not fire the
    # `Ragdoll` release before the body has actually landed.
    spine_contacts = {i for i in trunk
                      if parts[i].parent >= 0
                      and not (bone_words(parts[i].name) & {'tail'})}
    contact = sorted(limb_roots | spine_contacts)
    if not contact:
        contact = [i for i in range(n)
                   if i in children and parts[i].parent >= 0] or [0]
    return kf_full, kf_lower, contact


def ragdoll_info(skeleton_nif_path: str, bones: list):
    """Slim summary for the behavior generator (death/ragdoll states):

    pose_bones — pose-matching picks in RAGDOLL skeleton indices.  Vanilla
    dogbehavior uses (COM, RBackLegPalm, LBackLegPalm): the trunk root plus
    two SYMMETRIC low extremities, a wide well-conditioned triangle.  The
    old pick (root + the two deepest chain tips) could hand Havok a
    near-collinear tail-tip/toe triangle, and the pose matcher then derives
    a garbage worldFromModel — the corpse visibly teleported sideways on
    death and its collision no longer aligned with the rendered body
    (2026-08-07).  Generic rule: root part + the lowest opposite-side leaf
    pair, widest apart in X; depth picks only as a last resort.

    keyframe_full / keyframe_lower / contact_bones — the three
    `hkbBoneIndexArray` sets the behavior graph needs.  **None of them is
    `range(parts)`** (2026-08-08 root cause: all three were, which pinned
    every ragdoll body to the animation pose forever — see
    `_keyframe_bone_sets`).
    """
    try:
        parts = extract_ragdoll(skeleton_nif_path, bones)
    except Exception:
        return None
    if not parts:
        return None

    def _depth(i):
        d = 0
        while parts[i].parent >= 0:
            i = parts[i].parent
            d += 1
        return d

    worlds = bone_worlds(bones)
    pos = [worlds[p.anim_index][1] for p in parts]
    parents = {p.parent for p in parts}
    leaves = [i for i in range(len(parts)) if i not in parents]
    zs = sorted(v[2] for v in pos)
    median_z = zs[len(zs) // 2]

    b1 = b2 = None
    low_leaves = [i for i in leaves if pos[i][2] <= median_z] or leaves
    best = -1.0
    for a in low_leaves:
        for b in low_leaves:
            if a >= b or pos[a][0] * pos[b][0] >= 0:
                continue  # need one left-side and one right-side extremity
            spread = abs(pos[a][0] - pos[b][0])
            if spread > best:
                best, b1, b2 = spread, a, b
    if b1 is None:  # no symmetric pair (snake-like chain): depth fallback
        order = sorted(range(len(parts)), key=_depth, reverse=True)
        b1 = order[0] if len(parts) > 1 else 0
        b2 = next((i for i in order if i not in (0, b1)), b1)

    kf_full, kf_lower, contact = _keyframe_bone_sets(parts)
    return {'parts': len(parts), 'pose_bones': (0, b1, b2),
            # the three vanilla hkbBoneIndexArray sets — see
            # _keyframe_bone_sets; NEVER range(parts)
            'keyframe_full': kf_full,
            'keyframe_lower': kf_lower,
            'contact_bones': contact,
            # part BONE names (the skeleton.nif NODE names, NOT the
            # 'Ragdoll_'-prefixed ragdoll-skeleton bone names), part order —
            # the import side builds the race's BPTD from these.  Vanilla
            # BPTD BPNN/BPNT name plain skeleton nodes ('Canine_Pelvis',
            # 'Scull'); the Ragdoll_ prefix shipped 2026-08-07 pointed every
            # body part at a nonexistent node.
            'part_bones': [bones[p.anim_index].name for p in parts]}


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


def _filter_info(part_index: int, parent_index: int) -> int:
    """Havok group-filter value for a ragdoll body (vanilla dog census):
    layer 0 (engine ORs the live layer in at attach), systemGroup 1, and
    the standard ragdoll subsystem chain — subSystemId = part+1,
    subSystemDontCollideWith = parent's subSystemId — so CONSTRAINED
    neighbours never collide while non-adjacent parts still do.  All-zero
    filter info lets every overlapping capsule collide with its neighbour
    and the ragdoll blasts itself apart on death (the 2026-07-16 mangled-
    ragdoll report, second root cause)."""
    sub = (part_index + 1) & 0x1F
    dont = ((parent_index + 1) & 0x1F) if parent_index >= 0 else 0
    return (1 << 16) | (dont << 10) | (sub << 5)


def _add_rigid_body(pf, part, world_R, world_t, filter_info=0):
    """hkpCapsuleShape + hkpRigidBody pair; returns (body, shape)."""
    shape = pf.add('hkpCapsuleShape')
    r, va, vb = part.shape
    shape.param('userData', 0)
    shape.param('radius', f'{r:.6f}')
    shape.param('vertexA', fmt_vec(va[0], va[1], va[2], r))
    shape.param('vertexB', fmt_vec(vb[0], vb[1], vb[2], r))

    com_w = part.com @ world_R + world_t
    quat = mat_row_to_quat(world_R)
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
\t\t\t<hkparam name="collisionFilterInfo">{filter_info}</hkparam>
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
    ix, iy, iz = part.inertia
    inv_m = 1.0 / part.mass
    # Motion type — vanilla creature ragdolls are NOT uniformly BOX_INERTIA
    # (2026-08-08, the rigid-corpse root cause).  Dog census: 18 BOX + 4
    # SPHERE_INERTIA, and the 4 spheres are exactly the ROUND bodies — the
    # COM/torso hub, the mid-spine hub, and the tiny leg-tip caps — each with
    # an ISOTROPIC inertia (0.001,0.001,0.001 / 0.094,0.094,0.094).  A round,
    # heavily-CONSTRAINED hub carrying a strongly anisotropic box tensor
    # (ours shipped Spine3 invInertia (0.0002,0.0017,0.0002), 9x anisotropy)
    # ill-conditions Havok's ragdoll solver: the joint iteration cannot
    # converge, so the whole limp ragdoll stays RIGID and the destabilised
    # island snaps to a fallback transform and drops out of collision (the
    # teleport / fall-through-floor / can't-be-dragged / attached-parts-lose-
    # -collision cluster, all on the frame physics takes over).  A body is
    # "round" when its capsule segment is short relative to its radius; those
    # get SPHERE_INERTIA with the isotropic (min-axis) tensor a sphere has.
    # Long limb capsules keep BOX_INERTIA + their real anisotropic tensor
    # (an isotropic tensor there makes thin limbs tumble unnaturally).
    seg_len = float(np.linalg.norm(np.asarray(vb) - np.asarray(va)))
    is_round = seg_len < 0.8 * r
    if is_round:
        iso = min(ix, iy, iz)
        ix = iy = iz = iso
    motion_type = 'MOTION_SPHERE_INERTIA' if is_round else 'MOTION_BOX_INERTIA'
    body.param_raw('motion', f'''<hkobject>
\t<hkparam name="type">{motion_type}</hkparam>
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
\t<hkparam name="inertiaAndMassInv">{fmt_vec(1.0 / ix, 1.0 / iy, 1.0 / iz, inv_m)}</hkparam>
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
    return body, shape


def _add_ragdoll_constraint_data(pf, info, motor_ref):
    """hkpRagdollConstraintData from a bone-space info dict (extract_ragdoll).

    motor_ref=None emits motors as null (the hkpPhysicsSystem copy);
    vanilla motorizes ONLY the hkaRagdollInstance constraint set."""
    motors = (f'{motor_ref} {motor_ref} {motor_ref}' if motor_ref
              else 'null null null')
    rows_a, rows_b = info['rows_a'], info['rows_b']
    piv_a, piv_b = info['piv_a'], info['piv_b']
    cone = info['cone']
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
\t\t\t<hkparam name="motors">{motors}</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="angFriction">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_ANG_FRICTION</hkparam>
\t\t\t<hkparam name="isEnabled">1</hkparam>
\t\t\t<hkparam name="firstFrictionAxis">0</hkparam>
\t\t\t<hkparam name="numFrictionAxes">3</hkparam>
\t\t\t<hkparam name="maxFrictionTorque">{info['friction']:.6f}</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="twistLimit">
\t\t<hkobject>
\t\t\t<hkparam name="type">TYPE_TWIST_LIMIT</hkparam>
\t\t\t<hkparam name="isEnabled">1</hkparam>
\t\t\t<hkparam name="twistAxis">0</hkparam>
\t\t\t<hkparam name="refAxis">1</hkparam>
\t\t\t<hkparam name="minAngle">{info['twist_min']:.6f}</hkparam>
\t\t\t<hkparam name="maxAngle">{info['twist_max']:.6f}</hkparam>
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
\t\t\t<hkparam name="minAngle">{info['plane_min']:.6f}</hkparam>
\t\t\t<hkparam name="maxAngle">{info['plane_max']:.6f}</hkparam>
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


def _add_hinge_constraint_data(pf, info, motor_ref=None):
    """hkpLimitedHingeConstraintData from a bone-space info dict
    (extract_ragdoll). Plain hinges get wide limits.

    motor_ref: hkpPositionConstraintMotor for the hkaRagdollInstance copy,
    None (null) for the hkpPhysicsSystem copy.  The engine's ragdoll attach
    dereferences the RAGDOLL set's angMotor.motor without a null check —
    hinge constraints with a null motor there crash SSE at actor Load3D
    (2026-07-09 Storm Atronach / Skeleton crash: every vanilla creature
    skeleton.hkx motorizes ALL ragdoll-instance constraints and nulls ALL
    physics-system copies)."""
    rows_a, rows_b = info['rows_a'], info['rows_b']
    piv_a, piv_b = info['piv_a'], info['piv_b']
    min_a, max_a = info['min'], info['max']
    friction = info['friction']

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
\t\t\t<hkparam name="motor">{motor_ref or 'null'}</hkparam>
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


def assert_ragdoll_invariants(parts):
    """Fail LOUDLY here rather than as an access violation in the user's game.

    hkaRagdollInstance requires a bijection between ragdoll bones and rigid
    bodies: `boneToRigidBodyMap` is written as range(len(parts)), and the
    hkaSkeletonMapper keys bones by NAME.  If either the anim-bone mapping
    or the part names alias, the engine's ragdoll attach leaves constraint
    instances with a null hkpConstraintData and
    hkpConstraintUtils::convertToPowered dereferences it at actor Load3D
    (2026-08-27 Morroblivion alit crash).  Both defects are silent in every
    offline check -- the file loads fine -- so assert on the OUTPUT.  So is
    the part ORDER: it must be the NIF's DFS order, parents first, because
    the engine indexes constraint[i-1] by it (see plan_ragdoll_tree).
    """
    dup_names = sorted({p.name for p in parts
                        if sum(q.name == p.name for q in parts) > 1})
    if dup_names:
        raise ValueError(
            f'ragdoll part names are not unique: {dup_names} '
            f'({len(parts)} parts) — would null the constraint data and '
            f'crash SSE at actor Load3D')
    seen = {}
    for p in parts:
        if p.anim_index in seen:
            raise ValueError(
                f'ragdoll parts {seen[p.anim_index]!r} and {p.name!r} both '
                f'map to anim bone {p.anim_index} — ragdoll bones must map '
                f'1:1 onto rigid bodies or SSE crashes at actor Load3D')
        seen[p.anim_index] = p.name
    for i, p in enumerate(parts):
        if p.parent >= 0 and p.constraint is None:
            raise ValueError(
                f'ragdoll part {p.name!r} (index {i}) has a parent but no '
                f'constraint — its hkpConstraintInstance would carry null '
                f'data and crash SSE at actor Load3D')
    for i, p in enumerate(parts):
        if p.parent >= i:
            raise ValueError(
                f'ragdoll part {p.name!r} (index {i}) has parent index '
                f'{p.parent} -- parents must precede children (the NIF DFS '
                f'order the engine indexes constraints by)')


def emit_ragdoll(pf, bones, parts, anim_skel_ref):
    """Emit the full ragdoll object set; returns the extra namedVariants."""
    assert_ragdoll_invariants(parts)
    worlds = bone_worlds(bones)

    # ragdoll hkaSkeleton — reference pose relative to the ragdoll parent
    rskel = pf.add('hkaSkeleton')
    rskel.param('name', parts[0].name)
    rskel.param_array('parentIndices', [p.parent for p in parts])
    rskel.param_structs('bones', [
        [('name', p.name), ('lockTranslation', p.parent != -1)]
        for p in parts])
    # The root's reference pose is its transform relative to the ACTOR ROOT
    # (anim bone 0).  Identical to writing world on every vanilla rig, whose
    # bone 0 sits at the origin; kept root-relative because Oblivion rigs
    # sometimes carry a bind transform on `Bip01` itself.
    #
    # NOTE: this is NOT what caused the teleport-on-death — that was
    # `lockTranslation` on the ragdoll root's mapped anim bone (see
    # hkx_skeleton.build_skeleton_xml).  Changing this alone measured as a
    # pure no-op on every broken creature.
    _R_actor, t_actor = worlds[0]
    pose_lines = []
    for p in parts:
        R, t = worlds[p.anim_index]
        if p.parent < 0:
            lt = (t - t_actor) @ _R_actor.T
            lq = mat_row_to_quat(R @ _R_actor.T)
        else:
            Rp, tp = worlds[parts[p.parent].anim_index]
            inv = Rp.T
            lt = (t - tp) @ inv
            lq = mat_row_to_quat(R @ inv)
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

    # unmappedBones are indices in skeleton B (vanilla dog census: the
    # ragdoll->anim mapper lists the 28 anim bones with no ragdoll part; the
    # anim->ragdoll mapper lists none).  Putting anim indices on the
    # anim->ragdoll mapper instead points past the end of the ragdoll
    # skeleton — out-of-range bone indices in the engine's pose mapper.
    mapped_anim = {p.anim_index for p in parts}
    unmapped_anim = [i for i in range(len(bones)) if i not in mapped_anim]
    map_r2a = _mapper(rskel.ref, anim_skel_ref,
                      [(ri, p.anim_index) for ri, p in enumerate(parts)],
                      unmapped_anim)
    map_a2r = _mapper(anim_skel_ref, rskel.ref,
                      [(p.anim_index, ri) for ri, p in enumerate(parts)],
                      [])

    # vanilla motor values (dog skeleton.hkx #0126) — the omitted-`type`
    # default is TYPE_INVALID, which the solver dispatches on; always emit it
    motor = pf.add('hkpPositionConstraintMotor')
    motor.param('type', 'TYPE_POSITION')
    motor.param('minForce', '-1000000.000000')
    motor.param('maxForce', '100.000000')
    motor.param('tau', '0.800000')
    motor.param('damping', '1.000000')
    motor.param('proportionalRecoveryVelocity', '5.000000')
    motor.param('constantRecoveryVelocity', '0.200000')

    body_shapes = [_add_rigid_body(pf, p, *worlds[p.anim_index],
                                   filter_info=_filter_info(i, p.parent))
                   for i, p in enumerate(parts)]
    bodies = [b for b, _s in body_shapes]
    shapes = [s for _b, s in body_shapes]

    def _constraints(motor_ref):
        insts = []
        for ri, p in enumerate(parts):
            if p.constraint is None or p.parent < 0:
                continue
            kind, info = p.constraint
            if kind == 'ragdoll':
                data = _add_ragdoll_constraint_data(pf, info, motor_ref)
            else:
                data = _add_hinge_constraint_data(pf, info, motor_ref)
            insts.append(_add_constraint_instance(
                pf, data.ref, bodies[ri].ref, bodies[p.parent].ref, p.name))
        return insts

    # vanilla duplicates the constraint graph: the hkaRagdollInstance set is
    # fully motored, the hkpPhysicsSystem set is fully null (bodies shared)
    con_ragdoll = _constraints(motor.ref)
    con_system = _constraints(None)

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

    # 'Resource Data' tree (vanilla creature skeleton.hkx, dog census): one
    # hkMemoryResourceContainer PER RAGDOLL PART, named after the part,
    # nested along the ragdoll parent tree, each holding two
    # hkMemoryResourceHandles — 'hkRigidBody' -> the part's hkpRigidBody and
    # 'hkpShapeInfo' -> an hkpShapeInfo naming the part and carrying its
    # bind world transform.  Our old empty container was the last structural
    # delta against vanilla: this tree is the Bethesda-side registry of the
    # ragdoll parts (name -> body/shape), so ship it verbatim.
    ident_t = ('(1.000000 0.000000 0.000000)(0.000000 1.000000 0.000000)'
               '(0.000000 0.000000 1.000000)(0.000000 0.000000 0.000000)')
    kids = {}
    for i, p in enumerate(parts):
        if p.parent >= 0:
            kids.setdefault(p.parent, []).append(i)
    part_res = [None] * len(parts)
    for i in reversed(range(len(parts))):   # children before their parent:
        p = parts[i]                        # hkxcmd rejects forward refs
        R, t = worlds[p.anim_index]
        sinfo = pf.add('hkpShapeInfo')
        sinfo.param('shape', shapes[i].ref)
        sinfo.param('isHierarchicalCompound', False)
        sinfo.param('hkdShapesCollected', False)
        sinfo.param_raw('childShapeNames',
                        f'<hkcstring>{p.name}</hkcstring>', numelements=1)
        sinfo.param_raw('childTransforms', ident_t, numelements=1)
        sinfo.param('transform', _fmt_transform_rows(R, t))
        h_rb = pf.add('hkMemoryResourceHandle')
        h_rb.param('variant', bodies[i].ref)
        h_rb.param('name', 'hkRigidBody')
        h_rb.param_array('references', [])
        h_si = pf.add('hkMemoryResourceHandle')
        h_si.param('variant', sinfo.ref)
        h_si.param('name', 'hkpShapeInfo')
        h_si.param_array('references', [])
        cont = pf.add('hkMemoryResourceContainer')
        cont.param('name', p.name)
        cont.param_array('resourceHandles', [h_rb.ref, h_si.ref])
        cont.param_array('children',
                         [part_res[j].ref for j in kids.get(i, [])])
        part_res[i] = cont

    resource = pf.add('hkMemoryResourceContainer')
    resource.param('name', '')
    resource.param_array('resourceHandles', [])
    resource.param_array('children',
                         [part_res[i].ref for i, p in enumerate(parts)
                          if p.parent < 0])

    return rskel, [
        [('name', 'Resource Data'),
         ('className', 'hkMemoryResourceContainer'),
         ('variant', resource.ref)],
        [('name', 'Physics Data'), ('className', 'hkpPhysicsData'),
         ('variant', pdata.ref)],
        [('name', 'RagdollInstance'), ('className', 'hkaRagdollInstance'),
         ('variant', ragdoll.ref)],
        # namedVariants mapper order is a hard vanilla contract (census
        # 2026-07-20, 30/30 creature skeleton.hkx): the anim->ragdoll mapper
        # is listed FIRST, ragdoll->anim second.  Reversed order feeds the
        # engine's death-pose transfer the wrong mapping table.
        [('name', 'SkeletonMapper'), ('className', 'hkaSkeletonMapper'),
         ('variant', map_a2r.ref)],
        [('name', 'SkeletonMapper'), ('className', 'hkaSkeletonMapper'),
         ('variant', map_r2a.ref)],
    ]
