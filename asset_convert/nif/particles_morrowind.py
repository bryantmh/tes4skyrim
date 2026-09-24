"""Morrowind legacy particle emitters -> Oblivion-shaped NiParticleSystem.

Morrowind predates the NiPSys* vocabulary: an emitter is a NiParticles
subclass (NiRotatingParticles / NiAutoNormalParticles) whose emission is
driven by a NiParticleSystemController and whose affectors hang off that
controller's `particle_extra` chain.  Skyrim has no RTTI for any of those
types, so the whole file is rejected and renders as the missing-model red
triangle.

Upgrading to NiParticleSystem here, during the Morrowind pre-pass, lets the
normal Oblivion path in `particles.py` finish the job unchanged.

See: docs/commentary/asset_convert_nif.md#morrowind-particle-systems
"""

from pyffi.formats.nif import NifFormat

from asset_convert.nif.nif_flags import NIF_FLAGS

#: The legacy NiParticles subclasses Morrowind emits with.
LEGACY_EMITTERS = ('NiRotatingParticles', 'NiAutoNormalParticles')

#: Controllers that drive a legacy emitter (NiBSPArrayController is a subclass).
_LEGACY_CTLRS = ('NiParticleSystemController', 'NiBSPArrayController')

#: Active | Compute Scaled Time, the flags vanilla puts on every psys controller.
_PSYS_CTRL_FLAGS = 0x48

#: The update controller adds the loop bit; 0x4c on all 31 vanilla ones sampled.
_UPDATE_CTRL_FLAGS = 0x4c

#: Smallest particle pool Skyrim will allocate into.
_MIN_POOL = 75

#: Emission volume for a point emitter; Morrowind emits from the node origin.
_POINT_EMITTER_EXTENT = 0.0


def _legacy_controller(node):
    """The NiParticleSystemController driving `node`, or None."""
    ctrl = node.controller
    while ctrl is not None:
        if type(ctrl).__name__ in _LEGACY_CTLRS:
            return ctrl
        ctrl = getattr(ctrl, 'next_controller', None)
    return None


def _extra_chain(ctrl):
    """Every affector on the controller's `particle_extra` linked list."""
    out = []
    node = getattr(ctrl, 'particle_extra', None)
    while node is not None and node not in out:
        out.append(node)
        node = getattr(node, 'next_modifier', None)
    return out


def _grow_fade_from(extra):
    """A NiPSysGrowFadeModifier carrying the legacy grow/fade seconds."""
    mod = NifFormat.NiPSysGrowFadeModifier()
    mod.grow_time = extra.grow
    mod.fade_time = extra.fade
    mod.base_scale = 1.0
    return mod


def _gravity_from(extra, frame):
    """A NiPSysGravityModifier carrying the legacy NiGravity force.

    FieldType and ForceType share their values -- wind/planar 0, point/
    spherical 1 -- so `type` passes straight through.  PyFFI's older nif.xml
    names NiGravity's leading Decay float `unknown_float_1`.  `frame` is the
    emitter NiNode: the field is a Ptr to NiNode, and all 40 vanilla gravity
    modifiers sampled carry one.
    """
    mod = NifFormat.NiPSysGravityModifier()
    mod.strength = extra.force
    mod.force_type = extra.type
    mod.gravity_axis.x = extra.direction.x
    mod.gravity_axis.y = extra.direction.y
    mod.gravity_axis.z = extra.direction.z
    mod.decay = extra.unknown_float_1
    mod.turbulence = 0.0
    mod.turbulence_scale = 1.0
    mod.gravity_object = frame
    return mod


def _color_from(extra=None):
    """A NiPSysColorModifier on the legacy color curve; none means neutral."""
    mod = NifFormat.NiPSysColorModifier()
    mod.data = getattr(extra, 'color_data', None)
    return mod


def _translate_extra(extra, frame):
    """The Skyrim modifier a legacy affector becomes, or None when unmapped."""
    name = type(extra).__name__
    if name == 'NiParticleGrowFade':
        return _grow_fade_from(extra)
    if name == 'NiGravity':
        return _gravity_from(extra, frame)
    if name == 'NiParticleRotation':
        return NifFormat.NiPSysRotationModifier()
    if name == 'NiParticleColorModifier':
        return _color_from(extra)
    return None


def _emission_frame(ctrl, node, parents):
    """The NiNode whose transform orients the emission cone.

    The controller's `emitter` is a Ptr to NiNode, but 12 of the 298 in the
    corpus point at the particle block ITSELF (self-emitting) -- which becomes
    a NiParticleSystem, not a NiNode. Those fall back to the block's parent.
    """
    target = ctrl.emitter
    if isinstance(target, NifFormat.NiNode):
        return target
    return parents.get(id(node))


def _emitter_from(ctrl, frame):
    """A point emitter carrying the controller's authored emission cone.

    Morrowind's controller holds the rate, speed, life and cone half-angles
    directly; `frame` is the node whose transform orients the cone.
    """
    emitter = NifFormat.NiPSysBoxEmitter()
    emitter.speed = ctrl.speed
    emitter.speed_variation = ctrl.speed_random
    emitter.declination = ctrl.vertical_direction
    emitter.declination_variation = ctrl.vertical_angle
    emitter.planar_angle = ctrl.horizontal_direction
    emitter.planar_angle_variation = ctrl.horizontal_angle
    emitter.life_span = ctrl.lifetime
    emitter.life_span_variation = ctrl.lifetime_random
    emitter.emitter_object = frame
    emitter.width = _POINT_EMITTER_EXTENT
    emitter.height = _POINT_EMITTER_EXTENT
    emitter.depth = _POINT_EMITTER_EXTENT
    emitter.initial_radius = ctrl.size
    emitter.initial_color.r = emitter.initial_color.g = 1.0
    emitter.initial_color.b = emitter.initial_color.a = 1.0
    return emitter


