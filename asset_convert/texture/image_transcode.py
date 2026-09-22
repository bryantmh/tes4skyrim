"""Transcode shipped .tga/.bmp textures into the .dds the meshes name.

See: docs/commentary/asset_convert_texture.md#loose-tgabmp-textures
"""

import os
from pathlib import Path

import numpy as np
from PIL import Image

from asset_convert.nif.tex_paths import IMAGE_EXTS
from asset_convert.texture.dds_codec import (dds_header, encode_bc4_channel,
                                             encode_dxt1_quality)

#: Alpha below this counts as authored transparency, so the DDS needs DXT5.
OPAQUE_ALPHA = 250


def _pad_to_blocks(arr):
    """An (h,w,c) image padded up to a multiple of 4 in both axes."""
    h, w = arr.shape[:2]
    ph, pw = (h + 3) & ~3, (w + 3) & ~3
    if (ph, pw) == (h, w):
        return arr
    out = np.zeros((ph, pw, arr.shape[2]), np.uint8)
    out[:h, :w] = arr
    return out


def _encode_mip(rgba, with_alpha):
    """One mip level as DXT1 or DXT5 block bytes.

    A DXT5 block is the BC4 alpha block followed by an unmodified DXT1 color
    block, so both halves come from the encoders dds_codec already has.
    """
    padded = _pad_to_blocks(rgba)
    color = encode_dxt1_quality(padded[:, :, :3])
    if not with_alpha:
        return color
    alpha = encode_bc4_channel(padded[:, :, 3])
    color = np.frombuffer(color, np.uint8).reshape(-1, 8)
    return np.concatenate([alpha, color], axis=1).tobytes()


def _rect_header(w, h, linear_size, mip_count, fourcc):
    """`dds_header` with dwWidth, the fourth word after the magic, patched."""
    hdr = bytearray(dds_header(h, linear_size, fourcc, mip_count))
    hdr[16:20] = int(w).to_bytes(4, 'little')
    return bytes(hdr)


def encode_dds(img: Image.Image) -> bytes:
    """DXT1/DXT5 DDS bytes with a full mip chain, from any PIL image.

    DXT5 is chosen only when the alpha channel carries real transparency; an
    opaque or alpha-less source halves in size as DXT1.
    """
    rgba = img.convert('RGBA')
    with_alpha = bool(
        (np.asarray(rgba, dtype=np.uint8)[:, :, 3] < OPAQUE_ALPHA).any())

    mips = []
    w, h = rgba.size
    cur = rgba
    while True:
        mips.append(_encode_mip(np.asarray(cur, dtype=np.uint8), with_alpha))
        if cur.size == (1, 1):
            break
        cur = cur.resize((max(1, cur.size[0] // 2), max(1, cur.size[1] // 2)),
                         Image.LANCZOS)

    fourcc = b'DXT5' if with_alpha else b'DXT1'
    return _rect_header(w, h, len(mips[0]), len(mips), fourcc) + b''.join(mips)


def _transcode_one(src: Path, dst: Path) -> bool:
    """Encode `src` to the DDS `dst`, reporting whether it was written."""
    try:
        with Image.open(src) as img:
            data = encode_dds(img)
    except Exception as exc:
        print(f"    {src.name}: {exc}")
        return False
    dst.write_bytes(data)
    return True


def run(tex_root):
    """Write a .dds beside every loose .tga/.bmp under `tex_root` lacking one.

    Returns (found, written, failed).  Re-running is a no-op: a source whose
    .dds twin exists is skipped, so this cannot clobber a DDS the plugin
    shipped itself or an earlier repair pass rewrote.
    """
    found = written = failed = 0
    for root, _dirs, files in os.walk(tex_root):
        twins = {f.lower() for f in files}
        for fname in files:
            stem, dot, ext = fname.rpartition('.')
            if not dot or ext.lower() not in IMAGE_EXTS:
                continue
            found += 1
            if (stem + '.dds').lower() in twins:
                continue
            if _transcode_one(Path(root) / fname,
                              Path(root) / (stem + '.dds')):
                written += 1
            else:
                failed += 1
    return found, written, failed
