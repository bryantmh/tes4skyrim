"""Attach points, worn-armor skinning and inventory markers.

Three related jobs on a mesh a character carries or wears: remap the authored
`Prn` attach node onto the Skyrim skeleton and seat the geometry in that node's
frame; give worn armor and creature parts the skin Skyrim's renderer needs; and
orient the inventory marker the menu spins the model by.

Everything here keys off AUTHORED data -- the `Prn` string, the record's biped
slot, the source skeleton's own bone positions -- never a bounding box or a
filename guess, except the one documented 1H weapon-type refinement.

See: docs/commentary/asset_convert_armor.md#shield-attachment
"""

import json
import os

import numpy as np

from asset_convert import paths
from asset_convert.character.body_wrap import wrap_available, wrap_has_head
from asset_convert.character.bow_rig import add_bow_rig
from asset_convert.character.head_gear import (fit_prn_head_blocks,
                                               remap_bone_names)
from asset_convert.character.prn_skin import (BODY_PART_FALLBACK_PRN_BONE,
                                              add_prn_skin,
                                              bake_node_transforms_into_verts,
                                              bake_root_transform_into_verts,
                                              get_prn_bone,
                                              upgrade_skin_instances)
from asset_convert.character.skin_replacement import (apply_armor_offset,
                                                      collect_skin_info,
                                                      strip_body_skin_geometry)
from asset_convert.character.wearable_plan import mesh_is_ammo, mesh_weapon_prn
from asset_convert.character.skin_retarget import (dominant_body_part,
                                                   regen_skin_partition,
                                                   retarget_skin_to_skyrim)
from asset_convert.character.skyrim_overrides import (
    ARMOR_PIECE_OFFSETS,
    ARMOR_PIECE_OFFSETS_PRN,
    SHIELD_INV_MARKER_ROT_X,
    SHIELD_INV_MARKER_ROT_Y,
    SHIELD_INV_MARKER_ROT_Z,
    SHIELD_INV_MARKER_ZOOM,
    TORCH_INV_MARKER_ROT_X,
    TORCH_INV_MARKER_ROT_Y,
    TORCH_INV_MARKER_ROT_Z,
    TORCH_INV_MARKER_ZOOM,
    WEAPON_INV_MARKER_ROT_X,
    WEAPON_INV_MARKER_ROT_Y,
    WEAPON_INV_MARKER_ROT_Z,
    WEAPON_INV_MARKER_ZOOM,
)
from asset_convert.collision.collision import convert_all_collisions
from asset_convert.collision.collision_falloutnv import is_fallout_source
from asset_convert.collision.collision_constraints import (
    enforce_ragdoll_tree, scale_constraint_pivots,
    strip_marker_collision_bodies)
from asset_convert.havok.hkx_skeleton import BONE_RENAMES
from asset_convert.nif.inv_marker import compute_inv_rotation
from asset_convert.nif.nif_converter_morrowind import is_morrowind
from asset_convert.nif.nif_flags import NIF_FLAGS
from asset_convert.nif.nif_passes import add_bsx_flags

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat


# ---------------------------------------------------------------------------
# Attachment points
# ---------------------------------------------------------------------------

#: Oblivion Prn -> Skyrim skeleton node; see the doc, none of it is derivable.
_PRN_REMAP: dict[str, str] = {
    'BackWeapon':  'WeaponBack',
    'SideWeapon':  'WeaponSword',
    'Quiver':      'QUIVER',
    'Weapon':      'Weapon',
    'Shield':      'SHIELD',
    'Torch':       'SHIELD',
    'Bip01 L ForearmTwist': 'SHIELD',
    'Bip01 Head': 'NPC Head [Head]',
}

#: The Prn a Morrowind-version shield takes; the assembler builds it in that frame.
MORROWIND_SHIELD_PRN = 'Bip01 L ForearmTwist'

