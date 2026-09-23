"""
Assemble Morrowind's per-body-part wearables into one worn NIF per record.

Morrowind dresses an actor from BODY meshes hung on named nodes of its
skeleton; Skyrim wants one skinned mesh per armor addon. The export lists each
wearable's parts (`MorrowindPart[i].Slot/.Male/.Female`) and names the worn
model it expects; this module builds that model from the parts and the
rest-pose skeleton, at Morrowind's own NIF version: a rigid part becomes a
one-bone Prn piece hung from base_anim's attach node, a skinned part is bound
to the T-posed bind skeleton every vanilla skinned part shares (the retarget
then runs from Morrowind's own rig), and a shield sits in Oblivion's shield
attach frame.

See: docs/commentary/asset_convert_armor.md#morrowind-armor-assembly
See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
"""

import io
import os
from pathlib import Path

import numpy as np

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert import paths
from asset_convert.character.prn_skin import (rigid_skin_data,
                                              rigid_skin_instance)
from asset_convert.character.skin_retarget import (
    SKEL_MORROWIND, SKEL_SKYRIM_FEMALE, SKEL_SKYRIM_MALE, get_block_name,
    load_skeleton, local_for_world, m44_to_np, manual_update_bind_position,
    skin_transform_to_np, write_node_transform)
from asset_convert.character.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP
from asset_convert.character.wearable_plan import iter_records
from asset_convert.nif.nif_flags import NIF_FLAGS
from asset_convert.nif.sse_nif import read_nif
from asset_convert.sources import base_plugins
from asset_convert.sources.morrowind_assets import resolve_mesh

#: Body-part slot -> the skeleton node Morrowind hangs it on (OpenMW npcanimation.cpp sPartList).
PART_ATTACH_NODES = {
    0: 'Head', 1: 'Head', 2: 'Neck', 3: 'Chest', 4: 'Groin', 5: 'Groin',
    6: 'Right Hand', 7: 'Left Hand', 8: 'Right Wrist', 9: 'Left Wrist',
    10: 'Shield', 11: 'Right Forearm', 12: 'Left Forearm',
    13: 'Right Upper Arm', 14: 'Left Upper Arm', 15: 'Right Foot',
    16: 'Left Foot', 17: 'Right Ankle', 18: 'Left Ankle', 19: 'Right Knee',
    20: 'Left Knee', 21: 'Right Upper Leg', 22: 'Left Upper Leg',
    23: 'Right Clavicle', 24: 'Left Clavicle', 25: 'Weapon Bone', 26: 'Tail',
}

#: Gender -> the rest-pose skeleton its parts are placed against; the female file is optional.
SKELETON_NIFS = {False: 'base_anim.nif', True: 'base_anim_female.nif'}

#: The shield slot; its part stays rigid in the forearm's frame, which is Oblivion's shield frame.
_SHIELD_SLOT = 10
_SHIELD_BONE = 'Bip01 L Forearm'

#: Part node whose own translation offsets a rigid part from its attach node.
_BONE_OFFSET = 'BoneOffset'

#: Attach nodes containing this are mirrored in X, sharing the right side's mesh.
_LEFT = 'Left'

#: Shape-name prefix the skinned-part filter looks past.
_TRI_PREFIX = 'tri '

#: Prefix of the skeleton's bone nodes; everything else under them is an attach node.
_BONE_PREFIX = 'Bip01'

#: Record files that carry wearables.
_WEARABLE_FILES = ('ARMO.txt', 'CLOT.txt')


def shape_vertices(shape) -> np.ndarray:
    """The shape's stored vertices as an (n, 3) array."""
    return np.array([[v.x, v.y, v.z] for v in shape.data.vertices],
                    dtype=np.float64)


def shape_normals(shape):
    """The shape's stored normals as an (n, 3) array, or None."""
    if not getattr(shape.data, 'has_normals', 0):
        return None
    return np.array([[n.x, n.y, n.z] for n in shape.data.normals],
                    dtype=np.float64)


def write_geometry(shape, verts, normals) -> None:
    """Store `verts`/`normals` and zero the shape's own transform."""
    data = shape.data
    for v, (x, y, z) in zip(data.vertices, verts):
        v.x, v.y, v.z = float(x), float(y), float(z)
    if normals is not None:
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        lengths[lengths == 0] = 1.0
        for n, (x, y, z) in zip(data.normals, normals / lengths):
            n.x, n.y, n.z = float(x), float(y), float(z)
    data.update_center_radius()
    write_node_transform(shape, np.eye(4))


