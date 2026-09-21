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

#: How far a Morrowind door swings, as a NIF +Z rotation.
OPEN_ANGLE = -math.pi / 2.0

#: Seconds the swing takes; OpenMW's rotateDoor runs at 90 deg/s.
SWING_SECONDS = 1.0

#: NiTimeController flags of a managed controller: active, cycle clamp.
MANAGED_CONTROLLER_FLAGS = 0x4C

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
    manager = NifFormat.NiControllerManager()
    manager.flags = MANAGED_CONTROLLER_FLAGS
    manager.frequency = 1.0
    manager.phase = 0.0
    manager.start_time = 3.402823e38
    manager.stop_time = -3.402823e38
    manager.cumulative = False
    manager.target = root
    manager.object_palette = _object_palette(root, hinge)
    controller = NifFormat.NiMultiTargetTransformController()
    controller.flags = MANAGED_CONTROLLER_FLAGS
    controller.frequency = 1.0
    controller.phase = 0.0
    controller.start_time = 3.402823e38
    controller.stop_time = -3.402823e38
    controller.target = root
    controller.num_extra_targets = 1
    controller.extra_targets.update_size()
    controller.extra_targets[0] = hinge
    manager.next_controller = controller
    root.add_controller(manager)
    return manager, controller


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


def animate_morrowind_door(root) -> bool:
    """Give `root` the Open/Close sequences Skyrim swings a door with.

    The visible tree moves under one hinge node at the mesh origin, which the
    sequences turn about Z; the collision follows it as a keyframed body.
    Returns False when the root cannot hold children or is already animated.
    """
    if not hasattr(root, 'children') or not hasattr(root, 'add_controller'):
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
