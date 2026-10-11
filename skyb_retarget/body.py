"""Write the Skyblivion mesh as the converted creature's body NIF.

Same layout as the converter's own merged body: a root named after the file
holding a copy of the converted skeleton's bone tree, every shape skinned to
those nodes.  The Skyblivion art is untouched except for the lift onto the
ground; its weights move to the Oblivion bones (weights.mapped_weights).

See: skyb_retarget/README.md#the-body-mesh
"""
import os

import numpy as np

from asset_convert.character.skin_retarget import (manual_update_bind_position,
                                                   regen_skin_partition)
from pyffi.formats.nif import NifFormat
from skyb_retarget.skin_data import (block_name, node_worlds, read_nif,
                                     shape_mesh, skinned_shapes)
from skyb_retarget.weights import mapped_weights

#: Skyrim body-part id for the whole creature (BSDismemberSkinInstance partitions).
BODY_PART = 32

#: Most bones one vertex may carry in a Skyrim skin partition.
MAX_INFLUENCES = 4


def _bare_tree(node):
    """Strip a skeleton NiNode subtree down to plain bone nodes (in place)."""
    node.collision_object = None
    node.controller = None
    node.num_extra_data_list = 0
    node.extra_data_list.update_size()
    kids = [c for c in node.children if isinstance(c, NifFormat.NiNode)]
    node.num_children = len(kids)
    node.children.update_size()
    for i, c in enumerate(kids):
        node.children[i] = c
        _bare_tree(c)
    return node


def _bone_tree(skeleton_nif: str):
    """The converted skeleton's top bone node, stripped to plain NiNodes."""
    data = read_nif(skeleton_nif)
    root = data.roots[0]
    top = next(c for c in root.children if isinstance(c, NifFormat.NiNode))
    return _bare_tree(top)


def _top_influences(w: np.ndarray) -> np.ndarray:
    """Keep each vertex's MAX_INFLUENCES largest weights, renormalized."""
    out = np.zeros_like(w)
    keep = np.argsort(-w, axis=1)[:, :MAX_INFLUENCES]
    rows = np.arange(len(w))[:, None]
    out[rows, keep] = w[rows, keep]
    return out / np.maximum(out.sum(1, keepdims=True), 1e-9)


def _rename(name: str, nodes: dict) -> str:
    """Converted node name for an Oblivion bone (the root became 'NPC Root')."""
    if name in nodes:
        return name
    return next(n for n in nodes if n.startswith('NPC Root'))


def _reskin(shape, root, nodes: dict, names: list, w: np.ndarray) -> None:
    """Point the shape's skin at `nodes` with weights `w` (V x len(names))."""
    used = [i for i in range(len(names)) if w[:, i].max() > 0]
    skin = shape.skin_instance
    skin.skeleton_root = root
    skin.num_bones = len(used)
    skin.bones.update_size()
    data = NifFormat.NiSkinData()
    data.has_vertex_weights = 1
    data.num_bones = len(used)
    data.bone_list.update_size()
    for slot, i in enumerate(used):
        skin.bones[slot] = nodes[_rename(names[i], nodes)]
        verts = np.nonzero(w[:, i])[0]
        bone = data.bone_list[slot]
        bone.num_vertices = len(verts)
        bone.vertex_weights.update_size()
        for vw, v in zip(bone.vertex_weights, verts):
            vw.index, vw.weight = int(v), float(w[v, i])
    skin.data = data
    manual_update_bind_position(shape, skin, root)
    regen_skin_partition(shape, skin, block_name(shape),
                         authored_body_part=BODY_PART)


def write_body(skyb_nif: str, skeleton_nif: str, out_nif: str, lift: float,
               mapping) -> int:
    """Write the Skyblivion body skinned to the converted skeleton; returns shapes.

    `mapping` is (skin_map, split) for weights.mapped_weights.
    """
    data = read_nif(skyb_nif)
    worlds = node_worlds(data)
    shapes = skinned_shapes(data)
    meshes = [shape_mesh(s, worlds) for s in shapes]
    root = data.roots[0]
    root.name = os.path.basename(out_nif).encode('latin-1')
    tree = _bone_tree(skeleton_nif)
    kids = [tree] + list(shapes)
    root.num_children = len(kids)
    root.children.update_size()
    for i, c in enumerate(kids):
        root.children[i] = c
    nodes = {block_name(n): n for n in tree.tree() if isinstance(n, NifFormat.NiNode)}
    for shape, mesh in zip(shapes, meshes):
        for v in shape.data.vertices:
            v.z += lift
        mesh.verts[:, 2] += lift
        names, w = mapped_weights(mesh, *mapping)
        _reskin(shape, root, nodes, names, _top_influences(w))
    os.makedirs(os.path.dirname(os.path.abspath(out_nif)), exist_ok=True)
    with open(out_nif, 'wb') as f:
        data.write(f)
    return len(shapes)
