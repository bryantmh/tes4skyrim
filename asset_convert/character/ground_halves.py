"""
A two-handed wearable's ground model as a left and a right half.

The ground model's pieces are split by hand when every piece names one and
both hands are there; otherwise each half is built from its hand of the worn
model, laid flat. A piece names its hand through the texture: its UVs are
the worn left hand's, so a rigid fit onto the left either keeps or mirrors
it. Each half gets one convex body over its own geometry, copying the
source ground model's rigid body.

See: docs/commentary/asset_convert_armor.md#split-pair-ground-models
"""

import os

import numpy as np
from scipy.spatial import cKDTree

from asset_convert.character.body_slots import (piece_ids, right_triangles, set_children,
                                                skin_weights, vertex_arrays, write_geometry)
from asset_convert.character.head_gear import strip_gnd_skin
from asset_convert.character.skin_replacement import is_body_skin_geometry
from asset_convert.character.skin_retarget import bake_geoms_to_bind_pose
from asset_convert.character.wrap_mesh import (NifFormat, block_name, geom_triangles, geom_world,
                                               iter_skinned_geoms, read_nif)
from asset_convert.collision.cms_builder import GAME_UNITS_PER_HAVOK
from asset_convert.collision.collision_hulls import build_convex_shape, set_box_inertia
from asset_convert.collision.collision_material import get_havok_material

#: Oblivion's Havok unit is a tenth of Skyrim's; collision conversion scales it back.
_OB_GAME_UNITS_PER_HAVOK = GAME_UNITS_PER_HAVOK / 10.0

#: Convex radius in Oblivion Havok units: a synthesized Skyrim clutter hull's 0.01.
_HULL_RADIUS = 0.1

#: UV distance within which a ground vertex sits on the same texel as a worn one.
_UV_MATCH = 1e-3

#: Texels a piece must share with the worn left hand before its fit names a hand.
_MIN_SHARED_UVS = 8

#: Per-vertex unit vectors a bake rotates with the positions.
_UNIT_ATTRS = ('normals', 'tangents', 'bitangents')


def write_ground_halves(ground: str, worn: str, targets) -> bool:
    """Write the (left, right) halves of the two-handed ground model `ground` to `targets`.

    `worn` is the same item's two-handed worn model. False, writing nothing,
    when a half would be empty.
    """
    left = _worn_left(worn)
    sides = _piece_sides(_ground_shapes(_ground_data(ground)), left) if left else None
    halves = [_split_half(ground, sides, right) if sides else _worn_half(worn, right)
              for right in (False, True)]
    if not all(halves):
        return False
    for data, target in zip(halves, targets):
        root = data.roots[0]
        template = _body_template(_ground_data(ground))
        root.collision_object = _collision(template, root) if template else None
        os.makedirs(os.path.dirname(str(target)), exist_ok=True)
        with open(target, 'wb') as f:
            data.write(f)
    return True


def _ground_data(ground: str):
    """A fresh read of `ground`, any skinned shape baked to its bind pose, then unskinned."""
    data = read_nif(ground)
    for shape, skel in list(iter_skinned_geoms(data)):
        bake_geoms_to_bind_pose([(shape, False, None)], skel)
    strip_gnd_skin(data)
    return data


def _ground_shapes(data) -> list:
    """(shape, root-space arrays, triangles, piece per triangle) for every shape of the ground model."""
    root = data.roots[0]
    out = []
    for block in root.tree():
        if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        if block.data is None or not block.data.num_vertices or not block.data.uv_sets:
            continue
        tris = geom_triangles(block)
        if len(tris):
            arrays = _root_arrays(block, vertex_arrays(block), root)
            out.append((block, arrays, tris, piece_ids(arrays['verts'], tris)[tris[:, 0]]))
    return out


def _worn_shapes(data) -> list:
    """(shape, skeleton root, triangles, right-hand triangles) of the worn model's gear, baked to its bind pose.

    Bare-skin shapes are left out.
    See: docs/commentary/asset_convert_armor.md#split-pair-ground-models
    """
    shapes = [(shape, skel) for shape, skel in iter_skinned_geoms(data)
              if not is_body_skin_geometry(shape)]
    out = []
    for shape, skel in shapes:
        bake_geoms_to_bind_pose([(shape, False, None)], skel)
        tris = geom_triangles(shape)
        bones = [block_name(b) for b in shape.skin_instance.bones]
        out.append((shape, skel, tris, right_triangles(bones, skin_weights(shape), tris)))
    return out


def _worn_left(worn: str):
    """(skeleton-space vertices, UV tree) of the worn model's left hand, bare skin left out; None if it has none."""
    verts, uvs = [], []
    for shape, skel, tris, right in _worn_shapes(read_nif(worn)):
        rows = np.unique(tris[~right])
        verts.append(geom_world(shape, skel)[0][rows])
        uvs.append(vertex_arrays(shape)['uvs'][rows])
    if not verts:
        return None
    verts, uvs = np.concatenate(verts), np.concatenate(uvs)
    finite = np.isfinite(uvs).all(axis=1)
    return verts[finite], cKDTree(uvs[finite])


def _piece_sides(shapes: list, left) -> list:
    """Per ground shape, whether each triangle is on the right hand; None unless every piece names a hand and both do."""
    out, hands = [], set()
    for _shape, arrays, tris, pieces in shapes:
        right = np.zeros(len(tris), dtype=bool)
        for piece in np.unique(pieces):
            rows = np.unique(tris[pieces == piece])
            hand = _hand(arrays['verts'][rows], arrays['uvs'][rows], left)
            if hand is None:
                return None
            right[pieces == piece] = hand
            hands.add(hand)
        out.append(right)
    return out if hands == {False, True} else None