#: Filename keyword -> Skyrim Prn, refining Oblivion's single 1H node.
_WEAPON_FILENAME_PRN: list[tuple[str, str]] = [
    ('dagger',    'WeaponDagger'),
    ('mace',      'WeaponMace'),
    ('waraxe',    'WeaponAxe'),
    ('axe',       'WeaponAxe'),
    ('club',      'WeaponMace'),
    ('staff',     'WeaponStaff'),
    ('hammer',    'WeaponMace'),
]

#: Oblivion Prn values marking weapons/equipment, which also get a BSInvMarker.
_WEAPON_PRN_VALUES = frozenset({
    'SideWeapon', 'BackWeapon', 'Weapon', 'WeaponSword', 'WeaponBack',
    'WeaponMace', 'WeaponAxe', 'WeaponDagger', 'WeaponStaff', 'QUIVER',
    'Quiver',
})

#: Skyrim-side Prn values whose vanilla-derived BSInvMarker is already exact.
_EQUIPPED_PRN_VALUES = frozenset({
    'Weapon', 'WeaponSword', 'WeaponDagger', 'WeaponMace', 'WeaponAxe',
    'WeaponStaff', 'WeaponBack', 'WeaponBow', 'SHIELD', 'QUIVER', 'Quiver',
})

#: Biped slot -> the offset table entry that fits a piece on that slot.
_BP_TO_PIECE = {131: 'helmet', 32: 'cuirass', 44: 'greaves',
                33: 'gauntlets', 37: 'boots'}

#: Vanilla creature skeleton BSXFlags: Havok | Ragdoll | Dynamic | Articulated.
_CREATURE_SKELETON_BSX = 198

_SHIELD_ATTACH_T = None


def _remap_prn(oblivion_prn: str, nif_filename: str) -> str:
    """Map an Oblivion Prn value to the correct Skyrim skeleton node name.

    'SideWeapon' refines to a per-type node by filename keyword, and a
    'BackWeapon' whose name says bow becomes 'WeaponBow' -- with 'WeaponBack'
    the draw animation never reparents the mesh to the hand.
    See: docs/commentary/asset_convert_armor.md#prn-remap-table
    """
    skyrim_prn = _PRN_REMAP.get(oblivion_prn, oblivion_prn)
    lower = nif_filename.lower()
    if oblivion_prn == 'SideWeapon':
        for keyword, prn in _WEAPON_FILENAME_PRN:
            if keyword in lower:
                return prn
    elif oblivion_prn == 'BackWeapon' and 'bow' in lower:
        return 'WeaponBow'
    return skyrim_prn


def _anat_hand_frame(hand, mid_base, thumb_base):
    """Rows: [finger-dir, thumb-dir, cross, hand-origin] anatomy->world."""
    h = hand[3, :3]
    fdir = mid_base[3, :3] - h
    fdir /= np.linalg.norm(fdir)
    tdir = thumb_base[3, :3] - h
    tdir = tdir - (tdir @ fdir) * fdir
    tdir /= np.linalg.norm(tdir)
    q = np.cross(fdir, tdir)
    q /= np.linalg.norm(q)
    frame = np.eye(4)
    frame[0, :3] = fdir
    frame[1, :3] = tdir
    frame[2, :3] = q
    frame[3, :3] = h
    return frame


def _load_skeletons():
    """The Oblivion and Skyrim bone world-transform maps, as numpy 4x4s."""
    with open(paths.GENERATED / 'skeleton_bones_oblivion.json') as f:
        ob = {k: np.array(v, dtype=np.float64) for k, v in json.load(f).items()}
    with open(paths.GENERATED / 'skeleton_bones_skyrim_male.json') as f:
        sk = {k: np.array(v, dtype=np.float64) for k, v in json.load(f).items()}
    return ob, sk


