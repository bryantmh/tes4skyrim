"""Dump raw NAVI / NAVM records from a TES5 ESM/ESP for format verification.

Walks the file's top-level groups, finds NAVI (top-level record) and NAVM
(inside CELL/WRLD temporary groups), decompresses compressed records, and
prints subrecord signatures + hex so the on-disk layout can be compared
against the xEdit definition.

Usage:
    python tools/navmesh/dump.py "C:/.../Skyrim.esm" --navi --max 1
    python tools/navmesh/dump.py "C:/.../Skyrim.esm" --navm --max 3
    python tools/navmesh/dump.py "C:/.../Skyrim.esm" --navm --nvnm-decode --max 1
"""

import argparse
import struct

from tes5_import.base.tes5_reader import records, subrecords


def _hex(b, limit=None):
    if limit and len(b) > limit:
        return b[:limit].hex() + f'... (+{len(b)-limit} bytes)'
    return b.hex()


def _decode_nvnm(d):
    """Decode an NVNM blob and print the header + counts."""
    p = 0
    ver = struct.unpack_from('<I', d, p)[0]; p += 4
    crc = struct.unpack_from('<I', d, p)[0]; p += 4
    wrld = struct.unpack_from('<I', d, p)[0]; p += 4
    print(f"    NVNM version={ver} crc=0x{crc:08X} worldspace=0x{wrld:08X}")
    if wrld == 0:
        cell = struct.unpack_from('<I', d, p)[0]; p += 4
        print(f"    parent(interior) cell=0x{cell:08X}")
    else:
        gy, gx = struct.unpack_from('<hh', d, p); p += 4
        print(f"    parent(exterior) gridY={gy} gridX={gx}")
    nv = struct.unpack_from('<I', d, p)[0]; p += 4
    print(f"    vertices={nv}")
    p += nv * 12
    nt = struct.unpack_from('<I', d, p)[0]; p += 4
    print(f"    triangles={nt}")
    if nt:
        t0 = struct.unpack_from('<6h2H', d, p)
        print(f"    tri[0]= v({t0[0]},{t0[1]},{t0[2]}) "
              f"e({t0[3]},{t0[4]},{t0[5]}) flags=0x{t0[6]:04X} cover=0x{t0[7]:04X}")
    p += nt * 16
    # Edge Link = Type(U32) + Navmesh(FormID U32) + Triangle(S16) = 10 bytes.
    # NOT 12: verified against Skyrim.esm NAVM 0x00101F28 (63 links), whose raw
    # bytes read 00000000 a61a1000 4500 | 00000000 a61a1000 b200 | ... — three
    # links to the same neighbour mesh 0x00101AA6 at triangles 69/178/297.
    # Stride 12 overruns the blob; stride 10 lands on a plausible tail
    # (door=0 cover=15 divisor=6). A 12-byte stride silently misparses every
    # navmesh that HAS edge links, which is most vanilla exteriors.
    ne = struct.unpack_from('<I', d, p)[0]; p += 4 + ne * 10
    nd = struct.unpack_from('<I', d, p)[0]; p += 4 + nd * 10   # Door Tri = S16+U32+FormID = 10
    nc = struct.unpack_from('<I', d, p)[0]; p += 4 + nc * 2
    print(f"    edge_links={ne} door_tris={nd} cover_tris={nc}")
    divisor = struct.unpack_from('<I', d, p)[0]; p += 4
    maxxd, maxyd = struct.unpack_from('<ff', d, p); p += 8
    bbox = struct.unpack_from('<6f', d, p); p += 24
    print(f"    grid_divisor={divisor} maxXdist={maxxd:.1f} maxYdist={maxyd:.1f}")
    print(f"    bbox min=({bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f}) "
          f"max=({bbox[3]:.1f},{bbox[4]:.1f},{bbox[5]:.1f})")
    # NavMeshGrid: divisor^2 arrays of U32 count + count*S16
    total = 0
    for _ in range(divisor * divisor):
        if p + 4 > len(d):
            break
        cnt = struct.unpack_from('<I', d, p)[0]; p += 4 + cnt * 2
        total += cnt
    print(f"    navmeshgrid cells={divisor*divisor} total_indexed_tris={total} "
          f"(consumed {p}/{len(d)} bytes)")


