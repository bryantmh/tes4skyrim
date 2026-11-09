"""Measure how far a cell's navmesh floats above (or sinks below) real collision.

The corridor builder derives vertex heights from the pathgrid line, and the
pathgrid HOVERS above the walked surface by an arbitrary amount.  If that hover
survives into the mesh, two sheets that should meet at a floor level end up
offset in Z by the hover difference and never weld -- the mesh reads as several
components even though it looks continuous in plan.

This reports, per vertex, mesh_z - collision_z (positive = floating above the
floor), bucketed, plus the worst offenders and a per-Z-band breakdown so an
upper storey can be compared against the ground floor.

    python tools/navmesh/surface_residual.py --cell AnvilPinarusInventiusHouse
    python tools/navmesh/surface_residual.py --cell X --band 60,80
"""

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np

from asset_convert.collision import collision_extract as ce
from tes5_import.navmesh import (build, corridor, clean_validate,
                                 world)
from tools.navmesh.probe import load_cell


def measure(export_dir, cell):
    ctx = load_cell(export_dir, cell)
    nodes, edges = ctx['nodes'], ctx['edges']
    land = ctx['land'] if ctx['is_exterior'] else None
    ox, oy = ctx['grid_x'] * 4096.0, ctx['grid_y'] * 4096.0

    verts, tris = build.build_navmesh(
        ctx['refrs'], ctx['base_model'], ce.get_collision, nodes, edges,
        land_rec=land, origin_x=ox, origin_y=oy, doors=ctx.get('doors'))
    v = [tuple(map(float, p)) for p in verts]
    tris = [tuple(t) for t in tris]

    w, b, lw = world.gather_cell_geometry(
        ctx['refrs'], ctx['base_model'], ce.get_collision, land_rec=land,
        origin_x=ox, origin_y=oy, split_land=True)
    if lw is not None and len(lw):
        w = np.concatenate([w, lw]) if len(w) else lw
    sample = corridor._surface_sampler(w)

    comps = clean_validate.components(tris)
    comps.sort(key=len, reverse=True)
    vcomp = {}
    for ci, c in enumerate(comps):
        for ti in c:
            for i in tris[ti]:
                vcomp[i] = ci
    return v, tris, comps, vcomp, sample


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True)
    ap.add_argument('--band', help='only vertices with lo,hi mesh Z')
    a = ap.parse_args()

    v, tris, comps, vcomp, sample = measure(a.export, a.cell)
    print('%s: %d tris, components %s'
          % (a.cell, len(tris), [len(c) for c in comps[:10]]))

    band = None
    if a.band:
        lo, hi = (float(x) for x in a.band.split(','))
        band = (lo, hi)

    used = sorted({i for t in tris for i in t})
    rows = []
    for i in used:
        x, y, z = v[i]
        if band and not (band[0] <= z <= band[1]):
            continue
        s = sample(x, y, z)
        if s is None:
            rows.append((None, i))
            continue
        rows.append((z - s, i))

    have = [(d, i) for (d, i) in rows if d is not None]
    miss = [i for (d, i) in rows if d is None]
    if not have:
        print('no vertices with collision beneath them')
        return

    ds = sorted(d for (d, _i) in have)
    n = len(ds)
    print('\n%d vertices measured (%d with no collision under them)'
          % (n, len(miss)))
    print('  mesh_z - collision_z:  min=%.2f  p50=%.2f  p90=%.2f  max=%.2f'
          % (ds[0], ds[n // 2], ds[min(n - 1, int(n * 0.9))], ds[-1]))

    buckets = [(-1e9, -8), (-8, -2), (-2, 2), (2, 8), (8, 16), (16, 34),
               (34, 1e9)]
    print('\n  distribution:')
    for (lo, hi) in buckets:
        c = sum(1 for d in ds if lo <= d < hi)
        if c:
            print('    %8s .. %-8s  %5d  (%.1f%%)'
                  % (('%.0f' % lo if lo > -1e8 else '-inf'),
                     ('%.0f' % hi if hi < 1e8 else '+inf'), c,
                     100.0 * c / n))

    # per-component, so an upper storey can be compared with the ground floor
    per = defaultdict(list)
    for (d, i) in have:
        per[vcomp.get(i, -1)].append((d, v[i][2]))
    print('\n  by component:')
    for ci in sorted(per):
        vals = sorted(x[0] for x in per[ci])
        zs = [x[1] for x in per[ci]]
        m = len(vals)
        print('    comp%-3d n=%-5d float p50=%7.2f p90=%7.2f max=%7.2f   '
              'meshZ %.0f..%.0f'
              % (ci, m, vals[m // 2], vals[min(m - 1, int(m * 0.9))], vals[-1],
                 min(zs), max(zs)))

    print('\n  worst floaters:')
    for (d, i) in sorted(have, reverse=True)[:15]:
        print('    +%7.2f  v%-5d comp%-3d %s'
              % (d, i, vcomp.get(i, -1), tuple(round(x, 1) for x in v[i])))


if __name__ == '__main__':
    main()
