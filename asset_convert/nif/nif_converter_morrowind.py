"""Morrowind mesh fixups: collision, helper nodes, specular, skin partitions.

Morrowind carries no Havok data at all. Collision is a plain triangle mesh
parked under a `RootCollisionNode`, which the engine consumes and never draws;
a mesh without one collides with its RENDER geometry instead, and an `NC`
string extra on the root switches collision off
(`references/openmw/components/nifbullet/bulletnifloader.cpp:171`). Every
branch of that rule is reproduced here, in Skyrim havok units, AFTER the
Oblivion collision pass has run over the converted tree.

Detection is by BLOCK TYPE, never by name: pyffi reads the node as its own
`RootCollisionNode` class exactly as the engine registers it
(`references/openmw/components/nif/niffile.cpp:68`), and the 217 such nodes in
a strided sample of the corpus all carry an EMPTY name string.

See: docs/commentary/asset_convert_nif.md#morrowind-collision
"""

import os

from pyffi.formats.nif import NifFormat

from asset_convert.collision.cms_builder import build_cms_collision
from asset_convert.collision.collision import GAME_UNITS_PER_HAVOK
from asset_convert.collision.collision_hulls import build_clutter_hull
from asset_convert.collision.clutter_plan import mesh_clutter_mass
from asset_convert.havok.hkx_ragdoll_morrowind import attach_synthetic_bodies
from asset_convert.nif.nif_materials_morrowind import (
    havok_material, sample_materials)
from asset_convert.nif.nif_passes import add_bsx_flags
from asset_convert.nif.particles_morrowind import upgrade_legacy_particles

#: Render units to Skyrim havok units; Morrowind authors collision in render units.
_HAVOK_SCALE = 1.0 / GAME_UNITS_PER_HAVOK

#: The biggest real collision mesh in the sampled corpus is 2,219 triangles.
_MAX_COLLISION_TRIS = 20000

#: SKYL_CLUTTER, the layer every loose simulated prop uses.
_SKYL_CLUTTER = 4

#: Inertia floor (havok units) so a flat item still resists rotation.
_MIN_INERTIA_EXTENT = 0.02

#: Helper nodes Morrowind uses that must never reach Skyrim as geometry.
_HELPER_TYPES = ('AvoidNode',)

#: Node types whose collision comes from their first child only.
_FIRST_CHILD_ONLY = ('NiSwitchNode', 'NiFltAnimationNode')

#: Legacy nodes Skyrim has no RTTI for, rewritten as a plain NiNode.
_REWRITE_AS_NINODE = ('NiBSAnimationNode', 'NiBSParticleNode',
                      'NiCollisionSwitch')

#: Subtree collides only when this NiAVObject flag is set on a collision switch.
_ACTIVE_COLLISION_FLAG = 0x0020

#: Legacy LOD selector; child 0 is the nearest level and the one kept.
_LOD_NODE = 'NiLODNode'

#: Root string extra prefix that disables collision ("NC", "NCC").
_NO_COLLISION_PREFIX = 'nc'

#: Root string extra that marks editor-only geometry.
_MARKER_EXTRA = 'mrk'

#: Shape name prefix of editor-only geometry when the root carries MRK.
_MARKER_SHAPE_PREFIX = 'tri editormarker'

#: Skin partition limits; the values every vanilla Skyrim partition uses.
_MAX_BONES_PER_PARTITION = 4
_MAX_WEIGHTS_PER_VERTEX = 4


def is_collision_node(block) -> bool:
    """Whether this block is Morrowind's RootCollisionNode.

    See: docs/commentary/asset_convert_nif.md#morrowind-collision
    """
    return type(block).__name__ == 'RootCollisionNode'


def find_collision_node(root):
    """The RootCollisionNode the engine would use, or None.

    Direct children only and searched in REVERSE, matching
    `NiNode::findRootCollisionNode` (`references/openmw/components/nif/
    node.cpp:209`), whose recursive mode is opt-in via an "RCN" string extra.
    Every one of the 217 found in the corpus is a direct child anyway.
    """
    for child in reversed(list(getattr(root, 'children', None) or [])):
        if child is not None and is_collision_node(child):
            return child
    return None


def _text(value) -> str:
    """A pyffi string value as lower-case text."""
    if isinstance(value, bytes):
        value = value.decode('cp1252', 'replace')
    return str(value).lower()


