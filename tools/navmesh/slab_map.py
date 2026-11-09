"""Picture of a cell's walk collision layers under its SHIPPED navmesh: where the mesh sits off the floor.

    python tools/navmesh/slab_map.py --plugin Tribunal.esm --cell "Old Mournhold: Temple Sewers East" \
        --box 4900 8100 5600 8800 --band 11880 12010 --out slab.png [--step 8 --scale 2]

Background: black = no walk surface in the band, grey = one, purple = two or more (a slab over a floor).
Shipped triangles centred in the box and band are outlined: green within 15 of the highest walk surface at
their centre, yellow within 40, orange deeper (under a slab); the number is that depth.
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.navmesh.authored import navm_arrays
from tools.navmesh.floor_points import surfaces, walk_collision
from tools.navmesh.pin_region import shipped_target


class Canvas:
    """The image and the world-to-pixel mapping of the box."""

    def __init__(self, box, scale):
        """A black image covering `box` at `scale` pixels per unit."""
        self.box, self.scale = box, scale
        self.img = Image.new('RGB', (int((box[2] - box[0]) * scale), int((box[3] - box[1]) * scale)))
        self.draw = ImageDraw.Draw(self.img)

    def px(self, x, y):
        """Pixel of world (x, y)."""
        return (x - self.box[0]) * self.scale, (self.box[3] - y) * self.scale


def draw_layers(cv, walk, band, step):
    """Shade each grid square by how many walk surfaces it has in the band."""
    b = cv.box
    for y in np.arange(b[1], b[3], step):
        for x in np.arange(b[0], b[2], step):
            n = sum(1 for z in surfaces(walk, x + step / 2, y + step / 2) if band[0] <= z <= band[1])
            if n:
                cv.draw.rectangle([cv.px(x, y + step), cv.px(x + step, y)],
                                  fill=(110, 60, 140) if n > 1 else (90, 90, 90))


def draw_mesh(cv, walk, band, meshes):
    """Outline each shipped triangle in the box and band by its depth under the highest walk surface."""
    b = cv.box
    for verts, tris in meshes:
        for t in tris:
            p = np.array([verts[k][:3] for k in t[:3]])
            c = p.mean(0)
            if not (band[0] <= c[2] <= band[1] and b[0] <= c[0] <= b[2] and b[1] <= c[1] <= b[3]):
                continue
            hits = [z for z in surfaces(walk, c[0], c[1]) if band[0] <= z <= band[1]]
            below = max(hits) - c[2] if hits else 0
            colour = (60, 220, 60) if below < 15 else (250, 220, 0) if below < 40 else (255, 140, 0)
            cv.draw.polygon([cv.px(*q[:2]) for q in p], outline=colour)
            cv.draw.text(cv.px(c[0], c[1]), '%d' % below, fill=(255, 255, 255))


def draw_axes(cv):
    """Label the 100-unit grid lines along the top and left edges."""
    b = cv.box
    for gx in np.arange(np.ceil(b[0] / 100) * 100, b[2], 100):
        cv.draw.text(cv.px(gx, b[3] - 4), '%d' % gx, fill=(120, 200, 255))
    for gy in np.arange(np.ceil(b[1] / 100) * 100, b[3], 100):
        cv.draw.text(cv.px(b[0] + 2, gy), '%d' % gy, fill=(120, 200, 255))


def main():
    """Draw the map."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--cell', required=True, help='name, FormID or "<worldspace> X Y"')
    ap.add_argument('--box', type=float, nargs=4, required=True, metavar=('X0', 'Y0', 'X1', 'Y1'))
    ap.add_argument('--band', type=float, nargs=2, required=True, metavar=('ZMIN', 'ZMAX'))
    ap.add_argument('--esm', help='shipped ESM (default: the plugin\'s output ESM)')
    ap.add_argument('--step', type=float, default=8.0, help='collision sample spacing')
    ap.add_argument('--scale', type=float, default=2.0, help='pixels per world unit')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    _src, esm, cell = shipped_target(a.plugin, a.cell, a.esm)
    walk = walk_collision(a.plugin, a.cell)
    cv = Canvas(a.box, a.scale)
    draw_layers(cv, walk, a.band, a.step)
    draw_mesh(cv, walk, a.band, navm_arrays(esm, cell).values())
    draw_axes(cv)
    cv.img.save(a.out)
    print('wrote', a.out)


if __name__ == '__main__':
    main()
