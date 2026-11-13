"""Navmesh A/B: rebuild cells, then check protected and pinned cells and every health trait between two builds.

    python tools/navmesh/navmesh_ab.py build --plugin P --out side.pkl [--cells protected|all|A,B,...]
    python tools/navmesh/navmesh_ab.py protected --plugin P before.pkl after.pkl
    python tools/navmesh/navmesh_ab.py compare --plugin P before.pkl after.pkl [--skip PREFIX ...]

`build` rebuilds the chosen cells uncached, pins applied, with the current code: run it before the change and
after. `--cells protected` = the protected cells plus every pinned cell. `protected` prints the protected verdict
and, on its own line, the pinned verdict. `compare` scores every changed cell on every health trait, interiors
and exteriors apart, and lists the worst cells.
"""

import argparse
import json
import math
import os
import pickle
import sys
import time

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from tes5_import.navmesh import params, pool as navm_pool
from tes5_import.navmesh.from_pgrd import geom_equal
from tools.cellview.plugins import export_dir
from tools.navmesh import job_trace
from tools.navmesh.floor_points import surfaces
from tools.navmesh.index import NavIndex
from tools.navmesh.mesh_clean import defects

#: EditorID prefixes of the protected cells (Oblivion chargen, Nehrim intro): never changed without consent.
PROTECTED = ('ImperialDungeon', 'SchattenrufMine')
#: Traits where a higher number is worse.
WORSE_UP = ('islands', 'uncovered', 'narrow', 'holes', 'T', 'joins', 'overlaps', 'duplicates', 'off34', 'steep')


def pinned(plugin):
    """Lower-cased cell names any section of `navmesh_pins/<plugin>.json` names."""
    path = os.path.join(REPO, 'navmesh_pins', plugin + '.json')
    if not os.path.exists(path):
        return set()
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    return {name.lower() for v in data.values() if isinstance(v, dict) for name in v}


def wanted(job, cells, pins):
    """Whether `--cells` picks this job."""
    name = job['cell_rec'].get('EditorID') or ''
    if cells == 'all':
        return True
    if cells == 'protected':
        return name.startswith(PROTECTED) or name.lower() in pins
    return name in cells or job['cell_rec'].get('FormID', '').upper() in cells


def build(args):
    """Rebuild the chosen cells uncached; pickle geometry and per-cell names."""
    os.environ['TESCONV_NAVMESH_PLUGIN'] = args.plugin
    export = export_dir(args.plugin)
    im, _bt, door_fids, bm, jobs = job_trace.load_export(export, 0)
    job_trace.init_worker_for(export, im, door_fids, bm, 0)
    cells = args.cells if args.cells in ('all', 'protected') else set(args.cells.split(','))
    pins = pinned(args.plugin)
    todo = [dict(j, prove=True) for j in jobs if wanted(j, cells, pins)]
    print('%d jobs' % len(todo), flush=True)
    t0, geo, names = time.time(), {}, {}
    for job, (key, (_b, meta)) in zip(todo, navm_pool.pooled_prover(max(1, (os.cpu_count() or 2) - 1))(todo)):
        geo[key] = (meta or {}).get('geometry')
        names[key] = (job['cell_rec'].get('EditorID') or '%08X' % key[0],
                      bool(int(job['cell_rec'].get('DATA.Flags', '0'), 0) & 1),
                      len((meta or {}).get('door_refs') or ()), (meta or {}).get('error'))
    print('%s: %d jobs rebuilt in %.1fs' % (args.plugin, len(todo), time.time() - t0), flush=True)
    with open(args.out, 'wb') as fh:
        pickle.dump(geo, fh)
    with open(args.out + '.names.pkl', 'wb') as fh:
        pickle.dump(names, fh)


def load_side(path):
    """`(geometry by key, names by key)` of one `build` dump."""
    with open(path, 'rb') as fh:
        geo = pickle.load(fh)
    with open(path + '.names.pkl', 'rb') as fh:
        return geo, pickle.load(fh)


def changed(a, b):
    """Whether two dumped geometries differ (one missing counts)."""
    if a is None or b is None:
        return (a is None) != (b is None)
    return not geom_equal(a, b)


