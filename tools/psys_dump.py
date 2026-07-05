"""Particle-system chain dumper for NIF files.

Dumps EVERYTHING that determines whether a NiParticleSystem is visible in
Skyrim: node hierarchy, controller chain (flags/frequency/start/stop,
interpolators and their values), every modifier with all fields, emitter
fields, NiPSysData fields, shader + alpha properties, BSX flags.

Usage:
    python tools/psys_dump.py <nif> [<nif> ...] [--convert]

    --convert   treat inputs as Oblivion sources: run the full converter
                in-memory first, then dump the converted tree (bypasses the
                PyFFI-can't-reread-our-output limitation).
"""
import sys
import time

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

sys.path.insert(0, '.')
import asset_convert.pyffi_monkey_patch  # noqa: F401
from pyffi.formats.nif import NifFormat


def _nm(b):
    v = getattr(b, 'name', b'') or b''
    if isinstance(v, bytes):
        v = v.decode('latin1', 'ignore')
    return v


def _t(b):
    return type(b).__name__ if b is not None else 'None'


def dump_interpolator(interp, indent):
    pad = ' ' * indent
    if interp is None:
        print(f'{pad}interpolator=None')
        return
    tn = _t(interp)
    line = f'{pad}{tn}'
    for f in ('value', 'bool_value', 'float_value'):
        if hasattr(interp, f):
            line += f' {f}={getattr(interp, f)}'
    data = getattr(interp, 'data', None)
    print(line + f' data={_t(data)}')
    if data is not None and hasattr(data, 'data'):
        kd = data.data
        n = getattr(kd, 'num_keys', 0)
        keys = []
        try:
            keys = [(k.time, getattr(k, 'value', '?')) for k in kd.keys[:8]]
        except Exception:
            pass
        print(f'{pad}  keydata num_keys={n} interp={getattr(kd, "interpolation", "?")} keys[:8]={keys}')


def dump_controller_chain(ctrl, indent):
    pad = ' ' * indent
    while ctrl is not None:
        tn = _t(ctrl)
        print(f'{pad}{tn} flags=0x{ctrl.flags:02x} freq={ctrl.frequency} phase={ctrl.phase} '
              f'start={ctrl.start_time} stop={ctrl.stop_time} target={_t(getattr(ctrl, "target", None))}')
        mn = getattr(ctrl, 'modifier_name', None)
        if mn is not None:
            print(f'{pad}  modifier_name={mn!r}')
        if hasattr(ctrl, 'interpolator'):
            dump_interpolator(ctrl.interpolator, indent + 2)
        if hasattr(ctrl, 'visibility_interpolator'):
            vi = ctrl.visibility_interpolator
            print(f'{pad}  visibility_interpolator:')
            dump_interpolator(vi, indent + 4)
        ctrl = getattr(ctrl, 'next_controller', None)


_MOD_SKIP = {'name', 'order', 'target', 'active'}


def dump_modifier(m, indent):
    pad = ' ' * indent
    tn = _t(m)
    print(f'{pad}{tn} name={_nm(m)!r} order={m.order} active={m.active} target={_t(m.target)}')
    # dump every scalar field via PyFFI's attribute metadata (dir() breaks on
    # PyFFI descriptors)
    names = []
    for klass in reversed(type(m).__mro__):
        for a in klass.__dict__.get('_attrs', []):
            nm = getattr(a, 'name', None)
            if nm and nm not in names:
                names.append(nm)
    fields = []
    for f in names:
        if f in _MOD_SKIP:
            continue
        try:
            v = getattr(m, f)
        except Exception:
            continue
        if isinstance(v, (int, float, bytes, bool)):
            fields.append(f'{f}={v}')
        elif type(v).__name__ in ('Vector3', 'Vector4'):
            fields.append(f'{f}=({v.x:.4g},{v.y:.4g},{v.z:.4g})')
        elif type(v).__name__.startswith('Ni') or type(v).__name__.startswith('BS'):
            fields.append(f'{f}->{_t(v)}"{_nm(v)}"')
    for i in range(0, len(fields), 4):
        print(f'{pad}  ' + '  '.join(fields[i:i + 4]))
    if hasattr(m, 'floats') and getattr(m, 'num_floats', 0):
        fl = list(m.floats)
        print(f'{pad}  floats[{len(fl)}]: first={fl[0]:.3f} max={max(fl):.3f} last={fl[-1]:.3f}')
    if hasattr(m, 'colors'):
        try:
            cols = [(c.r, c.g, c.b, c.a) for c in m.colors]
            print(f'{pad}  colors={cols}')
        except Exception:
            pass


