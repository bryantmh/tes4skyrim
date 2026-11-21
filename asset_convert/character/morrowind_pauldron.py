"""
Skyrim fit for a Morrowind pauldron.

Morrowind hangs a pauldron as rigid parts on its clavicle, upper-arm and
forearm attach nodes (some are skinned rigs). The assembler centres the arm
parts on the Skyrim arm and hangs the shoulder parts from the shoulder joint
(`morrowind_armor.RestSkeleton.place`). Here each bone's parts are pushed
straight out from the arm's centre line by a smooth field (a few low-order
terms along and around the bone): the least one leaving every visible face
clear of the skin, so a part keeps its shape instead of following the skin.
Rigid parts then take the weights of their own arm's skin under them.

See: docs/commentary/asset_convert_armor.md#morrowind-pauldron-fit
"""

from types import SimpleNamespace

import numpy as np
from scipy.optimize import linprog
from scipy.spatial import cKDTree

from asset_convert.character.body_slots import write_weights
from asset_convert.character.body_wrap import CLEAR_K, blended_clearance
from asset_convert.character.mesh_rays import ray_distance, ray_hits
from asset_convert.character.morrowind_armor import reference_arm
from asset_convert.character.morrowind_weights import (add_bones, blended_weights, bone_node, bone_share,
                                                       limb_centre, pruned, skin_reference)
from asset_convert.character.skin_retarget import (bake_block_transform, get_block_name, m44_to_np,
                                                   write_node_transform)
from asset_convert.character.skyrim_overrides import (OBLIVION_TO_SKYRIM_BONE_MAP, SBP_57_LEFT_PAULDRON,
                                                      SBP_58_RIGHT_PAULDRON)
from asset_convert.character.wrap_mesh import (build_adjacency, geom_triangles, group_mean,
                                               smooth_group_field, weld_groups)
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

#: Pauldron body slot -> the side ('L'/'R') of the arm it is worn on.
PAULDRON_SIDES = {SBP_57_LEFT_PAULDRON: 'L', SBP_58_RIGHT_PAULDRON: 'R'}

#: Skyrim arm bone word -> (its child bone, code): the segment a part on that bone is pushed out from.
ARM_CHILD = {'Clavicle': ('UpperArm', 'Uar'), 'UpperArm': ('Forearm', 'Lar'), 'Forearm': ('Hand', 'Hnd')}

#: Words of the Skyrim bones that turn with the arm when it is raised (the clavicle stays).
ARM_WORDS = ('UpperArm', 'Upperarm', 'Forearm', 'Hand', 'Finger')

#: Clearance (game units) a visible face keeps outside the skin.
PAULDRON_MARGIN = 0.4

#: Least share a skin triangle has on a bone (and its twists) for that bone's parts to clear it.
OWN_SHARE = 0.3

#: Most a face is pushed out (game units), and the halving steps that find its push.
MAX_PUSH, PUSH_STEPS = 4.0, 12

#: Powers along the bone, and angle harmonics around it, of the smooth push field.
FIELD_ORDER, FIELD_HARMONICS = 2, 1

#: How far out (game units) armor over a face hides it, and the gap a part keeps under another bone's part.
COVER_REACH, LAYER_GAP = 12.0, 0.2

#: Spacing (game units) of the points tested across each face, and the most steps along one face.
SAMPLE_STEP = 1.0
MAX_FACE_STEPS = 24

#: Smoothing passes of the taken skin weights over the piece's own mesh.
WEIGHT_PASSES = 16


# ---------------------------------------------------------------------------
# The pieces and the skin
# ---------------------------------------------------------------------------

def _pieces(skinned_geoms, skel_root) -> list:
    """[(index into skinned_geoms, block, frame 4x4, rigid)]: a Prn piece's frame is its bone node's
    world, a skinned shape's its own."""
    out = []
    for i, (block, is_prn, bone) in enumerate(skinned_geoms):
        rigid = bool(is_prn and bone)
        node = block.skin_instance.bones[0] if rigid else block
        out.append((i, block, m44_to_np(node.get_transform(skel_root)), rigid))
    return out


def _skeleton_points(block, frame, rigid: bool) -> np.ndarray:
    """A shape's vertices in skeleton space; a Prn piece stores offsets from its bone's pivot."""
    if rigid:
        bake_block_transform(block)
    verts = np.array([[v.x, v.y, v.z] for v in block.data.vertices], dtype=np.float64)
    return verts + frame[3, :3] if rigid else verts @ frame[:3, :3] + frame[3, :3]