def _world_in(node, root) -> np.ndarray:
    """`node`'s transform relative to `root` (identity when it is the root)."""
    if node is root:
        return np.eye(4)
    return m44_to_np(node.get_transform(root))


def _full_transform(node, part_root) -> np.ndarray:
    """The node's transform in the part file's frame, root transform included."""
    return _world_in(node, part_root) @ m44_to_np(part_root.get_transform())


def bind_worlds(path) -> dict:
    """{Bip01 bone: 4x4} of a part file's bind skeleton, root transform included."""
    root = read_nif(str(path)).roots[0]
    return {get_block_name(n): _full_transform(n, root) for n in root.tree()
            if isinstance(n, NifFormat.NiNode) and get_block_name(n).startswith(_BONE_PREFIX)}


def _rotation_between(here, there) -> np.ndarray:
    """Row-vector rotation turning direction `here` onto `there`."""
    here = here / np.linalg.norm(here)
    there = there / np.linalg.norm(there)
    axis = np.cross(here, there)
    sine = np.linalg.norm(axis)
    if sine <= 1e-6:
        return np.eye(3)
    axis /= sine
    cosine = float(np.clip(here @ there, -1.0, 1.0))
    skew = np.array([[0, -axis[2], axis[1]],
                     [axis[2], 0, -axis[0]],
                     [-axis[1], axis[0], 0]])
    return (np.eye(3) + sine * skew + (1 - cosine) * skew @ skew).T


def _geometries(root) -> list:
    """Every triangle shape at or under `root`."""
    if isinstance(root, NifFormat.NiTriBasedGeom):
        return [root]
    return [b for b in root.tree() if isinstance(b, NifFormat.NiTriBasedGeom)]


def _weights(shape, skin) -> np.ndarray:
    """(vertices, bones) skin weights, rows normalized where they carry any."""
    table = np.zeros((shape.data.num_vertices, skin.num_bones))
    for i in range(min(skin.num_bones, skin.data.num_bones)):
        for vw in skin.data.bone_list[i].vertex_weights:
            table[vw.index, i] = vw.weight
    total = table.sum(axis=1, keepdims=True)
    total[total == 0] = 1.0
    return table / total


def _blend(verts, normals, weights, chains) -> tuple:
    """Linear-blend `verts` (n, 4) and `normals` over one 4x4 chain per bone."""
    moved, turned = np.zeros((len(verts), 3)), np.zeros((len(verts), 3))
    for i, chain in enumerate(chains):
        moved += weights[:, i:i + 1] * (verts @ chain[:, :3])
        if normals is not None:
            turned += weights[:, i:i + 1] * (normals @ chain[:3, :3])
    return moved, turned if normals is not None else None


def _bind_world(shape, weights, part_root) -> tuple:
    """The skinned shape's bind-pose vertices and normals in the part file's frame.

    The skinning contract (NifSkope `glmesh.cpp`): a vertex taken into the
    skeleton root's frame is blended over `skin transform @ bone bind @ bone
    world`, each relative to that root.
    """
    skin = shape.skin_instance
    skel = skin.skeleton_root if skin.skeleton_root is not None else part_root
    skel_world = _full_transform(skel, part_root)
    to_skel = _full_transform(shape, part_root) @ np.linalg.inv(skel_world)
    verts = shape_vertices(shape) @ to_skel[:3, :3] + to_skel[3, :3]
    normals = shape_normals(shape)
    if normals is not None:
        normals = normals @ to_skel[:3, :3]
    overall = skin_transform_to_np(skin.data.skin_transform)
    chains = [overall @ skin_transform_to_np(skin.data.bone_list[i].skin_transform)
              @ _full_transform(skin.bones[i], part_root)
              for i in range(skin.num_bones)]
    return _blend(np.hstack([verts, np.ones((len(verts), 1))]), normals,
                  weights, chains)


def _flat_bone(root, name: str):
    """A bone placeholder directly under the root, as `add_prn_skin` builds."""
    node = NifFormat.NiNode()
    node.name = name.encode('latin-1')
    node.flags = NIF_FLAGS
    root.add_child(node)
    return node


