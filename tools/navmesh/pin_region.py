"""Rebuild one region of a cell's SHIPPED navmesh on measured floor points, as clean pinned triangles.

    python tools/navmesh/pin_region.py --plugin Arktwend_English.esm --cell "Subterranean Canyon of Grul" \
        --points pts.txt --box 5180 7480 5430 7800 --band 12200 12500 \
        [--route route.txt --extend "x,y;x,y;..."] [--notch "x,y;..."] [--margin 20] [--pin]

Removes the shipped triangles centred in --box/--band (and, with --route, every one overlapping --extend near
the route's height), refills the hole plus --extend minus --notch by a constrained Delaunay over its outline and
the floor points, and with --pin saves the frozen patch into navmesh_pins/; ship it with --import-only.
Floor points come from floor_points.py or in-game drops. Check after with mesh_clean.py.
"""

import argparse
import math
import os
import sys

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from output_layout import plugin_esm
from tools.cellview.bake import cell_corrections, cell_grid, frozen_patch, merge_patches, resolve_cell, save_pins
from tools.navmesh.authored import navm_arrays
from tools.navmesh.floor_points import read_points, route_height

#: Outline densifying step for --extend / --notch.
STEP = 60.0
#: A ring vertex farther than this from the measured floor is set onto it.
OFF_FLOOR = 10.0
#: ...only when a floor point lies this close in plan: farther out the blended floor is a guess.
FLOOR_REACH = 48.0
#: A hole in the union smaller than this (a seam sliver between old triangles and --extend) is filled.
MIN_HOLE = 200.0
#: An old vertex farther than this from the new floor's height belongs to another layer and is never stitched in.
LAYER_GAP = 30.0


class Region:
    """What the rebuild touches: the box with its z band, and the optional route corridor with its band
    (`below`, `above` the route's height)."""

    def __init__(self, box, band, extend=None, height=None, route_band=(30.0, 80.0)):
        """The region; `extend` and `height` only with a route."""
        self.box, self.band, self.extend, self.height = box, band, extend, height
        self.reach = extend.buffer(24) if extend is not None else None
        self.below, self.above = route_band

    def on_route(self, c):
        """Whether height c[2] lies in the route's band at (c[0], c[1])."""
        dz = c[2] - self.height(c[0], c[1])
        return -self.below <= dz <= self.above

    def picks(self, c, poly):
        """Whether the triangle with centre `c` and plan `poly` is removed."""
        b = self.box
        if b[0] <= c[0] <= b[2] and b[1] <= c[1] <= b[3] and self.band[0] <= c[2] <= self.band[1]:
            return True
        return self.height is not None and self.reach.intersects(poly) and self.on_route(c)


def centre(verts, t):
    """The triangle's centroid (x, y, z)."""
    return [sum(verts[k][a] for k in t[:3]) / 3 for a in range(3)]


def on_segment(p, a, b, tol=0.5):
    """Whether plan point p lies on segment a-b, away from its ends."""
    seg = LineString([a[:2], b[:2]])
    return (seg.distance(Point(p[:2])) < tol and Point(a[:2]).distance(Point(p[:2])) > tol
            and Point(b[:2]).distance(Point(p[:2])) > tol)


def thin(poly, keep, gap):
    """`poly` without outline points (other than old vertices in `keep`) closer than `gap` to a neighbour, and
    without spikes."""
    pts = list(poly.exterior.coords)[:-1]
    changed = True
    while changed:
        changed = False
        for i, p in enumerate(pts):
            a, b = pts[i - 1], pts[(i + 1) % len(pts)]
            u, v = (a[0] - p[0], a[1] - p[1]), (b[0] - p[0], b[1] - p[1])
            spike = (abs(u[0] * v[1] - u[1] * v[0]) < 0.02 * math.hypot(*u) * math.hypot(*v)
                     and u[0] * v[0] + u[1] * v[1] > 0)
            near = min(math.dist(p, a), math.dist(p, b)) < gap
            if spike or (near and (round(p[0], 2), round(p[1], 2)) not in keep):
                del pts[i]
                changed = True
                break
    return Polygon(pts)


