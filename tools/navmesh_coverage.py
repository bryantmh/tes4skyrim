"""Measure navmesh coverage against the ground the corridors specify.

THE CONTRACT this checks:

    Every point of the uncut corridor-ribbon union must be covered by EXACTLY
    ONE mesh triangle at that point's height.  Nothing missing, nothing stacked.

The reference is the union of the full-width ribbons laid on the pathgrid — the
mesh the corridor model is supposed to produce before any cutting.  Cutting is
only allowed to decide WHICH corridor owns a piece of that ground, never to
remove it.  So:

    MISSING  = reference ground with no triangle over it   -> lost coverage
    DOUBLE   = reference ground with 2+ triangles over it  -> stacked sheets

Both must be zero.  Comparison is PER STOREY (a Z band), so a two-floor
building whose floors sit on top of each other in plan view is not miscounted as
overlap — that mistake made a clean cell look 42% broken.

Usage:
    python tools/navmesh_coverage.py --cell AnvilFightersGuild
    python tools/navmesh_coverage.py --cell AnvilFightersGuild --dump temp/gaps.txt
    python tools/navmesh_coverage.py --cells AnvilFightersGuild,00005F86 --step 6

--dump writes the world coordinates of every failing sample, so a defect can be
located and rendered instead of hunted for by eye.
"""

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import build, corridor, corridor_clean, world  # noqa: E402
from tools.navmesh_probe import load_cell  # noqa: E402

# A sample and a triangle are on the same storey when their heights differ by
# less than this.  Matches corridor_cut.CUT_Z_TOLERANCE.
Z_BAND = 48.0


def _reference_strips(ctx):
    """The corridors as specified, before any cutting."""
    nodes, edges = ctx['nodes'], ctx['edges']
    land = ctx['land'] if ctx['is_exterior'] else None
    ox = ctx['grid_x'] * 4096.0 if ctx['is_exterior'] else 0.0
    oy = ctx['grid_y'] * 4096.0 if ctx['is_exterior'] else 0.0
    walk, _b, land_walk = world.gather_cell_geometry(
        ctx['refrs'], ctx['base_model'], ce.get_collision,
        land_rec=land, origin_x=ox, origin_y=oy, split_land=True)
    if land_walk is not None and len(land_walk):
        import numpy as np
        walk = (np.concatenate([walk, land_walk]) if len(walk) else land_walk)
    sample = corridor._surface_sampler(walk)
    node_z = [corridor._snap_node_z(sample, nodes[i][0], nodes[i][1], nodes[i][2])
              for i in range(len(nodes))]
    return corridor._build_corridor_strips(nodes, edges, node_z)


def _ribbon_heights(strips, px, py):
    """Heights at which the reference ribbons cover (px, py), one per storey."""
    zs = []
    for s in strips:
        ax, ay, az = s['na']
        bx, by, bz = s['nb']
        dx, dy = bx - ax, by - ay
        d2 = dx * dx + dy * dy
        t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((px - ax) * dx +
                                                     (py - ay) * dy) / d2))
        cx, cy = ax + dx * t, ay + dy * t
        if math.hypot(px - cx, py - cy) <= s['half']:
            zs.append(az + (bz - az) * t)
    if not zs:
        return []
    zs.sort()
    storeys = [[zs[0]]]
    for z in zs[1:]:
        if z - storeys[-1][-1] <= Z_BAND:
            storeys[-1].append(z)
        else:
            storeys.append([z])
    return [sum(g) / len(g) for g in storeys]


