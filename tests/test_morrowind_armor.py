"""Tests for asset_convert/character/morrowind_armor.py -- skinned part bind pose."""
import numpy as np

from asset_convert.character.morrowind_armor import (PART_ATTACH_NODES, PART_SHAPE_FILTERS, NifFormat, _bind_world,
                                                     _wears, _weights, shape_vertices)
from asset_convert.character.prn_skin import rigid_skin_data, rigid_skin_instance
from asset_convert.character.skin_retarget import write_node_transform, write_skin_transform

#: A root turned 90 degrees about Z and raised 5, as Arktwend's guard chain shirt's Bip01 is.
_TURNED = np.array([[0.0, 1.0, 0.0, 0.0], [-1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 5.0, 1.0]])
#: A shape flipped 180 degrees about Y and lifted, as Arktwend's DMDB shirts are.
_FLIPPED = np.array([[-1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, -1.0, 0.0], [0.0, -2.6, 91.5, 1.0]])


def _part(shape_transform, overall, bone_skin, skel_root_resolves=True):
    """(root, shape): one shape under a node undoing the turned root, skinned to the root with these transforms."""
    root = NifFormat.NiNode()
    root.name = b'Bip01'
    write_node_transform(root, _TURNED)
    holder = NifFormat.NiNode()
    write_node_transform(holder, np.linalg.inv(_TURNED))
    root.add_child(holder)
    shape = NifFormat.NiTriShape()
    write_node_transform(shape, shape_transform)
    shape.data = NifFormat.NiTriShapeData()
    shape.data.num_vertices = 2
    shape.data.vertices.update_size()
    for v, (x, y, z) in zip(shape.data.vertices, [(-29.0, 0.0, 40.0), (29.0, 0.0, -40.0)]):
        v.x, v.y, v.z = x, y, z
    holder.add_child(shape)
    data = rigid_skin_data(shape.data)
    write_skin_transform(data.skin_transform, overall)
    write_skin_transform(data.bone_list[0].skin_transform, bone_skin)
    shape.skin_instance = rigid_skin_instance(root, root, data, 0, True)
    if not skel_root_resolves:
        shape.skin_instance.skeleton_root = None
    return root, shape


def _bind(root, shape):
    """The shape's bind-pose vertices."""
    return _bind_world(shape, _weights(shape, shape.skin_instance), root)[0]


def test_bind_is_bone_skin_transform_times_bone_world():
    """A bone at its bind leaves the vertices as stored, skin root or not."""
    for resolves in (True, False):
        root, shape = _part(np.eye(4), np.eye(4), np.linalg.inv(_TURNED), resolves)
        assert np.allclose(_bind(root, shape), shape_vertices(shape))


def test_hair_slot_wears_hair_shapes():
    """The hair slot hangs on Head but keeps its `Tri Hair` shapes (OpenMW)."""
    shape = NifFormat.NiTriShape()
    shape.name = b'Tri Hair 0'
    assert _wears(shape, PART_SHAPE_FILTERS.get(1, PART_ATTACH_NODES[1]))
    assert not _wears(shape, PART_SHAPE_FILTERS.get(0, PART_ATTACH_NODES[0]))


def test_shape_and_overall_transforms_are_not_applied():
    """A flipped DMDB-style shape stands upright by its bone transform alone."""
    root, shape = _part(_FLIPPED, np.linalg.inv(_FLIPPED), _FLIPPED @ np.linalg.inv(_TURNED))
    expected = shape_vertices(shape) @ _FLIPPED[:3, :3] + _FLIPPED[3, :3]
    assert np.allclose(_bind(root, shape), expected)
