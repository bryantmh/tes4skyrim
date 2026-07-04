"""bhkCompressedMeshShape(Data) inspector for Skyrim NIF collision.

Dumps every scalar field of the CMS shape + data blocks (plus per-chunk
headers) so converted output can be diffed field-by-field against vanilla
Skyrim meshes, and cross-checks the decoded triangle soup for geometric
sanity (degenerates, quantization range, bounds consistency).

Usage:
    python tools/cms_inspector.py <nif> [<nif> ...] [--chunks] [--tris N]
"""
import argparse
import math
import os
import sys
import time
if not hasattr(time, 'clock'):
    time.clock = time.perf_counter  # PyFFI 2.2.3 uses removed time.clock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asset_convert.cms import decode_cms  # noqa: E402


def _vec(v):
    parts = [getattr(v, n) for n in ('x', 'y', 'z', 'w') if hasattr(v, n)]
    return '(' + ', '.join('%.6g' % p for p in parts) + ')'


def _fmt(val):
    if hasattr(val, 'x'):
        return _vec(val)
    if isinstance(val, float):
        return '%.6g' % val
    return repr(val)


def dump_cms(path, show_chunks=False, show_tris=0):
    """Print all CMS shape/data fields of one NIF."""
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)
    print('=== %s' % path)
    found = False
    for blk in data.blocks:
        name = type(blk).__name__
        if name == 'bhkCompressedMeshShape':
            found = True
            print('bhkCompressedMeshShape:')
            for attr in blk._get_filtered_attribute_list(data):
                val = getattr(blk, attr.name, None)
                if attr.name in ('target', 'data'):
                    print('  %-24s -> %s' % (attr.name, type(val).__name__))
                else:
                    print('  %-24s = %s' % (attr.name, _fmt(val)))
        elif name == 'bhkCompressedMeshShapeData':
            found = True
            print('bhkCompressedMeshShapeData:')
            for attr in blk._get_filtered_attribute_list(data):
                val = getattr(blk, attr.name, None)
                if attr.name in ('chunk_materials', 'chunk_transforms',
                                 'big_verts', 'big_tris', 'chunks',
                                 'strips_data'):
                    try:
                        n = len(val)
                    except TypeError:
                        n = '?'
                    print('  %-24s = [%s items]' % (attr.name, n))
                else:
                    print('  %-24s = %s' % (attr.name, _fmt(val)))
            for ti in range(blk.num_transforms):
                t = blk.chunk_transforms[ti]
                print('  transform[%d]: trans=%s rot=(%.6g, %.6g, %.6g, %.6g)'
                      % (ti, _vec(t.translation), t.rotation.x, t.rotation.y,
                         t.rotation.z, t.rotation.w))
            for mi in range(blk.num_materials):
                m = blk.chunk_materials[mi]
                print('  material[%d]: material=%s filter=layer %s'
                      % (mi, getattr(m.material, '_value',
                                     getattr(m, 'material', '?')),
                         getattr(getattr(m, 'filter', None), 'layer', '?')))
            if show_chunks:
                for ci in range(blk.num_chunks):
                    ch = blk.chunks[ci]
                    offs = list(ch.vertices)
                    print('  chunk[%d]: trans=%s mat=%d ref=%d xform=%d '
                          'nverts=%d nindices=%d nstrips=%d nweld=%d '
                          'u16range=[%d..%d]'
                          % (ci, _vec(ch.translation), ch.material_index,
                             getattr(ch, 'reference', -1), ch.transform_index,
                             ch.num_vertices, ch.num_indices, ch.num_strips,
                             getattr(ch, 'num_indices_2', -1),
                             min(offs) if offs else 0,
                             max(offs) if offs else 0))
            sanity_check(blk, show_tris)
    if not found:
        print('  (no bhkCompressedMeshShape)')


def sanity_check(cms_data, show_tris=0):
    """Geometric sanity report over the decoded triangle soup."""
    tris = decode_cms(cms_data)
    n_degen = 0
    min_area = float('inf')
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    for _key, (a, b, c) in tris:
        for v in (a, b, c):
            for i in range(3):
                lo[i] = min(lo[i], v[i])
                hi[i] = max(hi[i], v[i])
        ab = [b[i] - a[i] for i in range(3)]
        ac = [c[i] - a[i] for i in range(3)]
        cx = (ab[1] * ac[2] - ab[2] * ac[1],
              ab[2] * ac[0] - ab[0] * ac[2],
              ab[0] * ac[1] - ab[1] * ac[0])
        area = 0.5 * math.sqrt(cx[0] ** 2 + cx[1] ** 2 + cx[2] ** 2)
        min_area = min(min_area, area)
        if area < 1e-10:
            n_degen += 1
    print('  decode: %d tris, %d degenerate, min area %.3g' %
          (len(tris), n_degen, min_area))
    print('  decoded bounds: (%.4g, %.4g, %.4g) .. (%.4g, %.4g, %.4g)' %
          (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]))
    bmin, bmax = cms_data.bounds_min, cms_data.bounds_max
    print('  stored  bounds: (%.4g, %.4g, %.4g) .. (%.4g, %.4g, %.4g)' %
          (bmin.x, bmin.y, bmin.z, bmax.x, bmax.y, bmax.z))
    for i, name in enumerate('xyz'):
        smin = getattr(bmin, name)
        smax = getattr(bmax, name)
        if lo[i] < smin - 0.01 or hi[i] > smax + 0.01:
            print('  ** decoded geometry exceeds stored bounds on %s: '
                  'decoded [%.4g, %.4g] vs stored [%.4g, %.4g]'
                  % (name, lo[i], hi[i], smin, smax))
    for key, tri in tris[:show_tris]:
        print('    key=0x%08X %s' % (key, tri))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('paths', nargs='+', help='NIF files')
    ap.add_argument('--chunks', action='store_true', help='per-chunk headers')
    ap.add_argument('--tris', type=int, default=0,
                    help='print first N decoded triangles with keys')
    args = ap.parse_args()
    for p in args.paths:
        dump_cms(p, show_chunks=args.chunks, show_tris=args.tris)


if __name__ == '__main__':
    main()
