"""Answer "can an actor PATH from A to B?" against a built ESM's navmeshes.

The package layer can be perfect and an actor still stands still: pathfinding
needs (1) a start triangle under the actor, (2) a goal triangle under the
destination, (3) a connected walk between them — inside one mesh via triangle
adjacency, across cell seams via NVNM Edge Links, and through load doors via
door triangles + the door's XTEL partner.  This tool walks exactly that graph
and reports where the chain breaks.

Usage:
    # Same-cell / cross-cell reachability between two placed refs:
    python tools/navmesh/reach.py output/Oblivion.esm/Oblivion.esm \
        --from-ref 0102FD6F --to-ref 0106D812

    # Reachability between explicit points (worldspace 0 = interior cell):
    python tools/navmesh/reach.py <esm> --cell 0102DC01 \
        --from-point 165,2,0 --to-point 712,-18,186

    # Dump one cell's mesh component structure (islands, z-ranges, doors):
    python tools/navmesh/reach.py <esm> --cell 0102DC01 --components

FormIDs are the OUTPUT (load-order) ids, e.g. 0102DC01 for Oblivion.esm cell
0002DC01 loaded after Skyrim.esm.
"""

import argparse
import struct
import sys
from collections import defaultdict, deque

import numpy as np

from tes5_import.base.tes5_reader import walk as walk_plugin


# ---------------------------------------------------------------------------
# NVNM decode
# ---------------------------------------------------------------------------

class Mesh:
    __slots__ = ('fid', 'wrld', 'cell', 'grid', 'verts', 'tris', 'links',
                 'doors', 'components', 'tri_comp')

    def __init__(self, fid, d):
        self.fid = fid
        p = 8
        self.wrld = struct.unpack_from('<I', d, p)[0]
        p += 4
        if self.wrld:
            gy, gx = struct.unpack_from('<hh', d, p)
            self.grid = (gx, gy)
            self.cell = None
        else:
            self.grid = None
            self.cell = struct.unpack_from('<I', d, p)[0]
        p += 4
        nv = struct.unpack_from('<I', d, p)[0]
        p += 4
        # Bulk-decode with numpy: per-element struct.unpack_from over 8k meshes
        # (~400 MB of vert/tri data) is the tool's dominant cost.  verts is a
        # (nv, 3) float32 view; tris is (nt, 8) int16 — the two trailing U16
        # flag fields read fine as int16 for the bit tests used here.
        self.verts = (np.frombuffer(d, dtype='<f4', count=nv * 3, offset=p)
                      .reshape(nv, 3))
        p += nv * 12
        nt = struct.unpack_from('<I', d, p)[0]
        p += 4
        self.tris = (np.frombuffer(d, dtype='<i2', count=nt * 8, offset=p)
                     .reshape(nt, 8))
        p += nt * 16
        ne = struct.unpack_from('<I', d, p)[0]
        p += 4
        self.links = [struct.unpack_from('<IIh', d, p + i * 10)
                      for i in range(ne)]
        p += ne * 10
        nd = struct.unpack_from('<I', d, p)[0]
        p += 4
        self.doors = [struct.unpack_from('<hII', d, p + i * 10)
                      for i in range(nd)]          # (tri, crc, door_ref)
        self.components = []
        self.tri_comp = {}

    # -- local components over triangle adjacency (edge links NOT followed) --
    def build_components(self):
        n = len(self.tris)
        seen = [False] * n
        comps = []
        for s in range(n):
            if seen[s] or (self.tris[s][6] & 0x0008):
                continue
            comp = []
            dq = deque([s])
            seen[s] = True
            while dq:
                t = dq.popleft()
                comp.append(t)
                tri = self.tris[t]
                for slot in range(3):
                    if tri[6] & (1 << slot):
                        continue          # external edge link, not local
                    nb = tri[3 + slot]
                    if 0 <= nb < n and not seen[nb]:
                        seen[nb] = True
                        dq.append(nb)
            comps.append(comp)
        comps.sort(key=len, reverse=True)
        self.components = comps
        self.tri_comp = {t: ci for ci, comp in enumerate(comps) for t in comp}

    def centroid_z(self, t):
        tri = self.tris[t]
        return sum(self.verts[tri[k]][2] for k in range(3)) / 3.0

    def locate(self, x, y, z, max_dz=256.0):
        """Triangle whose 2D projection contains (x,y), nearest in z; else the
        triangle with the closest centroid.  Returns (tri_index, how)."""
        best_in = None
        for i, tri in enumerate(self.tris):
            if tri[6] & 0x0008:
                continue
            ax, ay, _ = self.verts[tri[0]]
            bx, by, _ = self.verts[tri[1]]
            cx, cy, _ = self.verts[tri[2]]
            d1 = (ax - bx) * (y - by) - (x - bx) * (ay - by)
            d2 = (bx - cx) * (y - cy) - (x - cx) * (by - cy)
            d3 = (cx - ax) * (y - ay) - (x - ax) * (cy - ay)
            neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
            pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
            if neg and pos:
                continue
            dz = abs(self.centroid_z(i) - z)
            if dz <= max_dz and (best_in is None or dz < best_in[1]):
                best_in = (i, dz)
        if best_in is not None:
            return best_in[0], 'contains'
        best = None
        for i in range(len(self.tris)):
            if self.tris[i][6] & 0x0008:
                continue
            tri = self.tris[i]
            mx = sum(self.verts[tri[k]][0] for k in range(3)) / 3.0
            my = sum(self.verts[tri[k]][1] for k in range(3)) / 3.0
            mz = self.centroid_z(i)
            d = (mx - x) ** 2 + (my - y) ** 2 + (mz - z) ** 2
            if best is None or d < best[1]:
                best = (i, d)
        return (best[0], f'nearest d={best[1] ** 0.5:.0f}') if best else (None, 'empty')


