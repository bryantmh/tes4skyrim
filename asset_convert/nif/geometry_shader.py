"""Turn one Oblivion geometry block into a shaded Skyrim NiTriShape.

`process_geometry` is the single entry point: it repairs the mesh data the
engine reads, then builds exactly one of three shaders over it -- the sky pass,
the effect (FX/flip-book) pass, or ordinary lighting. Which one is chosen comes
from AUTHORED indicators only, never a texture path: `_is_fx_surface` reads
Oblivion's own emissive-only lighting mode and its additive blend flag.

See: docs/commentary/asset_convert_nif.md#geometry-preparation
See: docs/commentary/asset_convert_shader.md#fx-shader-discriminator
"""

import struct

from asset_convert.nif.shaders import (ALPHA_BLEND_ENABLED, ALPHA_DST_ONE,
                                       ALPHA_DST_SHIFT, APPLY_HILIGHT2,
                                       DEFAULT_DIFFUSE_TEXTURE,
                                       DEFAULT_GLOSSINESS,
                                       default_normal_texture,
                                       LIGHTING_EMISSIVE_ONLY,
                                       REFRACTION_STRENGTH, SPEC_STRENGTH,
                                       apply_fx_soft_effect, apply_glow,
                                       apply_parallax,
                                       attach_tex_transform_ctrls,
                                       collect_shader_inputs, collect_uv_ctrls,
                                       has_spec_mask, plan_flipbook_atlas,
                                       resolve_normal_for)
from asset_convert.nif.nif_flags import NIF_FLAGS
from asset_convert.nif.tex_paths import rewrite_tex_path
from asset_convert.nif.tri_reconstruct import (UnreconstructibleGeometry,
                                               clear_match_groups,
                                               fix_missing_triangles)

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat


# ---------------------------------------------------------------------------
# Sky meshes
# ---------------------------------------------------------------------------

#: BSSkyShaderProperty sky object types, as the Skyrim sky pass numbers them.
SKY_TEXTURE, SKY_SUNGLARE, SKY_BASE = 0, 1, 2
SKY_CLOUDS, SKY_STARS, SKY_MOON_STARS_MASK = 3, 5, 7

#: Oblivion sky meshes by lowercase basename; AUTHORED, not derivable.
_SKY_MESH_TYPES = {
    'stars.nif':            SKY_STARS,
    'stars_oblivion.nif':   SKY_STARS,
    'sestars.nif':          SKY_STARS,
    'clouds.nif':           SKY_CLOUDS,
    'clouds_oblivion.nif':  SKY_CLOUDS,
    'atmosphere.nif':       SKY_BASE,
    'sky.nif':              SKY_BASE,
    'sunbeam01.nif':        SKY_SUNGLARE,
    'sunbeam02.nif':        SKY_SUNGLARE,
    'sunbeam03.nif':        SKY_SUNGLARE,
}


def sky_object_type_for(src_path):
    """The BSSkyShaderProperty sky object type for a mesh, else None.

    Only meshes living under a `sky/` directory are eligible: the basenames
    alone are generic enough to collide with ordinary clutter.
    See: docs/commentary/asset_convert_shader.md#sky-object-types
    """
    if not src_path:
        return None
    norm = str(src_path).replace('\\', '/').lower()
    parts = norm.rsplit('/', 2)
    if len(parts) < 2 or parts[-2] != 'sky':
        return None
    return _SKY_MESH_TYPES.get(parts[-1])


# ---------------------------------------------------------------------------
# Geometry data repair
# ---------------------------------------------------------------------------

def _extract_inline_tangents(ed, nv):
    """(binormals, tangents) from a 'Tangent space...' NiBinaryExtraData.

    The layout is nv*12 bytes of binormals then nv*12 of tangents; the pair is
    (None, None) when the block is short.
    """
    raw = bytes(ed.binary_data)
    if len(raw) < nv * 12 * 2:
        return None, None
    binormals = [struct.unpack_from('<fff', raw, i * 12) for i in range(nv)]
    tangents = [struct.unpack_from('<fff', raw, nv * 12 + i * 12)
                for i in range(nv)]
    return binormals, tangents


