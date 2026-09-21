"""Oblivion texture/material properties -> Skyrim shader properties.

Resolves every texture slot a mesh names, decides which Skyrim shader
(Lighting or Effect) a shape needs, and builds it: glow maps, parallax, spec
masks, flip-book atlases and the UV-transform controllers that drive them.

See: docs/commentary/asset_convert_shader.md
"""

from asset_convert.game_paths import current_namespace
import os

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.nif import flipbook
from asset_convert.nif.nif_flags import (TT_SCALE_U, TT_SCALE_V,
                                         TT_TRANSLATE_U,
                                         TT_TRANSLATE_V)
from asset_convert.nif.sequences import (MATERIAL_COLOR_EMISSIVE,
                                         TEX_TRANSFORM_VARS)
from asset_convert.nif.tex_paths import (bs_pp_texture_slots,
                                         rewrite_tex_path)
from asset_convert.sources import base_plugins as _base_plugins
from asset_convert.texture import landscape_normals, parallax, spec_mask

#: Written by convert.py, read by the census tools.
BASE_PLUGINS_FILE = _base_plugins.FILE_NAME

#: apply_mode marking the diffuse alpha a height field. See: docs/commentary/asset_convert_shader.md#ob-enums
APPLY_HILIGHT2 = 4

#: lighting_mode declaring an unlit FX surface. See: docs/commentary/asset_convert_shader.md#ob-enums
LIGHTING_EMISSIVE_ONLY = 0

#: NiAlphaProperty.flags bit 0, the alpha-blend discriminator for the soft-particle fade.
ALPHA_BLEND_ENABLED = 0x0001

#: NiAlphaProperty.flags bits 5-8, the destination blend factor.
ALPHA_DST_SHIFT = 5

#: Destination blend GL_ONE, i.e. additive. See: docs/commentary/asset_convert_shader.md#fx-shader-discriminator
ALPHA_DST_ONE = 0

#: Depth over which a soft FX quad fades. See: docs/commentary/asset_convert_shader.md#soft-particle-fade
_SOFT_FALLOFF_DEPTH = 100.0

#: Emissive at/above which FX is self-lit. See: docs/commentary/asset_convert_shader.md#self-lit-flames-are-not-faded
_FX_SELF_LIT_EMISSIVE = 0.999

#: Boost for a self-lit FX surface. See: docs/commentary/asset_convert_shader.md#flame-brightness-is-authored
_FX_SELF_LIT_MULTIPLE = 1.5

#: Diffuse for a shape whose source names none. See: docs/commentary/asset_convert_shader.md#ob-enums
DEFAULT_DIFFUSE_TEXTURE = b'Textures\\white.dds'

#: Vanilla's modal type-0 glossiness. See: docs/commentary/asset_convert_shader.md#vanilla-census-80-real-tail
DEFAULT_GLOSSINESS = 80.0

#: Uniform for every shape. See: docs/commentary/asset_convert_shader.md#spec-strength-uniform
SPEC_STRENGTH = 1.0

#: Slot and shader type for a glow map. See: docs/commentary/asset_convert_shader.md#glow-shader
GLOW_SLOT = 2
SHADER_TYPE_GLOWMAP = 2

#: Oblivion's distant-LOD tier mesh suffixes; `lod_far_gen` derives the coarser two.
_LOD_TIER_SUFFIXES = ('_far', '_far8', '_far16')

#: NiUVData.uv_groups: the same curves a NiTextureTransformController carries.
_UV_GROUP_OPS = (TT_TRANSLATE_U, TT_TRANSLATE_V, TT_SCALE_U, TT_SCALE_V)

#: KeyType LINEAR_KEY. See: docs/commentary/asset_convert_nif.md#morrowind-quadratic-uv-keys
_LINEAR_KEY = 1

#: The vanilla "read the curve, not the constant" NiFloatInterpolator sentinel.
_USE_DATA_SENTINEL = -3.4028234663852886e+38

#: Active | Compute Scaled Time. See: docs/commentary/asset_convert_shader.md#shader-float-controller-flags
_CTRL_FLAGS_ACTIVE_SCALED = 0x48

