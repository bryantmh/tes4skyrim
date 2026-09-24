"""NiControllerSequence conversion: the animation half of a NIF.

Oblivion drives in-NIF animation through a NiControllerManager holding named
sequences; Skyrim keeps the same structure but accepts a much narrower set of
controller types and stores its strings differently.  Everything that reads or
rewrites a sequence lives here.

See: docs/commentary/asset_convert_nif.md#nif-controller-sequences
"""

import math

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat
from asset_convert.nif.nif_flags import (TT_SCALE_U, TT_SCALE_V,
                                         TT_TRANSLATE_U,
                                         TT_TRANSLATE_V)

#: Types vanilla ships in a controlled block. See: docs/commentary/asset_convert_nif.md#sequence-controller-retargeting
_VANILLA_SEQ_CONTROLLERS = frozenset({
    'BSEffectShaderPropertyFloatController',
    'BSEffectShaderPropertyColorController',
    'BSLightingShaderPropertyFloatController',
    'BSLightingShaderPropertyColorController',
    'BSNiAlphaPropertyTestRefController',
    'BSFrustumFOVController',
    'BSLagBoneController',
    'BSProceduralLightningController',
    'BSPSysMultiTargetEmitterCtlr',
    'NiControllerManager',
    'NiMultiTargetTransformController',
    'NiTransformController',
    'NiVisController',
    'NiFloatExtraDataController',
    'NiBSBoneLODController',
    'NiPSysUpdateCtlr',
    'NiPSysEmitterCtlr',
    'NiPSysModifierActiveCtlr',
    'NiPSysEmitterSpeedCtlr',
    'NiPSysGravityStrengthCtlr',
    'NiPSysEmitterInitialRadiusCtlr',
    'NiPSysEmitterLifeSpanCtlr',
    'NiPSysEmitterPlanarAngleCtlr',
    'NiPSysEmitterDeclinationCtlr',
    'NiPSysInitialRotSpeedCtlr',
})

#: operation -> (lighting float var, effect float var); TT_ROTATE has no Skyrim equivalent and is dropped.
TEX_TRANSFORM_VARS = {
    TT_TRANSLATE_U: (20, 6),
    TT_TRANSLATE_V: (22, 8),
    TT_SCALE_U: (21, 7),
    TT_SCALE_V: (23, 9),
}

#: NiMaterialColorController.target_color 3 = TC_SELF_ILLUM, the only channel with a Skyrim analogue.
MATERIAL_COLOR_EMISSIVE = 3

#: Emissive color-controller type, (lighting, effect). See: docs/commentary/asset_convert_shader.md#ob-enums
_SHADER_COLOR_EMISSIVE = (1, 0)

#: Opacity float-controller variable, (lighting, effect). See: docs/commentary/asset_convert_shader.md#ob-enums
_SHADER_ALPHA_VAR = (12, 5)

#: Group names a script can `playgroup`. See: docs/commentary/asset_convert_nif.md#script-driven-sequence-names
SCRIPT_DRIVEN_SEQUENCES = frozenset((
    'forward', 'backward', 'fastforward', 'fastbackward',
    'left', 'right', 'equip', 'unequip', 'specialidle', 'stagger',
))

#: Vanilla's self-playing pair. See: docs/commentary/asset_convert_nif.md#autoplay-ambient-sequences
AUTOPLAY_SEQUENCE = 'AutoPlay'
AUTOLOOP_SEQUENCE = 'AutoLoop'

#: nif.xml CycleType LOOP.
CYCLE_LOOP = 0
#: nif.xml CycleType CLAMP (1 is REVERSE).
CYCLE_CLAMP = 2

#: Oblivion names meaning "ambient, plays by itself"; script-driven names keep theirs.
_AMBIENT_SEQUENCES = frozenset(('idle',))


def _drop_mttc_target(mgr, node_name: bytes) -> int:
    """Remove `node_name` from every NiMultiTargetTransformController's targets.

    The extra-target list is POSITIONAL -- the engine pairs slot N with the
    NiControllerSequence entry that drives it -- so a target whose entry has
    been removed leaves a null interpolator that
    BGSGamebryoSequenceGenerator dereferences when the object animates.
    Whenever a controlled block goes, its target must go with it.

    Returns the number of slots removed (for stats/tests).
    """
    removed = 0
    for ctrl in _iter_controllers(mgr):
        if not isinstance(ctrl, NifFormat.NiMultiTargetTransformController):
            continue
        targets = list(getattr(ctrl, 'extra_targets', None) or ())
        kept = [t for t in targets
                if t is None
                or bytes(getattr(t, 'name', b'') or b'') != node_name]
        if len(kept) == len(targets):
            continue
        removed += len(targets) - len(kept)
        ctrl.num_extra_targets = len(kept)
        ctrl.extra_targets.update_size()
        for i, t in enumerate(kept):
            ctrl.extra_targets[i] = t
    return removed


