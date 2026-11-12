"""Give an animated Morrowind object the named sequences Skyrim plays.

Morrowind animates a mesh on one timeline: `NiKeyframeController`s and
`NiGeomMorpherController`s hold keys over the whole of it, and either the
root's text keys cut it into groups (`Idle`, `Idle2`...) or an AutoPlay
`NiBSAnimationNode` runs it forever. Skyrim plays only a `NiControllerManager`
whose sequences each start at 0, and the 4.0.0.2 controllers hold their keys in
a `Data` field Skyrim's format no longer has, so without this pass the mesh is
written with every key dropped.

Each group becomes one sequence, re-timed from 0 and sampled at the clip frame
rate, in the shape Oblivion meshes arrive in, so the Oblivion passes that follow
(morph emulation, the AutoPlay/AutoLoop split, the behaviour graph) apply as-is.

See: docs/commentary/asset_convert_animation.md#morrowind-object-animation
"""

import numpy as np

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.havok.kf_decode import DEFAULT_FPS
from asset_convert.nif.sequences import (CYCLE_CLAMP, CYCLE_LOOP, INTRO_SUFFIX,
                                         OUTRO_SUFFIX, transform_manager)
from asset_convert.nif.timeline_morrowind import (XYZ_ROTATION_KEY, chain,
                                                  channel, clip_range,
                                                  euler_arrays, groups, interp,
                                                  key_arrays, text_keys)

#: The group Morrowind loops on every animated object by default.
IDLE_GROUP = 'idle'

#: Groups Morrowind loops whatever their keys say (OpenMW `Animation::isLoopingAnimation`).
_ALWAYS_LOOPING = frozenset((
    'walkforward', 'walkback', 'walkleft', 'walkright', 'swimwalkforward',
    'swimwalkback', 'swimwalkleft', 'swimwalkright', 'runforward', 'runback',
    'runleft', 'runright', 'swimrunforward', 'swimrunback', 'swimrunleft',
    'swimrunright', 'sneakforward', 'sneakback', 'sneakleft', 'sneakright',
    'turnleft', 'turnright', 'swimturnleft', 'swimturnright', 'spellturnleft',
    'spellturnright', 'torch', 'idle', 'idle2', 'idle3', 'idle4', 'idle5',
    'idle6', 'idle7', 'idle8', 'idle9', 'idlesneak', 'idlestorm', 'idleswim',
    'jump', 'inventoryhandtohand', 'inventoryweapononehand',
    'inventoryweapontwohand', 'inventoryweapontwowide'))

#: NiTimeController cycle type, flags bits 1-2: 0 loop, 1 reverse, 2 clamp.
_CYCLE_SHIFT, _CYCLE_MASK = 1, 0x3
_CTRL_REVERSE, _CTRL_CLAMP = 1, 2

#: Key type LINEAR_KEY: the samples are dense, so nothing finer is needed.
_LINEAR_KEY = 1

#: "No base value": the channel defers to the node's own transform.
_NO_VALUE = -3.4028234663852886e+38

#: Prefix for an animated node whose name cannot address it uniquely.
_RENAMED_PREFIX = b'MWAnim'


def start_end_keys(duration: float):
    """The `start`/`end` text-key pair every vanilla object sequence carries."""
    block = NifFormat.NiTextKeyExtraData()
    block.num_text_keys = 2
    block.text_keys.update_size()
    block.text_keys[0].time, block.text_keys[0].value = 0.0, b'start'
    block.text_keys[1].time, block.text_keys[1].value = duration, b'end'
    return block


def _controller_time(ctrl, times: np.ndarray) -> np.ndarray:
    """Where `ctrl` reads its keys at each animation time, as OpenMW's ControllerFunction does."""
    t = times * float(ctrl.frequency or 1.0) + float(ctrl.phase)
    start, stop = float(ctrl.start_time), float(ctrl.stop_time)
    span = stop - start
    mode = (int(ctrl.flags) >> _CYCLE_SHIFT) & _CYCLE_MASK
    if span <= 0.0 or mode == _CTRL_CLAMP:
        return np.clip(t, start, stop)
    rel = np.mod(t - start, 2.0 * span if mode == _CTRL_REVERSE else span)
    if mode == _CTRL_REVERSE:
        rel = np.where(rel > span, 2.0 * span - rel, rel)
    inside = (t >= start) & (t <= stop)
    return np.where(inside, t, start + rel)