#: NiTimeController cycle bits, preserved so CLAMP/REVERSE loops survive.
_CTRL_FLAGS_CYCLE_MASK = 0x06

def default_normal_texture() -> str:
    """Slot-1 path for a shape with no resolvable normal map."""
    return 'Textures\\' + landscape_normals.default_normal_rel(
    ).split('\\', 1)[1]

#: Alpha verdicts by absolute path. See: docs/commentary/asset_convert_shader.md#texture-classification-caches
_PARALLAX_ALPHA_CACHE = {}

#: Mask verdicts by absolute path. See: docs/commentary/asset_convert_shader.md#texture-classification-caches
_SPEC_MASK_CACHE = {}


def resolve_source_texture(tex_rel, src_nif_path, fallback_roots=()):
    """The extracted source file behind a rewritten texture path, or None.

    Maps textures\\tes4\\fire\\x\\y.dds back to
    export/<esm>/textures/fire/x/y.dds beside the source mesh tree, falling
    back to the master's roots.
    See: docs/commentary/asset_convert_shader.md#texture-fallback-roots
    """
    if not src_nif_path:
        return None
    norm = src_nif_path.replace('/', os.sep).replace('\\', os.sep)
    key = os.sep + 'meshes' + os.sep
    i = norm.lower().rfind(key)
    if i < 0:
        return None
    tex_root = norm[:i] + os.sep + 'textures' + os.sep
    rel = tex_rel.replace('/', '\\')
    low = rel.lower()
    for prefix in ('textures\\' + current_namespace() + '\\',
                   'textures\\'):
        if low.startswith(prefix):
            rel = rel[len(prefix):]
            break
    cand = tex_root + rel.replace('\\', os.sep)
    if os.path.isfile(cand):
        return cand
    for root in (fallback_roots or ()):
        cand = os.path.join(root, rel.replace('\\', os.sep))
        if os.path.isfile(cand):
            return cand
    return None


def master_texture_roots(mesh_dir):
    """Texture trees to fall back on, in order, for the mod at `mesh_dir`.

    See: docs/commentary/asset_convert_shader.md#texture-fallback-roots
    """
    mesh_dir = str(mesh_dir).replace('/', os.sep)
    key = os.sep + 'meshes'
    i = mesh_dir.lower().rfind(key)
    if i < 0:
        return ()
    return _base_plugins.subdirs(mesh_dir[:i], 'textures')


def apply_fx_soft_effect(eff_shader, alpha_prop, emissive_rgb=None):
    """Enable the soft-particle depth fade on a blended FX shader.

    Keyed on the source's own NiAlphaProperty; self-lit surfaces are excluded
    and take the emissive boost instead.
    See: docs/commentary/asset_convert_shader.md#soft-particle-fade
    """
    if alpha_prop is None:
        return False
    if not (int(alpha_prop.flags) & ALPHA_BLEND_ENABLED):
        return False
    if emissive_rgb is not None and all(
            c >= _FX_SELF_LIT_EMISSIVE for c in emissive_rgb):
        eff_shader.shader_flags_1.slsf_1_soft_effect = 0
        eff_shader.emissive_multiple = _FX_SELF_LIT_MULTIPLE
        return False
    eff_shader.shader_flags_1.slsf_1_soft_effect = 1
    eff_shader.soft_falloff_depth = _SOFT_FALLOFF_DEPTH
    return True


def collect_tex_transform_ctrls(props):
    """Harvest animated NiTextureTransformControllers from Oblivion properties.

    (source controller, NiFloatData) pairs for the base-texture slot only; the
    caller reads operation/timing off the source.
    See: docs/commentary/asset_convert_shader.md#shader-float-controller-flags
    """
    out = []
    for prop in props:
        if not isinstance(prop, NifFormat.NiTexturingProperty):
            continue
        ctrl = prop.controller
        while ctrl is not None:
            if (isinstance(ctrl, NifFormat.NiTextureTransformController) and
                    ctrl.operation in TEX_TRANSFORM_VARS and
                    getattr(ctrl, 'texture_slot', 0) == 0):
                interp = getattr(ctrl, 'interpolator', None)
                data = getattr(interp, 'data', None) if interp is not None else None
                keys = getattr(data, 'data', None) if data is not None else None
                if keys is not None and keys.num_keys >= 2:
                    out.append((ctrl, data))
            ctrl = getattr(ctrl, 'next_controller', None)
    return out