def _iter_controllers(mgr):
    """Every controller reachable from a NiControllerManager."""
    ctrl = getattr(mgr, 'next_controller', None)
    while ctrl is not None:
        yield ctrl
        ctrl = getattr(ctrl, 'next_controller', None)
    for seq in (getattr(mgr, 'controller_sequences', None) or ()):
        for cb in (getattr(seq, 'controlled_blocks', None) or ()):
            c = getattr(cb, 'controller', None)
            if c is not None:
                yield c


#: Gamebryo's "this channel has no value" sentinel.
_NO_VALUE = -3.4028234663852886e+38


def _find_named_node(root, name):
    """The first node under `root` called `name` that carries a transform."""
    for b in root.tree():
        nm = getattr(b, 'name', None)
        if nm is not None and bytes(nm) == name and hasattr(b, 'translation'):
            return b
    return None


def _authored_pose(node):
    """(translation, moved, rotation-is-identity) for a node's authored pose."""
    t = node.translation
    m = node.rotation
    trace = m.m_11 + m.m_22 + m.m_33
    cos = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    rot_identity = math.degrees(math.acos(cos)) < 0.1
    root_t = (t.x, t.y, t.z)
    return root_t, max(abs(v) for v in root_t) >= 0.05, rot_identity


def _nonaccum_transform(seq, na_name, resolve_name):
    """(first translation, whether a rotation is animated) for NonAccum."""
    for cb in seq.controlled_blocks:
        nm = resolve_name(cb, seq, 'node_name')
        nm = nm.encode('latin-1') if isinstance(nm, str) else bytes(nm or b'')
        if nm != na_name:
            continue
        it = cb.interpolator
        if it is None or not hasattr(it, 'rotation'):
            continue
        d = getattr(it, 'data', None)
        na_t = None
        if d is not None and d.translations.num_keys:
            k = d.translations.keys[0].value
            na_t = (k.x, k.y, k.z)
        elif it.translation.x > _NO_VALUE:
            na_t = (it.translation.x, it.translation.y, it.translation.z)
        animated = bool(
            d is not None and (getattr(d, 'num_rotation_keys', 0)
                               or any(g.num_keys for g in d.xyz_rotations)))
        if not animated and it.rotation.w > _NO_VALUE:
            animated = True
        return na_t, animated
    return None, False


def _accum_root_mode(seq, root, resolve_name):
    """How the sequence's accum-root controlled block must be converted.

    'transferred' when the node's real transform lives on its NonAccum child,
    so playing the exporter's identity pose is correct and the entry must be
    left as authored; 'orphan' when nothing carries it, so every channel is
    sentinelled; None when there is no accum root or its pose is identity.
    See: docs/commentary/asset_convert_nif.md#accum-root-classification
    """
    accum = getattr(seq, 'target_name', b'') or b''
    if isinstance(accum, str):
        accum = accum.encode('latin-1')
    accum = bytes(accum)
    if not accum:
        return None
    anode = _find_named_node(root, accum)
    if anode is None:
        return None

    root_t, root_moved, rot_identity = _authored_pose(anode)
    if not root_moved and rot_identity:
        return None

    na_t, na_rot_animated = _nonaccum_transform(
        seq, accum + b' NonAccum', resolve_name)
    if root_moved:
        if (na_t is not None
                and max(abs(a - b) for a, b in zip(na_t, root_t)) < 1.0):
            return 'transferred'
        return 'orphan'
    return 'transferred' if na_rot_animated else 'orphan'


def _property_ctrl_index(root):
    """id(property controller) -> [geometry names wearing that property].

    Built once per manager (one tree walk), looked up per entry.
    See: docs/commentary/asset_convert_nif.md#shared-property-fan-out
    """
    index = {}
    for blk in root.tree():
        if not hasattr(blk, 'properties') or not hasattr(blk, 'data'):
            continue
        nm = bytes(getattr(blk, 'name', b'') or b'')
        if not nm:
            continue
        for prop in blk.properties or ():
            if prop is None:
                continue
            c = getattr(prop, 'controller', None)
            while c is not None:
                lst = index.setdefault(id(c), [])
                if nm not in lst:
                    lst.append(nm)
                c = getattr(c, 'next_controller', None)
    return index


def _shapes_sharing_property_ctrl(index, src_ctrl, own_name):
    """Names of the OTHER geometries whose Oblivion property carries *src_ctrl*."""
    return [nm for nm in index.get(id(src_ctrl), ()) if nm != own_name]


#: Controlled-block string offsets, reset to -1 on a synthesized entry.
CB_OFFSETS = ('node_name_offset', 'property_type_offset',
              'controller_type_offset', 'variable_1_offset',
              'variable_2_offset')

#: Private markers match_seq_shader_types reads back off a converted controller.
_CTRL_RESTAMP_MARKERS = ('_tt_operation', '_alpha_ctrl', '_is_color_ctrl')


