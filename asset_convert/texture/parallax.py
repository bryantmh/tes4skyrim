"""Carry Oblivion's parallax across to Skyrim.

Oblivion switches parallax on per shape with
``NiTexturingProperty.apply_mode == APPLY_HILIGHT2 (4)`` and keeps the height
field in the DIFFUSE's ALPHA channel.  Skyrim wants it as a separate greyscale
texture in slot 3 (``<name>_p.dds``) with shader type 3 (Heightmap) and
``SLSF1_Parallax`` set.

Two questions decide whether a shape is converted, and they need two different
sources:

  * the MESH FLAG answers "did the author want parallax here" — that is
    authored intent and is never guessed at;
  * the TEXTURE answers "is there any height data to carry" — measured over
    Nehrim's full 12,437-mesh set: 2359 flagged shapes on **130 distinct
    diffuse textures**, of which only **38** actually hold one.  67 are DXT1
    with no alpha channel at all (vanilla Oblivion architecture sets the flag
    and ships no data, so Oblivion itself renders no parallax there either),
    14 are flat, 6 are too coarsely quantised to be a surface, 1 is a
    soft-edged mask, 1 a transparency cutout, and 3 name a file that does not
    exist.

Both must say yes.  With the flag alone we would write an empty height map and
switch the shader for two thirds of the textures, and a parallax shape with no
usable height renders as a visibly swimming surface.

Per SHAPE the yield is much better than per texture — the textures that do
carry height are the ones used everywhere: **1495 of the 2359 flagged shapes
(63%) convert**, the rest are left flat.

**This never runs by default.**  Verified in game: a correctly built parallax
shape swims in vanilla SSE, and the SSE Parallax Shader Fix did not help.  It
renders correctly under Community Shaders and under ENB, so the output requires
one of those — which the converter cannot detect, hence the opt-in switch.

The alpha classification here follows the one in the author's own
TES4AutoParallaxer, whose thresholds were tuned against this very content.
"""

from asset_convert.game_paths import current_namespace
import math
import os
import struct

import numpy as np

from asset_convert.texture.dds_codec import encode_bc4_channel

# Oblivion's parallax switch on NiTexturingProperty.
APPLY_HILIGHT2 = 4

# Skyrim BSLightingShaderProperty shader type for the parallax path.
SHADER_TYPE_HEIGHTMAP = 3

# Texture slot the height map goes in (the 4th).
HEIGHT_SLOT = 3

_DDS_MAGIC = b'DDS '
_DDPF_FOURCC = 0x4
_FOURCC_DXT1 = b'DXT1'
_FOURCC_DXT3 = b'DXT3'
_FOURCC_DXT5 = b'DXT5'
_FOURCC_DX10 = b'DX10'
_DXGI_BC1 = (70, 71, 72)
_DXGI_BC2 = (73, 74, 75)
_DXGI_BC3 = (76, 77, 78)
_DXGI_BC4_UNORM = 80

# Classification thresholds.  Taken from TES4AutoParallaxer, where they were
# calibrated on Oblivion and Nehrim textures — the same content this converts.
_MIN_RANGE = 30        # max-min alpha below this is a flat channel
_MIN_MID_RATIO = 0.15  # share of texels in 16..239; low means a cutout mask
_MAX_EDGE_RATIO = 0.70 # share at the extremes; high means a soft-edged mask

# Distinct alpha values below which the channel is a staircase, not a surface.
#
# This is a STORAGE limit, not a tuning knob.  DXT3 keeps 4-bit explicit alpha,
# so it can hold at most 16 values however the artist authored them; DXT5
# interpolates and reaches 256.  Measured over the 44 textures this converter
# first accepted: every DXT3 one landed between 7 and 16 levels, every DXT5 one
# between 147 and 256 — two clusters with nothing in between, so the threshold
# is not fitted to the data.
#
# It matters because a parallax shader OFFSETS by the height.  RockBeach04 has
# 7 levels across a range of 102, i.e. ~15 units per step: the surface renders
# as visible terracing rather than depth, which is exactly what it looked like
# in game.  Counting levels rather than testing the FourCC keeps it honest — a
# DXT5 alpha that happens to be a 5-step staircase is just as unusable.
_MIN_LEVELS = 64


class AlphaInfo:
    """What a diffuse's alpha channel turned out to be.

    `kind` is one of: ``height``, ``quantised``, ``binary``, ``bimodal``,
    ``empty``, ``no_alpha``, ``unreadable``.  A category rather than a bool,
    because the build log has to be able to say WHY a shape was skipped —
    "skipped" alone sends the next person back to the texture with no lead.
    """

    __slots__ = ('kind', 'fmt', 'rng', 'mid_ratio', 'edge_ratio', 'mean',
                 'levels')

    def __init__(self, kind, fmt='unknown', rng=0, mid_ratio=0.0,
                 edge_ratio=0.0, mean=0.0, levels=0):
        self.kind = kind
        self.fmt = fmt
        self.rng = rng
        self.mid_ratio = mid_ratio
        self.edge_ratio = edge_ratio
        self.mean = mean
        self.levels = levels

    @property
    def usable(self) -> bool:
        return self.kind == 'height'

    def __repr__(self):
        return (f'<AlphaInfo {self.kind} {self.fmt} range={self.rng} '
                f'levels={self.levels} mid={self.mid_ratio:.2f} '
                f'edge={self.edge_ratio:.2f}>')