class _SyntheticTexTransform:
    """Adapter making a NiUVController group look like a NiTextureTransformController.

    attach_tex_transform_ctrls only reads operation/flags/frequency/phase/
    start_time/stop_time off the source, so a small stand-in is enough and
    avoids duplicating the emit logic.
    """

    def __init__(self, src, operation):
        """Copy the timing fields the emitter reads off a real controller."""
        self.operation = operation
        self.flags = int(getattr(src, 'flags', 0))
        self.frequency = getattr(src, 'frequency', 1.0) or 1.0
        self.phase = getattr(src, 'phase', 0.0)
        self.start_time = src.start_time
        self.stop_time = src.stop_time


def _uv_group_to_float_data(group):
    """One NiUVData key group copied into a standalone LINEAR NiFloatData.

    pyffi fixes a KeyGroup's element layout when `update_size` allocates and
    never re-reads `interpolation`, so a QUADRATIC group is declared 24 bytes
    per key and always written as 8; the engine then reads past the block and
    rejects the file.
    See: docs/commentary/asset_convert_nif.md#morrowind-quadratic-uv-keys
    """
    fdata = NifFormat.NiFloatData()
    fdata.data.num_keys = group.num_keys
    fdata.data.interpolation = _LINEAR_KEY
    fdata.data.keys.update_size()
    for dst, src_key in zip(fdata.data.keys, group.keys):
        dst.time = src_key.time
        dst.value = src_key.value
    return fdata


def _uv_ctrl_curves(ctrl):
    """(synthetic transform, NiFloatData) per animated group on one NiUVController."""
    data = getattr(ctrl, 'data', None)
    groups = list(getattr(data, 'uv_groups', []) or []) if data else []
    out = []
    for gi, group in enumerate(groups[:len(_UV_GROUP_OPS)]):
        if group is None or group.num_keys < 2:
            continue
        out.append((_SyntheticTexTransform(ctrl, _UV_GROUP_OPS[gi]),
                    _uv_group_to_float_data(group)))
    return out


def collect_uv_ctrls(geom):
    """Harvest animated NiUVControllers from a geometry node's controller chain.

    (source, NiFloatData) tuples for attach_tex_transform_ctrls. Must run
    BEFORE the pass that strips the dead controller.
    See: docs/commentary/asset_convert_shader.md#niuvcontroller-has-no-rtti
    """
    out = []
    ctrl = getattr(geom, 'controller', None)
    while ctrl is not None:
        nxt = getattr(ctrl, 'next_controller', None)
        if isinstance(ctrl, NifFormat.NiUVController):
            out.extend(_uv_ctrl_curves(ctrl))
        ctrl = nxt
    return out


def _build_float_ctrl(ctrl_type, src_ctrl, fdata, shader, is_effect):
    """One BS*ShaderPropertyFloatController re-emitting a source UV curve."""
    new = ctrl_type()
    new.flags = _CTRL_FLAGS_ACTIVE_SCALED | (
        int(getattr(src_ctrl, 'flags', 0)) & _CTRL_FLAGS_CYCLE_MASK)
    new.frequency = getattr(src_ctrl, 'frequency', 1.0) or 1.0
    new.phase = getattr(src_ctrl, 'phase', 0.0)
    new.start_time = src_ctrl.start_time
    new.stop_time = src_ctrl.stop_time
    new.target = shader
    new.type_of_controlled_variable = \
        TEX_TRANSFORM_VARS[src_ctrl.operation][1 if is_effect else 0]

    interp = NifFormat.NiFloatInterpolator()
    interp.float_value = _USE_DATA_SENTINEL
    interp.data = fdata
    new.interpolator = interp
    return new