def _nvnm_counts(d):
    """(worldspace, gridY, gridX, vertices, triangles) from an NVNM blob.

    Grid is (None, None) for an interior, whose parent is a cell FormID.
    """
    p = 12
    wrld = struct.unpack_from('<I', d, 8)[0]
    if wrld == 0:
        gy = gx = None
    else:
        gy, gx = struct.unpack_from('<hh', d, p)
    p += 4
    nv = struct.unpack_from('<I', d, p)[0]
    p += 4 + nv * 12
    nt = struct.unpack_from('<I', d, p)[0]
    return wrld, gy, gx, nv, nt


def _summarize(data, only_grid):
    """Print one line per NAVM: id, parent grid, vertex and triangle counts."""
    rows = []
    for rec in records(data, b'NAVM'):
        for stag, sdata in subrecords(rec.body):
            if stag != b'NVNM':
                continue
            try:
                wrld, gy, gx, nv, nt = _nvnm_counts(sdata)
            except struct.error:
                continue
            if only_grid and (gx, gy) != only_grid:
                continue
            rows.append((rec.form_id, wrld, gy, gx, nv, nt))
    for fid, wrld, gy, gx, nv, nt in rows:
        where = 'interior' if gy is None else f'({gx},{gy})'
        print(f"NAVM 0x{fid:08X} wrld=0x{wrld:08X} {where:>12} "
              f"verts={nv:<6} tris={nt}")
    print(f"total navmeshes={len(rows)} "
          f"verts={sum(r[4] for r in rows)} tris={sum(r[5] for r in rows)}")


def _parse_args():
    """The command line, defaulting to a NAVI dump when no mode is named."""
    ap = argparse.ArgumentParser()
    ap.add_argument('esm')
    ap.add_argument('--navi', action='store_true', help='dump NAVI records')
    ap.add_argument('--navm', action='store_true', help='dump NAVM records')
    ap.add_argument('--nvnm-decode', action='store_true',
                    help='decode NVNM blob structure')
    ap.add_argument('--summary', action='store_true',
                    help='one line per NAVM: parent grid, vertex/triangle count')
    ap.add_argument('--grid', help='restrict --summary to exterior cell "X,Y"')
    ap.add_argument('--max', type=int, default=1)
    ap.add_argument('--hexlimit', type=int, default=256)
    args = ap.parse_args()
    if not (args.navi or args.navm or args.summary):
        args.navi = True
    return args


def main():
    args = _parse_args()

    with open(args.esm, 'rb') as f:
        data = f.read()

    if args.summary:
        grid = None
        if args.grid:
            gx, gy = args.grid.split(',')
            grid = (int(gx), int(gy))
        _summarize(data, grid)
        return

    # Skip the TES4 header record.
    hdr_size = struct.unpack_from('<I', data, 4)[0]
    start = 24 + hdr_size

    want = set()
    if args.navi:
        want.add('NAVI')
    if args.navm:
        want.add('NAVM')

    count = {s: 0 for s in want}
    for rec in records(data, *(s.encode('latin1') for s in want)):
        sig, formid, flags, body = (rec.sig.decode('latin1'), rec.form_id,
                                    rec.flags, rec.body)
        if count[sig] >= args.max:
            if all(count[s] >= args.max for s in want):
                break
            continue
        count[sig] += 1
        print(f"\n=== {sig} 0x{formid:08X} flags=0x{flags:08X} "
              f"bodylen={len(body)} ===")
        for stag, sdata in subrecords(body):
            ssig = stag.decode('latin1')
            print(f"  {ssig} ({len(sdata)}): {_hex(sdata, args.hexlimit)}")
            if ssig == 'NVNM' and args.nvnm_decode:
                try:
                    _decode_nvnm(sdata)
                except Exception as e:
                    print(f"    NVNM decode error: {e}")

    print(f"\nDumped: {count}")


if __name__ == '__main__':
    main()