def protected(args):
    """Print the protected verdict and, separately, the pinned verdict between two dumps."""
    (before, _nb), (after, names) = load_side(args.before), load_side(args.after)
    pins = pinned(args.plugin)
    for label, want in (('PROTECTED', lambda n: n.startswith(PROTECTED)), ('pinned', lambda n: n.lower() in pins)):
        keys = [k for k, meta in names.items() if want(meta[0])]
        moved = sorted(names[k][0] for k in keys if changed(before.get(k), after.get(k)))
        print('%s %s: %d checked, %d changed %s  [%s]' % (args.plugin, label, len(keys), len(moved), moved or '',
                                                         ', '.join(sorted(names[k][0] for k in keys))), flush=True)


def pieces(verts, tris):
    """Triangle-index lists of each piece joined across shared edges (corner positions rounded to 0.1)."""
    parent = list(range(len(tris)))

    def find(i):
        """Union-find root of triangle i."""
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = {}
    for i, t in enumerate(tris):
        c = [tuple(round(v, 1) for v in verts[k][:3]) for k in t]
        for e in ((c[0], c[1]), (c[1], c[2]), (c[2], c[0])):
            e = tuple(sorted(e))
            if e in owner:
                parent[find(i)] = find(owner[e])
            else:
                owner[e] = i
    groups = {}
    for i in range(len(tris)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def single_point_joins(tris):
    """Vertices whose surrounding triangles form 2+ groups joined only at that vertex."""
    around = {}
    for i, t in enumerate(tris):
        for v in t:
            around.setdefault(v, []).append(i)
    return sum(fan_groups(v, [tris[i] for i in fan]) > 1 for v, fan in around.items())


def fan_groups(v, fan):
    """Groups the triangles around vertex `v` form when joined by the edges they share through `v`."""
    parent = list(range(len(fan)))

    def find(i):
        """Union-find root of fan triangle i."""
        while parent[i] != i:
            i = parent[i]
        return i

    first = {}
    for i, t in enumerate(fan):
        for w in t:
            if w != v and w in first:
                parent[find(i)] = find(first[w])
            elif w != v:
                first[w] = i
    return len({find(i) for i in range(len(fan))})


def narrow_parts(polys, groups):
    """Parts narrower than 32 units (area 200+) left after opening each piece by 16."""
    narrow = 0
    for piece in groups:
        shape = unary_union([polys[i] for i in piece])
        thin = shape.difference(shape.buffer(-16).buffer(16).buffer(0.5))
        narrow += sum(1 for p in getattr(thin, 'geoms', [thin]) if not p.is_empty and p.area >= 200)
    return narrow


def steep_count(verts, tris):
    """Triangles steeper than 46 degrees."""
    steep = 0
    for t in tris:
        a, b, c = (np.asarray(verts[k][:3], float) for k in t)
        n = np.cross(b - a, c - a)
        nl = np.linalg.norm(n)
        steep += nl > 0 and math.degrees(math.acos(min(1.0, abs(n[2]) / nl))) > 46
    return steep


def traits(geo, nodes, walk):
    """{trait: value} of one cell's mesh; `nodes` are pathgrid points, `walk` the walkable collision soup."""
    verts, tris = geo[0], [tuple(t[:3]) for t in geo[1]]
    polys = [Polygon([verts[k][:2] for k in t]).buffer(0) for t in tris]
    soup = np.asarray(verts, float)[:, :3][np.asarray(tris, int).reshape(-1, 3)] if tris else None
    max_climb = params.MAX_CLIMB * 3
    uncovered = sum(1 for n in nodes if soup is None
                    or not any(abs(h - n[2]) <= max_climb for h in surfaces(soup, n[0], n[1])))
    off = sum(1 for v in {k for t in tris for k in t}
              if len(walk) and (s := surfaces(walk, verts[v][0], verts[v][1]))
              and min(abs(verts[v][2] - h) for h in s) > 34)
    overlaps, tj, slivers, holes = defects(verts, tris, list(range(len(tris))))
    groups = pieces(verts, tris)
    unique = len(np.unique(np.sort(np.asarray(tris, int).reshape(-1, 3), axis=1), axis=0)) if tris else 0
    return {'area': sum(p.area for p in polys), 'islands': len(groups), 'uncovered': uncovered,
            'narrow': narrow_parts(polys, groups), 'holes': len(holes), 'T': len(tj),
            'joins': single_point_joins(tris), 'overlaps': len(overlaps), 'duplicates': len(tris) - unique,
            'slivers': len(slivers), 'off34': off, 'steep': steep_count(verts, tris)}


def score_cell(idx, name, ga, gb):
    """`(traits before, traits after)` of one changed cell, scored against its own collision and pathgrid."""
    ctx = idx.cell(name)
    walk = ctx.collision()[0] if ctx else np.zeros((0, 3, 3))
    nodes = ctx.nodes if ctx else []
    return traits(ga, nodes, walk), traits(gb, nodes, walk)


def tally(totals, side, ta, tb):
    """Add one cell's before/after traits to its side's totals."""
    for k in ta:
        totals[side].setdefault(k, [0, 0])
        totals[side][k][0] += ta[k]
        totals[side][k][1] += tb[k]


def report(totals, rows):
    """Print the per-side totals and the worst cells."""
    for side, t in totals.items():
        print('%s: %d of %d changed, lost mesh %d, build errors %d, door triangles %+d' % (
            side, t['changed'], t['cells'], t['lost mesh'], t['errors'], t['doors']), flush=True)
        for k, v in t.items():
            if isinstance(v, list):
                print('   %-10s %10.0f -> %10.0f' % (k, v[0], v[1]), flush=True)
    print('cells that got worse on some trait: %d' % sum(1 for r in rows if r[0] > 0), flush=True)
    for _bad, name, ta, tb in sorted(rows, key=lambda r: -r[0])[:12]:
        print('  %-34s %s' % (name[:34], ', '.join('%s %d->%d' % (k, ta[k], tb[k]) for k in WORSE_UP
                                                   if tb[k] > ta[k])), flush=True)


def compare(args):
    """Every health trait over the changed cells of two dumps, interiors and exteriors apart, and the worst cells."""
    (a, na), (b, nb) = load_side(args.before), load_side(args.after)
    idx = NavIndex(export_dir(args.plugin))
    totals, rows = {}, []
    for key in sorted(set(a) | set(b)):
        name, interior, doors, error = nb.get(key, na.get(key))
        if args.skip and name.startswith(tuple(args.skip)):
            continue
        side = 'interiors' if interior else 'exteriors'
        t = totals.setdefault(side, {'cells': 0, 'changed': 0, 'lost mesh': 0, 'errors': 0, 'doors': 0})
        t['cells'] += 1
        t['errors'] += bool(error)
        ga, gb = a.get(key), b.get(key)
        if not changed(ga, gb):
            continue
        t['changed'] += 1
        t['doors'] += doors - na.get(key, (0, 0, 0))[2]
        if gb is None or not gb[1]:
            t['lost mesh'] += 1
        elif ga is not None and ga[1]:
            ta, tb = score_cell(idx, name, ga, gb)
            tally(totals, side, ta, tb)
            rows.append((sum(max(0, tb[k] - ta[k]) for k in WORSE_UP), name, ta, tb))
    report(totals, rows)


def parse():
    """Command-line arguments."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--out', required=True)
    b.add_argument('--cells', default='protected', help='protected | all | comma list of EditorIDs or FormIDs')
    for name in ('protected', 'compare'):
        p = sub.add_parser(name)
        p.add_argument('before')
        p.add_argument('after')
    sub.choices['compare'].add_argument('--skip', nargs='*', default=[], help='EditorID prefixes to leave out')
    for p in sub.choices.values():
        p.add_argument('--plugin', required=True)
    return ap.parse_args()


def main():
    """Run the chosen subcommand."""
    args = parse()
    {'build': build, 'protected': protected, 'compare': compare}[args.cmd](args)


if __name__ == '__main__':
    main()
