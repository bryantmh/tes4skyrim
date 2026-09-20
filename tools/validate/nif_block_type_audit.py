#!/usr/bin/env python3
"""Audit converted NIFs for load-time CTDs the engine cannot survive.

Two checks, both for defects that PyFFI reads and writes happily and that no
amount of structural validation catches:

1. **Unknown block types.**  NiStream constructs each block by type name.  If
   the engine has no such class the block is never built, and a link to that
   slot hands NiPointer a non-NiObject pointer -- the engine then runs
   `lock cmpxchg` on a "refcount" that lands in read-only .rdata.  Found
   NiUVController this way (SkyrimSE.exe has RTTI for NiUVData but none for
   NiUVController); it crashed the 8 Ghostfence meshes carrying it.

2. **UV-set count.**  The u16 "BS Data Flags" packs the UV-set count in its low
   6 bits, and that count is the ONLY thing telling the engine how many
   TexCoord arrays follow the vertex colors.  A mesh storing 2 sets while
   BSLightingShaderProperty binds 1 leaves the vertex buffer an array short, so
   the copy runs past the allocation and faults on a non-temporal store
   (vmovntdq) at the next page boundary.  Vanilla census: 2,233 shapes carry 0
   or 1 sets and NEVER 2.  Found furnucomutableu05.nif (Seyda Neen Census and
   Excise Office) this way.

Usage:
    python tools/validate/nif_block_type_audit.py output/Morrowind_ob.esm/meshes
    python tools/validate/nif_block_type_audit.py <dir> --max 5000
    python tools/validate/nif_block_type_audit.py <dir> --exe "D:/.../SkyrimSE.exe"

Exit code is 1 when anything unconstructible or over-sized is found.
"""
import argparse
import collections
import os
import random
import re
import struct
import sys

try:
    import pefile
except ImportError:
    sys.exit('pefile required: pip install pefile')


def ru32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def ru16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def parse_header(b):
    """Decode the header far enough to walk the block payloads."""
    nl = b.index(b'\n')
    o = nl + 1
    o += 4 + 1 + 4          # version, endian, user version
    nb = ru32(b, o); o += 4
    o += 4                  # bs stream version
    for _ in range(3):      # author / process script / export script
        ln = b[o]; o += 1 + ln
    nt = ru16(b, o); o += 2
    types = []
    for _ in range(nt):
        ln = ru32(b, o); o += 4
        types.append(b[o:o + ln].decode('latin-1')); o += ln
    tidx = [ru16(b, o + 2 * i) for i in range(nb)]; o += 2 * nb
    sizes = [ru32(b, o + 4 * i) for i in range(nb)]; o += 4 * nb
    ns = ru32(b, o); o += 4
    o += 4                  # max string length
    for _ in range(ns):
        ln = ru32(b, o); o += 4
        o += ln
    ng = ru32(b, o); o += 4
    o += 4 * ng
    return dict(nb=nb, types=types, tidx=tidx, sizes=sizes, hdr_end=o)


def block_types(fp):
    """Return Counter of block type name -> count for one NIF."""
    with open(fp, 'rb') as fh:
        b = fh.read()
    h = parse_header(b)
    out = collections.Counter()
    for t in h['tidx']:
        out[h['types'][t]] += 1
    return out


def uv_set_counts(fp):
    """Yield (block index, class, uv_set_count, num_vertices) per geometry block.

    Only the low 6 bits of BS Data Flags are read -- that is the count the
    engine itself uses to decide how many TexCoord arrays to consume.
    """
    with open(fp, 'rb') as fh:
        raw = fh.read()
    h = parse_header(raw)
    off = h['hdr_end']
    for i in range(h['nb']):
        cls = h['types'][h['tidx'][i]]
        size = h['sizes'][i]
        blk = raw[off:off + size]
        off += size
        if cls not in ('NiTriShapeData', 'NiTriStripsData'):
            continue
        try:
            o = 4                       # Group ID
            nv = ru16(blk, o); o += 2
            o += 2                      # Keep Flags + Compress Flags
            has_verts = blk[o]; o += 1
            if has_verts:
                o += 12 * nv
            bs_flags = ru16(blk, o)
        except Exception:
            continue
        yield i, cls, bs_flags & 0x3F, nv


