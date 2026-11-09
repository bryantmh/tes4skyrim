"""Detect the SPATIAL defects width-grow can introduce, per cell.

Aggregate area/component counts cannot see the failures that matter, so this
checks every produced triangle against the cell's real collision:

  THROUGH-WALL   a triangle whose interior is separated from its own pathgrid
                 centerline by a blocking wall (a segment from the triangle
                 centroid back to the nearest pathgrid line crosses a wall slab)
  FLOATING       a triangle with no walkable collision under it within
                 MAX_CLIMB — it hangs in the air
  ON-FURNITURE   a triangle sitting a step ABOVE the walkable floor its own
                 pathgrid line sits on (climbed onto a bed / table / crate)
  STAIR GAP      a pathgrid edge with a steep slope that has NO triangle under
                 its centerline (the staircase lost its mesh)
  FLOOR MERGE    two triangles overlapping in XY whose Z differ by more than a
                 storey but which are EDGE-CONNECTED (the mesh joined storeys)

Usage:
    python tools/navmesh/grow_check.py --cell AnvilFightersGuild
    python tools/navmesh/grow_check.py --cell AnvilFightersGuild --fixed
    python tools/navmesh/grow_check.py --cells A,B --dump temp/defects.txt
"""

import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.navmesh import (build, corridor, clean_validate,
                                 corridor_grow, params, world)
from tools.navmesh.probe import load_cell


def _centroid(v, t):
    return ((v[t[0]][0] + v[t[1]][0] + v[t[2]][0]) / 3.0,
            (v[t[0]][1] + v[t[1]][1] + v[t[2]][1]) / 3.0,
            (v[t[0]][2] + v[t[1]][2] + v[t[2]][2]) / 3.0)


def _edge_points_sorted(nodes, edges, node_z, x, y, z):
    """Every pathgrid centerline's closest point to (x,y), nearest first."""
    out = []
    for (i, j) in edges:
        if i >= len(nodes) or j >= len(nodes):
            continue
        ax, ay, az = nodes[i][0], nodes[i][1], node_z[i]
        bx, by, bz = nodes[j][0], nodes[j][1], node_z[j]
        dx, dy = bx - ax, by - ay
        d2 = dx * dx + dy * dy
        t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((x - ax) * dx +
                                                     (y - ay) * dy) / d2))
        px, py = ax + dx * t, ay + dy * t
        pz = az + (bz - az) * t
        out.append((px, py, pz, math.hypot(x - px, y - py)))
    out.sort(key=lambda e: e[3])
    return out


