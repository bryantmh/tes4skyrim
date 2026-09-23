"""Assemble creature meshes: the death-pile lift and the body merge.

Both build a NIF out of pieces of other NIFs -- one lifts authored geometry out
of a skeleton, the other grafts part meshes onto a shared rig -- so they share
the tree-copy helpers here and nothing else in the converter uses them.

See: docs/commentary/asset_convert_creature.md#creature-mesh-merge
"""

import os

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.nif.nif_flags import NIF_FLAGS

#: Oblivion havok -> game units; the box is written pre-rescale.
_PILE_HAVOK_SCALE = 7.0

#: SkyrimLayer 15: collides with nothing, still ray-cast for activation.
_PILE_COLL_LAYER = 15

#: Vanilla's own pick-box thickness, the floor for the pile box's Z extent.
_PILE_MIN_HALF_Z = 8.0

#: BSXFlags bit 1 (Havok): without it a pile is inert even with a phantom.
_PILE_BSX = 2

#: bhkSPCollisionObject flags on every vanilla ash pile.
_PILE_CO_FLAGS = 129

#: Where an Oblivion body part attaches when it carries no `Prn`.
_DEFAULT_ATTACHMENT = 'SkinAttachment'


def _shape_blocks(root):
    """All NiTriShape/NiTriStrips geometry blocks under a root."""
    return [b for b in root.tree()
            if isinstance(b, (NifFormat.NiTriShape, NifFormat.NiTriStrips))]


def _append_child(node, child):
    """Append `child` to `node`, growing its children array by one."""
    node.num_children += 1
    node.children.update_size()
    node.children[node.num_children - 1] = child


def _geoms(root):
    """Every geometry block under `root`, each yielded once.

    pyffi's `tree()` yields a block once per REFERENCE, and the ghost's
    ectoplasm shape is referenced twice.
    See: docs/commentary/asset_convert_creature.md#pile-transform-baking
    """
    seen = set()
    for blk in root.tree():
        if isinstance(blk, NifFormat.NiTriBasedGeom) and id(blk) not in seen:
            seen.add(id(blk))
            yield blk


def _pile_bounds(root):
    """(min, max) of every shape under `root`, in root space, or None."""
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    for blk in _geoms(root):
        data = getattr(blk, 'data', None)
        verts = getattr(data, 'vertices', None) if data is not None else None
        if not verts:
            continue
        s = float(getattr(blk, 'scale', 1.0) or 1.0)
        t = blk.translation
        base = (t.x, t.y, t.z)
        for v in verts:
            for i, c in enumerate((v.x, v.y, v.z)):
                w = c * s + base[i]
                lo[i] = min(lo[i], w)
                hi[i] = max(hi[i], w)
    if lo[0] > hi[0]:
        return None
    return lo, hi


def _center_pile_xy(root):
    """Shift every shape so the pile straddles the origin in X and Y.

    Z is preserved: that is the authored ground drop, not drift.
    See: docs/commentary/asset_convert_creature.md#pile-transform-baking
    """
    b = _pile_bounds(root)
    if b is None:
        return False
    lo, hi = b
    dx = (lo[0] + hi[0]) / 2.0
    dy = (lo[1] + hi[1]) / 2.0
    if abs(dx) < 1e-4 and abs(dy) < 1e-4:
        return False
    for blk in _geoms(root):
        blk.translation.x -= dx
        blk.translation.y -= dy
    return True


def _pile_box(half, center):
    """The bhkTransformShape carrying a box of `half` extents at `center`."""
    box = NifFormat.bhkBoxShape()
    box.material.material = 0
    box.radius = 1.0
    box.dimensions.x = half[0] / _PILE_HAVOK_SCALE
    box.dimensions.y = half[1] / _PILE_HAVOK_SCALE
    box.dimensions.z = half[2] / _PILE_HAVOK_SCALE

    xf = NifFormat.bhkTransformShape()
    xf.material.material = 0
    xf.unknown_float_1 = 0.1
    xf.shape = box
    xf.transform.set_identity()
    xf.transform.m_14 = center[0] / _PILE_HAVOK_SCALE
    xf.transform.m_24 = center[1] / _PILE_HAVOK_SCALE
    xf.transform.m_34 = center[2] / _PILE_HAVOK_SCALE
    return xf


