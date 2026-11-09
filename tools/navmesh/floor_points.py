"""Sample a cell's walk collision into floor points: `x y z` lines that pin_region.py builds navmesh on.

    python tools/navmesh/floor_points.py --plugin Tribunal.esm --cell "Old Mournhold: Temple Shrine" \
        --box 1020 -2540 1300 -2300 --band -345 -190 --step 25 --out pts.txt
    python tools/navmesh/floor_points.py --plugin Arktwend_English.esm --cell "Subterranean Canyon of Grul" \
        --route route.txt --width 48 --step 24 --out pts.txt

--box takes the highest walk surface inside --band at each grid point. --route (`x y z` per line) takes,
within --width of the route line, the surface nearest the route's own height there (within --tol), and
prints the corridor outline as a pin_region.py --extend argument.
"""

import argparse
import os
import sys

import numpy as np
from shapely.geometry import LineString, Point

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.cellview.plugins import export_dir
from tools.navmesh.index import NavIndex


def surfaces(tris, x, y):
    """Sorted z of every collision triangle (n x 3 x 3 array) whose plan projection holds (x, y)."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    d = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    ok = np.abs(d) > 1e-9
    d = np.where(ok, d, 1.0)
    l1 = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / d
    l2 = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / d
    l3 = 1 - l1 - l2
    hit = ok & (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
    z = l1 * a[:, 2] + l2 * b[:, 2] + l3 * c[:, 2]
    return sorted(round(float(v)) for v in z[hit])


def walk_collision(plugin, cell):
    """The cell's walkable collision triangles."""
    return NavIndex(export_dir(plugin)).cell(cell).collision()[0]


def route_height(route):
    """f(x, y) -> the route's height at the point of its line nearest (x, y), linear between route points."""
    line = LineString([p[:2] for p in route])
    marks = [line.project(Point(p[:2])) for p in route]
    heights = [p[2] for p in route]
    return lambda x, y: float(np.interp(line.project(Point(x, y)), marks, heights))


def box_points(walk, box, band, step):
    """`[(x, y, z)]`: the highest walk surface inside `band` at each grid point of `box`."""
    out = []
    for x in np.arange(box[0], box[2] + 1, step):
        for y in np.arange(box[1], box[3] + 1, step):
            hits = [z for z in surfaces(walk, x, y) if band[0] <= z <= band[1]]
            if hits:
                out.append((float(x), float(y), float(max(hits))))
    return out


def corridor_points(walk, route, width, step, tol):
    """`(points, misses, outline)` along the buffered route; a miss is a grid point with no surface near the
    route height."""
    area = LineString([p[:2] for p in route]).buffer(width, join_style=2)
    height = route_height(route)
    x0, y0, x1, y1 = area.bounds
    points, misses = [], []
    for x in np.arange(x0 + 0.3, x1, step):
        for y in np.arange(y0 + 0.7, y1, step):
            if not area.contains(Point(x, y)):
                continue
            want = height(x, y)
            hits = [z for z in surfaces(walk, x, y) if abs(z - want) <= tol]
            if hits:
                points.append((float(x), float(y), float(min(hits, key=lambda z: abs(z - want)))))
            else:
                misses.append((round(x), round(y), round(want)))
    return points, misses, list(area.simplify(4).exterior.coords)[:-1]


def read_points(path):
    """`[(x, y, z)]` from a file of `x y z` lines."""
    with open(path, encoding='utf-8') as f:
        return [tuple(float(v) for v in line.split()[:3]) for line in f if line.strip()]


def main():
    """Write the floor points (and print the corridor outline for --route)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--cell', required=True, help='name, FormID or "<worldspace> X Y"')
    ap.add_argument('--box', type=float, nargs=4, metavar=('X0', 'Y0', 'X1', 'Y1'))
    ap.add_argument('--band', type=float, nargs=2, metavar=('ZMIN', 'ZMAX'))
    ap.add_argument('--route', help='`x y z` lines')
    ap.add_argument('--width', type=float, default=48.0)
    ap.add_argument('--tol', type=float, default=60.0)
    ap.add_argument('--step', type=float, default=20.0)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    walk = walk_collision(a.plugin, a.cell)
    if a.route:
        points, misses, outline = corridor_points(walk, read_points(a.route), a.width, a.step, a.tol)
        print('%d grid points with no surface near the route: %s' % (len(misses), misses[:40]))
        print('--extend=' + ';'.join('%.0f,%.0f' % p for p in outline))
    elif a.box and a.band:
        points = box_points(walk, a.box, a.band, a.step)
    else:
        ap.error('give --route, or --box with --band')
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(''.join('%.1f %.1f %.1f\n' % p for p in points))
    print(len(points), 'points ->', a.out)


if __name__ == '__main__':
    main()
