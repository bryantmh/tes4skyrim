"""PyFFI-based Oblivion → Skyrim NIF converter.

Replaces mesh_convert/nif_converter.py for the asset_convert pipeline.
Uses PyFFI to read/write NIF files and handles all conversions in-place.
Source files are NEVER modified; converted output is written to dst_path only.

Supported:
  - NiTriStrips → NiTriShape
  - NiTexturingProperty → BSLightingShaderProperty + BSShaderTextureSet
  - NiNode root → BSFadeNode
  - Root rotation baking into children (non-skinned)
  - Inline tangents from NiBinaryExtraData
  - NiControllerManager string palette resolution
  - Havok collision conversion (bhkNiTriStripsShape→bhkCompressedMeshShape via cms_builder)
  - NiParticleSystem removal

Skip reason codes (printed in skip list at end of batch_convert):
  VER   — NIF version is unsupported (too old, unrecognised)
  SKY   — Already Skyrim version, copied as-is
  RD    — Read failed (corrupt/truncated/unknown blocks)
  WR    — Write failed (version-incompatible blocks like NiGeomMorpherController)

`apply_patches()` must run before NifFormat is imported: it fixes
time.clock and the nif.xml conditions PyFFI reads at import time.
`landscape_normals` owns the shared stand-in normal map's path.
"""

import collections as _collections
import json as _json
import io as _io
import logging as _logging
import os
import re
import shutil
import struct
from pathlib import Path

import numpy as np

from asset_convert import paths
from asset_convert.nif.geometry_sanitize import sanitize_geometry_data
from asset_convert.nif.nif_materials_morrowind import carry_havok_material
from asset_convert.nif.nif_converter_morrowind import (
    attach_morrowind_collision, build_skin_partitions, disable_specular,
    is_morrowind, run_morrowind_fixups, strip_collision_nodes)
from asset_convert.nif.tex_paths import rewrite_tex_path
from asset_convert.nif.shaders import (ALPHA_BLEND_ENABLED,
                                       ALPHA_DST_ONE, ALPHA_DST_SHIFT,
                                       APPLY_HILIGHT2,
                                       DEFAULT_DIFFUSE_TEXTURE,
                                       DEFAULT_GLOSSINESS,
                                       LIGHTING_EMISSIVE_ONLY, SPEC_STRENGTH,
                                       apply_fx_soft_effect, apply_glow,
                                       apply_parallax,
                                       attach_tex_transform_ctrls,
                                       collect_shader_inputs,
                                       collect_uv_ctrls, has_spec_mask,
                                       plan_flipbook_atlas,
                                       resolve_normal_for)
from asset_convert.character.skin_replacement import (apply_armor_offset,
                                                     collect_skin_info,
                                                     splice_body_geometry,
                                                     strip_body_skin_geometry)
from asset_convert.character.head_gear import (is_ground_model,
                                               strip_gnd_skin)
from asset_convert.character.prn_skin import get_prn_bone
from asset_convert.nif.nif_passes import (add_animobject_bged, wrap_root_transform,
                                          add_bsx_flags,
                                          collect_sequence_names,
                                          convert_sound_text_keys,
                                          fade_above_rig_root,
                                          wrap_geometry_root,
                                          fix_controller_flags,
                                          resolve_palette_strings,
                                          strip_empty_text_keys,
    zero_fallout_root_rotation)
from asset_convert.nif.morphs import (emulate_morphs,
                                      normalize_blend_interpolators)
from asset_convert.nif.sequences import (apply_rest_visibility,
                                         attach_seq_shader_controllers,
                                         autoplay_ambient_sequences,
                                         match_seq_shader_types,
                                         process_controller_manager)
from asset_convert.character.wearable_plan import (body_part_for_flags,
                                                   body_parts_for_flags,
                                                   mesh_is_female)
from asset_convert.character.wearable_plan_falloutnv import shield_flags
from asset_convert.havok.hkx_skeleton import BONE_RENAMES
from asset_convert.character import wearable_plan as wp
from asset_convert.collision.clutter_plan import latch_clutter_mass
from asset_convert.character.body_wrap import morph_converted_to_weight1
from asset_convert.havok.hkx_animobject import generate_animobject_project
from asset_convert.nif.gun_parts_falloutnv import add_gun_part_sequences
from asset_convert.nif.particles import (convert_particle_system,
                                        skyrimize_billboard,
                                        wrap_in_billboard)
from asset_convert.nif.nif_flags import NIF_FLAGS
from asset_convert.character.equipment_rig import (add_bow_bend_rig,
                                                   add_inv_marker,
                                                   convert_prn,
                                                   convert_root_collision,
                                                   destripify_skin_partitions,
                                                   finalise_inv_markers,
                                                   prepare_armor_root,
                                                   prepare_creature_rig,
                                                   prepare_worn_armor,
                                                   regen_creature_skins,
                                                   retarget_worn_armor,
                                                   rigid_skin_creature_parts)
from asset_convert.nif.geometry_shader import (process_geometry,
                                               sky_object_type_for)
