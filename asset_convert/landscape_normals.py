"""Landscape normal-map specular fix: DXT1 → DXT5 with a dark alpha channel.

Skyrim's landscape shader reads the normal map's ALPHA channel as the
specular mask.  Oblivion's terrain shader never used it, so most Oblivion
landscape normal maps ship as DXT1 (no alpha channel).  DXT1 samples
alpha = 1.0 everywhere, which Skyrim treats as a full-strength specular
mask — the entire terrain turns glossy/shiny.  (Oblivion normal maps that
are already DXT5 carry a real specular mask in alpha and are left alone.)

Fix: re-container each DXT1 landscape ``*_n.dds`` as DXT5 with a constant
dark alpha (32/255 ≈ Oblivion-typical low specular).  DXT1 and DXT5 share
the same 8-byte color block format, so color data is reused verbatim; only
blocks in DXT1's 3-color mode (c0 <= c1, which DXT5 would misdecode as a
4-color block) get their endpoints swapped and indices remapped.  No
recompression loss.

CLI:
    python -m asset_convert.landscape_normals <textures_dir>
    # e.g. python -m asset_convert.landscape_normals \
    #          output/Oblivion.esm/textures/tes4/landscape
"""
import struct
import sys
from pathlib import Path

import numpy as np

# Specular mask value written into the new alpha channel.  Oblivion DXT5
# landscape normals average ~77/255; DXT1 sources were authored with no
# specular intent at all, so use a dimmer mask.
SPECULAR_ALPHA = 32


def _mip_dims(w, h, count):
    dims = []
    for _ in range(count):
        dims.append((max(1, w), max(1, h)))
        w //= 2
        h //= 2
    return dims


def _dxt1_to_dxt5_blocks(color_data, alpha):
    """Convert raw DXT1 block data to DXT5 block data with constant alpha."""
    n_blocks = len(color_data) // 8
    blocks = np.frombuffer(color_data, dtype='<u2').reshape(n_blocks, 4).copy()
    c0 = blocks[:, 0].copy()
    c1 = blocks[:, 1].copy()
    idx = blocks[:, 2:4].copy().view('<u4').reshape(n_blocks)

    # DXT1 3-color blocks (c0 <= c1) would be misread in DXT5's always-
    # 4-color mode: swap endpoints and flip indices 0<->1 (2/3 stay; the
    # midpoint/transparent entries land on the nearest 1/3-2/3 mix).
    three = c0 <= c1
    if three.any():
        blocks[three, 0] = c1[three]
        blocks[three, 1] = c0[three]
        i = idx[three]
        idx[three] = i ^ (~(i >> 1) & np.uint32(0x55555555))
        blocks[:, 2:4] = idx.view('<u2').reshape(n_blocks, 2)

    out = np.zeros((n_blocks, 16), dtype=np.uint8)
    out[:, 0] = alpha  # alpha0
    out[:, 1] = alpha  # alpha1; index bytes stay 0 -> all texels alpha0
    out[:, 8:] = blocks.view(np.uint8).reshape(n_blocks, 8)
    return out.tobytes()


def fix_normal_specular(path, alpha=SPECULAR_ALPHA):
    """Convert one DXT1 DDS to DXT5 with constant alpha.  Returns True if
    the file was rewritten (False = not DXT1, left untouched)."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'DDS ' or data[84:88] != b'DXT1':
        return False
    height, width = struct.unpack_from('<II', data, 12)
    mip_count = max(1, struct.unpack_from('<I', data, 28)[0])

    hdr = bytearray(data[:128])
    hdr[84:88] = b'DXT5'
    # dwPitchOrLinearSize: top-level mip byte size (16 bytes/block for DXT5)
    top_blocks = max(1, (width + 3) // 4) * max(1, (height + 3) // 4)
    struct.pack_into('<I', hdr, 20, top_blocks * 16)

    out = [bytes(hdr)]
    off = 128
    for w, h in _mip_dims(width, height, mip_count):
        n = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
        out.append(_dxt1_to_dxt5_blocks(data[off:off + n], alpha))
        off += n

    with open(path, 'wb') as f:
        f.write(b''.join(out))
    return True


def run(landscape_dir):
    """Fix every DXT1 ``*_n.dds`` under landscape_dir (recursive).
    Returns (checked, fixed) counts."""
    landscape_dir = Path(landscape_dir)
    checked = fixed = 0
    if not landscape_dir.exists():
        return checked, fixed
    for path in sorted(landscape_dir.rglob('*_n.dds')):
        checked += 1
        if fix_normal_specular(path):
            fixed += 1
    return checked, fixed


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 1
    checked, fixed = run(argv[0])
    print(f"Landscape normals: {checked} checked, {fixed} DXT1->DXT5 fixed")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
