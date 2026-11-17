"""Tests for asset_convert/character/morrowind_armor.py -- skinned part bind pose."""
import numpy as np

from asset_convert.character.morrowind_armor import NifFormat, _bind_world, _weights, shape_vertices
from asset_convert.character.prn_skin import rigid_skin_data, rigid_skin_instance
from asset_convert.character.skin_retarget import write_node_transform, write_skin_transform

#: A root turned 90 degrees about Z and raised 5, as Arktwend's guard chain shirt's Bip01 is.
_TURNED = np.array([[0.0, 1.0, 0.0, 0.0], [-1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 5.0, 1.0]])


def _turned_part(skel_root_resolves: bool):
    """(root, shape): a shape under a node undoing the turned root, skinned to the root at its bind."""
    root = NifFormat.NiNode()
    root.name = b'Bip01'
    write_node_transform(root, _TURNED)
    holder = NifFormat.NiNode()
    write_node_transform(holder, np.linalg.inv(_TURNED))
    root.add_child(holder)
    shape = NifFormat.NiTriShape()
    write_node_transform(shape, np.eye(4))
    shape.data = NifFormat.NiTriShapeData()
    shape.data.num_vertices = 2
    shape.data.vertices.update_size()
    for v, (x, y, z) in zip(shape.data.vertices, [(-29.0, 0.0, 40.0), (29.0, 0.0, 40.0)]):
        v.x, v.y, v.z = x, y, z
    holder.add_child(shape)
    data = rigid_skin_data(shape.data)
    write_skin_transform(data.bone_list[0].skin_transform, np.linalg.inv(_TURNED))
    shape.skin_instance = rigid_skin_instance(root, root, data, 0, True)
    if not skel_root_resolves:
        shape.skin_instance.skeleton_root = None
    return root, shape


def test_unresolved_skin_root_keeps_vertices_as_stored():
    """A skin root outside the file's tree leaves the vertices as stored."""
    root, shape = _turned_part(False)
    verts, _ = _bind_world(shape, _weights(shape, shape.skin_instance), root)
    assert np.allclose(verts, shape_vertices(shape))


def test_resolved_skin_root_measures_from_it():
    """A skin rooted at the turned Bip01 measures the shape from that root."""
    root, shape = _turned_part(True)
    verts, _ = _bind_world(shape, _weights(shape, shape.skin_instance), root)
    back = np.linalg.inv(_TURNED)
    assert np.allclose(verts, shape_vertices(shape) @ back[:3, :3] + back[3, :3])