def _store_points(block, points, frame) -> None:
    """Write skeleton-space `points` back into the skinned shape's own frame."""
    local = (points - frame[3, :3]) @ np.linalg.inv(frame[:3, :3])
    for vert, (x, y, z) in zip(block.data.vertices, local):
        vert.x, vert.y, vert.z = float(x), float(y), float(z)
    block.data.update_center_radius()


def arm_columns(names, side: str) -> np.ndarray:
    """Mask of the bone names that turn with the `side` arm."""
    return np.array([f' {side} ' in n and any(w in n for w in ARM_WORDS) for n in names])


def side_reference(ref, side: str):
    """`ref` cut to the triangles that weigh on the `side` arm or clavicle at any corner."""
    corners, corner_w, names = ref
    chain = arm_columns(names, side) | np.array([f'{side} Clavicle' in n for n in names])
    keep = (corner_w[..., chain].sum(axis=2) > 0).any(axis=1)
    return corners[keep], corner_w[keep], names


def skin_surface(corners) -> SimpleNamespace:
    """Skin triangles (tris, 3, 3) as the triangle soup `blended_clearance` reads."""
    verts, centres = corners.reshape(-1, 3), corners.mean(axis=1)
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    reach = np.linalg.norm(corners - centres[:, None], axis=2).max(axis=1)
    return SimpleNamespace(tris=np.arange(len(verts)).reshape(-1, 3), verts=verts, normals=normals,
                           tree=cKDTree(centres), centres=centres, reach=reach)


def clearance(surface, at) -> np.ndarray:
    """Signed clearance of points outside the skin; a point plainly far above it gets +inf unqueried."""
    near = surface.tree.query(at, k=1)[1]
    height = np.einsum('ij,ij->i', at - surface.centres[near], surface.normals[near])
    close = height <= PAULDRON_MARGIN + surface.reach[near]
    out = np.full(len(at), np.inf)
    if close.any():
        out[close] = blended_clearance(surface, at[close], surface.verts, surface.normals, surface.tree,
                                       k=CLEAR_K)[0]
    return out


# ---------------------------------------------------------------------------
# Bones, faces and the push
# ---------------------------------------------------------------------------

def _bone_of(block, rigid: bool) -> str:
    """The Skyrim name of the bone a part hangs on: a Prn piece's bone, a skinned shape's most-weighted one."""
    skin = block.skin_instance
    if not rigid:
        totals = [sum(vw.weight for vw in skin.data.bone_list[i].vertex_weights) for i in range(skin.num_bones)]
        return OBLIVION_TO_SKYRIM_BONE_MAP.get(get_block_name(skin.bones[int(np.argmax(totals))]), '')
    return OBLIVION_TO_SKYRIM_BONE_MAP.get(get_block_name(skin.bones[0]), '')


def bone_segment(sk_skel: dict, name: str):
    """(pivot, child pivot) at the Skyrim rest of an arm bone `name`, or None for any other bone."""
    word = next((w for w in ARM_CHILD if f' {w} ' in name), None)
    if word is None or name not in sk_skel:
        return None
    side = name.split()[1]
    return sk_skel[name][3, :3], sk_skel['NPC {0} {1} [{0}{2}]'.format(side, *ARM_CHILD[word])][3, :3]


def pierced(points, tris, segment) -> np.ndarray:
    """Mask of the faces the bone `segment` passes through: inside the limb whatever the fit."""
    return ray_hits(segment[0][None], (segment[1] - segment[0])[None], points[tris], 0.0, 1.0)[0]


def face_samples(points, tris) -> np.ndarray:
    """Points about SAMPLE_STEP apart across every face (corners included)."""
    edges = np.linalg.norm(points[tris] - points[np.roll(tris, 1, axis=1)], axis=2).max(axis=1)
    steps = np.clip(np.ceil(edges / SAMPLE_STEP), 1, MAX_FACE_STEPS).astype(int)
    out = [np.zeros((0, 3))]
    for k in np.unique(steps):
        grid = np.array([(i, j, k - i - j) for i in range(k + 1) for j in range(k + 1 - i)], float) / k
        out.append(np.einsum('gk,fkd->fgd', grid, points[tris[steps == k]]).reshape(-1, 3))
    return np.vstack(out)


