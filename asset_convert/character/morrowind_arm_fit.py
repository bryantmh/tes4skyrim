"""
Seat a Morrowind wearable's arm pieces on Skyrim's arm segments.

The pose cache turns Morrowind's arm into Skyrim's rest pose but keeps its
joints and bone lengths, so a sleeve hangs beside the Skyrim arm. Each vertex's
upper-arm and forearm share is carried from its posed bone onto the Skyrim
segment instead: the joint moved onto Skyrim's, the bone turned onto Skyrim's
direction, and the piece stretched along it to Skyrim's length.

See: docs/commentary/asset_convert_armor.md#morrowind-arm-segments
"""

import numpy as np
from scipy.spatial import cKDTree

from asset_convert.character.skin_retarget import get_block_name, m44_to_np
from asset_convert.character.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP

#: Morrowind arm bone -> the bone that ends its segment.
ARM_SEGMENTS = {'Bip01 R UpperArm': 'Bip01 R Forearm', 'Bip01 R Forearm': 'Bip01 R Hand',
                'Bip01 L UpperArm': 'Bip01 L Forearm', 'Bip01 L Forearm': 'Bip01 L Hand'}

#: Bind-space width (game units) of the band a seam's move is eased over.
SEAM_SIGMA = 3.0


def rotation_between(here, there) -> np.ndarray:
    """Row-vector rotation turning direction `here` onto `there`."""
    here = here / np.linalg.norm(here)
    there = there / np.linalg.norm(there)
    axis = np.cross(here, there)
    sine = np.linalg.norm(axis)
    if sine <= 1e-6:
        return np.eye(3)
    axis /= sine
    cosine = float(np.clip(here @ there, -1.0, 1.0))
    skew = np.array([[0, -axis[2], axis[1]],
                     [axis[2], 0, -axis[0]],
                     [-axis[1], axis[0], 0]])
    return (np.eye(3) + sine * skew + (1 - cosine) * skew @ skew).T


def stretch_along(offsets: np.ndarray, axis: np.ndarray, src_len: float, dst_len: float) -> np.ndarray:
    """Pivot offsets stretched along unit `axis` from a `src_len` bone to a `dst_len` one.

    Between the pivot and the child joint, distance along the bone scales by
    the length ratio; past the child joint it keeps its offset from that joint.
    """
    along = offsets @ axis
    moved = np.where(along > src_len, along - src_len + dst_len,
                     np.where(along > 0.0, along * (dst_len / src_len), along))
    return offsets + np.outer(moved - along, axis)


def _segment_maps(src_skel: dict, deltas: dict, sk_skel: dict) -> dict:
    """{lower-case arm bone: (delta, posed pivot, turn, axis, posed length, Skyrim length, Skyrim pivot)}."""
    maps = {}
    for bone, child in ARM_SEGMENTS.items():
        names = (bone, child, OBLIVION_TO_SKYRIM_BONE_MAP.get(bone), OBLIVION_TO_SKYRIM_BONE_MAP.get(child))
        if not all(n in src_skel for n in names[:2]) or not all(n in sk_skel for n in names[2:]) \
                or not all(n in deltas for n in names[:2]):
            continue
        posed = [(src_skel[n] @ deltas[n])[3, :3] for n in names[:2]]
        sk = [sk_skel[n][3, :3] for n in names[2:]]
        here, there = posed[1] - posed[0], sk[1] - sk[0]
        sk_len = float(np.linalg.norm(there))
        maps[bone.lower()] = (deltas[bone], posed[0], rotation_between(here, there), there / sk_len,
                              float(np.linalg.norm(here)), sk_len, sk[0])
    return maps


def _seat(bind: np.ndarray, seg) -> np.ndarray:
    """Bind-pose points carried by one arm bone onto its Skyrim segment."""
    delta, pivot, turn, axis, src_len, dst_len, sk_pivot = seg
    posed = bind @ delta[:3, :3] + delta[3, :3]
    return sk_pivot + stretch_along((posed - pivot) @ turn, axis, src_len, dst_len)


def _arm_weights(block, maps: dict) -> dict:
    """{arm bone: (vertex indices, weights)} for the shape's arm bones."""
    skin = block.skin_instance
    out = {}
    for i in range(min(skin.num_bones, skin.data.num_bones)):
        name = get_block_name(skin.bones[i]).lower() if skin.bones[i] is not None else ''
        if name in maps:
            bd = skin.data.bone_list[i]
            out[name] = (np.array([vw.index for vw in bd.vertex_weights], dtype=np.int64),
                         np.array([vw.weight for vw in bd.vertex_weights]))
    return out


def _total_weights(block) -> np.ndarray:
    """Per vertex, its summed skin weight over every bone."""
    total = np.zeros(block.data.num_vertices)
    for bd in block.skin_instance.data.bone_list:
        for vw in bd.vertex_weights:
            total[vw.index] += vw.weight
    return total


