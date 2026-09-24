"""Morph emulation: NiGeomMorpherController rebuilt as baked shape swaps.

Skyrim has no morph-controller class, so each animated target is baked into a
sibling copy, every swapped shape gets its own wrapper NiNode, and the sequence
gains NiVisController entries on those wrappers swapping base -> target as the
weight curve crosses 0.5.  The block-cloning machinery
that bake needs lives here too, as does the NiBlend*Interpolator header fixup
that must run after every pass which synthesizes one.

See: docs/commentary/asset_convert_nif.md#morph-emulation
"""

import numpy as np
from pyffi.object_models.xml.array import Array as _Arr

from asset_convert.lod.mesh_decimate import vertex_normals
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.nif.sequences import CB_OFFSETS


def _all_attr_names(cls):
    """Attribute names of a PyFFI struct class INCLUDING inherited ones.

    Walked MRO base-first so counts still precede their arrays.
    See: docs/commentary/asset_convert_nif.md#morph-swap-block-mechanics
    """
    names = []
    for klass in reversed(cls.__mro__):
        for a in klass.__dict__.get('_attrs', ()):
            if a.name not in names:
                names.append(a.name)
    return names


def _copy_block_fields(src, dst):
    """Field-by-field copy of a PyFFI block (scalars, compounds, arrays).

    Reference-typed fields are copied as POINTERS (shared blocks); the caller
    overrides the ones the clone must own (data, controller, collision).
    See: docs/commentary/asset_convert_nif.md#morph-swap-block-mechanics
    """
    def _copy_value(sv, dv, setter):
        """Recursively copy one field, descending arrays and compounds."""
        if isinstance(dv, _Arr):
            if hasattr(dv, 'update_size'):
                try:
                    dv.update_size()
                except Exception:
                    pass
            for i in range(min(len(sv), len(dv))):
                _copy_value(sv[i], dv[i],
                            lambda v, _dv=dv, _i=i: _dv.__setitem__(_i, v))
        elif hasattr(dv, '_attrs'):
            for name in _all_attr_names(type(dv)):
                try:
                    _copy_value(getattr(sv, name), getattr(dv, name),
                                lambda v, _dv=dv, _n=name: setattr(_dv, _n, v))
                except Exception:
                    pass
        else:
            try:
                setter(sv)
            except Exception:
                pass

    for name in _all_attr_names(type(dst)):
        try:
            sv = getattr(src, name)
            dv = getattr(dst, name)
        except Exception:
            continue
        if isinstance(dv, _Arr) or hasattr(dv, '_attrs'):
            _copy_value(sv, dv, lambda v, _n=name: setattr(dst, _n, v))
        else:
            try:
                setattr(dst, name, sv)
            except Exception:
                pass


def _morph_weight_curve(interp):
    """(time, value) list of a morph target's weight keys, or []."""
    data = getattr(interp, 'data', None)
    kg = getattr(data, 'data', None)
    keys = getattr(kg, 'keys', None)
    if not keys:
        return []
    return [(k.time, k.value) for k in keys]


#: Frames per second the morph flipbook samples each weight curve at.
FLIPBOOK_FPS = 30

#: A frame whose every vertex lies within this many game units of the shown shape reuses it.
FLIPBOOK_TOLERANCE = 1.0


#: unknown_short = Flags 0x01 | Array Size 0x02. See: docs/commentary/asset_convert_nif.md#blend-interp-flags
BLEND_INTERP_FLAGS_ARRAYSIZE = 0x0201


def _init_blend_interpolator(blend):
    """Give a synthesized NiBlend*Interpolator vanilla's manager-driven header."""
    blend.unknown_short = BLEND_INTERP_FLAGS_ARRAYSIZE
    blend.unknown_int = 0
    return blend


_BLEND_INTERP_TYPES = tuple(
    t for t in (getattr(NifFormat, n, None) for n in (
        'NiBlendBoolInterpolator', 'NiBlendFloatInterpolator',
        'NiBlendPoint3Interpolator', 'NiBlendTransformInterpolator',
        'NiBlendColorInterpolator')) if t is not None)


