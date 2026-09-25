"""L8 (DDPF_LUMINANCE) → uncompressed BGRA: the RED GLOW MAP fix.

Oblivion ships its glow maps as 8-bit LUMINANCE DDS files.  Measured over
Oblivion's whole texture tree: **469 files are DDPF_LUMINANCE, and every
single one of them is a ``_g`` glow map** — no other suffix uses the format,
and no glow map uses any other format.  Their pixel format is::

    pf flags  = 0x20000  (DDPF_LUMINANCE)
    bitcount  = 8
    masks     = R 0xFF, G 0x00, B 0x00, A 0x00

Oblivion's shader samples that single channel and replicates it across RGB.
Skyrim's glow shader (``skyrim_shader_type`` 2, slot 2) samples slot 2 as an
ordinary RGB texture and does NO such replication, so the luminance arrives in
RED with green and blue reading zero — **the glow renders pure red.**  That is
the "candles glow red" report: ``clutter\\candle_g.dds`` is L8, so
``uppersilverplatecandles01.nif`` lights its wax red instead of the warm cream
its authored emissive (0.953, 0.910, 0.678) specifies.

Vanilla Skyrim never ships L8.  Its own glow maps are ordinary RGB textures
whose CONTENT happens to be grey — ``spriggan_g.dds`` is DXT1 with
R == G == B == 17.2 mean.  So the fix is to give Skyrim the format it expects
while preserving the authored greyscale exactly: replicate L into R, G and B.

Output is uncompressed BGRA8 rather than DXT1.  These are small files (the
whole set is a few MB), and re-compressing a mask that the shader multiplies
against emissive would add block artifacts to the one channel that matters for
no meaningful size win.  Uncompressed is also lossless and needs no DXT
encoder dependency.

Runs AFTER the texture copy, exactly like ``landscape_normals.run`` — so a
re-copy cannot resurrect the L8 originals, and re-running is a no-op because a
converted file is no longer DDPF_LUMINANCE.

CLI:
    python -m asset_convert.texture.luminance_textures <textures_dir>
"""

import os
import struct
import sys
from pathlib import Path

try:
    import numpy as _np
except ImportError:                                  # pragma: no cover
    _np = None

# Fallback expansion table: L -> BGRA bytes.  Only used when numpy is absent.
_BGRA_FROM_L = [bytes((i, i, i, 0xFF)) for i in range(256)]

# DDS pixel-format flags.
DDPF_ALPHAPIXELS = 0x1
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDPF_LUMINANCE = 0x20000

# DDS header flags.
DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PITCH = 0x8
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000

DDSCAPS_COMPLEX = 0x8
DDSCAPS_TEXTURE = 0x1000
DDSCAPS_MIPMAP = 0x400000


def is_luminance(path):
    """Is this DDS an 8-bit DDPF_LUMINANCE file (the format Skyrim can't read)?

    Reads only the 128-byte header.  Returns False for anything unreadable, so
    a malformed file is left alone rather than mangled.
    """
    try:
        with open(path, 'rb') as fh:
            hdr = fh.read(128)
    except OSError:
        return False
    if len(hdr) < 128 or hdr[0:4] != b'DDS ':
        return False
    pf_flags = struct.unpack('<I', hdr[80:84])[0]
    bitcount = struct.unpack('<I', hdr[88:92])[0]
    return bool(pf_flags & DDPF_LUMINANCE) and bitcount == 8


