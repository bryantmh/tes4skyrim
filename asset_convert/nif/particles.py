"""Oblivion particle systems and billboards -> their Skyrim equivalents.

NiPSysData's binary layout differs between UV2=11 and UV2=83, so a particle
system is rebuilt rather than copied: the data block is replaced, the modifier
chain is rewritten into the BS* types vanilla ships, and the shader is built
from the same authored emissive the FX path reads.  Billboard axis correction
lives here too, because Oblivion's and Skyrim's billboard modes disagree about
which local axis faces the camera.

See: docs/commentary/asset_convert_nif.md#nif-particle-system-conversion
"""

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.nif.nif_flags import NIF_FLAGS
from asset_convert.nif.shaders import (apply_fx_soft_effect,
                                       attach_tex_transform_ctrls,
                                       base_texture_path,
                                       collect_tex_transform_ctrls,
                                       find_flip_controller)
from asset_convert.nif.tex_paths import (bs_pp_texture_slots,
                                         rewrite_tex_path)
from asset_convert.nif.uv_transform import apply_static_uv, base_map


#: Vanilla modifier `order` bands. See: docs/commentary/asset_convert_nif.md#nif-particle-system-conversion
_PSYS_ORDER = {
    'NiPSysAgeDeathModifier': 0,
    'BSPSysLODModifier': 1,
    'NiPSysEmitter': 1000,
    'NiPSysSpawnModifier': 1000,
    'BSPSysSimpleColorModifier': 3000,
    'NiPSysRotationModifier': 3000,
    'BSPSysScaleModifier': 3000,
    'NiPSysGravityModifier': 4000,
    'NiPSysPositionModifier': 6000,
    'NiPSysBoundUpdateModifier': 7000,
}


def _psys_order_for(mod):
    """The vanilla processing `order` band for one modifier.

    Any *Emitter shares the emitter band; anything unlisted sits mid-chain.
    """
    tn = type(mod).__name__
    if tn in _PSYS_ORDER:
        return _PSYS_ORDER[tn]
    if tn.endswith('Emitter'):
        return 1000
    return 3000


def _make_scale_ramp_from_growfade(gf):
    """A 60-entry BSPSysScaleModifier ramp for a NiPSysGrowFadeModifier.

    Reproduces its grow-in / hold / fade-out over the particle lifetime.
    See: docs/commentary/asset_convert_nif.md#psys-modifier-vocabulary
    """
    n = 60
    grow = max(float(getattr(gf, 'grow_time', 0.0)), 0.0)
    fade = max(float(getattr(gf, 'fade_time', 0.2)), 0.001)
    base = float(getattr(gf, 'base_scale', 1.0)) or 1.0
    grow_frac = min(max(grow, 0.0), 0.9)
    fade_frac = min(max(fade, 0.05), 0.9)
    scales = []
    for i in range(n):
        t = i / (n - 1)
        if grow_frac > 0 and t < grow_frac:
            s = t / grow_frac
        elif t > 1.0 - fade_frac:
            s = max((1.0 - t) / fade_frac, 0.1)
        else:
            s = 1.0
        scales.append(base * s)
    return scales


def _sample_color_keys(keys, t):
    """Linearly sample a NiColorData key list at normalized time `t`."""
    if not keys:
        return (1.0, 1.0, 1.0, 1.0)
    pts = sorted(((float(k.time), k.value) for k in keys),
                 key=lambda kv: kv[0])
    t0, t1 = pts[0][0], pts[-1][0]
    span = (t1 - t0) or 1.0
    want = t0 + t * span
    prev = pts[0]
    for cur in pts:
        if cur[0] >= want:
            if cur[0] == prev[0]:
                c = cur[1]
                return (c.r, c.g, c.b, c.a)
            f = (want - prev[0]) / (cur[0] - prev[0])
            a, b = prev[1], cur[1]
            return (a.r + (b.r - a.r) * f, a.g + (b.g - a.g) * f,
                    a.b + (b.b - a.b) * f, a.a + (b.a - a.a) * f)
        prev = cur
    c = pts[-1][1]
    return (c.r, c.g, c.b, c.a)


