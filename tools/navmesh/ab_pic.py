"""Pictures of exactly where two navmesh_ab builds differ: one PNG per changed spot, before beside after.

    python tools/navmesh/ab_pic.py --plugin Oblivion.esm before.pkl after.pkl out_dir [--cells A,B] [--scale 1.5]

For each cell whose geometry differs, the triangles only one side has are grouped into spots; each spot is drawn
twice in the same frame (left = before, right = after) over the cell's CURRENT walk collision layers (grey one
surface, purple two or more, black none). Triangles are outlined by their depth under the highest walk surface,
as in slab_map.py; the ones only that side has are filled red (before) or blue (after).
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.navmesh.floor_points import walk_collision
from tools.navmesh.navmesh_ab import changed, load_side
from tools.navmesh.slab_map import Canvas, draw_axes, draw_layers, draw_mesh

#: Changed triangles whose centres are closer than this (plan units) belong to one spot.
SPOT = 300.0
#: Margin drawn around a spot, and the height band above and below its triangles.
PAD, BAND = 160.0, 250.0
#: Fill of a triangle only the before / only the after side has.
ONLY_BEFORE, ONLY_AFTER = (150, 30, 30), (30, 60, 170)


def tri_keys(geo):
    """{rounded corner coordinates: corner array} of one side's triangles."""
    verts, tris = np.asarray(geo[0], float), geo[1]
    out = {}
    for t in tris:
        p = verts[list(t[:3]), :3]
        out[tuple(sorted(tuple(np.round(q, 1)) for q in p))] = p
    return out


def spots(only):
    """The triangles only one side has, grouped into lists of nearby ones."""
    groups = []
    for p in only:
        c = p.mean(0)
        near = [g for g in groups if any(np.hypot(*(q.mean(0) - c)[:2]) < SPOT for q in g)]
        merged = [p] + [q for g in near for q in g]
        groups = [g for g in groups if not any(g is n for n in near)] + [merged]
    return groups


def panel(box, band, scale, walk, geo, only, fill, title):
    """One side's picture of the box: layers, that side's mesh, its own triangles filled."""
    cv = Canvas(box, scale)
    draw_layers(cv, walk, band, 16.0)
    for p in only:
        cv.draw.polygon([cv.px(*q[:2]) for q in p], fill=fill)
    draw_mesh(cv, walk, band, [(geo[0], geo[1])])
    draw_axes(cv)
    cv.draw.rectangle([0, cv.img.height - 18, 8 + 7 * len(title), cv.img.height], fill=(0, 0, 0))
    cv.draw.text((4, cv.img.height - 15), title, fill=(255, 255, 255))
    return cv.img


def draw_spot(args, name, walk, sides, group, path):
    """Save one spot's before | after picture; returns its box."""
    pts = np.concatenate(group)
    box = (pts[:, 0].min() - PAD, pts[:, 1].min() - PAD, pts[:, 0].max() + PAD, pts[:, 1].max() + PAD)
    band = (pts[:, 2].min() - BAND, pts[:, 2].max() + BAND)
    inside = [[p for p in only if box[0] <= p.mean(0)[0] <= box[2] and box[1] <= p.mean(0)[1] <= box[3]]
              for _geo, only in sides]
    left = panel(box, band, args.scale, walk, sides[0][0], inside[0], ONLY_BEFORE, '%s BEFORE (red = removed)' % name)
    right = panel(box, band, args.scale, walk, sides[1][0], inside[1], ONLY_AFTER, '%s AFTER (blue = new)' % name)
    sheet = Image.new('RGB', (left.width + right.width + 6, left.height), (255, 255, 255))
    sheet.paste(left, (0, 0))
    sheet.paste(right, (left.width + 6, 0))
    sheet.save(path)
    return box


def cell_pictures(args, name, before, after):
    """Draw every changed spot of one cell; prints each picture's path, box and triangle counts."""
    a, b = tri_keys(before), tri_keys(after)
    gone, new = [a[k] for k in a if k not in b], [b[k] for k in b if k not in a]
    walk = walk_collision(args.plugin, name)
    for i, group in enumerate(spots(gone + new), 1):
        path = os.path.join(args.out, '%s_%02d.png' % (name, i))
        box = draw_spot(args, name, walk, ((before, gone), (after, new)), group, path)
        print('%s spot %d: x %d..%d y %d..%d, %d triangles differ -> %s' % (
            name, i, box[0], box[1 + 1], box[1], box[3], len(group), path), flush=True)


def main():
    """CLI: picture every changed spot of every changed (or named) cell."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('before')
    ap.add_argument('after')
    ap.add_argument('out')
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--cells', help='comma-separated EditorIDs (default: every changed cell)')
    ap.add_argument('--scale', type=float, default=1.5, help='pixels per unit')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    (before, _n), (after, names) = load_side(args.before), load_side(args.after)
    want = set(args.cells.split(',')) if args.cells else None
    for key, meta in sorted(names.items(), key=lambda kv: kv[1][0]):
        if (want is None or meta[0] in want) and changed(before.get(key), after.get(key)):
            cell_pictures(args, meta[0], before[key], after[key])


if __name__ == '__main__':
    main()