def _world_vertices(block, skel_root) -> tuple:
    """(skeleton-space vertices, the shape's 4x4) of one shape."""
    g = m44_to_np(block.get_transform(skel_root))
    return np.array([[v.x, v.y, v.z] for v in block.data.vertices]) @ g[:3, :3] + g[3, :3], g


def _shape_shift(block, bind: np.ndarray, maps: dict, posed: np.ndarray) -> tuple:
    """(per-vertex move from its posed place onto its arm bones' Skyrim segments, arm share of its weight).

    Also turns the shape's normals by its arm bones' turns, blended by weight.
    """
    data = block.data
    n = data.num_vertices
    target, share, turned = np.zeros((n, 3)), np.zeros(n), np.zeros((n, 3))
    normals = np.array([[v.x, v.y, v.z] for v in data.normals]) if getattr(data, 'has_normals', 0) else None
    for name, (idx, w) in _arm_weights(block, maps).items():
        np.add.at(target, idx, w[:, None] * _seat(bind[idx], maps[name]))
        np.add.at(share, idx, w)
        if normals is not None:
            np.add.at(turned, idx, w[:, None] * (normals[idx] @ maps[name][2]))
    total = np.maximum(_total_weights(block), 1e-6)
    if normals is not None and share.any():
        normals = normals * (1.0 - share / total)[:, None] + turned / total[:, None]
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
        for nrm, row in zip(data.normals, normals.tolist()):
            nrm.x, nrm.y, nrm.z = row
    hit = share > 1e-6
    move = np.where(hit[:, None], target / np.maximum(share, 1e-6)[:, None] - posed, 0.0)
    return move, share / total


def _eased(points: np.ndarray, move: np.ndarray, share: np.ndarray) -> np.ndarray:
    """Per vertex, its arm move scaled by the arm share smoothed over its bind-space neighbourhood.

    A vertex off the arm borrows its arm neighbours' move, so a hard
    torso/sleeve seam eases over a band instead of tearing, while the sleeve
    away from the seam stays exactly on its segment.
    """
    pairs = cKDTree(points).query_pairs(3.0 * SEAM_SIGMA, output_type='ndarray')
    i = np.concatenate([pairs[:, 0], pairs[:, 1], np.arange(len(points))])
    j = np.concatenate([pairs[:, 1], pairs[:, 0], np.arange(len(points))])
    k = np.exp(-0.5 * (np.linalg.norm(points[i] - points[j], axis=1) / SEAM_SIGMA) ** 2)
    arm, norm, borrowed = np.zeros(len(points)), np.zeros(len(points)), np.zeros_like(move)
    np.add.at(arm, i, k * share[j])
    np.add.at(norm, i, k)
    np.add.at(borrowed, i, (k * share[j])[:, None] * move[j])
    own = np.where((share > 1e-6)[:, None], move, borrowed / np.maximum(arm, 1e-9)[:, None])
    return (arm / norm)[:, None] * own


def bind_vertices(skinned_geoms, skel_root) -> dict:
    """{id(shape): skeleton-space bind vertices} for every non-Prn skinned shape, before the pose deform."""
    out = {}
    for block, is_prn, _bone in skinned_geoms:
        if is_prn or block.data is None or not block.data.num_vertices:
            continue
        g = m44_to_np(block.get_transform(skel_root))
        out[id(block)] = np.array([[v.x, v.y, v.z] for v in block.data.vertices]) @ g[:3, :3] + g[3, :3]
    return out


def seat_arms(skinned_geoms, skel_root, bind: dict, skeletons, deltas: dict) -> None:
    """Carry every posed shape's arm share onto the Skyrim arm segments.

    `bind` is `bind_vertices` taken before the pose deform; `skeletons` is
    (Morrowind rest skeleton, Skyrim skeleton); `deltas` the pose cache.
    Shapes without arm bones join the easing too, so a seam welded across
    two shapes moves as one; a shape the easing leaves still is not rewritten.
    See: docs/commentary/asset_convert_armor.md#morrowind-arm-segments
    """
    maps = _segment_maps(skeletons[0], deltas, skeletons[1])
    shapes = [b for b, is_prn, _bone in skinned_geoms if not is_prn and id(b) in bind]
    if not any(_arm_weights(b, maps) for b in shapes):
        return
    placed = [_world_vertices(b, skel_root) for b in shapes]
    shifts = [_shape_shift(b, bind[id(b)], maps, p) for b, (p, _g) in zip(shapes, placed)]
    eased = _eased(np.vstack([bind[id(b)] for b in shapes]), np.vstack([m for m, _s in shifts]),
                   np.concatenate([s for _m, s in shifts]))
    start = 0
    for block, (p, g) in zip(shapes, placed):
        move = eased[start:start + block.data.num_vertices]
        start += block.data.num_vertices
        if not move.any():
            continue
        for v, row in zip(block.data.vertices, ((p + move - g[3, :3]) @ np.linalg.inv(g[:3, :3])).tolist()):
            v.x, v.y, v.z = row