def dump_psys(ps, indent):
    pad = ' ' * indent
    print(f'{pad}NiParticleSystem "{_nm(ps)}" flags=0x{ps.flags:04x} '
          f'world_space={getattr(ps, "world_space", "?")} scale={ps.scale}')
    tr = ps.translation
    print(f'{pad}  translation=({tr.x:.3g},{tr.y:.3g},{tr.z:.3g})')
    d = ps.data
    if d is not None:
        print(f'{pad}  DATA {_t(d)}: num_vertices={d.num_vertices} bs_max_vertices={getattr(d, "bs_max_vertices", "?")} '
              f'has_vertices={d.has_vertices} has_normals={d.has_normals} '
              f'center=({d.center.x:.3g},{d.center.y:.3g},{d.center.z:.3g}) radius={d.radius:.3g}')
    else:
        print(f'{pad}  DATA=None !!!')
    print(f'{pad}  controllers:')
    dump_controller_chain(ps.controller, indent + 4)
    print(f'{pad}  modifiers ({ps.num_modifiers}):')
    for m in ps.modifiers:
        if m is not None:
            dump_modifier(m, indent + 4)
    # properties old + bs
    for p in ps.properties:
        print(f'{pad}  prop: {_t(p)}')
    for p in getattr(ps, 'bs_properties', []):
        if p is None:
            print(f'{pad}  bs_prop: None')
            continue
        tn = _t(p)
        if tn == 'BSEffectShaderProperty':
            print(f'{pad}  bs_prop: {tn} tex={p.source_texture!r} '
                  f'flags1=0x{int(p.shader_flags_1._value if hasattr(p.shader_flags_1, "_value") else 0):08x} '
                  f'emis_mult={p.emissive_multiple} '
                  f'emis_col=({p.emissive_color.r},{p.emissive_color.g},{p.emissive_color.b},{p.emissive_color.a}) '
                  f'clamp={p.texture_clamp_mode} falloff=({p.falloff_start_angle},{p.falloff_stop_angle},'
                  f'{p.falloff_start_opacity},{p.falloff_stop_opacity}) soft_depth={getattr(p, "soft_falloff_depth", "?")}')
            ctl = getattr(p, 'controller', None)
            if ctl is not None:
                print(f'{pad}    shader controllers:')
                dump_controller_chain(ctl, indent + 6)
        elif tn == 'NiAlphaProperty':
            print(f'{pad}  bs_prop: {tn} flags=0x{p.flags:04x} threshold={p.threshold}')
        else:
            print(f'{pad}  bs_prop: {tn}')


def dump_tree(root, indent=0):
    pad = ' ' * indent
    tn = _t(root)
    if isinstance(root, NifFormat.NiParticleSystem):
        dump_psys(root, indent)
        return
    extra = ''
    if hasattr(root, 'integer_data'):
        extra = f' int_data=0x{root.integer_data:x}'
    if hasattr(root, 'value') and isinstance(getattr(root, 'value', None), int):
        extra += f' value={root.value}'
    print(f'{pad}{tn} "{_nm(root)}"{extra}')
    if hasattr(root, 'extra_data_list'):
        for ed in root.extra_data_list:
            if ed is not None:
                ev = getattr(ed, 'integer_data', getattr(ed, 'string_data', ''))
                print(f'{pad}  [extra] {_t(ed)} "{_nm(ed)}" = {ev}')
    ctrl = getattr(root, 'controller', None)
    if ctrl is not None and not isinstance(root, NifFormat.NiParticleSystem):
        print(f'{pad}  node controllers:')
        dump_controller_chain(ctrl, indent + 4)
    if hasattr(root, 'children'):
        for c in root.children:
            if c is not None:
                dump_tree(c, indent + 2)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    convert = '--convert' in sys.argv
    for path in args:
        print('=' * 70)
        print(path, '(CONVERTED IN-MEMORY)' if convert else '(AS-IS)')
        print('=' * 70)
        data = NifFormat.Data()
        with open(path, 'rb') as f:
            data.inspect(f)
            data.read(f)
        if convert:
            import asset_convert.nif_converter as nc
            nc._convert_nif(data, fix_textures=True, src_path=path)
        for root in data.roots:
            dump_tree(root)


if __name__ == '__main__':
    main()
