"""Give a converted Morrowind door the `Open`/`Close` sequences Skyrim plays.

Morrowind animates nothing in the mesh -- `World::rotateDoor` swings the
whole reference 90 degrees about Z over one second -- so every converted door
arrives as a static prop. Skyrim instead plays two named sequences stored in
the NIF, and the swing is reproduced by synthesising them over a hinge node
holding the visible tree, keyframed collision and all.

The pivot is the mesh origin, because Morrowind's is the reference origin;
no geometry is re-centred. OpenMW turns rot[2] about (0, 0, -1), so its
+90 degrees is -90 degrees as a NIF +Z rotation.
See: docs/commentary/asset_convert_nif.md#morrowind-door-animation
"""

import math

from pyffi.formats.nif import NifFormat

from asset_convert.nif.nif_passes import add_bsx_flags
from asset_convert.nif.sequences import (MANAGED_CONTROLLER_FLAGS, palette_bytes,
                                         palette_lookup, transform_manager)

#: How far a Morrowind door swings, as a NIF +Z rotation.
OPEN_ANGLE = -math.pi / 2.0

#: Seconds the swing takes; OpenMW's rotateDoor runs at 90 deg/s.
SWING_SECONDS = 1.0

#: NiControllerSequence cycle type CLAMP.
CYCLE_CLAMP = 2

#: NiTransformData.rotation_type XYZ_ROTATION_KEY; vanilla doors use only Z.
XYZ_ROTATION_KEY = 4

#: NiFloatData key interpolation QUADRATIC, as vanilla's door keys are.
KEY_QUADRATIC = 2

#: Sequence names the engine plays on a door, open state first.
OPEN_SEQUENCE, CLOSE_SEQUENCE = 'Open', 'Close'

#: Name of the synthesised hinge node the sequences drive.
HINGE_NODE = 'Door01'

#: Animated-node flags: the standard 14 plus the physics-sync bit 0x80.
ANIMATED_NODE_FLAGS = 14 | 0x80

#: bhkCollisionObject flags on an animated body (ACTIVE | D_ANIMATED | bit 7).
ANIMATED_COLLISION_FLAGS = 137

#: The keyframed rigid-body settings every vanilla door body carries.
_KEYFRAMED_BODY = {'motion_system': 4, 'quality_type': 1,
                   'solver_deactivation': 2, 'deactivator_type': 1,
                   'mass': 0.0}

#: SKYL_ANIMSTATIC, the layer every vanilla keyframed door body sits on.
_SKYL_ANIMSTATIC = 2

#: Vanilla's velocity ceiling for an engine-driven keyframed body.
_KEYFRAMED_VELOCITY_CAP = 1000002.0

#: "No base value" sentinel: vanilla door interpolators defer to their keys.
_NO_BASE_VALUE = -3.4028234663852886e+38

#: Least panel offset, in half-widths, that leaves room to swing about an edge.
HINGE_RATIO = 0.25

#: Rotation below which an axis is holding still, not turning (0.5 degrees).
_STILL_RADIANS = 0.0087


def _name(block) -> bytes:
    """A block's name as bytes, whatever pyffi stored."""
    raw = getattr(block, 'name', b'')
    return raw if isinstance(raw, bytes) else bytes(str(raw), 'cp1252')


def _rotation_keys(start: float, stop: float):
    """The (time, value) Z keys of one swing direction."""
    return ((0.0, start), (SWING_SECONDS, stop))


def _interpolator(start: float, stop: float):
    """A NiTransformInterpolator turning about Z from `start` to `stop`.

    Translation and scale carry no keys: a Morrowind door only rotates, and
    vanilla leaves both channels empty for exactly that reason.
    """
    interp = NifFormat.NiTransformInterpolator()
    data = NifFormat.NiTransformData()
    interp.data = data
    interp.scale = 1.0
    for axis in ('x', 'y', 'z'):
        setattr(interp.translation, axis, _NO_BASE_VALUE)
    for axis in ('w', 'x', 'y', 'z'):
        setattr(interp.rotation, axis, _NO_BASE_VALUE)
    data.rotation_type = XYZ_ROTATION_KEY
    data.num_rotation_keys = 1
    for axis, keys in zip(data.xyz_rotations,
                          (_rotation_keys(0.0, 0.0), _rotation_keys(0.0, 0.0),
                           _rotation_keys(start, stop))):
        axis.interpolation = KEY_QUADRATIC
        axis.num_keys = len(keys)
        axis.keys.update_size()
        for i, (time, value) in enumerate(keys):
            axis.keys[i].arg = KEY_QUADRATIC
            axis.keys[i].time = time
            axis.keys[i].value = value
    return interp