def normalize_blend_interpolators(root, stats=None):
    """Force every NiBlend*Interpolator to vanilla's manager-driven header.

    Blocks COPIED from an Oblivion source hit the same trap as synthesized ones:
    the flags do not survive PyFFI's round trip.
    See: docs/commentary/asset_convert_nif.md#blend-interp-flags
    """
    fixed = 0
    if not _BLEND_INTERP_TYPES:
        return fixed
    for blk in root.tree():
        if not isinstance(blk, _BLEND_INTERP_TYPES):
            continue
        if getattr(blk, 'unknown_short', None) != BLEND_INTERP_FLAGS_ARRAYSIZE:
            blk.unknown_short = BLEND_INTERP_FLAGS_ARRAYSIZE
            blk.unknown_int = 0
            fixed += 1
    if fixed and stats is not None:
        stats['blend_interp_fixed'] = stats.get('blend_interp_fixed', 0) + fixed
    return fixed


def _palette_add(pal, obj):
    """Register a synthesized block in the manager's object palette."""
    if pal is None:
        return
    pal.num_objs += 1
    pal.objs.update_size()
    entry = pal.objs[pal.num_objs - 1]
    entry.name = bytes(obj.name)
    entry.av_object = obj


def _vis_controller(node):
    """The node's NiVisController, created on the vanilla pattern if absent.

    See: docs/commentary/asset_convert_nif.md#morph-swap-block-mechanics
    """
    ctrl = node.controller
    while ctrl is not None:
        if isinstance(ctrl, NifFormat.NiVisController):
            return ctrl
        ctrl = ctrl.next_controller
    ctrl = NifFormat.NiVisController()
    ctrl.flags = 108
    ctrl.frequency = 1.0
    ctrl.phase = 0.0
    ctrl.start_time = 0.0
    ctrl.stop_time = 0.0
    blend = NifFormat.NiBlendBoolInterpolator()
    _init_blend_interpolator(blend)
    blend.bool_value = 2
    ctrl.interpolator = blend
    ctrl.target = node
    ctrl.next_controller = node.controller
    node.controller = ctrl
    return ctrl


def _bool_key_data(initially_on, toggles):
    """Step-keyed NiBoolData toggling from `initially_on` at each time.

    Keys MUST be CONST_KEY (5).
    See: docs/commentary/asset_convert_nif.md#morph-swap-block-mechanics
    """
    bd = NifFormat.NiBoolData()
    kg = bd.data
    times = [0.0] + [t for t in toggles if t > 0.0]
    state = initially_on
    values = [1 if state else 0]
    for _ in times[1:]:
        state = not state
        values.append(1 if state else 0)
    kg.interpolation = 5
    kg.num_keys = len(times)
    kg.keys.update_size()
    for i, (t, v) in enumerate(zip(times, values)):
        kg.keys[i].time = t
        kg.keys[i].value = v
    return bd, values[0]


def _add_vis_cb(seq, node, initially_on, toggles):
    """Append a NiVisController entry swapping wrapper `node` on and off."""
    bd, first = _bool_key_data(initially_on, toggles)
    ip = NifFormat.NiBoolInterpolator()
    ip.bool_value = bool(first)
    ip.data = bd
    ctrl = _vis_controller(node)
    ctrl.stop_time = max(ctrl.stop_time, seq.stop_time)
    seq.num_controlled_blocks += 1
    seq.controlled_blocks.update_size()
    cb = seq.controlled_blocks[seq.num_controlled_blocks - 1]
    cb.interpolator = ip
    cb.controller = ctrl
    if hasattr(cb, 'priority'):
        cb.priority = 0
    cb.node_name = bytes(node.name)
    cb.property_type = b''
    cb.controller_type = b'NiVisController'
    cb.variable_1 = b''
    cb.variable_2 = b''
    for off in CB_OFFSETS:
        if hasattr(cb, off):
            try:
                setattr(cb, off, -1)
            except Exception:
                pass


def _morph_target_index(entry, morphs):
    """Which morph target this entry drives, by frame name then ordinal."""
    idx = entry['ordinal']
    frame = entry['frame']
    if frame:
        for i in range(len(morphs)):
            fn = getattr(morphs[i], 'frame_name', None)
            if fn is not None and bytes(fn) == frame:
                return i
    return idx


def _wrap_shape(geom, parent, pal):
    """Put `geom` under its own identity NiNode "<shape> Swap" in `parent`.

    Vanilla sequence-driven NiVisController entries target a NiNode in
    1221/1221 cases and geometry in none, so the swap drives this wrapper.
    See: docs/commentary/asset_convert_nif.md#morph-emulation
    """
    wrapper = NifFormat.NiNode()
    wrapper.name = bytes(geom.name) + b' Swap'
    wrapper.flags = 14
    wrapper.rotation.set_identity()
    wrapper.scale = 1.0
    for i in range(parent.num_children):
        if parent.children[i] is geom:
            parent.children[i] = wrapper
            break
    else:
        parent.add_child(wrapper)
    wrapper.add_child(geom)
    _palette_add(pal, wrapper)
    return wrapper