def _rows(local: np.ndarray, keys) -> np.ndarray:
    """One channel sampled at `local`, collapsed to one row when it never changes."""
    values = interp(local, keys)
    return values[:1] if np.allclose(values, values[0], atol=1e-6) else values


def _fill_float_keys(group, times: np.ndarray, values: np.ndarray) -> None:
    """Write linear float keys into a KeyGroup."""
    group.interpolation = _LINEAR_KEY
    group.num_keys = len(values)
    group.keys.update_size()
    for key, time, value in zip(group.keys, times, values[:, 0]):
        key.time, key.value = float(time), float(value)


def _fill_rotation(data, kd, local, times) -> None:
    """Quaternion or XYZ rotation keys onto NiTransformData."""
    quats = key_arrays(kd)[0]
    if quats is not None:
        rows = _rows(local, quats)
        rows /= np.maximum(np.linalg.norm(rows, axis=1, keepdims=True), 1e-12)
        data.rotation_type = _LINEAR_KEY
        data.num_rotation_keys = len(rows)
        data.quaternion_keys.update_size()
        for key, time, (w, x, y, z) in zip(data.quaternion_keys, times, rows):
            key.time = float(time)
            key.value.w, key.value.x, key.value.y, key.value.z = w, x, y, z
        return
    axes = euler_arrays(kd)
    if not any(axes):
        return
    data.rotation_type = XYZ_ROTATION_KEY
    data.num_rotation_keys = 1
    for group, keys in zip(data.xyz_rotations, axes):
        if keys is not None:
            rows = _rows(local, keys)
            _fill_float_keys(group, times, rows)


def _transform_interpolator(node, kd, local, times):
    """A NiTransformInterpolator replaying `kd` at `local`, keyed at `times`."""
    data = NifFormat.NiTransformData()
    _fill_rotation(data, kd, local, times)
    _, translation, scale = key_arrays(kd)
    if translation is not None:
        rows = _rows(local, translation)
        group = data.translations
        group.interpolation = _LINEAR_KEY
        group.num_keys = len(rows)
        group.keys.update_size()
        for key, time, (x, y, z) in zip(group.keys, times, rows):
            key.time = float(time)
            key.value.x, key.value.y, key.value.z = x, y, z
    if scale is not None:
        _fill_float_keys(data.scales, times, _rows(local, scale))
    ip = NifFormat.NiTransformInterpolator()
    ip.data = data
    for axis in ('x', 'y', 'z'):
        setattr(ip.translation, axis, _NO_VALUE)
    for axis in ('w', 'x', 'y', 'z'):
        setattr(ip.rotation, axis, _NO_VALUE)
    ip.scale = float(node.scale)
    return ip


def _morph_interpolators(morpher, local, times) -> list:
    """One NiFloatInterpolator per morph target, base first; keyless targets carry no data."""
    out = []
    for morph in morpher.data.morphs:
        ip = NifFormat.NiFloatInterpolator()
        ip.float_value = _NO_VALUE
        keys = channel(morph.keys, lambda v: (v,)) if morph.num_keys else None
        if keys is not None and out:
            ip.data = NifFormat.NiFloatData()
            _fill_float_keys(ip.data.data, times, _rows(local, keys))
        out.append(ip)
    return out


def _add_block(seq, name: bytes, ctype: bytes, ctrl, ip) -> None:
    """Append one controlled block to `seq`."""
    seq.num_controlled_blocks += 1
    seq.controlled_blocks.update_size()
    block = seq.controlled_blocks[seq.num_controlled_blocks - 1]
    block.node_name = name
    block.controller_type = ctype
    block.priority = 0
    block.controller = ctrl
    block.interpolator = ip


def _alpha_interpolator(ctrl, local, times):
    """A NiFloatInterpolator replaying a NiAlphaController's material alpha at `local`, keyed at `times`."""
    ip = NifFormat.NiFloatInterpolator()
    ip.float_value = _NO_VALUE
    ip.data = NifFormat.NiFloatData()
    _fill_float_keys(ip.data.data, times, _rows(local, channel(ctrl.data.data.keys, lambda v: (v,))))
    return ip