def _text_keys():
    """The `start`/`end` pair every vanilla door sequence carries."""
    block = NifFormat.NiTextKeyExtraData()
    block.num_text_keys = 2
    block.text_keys.update_size()
    block.text_keys[0].time, block.text_keys[0].value = 0.0, b'start'
    block.text_keys[1].time = SWING_SECONDS
    block.text_keys[1].value = b'end'
    return block


def _sequence(name: str, hinge, manager, controller, start: float,
              stop: float):
    """One named NiControllerSequence swinging `hinge` from `start` to `stop`."""
    seq = NifFormat.NiControllerSequence()
    seq.name = name.encode('cp1252')
    seq.start_time = 0.0
    seq.stop_time = SWING_SECONDS
    seq.cycle_type = CYCLE_CLAMP
    seq.frequency = 1.0
    seq.weight = 1.0
    seq.manager = manager
    seq.text_keys = _text_keys()
    seq.num_controlled_blocks = 1
    seq.controlled_blocks.update_size()
    block = seq.controlled_blocks[0]
    block.node_name = _name(hinge)
    block.controller_type = b'NiTransformController'
    block.priority = 0
    block.controller = controller
    block.interpolator = _interpolator(start, stop)
    return seq


def _object_palette(root, hinge):
    """The palette naming every node a sequence may address."""
    palette = NifFormat.NiDefaultAVObjectPalette()
    palette.scene = root
    entries = [hinge] + [c for c in (hinge.children or []) if c is not None]
    palette.num_objs = len(entries)
    palette.objs.update_size()
    for i, node in enumerate(entries):
        palette.objs[i].name = _name(node)
        palette.objs[i].av_object = node
    return palette


def _manager(root, hinge):
    """A NiControllerManager on `root` driving `hinge` through one controller."""
    return transform_manager(root, _object_palette(root, hinge), [hinge],
                             MANAGED_CONTROLLER_FLAGS)


def _move_children(root, hinge) -> None:
    """Re-parent every one of the root's children onto the hinge node."""
    kids = [c for c in (root.children or []) if c is not None]
    hinge.num_children = len(kids)
    hinge.children.update_size()
    for i, child in enumerate(kids):
        hinge.children[i] = child
    root.num_children = 1
    root.children.update_size()
    root.children[0] = hinge


def _keyframe_body(body) -> None:
    """Restate a static rigid body as the keyframed one a door needs."""
    for attr, value in _KEYFRAMED_BODY.items():
        setattr(body, attr, value)
    body.max_linear_velocity = _KEYFRAMED_VELOCITY_CAP
    body.max_angular_velocity = _KEYFRAMED_VELOCITY_CAP
    for name in ('havok_col_filter', 'havok_col_filter_copy'):
        col_filter = getattr(body, name, None)
        if col_filter is not None:
            col_filter.layer = _SKYL_ANIMSTATIC


def _move_collision(root, hinge) -> bool:
    """Hand the root's collision to the hinge, keyframed; did it move?

    A keyframed body only follows the animation when it hangs off the node the
    sequences drive, which is how every vanilla door is built.
    """
    collision = getattr(root, 'collision_object', None)
    if collision is None:
        return False
    root.collision_object = None
    hinge.collision_object = collision
    collision.target = hinge
    collision.flags = ANIMATED_COLLISION_FLAGS
    if collision.body is not None:
        _keyframe_body(collision.body)
    return True


def _panel_bounds(node):
    """XY bounds of the geometry under `node`, in that node's own space."""
    lo, hi = [1e30, 1e30], [-1e30, -1e30]
    stack, seen = [(node, (0.0, 0.0))], set()
    while stack:
        block, off = stack.pop()
        if id(block) in seen:
            continue
        seen.add(id(block))
        for child in (getattr(block, 'children', None) or ()):
            if child is None:
                continue
            shift = getattr(child, 'translation', None)
            stack.append((child, (off[0] + shift.x, off[1] + shift.y)
                          if shift is not None else off))
        for vert in (getattr(getattr(block, 'data', None),
                             'vertices', None) or ()):
            for i, coord in enumerate((vert.x + off[0], vert.y + off[1])):
                lo[i] = min(lo[i], coord)
                hi[i] = max(hi[i], coord)
    return (lo, hi) if lo[0] <= hi[0] else None


