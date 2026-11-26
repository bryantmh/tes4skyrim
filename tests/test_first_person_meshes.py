"""First-person copies of worn meshes keep only the geometry on the forearms and hands.

See: docs/commentary/asset_convert_armor.md#first-person-meshes
"""

import io

from asset_convert.character.first_person import NifFormat, first_person_copy, first_person_path

FOREARM, SPINE = 'NPC L Forearm [LLar]', 'NPC Spine2 [Spn2]'


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


def _shape(name, triangles, num_vertices):
    """A NiTriShape named `name` over `num_vertices` vertices with `triangles`."""
    data = NifFormat.NiTriShapeData()
    data.num_vertices, data.has_vertices = num_vertices, True
    data.vertices.update_size()
    for i, vert in enumerate(data.vertices):
        vert.x, vert.y, vert.z = float(i), float(i % 2), float(i // 2)
    data.set_triangles(triangles)
    shape = NifFormat.NiTriShape()
    shape.name, shape.data = name.encode(), data
    return shape


def _skin(shape, root, weights):
    """Skin `shape` to `root`: `weights` is {bone node: [vertex index, ...]} at full weight."""
    skin, skin_data = NifFormat.NiSkinInstance(), NifFormat.NiSkinData()
    skin.data, skin.skeleton_root = skin_data, root
    skin.num_bones = skin_data.num_bones = len(weights)
    skin.bones.update_size()
    skin_data.bone_list.update_size()
    for i, (bone, vertices) in enumerate(weights.items()):
        skin.bones[i] = bone
        entry = skin_data.bone_list[i]
        entry.skin_transform.rotation.set_identity()
        entry.num_vertices = len(vertices)
        entry.vertex_weights.update_size()
        for k, vertex in enumerate(vertices):
            entry.vertex_weights[k].index, entry.vertex_weights[k].weight = vertex, 1.0
    shape.skin_instance = skin


def _nif_bytes(root, prn=None):
    """The Skyrim-version NIF holding `root`, with a Prn string extra data when `prn` is given."""
    if prn:
        extra = NifFormat.NiStringExtraData()
        extra.name, extra.string_data = b'Prn', prn.encode()
        root.add_extra_data(extra)
    data = NifFormat.Data(version=0x14020007, user_version=12, user_version_2=83)
    data.header.endian_type = 1
    data.roots = [root]
    buf = io.BytesIO()
    data.write(buf)
    return buf.getvalue()


def _shapes(data):
    """{shape name: triangle count} of every shape left in `data`."""
    return {bytes(b.name).decode(): b.data.num_triangles for b in data.roots[0].tree()
            if isinstance(b, NifFormat.NiTriShape)}


def test_first_person_path_prefixes_the_file_name():
    """Either slash separates folders; a bare file name gets the prefix too."""
    assert first_person_path('clothes\\shirt\\male_1.nif') == 'clothes\\shirt\\1stPersonmale_1.nif'
    assert first_person_path('clothes/shirt/male.nif') == 'clothes/shirt/1stPersonmale.nif'
    assert first_person_path('male.nif') == '1stPersonmale.nif'


def test_skinned_shapes_keep_only_forearm_triangles():
    """A shirt with a forearm sleeve keeps the sleeve; a torso-only shape is removed."""
    root, forearm, spine = _node('Scene Root'), _node(FOREARM), _node(SPINE)
    sleeve = _shape('Sleeve', [(0, 1, 2), (1, 2, 3), (2, 3, 4)], 5)
    _skin(sleeve, root, {forearm: [0, 1, 2], spine: [3, 4]})
    torso = _shape('Torso', [(0, 1, 2)], 3)
    _skin(torso, root, {spine: [0, 1, 2]})
    _children(root, [forearm, spine, sleeve, torso])
    assert _shapes(first_person_copy(_nif_bytes(root))) == {'Sleeve': 1}


def test_rigid_shapes_stay_only_on_a_shown_attach_node():
    """A shield (Prn SHIELD) stays whole; a rigid helmet (Prn Bip01 Head) is emptied."""
    for prn, expect in (('SHIELD', {'Board': 2}), ('Bip01 Head', {}), (None, {})):
        root = _node('Scene Root')
        _children(root, [_shape('Board', [(0, 1, 2), (1, 2, 3)], 4)])
        assert _shapes(first_person_copy(_nif_bytes(root, prn))) == expect, prn