def _forearm_clearance_fix(ob, sk, f_ob, f_sk):
    """Rotation about the grip putting the shield along the SKYRIM forearm.

    Identity when the two axes already agree.
    See: docs/commentary/asset_convert_armor.md#shield-forearm-clearance
    """
    w_s_inv = np.linalg.inv(sk['SHIELD'])[:3, :3]
    d_ob = ob['Bip01 L Forearm'][3, :3] - ob['Bip01 L Hand'][3, :3]
    d_ob /= np.linalg.norm(d_ob)
    d_ob = d_ob @ (np.linalg.inv(f_ob) @ f_sk)[:3, :3] @ w_s_inv
    d_ob /= np.linalg.norm(d_ob)
    d_sk = sk['NPC L Forearm [LLar]'][3, :3] - sk['NPC L Hand [LHnd]'][3, :3]
    d_sk /= np.linalg.norm(d_sk)
    d_sk = d_sk @ w_s_inv
    d_sk /= np.linalg.norm(d_sk)

    axis = np.cross(d_ob, d_sk)
    s = np.linalg.norm(axis)
    if s <= 1e-6:
        return np.eye(4)
    axis /= s
    c = float(np.clip(d_ob @ d_sk, -1.0, 1.0))
    k = np.array([[0, -axis[2], axis[1]],
                  [axis[2], 0, -axis[0]],
                  [-axis[1], axis[0], 0]])
    fix4 = np.eye(4)
    fix4[:3, :3] = np.eye(3) + s * k.T + (1 - c) * (k.T @ k.T)
    return fix4


def shield_attach_transform():
    """4x4 mapping shield geometry from Oblivion attach space to Skyrim's.

    Rotation in rows 0-2, translation in row 3; None when the skeleton JSONs
    are unavailable, in which case the shield keeps its Oblivion orientation.
    See: docs/commentary/asset_convert_armor.md#shield-forearm-clearance
    """
    global _SHIELD_ATTACH_T
    if _SHIELD_ATTACH_T is not None:
        return _SHIELD_ATTACH_T
    try:
        ob, sk = _load_skeletons()
        f_ob = _anat_hand_frame(ob['Bip01 L Hand'], ob['Bip01 L Finger2'],
                                ob['Bip01 L Finger0'])
        f_sk = _anat_hand_frame(sk['NPC L Hand [LHnd]'],
                                sk['NPC L Finger20 [LF20]'],
                                sk['NPC L Finger00 [LF00]'])
        transform = (ob['Bip01 L ForearmTwist'] @ np.linalg.inv(f_ob)
                     @ f_sk @ np.linalg.inv(sk['SHIELD']))
        _SHIELD_ATTACH_T = transform @ _forearm_clearance_fix(ob, sk,
                                                              f_ob, f_sk)
    except (OSError, KeyError, ValueError) as e:
        print(f'  WARNING: shield attach transform unavailable ({e}); '
              f'shield keeps Oblivion orientation')
        _SHIELD_ATTACH_T = None
    return _SHIELD_ATTACH_T


def _prn_value(root):
    """The root's authored `Prn` attachment string, or None."""
    for ed in getattr(root, 'extra_data_list', ()):
        if not isinstance(ed, NifFormat.NiStringExtraData):
            continue
        if bytes(ed.name).rstrip(b'\x00') != b'Prn':
            continue
        return bytes(ed.string_data).rstrip(b'\x00').decode(
            'latin-1', errors='replace')
    return None


def _apply_shield_transform(fade):
    """Seat a shield on the forearm exactly where Oblivion had it.

    See: docs/commentary/asset_convert_armor.md#shield-attachment
    """
    t = shield_attach_transform()
    if t is None:
        return
    for r in range(3):
        for c in range(3):
            setattr(fade.rotation, f'm_{r + 1}{c + 1}', float(t[r, c]))
    fade.translation.x = float(t[3, 0])
    fade.translation.y = float(t[3, 1])
    fade.translation.z = float(t[3, 2])


def _apply_axe_flip(fade):
    """Rotate an Oblivion side-carried weapon 180 degrees about the
    handle-blade axis; a FO3/FNV weapon is authored in its bone's frame.

    See: docs/commentary/asset_convert_armor.md#weapon-attachment
    See: docs/commentary/asset_convert_falloutnv.md#weapon-track-rename
    """
    if is_fallout_source():
        return
    fade.rotation.m_11, fade.rotation.m_12, fade.rotation.m_13 = -1.0, 0.0, 0.0
    fade.rotation.m_21, fade.rotation.m_22, fade.rotation.m_23 = 0.0, 1.0, 0.0
    fade.rotation.m_31, fade.rotation.m_32, fade.rotation.m_33 = 0.0, 0.0, -1.0


