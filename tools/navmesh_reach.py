"""Answer "can an actor PATH from A to B?" against a built ESM's navmeshes.

The package layer can be perfect and an actor still stands still: pathfinding
needs (1) a start triangle under the actor, (2) a goal triangle under the
destination, (3) a connected walk between them — inside one mesh via triangle
adjacency, across cell seams via NVNM Edge Links, and through load doors via
door triangles + the door's XTEL partner.  This tool walks exactly that graph
and reports where the chain breaks.

Usage:
    # Same-cell / cross-cell reachability between two placed refs:
    python tools/navmesh_reach.py output/Oblivion.esm/Oblivion.esm \
        --from-ref 0102FD6F --to-ref 0106D812

    # Reachability between explicit points (worldspace 0 = interior cell):
    python tools/navmesh_reach.py <esm> --cell 0102DC01 \
        --from-point 165,2,0 --to-point 712,-18,186

    # Dump one cell's mesh component structure (islands, z-ranges, doors):
    python tools/navmesh_reach.py <esm> --cell 0102DC01 --components

FormIDs are the OUTPUT (load-order) ids, e.g. 0102DC01 for Oblivion.esm cell
0002DC01 loaded after Skyrim.esm.
"""

import argparse
import struct
import sys
import zlib
from collections import defaultdict, deque

import numpy as np


# ---------------------------------------------------------------------------
# ESM walking
# ---------------------------------------------------------------------------

def _iter_records(data, start, end, want):
    off = start
    while off + 24 <= end:
        sig = data[off:off + 4]
        size = struct.unpack_from('<I', data, off + 4)[0]
        if sig == b'GRUP':
            yield from _iter_records(data, off + 24, min(off + size, end), want)
            off += size
            continue
        flags = struct.unpack_from('<I', data, off + 8)[0]
        fid = struct.unpack_from('<I', data, off + 12)[0]
        if sig in want:
            body = data[off + 24:off + 24 + size]
            if flags & 0x00040000:
                try:
                    body = zlib.decompress(body[4:])
                except zlib.error:
                    body = b''
            yield sig, fid, body
        off += 24 + size


def _iter_subrecords(body):
    off = 0
    override = None
    while off + 6 <= len(body):
        sig = body[off:off + 4]
        size = struct.unpack_from('<H', body, off + 4)[0]
        off += 6
        if sig == b'XXXX':
            override = struct.unpack_from('<I', body, off)[0]
            off += size
            continue
        real = override if override is not None else size
        override = None
        yield sig, body[off:off + real]
        off += real


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