def _simple_color_from(mod):
    """BSPSysSimpleColorModifier carrying the AUTHORED color gradient.

    Oblivion's curve is sampled at its start, middle and end; with no curve at
    all a neutral white ramp tints nothing rather than inventing a hue.
    See: docs/commentary/asset_convert_nif.md#authored-particle-color
    """
    cm = NifFormat.BSPSysSimpleColorModifier()
    cm.fade_in_percent = 0.1
    cm.fade_out_percent = 0.25
    cm.color_1_start_percent = 0.0
    cm.color_1_end_percent = 0.15
    cm.color_2_start_percent = 1.0
    cm.color_2_end_percent = 0.5

    keys = []
    data = getattr(mod, 'data', None)
    kg = getattr(data, 'data', None) if data is not None else None
    if kg is not None:
        keys = list(getattr(kg, 'keys', []) or [])

    if not keys:
        cols = [(1.0, 1.0, 1.0, 0.0), (1.0, 1.0, 1.0, 1.0),
                (1.0, 1.0, 1.0, 0.0)]
    else:
        cols = [_sample_color_keys(keys, 0.0),
                _sample_color_keys(keys, 0.5),
                _sample_color_keys(keys, 1.0)]

    for i, (r, g, b, a) in enumerate(cols):
        cm.colors[i].r = float(r)
        cm.colors[i].g = float(g)
        cm.colors[i].b = float(b)
        cm.colors[i].a = float(a)
    return cm


def _chromatic(color):
    """Does this key carry a real hue, or sit on the grey axis?

    See: docs/commentary/asset_convert_nif.md#alpha-envelope-vs-color-curve
    """
    hi = max(color.r, color.g, color.b)
    lo = min(color.r, color.g, color.b)
    return hi > 0.02 and (hi - lo) > 0.03


def _modifier_colors(mod):
    """Every color key on one color modifier, in either vocabulary.

    A BSPSysSimpleColorModifier has already been rewritten by
    _skyrimize_modifiers, so its three sampled colors stand in for the curve.
    """
    if isinstance(mod, NifFormat.BSPSysSimpleColorModifier):
        return list(getattr(mod, 'colors', None) or [])
    if not isinstance(mod, NifFormat.NiPSysColorModifier):
        return []
    data = getattr(mod, 'data', None)
    kg = getattr(data, 'data', None) if data is not None else None
    return [k.value for k in (getattr(kg, 'keys', None) or [])]


def _color_curve_carries_hue(node):
    """Does this system's NiPSysColorModifier supply an actual COLOR?

    False for an achromatic ramp, which is an alpha envelope rather than a
    color and must not override the material's emissive.
    See: docs/commentary/asset_convert_nif.md#alpha-envelope-vs-color-curve
    """
    for mod in (node.modifiers or []):
        if any(_chromatic(c) for c in _modifier_colors(mod)):
            return True
    return False


def _scale_modifier_from(gf):
    """A BSPSysScaleModifier carrying the grow/fade ramp as its float list."""
    sm = NifFormat.BSPSysScaleModifier()
    ramp = _make_scale_ramp_from_growfade(gf)
    sm.num_floats = len(ramp)
    sm.floats.update_size()
    for i, v in enumerate(ramp):
        sm.floats[i] = v
    return sm


def _default_lod_modifier():
    """The BSPSysLODModifier vanilla puts on every particle system."""
    lod = NifFormat.BSPSysLODModifier()
    lod.uknown_float_1 = 0.033333
    lod.uknown_float_2 = 0.233333
    lod.uknown_float_3 = 0.2
    lod.uknown_float_4 = 1.0
    return lod


def _translate_modifiers(old):
    """Each source modifier in the Skyrim vocabulary, order not yet applied."""
    new = []
    for m in old:
        if isinstance(m, NifFormat.NiPSysGrowFadeModifier):
            new.append(_scale_modifier_from(m))
        elif isinstance(m, NifFormat.NiPSysColorModifier):
            new.append(_simple_color_from(m))
        else:
            new.append(m)
    if not any(isinstance(m, NifFormat.BSPSysLODModifier) for m in old):
        new.append(_default_lod_modifier())
    if not any(isinstance(m, NifFormat.NiPSysAgeDeathModifier) for m in old):
        new.append(NifFormat.NiPSysAgeDeathModifier())
    return new


def _stamp_modifier_fields(new, node):
    """Give every modifier the NiPSysModifier name/order/target/active fields."""
    for i, m in enumerate(new):
        if not (getattr(m, 'name', None) or b''):
            m.name = ('%s:%d' % (type(m).__name__, i)).encode('latin1')
        m.order = _psys_order_for(m)
        m.target = node
        m.active = True