def attach_tex_transform_ctrls(shader, harvested):
    """Re-emit harvested texture transforms as a Skyrim shader controller chain.

    Anything already on the shader is kept at the end of the chain.
    See: docs/commentary/asset_convert_shader.md#shader-float-controller-flags
    """
    if not harvested:
        return
    is_effect = isinstance(shader, NifFormat.BSEffectShaderProperty)
    ctrl_type = (NifFormat.BSEffectShaderPropertyFloatController if is_effect
                 else NifFormat.BSLightingShaderPropertyFloatController)

    head = None
    tail = None
    for src_ctrl, fdata in harvested:
        new = _build_float_ctrl(ctrl_type, src_ctrl, fdata, shader, is_effect)
        if head is None:
            head = new
        else:
            tail.next_controller = new
        tail = new

    tail.next_controller = shader.controller
    shader.controller = head


def _resolve_flipbook_frames(frame_rels, stats):
    """The frames' absolute paths, or None when any cannot be resolved/decoded.

    Every frame must share the first one's dimensions.
    """
    src_nif = stats.get('_src_path', '')
    files = []
    dims = None
    for rel in frame_rels:
        f = resolve_source_texture(rel, src_nif, stats.get('_tex_fallback', ()))
        if f is None:
            return None
        info = flipbook.probe_dds(f)
        if info is None:
            return None
        if dims is None:
            dims = info[:2]
        elif info[:2] != dims:
            return None
        files.append(f)
    return files


def plan_flipbook_atlas(frame_rels, stats):
    """Register an atlas-build job for a NiFlipController's frames.

    (atlas_rel_path, n_padded, n_real), or None when the frames cannot be
    resolved -- the caller then falls back to a static first frame. convert_nif
    executes the job, because only it knows the output tree.
    See: docs/commentary/asset_convert_shader.md#flipbook-to-atlas
    """
    if stats is None or len(frame_rels) < 2:
        return None
    files = _resolve_flipbook_frames(frame_rels, stats)
    if files is None:
        return None
    first = frame_rels[0].replace('/', '\\')
    atlas_rel = first.rsplit('\\', 1)[0].rstrip('\\') + '_flip.dds'
    jobs = stats.setdefault('_flipbook_atlases', {})
    jobs[atlas_rel.lower()] = {'atlas_rel': atlas_rel, 'files': files}
    return atlas_rel, flipbook.next_pow2(len(files)), len(files)


def _classify_alpha(src):
    """The cached parallax alpha verdict for a resolved texture path."""
    key = src.lower()
    info = _PARALLAX_ALPHA_CACHE.get(key)
    if info is None:
        try:
            with open(src, 'rb') as f:
                raw = f.read()
        except OSError:
            raw = b''
        info = parallax.classify_alpha(raw)
        _PARALLAX_ALPHA_CACHE[key] = info
    return info


def _plan_parallax(diffuse_rel, stats):
    """The height map's texture path for this diffuse, or None.

    The mesh flag (checked by the caller) is the AUTHORED intent; this is the
    measurement of whether there is anything to carry. Registers a build job in
    stats, which convert_nif executes.
    See: docs/commentary/asset_convert_shader.md#texture-classification-caches
    """
    src = resolve_source_texture(diffuse_rel, stats.get('_src_path', ''),
                                 stats.get('_tex_fallback', ()))
    if src is None:
        stats['parallax_texture_unresolved'] = \
            stats.get('parallax_texture_unresolved', 0) + 1
        return None
    info = _classify_alpha(src)
    if not info.usable:
        stats[f'parallax_skipped_{info.kind}'] = \
            stats.get(f'parallax_skipped_{info.kind}', 0) + 1
        return None

    rel = parallax.height_path(diffuse_rel)
    jobs = stats.setdefault('_parallax_maps', {})
    jobs[rel.lower()] = {'height_rel': rel, 'src': src}
    return rel


def _normal_exists(normal_rel, stats):
    """Is there a real source file behind this DERIVED `_n` path?

    See: docs/commentary/asset_convert_shader.md#normal-base-name-fallback
    """
    if not normal_rel:
        return False
    return resolve_source_texture(normal_rel, stats.get('_src_path', ''),
                                  stats.get('_tex_fallback', ())) is not None


