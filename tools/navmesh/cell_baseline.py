"""Build cells natively and compare them to a saved baseline of the same cells.

The first run against a baseline file saves every cell's build; later runs
compare the current build to the saved one.  Vertices, triangles and ledge
links must match exactly.  Exits 1 on any mismatch or error.  `--sample N`
adds N pathgrid cells picked at random (`--seed`) for wider coverage.

    python tools/navmesh/cell_baseline.py before.pkl ImperialDungeon01 AnvilCastleGreatHall
    python tools/navmesh/cell_baseline.py --export export/Nehrim.esm before.pkl Wendir02
    python tools/navmesh/cell_baseline.py --sample 12 --seed 3 before.pkl

See: docs/commentary/tes5_import_navmesh.md#cell-baseline
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
from tes5_import.navmesh.seeding import seeded_graph, seeded_record
from tes5_import.navmesh.world import base_fid
from tools.navmesh.index import DEFAULT_EXPORT, NavIndex


#: Obstacles a `--mode stress` build bridges across: this many of the cell's collision refs.
STRESS_OBSTACLES = 40

#: A `--mode stress` build floods the cell this far above its lowest pathgrid node.
STRESS_WATER = 200.0

#: A `--mode stress` build drops every this-many'th pathgrid edge, so bridging has components to join.
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


def build(ctx, mode):
    """`(result, seconds)` for one cell: (verts, tris, ledges), or the seed flood's graph."""
    t0 = time.perf_counter()
    if mode == 'flood':
        job = {'cell_rec': ctx.rec, 'land_rec': ctx.land, 'refr_recs': ctx.refrs,
               'pgrd_rec': seeded_record(ctx.rec), 'seeds': sorted(set(ctx.nodes[:3]))}
        out = seeded_graph(job, ctx.index.base_model, ctx.index.door_fids, ce.get_collision)
    else:
        ledges = []
        verts, tris = ctx.build(ledges_out=ledges,
                                **(stress_inputs(ctx) if mode == 'stress' else {}))
        out = (verts, tris, ledges)
    return out, time.perf_counter() - t0


def first_difference(a, b):
    """A short description of where two builds first differ, or '' when equal."""
    if not isinstance(a, tuple) or len(a) != 3:
        return '' if a == b else 'flood graphs differ'
    for label, x, y in zip(('verts', 'tris', 'ledges'), a, b):
        if len(x) != len(y):
            return '%s count %d vs %d' % (label, len(x), len(y))
        for i, (p, q) in enumerate(zip(x, y)):
            if tuple(p) != tuple(q):
                return '%s[%d] %r vs %r' % (label, i, tuple(p), tuple(q))
    return ''


def check_named(index, name, saved, mode):
    """True when one named cell failed, printing its result row."""
    ctx = index.cell(name)
    if ctx is None:
        print('%-28s not found' % name)
        return True
    try:
        got, secs = build(ctx, mode)
    except Exception:
        traceback.print_exc()
        print('%-28s ERROR' % name[:28])
        return True
    key = (mode, ctx.fid)
    if key not in saved:
        saved[key] = got
        print('%-28s %8.2f  SAVED' % (name[:28], secs), flush=True)
        return False
    diff = first_difference(saved[key], got)
    print('%-28s %8.2f  %s' % (name[:28], secs, 'MATCH' if not diff else 'DIFF ' + diff),
          flush=True)
    return bool(diff)


def parse_args():
    """The command line."""
    ap = argparse.ArgumentParser()
    ap.add_argument('baseline', help='saved builds: written on first use, compared after')
    ap.add_argument('cells', nargs='*', help='EditorID or FormID')
    ap.add_argument('--export', default=DEFAULT_EXPORT)
    ap.add_argument('--sample', type=int, default=0, help='also check N random pathgrid cells')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--mode', choices=('stress', 'flood'),
                    help='stress: bridge obstacles and flood water; flood: the seeded-cell flood')
    return ap.parse_args()


def main():
    """Check every named (and sampled) cell against the baseline; exit 1 on any mismatch."""
    a = parse_args()
    saved = {}
    if os.path.exists(a.baseline):
        with open(a.baseline, 'rb') as fh:
            saved = pickle.load(fh)
    index = NavIndex(a.export)
    if a.sample:
        pool = sorted(index.pathgrid_fids())
        a.cells += random.Random(a.seed).sample(pool, min(a.sample, len(pool)))
    print('%-28s %8s  %s' % ('CELL', 'SEC', 'RESULT'))
    bad = sum(check_named(index, name, saved, a.mode) for name in a.cells)
    with open(a.baseline, 'wb') as fh:
        pickle.dump(saved, fh, pickle.HIGHEST_PROTOCOL)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
