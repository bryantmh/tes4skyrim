"""Oblivion's base-map texture transform restated as Skyrim shader UVs.

Oblivion's MAX method scales about a center, uv' = S*(uv + T - C) + C, while a
Skyrim shader computes uv*scale + offset.  So scale = S and offset =
S*(T - C) + C: an animated scale also moves the offset, and an animated
translate is scaled.  The channel that is not animated holds its authored
value; rotation has no Skyrim equivalent and is not carried.

See: docs/commentary/asset_convert_shader.md#texture-transform
"""

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.nif.nif_flags import (TT_SCALE_U, TT_SCALE_V,
                                         TT_TRANSLATE_U, TT_TRANSLATE_V)

#: nif.xml TransformMethod "Max": Center * Scale * Rotation * Translate * Back.
TM_MAX = 1

#: Low byte of a Skyrim texture_clamp_mode; the high bytes carry other fields.
_CLAMP_MASK = 0xFF

#: A translate or scale operation's axis: 0 = U, 1 = V.
_AXIS_OF = {TT_TRANSLATE_U: 0, TT_TRANSLATE_V: 1, TT_SCALE_U: 0, TT_SCALE_V: 1}

#: The translate operation that carries a scale operation's offset.
OFFSET_OP_OF_SCALE = {TT_SCALE_U: TT_TRANSLATE_U, TT_SCALE_V: TT_TRANSLATE_V}

#: NiFloatInterpolator's "use the key data" pose.
_NO_POSE = -3.4028234663852886e+38


def base_map(props):
    """The base-texture TexDesc of the first NiTexturingProperty, or None."""
    for prop in props:
        if isinstance(prop, NifFormat.NiTexturingProperty) and prop.has_base_texture:
            return prop.base_texture
    return None


def _axes(desc):
    """((S, T, C, max_method) for U, same for V); identity without a transform."""
    if desc is None or not desc.has_texture_transform:
        return (1.0, 0.0, 0.0, False), (1.0, 0.0, 0.0, False)
    is_max = int(desc.transform_type) == TM_MAX
    return ((desc.tiling.u, desc.translation.u, desc.center_offset.u, is_max),
            (desc.tiling.v, desc.translation.v, desc.center_offset.v, is_max))


def _offset(scale, translate, center, is_max):
    """The Skyrim offset of one axis."""
    return scale * (translate - center) + center if is_max else translate


def apply_static_uv(shader, desc):
    """Stamp the map's authored UV scale, offset and clamp mode on `shader`."""
    u, v = _axes(desc)
    shader.uv_scale.u, shader.uv_scale.v = u[0], v[0]
    shader.uv_offset.u = _offset(*u)
    shader.uv_offset.v = _offset(*v)
    if desc is not None:
        shader.texture_clamp_mode = ((int(shader.texture_clamp_mode) & ~_CLAMP_MASK)
                                     | int(desc.clamp_mode))


def translate_affine(desc, op):
    """(a, b) mapping an animated translate onto the Skyrim offset, or None."""
    scale, _, center, is_max = _axes(desc)[_AXIS_OF[op]]
    if not is_max or scale == 1.0:
        return None
    return scale, center * (1.0 - scale)


def scale_offset_affine(desc, op):
    """(a, b) deriving the Skyrim offset from an animated scale, or None."""
    _, translate, center, is_max = _axes(desc)[_AXIS_OF[op]]
    if not is_max or translate == center:
        return None
    return translate - center, center


def affine_float_data(fdata, a, b):
    """A new NiFloatData holding a*value + b of every key in `fdata`.

    Each key's `arg` is set by hand: pyffi never re-reads the group's
    interpolation, so a fresh QUADRATIC key would be written in the LINEAR layout.
    """
    out = NifFormat.NiFloatData()
    src, dst = fdata.data, out.data
    dst.num_keys = src.num_keys
    dst.interpolation = src.interpolation
    dst.keys.update_size()
    for d, s in zip(dst.keys, src.keys):
        d.arg = src.interpolation
        d.time = s.time
        d.value = a * s.value + b
        d.forward = a * s.forward
        d.backward = a * s.backward
        d.tbc.t, d.tbc.b, d.tbc.c = s.tbc.t, s.tbc.b, s.tbc.c
    return out


def affine_interpolator(interp, a, b):
    """A new NiFloatInterpolator whose pose and keys are a*x + b of `interp`'s."""
    out = NifFormat.NiFloatInterpolator()
    pose = getattr(interp, 'float_value', _NO_POSE)
    out.float_value = pose if pose == _NO_POSE else a * pose + b
    data = getattr(interp, 'data', None)
    out.data = affine_float_data(data, a, b) if data is not None else None
    return out


def target_map(ctrl):
    """The base TexDesc of the NiTexturingProperty a controller drives, or None."""
    return base_map([getattr(ctrl, 'target', None)])


def expand_harvested(harvested, desc):
    """(operation, source, NiFloatData) per Skyrim curve for free-running transforms.

    `desc` is the caller's base TexDesc: a controller's target is a weak
    pointer, gone once the geometry pass drops the property.  A translate's
    keys move onto the offset; a scale also yields an offset curve unless that
    axis's translate is animated too.
    """
    ops = {src.operation for src, _ in harvested}
    out = []
    for src, fdata in harvested:
        op = src.operation
        if op in OFFSET_OP_OF_SCALE:
            out.append((op, src, fdata))
            ab = scale_offset_affine(desc, op)
            if ab is not None and OFFSET_OP_OF_SCALE[op] not in ops:
                out.append((OFFSET_OP_OF_SCALE[op], src, affine_float_data(fdata, *ab)))
            continue
        ab = translate_affine(desc, op) if op in _AXIS_OF else None
        out.append((op, src, fdata if ab is None else affine_float_data(fdata, *ab)))
    return out