def _own_properties(clone):
    """Replace the clone's shared shader/alpha property pointers with copies.

    The engine keeps per-shape render state in the shader property, so a shape
    shown mid-view through its twin's property stays semi-transparent until it
    leaves the screen (measured live on ctrigtripwire01).
    See: docs/commentary/asset_convert_animation.md#morph-emulation
    """
    for i, prop in enumerate(clone.bs_properties):
        if prop is None:
            continue
        mine = prop.__class__()
        _copy_block_fields(prop, mine)
        mine.controller = None
        tex = getattr(prop, 'texture_set', None)
        if tex is not None:
            mine.texture_set = tex.__class__()
            _copy_block_fields(tex, mine.texture_set)
        clone.bs_properties[i] = mine


def _xyz(vectors):
    """An (n, 3) float64 array of a PyFFI vector list."""
    return np.array([(v.x, v.y, v.z) for v in vectors], dtype=np.float64)


def _set_xyz(vectors, arr):
    """Write an (n, 3) array back into a PyFFI vector list."""
    for v, (x, y, z) in zip(vectors, arr.tolist()):
        v.x, v.y, v.z = x, y, z


def _frame_normals(data, base, pos):
    """The authored normals bent by how far the smooth normals moved base -> pos.

    Adding the smooth-normal CHANGE keeps the authored hard edges and seams.
    """
    tris = np.array(data.get_triangles(), dtype=np.int64)
    bent = (_xyz(data.normals) + vertex_normals(pos, tris)
            - vertex_normals(base, tris))
    length = np.linalg.norm(bent, axis=1, keepdims=True)
    length[length < 1e-10] = 1.0
    return bent / length


def _bake_morph_clone(geom, base, pos, name):
    """A sibling shape at vertex positions `pos`, normals and tangents rebuilt."""
    gdata = geom.data
    clone = geom.__class__()
    _copy_block_fields(geom, clone)
    cdata = gdata.__class__()
    _copy_block_fields(gdata, cdata)
    _set_xyz(cdata.vertices, pos)
    if cdata.has_normals:
        _set_xyz(cdata.normals, _frame_normals(gdata, base, pos))
    try:
        cdata.update_center_radius()
    except Exception:
        pass
    clone.data = cdata
    clone.name = name
    clone.controller = None
    clone.collision_object = None
    clone.flags = int(geom.flags) & ~0x01
    if cdata.has_normals and int(getattr(cdata, 'extra_vectors_flags', 0)) & 16:
        clone.update_tangent_space(as_extra=False)
    _own_properties(clone)
    return clone


def _curve_value_at(curve, t):
    """The morph weight at time `t`, linearly interpolated."""
    for (t0, v0), (t1, v1) in zip(curve, curve[1:]):
        if t0 <= t <= t1:
            span = ((t - t0) / (t1 - t0)) if t1 > t0 else 0.0
            return v0 + (v1 - v0) * span
    if curve:
        return curve[0][1] if t <= curve[0][0] else curve[-1][1]
    return None


def _frame_times(seq):
    """The sequence's span sampled at FLIPBOOK_FPS, both ends included."""
    start, stop = float(seq.start_time), float(seq.stop_time)
    count = max(1, int(round((stop - start) * FLIPBOOK_FPS)))
    return [start + (stop - start) * k / count for k in range(count + 1)]


def _frame_positions(geom, md, targets, times):
    """(base positions, [positions at each time]) blending every target's weight."""
    base = _xyz(geom.data.vertices)
    relative = bool(getattr(md, 'relative_targets', 1))
    deltas = []
    for vectors, curve in targets.values():
        d = _xyz(vectors)
        deltas.append((d if relative else d - base, curve))
    frames = []
    for t in times:
        pos = base.copy()
        for d, curve in deltas:
            pos += (_curve_value_at(curve, t) or 0.0) * d
        frames.append(pos)
    return base, frames


def _flipbook_states(base, frames):
    """(kept shapes, shape index per frame); index 0 is the base shape.

    A frame within FLIPBOOK_TOLERANCE of the shape on screen keeps showing it,
    else reuses the closest kept shape within tolerance (a looping clip repeats
    its poses), else becomes a new kept shape.
    """
    kept, states = [base], []
    for pos in frames:
        shown = states[-1] if states else 0
        if np.abs(pos - kept[shown]).max() <= FLIPBOOK_TOLERANCE:
            states.append(shown)
            continue
        errors = [np.abs(pos - shape).max() for shape in kept]
        best = int(np.argmin(errors))
        if errors[best] > FLIPBOOK_TOLERANCE:
            kept.append(pos)
            best = len(kept) - 1
        states.append(best)
    return kept, states


