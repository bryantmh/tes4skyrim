"""Offline Skyrim pathfinder over a built ESM: the route an actor walking from A to B takes.

    python tools/navmesh/pathfind.py --plugin Arktwend_English.esm --from x,y,z --to x,y,z
    python tools/navmesh/pathfind.py --plugin Arktwend_English.esm --interior "Gor, Old House" --from .. --to ..

Start and goal triangles come from the engine's lookup (engine_check.locate); the route is a triangle A*
over neighbour slots and edge links; the path is pulled taut through the crossed edges.
See: docs/reference/navmesh_engine_contracts.md#the-path-build
"""

import argparse
import heapq
import math
import os
import struct
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.plugin_masters import export_root, master_export_dir
from output_layout import plugin_esm
from tes5_import.base.tes5_reader import walk
from tools.navmesh.engine_check import EDGE_LINK_BITS, Nav, barycentric, locate

#: Skyrim's exterior cell width.
CELL = 4096.0
#: Edge-link types the A* crosses: 0 portal, 2 ledge down; 1 ledge up and 3 enable portal are refused (CK 0x28a2250).
LINK_PASSABLE = 2
#: (flag, x when the destination has it, x when it lacks it, x when it changes): Preferred, Water (CK 0x28a1f80).
COST_FLAGS = ((0x0040, 0.01, 1.0, 1.0), (0x0200, 4.0, 1.0, 5.0))
#: Edges this wide or wider halve a crossing's cost (CK const 0x393e898).
WIDE_EDGE = 512.0
#: Weight of the 3D distance-to-goal heuristic (CK 0x28a2c16).
HEURISTIC_WEIGHT = 0.01
#: Actor radius of the CK's default path request (CK 0x286e450); the game's per-actor value is not decoded.
DEFAULT_RADIUS = 35.0
#: The pseudo edge slot of a load door's hop to its partner door's triangle.
DOOR = 3
#: A* cost of a door hop; the engine weighs doors at navmesh-info level (CK 0x2886630), not modelled yet.
DOOR_HOP_COST = 0.0


# ---------------------------------------------------------------------------
# The built navmeshes
# ---------------------------------------------------------------------------

