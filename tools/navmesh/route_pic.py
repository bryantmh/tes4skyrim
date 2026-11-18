"""Picture of a walk over every cell it crosses, for the user and for diagnosis.

    python tools/navmesh/route_pic.py --plugin P (--world WRLD | --interior CELL) START TARGET OUT.png
        [--scale 0.25] [--box x0,y0,x1,y1] [--game-stop x,y,z] [--track file] [--outline model-part]

Draws each cell's collision layers and shipped navmesh, cell names, the planned route coloured by how the mesh
under it sits on the floor, the offline predictor's stop, the game's stop and logged track, and a key.
Points are `x,y,z`; a track file holds one `x,y,z` per line.
"""

import argparse
import functools
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.plugin_masters import export_root, master_export_dir
from output_layout import plugin_esm
from tools.cellview.bake import find_by_coords
from tools.navmesh.floor_points import export_dir
from tools.navmesh.index import NavIndex
from tools.navmesh.pathfind import World, plan
from tools.navmesh.pathfind_body import Collision
from tools.navmesh.pathfind_calibrate import predict
from tools.navmesh.slab_map import (Canvas, draw_axes, draw_layers, draw_mesh, navm_arrays, shipped_target,
                                    surfaces, walk_collision)

#: Route resample step and the margin around the route when no box is given.
STEP, MARGIN = 32.0, 400.0
#: Body radius the predictor walks with.
RADIUS = 22.0
GREEN, ORANGE, RED, GREY = (60, 230, 60), (255, 150, 0), (255, 40, 40), (150, 150, 150)
START_C, TARGET_C, PRED_C, TRACK_C = (80, 160, 255), (255, 255, 0), (255, 0, 255), (0, 200, 255)
LEGEND = [(START_C, 'START of the walk'), (TARGET_C, 'TARGET of the walk'),
          (RED, 'GAME STOP (player stood still here)'), (TRACK_C, 'game track (logged player positions)'),
          (GREEN, 'planned route: mesh on the floor (within 40)'),
          (ORANGE, 'planned route: mesh 40+ above the floor'), (RED, 'planned route: no floor'),
          (PRED_C, 'offline predictor stop'),
          ((200, 200, 200), 'tri numbers = mesh height minus floor; purple/grey = collision layers')]


def floats(text):
    """Comma-separated floats."""
    return tuple(float(v) for v in text.split(','))


def dense(points):
    """The route resampled every STEP units in plan."""
    out = []
    for a, b in zip(points, points[1:]):
        n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / STEP))
        out += [tuple(a[k] + (b[k] - a[k]) * i / n for k in range(3)) for i in range(n)]
    return out + [tuple(points[-1])]


def cell_at(args, gx, gy):
    """EditorID (else FormID) of the exterior cell at grid (gx, gy), or ''."""
    rec, _err = find_by_coords(args.plugin, args.world, gx, gy)
    return (rec or {}).get('EditorID') or (rec or {}).get('FormID') or ''


def cells_in(args, box):
    """{(gx, gy): EditorID} of every exterior cell the box overlaps; {None: name} for an interior."""
    if args.interior:
        return {None: args.interior}
    return {(gx, gy): cell_at(args, gx, gy)
            for gx in range(math.floor(box[0] / 4096), math.floor(box[2] / 4096) + 1)
            for gy in range(math.floor(box[1] / 4096), math.floor(box[3] / 4096) + 1)}


@functools.lru_cache(maxsize=None)
def cell_walk(plugin, name):
    """One cell's walkable collision, loaded once."""
    return walk_collision(plugin, name)


