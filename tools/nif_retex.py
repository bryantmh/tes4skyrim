"""Raw-byte texture path swapper for Skyrim NIFs (BSEffectShaderProperty /
BSShaderTextureSet).  Never round-trips through PyFFI, so it is safe on files
PyFFI cannot fully parse (vanilla NiPSysData atlas arrays, our hand-rolled
output).  Rewrites SizedStrings in place and patches the header block_size
table when lengths change.

Usage:
    python tools/nif_retex.py <in.nif> <out.nif> --map OLD=NEW [--map ...]
        OLD is a case-insensitive substring of the existing texture path;
        NEW is the full replacement path.
    python tools/nif_retex.py <in.nif> --list      # just print texture paths
"""
import argparse
import struct
import sys
import time

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

sys.path.insert(0, '.')
import asset_convert.pyffi_monkey_patch  # noqa: F401
from pyffi.formats.nif import NifFormat


def load_layout(path):
    d = NifFormat.Data()
    with open(path, 'rb') as f:
        d.inspect(f)
    hdr = d.header
    bt = [b.decode('latin1') for b in hdr.block_types]
    bti = list(hdr.block_type_index)
    bsz = list(hdr.block_size)
    raw = bytearray(open(path, 'rb').read())
    first = hdr.get_size(data=d)
    offs = []
    pos = first
    for i in range(hdr.num_blocks):
        offs.append(pos)
        pos += bsz[i]
    types = [bt[bti[i]] for i in range(hdr.num_blocks)]
    # locate the block_size u32 array inside the header by byte-search
    needle = struct.pack('<%dI' % hdr.num_blocks, *bsz)
    tbl = raw.find(needle, 0, first)
    if tbl < 0:
        raise RuntimeError('block_size table not found in header')
    return raw, types, offs, bsz, tbl, first


def shader_string_spans(raw, types, offs, bsz):
    """Yield (block_idx, str_off, str_len) for source + greyscale texture
    SizedStrings in every BSEffectShaderProperty block."""
    for i, t in enumerate(types):
        if t != 'BSEffectShaderProperty':
            continue
        o = offs[i]
        nextra, = struct.unpack_from('<I', raw, o + 4)
        q = o + 12 + 4 * nextra + 8 + 16  # flags + uv offset/scale
        slen, = struct.unpack_from('<I', raw, q)
        yield i, q, slen
        q2 = q + 4 + slen + 4 + 16 + 16 + 8  # clamp + falloff + color4 + mult/soft
        glen, = struct.unpack_from('<I', raw, q2)
        yield i, q2, glen
        # BSShaderTextureSet strings (diffuse etc.) are inside their own block
    for i, t in enumerate(types):
        if t != 'BSShaderTextureSet':
            continue
        o = offs[i]
        n, = struct.unpack_from('<I', raw, o)
        q = o + 4
        for _ in range(n):
            slen, = struct.unpack_from('<I', raw, q)
            yield i, q, slen
            q += 4 + slen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst', nargs='?')
    ap.add_argument('--map', action='append', default=[],
                    help='OLDSUBSTR=NEWPATH (OLD matched case-insensitively)')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    raw, types, offs, bsz, tbl, first = load_layout(args.src)

    spans = list(shader_string_spans(raw, types, offs, bsz))
    if args.list or not args.map:
        for i, off, slen in spans:
            print(f'block[{i}] {types[i]}: {bytes(raw[off + 4:off + 4 + slen])!r}')
        return

    maps = []
    for m in args.map:
        old, _, new = m.partition('=')
        maps.append((old.lower(), new.encode('latin1')))

    # apply replacements back-to-front so earlier offsets stay valid
    edits = []
    for i, off, slen in spans:
        cur = bytes(raw[off + 4:off + 4 + slen])
        for old, new in maps:
            if old in cur.decode('latin1', 'ignore').lower():
                edits.append((i, off, slen, new))
                break
    for i, off, slen, new in sorted(edits, key=lambda e: -e[1]):
        raw[off:off + 4 + slen] = struct.pack('<I', len(new)) + new
        bsz[i] += len(new) - slen
        print(f'block[{i}] {types[i]}: -> {new!r} (size {bsz[i]})')

    # rewrite the block_size table
    raw[tbl:tbl + 4 * len(bsz)] = struct.pack('<%dI' % len(bsz), *bsz)

    with open(args.dst, 'wb') as f:
        f.write(raw)
    print(f'wrote {args.dst} ({len(edits)} paths replaced)')


if __name__ == '__main__':
    main()