def _clamp_uv_sets(ts_data):
    """Reduce geometry data to the one UV set Skyrim reads; the count dropped.

    Set 0 is the diffuse UVs every shader samples, so the surplus is dropped
    rather than remapped.
    See: docs/commentary/asset_convert_nif.md#one-uv-set
    """
    n = int(getattr(ts_data, 'num_uv_sets', 0) or 0)
    if n <= 1:
        return 0
    keep = list(ts_data.uv_sets[0]) if len(ts_data.uv_sets) else []
    ts_data.num_uv_sets = 1
    ts_data.uv_sets.update_size()
    if keep and len(ts_data.uv_sets):
        for dst, src in zip(ts_data.uv_sets[0], keep):
            dst.u = src.u
            dst.v = src.v
    return n - 1


def _set_tangents(ts_data, bitangents, tangents):
    """Write inline tangents/bitangents into NiTriShapeData.

    PyFFI Array elements must be mutated in-place (no item assignment), and
    extra_vectors_flags must be set before update_size so the arrays are sized.
    See: docs/commentary/asset_convert_nif.md#geometry-preparation
    """
    nv = ts_data.num_vertices
    if len(tangents) != nv or len(bitangents) != nv:
        return
    ts_data.extra_vectors_flags = 16
    ts_data.tangents.update_size()
    ts_data.bitangents.update_size()
    for i in range(nv):
        tan, bit = ts_data.tangents[i], ts_data.bitangents[i]
        tan.x, tan.y, tan.z = tangents[i]
        bit.x, bit.y, bit.z = bitangents[i]


def _strip_dead_geometry_controllers(geom):
    """Remove the controllers Skyrim has no block type for, in place.

    NiUVController's curves must be harvested by collect_uv_ctrls BEFORE this
    runs, because this is what unlinks them.
    See: docs/commentary/asset_convert_shader.md#niuvcontroller-has-no-rtti
    """
    prev = None
    ctrl = getattr(geom, 'controller', None)
    while ctrl is not None:
        nxt = getattr(ctrl, 'next_controller', None)
        if isinstance(ctrl, (NifFormat.NiGeomMorpherController,
                             NifFormat.NiMaterialColorController,
                             NifFormat.NiUVController)):
            if prev is None:
                geom.controller = nxt
            else:
                prev.next_controller = nxt
        else:
            prev = ctrl
        ctrl = nxt


def _as_tri_shape(strips_or_shape):
    """(shape to write, source shape) for a NiTriStrips or NiTriShape.

    Strips convert to a shape only when nothing controls them: a controller
    still references the original node by block index, so converting a
    controlled strip breaks the NIF.
    """
    if not isinstance(strips_or_shape, NifFormat.NiTriStrips):
        return strips_or_shape, strips_or_shape
    if strips_or_shape.controller is not None:
        return strips_or_shape, strips_or_shape
    return (strips_or_shape.get_interchangeable_tri_shape(), strips_or_shape)


def _inline_tangents(src):
    """(bitangents, tangents) from NiBinaryExtraData, or (None, None)."""
    for ed in list(src.extra_data_list):
        if (isinstance(ed, NifFormat.NiBinaryExtraData)
                and ed.name == b'Tangent space (binormal & tangent vectors)'):
            return _extract_inline_tangents(ed, src.data.num_vertices)
    return None, None


def _prepare_geometry_data(ts, src, stats):
    """Repair the mesh data Skyrim reads, before any shader is built.

    Carries the AUTHORED hidden bit across, clears Oblivion extra data,
    rebuilds absent triangle arrays, and clamps the UV sets.
    See: docs/commentary/asset_convert_nif.md#geometry-preparation
    """
    ts.flags = NIF_FLAGS | (int(getattr(src, 'flags', 0)) & 0x0001)
    bitangents, tangents = _inline_tangents(src)

    ts.num_extra_data_list = 0
    ts.extra_data_list.update_size()
    if hasattr(ts.data, 'consistency_flags'):
        ts.data.consistency_flags = 0x4000
    fix_missing_triangles(ts.data)
    clear_match_groups(ts.data)
    if hasattr(ts.data, 'extra_vectors_flags'):
        ts.data.extra_vectors_flags = 0

    dropped_uv = _clamp_uv_sets(ts.data)
    if dropped_uv and stats is not None:
        stats['uv_sets_dropped'] = stats.get('uv_sets_dropped', 0) + dropped_uv
    if tangents is not None and hasattr(ts.data, 'tangents'):
        _set_tangents(ts.data, bitangents, tangents)


# ---------------------------------------------------------------------------
# Texture slots
# ---------------------------------------------------------------------------

