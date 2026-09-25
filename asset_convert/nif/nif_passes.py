"""Whole-file NIF passes: BSXFlags, palette strings, text keys, graph names.

Everything here reads or rewrites the FILE rather than one shape, and every one
of these runs once per NIF after the tree walk has finished.

See: docs/commentary/asset_convert_nif.md#bsxflags-value-selection
"""

import re

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.collision.collision import bake_node_transform_into_body
from asset_convert.collision.collision_falloutnv import is_fallout_source
from asset_convert.nif.nif_flags import (BSX_FLAGS_ANIMATED,
                                         BSX_FLAGS_CONSTRAINED,
                                         BSX_FLAGS_DYNAMIC,
                                         BSX_FLAGS_STATIC,
                                         NIF_FLAGS)
from asset_convert.nif.sequences import (AUTOLOOP_SEQUENCE, AUTOPLAY_SEQUENCE,
                                         SCRIPT_DRIVEN_SEQUENCES)
from tes5_import.record_types.sound import sndr_editor_id


# ---------------------------------------------------------------------------
# BSXFlags: what the engine is told the file contains
# ---------------------------------------------------------------------------


def _has_autoplay_sequence(root):
    """True if the tree carries an ambient (AutoPlay/AutoLoop) sequence.

    Those meshes get the animated-object BSX (0x8B, later masked to 0x0B when
    the BGED is attached) like a converted animated door.  Verified running in
    game with 0x0B (arena spectators, 2026-08-18); vanilla ships 0x01 for the
    same graph on collisionless meshes (29/63), so either would do -- this is
    the one that has been seen working.
    """
    for b in root.tree():
        if not isinstance(b, NifFormat.NiControllerSequence):
            continue
        raw = getattr(b, 'name', b'') or b''
        nm = raw.decode('latin-1') if isinstance(raw, bytes) else str(raw)
        if nm in (AUTOPLAY_SEQUENCE, AUTOLOOP_SEQUENCE):
            return True
    return False


def _tree_is_animated(root):
    """True if anything in the tree needs per-frame controller updates:
    a NiParticleSystem, or any block with a NiTimeController attached.

    Vanilla census (400 particle-bearing Skyrim meshes): 399/400 set BSXFlags
    bit 0 (Animated) — the exception is a trailer camera rig.  Without bit 0
    the engine never ticks the controllers, so particles never emit (the file
    is valid but the fire/effect is INVISIBLE)."""
    for b in root.tree():
        if isinstance(b, NifFormat.NiParticleSystem):
            return True
        if getattr(b, 'controller', None) is not None:
            return True
    return False


def _has_any_collision(node):
    """Whether any node in the subtree carries a collision object."""
    if node is None:
        return False
    if getattr(node, 'collision_object', None) is not None:
        return True
    return any(_has_any_collision(c) for c in getattr(node, 'children', ()))


def _has_dynamic_body(node):
    """Whether any rigid body in the subtree has mass > 0."""
    if node is None:
        return False
    co = getattr(node, 'collision_object', None)
    if co is not None:
        rb = getattr(co, 'body', None)
        if rb is not None and getattr(rb, 'mass', 0) > 0:
            return True
    return any(_has_dynamic_body(c) for c in getattr(node, 'children', ()))


def _bsx_value(root, has_constraints, tree_animated):
    """The BSXFlags value for this tree, or None when it needs none.

    Priority: constrained, then animated, then dynamic, then static, with the
    Animated bit OR'd in whenever controllers or particles must be ticked.
    See: docs/commentary/asset_convert_nif.md#bsxflags-value-selection
    """
    if not _has_any_collision(root):
        if not tree_animated:
            return None
        if _has_autoplay_sequence(root):
            return BSX_FLAGS_ANIMATED
        return 0x01

    root_is_animated = isinstance(getattr(root, 'controller', None),
                                  NifFormat.NiControllerManager)
    if has_constraints:
        value = BSX_FLAGS_CONSTRAINED
    elif root_is_animated:
        value = BSX_FLAGS_ANIMATED
    elif _has_dynamic_body(root):
        value = BSX_FLAGS_DYNAMIC
    else:
        value = BSX_FLAGS_STATIC
    return value | 0x01 if tree_animated else value