def _fit_pile_collision(root):
    """Attach vanilla's ash-pile PHANTOM, box-fitted to `root`'s geometry.

    A pile's activation volume must be a bhkSimpleShapePhantom, never a rigid
    body: the crosshair pick never sees a body.
    See: docs/commentary/asset_convert_creature.md#pile-activation-phantom
    """
    b = _pile_bounds(root)
    if b is None:
        return False
    lo, hi = b
    half = [(hi[i] - lo[i]) / 2.0 for i in range(3)]
    half[2] = max(half[2], _PILE_MIN_HALF_Z)
    center = [(hi[i] + lo[i]) / 2.0 for i in range(3)]

    phantom = NifFormat.bhkSimpleShapePhantom()
    phantom.shape = _pile_box(half, center)
    phantom.havok_col_filter.layer = _PILE_COLL_LAYER
    for i in range(3):
        phantom.unknown_floats_2[i][0] = 1.0

    box_node = NifFormat.NiNode()
    box_node.name = b'Box01'
    box_node.flags = NIF_FLAGS
    co = NifFormat.bhkSPCollisionObject()
    co.flags = _PILE_CO_FLAGS
    co.target = box_node
    co.body = phantom
    box_node.collision_object = co
    _append_child(root, box_node)
    return True


def _read_nif(path):
    """The parsed NIF at `path`, or None when it cannot be read."""
    try:
        data = NifFormat.Data()
        with open(path, 'rb') as f:
            data.read(f)
        return data
    except Exception:
        return None


def _write_nif(data, dst_path):
    """Write `data` to `dst_path`, creating the directory."""
    dst_dir = os.path.dirname(dst_path)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    with open(dst_path, 'wb') as f:
        data.write(f)


def _node_name(blk):
    """A block's name as text."""
    return bytes(blk.name).rstrip(b'\x00').decode('latin-1', 'replace')


def _pick_pile_shapes(root, reveal_holders):
    """(shape, holder name, holder node) for geometry the death clip reveals."""
    picked = []
    seen = set()
    for want in reveal_holders:
        for blk in root.tree():
            if not isinstance(blk, NifFormat.NiNode):
                continue
            nm = _node_name(blk)
            if nm != want:
                continue
            for sub in blk.tree():
                if (isinstance(sub, NifFormat.NiTriBasedGeom)
                        and id(sub) not in seen):
                    seen.add(id(sub))
                    picked.append((sub, nm, blk))
    return picked


def _parent_map(root):
    """{id(child node): parent node} over the whole tree."""
    parent_of = {}
    for blk in root.tree():
        if isinstance(blk, NifFormat.NiNode):
            for ch in blk.children:
                if ch is not None:
                    parent_of[id(ch)] = blk
    return parent_of


def _holder_shift(holder, holder_node, parent_of, root, holder_offsets):
    """Where the clip's last frame parks `holder`, as a world delta."""
    if holder not in holder_offsets:
        return 0.0, 0.0, 0.0
    parent = parent_of.get(id(holder_node))
    try:
        pw = parent.get_transform(root) if parent is not None else None
        hw = holder_node.get_transform(root)
    except Exception:
        return 0.0, 0.0, 0.0
    if pw is None or hw is None:
        return 0.0, 0.0, 0.0
    fx, fy, fz = holder_offsets[holder]
    return ((pw.m_41 + float(fx)) - hw.m_41,
            (pw.m_42 + float(fy)) - hw.m_42,
            (pw.m_43 + float(fz)) - hw.m_43)


def _bake_pile_shape(shape, tm, shift):
    """Park `shape` at its world transform plus the clip's holder shift.

    The composed transform is written straight onto the node: the ghost's
    ectoplasm carries a 0.57 scale in its rotation rows that `set_transform`
    re-decomposes, so arithmetic on m_43 does not survive.
    See: docs/commentary/asset_convert_creature.md#pile-transform-baking
    """
    shape.set_transform(tm)
    shape.translation.x = tm.m_41 + shift[0]
    shape.translation.y = tm.m_42 + shift[1]
    shape.translation.z = tm.m_43 + shift[2]
    shape.flags = NIF_FLAGS
    shape.controller = None