def _entry_signature(cb):
    """(node name, controller class, variable, color) identifying an entry."""
    return (bytes(cb.node_name or b''), cb.controller.__class__.__name__,
            getattr(cb.controller, 'type_of_controlled_variable', None),
            getattr(cb.controller, 'type_of_controlled_color', None))


def _shallow_clone(block):
    """A same-class copy of a pyffi block, field by declared field."""
    out = block.__class__()
    for m in block._get_names():
        try:
            setattr(out, m, getattr(block, m))
        except Exception:
            pass
    return out


def _clone_sibling_controller(src_ctrl):
    """A per-shape copy of a converted controller, key data shared."""
    c2 = _shallow_clone(src_ctrl)
    for priv in _CTRL_RESTAMP_MARKERS:
        if hasattr(src_ctrl, priv):
            setattr(c2, priv, getattr(src_ctrl, priv))
    c2.next_controller = None
    c2.target = None
    return c2


def _append_sibling_entry(seq, src_cb, sib):
    """Append one controlled block driving `sib` the way `src_cb` drives its own."""
    seq.num_controlled_blocks += 1
    seq.controlled_blocks.update_size()
    cb = seq.controlled_blocks[seq.num_controlled_blocks - 1]
    for m in src_cb._get_names():
        setattr(cb, m, getattr(src_cb, m))
    cb.node_name = sib
    for off in CB_OFFSETS:
        if hasattr(cb, off):
            try:
                setattr(cb, off, -1)
            except Exception:
                pass
    c2 = _clone_sibling_controller(src_cb.controller)
    if src_cb.interpolator is not None:
        i2 = _shallow_clone(src_cb.interpolator)
        c2.interpolator = i2
        cb.interpolator = i2
    cb.controller = c2


def _fan_out_shared_entries(seq, extras):
    """Append one controlled block per (source entry, sibling name) pair.

    See: docs/commentary/asset_convert_nif.md#shared-property-fan-out
    """
    if not extras:
        return 0
    have = {_entry_signature(cb) for cb in seq.controlled_blocks}
    added = 0
    for src_cb, sib in extras:
        sc = src_cb.controller
        sig = (sib, sc.__class__.__name__,
               getattr(sc, 'type_of_controlled_variable', None),
               getattr(sc, 'type_of_controlled_color', None))
        if sig in have:
            continue
        have.add(sig)
        _append_sibling_entry(seq, src_cb, sib)
        added += 1
    return added


def _apply_rotation(m, quat):
    """Write quaternion (w,x,y,z) into an existing pyffi Matrix33."""
    w, x, y, z = quat
    m.m_11 = 1 - 2 * (y * y + z * z)
    m.m_12 = 2 * (x * y - z * w)
    m.m_13 = 2 * (x * z + y * w)
    m.m_21 = 2 * (x * y + z * w)
    m.m_22 = 1 - 2 * (x * x + z * z)
    m.m_23 = 2 * (y * z - x * w)
    m.m_31 = 2 * (x * z - y * w)
    m.m_32 = 2 * (y * z + x * w)
    m.m_33 = 1 - 2 * (x * x + y * y)


def _dropped_accum_root_pose(root, mgr, resolve_name):
    """Apply the accum-root entry's pose to the root NODE, since the entry dies.

    Only for an entry naming the FILE ROOT, the one the root-name rule drops.
    See: docs/commentary/asset_convert_nif.md#accum-root-classification
    """
    root_name = bytes(getattr(root, 'name', b'') or b'')
    if not root_name:
        return None
    for seq in mgr.controller_sequences:
        accum = getattr(seq, 'target_name', b'') or b''
        accum = accum.encode('latin-1') if isinstance(accum, str) else bytes(accum)
        if accum != root_name:
            continue
        for cb in seq.controlled_blocks:
            nm = resolve_name(cb, seq, 'node_name')
            nm = nm.encode('latin-1') if isinstance(nm, str) else bytes(nm or b'')
            if nm != accum:
                continue
            it = cb.interpolator
            if (isinstance(it, NifFormat.NiTransformInterpolator) and
                    it.data is None and it.rotation.w > _NO_VALUE):
                q = it.rotation
                return (float(q.w), float(q.x), float(q.y), float(q.z))
            break
        break
    return None


def _seq_name_resolver(palette):
    """A reader for controlled-block names, palette-aware.

    Names live EITHER in the bytes field OR at an offset into the sequence's
    NiStringPalette; Oblivion NIFs written with a palette leave the bytes
    EMPTY, and reading only those returned '' for every entry.
    See: docs/commentary/asset_convert_nif.md#controlled-block-names
    """
    def resolve(blk, seq, attr):
        """The named string off a controlled block, bytes field or palette."""
        val = getattr(blk, attr, b'')
        if isinstance(val, bytes) and val:
            return val
        if isinstance(val, int) and val and palette is not None:
            try:
                return palette.get_string(val)
            except Exception:
                pass
        return palette_lookup(
            palette_bytes(getattr(seq, 'string_palette', None)),
            getattr(blk, attr + '_offset', None))
    return resolve