def add_inv_marker(node, rot_x, rot_y, rot_z, zoom):
    """Append a BSInvMarker so the item is visible in the inventory viewer."""
    marker = NifFormat.BSInvMarker()
    marker.name = b'INV'
    marker.rotation_x = rot_x
    marker.rotation_y = rot_y
    marker.rotation_z = rot_z
    marker.zoom = zoom
    node.num_extra_data_list += 1
    node.extra_data_list.update_size()
    node.extra_data_list[node.num_extra_data_list - 1] = marker


def _seat_equipment(fade, prn_val, remapped):
    """Add the inventory marker and attach transform this gear type needs."""
    if prn_val in _WEAPON_PRN_VALUES:
        add_inv_marker(fade, WEAPON_INV_MARKER_ROT_X,
                       WEAPON_INV_MARKER_ROT_Y, WEAPON_INV_MARKER_ROT_Z,
                       WEAPON_INV_MARKER_ZOOM)
        if remapped != 'WeaponBow':
            _apply_axe_flip(fade)
    elif remapped == 'SHIELD' and prn_val == 'Torch':
        add_inv_marker(fade, TORCH_INV_MARKER_ROT_X, TORCH_INV_MARKER_ROT_Y,
                       TORCH_INV_MARKER_ROT_Z, TORCH_INV_MARKER_ZOOM)
    elif remapped == 'SHIELD':
        add_inv_marker(fade, SHIELD_INV_MARKER_ROT_X,
                       SHIELD_INV_MARKER_ROT_Y, SHIELD_INV_MARKER_ROT_Z,
                       SHIELD_INV_MARKER_ZOOM)
        _apply_shield_transform(fade)


def _add_prn(fade, value: str):
    """Append the `Prn` NiStringExtraData naming the attach node."""
    new_prn = NifFormat.NiStringExtraData()
    new_prn.name = b'Prn'
    new_prn.string_data = value.encode('latin-1')
    fade.num_extra_data_list += 1
    fade.extra_data_list.update_size()
    fade.extra_data_list[fade.num_extra_data_list - 1] = new_prn


def name_morrowind_shield(data) -> None:
    """Name the Prn a 4.0.0.2 shield cannot carry, before the version upgrade."""
    for root in data.roots:
        if root is not None and _prn_value(root) is None:
            _add_prn(root, MORROWIND_SHIELD_PRN)


def convert_prn(root, fade, src_path):
    """Carry the authored Prn onto the new root, remapped to a Skyrim node.

    A Morrowind weapon takes the Prn its WEAP record gives it; an AMMO model
    without one hangs on QUIVER, unseated. A torch shares the SHIELD node but
    never the shield's attach transform.
    See: docs/commentary/asset_convert_armor.md#shield-attachment
    See: docs/commentary/asset_convert_falloutnv.md#ammo-prn
    """
    prn_val = _prn_value(root) or mesh_weapon_prn()
    if prn_val is None:
        if mesh_is_ammo():
            _add_prn(fade, 'QUIVER')
        return
    remapped = _remap_prn(prn_val, os.path.basename(src_path))
    _seat_equipment(fade, prn_val, remapped)
    _add_prn(fade, remapped)


# ---------------------------------------------------------------------------
# Creature rigs
# ---------------------------------------------------------------------------