def _state_toggles(states, times, index):
    """(initially shown, toggle times) for shape `index` over the frames."""
    shown = [s == index for s in states]
    toggles = [times[k] for k in range(1, len(shown)) if shown[k] != shown[k - 1]]
    return shown[0], toggles


def _morph_geometry_index(root):
    """({name: geometry}, {id(geometry): parent}) for every shape."""
    geoms, parents = {}, {}
    for blk in root.tree():
        if isinstance(blk, NifFormat.NiNode):
            for ch in blk.children:
                if isinstance(ch, NifFormat.NiTriBasedGeom):
                    geoms[bytes(ch.name)] = ch
                    parents[id(ch)] = blk
    return geoms, parents


def _morph_swap_plan(entry, geoms):
    """(geometry, morph data, target index, vectors) for a swap, or None.

    None whenever the entry cannot be rebuilt: unknown shape, no morph data, an
    index outside the target list, or a vector count the geometry disagrees with.
    """
    geom = geoms.get(entry['shape'])
    if geom is None:
        return None
    md = getattr(entry['morpher'], 'data', None)
    morphs = getattr(md, 'morphs', None)
    if md is None or not morphs:
        return None
    idx = _morph_target_index(entry, morphs)
    if idx <= 0 or idx >= len(morphs):
        return None
    nverts = getattr(geom.data, 'num_vertices', 0)
    vectors = morphs[idx].vectors
    if nverts == 0 or len(vectors) != nverts:
        return None
    return geom, md, idx, vectors


def _collect_morph_swaps(root):
    """Every swap request the retargeting pass harvested onto the tree."""
    swaps = []
    for blk in root.tree():
        got = getattr(blk, '_morph_swaps', None)
        if got:
            swaps.extend(got)
    return swaps


def _group_targets(swaps, geoms):
    """{(sequence id, shape): (sequence, geometry, morph data, {index: (vectors, curve)})}."""
    groups = {}
    for entry in swaps:
        plan = _morph_swap_plan(entry, geoms)
        curve = _morph_weight_curve(entry['interp'])
        if plan is None or not curve:
            continue
        geom, md, idx, vectors = plan
        group = groups.setdefault((id(entry['seq']), entry['shape']),
                                  (entry['seq'], geom, md, {}))
        group[3][idx] = (vectors, curve)
    return groups


def _emit_flipbook(group, parent, base_wrapper, pal):
    """Bake one sequence's in-between shapes of one shape; how many were made.

    See: docs/commentary/asset_convert_animation.md#morph-emulation
    """
    seq, geom, md, targets = group
    times = _frame_times(seq)
    base, frames = _frame_positions(geom, md, targets, times)
    kept, states = _flipbook_states(base, frames)
    seq_name = bytes(seq.name or b'')
    for index in range(1, len(kept)):
        name = bytes(geom.name) + b'Mrph' + seq_name + str(index).encode('ascii')
        clone = _bake_morph_clone(geom, base, kept[index], name)
        _add_vis_cb(seq, _wrap_shape(clone, parent, pal),
                    *_state_toggles(states, times, index))
    _add_vis_cb(seq, base_wrapper, *_state_toggles(states, times, 0))
    return len(kept) - 1


def emulate_morphs(root, stats=None):
    """Rebuild dropped NiGeomMorpherController animation as a shape flipbook.

    See: docs/commentary/asset_convert_nif.md#morph-emulation
    """
    swaps = _collect_morph_swaps(root)
    if not swaps:
        return
    geoms, parents = _morph_geometry_index(root)
    mgr = root.controller
    pal = (mgr.object_palette
           if isinstance(mgr, NifFormat.NiControllerManager) else None)
    base_wrappers = {}
    made = 0
    for (_, name), group in _group_targets(swaps, geoms).items():
        geom = group[1]
        parent = parents[id(geom)]
        if name not in base_wrappers:
            base_wrappers[name] = _wrap_shape(geom, parent, pal)
        made += _emit_flipbook(group, parent, base_wrappers[name], pal)
    if stats is not None:
        stats['morph_swaps'] = stats.get('morph_swaps', 0) + made
