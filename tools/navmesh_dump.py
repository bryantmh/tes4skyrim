"""Dump raw NAVI / NAVM records from a TES5 ESM/ESP for format verification.

Walks the file's top-level groups, finds NAVI (top-level record) and NAVM
(inside CELL/WRLD temporary groups), decompresses compressed records, and
prints subrecord signatures + hex so the on-disk layout can be compared
against the xEdit definition.

Usage:
    python tools/navmesh_dump.py "C:/.../Skyrim.esm" --navi --max 1
    python tools/navmesh_dump.py "C:/.../Skyrim.esm" --navm --max 3
    python tools/navmesh_dump.py "C:/.../Skyrim.esm" --navm --nvnm-decode --max 1
"""

import argparse
import struct
import zlib


def _iter_records(data, start, end):
    """Yield (sig, formid, flags, body_bytes) for records under [start,end),
    recursing into GRUP groups. Decompresses compressed records."""
    off = start
    while off + 24 <= end:
        sig = data[off:off + 4]
        size = struct.unpack_from('<I', data, off + 4)[0]
        if sig == b'GRUP':
            grp_end = off + size
            yield from _iter_records(data, off + 24, min(grp_end, end))
            off = grp_end
            continue
        flags = struct.unpack_from('<I', data, off + 8)[0]
        formid = struct.unpack_from('<I', data, off + 12)[0]
        body = data[off + 24:off + 24 + size]
        if flags & 0x00040000:  # Compressed
            try:
                body = zlib.decompress(body[4:])
            except zlib.error:
                pass
        yield sig.decode('latin1'), formid, flags, body
        off += 24 + size


def _iter_subrecords(body):
    """Yield (sig, data_bytes) honoring the XXXX oversized protocol."""
    off = 0
    override = None
    while off + 6 <= len(body):
        sig = body[off:off + 4].decode('latin1')
        size = struct.unpack_from('<H', body, off + 4)[0]
        off += 6
        if sig == 'XXXX':
            override = struct.unpack_from('<I', body, off)[0]
            off += size
            continue
        real = override if override is not None else size
        override = None
        yield sig, body[off:off + real]
        off += real


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
    ne = struct.unpack_from('<I', d, p)[0]; p += 4 + ne * 12   # Edge Link = 12 bytes
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('esm')
    ap.add_argument('--navi', action='store_true', help='dump NAVI records')
    ap.add_argument('--navm', action='store_true', help='dump NAVM records')
    ap.add_argument('--nvnm-decode', action='store_true',
                    help='decode NVNM blob structure')
    ap.add_argument('--max', type=int, default=1)
    ap.add_argument('--hexlimit', type=int, default=256)
    args = ap.parse_args()

    if not (args.navi or args.navm):
        args.navi = True

    with open(args.esm, 'rb') as f:
        data = f.read()

    # Skip the TES4 header record.
    hdr_size = struct.unpack_from('<I', data, 4)[0]
    start = 24 + hdr_size

    want = set()
    if args.navi:
        want.add('NAVI')
    if args.navm:
        want.add('NAVM')

    count = {s: 0 for s in want}
    for sig, formid, flags, body in _iter_records(data, start, len(data)):
        if sig not in want:
            continue
        if count[sig] >= args.max:
            if all(count[s] >= args.max for s in want):
                break
            continue
        count[sig] += 1
        print(f"\n=== {sig} 0x{formid:08X} flags=0x{flags:08X} "
              f"bodylen={len(body)} ===")
        for ssig, sdata in _iter_subrecords(body):
            print(f"  {ssig} ({len(sdata)}): {_hex(sdata, args.hexlimit)}")
            if ssig == 'NVNM' and args.nvnm_decode:
                try:
                    _decode_nvnm(sdata)
                except Exception as e:
                    print(f"    NVNM decode error: {e}")

    print(f"\nDumped: {count}")


if __name__ == '__main__':
    main()