def _normal_slot(diffuse, authored_normal, fix_textures, stats):
    """The normal map path for a shape whose diffuse is `diffuse`.

    Prefers the authored path, then what resolve_normal_for finds, and falls
    back to the shared flat normal rather than a dangling derived path.
    See: docs/commentary/asset_convert_shader.md#texture-slots-never-empty
    """
    base = diffuse.rsplit('.', 1)[0] if '.' in diffuse else diffuse
    if authored_normal:
        return (rewrite_tex_path(authored_normal) if fix_textures
                else authored_normal.decode('utf-8', errors='replace'))
    if stats is None:
        return base + '_n.dds'
    found = resolve_normal_for(diffuse, stats)
    if found is None:
        stats['spec_normal_defaulted'] = \
            stats.get('spec_normal_defaulted', 0) + 1
        return default_normal_texture()
    if found != base + '_n.dds':
        stats['spec_normal_from_base'] = \
            stats.get('spec_normal_from_base', 0) + 1
    return found


def _fill_texture_slots(tex_set, diffuse_path, authored_normal,
                        fix_textures, stats):
    """Write the diffuse and normal slots, neither ever left empty.

    Skyrim binds the diffuse unconditionally, so a null slot 0 is an access
    violation; slot 1 is null-checked but vanilla never ships it empty.
    See: docs/commentary/asset_convert_shader.md#texture-slots-never-empty
    """
    if not diffuse_path:
        tex_set.textures[0] = DEFAULT_DIFFUSE_TEXTURE
        if stats is not None:
            stats['untextured_diffuse_defaulted'] = (
                stats.get('untextured_diffuse_defaulted', 0) + 1)
        return
    diffuse = (rewrite_tex_path(diffuse_path) if fix_textures
               else diffuse_path.decode('utf-8', errors='replace'))
    tex_set.textures[0] = diffuse.encode('utf-8')
    tex_set.textures[1] = _normal_slot(
        diffuse, authored_normal, fix_textures, stats).encode('utf-8')


def _build_texture_set(diffuse_path, authored_normal, fix_textures, stats):
    """A filled 9-slot BSShaderTextureSet for one shape."""
    tex_set = NifFormat.BSShaderTextureSet()
    tex_set.num_textures = 9
    tex_set.textures.update_size()
    _fill_texture_slots(tex_set, diffuse_path, authored_normal,
                        fix_textures, stats)
    return tex_set


# ---------------------------------------------------------------------------
# Sky shader
# ---------------------------------------------------------------------------

def _build_sky_shader(ts, tex_set, diffuse_path, sky_type, stats):
    """Give sky geometry the dedicated sky shader and return the shape.

    The sky pass does its own blending and vanilla sky meshes carry no
    NiAlphaProperty, so the Oblivion one is dropped.
    See: docs/commentary/asset_convert_shader.md#sky-object-types
    """
    sky_shader = NifFormat.BSSkyShaderProperty()
    sky_shader.shader_flags_1.slsf_1_z_buffer_test = 1
    ssf2 = sky_shader.shader_flags_2
    ssf2.slsf_2_z_buffer_write = 1
    if getattr(ts.data, 'has_vertex_colors', False):
        ssf2.slsf_2_vertex_colors = 1
    sky_shader.uv_offset.u = 0.0
    sky_shader.uv_offset.v = 0.0
    sky_shader.uv_scale.u = 1.0
    sky_shader.uv_scale.v = 1.0
    sky_shader.source_texture = tex_set.textures[0] if diffuse_path else b''
    sky_shader.sky_object_type = sky_type
    ts.bs_properties[0] = sky_shader
    ts.bs_properties[1] = None
    if stats is not None:
        stats['sky_shaders'] = stats.get('sky_shaders', 0) + 1
    return ts


# ---------------------------------------------------------------------------
# Effect shader
# ---------------------------------------------------------------------------

def _is_additive(alpha_prop):
    """Whether the alpha property blends with dst=ONE, i.e. adds its color."""
    if alpha_prop is None:
        return False
    flags = int(alpha_prop.flags)
    return bool(flags & ALPHA_BLEND_ENABLED) and (
        ((flags >> ALPHA_DST_SHIFT) & 0xF) == ALPHA_DST_ONE)


