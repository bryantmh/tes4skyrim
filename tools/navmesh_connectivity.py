#!/usr/bin/env python3
"""Audit navmesh CONNECTIVITY: are cell navmeshes stitched together?

A navmesh can be geometrically perfect and still be useless: without Edge Links
each cell's mesh is an isolated island, so an actor paths fine inside its current
cell and can NEVER cross a cell boundary.  Any AI package whose destination is in
another cell then starts (the actor stands up, plays its en-route dialogue) and
never moves.  That is a silent, game-wide AI failure that geometry-only checks
(coverage, triangle quality) do not see.

Binary facts this encodes (verified against Skyrim.esm, 15,949/15,949 clean):
  * Edge Link = Type(U32) + Navmesh(FormID U32) + Triangle(S16) = **10 bytes**.
    A 12-byte stride misparses every navmesh that has links.
  * A triangle's flag bits 0/1/2 mean 'Edge 0-1 / 1-2 / 2-0 Link'.  When bit N is
    set, that triangle's edge-N field is an INDEX into the Edge Links array
    rather than a local neighbour-triangle index (xEdit wbEdgeToStr).
  * Edge Link Type: 0 Portal (cell seam), 1 Ledge Up, 2 Ledge Down,
    3 Enable/Disable Portal.
  * NVNM header: version(U32) crc(U32) worldspace(U32); then for an EXTERIOR a
    4-byte grid (S16 gridY, S16 gridX), for an INTERIOR a 4-byte cell FormID.

Vanilla baseline: 84% of exterior navmeshes carry edge links (194,744 total).

Usage:
    python tools/navmesh_connectivity.py output/Oblivion.esm/Oblivion.esm
    python tools/navmesh_connectivity.py <esm> --ref <Skyrim.esm>
    python tools/navmesh_connectivity.py <esm> --cell -48,-7    # one grid cell
"""

import argparse
import mmap
import struct
import sys
import zlib
from collections import Counter, defaultdict

REC_HDR = 24
GRP_HDR = 24

EDGE_LINK_SIZE = 10          # Type U32 + Navmesh U32 + Triangle S16
DOOR_TRI_SIZE = 10           # Triangle S16 + CRC U32 + Door FormID U32

TRI_FLAG_EDGE_LINK = 0x0007  # bits 0,1,2
TRI_FLAG_DOOR = 0x0400
TRI_FLAG_FOUND = 0x0800

LINK_TYPES = {0: 'Portal', 1: 'LedgeUp', 2: 'LedgeDown', 3: 'EnableDisablePortal'}

# Vanilla share of exterior navmeshes carrying edge links.
VANILLA_EXTERIOR_LINK_RATE = 0.84


def walk(data, start, end):
    p = start
    while p < end - 8:
        sig = data[p:p + 4]
        if sig == b'GRUP':
            size = struct.unpack('<I', data[p + 4:p + 8])[0]
            if size < GRP_HDR:
                return
            yield from walk(data, p + GRP_HDR, p + size)
            p += size
            continue
        size, flags = struct.unpack('<II', data[p + 4:p + 12])
        fid = struct.unpack('<I', data[p + 12:p + 16])[0]
        body = data[p + REC_HDR:p + REC_HDR + size]
        if flags & 0x00040000:
            try:
                body = zlib.decompress(body[4:])
            except zlib.error:
                body = b''
        yield sig.decode('latin1'), fid, body
        p += REC_HDR + size


def subrecords(body):
    out = []
    p = 0
    while p < len(body) - 5:
        sig = body[p:p + 4].decode('latin1')
        ln = struct.unpack('<H', body[p + 4:p + 6])[0]
        out.append((sig, body[p + 6:p + 6 + ln]))
        p += 6 + ln
    return out