def outward(at, centre, axis) -> np.ndarray:
    """Unit directions from the line through `centre` along `axis` out to `at`."""
    rel = at - centre
    out = rel - (rel @ axis)[:, None] * axis
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-6)


def cover_gap(points, faces, line, armor) -> np.ndarray:
    """Per face the distance straight out from its centre to `armor` corners within COVER_REACH; inf if none."""
    mid = points[faces].mean(axis=1)
    out = outward(mid, *line)
    return ray_distance(mid + out * 0.02, out, armor, 0.0, COVER_REACH) + 0.02


def push_needed(samples, line, surface) -> np.ndarray:
    """Per sample the least push straight out (to MAX_PUSH) leaving it PAULDRON_MARGIN clear; inf if none."""
    out = outward(samples, *line)
    low, high = np.zeros(len(samples)), np.full(len(samples), MAX_PUSH)
    never = clearance(surface, samples + out * MAX_PUSH) < PAULDRON_MARGIN
    for _ in range(PUSH_STEPS):
        mid = 0.5 * (low + high)
        clear = clearance(surface, samples + out * mid[:, None]) >= PAULDRON_MARGIN
        high, low = np.where(clear, mid, high), np.where(clear, low, mid)
    done = clearance(surface, samples) >= PAULDRON_MARGIN
    return np.where(done, 0.0, np.where(never, np.inf, high))


def _basis(at, line, span) -> np.ndarray:
    """Rows of the push field's terms at `at`: powers of the place along the bone times angle harmonics."""
    centre, axis = line
    across = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
    u = across / np.linalg.norm(across)
    rel = at - centre
    t = np.clip(((rel @ axis) - span[0]) / max(span[1] - span[0], 1e-6), 0.0, 1.0)
    angle = np.arctan2(rel @ np.cross(axis, u), rel @ u)
    ring = [np.ones_like(t)] + [f(h * angle) for h in range(1, FIELD_HARMONICS + 1) for f in (np.cos, np.sin)]
    return np.stack([t ** p * r for p in range(FIELD_ORDER + 1) for r in ring], axis=1)


def smooth_push(points, need, cap, line) -> np.ndarray:
    """Per point push of the least smooth field (mean over `points`) between 0 and MAX_PUSH at every point.

    `need` is (samples, least push at each), `cap` (places, most push at
    each). When no field keeps both, the caps give way: the skin wins.
    """
    along = (points - line[0]) @ line[1]
    span = (along.min(), along.max())
    at_points = _basis(points, line, span)
    rows = [-_basis(need[0], line, span), -at_points, at_points, _basis(cap[0], line, span)]
    zero = np.zeros(len(points))
    bound = [-need[1], zero, zero + MAX_PUSH, cap[1]]
    for keep in (4, 3):
        res = linprog(at_points.mean(axis=0), A_ub=np.vstack(rows[:keep]), b_ub=np.concatenate(bound[:keep]),
                      bounds=(None, None), method='highs')
        if res.success:
            return np.clip(at_points @ res.x, 0.0, MAX_PUSH)
    return zero + need[1].max()


def push_group(group, blocked, cap, extra) -> np.ndarray:
    """Per point push of one bone's parts (`smooth_push`): the least that clears every open face.

    `group` is its namespace from `_bone_groups`; `blocked` masks the faces
    under armor (they need not clear the skin); `cap` (places, most push) keeps
    faces under an already placed part beneath it; `extra` (places, least push)
    keeps this group above parts placed before it. Faces inside the Morrowind
    arm, where caps hide by design, need not clear either.
    """
    samples = face_samples(group.points, group.faces[~blocked])
    if group.hidden is not None and len(samples):
        samples = samples[clearance(group.hidden, samples) >= 0.0]
    need = push_needed(samples, group.line, group.surface)
    reach = np.isfinite(need) & (need > 0.0)
    places, needs = np.vstack([samples[reach], extra[0]]), np.concatenate([need[reach], extra[1]])
    if not len(needs):
        return np.zeros(len(group.points))
    return smooth_push(group.points, (places, needs), cap, group.line)


