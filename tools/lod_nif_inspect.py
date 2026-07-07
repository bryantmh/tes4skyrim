"""Inspect terrain (.btr) / object (.bto) LOD NIFs.

Dumps the geometry/shader facts that matter for LOD correctness so converted
output can be compared field-by-field against vanilla Skyrim references:
  - root type/name/flags, BSMultiBound AABB
  - per-shape: name, flags, scale, vertex/triangle count, has_normals,
    has_vertex_colors, num_uv_sets, UV range, vertex XYZ range
  - shader type + flags + texture set paths

Usage:
  python tools/lod_nif_inspect.py <nif_or_dir> [--max N] [--verbose]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from asset_convert import pyffi_monkey_patch as _patch  # noqa: F401
from pyffi.formats.nif import NifFormat


def _rng(vals):
    if not vals:
        return "(none)"
    return f"[{min(vals):.2f}, {max(vals):.2f}]"


def inspect(path: Path, verbose=False):
    data = NifFormat.Data()
    try:
        with open(path, 'rb') as f:
            data.read(f)
    except Exception as e:
        print(f"{path.name}: PARSE ERROR: {e}")
        return

    print(f"\n=== {path.name} ({path.stat().st_size} bytes) ===")
    print(f"  version={data.version:#010x} uv={data.user_version} uv2={data.user_version_2}")

    for root in data.roots:
        print(f"  ROOT {type(root).__name__} name={root.name!r} flags={getattr(root,'flags',None)}")
        mb = getattr(root, 'multi_bound', None)
        if mb and getattr(mb, 'data', None) is not None:
            d = mb.data
            if hasattr(d, 'position'):
                p, e = d.position, d.extent
                print(f"    MultiBoundAABB pos=({p.x:.1f},{p.y:.1f},{p.z:.1f}) "
                      f"ext=({e.x:.1f},{e.y:.1f},{e.z:.1f})")

        for block in _iter_blocks(root):
            if isinstance(block, NifFormat.NiTriShape) or isinstance(block, NifFormat.NiTriStrips) \
               or type(block).__name__ in ('BSLODTriShape', 'BSMeshLODTriShape', 'BSSegmentedTriShape'):
                _dump_shape(block, verbose)


def _iter_blocks(root):
    seen = set()
    stack = [root]
    while stack:
        b = stack.pop()
        if id(b) in seen:
            continue
        seen.add(id(b))
        yield b
        for c in getattr(b, 'get_refs', lambda: [])():
            stack.append(c)


def _dump_shape(shape, verbose):
    sd = shape.data
    name = shape.name
    scale = getattr(shape, 'scale', 1.0)
    tr = shape.translation
    print(f"    SHAPE {type(shape).__name__} name={name!r} flags={shape.flags} "
          f"scale={scale} trans=({tr.x:.1f},{tr.y:.1f},{tr.z:.1f})")
    if sd is None:
        print("      (no data)")
    else:
        nv = sd.num_vertices
        nt = getattr(sd, 'num_triangles', 0)
        xs = [v.x for v in sd.vertices]
        ys = [v.y for v in sd.vertices]
        zs = [v.z for v in sd.vertices]
        print(f"      verts={nv} tris={nt} has_normals={sd.has_normals} "
              f"has_vcol={sd.has_vertex_colors} num_uv={sd.num_uv_sets}")
        print(f"      X={_rng(xs)} Y={_rng(ys)} Z={_rng(zs)}")
        if sd.has_uv and sd.num_uv_sets and len(sd.uv_sets):
            us = [uv.u for uv in sd.uv_sets[0]]
            vs = [uv.v for uv in sd.uv_sets[0]]
            print(f"      U={_rng(us)} V={_rng(vs)}")
        if sd.has_vertex_colors and len(sd.vertex_colors):
            c0 = sd.vertex_colors[0]
            print(f"      vcol[0]=({c0.r:.2f},{c0.g:.2f},{c0.b:.2f},{c0.a:.2f})")
    # shader
    for prop in list(getattr(shape, 'bs_properties', [])) + \
                [p for p in getattr(shape, 'properties', [])]:
        if prop is None:
            continue
        tn = type(prop).__name__
        if 'ShaderProperty' in tn:
            st = getattr(prop, 'skyrim_shader_type', None)
            print(f"      SHADER {tn} type={st}")
            _dump_shader_flags(prop)
            ts = getattr(prop, 'texture_set', None)
            if ts is not None:
                for i, t in enumerate(ts.textures):
                    s = bytes(t).rstrip(b'\x00').decode('latin-1')
                    if s:
                        print(f"        TX{i:02d} = {s}")
            uvs = getattr(prop, 'uv_scale', None)
            uvo = getattr(prop, 'uv_offset', None)
            if uvs is not None:
                print(f"        uv_scale=({uvs.u},{uvs.v}) "
                      f"uv_offset=({uvo.u},{uvo.v})" if uvo else f"        uv_scale=({uvs.u},{uvs.v})")
        elif tn == 'NiAlphaProperty':
            print(f"      ALPHA flags={prop.flags:#06x} threshold={prop.threshold}")


def _dump_shader_flags(prop):
    for attr in ('shader_flags_1', 'shader_flags_2'):
        sf = getattr(prop, attr, None)
        if sf is None:
            continue
        on = [n for n in dir(sf) if n.startswith('slsf') and getattr(sf, n)]
        if on:
            print(f"        {attr}: {', '.join(on)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target')
    ap.add_argument('--max', type=int, default=8)
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    t = Path(args.target)
    if t.is_dir():
        files = sorted(list(t.glob('*.btr')) + list(t.glob('*.bto')) + list(t.glob('*.nif')))[:args.max]
    else:
        files = [t]
    for f in files:
        inspect(f, args.verbose)


if __name__ == '__main__':
    main()