def _skyrimize_modifiers(node):
    """Rewrite a NiParticleSystem's modifier list to the Skyrim vocabulary.

    The SSE particle engine drives only its own vocabulary; left as authored,
    the particles are invisible.
    See: docs/commentary/asset_convert_nif.md#psys-modifier-vocabulary
    """
    old = [m for m in node.modifiers if m is not None]
    new = _translate_modifiers(old)
    new.sort(key=_psys_order_for)
    _stamp_modifier_fields(new, node)

    node.num_modifiers = len(new)
    node.modifiers.update_size()
    for i, m in enumerate(new):
        node.modifiers[i] = m


def _authored_emissive(color):
    """A material's emissive as a tuple; None when black (no glow authored)."""
    if color.r > 0.0 or color.g > 0.0 or color.b > 0.0:
        return (color.r, color.g, color.b)
    return None


def _collect_psys_properties(node):
    """Texture, flip controller, alpha and authored color from a particle emitter.

    Returns (diffuse_path, flip_ctrl, alpha_prop, emissive, alpha). The emissive
    is the emitter's authored brightness, taken verbatim so a smoke emitter at
    (0.35, 0.35, 0.35) is never promoted to white.

    See: docs/commentary/asset_convert_nif.md#fo3fnv-shader-properties
    """
    diffuse_path = b''
    flip_ctrl = None
    alpha_prop = None
    emissive = None
    alpha = 1.0
    for prop in node.properties:
        if isinstance(prop, NifFormat.NiTexturingProperty):
            diffuse_path = base_texture_path(prop) or diffuse_path
            flip_ctrl = find_flip_controller(prop) or flip_ctrl
            continue
        if isinstance(prop, NifFormat.BSShaderPPLightingProperty):
            diffuse_path = bs_pp_texture_slots(prop)[0]
            continue
        if isinstance(prop, NifFormat.NiMaterialProperty):
            ec = prop.emissive_color
            emissive = _authored_emissive(ec) or emissive
            alpha = float(prop.alpha)
        elif isinstance(prop, NifFormat.NiAlphaProperty):
            alpha_prop = prop
    return diffuse_path, flip_ctrl, alpha_prop, emissive, alpha


#: Active | Compute Scaled Time; OR'd in so Oblivion's CLAMP cycle bits survive.
_PSYS_CTRL_FLAGS = 0x48

#: Clamp mode 3 (WRAP_S|WRAP_T) packed with lighting influence 0xFF.
_PSYS_CLAMP_MODE = 0xFF03

#: Additive: src=SRC_ALPHA dst=ONE, the value on every vanilla particle system.
_PSYS_ALPHA_FLAGS = 0x100d

#: Smallest particle pool Skyrim will allocate into.
_PSYS_MIN_POOL = 75


def _fresh_psys_data(node):
    """Replace the NiPSysData, keeping only the pool size.

    UV2=11 and UV2=83 disagree on the binary layout, so the block is rebuilt
    rather than converted. See: docs/commentary/asset_convert_nif.md#psys-shader-values
    """
    if node.data is None:
        return
    fresh = NifFormat.NiPSysData()
    fresh.bs_max_vertices = max(node.data.num_vertices, _PSYS_MIN_POOL)
    fresh.has_vertices = True
    fresh.has_normals = False
    node.data = fresh


def _fix_psys_controller_flags(node):
    """Give every emitter/update controller vanilla's Compute-Scaled-Time bit."""
    ctrl = node.controller
    while ctrl is not None:
        if isinstance(ctrl, (NifFormat.NiPSysEmitterCtlr,
                             NifFormat.NiPSysUpdateCtlr,
                             NifFormat.NiPSysModifierActiveCtlr)):
            ctrl.flags |= _PSYS_CTRL_FLAGS
        ctrl = getattr(ctrl, 'next_controller', None)


def _effective_psys_texture(flip_ctrl, diffuse_path, fix_textures):
    """The particle's texture path: a flip-book's first frame, else the diffuse.

    The NiFlipController itself is never attached -- it targets a
    NiTexturingProperty that no longer exists.
    See: docs/commentary/asset_convert_nif.md#psys-shader-values
    """
    if flip_ctrl is not None:
        for src_tex in flip_ctrl.sources:
            if src_tex is not None and src_tex.file_name:
                pth = src_tex.file_name
                pth = (rewrite_tex_path(pth) if fix_textures
                       else pth.decode('utf-8', errors='replace'))
                src_tex.file_name = pth.encode('utf-8')
        srcs = [s for s in flip_ctrl.sources if s is not None and s.file_name]
        return srcs[0].file_name if srcs else b''
    if not diffuse_path:
        return b''
    ep = (rewrite_tex_path(diffuse_path) if fix_textures
          else diffuse_path.decode('utf-8', errors='replace'))
    return ep.encode('utf-8')


