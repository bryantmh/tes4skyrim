"""Carry an Oblivion skeleton.nif's ragdoll onto fitted bone positions.

Each bhkRigidBody keeps its offset from its bone; capsules stretch along a
segment whose length changed; every constraint's parent-side frame is re-seated
so the joint still closes at rest; bodies on bones the new mesh never weights
are removed with every constraint that names them.

See: skyb_retarget/README.md#the-ragdoll
"""
import numpy as np

from asset_convert.collision.collision_constraints import joint_descriptor
from asset_convert.havok.clip_retarget import mat_to_quat_wxyz, quat_wxyz_to_mat
from pyffi.formats.nif import NifFormat
from skyb_retarget.skin_data import block_name

#: Game units per Havok unit in an Oblivion NIF's bhk blocks.
HAVOK_SCALE = 7.0


def body_world(rb) -> np.ndarray:
    """A bhkRigidBody's row-convention world matrix in game units."""
    q = rb.rotation
    m = np.eye(4)
    m[:3, :3] = quat_wxyz_to_mat((q.w, q.x, q.y, q.z))
    m[3, :3] = np.array([rb.translation.x, rb.translation.y,
                         rb.translation.z]) * HAVOK_SCALE
    return m


def set_body_world(rb, m: np.ndarray) -> None:
    """Write a game-unit world matrix back onto a bhkRigidBody."""
    w, x, y, z = mat_to_quat_wxyz(m[:3, :3])
    rb.rotation.w, rb.rotation.x, rb.rotation.y, rb.rotation.z = (
        float(w), float(x), float(y), float(z))
    t = m[3, :3] / HAVOK_SCALE
    rb.translation.x, rb.translation.y, rb.translation.z = map(float, t)


def _vec(v) -> np.ndarray:
    """A PyFFI Vector3/4 as a 3-array."""
    return np.array([v.x, v.y, v.z], dtype=np.float64)


def _put(v, a) -> None:
    """Write a 3-array into a PyFFI Vector3/4."""
    v.x, v.y, v.z = map(float, a)


def _side_fields(desc, side: str) -> list:
    """Every vector field of one entity's side ('a' or 'b')."""
    return [n for n in desc._names if n.endswith('_' + side)
            or f'_in_{side}_' in n or n.endswith(f'_in_{side}')]


def reseat_constraint(constraint, owner, worlds_old: dict, worlds_new: dict) -> None:
    """Move the non-owner side's frame so it follows the owner rigidly."""
    desc = joint_descriptor(constraint)[1]
    ents = list(constraint.entities)
    if desc is None or len(ents) != 2:
        return
    other_side = 'b' if ents[0] is owner else 'a'
    other = ents[1] if ents[0] is owner else ents[0]
    t = (worlds_old[id(other)] @ np.linalg.inv(worlds_old[id(owner)])
         @ worlds_new[id(owner)] @ np.linalg.inv(worlds_new[id(other)]))
    for name in _side_fields(desc, other_side):
        v = getattr(desc, name)
        if name.startswith('pivot'):
            p = np.append(_vec(v) * HAVOK_SCALE, 1.0) @ t
            _put(v, p[:3] / HAVOK_SCALE)
        else:
            _put(v, _vec(v) @ t[:3, :3])


def stretch_capsule(rb, body_new: np.ndarray, pivot, axis_world, k: float) -> None:
    """Scale a capsule's points by `k` along `axis_world` about the bone `pivot`."""
    shape = rb.shape
    if not isinstance(shape, NifFormat.bhkCapsuleShape) or abs(k - 1.0) < 1e-3:
        return
    rot_t = body_new[:3, :3].T
    a = axis_world @ rot_t
    a /= np.linalg.norm(a)
    origin = (pivot - body_new[3, :3]) @ rot_t / HAVOK_SCALE
    for pt in (shape.first_point, shape.second_point):
        rel = _vec(pt) - origin
        _put(pt, origin + rel + a * np.dot(rel, a) * (k - 1.0))


def bone_bodies(data) -> dict:
    """{bone name: (NiNode, bhkRigidBody)} for every node carrying a body."""
    out = {}
    for b in data.blocks:
        if isinstance(b, NifFormat.NiNode) and b.collision_object is not None:
            out[block_name(b)] = (b, b.collision_object.body)
    return out


def carry_ragdoll(data, src, new_world, swing: dict) -> dict:
    """Move every body with its bone, stretch swung capsules, reseat constraints.

    `src` is the source clip_retarget.Skeleton, `new_world` its fitted world
    matrices, `swing` {bone: chain child}.  Returns {bone: capsule stretch}.
    """
    olds, news, stretch = {}, {}, {}
    for name, (_node, rb) in bone_bodies(data).items():
        i = src.index[name]
        old = body_world(rb)
        new = old @ np.linalg.inv(src.world[i]) @ new_world[i]
        olds[id(rb)], news[id(rb)] = old, new
        set_body_world(rb, new)
        child = swing.get(name)
        if child in src.index:
            c = src.index[child]
            seg_new = new_world[c][3, :3] - new_world[i][3, :3]
            seg_old = src.world[c][3, :3] - src.world[i][3, :3]
            k = float(np.linalg.norm(seg_new) / np.linalg.norm(seg_old))
            stretch[name] = round(k, 3)
            stretch_capsule(rb, new, new_world[i][3, :3], seg_new, k)
    for _name, (_node, rb) in bone_bodies(data).items():
        for c in rb.constraints:
            reseat_constraint(c, rb, olds, news)
    return stretch


def drop_bodies(data, names) -> list:
    """Remove the bodies on bones `names` and every constraint naming one."""
    bodies = bone_bodies(data)
    dropped = {id(bodies[n][1]) for n in names if n in bodies}
    for n in names:
        if n in bodies:
            bodies[n][0].collision_object = None
    for _node, rb in bone_bodies(data).values():
        keep = [c for c in rb.constraints
                if not any(id(e) in dropped for e in c.entities)]
        rb.num_constraints = len(keep)
        rb.constraints.update_size()
        for i, c in enumerate(keep):
            rb.constraints[i] = c
    return sorted(n for n in names if n in bodies)