def _is_fx_surface(alpha_prop, vertex_lighting_mode, shader_declared_unlit=False):
    """Whether a shape belongs on the Effect shader rather than Lighting.

    FO3/FNV state it outright with BSShaderNoLightingProperty; Oblivion has no
    such block and is read from its lighting mode and blend instead.
    See: docs/commentary/asset_convert_shader.md#fx-shader-discriminator
    """
    if shader_declared_unlit:
        return True
    if vertex_lighting_mode == LIGHTING_EMISSIVE_ONLY:
        return True
    return _is_additive(alpha_prop)


def _flip_frames(flip_ctrl, fix_textures):
    """Every resolvable texture path a NiFlipController steps through."""
    if flip_ctrl is None:
        return []
    return [(rewrite_tex_path(s.file_name) if fix_textures
             else s.file_name.decode('utf-8', errors='replace'))
            for s in flip_ctrl.sources if s is not None and s.file_name]


def _atlas_controller(eff_shader, flip_ctrl, atlas):
    """Drive a frame-strip atlas from a stepped U-Offset controller.

    Frame duration comes from NiFlipController.delta, else its cycle spread
    over the frames, else Oblivion's ~15fps default. Keys are CONST so the
    frames step rather than smear.
    See: docs/commentary/asset_convert_shader.md#flipbook-to-atlas
    """
    atlas_path, n_pad, n_real = atlas
    eff_shader.source_texture = atlas_path.encode('utf-8')
    eff_shader.uv_scale.u = 1.0 / n_pad
    delta = float(getattr(flip_ctrl, 'delta', 0.0) or 0.0)
    if delta <= 0.0:
        span = float(flip_ctrl.stop_time) - float(flip_ctrl.start_time)
        delta = span / n_real if span > 0 else 1.0 / 15.0

    fc = NifFormat.BSEffectShaderPropertyFloatController()
    fc.flags = 0x48
    fc.frequency = 1.0
    fc.phase = 0.0
    fc.start_time = 0.0
    fc.stop_time = n_real * delta
    fc.type_of_controlled_variable = 6
    fc.target = eff_shader
    interp = NifFormat.NiFloatInterpolator()
    interp.float_value = 0.0
    fdata = NifFormat.NiFloatData()
    kg = fdata.data
    kg.interpolation = 5
    kg.num_keys = n_real
    kg.keys.update_size()
    for k in range(n_real):
        kg.keys[k].time = k * delta
        kg.keys[k].value = k / float(n_pad)
    interp.data = fdata
    fc.interpolator = interp
    eff_shader.controller = fc


def _effect_emissive(eff_shader, si):
    """Carry the AUTHORED emissive onto an effect shader; white if none.

    The material alpha rides in the emissive alpha, which is what the engine
    multiplies the sampled texel by. Returns the authored RGB, else None.
    See: docs/commentary/asset_convert_shader.md#effect-shader-emissive
    """
    eff_shader.emissive_multiple = 1.0
    authored = (si.emissive_r > 0.0 or si.emissive_g > 0.0
                or si.emissive_b > 0.0)
    if authored:
        eff_shader.emissive_color.r = si.emissive_r
        eff_shader.emissive_color.g = si.emissive_g
        eff_shader.emissive_color.b = si.emissive_b
    else:
        eff_shader.emissive_color.r = 1.0
        eff_shader.emissive_color.g = 1.0
        eff_shader.emissive_color.b = 1.0
    eff_shader.emissive_color.a = si.material_alpha
    return ((si.emissive_r, si.emissive_g, si.emissive_b) if authored
            else None)


def _build_effect_shader(ts, tex_set, si, flip_ctrl, diffuse_path,
                         has_double_sided, fix_textures, stats):
    """The BSEffectShaderProperty for a flip-book or static FX surface.

    pyffi defaults UV scale to (0,0), which collapses every UV onto the
    texture's top-left texel and renders the quad invisible; vanilla is offset
    (0,0), scale (1,1).
    See: docs/commentary/asset_convert_shader.md#flipbook-to-atlas
    """
    frames = _flip_frames(flip_ctrl, fix_textures)
    atlas = plan_flipbook_atlas(frames, stats) if len(frames) >= 2 else None
    if frames:
        effective_path = frames[0].encode('utf-8')
    else:
        effective_path = tex_set.textures[0] if diffuse_path else b''

    eff_shader = NifFormat.BSEffectShaderProperty()
    eff_shader.uv_offset.u = 0.0
    eff_shader.uv_offset.v = 0.0
    eff_shader.uv_scale.u = 1.0
    eff_shader.uv_scale.v = 1.0
    esf1 = eff_shader.shader_flags_1
    esf1.slsf_1_own_emit = 1
    esf1.slsf_1_z_buffer_test = 1
    esf2 = eff_shader.shader_flags_2
    esf2.slsf_2_z_buffer_write = 0
    if has_double_sided:
        esf2.slsf_2_double_sided = 1
    if getattr(ts.data, 'has_vertex_colors', False):
        esf2.slsf_2_vertex_colors = 1
        esf1.slsf_1_vertex_alpha = 1
    eff_shader.source_texture = effective_path
    eff_shader.texture_clamp_mode = 3

    authored = _effect_emissive(eff_shader, si)
    if apply_fx_soft_effect(eff_shader, si.alpha_prop,
                            authored) and stats is not None:
        stats['fx_soft_effect'] = stats.get('fx_soft_effect', 0) + 1
    if atlas is not None:
        _atlas_controller(eff_shader, flip_ctrl, atlas)
    return eff_shader