#: (name, anchors, transform source). See: docs/commentary/asset_convert_creature.md#equip-node-synthesis
_CREATURE_EQUIP_NODES = (
    ('WeaponSword',  ('Bip01 Pelvis', 'Bip01 Spine'),           'WEAPON'),
    ('WeaponDagger', ('Bip01 Pelvis', 'Bip01 Spine'),           'WEAPON'),
    ('WeaponAxe',    ('Bip01 Pelvis', 'Bip01 Spine'),           'WEAPON'),
    ('WeaponMace',   ('Bip01 Pelvis', 'Bip01 Spine'),           'WEAPON'),
    ('WeaponBack',   ('Bip01 Spine2', 'Bip01 Spine1',
                      'Bip01 Spine'),                           'QUIVER'),
    ('WeaponBow',    ('Bip01 Spine2', 'Bip01 Spine1',
                      'Bip01 Spine'),                           'QUIVER'),
    ('WeaponStaff',  ('Bip01 Spine2', 'Bip01 Spine1',
                      'Bip01 Spine'),                           'QUIVER'),
)

#: Spell-cast attach points: one per hand plus a body-center node, as vanilla rigs carry.
_CREATURE_MAGIC_NODES = (
    ('NPC L MagicNode [LMag]', ('Bip01 L Hand',),               'SHIELD'),
    ('NPC R MagicNode [RMag]', ('Bip01 R Hand',),               'WEAPON'),
    ('MagicEffectsNode',       ('Bip01 Spine', 'Bip01 Spine1'), None),
)


def add_creature_equip_nodes(data):
    """Give a converted creature rig the equip/sheath nodes Skyrim expects.

    Returns the number of nodes added.  Runs AFTER the BONE_RENAMES pass so the
    renamed WEAPON/SHIELD/QUIVER nodes are available as transform sources, and
    is a no-op for any node the rig already has (so re-running is safe and a rig
    that legitimately ships one keeps its own).
    """
    added = 0
    for root in data.roots:
        if root is None:
            continue
        by_name, parent_of = {}, {}
        for block in root.tree():
            if not isinstance(block, NifFormat.NiNode):
                continue
            by_name.setdefault(
                bytes(block.name).rstrip(b'\x00').decode(
                    'cp1252', 'replace'), block)
            for child in block.children or []:
                if isinstance(child, NifFormat.NiNode):
                    parent_of[id(child)] = block

        for name, anchors, source in (_CREATURE_EQUIP_NODES
                                      + _CREATURE_MAGIC_NODES):
            if name in by_name:
                continue
            parent = next((by_name[a] for a in anchors if a in by_name), None)
            if parent is None:
                continue
            node = NifFormat.NiNode()
            node.name = name.encode('latin-1')
            src = by_name.get(source) if source else None
            if src is not None and parent_of.get(id(src)) is parent:
                node.translation.x = src.translation.x
                node.translation.y = src.translation.y
                node.translation.z = src.translation.z
                node.rotation = src.rotation
            node.scale = 1.0
            node.flags = parent.flags
            parent.add_child(node)
            by_name[name] = node
            added += 1
    return added


def _is_dead_controller(ctrl) -> bool:
    """Whether this controller is an Oblivion-runtime leftover Skyrim drives.

    A NiTransformController carrying an interpolator is real embedded
    animation and survives.
    See: docs/commentary/asset_convert_creature.md#dead-bone-controllers
    """
    return isinstance(ctrl, (NifFormat.bhkBlendController,
                             NifFormat.NiBSBoneLODController)) \
        or (isinstance(ctrl, NifFormat.NiTransformController)
            and getattr(ctrl, 'interpolator', None) is None)


def _strip_controller_chain(block) -> int:
    """Drop every dead controller from one block's chain; how many went."""
    removed = 0
    prev = None
    ctrl = getattr(block, 'controller', None)
    while ctrl is not None:
        nxt = getattr(ctrl, 'next_controller', None)
        if _is_dead_controller(ctrl):
            if prev is None:
                block.controller = nxt
            else:
                prev.next_controller = nxt
            removed += 1
        else:
            prev = ctrl
        ctrl = nxt
    return removed


def strip_creature_bone_controllers(data):
    """Remove Oblivion-runtime controllers from creature NIF node chains.

    Returns the number of controllers removed.
    See: docs/commentary/asset_convert_creature.md#dead-bone-controllers
    """
    removed = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if hasattr(block, 'controller'):
                removed += _strip_controller_chain(block)
    return removed


