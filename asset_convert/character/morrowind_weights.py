"""
Skyrim skin weights for the hands and arms of a Morrowind wearable.

Morrowind binds a glove to three finger chains rooted inside the palm, and an
arm with no twist bones. Renamed onto Skyrim's five-finger hand and twisting
arm, the palm follows the fingers, the cuff stays still while the wrist turns,
and a sleeve rolls whole with the upper arm. Every vertex's share on those bones is replaced by the
weights at the closest point of the Skyrim skin (body and hands), so the
piece moves as the skin it covers.

See: docs/commentary/asset_convert_armor.md#morrowind-hand-weights
"""

import numpy as np
from scipy.spatial import cKDTree

from asset_convert.character.body_slots import slot_shapes, write_weights
from asset_convert.character.skin_retarget import get_block_name, write_node_transform
from asset_convert.character.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP
from asset_convert.character.wrap_mesh import closest_point_on_triangles
from asset_convert.nif.nif_flags import NIF_FLAGS
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

#: Bone-name words of the hand and arm chains, in every skeleton's naming.
_ARM_WORDS = ('Hand', 'Finger', 'Forearm', 'UpperArm', 'Upperarm')

#: Skyrim skin triangles, nearest by centroid, searched per armor vertex.
_CANDIDATES = 8

#: Influences kept per vertex, as vanilla skins carry.
_MAX_INFLUENCES = 4

#: Skyrim bone -> the source-skeleton name that is renamed onto it.
_SOURCE_NAME = {sk: src for src, sk in OBLIVION_TO_SKYRIM_BONE_MAP.items()}


def _is_arm(name: str) -> bool:
    """Whether a bone belongs to the hand or arm chain."""
    return any(word in name for word in _ARM_WORDS)


def _sk_name(name: str) -> str:
    """The Skyrim bone a (possibly source-named) bone ends up as."""
    return OBLIVION_TO_SKYRIM_BONE_MAP.get(name, name)


def skin_reference(female: bool, weight: int, keep=_is_arm):
    """(corners (tris, 3, 3), corner weights (tris, 3, bones), bone names) of the Skyrim body and hands, or None.

    Only bones `keep` accepts are columns, and only triangles whose three
    corners all weigh on one of them are kept.
    """
    sex = 'female' if female else 'male'
    shapes = [ss for part in ('body', 'hands') for ss in slot_shapes(f'{sex}{part}_{weight}.nif')]
    names = sorted({n for ss in shapes for n in ss.bones if keep(n)})
    if not names:
        return None
    column = {n: i for i, n in enumerate(names)}
    corners, corner_w = [], []
    for ss in shapes:
        t = ss.shape.translation
        points = ss.arrays['verts'] + np.array([t.x, t.y, t.z])
        w = np.zeros((len(points), len(names)))
        for bi, name in enumerate(ss.bones):
            if name in column:
                w[:, column[name]] += ss.arrays['weights'][:, bi]
        tris = ss.tris[(w.sum(axis=1) > 0)[ss.tris].all(axis=1)]
        corners.append(points[tris])
        corner_w.append(w[tris] / w[tris].sum(axis=2, keepdims=True))
    return np.vstack(corners), np.vstack(corner_w), names


def _skin_weights(block) -> np.ndarray:
    """(vertices, skin bones) weight matrix of a skinned shape."""
    sd = block.skin_instance.data
    w = np.zeros((block.data.num_vertices, sd.num_bones))
    for bi in range(sd.num_bones):
        for vw in sd.bone_list[bi].vertex_weights:
            w[vw.index, bi] += vw.weight
    return w


def bone_node(sk_name: str, nodes: dict, place):
    """The tree's node renamed onto `sk_name`, created at its Skyrim rest frame when absent."""
    skel_root, sk_skel = place
    name = _SOURCE_NAME.get(sk_name, sk_name)
    node = nodes.get(name) or nodes.get(sk_name)
    if node is None:
        node = NifFormat.NiNode()
        node.name = name.encode('latin-1')
        node.flags = NIF_FLAGS
        write_node_transform(node, sk_skel[sk_name])
        skel_root.add_child(node)
        nodes[name] = node
    return node


def add_bones(skin, new_nodes: list) -> None:
    """Append bone nodes to a skin instance and empty entries to its skin data."""
    first = skin.num_bones
    skin.num_bones = skin.data.num_bones = first + len(new_nodes)
    skin.bones.update_size()
    skin.data.bone_list.update_size()
    for i, node in enumerate(new_nodes):
        skin.bones[first + i] = node


def pruned(w: np.ndarray) -> np.ndarray:
    """Weights cut to the strongest `_MAX_INFLUENCES` per vertex and renormalized."""
    if w.shape[1] > _MAX_INFLUENCES:
        w[w < -np.sort(-w, axis=1)[:, [_MAX_INFLUENCES - 1]]] = 0.0
    return w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12)


