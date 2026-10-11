"""Fit an Oblivion creature skeleton onto a Skyblivion mesh.

Bones keep their names, hierarchy and (unless SWUNG) their orientation
relative to their parent; landmarked bones move onto the joints the
Skyblivion artist painted.  The fitted skeleton.nif keeps the source's
Oblivion format, ragdoll included, so the unmodified converter builds the
Skyrim project from it.

See: skyb_retarget/README.md#fitting-the-skeleton
"""
import numpy as np

from asset_convert.havok.clip_retarget import Skeleton, rotation_between
from pyffi.formats.nif import NifFormat
from skyb_retarget.ragdoll_fit import carry_ragdoll, drop_bodies
from skyb_retarget.skin_data import block_name, read_nif


# ---------------------------------------------------------------------------
# Landmarks on the Skyblivion mesh
# ---------------------------------------------------------------------------

def _boundary(mesh, a: str, b: str) -> np.ndarray:
    """Centroid of the vertices weighted to both `a` and `b`."""
    w = mesh.column(a) * mesh.column(b)
    if w.sum() <= 0:
        raise ValueError(f'no vertex shares {a} and {b}')
    return (mesh.verts * w[:, None]).sum(0) / w.sum()


def _tip(mesh, bones) -> np.ndarray:
    """Centroid of the lowest vertices dominated by `bones` (a foot tip)."""
    w = sum(mesh.column(b) for b in bones)
    sel = mesh.verts[w > 0.5]
    low = sel[sel[:, 2] <= sel[:, 2].min() + 3.0]
    return low.mean(0)


def _reach(mesh, bone: str, frac: float, joint) -> np.ndarray:
    """`frac` of the way from `joint` to the far end of `bone`'s vertices."""
    sel = mesh.verts[mesh.column(bone) > 0.5]
    dist = np.linalg.norm(sel - joint, axis=1)
    far = sel[dist >= np.percentile(dist, 95)].mean(0)
    return joint + frac * (far - joint)


def ground_lift(mesh, landmarks: dict) -> float:
    """Height that puts the mean foot tip ('tip' landmarks) on z = 0."""
    tips = [_tip(mesh, s[1])[2] for s in landmarks.values() if s[0] == 'tip']
    return -float(np.mean(tips)) if tips else 0.0


def landmark_positions(mesh, landmarks: dict) -> dict:
    """{Oblivion bone: world position on the Skyblivion mesh}."""
    out = {}
    for bone, spec in landmarks.items():
        if spec[0] == 'boundary':
            out[bone] = _boundary(mesh, spec[1], spec[2])
        elif spec[0] == 'tip':
            out[bone] = _tip(mesh, spec[1])
    for bone, spec in landmarks.items():
        if spec[0] == 'midline':
            out[bone] = out[spec[1]] * np.array([0.0, 1.0, 1.0])
        elif spec[0] == 'reach':
            joint = next(out[b] for b, s in landmarks.items()
                         if s[0] == 'boundary' and s[2] == spec[1])
            out[bone] = _reach(mesh, spec[1], spec[2], joint)
    return out


# ---------------------------------------------------------------------------
# Fitted world transforms
# ---------------------------------------------------------------------------

def fitted_worlds(src: Skeleton, positions: dict, swing: dict) -> np.ndarray:
    """New rest world matrix per bone of `src` (parents before children)."""
    new = np.empty_like(src.world)
    for i, p in enumerate(src.parents):
        name = src.names[i]
        new[i] = src.local[i] if p < 0 else src.local[i] @ new[p]
        if name in positions:
            new[i][3, :3] = positions[name]
        child = swing.get(name)
        if child in src.index:
            old_dir = src.world[src.index[child]][3, :3] - src.world[i][3, :3]
            new_dir = (_child_position(src, new, positions, i, child)
                       - new[i][3, :3])
            new[i][:3, :3] = new[i][:3, :3] @ rotation_between(
                old_dir, new_dir, new[i][2, :3])
    return new


def _child_position(src: Skeleton, new, positions, i: int, child: str):
    """Where `child` will sit: its landmark, else rigidly off bone `i`."""
    if child in positions:
        return positions[child]
    return (src.local[src.index[child]] @ new[i])[3, :3]


# ---------------------------------------------------------------------------
# Writing the fitted skeleton.nif
# ---------------------------------------------------------------------------

def _write_transform(node, m: np.ndarray) -> None:
    """Set a NiAVObject's local transform from a row-convention 4x4."""
    _set_rows(node.rotation, m[:3, :3])
    node.translation.x, node.translation.y, node.translation.z = map(
        float, m[3, :3])
    node.scale = 1.0


def _set_rows(rot, r) -> None:
    """Write a 3x3 into a PyFFI Matrix33 row by row."""
    (rot.m_11, rot.m_12, rot.m_13), (rot.m_21, rot.m_22, rot.m_23), \
        (rot.m_31, rot.m_32, rot.m_33) = [tuple(map(float, row)) for row in r]


def unweighted_bones(src: Skeleton, weighted) -> list:
    """Bones that neither carry weight nor have a weighted descendant."""
    keep = set()
    for name in weighted:
        i = src.index.get(name, -1)
        while i >= 0:
            keep.add(i)
            i = src.parents[i]
    return [n for i, n in enumerate(src.names) if i not in keep]


def write_fitted_skeleton(src_nif: str, out_nif: str, new_world, swing: dict,
                          weighted) -> dict:
    """Write `src_nif` with fitted bones and ragdoll; returns a short report."""
    src = Skeleton.from_nif(src_nif)
    data = read_nif(src_nif)
    stretch = carry_ragdoll(data, src, new_world, swing)
    dropped = drop_bodies(data, unweighted_bones(src, weighted))
    write_bone_nodes(data, src, new_world)
    with open(out_nif, 'wb') as f:
        data.write(f)
    return {'capsule_stretch': stretch, 'dropped_bodies': dropped}


def write_bone_nodes(data, src: Skeleton, new_world: np.ndarray) -> None:
    """Give every bone NiNode its fitted local transform."""
    by_name = {block_name(b): b for b in data.blocks
               if isinstance(b, NifFormat.NiNode)}
    for i, p in enumerate(src.parents):
        node = by_name.get(src.names[i])
        if node is None or p < 0:
            continue
        _write_transform(node, new_world[i] @ np.linalg.inv(new_world[p]))