# ---------------------------------------------------------------------------
# Global reachability graph: node = (mesh_fid, component)
# ---------------------------------------------------------------------------

def build_graph(meshes, ref_parent, ref_xtel):
    """Adjacency over (mesh, component) nodes via NVNM edge links and doors."""
    adj = defaultdict(set)

    for m in meshes.values():
        # Edge links: the triangle carrying flag bit N stores a LINK INDEX.
        for i, tri in enumerate(m.tris):
            for slot in range(3):
                if not (tri[6] & (1 << slot)):
                    continue
                li = tri[3 + slot]
                if not (0 <= li < len(m.links)):
                    continue
                typ, other_fid, other_tri = m.links[li]
                other = meshes.get(other_fid)
                if other is None or other_tri not in other.tri_comp:
                    continue
                a = (m.fid, m.tri_comp.get(i))
                b = (other_fid, other.tri_comp[other_tri])
                adj[a].add(b)
                adj[b].add(a)

    # Doors: mesh A's door tri names door REFR D; D teleports (XTEL) to door
    # REFR D2; the mesh holding a door tri for D2 is the other side.
    door_side = {}
    for m in meshes.values():
        for (t, _crc, dref) in m.doors:
            if t in m.tri_comp:
                door_side[dref] = (m.fid, m.tri_comp[t])
    for dref, node in door_side.items():
        partner = ref_xtel.get(dref)
        if partner and partner in door_side:
            adj[node].add(door_side[partner])
            adj[door_side[partner]].add(node)
    return adj, door_side


def route(adj, a, b):
    """The fewest-hop chain of (mesh, component) nodes from a to b, or None."""
    came = {a: None}
    dq = deque([a])
    while dq and b not in came:
        n = dq.popleft()
        for nb in adj.get(n, ()):
            if nb not in came:
                came[nb] = n
                dq.append(nb)
    if b not in came:
        return None
    chain = [b]
    while came[chain[-1]] is not None:
        chain.append(came[chain[-1]])
    return chain[::-1]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def parse_args():
    """The command line."""
    ap = argparse.ArgumentParser()
    ap.add_argument('esm')
    ap.add_argument('--from-ref')
    ap.add_argument('--to-ref')
    ap.add_argument('--cell', help='cell FormID for --from-point/--components')
    ap.add_argument('--from-point')
    ap.add_argument('--to-point')
    ap.add_argument('--components', action='store_true',
                    help='dump per-mesh component structure for --cell')
    return ap.parse_args()


def load_plugin(path, want_refs):
    """Every NAVM, the wanted refs' positions and parents, and door XTELs."""
    data = open(path, 'rb').read()
    start = 24 + struct.unpack_from('<I', data, 4)[0]
    meshes, ref_pos, ref_parent, ref_xtel = {}, {}, {}, {}
    for rec, stack in walk_plugin(data, b'NAVM', b'REFR', b'ACHR', b'ACRE',
                                  span=(start, len(data))):
        subs = rec.sub_map()
        if rec.sig == b'NAVM':
            nvnm = subs.get(b'NVNM')
            try:
                if nvnm:
                    meshes[rec.form_id] = Mesh(rec.form_id, nvnm)
            except (struct.error, IndexError):
                pass
            continue
        xtel = subs.get(b'XTEL')
        if xtel and len(xtel) >= 4:
            ref_xtel[rec.form_id] = struct.unpack_from('<I', xtel, 0)[0]
        data_sub = subs.get(b'DATA')
        if rec.form_id in want_refs and data_sub and len(data_sub) >= 12:
            ref_pos[rec.form_id] = struct.unpack_from('<fff', data_sub, 0)
            ref_parent[rec.form_id] = (stack.worldspace, stack.cell)
    return meshes, ref_pos, ref_parent, ref_xtel