def _extra_strings(node) -> list:
    """Every NiStringExtraData text on `node`, lower-cased."""
    return [_text(extra.string_data) for extra in node.get_extra_datas()
            if getattr(extra, 'string_data', None) is not None]


def source_children_owner(root):
    """The node that holds the SOURCE root's children.

    `_wrap_root_transform` moves them under one NiNode carrying the root's
    name when it bakes a root rotation; the RootCollisionNode moves with
    them, so that inner node is where the engine's direct-child search must
    run after conversion.
    See: docs/commentary/asset_convert_nif.md#morrowind-collision
    """
    kids = [c for c in (getattr(root, 'children', None) or []) if c is not None]
    if (len(kids) == 1 and type(kids[0]).__name__ == 'NiNode'
            and kids[0].name == root.name):
        return kids[0]
    return root


def collision_source(root) -> tuple:
    """(subtree, generated) naming the geometry Morrowind collides with.

    `subtree` is None when the mesh has no collision: an `NC`/`NCC` root
    extra, or an EMPTY RootCollisionNode, which the engine treats as
    camera-only. `generated` is True when the render mesh itself is the
    collision, because no RootCollisionNode exists.
    See: docs/commentary/asset_convert_nif.md#morrowind-collision
    """
    if any(s.startswith(_NO_COLLISION_PREFIX) for s in _extra_strings(root)):
        return None, False
    node = find_collision_node(source_children_owner(root))
    if node is not None:
        return (node if getattr(node, 'num_children', 0) else None), False
    return root, True


def _collision_shapes(node, skip_markers: bool):
    """The render shapes under `node` the engine builds collision from.

    AvoidNode subtrees are AI hints, skinned shapes are actors, a node an
    inactive NiCollisionSwitch became prunes its whole subtree, and a switch
    node contributes its first child only (`bulletnifloader.cpp:handleNode`).
    """
    type_name = type(node).__name__
    if type_name in _HELPER_TYPES or getattr(node, '_mw_no_collision', False):
        return
    if isinstance(node, NifFormat.NiTriBasedGeom):
        marker = skip_markers and _text(node.name).startswith(
            _MARKER_SHAPE_PREFIX)
        if node.skin_instance is None and not marker:
            yield node
        return
    children = [c for c in (getattr(node, 'children', None) or [])
                if c is not None]
    if type_name in _FIRST_CHILD_ONLY:
        children = children[:1]
    for child in children:
        yield from _collision_shapes(child, skip_markers)


def collision_triangles(node, root=None, scale: float = _HAVOK_SCALE) -> list:
    """Every collision triangle under `node`, in havok units in `root`'s frame.

    Returns [] when the subtree holds no usable geometry, or more than
    `_MAX_COLLISION_TRIS`, which is treated as no collision.
    """
    root = node if root is None else root
    skip_markers = _MARKER_EXTRA in _extra_strings(root)
    out = []
    for block in _collision_shapes(node, skip_markers):
        data = block.data
        if data is None:
            continue
        verts = _transformed_verts(block, root, data, scale)
        for a, b, c in data.get_triangles():
            if a != b and b != c and a != c:
                out.append((verts[a], verts[b], verts[c]))
        if len(out) > _MAX_COLLISION_TRIS:
            return []
    return out


def _transformed_verts(block, root, data, scale: float) -> list:
    """This shape's vertices in the root's frame, scaled to havok units."""
    m = block.get_transform(root)
    out = []
    for v in data.vertices:
        x = v.x * m.m_11 + v.y * m.m_21 + v.z * m.m_31 + m.m_41
        y = v.x * m.m_12 + v.y * m.m_22 + v.z * m.m_32 + m.m_42
        z = v.x * m.m_13 + v.y * m.m_23 + v.z * m.m_33 + m.m_43
        out.append((x * scale, y * scale, z * scale))
    return out


def _set_box_inertia(body, tris, mass) -> None:
    """Solid-box inertia over the triangles' AABB, about the center of mass.

    Morrowind authors no tensor, so it is computed rather than rescaled: these
    triangles are already in Skyrim havok units and need no _HAVOK_SCALE**2.
    """
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    dx = max(max(xs) - min(xs), _MIN_INERTIA_EXTENT)
    dy = max(max(ys) - min(ys), _MIN_INERTIA_EXTENT)
    dz = max(max(zs) - min(zs), _MIN_INERTIA_EXTENT)
    k = mass / 12.0
    body.center.x = (max(xs) + min(xs)) / 2.0
    body.center.y = (max(ys) + min(ys)) / 2.0
    body.center.z = (max(zs) + min(zs)) / 2.0
    body.inertia.m_11 = k * (dy * dy + dz * dz)
    body.inertia.m_22 = k * (dx * dx + dz * dz)
    body.inertia.m_33 = k * (dx * dx + dy * dy)


