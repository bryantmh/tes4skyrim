"""Tests for asset_convert/character/skin_bone_cap.py: skinned shapes fit SSE's 80-matrix bone buffer."""
from asset_convert.character.skin_bone_cap import SSE_MAX_SKIN_BONES, NifFormat, cap_skin_bones


def _node(name):
    """A named NiNode."""
    node = NifFormat.NiNode()
    node.name = name.encode()
    return node


def _children(node, kids):
    """Give `node` the children `kids`."""
    node.num_children = len(kids)
    node.children.update_size()
    for i, kid in enumerate(kids):
        node.children[i] = kid


def _fan(leaves):
    """NiTriShapeData of a triangle fan over `leaves` + 1 vertices."""
    data = NifFormat.NiTriShapeData()
    data.num_vertices = leaves + 1
    data.has_vertices = True
    data.vertices.update_size()
    for i, vert in enumerate(data.vertices):
        vert.x, vert.y, vert.z = float(i), float(i % 2), 0.0
    data.set_triangles([(0, i, i + 1) for i in range(1, leaves)])
    return data


def _skin(root, bones):
    """A NiSkinInstance binding vertices 0 and 1 to the first bone and vertex i + 1 to bone i."""
    skin, skin_data = NifFormat.NiSkinInstance(), NifFormat.NiSkinData()
    skin.data, skin.skeleton_root = skin_data, root
    skin.num_bones = skin_data.num_bones = len(bones)
    skin.bones.update_size()
    skin_data.bone_list.update_size()
    for i, bone in enumerate(bones):
        skin.bones[i] = bone
        entry = skin_data.bone_list[i]
        entry.skin_transform.rotation.set_identity()
        vertices = (0, 1) if i == 0 else (i + 1,)
        entry.num_vertices = len(vertices)
        entry.vertex_weights.update_size()
        for k, vertex in enumerate(vertices):
            entry.vertex_weights[k].index, entry.vertex_weights[k].weight = vertex, 1.0
    return skin


def _skinned_rig(leaves):
    """A root holding a hub bone with `leaves` leaf bones and one shape skinned to the hub and the leaves."""
    root, hub = _node('Scene Root'), _node('Hub')
    bones = [_node('Leaf%02d' % i) for i in range(leaves)]
    _children(hub, bones)
    shape = NifFormat.NiTriShape()
    shape.name = b'Body'
    shape.data = _fan(leaves + 1)
    shape.skin_instance = _skin(root, [hub] + bones)
    _children(root, [hub, shape])
    return root, shape


def test_an_oversized_skin_is_merged_down_and_repartitioned():
    """85 leaf bones come out at the cap, with a partition built from the survivors only.

    See: docs/commentary/asset_convert_armor.md#creature-skin-render-crash-80
    """
    root, shape = _skinned_rig(85)
    assert cap_skin_bones([root]) == 1
    skin = shape.skin_instance
    assert skin.num_bones == SSE_MAX_SKIN_BONES
    assert skin.skin_partition is not None
    used = {b for part in skin.skin_partition.skin_partition_blocks for b in part.bones}
    assert max(used) < SSE_MAX_SKIN_BONES


def test_a_flat_body_part_takes_its_hierarchy_from_the_skeleton(tmp_path):
    """Bones hung flat off the root merge once the folder's skeleton NIF names their parents.

    See: docs/commentary/asset_convert_armor.md#creature-skin-render-crash-80
    """
    skeleton_root, _ = _skinned_rig(85)
    skeleton = NifFormat.Data()
    skeleton.roots = [skeleton_root]
    with open(tmp_path / 'skeleton.nif', 'wb') as stream:
        skeleton.write(stream)
    root, shape = _skinned_rig(85)
    hub = root.children[0]
    _children(root, [hub] + list(hub.children) + [shape])
    _children(hub, [])
    assert cap_skin_bones([root]) == 0
    assert cap_skin_bones([root], str(tmp_path / 'skeleton.nif')) == 1
    assert shape.skin_instance.num_bones == SSE_MAX_SKIN_BONES


def test_a_skin_inside_the_cap_is_left_alone():
    """A shape already inside the buffer is not touched."""
    root, shape = _skinned_rig(20)
    assert cap_skin_bones([root]) == 0
    assert shape.skin_instance.num_bones == 21
    assert shape.skin_instance.skin_partition is None