def locate_point(meshes, by_cell, pos, wrld, cell):
    """(mesh, tri, how) under `pos`: its interior cell's meshes, else its grid's."""
    x, y, z = pos
    cands = by_cell.get(cell, []) if cell else []
    if not cands:
        grid = (int(x // 4096), int(y // 4096))
        cands = [m for m in meshes.values()
                 if m.grid == grid and (wrld is None or m.wrld == wrld)]
    best = None
    for m in cands:
        t, how = m.locate(x, y, z)
        score = 0 if how == 'contains' else 1
        if t is not None and (best is None or score < best[3]):
            best = (m, t, how, score)
    return best[:3] if best else None


def dump_components(by_cell, cfid):
    """Print each of one cell's meshes as islands with z range and doors."""
    for m in by_cell.get(cfid, []):
        print(f"\nNAVM {m.fid:#010x} cell={cfid:#010x}: "
              f"{len(m.verts)} verts, {len(m.tris)} tris, "
              f"{len(m.links)} edge links, {len(m.doors)} door tris")
        for ci, comp in enumerate(m.components):
            zs = [m.centroid_z(t) for t in comp]
            doors = [hex(dr) for (t, _c, dr) in m.doors
                     if m.tri_comp.get(t) == ci]
            print(f"  component {ci}: {len(comp)} tris, "
                  f"z {min(zs):.0f}..{max(zs):.0f}, doors={doors}")


def report_route(meshes, adj, ends):
    """Print where each end lands and the fewest-hop route between them."""
    nodes = []
    for label, (ep, pos, par) in zip(('FROM', 'TO'), ends):
        if ep is None:
            print(f"{label}: {pos} parent={par} -> NO MESH FOUND")
            continue
        m, t, how = ep
        print(f"{label}: {pos} -> mesh {m.fid:#010x} tri {t} ({how}) "
              f"component {m.tri_comp.get(t)} of {len(m.components)} "
              f"(sizes {[len(c) for c in m.components[:6]]})")
        nodes.append((m.fid, m.tri_comp.get(t)))
    if len(nodes) < 2:
        return
    chain = route(adj, *nodes)
    print(f"REACHABLE: {chain is not None}   ({nodes[0][0]:#010x}/c{nodes[0][1]} -> "
          f"{nodes[1][0]:#010x}/c{nodes[1][1]})")
    for fid, comp in chain or ():
        place = meshes[fid].grid or f'cell {meshes[fid].cell:#010x}'
        print(f"  via {fid:#010x}/c{comp} {place}")


def main():
    """Load the plugin, build the graph, answer what the arguments ask."""
    args = parse_args()
    refs = {int(a, 16) for a in (args.from_ref, args.to_ref) if a}
    meshes, ref_pos, ref_parent, ref_xtel = load_plugin(args.esm, refs)
    print(f"meshes={len(meshes)} teleport_doors={len(ref_xtel)}")
    for m in meshes.values():
        m.build_components()
    adj, door_side = build_graph(meshes, ref_parent, ref_xtel)
    n_door_edges = sum(1 for a in adj for b in adj[a] if a[0] != b[0]) // 2
    print(f"graph nodes={len(adj)} (cross-mesh edges={n_door_edges}, "
          f"door-tri meshes={len(door_side)})")
    by_cell = defaultdict(list)
    for m in meshes.values():
        if m.cell:
            by_cell[m.cell].append(m)
    if args.components and args.cell:
        dump_components(by_cell, int(args.cell, 16))
    ends = []
    for ref_arg, point_arg in ((args.from_ref, args.from_point), (args.to_ref, args.to_point)):
        if ref_arg and int(ref_arg, 16) not in ref_pos:
            sys.exit(f"ref {int(ref_arg, 16):#010x} not found (or no DATA)")
        if ref_arg:
            pos, par = ref_pos[int(ref_arg, 16)], ref_parent[int(ref_arg, 16)]
        elif point_arg:
            pos = tuple(float(v) for v in point_arg.split(','))
            par = (None, int(args.cell, 16) if args.cell else None)
        else:
            return
        ends.append((locate_point(meshes, by_cell, pos, *par), pos, par))
    report_route(meshes, adj, ends)


if __name__ == '__main__':
    main()

