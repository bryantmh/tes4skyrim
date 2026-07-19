"""Render the NEW collision-driven navmesh for a cell, over the collision layer.

This is the primary iteration tool.  Unlike the old renderer, it draws the real
WALLS (blocking collision), which is what made the previous debugging blind:
"the walls don't show on the image" — because the old converter never loaded them.

    python tools/navmesh_preview.py --cell AnvilFightersGuild
    python tools/navmesh_preview.py --cell 00003313 --out temp/ext.png

Zoom-in on a spot (world coords) with triangle indices + quality colouring:

    python tools/navmesh_preview.py --cell X --focus 1234,-567 --span 500 --ids
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import build  # noqa: E402
from tools.navmesh_probe import load_cell, cell_geometry  # noqa: E402


def render(export_dir, cell_arg, out_path, size, focus=None, span=None,
           ids=False, quality=False):
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
        origin_x=ctx['grid_x'] * 4096.0, origin_y=ctx['grid_y'] * 4096.0,
        doors=ctx.get('doors'))
    dt = time.time() - t0
    print('navmesh: %d verts %d tris %d doors  (%.2fs)'
          % (len(verts), len(tris), len(ctx.get('doors') or ()), dt))

    # Frame the view on the pathgrid + navmesh, NOT the raw collision: one
    # outlier REFR (FelgageldtCave has collision 70k units away) otherwise
    # zooms the whole cell down to a blob in the corner.  Collision is still
    # DRAWN wherever it falls; it just doesn't get to pick the framing.
    xs, ys = [], []
    for n in ctx['nodes']:
        xs.append(n[0])
        ys.append(n[1])
    for v in verts:
        xs.append(v[0])
        ys.append(v[1])
    if not xs:
        print('nothing to draw')
        return
    if focus is not None:
        # Zoom window: world-coord centre + half-size.
        fx, fy = focus
        half = span or 400.0
        x0, x1 = fx - half, fx + half
        y0, y1 = fy - half, fy + half
    else:
        pad = 400.0
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

    # The generated navmesh, coloured by height so a ceiling triangle or a
    # missing floor is visible in the top-down view (a flat single colour hid
    # exactly the wrong-floor / missing-storey bugs).
    zs = [verts[i][2] for t in tris for i in t]
    zlo, zhi = (min(zs), max(zs)) if zs else (0.0, 1.0)
    zspan = max(1.0, zhi - zlo)

    def zcolor(z):
        t = (z - zlo) / zspan          # 0 low .. 1 high
        # low = blue, mid = green, high = orange
        r = int(40 + 200 * t)
        g = int(90 + 130 * (1 - abs(t - 0.5) * 2))
        b = int(220 * (1 - t))
        return (r, g, b, 120)

    import math as _mm

    def _quality_color(a, b, c):
        """Red = steep, magenta = needle/sliver, else height colour."""
        va, vb, vc = verts[a], verts[b], verts[c]
        e = sorted((_mm.dist(va, vb), _mm.dist(vb, vc), _mm.dist(vc, va)))
        ux, uy, uz = vb[0] - va[0], vb[1] - va[1], vb[2] - va[2]
        wx, wy, wz = vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        ln = _mm.sqrt(nx * nx + ny * ny + nz * nz)
        area = 0.5 * ln
        if area < 1e-6:
            return (255, 0, 255, 200)
        if abs(nz) / ln < _mm.cos(_mm.radians(50.0)):
            return (255, 40, 40, 190)                   # steep
        if e[2] * e[2] / (4.0 * area) > 6.0 or e[2] / max(e[0], 1e-9) > 4.0:
            return (235, 60, 235, 170)                  # needle / bad ratio
        return None

    for ti, (a, b, c) in enumerate(tris):
        pa, pb, pc = (px(verts[a][0], verts[a][1]),
                      px(verts[b][0], verts[b][1]),
                      px(verts[c][0], verts[c][1]))
        zc = (verts[a][2] + verts[b][2] + verts[c][2]) / 3.0
        fill = None
        if quality:
            fill = _quality_color(a, b, c)
        d.polygon([pa, pb, pc], fill=fill or zcolor(zc),
                  outline=(20, 240, 130, 180))
        if ids:
            cx = (pa[0] + pb[0] + pc[0]) / 3.0
            cy = (pa[1] + pb[1] + pc[1]) / 3.0
            if -20 < cx < W + 20 and -20 < cy < H + 20:
                d.text((cx, cy), str(ti), fill=(255, 255, 255, 220),
                       anchor='mm')

    # Mesh vertices (visible when zoomed in): white dots + height label.
    if focus is not None:
        for v in verts:
            p = px(v[0], v[1])
            if -10 < p[0] < W + 10 and -10 < p[1] < H + 10:
                d.ellipse([p[0] - 2, p[1] - 2, p[0] + 2, p[1] + 2],
                          fill=(255, 255, 255, 230))
                if ids:
                    d.text((p[0] + 3, p[1] - 3), '%.0f' % v[2],
                           fill=(180, 200, 255, 200))

    # Pathgrid on top.
    for (i, j) in ctx['edges']:
        a, b = ctx['nodes'][i], ctx['nodes'][j]
        d.line([px(a[0], a[1]), px(b[0], b[1])], fill=(235, 220, 70, 170))
    for (x, y, _z) in ctx['nodes']:
        p = px(x, y)
        d.ellipse([p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3],
                  fill=(255, 240, 90))

    # Doors: cyan diamond + threshold line (teleport doors get a white core),
    # so door-quad placement is checkable against the door itself.
    import math as _m
    for (x, y, _z, rz, is_tp) in ctx.get('doors') or ():
        p = px(x, y)
        tx, ty = _m.cos(rz), _m.sin(rz)
        a = px(x - 48 * tx, y - 48 * ty)
        b = px(x + 48 * tx, y + 48 * ty)
        d.line([a, b], fill=(60, 230, 255, 230), width=2)
        d.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4],
                  fill=(255, 255, 255) if is_tp else (60, 230, 255))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    print('wrote %s (%dx%d)' % (out_path, W, H))

    # Elevation (looking along +Y): X across, Z up.  This is the definitive view
    # for wrong-floor / missing-storey bugs — floors separate cleanly by height.
    _render_elevation(out_path, walk, block, verts, tris, ctx, x0, x1, size)


def _render_elevation(out_path, walk, block, verts, tris, ctx, x0, x1, size):
    zs = [v[2] for v in verts]
    for t in (walk, block):
        if len(t):
            zs += [float(t[:, :, 2].min()), float(t[:, :, 2].max())]
    zs += [n[2] for n in ctx['nodes']]
    if not zs:
        return
    z0, z1 = min(zs) - 50, max(zs) + 50
    S = size / max(x1 - x0, z1 - z0)
    W, H = max(1, int((x1 - x0) * S)), max(1, int((z1 - z0) * S))
    img = Image.new('RGB', (W, H), (18, 18, 22))
    d = ImageDraw.Draw(img, 'RGBA')

    def px(x, z):
        return ((x - x0) * S, H - (z - z0) * S)

    for t in block:
        d.polygon([px(t[0][0], t[0][2]), px(t[1][0], t[1][2]),
                   px(t[2][0], t[2][2])], fill=(190, 50, 45, 40))
    for t in walk:
        d.polygon([px(t[0][0], t[0][2]), px(t[1][0], t[1][2]),
                   px(t[2][0], t[2][2])], fill=(55, 70, 60, 40))
    for (a, b, c) in tris:
        d.polygon([px(verts[a][0], verts[a][2]), px(verts[b][0], verts[b][2]),
                   px(verts[c][0], verts[c][2])],
                  fill=(50, 200, 110, 110), outline=(30, 240, 130, 160))
    for (i, j) in ctx['edges']:
        a, b = ctx['nodes'][i], ctx['nodes'][j]
        d.line([px(a[0], a[2]), px(b[0], b[2])], fill=(235, 220, 70, 200))
    for (x, _y, z) in ctx['nodes']:
        p = px(x, z)
        d.ellipse([p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3], fill=(255, 240, 90))

    ele = out_path.rsplit('.', 1)[0] + '_elev.png'
    img.save(ele)
    print('wrote %s (%dx%d)' % (ele, W, H))


def _render_one(args):
    export, cell, out, size, focus, span, ids, quality = args
    try:
        render(export, cell, out, size, focus, span, ids, quality)
    except SystemExit as e:
        print('%s: %s' % (cell, e))
    return cell


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True,
                    help='EditorID/FormID, or a comma-separated list')
    ap.add_argument('--out', default=None,
                    help='output png (single cell only; ignored for a list)')
    ap.add_argument('--size', type=int, default=1100)
    ap.add_argument('--focus', default=None,
                    help='zoom centre as world "X,Y" (single cell)')
    ap.add_argument('--span', type=float, default=None,
                    help='zoom half-size in game units (with --focus)')
    ap.add_argument('--ids', action='store_true',
                    help='label triangle indices + vertex heights (zoomed)')
    ap.add_argument('--quality', action='store_true',
                    help='colour steep (red) / sliver (magenta) triangles')
    ap.add_argument('--workers', type=int,
                    default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    focus = None
    if a.focus:
        fx, fy = (float(v) for v in a.focus.split(','))
        focus = (fx, fy)

    def _outname(cell):
        # "grid:2:4" etc. — colons are not legal in Windows filenames.
        return 'temp/navnew_%s.png' % cell.replace(':', '_')

    cells = [c.strip() for c in a.cell.split(',') if c.strip()]
    if len(cells) == 1:
        render(a.export, cells[0], a.out or _outname(cells[0]), a.size,
               focus, a.span, a.ids, a.quality)
        return

    # Several cells: render them in parallel.  Each worker re-reads the cached
    # export index (see navmesh_probe.load_by_type), so this scales with cores.
    jobs = [(a.export, c, _outname(c), a.size, focus, a.span, a.ids, a.quality)
            for c in cells]
    with ProcessPoolExecutor(max_workers=min(a.workers, len(jobs))) as ex:
        for _ in ex.map(_render_one, jobs):
            pass


if __name__ == '__main__':
    main()