class World(object):
    """Every navmesh of a built ESM, by FormID, exterior grid and interior cell."""

    def __init__(self, esm):
        """Read every NAVM and every interior cell's FULL name from `esm`."""
        with open(esm, 'rb') as fh:
            data = fh.read()
        self.navs, self.by_grid, self.by_cell, self.cell_names = {}, defaultdict(list), defaultdict(list), {}
        self.xtel, self.door_tri, self.tri_door, self.interior_of = {}, {}, {}, {}
        for rec, stack in walk(data, b'NAVM', b'CELL', b'REFR'):
            subs = rec.sub_map()
            if rec.sig == b'CELL':
                full = subs.get(b'FULL')
                if full and not stack.worldspace:
                    self.cell_names[full.split(b'\0')[0].decode('cp1252', 'replace').lower()] = rec.form_id
                continue
            if rec.sig == b'REFR':
                if b'XTEL' in subs:
                    self.xtel[rec.form_id] = struct.unpack_from('<I', subs[b'XTEL'], 0)[0]
                continue
            if subs.get(b'NVNM'):
                self.add_nav(Nav(rec.form_id, subs[b'NVNM']), stack.cell)
        names = {fid: name for name, fid in self.cell_names.items()}
        for fid, cell in list(self.interior_of.items()):
            self.interior_of[fid] = names.get(cell)

    def add_nav(self, nav, cell):
        """Index one navmesh by grid or interior cell, and its door triangles by door reference."""
        self.navs[nav.fid] = nav
        if nav.view.exterior:
            self.by_grid[nav.view.grid].append(nav)
        else:
            self.by_cell[cell].append(nav)
            self.interior_of[nav.fid] = cell
        for ti, ref in nav.view.door_triangles():
            self.door_tri[ref] = (nav.fid, ti)
            self.tri_door[(nav.fid, ti)] = ref

    def meshes_at(self, x, y, interior=None):
        """The interior's navmeshes, else the exterior grid square's."""
        if interior:
            return self.by_cell.get(self.cell_names.get(interior.lower()), [])
        return self.by_grid.get((int(x // CELL), int(y // CELL)), [])

    def find(self, p, interior=None):
        """`(fid, tri, exact)` the engine's lookup gives for point `p`, or None."""
        navs = self.meshes_at(p[0], p[1], interior)
        mi, ti, exact = locate(navs, *p)
        return (navs[mi].fid, ti, exact) if mi is not None else None

    def surface_z(self, p, interior=None):
        """Height of the navmesh triangle the lookup finds under `p`, on its plane; `p`'s own z if none."""
        got = self.find(p, interior)
        if got is None:
            return p[2]
        c = self.navs[got[0]].corners[got[1]]
        w = barycentric(c[None, 0, :2], c[None, 1, :2], c[None, 2, :2], p[0], p[1])[0]
        return float((w * c[:, 2]).sum())


# ---------------------------------------------------------------------------
# Triangle graph
# ---------------------------------------------------------------------------

def across(world, fid, ti, slot):
    """`(fid, tri)` across edge `slot` of triangle `ti`, through a neighbour slot or an edge link."""
    view = world.navs[fid].view
    tri = view.tris[ti]
    e = int(tri[3 + slot])
    if not tri[6] & EDGE_LINK_BITS[slot]:
        return (fid, e) if 0 <= e < len(view.tris) else None
    if not 0 <= e < len(view.links):
        return None
    typ, other, oti = view.links[e]
    if typ & ~LINK_PASSABLE:
        return None
    return (other, int(oti)) if other in world.navs and 0 <= oti < len(world.navs[other].t) else None


def edge(world, fid, ti, slot):
    """The two 3D end points of edge `slot` of triangle `ti`."""
    view = world.navs[fid].view
    tri = view.tris[ti]
    return view.verts[int(tri[slot])].astype(float), view.verts[int(tri[(slot + 1) % 3])].astype(float)


def center(world, node):
    """A triangle's 3D center."""
    return world.navs[node[0]].centers[node[1]].astype(float)


def step_cost(world, node, slot, nb, here):
    """The engine's cost of crossing edge `slot` of `node` into `nb` from point `here` (CK 0x28a2250)."""
    cost = float(np.linalg.norm(center(world, nb)[:2] - here[:2]))
    src = int(world.navs[node[0]].t[node[1], 6])
    dst = int(world.navs[nb[0]].t[nb[1], 6])
    for flag, has, lacks, change in COST_FLAGS:
        if (src ^ dst) & flag:
            cost *= change
        cost *= has if dst & flag else lacks
    a, b = edge(world, node[0], node[1], slot)
    width = float(np.linalg.norm((b - a)[:2]))
    return cost * 0.5 * (1.0 + min(max(1.0 - width / WIDE_EDGE, 0.0), 1.0))


def neighbours(world, node):
    """`(slot, node)` for each way out of a triangle: its three edges, and DOOR for a load door's hop."""
    for slot in range(3):
        nb = across(world, node[0], node[1], slot)
        if nb is not None:
            yield slot, nb
    ref = world.tri_door.get(node)
    other = world.door_tri.get(world.xtel.get(ref)) if ref else None
    if other is not None:
        yield DOOR, other


def astar(world, start, goal, start_point, goal_point):
    """`[(node, slot crossed out of it)]` from start to goal triangle, or None when none connects them."""
    best = {start: 0.0}
    came = {start: None}
    heap = [(0.0, 0.0, start)]
    while heap:
        _f, g, node = heapq.heappop(heap)
        if node == goal:
            break
        if g > best.get(node, float('inf')):
            continue
        here = start_point if node == start else center(world, node)
        for slot, nb in neighbours(world, node):
            ng = g + (DOOR_HOP_COST if slot == DOOR else step_cost(world, node, slot, nb, here))
            if ng < best.get(nb, float('inf')):
                best[nb], came[nb] = ng, (node, slot)
                h = HEURISTIC_WEIGHT * float(np.linalg.norm(center(world, nb) - goal_point))
                heapq.heappush(heap, (ng + h, ng, nb))
    if goal not in came:
        return None
    chain, node = [], goal
    while came[node] is not None:
        prev, slot = came[node]
        chain.append((prev, slot))
        node = prev
    return chain[::-1]


# ---------------------------------------------------------------------------
# Taut path through the crossed edges
# ---------------------------------------------------------------------------

def cross2(o, a, b):
    """Plan cross product of o->a and o->b."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def border_verts(world, fid):
    """Vertex indices of navmesh `fid` that lie on an open edge (no neighbour, no link), cached."""
    nav = world.navs[fid]
    if not hasattr(nav, 'border'):
        t = nav.t
        open_ = [(t[:, 3 + s] < 0) & ((t[:, 6] & EDGE_LINK_BITS[s]) == 0) for s in range(3)]
        nav.border = set(t[open_[0], 0].tolist() + t[open_[0], 1].tolist() + t[open_[1], 1].tolist()
                         + t[open_[1], 2].tolist() + t[open_[2], 2].tolist() + t[open_[2], 0].tolist())
    return nav.border


def inset(a, b, by, pull_a, pull_b):
    """Edge a-b with each border end pulled `by` inward; the midpoint twice when it cannot fit."""
    d = b - a
    n = float(np.linalg.norm(d[:2]))
    need = by * (pull_a + pull_b)
    if n <= need:
        m = (a + b) / 2.0
        return m, m
    return a + d * (by * pull_a / n), b - d * (by * pull_b / n)


def portals(world, chain, start, goal, radius):
    """`[(left, right)]` gates for the funnel: the start, each crossed edge kept clear of its ends, the goal.

    The engine's smoother casts rays (radius + 5) / 1.01 wide (CK 0x28c3aaa), so its path keeps that far
    from the corridor's corners.
    """
    clear = (radius + 5.0) / 1.01
    out = [(start, start)]
    for (fid, ti), slot in chain:
        tri = world.navs[fid].t[ti]
        border = border_verts(world, fid)
        a, b = inset(*edge(world, fid, ti, slot), clear,
                     int(tri[slot]) in border, int(tri[(slot + 1) % 3]) in border)
        c = center(world, (fid, ti))
        out.append((b, a) if cross2(c, a, b) > 0 else (a, b))
    out.append((goal, goal))
    return out


def funnel(gates):
    """The taut path through `[(left, right)]` gates (Mononen's simple stupid funnel), as 3D points."""
    path = [gates[0][0]]
    apex = left = right = gates[0][0]
    ai = li = ri = 0
    i = 1
    while i < len(gates):
        nl, nr = gates[i]
        if cross2(apex, right, nr) >= 0:
            if np.allclose(apex, right) or cross2(apex, left, nr) < 0:
                right, ri = nr, i
            else:
                path.append(left)
                apex, ai = left, li
                right, ri = apex, ai
                i = ai + 1
                continue
        if cross2(apex, left, nl) <= 0:
            if np.allclose(apex, left) or cross2(apex, right, nl) > 0:
                left, li = nl, i
            else:
                path.append(right)
                apex, ai = right, ri
                left, li = apex, ai
                i = ai + 1
                continue
        i += 1
    if not np.allclose(path[-1], gates[-1][0]):
        path.append(gates[-1][0])
    return path


def reachable(world, start, limit=400000):
    """Every triangle node reachable from `start` over edges, passable edge links and load doors."""
    seen, todo = {start}, [start]
    while todo and len(seen) < limit:
        node = todo.pop()
        for _slot, nb in neighbours(world, node):
            if nb not in seen:
                seen.add(nb)
                todo.append(nb)
    return seen


def gap(world, a, b, interior=None):
    """`(distance, point near the start's side, point near the goal's side)`: where the navmesh breaks."""
    s, g = world.find(a, interior), world.find(b, interior)
    from_a, from_b = reachable(world, s[:2]), reachable(world, g[:2])
    pa = np.array([center(world, n) for n in from_a])
    pb = np.array([center(world, n) for n in from_b])
    goal = np.asarray(b, float)
    near_a = pa[np.argsort(((pa - goal) ** 2).sum(axis=1))[:2000]]
    best = None
    for p in near_a:
        d = ((pb - p) ** 2).sum(axis=1)
        k = int(np.argmin(d))
        if best is None or d[k] < best[0]:
            best = (float(d[k]), p, pb[k])
    return math.sqrt(best[0]), best[1], best[2]


def legs(world, chain, a, b):
    """Split a triangle chain at its door hops: `[(start point, sub-chain, end point, interior name)]`."""
    out, part, start, place = [], [], np.array(a, float), world.interior_of.get(chain[0][0][0] if chain else None)
    for node, slot in chain:
        if slot == DOOR:
            out.append((start, part, center(world, node), place))
            dest = world.door_tri[world.xtel[world.tri_door[node]]]
            part, start, place = [], center(world, dest), world.interior_of.get(dest[0])
            continue
        part.append((node, slot))
    out.append((start, part, np.array(b, float), place))
    return out


def plan(world, a, b, interior=None, radius=DEFAULT_RADIUS):
    """`(legs, navmeshes crossed, why)`: legs are `[(points, interior name)]` split at load doors, else None."""
    s, g = world.find(a, interior), world.find(b, interior)
    if s is None or g is None:
        return None, None, 'no navmesh under the %s' % ('start' if s is None else 'goal')
    chain = astar(world, s[:2], g[:2], np.array(a, float), np.array(b, float))
    if chain is None:
        return None, None, 'no triangle path'
    meshes = [s[0]]
    for (fid, _ti), _slot in chain:
        if fid != meshes[-1]:
            meshes.append(fid)
    if g[0] != meshes[-1]:
        meshes.append(g[0])
    out = [(funnel(portals(world, part, p, q, radius)), place) for p, part, q, place in legs(world, chain, a, b)]
    if not chain:
        out = [([np.array(a, float), np.array(b, float)], interior)]
    return out, meshes, 'ok'


def main():
    """CLI: one path between two points of a built plugin."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--interior', help="the interior cell's name (exteriors need none)")
    ap.add_argument('--from', dest='a', required=True)
    ap.add_argument('--to', dest='b', required=True)
    ap.add_argument('--esm', help='a built ESM to read instead of the plugin output (a baseline copy)')
    a = ap.parse_args()
    export = master_export_dir('export', a.plugin)
    world = World(a.esm or str(plugin_esm('output', a.plugin, export_root(export))))
    route, meshes, why = plan(world, [float(v) for v in a.a.split(',')], [float(v) for v in a.b.split(',')],
                              a.interior)
    print(why, 'via navmeshes', ' '.join('%08X' % f for f in meshes or ()))
    if why == 'no triangle path':
        dist, pa, pb = gap(world, [float(v) for v in a.a.split(',')], [float(v) for v in a.b.split(',')],
                           a.interior)
        print('navmesh breaks: %.0f apart between %.0f %.0f %.0f (start side) and %.0f %.0f %.0f (goal side)'
              % ((dist,) + tuple(pa) + tuple(pb)))
    for points, place in route or ():
        print('-- leg in %s' % (place or 'the exterior'))
        for p in points:
            print('%.0f %.0f %.0f' % tuple(p))
    return 0


if __name__ == '__main__':
    sys.exit(main())
