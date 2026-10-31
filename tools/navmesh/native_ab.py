"""Build cells with the Python navmesh reference and the native kernels, and require identical output.

Both builds run in one process from the same loaded cell, flipping
`native_loader.NATIVE` between them, so the only difference is the code path.
Vertices, triangles and ledge links must match exactly.  Exits 1 on any
mismatch or error.

`--baseline FILE` instead checks the Python reference against itself across a
change to it: the first run saves every cell's Python build, later runs
compare the current Python build to the saved one.

    python tools/navmesh/native_ab.py ImperialDungeon01 AnvilCastleGreatHall
    python tools/navmesh/native_ab.py --export export/Nehrim.esm Wendir02
    python tools/navmesh/native_ab.py --baseline before.pkl ImperialDungeon01

See: docs/commentary/tes5_import_navmesh.md#native-port-ab
"""

import argparse
import os
import pickle
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.navmesh import native_loader
from tools.navmesh.index import DEFAULT_EXPORT, NavIndex


def build_with(ctx, native):
    """`(verts, tris, ledges, seconds)` for one cell on the chosen code path."""
    native_loader.NATIVE = native
    ledges = []
    t0 = time.perf_counter()
    verts, tris = ctx.build(ledges_out=ledges)
    return verts, tris, ledges, time.perf_counter() - t0


def first_difference(a, b):
    """A short description of where two builds first differ, or '' when equal."""
    for label, x, y in (('verts', a[0], b[0]), ('tris', a[1], b[1]),
                        ('ledges', a[2], b[2])):
        if len(x) != len(y):
            return '%s count %d vs %d' % (label, len(x), len(y))
        for i, (p, q) in enumerate(zip(x, y)):
            if tuple(p) != tuple(q):
                return '%s[%d] %r vs %r' % (label, i, tuple(p), tuple(q))
    return ''


def compare_cell(ctx, saved=None):
    """`(python_s, other_s, difference)`: Python vs native, or vs `saved` when given."""
    py = build_with(ctx, False)
    if saved is None:
        other = build_with(ctx, True)
    elif ctx.fid in saved:
        other = saved[ctx.fid]
    else:
        saved[ctx.fid] = py
        return py[3], py[3], ''
    return py[3], other[3], first_difference(py, other)


def load_baseline(path):
    """The saved `{cell fid: build}` at `path`, or {} when it does not exist yet."""
    if not os.path.exists(path):
        return {}
    with open(path, 'rb') as fh:
        return pickle.load(fh)


def main():
    """Compare every named cell; exit 1 on any mismatch."""
    ap = argparse.ArgumentParser()
    ap.add_argument('cells', nargs='+', help='EditorID or FormID')
    ap.add_argument('--export', default=DEFAULT_EXPORT)
    ap.add_argument('--baseline', help='saved Python builds to compare against')
    a = ap.parse_args()

    saved = load_baseline(a.baseline) if a.baseline else None
    index = NavIndex(a.export)
    bad = 0
    tot_py = tot_nat = 0.0
    print('%-28s %8s %8s %6s  %s' % ('CELL', 'PY s', 'NAT s', 'SPEED', 'RESULT'))
    for name in a.cells:
        ctx = index.cell(name)
        if ctx is None:
            print('%-28s not found' % name)
            bad += 1
            continue
        try:
            t_py, t_nat, diff = compare_cell(ctx, saved)
        except Exception:
            traceback.print_exc()
            print('%-28s ERROR' % name[:28])
            bad += 1
            continue
        tot_py += t_py
        tot_nat += t_nat
        bad += bool(diff)
        print('%-28s %8.2f %8.2f %5.2fx  %s'
              % (name[:28], t_py, t_nat, t_py / max(t_nat, 1e-9),
                 'MATCH' if not diff else 'DIFF ' + diff), flush=True)
    print('%-28s %8.2f %8.2f %5.2fx' % ('TOTAL', tot_py, tot_nat,
                                        tot_py / max(tot_nat, 1e-9)))
    native_loader.NATIVE = True
    if a.baseline:
        with open(a.baseline, 'wb') as fh:
            pickle.dump(saved, fh, pickle.HIGHEST_PROTOCOL)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