def walk_around(args, grid, name):
    """The cell's walkable collision plus its 8 neighbours' (objects overhang cell borders)."""
    if grid is None:
        return cell_walk(args.plugin, name)
    names = [cell_at(args, grid[0] + dx, grid[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    soups = [s for s in (cell_walk(args.plugin, n) for n in names if n) if len(s)]
    return np.concatenate(soups) if soups else cell_walk(args.plugin, name)


def colour(walk, p):
    """The route colour at p: by the nearest walk surface at or below p (+40)."""
    hits = [z for z in surfaces(walk, p[0], p[1]) if p[2] - 128 <= z <= p[2] + 40]
    if not hits:
        return RED
    return GREEN if p[2] - max(hits) <= 40 else ORANGE


def outline_model(args, cv, name):
    """Outline (white, with z) every collision triangle of models whose name contains --outline."""
    for model, w, b in NavIndex(export_dir(args.plugin)).cell(name).collision_sources():
        if args.outline.lower() not in model.lower():
            continue
        for t in list(w if w is not None else []) + list(b if b is not None else []):
            cv.draw.polygon([cv.px(p[0], p[1]) for p in t], outline=(255, 255, 255))
            cx, cy = cv.px(sum(p[0] for p in t) / 3, sum(p[1] for p in t) / 3)
            cv.draw.text((cx, cy), 'z%d' % (sum(p[2] for p in t) / 3), fill=(255, 255, 255))


def draw_cell(args, cv, box, grid, name, band, step):
    """Draw one cell's collision layers + shipped navmesh into its part of the box, labelled; its walk surfaces."""
    sub = box if grid is None else (max(box[0], grid[0] * 4096.0), max(box[1], grid[1] * 4096.0),
                                    min(box[2], grid[0] * 4096.0 + 4096), min(box[3], grid[1] * 4096.0 + 4096))
    walk = walk_around(args, grid, name)
    sub_cv = Canvas(sub, cv.scale)
    draw_layers(sub_cv, walk, band, step)
    _s, esm, rec = shipped_target(args.plugin, name, None)
    draw_mesh(sub_cv, walk, band, navm_arrays(esm, rec).values(), nearest=True)
    cv.img.paste(sub_cv.img, tuple(int(v) for v in cv.px(sub[0], sub[3])))
    sx, sy = cv.px(sub[0], sub[1])
    cv.draw.rectangle([sx, sy - 22, sx + 1, sy], outline=(120, 200, 255))
    cv.draw.rectangle([sx + 2, sy - 22, sx + 52 + 7 * len(name), sy - 2], fill=(0, 0, 0))
    cv.draw.text((sx + 6, sy - 18), 'CELL ' + name, fill=(120, 200, 255))
    if args.outline:
        outline_model(args, cv, name)
    print('drew', name, flush=True)
    return walk


def edge_mark(cv, box, p, col, name):
    """Mark p; a point outside the box is drawn on the box edge, labelled with its coordinates and distance."""
    q = (min(max(p[0], box[0] + 30), box[2] - 30), min(max(p[1], box[1] + 30), box[3] - 30))
    text = '%s (%d, %d, z%d)' % (name, p[0], p[1], p[2])
    if q != (p[0], p[1]):
        text += ' off-picture this way, %d further' % math.hypot(p[0] - q[0], p[1] - q[1])
    x, y = cv.px(q[0], q[1])
    cv.draw.ellipse([x - 9, y - 9, x + 9, y + 9], fill=col, outline=(255, 255, 255))
    width = 6 * len(text) + 8
    tx = x + 14 if x + 14 + width < cv.img.width else x - 14 - width
    ty = min(max(y - 8, 2), cv.img.height - 18)
    cv.draw.rectangle([tx - 3, ty - 2, tx + width, ty + 14], fill=(0, 0, 0))
    cv.draw.text((tx, ty), text, fill=col)


def with_key(img):
    """The map with the colour key in its own strip underneath, so the key never covers the map."""
    sheet = Image.new('RGB', (max(img.width, 400), img.height + 12 + 16 * len(LEGEND)), (0, 0, 0))
    sheet.paste(img, (0, 0))
    draw = ImageDraw.Draw(sheet)
    for i, (col, text) in enumerate(LEGEND):
        y = img.height + 6 + 16 * i
        draw.rectangle([6, y, 18, y + 12], fill=col)
        draw.text((24, y), text, fill=(255, 255, 255))
    return sheet


def frame(args, pts, track):
    """`(box, points inside it)`: --box, else the route plus MARGIN."""
    box = floats(args.box) if args.box else (min(p[0] for p in pts) - MARGIN, min(p[1] for p in pts) - MARGIN,
                                             max(p[0] for p in pts) + MARGIN, max(p[1] for p in pts) + MARGIN)
    extra = [args.start, args.target] + ([args.game_stop] if args.game_stop else [])
    inside = [p for p in pts + track + extra if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]]
    return box, inside


def draw_route(args, cv, pts, walks):
    """The planned route, each 32u piece coloured by how the mesh under it sits on the floor."""
    for a, b in zip(pts, pts[1:]):
        g = None if args.interior else (math.floor(a[0] / 4096), math.floor(a[1] / 4096))
        cv.draw.line([cv.px(a[0], a[1]), cv.px(b[0], b[1])], fill=colour(walks[g], a) if g in walks else GREY,
                     width=5)


def mark_points(args, cv, box, world):
    """Start, target, the predictor's stop and the game's stop."""
    got, at, why = predict(world, Collision(master_export_dir('export', args.plugin)),
                           {'place': args.interior or '', 'samples': [], 'start': list(args.start),
                            'target': list(args.target)}, RADIUS)
    print('predictor:', got, why, at, flush=True)
    edge_mark(cv, box, args.start, START_C, 'START')
    edge_mark(cv, box, args.target, TARGET_C, 'TARGET')
    if got != 'reached' and at is not None:
        edge_mark(cv, box, at, PRED_C, 'PREDICTOR STOP (%s)' % why)
    if args.game_stop:
        edge_mark(cv, box, args.game_stop, RED, 'GAME STOP')


def parse():
    """Command-line arguments; points parsed to float tuples."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plugin', required=True)
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument('--world', help='exterior worldspace EditorID')
    where.add_argument('--interior', help='interior cell EditorID')
    ap.add_argument('start', type=floats)
    ap.add_argument('target', type=floats)
    ap.add_argument('out')
    ap.add_argument('--scale', type=float, default=0.25, help='pixels per unit')
    ap.add_argument('--box', help='x0,y0,x1,y1 to draw instead of the route bounds')
    ap.add_argument('--game-stop', type=floats, help='where the game stopped the walker')
    ap.add_argument('--track', help="file of the game's logged positions, one x,y,z per line")
    ap.add_argument('--outline', default='', help='model-name part whose collision is outlined')
    return ap.parse_args()


def main():
    """Plan the walk on the shipped ESM, draw every cell it crosses, mark the stops, save the PNG."""
    args = parse()
    track = []
    if args.track:
        with open(args.track, encoding='utf-8') as fh:
            track = [floats(line) for line in fh if line.strip()]
    world = World(str(plugin_esm('output', args.plugin, export_root(master_export_dir('export', args.plugin)))))
    legs = plan(world, args.start, args.target, args.interior)[0]
    pts = dense([p for leg, _place in legs for p in leg] if legs else [args.start, args.target])
    box, inside = frame(args, pts, track)
    pts = [p for p in pts if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]]
    band = (min(p[2] for p in inside) - 300, max(p[2] for p in inside) + 300)
    cv = Canvas(box, args.scale)
    cells = cells_in(args, box)
    step = 10.0 if args.scale >= 1 else 20.0 if args.scale >= 0.4 else 64.0
    walks = {g: draw_cell(args, cv, box, g, name, band, step) for g, name in cells.items() if name}
    draw_route(args, cv, pts, walks)
    if len(track) > 1:
        cv.draw.line([cv.px(p[0], p[1]) for p in track], fill=TRACK_C, width=3)
    mark_points(args, cv, box, world)
    draw_axes(cv)
    with_key(cv.img).save(args.out)
    print('wrote', args.out, 'cells', sorted(v for v in cells.values() if v), flush=True)


if __name__ == '__main__':
    main()
