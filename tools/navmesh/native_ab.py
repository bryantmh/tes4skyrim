"""Build cells with the Python navmesh reference and the native kernels, and require identical output.

Both builds run in one process from the same loaded cell, flipping
`native_loader.NATIVE` between them, so the only difference is the code path.
Vertices, triangles and ledge links must match exactly.  Exits 1 on any
mismatch or error.

`--baseline FILE` instead checks the Python reference against itself across a
change to it: the first run saves every cell's Python build, later runs
compare the current Python build to the saved one.  `--sample N` adds N
pathgrid cells picked at random (`--seed`) for wider coverage.

    python tools/navmesh/native_ab.py ImperialDungeon01 AnvilCastleGreatHall
    python tools/navmesh/native_ab.py --export export/Nehrim.esm Wendir02
    python tools/navmesh/native_ab.py --baseline before.pkl ImperialDungeon01
    python tools/navmesh/native_ab.py --sample 12 --seed 3

See: docs/commentary/tes5_import_navmesh.md#native-port-ab
"""

import argparse
import os
import pickle
import random
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.collision import collision_extract as ce
from tes5_import.base.text_reader import get_formid
from tes5_import.navmesh import native_loader
from tes5_import.navmesh.seeding import seeded_graph, seeded_record
from tes5_import.navmesh.world import base_fid
from tools.navmesh.index import DEFAULT_EXPORT, NavIndex


#: Obstacles a `--stress` build bridges across: this many of the cell's collision refs.
STRESS_OBSTACLES = 40

#: A `--stress` build floods the cell this far above its lowest pathgrid node.
STRESS_WATER = 200.0

#: A `--stress` build drops every this-many'th pathgrid edge, so bridging has components to join.
STRESS_EDGE_DROP = 3


def stress_inputs(ctx):
    """`{obstacles, water_z, edges}` that drive the bridge and submerged-ref paths on real data."""
    doors = set(ctx.index.door_fids.keys())
    placed = sorted((get_formid(r, 'FormID'), r) for r in ctx.refrs
                    if base_fid(r) not in doors and ctx.index.base_model.get(base_fid(r)))
    obstacles = [(r, ctx.index.base_model[base_fid(r)]) for _f, r in placed[:STRESS_OBSTACLES]]
    low = min((n[2] for n in ctx.nodes), default=0.0)
    edges = [e for i, e in enumerate(ctx.edges) if i % STRESS_EDGE_DROP]
    return {'obstacles': obstacles, 'water_z': low + STRESS_WATER, 'edges': edges}


def build_with(ctx, native, extra=None):
    """`(verts, tris, ledges, seconds)` for one cell on the chosen code path."""
    native_loader.NATIVE = native
    ledges = []
    t0 = time.perf_counter()
    verts, tris = ctx.build(ledges_out=ledges, **(extra or {}))
    return verts, tris, ledges, time.perf_counter() - t0


def flood_with(ctx, native):
    """`(graph, seconds)`: seeding.seeded_graph from the cell's first nodes as seeds."""
    native_loader.NATIVE = native
    job = {'cell_rec': ctx.rec, 'land_rec': ctx.land, 'refr_recs': ctx.refrs,
           'pgrd_rec': seeded_record(ctx.rec), 'seeds': sorted(set(ctx.nodes[:3]))}
    t0 = time.perf_counter()
    graph = seeded_graph(job, ctx.index.base_model, ctx.index.door_fids, ce.get_collision)
    return graph, time.perf_counter() - t0


def compare_flood(ctx):
    """`(python_s, native_s, difference)` of the seed flood on both paths."""
    py, t_py = flood_with(ctx, False)
    nat, t_nat = flood_with(ctx, True)
    return t_py, t_nat, '' if py == nat else 'flood graphs differ'


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


def compare_cell(ctx, saved=None, mode=None):
    """`(python_s, other_s, difference)`: Python vs native, or vs `saved` when given.

    `mode` 'stress' adds stress_inputs to both builds; 'flood' compares the seed flood.
    """
    if mode == 'flood':
        return compare_flood(ctx)
    extra = stress_inputs(ctx) if mode == 'stress' else None
    py = build_with(ctx, False, extra)
    if saved is None:
        other = build_with(ctx, True, extra)
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


def compare_named(index, name, saved, mode=None):
    """`(python_s, other_s, failed)` for one named cell, printing its result row."""
    ctx = index.cell(name)
    if ctx is None:
        print('%-28s not found' % name)
        return 0.0, 0.0, True
    try:
        t_py, t_nat, diff = compare_cell(ctx, saved, mode)
    except Exception:
        traceback.print_exc()
        print('%-28s ERROR' % name[:28])
        return 0.0, 0.0, True
    print('%-28s %8.2f %8.2f %5.2fx  %s'
          % (name[:28], t_py, t_nat, t_py / max(t_nat, 1e-9),
             'MATCH' if not diff else 'DIFF ' + diff), flush=True)
    return t_py, t_nat, bool(diff)


def parse_args():
    """The command line."""
    ap = argparse.ArgumentParser()
    ap.add_argument('cells', nargs='*', help='EditorID or FormID')
    ap.add_argument('--export', default=DEFAULT_EXPORT)
    ap.add_argument('--baseline', help='saved Python builds to compare against')
    ap.add_argument('--sample', type=int, default=0, help='also compare N random pathgrid cells')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--mode', choices=('stress', 'flood'),
                    help='stress: bridge obstacles and flood water; flood: the seeded-cell flood')
    return ap.parse_args()


def main():
    """Compare every named (and sampled) cell; exit 1 on any mismatch."""
    a = parse_args()
    saved = load_baseline(a.baseline) if a.baseline else None
    index = NavIndex(a.export)
    if a.sample:
        pool = sorted(index.pathgrid_fids())
        a.cells += random.Random(a.seed).sample(pool, min(a.sample, len(pool)))
    bad = 0
    tot_py = tot_nat = 0.0
    print('%-28s %8s %8s %6s  %s' % ('CELL', 'PY s', 'NAT s', 'SPEED', 'RESULT'))
    for name in a.cells:
        t_py, t_nat, failed = compare_named(index, name, saved, a.mode)
        tot_py += t_py
        tot_nat += t_nat
        bad += failed
    print('%-28s %8.2f %8.2f %5.2fx' % ('TOTAL', tot_py, tot_nat,
                                        tot_py / max(tot_nat, 1e-9)))
    native_loader.NATIVE = True
    if a.baseline:
        with open(a.baseline, 'wb') as fh:
            pickle.dump(saved, fh, pickle.HIGHEST_PROTOCOL)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