def _barycentric(p, a, b, c) -> np.ndarray:
    """(n, 3) barycentric coordinates of points `p` on triangles (a, b, c)."""
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = (np.einsum('ni,ni->n', x, y) for x, y in ((v0, v0), (v0, v1), (v1, v1)))
    d20, d21 = np.einsum('ni,ni->n', v2, v0), np.einsum('ni,ni->n', v2, v1)
    denom = np.where(np.abs(d00 * d11 - d01 * d01) < 1e-12, 1.0, d00 * d11 - d01 * d01)
    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    return np.clip(np.stack([1.0 - v - w, v, w], axis=1), 0.0, 1.0)


def blended_weights(verts, ref, tree) -> np.ndarray:
    """Per vertex, the `skin_reference` weights at its closest point on that skin."""
    corners, corner_w = ref[0], ref[1]
    cand = tree.query(verts, k=min(_CANDIDATES, len(corners)))[1].reshape(len(verts), -1)
    tri = corners[cand]
    cp = closest_point_on_triangles(verts[:, None, :], tri[..., 0, :], tri[..., 1, :], tri[..., 2, :])
    rows = np.arange(len(verts))
    pick = np.argmin(np.linalg.norm(cp - verts[:, None, :], axis=2), axis=1)
    best, point = cand[rows, pick], cp[rows, pick]
    bary = _barycentric(point, corners[best, 0], corners[best, 1], corners[best, 2])
    bary /= np.maximum(bary.sum(axis=1, keepdims=True), 1e-12)
    return np.einsum('nk,nkb->nb', bary, corner_w[best])


def _reweight(block, ref, tree, nodes: dict, place) -> bool:
    """Give one shape's arm-bone share the reference skin's weights; whether it changed."""
    skin = block.skin_instance
    names = [get_block_name(b) for b in skin.bones]
    arm = [i for i, n in enumerate(names) if _is_arm(n)]
    w = _skin_weights(block)
    share = w[:, arm].sum(axis=1)
    hit = np.flatnonzero(share > 0)
    if not len(hit):
        return False
    verts = np.array([[v.x, v.y, v.z] for v in block.data.vertices])[hit]
    moved = blended_weights(verts, ref, tree)
    column = {_sk_name(n): i for i, n in enumerate(names)}
    used = [(n, col) for n, col in zip(ref[2], moved.T) if col.any()]
    added = [bone_node(n, nodes, place) for n, _col in used if n not in column]
    for k, node in enumerate(added):
        column[_sk_name(get_block_name(node))] = len(names) + k
    add_bones(skin, added)
    out = np.hstack([w, np.zeros((len(w), len(added)))])
    out[np.ix_(hit, arm)] = 0.0
    for name, col in used:
        out[hit, column[name]] += share[hit] * col
    write_weights(skin.data, pruned(out))
    return True


def reweight_arms(skinned_geoms, skel_root, sk_skel: dict, female: bool, weight: int) -> int:
    """Give every skinned shape's hand and arm share the Skyrim skin's weights; shapes changed.

    Runs on skeleton-space vertices at the Skyrim rest, after the bones are
    placed and before the bind data is rebuilt; bones the Skyrim skin needs
    and the piece lacks are added at their Skyrim rest frame.
    See: docs/commentary/asset_convert_armor.md#morrowind-hand-weights
    """
    ref = skin_reference(female, weight)
    if ref is None:
        return 0
    tree = cKDTree(ref[0].mean(axis=1))
    nodes = {get_block_name(n): n for n in skel_root.tree() if isinstance(n, NifFormat.NiNode)}
    return sum(_reweight(block, ref, tree, nodes, (skel_root, sk_skel))
               for block, is_prn, _bone in skinned_geoms if not is_prn)


def bone_share(arm, name: str) -> np.ndarray:
    """Per skin triangle, its mean weight on bone `name` and that bone's twists."""
    corners, corner_w, names = arm
    base, side = name.split()[2].lower(), name.split()[1]
    own = np.array([f' {side} ' in n and base in n.lower() for n in names])
    return corner_w[..., own].sum(axis=2).mean(axis=1)


def limb_centre(arm, segment, share, points) -> np.ndarray:
    """The centre of the Skyrim arm at `points`: the area-weighted centre of the skin mostly on the
    bone (`share` per triangle) across the stretch of `segment` the points span."""
    corners = arm[0]
    centres = corners.mean(axis=1)
    axis = (segment[1] - segment[0]) / np.linalg.norm(segment[1] - segment[0])
    lo, hi = np.percentile((points - segment[0]) @ axis, [5, 95])
    along = (centres - segment[0]) @ axis
    ring = (share > 0.5) & (along >= lo) & (along <= hi)
    area = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
    if not ring.any():
        return segment[0] + axis * 0.5 * (lo + hi)
    return (centres[ring] * area[ring, None]).sum(axis=0) / area[ring].sum()