def _wall_between(wall_hit, px, py, pz, cx, cy):
    """True if a wall slab stands between the centerline point and (cx, cy)."""
    dx, dy = cx - px, cy - py
    dist = math.hypot(dx, dy)
    if dist <= 1e-6:
        return False
    ux, uy = dx / dist, dy / dist
    tx, ty = -uy, ux
    zlo = pz + params.RIBBON_GROW_SLAB_Z_BOTTOM
    zhi = pz + params.AGENT_HEIGHT
    steps = int(dist // 8.0) + 1
    for k in range(1, steps + 1):
        q = min(dist, k * 8.0)
        if wall_hit(px + ux * q, py + uy * q, ux, uy, tx, ty, zlo, zhi, 5.0):
            return True
    return False


def _nearest_edge_point(nodes, edges, node_z, x, y, z):
    """Closest point on any pathgrid centerline, and its Z. (px,py,pz,dist)."""
    best = None
    for (i, j) in edges:
        if i >= len(nodes) or j >= len(nodes):
            continue
        ax, ay, az = nodes[i][0], nodes[i][1], node_z[i]
        bx, by, bz = nodes[j][0], nodes[j][1], node_z[j]
        dx, dy = bx - ax, by - ay
        d2 = dx * dx + dy * dy
        t = 0.0 if d2 < 1e-9 else max(0.0, min(1.0, ((x - ax) * dx +
                                                     (y - ay) * dy) / d2))
        px, py = ax + dx * t, ay + dy * t
        pz = az + (bz - az) * t
        d = math.hypot(x - px, y - py) + abs(z - pz) * 0.25
        if best is None or d < best[3]:
            best = (px, py, pz, d)
    return best


def _floor_merges(v, t):
    """(ti, nb, z0, z1) for edge-connected triangle pairs whose heights differ by over a storey."""
    merges = []
    for ti, nbrs in enumerate(clean_validate.edge_adjacency(t)):
        _, _, z0 = _centroid(v, t[ti])
        for nb in nbrs:
            if nb < 0 or nb <= ti:
                continue
            _, _, z1 = _centroid(v, t[nb])
            if abs(z1 - z0) > 120.0:
                merges.append((ti, nb, z0, z1))
    return merges


def check(export_dir, cell, use_grow=True, dump=None):
    ctx = load_cell(export_dir, cell)
    ox, oy = ctx['grid_x'] * 4096.0, ctx['grid_y'] * 4096.0
    from asset_convert.collision.collision_extract import get_collision

    walk, block, land = world.gather_cell_geometry(
        ctx['refrs'], ctx['base_model'], get_collision,
        land_rec=ctx['land'] if ctx['is_exterior'] else None,
        origin_x=ox, origin_y=oy, split_land=True)
    if land is not None and len(land):
        walk = np.concatenate([walk, land]) if len(walk) else land

    old = params.RIBBON_GROW
    params.RIBBON_GROW = use_grow
    try:
        verts, tris = build.build_navmesh(
            ctx['refrs'], ctx['base_model'], get_collision,
            ctx['nodes'], ctx['edges'],
            land_rec=ctx['land'] if ctx['is_exterior'] else None,
            origin_x=ox, origin_y=oy, doors=ctx['doors'])
    finally:
        params.RIBBON_GROW = old

    if not tris:
        print(f"{cell}: NO MESH")
        return

    v = [tuple(map(float, p)) for p in verts]
    t = [tuple(map(int, x)) for x in tris]

    sample = corridor_grow.walkable_sampler(walk)
    wall_hit = corridor_grow.wall_slab_sampler(block)
    snap = corridor._surface_sampler(walk)
    node_z = [corridor._snap_node_z(snap, ctx['nodes'][i][0], ctx['nodes'][i][1],
                                    ctx['nodes'][i][2])
              for i in range(len(ctx['nodes']))]

    floating = []
    furniture = []
    through = []
    for ti, tr in enumerate(t):
        cx, cy, cz = _centroid(v, tr)
        s = sample(cx, cy, cz)
        if s is None or abs(s - cz) > params.MAX_CLIMB:
            floating.append((ti, cx, cy, cz, s))
            continue
        # Attribute the triangle to the centerline that actually GENERATED it —
        # the nearest one whose own corridor could reach here.  Using the
        # merely-nearest centerline mis-blames a triangle whenever a different
        # edge lies closer in XY but across a wall (a corridor legitimately grown
        # from edge A, with edge B just the other side of the partition).  We
        # take the nearest centerline that has NO wall between it and the
        # triangle; only if EVERY candidate is walled off is it a real leak.
        cands = _edge_points_sorted(ctx['nodes'], ctx['edges'], node_z, cx, cy, cz)
        if not cands:
            continue
        clear = None
        for (px, py, pz, _d) in cands[:6]:
            if not _wall_between(wall_hit, px, py, pz, cx, cy):
                clear = (px, py, pz)
                break
        if clear is not None:
            px, py, pz = clear
        else:
            px, py, pz, _d = cands[0]
        # ON-FURNITURE: this triangle's own floor is a step above the floor at
        # its governing pathgrid line.
        ps = sample(px, py, pz)
        if ps is not None and s is not None and s - ps > params.MAX_CLIMB:
            furniture.append((ti, cx, cy, cz, s, ps))
        # THROUGH-WALL: no candidate centerline can reach this triangle without
        # crossing a wall (see the attribution above) -> it is on the far side of
        # a partition from every line that could have generated it.
        if clear is None:
            through.append((ti, cx, cy, cz))

    # STAIR GAP: a steep pathgrid edge with no triangle under its midpoint.
    stair_gaps = []
    for (i, j) in ctx['edges']:
        if i >= len(ctx['nodes']) or j >= len(ctx['nodes']):
            continue
        az, bz = node_z[i], node_z[j]
        ax, ay = ctx['nodes'][i][0], ctx['nodes'][i][1]
        bx, by = ctx['nodes'][j][0], ctx['nodes'][j][1]
        run = math.hypot(bx - ax, by - ay)
        if run < 1e-6 or abs(bz - az) / run < 0.30:
            continue                              # not a stair
        mx, my, mz = (ax + bx) / 2, (ay + by) / 2, (az + bz) / 2
        hit = False
        for tr in t:
            va, vb, vc = v[tr[0]], v[tr[1]], v[tr[2]]
            d = ((vb[1] - vc[1]) * (va[0] - vc[0]) +
                 (vc[0] - vb[0]) * (va[1] - vc[1]))
            if abs(d) < 1e-9:
                continue
            l0 = ((vb[1] - vc[1]) * (mx - vc[0]) +
                  (vc[0] - vb[0]) * (my - vc[1])) / d
            l1 = ((vc[1] - va[1]) * (mx - vc[0]) +
                  (va[0] - vc[0]) * (my - vc[1])) / d
            l2 = 1.0 - l0 - l1
            if l0 < -0.01 or l1 < -0.01 or l2 < -0.01:
                continue
            zz = l0 * va[2] + l1 * vb[2] + l2 * vc[2]
            if abs(zz - mz) <= params.MAX_CLIMB * 2:
                hit = True
                break
        if not hit:
            stair_gaps.append((i, j, mx, my, mz))

    merges = _floor_merges(v, t)
    comps = len(clean_validate.components(t))
    tag = 'GROW' if use_grow else 'FIXED'
    print(f"{cell} [{tag}] {len(v)}v {len(t)}t comps={comps}")
    print(f"   through-wall : {len(through)}")
    print(f"   floating     : {len(floating)}")
    print(f"   on-furniture : {len(furniture)}")
    print(f"   stair gaps   : {len(stair_gaps)}")
    print(f"   floor merges : {len(merges)}")

    if dump:
        with open(dump, 'a', encoding='utf-8') as fh:
            fh.write(f"# {cell} [{tag}]\n")
            for nm, rows in (('through', through), ('floating', floating),
                             ('furniture', furniture), ('stair', stair_gaps),
                             ('merge', merges)):
                for r in rows:
                    fh.write(f"{nm} {r}\n")
    return {'through': len(through), 'floating': len(floating),
            'furniture': len(furniture), 'stair': len(stair_gaps),
            'merge': len(merges), 'comps': comps, 'tris': len(t)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell')
    ap.add_argument('--cells')
    ap.add_argument('--fixed', action='store_true',
                    help='check the Phase-1 fixed-width mesh instead')
    ap.add_argument('--both', action='store_true', help='check fixed AND grown')
    ap.add_argument('--dump')
    args = ap.parse_args()

    cells = ([args.cell] if args.cell else []) + \
            ([c for c in (args.cells or '').split(',') if c])
    if not cells:
        ap.error('need --cell or --cells')
    for c in cells:
        if args.both:
            check(args.export, c, False, args.dump)
        check(args.export, c, not args.fixed, args.dump)


if __name__ == '__main__':
    main()