def _resolve_map_for(diffuse, suffix, stats):
    """The best real `<diffuse base><suffix>.dds` for a diffuse, or None.

    Generic in the suffix: Oblivion's base-name rule applies to every derived
    map, glow included.
    See: docs/commentary/asset_convert_shader.md#normal-base-name-fallback
    """
    base = diffuse.rsplit('.', 1)[0] if '.' in diffuse else diffuse
    own = base + suffix + '.dds'
    if _normal_exists(own, stats):
        return own
    head, sep, _tail = base.rpartition('_')
    if sep and head:
        shared = head + suffix + '.dds'
        if _normal_exists(shared, stats):
            return shared
    return None


def resolve_normal_for(diffuse, stats):
    """The best real normal map for a diffuse, or None.

    See: docs/commentary/asset_convert_shader.md#normal-base-name-fallback
    """
    return _resolve_map_for(diffuse, '_n', stats)


def has_spec_mask(normal_rel, stats):
    """True when slot 1's alpha is a usable specular mask.

    Counts its verdict into `stats` per category.
    See: docs/commentary/asset_convert_shader.md#texture-classification-caches
    """
    if not normal_rel or stats is None:
        return False
    rel = normal_rel.decode('utf-8', 'replace') \
        if isinstance(normal_rel, bytes) else normal_rel
    src = resolve_source_texture(rel, stats.get('_src_path', ''),
                                 stats.get('_tex_fallback', ()))
    if src is None:
        stats['spec_missing_normal'] = stats.get('spec_missing_normal', 0) + 1
        return False
    key = src.lower()
    kind = _SPEC_MASK_CACHE.get(key)
    if kind is None:
        try:
            with open(src, 'rb') as f:
                raw = f.read()
        except OSError:
            raw = b''
        kind = spec_mask.classify_bytes(raw)
        _SPEC_MASK_CACHE[key] = kind
    stats[f'spec_{kind}'] = stats.get(f'spec_{kind}', 0) + 1
    return kind == 'mask'


def _is_lod_tier_mesh(src_path) -> bool:
    """True for a `_far` / `_far8` / `_far16` distant-LOD tier mesh."""
    stem = os.path.splitext(os.path.basename(str(src_path or '')))[0].lower()
    return stem.endswith(_LOD_TIER_SUFFIXES)


def _glow_map_for(glow_path, tex_set, stats):
    """The glow texture for a shape: the authored path, else the derived one.

    Counts a derivation, and counts an authored path that resolves nowhere.
    """
    if glow_path:
        named = rewrite_tex_path(glow_path)
        if _normal_exists(named, stats):
            return named
    diffuse = tex_set.textures[0]
    if isinstance(diffuse, bytes):
        diffuse = diffuse.decode('utf-8', errors='replace')
    rel = None
    if diffuse and diffuse != DEFAULT_DIFFUSE_TEXTURE.decode('utf-8'):
        rel = _resolve_map_for(diffuse, '_g', stats)
    if rel is not None and not glow_path:
        stats['glow_derived'] = stats.get('glow_derived', 0) + 1
    if rel is None and glow_path:
        stats['glow_unresolved'] = stats.get('glow_unresolved', 0) + 1
    return rel


def apply_glow(shader, tex_set, glow_path, stats):
    """Give a shape Skyrim's GLOW shader when Oblivion had a glow map for it.

    True when the shape became a glow shape, which BLOCKS parallax.
    See: docs/commentary/asset_convert_shader.md#glow-shader
    """
    if stats is None:
        return False
    rel = _glow_map_for(glow_path, tex_set, stats)
    if rel is None:
        return False

    tex_set.textures[GLOW_SLOT] = rel.encode('utf-8')
    shader.skyrim_shader_type = SHADER_TYPE_GLOWMAP
    shader.shader_flags_2.slsf_2_glow_map = 1
    shader.shader_flags_1.slsf_1_environment_mapping = 0
    shader.shader_flags_1.slsf_1_own_emit = 1
    emissive = shader.emissive_color
    if max(emissive.r, emissive.g, emissive.b) <= 0.0:
        emissive.r = emissive.g = emissive.b = 1.0
        stats['glow_emissive_defaulted'] = \
            stats.get('glow_emissive_defaulted', 0) + 1
    if float(shader.emissive_multiple) <= 0.0:
        shader.emissive_multiple = 1.0
    stats['glow_applied'] = stats.get('glow_applied', 0) + 1
    return True


