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
        r = float(shape.radius) * _OB_TO_GAME
        return (r, _v4(shape.first_point, _OB_TO_GAME),
                _v4(shape.second_point, _OB_TO_GAME))
    if name == 'bhkSphereShape':
        r = float(shape.radius) * _OB_TO_GAME
        eps = np.array([0.0, 0.0, max(0.1, r * 0.05)])
        return (r, -eps, eps)
    if name == 'bhkBoxShape':
        d = _v4(shape.dimensions, _OB_TO_GAME)     # half extents
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
        t = np.array([m.m_14, m.m_24, m.m_34]) * _OB_TO_GAME
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


# --- vanilla rock-joint template (atronachstorm skeleton.nif census: every
# free orbiting rock is ragdoll-constrained to its nearest body-carrying
# ancestor with exactly these limits) ---
_SYNTH_CONE = 0.872665          # 50 deg
_SYNTH_PLANE = 1.570796         # +/- 90 deg
_SYNTH_TWIST = 0.087266         # +/- 5 deg
_SYNTH_FRICTION = 10.0


def _decode_name(node):
    return bytes(node.name).decode('latin-1').rstrip('\x00')


def plan_ragdoll_tree(data):
    """Plan the single constrained tree over EVERY collision body in a
    creature skeleton.nif (works on Oblivion source or mid-conversion data).

    ENGINE CONTRACT (2026-07-09 Storm Atronach / Skeleton Load3D crash): the
    SSE ragdoll attach walks constraints across ALL bhkBlendCollisionObject
    bodies; any body outside one connected constrained tree leaves the walk
    dereferencing an uninitialized hkpPositionConstraintMotor pointer ->
    EXCEPTION_ACCESS_VIOLATION.  Vanilla atronachstorm constrains all 26
    free orbiting rocks to their parent bones (27 bodies / 26 constraints);
    Oblivion ships those rocks UNCONSTRAINED, so joints must be synthesized.

    Returns None when there are fewer than 2 bodies, else a dict:
      body_nodes  [NiNode] every body-carrying node under the bone root, DFS
      edges       {id(child): parent NiNode} existing constraint links
                  (first valid constraint per body; cycles broken)
      synthetic   [(child NiNode, parent NiNode)] joints to ADD so the graph
                  becomes one tree (nearest body-carrying NIF ancestor,
                  fallback = the main tree root)
      worlds      {id(node): (R 3x3 row-conv, t vec3)} world transforms in
                  game units
      root        the main tree root NiNode
      node_of_id  {id(node): NiNode}
    """
    from asset_convert.hkx_skeleton import find_skeleton_root
    try:
        skel_root = find_skeleton_root(data)
    except ValueError:
        return None

    body_nodes = []
    node_parent = {}
    worlds = {}

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
        co = getattr(node, 'collision_object', None)
        if co is not None and getattr(co, 'body', None) is not None:
            body_nodes.append(node)
        for child in node.children:
            if isinstance(child, NifFormat.NiNode):
                visit(child, node, R_w, t_w)

    visit(skel_root, None, np.eye(3), np.zeros(3))

    if len(body_nodes) < 2:
        return None

    dfs_index = {id(n): i for i, n in enumerate(body_nodes)}
    body_of = {id(n): n.collision_object.body for n in body_nodes}
    node_of_body = {id(b): nid for nid, b in
                    ((id(n), body_of[id(n)]) for n in body_nodes)}
    node_of_id = {id(n): n for n in body_nodes}

    # existing constraint links: first valid constraint per body whose
    # entities are (self, another body)
    edges = {}
    for n in body_nodes:
        body = body_of[id(n)]
        for con in getattr(body, 'constraints', []):
            kind, d = _descriptor(con)
            if kind is None:
                continue
            ents = list(con.entities)
            if (len(ents) == 2 and ents[0] is body
                    and id(ents[1]) in node_of_body):
                edges[id(n)] = node_of_id[node_of_body[id(ents[1])]]
                break

    # break constraint cycles (defensive; each body has <= 1 outgoing edge)
    for n in list(body_nodes):
        seen = set()
        nid = id(n)
        while nid in edges and nid not in seen:
            seen.add(nid)
            nid = id(edges[nid])
        if nid in seen:
            del edges[nid]

    # connected components (union-find over edges)
    uf = {}

    def find(x):
        r = x
        while uf.get(r, r) != r:
            r = uf[r]
        while uf.get(x, x) != x:
            uf[x], x = r, uf[x]
        return r

    for cid, pnode in edges.items():
        uf[find(cid)] = find(id(pnode))

    comps = {}
    for n in body_nodes:
        comps.setdefault(find(id(n)), []).append(id(n))
    comp_roots = {}     # component key -> tree root id (no outgoing edge)
    for key, members in comps.items():
        roots = [m for m in members if m not in edges]
        comp_roots[key] = roots[0]

    # main root = root of the largest component (ties: earliest DFS)
    main_key = max(comps, key=lambda k: (len(comps[k]),
                                         -dfs_index[comp_roots[k]]))
    main_root = node_of_id[comp_roots[main_key]]

    # synthesize joints: link every other component root to its nearest
    # body-carrying NIF ancestor outside its own component (vanilla rock
    # pattern), fallback = the main tree root
    synthetic = []
    other_roots = sorted((comp_roots[k] for k in comps if k != main_key),
                         key=lambda nid: dfs_index[nid])
    for rid in other_roots:
        child = node_of_id[rid]
        target = None
        anc = node_parent.get(rid)
        while anc is not None:
            if id(anc) in body_of and find(id(anc)) != find(rid):
                target = anc
                break
            anc = node_parent.get(id(anc))
        if target is None and find(id(main_root)) != find(rid):
            target = main_root
        if target is None:
            continue
        synthetic.append((child, target))
        uf[find(rid)] = find(id(target))

    return {'body_nodes': body_nodes, 'edges': edges, 'synthetic': synthetic,
            'worlds': worlds, 'root': main_root, 'node_of_id': node_of_id}


