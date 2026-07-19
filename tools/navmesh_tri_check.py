"""Check EVERY triangle of a cell's generated navmesh against quality rules.

For each triangle: slope, vertical extent, edge lengths/ratio, aspect
(longest^2 / 4*area), area, and FLOAT — how far the triangle sits off the real
walkable collision surface at its own XY (the "navmesh juts out" defect: a
vertex pulled above a flat slope reads as a float on an existing surface).

    python tools/navmesh_tri_check.py --cell AnvilFightersGuild
    python tools/navmesh_tri_check.py --cell Wendir02,grid:47:6 --all
    python tools/navmesh_tri_check.py --cell X --csv temp/tris.csv

Classification (a triangle can carry several flags):
    STEEP   slope > steep_deg (default 50) AND zspan > MAX_CLIMB
    NEEDLE  aspect > aspect_lim (default MAX_ASPECT) -- long thin sliver
    RATIO   longest/shortest edge > ratio_lim (default 4) -- "one side way
            shorter than the others"
    MICRO   area < micro_area (default 64 u^2)
    FLOAT   a vertex sits > float_tol (24u) off every walkable collision
            surface at its XY while such a surface exists within 200u in Z
            (synthesized pathgrid ribbons over missing geometry are exempt --
            there is no surface to compare against)
"""

import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import build, params  # noqa: E402
from tools.navmesh_probe import load_cell, cell_geometry  # noqa: E402