def _add_neutral_vertex_colors(data, stats):
    """Give a shape the all-white vertex colors the heightmap shader needs."""
    if getattr(data, 'has_vertex_colors', False):
        return
    data.has_vertex_colors = True
    data.vertex_colors.update_size()
    for c in data.vertex_colors:
        c.r = c.g = c.b = c.a = 1.0
    stats['parallax_vertex_colors_added'] = \
        stats.get('parallax_vertex_colors_added', 0) + 1


def _parallax_diffuse(ts, tex_set, stats):
    """(geometry data, diffuse path) for a parallax candidate, or None.

    None whenever the shape cannot carry a height map at all -- no geometry, no
    diffuse, or a distant-LOD tier mesh.
    """
    if _is_lod_tier_mesh(stats.get('_src_path', '')):
        stats['parallax_skipped_lod_tier'] = \
            stats.get('parallax_skipped_lod_tier', 0) + 1
        return None
    data = getattr(ts, 'data', None)
    diffuse_rel = tex_set.textures[0]
    if data is None or not diffuse_rel:
        return None
    if isinstance(diffuse_rel, bytes):
        diffuse_rel = diffuse_rel.decode('utf-8', errors='replace')
    return data, diffuse_rel


def apply_parallax(ts, shader, tex_set, tex_apply_mode, stats):
    """Rebuild a flagged shape as a Skyrim parallax shape.

    Never runs by default: verified in game, a correctly built parallax shape
    SWIMS under vanilla SSE, so the output needs Community Shaders or ENB. The
    converter cannot detect that, hence the opt-in.
    See: docs/commentary/asset_convert_shader.md#parallax-not-on-lod-tiers
    """
    if (stats is None or not stats.get('_parallax')
            or tex_apply_mode != APPLY_HILIGHT2):
        return
    found = _parallax_diffuse(ts, tex_set, stats)
    if found is None:
        return
    data, diffuse_rel = found
    height_rel = _plan_parallax(diffuse_rel, stats)
    if height_rel is None:
        return

    tex_set.textures[parallax.HEIGHT_SLOT] = height_rel.encode('utf-8')
    shader.skyrim_shader_type = parallax.SHADER_TYPE_HEIGHTMAP
    shader.shader_flags_1.slsf_1_parallax = 1
    shader.shader_flags_1.slsf_1_environment_mapping = 0
    shader.shader_flags_2.slsf_2_glow_map = 0

    _add_neutral_vertex_colors(data, stats)
    shader.shader_flags_2.slsf_2_vertex_colors = 1
    stats['parallax_shapes'] = stats.get('parallax_shapes', 0) + 1


def base_texture_path(prop):
    """The base (diffuse) texture path on a NiTexturingProperty, or empty."""
    if prop.has_base_texture and prop.base_texture.source:
        return prop.base_texture.source.file_name
    return b''


def _glow_texture_path(prop):
    """The glow path a NiTexturingProperty NAMES, or empty.

    Authored data taken verbatim: Oblivion names its glow map, unlike the
    normal, which it derives.
    """
    if not getattr(prop, 'has_glow_texture', False):
        return b''
    source = getattr(prop.glow_texture, 'source', None)
    return source.file_name if source is not None and source.file_name else b''


def find_flip_controller(prop):
    """The NiFlipController on a property, or None.

    See: docs/commentary/asset_convert_shader.md#flipbook-to-atlas
    """
    ctrl = prop.controller
    while ctrl is not None:
        if isinstance(ctrl, NifFormat.NiFlipController):
            return ctrl
        ctrl = getattr(ctrl, 'next_controller', None)
    return None


def _has_emissive_animation(prop):
    """True when a NiMaterialColorController animates the emissive channel.

    The static emissive is then only the curve's starting point -- frequently
    (0,0,0) -- so it must not be read as "this surface does not glow".
    """
    ctrl = prop.controller
    while ctrl is not None:
        if (isinstance(ctrl, NifFormat.NiMaterialColorController) and
                int(getattr(ctrl, 'target_color', -1)) == MATERIAL_COLOR_EMISSIVE):
            return True
        ctrl = getattr(ctrl, 'next_controller', None)
    return False