def reachable(adj, a, b):
    if a == b:
        return True
    seen = {a}
    dq = deque([a])
    while dq:
        n = dq.popleft()
        for nb in adj.get(n, ()):
            if nb == b:
                return True
            if nb not in seen:
                seen.add(nb)
                dq.append(nb)
    return False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('esm')
    ap.add_argument('--from-ref')
    ap.add_argument('--to-ref')
    ap.add_argument('--cell', help='cell FormID for --from-point/--components')
    ap.add_argument('--from-point')
    ap.add_argument('--to-point')
    ap.add_argument('--components', action='store_true',
                    help='dump per-mesh component structure for --cell')
    args = ap.parse_args()

    data = open(args.esm, 'rb').read()
    hdr = struct.unpack_from('<I', data, 4)[0]
    start = 24 + hdr

    want_refs = set()
    for a in (args.from_ref, args.to_ref):
        if a:
            want_refs.add(int(a, 16))

    # Pass 1: all NAVMs + the requested refs' positions/parents + door XTELs.
    meshes = {}
    ref_pos = {}
    ref_parent = {}
    ref_xtel = {}
    # Track the current CELL/WRLD context while walking records in order.
    cur_cell = [None]
    cur_wrld = [None]

    def walk(d, s, e):
        off = s
        while off + 24 <= e:
            sig = d[off:off + 4]
            size = struct.unpack_from('<I', d, off + 4)[0]
            if sig == b'GRUP':
                walk(d, off + 24, min(off + size, e))
                off += size
                continue
            flags = struct.unpack_from('<I', d, off + 8)[0]
            fid = struct.unpack_from('<I', d, off + 12)[0]
            if sig == b'CELL':
                cur_cell[0] = fid
            elif sig == b'WRLD':
                cur_wrld[0] = fid
            elif sig == b'NAVM':
                body = d[off + 24:off + 24 + size]
                if flags & 0x00040000:
                    try:
                        body = zlib.decompress(body[4:])
                    except zlib.error:
                        body = b''
                for ssig, sd in _iter_subrecords(body):
                    if ssig == b'NVNM':
                        try:
                            meshes[fid] = Mesh(fid, sd)
                        except (struct.error, IndexError):
                            pass
            elif sig in (b'REFR', b'ACHR', b'ACRE'):
                body = d[off + 24:off + 24 + size]
                if flags & 0x00040000:
                    try:
                        body = zlib.decompress(body[4:])
                    except zlib.error:
                        body = b''
                pos = None
                xtel = None
                for ssig, sd in _iter_subrecords(body):
                    if ssig == b'DATA' and len(sd) >= 12:
                        pos = struct.unpack_from('<fff', sd, 0)
                    elif ssig == b'XTEL' and len(sd) >= 4:
                        xtel = struct.unpack_from('<I', sd, 0)[0]
                if xtel:
                    ref_xtel[fid] = xtel
                if fid in want_refs and pos:
                    ref_pos[fid] = pos
                    ref_parent[fid] = (cur_wrld[0], cur_cell[0])
            off += 24 + size

    walk(data, start, len(data))
    print(f"meshes={len(meshes)} teleport_doors={len(ref_xtel)}")

    for m in meshes.values():
        m.build_components()

    adj, door_side = build_graph(meshes, ref_parent, ref_xtel)
    n_door_edges = sum(1 for a in adj for b in adj[a]
                       if a[0] != b[0]) // 2
    print(f"graph nodes={len(adj)} (cross-mesh edges={n_door_edges}, "
          f"door-tri meshes={len(door_side)})")

    by_cell = defaultdict(list)
    for m in meshes.values():
        if m.cell:
            by_cell[m.cell].append(m)

    def endpoint(ref_arg, point_arg):
        if ref_arg:
            fid = int(ref_arg, 16)
            if fid not in ref_pos:
                sys.exit(f"ref {fid:#010x} not found (or no DATA)")
            x, y, z = ref_pos[fid]
            wrld, cell = ref_parent[fid]
        else:
            x, y, z = (float(v) for v in point_arg.split(','))
            wrld, cell = None, int(args.cell, 16) if args.cell else None
        # candidate meshes: same interior cell, or same worldspace grid
        cands = []
        if cell and cell in by_cell:
            cands = by_cell[cell]
        if not cands:
            gx, gy = int(x // 4096), int(y // 4096)
            cands = [m for m in meshes.values()
                     if m.grid == (gx, gy) and (wrld is None or m.wrld == wrld)]
        best = None
        for m in cands:
            t, how = m.locate(x, y, z)
            if t is None:
                continue
            score = 0 if how == 'contains' else 1
            if best is None or score < best[3]:
                best = (m, t, how, score)
        if best is None:
            return None, (x, y, z), (wrld, cell)
        return best[:3], (x, y, z), (wrld, cell)

    if args.components and args.cell:
        cfid = int(args.cell, 16)
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

    if (args.from_ref or args.from_point) and (args.to_ref or args.to_point):
        a, apos, apar = endpoint(args.from_ref, args.from_point)
        b, bpos, bpar = endpoint(args.to_ref, args.to_point)
        for label, ep, pos, par in (('FROM', a, apos, apar),
                                    ('TO', b, bpos, bpar)):
            if ep is None:
                print(f"{label}: {pos} parent={par} -> NO MESH FOUND")
            else:
                m, t, how = ep
                print(f"{label}: {pos} -> mesh {m.fid:#010x} tri {t} ({how}) "
                      f"component {m.tri_comp.get(t)} "
                      f"of {len(m.components)} (sizes "
                      f"{[len(c) for c in m.components[:6]]})")
        if a and b:
            na = (a[0].fid, a[0].tri_comp.get(a[1]))
            nb = (b[0].fid, b[0].tri_comp.get(b[1]))
            ok = reachable(adj, na, nb)
            print(f"REACHABLE: {ok}   ({na[0]:#010x}/c{na[1]} -> "
                  f"{nb[0]:#010x}/c{nb[1]})")


if __name__ == '__main__':
    main()