def _sequence(manager, mttc, animated, span) -> object:
    """One NiControllerSequence replaying every animated controller over `span`."""
    name, start, stop, cycle = span
    duration = stop - start
    count = max(int(round(duration * DEFAULT_FPS)), 1) + 1
    times = np.linspace(0.0, duration, count)
    seq = NifFormat.NiControllerSequence()
    seq.name = name.encode('cp1252')
    seq.start_time, seq.stop_time = 0.0, duration
    seq.cycle_type, seq.frequency, seq.weight = cycle, 1.0, 1.0
    seq.manager = manager
    seq.text_keys = start_end_keys(duration)
    for node, ctrl in animated:
        local = _controller_time(ctrl, times + start)
        if isinstance(ctrl, NifFormat.NiKeyframeController):
            _add_block(seq, bytes(node.name), b'NiTransformController', mttc,
                       _transform_interpolator(node, ctrl.data, local, times))
            continue
        if isinstance(ctrl, NifFormat.NiAlphaController):
            _add_block(seq, bytes(node.name), b'NiAlphaController', ctrl,
                       _alpha_interpolator(ctrl, local, times))
            continue
        for ip in _morph_interpolators(ctrl, local, times):
            _add_block(seq, bytes(node.name), b'NiGeomMorpherController',
                       ctrl, ip)
    return seq


def _usable(ctrl) -> bool:
    """Whether a Morrowind controller carries keys this pass can replay."""
    data = getattr(ctrl, 'data', None)
    if isinstance(ctrl, NifFormat.NiKeyframeController):
        return isinstance(data, NifFormat.NiKeyframeData)
    if isinstance(ctrl, NifFormat.NiAlphaController):
        return isinstance(data, NifFormat.NiFloatData) and data.data.num_keys > 0
    return (isinstance(ctrl, NifFormat.NiGeomMorpherController)
            and data is not None and data.num_morphs > 1)


def _controllers(node) -> list:
    """The node's own controllers, then those on its properties (a material's alpha fade)."""
    found = list(chain(node.controller, 'next_controller'))
    for prop in getattr(node, 'properties', None) or ():
        if prop is not None:
            found += chain(prop.controller, 'next_controller')
    return found


def _animated(root, autoplay: bool) -> list:
    """(node, controller) for every replayable controller, in tree order.

    `autoplay` picks the controllers under an AutoPlay node, which run by
    themselves; otherwise those outside one, which the text-key groups drive.
    """
    out, seen, stack = [], set(), [(root, False)]
    while stack:
        node, under = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        under = under or getattr(node, '_mw_autoplay', False)
        if under == autoplay:
            out.extend((node, c) for c in _controllers(node) if _usable(c))
        kids = [c for c in (getattr(node, 'children', None) or ()) if c is not None]
        stack.extend((kid, under) for kid in reversed(kids))
    return out


def _looping_spans(name: str, events: dict) -> list:
    """A looping group's loop segment as a LOOP sequence, plus its Start..Loop
    Start lead-in and Loop Stop..Stop lead-out as CLAMP ones where they last."""
    got = clip_range(events)
    if not got:
        return []
    loop_start, loop_stop, _ = got
    spans = [(name, loop_start, loop_stop, CYCLE_LOOP)]
    if loop_start > events['start']:
        spans.append((name + INTRO_SUFFIX, events['start'], loop_start, CYCLE_CLAMP))
    if events['stop'] > loop_stop:
        spans.append((name + OUTRO_SUFFIX, loop_stop, events['stop'], CYCLE_CLAMP))
    return spans


def _group_spans(data) -> list:
    """(sequence name, start, stop, cycle) per text-key group.

    A looping group -- one with a loop start key, or one Morrowind always
    loops -- ships `_looping_spans`; the LOOP cycle is what tells the
    runtime's `LoopGroup` it repeats. Every other group plays once, Start to
    Stop, and holds.
    """
    spans = []
    for name, events in sorted(groups(text_keys(data))[0].items()):
        title = name.capitalize()
        if name in _ALWAYS_LOOPING or 'loop start' in events:
            spans.extend(_looping_spans(title, events))
            continue
        start, stop = events.get('start'), events.get('stop')
        if start is not None and stop is not None and stop > start:
            spans.append((title, start, stop, CYCLE_CLAMP))
    return spans


def _autoplay_span(animated) -> list:
    """The one looping Idle an AutoPlay node plays: its longest controller period."""
    periods = [(float(c.stop_time) - float(c.start_time)) / float(c.frequency or 1.0)
               for _, c in animated]
    longest = max(periods, default=0.0)
    return [(IDLE_GROUP.capitalize(), 0.0, longest, CYCLE_LOOP)] if longest > 0 else []