def _first_mip_bytes(data: bytes, block_size: int = 16) -> int:
    if len(data) < 20:
        return 0
    height = struct.unpack_from('<I', data, 12)[0]
    width = struct.unpack_from('<I', data, 16)[0]
    return ((max(1, width) + 3) // 4) * ((max(1, height) + 3) // 4) * block_size


def _stats(seen, amin, amax, mid, edge, total, ssum):
    return {
        'rng': amax - amin if total else 0,
        'mid_ratio': mid / total if total else 0.0,
        'edge_ratio': edge / total if total else 0.0,
        'mean': ssum / total if total else 0.0,
        'levels': sum(seen) if total else 0,
    }


def _scan_dxt5_alpha(data: bytes, offset: int, nbytes: int):
    """Decode DXT5 alpha blocks through the INTERPOLATED palette.

    Sampling only the two endpoints (a0/a1) misreads every smooth height field
    as binary, because the endpoints of a gentle gradient block sit far apart
    while everything between them is mid-tone.
    """
    # Vectorized: the per-texel Python loop this replaces was 76% of the whole
    # texture stage (6.06s of 7.99s over 300 normal maps, 2.68M int.from_bytes
    # calls) and made `normalize_specular_alpha` take MINUTES on Oblivion's
    # 3,940 normal maps.  Threading it bought exactly 1.0x -- the work is
    # CPU-bound Python holding the GIL, not I/O.  numpy does the same
    # arithmetic on whole arrays; results are bit-identical.
    end = min(len(data), offset + nbytes)
    n = (end - offset) // 16
    if n <= 0:
        return _stats(bytearray(256), 255, 0, 0, 0, 0, 0)
    blocks = np.frombuffer(data, dtype=np.uint8, count=n * 16,
                           offset=offset).reshape(n, 16)

    a0 = blocks[:, 0].astype(np.int32)
    a1 = blocks[:, 1].astype(np.int32)
    pal = np.empty((n, 8), dtype=np.int32)
    pal[:, 0] = a0
    pal[:, 1] = a1
    eight = a0 > a1                      # 8-alpha vs 6-alpha block mode
    # 8-alpha mode: six interpolated steps in sevenths.
    for k in range(2, 8):
        num = (8 - k) * a0 + (k - 1) * a1
        pal[:, k] = num // 7
    # 6-alpha mode: four interpolated steps in fifths, then 0 and 255.
    six = ~eight
    if six.any():
        b0, b1 = a0[six], a1[six]
        sub = pal[six]
        for k in range(2, 6):
            sub[:, k] = ((6 - k) * b0 + (k - 1) * b1) // 5
        sub[:, 6] = 0
        sub[:, 7] = 255
        pal[six] = sub

    # The 48 index bits (6 bytes) hold 16 three-bit selectors.
    idx_bytes = blocks[:, 2:8].astype(np.uint64)
    bits = np.zeros(n, dtype=np.uint64)
    for i in range(6):
        bits |= idx_bytes[:, i] << np.uint64(8 * i)
    shifts = (np.arange(16, dtype=np.uint64) * np.uint64(3))
    sel = ((bits[:, None] >> shifts[None, :])
           & np.uint64(0x7)).astype(np.intp)
    vals = np.take_along_axis(pal, sel, axis=1).ravel()

    total = vals.size
    counts = np.bincount(vals, minlength=256)
    seen = (counts > 0)
    amin = int(vals.min())
    amax = int(vals.max())
    ssum = int(vals.sum())
    # Disjoint bands: a texel counts as mid OR edge, never both, so the two
    # ratios can be tuned independently.
    mid = int(counts[16:240].sum())
    edge = total - mid
    return _stats(seen, amin, amax, mid, edge, total, ssum)


def _scan_dxt3_alpha(data: bytes, offset: int, nbytes: int):
    """DXT3 stores 4-bit explicit alpha — 16 nibbles per block.

    At most 16 distinct values exist in this format, which is why the level
    count below rejects every DXT3 source: see `_MIN_LEVELS`.
    """
    # Vectorized for the same reason as _scan_dxt5_alpha above.
    end = min(len(data), offset + nbytes)
    n = (end - offset) // 16
    if n <= 0:
        return _stats(bytearray(256), 255, 0, 0, 0, 0, 0)
    blocks = np.frombuffer(data, dtype=np.uint8, count=n * 16,
                           offset=offset).reshape(n, 16)
    alpha_bytes = blocks[:, :8].astype(np.int32)
    # Low nibble first, then high -- the same order the serial loop walked.
    nibbles = np.empty((n, 8, 2), dtype=np.int32)
    nibbles[:, :, 0] = alpha_bytes & 0x0F
    nibbles[:, :, 1] = alpha_bytes >> 4
    vals = (nibbles * 17).ravel()          # 4-bit -> 8-bit

    total = vals.size
    counts = np.bincount(vals, minlength=256)
    seen = (counts > 0)
    amin = int(vals.min())
    amax = int(vals.max())
    ssum = int(vals.sum())
    mid = int(counts[16:240].sum())
    edge = total - mid
    return _stats(seen, amin, amax, mid, edge, total, ssum)


def classify_alpha(data: bytes) -> AlphaInfo:
    """Decide what a diffuse's alpha channel is.

    Returns an :class:`AlphaInfo`; only ``kind == 'height'`` may be converted.
    """
    if len(data) < 128 or data[:4] != _DDS_MAGIC:
        return AlphaInfo('unreadable')

    pf_flags = struct.unpack_from('<I', data, 80)[0]
    fourcc = data[84:88]
    if not (pf_flags & _DDPF_FOURCC):
        return AlphaInfo('no_alpha', 'uncompressed')

    payload = 128
    if fourcc == _FOURCC_DX10:
        if len(data) < 148:
            return AlphaInfo('unreadable')
        dxgi = struct.unpack_from('<I', data, 128)[0]
        payload = 148
        if dxgi in _DXGI_BC1:
            return AlphaInfo('no_alpha', 'bc1')
        if dxgi in _DXGI_BC2:
            fourcc = _FOURCC_DXT3
        elif dxgi in _DXGI_BC3:
            fourcc = _FOURCC_DXT5
        else:
            return AlphaInfo('no_alpha', f'dxgi{dxgi}')

    if fourcc == _FOURCC_DXT1:
        # 1-bit punch-through at most — never a height field.
        return AlphaInfo('no_alpha', 'dxt1')
    if fourcc == _FOURCC_DXT3:
        s = _scan_dxt3_alpha(data, payload, _first_mip_bytes(data))
        fmt = 'dxt3'
    elif fourcc == _FOURCC_DXT5:
        s = _scan_dxt5_alpha(data, payload, _first_mip_bytes(data))
        fmt = 'dxt5'
    else:
        return AlphaInfo('no_alpha', fourcc.decode('latin-1', 'replace'))

    if s['rng'] < _MIN_RANGE:
        # Flat channel.  Measured on Nehrim: every flat one was fully WHITE
        # (mean 255), not black — do not assume an empty alpha reads as 0.
        return AlphaInfo('empty', fmt, **s)
    if s['mid_ratio'] < _MIN_MID_RATIO:
        return AlphaInfo('binary', fmt, **s)
    if s['edge_ratio'] >= _MAX_EDGE_RATIO:
        # Soft-edged cutout (vegetation, splatter): mostly extremes with a thin
        # transition. Enough mid-tones to pass the previous test, still a mask.
        return AlphaInfo('bimodal', fmt, **s)
    if s['levels'] < _MIN_LEVELS:
        # Right shape, not enough resolution to be a surface.  Checked LAST so
        # a coarse mask is still reported as the mask it is.
        return AlphaInfo('quantised', fmt, **s)
    return AlphaInfo('height', fmt, **s)


def decode_alpha_plane(data: bytes) -> 'tuple[int, int, bytearray] | None':
    """Top mip's alpha channel as (width, height, one byte per texel)."""
    if len(data) < 128 or data[:4] != _DDS_MAGIC:
        return None
    h = struct.unpack_from('<I', data, 12)[0]
    w = struct.unpack_from('<I', data, 16)[0]
    fourcc = data[84:88]
    payload = 128
    if fourcc == _FOURCC_DX10:
        dxgi = struct.unpack_from('<I', data, 128)[0]
        payload = 148
        fourcc = (_FOURCC_DXT3 if dxgi in _DXGI_BC2 else
                  _FOURCC_DXT5 if dxgi in _DXGI_BC3 else b'')
    if fourcc not in (_FOURCC_DXT3, _FOURCC_DXT5) or not w or not h:
        return None

    out = bytearray(w * h)
    bx, by = (w + 3) // 4, (h + 3) // 4
    pos = payload
    for byi in range(by):
        for bxi in range(bx):
            if pos + 16 > len(data):
                return None
            texels = [0] * 16
            if fourcc == _FOURCC_DXT5:
                a0, a1 = data[pos], data[pos + 1]
                if a0 > a1:
                    pal = (a0, a1,
                           (6 * a0 + a1) // 7, (5 * a0 + 2 * a1) // 7,
                           (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
                           (2 * a0 + 5 * a1) // 7, (a0 + 6 * a1) // 7)
                else:
                    pal = (a0, a1,
                           (4 * a0 + a1) // 5, (3 * a0 + 2 * a1) // 5,
                           (2 * a0 + 3 * a1) // 5, (a0 + 4 * a1) // 5,
                           0, 255)
                bits = int.from_bytes(data[pos + 2:pos + 8], 'little')
                for i in range(16):
                    texels[i] = pal[(bits >> (i * 3)) & 0x7]
            else:
                for i in range(8):
                    byte = data[pos + i]
                    texels[i * 2] = (byte & 0x0F) * 17
                    texels[i * 2 + 1] = (byte >> 4) * 17
            for ty in range(4):
                y = byi * 4 + ty
                if y >= h:
                    break
                for tx in range(4):
                    x = bxi * 4 + tx
                    if x < w:
                        out[y * w + x] = texels[ty * 4 + tx]
            pos += 16
    return w, h, out


# --------------------------------------------------------------------------
# Output conditioning: half size, then blur, then the tone curve.
# --------------------------------------------------------------------------

# Blur radius in texels per 1000 texels of OUTPUT width, i.e. resolution
# relative.  A fixed pixel radius would hit a 512 map about eight times harder
# than a 4096 one, and this content ships both.  Radius is the kernel's
# half-width; sigma is a third of it, the usual truncation.
#
# 5.0 was the first in-game test and came back NOT ENOUGH -- Skyrim's parallax
# stepping still read as "comic".  The author asked for "at least 15, if not
# 20"; this takes the top of that range, because each retry costs them a full
# build-and-play cycle and an over-soft height field still reads as depth
# while an under-blurred one keeps the artifact.  Override per run with
# `tools/audit/parallax_check.py regen --blur N` rather than editing this.
BLUR_RADIUS_PER_1000 = 20.0

# Linear size divisor applied before the blur.  A height field carries no fine
# detail worth keeping at diffuse resolution -- and see the encoder note above.
HEIGHT_DOWNSCALE = 2

#: Mitchell-Netravali (B = C = 1/3) taps for an exact 2x reduction; negative lobes are why output is clipped to 0..255.
_MITCHELL_TAPS = (-3, -2, -1, 0, 1, 2, 3)
_MITCHELL_WEIGHTS = np.array(
    [-5.0 / 288, 1.0 / 36, 77.0 / 288, 4.0 / 9, 77.0 / 288, 1.0 / 36,
     -5.0 / 288], dtype=np.float32)


def _as_array(w, h, plane):
    return np.frombuffer(bytes(plane), dtype=np.uint8).reshape(h, w)


def _to_plane(arr):
    return bytearray(np.clip(arr + 0.5, 0, 255).astype(np.uint8).tobytes())


def mitchell_halve(w, h, plane):
    """Halve a single-channel plane with a Mitchell filter.

    Separable and accumulated tap by tap rather than gathered into one big
    array: a 4096-square map would otherwise materialise a
    (4096, 2048, 7) float32 intermediate -- 234 MB, in each of nine workers.
    """
    nw, nh = max(1, w // 2), max(1, h // 2)
    if nw == w and nh == h:
        return w, h, plane
    a = _as_array(w, h, plane).astype(np.float32)

    tmp = np.zeros((h, nw), dtype=np.float32)
    xs = 2 * np.arange(nw)
    for d, wt in zip(_MITCHELL_TAPS, _MITCHELL_WEIGHTS):
        tmp += wt * a[:, np.clip(xs + d, 0, w - 1)]

    out = np.zeros((nh, nw), dtype=np.float32)
    ys = 2 * np.arange(nh)
    for d, wt in zip(_MITCHELL_TAPS, _MITCHELL_WEIGHTS):
        out += wt * tmp[np.clip(ys + d, 0, h - 1), :]

    return nw, nh, _to_plane(out)


def blur_radius_for(width: int, per_1000: float = None) -> float:
    """Resolution-relative blur radius for a map this wide."""
    if per_1000 is None:
        per_1000 = BLUR_RADIUS_PER_1000
    return per_1000 * width / 1000.0


def gaussian_blur(w, h, plane, radius: float):
    """Separable Gaussian with edge clamping.

    Padded slices rather than fancy indexing -- contiguous slicing is several
    times faster, and the kernel can reach 20 taps on a 4096 source.
    """
    if radius < 0.5 or w < 2 or h < 2:
        return plane
    r = int(math.ceil(radius))
    sigma = radius / 3.0
    xs = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-(xs * xs) / (2.0 * sigma * sigma))
    k /= k.sum()

    a = _as_array(w, h, plane).astype(np.float32)
    pad = np.pad(a, ((0, 0), (r, r)), mode='edge')
    tmp = np.zeros_like(a)
    for i, wt in enumerate(k):
        tmp += wt * pad[:, i:i + w]

    pad = np.pad(tmp, ((r, r), (0, 0)), mode='edge')
    out = np.zeros_like(tmp)
    for i, wt in enumerate(k):
        out += wt * pad[i:i + h, :]

    return _to_plane(out)


def _downsample(w, h, plane):
    """Box-filter to half size, for the mip chain."""
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = bytearray(nw * nh)
    for y in range(nh):
        y0, y1 = min(2 * y, h - 1), min(2 * y + 1, h - 1)
        for x in range(nw):
            x0, x1 = min(2 * x, w - 1), min(2 * x + 1, w - 1)
            out[y * nw + x] = (plane[y0 * w + x0] + plane[y0 * w + x1] +
                               plane[y1 * w + x0] + plane[y1 * w + x1]) // 4
    return nw, nh, out


def _blocks_clamped(w: int, h: int, plane) -> "np.ndarray":
    """The plane as a (ph, pw) uint8 array padded to whole 4x4 blocks.

    Short edges repeat the last real texel rather than zero-filling: a black
    pad would drag the block's endpoints down and band the edge of every map
    whose side is not a multiple of 4.
    """
    a = np.frombuffer(bytes(plane), dtype=np.uint8).reshape(h, w)
    ph, pw = (h + 3) & ~3, (w + 3) & ~3
    if (ph, pw) == (h, w):
        return a
    return np.pad(a, ((0, ph - h), (0, pw - w)), mode='edge')


def encode_bc4_dds(w: int, h: int, plane, mipmaps: bool = True) -> bytes:
    """A single-channel BC4 DDS, with a full mip chain.

    BC4 is what Community Shaders recommends for height maps: one channel at
    the file size of BC1, without BC1's banding on grey gradients.  ENB reads
    it too.

    Block encoding is shared with the terrain codec, which picks each texel's
    palette entry by nearest value; the blurred, narrow-range blocks a height
    field is made of are where that matters most.
    See: docs/commentary/asset_convert_terrain.md#bc4-index-selection
    """
    levels = []
    cw, ch, cp = w, h, plane
    while True:
        levels.append(
            encode_bc4_channel(_blocks_clamped(cw, ch, cp)).tobytes())
        if not mipmaps or (cw == 1 and ch == 1):
            break
        cw, ch, cp = _downsample(cw, ch, cp)

    hdr = bytearray(128)
    hdr[0:4] = _DDS_MAGIC
    struct.pack_into('<I', hdr, 4, 124)                     # dwSize
    # CAPS | HEIGHT | WIDTH | PIXELFORMAT | LINEARSIZE | MIPMAPCOUNT
    struct.pack_into('<I', hdr, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 |
                     (0x20000 if len(levels) > 1 else 0))
    struct.pack_into('<I', hdr, 12, h)
    struct.pack_into('<I', hdr, 16, w)
    struct.pack_into('<I', hdr, 20, len(levels[0]))         # linear size
    struct.pack_into('<I', hdr, 28, len(levels))            # mip count
    struct.pack_into('<I', hdr, 76, 32)                     # pf size
    struct.pack_into('<I', hdr, 80, _DDPF_FOURCC)
    hdr[84:88] = _FOURCC_DX10
    struct.pack_into('<I', hdr, 108, 0x1000 |               # TEXTURE
                     (0x400000 | 0x8 if len(levels) > 1 else 0))
    dx10 = struct.pack('<IIIII', _DXGI_BC4_UNORM, 3, 0, 1, 0)
    return bytes(hdr) + dx10 + b''.join(levels)


def _mip_chain(w, h, count):
    dims = []
    for _ in range(max(1, count)):
        dims.append((w, h))
        if w == 1 and h == 1:
            break
        w, h = max(1, w // 2), max(1, h // 2)
    return dims


def _bc1_repair_modes(color):
    """Make DXT3/DXT5 color blocks legal as DXT1.  `color` is (n, 8) uint8.

    DXT1 reads a block with ``c0 <= c1`` as three colors plus TRANSPARENT
    black; DXT3/DXT5 color blocks are always four opaque colors.  Both
    repairs below are exact -- no texel changes color:

      c0 <  c1  swap the endpoints and flip the low bit of every 2-bit index
                (0<->1, 2<->3).  The swapped palette names the same four
                colors in a different order, so the flip restores each texel.
      c0 == c1  every palette entry already equals c0, whatever the indices
                say, so zeroing them reproduces the block and steps around the
                transparent slot.
    """
    n = color.shape[0]
    u16 = color.view('<u2').reshape(n, 4)
    c0, c1 = u16[:, 0], u16[:, 1]
    idx = np.ascontiguousarray(color[:, 4:8]).view('<u4').reshape(n)

    less = c0 < c1
    equal = c0 == c1
    nc0 = np.where(less, c1, c0).astype('<u2')
    nc1 = np.where(less, c0, c1).astype('<u2')
    nidx = np.where(less, idx ^ np.uint32(0x55555555), idx).astype('<u4')
    nidx = np.where(equal, np.uint32(0), nidx).astype('<u4')

    out = np.empty((n, 8), dtype=np.uint8)
    out[:, 0:2] = nc0.view(np.uint8).reshape(n, 2)
    out[:, 2:4] = nc1.view(np.uint8).reshape(n, 2)
    out[:, 4:8] = nidx.view(np.uint8).reshape(n, 4)
    return out.tobytes()


def strip_alpha_to_bc1(data: bytes):
    """Re-container a DXT3/DXT5 diffuse as DXT1, dropping the alpha channel.

    This is NOT a recompression.  A DXT3/DXT5 block is 8 bytes of alpha
    followed by 8 bytes of color in exactly BC1's color-block layout, so the
    color half is copied verbatim and the endpoints keep the values the
    original encoder chose.  Dithering and perceptual error metrics have
    nothing to act on: nothing is being quantised, and decoding to RGB just to
    re-quantise would LOSE quality rather than gain it.

    Called only where the alpha is known not to be transparency: a diffuse
    whose alpha was classified ``height`` and carried out to a `_p` map, or a
    LOD tier's copy of an APPLY_HILIGHT2 overlay, whose alpha is a blend
    weight (`lod_far_gen.redirect_overlay_diffuses`).

    Returns DDS bytes, or None if `data` is not DXT3/DXT5 (a DXT1 input is
    already stripped, which makes a re-run a no-op).
    """
    if len(data) < 128 or data[:4] != _DDS_MAGIC:
        return None
    if not (struct.unpack_from('<I', data, 80)[0] & _DDPF_FOURCC):
        return None
    fourcc = data[84:88]
    payload = 128
    if fourcc == _FOURCC_DX10:
        if len(data) < 148:
            return None
        dxgi = struct.unpack_from('<I', data, 128)[0]
        payload = 148
        if dxgi in _DXGI_BC2:
            fourcc = _FOURCC_DXT3
        elif dxgi in _DXGI_BC3:
            fourcc = _FOURCC_DXT5
        else:
            return None
    if fourcc not in (_FOURCC_DXT3, _FOURCC_DXT5):
        return None

    h = struct.unpack_from('<I', data, 12)[0]
    w = struct.unpack_from('<I', data, 16)[0]
    if not w or not h:
        return None

    levels = []
    off = payload
    for mw, mh in _mip_chain(w, h, struct.unpack_from('<I', data, 28)[0]):
        n = ((mw + 3) // 4) * ((mh + 3) // 4)
        end = off + n * 16
        if end > len(data):
            break
        blocks = np.frombuffer(data[off:end], dtype=np.uint8).reshape(n, 16)
        levels.append(_bc1_repair_modes(np.ascontiguousarray(blocks[:, 8:16])))
        off = end
    if not levels:
        return None

    hdr = bytearray(data[:128])
    flags = struct.unpack_from('<I', hdr, 8)[0] | 0x80000    # LINEARSIZE
    flags &= ~0x8                                            # not PITCH
    flags = (flags | 0x20000) if len(levels) > 1 else (flags & ~0x20000)
    struct.pack_into('<I', hdr, 8, flags)
    struct.pack_into('<I', hdr, 20, len(levels[0]))          # linear size
    struct.pack_into('<I', hdr, 28, len(levels))             # mip count
    struct.pack_into('<I', hdr, 76, 32)                      # pf size
    struct.pack_into('<I', hdr, 80, _DDPF_FOURCC)            # no alpha flags
    hdr[84:88] = _FOURCC_DXT1
    struct.pack_into('<I', hdr, 108, 0x1000 |
                     (0x400000 | 0x8 if len(levels) > 1 else 0))
    return bytes(hdr) + b''.join(levels)


def strip_diffuse_alpha(tex_root, keep=()) -> 'tuple[int, int, int, int]':
    """Drop the height-carrying alpha from every converted diffuse.

    The presence of `<name>_p.dds` beside `<name>.dds` IS the record that this
    texture's alpha was a height field — the mesh stage already decided that
    and wrote the map.  Keying off it needs no plumbing from the workers, and
    makes a non-parallax build a no-op by construction.

    `keep` is the set of texture paths some shape reads as OPACITY (collected
    by the mesh stage; paths relative to the plugin's texture root, lowercased
    with backslashes).  A shape blending or testing against the channel is
    evidence it is not a height field there, whatever the texture-level
    classifier said, so those are left as they are.

    Runs AFTER the texture copy, like the landscape-normal fix, so a re-copy
    cannot resurrect the DXT5 versions.

    Returns (converted, skipped, kept, bytes_saved).
    """
    keep = {k.replace('/', '\\').lower() for k in (keep or ())}
    root = os.path.abspath(str(tex_root))
    converted = skipped = kept = saved = 0
    for dirpath, _, files in os.walk(root):
        have = {f.lower() for f in files}
        for fn in files:
            low = fn.lower()
            if not low.endswith('.dds') or low.endswith('_p.dds'):
                continue
            if low[:-4] + '_p.dds' not in have:
                continue
            path = os.path.join(dirpath, fn)
            if keep:
                # The mesh stage names textures the way the NIFs do
                # (`textures\tes4\...`), so compare on that tail.
                rel = os.path.relpath(path, root).replace('/', '\\').lower()
                if (('textures\\' + current_namespace() + '\\' + rel)
                        in keep or rel in keep):
                    kept += 1
                    continue
            try:
                with open(path, 'rb') as f:
                    data = f.read()
            except OSError:
                continue
            blob = strip_alpha_to_bc1(data)
            if blob is None:
                skipped += 1
                continue
            try:
                with open(path, 'wb') as f:
                    f.write(blob)
            except OSError:
                continue
            saved += len(data) - len(blob)
            converted += 1
    return converted, skipped, kept, saved


def height_path(diffuse_rel: str) -> str:
    """`textures\\x\\stone.dds` -> `textures\\x\\stone_p.dds`."""
    if diffuse_rel.lower().endswith('.dds'):
        return diffuse_rel[:-4] + '_p.dds'
    return diffuse_rel + '_p.dds'


def _median(texels):
    srt = sorted(texels)
    return srt[len(srt) // 2]


#: Half-width of the band around a field's own median used to measure flatness.
FLAT_BAND = 20
#: Share within +/-FLAT_BAND of the median the curve aims for; hand-tuned 63.2 vs Nehrim 36.6.
TARGET_FLAT_SHARE = 0.68


def _cumulative(texels):
    """(cumulative 256-bin histogram, total) — one O(n) pass.

    Everything the fit needs is a count per level, so building this once turns
    every bisection step below into O(1) arithmetic instead of a pass over the
    texels.  It also replaces the sort `_median` used to do on this path.
    """
    hist = [0] * 256
    for v in texels:
        hist[v] += 1
    cum = [0] * 256
    run = 0
    for i, k in enumerate(hist):
        run += k
        cum[i] = run
    return cum, run


def _median_from(cum, total):
    """Same element `_median` picks: the (n//2)-th, zero-based."""
    want = total // 2 + 1
    for i, c in enumerate(cum):
        if c >= want:
            return i
    return 255


#: Output levels the +/-FLAT_BAND body must still span; 21 is the 3631-map reference floor.
_MIN_BODY_LEVELS = 21
#: Upper bracket for the p bisection only -- _MIN_BODY_LEVELS is what actually binds.
_MAX_FLATTEN_P = 4.0
_MAX_FLATTEN_P = 4.0


def _flatten(texels, lo, hi, med, p):
    """Apply the curve above.  `lo`, `hi` and `med` all map to themselves."""
    dlo, dhi = med - lo, hi - med
    curve = [0.0] * 256
    for v in range(256):
        d = v - med
        if d < 0 and dlo > 0:
            curve[v] = med - dlo * ((-d / dlo) ** p)
        elif d > 0 and dhi > 0:
            curve[v] = med + dhi * ((d / dhi) ** p)
        else:
            curve[v] = float(med)
    return bytearray(int(round(curve[v])) for v in texels)


def _flat_share_at(cum, total, lo, hi, med, band, p):
    """Share within +/-band of the median AFTER the curve, without applying it.

    The curve is monotone and fixes the median, so the texels that land inside
    the band are exactly those between the band edges' pre-images — and those
    come from inverting the curve, which is the same expression with 1/p.
    """
    dlo, dhi = med - lo, hi - med
    q = 1.0 / p
    top = med + (dhi * (band / dhi) ** q if 0 < band < dhi else dhi)
    bot = med - (dlo * (band / dlo) ** q if 0 < band < dlo else dlo)
    a = max(0, int(math.ceil(bot)))
    b = min(255, int(math.floor(top)))
    if b < a or not total:
        return 0.0
    return (cum[b] - (cum[a - 1] if a else 0)) / total


def _p_ceiling(dlo, dhi, band, f):
    """Largest p that still leaves `_MIN_BODY_LEVELS` inside the band.

    On the output scale the band's half width becomes ``f * D * (band/D)**p``,
    so requiring that to stay above half the level budget pins p from above:

        f * D * (band/D)**p >= _MIN_BODY_LEVELS / 2
        p <= ln(H / (f*D)) / ln(band/D)        — both logs are negative

    A side narrower than the band is not compressed by the curve at all
    (``(band/D)**p`` is then >= 1), so it places no limit.  At p = 1 the band
    is always exactly `FLAT_BAND` wide on the output, i.e. 40 levels, so the
    guard can never forbid leaving the map alone.
    """
    half = _MIN_BODY_LEVELS / 2.0
    cap = _MAX_FLATTEN_P
    for d in (dlo, dhi):
        if d <= band or d <= 0:
            continue
        out = f * d
        if out <= half:
            return 1.0
        cap = min(cap, math.log(half / out) / math.log(band / d))
    return max(1.0, cap)


def _fit_flatten(cum, total, lo, hi, med, band, target, f):
    """Smallest p that puts `target` of the surface inside the band.

    A texture that simply IS restless cannot be dragged onto the target
    without pressing its face flat, so the curve goes as far as the level
    guard allows and no further.  A partial correction beats a destroyed map —
    the same trade the old gamma floor made, for the same reason.
    """
    if hi <= lo or not total:
        return 1.0
    if _flat_share_at(cum, total, lo, hi, med, band, 1.0) >= target:
        return 1.0                          # already flat enough: hands off
    pmax = _p_ceiling(med - lo, hi - med, band, f)
    if pmax <= 1.0:
        return 1.0
    if _flat_share_at(cum, total, lo, hi, med, band, pmax) < target:
        return pmax                         # unreachable: go as far as allowed
    p_lo, p_hi = 1.0, pmax
    for _ in range(24):
        p = (p_lo + p_hi) / 2.0
        if _flat_share_at(cum, total, lo, hi, med, band, p) >= target:
            p_hi = p
        else:
            p_lo = p
    return p_hi


#: Amplitude a field may span before compression -- the DETECTOR; 163 sits in the 156..169 gap, and is FRAGILE.
DEFAULT_MAX_RANGE = 163

#: Median a CORRECTED field is moved to; a correction, never a detector.
TARGET_MEDIAN = 117

#: One-sided floor: below this a field is re-centered ALONE; nothing authored is darker than 52.
MIN_MEDIAN = 45


#: How deep a CORRECTED field is taken -- a dial set by eye, kept apart from the detector; 0 = same as max_range.
DEFAULT_TARGET_RANGE = 0


def normalize_height(texels, strength: float = 1.0,
                     max_range: int = DEFAULT_MAX_RANGE,
                     target_median: int = TARGET_MEDIAN,
                     target_range: int = DEFAULT_TARGET_RANGE,
                     flat_target: float = TARGET_FLAT_SHARE,
                     min_median: int = MIN_MEDIAN):
    """Compress an over-deep height field — and leave a good one ALONE.

    Curve, then compression about the field's own median, then the shift,
    limited so nothing clips past 0 or 255.  Each stage takes 0 to disable it,
    and each argument defaults to the constant that documents it.

    `max_range` and `min_median` are the two DETECTORS: a field inside the cap
    whose median clears the floor is returned bit for bit unchanged.

    See: docs/commentary/asset_convert_texture.md#oblivion-parallax-skyrim-height-maps
    """
    if not texels:
        return texels
    lo, hi = min(texels), max(texels)
    rng = hi - lo
    cum, total = _cumulative(texels)
    med = _median_from(cum, total)

    f = float(strength)
    if max_range and rng > max_range:
        # detected as over-deep -> take it down to target_range, not merely
        # to the detection threshold
        f = min(f, (target_range or max_range) / rng)
    off_center = bool(min_median and target_median and med < min_median)
    if f >= 1.0 and not off_center:
        return texels                      # already fine: do not touch it

    # Shape first: press the body of the surface together around its own
    # median so only the genuine grooves stay deep.  Done before the linear
    # steps because it is what actually changes how the surface reads; the cap
    # and the shift then place the reshaped field.
    #
    # The band is expressed on the OUTPUT scale.  `f` is a uniform factor
    # about the median, so a texel ends up within FLAT_BAND of the output
    # median exactly when it is within FLAT_BAND/f of the median here — no
    # approximation, which is why the fit can be done before the scaling.
    #
    # Skipped entirely when only the median floor fired: that field's SHAPE was
    # never in question, and the amplitude detector stays the only thing
    # allowed to decide a map needs reshaping.
    if flat_target and rng > 0 and f < 1.0:
        p = _fit_flatten(cum, total, lo, hi, med, FLAT_BAND / f, flat_target,
                         f)
        if p > 1.0:
            texels = _flatten(texels, lo, hi, med, p)
            # The curve fixes `lo`, `hi` and the median exactly and is
            # monotone, so none of the three needs recomputing.

    shift = 0.0
    if target_median:
        lo_s = med + (lo - med) * f
        hi_s = med + (hi - med) * f
        shift = target_median - med
        shift = max(shift, -lo_s)          # keep the darkest texel >= 0
        shift = min(shift, 255.0 - hi_s)   # keep the brightest <= 255

    out = bytearray(len(texels))
    for i, v in enumerate(texels):
        nv = int(round(med + (v - med) * f + shift))
        out[i] = 0 if nv < 0 else 255 if nv > 255 else nv
    return out


# --------------------------------------------------------------------------
# The global depth scale.
NEUTRAL_LEVEL = 128
DEPTH_SCALE = 0.6


def scale_depth(texels, factor: float = None, center: int = NEUTRAL_LEVEL):
    """Compress every height field toward the shader's neutral plane.

    One affine map, identical for every texture:

        v' = center + (v - center) * factor

    so relative depth WITHIN a map and BETWEEN maps both survive exactly; only
    the absolute excursion shrinks.  `factor` 1.0 (or None -> DEPTH_SCALE)
    leaves the field alone; 0.5 halves how far the surface travels.
    """
    if not texels:
        return texels
    f = DEPTH_SCALE if factor is None else float(factor)
    if f == 1.0:
        return texels
    lut = bytes(max(0, min(255, int(round(center + (v - center) * f))))
                for v in range(256))
    return bytearray(lut[v] for v in texels)


def build_height_map(src_dds: str, out_path: str, strength: float = 1.0,
                     max_range: int = DEFAULT_MAX_RANGE,
                     target_range: int = DEFAULT_TARGET_RANGE,
                     blur_per_1000: float = None,
                     depth: float = None) -> bool:
    """Write the diffuse's alpha channel out as a BC4 height map.

    Mesh conversion is a process POOL and several meshes share a diffuse, so
    the bytes go to a per-process temp name and are moved into place with
    :func:`os.replace`, atomic on NTFS.

    Order is halve, blur, tone curve, then the global depth scale.
    See: docs/commentary/asset_convert_texture.md#output-conditioning
    """
    try:
        with open(src_dds, 'rb') as f:
            data = f.read()
    except OSError:
        return False
    plane = decode_alpha_plane(data)
    if plane is None:
        return False
    w, h, texels = plane
    if HEIGHT_DOWNSCALE == 2:
        w, h, texels = mitchell_halve(w, h, texels)
    texels = gaussian_blur(w, h, texels, blur_radius_for(w, blur_per_1000))
    texels = normalize_height(texels, strength, max_range, TARGET_MEDIAN,
                              target_range)
    # Global depth scale, same factor on every map -- see scale_depth.
    texels = scale_depth(texels, depth)
    blob = encode_bc4_dds(w, h, texels)

    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = f'{out_path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'wb') as f:
            f.write(blob)
        os.replace(tmp, out_path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True