def bool_key_types(fp):
    """Yield (block index, interpolation, num_keys) per NiBoolData block.

    NiBoolData is a bare KeyGroup<byte>: u32 Num Keys, then (only when Num Keys
    is non-zero) a u32 KeyType, then Num Keys * {float Time, byte Value}.  The
    engine wants CONST_KEY (5) here -- nif.xml calls 5 "Step function.  Used
    for visibility keys in NiBoolData", 3449/3449 vanilla Skyrim and 1296/1296
    Oblivion source blocks store 5, and writing LINEAR (1) crashed the engine
    inside NiBoolData::Load (ctrigtripwire01, Vilverin).
    """
    with open(fp, 'rb') as fh:
        raw = fh.read()
    h = parse_header(raw)
    off = h['hdr_end']
    for i in range(h['nb']):
        cls = h['types'][h['tidx'][i]]
        size = h['sizes'][i]
        blk = raw[off:off + size]
        off += size
        if cls != 'NiBoolData':
            continue
        try:
            nk = ru32(blk, 0)
            if nk == 0:
                continue
            yield i, ru32(blk, 4), nk
        except Exception:
            continue


BLEND_INTERP = ('NiBlendBoolInterpolator', 'NiBlendFloatInterpolator',
                'NiBlendPoint3Interpolator', 'NiBlendTransformInterpolator',
                'NiBlendColorInterpolator')


def blend_interp_flags(fp):
    """Yield (block index, class, flags, array_size) per NiBlend*Interpolator.

    NiBlendInterpolator starts with byte Flags then byte Array Size.  Flags bit
    0 is "Manager Controlled"; when it is CLEAR, nif.xml appends seven more
    fields, so the engine reads 15 bytes instead of 7.  Vanilla writes Flags=1
    and Array Size=2 in 8779/8779 blocks -- anything else makes the engine read
    past the end of the block and AddRef whatever follows (the Vilverin
    `lock inc [rax+0x08]` CTD).
    """
    with open(fp, 'rb') as fh:
        raw = fh.read()
    h = parse_header(raw)
    off = h['hdr_end']
    for i in range(h['nb']):
        cls = h['types'][h['tidx'][i]]
        size = h['sizes'][i]
        blk = raw[off:off + size]
        off += size
        if cls not in BLEND_INTERP or size < 2:
            continue
        yield i, cls, blk[0], blk[1]


def rtti_names(exe):
    """Set of class names with RTTI type descriptors in the executable."""
    pe = pefile.PE(exe, fast_load=True)
    img = pe.get_memory_mapped_image()
    names = set()
    for m in re.finditer(rb'\.\?AV([A-Za-z_][A-Za-z0-9_]*)@@', img):
        names.add(m.group(1).decode('latin-1'))
    return names


