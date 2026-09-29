"""DXT1 / BC4 / BC5 block encoding and the DDS headers around it.

Split out of terrain_lod.py.  Nothing here knows about terrain: every entry
point takes a pixel array and returns bytes, which is why the encoder tests
import it as a unit.

The encoders are vectorised over 4x4 blocks with numpy rather than looped in
Python -- the terrain LOD bake writes thousands of textures per worldspace.
"""

import struct
from pathlib import Path

import numpy as np
from PIL import Image

#: Fallback tile texture side in pixels; terrain_lod picks per LOD level.
TEX_SIZE = 512


# ---------------------------------------------------------------------------

def write_dds_dxt1(colors_rgb: np.ndarray, path: Path, size: int = TEX_SIZE):
    """Write a DXT1 DDS with a full mipmap chain from an RGB ndarray.

    See: docs/commentary/asset_convert_terrain.md#one-dds-header-builder
    """
    img = Image.fromarray(colors_rgb, 'RGB')
    img = img.resize((size, size), Image.LANCZOS)

    mip_levels = []
    mip_img = img
    while True:
        mip_arr = np.array(mip_img)
        mip_levels.append(encode_dxt1_quality(mip_arr))
        mip_w, mip_h = mip_img.size
        if mip_w == 1 and mip_h == 1:
            break
        mip_img = mip_img.resize((max(1, mip_w // 2), max(1, mip_h // 2)), Image.LANCZOS)

    mip_count = len(mip_levels)
    all_data = b''.join(mip_levels)
    hdr = make_dds_header_dxt1(size, size, len(mip_levels[0]), mip_count=mip_count)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(hdr + all_data)


def _mip_count(size):
    """How many mip levels a square texture of `size` has, down to 1x1."""
    n, s = 0, size
    while s >= 1:
        n += 1
        if s == 1:
            break
        s //= 2
    return n


def _bc_linear_size(size, bytes_per_block):
    """Byte size of the TOP mip: one block per 4x4 pixels."""
    blocks = max(1, size // 4) * max(1, size // 4)
    return blocks * bytes_per_block


#: DDSD_* header flags: CAPS, HEIGHT, WIDTH, PIXELFORMAT, LINEARSIZE.
_DDSD_BASE = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
#: DDSD_MIPMAPCOUNT, then DDSCAPS_TEXTURE and the MIPMAP|COMPLEX pair.
_DDSD_MIPMAPCOUNT = 0x20000
_DDSCAPS_TEXTURE = 0x1000
_DDSCAPS_MIPPED = 0x400000 | 0x8
#: DDPF_FOURCC: the pixel format names a compressed codec.
_DDPF_FOURCC = 0x4


def dds_header(size, linear_size, fourcc, mip_count=1):
    """The 128-byte DDS header for a compressed square texture.

    Layout, in order: magic, dwSize, dwFlags, dwHeight, dwWidth,
    dwPitchOrLinearSize (the TOP mip), dwDepth, dwMipMapCount,
    dwReserved1[11], then the 32-byte pixel format (size, flags, FourCC and
    five unused masks), dwCaps and four trailing reserved words.
    """
    flags = _DDSD_BASE
    caps = _DDSCAPS_TEXTURE
    if mip_count > 1:
        flags |= _DDSD_MIPMAPCOUNT
        caps |= _DDSCAPS_MIPPED
    hdr = b'DDS ' + struct.pack(
        '<7I', 124, flags, size, size, linear_size, 0, mip_count)
    hdr += b'\x00' * 44
    hdr += struct.pack('<II', 32, _DDPF_FOURCC) + fourcc
    hdr += struct.pack('<5I', 0, 0, 0, 0, 0)
    hdr += struct.pack('<5I', caps, 0, 0, 0, 0)
    assert len(hdr) == 128
    return hdr


def make_dds_header_dxt1(w, h, linear_size, mip_count=1):
    """The DDS header for a square DXT1 texture; `w` and `h` must match."""
    assert w == h, 'terrain LOD textures are square'
    return dds_header(h, linear_size, b'DXT1', mip_count)


def blocks_4x4(a: np.ndarray) -> np.ndarray:
    """Reshape a padded (ph, pw[, ch]) image into (n_blocks, 16[, ch]) in the
    row-major block order DXT/BC formats store (block row 0 left-to-right first).
    """
    ph, pw = a.shape[:2]
    tail = a.shape[2:]
    return (a.reshape(ph // 4, 4, pw // 4, 4, *tail)
             .transpose(0, 2, 1, 3, *range(4, 4 + len(tail)))
             .reshape(-1, 16, *tail))


def _nearest_palette_codes(blocks, palette):
    """The nearest palette index for every pixel, as (N,16) uint8.

    Computed in CHUNKS: the whole-array form transiently needs ~80 MB for a
    1024^2 tile, which is fatal once one worker per core peaks together.
    See: docs/commentary/asset_convert_terrain.md#dxt1-chunking-is-a-memory-fix
    """
    codes = np.empty((len(blocks), 16), dtype=np.uint8)
    step = 4096
    for s in range(0, len(blocks), step):
        e = min(s + step, len(blocks))
        d = blocks[s:e, :, None, :] - palette[s:e, None, :, :]
        np.multiply(d, d, out=d)
        codes[s:e] = d.sum(axis=3, dtype=np.int32).argmin(axis=2)
    return codes


def encode_dxt1_quality(img: np.ndarray) -> bytes:
    """DXT1 encode an RGB image, vectorised over its 4x4 blocks.

    Endpoints are each block's per-channel min and max; the palette is
    re-expanded FROM 565 so the output stays byte-identical to the scalar
    version this replaced.
    See: docs/commentary/asset_convert_terrain.md#dxt1-is-vectorised-over-blocks
    """
    h, w = img.shape[:2]
    ph = (h + 3) & ~3
    pw = (w + 3) & ~3
    padded = np.zeros((ph, pw, 3), dtype=np.uint8)
    padded[:h, :w] = img

    blocks = blocks_4x4(padded).astype(np.int32)
    c0 = rgb_to_565_vec(blocks.max(axis=1))
    c1 = rgb_to_565_vec(blocks.min(axis=1))

    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    eq = c0 == c1
    c1 = np.where(eq & (c0 != 0), c0 - 1, c1)
    c0 = np.where(eq & (c0 == 0), 1, c0)

    p0 = c565_to_rgb_vec(c0)
    p1 = c565_to_rgb_vec(c1)
    palette = np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], axis=1)
    codes = _nearest_palette_codes(blocks, palette)

    shifts = (np.arange(16, dtype=np.uint32) * 2)
    packed = (codes.astype(np.uint32) << shifts).sum(axis=1, dtype=np.uint32)

    out = np.empty(len(blocks),
                   dtype=np.dtype([('c0', '<u2'), ('c1', '<u2'), ('p', '<u4')]))
    out['c0'] = c0
    out['c1'] = c1
    out['p'] = packed
    return out.tobytes()


def rgb_to_565_vec(rgb: np.ndarray) -> np.ndarray:
    """Vectorised rgb_to_565 over an (N,3) int array."""
    r = rgb[:, 0].astype(np.int32)
    g = rgb[:, 1].astype(np.int32)
    b = rgb[:, 2].astype(np.int32)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def c565_to_rgb_vec(c565: np.ndarray) -> np.ndarray:
    """Vectorised c565_to_rgb → (N,3) int32."""
    r = (c565 >> 11) & 0x1F
    g = (c565 >> 5) & 0x3F
    b = c565 & 0x1F
    return np.stack([(r << 3) | (r >> 2),
                     (g << 2) | (g >> 4),
                     (b << 3) | (b >> 2)], axis=-1).astype(np.int32)


def rgb_to_565(rgb):
    """Pack one RGB triple into a 16-bit 565 value."""
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def c565_to_rgb(c565):
    """Expand one 16-bit 565 value back to an RGB triple."""
    r = (c565 >> 11) & 0x1F
    g = (c565 >> 5)  & 0x3F
    b =  c565        & 0x1F
    return np.array([(r << 3) | (r >> 2),
                     (g << 2) | (g >> 4),
                     (b << 3) | (b >> 2)], dtype=np.uint8)


def encode_bc5_flat_block() -> bytes:
    """One 16-byte BC5 block encoding a flat normal (X=128, Y=128)."""
    #: Two BC4 channels of 8 bytes: endpoints 128, then six zero index bytes.
    channel = struct.pack('BB', 128, 128) + b'\x00' * 6
    return channel + channel


def make_flat_bc5_dds(size: int) -> bytes:
    """A BC5 DDS whose every block encodes the flat normal, with mipmaps."""
    mip_count = _mip_count(size)
    hdr = make_bc5_dds_header(size, mip_count)

    flat_block = encode_bc5_flat_block()
    pixel_data = bytearray()
    s = size
    while s >= 1:
        n_blocks = max(1, s // 4) * max(1, s // 4)
        pixel_data += flat_block * n_blocks
        if s == 1:
            break
        s //= 2

    return bytes(hdr) + bytes(pixel_data)


def make_bc5_dds_header(size: int, mip_count: int) -> bytes:
    """The DDS header for a square BC5 texture (ATI2 FourCC)."""
    return dds_header(size, _bc_linear_size(size, 16), b'ATI2', mip_count)


def encode_bc4_channel(chan: np.ndarray) -> np.ndarray:
    """Encode a whole padded single-channel (ph,pw) uint8 image as BC4.

    Returns an (n_blocks, 8) uint8 array — 8 bytes per 4×4 block, in row-major
    block order.  Vectorised over blocks; byte-identical to encoding each block
    separately (same 8-value interpolation mode, same endpoint and index rules).
    """
    blocks = blocks_4x4(chan).astype(np.int32)
    r0 = blocks.max(axis=1)
    r1 = blocks.min(axis=1)
    flat = r0 == r1

    i = np.arange(1, 7)
    palette = np.empty((len(blocks), 8), np.int32)
    palette[:, 0] = r0
    palette[:, 1] = r1
    palette[:, 2:] = ((7 - i)[None, :] * r0[:, None]
                      + i[None, :] * r1[:, None]) // 7

    idx = np.abs(blocks[:, :, None] - palette[:, None, :]).argmin(axis=2)
    idx = idx.astype(np.uint64)
    idx[flat] = 0

    bits = (idx << (np.arange(16, dtype=np.uint64) * 3)).sum(axis=1,
                                                             dtype=np.uint64)
    out = np.empty((len(blocks), 8), np.uint8)
    out[:, 0] = r0
    out[:, 1] = r1
    for k in range(6):
        out[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(0xFF)).astype(np.uint8)
    return out


def write_normal_dds(normal_rgb: np.ndarray, path: Path):
    """Write a terrain-LOD normal map as full-RGB DXT1, with mips.

    The landscape LOD shader reads all three channels (up is G, see
    terrain_lod._heightmap_normal_rgb); BC5 kept only R and G and threw the
    third axis away.  Vanilla ships DXT5 with a constant opaque alpha, which
    carries the same colour block as DXT1.
    """
    write_dds_dxt1(normal_rgb, path, size=normal_rgb.shape[0])