class _ShaderInputs:
    """The shader values harvested from one shape's old NIF properties.

    Defaults describe a shape with no properties at all: no textures, opaque,
    lit (vertex_lighting_mode 1), and no animation.
    """

    __slots__ = ('diffuse_path', 'glow_path', 'authored_normal', 'has_double_sided',
                 'alpha_prop', 'tex_apply_mode', 'emissive_r', 'emissive_g',
                 'emissive_b', 'material_alpha', 'emissive_animated',
                 'vertex_lighting_mode', 'flip_ctrl', 'tex_transforms',
                 'shader_declared_unlit', 'is_refraction')

    def __init__(self, tex_transforms):
        """Start from the no-properties defaults, carrying the UV transforms in."""
        self.diffuse_path = b''
        self.glow_path = b''
        self.authored_normal = b''
        self.has_double_sided = False
        self.alpha_prop = None
        self.tex_apply_mode = None
        self.emissive_r = 0.0
        self.emissive_g = 0.0
        self.emissive_b = 0.0
        self.material_alpha = 1.0
        self.emissive_animated = False
        self.vertex_lighting_mode = 1
        self.flip_ctrl = None
        self.tex_transforms = tex_transforms
        self.shader_declared_unlit = False
        self.is_refraction = False


def _harvest_texturing(prop, out):
    """Fold one NiTexturingProperty's paths, apply mode and flip controller in."""
    out.diffuse_path = base_texture_path(prop) or out.diffuse_path
    out.glow_path = _glow_texture_path(prop) or out.glow_path
    out.tex_apply_mode = int(prop.apply_mode)
    out.flip_ctrl = find_flip_controller(prop) or out.flip_ctrl


#: Bethesda's material name on every TES4 refraction surface.
REFRACTION_MATERIAL = 'refractf'


def _harvest_material(prop, out):
    """Fold one NiMaterialProperty's emissive, alpha and refraction flag in."""
    color = prop.emissive_color
    out.emissive_r, out.emissive_g, out.emissive_b = color.r, color.g, color.b
    out.material_alpha = prop.alpha
    out.emissive_animated = _has_emissive_animation(prop)
    name = bytes(prop.name).rstrip(b'\x00').decode('latin-1', 'replace')
    out.is_refraction = name.lower() == REFRACTION_MATERIAL


def collect_shader_inputs(src, uv_transforms):
    """Harvest shader inputs from a shape's Oblivion / FO3 properties.

    Oblivion keeps them on NiTexturingProperty and friends; FO3/FNV keep their
    texture paths in a BSShaderTextureSet on BSShaderPPLightingProperty, or as
    the single File Name of TallGrassShaderProperty and
    BSShaderNoLightingProperty. All are read here.

    See: docs/commentary/asset_convert_nif.md#fo3fnv-shader-properties
    """
    out = _ShaderInputs(collect_tex_transform_ctrls(src.properties) + uv_transforms)

    for prop in src.properties:
        if isinstance(prop, NifFormat.NiTexturingProperty):
            _harvest_texturing(prop, out)
            continue
        if isinstance(prop, NifFormat.NiMaterialProperty):
            _harvest_material(prop, out)
            continue
        if isinstance(prop, NifFormat.BSShaderPPLightingProperty):
            diffuse, normal, glow = bs_pp_texture_slots(prop)
            out.diffuse_path = diffuse
            out.authored_normal = normal
            out.glow_path = glow or out.glow_path
            continue
        if isinstance(prop, NifFormat.TallGrassShaderProperty):
            out.diffuse_path = prop.file_name or out.diffuse_path
            continue
        if isinstance(prop, NifFormat.BSShaderNoLightingProperty):
            out.diffuse_path = prop.file_name or out.diffuse_path
            out.shader_declared_unlit = True
            continue
        if isinstance(prop, NifFormat.NiVertexColorProperty):
            out.vertex_lighting_mode = int(prop.lighting_mode)
        elif isinstance(prop, NifFormat.NiStencilProperty):
            out.has_double_sided = True
        elif isinstance(prop, NifFormat.NiAlphaProperty):
            out.alpha_prop = prop
    return out