def prepare_creature_rig(data):
    """Rename the rig to Skyrim's contract and add the equip nodes.

    The rig root MUST be 'NPC Root [Root]': the engine binds the graph to the
    actor through that name, so a 'Bip01' root spawns an invisible actor. The
    rename covers every body part too, since skin bones resolve by node name.
    Oblivion-runtime bone controllers go first: vanilla Skyrim creature assets
    have none, because the behaviour graph drives the bones.
    """
    strip_creature_bone_controllers(data)
    renames = {k.encode('latin-1'): v.encode('latin-1')
               for k, v in BONE_RENAMES.items()}
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            nm = getattr(block, 'name', None)
            if nm is None:
                continue
            key = bytes(nm).rstrip(b'\x00')
            if key in renames:
                block.name = renames[key]
    add_creature_equip_nodes(data)


def rigid_skin_creature_parts(data):
    """Rigid-skin Prn-attached creature parts to their original bone.

    Heads, eyes and tails: the verts are baked into bone-local space first
    because add_prn_skin writes an identity bind, and the bone keeps its
    OBLIVION name, since the converted creature skeleton does too.
    See: docs/commentary/asset_convert_armor.md#rigid-prn-skinning
    """
    for root in data.roots:
        if root is not None and get_prn_bone(root) is not None:
            bake_node_transforms_into_verts(root)
            add_prn_skin(data, root, keep_bone_names=True, plain=True)


def regen_creature_skins(data, authored_bp, authored_allowed):
    """Rebuild every creature skin partition in Skyrim triangle format.

    Creature skins keep their Oblivion bones, weights and bind matrices
    verbatim -- the skeleton is the same -- so only the partition changes. It
    must run after the strips-to-shapes pass, which is what gives
    update_skin_partition triangles to read. The 80-bone cap is applied later,
    in merge_creature_body, because part NIFs store bones flat.
    """
    for root in data.roots:
        if root is None:
            continue
        for block in list(root.tree()):
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None:
                continue
            geom_name = bytes(block.name).rstrip(b'\x00').decode(
                'latin-1', errors='replace')
            regen_skin_partition(block, skin, geom_name,
                                 authored_body_part=authored_bp,
                                 authored_allowed=authored_allowed)


def _set_bsx_value(root, value):
    """Force the root's BSXFlags to `value`, adding the block if absent."""
    if not hasattr(root, 'extra_data_list'):
        return
    bsx = next((ed for ed in root.extra_data_list
                if isinstance(ed, NifFormat.BSXFlags)), None)
    if bsx is None:
        bsx = NifFormat.BSXFlags()
        bsx.name = b'BSX'
        root.num_extra_data_list += 1
        root.extra_data_list.update_size()
        root.extra_data_list[root.num_extra_data_list - 1] = bsx
    bsx.integer_data = value


def convert_root_collision(data, root, creature, nif_basename,
                           has_constraints):
    """Convert every collision object under `root`; the updated flag.

    Child-node collisions need the same Skyrim-format fields as the root's.
    A creature skeleton drops its marker proxies BEFORE the conversion, while
    the bodies still carry SOURCE units, so hkx_ragdoll's predicate agrees
    body for body.
    See: docs/commentary/asset_convert_creature.md#creature-mesh-merge
    """
    skeleton = creature and 'skeleton' in nif_basename
    if skeleton:
        strip_marker_collision_bodies(data, root)
    convert_all_collisions(root, keep_blend=creature)
    if has_constraints:
        scale_constraint_pivots(data)
    if skeleton and enforce_ragdoll_tree(data, root):
        has_constraints = True

    add_bsx_flags(root, has_constraints=has_constraints)
    if skeleton:
        _set_bsx_value(root, _CREATURE_SKELETON_BSX)
    return has_constraints


# ---------------------------------------------------------------------------
# Worn armor
# ---------------------------------------------------------------------------