def _wears(shape, node_name: str) -> bool:
    """Whether a skinned part's shape belongs on `node_name`.

    OpenMW `CopyRigVisitor::filterMatches`: the shape name starts with the
    attach node's name, case-insensitively, optionally after `Tri `.
    """
    name = get_block_name(shape).lower()
    if name.startswith(_TRI_PREFIX):
        name = name[len(_TRI_PREFIX):]
    return name.startswith(node_name.lower())


def _attach_frame(part_root, node_name: str, node_world) -> np.ndarray:
    """The frame a rigid part's root is placed in, as OpenMW `attach` builds it.

    A `BoneOffset` node's translation offsets the part, and a node whose name
    holds `Left` mirrors it in X: Morrowind ships one mesh for both sides.
    """
    frame = np.eye(4)
    if _LEFT in node_name:
        frame[0, 0] = -1.0
    for node in part_root.tree():
        if isinstance(node, NifFormat.NiNode) \
                and get_block_name(node) == _BONE_OFFSET:
            frame[3, :3] = [node.translation.x, node.translation.y,
                            node.translation.z]
            break
    return frame @ node_world


def _reverse_winding(shape) -> None:
    """Flip every triangle so a mirrored part keeps its faces outward."""
    for tri in shape.data.triangles:
        tri.v_2, tri.v_3 = tri.v_3, tri.v_2


def _prune_bones(root, used) -> None:
    """Drop every bone node no part references, keeping the ancestors of `used`."""
    nodes = [n for n in root.tree() if isinstance(n, NifFormat.NiNode)]
    parents = {id(c): n for n in nodes for c in n.children if c is not None}
    keep = set()
    for node in used:
        while node is not None and id(node) not in keep:
            keep.add(id(node))
            node = parents.get(id(node))
    for node in nodes:
        wanted = [c for c in node.children if c is not None and (
            id(c) in keep or not get_block_name(c).startswith(_BONE_PREFIX))]
        if len(wanted) != node.num_children:
            node.num_children = len(wanted)
            node.children.update_size()
            for i, child in enumerate(wanted):
                node.children[i] = child


def _bone_tree(data, old_root) -> bytes:
    """The skeleton's bone subtree alone, under a fresh root, as NIF bytes."""
    bones = [b for b in old_root.tree() if isinstance(b, NifFormat.NiNode)
             and get_block_name(b).startswith(_BONE_PREFIX)]
    for bone in bones:
        keep = [c for c in bone.children if isinstance(c, NifFormat.NiNode)
                and get_block_name(c).startswith(_BONE_PREFIX)]
        bone.num_children = len(keep)
        bone.children.update_size()
        for i, child in enumerate(keep):
            bone.children[i] = child
        bone.controller = None
        bone.extra_data = None
    root = NifFormat.NiNode()
    root.flags = NIF_FLAGS
    root.add_child(bones[0])
    data.roots = [root]
    buffer = io.BytesIO()
    data.write(buffer)
    return buffer.getvalue()