def hinge_offset(root) -> float:
    """How far the panel sits from the pivot, in its own half-widths.

    Measured in the ROOT's frame, because that is where the hinge node goes
    and where the synthesised sequences turn. A shape may carry a large
    offset in its own space and still be centred here, its translation
    cancelling its geometry, in which case there is nothing to swing about.
    See: docs/commentary/asset_convert_nif.md#a-door-turns-about-its-edge
    """
    bounds = _panel_bounds(root)
    if bounds is None:
        return 0.0
    lo, hi = bounds
    width = max(hi[0] - lo[0], hi[1] - lo[1])
    if width <= 0.0:
        return 0.0
    mid_x, mid_y = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    return (mid_x ** 2 + mid_y ** 2) ** 0.5 / (width / 2.0)


def _node_ratio(node) -> float:
    """A node's own panel offset over its own half-width."""
    bounds = _panel_bounds(node)
    if bounds is None:
        return 0.0
    lo, hi = bounds
    width = max(hi[0] - lo[0], hi[1] - lo[1])
    if width <= 0.0:
        return 0.0
    mid_x, mid_y = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    return (mid_x ** 2 + mid_y ** 2) ** 0.5 / (width / 2.0)


def has_hinge(root) -> bool:
    """Whether the panel clears its pivot far enough to swing about an edge.

    See: docs/commentary/asset_convert_nif.md#a-door-turns-about-its-edge
    """
    return hinge_offset(root) >= HINGE_RATIO


#: Source mesh's hinge verdict, latched before any pass alters its tree.
_HINGE_LATCH = [False]


def latch_source_hinge(data) -> None:
    """Read the hinge off the source mesh, before any pass alters the tree."""
    _HINGE_LATCH[0] = bool(data is not None and any(
        has_hinge(root) for root in data.roots if root is not None))


def source_had_hinge() -> bool:
    """Whether the source mesh of the NIF being converted had a hinge."""
    return _HINGE_LATCH[0]


def animate_morrowind_door(root) -> bool:
    """Give `root` the Open/Close sequences Skyrim swings a door with.

    The visible tree moves under one hinge node at the mesh origin, which the
    sequences turn about Z; the collision follows it as a keyframed body.
    Returns False when the root cannot hold children, is already animated, or
    has no hinge to turn about.
    """
    if not hasattr(root, 'children') or not hasattr(root, 'add_controller'):
        return False
    if not source_had_hinge():
        return False
    if isinstance(getattr(root, 'controller', None),
                  NifFormat.NiControllerManager):
        return False

    hinge = NifFormat.NiNode()
    hinge.name = HINGE_NODE.encode('cp1252')
    hinge.flags = ANIMATED_NODE_FLAGS
    _move_children(root, hinge)
    _move_collision(root, hinge)

    manager, controller = _manager(root, hinge)
    sequences = (_sequence(OPEN_SEQUENCE, hinge, manager, controller,
                           0.0, OPEN_ANGLE),
                 _sequence(CLOSE_SEQUENCE, hinge, manager, controller,
                           OPEN_ANGLE, 0.0))
    manager.num_controller_sequences = len(sequences)
    manager.controller_sequences.update_size()
    for i, seq in enumerate(sequences):
        manager.controller_sequences[i] = seq
    return True

#: bhkCollisionObject flags on an ordinary static body.
STATIC_COLLISION_FLAGS = 129

#: The rigid-body settings a non-animated door body carries.
_STATIC_BODY = {'motion_system': 5, 'quality_type': 0,
                'solver_deactivation': 0, 'deactivator_type': 1,
                'mass': 0.0}

#: SKYL_STATIC, the layer an immovable door body sits on.
_SKYL_STATIC = 1


def _static_body(body) -> None:
    """Restate a keyframed door body as the static one a twin needs."""
    for attr, value in _STATIC_BODY.items():
        setattr(body, attr, value)
    for name in ('havok_col_filter', 'havok_col_filter_copy'):
        col_filter = getattr(body, name, None)
        if col_filter is not None:
            col_filter.layer = _SKYL_STATIC


def _drop_manager(root) -> bool:
    """Unhook a NiControllerManager from the root's controller chain."""
    prev = None
    ctrl = getattr(root, 'controller', None)
    found = False
    while ctrl is not None:
        nxt = getattr(ctrl, 'next_controller', None)
        if isinstance(ctrl, (NifFormat.NiControllerManager,
                             NifFormat.NiMultiTargetTransformController)):
            if prev is None:
                root.controller = nxt
            else:
                prev.next_controller = nxt
            found = True
        else:
            prev = ctrl
        ctrl = nxt
    return found