def _add_pile_bsx(out_root):
    """Flag the pile as having havok, so the engine traces against it."""
    bsx = NifFormat.BSXFlags()
    bsx.name = b'BSX'
    bsx.integer_data = _PILE_BSX
    out_root.num_extra_data_list += 1
    out_root.extra_data_list.update_size()
    out_root.extra_data_list[out_root.num_extra_data_list - 1] = bsx


def extract_death_pile(src_skeleton_path, dst_path, reveal_holders=None,
                       holder_offsets=None):
    """Lift a dissolving creature's AUTHORED death pile into its own NIF.

    reveal_holders: nodes the death clip turns ON; only geometry under one
        is a pile.  holder_offsets: {holder: (dx, dy, dz)} the clip applies
        by its LAST frame, lowering the pile to the ground.

    Writes an unskinned NIF, each shape baked to its final world transform
    and stripped of the reveal controllers.
    See: docs/commentary/asset_convert_creature.md#pile-transform-baking
    """
    reveal_holders = tuple(reveal_holders or ())
    holder_offsets = holder_offsets or {}
    if not reveal_holders:
        return False
    data = _read_nif(src_skeleton_path)
    root = (data.roots[0] if data is not None and data.roots else None)
    if root is None:
        return False

    picked = _pick_pile_shapes(root, reveal_holders)
    if not picked:
        return False

    out_root = NifFormat.NiNode()
    out_root.name = os.path.basename(dst_path).encode('latin-1')
    out_root.flags = NIF_FLAGS
    parent_of = _parent_map(root)

    for shape, holder, holder_node in picked:
        try:
            tm = shape.get_transform(root)
        except Exception:
            tm = None
        if tm is None:
            _append_child(out_root, shape)
            continue
        shift = _holder_shift(holder, holder_node, parent_of, root,
                              holder_offsets)
        _bake_pile_shape(shape, tm, shift)
        _append_child(out_root, shape)

    _center_pile_xy(out_root)
    if _fit_pile_collision(out_root):
        _add_pile_bsx(out_root)

    data.roots = [out_root]
    _write_nif(data, dst_path)
    return True


def source_hidden_attachment_nodes(src_skeleton_path):
    """Attachment nodes the SOURCE skeleton hides at rest.

    Oblivion authors rest visibility on the attachment NODE and conversion
    normalizes node flags, so the bit must be carried onto the shape or a
    LIVING ghost wears its own ectoplasm.  Read from the SKELETON, not the
    parts, which set the same bit on ordinary bones where it means nothing.
    """
    out = set()
    data = _read_nif(src_skeleton_path)
    if data is None:
        return out
    for r in data.roots:
        if r is None:
            continue
        for blk in r.tree():
            if isinstance(blk, NifFormat.NiNode) and \
                    int(getattr(blk, 'flags', 0)) & 1:
                out.add(_node_name(blk))
    return out


def source_attachment_node(src_nif_path):
    """Attachment node of an UNCONVERTED Oblivion creature body part.

    convert_nif strips the `Prn` NiStringExtraData, so the creature pipeline
    reads it from the source and hands it to merge_creature_body.
    """
    data = NifFormat.Data()
    with open(src_nif_path, 'rb') as f:
        data.read(f)
    for r in data.roots:
        if r is None:
            continue
        return _part_attachment_node(r)
    return _DEFAULT_ATTACHMENT


def _part_attachment_node(src_root):
    """Name of the node an Oblivion creature body part attaches to.

    The `Prn` value when the part carries one (heademissive ->
    'AttachmentsHead'), else 'SkinAttachment'.  The death animation drives
    NiVisControllers on exactly these nodes, so the association must survive
    the body merge.
    """
    for blk in src_root.tree():
        if isinstance(blk, NifFormat.NiStringExtraData) and \
                bytes(blk.name).rstrip(b'\x00') == b'Prn':
            v = bytes(blk.string_data).rstrip(b'\x00').decode('latin-1')
            if v:
                return v
    return _DEFAULT_ATTACHMENT