def _build_psys_shader(effective_path, emissive, alpha, curve_hue):
    """The BSEffectShaderProperty for one particle system.

    The multiple stays neutral and the AUTHORED emissive supplies the
    brightness -- unless a chromatic curve is the real color source.
    See: docs/commentary/asset_convert_nif.md#psys-shader-values
    """
    shader = NifFormat.BSEffectShaderProperty()
    shader.shader_flags_1.slsf_1_z_buffer_test = 1
    sf2 = shader.shader_flags_2
    sf2.slsf_2_z_buffer_write = 0
    sf2.slsf_2_vertex_colors = 1
    shader.source_texture = effective_path
    shader.texture_clamp_mode = _PSYS_CLAMP_MODE
    shader.emissive_multiple = 1.0
    if emissive is not None and not curve_hue:
        shader.emissive_color.r, shader.emissive_color.g, \
            shader.emissive_color.b = emissive
    else:
        shader.emissive_color.r = 1.0
        shader.emissive_color.g = 1.0
        shader.emissive_color.b = 1.0
    shader.emissive_color.a = alpha
    return shader


def _own_alpha_property(alpha_prop):
    """A NiAlphaProperty this system alone owns, defaulted to additive.

    Oblivion shares one across several systems; vanilla Skyrim never does.
    See: docs/commentary/asset_convert_nif.md#psys-shader-values
    """
    own = NifFormat.NiAlphaProperty()
    if alpha_prop is None:
        own.flags = _PSYS_ALPHA_FLAGS
    else:
        own.flags = alpha_prop.flags
        own.threshold = alpha_prop.threshold
    return own


def convert_particle_system(node, fix_textures):
    """Convert an Oblivion NiParticleSystem's properties for Skyrim.

    Oblivion stores its textures in NiTexturingProperty; Skyrim particle
    systems use a BSEffectShaderProperty in bs_properties. All modifiers are
    kept, rewritten into the Skyrim vocabulary.
    See: docs/commentary/asset_convert_nif.md#nif-particle-system-conversion
    """
    psys_curve_hue = _color_curve_carries_hue(node)
    tex_transforms = collect_tex_transform_ctrls(node.properties)
    tex_map = base_map(node.properties)
    (diffuse_path, flip_ctrl, alpha_prop,
     psys_emissive, psys_alpha) = _collect_psys_properties(node)

    node.num_properties = 0
    node.properties.update_size()

    _fresh_psys_data(node)
    _skyrimize_modifiers(node)
    _fix_psys_controller_flags(node)

    shader = _build_psys_shader(
        _effective_psys_texture(flip_ctrl, diffuse_path, fix_textures),
        psys_emissive, psys_alpha, psys_curve_hue)
    apply_static_uv(shader, tex_map)
    node.bs_properties[0] = shader
    attach_tex_transform_ctrls(shader, tex_transforms, tex_map)

    alpha_prop = _own_alpha_property(alpha_prop)
    node.bs_properties[1] = alpha_prop
    apply_fx_soft_effect(shader, alpha_prop, psys_emissive)


#: The −90°-about-X billboard correction. See: docs/commentary/asset_convert_nif.md#billboard-axis-fix
_BB_AXIS_FIX = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))


def _compose_axis_fix(rot):
    """rot ← rot · R_fix (PyFFI row-vector convention) in place."""
    m = [[rot.m_11, rot.m_12, rot.m_13],
         [rot.m_21, rot.m_22, rot.m_23],
         [rot.m_31, rot.m_32, rot.m_33]]
    f = _BB_AXIS_FIX
    r = [[sum(m[i][k] * f[k][j] for k in range(3)) for j in range(3)]
         for i in range(3)]
    rot.m_11, rot.m_12, rot.m_13 = r[0]
    rot.m_21, rot.m_22, rot.m_23 = r[1]
    rot.m_31, rot.m_32, rot.m_33 = r[2]


