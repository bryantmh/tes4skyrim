"""Clean-mesh check of a SHIPPED navmesh in a box: overlaps, T-junctions, slivers and holes.

    python tools/navmesh/mesh_clean.py output/Tribunal.esm/Tribunal.esm C0C4C7 -50 -2950 600 -2400
    python tools/navmesh/mesh_clean.py output/Arktwend/Arktwend_English.esm - 138700 -142100 139100 -141800

The cell is the interior cell's FormID (low 24 bits, hex), or `-` for every NAVM (exteriors). Overlap: two
triangles whose plan interiors share more than 4 sq units within 60 z. T-junction: a vertex inside another
triangle's edge. Sliver: smallest corner under 5 deg. Hole: a ring of open edges the mesh does not cover.
"""

import argparse
import itertools
import math
import os
import sys

from shapely import make_valid
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.navmesh.authored import navm_arrays


def in_box(p, box):
    """Whether a point lies in the plan box."""
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def min_angle(a, b, c):
    """The triangle's smallest corner angle in plan, degrees."""
    out = 180.0
    for p, q, r in ((a, b, c), (b, c, a), (c, a, b)):
        u, v = (q[0] - p[0], q[1] - p[1]), (r[0] - p[0], r[1] - p[1])
        nu, nv = math.hypot(*u), math.hypot(*v)
        if nu and nv:
            out = min(out, math.degrees(math.acos(max(-1, min(1, (u[0] * v[0] + u[1] * v[1]) / (nu * nv))))))
    return out


def on_edge(p, a, b, tol=2.0):
    """Whether p lies strictly inside segment a-b in plan (within tol), away from its ends."""
    ab = (b[0] - a[0], b[1] - a[1])
    length = math.hypot(*ab)
    if length < 1e-6:
        return False
    t = ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / (length * length)
    if t <= 0.01 or t >= 0.99:
        return False
    d = abs((p[0] - a[0]) * ab[1] - (p[1] - a[1]) * ab[0]) / length
    return d < tol and math.dist(p[:2], a[:2]) > tol and math.dist(p[:2], b[:2]) > tol


def holes(verts, tris, local, polys):
    """`[(area, centroid)]` of closed rings of open edges whose inside the mesh does not cover."""
    edges = {}
    for i in local:
        t = tris[i]
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edges.setdefault(tuple(sorted((a, b))), []).append(i)
    rings = polygonize([LineString([verts[a][:2], verts[b][:2]]) for (a, b), own in edges.items() if len(own) == 1])
    covered = make_valid(unary_union(list(polys.values())))
    return [(round(r.area), [round(c) for c in r.centroid.coords[0]]) for r in rings
            if r.area > 4.0 and covered.intersection(make_valid(r)).area < 0.5 * r.area]


def defects(verts, tris, local):
    """`(overlaps, T-junctions, slivers, holes)` among the `local` triangles."""
    every = {i: Polygon([verts[k][:2] for k in tris[i][:3]]) for i in local}
    flat = {i for i in local if every[i].area < 0.01}
    solid = [i for i in local if i not in flat]
    polys = {i: every[i] for i in solid}
    zc = {i: sum(verts[k][2] for k in tris[i][:3]) / 3 for i in local}
    overlaps = [(i, j) for i, j in itertools.combinations(solid, 2)
                if abs(zc[i] - zc[j]) < 60 and polys[i].intersection(polys[j]).area > 4.0]
    used = {k for i in local for k in tris[i][:3]}
    tjunc = [(v, i) for i in local for v in used if v not in tris[i][:3]
             for a, b in ((0, 1), (1, 2), (2, 0))
             if on_edge(verts[v], verts[tris[i][a]], verts[tris[i][b]])
             and abs(verts[v][2] - (verts[tris[i][a]][2] + verts[tris[i][b]][2]) / 2) < 60]
    slivers = [i for i in local if i in flat or min_angle(*(verts[k] for k in tris[i][:3])) < 5.0]
    return overlaps, tjunc, slivers, holes(verts, tris, solid, polys)


def report(mesh, verts, tris, box):
    """Print one NAVM's defects in the box."""
    local = [i for i, t in enumerate(tris) if any(in_box(verts[k], box) for k in t[:3])]
    if not local:
        return
    overlaps, tjunc, slivers, gaps = defects(verts, tris, local)
    print('NAVM %08X: %d tris in box; overlaps %d, T-junctions %d, slivers %d, holes %d'
          % (mesh, len(local), len(overlaps), len(tjunc), len(slivers), len(gaps)))
    for area, at in gaps:
        print('  hole area', area, 'at', at)
    for i, j in overlaps:
        print('  overlap', i, j, [round(c) for c in Polygon([verts[k][:2] for k in tris[i][:3]]).intersection(
            Polygon([verts[k][:2] for k in tris[j][:3]])).centroid.coords[0]])
    for v, i in tjunc:
        print('  T-junction vertex', v, [round(c) for c in verts[v]], 'on tri', i)
    for i in slivers:
        print('  sliver', i, [[round(c) for c in verts[k]] for k in tris[i][:3]])


def main():
    """Print every defect found in the box."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('esm')
    ap.add_argument('cell', help='interior cell FormID (low 24 bits, hex), or - for all NAVMs')
    ap.add_argument('box', type=float, nargs=4, metavar=('X0', 'Y0', 'X1', 'Y1'))
    a = ap.parse_args()
    for mesh, (verts, tris) in navm_arrays(a.esm, None if a.cell == '-' else int(a.cell, 16)).items():
        report(mesh, verts, tris, a.box)


if __name__ == '__main__':
    main()