def _shader_float_ctrl(src_ctrl, interp, variable):
    """A Skyrim shader float controller carrying `src_ctrl`'s timing.

    The variable is provisionally the Lighting one.
    See: docs/commentary/asset_convert_shader.md#shader-float-controller-flags
    """
    new = NifFormat.BSLightingShaderPropertyFloatController()
    new.flags = 0x48 | (int(getattr(src_ctrl, 'flags', 0)) & 0x06)
    new.frequency = getattr(src_ctrl, 'frequency', 1.0) or 1.0
    new.phase = getattr(src_ctrl, 'phase', 0.0)
    new.start_time = src_ctrl.start_time
    new.stop_time = src_ctrl.stop_time
    new.interpolator = interp
    new.type_of_controlled_variable = variable
    return new


def _neutralise_transform(blk, node_name, accum_name, accum_mode):
    """Sentinel a dataless NiTransformInterpolator instead of dropping it.

    Vanilla keeps these and stores -FLT_MAX per channel, which tells the
    engine the channel has no value.  The accum root is the exception both
    ways.
    See: docs/commentary/asset_convert_nif.md#dataless-transform-interpolators
    """
    interp = blk.interpolator
    nn = (node_name.encode('latin-1') if isinstance(node_name, str)
          else bytes(node_name or b''))
    is_accum_root = bool(nn) and nn == accum_name
    if (accum_mode == 'transferred' and bool(nn)
            and (is_accum_root or nn == accum_name + b' NonAccum')):
        return
    if is_accum_root and interp.data is not None:
        d = interp.data
        if (getattr(d, 'num_rotation_keys', 0) == 0
                and d.translations.num_keys == 0
                and d.scales.num_keys == 0
                and all(g.num_keys == 0 for g in d.xyz_rotations)):
            interp.data = None
    if interp.data is None:
        interp.rotation.w = _NO_VALUE
        interp.rotation.x = _NO_VALUE
        interp.rotation.y = _NO_VALUE
        interp.rotation.z = _NO_VALUE
        interp.scale = _NO_VALUE
        if is_accum_root and accum_mode == 'orphan':
            interp.translation.x = _NO_VALUE
            interp.translation.y = _NO_VALUE
            interp.translation.z = _NO_VALUE


def _harvest_morph(node, seq, blk, node_name, resolve):
    """Record what emulate_morphs needs to rebuild this morph as geometry.

    Skyrim has no NiGeomMorpherController RTTI class at all, so the entry
    must go -- but the morph IS the visible effect for a family of meshes.
    See: docs/commentary/asset_convert_nif.md#morph-emulation
    """
    seen = getattr(node, '_morph_cb_seen', None)
    if seen is None:
        seen = node._morph_cb_seen = {}
    k = (id(seq), bytes(node_name))
    ordinal = seen.get(k, 0)
    seen[k] = ordinal + 1
    if getattr(blk.interpolator, 'data', None) is None:
        return
    swaps = getattr(node, '_morph_swaps', None)
    if swaps is None:
        swaps = node._morph_swaps = []
    swaps.append({
        'seq': seq,
        'shape': bytes(node_name),
        'frame': bytes(resolve(blk, seq, 'variable_2') or b''),
        'ordinal': ordinal,
        'interp': blk.interpolator,
        'morpher': blk.controller,
    })


def _convert_color_ctrl(blk):
    """Retarget an emissive NiMaterialColorController; True when converted.

    Deleting it froze the animation at its first key, which for
    se11sheopooffx's Cone01 is emissive black.
    See: docs/commentary/asset_convert_nif.md#sequence-controller-retargeting
    """
    if (int(getattr(blk.controller, 'target_color', -1))
            != MATERIAL_COLOR_EMISSIVE
            or not isinstance(blk.interpolator, NifFormat.NiPoint3Interpolator)):
        return False
    src = blk.controller
    new = NifFormat.BSLightingShaderPropertyColorController()
    new.flags = 0x48 | (int(getattr(src, 'flags', 0)) & 0x06)
    new.frequency = getattr(src, 'frequency', 1.0) or 1.0
    new.phase = getattr(src, 'phase', 0.0)
    new.start_time = src.start_time
    new.stop_time = src.stop_time
    new.type_of_controlled_color = _SHADER_COLOR_EMISSIVE[0]
    new.interpolator = blk.interpolator
    new._is_color_ctrl = True
    blk.controller = new
    blk.controller_type = b'BSLightingShaderPropertyColorController'
    return True


def _convert_tex_transform(blk):
    """Retarget a NiTextureTransformController; True when converted.

    Vanilla ships ZERO of these, and the engine instantiates a controlled
    block's type by NAME, so leaving one rejects the whole NIF.  TT_ROTATE has
    no Skyrim equivalent.
    See: docs/commentary/asset_convert_nif.md#sequence-controller-retargeting
    """
    src = blk.controller
    op = getattr(src, 'operation', None)
    if op not in TEX_TRANSFORM_VARS:
        return False
    new = _shader_float_ctrl(src, blk.interpolator, TEX_TRANSFORM_VARS[op][0])
    new._tt_operation = op
    blk.controller = new
    blk.controller_type = b'BSLightingShaderPropertyFloatController'
    return True


