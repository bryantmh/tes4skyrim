"""Audit the INVARIANT: one connected pathgrid => one connected navmesh component.

A pathgrid connected component asserts "an actor walks between all of these
nodes".  If the navmesh built from it comes out in more pieces than the pathgrid
has components, the engine cannot make that walk — the mesh is broken however
good it looks.  This is the acceptance test for the corridor builder.

For every cell it reports:

    pathgrid components   (the target)
    navmesh components    (must not exceed it)
    per-break diagnosis   which pathgrid component fragmented, and the closest
                          approach between the fragments, so the cause is
                          located rather than guessed

    python tools/navmesh/component_audit.py --cell ChorrolFightersGuild
    python tools/navmesh/component_audit.py --cells A,B,C
    python tools/navmesh/component_audit.py --all --limit 40

MEMORY: this runs SINGLE-PROCESS on purpose.  `load_cell` builds a ~2GB export
index, and a process pool builds one PER WORKER rather than sharing it, which
exhausts RAM and locks the machine.  Do not add a worker pool here.  Keep
--limit small and run batches sequentially instead.
"""

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.collision import collision_extract as ce
from tes5_import.navmesh import build, corridor, clean_validate, world
from tools.navmesh.probe import load_cell


def _pathgrid_components(nodes, edges):
    parent = list(range(len(nodes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (i, j) in edges:
        if i < len(nodes) and j < len(nodes):
            a, b = find(i), find(j)
            if a != b:
                parent[a] = b
    groups = defaultdict(list)
    for i in range(len(nodes)):
        groups[find(i)].append(i)
    # A lone node with no edge is not a walk assertion; ignore singletons.
    return [g for g in groups.values() if len(g) > 1]


def audit(export_dir, cell_arg, verbose=True):
    ctx = load_cell(export_dir, cell_arg)
    nodes, edges = ctx['nodes'], ctx['edges']
    if not nodes or not edges:
        return None
    land = ctx['land'] if ctx['is_exterior'] else None
    ox, oy = ctx['grid_x'] * 4096.0, ctx['grid_y'] * 4096.0

    verts, tris = build.build_navmesh(
        ctx['refrs'], ctx['base_model'], ce.get_collision, nodes, edges,
        land_rec=land, origin_x=ox, origin_y=oy, doors=ctx.get('doors'))
    if not tris:
        return {'cell': cell_arg, 'pg': len(_pathgrid_components(nodes, edges)),
                'nav': 0, 'tris': 0, 'ok': False, 'note': 'NO MESH'}

    v = [tuple(map(float, p)) for p in verts]
    pg = _pathgrid_components(nodes, edges)
    comps = clean_validate.components([tuple(t) for t in tris])
    comps.sort(key=len, reverse=True)

    row = {'cell': cell_arg, 'pg': len(pg), 'nav': len(comps),
           'tris': len(tris), 'ok': len(comps) <= max(1, len(pg)),
           'sizes': [len(c) for c in comps[:12]], 'note': ''}

    if verbose and not row['ok']:
        # Which pathgrid component fragmented?  Map each pathgrid node to the
        # navmesh component covering it, then report components that a single
        # pathgrid component touches.
        w, b, lw = world.gather_cell_geometry(
            ctx['refrs'], ctx['base_model'], ce.get_collision, land_rec=land,
            origin_x=ox, origin_y=oy, split_land=True)
        import numpy as np
        if lw is not None and len(lw):
            w = np.concatenate([w, lw]) if len(w) else lw
        sample = corridor._surface_sampler(w)
        nz = [corridor._snap_node_z(sample, nodes[i][0], nodes[i][1],
                                   nodes[i][2]) for i in range(len(nodes))]
        tc = {}
        for ci, c in enumerate(comps):
            for ti in c:
                tc[ti] = ci
        cents = []
        for ti, t in enumerate(tris):
            cents.append((sum(v[i][0] for i in t) / 3.0,
                          sum(v[i][1] for i in t) / 3.0,
                          sum(v[i][2] for i in t) / 3.0, tc[ti]))
        node_comp = {}
        for i in range(len(nodes)):
            px, py, pz = nodes[i][0], nodes[i][1], nz[i]
            best = None
            for (cx, cy, cz, ci) in cents:
                d = (cx - px) ** 2 + (cy - py) ** 2 + (cz - pz) ** 2
                if best is None or d < best[0]:
                    best = (d, ci)
            if best and best[0] <= 160.0 ** 2:
                node_comp[i] = best[1]
        for gi, g in enumerate(pg):
            touched = sorted({node_comp[i] for i in g if i in node_comp})
            if len(touched) > 1:
                print('     pathgrid comp %d (%d nodes) spans navmesh comps %s'
                      % (gi, len(g), touched))
                # closest approach between the first two offending components
                a, bb = touched[0], touched[1]
                va = {i for ti in comps[a] for i in tris[ti]}
                vb = {i for ti in comps[bb] for i in tris[ti]}
                best = None
                for i in va:
                    for j in vb:
                        d = math.dist(v[i], v[j])
                        if best is None or d < best[0]:
                            best = (d, i, j)
                if best:
                    print('        comp%d<->comp%d closest %.2fu at %s'
                          % (a, bb, best[0],
                             tuple(round(x, 1) for x in v[best[1]])))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell')
    ap.add_argument('--cells')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=40,
                    help='max cells for --all (keep small: single-process)')
    a = ap.parse_args()

    if a.all:
        path = os.path.join(a.export, 'CELL.txt')
        names = []
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if line.startswith('EditorID='):
                    names.append(line[9:].strip())
        cells = names[:a.limit]
    else:
        cells = ([c.strip() for c in a.cells.split(',')] if a.cells
                 else [a.cell])

    rows = []
    for c in cells:
        try:
            r = audit(a.export, c, verbose=True)
        except Exception as e:
            r = {'cell': c, 'pg': -1, 'nav': -1, 'tris': 0, 'ok': False,
                 'sizes': [], 'note': 'ERROR %s' % e}
        if r:
            rows.append(r)
            print('%-34s pathgrid=%-3d navmesh=%-3d tris=%-5d %s %s %s'
                  % (r['cell'], r['pg'], r['nav'], r['tris'],
                     'OK ' if r['ok'] else 'BAD',
                     r.get('sizes') or '', r.get('note') or ''))
            sys.stdout.flush()

    if len(rows) > 1:
        bad = [r for r in rows if not r['ok']]
        print('\n%d cells audited, %d violate the invariant (%.1f%%)'
              % (len(rows), len(bad), 100.0 * len(bad) / max(1, len(rows))))
        for r in sorted(bad, key=lambda x: -(x['nav'] - max(1, x['pg'])))[:25]:
            print('  %-34s pathgrid=%-3d navmesh=%-3d tris=%-5d %s %s'
                  % (r['cell'], r['pg'], r['nav'], r['tris'],
                     r.get('sizes') or '', r.get('note') or ''))


if __name__ == '__main__':
    main()