def _unique_names(root, animated) -> None:
    """Rename every animated node whose name is empty or shared, so a sequence can address it."""
    counts = {}
    for block in root.tree():
        if isinstance(block, NifFormat.NiAVObject):
            name = bytes(block.name or b'')
            counts[name] = counts.get(name, 0) + 1
    renamed = 0
    for node, _ in animated:
        name = bytes(node.name or b'')
        if name and counts.get(name) == 1:
            continue
        renamed += 1
        node.name = _RENAMED_PREFIX + str(renamed).encode('ascii')
        counts[bytes(node.name)] = 1


def _lift_root(root, animated) -> list:
    """Move the root's own animation onto a new child, since sequences skip the root.

    The child takes the root's transform and children; the root goes identity.
    """
    own = [c for n, c in animated if n is root]
    if not own:
        return animated
    lifted = NifFormat.NiNode()
    lifted.name = bytes(root.name or b'') + b' Anim'
    lifted.flags = root.flags
    lifted.translation, lifted.rotation = root.translation, root.rotation
    lifted.scale = root.scale
    for kid in [c for c in root.children if c is not None]:
        lifted.add_child(kid)
    root.num_children = 0
    root.children.update_size()
    root.add_child(lifted)
    root.translation.x = root.translation.y = root.translation.z = 0.0
    root.rotation.set_identity()
    root.scale = 1.0
    for ctrl in own:
        _detach(root, ctrl)
        lifted.add_controller(ctrl)
        ctrl.target = lifted
    return [(lifted if n is root else n, c) for n, c in animated]


def _detach(node, ctrl) -> None:
    """Unlink one controller from a node's controller chain."""
    if node.controller is ctrl:
        node.controller = ctrl.next_controller
    else:
        for prev in chain(node.controller, 'next_controller'):
            if prev.next_controller is ctrl:
                prev.next_controller = ctrl.next_controller
                break
    ctrl.next_controller = None


def _palette(root):
    """The object palette naming every named NiAVObject, first of each name."""
    palette = NifFormat.NiDefaultAVObjectPalette()
    named = {}
    for block in root.tree():
        if isinstance(block, NifFormat.NiAVObject) and block.name:
            named.setdefault(bytes(block.name), block)
    palette.num_objs = len(named)
    palette.objs.update_size()
    for entry, (name, block) in zip(palette.objs, named.items()):
        entry.name, entry.av_object = name, block
    return palette


def animate_timeline(data, root, keyed: bool) -> int:
    """Cut `root`'s Morrowind timeline into a NiControllerManager's sequences; how many.

    `keyed`: Morrowind plays this mesh's text-key groups, which then win.
    Otherwise only an AutoPlay node's controllers move, looping as one Idle;
    everything else stays still, as it does in Morrowind.
    """
    if isinstance(getattr(root, 'controller', None), NifFormat.NiControllerManager):
        return 0
    spans = _group_spans(data) if keyed else []
    animated = _animated(root, autoplay=not spans)
    if not spans:
        spans = _autoplay_span(animated)
    if not animated or not spans:
        return 0
    animated = _lift_root(root, animated)
    _unique_names(root, animated)
    moved = {id(n): n for n, c in animated
             if isinstance(c, NifFormat.NiKeyframeController)}
    manager, mttc = transform_manager(root, _palette(root), list(moved.values()))
    sequences = [_sequence(manager, mttc, animated, span) for span in spans]
    manager.num_controller_sequences = len(sequences)
    manager.controller_sequences.update_size()
    for i, seq in enumerate(sequences):
        manager.controller_sequences[i] = seq
    for node, ctrl in animated:
        if isinstance(ctrl, NifFormat.NiKeyframeController):
            _detach(node, ctrl)
    return len(sequences)


def drop_unplayed_keyframes(data) -> int:
    """Unlink every NiKeyframeController left on a node; how many.

    Skyrim's format has no field for their 4.0.0.2 keys, so one left behind
    ships as an empty controller that vanilla never carries.
    """
    dropped = 0
    live = [b for root in data.roots if root is not None for b in root.tree()]
    for block in live:
        ctrl = getattr(block, 'controller', None)
        if not isinstance(block, NifFormat.NiObjectNET) or ctrl is None:
            continue
        for found in [c for c in chain(ctrl, 'next_controller')
                      if isinstance(c, NifFormat.NiKeyframeController)]:
            _detach(block, found)
            dropped += 1
    return dropped