class RestSkeleton:
    """A rest-pose skeleton: bone frames, attach nodes, and a bones-only tree.

    `world` holds every bone's frame in base_anim's animation rest (where
    rigid parts hang), `bind` the T-posed bind skeleton skinned parts are
    authored in, `attach` every attach node as (bone name, node world frame),
    and `tree` the bone hierarchy as NIF bytes a record can load fresh.
    See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
    """

    def __init__(self, path, skyrim: dict, bind: dict = None):
        """Index `path` against the Skyrim skeleton `skyrim` and the bind skeleton `bind`."""
        data = read_nif(str(path))
        old_root = data.roots[0]
        self.skyrim, self.bind = skyrim, bind or {}
        self.world, self.parent, self.child, self.attach = {}, {}, {}, {}
        for node in old_root.tree():
            if isinstance(node, NifFormat.NiNode) \
                    and get_block_name(node).startswith(_BONE_PREFIX):
                self._index_bone(node, old_root)
        self.tree = _bone_tree(data, old_root)

    def _index_bone(self, node, old_root) -> None:
        """Record the bone's frame, its first bone child and its attach nodes."""
        name = get_block_name(node)
        self.world[name] = m44_to_np(node.get_transform(old_root))
        for kid in node.children:
            if not isinstance(kid, NifFormat.NiNode):
                continue
            kid_name = get_block_name(kid)
            if kid_name.startswith(_BONE_PREFIX):
                self.parent[kid_name] = name
                self.child.setdefault(name, kid_name)
            else:
                self._index_attach(kid, name, old_root)

    def _index_attach(self, node, bone: str, old_root) -> None:
        """Record `node` and its descendants as attach points of `bone`."""
        self.attach[get_block_name(node)] = (
            bone, m44_to_np(node.get_transform(old_root)))
        for kid in node.children:
            if isinstance(kid, NifFormat.NiNode):
                self._index_attach(kid, bone, old_root)

    def rest_world(self, name: str):
        """The bone's skinned rest frame: the bind skeleton's, else base_anim's, else None."""
        return self.bind.get(name, self.world.get(name))

    def direction_delta(self, bone: str) -> np.ndarray:
        """Rotation turning the bone's rest direction onto the Skyrim bone's.

        Identity for a bone without a mapped bone child (the head), or when
        both skeletons already agree.
        """
        child = self.child.get(bone)
        sk_bone = OBLIVION_TO_SKYRIM_BONE_MAP.get(bone)
        sk_child = OBLIVION_TO_SKYRIM_BONE_MAP.get(child)
        if child is None or sk_bone not in self.skyrim \
                or sk_child not in self.skyrim:
            return np.eye(3)
        return _rotation_between(
            self.world[child][3, :3] - self.world[bone][3, :3],
            self.skyrim[sk_child][3, :3] - self.skyrim[sk_bone][3, :3])

    def hierarchy(self) -> tuple:
        """(fresh Data of the bone tree posed at `rest_world`, {lower-case bone name: node}).

        Keyed lower-case because Morrowind binds skins to bones by
        case-insensitive name (`Bip01 R Upperarm` on a shirt, `UpperArm` on
        the skeleton).
        """
        data = read_nif(self.tree)
        bones = {get_block_name(b).lower(): b for b in data.roots[0].tree()
                 if isinstance(b, NifFormat.NiNode)
                 and get_block_name(b).startswith(_BONE_PREFIX)}
        for node in bones.values():
            name = get_block_name(node)
            world = self.rest_world(name)
            parent = self.parent.get(name)
            if world is not None:
                write_node_transform(node, world if parent is None
                                     else local_for_world(world, self.rest_world(parent)))
        return data, bones


def _adopt_rigid(shape, part_root, frame, bone, skel, root, flat) -> None:
    """Skin a rigid part onto a flat bone node as upright offsets from the pivot.

    The stored vertices are what `add_prn_skin` leaves for an Oblivion helmet:
    the bone pivot's position with the world's orientation, turned so the part
    hangs along the Skyrim bone's rest direction.
    See: docs/commentary/asset_convert_armor.md#morrowind-armor-assembly
    """
    full = _full_transform(shape, part_root) @ frame
    turn = skel.direction_delta(bone)
    verts = (shape_vertices(shape) @ full[:3, :3] + full[3, :3]
             - skel.world[bone][3, :3]) @ turn
    normals = shape_normals(shape)
    if normals is not None:
        normals = normals @ full[:3, :3] @ turn
    write_geometry(shape, verts, normals)
    if np.linalg.det(full[:3, :3]) < 0:
        _reverse_winding(shape)
    if bone not in flat:
        flat[bone] = _flat_bone(root, bone)
    shape.skin_instance = rigid_skin_instance(
        root, flat[bone], rigid_skin_data(shape.data), 0, True)
    root.add_child(shape)


def _adopt_skinned(shape, part_root, skel, root, bones) -> list:
    """Re-bind a skinned part to the shared skeleton at its rest (bind) pose.

    Bind-pose vertices are moved from the part's own bind skeleton onto the
    shared rest bone by bone (linear blend; a no-op for the vanilla parts,
    which share one bind skeleton), then bound to the tree `hierarchy` builds
    in that pose. Returns the shared bones the shape now uses.
    """
    skin = shape.skin_instance
    weights = _weights(shape, skin)
    verts, normals = _bind_world(shape, weights, part_root)
    shared, deltas = [], []
    for i in range(skin.num_bones):
        node = bones.get(get_block_name(skin.bones[i]).lower())
        if node is None:
            raise ValueError(f'skinned part names unknown bone '
                             f'{get_block_name(skin.bones[i])!r}')
        rest = _full_transform(skin.bones[i], part_root)
        target = skel.rest_world(get_block_name(node))
        deltas.append(np.linalg.inv(rest) @ (rest if target is None else target))
        shared.append(node)
    verts, normals = _blend(np.hstack([verts, np.ones((len(verts), 1))]),
                            normals, weights, deltas)
    write_geometry(shape, verts, normals)
    for i, node in enumerate(shared):
        skin.bones[i] = node
    skin.skeleton_root = root
    root.add_child(shape)
    manual_update_bind_position(shape, skin, root)
    return shared