def _set_clutter_motion(body, tris, mass) -> None:
    """Make `body` a simulated clutter prop of `mass` kilograms.

    See: docs/commentary/asset_convert_collision.md#nif-dynamic-clutter-physics
    """
    body.mass = mass
    _set_box_inertia(body, tris, mass)
    body.motion_system = 3
    body.quality_type = 4
    body.solver_deactivation = 2
    body.havok_col_filter.layer = _SKYL_CLUTTER
    body.havok_col_filter_copy.layer = _SKYL_CLUTTER


def build_collision(root, tris, mass=None):
    """A bhkCollisionObject over `tris`, or None when it cannot build.

    Without `mass` the body is the vanilla static block SpeedTree already
    establishes for a generated CMS: identity transform, mass 0, and the
    collision layer every immovable object uses.  With one it is simulated
    clutter, which needs a CONVEX shape -- havok will not simulate the
    concave MOPP the static path builds.  The material was sampled from the
    render geometry before the upgrade.
    See: docs/commentary/asset_convert_nif.md#morrowind-surface-materials
    """
    material = havok_material(root)
    shape = (build_clutter_hull(tris, material) if mass
             else build_cms_collision(tris, material, NifFormat))
    if shape is None:
        return None
    if not mass:
        shape.shape.target = root

    body = NifFormat.bhkRigidBody()
    body.shape = shape
    body.mass = 0.0
    body.friction = 0.5
    body.restitution = 0.4
    body.linear_damping = 0.0996
    body.angular_damping = 0.0498
    body.max_linear_velocity = 104.4
    body.max_angular_velocity = 31.57
    body.motion_system = 5
    body.quality_type = 0
    body.deactivator_type = 1
    body.havok_col_filter.layer = 1
    body.havok_col_filter_copy.layer = 1
    body.unknown_int_2 = 1
    body.unknown_3_ints[2] = -2147483648
    body.unknown_byte = 116
    body.unknown_time_factor_or_gravity_factor_1 = 1.0
    body.unknown_time_factor_or_gravity_factor_2 = 1.0
    if mass:
        _set_clutter_motion(body, tris, mass)

    obj = NifFormat.bhkCollisionObject()
    obj.flags = 129
    obj.target = root
    obj.body = body
    return obj


def _compact(array_owner, count_attr: str, array_attr: str, keep: list) -> int:
    """Rewrite one pyffi ref array to `keep`; how many entries were dropped."""
    array = getattr(array_owner, array_attr)
    dropped = getattr(array_owner, count_attr) - len(keep)
    if dropped <= 0:
        return 0
    setattr(array_owner, count_attr, len(keep))
    array.update_size()
    for i, item in enumerate(keep):
        array[i] = item
    return dropped


def _strip_children(root, doomed) -> int:
    """Drop `doomed` children from `root`, compacting the array."""
    keep = [c for c in root.children if c is not None and id(c) not in doomed]
    return _compact(root, 'num_children', 'children', keep)


def _count(stats, key: str, amount: int = 1) -> None:
    """Add to one stats counter, when stats are being kept."""
    if stats is not None and amount:
        stats[key] = stats.get(key, 0) + amount


def _recompute_bsx(root) -> None:
    """Replace the BSXFlags the collision-less tree earned with a fresh one."""
    keep = [e for e in root.extra_data_list
            if e is not None and not isinstance(e, NifFormat.BSXFlags)]
    _compact(root, 'num_extra_data_list', 'extra_data_list', keep)
    add_bsx_flags(root)


def _loose_item_mass(root):
    """The clutter mass for this tree, or None when it must stay static.

    A skinned tree is a WORN mesh -- a wearable's biped model, which the
    armor path rigs to the body.  Its shape follows bones rather than a
    rigid body, so simulating it would detach the gear from the actor; only
    the record's separate world model is the dropped item.
    """
    mass = mesh_clutter_mass()
    if mass is None:
        return None
    for block in root.tree():
        if isinstance(block, NifFormat.NiTriBasedGeom) and block.skin_instance:
            return None
    return mass