from asset_convert.character.skyrim_overrides import (
    ARMOR_DEFAULT_BODY_PART,
    ARMOR_GEOMETRY_BODY_PARTS,
    ARMOR_GND_INV_MARKER_ROT_X,
    ARMOR_GND_INV_MARKER_ROT_Y,
    ARMOR_GND_INV_MARKER_ROT_Z,
    ARMOR_GND_INV_MARKER_ZOOM,
    ARMOR_PIECE_OFFSETS,
    ARMOR_PIECE_OFFSETS_PRN,
    OBLIVION_TO_SKYRIM_BONE_MAP,
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
from asset_convert.character.dismember_falloutnv import hide_dismember_caps
from asset_convert.nif.mesh_scan_emit import (record_scan_alias,
                                              record_scan_entry,
                                              record_scan_removal)
from asset_convert.collision.collision import (hoist_collision,
                                               remove_empty_collision_nodes)
from asset_convert.collision.collision_falloutnv import (
    is_fallout_source, latch_source, merge_static_parts)
from asset_convert.collision.collision_constraints import (enforce_ragdoll_tree,
                        scale_constraint_pivots, strip_marker_collision_bodies)
from asset_convert.nif.tri_reconstruct import (clear_match_groups, fix_missing_triangles,
                              UnreconstructibleGeometry)

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert.texture.texture_prune import texture_refs_in
from asset_convert.nif.nif_flames import convert_flame_nodes

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
    try:
        from pyffi.spells.nif.fix import SpellAddTangentSpace as _SpellAddTangentSpace
        from pyffi.spells.nif import NifToaster as _NifToaster
        _TANGENT_SPELL = True
    except ImportError:
        _TANGENT_SPELL = False
except ImportError:
    _PYFFI = False


# ---------------------------------------------------------------------------
# CONSTANTS — edit these to change conversion behaviour
# ---------------------------------------------------------------------------

OUTPUT_VERSION       = 0x14020007  # Skyrim SE NIF version
OUTPUT_USER_VERSION  = 12
OUTPUT_USER_VERSION_2 = 83


#: Convertible source versions; anything else is skipped, not copied.
_SUPPORTED_VERSIONS = {
    0x14000004,  # Gamebryo 20.0.0.4 - the primary Oblivion format
    0x14000005,  # Gamebryo 20.0.0.5
    0x14020007,  # Gamebryo 20.2.0.7 - FO3/FNV
    0x0a020000,  # Gamebryo 10.2.0.0
    0x0a01006a,  # Gamebryo 10.1.0.106
    0x0a010065,  # Gamebryo 10.1.0.101
    0x0a000100,  # NetImmerse 10.0.1.0
    0x0a000102,  # NetImmerse 10.0.1.2
    0x04000002,  # NetImmerse 4.0.0.2 - Morrowind
}

#: Already-Skyrim (version, user_version_2), copied out unchanged.
_SKYRIM_VERSIONS = {
    (0x14020007, 83),  # FO3/FNV share the version, differing only in uv2
}

#: Havok unit scale, Oblivion to Skyrim: bodies, mass centers, primitive dims.
_HAVOK_SCALE = 0.1

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# --- Furniture marker conversion ------------------------------------------
# The full algorithm and data-verified ref/heading/z relations live in
from asset_convert.nif.furniture_markers import (
    ENTRY_BEHIND as _ENTRY_BEHIND,
    ENTRY_FRONT as _ENTRY_FRONT,
    ENTRY_LEFT as _ENTRY_LEFT,
    ENTRY_RIGHT as _ENTRY_RIGHT,
    cluster_seats as _cluster_seats,
    drop_superseded_markers,
    extract_entries as _extract_furniture_entries,
    geometry_center_xy as _geometry_center_xy,
    origin_shift as _furniture_origin_shift,
)


def _convert_furniture_markers(markers, root):
    """Convert Oblivion BSFurnitureMarker blocks (entry points) into one
    Skyrim BSFurnitureMarkerNode (seat positions).

    Returns (frn, origin_shift) — origin_shift is the +z translation that
    re-origins the model to the vanilla floor-origin convention.  The
    engine anchors the seated actor to the REFR z (not the marker z), so
    the model must be wrapped in an inner NiNode translated by this amount
    and the importer lowers the REFRs to match (see furniture_markers.py).
    Returns (None, 0.0) if the markers contain no positions."""
    entries = _extract_furniture_entries(markers)
    if not entries:
        return None, 0.0
    shift = _furniture_origin_shift(entries)
    seats = _cluster_seats(entries, lambda: _geometry_center_xy(root))

    frn = NifFormat.BSFurnitureMarkerNode()
    frn.name = b'FRN'
    frn.num_positions = len(seats)
    frn.positions.update_size()
    for ci, seat in enumerate(seats):
        dst = frn.positions[ci]
        dst.offset.x = seat['x']
        dst.offset.y = seat['y']
        dst.offset.z = seat['z'] + shift  # re-origined coords (floor = 0)
        dst.heading = seat['heading']
        dst.animation_type = 2 if seat['sleep'] else 1
        ep = dst.entry_properties
        ep.front = 1 if seat['entry_flags'] & _ENTRY_FRONT else 0
        ep.behind = 1 if seat['entry_flags'] & _ENTRY_BEHIND else 0
        ep.right = 1 if seat['entry_flags'] & _ENTRY_RIGHT else 0
        ep.left = 1 if seat['entry_flags'] & _ENTRY_LEFT else 0
    return frn, shift


def _norm_tex_ref(raw):
    """Normalize a NIF texture path to a key relative to the textures root.

    'Textures\\tes4\\foo\\Bar.DDS' -> 'tes4/foo/bar.dds'.  Returns None for
    anything that isn't a texture (file_name also carries non-DDS paths).
    """
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    p = raw.strip().lower().replace('\\', '/').lstrip('/')
    if not p.endswith('.dds'):
        return None
    if p.startswith('textures/'):
        p = p[len('textures/'):]
    return p or None


def _harvest_textures(data, out):
    """Add every texture path the converted NIF references to the set *out*.

    Walks the finished blocks rather than the paths we rewrote, so it also picks
    up textures written by the particle/effect/flip-book branches and by any
    block we pass through untouched.
    """
    def add(raw):
        p = _norm_tex_ref(raw)
        if p:
            out.add(p)

    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            tex_set = getattr(block, 'texture_set', None)
            if tex_set is not None:
                for t in tex_set.textures:
                    add(t)
            add(getattr(block, 'source_texture', None))
            add(getattr(block, 'greyscale_texture', None))
            add(getattr(block, 'file_name', None))


def _harvest_texture_bytes(raw: bytes, out):
    """Scrape texture paths out of a NIF we copied through without parsing.

    Uses texture_prune's scanner rather than a local regex: the equivalent
    `[A-Za-z0-9_\\\\/ .()&+-]{3,200}?\\.dds` pattern opens with a lazy star, so
    it retried at every offset of every mesh (22.8x slower, measured).
    """
    for match in texture_refs_in(raw):
        p = _norm_tex_ref(match)
        if p:
            out.add(p)


def _has_skin(data):
    """Return True if any block in the NIF is a NiSkinInstance."""
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if isinstance(block, NifFormat.NiSkinInstance):
                return True
    return False


# ---------------------------------------------------------------------------
# Node-level conversion
# ---------------------------------------------------------------------------

def _prune_orphan_roots(data):
    """Drop entries from data.roots that are not scene-graph roots.

    Many Nehrim meshes were authored by tools that leave dangling blocks —
    NiTriShapeData, NiTriStripsData, NiBinaryExtraData, bhkCollisionObject,
    NiTexturingProperty — in the block list with nothing referencing them.
    PyFFI reports every unreferenced block as a root, so data.roots comes back
    as [NiNode, NiTriShapeData, ...].  Every pass here assumes a root is a
    scene node and reads root.controller / root.children, which raises
    AttributeError on those orphans (the castle\\*_far.nif and
    artilleryduell\\flamecannonballnew.nif failures).

    The orphans are unreachable from the real root, so they are dead weight:
    dropping them both fixes the crash and shrinks the output.  Keeps every
    NiAVObject root, and keeps a non-NiAVObject root only if it is the sole
    root (nothing to fall back to — let the later passes deal with it).

    Returns the number of roots removed.
    """
    roots = [r for r in data.roots if r is not None]
    if len(roots) < 2:
        return 0

    keep = [r for r in roots if isinstance(r, NifFormat.NiAVObject)]
    if not keep:
        return 0

    # An orphan is only safe to drop if nothing we keep still references it.
    reachable = set()
    for r in keep:
        for block in r.tree():
            reachable.add(id(block))
    keep_ids = set(id(r) for r in keep)
    keep += [r for r in roots
             if id(r) not in keep_ids and id(r) in reachable]

    removed = len(roots) - len(keep)
    if removed <= 0:
        return 0

    data.roots = keep
    return removed


# Equip/sheath nodes Skyrim looks up by hard-coded name, and where each one
# hangs on a vanilla weapon-using creature rig (Draugr skeleton.nif node order:
# WeaponAxe/Sword/Mace sit by the pelvis, WeaponBack/Bow/QUIVER after the left
# pauldron on the upper spine, WEAPON under the right hand, SHIELD under the
# left).
#
# Oblivion rigs have only three attachment points — Weapon, Torch, Quiver —
# which the BONE_RENAMES pass turns into WEAPON/SHIELD/QUIVER.  The per-type
# SHEATH nodes have no Oblivion counterpart at all, so the converted rig simply
# lacked them: a converted weapon carries Prn=WeaponMace (the sheathed node,
# which is what vanilla Skyrim weapon meshes use), the engine could not find a
# node by that name, and the mesh fell back to the actor root — the weapon
# visibly slid around at the creature's feet instead of sitting in its hand,
# and no draw animation could ever reparent it.
#
# 'anchor' is the node to hang it under, in preference order (the first that
# exists on this rig wins); 'source' is the node whose LOCAL transform is
# copied, so the new node lands somewhere sensible for a rig of any size
# rather than at a hardcoded human offset.
#
# Oblivion creatures do NOT sheathe: every one of the 41 armed creature folders
# ships equip/unequip clips whose text keys are `attach`/`detach` (the AnimObject
# mechanism) -- the weapon is created in the hand and destroyed, never parked on
# the body.  38 of those 41 rigs accordingly carry NO Quiver/Shield/BackWeapon
# node at all.  So the per-type SHEATH nodes below exist only to give the ENGINE
# a node of the name it looks up; nothing is ever displayed at them, and the
# creature's own rig is the authority on what it needs.
#
# An earlier pass synthesized "proportionate" offsets for them from vanilla
# Skyrim ratios.  That was wrong on two counts: the placements disagreed with
# vanilla anyway (axe/mace hang on the RIGHT hip in Skyrim, the guessed offset
# put them on the left), and no Oblivion creature has anything to place there.
# The node is created at the anchor's origin, which is what a node nothing
# renders at should be.
_CREATURE_EQUIP_NODES = (
    # name,           anchors (first match wins),               source
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


def _add_creature_equip_nodes(data):
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
            # Copy a sibling attachment point's local transform where we have
            # one; otherwise sit at the anchor's origin.  Either way the node
            # is scaled to THIS creature, not to a human.
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


def _strip_creature_bone_controllers(data):
    """Remove Oblivion-runtime controllers from creature NIF node chains.

    Oblivion creature skeletons carry an active (flags=12) but DATALESS
    NiTransformController on every bone plus a bhkBlendController on every
    ragdoll bone and a NiBSBoneLODController on Bip01 — all driven by
    Oblivion's engine at runtime.  Vanilla Skyrim creature skeletons ship
    NONE of these (bhkBlendController: 0 of all vanilla actor meshes; their
    only NiTransformControllers have a real interpolator+data — e.g. the
    dog's jaw/tongue idle).  Skyrim drives bones from the behavior graph, so
    these leftovers are at best dead weight and at worst engine hazards
    (an active controller with a null interpolator on every bone).

    Keeps NiTransformControllers that have an interpolator (real embedded
    animation).  Returns the number of controllers removed.
    """
    removed = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not hasattr(block, 'controller'):
                continue
            prev = None
            ctrl = getattr(block, 'controller', None)
            while ctrl is not None:
                nxt = getattr(ctrl, 'next_controller', None)
                dead = isinstance(ctrl, (NifFormat.bhkBlendController,
                                         NifFormat.NiBSBoneLODController)) \
                    or (isinstance(ctrl, NifFormat.NiTransformController)
                        and getattr(ctrl, 'interpolator', None) is None)
                if dead:
                    if prev is None:
                        block.controller = nxt
                    else:
                        prev.next_controller = nxt
                    removed += 1
                else:
                    prev = ctrl
                ctrl = nxt
    return removed


#: PyFFI defaults endian_type to 0 (BIG); Skyrim NIFs are little-endian.
ENDIAN_LITTLE = 1


#: Node-name prefixes Oblivion uses for geometry that must not ship.
_STRIPPED_NODE_PREFIXES = (b'SecretBigger', b'Secret Bigger', b'EditorMarker')


def _is_stripped_node(node):
    """Whether this node is editor-only or a load-distance hack.

    SecretBigger* are tiny triangles parked far below the origin to inflate
    the bounding sphere; EditorMarker* are hidden in Oblivion by a flag our
    conversion overwrites.  Both render as stray geometry in Skyrim.
    See: docs/commentary/asset_convert_nif.md#nodes-stripped-by-name
    """
    name = getattr(node, 'name', b'') or b''
    if not name:
        return False
    return any(name.startswith(p) for p in _STRIPPED_NODE_PREFIXES)


def _walk_geometry(node, fix_textures, stats):
    """Convert one shape, or None when it has no usable topology."""
    try:
        ts = process_geometry(node, fix_textures, stats,
                              sky_type=(stats or {}).get('_sky_type'),
                              norm_tex_ref=_norm_tex_ref)
    except UnreconstructibleGeometry as e:
        name = (node.name.decode('latin-1', 'replace')
                if isinstance(node.name, bytes) else str(node.name))
        print(f"  [warn] dropping triangle-less shape '{name}': {e}")
        stats['shapes_dropped'] = stats.get('shapes_dropped', 0) + 1
        return None
    if (isinstance(node, NifFormat.NiTriStrips)
            and not isinstance(ts, NifFormat.NiTriStrips)):
        stats['strips_fixed'] += 1
    stats['properties_converted'] += 1
    if ts is not node:
        stats.setdefault('_block_map', {})[id(node)] = ts
    return ts


def _string_palette(node):
    """The first NiStringPalette in the subtree, or None."""
    for block in node.tree():
        if isinstance(block, NifFormat.NiStringPalette):
            return block.palette
    return None


def _compact_children(node):
    """Drop the None slots stripped nodes left behind.

    pyffi writes a None ref as -1, but a non-zero num_children with null slots
    can confuse Skyrim.
    """
    keep = [c for c in node.children if c is not None]
    if len(keep) >= node.num_children:
        return
    node.num_children = len(keep)
    node.children.update_size()
    for i, child in enumerate(keep):
        node.children[i] = child


def _walk_ninode(node, fix_textures, stats):
    """Convert a NiNode in place and recurse into its children."""
    node.flags = NIF_FLAGS
    if getattr(node, 'num_effects', 0) > 0:
        node.num_effects = 0
        node.effects.update_size()

    if isinstance(getattr(node, 'controller', None),
                  NifFormat.NiControllerManager):
        process_controller_manager(node, _string_palette(node))

    for i in range(len(node.children)):
        result = walk_node(node, node.children[i], fix_textures, stats)
        if isinstance(result, NifFormat.NiBillboardNode):
            result = skyrimize_billboard(result)
        node.children[i] = result
    _compact_children(node)


def walk_node(parent, node, fix_textures, stats):
    """Convert a node and its children; the node that takes the parent's slot.

    None means the node is dropped: an editor marker, a load-distance hack, a
    dynamic effect, or a shape with no reconstructible topology.
    See: docs/commentary/asset_convert_nif.md#nodes-stripped-by-name
    """
    if node is None or _is_stripped_node(node):
        return None

    if isinstance(node, NifFormat.NiParticleSystem):
        convert_particle_system(node, fix_textures)
        node.flags = NIF_FLAGS
        return node

    if isinstance(node, NifFormat.NiDynamicEffect):
        stats['dynamic_effects_stripped'] = \
            stats.get('dynamic_effects_stripped', 0) + 1
        return None

    if isinstance(node, (NifFormat.NiTriStrips, NifFormat.NiTriShape)):
        return _walk_geometry(node, fix_textures, stats)

    if isinstance(node, NifFormat.NiNode):
        _walk_ninode(node, fix_textures, stats)
    return node


def _run_animation_passes(root, stats):
    """The sequence passes that must follow the geometry walk, in order.

    Order is the contract: morph emulation clones CONVERTED shapes, so it
    follows the walk and precedes rest visibility and sequence-name
    collection; the autoplay split precedes collect_sequence_names so the
    behaviour graph is built from the final names; shader controllers attach
    after the type match; interpolator normalizing runs last.
    See: docs/commentary/asset_convert_nif.md#post-walk-animation-passes
    """
    match_seq_shader_types(root)
    emulate_morphs(root, stats)
    autoplay_ambient_sequences(root, stats)
    apply_rest_visibility(root, stats)
    attach_seq_shader_controllers(root, stats)
    normalize_blend_interpolators(root, stats)


def _demote_billboard_root(root, bb_mode):
    """A plain NiNode carrying the billboard root's children and transform.

    The rotation is IDENTITY, not the billboard's: a NiBillboardNode discards
    its own rotation at runtime, so copying it onto the replacement revives a
    value the engine never used and skews the whole subtree.  Direct geometry
    children are re-wrapped in child billboards so the quads still face the
    camera.
    See: docs/commentary/asset_convert_nif.md#billboard-roots
    """
    plain = NifFormat.NiNode()
    plain.name = root.name
    plain.flags = NIF_FLAGS
    plain.translation = root.translation
    plain.rotation.set_identity()
    plain.scale = root.scale
    plain.num_children = root.num_children
    plain.children.update_size()
    for j, c in enumerate(root.children):
        plain.children[j] = c
    plain.num_extra_data_list = root.num_extra_data_list
    plain.extra_data_list.update_size()
    for j, ed in enumerate(root.extra_data_list):
        plain.extra_data_list[j] = ed
    if root.controller is not None:
        plain.controller = root.controller
    for j in range(len(plain.children)):
        c = plain.children[j]
        if isinstance(c, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            plain.children[j] = wrap_in_billboard(c, bb_mode)
    return plain


def _normalize_billboard_root(data, i, root):
    """Demote a billboard root over particles, else wrap it; the new root.

    A NiBillboardNode re-orients its ENTIRE subtree every frame, which
    scrambles world-space particle emission -- the invisible flames.
    See: docs/commentary/asset_convert_nif.md#billboard-roots
    """
    if not isinstance(root, NifFormat.NiBillboardNode):
        return root
    has_psys = any(isinstance(b, NifFormat.NiParticleSystem)
                   for b in root.tree())
    if has_psys:
        bb_mode = int(getattr(root, 'billboard_mode', 1)) or 1
        new_root = _demote_billboard_root(root, bb_mode)
    else:
        new_root = NifFormat.NiNode()
        new_root.flags = NIF_FLAGS
        new_root.num_children = 1
        new_root.children.update_size()
        new_root.children[0] = root
    data.roots[i] = new_root
    return new_root


def _repoint_root_refs(root, old_root):
    """Move every back-reference from the replaced root onto the new one.

    old_root leaves data.roots, so pyffi writes any surviving link to it as
    null and Skyrim null-derefs on load.  Three kinds of link point back:
    controller targets (including a NiMultiTargetTransformController's
    extra_targets array), NiDefaultAVObjectPalette entries, and a skinned
    shape's NiSkinInstance.skeleton_root.
    See: docs/commentary/asset_convert_nif.md#dangling-root-back-references
    """
    ctrl = root.controller
    while ctrl is not None:
        if getattr(ctrl, 'target', None) is old_root:
            ctrl.target = root
        for k in range(len(getattr(ctrl, 'extra_targets', ()) or ())):
            if ctrl.extra_targets[k] is old_root:
                ctrl.extra_targets[k] = root
        ctrl = getattr(ctrl, 'next_controller', None)

    mgr = root.controller
    palette = getattr(mgr, 'object_palette', None) if mgr is not None else None
    if palette is not None and hasattr(palette, 'num_objs'):
        for entry in palette.objs:
            if entry.av_object is old_root:
                entry.av_object = root

    for blk in root.tree():
        si = getattr(blk, 'skin_instance', None)
        if si is not None and si.skeleton_root is old_root:
            si.skeleton_root = root


def _walk_root_children(root, fix_textures, stats):
    """Convert every child of the root, then compact the stripped slots."""
    if not hasattr(root, 'children'):
        return
    for j in range(len(root.children)):
        res = walk_node(root, root.children[j], fix_textures, stats)
        if isinstance(res, NifFormat.NiBillboardNode):
            res = skyrimize_billboard(res)
        root.children[j] = res
    _compact_children(root)


def _remap_replaced_blocks(root, block_map):
    """Re-point second links at the shapes that replaced their strips.

    A NiDefaultAVObjectPalette entry and a NiPSysMeshEmitter's emitter_meshes
    both reference geometry OUTSIDE the children arrays the walk rewrites, so
    each still names the orphaned NiTriStrips.  pyffi re-serialises that block
    because it is still reachable, leaving raw Oblivion strips in a Skyrim
    file, and the engine fails the whole NIF.
    See: docs/commentary/asset_convert_nif.md#second-links-to-replaced-geometry
    """
    if not block_map:
        return
    mgr = root.controller
    palette = getattr(mgr, 'object_palette', None) if mgr is not None else None
    if palette is not None and hasattr(palette, 'num_objs'):
        for entry in palette.objs:
            replacement = block_map.get(id(entry.av_object))
            if replacement is not None:
                entry.av_object = replacement

    for block in root.tree():
        if not isinstance(block, NifFormat.NiPSysMeshEmitter):
            continue
        for mi in range(len(block.emitter_meshes)):
            replacement = block_map.get(id(block.emitter_meshes[mi]))
            if replacement is not None:
                block.emitter_meshes[mi] = replacement


def _hide_emitter_sources(root, stats):
    """Hide the shapes that only say where particles spawn.

    Oblivion hides them with NiMaterialProperty.alpha 0; Skyrim has no such
    property and the conversion forces every node visible, so they shipped as
    solid untextured boxes over the effect.
    See: docs/commentary/asset_convert_nif.md#helper-geometry-must-not-draw
    """
    for block in root.tree():
        if not isinstance(block, NifFormat.NiPSysMeshEmitter):
            continue
        for mesh in block.emitter_meshes:
            if mesh is None:
                continue
            mesh.flags = int(mesh.flags) | 0x0001
            for pi in range(len(mesh.bs_properties)):
                mesh.bs_properties[pi] = None
            stats['emitter_meshes_hidden'] = \
                stats.get('emitter_meshes_hidden', 0) + 1


def _hide_uvless_lit_shapes(root, stats):
    """Drop the shader and hide any lit shape with no UVs.

    A BSLightingShaderProperty ALWAYS samples a diffuse texcoord, so UV-less
    geometry makes it read past the vertex buffer.  Genuine geometry always
    has UVs, so this can only catch helper volumes.
    See: docs/commentary/asset_convert_nif.md#helper-geometry-must-not-draw
    """
    for block in root.tree():
        if not isinstance(block, NifFormat.NiTriBasedGeom):
            continue
        geom_data = getattr(block, 'data', None)
        if geom_data is None or int(getattr(geom_data, 'num_uv_sets', 0) or 0):
            continue
        props = getattr(block, 'bs_properties', None)
        if props is None:
            continue
        lit = [pi for pi, p in enumerate(props)
               if isinstance(p, NifFormat.BSLightingShaderProperty)]
        if not lit:
            continue
        for pi in lit:
            props[pi] = None
        block.flags = int(block.flags) | 0x0001
        stats['uvless_lit_shapes_hidden'] = \
            stats.get('uvless_lit_shapes_hidden', 0) + 1


def _hide_helper_geometry(root, stats):
    """Hide the Oblivion helper volumes that must never be drawn."""
    _hide_emitter_sources(root, stats)
    _hide_uvless_lit_shapes(root, stats)


def _hoist_root_collision(root, wrapped, root_is_animated, has_constraints,
                          creature):
    """Move a child's collision onto the root, where Skyrim wants it.

    Skipped when the root was wrapped (hoisting from under the wrapper would
    have to compose the WRAPPER's transform too, and the wrap path already
    absorbs it), for animated objects (the keyframed body must follow the
    animated child), for constrained NIFs (the spatial relationship is the
    constraint), and for creatures (ragdoll collision lives on the bones).
    """
    if wrapped or root_is_animated or has_constraints or creature:
        return
    if not hasattr(root, 'collision_object') or root.collision_object is not None:
        return
    if (is_fallout_source() and merge_static_parts(root)) or hoist_collision(root):
        remove_empty_collision_nodes(root)


def _run_source_fixups(data, stats=None):
    """Repair the source tree before the version upgrade changes how it reads.

    See: docs/commentary/asset_convert_nif.md#pre-upgrade-source-fixups
    """
    _prune_orphan_roots(data)
    resolve_palette_strings(data)
    convert_sound_text_keys(data)
    fix_controller_flags(data)
    sanitize_geometry_data(data)
    if is_morrowind(data):
        run_morrowind_fixups(data, stats)


def _classify_wearable(src_path, nif_basename, worn, biped_flags):
    """What the plugin says this mesh IS: armor, shield, and which slots.

    The wearing record's biped flags are AUTHORED data and always beat the
    filename or the folder.  Folder alone is never enough -- Nehrim files its
    armor under eyren/, spinat/, nehrim/ and skeletonk/, and would lose the
    dismember skin, the NiNode root and the retarget on all 88 of them.
    Bit 13 of BMDT is Shield, and it catches what a filename check misses:
    'towersheild.nif' is misspelled.
    See: docs/commentary/asset_convert_armor.md#armor-offset-slot
    """
    lowered = src_path.lower().replace('\\', '/')
    in_armor_dir = worn or 'armor' in lowered or 'clothes' in lowered
    is_shield = (shield_flags(biped_flags) if biped_flags
                 else 'shield' in nif_basename)
    authored_bp = body_part_for_flags(biped_flags) if biped_flags else None
    allowed = body_parts_for_flags(biped_flags) if biped_flags else None
    single_slot = allowed is not None and len(allowed) == 1
    return {
        'in_armor_dir': in_armor_dir,
        'is_shield': is_shield,
        'authored_bp': authored_bp,
        'authored_allowed': allowed,
        'single_slot': single_slot,
        'slot_for_offset': authored_bp if single_slot else None,
    }


def _capture_bow_masks(data, nif_basename, is_gnd, in_armor_dir):
    """(is a bow, string vertex masks) read before the morpher is stripped.

    Detection mirrors equipment_rig: Prn='BackWeapon' plus 'bow' in the filename.
    """
    if 'bow' not in nif_basename or is_gnd or in_armor_dir:
        return False, {}
    for root in data.roots:
        if root is not None and (get_prn_bone(root)
                                 or wp.mesh_weapon_prn()) == 'BackWeapon':
            from asset_convert.character.bow_rig import capture_string_masks
            return True, capture_string_masks(data)
    return False, {}


def _copy_root_frame(fade, root):
    """Copy the root's transform, children, controller and collision across.

    The Morrowind havok material rides along: it is sampled pre-upgrade onto
    the OLD root, so without this collision falls back to stone.
    See: docs/commentary/asset_convert_nif.md#morrowind-surface-materials
    """
    fade.name = root.name
    fade.flags = NIF_FLAGS
    fade.translation = root.translation
    fade.rotation = root.rotation
    fade.scale = root.scale
    carry_havok_material(root, fade)
    if hasattr(root, 'collision_object'):
        fade.collision_object = root.collision_object
        if fade.collision_object is not None:
            fade.collision_object.target = fade
    fade.num_children = root.num_children
    fade.children.update_size()
    for j, child in enumerate(root.children):
        fade.children[j] = child
    if root.controller is not None:
        fade.controller = root.controller


def _append_extra(node, block):
    """Append one extra-data block to `node`."""
    node.num_extra_data_list += 1
    node.extra_data_list.update_size()
    node.extra_data_list[node.num_extra_data_list - 1] = block


def _carry_bsbound(fade, root, stats):
    """Carry the actor bounding box across, if the source has one.

    The engine uses BSBound as the actor's physical bounds, so a creature
    skeleton without one has nothing for the ragdoll handoff to land on.  Its
    values are NIF object space, not Havok space, so they copy verbatim.
    See: docs/commentary/asset_convert_nif.md#dangling-root-back-references
    """
    for ed in root.extra_data_list:
        if isinstance(ed, NifFormat.BSBound):
            _append_extra(fade, ed)
            stats['bsbound_kept'] = stats.get('bsbound_kept', 0) + 1
            return


def _carry_furniture_markers(fade, root, stats):
    """Convert Oblivion floor entry points into Skyrim seat positions."""
    markers = [ed for ed in root.extra_data_list
               if isinstance(ed, NifFormat.BSFurnitureMarker)
               and not isinstance(ed, NifFormat.BSFurnitureMarkerNode)]
    if not markers:
        return
    frn, furn_shift = _convert_furniture_markers(markers, root)
    if frn is None:
        return
    _append_extra(fade, frn)
    stats['furniture_markers'] = stats.get('furniture_markers', 0) + 1
    stats['_furn_origin_shift'] = furn_shift


def _to_fade_node(data, i, root, stats, src_path, wants_gnd_marker):
    """Replace a NiNode root with a BSFadeNode; the new root.

    Extra data is carried SELECTIVELY -- a bulk copy breaks animated objects.
    See: docs/commentary/asset_convert_nif.md#dangling-root-back-references
    """
    old_root = root
    if bytes(root.name).rstrip(b'\x00') == b'NPC Root [Root]':
        return fade_above_rig_root(data, i, root, stats)
    fade = NifFormat.BSFadeNode()
    _copy_root_frame(fade, root)
    if hasattr(root, 'extra_data_list'):
        _carry_bsbound(fade, root, stats)
        _carry_furniture_markers(fade, root, stats)
        convert_prn(root, fade, src_path)

    data.roots[i] = fade
    stats['root_converted'] += 1
    if wants_gnd_marker:
        add_inv_marker(fade, ARMOR_GND_INV_MARKER_ROT_X,
                        ARMOR_GND_INV_MARKER_ROT_Y,
                        ARMOR_GND_INV_MARKER_ROT_Z,
                        ARMOR_GND_INV_MARKER_ZOOM)
    _repoint_root_refs(fade, old_root)
    return fade


def _normalize_fade_root(root, stats, src_path):
    """Apply the root-normalization passes to an already-BSFadeNode root.

    The NiNode->BSFadeNode swap carries these across; a root that is already
    a BSFadeNode never reaches it.
    See: docs/commentary/asset_convert_nif.md#already-a-bsfadenode
    """
    if not hasattr(root, 'extra_data_list'):
        return
    _carry_furniture_markers(root, root, stats)
    drop_superseded_markers(root)
    convert_prn(root, root, src_path)


def _convert_one_root(data, i, root, stats, fix_textures, src_path, creature,
                      nif_basename, has_skin, is_worn_armor, wants_gnd_marker):
    """Convert one root in place.

    Root normalization, the NiNode->BSFadeNode swap (or the worn-armor
    equivalent), the tree walk, the animation passes and the collision work,
    in the order each depends on the last.
    """
    root = wrap_geometry_root(data, i, root, stats)
    root = _normalize_billboard_root(data, i, root)
    zero_fallout_root_rotation(root)

    is_sky = stats.get('_sky_type') is not None
    if type(root).__name__ == 'NiNode' and not is_worn_armor and not is_sky:
        root = _to_fade_node(data, i, root, stats, src_path, wants_gnd_marker)
    elif type(root).__name__ == 'NiNode' and is_worn_armor:
        prepare_armor_root(root)
    elif not is_worn_armor and not is_sky:
        _normalize_fade_root(root, stats, src_path)

    if isinstance(getattr(root, 'controller', None),
                  NifFormat.NiControllerManager):
        process_controller_manager(root, None)

    furn_shift = stats.pop('_furn_origin_shift', 0.0)
    wrapped = wrap_root_transform(root, has_skin, furn_shift)
    if wrapped:
        stats['root_rotation_baked'] += 1

    _walk_root_children(root, fix_textures, stats)
    _remap_replaced_blocks(root, stats.get('_block_map', {}))
    _run_animation_passes(root, stats)
    _hide_helper_geometry(root, stats)

    root_is_animated = isinstance(getattr(root, 'controller', None),
                                  NifFormat.NiControllerManager)
    has_constraints = any(isinstance(b, NifFormat.bhkConstraint)
                          for b in data.blocks)
    _hoist_root_collision(root, wrapped, root_is_animated, has_constraints,
                          creature)
    convert_root_collision(data, root, creature, nif_basename,
                            has_constraints)


def _upgrade_version(data, stats=None) -> bool:
    """Stamp the Skyrim version, then run the fixups that need it.

    Returns whether the source was a Morrowind mesh. A Morrowind skin
    partition is built HERE, not with the other source fixups: the 4.0.0.2
    schema has no `skin_partition` ref, so one built earlier is unreachable
    and the writer drops it.
    See: docs/commentary/asset_convert_nif.md#morrowind-skin-partitions
    """
    was_morrowind = is_morrowind(data)
    data.version = OUTPUT_VERSION
    data.user_version = OUTPUT_USER_VERSION
    data.user_version_2 = OUTPUT_USER_VERSION_2
    data.header.endian_type = ENDIAN_LITTLE
    if was_morrowind:
        build_skin_partitions(data, stats)
    return was_morrowind


def _prepare_rig(data, creature, is_gnd, in_armor_dir, is_shield,
                 authored_bp) -> bool:
    """Ready the skin and rig before the version upgrade; is the mesh skinned?

    A _gnd model's cloth-physics bones are stripped first.
    See: docs/commentary/asset_convert_armor.md#nif-worn-armor-conversion
    """
    has_skin = _has_skin(data)
    if is_gnd and has_skin:
        strip_gnd_skin(data)
        has_skin = False
    if creature:
        prepare_creature_rig(data, _strip_creature_bone_controllers,
                             _add_creature_equip_nodes)
    if creature and not has_skin:
        rigid_skin_creature_parts(data)
        has_skin = _has_skin(data)
    if not creature and not is_gnd and in_armor_dir:
        has_skin = prepare_worn_armor(data, has_skin, is_shield,
                                      authored_bp, _has_skin)
    return has_skin


def _convert_roots(data, stats, fix_textures, src_path, creature,
                   nif_basename, has_skin, is_worn_armor, is_gnd_armor,
                   was_morrowind) -> None:
    """Convert every root, then apply the Morrowind collision and shader rules.

    See: docs/commentary/asset_convert_nif.md#morrowind-collision
    """
    for i, root in enumerate(data.roots):
        if root is None:
            continue
        _convert_one_root(data, i, root, stats, fix_textures, src_path,
                          creature, nif_basename, has_skin, is_worn_armor,
                          is_gnd_armor)
        if was_morrowind:
            attach_morrowind_collision(data.roots[i], stats)
    if was_morrowind:
        strip_collision_nodes(data, stats)
        disable_specular(data, stats)


def _convert_nif(data, fix_textures=True, src_path='', weight=0,
                 creature=False, worn=False, parallax=False, biped_flags=0,
                 tex_fallback=(), hair=False, race=None):
    """Convert a PyFFI NifFormat.Data in-place to Skyrim format; stats dict.

    worn, parallax and creature each select a different path.
    See: docs/commentary/asset_convert_nif.md#convert-nif-path-flags
    Worn gear keeps a NiNode root; shields and _gnd take BSFadeNode.
    See: docs/commentary/asset_convert_armor.md#nif-worn-armor-conversion
    Body geometry is spliced after the retarget.
    See: docs/commentary/asset_convert_armor.md#body-splice-fill-partition
    """
    stats = {
        'strips_fixed': 0,
        'properties_converted': 0,
        'root_converted': 0,
        'root_rotation_baked': 0,
        'tangents_injected': 0,
        'bones_remapped': 0,
        'textures_fixed': 0,
        '_src_path': str(src_path),
        '_tex_fallback': tex_fallback or (),
        '_parallax': bool(parallax),
        '_sky_type': sky_object_type_for(src_path),
    }

    latch_source(data)
    _run_source_fixups(data, stats)
    stats['gore_caps_hidden'] = hide_dismember_caps(data)

    nif_basename = os.path.basename(src_path).lower()
    _is_gnd = is_ground_model(nif_basename)
    _cls = _classify_wearable(src_path, nif_basename, worn, biped_flags)
    _in_armor_dir = _cls['in_armor_dir']
    _is_shield = _cls['is_shield']
    _authored_bp = _cls['authored_bp']
    _authored_allowed = _cls['authored_allowed']
    _single_slot = _cls['single_slot']
    _slot_for_offset = _cls['slot_for_offset']

    _is_bow_weapon, _bow_string_masks = _capture_bow_masks(
        data, nif_basename, _is_gnd, _in_armor_dir)
    has_skin = _prepare_rig(data, creature, _is_gnd, _in_armor_dir,
                            _is_shield, _authored_bp)
    _body_nibs_to_splice: dict = {}

    was_morrowind = _upgrade_version(data, stats)

    _is_creature_body = creature and has_skin
    _is_worn_armor = (not _is_gnd and _in_armor_dir and not _is_shield) \
        or _is_creature_body
    _convert_roots(data, stats, fix_textures, src_path, creature,
                   nif_basename, has_skin, _is_worn_armor,
                   _is_gnd and _in_armor_dir, was_morrowind)

    if creature and has_skin:
        regen_creature_skins(data, _authored_bp, _authored_allowed)

    if not creature and not _is_gnd and _in_armor_dir and has_skin:
        _slot_for_offset, _body_nibs_to_splice = retarget_worn_armor(
            data, stats, src_path, weight, race, hair, has_skin,
            _authored_bp, _authored_allowed, _single_slot, _slot_for_offset)

    if _body_nibs_to_splice:
        _fill_bp = (_authored_bp if _single_slot else _slot_for_offset) or 32
        splice_body_geometry(data, _body_nibs_to_splice, fill_body_part=_fill_bp)

    if _is_bow_weapon:
        add_bow_bend_rig(data, stats, _bow_string_masks)
    destripify_skin_partitions(data, stats)
    if not creature:
        finalise_inv_markers(data, stats, _has_skin)

    stats['tangents_injected'] = stats['properties_converted']

    return stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _read_source(src_path, dst_path, result):
    """Read a source NIF, or None with result['error'] / ['copied'] set.

    Already-Skyrim versions are copied to dst_path unchanged with their texture
    bytes harvested; unsupported versions and scene-less animation files
    (creatures/*/idleanims/*.nif hold only a NiControllerSequence) are skipped.
    """
    data = NifFormat.Data()
    try:
        with open(src_path, 'rb') as f:
            data.inspect(f)
    except Exception:
        result['error'] = 'RD'
        return None
    if (data.version, data.user_version_2) in _SKYRIM_VERSIONS:
        dst_dir = os.path.dirname(dst_path)
        if dst_dir:
            os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        with open(src_path, 'rb') as f:
            _harvest_texture_bytes(f.read(), result['textures'])
        result['copied'] = True
        return None
    if data.version not in _SUPPORTED_VERSIONS:
        result['error'] = 'VER'
        return None
    data = NifFormat.Data()
    try:
        with open(src_path, 'rb') as f:
            data.inspect(f)
            data.read(f)
    except Exception:
        result['error'] = 'RD'
        return None
    if not any(isinstance(r, NifFormat.NiAVObject) for r in data.roots):
        result['error'] = 'NOGEO'
        return None
    return data


def _authored_wear(src_path, src_meshes_dir, wearable_plan, creature, hair):
    """(worn, biped flags) the plugin states for this mesh; latches its gender.

    Hair carries biped bit 1, the slot a helmet-bearing record would, so it
    resolves body part 131 without guessing.  Asked before the conversion so
    the armor rules apply to gear filed outside meshes\armor and clothes.

    The clutter mass latches off the UNGATED plan: an item model is simulated
    whatever folder it sits in, and only a creature is never loose.
    """
    plan = (wearable_plan if src_meshes_dir is not None and not creature
            and not hair else None)
    wp.latch_variants(plan, src_path, src_meshes_dir)
    latch_clutter_mass(wearable_plan if not creature else None,
                       src_path, src_meshes_dir)
    if plan is None:
        return bool(hair), 0x02 if hair else 0
    return (wp.is_worn(plan, src_path, src_meshes_dir),
            wp.biped_flags_for(plan, src_path, src_meshes_dir))


def convert_nif(src_path, dst_path, *, fix_textures=True, remap_skeleton=None,
                src_meshes_dir=None, creature=False, wearable_plan=None,
                parallax=False, textures_only=False, tex_fallback=(),
                hair=False, race=None):
    """Convert a single Oblivion NIF to Skyrim format.

    Already-Skyrim versions are copied to dst_path unchanged.
    Unsupported/incompatible versions are skipped (not written to dst_path).
    Returns a result dict compatible with batch_convert's _update() expectations.

    src_meshes_dir: root of the source mesh tree (passed through by
    batch_convert), used to key a NIF against the wearable plan.

    wearable_plan: mapping from asset_convert.character.wearable_plan.build_plan, naming
    the _0/_1/plain variants each armor/clothing mesh is referenced as.  None
    disables weight-variant output entirely.

    parallax: carry Oblivion's parallax across (opt-in — see apply_parallax).

    textures_only: read and analyse every mesh, write NONE of them.  The
    height maps still get built, because the decision to build one needs the
    mesh's own APPLY_HILIGHT2 flag — see the mode's rationale in batch_convert.

    hair: this NIF is an Oblivion hair head part (asset_convert.character.hair_pipeline).
    Hair lives outside meshes\armor and no ARMO/CLOT record names it, so the
    wearable plan cannot mark it worn — but it is rigid Prn-attached geometry
    that needs exactly the same treatment as a helmet: a dismember skin bound
    to the head bone in slot 131.  Without this the mesh ships unskinned and
    also picks up a meaningless BSInvMarker (hair is never an inventory item).

    race: fit head gear to a BEAST race's skull instead of the shared human
    one (head_fit.BEAST_RACES).  Set only by the beast-variant pass below,
    which re-runs this conversion once per race; None is the normal path.
    """
    result = {
        'converted': False,
        'skipped': False,
        'copied': False,           # already-Skyrim, copied as-is
        'skip_reason': None,       # VER | RD | WR
        'error': None,
        'strips_fixed': False,
        'properties_converted': False,
        'root_converted': False,
        'root_rotation_baked': False,
        'version_upgraded': False,
        'textures': set(),         # texture paths this mesh references
        'overlay_diffuses': set(), # of those, the APPLY_HILIGHT2 overlays
    }

    if not _PYFFI:
        result['error'] = 'pyffi not installed'
        return result
    data = _read_source(src_path, dst_path, result)
    if data is None:
        return result
    _worn, _biped_flags = _authored_wear(src_path, src_meshes_dir, wearable_plan,
                                         creature, hair)

    stats = _convert_nif(data, fix_textures=fix_textures,
                         src_path=str(src_path), creature=creature,
                         worn=_worn, parallax=parallax,
                         biped_flags=_biped_flags,
                         tex_fallback=tex_fallback, hair=hair, race=race)

    _run_post_passes(data, stats, result, src_path, dst_path, textures_only)

    _harvest_textures(data, result['textures'])
    result['overlay_diffuses'] = stats.get('overlay_diffuses', set())
    if textures_only:
        return _finish_result(result, stats)

    buf = _io.BytesIO()
    try:
        data.write(buf)
    except Exception:
        result['error'] = 'WR'
        return result

    dst_dir = os.path.dirname(dst_path)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    with open(dst_path, 'wb') as f:
        f.write(buf.getvalue())
    record_scan_entry(data, _output_root(dst_path)[2])

    _write_weight_variants(data, buf, src_path, dst_path, src_meshes_dir,
                           wearable_plan, creature, race)
    if stats.get('_head_gear') and not creature and not hair and race is None:
        _write_beast_head_variants(
            src_path, dst_path,
            fix_textures=fix_textures, src_meshes_dir=src_meshes_dir,
            wearable_plan=wearable_plan, parallax=parallax)
    return _finish_result(result, stats)


def _output_root(dst_path):
    """(tree root, meshes root, model path) around `dst_path`'s meshes/.

    Only convert_nif knows where the output tree is, so the texture passes
    that write beside it resolve their roots here.  All three are None when
    the destination is not inside a meshes/ tree.
    """
    dstn = str(dst_path).replace('/', os.sep).replace('\\', os.sep)
    key = os.sep + 'meshes' + os.sep
    i = dstn.lower().rfind(key)
    if i < 0:
        return None, None, None
    return dstn[:i] + os.sep, dstn[:i + len(key)], dstn[i + len(key):]


def _build_flip_atlases(stats, dst_path):
    """Compose the frame-strip atlases process_geometry planned."""
    jobs = stats.pop('_flipbook_atlases', {})
    if not jobs:
        return
    out_root = _output_root(dst_path)[0]
    if out_root is None:
        return
    from asset_convert.nif import flipbook
    for job in jobs.values():
        out = out_root + job['atlas_rel'].replace('\\', os.sep)
        if os.path.isfile(out):
            continue
        try:
            flipbook.build_flip_atlas(job['files'], out)
        except Exception:
            pass


def _build_height_maps(stats, dst_path):
    """Write the BC4 height maps apply_parallax planned.

    Each map is written once: the file test skips the other meshes sharing
    that diffuse, and 2359 flagged shapes share just 163 textures.
    """
    jobs = stats.pop('_parallax_maps', {})
    if not jobs:
        return
    out_root = _output_root(dst_path)[0]
    if out_root is None:
        return
    from asset_convert.texture import parallax
    for job in jobs.values():
        out = out_root + job['height_rel'].replace('\\', os.sep)
        if not os.path.isfile(out):
            parallax.build_height_map(job['src'], out)


def _build_animobject_graph(data, stats, result, dst_path):
    """Give an animated object the behaviour graph PlayAnimation needs.

    Runs AFTER the conversion so stripped sequences cannot become dead states,
    and before the write so the BGED ships in the file. A gun's part
    sequences never earn one: TESRuntime starts them itself.
    See: docs/commentary/asset_convert_nif.md#animated-object-graphs
    """
    seq_names = collect_sequence_names(data)
    if not seq_names:
        return
    stripped = strip_empty_text_keys(data)
    if stripped:
        stats['empty_text_keys_stripped'] = stripped

    _, meshes_root, model_rel = _output_root(dst_path)
    if meshes_root is None:
        return
    try:
        bged = generate_animobject_project(meshes_root, model_rel, seq_names)
        if bged and add_animobject_bged(data, bged):
            result['animobject_graph'] = bged
            stats['animobject_sequences'] = len(seq_names)
    except Exception as e:
        result['animobject_error'] = str(e)


def _add_tangent_space(data):
    """Generate tangent space where a shape lacks it.

    Missing tangents light normal maps wrongly in Skyrim -- the "rainbow
    shaders" on architecture.

    verbose=0 stops the toaster lowering the shared 'pyffi' logger to INFO.
    See: docs/commentary/asset_convert_nif.md#pyffi-log-capture
    """
    if not _TANGENT_SPELL:
        return
    try:
        toaster = _NifToaster(options=dict(verbose=0))
        spell = _SpellAddTangentSpace(data=data, toaster=toaster)
        spell.recurse()
    except Exception:
        pass


def _run_post_passes(data, stats, result, src_path, dst_path, textures_only):
    """Everything that runs on the converted tree before it is written."""
    for root in data.roots:
        if root is not None:
            convert_flame_nodes(root, src_path, _convert_nif, stats)
    _build_flip_atlases(stats, dst_path)
    _build_height_maps(stats, dst_path)
    if not textures_only:
        parts = add_gun_part_sequences(data, src_path) if is_fallout_source() else []
        if parts:
            stats['gun_part_sequences'] = len(parts)
        _build_animobject_graph(data, stats, result, dst_path)
    _add_tangent_space(data)


def _write_weight_variants(data, buf, src_path, dst_path, src_meshes_dir,
                           wearable_plan, creature, race):
    """Write the _0/_1 weight-slider pair the plugin actually references.

    The _1 file is NEVER a second conversion: the engine lerps the pair
    per-vertex and that REQUIRES identical topology, so it is the finished
    weight-0 mesh post-morphed by the fitted body morph.
    See: docs/commentary/asset_convert_armor.md#weight-slider-variants
    """
    srcl = str(src_path).lower().replace('\\', '/')
    wearable = (not creature and race is None
                and not is_ground_model(srcl.rsplit('/', 1)[-1]))
    if not (wearable and wearable_plan is not None):
        return

    want = wp.variants_for(wearable_plan, src_path, src_meshes_dir)
    root, ext = os.path.splitext(str(dst_path))

    if not want & wp.BASE:
        try:
            os.remove(dst_path)
        except OSError:
            pass
        record_scan_removal(_output_root(dst_path)[2])
    if want & wp.W0:
        with open(root + '_0' + ext, 'wb') as f:
            f.write(buf.getvalue())
        record_scan_alias(_output_root(root + '_0' + ext)[2],
                          _output_root(dst_path)[2])
    if not want & wp.W1:
        return

    w1_bytes = None
    try:
        if morph_converted_to_weight1(data, mesh_is_female(src_path)):
            buf1 = _io.BytesIO()
            data.write(buf1)
            w1_bytes = buf1.getvalue()
    except Exception:
        w1_bytes = None
    with open(root + '_1' + ext, 'wb') as f:
        f.write(w1_bytes if w1_bytes is not None else buf.getvalue())
    if w1_bytes is None:
        record_scan_alias(_output_root(root + '_1' + ext)[2],
                          _output_root(dst_path)[2])
    else:
        record_scan_entry(data, _output_root(root + '_1' + ext)[2])


def _write_beast_head_variants(src_path, dst_path, *, fix_textures,
                               src_meshes_dir, wearable_plan, parallax):
    """Write the per-beast-race copies of a head-gear NIF.

    The whole conversion is RE-RUN per race from the source file rather than
    the finished mesh being re-fitted.  A hood is multi-bone SKINNED geometry
    (Bip01 Head + Neck + Clavicles), so its head fit happens inside the
    retarget wrap -- there is no later point at which the head verts can be
    displaced again without redoing the skin solve.  Re-reading also keeps
    each variant a FIRST fit through its race's field, never a second
    displacement stacked on the human result.

    A variant that fails for any reason is simply not written: the ARMA for it
    then points at a missing mesh, which the engine falls back from to the
    default armature -- the pre-existing behaviour, never worse than it.
    """
    from asset_convert.character import head_fit
    races = head_fit.beast_races_available(mesh_is_female(src_path))
    if not races:
        return

    root, ext = os.path.splitext(str(dst_path))
    for race in races:
        out = root + head_fit.beast_variant_suffix(race) + ext
        try:
            convert_nif(src_path, out, fix_textures=fix_textures,
                        src_meshes_dir=src_meshes_dir,
                        wearable_plan=wearable_plan, parallax=parallax,
                        race=race)
        except Exception:
            try:
                if os.path.isfile(out):
                    os.remove(out)
            except OSError:
                pass


def _finish_result(result, stats):
    """Roll `stats` up into the worker's result dict.

    Its own function because --textures-only returns before the mesh is ever
    written, and both exits owe batch_convert the same accounting.
    """
    result['converted'] = True
    result['strips_fixed'] = stats['strips_fixed'] > 0
    result['properties_converted'] = stats['properties_converted'] > 0
    result['root_converted'] = stats['root_converted'] > 0
    result['root_rotation_baked'] = stats['root_rotation_baked'] > 0
    result['version_upgraded'] = True
    result['bones_remapped'] = stats['bones_remapped'] > 0
    result['textures_fixed'] = stats['properties_converted'] > 0  # proxy: every property conversion rewrites textures
    # Parallax accounting.  Carried up per CATEGORY, because "skipped" on its
    # own sends the next person back to all 163 flagged textures with no lead —
    # over half of them legitimately have no height data to carry.
    # `spec_` rides in the same bucket: both are per-category counters merged
    # with Counter.update(), and both answer "why was this shape left alone".
    _px = {k: v for k, v in stats.items()
           if k.startswith('parallax_') or k.startswith('spec_')
           or k.startswith('glow_')}
    if _px:
        result['parallax'] = _px
    # Carried separately from the counters above: this one is a SET of texture
    # paths, and `parallax` is merged with Counter.update().
    _au = stats.get('_alpha_opacity_diffuse')
    if _au:
        result['alpha_opacity_diffuse'] = _au
    return result