def prepare_worn_armor(data, has_skin, is_shield, authored_bp, has_skin_fn):
    """Give worn armor a dismember skin; the updated has_skin.

    A non-skinned piece is rigid-skinned after baking the ROOT transform into
    the verts; the geometry node's own transform is deliberately NOT baked,
    because skin_retarget composes it with the Skyrim bone position.
    See: docs/commentary/asset_convert_armor.md#rigid-prn-skinning
    """
    if is_shield and is_morrowind(data):
        name_morrowind_shield(data)
    if not has_skin and not is_shield:
        fallback = BODY_PART_FALLBACK_PRN_BONE.get(authored_bp)
        for root in data.roots:
            if root is not None:
                bake_root_transform_into_verts(root)
                add_prn_skin(data, root, fallback_bone=fallback)
        has_skin = has_skin_fn(data)
    if has_skin:
        upgrade_skin_instances(data)
    return has_skin


def _offset_slot(data, single_slot, slot_for_offset, authored_allowed):
    """Which offset entry fits this NIF: the stated slot, or the vertex mass.

    See: docs/commentary/asset_convert_armor.md#armor-offset-slot
    """
    if single_slot:
        return slot_for_offset
    return dominant_body_part(data, allowed=authored_allowed)


def _apply_head_and_offsets(data, src_path, piece_type, prn_block_ids,
                            prn_head_ids):
    """Fit rigid head gear by measurement, then apply the fallback offsets.

    The two skulls differ in SHAPE, not by a factor, so head gear is fitted
    per vertex rather than scaled. The FK constants remain the fallback
    whenever the fit data is unavailable.
    See: docs/commentary/asset_convert_armor.md#head-gear-fit
    """
    skinned_head_ok = wrap_has_head(src_path)
    if not wrap_available(src_path) or (piece_type == 'helmet'
                                        and not skinned_head_ok):
        cfg = ARMOR_PIECE_OFFSETS.get(piece_type,
                                      ARMOR_PIECE_OFFSETS['default'])
        apply_armor_offset(data, cfg, exclude_block_ids=prn_block_ids)
    legacy = prn_block_ids - prn_head_ids
    if legacy:
        cfg_prn = ARMOR_PIECE_OFFSETS_PRN.get(
            piece_type, ARMOR_PIECE_OFFSETS_PRN['default'])
        apply_armor_offset(data, cfg_prn, only_block_ids=legacy)


def retarget_worn_armor(data, stats, src_path, weight, race, hair, has_skin,
                        authored_bp, authored_allowed, single_slot,
                        slot_for_offset, morrowind=False):
    """Retarget a worn piece onto the Skyrim skeleton; (slot, body splices).

    Bones are renamed only AFTER the skin transforms are correct, and the body
    skin is collected after that, when the verts sit in Skyrim space.
    `morrowind` (the source was Morrowind's NIF version) retargets from
    Morrowind's rig.
    See: docs/commentary/asset_convert_armor.md#head-gear-fit
    """
    slot_for_offset = _offset_slot(data, single_slot, slot_for_offset,
                                   authored_allowed)
    piece_type = _BP_TO_PIECE.get(slot_for_offset, 'default')

    prn_block_ids = set()
    retarget_skin_to_skyrim(data, src_path=src_path, prn_out=prn_block_ids,
                            weight=weight, authored_body_part=authored_bp,
                            authored_allowed=authored_allowed, race=race,
                            morrowind=morrowind)
    stats['bones_remapped'] += remap_bone_names(data)

    body_nibs = collect_skin_info(data, src_path=src_path)
    strip_body_skin_geometry(data)

    prn_head_ids = set()
    if prn_block_ids and not hair:
        prn_head_ids = fit_prn_head_blocks(data, prn_block_ids, src_path,
                                           race=race)
    if not hair:
        _apply_head_and_offsets(data, src_path, piece_type, prn_block_ids,
                                prn_head_ids)
    if piece_type == 'helmet' and (prn_head_ids or has_skin):
        stats['_head_gear'] = True
    return slot_for_offset, body_nibs


