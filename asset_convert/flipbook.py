"""Flip-book atlas builder: NiFlipController frame DDS files → one horizontal
strip atlas + parameters for a BSEffectShaderPropertyFloatController (U Offset).

Oblivion animates fire/effect quads by flipping the diffuse texture each frame
(NiFlipController, dead in Skyrim: 0/17,216 vanilla meshes).  Skyrim animates
shader UVs instead: uv_scale.u = 1/N shows one frame of an N-frame strip and a
float controller steps U Offset through k/N with stepped (CONST) keys.

Frames are decoded from DXT1/3/5 (or uncompressed BGRA) and re-packed as an
uncompressed 32-bit BGRA DDS (no mips) — universally loadable, no encoder
needed.  The strip is padded to a power-of-two frame count so the atlas width
stays POT (128px frames → 2048/4096 wide).
"""
import os
import struct

_DDS_MAGIC = b'DDS '


def _read_dds_header(raw):
    if raw[:4] != _DDS_MAGIC or len(raw) < 128:
        raise ValueError('not a DDS')
    h, w = struct.unpack_from('<II', raw, 12)
    pf_flags, fourcc = struct.unpack_from('<I4s', raw, 80)
    rgb_bits, = struct.unpack_from('<I', raw, 88)
    return w, h, pf_flags, fourcc, rgb_bits


def probe_dds(path):
    """Return (width, height, kind) or None if unsupported."""
    try:
        with open(path, 'rb') as f:
            raw = f.read(128)
        w, h, pf_flags, fourcc, rgb_bits = _read_dds_header(raw)
    except Exception:
        return None
    if pf_flags & 0x4:  # FOURCC
        if fourcc in (b'DXT1', b'DXT3', b'DXT5'):
            return (w, h, fourcc.decode())
        return None
    if pf_flags & 0x40 and rgb_bits == 32:  # uncompressed RGB(A)
        return (w, h, 'RGBA32')
    return None


def _c565(v):
    r = ((v >> 11) & 31) * 255 // 31
    g = ((v >> 5) & 63) * 255 // 63
    b = (v & 31) * 255 // 31
    return r, g, b


def _decode_dxt(raw_data, w, h, kind):
    """Decode one DXT1/3/5 mip level to a bytearray of BGRA pixels."""
    out = bytearray(w * h * 4)
    bs = 8 if kind == 'DXT1' else 16
    bw = max(w // 4, 1)
    pos = 0
    for by in range(max(h // 4, 1)):
        for bx in range(bw):
            blk = raw_data[pos:pos + bs]
            pos += bs
            if len(blk) < bs:
                return out
            coff = 0 if kind == 'DXT1' else 8
            c0, c1 = struct.unpack_from('<HH', blk, coff)
            lut, = struct.unpack_from('<I', blk, coff + 4)
            r0, g0, b0 = _c565(c0)
            r1, g1, b1 = _c565(c1)
            if kind == 'DXT1' and c0 <= c1:
                pal = [(r0, g0, b0, 255), (r1, g1, b1, 255),
                       ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255),
                       (0, 0, 0, 0)]
            else:
                pal = [(r0, g0, b0, 255), (r1, g1, b1, 255),
                       ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
                       ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255)]
            # alpha
            alphas = None
            if kind == 'DXT3':
                alphas = []
                for i in range(8):
                    byte = blk[i]
                    alphas.append((byte & 0xF) * 17)
                    alphas.append((byte >> 4) * 17)
            elif kind == 'DXT5':
                a0, a1 = blk[0], blk[1]
                bits = int.from_bytes(blk[2:8], 'little')
                apal = [a0, a1]
                if a0 > a1:
                    apal += [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
                else:
                    apal += [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)]
                    apal += [0, 255]
                alphas = [apal[(bits >> (3 * i)) & 7] for i in range(16)]
            for py in range(4):
                yy = by * 4 + py
                if yy >= h:
                    break
                for px in range(4):
                    xx = bx * 4 + px
                    if xx >= w:
                        continue
                    idx = (lut >> (2 * (py * 4 + px))) & 3
                    r, g, b, a = pal[idx]
                    if alphas is not None:
                        a = alphas[py * 4 + px]
                    o = (yy * w + xx) * 4
                    out[o] = b; out[o + 1] = g; out[o + 2] = r; out[o + 3] = a
    return out


def decode_dds(path):
    """Decode the top mip of a DDS to (w, h, BGRA bytearray)."""
    raw = open(path, 'rb').read()
    w, h, pf_flags, fourcc, rgb_bits = _read_dds_header(raw)
    data = raw[128:]
    if pf_flags & 0x4:
        return w, h, _decode_dxt(data, w, h, fourcc.decode())
    if pf_flags & 0x40 and rgb_bits == 32:
        return w, h, bytearray(data[:w * h * 4])
    raise ValueError(f'unsupported DDS format in {path}')


def _write_bgra_dds(path, w, h, pixels):
    hdr = bytearray(128)
    hdr[0:4] = _DDS_MAGIC
    struct.pack_into('<I', hdr, 4, 124)                   # header size
    struct.pack_into('<I', hdr, 8, 0x0002100F)            # caps|h|w|pitch|pixfmt
    struct.pack_into('<II', hdr, 12, h, w)
    struct.pack_into('<I', hdr, 20, w * 4)                # pitch
    struct.pack_into('<I', hdr, 28, 1)                    # mip count
    struct.pack_into('<I', hdr, 76, 32)                   # pixfmt size
    struct.pack_into('<I', hdr, 80, 0x41)                 # RGB | ALPHAPIXELS
    struct.pack_into('<I', hdr, 88, 32)                   # bit count
    struct.pack_into('<IIII', hdr, 92, 0x00FF0000, 0x0000FF00,
                     0x000000FF, 0xFF000000)              # BGRA masks
    struct.pack_into('<I', hdr, 108, 0x1000)              # caps: TEXTURE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(hdr)
        f.write(pixels)


def next_pow2(n):
    p = 1
    while p < n:
        p *= 2
    return p


def build_flip_atlas(frame_files, out_path):
    """Compose frame DDS files into one horizontal-strip BGRA DDS.

    Returns the padded frame count (pad slots repeat the last frame so linear
    filtering at the strip edge stays clean)."""
    frames = [decode_dds(p) for p in frame_files]
    w, h = frames[0][0], frames[0][1]
    if any(fw != w or fh != h for fw, fh, _ in frames):
        raise ValueError('flip frames differ in size')
    n_pad = next_pow2(len(frames))
    row = bytearray(w * n_pad * h * 4)
    stride = w * n_pad * 4
    for i in range(n_pad):
        _, _, px = frames[min(i, len(frames) - 1)]
        for y in range(h):
            dst = y * stride + i * w * 4
            src = y * w * 4
            row[dst:dst + w * 4] = px[src:src + w * 4]
    _write_bgra_dds(out_path, w * n_pad, h, row)
    return n_pad