def attach_morrowind_collision(root, stats=None) -> bool:
    """Give a CONVERTED root the collision Morrowind's engine would build.

    Runs after the Oblivion collision pass, whose `_convert_shape` unwraps
    every bhkMoppBvTreeShape as stale Oblivion data, and after the root swap,
    so the CMS targets the final root. A RootCollisionNode is consumed and
    stripped either way: it must never render.  An item record's model is
    simulated clutter rather than a static; `clutter_plan` holds that mapping.
    See: docs/commentary/asset_convert_collision.md#morrowind-dynamic-clutter
    """
    node, generated = collision_source(root)
    tris = collision_triangles(node, root) if node is not None else []
    built = False
    if tris and getattr(root, 'collision_object', None) is None:
        obj = build_collision(root, tris, _loose_item_mass(root))
        if obj is not None:
            root.collision_object = obj
            _recompute_bsx(root)
            built = True
    owner = source_children_owner(root)
    rcn = find_collision_node(owner)
    if rcn is not None:
        _strip_children(owner, {id(rcn)})
        _count(stats, 'mw_collision_stripped')
    _count(stats, 'mw_collision_built', int(built))
    _count(stats, 'mw_collision_generated', int(built and generated))
    return built


def strip_helper_nodes(root, stats=None) -> int:
    """Remove the Morrowind-only helper nodes Skyrim would draw.

    AvoidNode marks geometry the pathing system routes around; it is not
    renderable content (`bulletnifloader.cpp:233`).
    """
    doomed = {id(c) for c in (getattr(root, 'children', None) or [])
              if c is not None and type(c).__name__ in _HELPER_TYPES}
    if not doomed:
        return 0
    dropped = _strip_children(root, doomed)
    _count(stats, 'mw_helpers_stripped', dropped)
    return dropped


def _as_ni_node(block):
    """A plain NiNode carrying everything `block` held as a NiNode.

    The legacy types add no fields of their own, so copying the NiNode
    attributes is lossless.
    """
    node = NifFormat.NiNode()
    node.name = block.name
    node.flags = block.flags
    node.translation = block.translation
    node.rotation = block.rotation
    node.scale = block.scale
    node.collision_object = block.collision_object
    node.controller = block.controller
    for count, array in (('num_children', 'children'),
                         ('num_extra_data_list', 'extra_data_list'),
                         ('num_properties', 'properties'),
                         ('num_effects', 'effects')):
        source = getattr(block, array)
        setattr(node, count, getattr(block, count))
        getattr(node, array).update_size()
        for i in range(getattr(block, count)):
            getattr(node, array)[i] = source[i]
    return node


def _keep_nearest_lod(block) -> None:
    """Drop every LOD level but the nearest, which child 0 always is."""
    keep = [c for c in (block.children or []) if c is not None][:1]
    _compact(block, 'num_children', 'children', keep)


def convert_legacy_nodes(data, stats=None) -> int:
    """Replace the node types Skyrim has no RTTI for; how many were rewritten.

    A block type the engine cannot instantiate rejects the whole file, so the
    mesh renders as the missing-model red triangle. NiSwitchNode is the
    control: same family, 88 vanilla Skyrim meshes, and it is left alone.
    An inactive NiCollisionSwitch carries its "no collision" meaning in its
    TYPE, which the rewrite erases, so the decision is marked here instead.
    See: docs/commentary/asset_convert_nif.md#morrowind-legacy-node-types
    """
    replaced = {}
    for block in data.blocks:
        name = type(block).__name__
        if name == _LOD_NODE:
            _keep_nearest_lod(block)
        if name in _REWRITE_AS_NINODE or name == _LOD_NODE:
            node = _as_ni_node(block)
            if (name == 'NiCollisionSwitch'
                    and not block.flags & _ACTIVE_COLLISION_FLAG):
                node._mw_no_collision = True
            replaced[id(block)] = (block, node)
    if not replaced:
        return 0
    holders = list(data.blocks) + [new for _, new in replaced.values()]
    for old, new in replaced.values():
        for block in holders:
            block.replace_global_node(old, new)
        data.roots = [new if r is old else r for r in data.roots]
    _count(stats, 'mw_legacy_nodes', len(replaced))
    return len(replaced)