def _convert_alpha_ctrl(blk):
    """Retarget a NiAlphaController onto the shader's Alpha; True if done.

    Dropping it froze the fade and left the surface static.
    See: docs/commentary/asset_convert_nif.md#sequence-controller-retargeting
    """
    if not isinstance(blk.interpolator, NifFormat.NiFloatInterpolator):
        return False
    new = _shader_float_ctrl(blk.controller, blk.interpolator,
                             _SHADER_ALPHA_VAR[0])
    new._alpha_ctrl = True
    blk.controller = new
    blk.controller_type = b'BSLightingShaderPropertyFloatController'
    return True


#: Retargeting handlers, tried in order; each returns True when it converted.
_CTRL_CONVERTERS = (
    (NifFormat.NiMaterialColorController, _convert_color_ctrl),
    (NifFormat.NiTextureTransformController, _convert_tex_transform),
    (NifFormat.NiAlphaController, _convert_alpha_ctrl),
)


def _drop_block(seq, key):
    """Remove controlled block `key`; the loop index does not advance."""
    seq.controlled_blocks.pop(key)
    seq.num_controlled_blocks -= 1


def _handle_block(ctx, seq, key):
    """Convert or drop one controlled block; True when it survived.

    See: docs/commentary/asset_convert_nif.md#sequence-controller-retargeting
    """
    node, mgr, resolve = ctx['node'], ctx['mgr'], ctx['resolve']
    blk = seq.controlled_blocks[key]
    node_name = resolve(blk, seq, 'node_name')

    if not node_name or node_name == ctx['root_name']:
        if node_name:
            _drop_mttc_target(mgr, node_name)
        return False

    if isinstance(blk.interpolator, NifFormat.NiTransformInterpolator):
        _neutralise_transform(blk, node_name, ctx['accum_name'],
                              ctx['accum_mode'])

    if isinstance(blk.controller, NifFormat.NiGeomMorpherController):
        _harvest_morph(node, seq, blk, node_name, resolve)
        return False

    for cls, convert in _CTRL_CONVERTERS:
        if isinstance(blk.controller, cls):
            src_ctrl = blk.controller
            if not convert(blk):
                return False
            if ctx['prop_index'] is None:
                ctx['prop_index'] = _property_ctrl_index(node)
            for sib in _shapes_sharing_property_ctrl(ctx['prop_index'],
                                                     src_ctrl, node_name):
                ctx['shared_extras'].append((blk, sib))
            return True

    if isinstance(blk.controller, NifFormat.NiFlipController):
        return False

    ctrl_cls = blk.controller.__class__.__name__ if blk.controller else None
    return ctrl_cls is None or ctrl_cls in _VANILLA_SEQ_CONTROLLERS


def process_controller_manager(node, palette):
    """Strip and retarget a NiControllerManager's sequences for Skyrim.

    Resolves node names through the string palette, drops blocks naming the
    root, converts the Oblivion-only controller types vanilla never ships, and
    sentinels dataless transform interpolators.
    See: docs/commentary/asset_convert_nif.md#sequence-controller-retargeting
    """
    mgr = node.controller
    resolve = _seq_name_resolver(palette)
    ctx = {'node': node, 'mgr': mgr, 'resolve': resolve,
           'root_name': node.name, 'prop_index': None}

    pending_bake = _dropped_accum_root_pose(node, mgr, resolve)
    for seq in mgr.controller_sequences:
        accum_name = getattr(seq, 'target_name', b'') or b''
        if isinstance(accum_name, str):
            accum_name = accum_name.encode('latin-1')
        ctx['accum_name'] = bytes(accum_name)
        ctx['accum_mode'] = _accum_root_mode(seq, node, resolve)
        ctx['shared_extras'] = []
        key = 0
        while key < seq.num_controlled_blocks:
            if _handle_block(ctx, seq, key):
                key += 1
            else:
                _drop_block(seq, key)
        _fan_out_shared_entries(seq, ctx['shared_extras'])

    if pending_bake is not None:
        _apply_rotation(node.rotation, pending_bake)


#: NiAVObject.flags bit 0. Only scene-graph objects have it.
_FLAG_HIDDEN = 0x0001


def _cb_rest_visible(cb):
    """Is this NiVisController entry's node visible at t=0? None if unreadable.

    A dataless NiBoolInterpolator is a CONSTANT whose bool_value IS the rest
    state. See: docs/commentary/asset_convert_nif.md#rest-visibility
    """
    interp = cb.interpolator
    if interp is None:
        return None
    data = getattr(interp, 'data', None)
    keys = getattr(data, 'data', None) if data is not None else None
    if keys is not None and keys.num_keys:
        return bool(min(keys.keys, key=lambda k: k.time).value)
    if not isinstance(interp, NifFormat.NiBoolInterpolator):
        return None
    return bool(getattr(interp, 'bool_value', True))


