"""Whether a node is really moved by animation, for the collision-hoist gate.

Oblivion's exporter emits a transform-controller entry for the root and
NonAccum node of nearly every mesh; on a static the interpolator holds no keys.
Those stubs must not read as animation, or ordinary statics keep their
collision on a child node instead of the root.

See: docs/commentary/asset_convert_collision.md#keyless-transform-stubs
"""
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()

from pyffi.formats.nif import NifFormat


def _text(value) -> str:
    """Decode a pyffi string-ish value."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode('latin-1')
    return str(value)


def _key_count(data) -> int:
    """Total rotation, translation and scale keys in one NiTransformData."""
    trans = getattr(data, 'translations', None)
    scales = getattr(data, 'scales', None)
    return (int(getattr(data, 'num_rotation_keys', 0) or 0)
            + (int(getattr(trans, 'num_keys', 0) or 0) if trans is not None else 0)
            + (int(getattr(scales, 'num_keys', 0) or 0) if scales is not None else 0))


def _has_transform_keys(interp) -> bool:
    """True when `interp` stores at least one transform key."""
    if interp is None:
        return False
    data = getattr(interp, 'data', None)
    return data is not None and _key_count(data) > 0


def _controls_node(cb, want: str) -> bool:
    """True when this controlled block drives `want`'s TRANSFORM."""
    try:
        name = _text(cb.get_node_name())
        ctype = _text(cb.get_controller_type())
    except Exception:
        return False
    return name == want and 'Transform' in ctype


def _root_sequences(root):
    """Every NiControllerSequence on a controller manager in `root`'s tree."""
    stack = [root]
    while stack:
        node = stack.pop()
        ctrl = getattr(node, 'controller', None)
        while ctrl is not None:
            for seq in (getattr(ctrl, 'controller_sequences', None) or []):
                if seq is not None:
                    yield seq
            ctrl = getattr(ctrl, 'next_controller', None)
        stack.extend(c for c in (getattr(node, 'children', None) or [])
                     if c is not None)


def _values_vary(values) -> bool:
    """True when the key values of one channel are not all equal."""
    first = values[0] if values else None
    return any(max(abs(a - b) for a, b in zip(v, first)) > 1e-3
               for v in values[1:])


def _interp_moves(interp) -> bool:
    """True when a transform interpolator's keys change the transform over time."""
    data = getattr(interp, 'data', None)
    if not isinstance(interp, NifFormat.NiTransformInterpolator) or data is None:
        return False
    channels = [[(k.value.x, k.value.y, k.value.z)
                 for k in data.translations.keys]]
    if data.rotation_type == 4:
        channels += [[(k.value,) for k in axis.keys]
                     for axis in data.xyz_rotations]
    else:
        channels.append([(k.value.w, k.value.x, k.value.y, k.value.z)
                         for k in data.quaternion_keys])
    return any(_values_vary(ch) for ch in channels)


def mesh_has_sequence(root) -> bool:
    """True when `root`'s tree carries at least one NiControllerSequence."""
    return next(_root_sequences(root), None) is not None


def clip_moves_node(root, node) -> bool:
    """True when some sequence in `root`'s tree really moves `node`.

    A block whose keys all hold one pose (the cave-in's rocks, the trap logs)
    does not move the node, only pins it.
    """
    want = _text(getattr(node, 'name', b''))
    return any(_controls_node(cb, want) and _interp_moves(cb.interpolator)
               for seq in _root_sequences(root)
               for cb in (getattr(seq, 'controlled_blocks', None) or []))


def node_transform_is_animated(data, node) -> bool:
    """True when some sequence MOVES `node`, ignoring keyless exporter stubs."""
    want = _text(getattr(node, 'name', b''))
    for block in data.blocks:
        if not isinstance(block, NifFormat.NiControllerSequence):
            continue
        for cb in (getattr(block, 'controlled_blocks', None) or []):
            if _controls_node(cb, want) and \
                    _has_transform_keys(getattr(cb, 'interpolator', None)):
                return True
    return False