def _insert_bsx(root, value):
    """Add a BSXFlags block, immediately after BSInvMarker when present."""
    bsx = NifFormat.BSXFlags()
    bsx.name = b'BSX'
    bsx.integer_data = value
    root.num_extra_data_list += 1
    root.extra_data_list.update_size()

    insert_at = 0
    for i in range(root.num_extra_data_list - 1):
        if type(root.extra_data_list[i]).__name__ == 'BSInvMarker':
            insert_at = i + 1
            break
    for i in range(root.num_extra_data_list - 1, insert_at, -1):
        root.extra_data_list[i] = root.extra_data_list[i - 1]
    root.extra_data_list[insert_at] = bsx


def add_bsx_flags(root, has_constraints=False):
    """Give the root a BSXFlags block when it has collision or animation.

    An existing block is left in place, with only the Animated bit corrected.
    See: docs/commentary/asset_convert_nif.md#bsxflags-value-selection
    """
    tree_animated = _tree_is_animated(root)
    value = _bsx_value(root, has_constraints, tree_animated)
    if value is None:
        return

    for ed in getattr(root, 'extra_data_list', ()):
        if isinstance(ed, NifFormat.BSXFlags):
            if tree_animated:
                ed.integer_data |= 0x01
            return
    _insert_bsx(root, value)


# ---------------------------------------------------------------------------
# Main per-file conversion
# ---------------------------------------------------------------------------


#: Controlled-block string fields, paired with the offset that resolves each.
_CB_STRING_FIELDS = (
    ('node_name', 'node_name_offset'),
    ('controller_type', 'controller_type_offset'),
    ('variable_1', 'variable_1_offset'),
    ('variable_2', 'variable_2_offset'),
    ('property_type', 'property_type_offset'),
)


def _palette_string(raw, offset):
    """The NUL-terminated string at `offset` in a palette blob."""
    if offset < 0 or offset >= len(raw):
        return b''
    end = raw.find(b'\x00', offset)
    return raw[offset:end] if end >= 0 else raw[offset:]


def _sequence_palette(block):
    """A NiControllerSequence's raw string-palette blob, or b''."""
    sp = getattr(block, 'string_palette', None)
    pal = getattr(sp, 'palette', None) if sp is not None else None
    if pal is None:
        return b''
    return bytes(pal.palette) if hasattr(pal, 'palette') else b''


def _resolve_sequence_block(block, raw):
    """Write every controlled block's resolved strings into its string fields.

    property_type does not exist at every NIF version, hence the guard.
    """
    for cb in block.controlled_blocks:
        for field, off_field in _CB_STRING_FIELDS:
            offset = getattr(cb, off_field, -1)
            if offset < 0:
                continue
            try:
                setattr(cb, field, _palette_string(raw, offset))
            except AttributeError:
                pass


def resolve_palette_strings(data):
    """Resolve StringOffset fields in NiControllerSequence controlled_blocks.

    MUST run before the version upgrade: after it, PyFFI switches to
    direct-string mode and ignores the offsets, leaving every node_name empty,
    which Skyrim null-derefs on load.
    See: docs/commentary/asset_convert_nif.md#controlled-block-names
    """
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiControllerSequence):
                continue
            raw = _sequence_palette(block)
            if raw:
                _resolve_sequence_block(block, raw)


#: NiTimeController.flags bit 6. See: docs/commentary/asset_convert_animation.md#nif-animated-mesh-conversion
_CTLR_COMPUTE_SCALED_TIME = 0x40


_TES4_SOUND_KEY = re.compile(rb'^sound:\s*(\S+)\s*$', re.IGNORECASE)


def graph_sound_text_keys(data):
    """Rewrite `sound: <SOUN>` keys to `SoundPlay.<its SNDR>`; how many changed.

    ONLY for meshes that get a behaviour graph: a graph-driven sequence fires
    `SoundPlay.` keys, a graphless one (doors) fires `sound:` natively.
    See: docs/commentary/asset_convert_animation.md#sound-text-keys-are-native
    """
    changed = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiTextKeyExtraData):
                continue
            for key in block.text_keys:
                m = _TES4_SOUND_KEY.match(bytes(key.value or b''))
                if m:
                    sndr = sndr_editor_id(m.group(1).decode('latin-1'), 0)
                    key.value = b'SoundPlay.' + sndr.encode('latin-1')
                    changed += 1
    return changed