def _mip_dims(w, h, count):
    """Every mip level's dimensions, largest first."""
    dims = []
    for _ in range(max(1, count)):
        dims.append((w, h))
        if w == 1 and h == 1:
            break
        w = max(1, w // 2)
        h = max(1, h // 2)
    return dims


def convert_file(path):
    """Rewrite one L8 DDS in place as uncompressed BGRA8.  True if converted.

    L is replicated into R, G and B and alpha is set opaque, which is what
    Oblivion's own sampler did with the single channel.  Every mip level is
    converted, so the texture stays complete at distance.  The bytes are
    overwritten in the SAME file, never swapped in via a rename, so every
    hard link to it (a deployed game copy) receives the fix.
    """
    path = str(path)
    if not is_luminance(path):
        return False
    with open(path, 'rb') as fh:
        blob = fh.read()
    if len(blob) < 128:
        return False

    hdr = bytearray(blob[:128])
    height, width = struct.unpack('<II', hdr[12:20])
    mips = struct.unpack('<I', hdr[28:32])[0]
    dims = _mip_dims(width, height, mips)

    body = blob[128:]
    expected = sum(w * h for w, h in dims)
    if len(body) < expected:
        # Truncated or a layout we did not predict -- never guess at pixels.
        return False

    # Expand L -> BGRA for every mip at once: the levels are contiguous and
    # the transform is per byte, so there is nothing to do level by level.
    # BGRA byte order, which is what the RGB masks below declare.
    #
    # A per-byte Python loop here cost ~100s over Oblivion's 469 glow maps.
    # numpy does the same work in ~0.1ms per file (measured 120x on one
    # 128x128 body, byte-identical output); the table-join fallback is 5x and
    # keeps the module importable where numpy is missing.
    pixels = body[:expected]
    if _np is not None:
        arr = _np.frombuffer(pixels, dtype=_np.uint8)
        rgba = _np.empty((arr.size, 4), dtype=_np.uint8)
        rgba[:, 0] = arr        # B
        rgba[:, 1] = arr        # G
        rgba[:, 2] = arr        # R
        rgba[:, 3] = 0xFF       # A
        out = rgba.tobytes()
    else:
        out = b''.join(map(_BGRA_FROM_L.__getitem__, pixels))

    # Rebuild the pixel format as uncompressed 32-bit BGRA.
    struct.pack_into('<I', hdr, 8,
                     DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT
                     | DDSD_PITCH | (DDSD_MIPMAPCOUNT if len(dims) > 1 else 0))
    struct.pack_into('<I', hdr, 20, width * 4)          # pitch, not linear size
    struct.pack_into('<I', hdr, 28, len(dims))
    struct.pack_into('<I', hdr, 76, 32)                 # ddspf size
    struct.pack_into('<I', hdr, 80, DDPF_RGB | DDPF_ALPHAPIXELS)
    hdr[84:88] = b'\0\0\0\0'                            # no FourCC
    struct.pack_into('<I', hdr, 88, 32)                 # bit count
    struct.pack_into('<I', hdr, 92, 0x00FF0000)         # R mask
    struct.pack_into('<I', hdr, 96, 0x0000FF00)         # G mask
    struct.pack_into('<I', hdr, 100, 0x000000FF)        # B mask
    struct.pack_into('<I', hdr, 104, 0xFF000000)        # A mask
    caps = DDSCAPS_TEXTURE | (
        (DDSCAPS_COMPLEX | DDSCAPS_MIPMAP) if len(dims) > 1 else 0)
    struct.pack_into('<I', hdr, 108, caps)

    with open(path, 'wb') as fh:
        fh.write(bytes(hdr))
        fh.write(bytes(out))
    return True


def run(textures_dir):
    """Convert every L8 DDS under a texture tree.  Returns (checked, fixed).

    Not restricted to ``_g``: the format is the defect, wherever it appears.
    In Oblivion the two sets happen to coincide exactly (469/469), but keying
    on the format means a plugin that ships an L8 diffuse is handled too.
    """
    checked = fixed = 0
    root = Path(textures_dir)
    if not root.is_dir():
        return 0, 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith('.dds'):
                continue
            path = os.path.join(dirpath, name)
            if not is_luminance(path):
                continue
            checked += 1
            if convert_file(path):
                fixed += 1
    return checked, fixed


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 1
    checked, fixed = run(argv[0])
    print(f"Luminance textures: {checked} L8 found, {fixed} converted to BGRA")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