def _hide_named_nodes(root, name):
    """Set the hidden bit on every NiAVObject called `name`; how many changed."""
    hidden = 0
    for node in root.tree():
        if not isinstance(node, NifFormat.NiAVObject):
            continue
        if bytes(getattr(node, 'name', b'') or b'') != name:
            continue
        if not int(getattr(node, 'flags', 0)) & _FLAG_HIDDEN:
            node.flags = int(node.flags) | _FLAG_HIDDEN
            hidden += 1
    return hidden


def _vis_entries(block):
    """(entry, node name) for each NiVisController entry in a sequence."""
    raw = palette_bytes(getattr(block, 'string_palette', None))
    out = []
    for cb in block.controlled_blocks:
        ctrl_type = bytes(getattr(cb, 'controller_type', b'') or b'')
        if not ctrl_type:
            ctrl_type = palette_lookup(
                raw, getattr(cb, 'controller_type_offset', None))
        if ctrl_type != b'NiVisController':
            continue
        name = bytes(getattr(cb, 'node_name', b'') or b'')
        if not name:
            name = palette_lookup(raw, getattr(cb, 'node_name_offset', None))
        if name:
            out.append((cb, name))
    return out


def _plays_from_cell_load(block):
    """True for the AutoPlay/AutoLoop pair, whose own keys set visibility."""
    seq_name = bytes(getattr(block, 'name', b'') or b'')
    return seq_name in (AUTOPLAY_SEQUENCE.encode('latin-1'),
                        AUTOLOOP_SEQUENCE.encode('latin-1'))


def apply_rest_visibility(root, stats=None):
    """Hide nodes a sequence keeps invisible at time 0.

    See: docs/commentary/asset_convert_nif.md#rest-visibility
    """
    hidden = 0
    for block in root.tree():
        if not isinstance(block, NifFormat.NiControllerSequence):
            continue
        if _plays_from_cell_load(block):
            continue
        for cb, name in _vis_entries(block):
            if _cb_rest_visible(cb) is False:
                hidden += _hide_named_nodes(root, name)
    if hidden and stats is not None:
        stats['rest_hidden_nodes'] = stats.get('rest_hidden_nodes', 0) + hidden
    return hidden


#: The two Skyrim shader property classes a sequence entry can drive.
_SHADER_CLASSES = ('BSLightingShaderProperty', 'BSEffectShaderProperty')


def _shader_by_node_name(root):
    """{node name: its BS*ShaderProperty} for every named block in the tree."""
    shaders = {}
    for block in root.tree():
        nm = bytes(getattr(block, 'name', b'') or b'')
        if not nm:
            continue
        for pr in getattr(block, 'bs_properties', []) or []:
            if pr is not None and pr.__class__.__name__ in _SHADER_CLASSES:
                shaders[nm] = pr
    return shaders


def _already_chained(shader, ctrl):
    """Is `ctrl` already somewhere on the shader's controller chain?"""
    probe = shader.controller
    while probe is not None:
        if probe is ctrl:
            return True
        probe = probe.next_controller
    return False


def attach_seq_shader_controllers(root, stats=None):
    """Hang each sequence's shader controller off the shader it drives.

    See: docs/commentary/asset_convert_nif.md#seq-shader-controller-attach
    """
    shaders = _shader_by_node_name(root)
    attached = 0
    for block in root.tree():
        if not isinstance(block, NifFormat.NiControllerSequence):
            continue
        raw = palette_bytes(getattr(block, 'string_palette', None))
        for cb in block.controlled_blocks:
            ctrl = cb.controller
            if ctrl is None or 'ShaderProperty' not in ctrl.__class__.__name__:
                continue
            name = bytes(getattr(cb, 'node_name', b'') or b'')
            if not name:
                name = palette_lookup(raw, getattr(cb, 'node_name_offset', None))
            shader = shaders.get(name)
            if shader is None or _already_chained(shader, ctrl):
                continue
            ctrl.target = shader
            ctrl.next_controller = shader.controller
            shader.controller = ctrl
            attached += 1
    if attached and stats is not None:
        stats['seq_shader_ctrls_attached'] = \
            stats.get('seq_shader_ctrls_attached', 0) + attached
    return attached