def find_exe(explicit):
    if explicit:
        return explicit
    for p in (r'C:\Program Files (x86)\Steam\steamapps\content\app_489830'
              r'\depot_489833\SkyrimSE.1.6.659.unpacked.exe',
              r'C:\Program Files (x86)\Steam\steamapps\common'
              r'\Skyrim Special Edition\SkyrimSE.exe'):
        if os.path.exists(p):
            return p
    sys.exit('SkyrimSE.exe not found; pass --exe')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root', help='directory of converted NIFs (or one .nif)')
    ap.add_argument('--exe', default=None,
                    help='SkyrimSE.exe to read RTTI from (GOG build preferred)')
    ap.add_argument('--max', type=int, default=4000, help='sample size')
    ap.add_argument('--seed', type=int, default=7)
    a = ap.parse_args()

    exe = find_exe(a.exe)
    print('RTTI source: %s' % exe, flush=True)
    known = rtti_names(exe)
    print('  %d RTTI class names' % len(known), flush=True)

    if os.path.isfile(a.root):
        files = [a.root]
    else:
        files = []
        for r, d, f in os.walk(a.root):
            for x in f:
                if x.lower().endswith('.nif'):
                    files.append(os.path.join(r, x))
        random.seed(a.seed)
        random.shuffle(files)
        files = files[:a.max]
    print('scanning %d nifs under %s' % (len(files), a.root), flush=True)

    used = collections.Counter()
    owners = collections.defaultdict(list)
    uv_hist = collections.Counter()
    uv_bad = []
    key_hist = collections.Counter()
    key_bad = []
    blend_hist = collections.Counter()
    blend_bad = []
    for i, fp in enumerate(files):
        if i and i % 1000 == 0:
            print('  ...%d' % i, flush=True)
        try:
            t = block_types(fp)
        except Exception:
            continue
        used.update(t)
        for k in t:
            if k not in known and len(owners[k]) < 6:
                owners[k].append(fp)
        try:
            for bi, cls, n_uv, nv in uv_set_counts(fp):
                uv_hist[n_uv] += 1
                if n_uv > 1:
                    uv_bad.append((fp, bi, cls, n_uv, nv))
        except Exception:
            pass
        try:
            for bi, ktype, nk in bool_key_types(fp):
                key_hist[ktype] += 1
                if ktype != 5:
                    key_bad.append((fp, bi, ktype, nk))
        except Exception:
            pass
        try:
            for bi, cls, bflags, asize in blend_interp_flags(fp):
                blend_hist[(bflags, asize)] += 1
                if not bflags & 1:
                    blend_bad.append((fp, bi, cls, bflags, asize))
        except Exception:
            pass

    rc = 0

    missing = {k: v for k, v in used.items() if k not in known}
    print('\n%d distinct block types emitted; %d have NO RTTI in the exe'
          % (len(used), len(missing)), flush=True)
    if missing:
        rc = 1
        for k, v in sorted(missing.items(), key=lambda kv: -kv[1]):
            print('\n  *** %s  (%d blocks) - engine cannot construct this'
                  % (k, v), flush=True)
            for o in owners[k]:
                print('        %s' % o, flush=True)
    else:
        print('OK - every emitted block type is constructible by the engine.',
              flush=True)

    print('\nUV-set counts: %s'
          % dict(sorted(uv_hist.items())), flush=True)
    if uv_bad:
        rc = 1
        print('  *** %d geometry blocks declare >1 UV set - Skyrim reads one,'
              ' so the vertex buffer overruns (CTD)' % len(uv_bad), flush=True)
        seen = set()
        for fp, bi, cls, n_uv, nv in uv_bad:
            if fp in seen:
                continue
            seen.add(fp)
            print('        %s  block[%d] %s uv_sets=%d nv=%d'
                  % (fp, bi, cls, n_uv, nv), flush=True)
    else:
        print('OK - no geometry block declares more than one UV set.',
              flush=True)

    print('\nNiBoolData key types: %s'
          % dict(sorted(key_hist.items())), flush=True)
    if key_bad:
        rc = 1
        print('  *** %d NiBoolData blocks are not CONST_KEY (5) - visibility'
              ' keys must be a step function, and the engine crashes in'
              ' NiBoolData::Load otherwise' % len(key_bad), flush=True)
        seen = set()
        for fp, bi, ktype, nk in key_bad:
            if fp in seen:
                continue
            seen.add(fp)
            print('        %s  block[%d] interpolation=%d num_keys=%d'
                  % (fp, bi, ktype, nk), flush=True)
    else:
        print('OK - every NiBoolData uses CONST_KEY step interpolation.',
              flush=True)

    print('\nNiBlend*Interpolator (flags, array_size): %s'
          % dict(sorted(blend_hist.items())), flush=True)
    if blend_bad:
        rc = 1
        print('  *** %d blend interpolators have Manager Controlled CLEAR -'
              ' the engine then reads 15 bytes from a 7-byte block and'
              ' AddRefs past the end (CTD)' % len(blend_bad), flush=True)
        seen = set()
        for fp, bi, cls, bflags, asize in blend_bad:
            if fp in seen:
                continue
            seen.add(fp)
            print('        %s  block[%d] %s flags=%d array_size=%d'
                  % (fp, bi, cls, bflags, asize), flush=True)
    else:
        print('OK - every blend interpolator is manager-controlled.',
              flush=True)

    return rc


if __name__ == '__main__':
    sys.exit(main())