def _bone_groups(shapes, tris, pieces, fit) -> list:
    """[namespace] per arm bone carrying parts: members, points, faces, line, own skin, hidden skin.

    `fit` is (Skyrim rest skeleton, {side: (arm skin reference, Morrowind
    reference skin or None)}); faces the bone passes through are left out.
    """
    sk_skel, sides = fit
    by_bone = {}
    for k, (_i, block, _frame, rigid) in enumerate(pieces):
        by_bone.setdefault(_bone_of(block, rigid), []).append(k)
    out = []
    for name, members in by_bone.items():
        segment = bone_segment(sk_skel, name)
        if segment is None:
            continue
        arm, hidden = sides[name.split()[1]]
        points = np.vstack([shapes[k] for k in members])
        bounds = np.cumsum([0] + [len(shapes[k]) for k in members])
        faces = np.vstack([tris[k] + lo for k, lo in zip(members, bounds[:-1])])
        share = bone_share(arm, name)
        axis = (segment[1] - segment[0]) / np.linalg.norm(segment[1] - segment[0])
        out.append(SimpleNamespace(members=members, bounds=bounds, points=points, corners=points[faces],
                                   all_faces=faces, faces=faces[~pierced(points, faces, segment)],
                                   line=(limb_centre(arm, segment, share, points), axis),
                                   surface=skin_surface(arm[0][share > OWN_SHARE]), hidden=hidden))
    return out


def _gaps(group, others) -> np.ndarray:
    """(len(others) + 1, faces) distance out from each face to each other group's parts, then its own."""
    rows = [cover_gap(group.points, group.faces, group.line, g.corners) for g in others]
    return np.vstack(rows + [cover_gap(group.points, group.faces, group.line, group.corners)])


def expand_by_bone(shapes, tris, pieces, fit) -> list:
    """`shapes` with each arm bone's parts pushed out together, innermost bone first (`push_group`).

    `fit` is as `_bone_groups` takes. A bone's parts share one field, so a
    lining stays under its plate. A face under a placed bone's part stays
    LAYER_GAP under it; a face under a later bone's part obliges that part to
    move out at least as far.
    See: docs/commentary/asset_convert_armor.md#morrowind-pauldron-fit
    """
    groups = _bone_groups(shapes, tris, pieces, fit)
    covered = [np.isfinite(_gaps(g, [o for o in groups if o is not g])[:-1]).any(axis=0).mean() for g in groups]
    order = [groups[i] for i in np.argsort(covered, kind='stable')[::-1]]
    extra = {id(g): (np.zeros((0, 3)), np.zeros(0)) for g in groups}
    out = list(shapes)
    for n, group in enumerate(order):
        gaps = _gaps(group, order[:n] + order[n + 1:])
        done = gaps[:n].min(axis=0, initial=np.inf)
        cap = (group.points[group.faces[np.isfinite(done)]].mean(axis=1),
               np.maximum(done[np.isfinite(done)] - LAYER_GAP, 0.0))
        push = push_group(group, np.isfinite(gaps).any(axis=0), cap, extra[id(group)])
        _oblige(group, push, gaps[n:-1], order[n + 1:], extra)
        group.points = group.points + outward(group.points, *group.line) * push[:, None]
        group.corners = group.points[group.all_faces]
        for k, lo, hi in zip(group.members, group.bounds[:-1], group.bounds[1:]):
            out[k] = group.points[lo:hi]
    return out


def _oblige(group, push, gaps, later, extra) -> None:
    """Add to each later group's `extra` the least push keeping it LAYER_GAP above this group's faces."""
    face_push = push[group.faces].mean(axis=1)
    mid = group.points[group.faces].mean(axis=1)
    out = outward(mid, *group.line)
    for row, other in zip(gaps, later):
        hit = np.isfinite(row) & (face_push + LAYER_GAP > row)
        if hit.any():
            places, needs = extra[id(other)]
            extra[id(other)] = (np.vstack([places, mid[hit] + out[hit] * row[hit, None]]),
                                np.concatenate([needs, face_push[hit] + LAYER_GAP - row[hit]]))


# ---------------------------------------------------------------------------
# Weights and the fit
# ---------------------------------------------------------------------------

def _smoothed(weights, points, tris) -> np.ndarray:
    """`weights` smoothed WEIGHT_PASSES times over the piece's welded mesh graph."""
    group = weld_groups(points)
    n_groups = int(group.max()) + 1
    nbr_idx, nbr_ptr = build_adjacency(tris, group, n_groups)
    return smooth_group_field(group_mean(weights, group, n_groups), nbr_idx, nbr_ptr, WEIGHT_PASSES)[group]


