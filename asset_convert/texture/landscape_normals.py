"""Landscape normal-map specular masks.

Skyrim's landscape shader reads the normal map's ALPHA channel as the
specular mask.  Oblivion's terrain shader never used it, so most Oblivion
landscape normal maps ship as DXT1 (sampled alpha = 1.0, full specular) and
the rest carry whatever their author's tool left there.  Morrowind ships no
normal map at all, and the TXST still names one.

Two passes fix that:

  run(landscape_dir)        every ``*_n.dds`` in the landscape tree gets a
                            constant dark alpha (32/255), whatever its format.
  ensure_ltex_normals(...)  every land texture an LTEX names gets a flat
                            normal with that same mask when no plugin ships one.

DXT1 and DXT5 share the 8-byte color block format, so color data is reused
verbatim; only DXT1 3-color blocks (c0 <= c1) get their endpoints swapped and
indices remapped.  No recompression loss.

CLI:
    python -m asset_convert.texture.landscape_normals <landscape_dir>
    python -m asset_convert.texture.landscape_normals <export_dir> <textures_root> <output_dir>
"""
from asset_convert.game_paths import (current_namespace, find_nocase,
                                      folder_names, namespace_for,
                                      set_namespace)
import os
import struct
import sys
from pathlib import Path

import numpy as np

from tes5_import.record_types.common import landscape_texture_path

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


def _dxt3_to_dxt5_blocks(block_data, alpha):
    """DXT3 -> DXT5 blocks, replacing the explicit alpha with a constant.

    Both are 16 bytes per block and share the trailing 8-byte COLOR block
    verbatim; only the leading alpha block differs.  Going to DXT5 rather than
    rewriting DXT3's nibbles in place buys an EXACT value -- DXT3 quantises
    alpha to multiples of 17, so 64 would land on 68.
    """
    n_blocks = len(block_data) // 16
    src = np.frombuffer(block_data, dtype=np.uint8).reshape(n_blocks, 16)
    out = np.zeros((n_blocks, 16), dtype=np.uint8)
    out[:, 0] = alpha       # alpha0
    out[:, 1] = alpha       # alpha1; index bytes stay 0 -> every texel alpha0
    out[:, 8:] = src[:, 8:]
    return out.tobytes()


def _dxt5_set_alpha_blocks(block_data, alpha):
    """Rewrite DXT5 blocks to a constant alpha; color untouched."""
    n_blocks = len(block_data) // 16
    src = np.frombuffer(block_data, dtype=np.uint8).reshape(n_blocks, 16)
    out = src.copy()
    out[:, 0] = alpha
    out[:, 1] = alpha
    out[:, 2:8] = 0         # every alpha index -> alpha0
    return out.tobytes()