def surface_z_index(walk_tris, bucket=128.0):
    """Spatial hash of walkable collision triangles by XY bbox."""
    buckets = {}
    if walk_tris is None or not len(walk_tris):
        return buckets, bucket
    x0 = walk_tris[:, :, 0].min(axis=1)
    x1 = walk_tris[:, :, 0].max(axis=1)
    y0 = walk_tris[:, :, 1].min(axis=1)
    y1 = walk_tris[:, :, 1].max(axis=1)
    for i in range(len(walk_tris)):
        for bx in range(int(x0[i] // bucket), int(x1[i] // bucket) + 1):
            for by in range(int(y0[i] // bucket), int(y1[i] // bucket) + 1):
                buckets.setdefault((bx, by), []).append(i)
    return buckets, bucket


def surface_dz(px, py, pz, walk_tris, buckets, bucket, zwin=200.0):
    """(dz, found): signed distance from pz to the NEAREST walkable collision
    surface directly at (px,py), or (None, False) when no surface exists
    within zwin."""
    cand = buckets.get((int(px // bucket), int(py // bucket)))
    if not cand:
        return None, False
    best = None
    for i in cand:
        a, b, c = walk_tris[i]
        v0x, v0y = c[0] - a[0], c[1] - a[1]
        v1x, v1y = b[0] - a[0], b[1] - a[1]
        v2x, v2y = px - a[0], py - a[1]
        den = v0x * v1y - v1x * v0y
        if abs(den) < 1e-9:
            continue
        u = (v2x * v1y - v1x * v2y) / den
        v = (v0x * v2y - v2x * v0y) / den
        if u < -0.02 or v < -0.02 or u + v > 1.02:
            continue
        z = a[2] + u * (c[2] - a[2]) + v * (b[2] - a[2])
        dz = pz - z
        if abs(dz) <= zwin and (best is None or abs(dz) < abs(best)):
            best = dz
    return best, best is not None


def check_cell(cell_arg, export_dir, args):
    ctx = load_cell(export_dir, cell_arg)
    walk, _block, _b = cell_geometry(ctx)
    verts, tris = build.build_navmesh(
        ctx['refrs'], ctx['base_model'], ce.get_collision,
        ctx['nodes'], ctx['edges'],
        land_rec=ctx['land'] if ctx['is_exterior'] else None,
        origin_x=ctx['grid_x'] * 4096.0, origin_y=ctx['grid_y'] * 4096.0,
        doors=ctx.get('doors'))
    name = ctx['cell'].get('EditorID') or ctx['cell_fid']
    if not tris:
        print('%s: NO NAVMESH' % name)
        return []

    buckets, bucket = surface_z_index(walk)
    v = np.asarray(verts)

    # Per-vertex float against the collision surface (computed once; a vertex
    # is shared by many triangles).
    vfloat = {}
    for vi in range(len(v)):
        dz, found = surface_dz(v[vi][0], v[vi][1], v[vi][2],
                               walk, buckets, bucket)
        vfloat[vi] = (dz, found)

    rows = []
    for ti, (a, b, c) in enumerate(tris):
        pa, pb, pc = v[a], v[b], v[c]
        e = sorted((float(np.linalg.norm(pa - pb)),
                    float(np.linalg.norm(pb - pc)),
                    float(np.linalg.norm(pc - pa))))
        n = np.cross(pb - pa, pc - pa)
        ln = float(np.linalg.norm(n))
        area = 0.5 * ln
        slope = 90.0 if ln < 1e-9 else math.degrees(
            math.acos(min(1.0, abs(float(n[2])) / ln)))
        zspan = float(max(pa[2], pb[2], pc[2]) - min(pa[2], pb[2], pc[2]))
        aspect = 1e9 if area < 1e-6 else e[2] * e[2] / (4.0 * area)
        ratio = e[2] / max(e[0], 1e-9)
        floats = [vfloat[i] for i in (a, b, c)]
        worst_float = 0.0
        for (dz, found) in floats:
            if found and abs(dz) > abs(worst_float):
                worst_float = dz

        flags = []
        if slope > args.steep_deg and zspan > params.MAX_CLIMB:
            flags.append('STEEP')
        if aspect > args.aspect_lim:
            flags.append('NEEDLE')
        if ratio > args.ratio_lim:
            flags.append('RATIO')
        if area < args.micro_area:
            flags.append('MICRO')
        # JUT: mesh hovering ABOVE a real surface (the "juts out" defect).
        # SINK: mesh below the nearest surface — usually a synthesized ribbon
        # in a room whose floor shell lives in a neighbouring cell, with some
        # unrelated geometry overhead; informational, not a hard defect.
        if worst_float > args.float_tol:
            flags.append('JUT')
        elif worst_float < -args.float_tol:
            flags.append('SINK')

        cx = float(pa[0] + pb[0] + pc[0]) / 3.0
        cy = float(pa[1] + pb[1] + pc[1]) / 3.0
        cz = float(pa[2] + pb[2] + pc[2]) / 3.0
        rows.append({
            'tri': ti, 'x': cx, 'y': cy, 'z': cz, 'slope': slope,
            'zspan': zspan, 'emin': e[0], 'emax': e[2], 'ratio': ratio,
            'aspect': aspect, 'area': area, 'float': worst_float,
            'flags': '+'.join(flags),
        })

    flagged = [r for r in rows if r['flags']]
    counts = {}
    for r in flagged:
        for f in r['flags'].split('+'):
            counts[f] = counts.get(f, 0) + 1
    print('%s: %d tris, %d flagged  %s' % (
        name, len(rows), len(flagged),
        ' '.join('%s=%d' % kv for kv in sorted(counts.items()))))

    show = rows if args.all else flagged
    show = sorted(show, key=lambda r: (r['flags'] == '', -abs(r['float']),
                                       -r['slope'], -r['aspect']))
    if not args.quiet:
        hdr = ('  tri     x        y        z    slope zspan  emin  emax '
               'ratio aspect  area  float flags')
        print(hdr)
        for r in show[:args.max_rows]:
            print('  %4d %8.0f %8.0f %6.0f %5.1f %5.0f %5.0f %5.0f %5.1f '
                  '%6.1f %5.0f %6.1f %s' %
                  (r['tri'], r['x'], r['y'], r['z'], r['slope'], r['zspan'],
                   r['emin'], r['emax'], r['ratio'], r['aspect'], r['area'],
                   r['float'], r['flags']))
        if len(show) > args.max_rows:
            print('  ... %d more rows' % (len(show) - args.max_rows))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True,
                    help='EditorID/FormID/grid:X:Y, comma-separated list ok')
    ap.add_argument('--all', action='store_true',
                    help='list every triangle, not just flagged ones')
    ap.add_argument('--quiet', action='store_true',
                    help='summary line only')
    ap.add_argument('--csv', default=None, help='write full table as CSV')
    ap.add_argument('--max-rows', type=int, default=80)
    ap.add_argument('--steep-deg', type=float, default=50.0)
    ap.add_argument('--aspect-lim', type=float, default=params.MAX_ASPECT * 1.5)
    ap.add_argument('--ratio-lim', type=float, default=4.0)
    ap.add_argument('--micro-area', type=float, default=64.0)
    ap.add_argument('--float-tol', type=float, default=24.0)
    a = ap.parse_args()

    all_rows = []
    for cell in [c.strip() for c in a.cell.split(',') if c.strip()]:
        all_rows += check_cell(cell, a.export, a)

    if a.csv and all_rows:
        import csv as _csv
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
        with open(a.csv, 'w', newline='') as fh:
            wr = _csv.DictWriter(fh, fieldnames=list(all_rows[0]))
            wr.writeheader()
            wr.writerows(all_rows)
        print('wrote %s (%d rows)' % (a.csv, len(all_rows)))


if __name__ == '__main__':
    main()