def add_bow_bend_rig(data, stats, string_masks):
    """Graft the vanilla 7-bone bend rig and its BGED onto a converted bow.

    Runs LAST: it needs the final NiTriShape geometry and a BSFadeNode root
    whose Prn is already remapped to WeaponBow.
    See: docs/commentary/asset_convert_armor.md#nif-weapon-prn-contract
    """
    for root in data.roots:
        if root is not None and get_prn_bone(root) == 'WeaponBow':
            stats['bow_rig_shapes'] = add_bow_rig(data, string_masks)
            return


def destripify_skin_partitions(data, stats):
    """Rewrite any skin partition still in STRIP format as triangles.

    Skyrim's renderer draws a skinned shape from the partition, not the
    NiTriShapeData, so a strip partition gives it no triangles at all and the
    shape renders as the red missing-geometry marker.
    See: docs/commentary/asset_convert_nif.md#strip-format-skin-partitions
    """
    for root in data.roots:
        if root is None:
            continue
        for block in list(root.tree()):
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None or skin.skin_partition is None:
                continue
            if not any(pb.num_strips > 0
                       for pb in skin.skin_partition.skin_partition_blocks):
                continue
            geom_name = bytes(block.name).rstrip(b'\x00').decode(
                'latin-1', errors='replace')
            regen_skin_partition(block, skin, geom_name)
            stats['skin_partitions_destripified'] = \
                stats.get('skin_partitions_destripified', 0) + 1


def prepare_armor_root(root):
    """Ready a worn-armor NiNode root, which stays a NiNode.

    Skyrim positions worn armor from the ARMA's biped slot, so a `Prn` naming
    an Oblivion bone only mis-attaches it and is stripped.
    """
    root.flags = NIF_FLAGS
    if getattr(root, 'num_effects', 0) > 0:
        root.num_effects = 0
        root.effects.update_size()
    if not hasattr(root, 'extra_data_list'):
        return
    keep = [ed for ed in root.extra_data_list
            if not (isinstance(ed, NifFormat.NiStringExtraData)
                    and bytes(ed.name).rstrip(b'\x00') == b'Prn')]
    if len(keep) < root.num_extra_data_list:
        root.num_extra_data_list = len(keep)
        root.extra_data_list.update_size()
        for i, ed in enumerate(keep):
            root.extra_data_list[i] = ed


# ---------------------------------------------------------------------------
# Inventory markers
# ---------------------------------------------------------------------------

def _inv_marker_of(root):
    """The root's existing BSInvMarker, or None."""
    for ed in getattr(root, 'extra_data_list', []) or []:
        if isinstance(ed, NifFormat.BSInvMarker):
            return ed
    return None


def finalise_inv_markers(data, stats, has_skin_fn):
    """Orient every inventory-visible mesh from its finished geometry.

    Weapons and shields already sit in Skyrim's normalized attachment frames,
    so their constant markers are exact and are left alone. Everything else is
    still in an arbitrary Oblivion modelling frame, where a fixed rotation
    shows a random side.
    See: docs/commentary/asset_convert_nif.md#inventory-marker-orientation
    """
    skinned = has_skin_fn(data)
    for root in data.roots:
        if root is None or type(root).__name__ != 'BSFadeNode':
            continue
        if get_prn_bone(root) in _EQUIPPED_PRN_VALUES:
            continue
        marker = _inv_marker_of(root)
        if marker is None and skinned:
            continue
        rot = compute_inv_rotation(root)
        if rot is None:
            continue
        if marker is None:
            marker = NifFormat.BSInvMarker()
            marker.name = b'INV'
            marker.zoom = 1.0
            root.num_extra_data_list += 1
            root.extra_data_list.update_size()
            root.extra_data_list[root.num_extra_data_list - 1] = marker
        marker.rotation_x, marker.rotation_y, marker.rotation_z = rot
        stats['inv_markers_computed'] = \
            stats.get('inv_markers_computed', 0) + 1