def densify(poly, keep):
    """`poly` with a point every STEP along each outline edge that is not an old edge (both ends in `keep`)."""
    def loop(coords):
        """One closed loop, densified."""
        pts = list(coords)[:-1]
        out = []
        for a, b in zip(pts, pts[1:] + pts[:1]):
            out.append(a)
            if {(round(a[0], 2), round(a[1], 2)), (round(b[0], 2), round(b[1], 2))} <= keep:
                continue
            n = int(math.dist(a, b) // STEP)
            out += [(a[0] + (b[0] - a[0]) * i / (n + 1), a[1] + (b[1] - a[1]) * i / (n + 1)) for i in range(1, n + 1)]
        return out
    return Polygon(loop(poly.exterior.coords), [loop(h.coords) for h in poly.interiors])


def outline(verts, tris, removed, region, notch, gap):
    """`(removed, [hole part polygons])`: the removed triangles' union plus --extend minus --notch."""
    poly = unary_union([Polygon([verts[k][:2] for k in tris[i][:3]]) for i in removed])
    keep = {(round(verts[k][0], 2), round(verts[k][1], 2)) for i in removed for k in tris[i][:3]}
    if region.extend is not None:
        poly = unary_union([poly, region.extend])
        poly = poly.buffer(1.0, join_style=2, mitre_limit=100).buffer(-1.0, join_style=2, mitre_limit=100)
        poly = unary_union([densify(p, keep) for p in getattr(poly, 'geoms', [poly])])
    elif poly.geom_type == 'MultiPolygon':
        poly = max(poly.geoms, key=lambda p: p.area)
        removed = [i for i in removed if poly.contains(Point(centre(verts, tris[i])[:2]))]
    if notch is not None:
        poly = poly.difference(notch.segmentize(STEP / 2))
    return removed, [Polygon(thin(Polygon(p.exterior), keep, gap).exterior,
                             [h.coords for h in p.interiors if Polygon(h).area > MIN_HOLE])
                     for p in getattr(poly, 'geoms', [poly]) if p.area > 1.0]


def with_on_edges(verts, ring, cands):
    """`ring` with every vertex of `cands` lying on one of its edges inserted there (no T-junction copied)."""
    out = []
    for n, (k, x, y) in enumerate(ring):
        out.append((k, x, y))
        _k2, x2, y2 = ring[(n + 1) % len(ring)]
        on = [v for v in cands if v != k and on_segment(verts[v], (x, y), (x2, y2), tol=2.0)
              and min(math.dist(verts[v][:2], (x, y)), math.dist(verts[v][:2], (x2, y2))) > 1.0]
        out += [(v, verts[v][0], verts[v][1]) for v in sorted(on, key=lambda v: math.dist(verts[v][:2], (x, y)))]
    return without_near_new(out)


def without_near_new(ring, tol=6.0):
    """`ring` without each new point lying within `tol` of a neighbouring old vertex (the old one stands
    in for it), and without a point on top of the one before it (two old vertices sharing a spot)."""
    out = []
    for n, (k, x, y) in enumerate(ring):
        nbrs = (ring[n - 1], ring[(n + 1) % len(ring)])
        if k is None and any(o[0] is not None and math.dist((x, y), o[1:]) < tol for o in nbrs):
            continue
        if out and math.dist((x, y), out[-1][1:]) < 0.5:
            continue
        out.append((k, x, y))
    return out


def ring_of(verts, removed, poly, kept_edges):
    """One part's ring `[(vertex index or None, x, y)]`; refuses one that would split a kept edge."""
    lookup = {(round(verts[k][0], 2), round(verts[k][1], 2)): k for t in removed for k in t[:3]}
    ring = [(lookup.get((round(x, 2), round(y, 2))), x, y) for x, y in list(poly.exterior.coords)[:-1]]
    split = [((round(x), round(y)), [round(c) for c in verts[a][:3]], [round(c) for c in verts[b][:3]])
             for k, x, y in ring if k is None for a, b in kept_edges if on_segment((x, y), verts[a], verts[b])]
    if split:
        raise SystemExit('new ring points split kept edges: %s' % split)
    return with_on_edges(verts, ring, {k for e in kept_edges for k in e})


def on_floor(p, floor):
    """Whether point p lies within LAYER_GAP of the new floor (same layer)."""
    return abs(p[2] - floor(p[0], p[1])) <= LAYER_GAP


def hole(verts, tris, region, notch, gap, floor):
    """`(removed indices, [(part polygon, ring)])`; refuses when a kept triangle of the floor's own layer lies
    inside the hole. Only same-layer old vertices are stitched in, a part's old corner on another part's edge
    included."""
    removed = [i for i, t in enumerate(tris)
               if region.picks(centre(verts, t), Polygon([verts[k][:2] for k in t[:3]]))]
    removed, parts = outline(verts, tris, removed, region, notch, gap)
    gone = set(removed)
    covered = [i for i, t in enumerate(tris) if i not in gone and on_floor(centre(verts, t), floor)
               and any(p.contains(Point(centre(verts, t)[:2])) for p in parts)]
    if covered:
        raise SystemExit('kept triangles lie inside the hole: %s' % covered)
    kept_edges = {(t[a], t[b]) for i, t in enumerate(tris) if i not in gone
                  for a, b in ((0, 1), (1, 2), (2, 0))
                  if on_floor(verts[t[a]], floor) and on_floor(verts[t[b]], floor)}
    rings = [ring_of(verts, [tris[i] for i in removed], p, kept_edges) for p in parts]
    rings = [with_on_edges(verts, r, {k for o in rings if o is not r for k, _x, _y in o if k is not None})
             for r in rings]
    return removed, list(zip(parts, rings))


def check_lost(verts, tris, removed, parts):
    """Refuse when a vertex a kept triangle still uses ends up inside a part, off every ring."""
    gone = set(removed)
    shared = ({k for i, t in enumerate(tris) if i not in gone for k in t[:3]}
              & {k for i in removed for k in tris[i][:3]})
    on_ring = {k for _p, ring in parts for k, _x, _y in ring if k is not None}
    lost = [k for k in shared - on_ring if any(p.buffer(-0.5).contains(Point(verts[k][:2])) for p, _r in parts)]
    if lost:
        raise SystemExit('kept triangles use vertices the hole swallows: %s'
                         % [(k, [round(c) for c in verts[k][:3]]) for k in sorted(lost)])


def ccw(pts, t):
    """`t` ordered counter-clockwise in plan."""
    a, b, c = t
    cross = (pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1]) - (pts[b][1] - pts[a][1]) * (pts[c][0] - pts[a][0])
    return (a, b, c) if cross > 0 else (a, c, b)


