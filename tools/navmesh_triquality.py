"""Report triangle-quality stats for a cell's corridor navmesh.

Builds the Phase-1 corridor mesh for one cell and prints:
  * triangle count / vertex count
  * edge-length distribution (min/median/max side)
  * per-triangle edge RATIO (longest side / shortest side) distribution — the
    "one side much shorter than the others" needle metric the author flagged
  * how many triangles are needles (ratio > threshold)

    python tools/navmesh_triquality.py --cell AnvilFightersGuild
"""

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import build as nbuild  # noqa: E402
from tools.navmesh_probe import load_cell  # noqa: E402


def _sides(v, t):
    a, b, c = v[t[0]], v[t[1]], v[t[2]]
    return sorted((math.dist(a, b), math.dist(b, c), math.dist(c, a)))


def _pct(vals, p):
    if not vals:
        return 0.0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(p * len(vals)))]


def report(verts, tris, ratio_thresh=3.0):
    print('verts=%d  tris=%d' % (len(verts), len(tris)))
    if not tris:
        return
    ratios, shorts, longs = [], [], []
    for t in tris:
        s = _sides(verts, t)
        shorts.append(s[0])
        longs.append(s[2])
        ratios.append(s[2] / s[0] if s[0] > 1e-6 else 999.0)

    print('shortest side:  p10=%.0f p50=%.0f p90=%.0f  min=%.0f'
          % (_pct(shorts, .1), _pct(shorts, .5), _pct(shorts, .9), min(shorts)))
    print('longest side:   p10=%.0f p50=%.0f p90=%.0f  max=%.0f'
          % (_pct(longs, .1), _pct(longs, .5), _pct(longs, .9), max(longs)))
    print('edge ratio:     p50=%.2f p90=%.2f p99=%.2f  max=%.2f'
          % (_pct(ratios, .5), _pct(ratios, .9), _pct(ratios, .99), max(ratios)))
    needles = sum(1 for r in ratios if r > ratio_thresh)
    print('needles (ratio > %.1f): %d / %d  (%.1f%%)'
          % (ratio_thresh, needles, len(tris), 100.0 * needles / len(tris)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True)
    ap.add_argument('--ratio', type=float, default=3.0)
    a = ap.parse_args()

    ctx = load_cell(a.export, a.cell)
    ox = ctx['grid_x'] * 4096.0
    oy = ctx['grid_y'] * 4096.0
    verts, tris = nbuild.build_navmesh(
        ctx['refrs'], ctx['base_model'], ce.get_collision,
        ctx['nodes'], ctx['edges'],
        land_rec=ctx['land'] if ctx['is_exterior'] else None,
        origin_x=ox, origin_y=oy, doors=ctx['doors'])
    report(verts, tris, a.ratio)


if __name__ == '__main__':
    main()
