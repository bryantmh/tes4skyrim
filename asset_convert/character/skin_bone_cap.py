"""
Skin bone-count reduction for SSE's 80-matrix skinning buffer.

SSE copies one 3x4 matrix per skin bone into a fixed 80-matrix buffer when it
draws a skinned shape; a NiSkinInstance with more bones overflows it and
crashes the shadow pass. Merging the lightest leaf bones into their parents
keeps the bind pose exact (B_i . W_i = I at rest) and loses only tip
articulation; splitting the shape instead froze the game.

See: docs/commentary/asset_convert_armor.md#creature-skin-render-crash-80
"""

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

#: Skin bones one shape may carry before SSE's matrix buffer overflows.
SSE_MAX_SKIN_BONES = 80

#: NiSkinData bind-rotation fields copied from a surviving bone's old entry.
_ROTATION_FIELDS = ('m_11', 'm_12', 'm_13', 'm_21', 'm_22', 'm_23',
                    'm_31', 'm_32', 'm_33')


def _node_name(node) -> str:
    """A node's name as text."""
    return bytes(node.name).rstrip(b'\x00').decode('latin-1', 'replace')


def _parent_names(root) -> dict:
    """{node name: parent node name} over the NiNode tree under `root`."""
    parent_name = {}
    for node in root.tree():
        if isinstance(node, NifFormat.NiNode):
            for ch in node.children:
                if isinstance(ch, NifFormat.NiNode):
                    parent_name[_node_name(ch)] = _node_name(node)
    return parent_name


def _parent_indices(names, parent_name) -> list:
    """Each skin bone's parent as an index into the same skin, -1 if not in it."""
    name_idx = {nm: i for i, nm in enumerate(names)}
    return [name_idx.get(parent_name[nm], -1) if nm in parent_name else -1
            for nm in names]


def _leaf_candidates(parents, merge_set, tot_w) -> list:
    """Live leaf bones with a live parent, lightest first."""
    n = len(parents)
    live_children = [0] * n
    for i in range(n):
        p = parents[i]
        if i not in merge_set and p >= 0 and p not in merge_set:
            live_children[p] += 1
    return sorted((i for i in range(n)
                   if i not in merge_set and live_children[i] == 0
                   and parents[i] >= 0 and parents[i] not in merge_set),
                  key=lambda i: tot_w[i])


def _merge_set(parents, tot_w, max_bones) -> set:
    """Bones to merge away, leaves first, until the skin fits or none are left."""
    n = len(parents)
    merge_set = set()
    while n - len(merge_set) > max_bones:
        before = len(merge_set)
        for i in _leaf_candidates(parents, merge_set, tot_w):
            if n - len(merge_set) <= max_bones:
                break
            merge_set.add(i)
        if len(merge_set) == before:
            break
    return merge_set


def _rerouted_weights(sd, parents, merge_set) -> dict:
    """{vertex: {surviving bone index: weight}}, merged bones' weight moved to survivors."""
    vert_w = {}
    for i in range(len(parents)):
        tgt = i
        while tgt in merge_set:
            tgt = parents[tgt]
        for vw in sd.bone_list[i].vertex_weights:
            acc = vert_w.setdefault(vw.index, {})
            acc[tgt] = acc.get(tgt, 0.0) + vw.weight
    return vert_w


def _copy_bone_entry(src, dst, weights) -> None:
    """Give `dst` the bind and bound of `src` and the (vertex, weight) list `weights`."""
    for m in _ROTATION_FIELDS:
        setattr(dst.skin_transform.rotation, m, getattr(src.skin_transform.rotation, m))
    for axis in ('x', 'y', 'z'):
        setattr(dst.skin_transform.translation, axis,
                getattr(src.skin_transform.translation, axis))
        setattr(dst.bounding_sphere_offset, axis, getattr(src.bounding_sphere_offset, axis))
    dst.skin_transform.scale = src.skin_transform.scale
    dst.bounding_sphere_radius = src.bounding_sphere_radius
    dst.num_vertices = len(weights)
    dst.vertex_weights.update_size()
    for wi, (vi, w) in enumerate(weights):
        dst.vertex_weights[wi].index = vi
        dst.vertex_weights[wi].weight = w


def _rewrite_skin(skin, bones, survivors, vert_w) -> None:
    """Keep only `survivors` in the skin, renormalizing every vertex's weights."""
    sd = skin.data
    new_index = {old: k for k, old in enumerate(survivors)}
    skin.num_bones = len(survivors)
    skin.bones.update_size()
    for k, old in enumerate(survivors):
        skin.bones[k] = bones[old]
    old_entries = [sd.bone_list[i] for i in range(len(bones))]
    sd.num_bones = len(survivors)
    sd.bone_list.update_size()
    per_bone = {k: [] for k in range(len(survivors))}
    for vi, wmap in vert_w.items():
        tot = sum(wmap.values())
        for old, w in wmap.items():
            per_bone[new_index[old]].append((vi, w / tot))
    for k, old in enumerate(survivors):
        _copy_bone_entry(old_entries[old], sd.bone_list[k], sorted(per_bone[k]))


def _reduce_shape(shape, parent_name, max_bones) -> bool:
    """Merge one shape's lightest leaf bones until it fits; whether it changed."""
    skin = shape.skin_instance
    bones = list(skin.bones)
    names = [_node_name(b) for b in bones]
    parents = _parent_indices(names, parent_name)
    tot_w = [sum(vw.weight for vw in skin.data.bone_list[i].vertex_weights)
             for i in range(len(bones))]
    merge_set = _merge_set(parents, tot_w, max_bones)
    if len(bones) - len(merge_set) > max_bones:
        print(f'      [SKIN-BONES] WARNING: "{_node_name(shape)}" still '
              f'{len(bones) - len(merge_set)} bones (> {max_bones}) — not enough '
              f'mergeable leaf bones')
    if not merge_set:
        return False
    survivors = [i for i in range(len(bones)) if i not in merge_set]
    _rewrite_skin(skin, bones, survivors, _rerouted_weights(skin.data, parents, merge_set))
    merged = [bytes(bones[i].name).rstrip(b'\x00').decode('latin-1')
              for i in sorted(merge_set)]
    print(f'      [SKIN-BONES] "{_node_name(shape)}": {len(bones)} -> '
          f'{len(survivors)} bones (merged {merged})')
    return True


def merge_oversized_skin_bones(root, max_bones: int = SSE_MAX_SKIN_BONES) -> int:
    """Reduce every skinned shape under `root` to <= max_bones skin bones; how many changed.

    Bones are matched to their parents BY NAME: during part conversion a
    skin's bone pointers may not be members of the current tree, but node
    names are stable. Callers must regenerate the NiSkinPartition afterwards.
    """
    parent_name = _parent_names(root)
    reduced = 0
    for shape in list(root.tree()):
        if not isinstance(shape, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        skin = getattr(shape, 'skin_instance', None)
        if skin is None or skin.data is None or skin.num_bones <= max_bones:
            continue
        reduced += _reduce_shape(shape, parent_name, max_bones)
    return reduced