def clone_sequence_as(root, seq, new_name, cycle_type):
    """Add a second NiControllerSequence named *new_name* beside *seq*.

    None when the manager cannot be reached -- a sequence with no manager is
    unreachable by the graph, so there is nothing to register.
    See: docs/commentary/asset_convert_nif.md#autoplay-ambient-sequences
    """
    mgr = getattr(seq, 'manager', None)
    if mgr is None:
        return None
    clone = NifFormat.NiControllerSequence()
    clone.name = new_name.encode('latin-1')
    clone.cycle_type = cycle_type
    clone.frequency = seq.frequency
    clone.start_time = seq.start_time
    clone.stop_time = seq.stop_time
    clone.manager = mgr
    clone.text_keys = seq.text_keys
    clone.string_palette = seq.string_palette
    if hasattr(seq, 'target_name'):
        clone.target_name = seq.target_name
    clone.num_controlled_blocks = seq.num_controlled_blocks
    clone.array_grow_by = getattr(seq, 'array_grow_by', 0)
    clone.controlled_blocks.update_size()
    names = list(seq.controlled_blocks[0]._get_names()) if seq.num_controlled_blocks else []
    for dst, src in zip(clone.controlled_blocks, seq.controlled_blocks):
        for member in names:
            setattr(dst, member, getattr(src, member))
    mgr.num_controller_sequences += 1
    mgr.controller_sequences.update_size()
    mgr.controller_sequences[mgr.num_controller_sequences - 1] = clone
    return clone


def autoplay_ambient_sequences(root, stats=None):
    """Turn Oblivion's self-playing "Idle" sequence into vanilla's AutoPlay pair.

    The authored Idle becomes AutoLoop and KEEPS its cycle type; a CLAMP clone
    named AutoPlay is added for the graph's start state. Script-driven names
    are left alone.
    See: docs/commentary/asset_convert_nif.md#autoplay-ambient-sequences
    """
    renamed = 0
    for block in root.tree():
        if not isinstance(block, NifFormat.NiControllerSequence):
            continue
        raw = getattr(block, 'name', b'') or b''
        name = raw.decode('latin-1') if isinstance(raw, bytes) else str(raw)
        if name.lower() not in _AMBIENT_SEQUENCES:
            continue
        block.name = AUTOLOOP_SEQUENCE.encode('latin-1')
        clone_sequence_as(root, block, AUTOPLAY_SEQUENCE, CYCLE_CLAMP)
        renamed += 1
    if renamed and stats is not None:
        stats['autoplay_sequences'] = stats.get('autoplay_sequences', 0) + renamed
    return renamed


def palette_bytes(string_palette):
    """Raw NUL-separated blob out of a NiStringPalette ref, or b''.

    PyFFI nests it: NiStringPalette.palette is a StringPalette struct whose own
    `palette` member is the byte string.
    """
    if string_palette is None:
        return b''
    pal = getattr(string_palette, 'palette', string_palette)
    raw = getattr(pal, 'palette', pal)
    if isinstance(raw, bytes):
        return raw
    try:
        return bytes(raw)
    except Exception:
        return b''


def palette_lookup(raw, offset):
    """Read the NUL-terminated string at `offset` in a palette blob."""
    if not raw or offset is None or offset == 0xFFFFFFFF or offset >= len(raw):
        return b''
    end = raw.find(b'\x00', offset)
    return raw[offset:end] if end >= 0 else raw[offset:]


def _bind_shader_ctrl_target(ctrl, shader):
    """Point a BS*ShaderProperty*Controller at the shader property it drives.

    A Lighting controller is never bound to an Effect shader or vice versa.
    See: docs/commentary/asset_convert_nif.md#controlled-block-id-strings
    """
    if ctrl is None or shader is None:
        return
    cn = ctrl.__class__.__name__
    if 'ShaderProperty' not in cn or 'Controller' not in cn:
        return
    want = ('BSEffectShaderProperty' if cn.startswith('BSEffectShaderProperty')
            else 'BSLightingShaderProperty')
    if shader.__class__.__name__ != want:
        return
    ctrl.target = shader


def _shader_class_index(root):
    """({node name: shader class name}, {node name: shader block})."""
    shader_of, shader_block_of = {}, {}
    for blk in root.tree():
        nm = getattr(blk, 'name', None)
        if not nm:
            continue
        for pr in getattr(blk, 'bs_properties', []) or []:
            if pr is None:
                continue
            cn = pr.__class__.__name__
            if cn in _SHADER_CLASSES:
                shader_of[bytes(nm)] = cn
                shader_block_of[bytes(nm)] = pr
    return shader_of, shader_block_of


def _copy_ctrl_timing(dst, src):
    """Carry a controller's flags, timing and interpolator onto a replacement."""
    dst.flags = src.flags
    dst.frequency = src.frequency
    dst.phase = src.phase
    dst.start_time = src.start_time
    dst.stop_time = src.stop_time
    dst.interpolator = src.interpolator


def _effect_variant_of(ctrl):
    """The Effect-shader controller replacing `ctrl`, or None if it needs none.

    Keyed on the private markers the retargeting pass stamped.
    """
    op = getattr(ctrl, '_tt_operation', None)
    if op is not None:
        eff = NifFormat.BSEffectShaderPropertyFloatController()
        eff.type_of_controlled_variable = TEX_TRANSFORM_VARS[op][1]
    elif getattr(ctrl, '_alpha_ctrl', False):
        eff = NifFormat.BSEffectShaderPropertyFloatController()
        eff.type_of_controlled_variable = _SHADER_ALPHA_VAR[1]
    elif getattr(ctrl, '_is_color_ctrl', False):
        eff = NifFormat.BSEffectShaderPropertyColorController()
        eff.type_of_controlled_color = _SHADER_COLOR_EMISSIVE[1]
    else:
        return None
    _copy_ctrl_timing(eff, ctrl)
    return eff