def set_constant_alpha(path, alpha):
    """Give one normal map a constant specular mask, whatever its format.

    Three different ways a normal map ends up with no usable mask, all of
    which Skyrim reads as a specular instruction:

      DXT1  no alpha channel at all -- sampled as 1.0, FULL specular.
      DXT3  an explicit 4-bit alpha that is written whether the author cared
            or not.  Nehrim's 33 poster/sign normals are exactly this: a
            constant 255, i.e. mirror-glossy signage, with no intent behind it.
      DXT5  a constant or near-constant alpha, same story.

    All three are rewritten as DXT5 carrying `alpha`.  Returns True if the
    file was rewritten.
    """
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'DDS ':
        return False
    fourcc = data[84:88]
    if fourcc not in (b'DXT1', b'DXT3', b'DXT5'):
        return False
    height, width = struct.unpack_from('<II', data, 12)
    mip_count = max(1, struct.unpack_from('<I', data, 28)[0])

    hdr = bytearray(data[:128])
    hdr[84:88] = b'DXT5'
    top_blocks = max(1, (width + 3) // 4) * max(1, (height + 3) // 4)
    struct.pack_into('<I', hdr, 20, top_blocks * 16)

    out = [bytes(hdr)]
    off = 128
    for w, h in _mip_dims(width, height, mip_count):
        nb = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
        n = nb * (8 if fourcc == b'DXT1' else 16)
        chunk = data[off:off + n]
        if len(chunk) < n:
            return False            # truncated file -- leave it alone
        if fourcc == b'DXT1':
            out.append(_dxt1_to_dxt5_blocks(chunk, alpha))
        elif fourcc == b'DXT3':
            out.append(_dxt3_to_dxt5_blocks(chunk, alpha))
        else:
            out.append(_dxt5_set_alpha_blocks(chunk, alpha))
        off += n

    with open(path, 'wb') as f:
        f.write(b''.join(out))
    return True


def _has_constant_alpha(path, alpha) -> bool:
    """Whether a normal map already carries `alpha` as a flat mask."""
    from asset_convert.texture import parallax
    blob = _read_top_mip(path)
    info = None if blob is None else parallax.classify_alpha(blob)
    return (info is not None and getattr(info, 'levels', 0) == 1
            and abs(getattr(info, 'mean', -1) - alpha) < 0.5)


def run(landscape_dir, alpha=SPECULAR_ALPHA):
    """Give every ``*_n.dds`` under landscape_dir the constant landscape mask.

    Every format is rewritten, a real-looking mask included: TES4's terrain
    shader never read the alpha, so whatever sits there (a generated height
    field, in Morroblivion's case) carries no specular intent.
    Returns (checked, fixed) counts.
    See: docs/commentary/asset_convert_texture.md#landscape-normal-maps-dxt1-shiny
    """
    landscape_dir = Path(landscape_dir)
    checked = fixed = 0
    if not landscape_dir.exists():
        return checked, fixed
    for path in sorted(landscape_dir.rglob('*_n.dds')):
        checked += 1
        if not _has_constant_alpha(path, alpha) \
                and set_constant_alpha(path, alpha):
            fixed += 1
    return checked, fixed


def _ltex_texture_rels(rec_dir):
    """Every land texture the plugin's LTEX records name, as shipped paths."""
    ltex_txt = Path(rec_dir) / 'LTEX.txt'
    rels = set()
    if not ltex_txt.is_file():
        return rels
    with open(ltex_txt, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith('ICON='):
                icon = line.strip().split('=', 1)[1].replace('\\\\', '\\')
                rels.add(landscape_texture_path(icon))
    return rels


class _Listings(dict):
    """`folder_names` per folder, read once until the next `clear()`."""

    def __missing__(self, folder):
        names = self[folder] = folder_names(folder)
        return names


def ensure_ltex_normals(rec_dir, textures_root, output_dir,
                        alpha=SPECULAR_ALPHA):
    """Write a flat normal for every land texture that ships none.

    A landscape TXST names ``<diffuse>_n.dds`` whether or not the source game
    had one; Morrowind has no normal maps at all, and a missing landscape
    normal renders at full specular. The file is looked for, ignoring case,
    under every plugin's output tree (a dependent's terrain borrows its
    master's textures) and written under THIS plugin's when absent.
    Returns (checked, written).
    See: docs/commentary/asset_convert_texture.md#landscape-normal-maps-dxt1-shiny
    """
    output_dir = Path(output_dir)
    trees = [p for p in output_dir.iterdir() if p.is_dir()]
    written = 0
    seen = _Listings()
    rels = _ltex_texture_rels(rec_dir)
    for rel in sorted(rels):
        parts = (rel.rsplit('.', 1)[0] + '_n.dds').split('\\')
        if any(find_nocase(tree, 'textures', *parts, listing=seen.__getitem__)
               for tree in trees):
            continue
        dest = Path(textures_root).joinpath(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(_flat_normal_bytes(alpha))
        seen.clear()
        written += 1
    return len(rels), written


# See: docs/commentary/asset_convert_shader.md#default-mask-alpha
DEFAULT_MASK_ALPHA = 64


def default_normal_rel() -> str:
    """What a mesh names when its own normal map does not exist."""
    return 'textures\\' + current_namespace() + '\\default_n.dds'


def _flat_normal_bytes(alpha, size=32) -> bytes:
    """A DXT5 flat normal (128,128,255) with a constant mask, mips included.

    Every 16-byte block is an 8-byte constant-alpha block plus a 4-byte color
    block with c0 == c1 and a zero index word, so each texel resolves to c0.
    See: docs/commentary/asset_convert_texture.md#default-normal-is-dxt5
    """
    # RGB565 for (128,128,255): R=16, G=32, B=31
    c565 = (16 << 11) | (32 << 5) | 31
    color = struct.pack('<HHI', c565, c565, 0)
    alpha_blk = bytes((alpha, alpha, 0, 0, 0, 0, 0, 0))
    block = alpha_blk + color

    dims = []
    w = h = size
    while True:
        dims.append((w, h))
        if w == 1 and h == 1:
            break
        w = max(1, w // 2)
        h = max(1, h // 2)

    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<I', hdr, 4, 124)
    # CAPS | HEIGHT | WIDTH | PIXELFORMAT | MIPMAPCOUNT | LINEARSIZE
    struct.pack_into('<I', hdr, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000)
    struct.pack_into('<I', hdr, 12, size)             # height
    struct.pack_into('<I', hdr, 16, size)             # width
    top_blocks = max(1, size // 4) * max(1, size // 4)
    struct.pack_into('<I', hdr, 20, top_blocks * 16)  # linear size
    struct.pack_into('<I', hdr, 28, len(dims))        # mip count
    struct.pack_into('<I', hdr, 76, 32)               # ddspf size
    struct.pack_into('<I', hdr, 80, 0x4)              # DDPF_FOURCC
    hdr[84:88] = b'DXT5'
    struct.pack_into('<I', hdr, 108, 0x1000 | 0x400000 | 0x8)   # TEXTURE|MIPMAP|COMPLEX

    body = []
    for w, h in dims:
        nb = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
        body.append(block * nb)
    return bytes(hdr) + b''.join(body)


def write_default_normal(textures_root, alpha=None, size=32):
    """Write the shared flat normal (128,128,255) with a constant mask.

    `textures_root` is the TEXTURES root; the namespace segment is appended
    here.  Call AFTER normalize_specular_alpha.
    See: docs/commentary/asset_convert_texture.md#default-normal-is-dxt5
    """
    if alpha is None:
        alpha = DEFAULT_MASK_ALPHA
    dest = Path(textures_root) / current_namespace() / 'default_n.dds'
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(_flat_normal_bytes(alpha, size))
    return dest


def _read_top_mip(path):
    """DDS header plus the FIRST mip only -- all a classifier looks at.

    Reading whole files here meant several GB of I/O across a texture tree
    (5171 normal maps, many of them 2.8 MB) to inspect data that lives
    entirely in the top mip.
    """
    with open(path, 'rb') as f:
        hdr = f.read(128)
        if len(hdr) < 128 or hdr[:4] != b'DDS ':
            return None
        fourcc = hdr[84:88]
        if fourcc not in (b'DXT1', b'DXT3', b'DXT5'):
            return hdr + f.read()      # uncompressed: let the classifier say
        height, width = struct.unpack_from('<II', hdr, 12)
        blocks = max(1, (width + 3) // 4) * max(1, (height + 3) // 4)
        return hdr + f.read(blocks * (8 if fourcc == b'DXT1' else 16))


def normalize_specular_alpha(tex_dir, alpha=DEFAULT_MASK_ALPHA, skip=()):
    """Give every maskless normal map under `tex_dir` a constant mask.

    `spec_mask` decides what counts: a real mask is left ALONE -- authored
    per-texel data is exactly what we want to keep.  Only 'no_alpha', 'flat'
    and 'binary' are rewritten, and 'binary' includes the constant-255 case
    that Skyrim reads as full specular.

    `skip` is a sequence of lowercase path fragments to leave untouched (the
    landscape tree has its own, dimmer, value).

    Returns (checked, fixed, per-verdict Counter).
    """
    from collections import Counter
    from asset_convert.texture import parallax
    from asset_convert.texture import spec_mask

    tex_dir = Path(tex_dir)
    counts = Counter()
    checked = fixed = 0
    if not tex_dir.exists():
        return checked, fixed, counts
    paths = [p for p in sorted(tex_dir.rglob('*_n.dds'))
             if not any(s in str(p).lower() for s in skip)]

    def _classify(path):
        """Read + classify one normal map.  Returns (path, verdict) or None.

        Pure I/O and decode with no shared state, which is what makes this
        safe to fan out.  The WRITE decision is returned rather than made
        here, so the mutation stays in the caller and the counters stay
        deterministic regardless of completion order.
        """
        try:
            blob = _read_top_mip(path)
        except OSError:
            return None
        if blob is None:
            return None
        info = parallax.classify_alpha(blob)
        verdict = spec_mask.verdict(info)
        if verdict == 'mask':
            return (path, 'mask')
        # Our own constant is itself 'binary' (one level), so a second run
        # would rewrite every file it already fixed and report the same count
        # forever -- which would make the log number useless as a health
        # signal.  Recognise the finished state instead.
        if (info is not None and getattr(info, 'levels', 0) == 1
                and abs(getattr(info, 'mean', -1) - alpha) < 0.5):
            return (path, 'already')
        return (path, verdict)

    # Reading and decoding 5k+ DDS files dominated this stage (measured in
    # MINUTES on Oblivion's tree, against ~2s for every other texture sweep).
    # It is I/O plus a decode that releases the GIL, so THREADS are the right
    # tool here -- see docs/commentary/performance.md; processes would pay pickling
    # for every blob.  Results are collected first and applied in sorted path
    # order, so the counters and the set of rewritten files are identical to
    # the serial version no matter how the pool schedules.
    workers = min(32, (os.cpu_count() or 4) * 2)
    results = []
    if len(paths) > 1 and workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = [r for r in pool.map(_classify, paths) if r is not None]
    else:
        results = [r for r in map(_classify, paths) if r is not None]

    checked = len(paths)
    for path, verdict in results:
        if verdict in ('mask', 'already'):
            counts[verdict] += 1
            continue
        counts[verdict] += 1
        if set_constant_alpha(path, alpha):
            fixed += 1
    return checked, fixed, counts


def main(argv):
    """`<landscape_dir>` runs the tree sweep; `<export_dir> <textures_root>
    <output_dir>` writes the missing LTEX normals."""
    if len(argv) == 1:
        checked, fixed = run(argv[0])
        print(f"Landscape normals: {checked} checked, {fixed} given the mask")
        return 0
    if len(argv) == 3:
        set_namespace(namespace_for(argv[0]))
        checked, written = ensure_ltex_normals(*argv)
        print(f"LTEX normals: {checked} land textures, {written} flat "
              f"normals written")
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
