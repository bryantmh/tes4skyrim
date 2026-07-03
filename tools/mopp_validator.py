"""MOPP bytecode validator for Skyrim/Oblivion NIF collision.

Walks the MOPP virtual-machine bytecode of every bhkMoppBvTreeShape in a NIF
and reports structural problems that crash Skyrim's hkpCollisionDispatcher
(EXCEPTION_STACK_OVERFLOW during character-proxy / NiPick closest-point
queries):

  - jumps/branches landing outside the mopp data
  - walks reaching uninitialised 0xCD filler bytes (MOPP_RL leaves these)
  - unknown opcodes (with context bytes for reverse-engineering)
  - shape keys out of range for the shape's triangle count
  - unreachable tail bytes (reports the true code length)

Opcode table: PyFFI's parse_mopp (reverse-engineered by niftools) extended
with the Skyrim-era commands found in vanilla Skyrim SE meshes (0x52 TERM24,
etc.).  Vanilla meshes in `references/Skyrim Meshes` serve as ground truth:
they must all validate clean.

Usage:
    python tools/mopp_validator.py <nif_or_dir> [<nif_or_dir> ...]
        [--max N] [--verbose] [--summary]
"""
import argparse
import os
import sys
import time
if not hasattr(time, 'clock'):
    time.clock = time.perf_counter  # PyFFI 2.2.3 uses removed time.clock

# The walker lives in asset_convert.mopp (shared with the conversion
# pipeline's dechunker); this tool is the CLI front-end.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asset_convert.mopp import walk_mopp  # noqa: E402


def shape_key_count(mopp_shape):
    """Number of valid shape keys for the shape under a bhkMoppBvTreeShape.

    bhkCompressedMeshShape: chunk triangles are keyed
    (chunk_id << bits_per_index) | tri; big tris follow in the highest chunk
    slot.  We return a conservative upper bound check instead: total triangle
    count for packed shapes, None (skip check) for CMS.
    """
    from pyffi.formats.nif import NifFormat
    inner = getattr(mopp_shape, 'shape', None)
    if inner is None:
        return None
    if isinstance(inner, NifFormat.bhkPackedNiTriStripsShape):
        data = getattr(inner, 'data', None)
        if data is not None:
            return data.num_triangles
    return None


def validate_nif(path, verbose=False, counter=None):
    """Validate all MOPPs in one NIF. Returns list of issue strings ([] = clean).

    counter: optional dict aggregating executed-opcode counts across files.
    """
    from pyffi.formats.nif import NifFormat
    issues = []
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)
    found = False
    for blk in data.blocks:
        if type(blk).__name__ != 'bhkMoppBvTreeShape':
            continue
        found = True
        size = blk.mopp_data_size
        mopp = list(blk.mopp_data)
        if size == 0:
            issues.append('empty mopp data')
            continue
        r = walk_mopp(mopp, size)
        if counter is not None:
            for op, n in r['counts'].items():
                counter[op] = counter.get(op, 0) + n
        issues.extend(r['errors'])
        # Reachable code that reads 0xCD-filler runs is corruption evidence;
        # a pure unreachable 0xCD tail is inert but reported for information.
        tail_start = r['max_offset'] + 1
        tail = mopp[tail_start:]
        if verbose or tail:
            n_cd = sum(1 for b in tail if b == 0xCD)
            if verbose:
                print('  mopp size=%d true_code_end=%d tail_bytes=%d (0xCD in tail: %d) '
                      'tris=%d coverage=%d/%d' % (size, tail_start, len(tail), n_cd,
                                                  len(r['tris']), len(r['visited']), size))
        # Reachable-garbage check: visited byte that is part of an 8+ run of 0xCD
        run = 0
        cd_runs = set()
        for j, b in enumerate(mopp):
            if b == 0xCD:
                run += 1
                if run >= 8:
                    cd_runs.update(range(j - run + 1, j + 1))
            else:
                run = 0
        hit = sorted(cd_runs & r['visited'])
        if hit:
            issues.append('walk reaches 0xCD filler at offsets %s' % hit[:8])
        nkeys = shape_key_count(blk)
        if nkeys is not None:
            bad = [t for t in r['tris'] if t >= nkeys]
            if bad:
                issues.append('shape keys out of range (max valid %d): %s'
                              % (nkeys - 1, sorted(bad)[:8]))
    if not found and verbose:
        print('  (no bhkMoppBvTreeShape)')
    return issues


def collect_nifs(paths, limit=None):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    if fn.lower().endswith('.nif'):
                        out.append(os.path.join(root, fn))
        else:
            out.append(p)
    if limit:
        out = out[:limit]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('paths', nargs='+', help='NIF files or directories')
    ap.add_argument('--max', type=int, default=None, help='max NIFs to check')
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--summary', action='store_true',
                    help='only print totals and failing files')
    ap.add_argument('--histogram', action='store_true',
                    help='print aggregate opcode histogram over all walked mopps')
    args = ap.parse_args()

    nifs = collect_nifs(args.paths, args.max)
    n_clean = n_bad = n_err = 0
    counter = {} if args.histogram else None
    for path in nifs:
        try:
            if args.verbose:
                print(path)
            issues = validate_nif(path, verbose=args.verbose, counter=counter)
        except Exception as e:
            n_err += 1
            print('%s: READ ERROR %r' % (path, e))
            continue
        if issues:
            n_bad += 1
            print('%s:' % path)
            for msg in issues:
                print('  %s' % msg)
        else:
            n_clean += 1
            if not args.summary and not args.verbose:
                print('%s: OK' % path)
    print('---')
    print('%d clean, %d with issues, %d unreadable (of %d)' %
          (n_clean, n_bad, n_err, len(nifs)))
    if counter:
        print('opcode histogram (executed sites):')
        for op in sorted(counter):
            print('  0x%02X: %d' % (op, counter[op]))
    return 1 if n_bad or n_err else 0


if __name__ == '__main__':
    sys.exit(main())