def strip_collision_nodes(data, stats=None) -> int:
    """Drop every RootCollisionNode in the tree; how many were removed.

    `attach_morrowind_collision` strips only the one the engine's direct-child
    search finds, which decides the collision that gets BUILT. Walks the live
    root graph, not `data.blocks`: that list is derived and omits a node the
    earlier strip detached, hiding its still-attached sibling.
    See: docs/commentary/asset_convert_nif.md#morrowind-legacy-node-types
    """
    removed = 0
    seen = set()
    pending = [r for r in data.roots if r is not None]
    while pending:
        node = pending.pop()
        if id(node) in seen or not hasattr(node, 'children'):
            continue
        seen.add(id(node))
        doomed = {id(c) for c in (node.children or [])
                  if c is not None and is_collision_node(c)}
        if doomed:
            removed += _strip_children(node, doomed)
        pending.extend(c for c in (node.children or []) if c is not None)
    _count(stats, 'mw_nested_collision_stripped', removed)
    return removed


def raise_triangle_flags(data, stats=None) -> int:
    """Set `has_triangles` on shapes that carry triangles but declare none.

    Returns the number of shapes fixed. Emptiness is judged on the ARRAY,
    never on the flag, which does not exist below 10.1.0.0.
    See: docs/commentary/asset_convert_nif.md#morrowind-triangle-flag
    """
    fixed = 0
    for block in data.blocks:
        tris = getattr(block, 'triangles', None)
        if not tris or getattr(block, 'has_triangles', True):
            continue
        block.has_triangles = True
        block.num_triangles = len(tris)
        block.num_triangle_points = len(tris) * 3
        fixed += 1
    _count(stats, 'mw_triangle_flags', fixed)
    return fixed


def build_skin_partitions(data, stats=None) -> int:
    """Give every skinned shape the NiSkinPartition Skyrim renders from.

    Returns the number of shapes partitioned. NiSkinPartition postdates
    4.0.0.2, so no Morrowind mesh carries one and the renderer dereferences
    the null. The partition is MOVED onto the NiSkinInstance, where Skyrim
    reads it; pyffi's builder leaves it on the NiSkinData instead.
    See: docs/commentary/asset_convert_nif.md#morrowind-skin-partitions
    """
    built = 0
    for block in data.blocks:
        if not isinstance(block, NifFormat.NiTriBasedGeom):
            continue
        skin = block.skin_instance
        if skin is None or skin.data is None:
            continue
        if getattr(skin, 'skin_partition', None) is not None:
            continue
        try:
            block.update_skin_partition(
                maxbonesperpartition=_MAX_BONES_PER_PARTITION,
                maxbonespervertex=_MAX_WEIGHTS_PER_VERTEX,
                stripify=False, padbones=False, verbose=0)
        except Exception:
            _count(stats, 'mw_skin_failed')
            continue
        partition = getattr(skin.data, 'skin_partition', None)
        if partition is None:
            continue
        skin.skin_partition = partition
        skin.data.skin_partition = None
        built += 1
    _count(stats, 'mw_skin_partitions', built)
    return built


def disable_specular(data, stats=None) -> int:
    """Clear the specular flag on every converted lighting shader.

    Returns the number of shaders changed. Morrowind's renderer never
    applied specular lighting, whatever the material said.
    See: docs/commentary/asset_convert_nif.md#morrowind-specular
    """
    cleared = 0
    for root in data.roots:
        for block in root.tree():
            if not isinstance(block, NifFormat.BSLightingShaderProperty):
                continue
            if block.shader_flags_1.slsf_1_specular:
                block.shader_flags_1.slsf_1_specular = 0
                cleared += 1
    _count(stats, 'mw_specular_cleared', cleared)
    return cleared


def run_morrowind_fixups(data, stats=None) -> None:
    """Apply the Morrowind-only repairs that must precede the version upgrade."""
    raise_triangle_flags(data, stats)
    sample_materials(data, stats)
    upgrade_legacy_particles(data, stats)
    convert_legacy_nodes(data, stats)
    for root in data.roots:
        if hasattr(root, 'children'):
            strip_helper_nodes(root, stats)
    src_path = (stats or {}).get('_src_path', '')
    if os.path.basename(src_path).lower() == 'skeleton.nif':
        attach_synthetic_bodies(data, src_path)


def is_morrowind(data) -> bool:
    """Whether this NIF came from Morrowind rather than a later game."""
    return 0 < getattr(data, 'version', 0) <= 0x04000002