# ---------------------------------------------------------------------------
# Lighting shader
# ---------------------------------------------------------------------------

def _set_material_defaults(shader):
    """Stamp vanilla's modal glossiness, specular color and strength.

    Oblivion's own glossiness is deliberately not carried across.
    See: docs/commentary/asset_convert_shader.md#shader-material-defaults
    """
    shader.glossiness = DEFAULT_GLOSSINESS
    shader.specular_color.r = 1.0
    shader.specular_color.g = 1.0
    shader.specular_color.b = 1.0
    shader.specular_strength = SPEC_STRENGTH


def _set_emissive(shader, sf1, r, g, b, animated):
    """Carry NiMaterialProperty's emissive color onto the Skyrim shader.

    Skyrim MULTIPLIES the color by emissive_multiple, so the multiple is
    stamped to 1.0 whenever the flag goes on. The flag is cleared on a shape
    with neither an emissive color nor an emissive animation.
    See: docs/commentary/asset_convert_shader.md#emissive-own-emit
    """
    if not (r > 0.0 or g > 0.0 or b > 0.0 or animated):
        sf1.slsf_1_own_emit = 0
        return
    sf1.slsf_1_own_emit = 1
    shader.emissive_color.r = r
    shader.emissive_color.g = g
    shader.emissive_color.b = b
    shader.emissive_multiple = 1.0


def _make_refractive(shader, sf1):
    """Convert the lighting preset into a refraction distortion surface.

    See: docs/commentary/asset_convert_shader.md#refraction-surfaces
    """
    sf1.slsf_1_refraction = 1
    sf1.slsf_1_fire_refraction = 1
    shader.refraction_strength = REFRACTION_STRENGTH


def _build_lighting_shader(ts, tex_set, si, has_double_sided):
    """The BSLightingShaderProperty preset every ordinary world surface gets.

    See: docs/commentary/asset_convert_shader.md#shader-material-defaults
    """
    shader = NifFormat.BSLightingShaderProperty()
    _set_material_defaults(shader)

    sf1 = shader.shader_flags_1
    sf1.slsf_1_specular = 1
    sf1.slsf_1_recieve_shadows = 1
    sf1.slsf_1_cast_shadows = 1
    sf1.slsf_1_own_emit = 1
    sf1.slsf_1_remappable_textures = 1
    sf1.slsf_1_z_buffer_test = 1

    sf2 = shader.shader_flags_2
    sf2.slsf_2_z_buffer_write = 1
    sf2.slsf_2_env_map_light_fade = 1
    if has_double_sided:
        sf2.slsf_2_double_sided = 1
    if ts.data.has_vertex_colors:
        sf2.slsf_2_vertex_colors = 1
    if si.is_refraction:
        _make_refractive(shader, sf1)

    shader.texture_clamp_mode = 3
    shader.uv_scale.u = 1.0
    shader.uv_scale.v = 1.0
    shader.texture_set = tex_set

    _set_emissive(shader, sf1, si.emissive_r, si.emissive_g, si.emissive_b,
                  si.emissive_animated)
    shader.alpha = si.material_alpha
    return shader


def _apply_glow_or_parallax(ts, shader, tex_set, si, stats):
    """Bind the glow map, else the parallax height map; they are exclusive.

    See: docs/commentary/asset_convert_shader.md#glow-shader
    """
    if not apply_glow(shader, tex_set, si.glow_path, stats):
        apply_parallax(ts, shader, tex_set, si.tex_apply_mode, stats)
    elif si.tex_apply_mode == APPLY_HILIGHT2 and stats is not None:
        stats['parallax_skipped_glow'] = \
            stats.get('parallax_skipped_glow', 0) + 1