def _copy_bone_tree(src_node, dst_parent, mapping):
    """Copy the NiNode-only hierarchy under src_node into dst_parent.

    Name, flags and full local transform only -- no collision objects,
    controllers or extra data.  Fills mapping[name] = copied node.
    """
    for child in src_node.children:
        if not isinstance(child, NifFormat.NiNode):
            continue
        cp = NifFormat.NiNode()
        cp.name = child.name
        cp.flags = child.flags
        cp.set_transform(child.get_transform())
        mapping.setdefault(_node_name(child), cp)
        _append_child(dst_parent, cp)
        _copy_bone_tree(child, cp, mapping)


def _load_rig(skeleton_path, root):
    """Copy the converted skeleton's bone hierarchy under `root`."""
    bones = {}
    if not (skeleton_path and os.path.exists(skeleton_path)):
        return bones
    skel = NifFormat.Data()
    with open(skeleton_path, 'rb') as f:
        skel.read(f)
    for r in skel.roots:
        if isinstance(r, NifFormat.NiNode):
            _copy_bone_tree(r, root, bones)
    return bones


def _rebind_skin(si, src_root, root, bones):
    """Re-point a skin's bones by NAME at the merged rig.

    A skin bone the rig lacks (a part-local control node) is copied from the
    part's own tree with its true world transform.
    """
    for bi, bone in enumerate(si.bones):
        if bone is None:
            continue
        nm = _node_name(bone)
        tgt = bones.get(nm)
        if tgt is None:
            tgt = NifFormat.NiNode()
            tgt.name = bone.name
            tgt.flags = bone.flags
            tgt.set_transform(bone.get_transform(src_root))
            _append_child(root, tgt)
            bones[nm] = tgt
        si.bones[bi] = tgt
    if si.skeleton_root is not None:
        si.skeleton_root = root


def _graft_part(src_root, root, bones, prn, hidden_nodes):
    """Graft one part's shapes onto the merged root; count them."""
    grafted = 0
    for shape in _shape_blocks(src_root):
        si = shape.skin_instance
        if si is not None:
            _rebind_skin(si, src_root, root, bones)
        if prn in (hidden_nodes or ()):
            shape.flags = int(shape.flags) | 1
        _append_child(root, shape)
        grafted += 1
    return grafted


def _cap_skin_bones(root):
    """Merge leaf bones until every shape is inside SSE's 80-bone buffer.

    Runs after grafting: only the merged rig has bone hierarchy, and the merge
    invalidates the parts' NiSkinPartitions.
    See: docs/commentary/asset_convert_creature.md#merged-shapes-stay-at-the-root
    """
    from asset_convert.character.skin_bone_cap import merge_oversized_skin_bones
    from asset_convert.character.skin_retarget import regen_skin_partition
    if not merge_oversized_skin_bones(root):
        return
    for shape in _shape_blocks(root):
        si = shape.skin_instance
        if si is not None:
            regen_skin_partition(shape, si, _node_name(shape))


def merge_creature_body(part_paths, dst_path, skeleton_path=None,
                        attachments=None, hidden_nodes=None):
    """Merge the converted creature body-part NIFs into ONE skinned NIF.

    part_paths: already-converted .nif paths (Skyrim version).
    skeleton_path: the creature's converted 'character assets/skeleton.nif'.
    attachments: {part path: attachment node name}, read from the SOURCE parts'
        `Prn` before conversion strips it.

    Returns {'grafted': int, 'shapes': int, 'bones': int}.
    See: docs/commentary/asset_convert_creature.md#merged-shapes-stay-at-the-root
    """
    if not part_paths:
        return {'error': 'no parts'}

    datas = []
    for p in part_paths:
        d = NifFormat.Data()
        with open(p, 'rb') as f:
            d.read(f)
        datas.append((p, d))

    root = NifFormat.NiNode()
    root.name = os.path.basename(dst_path).encode('latin-1')
    root.flags = NIF_FLAGS
    bones = _load_rig(skeleton_path, root)

    grafted = 0
    for _path, d in datas:
        for src_root in d.roots:
            if src_root is None:
                continue
            prn = ((attachments or {}).get(_path)
                   or _part_attachment_node(src_root))
            grafted += _graft_part(src_root, root, bones, prn, hidden_nodes)

    _cap_skin_bones(root)

    out_data = datas[0][1]
    out_data.roots = [root]
    _write_nif(out_data, dst_path)
    return {'grafted': grafted, 'shapes': len(_shape_blocks(root)),
            'bones': len(bones)}