def _adopt_shield(shape, part_root, frame, skel, root) -> None:
    """Keep a shield rigid in the forearm's local frame.

    That frame is `Bip01 L ForearmTwist`'s convention as measured on both
    games' iron shields (X along the arm, boss at -Y, width on Z), so once
    `equipment_rig.name_morrowind_shield` names the Prn the Oblivion shield
    path seats it.
    See: docs/commentary/asset_convert_armor.md#morrowind-armor-assembly
    """
    full = _full_transform(shape, part_root) @ frame
    forearm = skel.world[_SHIELD_BONE]
    turn = np.linalg.inv(forearm[:3, :3])
    verts = (shape_vertices(shape) @ full[:3, :3] + full[3, :3] - forearm[3, :3]) @ turn
    normals = shape_normals(shape)
    if normals is not None:
        normals = normals @ full[:3, :3] @ turn
    write_geometry(shape, verts, normals)
    if np.linalg.det(full[:3, :3]) < 0:
        _reverse_winding(shape)
    root.add_child(shape)


def _adopt_rigid_world(shape, part_root, frame, bone, skel, place) -> list:
    """Skin a rigid part fully to its real bone, carried from base_anim onto the rest pose.

    The reference body the Morrowind wrap field is fitted from needs its
    rigid parts where the skinned rest holds their bone.
    See: docs/commentary/asset_convert_armor.md#morrowind-wrap-field
    """
    root, bones = place[1], place[2]
    full = (_full_transform(shape, part_root) @ frame @ np.linalg.inv(skel.world[bone])
            @ skel.rest_world(bone))
    normals = shape_normals(shape)
    write_geometry(shape, shape_vertices(shape) @ full[:3, :3] + full[3, :3],
                   None if normals is None else normals @ full[:3, :3])
    if np.linalg.det(full[:3, :3]) < 0:
        _reverse_winding(shape)
    shape.skin_instance = rigid_skin_instance(
        root, bones[bone.lower()], rigid_skin_data(shape.data), 0, True)
    root.add_child(shape)
    manual_update_bind_position(shape, shape.skin_instance, root)
    return [bones[bone.lower()]]


def _is_skinned_file(part_root) -> bool:
    """Whether any shape of the part file is skinned, which makes the file a rig."""
    return any(shape.skin_instance is not None for shape in _geometries(part_root))


def _adopt_shape(shape, part, place) -> list:
    """Move one shape onto the worn root by its kind; the bones it now uses.

    A rig file (any skinned shape) wears only its skinned shapes named for
    the attach node (OpenMW `CopyRigVisitor`); a file with no skin hangs
    whole on the node. `part` is (slot, attach node, part root, frame, rig),
    `rig` judged before any shape is adopted; `place` is (skeleton, worn
    root, shared bones, flat Prn bones, rigid_world).
    See: docs/commentary/asset_convert_armor.md#morrowind-armor-assembly
    """
    slot, node_name, part_root, frame, rig = part
    skel, root, bones, flat, rigid_world = place
    bone = skel.attach[node_name][0]
    if slot == _SHIELD_SLOT:
        _adopt_shield(shape, part_root, frame, skel, root)
        return []
    if rig:
        if shape.skin_instance is None or not _wears(shape, node_name):
            return []
        return _adopt_skinned(shape, part_root, skel, root, bones)
    if rigid_world:
        return _adopt_rigid_world(shape, part_root, frame, bone, skel, place)
    _adopt_rigid(shape, part_root, frame, bone, skel, root, flat)
    return [flat[bone]]


