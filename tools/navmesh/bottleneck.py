"""Find the narrow joints in a navmesh: where removing a few shared edges
disconnects the surface.

A cell can pass the "one component" invariant and still be unnavigable if the
two halves meet through a SLIVER -- one or two shared edges a few units wide.
The engine's pathfinder crosses triangles through shared edges, so a joint's
capacity is the total WIDTH of the shared edges spanning it, not the mere fact
that it exists.

Reported per articulation edge / bridge:

    bridge edges   a shared edge whose removal splits the mesh in two
    joint width    length of that edge (how wide the doorway actually is)
    split sizes    how many triangles end up on each side

    python tools/navmesh/bottleneck.py --cell AnvilPinarusInventiusHouse
    python tools/navmesh/bottleneck.py --cell X --min-side 5
"""

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.collision import collision_extract as ce
from tes5_import.navmesh import build, clean_validate
from tools.navmesh.probe import load_cell


def _components_of(tris, skip_edges=()):
    """Triangle components linked through shared EDGES, minus skip_edges."""
    skip = set(skip_edges)
    emap = defaultdict(list)
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            e = (a, b) if a < b else (b, a)
            if e in skip:
                continue
            emap[e].append(ti)
    parent = list(range(len(tris)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e, ts in emap.items():
        for i in range(1, len(ts)):
            a, b = find(ts[0]), find(ts[i])
            if a != b:
                parent[a] = b
    groups = defaultdict(list)
    for ti in range(len(tris)):
        groups[find(ti)].append(ti)
    return list(groups.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True)
    ap.add_argument('--min-side', type=int, default=3,
                    help='ignore bridges cutting off fewer tris than this')
    a = ap.parse_args()

    ctx = load_cell(a.export, a.cell)
    land = ctx['land'] if ctx['is_exterior'] else None
    ox, oy = ctx['grid_x'] * 4096.0, ctx['grid_y'] * 4096.0
    verts, tris = build.build_navmesh(
        ctx['refrs'], ctx['base_model'], ce.get_collision, ctx['nodes'],
        ctx['edges'], land_rec=land, origin_x=ox, origin_y=oy,
        doors=ctx.get('doors'))
    v = [tuple(map(float, p)) for p in verts]
    tris = [tuple(t) for t in tris]
    comps = clean_validate.components(tris)
    comps.sort(key=len, reverse=True)
    print('%s: %d tris, components %s'
          % (a.cell, len(tris), [len(c) for c in comps[:10]]))

    # interior (shared) edges only -- a border edge cannot be a bridge
    cnt = defaultdict(list)
    for ti, t in enumerate(tris):
        for k in range(3):
            x, y = t[k], t[(k + 1) % 3]
            e = (x, y) if x < y else (y, x)
            cnt[e].append(ti)
    shared = [e for e, ts in cnt.items() if len(ts) >= 2]
    print('%d shared edges, %d border edges'
          % (len(shared), len(cnt) - len(shared)))

    base = len(_components_of(tris))
    found = []
    for e in shared:
        parts = _components_of(tris, skip_edges=(e,))
        if len(parts) <= base:
            continue
        sizes = sorted(len(p) for p in parts)
        if sizes[0] < a.min_side:
            continue
        w = math.dist(v[e[0]], v[e[1]])
        found.append((w, e, sizes))

    if not found:
        print('\nno single-edge bridges (joints have redundant connections)')
    else:
        print('\n%d SINGLE-EDGE BRIDGES -- the mesh hangs together by these:'
              % len(found))
        for (w, e, sizes) in sorted(found):
            print('    width=%7.2f  edge(%d,%d)  splits %s'
                  % (w, e[0], e[1], sizes))
            print('              %s -> %s'
                  % (tuple(round(x, 1) for x in v[e[0]]),
                     tuple(round(x, 1) for x in v[e[1]])))

    # Total joint width across Z bands -- what a stair-to-landing link really is.
    print('\njoint capacity across Z (shared edges spanning each level):')
    zs = sorted(p[2] for p in v)
    if zs:
        lo, hi = zs[0], zs[-1]
        nb = 8
        step = max(1.0, (hi - lo) / nb)
        for bi in range(nb):
            zb = lo + step * (bi + 1)
            tot, num = 0.0, 0
            for e, ts in cnt.items():
                if len(ts) < 2:
                    continue
                za, zc = v[e[0]][2], v[e[1]][2]
                if (za - zb) * (zc - zb) < 0:
                    tot += math.dist(v[e[0]], v[e[1]])
                    num += 1
            if num:
                print('    z=%8.1f   %3d shared edges, total width %8.1fu'
                      % (zb, num, tot))


if __name__ == '__main__':
    main()