def strip_empty_text_keys(data):
    """Drop whitespace-only text keys -- ONLY for meshes that get a graph.

    An empty NiString loads as a NULL BSFixedString that the sequence
    generator strchr()s. Trailing whitespace is left alone: 107 vanilla
    dungeon keys carry it, so it is engine-legal and not ours to "fix".
    See: docs/commentary/asset_convert_nif.md#animated-object-graphs
    """
    removed = 0
    seen = set()
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiTextKeyExtraData):
                continue
            if id(block) in seen:
                continue
            seen.add(id(block))
            kept = [(k.time, k.value) for k in block.text_keys
                    if bytes(k.value or b'').strip()]
            if len(kept) == block.num_text_keys:
                continue
            removed += block.num_text_keys - len(kept)
            block.num_text_keys = len(kept)
            block.text_keys.update_size()
            for slot, (t, v) in zip(block.text_keys, kept):
                slot.time = t
                slot.value = v
    return removed


def fix_controller_flags(data):
    """Set "Compute Scaled Time" on every NiTimeController (Skyrim requirement).

    Oblivion sources always leave bit 0x40 clear; Skyrim needs it set or the
    controller's scaled time never advances and the animation never plays.
    Applies to the whole tree, so animated activators, doors, traps and levers
    are all covered — not just the record that surfaced the bug.
    """
    fixed = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiTimeController):
                continue
            flags = getattr(block, 'flags', None)
            if flags is None or (flags & _CTLR_COMPUTE_SCALED_TIME):
                continue
            block.flags = flags | _CTLR_COMPUTE_SCALED_TIME
            fixed += 1
    return fixed


def collect_sequence_names(data):
    """NiControllerSequence names a script can reach via PlayAnimation().

    These become both the behaviour-graph state names and the events that
    select them.  Order is the manager's own, deduplicated, so the generated
    graph is byte-reproducible.  Empty when the mesh has no controller
    manager: a static mesh must not get a BGED.
    See: docs/commentary/asset_convert_nif.md#which-sequences-earn-a-graph
    """
    names = []
    seen = set()
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiControllerManager):
                continue
            for seq in block.controller_sequences:
                if seq is None:
                    continue
                raw = getattr(seq, 'name', b'') or b''
                name = raw.decode('latin-1') if isinstance(raw, bytes) else str(raw)
                if not name or name in seen or not seq.num_controlled_blocks:
                    continue
                if (name not in (AUTOPLAY_SEQUENCE, AUTOLOOP_SEQUENCE) and
                        name.lower() not in SCRIPT_DRIVEN_SEQUENCES):
                    continue
                seen.add(name)
                names.append(name)
    return names


def add_animobject_bged(data, graph_file):
    """Point the root at the generated hkx project + mark the tree Animated.

    Mirrors the vanilla animated-object contract (and `bow_rig._add_bged`):
    without the BGED there is no animation graph manager, so PlayAnimation()
    returns immediately and does nothing; without the BSX Animated bit the
    engine never ticks the graph it just loaded.
    """
    for root in data.roots:
        if root is None or not hasattr(root, 'extra_data_list'):
            continue
        for ed in root.extra_data_list:
            if isinstance(ed, NifFormat.BSBehaviorGraphExtraData):
                ed.behaviour_graph_file = graph_file.encode('latin-1')
                break
        else:
            bged = NifFormat.BSBehaviorGraphExtraData()
            bged.name = b'BGED'
            bged.behaviour_graph_file = graph_file.encode('latin-1')
            bged.controls_base_skeleton = 0
            root.num_extra_data_list += 1
            root.extra_data_list.update_size()
            root.extra_data_list[root.num_extra_data_list - 1] = bged
        for ed in root.extra_data_list:
            if isinstance(ed, NifFormat.BSXFlags):
                ed.integer_data = (int(ed.integer_data) | 0x01) & ~0x80
                break
        return True
    return False


def _is_identity(rotation):
    """Return True if a PyFFI Matrix33 is the identity matrix."""
    return (abs(rotation.m_11 - 1.0) < 1e-4 and abs(rotation.m_22 - 1.0) < 1e-4 and
            abs(rotation.m_33 - 1.0) < 1e-4 and abs(rotation.m_12) < 1e-4 and
            abs(rotation.m_13) < 1e-4 and abs(rotation.m_21) < 1e-4 and
            abs(rotation.m_23) < 1e-4 and abs(rotation.m_31) < 1e-4 and
            abs(rotation.m_32) < 1e-4)


