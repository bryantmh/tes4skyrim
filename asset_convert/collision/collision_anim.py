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