def inside(pts, t, p):
    """Whether plan point p lies strictly inside CCW triangle t."""
    for i in range(3):
        a, b = pts[t[i]], pts[t[(i + 1) % 3]]
        if (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) <= 1e-6:
            return False
    return True


def angle_min(pts, t):
    """Smallest plan corner angle of triangle t, degrees (0 for a degenerate one)."""
    out = 180.0
    for i in range(3):
        p, q, r = pts[t[i]], pts[t[(i + 1) % 3]], pts[t[(i + 2) % 3]]
        u, v = (q[0] - p[0], q[1] - p[1]), (r[0] - p[0], r[1] - p[1])
        if not math.hypot(*u) or not math.hypot(*v):
            return 0.0
        cos = (u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v))
        out = min(out, math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
    return out


def flip_once(pts, tris, fixed):
    """Flip the first interior edge whose flip raises the pair's smallest angle; whether one flipped."""
    owner = {}
    for n, t in enumerate(tris):
        for i in range(3):
            owner.setdefault(frozenset((t[i], t[(i + 1) % 3])), []).append(n)
    for edge, pair in owner.items():
        if len(pair) != 2 or len(edge) != 2 or edge in fixed:
            continue
        a, b = tuple(edge)
        (c,), (d,) = (set(tris[pair[0]]) - edge), (set(tris[pair[1]]) - edge)
        t1, t2 = ccw(pts, (c, d, a)), ccw(pts, (c, d, b))
        quad = Polygon([pts[a], pts[c], pts[b], pts[d]])
        areas = Polygon([pts[k] for k in t1]).area, Polygon([pts[k] for k in t2]).area
        if min(areas) < 1e-3 or not quad.is_valid or abs(quad.area - sum(areas)) > 1e-3:
            continue
        if min(angle_min(pts, t1), angle_min(pts, t2)) > min(angle_min(pts, tris[n]) for n in pair) + 1e-6:
            tris[pair[0]], tris[pair[1]] = t1, t2
            return True
    return False


def fill(poly, ring, points, margin):
    """`(new interior points xyz, triangles over [ring..., new...] indices)`, CCW, ring edges kept. A point
    lying on an edge is dropped."""
    new = [p for p in points if poly.buffer(-margin).contains(Point(p[:2]))]
    pts = [(x, y) for _k, x, y in ring] + [p[:2] for p in new]
    at = {(round(p[0], 3), round(p[1], 3)): i for i, p in enumerate(pts[:len(ring)])}
    tris = [ccw(pts, tuple(at[(round(x, 3), round(y, 3))] for x, y in list(t.exterior.coords)[:3]))
            for t in shapely.constrained_delaunay_triangles(poly).geoms]
    for j in range(len(ring), len(pts)):
        host = next((n for n, t in enumerate(tris) if inside(pts, t, pts[j])), None)
        if host is None:
            return fill(poly, ring, [p for p in points if tuple(p[:2]) != tuple(pts[j])], margin)
        a, b, c = tris.pop(host)
        tris += [(a, b, j), (b, c, j), (c, a, j)]
    loops = [list(poly.exterior.coords)[:-1]] + [list(h.coords)[:-1] for h in poly.interiors]
    keyed = [[at[(round(x, 3), round(y, 3))] for x, y in loop] for loop in loops]
    fixed = {frozenset((loop[i], loop[(i + 1) % len(loop)])) for loop in keyed for i in range(len(loop))}
    while flip_once(pts, tris, fixed):
        pass
    return new, tris


def floor_of(points, k=6):
    """f(x, y) -> the floor height blended from the `k` nearest points by inverse distance (local, so a
    winding corridor never borrows heights across a gap)."""
    arr = np.array(points)
    tree = cKDTree(arr[:, :2])

    def floor(x, y):
        """The blended height at (x, y)."""
        dist, idx = tree.query((x, y), k=min(k, len(arr)))
        dist, idx = np.atleast_1d(dist), np.atleast_1d(idx)
        if dist[0] < 1e-6:
            return float(arr[idx[0], 2])
        w = 1.0 / dist ** 2
        return float((w * arr[idx, 2]).sum() / w.sum())
    floor.measured = lambda x, y: tree.query((x, y))[0] <= FLOOR_REACH
    return floor


def plan_part(verts, poly, ring, floor, points, margin):
    """Print one part's plan; `(ring, ring z, new points, triangles)`. Notch holes join the ring as new points;
    an old ring vertex keeps its z unless it is more than OFF_FLOOR off a measured floor."""
    ring = ring + [(None, x, y) for h in poly.interiors for x, y in list(h.coords)[:-1]]
    zs = [floor(x, y) if k is None or (abs(verts[k][2] - floor(x, y)) > OFF_FLOOR and floor.measured(x, y))
          else verts[k][2] for k, x, y in ring]
    moved = ['v%d %.0f->%.0f' % (k, verts[k][2], z) for (k, _x, _y), z in zip(ring, zs)
             if k is not None and z != verts[k][2]]
    new, tris = fill(poly, ring, points, margin)
    pts = [(x, y) for _k, x, y in ring] + [p[:2] for p in new]
    worst = min(tris, key=lambda t: angle_min(pts, t))
    print('part area %.0f: ring %d points (%d new), %d floor points, %d triangles; smallest angle %.1f deg at %s'
          % (poly.area, len(ring), sum(k is None for k, _x, _y in ring), len(new), len(tris),
             angle_min(pts, worst), [tuple(round(c) for c in pts[k]) for k in worst]))
    if moved:
        print('  ring vertices set onto the floor:', ', '.join(moved))
    return ring, zs, new, tris


def ops_for(verts, removed, fills):
    """cellview ops: delete the old triangles, then per part set the ring on the floor and add the fill."""
    nxt = len(verts)
    ops = [{'op': 'del_tri', 'tri': i} for i in removed]
    for ring, zs, new, tris in fills:
        index = {}
        for i, ((k, x, y), z) in enumerate(zip(ring, zs)):
            if k is None:
                ops.append({'op': 'add_vert', 'to': [x, y, z]})
                index[i], nxt = nxt, nxt + 1
            else:
                ops.append({'op': 'move_vert', 'v': k, 'to': [x, y, z]})
                index[i] = k
        for j, p in enumerate(new):
            ops.append({'op': 'add_vert', 'to': list(p)})
            index[len(ring) + j], nxt = nxt, nxt + 1
        ops += [{'op': 'add_tri', 'verts': [index[a], index[b], index[c]]} for a, b, c in tris]
    return ops


def parse_polygon(text, verts, snap=8.0):
    """The polygon `x,y;x,y;...` (several joined by `|`), each corner snapped onto an old vertex within
    `snap`, or None."""
    if not text:
        return None
    polys = []
    for ring in text.split('|'):
        out = []
        for p in ring.split(';'):
            x, y = map(float, p.split(','))
            best = min(verts, key=lambda v: math.dist(v[:2], (x, y)))
            out.append(tuple(best[:2]) if math.dist(best[:2], (x, y)) < snap else (x, y))
        polys.append(Polygon(out))
    return unary_union(polys)


def shipped_mesh(esm, box, cell, square=None):
    """`(verts, tris)` of the shipped NAVM (of interior `cell`, low 24 bits, when given) with the most
    triangles centred in the box; else, for an exterior `square` (gx, gy), the most in that cell."""
    meshes = list(navm_arrays(esm, cell).values())
    best = _most_inside(meshes, box)
    if not best[1] and square is not None:
        gx, gy = square
        best = _most_inside(meshes, (gx * 4096.0, gy * 4096.0, (gx + 1) * 4096.0, (gy + 1) * 4096.0))
    if not best[1]:
        raise SystemExit('no shipped navmesh in the box')
    return [tuple(float(c) for c in v[:3]) for v in best[1][0]], [tuple(int(k) for k in t[:3]) for t in best[1][1]]


def _most_inside(meshes, box):
    """`(count, (verts, tris))` of the mesh with the most triangles centred in `box`; `(0, None)` if none."""
    best = (0, None)
    for verts, tris in meshes:
        n = sum(1 for t in tris if box[0] <= centre(verts, t)[0] <= box[2] and box[1] <= centre(verts, t)[1] <= box[3])
        if n > best[0]:
            best = (n, (verts, tris))
    return best


def pin(plugin, src, verts, tris, ops):
    """Merge the ops' frozen patch over the shipped mesh into the cell's pins."""
    edits, key = cell_corrections(plugin, src)
    frozen, voids = merge_patches(edits.get('frozen', ()), edits.get('voids', ()), *frozen_patch(verts, tris, ops))
    path, n = save_pins(plugin, key, frozen=frozen, voids=voids)
    return {'pinned': path, 'key': key, 'tris': n['frozen'], 'voids': n['voids']}


def shipped_target(plugin, cell, esm=None):
    """`(cell ctx, ESM path, interior cell low 24 bits or None)`: what the shipped-mesh tools read."""
    src, why = resolve_cell(plugin, cell)
    if src is None:
        raise SystemExit(why)
    low = None if cell_grid(src) is not None else int(src.rec['FormID'], 16) & 0xFFFFFF
    return src, esm or str(plugin_esm('output', plugin, 'export')), low


def args():
    """The command line."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--cell', required=True, help='name, FormID or "<worldspace> X Y"')
    ap.add_argument('--points', required=True, help='floor points, `x y z` lines')
    ap.add_argument('--box', type=float, nargs=4, required=True, metavar=('X0', 'Y0', 'X1', 'Y1'))
    ap.add_argument('--band', type=float, nargs=2, required=True, metavar=('ZMIN', 'ZMAX'))
    ap.add_argument('--extend', help='unmeshed floor to add: "x,y;x,y;..." (several joined by |)')
    ap.add_argument('--notch', help='floor to leave out (a ledge, a rock): same form')
    ap.add_argument('--route', help='`x y z` lines; also removes triangles overlapping --extend near its height')
    ap.add_argument('--route-band', type=float, nargs=2, default=(30.0, 80.0), metavar=('BELOW', 'ABOVE'),
                    help='with --route: remove triangles centred this far below / above the route height')
    ap.add_argument('--margin', type=float, default=15.0, help='floor points closer to the outline are skipped')
    ap.add_argument('--thin', type=float, default=12.0, help='outline points closer than this are merged')
    ap.add_argument('--esm', help='shipped ESM to plan on (default: the plugin\'s output ESM)')
    ap.add_argument('--pin', action='store_true', help='save the patch into navmesh_pins/')
    return ap.parse_args()


def main():
    """Plan (and with --pin, save) the rebuild."""
    a = args()
    src, esm, cell = shipped_target(a.plugin, a.cell, a.esm)
    verts, tris = shipped_mesh(esm, a.box, cell, cell_grid(src))
    region = Region(a.box, a.band, parse_polygon(a.extend, verts),
                    route_height(read_points(a.route)) if a.route else None, a.route_band)
    if a.route and region.extend is None:
        raise SystemExit('--route needs --extend (floor_points.py --route prints it)')
    points = read_points(a.points)
    floor = floor_of(points)
    removed, parts = hole(verts, tris, region, parse_polygon(a.notch, verts), a.thin, floor)
    check_lost(verts, tris, removed, parts)
    print('%s: remove %d triangles; %d parts' % (esm, len(removed), len(parts)))
    fills = [plan_part(verts, poly, ring, floor, points, a.margin) for poly, ring in parts]
    if a.pin:
        print(pin(a.plugin, src, verts, tris, ops_for(verts, removed, fills)))


if __name__ == '__main__':
    main()