def _identity_matrix():
    """A fresh identity Matrix33."""
    m = NifFormat.Matrix33()
    m.m_11 = 1.0; m.m_22 = 1.0; m.m_33 = 1.0
    return m


def zero_fallout_root_rotation(root) -> None:
    """Zero a FO3/FNV root's authored rotation (before the Prn seating).
    See: docs/commentary/asset_convert_falloutnv.md#fnv-weapon-flip
    """
    if is_fallout_source() and hasattr(root, 'rotation'):
        root.rotation = _identity_matrix()


def wrap_geometry_root(data, i, root, stats):
    """Put a NiNode above a bare geometry root; the root to carry on with.

    Skyrim never ships a geometry root -- a 400-mesh vanilla census found 0
    (BSFadeNode 340, NiNode 55, BSMasterParticleSystem 2, BSLeafAnimNode 3) --
    and LODGenx64 hard-crashes with "Unable to cast NiTriShape to NiNode",
    abandoning the ENTIRE worldspace's object LOD rather than the one mesh.
    The geometry keeps its transform, so the wrap is visually identity.
    """
    if not isinstance(root, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
        return root
    holder = NifFormat.NiNode()
    holder.name = root.name
    holder.flags = NIF_FLAGS
    holder.num_children = 1
    holder.children.update_size()
    holder.children[0] = root
    data.roots[i] = holder
    stats['geometry_roots_wrapped'] = \
        stats.get('geometry_roots_wrapped', 0) + 1
    return holder


def fade_above_rig_root(data, i, root, stats):
    """Put a scene BSFadeNode ABOVE a rig root, keeping it a bone; the new root.

    A rig authored with no scene node makes the bone the file root. Replacing
    it, as the normal wrap does, leaves the FILE node holding the name the
    engine binds the actor through, and no bone carries it.
    See: docs/commentary/tes4_export_morrowind.md#rig-root-is-the-file-root
    """
    fade = NifFormat.BSFadeNode()
    fade.name = b'Scene Root'
    fade.flags = NIF_FLAGS
    fade.num_children = 1
    fade.children.update_size()
    fade.children[0] = root
    data.roots[i] = fade
    stats['root_converted'] += 1
    return fade


def wrap_root_transform(root, has_skin, furn_shift):
    """Move a static root's rotation onto an inner NiNode; True when wrapped.

    Skyrim ignores a root rotation Oblivion honoured, so it moves down one
    level; collision stays on the root and its body absorbs the transform.
    The furniture shift rides the same wrapper; on a FO3/FNV root only the
    weapon flip survives. A rig root is exempt: the engine binds the actor by
    that name, so the wrapper would be a second node carrying it.
    See: docs/commentary/tes4_export_morrowind.md#rig-root-is-the-file-root
    """
    if has_skin or not (hasattr(root, 'rotation') and hasattr(root, 'children')):
        return False
    if _is_identity(root.rotation) and abs(furn_shift) <= 1e-4:
        return False
    if bytes(root.name).rstrip(b'\x00') == b'NPC Root [Root]':
        return False

    inner = NifFormat.NiNode()
    inner.name = root.name
    inner.flags = NIF_FLAGS
    r = root.rotation
    for row in (1, 2, 3):
        for col in (1, 2, 3):
            setattr(inner.rotation, f'm_{row}{col}',
                    getattr(r, f'm_{row}{col}'))
    inner.translation.x = root.translation.x
    inner.translation.y = root.translation.y
    inner.translation.z = root.translation.z + furn_shift
    inner.scale = root.scale

    if getattr(root, 'collision_object', None) is not None:
        bake_node_transform_into_body(root.collision_object, root,
                                      extra_z=furn_shift)

    inner.num_children = root.num_children
    inner.children.update_size()
    for j in range(root.num_children):
        inner.children[j] = root.children[j]

    root.rotation = _identity_matrix()
    root.translation.x = 0.0
    root.translation.y = 0.0
    root.translation.z = 0.0
    root.scale = 1.0
    root.num_children = 1
    root.children.update_size()
    root.children[0] = inner
    return True