def _tri_heights_at(verts, tris, px, py):
    """Heights of the mesh triangles covering (px, py).

    Returns (inclusive, strict):
      inclusive — triangles containing the point, edges and vertices included;
                  used for COVERAGE, so a sample landing exactly on a shared
                  edge still counts as covered.
      strict    — triangles whose INTERIOR contains the point; used for
                  OVERLAP, because a point on a shared edge legitimately lies in
                  both neighbours and is not a defect.  Counting those made a
                  provably non-overlapping mesh report ~25% doubled.
    """
    inclusive = []
    strict = []
    for (a, b, c) in tris:
        va, vb, vc = verts[a], verts[b], verts[c]
        d = ((vb[1] - vc[1]) * (va[0] - vc[0]) +
             (vc[0] - vb[0]) * (va[1] - vc[1]))
        if abs(d) < 1e-9:
            continue
        l0 = ((vb[1] - vc[1]) * (px - vc[0]) +
              (vc[0] - vb[0]) * (py - vc[1])) / d
        l1 = ((vc[1] - va[1]) * (px - vc[0]) +
              (va[0] - vc[0]) * (py - vc[1])) / d
        l2 = 1.0 - l0 - l1
        if l0 < -_EDGE_EPS or l1 < -_EDGE_EPS or l2 < -_EDGE_EPS:
            continue
        z = l0 * va[2] + l1 * vb[2] + l2 * vc[2]
        inclusive.append(z)
        if l0 > _EDGE_EPS and l1 > _EDGE_EPS and l2 > _EDGE_EPS:
            strict.append(z)
    return inclusive, strict


# Barycentric margin separating "on an edge" from "strictly inside".
_EDGE_EPS = 1e-9


def measure(export_dir, cell, step=8.0, dump=None):
    ctx = load_cell(export_dir, cell)
    strips = _reference_strips(ctx)
    if not strips:
        print('%-28s no corridors' % cell)
        return None

    land = ctx['land'] if ctx['is_exterior'] else None
    ox = ctx['grid_x'] * 4096.0 if ctx['is_exterior'] else 0.0
    oy = ctx['grid_y'] * 4096.0 if ctx['is_exterior'] else 0.0
    verts, tris = build.build_navmesh(
        ctx['refrs'], ctx['base_model'], ce.get_collision,
        ctx['nodes'], ctx['edges'], land_rec=land,
        origin_x=ox, origin_y=oy, doors=ctx.get('doors'))
    if not tris:
        print('%-28s EMPTY MESH' % cell)
        return None

    xs, ys = [], []
    for s in strips:
        xs += [s['a'][0], s['b'][0]]
        ys += [s['a'][1], s['b'][1]]
    pad = strips[0]['half'] + step
    minx, maxx = min(xs) - pad, max(xs) + pad
    miny, maxy = min(ys) - pad, max(ys) + pad

    total = missing = double = 0
    gaps, dups = [], []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            want = _ribbon_heights(strips, x, y)
            if want:
                inc, strict = _tri_heights_at(verts, tris, x, y)
                for wz in want:
                    total += 1
                    if not any(abs(gz - wz) <= Z_BAND for gz in inc):
                        missing += 1
                        gaps.append((x, y, wz))
                    n = sum(1 for gz in strict if abs(gz - wz) <= Z_BAND)
                    if n > 1:
                        double += 1
                        dups.append((x, y, wz, n))
            x += step
        y += step

    comps = corridor_clean.components(tris)
    cov = 100.0 * (total - missing) / total if total else 0.0
    dbl = 100.0 * double / total if total else 0.0
    print('%-28s coverage=%6.2f%%  overlap=%5.2f%%  missing=%-5d doubled=%-5d '
          'tris=%d comps=%d'
          % (cell, cov, dbl, missing, double, len(tris), len(comps)))

    if dump:
        with open(dump, 'w', encoding='utf-8') as fh:
            fh.write('# MISSING x y z\n')
            for (x, y, z) in gaps:
                fh.write('MISSING %.1f %.1f %.1f\n' % (x, y, z))
            fh.write('# DOUBLED x y z count\n')
            for (x, y, z, n) in dups:
                fh.write('DOUBLED %.1f %.1f %.1f %d\n' % (x, y, z, n))
        print('  wrote %s (%d missing, %d doubled)'
              % (dump, len(gaps), len(dups)))
    return missing, double, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell')
    ap.add_argument('--cells', help='comma-separated list')
    ap.add_argument('--step', type=float, default=8.0)
    ap.add_argument('--dump', help='write failing sample coordinates here')
    args = ap.parse_args()

    cells = ([args.cell] if args.cell else
             args.cells.split(',') if args.cells else [])
    if not cells:
        ap.error('need --cell or --cells')
    for c in cells:
        measure(args.export, c.strip(), step=args.step, dump=args.dump)


if __name__ == '__main__':
    main()