def _restamp_entry(cb, name, shader_of, shader_block_of):
    """Re-stamp one entry onto the Effect shader when its node ended up there."""
    ctrl = cb.controller
    shader = shader_block_of.get(name)
    if shader_of.get(name) != 'BSEffectShaderProperty':
        _bind_shader_ctrl_target(ctrl, shader)
        return
    eff = _effect_variant_of(ctrl)
    if eff is None:
        _bind_shader_ctrl_target(ctrl, shader)
        return
    cb.controller = eff
    cb.controller_type = eff.__class__.__name__.encode('ascii')
    _bind_shader_ctrl_target(eff, shader)


def match_seq_shader_types(root):
    """Make each retargeted UV controller match its target node's shader.

    Must run AFTER the geometry walk, which is what decides Lighting vs Effect.
    See: docs/commentary/asset_convert_nif.md#controlled-block-id-strings
    """
    shader_of, shader_block_of = _shader_class_index(root)
    for blk in root.tree():
        if not isinstance(blk, NifFormat.NiControllerSequence):
            continue
        raw = palette_bytes(getattr(blk, 'string_palette', None))
        for cb in blk.controlled_blocks:
            if cb.controller is None:
                continue
            name = bytes(getattr(cb, 'node_name', b'') or b'')
            if not name:
                name = palette_lookup(raw, getattr(cb, 'node_name_offset', None))
            _restamp_entry(cb, name, shader_of, shader_block_of)

    _normalize_shader_cb_strings(root)
    _retarget_geometry_suffix_entries(root)


def _normalize_shader_cb_strings(root):
    """Stamp vanilla controlled-block ID strings on converted shader controllers.

    See: docs/commentary/asset_convert_nif.md#controlled-block-id-strings
    """
    for blk in root.tree():
        if not isinstance(blk, NifFormat.NiControllerSequence):
            continue
        for cb in blk.controlled_blocks:
            ctrl = cb.controller
            if ctrl is None:
                continue
            cn = ctrl.__class__.__name__
            if not (cn.startswith(('BSEffectShaderProperty',
                                   'BSLightingShaderProperty'))
                    and cn.endswith('Controller')):
                continue
            cb.property_type = (
                b'BSEffectShaderProperty' if cn.startswith('BSEffectShaderProperty')
                else b'BSLightingShaderProperty')
            cb.controller_type = cn.encode('ascii')
            var = getattr(ctrl, 'type_of_controlled_variable',
                          getattr(ctrl, 'type_of_controlled_color', None))
            cb.variable_1 = (b'' if var is None else str(int(var)).encode('ascii'))
            cb.variable_2 = b''
def _retarget_geometry_suffix_entries(root):
    """Bind sequence entries that name geometry as "<node>:<index>".

    See: docs/commentary/asset_convert_nif.md#geometry-suffix-entries
    """
    for blk in root.tree():
        if not isinstance(blk, NifFormat.NiControllerSequence):
            continue
        raw = palette_bytes(getattr(blk, 'string_palette', None))
        for cb in blk.controlled_blocks:
            ctrl = cb.controller
            cn = ctrl.__class__.__name__ if ctrl is not None else ''
            if 'ShaderProperty' not in cn or 'Controller' not in cn:
                continue
            if getattr(ctrl, 'target', None) is not None:
                continue
            name = bytes(getattr(cb, 'node_name', b'') or b'')
            if not name:
                name = palette_lookup(raw, getattr(cb, 'node_name_offset', None))
            shader = _resolve_geometry_suffix(root, name)
            if shader is None:
                continue
            _bind_shader_ctrl_target(ctrl, shader)
            owner = getattr(shader, '_owner_name', None)
            if owner:
                cb.node_name = owner


def _resolve_geometry_suffix(root, name):
    """Map a "<node>:<index>" palette name onto that node's Nth shader."""
    if not name or b':' not in name:
        return None
    parent, _, idx = name.rpartition(b':')
    try:
        want = int(idx)
    except ValueError:
        return None

    target = None
    for blk in root.tree():
        if bytes(getattr(blk, 'name', b'') or b'') == parent:
            target = blk
            break
    if target is None:
        return None

    geoms = []
    for blk in target.tree():
        if not isinstance(blk, NifFormat.NiTriBasedGeom):
            continue
        for pr in getattr(blk, 'bs_properties', []) or []:
            if pr is None:
                continue
            if pr.__class__.__name__ in _SHADER_CLASSES:
                geoms.append((blk, pr))
                break
    if want >= len(geoms):
        return None
    node, shader = geoms[want]
    shader._owner_name = bytes(getattr(node, 'name', b'') or b'')
    return shader