class NavMesh:
    __slots__ = ('fid', 'exterior', 'grid', 'cell', 'nverts', 'tris',
                 'links', 'ndoor', 'clean')

    def __init__(self, fid, d):
        self.fid = fid
        p = 8                                   # version + crc
        wrld = struct.unpack_from('<I', d, p)[0]; p += 4
        self.exterior = wrld != 0
        if self.exterior:
            gy, gx = struct.unpack_from('<hh', d, p); p += 4
            self.grid = (gx, gy)
            self.cell = None
        else:
            self.grid = None
            self.cell = struct.unpack_from('<I', d, p)[0]; p += 4
        nv = struct.unpack_from('<I', d, p)[0]; p += 4 + nv * 12
        self.nverts = nv
        nt = struct.unpack_from('<I', d, p)[0]; p += 4
        self.tris = [struct.unpack_from('<6h2H', d, p + i * 16) for i in range(nt)]
        p += nt * 16
        ne = struct.unpack_from('<I', d, p)[0]; p += 4
        self.links = [struct.unpack_from('<IIh', d, p + i * EDGE_LINK_SIZE)
                      for i in range(ne)]
        p += ne * EDGE_LINK_SIZE
        nd = struct.unpack_from('<I', d, p)[0]; p += 4 + nd * DOOR_TRI_SIZE
        self.ndoor = nd
        nc = struct.unpack_from('<I', d, p)[0]; p += 4 + nc * 2
        div = struct.unpack_from('<I', d, p)[0]; p += 4 + 8 + 24
        for _ in range(div * div):
            if p + 4 > len(d):
                break
            cnt = struct.unpack_from('<I', d, p)[0]; p += 4 + cnt * 2
        self.clean = (p == len(d))

    def linked_edge_count(self):
        return sum(bin(t[6] & TRI_FLAG_EDGE_LINK).count('1') for t in self.tris)

    def check(self):
        out = []
        if not self.clean:
            out.append('NVNM did not consume exactly its length (misparse)')
        flagged = self.linked_edge_count()
        if flagged != len(self.links):
            out.append(f'{flagged} triangle edges flagged as links but '
                       f'{len(self.links)} Edge Link entries')
        for t in self.tris:
            for bit, edge_field in ((0, t[3]), (1, t[4]), (2, t[5])):
                if t[6] & (1 << bit):
                    if not (0 <= edge_field < len(self.links)):
                        out.append(f'edge flagged as link but its field '
                                   f'{edge_field} is not a valid Edge Link index '
                                   f'(0..{len(self.links) - 1})')
                        break
        return out


def load(path):
    with open(path, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    hsize = struct.unpack('<I', mm[4:8])[0]
    out = []
    for sig, fid, body in walk(mm, REC_HDR + hsize, len(mm)):
        if sig != 'NAVM' or not body:
            continue
        for s, d in subrecords(body):
            if s != 'NVNM' or len(d) < 32:
                continue
            try:
                out.append(NavMesh(fid, d))
            except (struct.error, IndexError):
                pass
    return out


def report(meshes, label):
    ext = [m for m in meshes if m.exterior]
    inte = [m for m in meshes if not m.exterior]
    ext_linked = [m for m in ext if m.links]
    total_links = sum(len(m.links) for m in meshes)
    doors = sum(m.ndoor for m in meshes)
    misparse = sum(1 for m in meshes if not m.clean)
    rate = len(ext_linked) / len(ext) if ext else 0.0
    print(f'{label}:')
    print(f'  NAVM parsed      : {len(meshes)} (misparse {misparse})')
    print(f'  exterior         : {len(ext)}  with edge links: '
          f'{len(ext_linked)} ({rate * 100:.0f}%)')
    print(f'  interior         : {len(inte)}  (interiors connect via door '
          f'triangles, not edge links)')
    print(f'  total edge links : {total_links}')
    print(f'  total door tris  : {doors}')
    types = Counter(LINK_TYPES.get(l[0], f'?{l[0]}')
                    for m in meshes for l in m.links)
    if types:
        print(f'  link types       : {dict(types)}')
    return rate


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('esm')
    ap.add_argument('--ref', help='reference master to compare against')
    ap.add_argument('--cell', help='report one exterior grid cell "gx,gy"')
    ap.add_argument('--limit', type=int, default=20)
    args = ap.parse_args()

    meshes = load(args.esm)
    rate = report(meshes, args.esm)

    if args.cell:
        gx, gy = [int(v) for v in args.cell.split(',')]
        for m in meshes:
            if m.grid == (gx, gy):
                print(f'\n  cell ({gx},{gy}) NAVM 0x{m.fid:08x}: '
                      f'verts={m.nverts} tris={len(m.tris)} '
                      f'edge_links={len(m.links)} door_tris={m.ndoor}')
                for i, (t, nav, tri) in enumerate(m.links[:args.limit]):
                    print(f'      [{i}] {LINK_TYPES.get(t, t)} -> '
                          f'NAVM 0x{nav:08x} tri {tri}')

    problems = 0
    for m in meshes:
        for msg in m.check():
            problems += 1
            if problems <= args.limit:
                print(f'  [ERROR] NAVM 0x{m.fid:08x} grid={m.grid}: {msg}')
    if problems:
        print(f'  internal-consistency errors: {problems}')

    if args.ref:
        print()
        report(load(args.ref), args.ref)

    print()
    if rate == 0 and meshes:
        print('VERDICT: NO navmesh carries edge links. Every cell navmesh is an '
              'island — actors cannot path across a cell boundary, so any AI '
              'package with an out-of-cell destination will start and never '
              f'move. Vanilla links {VANILLA_EXTERIOR_LINK_RATE:.0%} of exterior '
              'navmeshes.')
        return 1
    if rate < VANILLA_EXTERIOR_LINK_RATE * 0.5:
        print(f'VERDICT: only {rate:.0%} of exterior navmeshes carry edge links '
              f'vs {VANILLA_EXTERIOR_LINK_RATE:.0%} in vanilla — cross-cell '
              'pathing is likely broken in much of the world.')
        return 1
    print(f'VERDICT: exterior edge-link coverage {rate:.0%} '
          f'(vanilla {VANILLA_EXTERIOR_LINK_RATE:.0%}).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