def extract_ragdoll(skeleton_nif_path: str, bones: list):
    """Parse the Oblivion skeleton.nif into RagdollPart list (parent-before-
    child, constraints attached), or None when the skeleton has no ragdoll.

    EVERY blend-collision body becomes a ragdoll part of one connected
    constrained tree; unconstrained bodies (atronach rocks, detached
    skeleton-creature clusters) get synthetic vanilla-template joints to
    their nearest body-carrying ancestor (see plan_ragdoll_tree)."""
    data = NifFormat.Data()
    with open(skeleton_nif_path, 'rb') as f:
        data.read(f)

    plan = plan_ragdoll_tree(data)
    if plan is None:
        return None

    from asset_convert.hkx_skeleton import BONE_RENAMES
    bone_index = {b.name: i for i, b in enumerate(bones)}

    def anim_idx(node):
        name = _decode_name(node)
        return bone_index.get(BONE_RENAMES.get(name, name))

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
        R_bw = _quat_to_mat_row((q.x, q.y, q.z, q.w))
        t_bw = _v4(body.translation, _OB_TO_GAME)
        R_bone, t_bone = plan['worlds'][id(n)]
        R_delta = R_bw @ R_bone.T
        t_delta = (t_bw - t_bone) @ R_bone.T
        xf_of[id(n)] = (R_delta, t_delta)

    def _to_bone(nid, v, is_point):
        R_delta, t_delta = xf_of[nid]
        out = np.asarray(v, dtype=float) @ R_delta
        return out + t_delta if is_point else out

    def _unit(v):
        v = np.asarray(v, dtype=float)
        return v / (np.linalg.norm(v) or 1.0)

    # per-child constraint info: real descriptors for planned edges,
    # synthetic vanilla-template ragdoll joints for the augmentation.
    # Everything is normalized here into bone-space game-unit dicts so the
    # XML emitters do no frame math.  Converted joints get friction 0.0
    # (vanilla creature census); synthetic rock joints keep the vanilla
    # atronach value.
    parent_of = {}          # id(child node) -> parent NiNode
    con_of = {}             # id(child node) -> (kind, info dict)
    for n in plan['body_nodes']:
        pnode = plan['edges'].get(id(n))
        if pnode is None:
            continue
        body, pbody = body_of[id(n)], body_of[id(pnode)]
        for con in getattr(body, 'constraints', []):
            kind, d = _descriptor(con)
            if kind is None:
                continue
            ents = list(con.entities)
            if not (len(ents) == 2 and ents[0] is body and ents[1] is pbody):
                continue
            cid, pid = id(n), id(pnode)
            if kind == 'ragdoll':
                info = {
                    'rows_a': _basis_rows(_to_bone(cid, _v4(d.twist_a), 0),
                                          _to_bone(cid, _v4(d.plane_a), 0)),
                    'rows_b': _basis_rows(_to_bone(pid, _v4(d.twist_b), 0),
                                          _to_bone(pid, _v4(d.plane_b), 0)),
                    'piv_a': _to_bone(cid, _v4(d.pivot_a, _OB_TO_GAME), 1),
                    'piv_b': _to_bone(pid, _v4(d.pivot_b, _OB_TO_GAME), 1),
                    'cone': float(d.cone_max_angle),
                    'plane_min': float(d.plane_min_angle),
                    'plane_max': float(d.plane_max_angle),
                    'twist_min': float(d.twist_min_angle),
                    'twist_max': float(d.twist_max_angle),
                    'friction': 0.0,
                }
            else:
                axle_a = _to_bone(cid, _v4(d.axle_a), 0)
                perp_a = getattr(d, 'perp_2_axle_in_a_1', None)
                rows_a = (_basis_rows(axle_a, _to_bone(cid, _v4(perp_a), 0))
                          if perp_a is not None
                          else _basis_rows(axle_a, np.array([0.0, 0.0, 1.0])))
                axle_b = _to_bone(pid, _v4(d.axle_b), 0)
                p2b = getattr(d, 'perp_2_axle_in_b_2', None)
                if p2b is not None:
                    # stored basis B = (axle, p1, p2); p1 = p2 × axle
                    p1b = np.cross(_unit(_v4(p2b)), _unit(_v4(d.axle_b)))
                    rows_b = _basis_rows(axle_b, _to_bone(pid, p1b, 0))
                else:
                    rows_b = _basis_rows(axle_b, np.array([0.0, 0.0, 1.0]))
                if kind == 'hinge':
                    min_a, max_a = float(d.min_angle), float(d.max_angle)
                else:
                    min_a, max_a = -math.pi, math.pi
                info = {
                    'rows_a': rows_a, 'rows_b': rows_b,
                    'piv_a': _to_bone(cid, _v4(d.pivot_a, _OB_TO_GAME), 1),
                    'piv_b': _to_bone(pid, _v4(d.pivot_b, _OB_TO_GAME), 1),
                    'min': min_a, 'max': max_a,
                    'friction': 0.0,
                }
            parent_of[id(n)] = pnode
            con_of[id(n)] = (kind, info)
            break

    for child, pnode in plan['synthetic']:
        body = body_of[id(child)]
        cid, pid = id(child), id(pnode)
        R_cw, t_cw = plan['worlds'][cid]
        R_pw, t_pw = plan['worlds'][pid]
        # pivot at the child body COM, expressed in each bone's frame
        com_child = _to_bone(cid, _v4(body.center, _OB_TO_GAME), 1)
        com_w = com_child @ R_cw + t_cw
        piv_parent = (com_w - t_pw) @ R_pw.T
        R_rel = R_cw @ R_pw.T           # child-frame vec -> parent frame
        parent_of[id(child)] = pnode
        con_of[id(child)] = ('ragdoll', {
            'rows_a': _basis_rows(np.array([1.0, 0.0, 0.0]),
                                  np.array([0.0, 1.0, 0.0])),
            'rows_b': _basis_rows(_unit(R_rel[0]), _unit(R_rel[1])),
            'piv_a': com_child,
            'piv_b': piv_parent,
            'cone': _SYNTH_CONE,
            'plane_min': -_SYNTH_PLANE, 'plane_max': _SYNTH_PLANE,
            'twist_min': -_SYNTH_TWIST, 'twist_max': _SYNTH_TWIST,
            'friction': _SYNTH_FRICTION,
        })

    # part order: DFS over the final tree (parent-before-child by
    # construction, required by hkaSkeleton parentIndices)
    dfs_index = {id(n): i for i, n in enumerate(plan['body_nodes'])}
    children = {}
    for cid, pnode in parent_of.items():
        children.setdefault(id(pnode), []).append(cid)
    for lst in children.values():
        lst.sort(key=dfs_index.__getitem__)

    node_of_id = plan['node_of_id']
    order = []
    stack = [id(plan['root'])]
    while stack:
        nid = stack.pop()
        order.append(nid)
        stack.extend(reversed(children.get(nid, [])))
    if len(order) != len(plan['body_nodes']):
        return None     # tree did not cover every body — bail to anim-only

    part_of_node = {}
    parts = []
    for nid in order:
        node = node_of_id[nid]
        body = body_of[nid]
        idx = anim_idx(node)
        p = RagdollPart()
        p.anim_index = idx
        p.name = 'Ragdoll_' + bones[idx].name
        pnode = parent_of.get(nid)
        p.parent = part_of_node[id(pnode)] if pnode is not None else -1
        p.constraint = con_of.get(nid)

        p.mass = float(body.mass) if body.mass > 0 else 1.0
        # anisotropic inertia (vanilla bodies are MOTION_BOX_INERTIA — an
        # isotropic sphere tensor makes long thin limbs tumble unnaturally).
        # The Oblivion diagonal is in the body frame; rotate the tensor into
        # our bone frame (R_delta is identity on real exports, kept for
        # safety) and keep the diagonal.
        R_delta, _t_delta = xf_of[nid]
        I_body = np.diag([max(0.0, body.inertia.m_11),
                          max(0.0, body.inertia.m_22),
                          max(0.0, body.inertia.m_33)]) * (_OB_TO_GAME ** 2)
        I_bone = np.abs(np.diag(R_delta.T @ I_body @ R_delta))
        p.com = _to_bone(nid, _v4(body.center, _OB_TO_GAME), 1)
        shape = _capsule_from_shape(body.shape)
        if shape is not None:
            r, va, vb = shape
            p.shape = (r, _to_bone(nid, va, 1), _to_bone(nid, vb, 1))
        else:
            r = max(1.0, float(np.linalg.norm(p.com)))
            p.shape = (r, p.com - [0, 0, 0.5], p.com + [0, 0, 0.5])
        if not np.all(I_bone > 0):
            r_bs = max(np.linalg.norm(p.shape[1]),
                       np.linalg.norm(p.shape[2])) + p.shape[0]
            fallback = 0.4 * p.mass * r_bs * r_bs
            I_bone = np.where(I_bone > 0, I_bone, fallback)
        p.inertia = tuple(float(x) for x in I_bone)

        part_of_node[nid] = len(parts)
        parts.append(p)

    return parts


def ragdoll_info(skeleton_nif_path: str, bones: list):
    """Slim summary for the behavior generator (death/ragdoll states):
    {'parts': n, 'pose_bones': (i0, i1, i2)} with pose-matching picks in
    RAGDOLL skeleton indices (vanilla uses pelvis + a leg + head; we pick the
    root part and the two deepest parts of distinct subtrees), or None when
    the skeleton has no usable ragdoll."""
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

    order = sorted(range(len(parts)), key=_depth, reverse=True)
    b1 = order[0] if len(parts) > 1 else 0
    b2 = next((i for i in order if i not in (0, b1)), b1)
    return {'parts': len(parts), 'pose_bones': (0, b1, b2)}


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
    body.param_raw('motion', f'''<hkobject>
\t<hkparam name="type">MOTION_BOX_INERTIA</hkparam>
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
    return body


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

    bodies = [_add_rigid_body(pf, p, *worlds[p.anim_index],
                              filter_info=_filter_info(i, p.parent))
              for i, p in enumerate(parts)]

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