def assemble(skel: RestSkeleton, parts, roots, out_name: str,
             rigid_world: bool = False) -> NifFormat.Data:
    """One worn NIF from `parts`, each (slot, mesh path relative to a root).

    `rigid_world` binds rigid parts to their real bones at the rest pose
    instead of as Prn pieces, keeping the bone tree.
    """
    loaded = []
    for slot, rel in parts:
        path = resolve_mesh(roots, rel, paths.EXPORT)
        if path is None:
            raise ValueError(f'part mesh not found: {rel}')
        node_name = PART_ATTACH_NODES.get(slot)
        if node_name not in skel.attach:
            raise ValueError(f'slot {slot} has no attach node in the skeleton')
        loaded.append((slot, node_name, read_nif(str(path)).roots[0]))
    skinned = rigid_world or any(
        slot != _SHIELD_SLOT and shape.skin_instance is not None
        for slot, _node, part_root in loaded for shape in _geometries(part_root))
    data, bones = skel.hierarchy()
    root = data.roots[0]
    root.name = out_name.encode('latin-1', 'replace')
    if not skinned:
        root.num_children = 0
        root.children.update_size()
    place, used = (skel, root, bones, {}, rigid_world), []
    for slot, node_name, part_root in loaded:
        part = (slot, node_name, part_root,
                _attach_frame(part_root, node_name, skel.attach[node_name][1]),
                _is_skinned_file(part_root))
        for shape in _geometries(part_root):
            used.extend(_adopt_shape(shape, part, place))
    if skinned:
        _prune_bones(root, used)
    return data


def _worn_specs(rec_dir):
    """(worn model, [(slot, part mesh)], female) for every wearable the export lists."""
    for name in _WEARABLE_FILES:
        for rec in iter_records(Path(rec_dir) / name):
            count = int(rec.get('MorrowindPartCount', '0') or 0)
            male_out = rec.get('Male.BipedModel.MODL', '')
            if not count or not male_out:
                continue
            parts = [(int(rec[f'MorrowindPart[{i}].Slot']),
                      rec.get(f'MorrowindPart[{i}].Male', ''),
                      rec.get(f'MorrowindPart[{i}].Female', ''))
                     for i in range(count)]
            yield male_out, [(s, m or f) for s, m, f in parts], False
            female_out = rec.get('Female.BipedModel.MODL', '')
            if female_out:
                yield female_out, [(s, f or m) for s, m, f in parts], True


def rest_skeleton(nif, female: bool) -> RestSkeleton:
    """The gender's rest skeleton from base_anim `nif` and the generated bind skeleton."""
    return RestSkeleton(nif, load_skeleton(SKEL_SKYRIM_FEMALE if female else SKEL_SKYRIM_MALE),
                        load_skeleton(SKEL_MORROWIND[female]))


def _skeletons(roots) -> dict:
    """{female: RestSkeleton} for both genders; the female file falls back to the male."""
    male = resolve_mesh(roots, SKELETON_NIFS[False], paths.EXPORT)
    if male is None:
        return {}
    female = resolve_mesh(roots, SKELETON_NIFS[True], paths.EXPORT) or male
    return {False: rest_skeleton(male, False), True: rest_skeleton(female, True)}


def assemble_armor(rec_dir, meshes_root, log=print) -> int:
    """Write every worn NIF the export's wearables name; how many were written.

    Runs before the batch mesh conversion so the assembled files are converted
    with everything else. Part meshes and the skeletons resolve through this
    plugin's mesh tree, its masters', then the vanilla Morrowind install.
    """
    specs = list(_worn_specs(rec_dir))
    if not specs:
        return 0
    roots = [Path(meshes_root)] + [Path(d) / 'meshes'
                                   for d in base_plugins.export_dirs(rec_dir)]
    skeletons = _skeletons(roots)
    if not skeletons:
        log(f'  Morrowind armor: {SKELETON_NIFS[False]} not found; '
            f'{len(specs)} worn meshes skipped')
        return 0
    written, failed = 0, 0
    for out_rel, parts, female in specs:
        stem = os.path.splitext(os.path.basename(out_rel.replace(chr(92), '/')))[0]
        try:
            data = assemble(skeletons[female], parts, roots, stem)
        except (OSError, ValueError, KeyError) as exc:
            failed += 1
            log(f'  [skip] {out_rel}: {exc}')
            continue
        out_path = Path(meshes_root) / out_rel.replace(chr(92), '/')
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'wb') as fh:
            data.write(fh)
        written += 1
    log(f'  Morrowind armor: {written} worn meshes assembled'
        + (f', {failed} failed' if failed else ''))
    return written