# ---------------------------------------------------------------------------
# Alpha and overlay bookkeeping
# ---------------------------------------------------------------------------

def _record_overlay(tex_set, tex_apply_mode, stats, norm_tex_ref):
    """Note this shape's diffuse as an overlay source for the texture pass.

    See: docs/commentary/asset_convert_shader.md#detail-overlay-diffuses
    """
    if tex_apply_mode != APPLY_HILIGHT2 or stats is None:
        return
    key = norm_tex_ref(tex_set.textures[0])
    if key:
        stats.setdefault('overlay_diffuses', set()).add(key)


def _carry_alpha(ts, tex_set, si, stats):
    """Carry the Oblivion NiAlphaProperty across, unless HILIGHT2 drops it.

    See: docs/commentary/asset_convert_shader.md#hilight2-alpha-dropped
    """
    alpha_prop = si.alpha_prop
    if alpha_prop is None:
        return
    if si.tex_apply_mode == APPLY_HILIGHT2 and (int(alpha_prop.flags) & 0x0001):
        stats['hilight2_alpha_dropped'] = \
            stats.get('hilight2_alpha_dropped', 0) + 1
        return
    ts.bs_properties[1] = alpha_prop
    diffuse = tex_set.textures[0] if tex_set.textures else None
    if diffuse and stats is not None:
        if isinstance(diffuse, bytes):
            diffuse = diffuse.decode('utf-8', errors='replace')
        stats.setdefault('_alpha_opacity_diffuse', set()).add(
            diffuse.replace('/', '\\').lower())


def _mark_skinned(ts):
    """Set slsf_1_skinned when the shape carries a skin instance."""
    if getattr(ts, 'skin_instance', None) is None:
        return
    active = ts.bs_properties[0]
    if isinstance(active, NifFormat.BSLightingShaderProperty):
        active.shader_flags_1.slsf_1_skinned = 1


def process_geometry(strips_or_shape, fix_textures, stats=None, sky_type=None,
                     norm_tex_ref=None):
    """Convert a NiTriStrips or NiTriShape into a ready Skyrim NiTriShape.

    Returns the shape, which is a NEW object when the input was uncontrolled
    strips. UV curves are harvested before the controller strip, because
    NiUVController lives on the chain that strip removes.
    See: docs/commentary/asset_convert_nif.md#geometry-preparation
    """
    if strips_or_shape.data is None:
        raise UnreconstructibleGeometry('shape carries a null data reference')
    uv_transforms = collect_uv_ctrls(strips_or_shape)
    _strip_dead_geometry_controllers(strips_or_shape)
    ts, src = _as_tri_shape(strips_or_shape)
    _prepare_geometry_data(ts, src, stats)

    si = collect_shader_inputs(src, uv_transforms)
    ts.num_properties = 0
    ts.properties.update_size()

    tex_set = _build_texture_set(si.diffuse_path, si.authored_normal,
                                 fix_textures, stats)
    has_spec_mask(tex_set.textures[1], stats)
    if sky_type is not None:
        return _build_sky_shader(ts, tex_set, si.diffuse_path, sky_type, stats)

    shader = _build_lighting_shader(ts, tex_set, si, si.has_double_sided)
    is_static_fx = (si.flip_ctrl is None and si.diffuse_path
                    and _is_fx_surface(si.alpha_prop, si.vertex_lighting_mode,
                                       si.shader_declared_unlit))
    if si.flip_ctrl is not None or is_static_fx:
        ts.bs_properties[0] = _build_effect_shader(
            ts, tex_set, si, si.flip_ctrl, si.diffuse_path,
            si.has_double_sided, fix_textures, stats)
    else:
        _apply_glow_or_parallax(ts, shader, tex_set, si, stats)
        ts.bs_properties[0] = shader

    if norm_tex_ref is not None:
        _record_overlay(tex_set, si.tex_apply_mode, stats, norm_tex_ref)
    _carry_alpha(ts, tex_set, si, stats)
    attach_tex_transform_ctrls(ts.bs_properties[0], si.tex_transforms)
    _mark_skinned(ts)

    if hasattr(ts, 'data') and ts.data is not None:
        ts.data.unknown_int_2 = 0
    return ts