def _emitter_ctlr(ctrl, emitter, psys):
    """The NiPSysEmitterCtlr that carries the authored birth rate.

    `target` is NOT optional: the engine loads it unconditionally and walks
    it, so a null there is an access violation the moment the system updates.
    See: docs/commentary/asset_convert_nif.md#morrowind-particle-systems
    """
    ectlr = NifFormat.NiPSysEmitterCtlr()
    ectlr.flags = _PSYS_CTRL_FLAGS
    ectlr.frequency = ctrl.frequency
    ectlr.start_time = ctrl.emit_start_time
    ectlr.stop_time = ctrl.emit_stop_time
    ectlr.modifier_name = emitter.name
    ectlr.target = psys
    interp = NifFormat.NiFloatInterpolator()
    interp.float_value = ctrl.emit_rate
    ectlr.interpolator = interp
    visible = NifFormat.NiBoolInterpolator()
    visible.bool_value = True
    ectlr.visibility_interpolator = visible
    return ectlr


def _build_modifiers(ctrl, frame):
    """The Skyrim modifier chain for one legacy emitter, before ordering."""
    emitter = _emitter_from(ctrl, frame)
    mods = [NifFormat.NiPSysAgeDeathModifier(), emitter]
    for extra in _extra_chain(ctrl):
        translated = _translate_extra(extra, frame)
        if translated is not None:
            mods.append(translated)
    if not any(isinstance(m, NifFormat.NiPSysColorModifier) for m in mods):
        mods.append(_color_from())
    mods.append(NifFormat.NiPSysPositionModifier())
    mods.append(NifFormat.NiPSysBoundUpdateModifier())
    return mods, emitter


def _stamp(mods, psys):
    """Give every modifier the name/target/active fields NiPSysModifier needs."""
    for i, mod in enumerate(mods):
        mod.name = ('%s:%d' % (type(mod).__name__, i)).encode('latin1')
        mod.target = psys
        mod.active = True


def _psys_data(legacy_data):
    """A NiPSysData sized from the legacy particle pool."""
    data = NifFormat.NiPSysData()
    data.bs_max_vertices = max(getattr(legacy_data, 'num_vertices', 0),
                               _MIN_POOL)
    data.has_vertices = True
    data.has_normals = False
    return data


def _copy_avobject(psys, node):
    """Carry the emitter's name, transform and properties onto the new block."""
    psys.name = node.name
    psys.flags = NIF_FLAGS
    psys.translation = node.translation
    psys.rotation = node.rotation
    psys.scale = node.scale
    psys.num_properties = node.num_properties
    psys.properties.update_size()
    for i, prop in enumerate(node.properties):
        psys.properties[i] = prop


def _as_particle_system(node, parents):
    """The NiParticleSystem a legacy emitter becomes, or None when undrivable."""
    ctrl = _legacy_controller(node)
    if ctrl is None:
        return None
    psys = NifFormat.NiParticleSystem()
    _copy_avobject(psys, node)
    psys.data = _psys_data(node.data)

    mods, emitter = _build_modifiers(ctrl, _emission_frame(ctrl, node, parents))
    _stamp(mods, psys)
    psys.num_modifiers = len(mods)
    psys.modifiers.update_size()
    for i, mod in enumerate(mods):
        psys.modifiers[i] = mod

    ectlr = _emitter_ctlr(ctrl, emitter, psys)
    update = NifFormat.NiPSysUpdateCtlr()
    update.flags = _UPDATE_CTRL_FLAGS
    update.frequency = ctrl.frequency
    update.target = psys
    ectlr.next_controller = update
    psys.controller = ectlr
    return psys


def upgrade_legacy_particles(data, stats=None) -> int:
    """Rewrite every legacy particle emitter as a NiParticleSystem.

    Returns how many were upgraded.  An emitter with no controller carries no
    emission and is left alone for the geometry walk to handle.
    See: docs/commentary/asset_convert_nif.md#morrowind-particle-systems
    """
    parents = {id(child): block for block in data.blocks
               for child in (getattr(block, 'children', None) or [])
               if child is not None and isinstance(block, NifFormat.NiNode)}
    replaced = {}
    for block in data.blocks:
        if type(block).__name__ not in LEGACY_EMITTERS:
            continue
        psys = _as_particle_system(block, parents)
        if psys is not None:
            replaced[id(block)] = (block, psys)
    if not replaced:
        return 0
    holders = list(data.blocks) + [new for _, new in replaced.values()]
    for old, new in replaced.values():
        for holder in holders:
            holder.replace_global_node(old, new)
        data.roots = [new if r is old else r for r in data.roots]
    if stats is not None:
        stats['mw_psys_upgraded'] = (stats.get('mw_psys_upgraded', 0)
                                     + len(replaced))
    return len(replaced)