def wrap_in_billboard(child, bb_mode):
    """Wrap a geometry block in a fresh NiBillboardNode so the quad faces camera.

    Vanilla's campfire pattern: BSFadeNode -> NiBillboardNode -> NiTriShape.
    The wrapper carries NO axis correction and is tagged `_axis_fixed` so the
    later pass leaves it alone.
    See: docs/commentary/asset_convert_nif.md#billboard-axis-fix
    """
    bb = NifFormat.NiBillboardNode()
    bb.name = (child.name or b'') + b'-Billboard'
    bb.flags = NIF_FLAGS
    bb.billboard_mode = bb_mode
    bb._axis_fixed = True
    bb.num_children = 1
    bb.children.update_size()
    bb.children[0] = child
    return bb


def _is_emitter_marker(node):
    """Is this node referenced as a particle emitter/gravity marker?

    Such a node's rotation is the emission DIRECTION a NiPSysEmitter reads, so
    it survives demotion even though a billboard's rotation is otherwise
    discarded for drawing (NifSkope BillboardNode::viewTrans).
    """
    for blk in node.tree():
        for attr in ('emitter_object', 'gravity_object'):
            if getattr(blk, attr, None) is node:
                return True
    return False


def _inherit_billboard_rotation(plain, bb):
    """Carry a demoted billboard's rotation across only if it is live data.

    A NiBillboardNode discards its own rotation at runtime, so the demoted node
    inherits identity -- EXCEPT on an emitter marker, whose orientation a
    NiPSysEmitter reads as the emission DIRECTION.
    See: docs/commentary/asset_convert_nif.md#billboard-demotion
    """
    if not _is_emitter_marker(bb):
        plain.rotation.set_identity()
        return
    for row in (1, 2, 3):
        for col in (1, 2, 3):
            attr = 'm_%d%d' % (row, col)
            setattr(plain.rotation, attr, getattr(bb.rotation, attr))


def _copy_billboard_frame(plain, bb):
    """Carry a billboard's transform, extra data, controller and collision over."""
    plain.name = bb.name
    plain.flags = NIF_FLAGS
    plain.translation.x = bb.translation.x
    plain.translation.y = bb.translation.y
    plain.translation.z = bb.translation.z
    _inherit_billboard_rotation(plain, bb)
    plain.scale = bb.scale
    plain.num_extra_data_list = bb.num_extra_data_list
    plain.extra_data_list.update_size()
    for j, ed in enumerate(bb.extra_data_list):
        plain.extra_data_list[j] = ed
    if bb.controller is not None:
        plain.controller = bb.controller
    if getattr(bb, 'collision_object', None) is not None:
        plain.collision_object = bb.collision_object
        plain.collision_object.target = plain


def _repoint_psys_markers(plain, bb):
    """Move emitter/gravity references off the replaced node.

    A modifier still naming the old block dangles ("block is missing from the
    nif tree") and the simulation breaks.
    """
    for blk in plain.tree():
        for attr in ('emitter_object', 'gravity_object'):
            if getattr(blk, attr, None) is bb:
                setattr(blk, attr, plain)


def _demote_billboard(bb, bb_mode):
    """The plain NiNode replacing a billboard that holds a particle system.

    Its geometry children are re-wrapped so the quads still face the camera.
    See: docs/commentary/asset_convert_nif.md#billboard-demotion
    """
    plain = NifFormat.NiNode()
    _copy_billboard_frame(plain, bb)
    plain.num_children = bb.num_children
    plain.children.update_size()
    for j in range(bb.num_children):
        c = bb.children[j]
        if isinstance(c, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            c = wrap_in_billboard(c, bb_mode)
        plain.children[j] = c
    _repoint_psys_markers(plain, bb)
    return plain


def skyrimize_billboard(bb):
    """Convert a (non-root) Oblivion NiBillboardNode for Skyrim.

    A billboard holding a particle system is DEMOTED to a plain NiNode; a pure
    geometry billboard keeps its type and gains the axis correction. A wrapper
    this converter built is already in the right frame and is left alone.
    See: docs/commentary/asset_convert_nif.md#billboard-demotion
    """
    if getattr(bb, '_axis_fixed', False):
        return bb
    bb_mode = int(getattr(bb, 'billboard_mode', 1)) or 1
    has_psys = any(isinstance(b, NifFormat.NiParticleSystem)
                   for b in bb.tree())
    if not has_psys:
        _compose_axis_fix(bb.rotation)
        bb._axis_fixed = True
        return bb
    return _demote_billboard(bb, bb_mode)
