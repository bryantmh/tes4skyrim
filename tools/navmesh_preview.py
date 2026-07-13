"""Render the NEW collision-driven navmesh for a cell, over the collision layer.

This is the primary iteration tool.  Unlike the old renderer, it draws the real
WALLS (blocking collision), which is what made the previous debugging blind:
"the walls don't show on the image" — because the old converter never loaded them.

    python tools/navmesh_preview.py --cell AnvilFightersGuild
    python tools/navmesh_preview.py --cell 00003313 --out temp/ext.png
"""

import argparse
import os
import sys
import time

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import build  # noqa: E402
from tools.navmesh_probe import load_cell, cell_geometry  # noqa: E402


def render(export_dir, cell_arg, out_path, size):
    ctx = load_cell(export_dir, cell_arg)
    walk, block, _b = cell_geometry(ctx)
    print('cell %s (%s) exterior=%s' %
          (ctx['cell_fid'], ctx['cell'].get('EditorID'), ctx['is_exterior']))
    print('collision: walkable=%d blocking=%d  pathgrid: %d nodes %d edges'
          % (len(walk), len(block), len(ctx['nodes']), len(ctx['edges'])))

    t0 = time.time()
    verts, tris = build.build_navmesh(
        ctx['refrs'], ctx['base_model'], ce.get_collision,
        ctx['nodes'], ctx['edges'],
        land_rec=ctx['land'] if ctx['is_exterior'] else None,
        origin_x=ctx['grid_x'] * 4096.0, origin_y=ctx['grid_y'] * 4096.0)
    dt = time.time() - t0
    print('navmesh: %d verts %d tris  (%.2fs)' % (len(verts), len(tris), dt))

    xs, ys = [], []
    for t in (walk, block):
        if len(t):
            xs += [float(t[:, :, 0].min()), float(t[:, :, 0].max())]
            ys += [float(t[:, :, 1].min()), float(t[:, :, 1].max())]
    for n in ctx['nodes']:
        xs.append(n[0])
        ys.append(n[1])
    if not xs:
        print('nothing to draw')
        return
    pad = 100.0
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad
    S = size / max(x1 - x0, y1 - y0)
    W, H = max(1, int((x1 - x0) * S)), max(1, int((y1 - y0) * S))

    img = Image.new('RGB', (W, H), (18, 18, 22))
    d = ImageDraw.Draw(img, 'RGBA')

    def px(x, y):
        return ((x - x0) * S, H - (y - y0) * S)

    # Walkable collision (dim) — the floor the navmesh may use.
    for t in walk:
        d.polygon([px(t[0][0], t[0][1]), px(t[1][0], t[1][1]),
                   px(t[2][0], t[2][1])], fill=(55, 70, 60, 70))
    # Blocking collision (red) — THE WALLS.
    for t in block:
        d.polygon([px(t[0][0], t[0][1]), px(t[1][0], t[1][1]),
                   px(t[2][0], t[2][1])], fill=(190, 50, 45, 90))

    # The generated navmesh.
    for (a, b, c) in tris:
        pa, pb, pc = (px(verts[a][0], verts[a][1]),
                      px(verts[b][0], verts[b][1]),
                      px(verts[c][0], verts[c][1]))
        d.polygon([pa, pb, pc], fill=(50, 200, 110, 105),
                  outline=(30, 240, 130, 200))

    # Pathgrid on top.
    for (i, j) in ctx['edges']:
        a, b = ctx['nodes'][i], ctx['nodes'][j]
        d.line([px(a[0], a[1]), px(b[0], b[1])], fill=(235, 220, 70, 170))
    for (x, y, _z) in ctx['nodes']:
        p = px(x, y)
        d.ellipse([p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3],
                  fill=(255, 240, 90))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    print('wrote %s (%dx%d)' % (out_path, W, H))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True)
    ap.add_argument('--out', default=None)
    ap.add_argument('--size', type=int, default=1100)
    a = ap.parse_args()
    render(a.export, a.cell, a.out or 'temp/navnew_%s.png' % a.cell, a.size)


if __name__ == '__main__':
    main()