def _reset_bsx(root) -> None:
    """Recompute BSXFlags once the tree no longer animates."""
    keep = [e for e in getattr(root, 'extra_data_list', ())
            if e is not None and not isinstance(e, NifFormat.BSXFlags)]
    root.num_extra_data_list = len(keep)
    root.extra_data_list.update_size()
    for i, block in enumerate(keep):
        root.extra_data_list[i] = block
    add_bsx_flags(root)


def _controlled_name(seq, cb) -> bytes:
    """The node a controlled block drives, from its bytes or the palette.

    An Oblivion sequence written with a NiStringPalette leaves the bytes
    field empty and stores an offset instead.
    See: docs/commentary/asset_convert_nif.md#controlled-block-names
    """
    raw = getattr(cb, 'node_name', None)
    if raw:
        return bytes(raw).rstrip(b'\x00')
    blob = palette_bytes(getattr(cb, 'string_palette', None))         or palette_bytes(getattr(seq, 'string_palette', None))
    return palette_lookup(blob, getattr(cb, 'node_name_offset', None))


def _spun_nodes(root):
    """Every node a sequence turns about Z without moving it anywhere."""
    named = {}
    for block in root.tree():
        if isinstance(block, NifFormat.NiAVObject):
            named.setdefault(_name(block).rstrip(b'\x00'), block)
    spun = []
    for seq in root.tree():
        if not isinstance(seq, NifFormat.NiControllerSequence):
            continue
        for cb in seq.controlled_blocks:
            data = getattr(getattr(cb, 'interpolator', None), 'data', None)
            if data is None or not _turns_only_about_z(data):
                continue
            node = named.get(_controlled_name(seq, cb))
            if node is not None:
                spun.append(node)
    return spun


def _spins_through_pivot(node) -> bool:
    """Whether the node's own largest piece runs through its pivot.

    A slab turning about its middle crosses the pivot as ONE piece. A double
    door's leaves each lie wholly to one side and hinge at their outer ends,
    so their bounds only look centred once summed; rubble and gate halves
    read the same way. Anything but a single crossing piece is left alone.
    """
    widest, crosses = 0.0, False
    for child in (getattr(node, 'children', None) or ()):
        if child is None:
            continue
        bounds = _panel_bounds(child)
        if bounds is None:
            continue
        shift = getattr(child, 'translation', None)
        offset = shift.x if shift is not None else 0.0
        lo, hi = bounds[0][0] + offset, bounds[1][0] + offset
        if hi - lo > widest:
            widest, crosses = hi - lo, lo < 0.0 < hi
    return crosses


def _axis_turns(axis) -> bool:
    """Whether an axis carries a key that actually rotates, not a flat one."""
    return any(abs(key.value) > _STILL_RADIANS for key in (axis.keys or ()))


def _turns_only_about_z(data) -> bool:
    """Whether this transform data spins about Z and translates nowhere."""
    if getattr(getattr(data, 'translations', None), 'num_keys', 0):
        return False
    axes = list(getattr(data, 'xyz_rotations', None) or ())
    if len(axes) < 3 or any(_axis_turns(axis) for axis in axes[:2]):
        return False
    return _axis_turns(axes[2])


def strip_hingeless_swing(root) -> bool:
    """Drop an authored swing the panel has no hinge to turn about.

    Morroblivion re-exported Morrowind's centred load doors with a 92 degree
    Z rotation baked in, so they spin through their own frame. The collision
    goes back to an ordinary static body so the shut door still blocks.
    See: docs/commentary/asset_convert_nif.md#a-door-turns-about-its-edge
    """
    spun = {id(node): node for node in _spun_nodes(root)}.values()
    if not spun:
        return False
    if any(_node_ratio(node) >= HINGE_RATIO or not _spins_through_pivot(node)
           for node in spun):
        return False
    if not _drop_manager(root):
        return False
    for block in root.tree():
        if not isinstance(block, NifFormat.NiAVObject):
            continue
        if block.flags == ANIMATED_NODE_FLAGS:
            block.flags = ANIMATED_NODE_FLAGS & ~0x80
        collision = getattr(block, 'collision_object', None)
        if collision is None:
            continue
        collision.flags = STATIC_COLLISION_FLAGS
        if collision.body is not None:
            _static_body(collision.body)
    _reset_bsx(root)
    return True