def _hand(verts, uvs, left):
    """True for a right-hand piece, False for a left; None when its texels or its fit cannot say."""
    left_verts, tree = left
    rows = np.flatnonzero(np.isfinite(uvs).all(axis=1))
    dist, near = tree.query(uvs[rows])
    shared = dist < _UV_MATCH
    if shared.sum() < _MIN_SHARED_UVS:
        return None
    same, mirror = fit_errors(verts[rows[shared]], left_verts[near[shared]])
    return None if np.isclose(same, mirror) else bool(mirror < same)


def fit_errors(a, b) -> tuple:
    """RMS error of the best rotation, then of the best mirroring, carrying the matched rows `a` onto `b`."""
    ca, cb = a - a.mean(axis=0), b - b.mean(axis=0)
    u, _s, vt = np.linalg.svd(ca.T @ cb)
    flip = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    errors = {}
    for sign in (1.0, -1.0):
        turn = vt.T @ np.diag([1.0, 1.0, sign * flip]) @ u.T
        errors[round(np.linalg.det(turn))] = np.sqrt(((ca @ turn.T - cb) ** 2).sum(axis=1).mean())
    return errors[1], errors[-1]


def _split_half(ground: str, sides: list, right: bool):
    """The ground model holding only the pieces of one hand, centered."""
    data = _ground_data(ground)
    kept = [(shape, arrays, tris[side == right])
            for (shape, arrays, tris, _p), side in zip(_ground_shapes(data), sides)
            if (side == right).any()]
    return _finish(data, kept, turn=False)


def _worn_half(worn: str, right: bool):
    """One hand of the worn model, unskinned and laid flat; None if that hand is empty."""
    data = read_nif(worn)
    kept = []
    for shape, skel, tris, on_right in _worn_shapes(data):
        shape.skin_instance = None
        if (on_right == right).any():
            kept.append((shape, _root_arrays(shape, vertex_arrays(shape), skel), tris[on_right == right]))
    return _finish(data, kept, turn=True)


def _root_arrays(shape, arrays: dict, root) -> dict:
    """`arrays` carried from `shape`'s frame into `root`'s."""
    verts, transform = geom_world(shape, root)
    out = dict(arrays, verts=verts)
    for attr in _UNIT_ATTRS:
        if attr in out:
            turned = out[attr] @ transform[:3, :3]
            out[attr] = turned / np.maximum(np.linalg.norm(turned, axis=1, keepdims=True), 1e-12)
    return out


def _finish(data, kept: list, turn: bool):
    """`data` reduced to the `kept` (shape, root-space arrays, triangles), each hung straight off the root.

    The shapes are moved to rest centered on the origin, on the ground plane;
    `turn` first lays them flat, thinnest axis up. None when nothing is kept.
    """
    if not kept:
        return None
    root = data.roots[0]
    points = np.concatenate([arrays['verts'][np.unique(tris)] for _s, arrays, tris in kept])
    rotation, shift = _layout(points, turn)
    identity = NifFormat.Matrix44()
    identity.set_identity()
    for shape, arrays, tris in kept:
        moved = dict(arrays, verts=arrays['verts'] @ rotation.T + shift)
        for attr in _UNIT_ATTRS:
            if attr in moved:
                moved[attr] = moved[attr] @ rotation.T
        write_geometry(shape, moved, tris)
        shape.set_transform(identity)
        shape.collision_object = None
        if 'tangents' not in moved:
            shape.update_tangent_space()
    set_children(root, [shape for shape, _a, _t in kept])
    root.collision_object = None
    return data


def _layout(points, turn: bool) -> tuple:
    """(rotation, shift) resting `points` centered on the origin on the ground plane.

    With `turn` the points are first laid on their principal axes, the
    thinnest one kept pointing at +Z, never mirrored.
    See: docs/commentary/asset_convert_armor.md#split-pair-ground-models
    """
    rotation = np.eye(3)
    if turn:
        rotation = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)[2]
        if np.linalg.det(rotation) < 0:
            rotation[1] *= -1.0
        if rotation[2, 2] < 0:
            rotation[1:] *= -1.0
    moved = points @ rotation.T
    low, high = moved.min(axis=0), moved.max(axis=0)
    return rotation, -np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])


def _body_template(data):
    """The ground model's first collision object with a rigid body, or None."""
    for block in data.roots[0].tree():
        obj = getattr(block, 'collision_object', None)
        if obj is not None and isinstance(getattr(obj, 'body', None), NifFormat.bhkRigidBody):
            return obj
    return None


def _collision(template, root):
    """`template`, a collision object read for this half alone, made one plain convex body wrapping `root`'s shapes.

    None when no hull builds.
    """
    points = np.concatenate([np.array([[v.x, v.y, v.z] for v in shape.data.vertices])
                             for shape in root.children if shape is not None])
    body = template.body
    material = get_havok_material(body.shape.material) if hasattr(body.shape, 'material') else 0
    hull_points = points / _OB_GAME_UNITS_PER_HAVOK
    shape = build_convex_shape(hull_points, _HULL_RADIUS, material)
    if shape is None:
        return None
    body.__class__ = NifFormat.bhkRigidBody
    body.shape = shape
    body.num_constraints = 0
    body.constraints.update_size()
    body.translation.x = body.translation.y = body.translation.z = 0.0
    body.rotation.x = body.rotation.y = body.rotation.z = 0.0
    body.rotation.w = 1.0
    set_box_inertia(body, [hull_points], body.mass)
    template.target = root
    return template