def _skin_to(block, points, weights, names, place) -> None:
    """Store `points` and bind the piece to the bones `names` with `weights` (one column each)."""
    skel_root, nodes = place[0], place[2]
    for vert, (x, y, z) in zip(block.data.vertices, points):
        vert.x, vert.y, vert.z = float(x), float(y), float(z)
    block.data.update_center_radius()
    write_node_transform(block, np.eye(4))
    skin = block.skin_instance
    column = {OBLIVION_TO_SKYRIM_BONE_MAP.get(get_block_name(skin.bones[0]), ''): 0}
    used = [(n, weights[:, i]) for i, n in enumerate(names) if weights[:, i].any()]
    added = [bone_node(n, nodes, (skel_root, place[1])) for n, _col in used if n not in column]
    for node in added:
        column[OBLIVION_TO_SKYRIM_BONE_MAP.get(get_block_name(node), get_block_name(node))] = len(column)
    add_bones(skin, added)
    out = np.zeros((len(points), len(column)))
    for name, col in used:
        out[:, column[name]] += col
    write_weights(skin.data, out)


def _side_of(block, rigid: bool, default: str) -> str:
    """The side ('L'/'R') of the arm bone a part hangs on; `default` for a part off the arms."""
    words = _bone_of(block, rigid).split()
    return words[1] if len(words) > 2 and words[1] in ('L', 'R') else default


def _skin_side(pieces, shapes, tris, arm, place) -> None:
    """Weight one side's rigid pieces from that arm's skin (`arm`), smoothed over their mesh together."""
    points = np.vstack(shapes)
    starts = np.cumsum([0] + [len(p) for p in shapes])
    all_tris = np.vstack([t + lo for t, lo in zip(tris, starts[:-1])])
    weights = pruned(_smoothed(blended_weights(points, arm, cKDTree(arm[0].mean(axis=1))), points, all_tris))
    for (_i, block, frame, rigid), lo, hi in zip(pieces, starts[:-1], starts[1:]):
        if rigid:
            _skin_to(block, points[lo:hi], weights[lo:hi], arm[2], place)
        else:
            _store_points(block, points[lo:hi], frame)


def fit_pauldron(skinned_geoms, skel_root, sk_skel: dict, rig: tuple, body_part) -> int:
    """Push a pauldron's parts out clear of the Skyrim skin and skin its rigid ones like it; how many.

    Only a mesh authored for a pauldron slot (`body_part`); `rig` is (female,
    body weight). Each part fits the arm of its own bone's side. Runs at the
    Skyrim rest after the bones are placed, before the bind data is rebuilt; a
    skinned shape keeps its weights, a rigid piece stops being a Prn piece.
    See: docs/commentary/asset_convert_armor.md#morrowind-pauldron-fit
    """
    slot_side = PAULDRON_SIDES.get(body_part)
    pieces = _pieces(skinned_geoms, skel_root) if slot_side else []
    ref = skin_reference(*rig, keep=bool) if pieces else None
    if ref is None:
        return 0
    sides = {}
    for side in ('L', 'R'):
        hidden = reference_arm(rig[0], side)
        sides[side] = (side_reference(ref, side), skin_surface(hidden) if len(hidden) else None)
    shapes = [_skeleton_points(block, frame, rigid) for _i, block, frame, rigid in pieces]
    tris = [geom_triangles(block) for _i, block, _frame, _rigid in pieces]
    shapes = expand_by_bone(shapes, tris, pieces, (sk_skel, sides))
    nodes = {get_block_name(n): n for n in skel_root.tree() if isinstance(n, NifFormat.NiNode)}
    owner = [_side_of(block, rigid, slot_side) for _i, block, _frame, rigid in pieces]
    for side in sorted(set(owner)):
        mine = [k for k, o in enumerate(owner) if o == side]
        _skin_side([pieces[k] for k in mine], [shapes[k] for k in mine], [tris[k] for k in mine],
                   sides[side][0], (skel_root, sk_skel, nodes))
    for i, block, _frame, rigid in pieces:
        if rigid:
            skinned_geoms[i] = (block, False, None)
    return len(pieces)
