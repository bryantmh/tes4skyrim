"""Terrain LOD generation for TES4→TES5 worldspaces.

Reads LAND records (VHGT heights, VCLR vertex colors, ATXT/VTXT texture layers)
from the converted ESM and produces:
  meshes/terrain/<WRLD>/<WRLD>.<level>.<tx>.<ty>.btr   — heightmap NIF per tile
  textures/terrain/<WRLD>/<WRLD>.<level>.<tx>.<ty>.dds  — per-tile diffuse (DXT1)
  textures/terrain/<WRLD>/<WRLD>.<level>.<tx>.<ty>_n.dds — per-tile normal (flat)

LOD levels generated: 4, 8, 16 (cells per tile side).
Each tile covers level×level cells.

Vertex layout per tile: (level*32+1) × (level*32+1) vertices.
Each cell contributes exactly 33 verts per side (32 intervals), sharing the
boundary vertex with its neighbor, so a level-N tile has N*32+1 verts per side.
Local step = CELL_SIZE / (level*32) so that level verts × step = CELL_SIZE.
Heights are in Skyrim units (1 unit ≈ 1.4 cm).  Cell size = 4096 units.
"""

import io
import math
import os
import struct
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
except ImportError:
    _PYFFI = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CELL_SIZE   = 4096.0   # Skyrim units per cell
VERTS_SIDE  = 33       # vertices per cell side in TES5 LAND records (32 intervals)
DELTA_SCALE = 8.0      # each int8 delta = 8 Skyrim units of height

LOD_LEVELS  = [4, 8, 16, 32]

# Diffuse DDS size per LOD level.  A level-N tile spans N cells; it is only ever
# viewed at distance, so per-cell texel density can stay low.  The old flat
# 1024/2048 gave every one of the ~960 LOD4 tiles a 683KB diffuse + 1.4MB normal
# => 3GB.  Scaling with level (roughly constant texels/cell) keeps quality where
# it's seen and cuts the total ~8x.
TEX_SIZE_BY_LEVEL = {4: 256, 8: 512, 16: 1024, 32: 2048}
TEX_SIZE = 512  # fallback default

# Normal maps carry far less perceptible detail than diffuse at LOD distance,
# so bake them at half the diffuse resolution (BC5 is 2x DXT1 per texel, so this
# is the single biggest size win).
NORMAL_SIZE_DIVISOR = 2

# ---------------------------------------------------------------------------
# LAND record parsing
# ---------------------------------------------------------------------------

def _find_worldspace_fid(raw: bytes, n: int, edid: str):
    """Linear scan for a WRLD record matching edid; return its FormID or None."""
    p = 0
    while p < n - 24:
        sig4 = raw[p:p+4]
        if sig4 == b'WRLD':
            size = struct.unpack_from('<I', raw, p+4)[0]
            if p + 24 + size > n:
                break
            fid  = struct.unpack_from('<I', raw, p+12)[0]
            body = raw[p+24:p+24+size]
            q = 0
            while q + 6 <= len(body):
                s  = body[q:q+4]
                sz = struct.unpack_from('<H', body, q+4)[0]
                if s == b'EDID':
                    if body[q+6:q+6+sz].rstrip(b'\x00').decode('latin-1', errors='replace') == edid:
                        return fid
                    break
                q += 6 + sz
            p += 24 + size
        elif sig4 == b'GRUP':
            p += 24          # descend into group
        else:
            if p + 8 > n: break
            size = struct.unpack_from('<I', raw, p+4)[0]
            p += 24 + size
    return None


def _parse_land_records(esm_path: Path, worldspace_edid: str = 'TES4Tamriel'):
    """Parse LAND + CELL water data for one worldspace from the output ESM.

    Returns (lands, cell_water, default_water_height):
      lands:      (cell_x, cell_y) -> {heights: ndarray(33,33 float32),
                                       colors:  ndarray(33,33,3 uint8),
                                       layers:  BTXT/ATXT/VTXT dict}
      cell_water: (cell_x, cell_y) -> (has_water: bool, height: float or None)
                  height is the cell XCLW override; None = use worldspace default
      default_water_height: WRLD DNAM default water height (0.0 if absent)
    """
    raw = esm_path.read_bytes()
    n   = len(raw)
    lands = {}
    cell_water = {}
    wrld_water = {'default': None}

    # We need CELL grid coords alongside each LAND.
    # Strategy: track current CELL grid coords via a lightweight group scanner.
    # Group type 6  = cell children group (label = cell FormID).
    # Group type 1  = world children group (label = parent WRLD FormID).
    # We only collect LAND records that belong to the target worldspace.

    cell_coords = {}   # form_id → (x, y)

    # Find target worldspace FormID first (fast linear scan)
    target_wrld_fid = _find_worldspace_fid(raw, n, worldspace_edid)
    if target_wrld_fid is not None:
        print(f"  Filtering to worldspace '{worldspace_edid}' (FormID={target_wrld_fid:#010x})")
    else:
        print(f"  WARNING: Worldspace '{worldspace_edid}' not found — collecting all LAND records")

    def _read_rec(p):
        if p + 24 > n:
            return None, p + 1
        sig  = raw[p:p+4].decode('latin-1', errors='replace')
        size = struct.unpack_from('<I', raw, p+4)[0]
        fid  = struct.unpack_from('<I', raw, p+12)[0]
        body = raw[p+24: p+24+size]
        return (sig, fid, body), p+24+size

    def _sub(body, tag):
        tag4 = tag.encode()
        p = 0
        while p + 6 <= len(body):
            s = body[p:p+4]
            sz = struct.unpack_from('<H', body, p+4)[0]
            if s == tag4:
                return body[p+6:p+6+sz]
            p += 6 + sz
        return None

    def scan(start, end, cur_cell_fid, cur_wrld_fid=0):
        p = start
        while p < end and p < n:
            if p + 4 > n:
                break
            sig4 = raw[p:p+4]
            if sig4 == b'GRUP':
                if p + 24 > n:
                    break
                g_size  = struct.unpack_from('<I', raw, p+4)[0]
                g_type  = struct.unpack_from('<I', raw, p+12)[0]
                g_label = raw[p+8:p+12]
                next_cell = cur_cell_fid
                next_wrld = cur_wrld_fid
                if g_type == 1:          # world children: label = parent WRLD FormID
                    next_wrld = struct.unpack_from('<I', g_label)[0]
                elif g_type == 6:        # cell children (persistent+temp block): label = parent CELL FormID
                    next_cell = struct.unpack_from('<I', g_label)[0]
                elif g_type in (8, 9):   # persistent (8) / temporary (9) cell subgroup
                    # LAND records live in type-9; carry cur_cell_fid through unchanged
                    pass
                scan(p+24, p+g_size, next_cell, next_wrld)
                p += g_size
            else:
                rec, np_ = _read_rec(p)
                if rec is None:
                    break
                sig, fid, body = rec
                if sig == 'CELL':
                    xclc = _sub(body, 'XCLC')
                    if xclc and len(xclc) >= 8:
                        gx = struct.unpack_from('<i', xclc, 0)[0]
                        gy = struct.unpack_from('<i', xclc, 4)[0]
                        cell_coords[fid] = (gx, gy)
                        cur_cell_fid = fid
                        if target_wrld_fid is None or cur_wrld_fid == target_wrld_fid:
                            # DATA bit 0x02 = Has Water; XCLW = height override
                            data = _sub(body, 'DATA')
                            flags = 0
                            if data:
                                flags = data[0] | (data[1] << 8 if len(data) >= 2 else 0)
                            wh = None
                            xclw = _sub(body, 'XCLW')
                            if xclw and len(xclw) >= 4:
                                v = struct.unpack_from('<f', xclw)[0]
                                if -1e9 < v < 1e9:   # exclude "default" sentinels
                                    wh = v
                            cell_water[(gx, gy)] = (bool(flags & 0x02), wh)
                elif sig == 'WRLD':
                    if target_wrld_fid is not None and fid == target_wrld_fid:
                        dnam = _sub(body, 'DNAM')
                        if dnam and len(dnam) >= 8:
                            wrld_water['default'] = struct.unpack_from('<f', dnam, 4)[0]
                elif sig == 'LAND':
                    # Only collect LAND from the target worldspace
                    if target_wrld_fid is None or cur_wrld_fid == target_wrld_fid:
                        coords = cell_coords.get(cur_cell_fid)
                        if coords is not None:
                            land = _decode_land(body, _sub)
                            if land is not None:
                                lands[coords] = land
                p = np_

    # Skip TES4/TES5 file header
    hdr_size = struct.unpack_from('<I', raw, 4)[0]
    scan(24 + hdr_size, n, 0, 0)
    default_wh = wrld_water['default'] if wrld_water['default'] is not None else 0.0
    return lands, cell_water, default_wh


def _decode_land(body, _sub):
    """Decode VHGT → heights (33×33), VCLR → colors (33×33,3)."""
    vhgt = _sub(body, 'VHGT')
    if vhgt is None or len(vhgt) < 4 + VERTS_SIDE * VERTS_SIDE:
        return None

    # VHGT format (UESP wiki / xEdit confirmed):
    #   Offset: float — starting accumulator value in "delta units"
    #   delta[row][col]: int8 — cumulative delta; row start comes from delta[row][0]
    #
    # Accumulation (all in delta units, i.e. 1 unit = DELTA_SCALE game units):
    #   current = Offset
    #   for row 0..32:
    #       current += delta[row][0]       ← first column updates the accumulator
    #       row_start = current
    #       for col 1..32:
    #           current += delta[row][col]
    #           h[row][col] = current * DELTA_SCALE
    #       h[row][0] = row_start * DELTA_SCALE
    #
    # xEdit confirms: to shift terrain by ShiftZ game units,
    #   Offset += ShiftZ / DELTA_SCALE  → Offset is in delta units.
    vhgt_offset = struct.unpack_from('<f', vhgt, 0)[0]
    deltas = np.frombuffer(vhgt[4:4 + VERTS_SIDE*VERTS_SIDE], dtype=np.int8
                           ).reshape(VERTS_SIDE, VERTS_SIDE).astype(np.float32)

    heights = np.zeros((VERTS_SIDE, VERTS_SIDE), dtype=np.float32)
    acc = vhgt_offset
    for row in range(VERTS_SIDE):
        acc += deltas[row, 0]           # first delta of each row updates row accumulator
        row_start = acc
        col_acc = row_start
        heights[row, 0] = col_acc * DELTA_SCALE
        for col in range(1, VERTS_SIDE):
            col_acc += deltas[row, col]
            heights[row, col] = col_acc * DELTA_SCALE
        acc = row_start                 # next row's delta[row,0] is relative to this row's start

    vclr = _sub(body, 'VCLR')
    if vclr and len(vclr) >= VERTS_SIDE * VERTS_SIDE * 3:
        colors = np.frombuffer(vclr[:VERTS_SIDE*VERTS_SIDE*3], dtype=np.uint8
                               ).reshape(VERTS_SIDE, VERTS_SIDE, 3).copy()
    else:
        colors = np.full((VERTS_SIDE, VERTS_SIDE, 3), 128, dtype=np.uint8)

    # Full per-quadrant texture layer structure (BTXT/ATXT/VTXT) for the
    # diffuse compositor.  decode_land_layers takes the raw record body.
    from .terrain_lod_textures import decode_land_layers
    layers = decode_land_layers(body)

    return {'heights': heights, 'colors': colors, 'layers': layers}


# ---------------------------------------------------------------------------
# Tile assembly
# ---------------------------------------------------------------------------

def _assemble_tile(lands, tile_x, tile_y, level):
    """Merge level×level cells into a ((level*32+1) × (level*32+1)) height+color grid.

    tile_x, tile_y: SW cell coordinates of this tile.

    Each cell contributes exactly VERTS_SIDE (33) vertices per side with 32
    intervals.  Adjacent cells share their boundary vertex, so:
      total verts per side = level * 32 + 1

    Vertex mapping for cell (cx, cy) within the tile:
      destination column range: [cx*32 .. cx*32+32]  (inclusive both ends)
      destination row    range: [cy*32 .. cy*32+32]

    Missing cells (world edges, water areas) are filled by edge-extending from
    the nearest row/column that has real data, preventing flat Z=0 holes.

    Returns (heights ndarray (tv×tv) float32,
             colors  ndarray (tv×tv×3) uint8)
    where tv = level*32+1.
    """
    tv = level * 32 + 1   # verts per tile side

    # Use NaN as sentinel so we can distinguish "no data" from "height=0"
    out_h = np.full((tv, tv), np.nan, dtype=np.float32)
    out_c = np.full((tv, tv, 3), 100, dtype=np.uint8)

    for cy in range(level):
        for cx in range(level):
            cell_key = (tile_x + cx, tile_y + cy)
            land = lands.get(cell_key)
            if land is None:
                continue
            h = land['heights']   # (33,33) float32
            c = land['colors']    # (33,33,3) uint8

            # Destination start in tile grid (SW = row/col 0, NE = row/col tv-1)
            dst_x0 = cx * 32
            dst_y0 = cy * 32

            # Copy all 33×33 source verts into their destination positions.
            # The boundary column/row (src col 32 / row 32) overlaps with the
            # next cell's column/row 0; we write it here and the neighbor will
            # overwrite it with the same value (heights must agree at boundaries).
            out_h[dst_y0:dst_y0+33, dst_x0:dst_x0+33] = h
            out_c[dst_y0:dst_y0+33, dst_x0:dst_x0+33] = c

    # Fill NaN regions (missing cells) by edge-extending from nearest real data.
    # Process row-by-row then column-by-column with forward/backward fill.
    if np.any(np.isnan(out_h)):
        _fill_missing(out_h, out_c)

    return out_h, out_c


def _fill_missing(h: np.ndarray, c: np.ndarray):
    """In-place fill of NaN cells in h (and corresponding rows in c) by
    edge-extending from the nearest valid row/column.

    Strategy:
      1. For each column, forward-fill NaN rows downward from the first valid row,
         then backward-fill upward from the last valid row.
      2. If an entire column is NaN, copy from the nearest non-NaN column.
    """
    tv = h.shape[0]

    # Step 1: fill each column vertically
    for col in range(tv):
        col_h = h[:, col]
        valid = ~np.isnan(col_h)
        if valid.all():
            continue
        if not valid.any():
            continue  # whole column empty — handled in step 2

        # Forward fill (propagate downward from last valid)
        last_val = None
        last_col_c = None
        for row in range(tv):
            if valid[row]:
                last_val   = col_h[row]
                last_col_c = c[row, col].copy()
            elif last_val is not None:
                h[row, col]    = last_val
                c[row, col]    = last_col_c

        # Backward fill (propagate upward from first valid)
        first_val = None
        first_col_c = None
        for row in range(tv - 1, -1, -1):
            if not np.isnan(h[row, col]):
                first_val   = h[row, col]
                first_col_c = c[row, col].copy()
            elif first_val is not None:
                h[row, col]    = first_val
                c[row, col]    = first_col_c

    # Step 2: fill any columns that are entirely NaN from adjacent column
    for col in range(tv):
        if not np.any(np.isnan(h[:, col])):
            continue
        # Search left then right for a valid column
        src = None
        for d in range(1, tv):
            if col - d >= 0 and not np.any(np.isnan(h[:, col - d])):
                src = col - d; break
            if col + d < tv and not np.any(np.isnan(h[:, col + d])):
                src = col + d; break
        if src is not None:
            nan_rows = np.isnan(h[:, col])
            h[nan_rows, col] = h[nan_rows, src]
            c[nan_rows, col] = c[nan_rows, src]

    # Fallback: any remaining NaN → 0
    nan_mask = np.isnan(h)
    if nan_mask.any():
        h[nan_mask] = 0.0


# ---------------------------------------------------------------------------
# LOD water (vanilla-style)
# ---------------------------------------------------------------------------

def _cell_water_height(cell_water, key, default_wh):
    """Water height for a cell, or None if the cell has no water."""
    cw = cell_water.get(key)
    if cw is None or not cw[0]:
        return None
    return cw[1] if cw[1] is not None else default_wh


def _tile_water_quads(lands, cell_water, tile_x, tile_y, level, default_wh):
    """Return [(cx, cy, water_height_world), ...] for cells in this tile that
    need a LOD water quad (cell has water and its terrain dips below the water
    surface), matching how vanilla terrain LOD only carries water quads where
    water is actually visible.  cx/cy are cell offsets within the tile."""
    quads = []
    for cx in range(level):
        for cy in range(level):
            key = (tile_x + cx, tile_y + cy)
            wh = _cell_water_height(cell_water, key, default_wh)
            if wh is None:
                continue
            land = lands.get(key)
            if land is not None and float(land['heights'].min()) >= wh:
                continue   # terrain entirely above water in this cell
            quads.append((cx, cy, wh))
    return quads


# ---------------------------------------------------------------------------
# DDS writing (DXT1 via PIL/Pillow or pure-Python fallback)
# ---------------------------------------------------------------------------

def _write_dds_dxt1(colors_rgb: np.ndarray, path: Path, size: int = TEX_SIZE):
    """Write a DXT1 DDS with full mipmap chain from an RGB ndarray.

    Generates mipmaps down to 1×1, as vanilla Skyrim terrain LOD DDS files do.
    size should match vanilla per LOD level (1024 for LOD4/8, 2048 for LOD16/32).
    """
    from PIL import Image
    img = Image.fromarray(colors_rgb, 'RGB')
    img = img.resize((size, size), Image.LANCZOS)

    # Build mip chain: size, size/2, size/4, ... down to 1×1
    mip_levels = []
    mip_img = img
    while True:
        mip_arr = np.array(mip_img)
        mip_levels.append(_encode_dxt1_quality(mip_arr))
        mip_w, mip_h = mip_img.size
        if mip_w == 1 and mip_h == 1:
            break
        mip_img = mip_img.resize((max(1, mip_w // 2), max(1, mip_h // 2)), Image.LANCZOS)

    mip_count = len(mip_levels)
    all_data = b''.join(mip_levels)
    hdr = _make_dds_header_dxt1(size, size, len(mip_levels[0]), mip_count=mip_count)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(hdr + all_data)


def _make_dds_header_dxt1(w, h, linear_size, mip_count=1):
    DDSD_CAPS        = 0x1
    DDSD_HEIGHT      = 0x2
    DDSD_WIDTH       = 0x4
    DDSD_PIXELFORMAT = 0x1000
    DDSD_LINEARSIZE  = 0x80000
    DDSD_MIPMAPCOUNT = 0x20000
    DDPF_FOURCC      = 0x4
    DDSCAPS_TEXTURE  = 0x1000
    DDSCAPS_MIPMAP   = 0x400000
    DDSCAPS_COMPLEX  = 0x8

    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
    caps  = DDSCAPS_TEXTURE
    if mip_count > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps  |= DDSCAPS_MIPMAP | DDSCAPS_COMPLEX

    hdr  = b'DDS '
    hdr += struct.pack('<I', 124)             # dwSize
    hdr += struct.pack('<I', flags)            # dwFlags
    hdr += struct.pack('<I', h)                # dwHeight
    hdr += struct.pack('<I', w)                # dwWidth
    hdr += struct.pack('<I', linear_size)      # dwPitchOrLinearSize (size of top mip)
    hdr += struct.pack('<I', 0)                # dwDepth
    hdr += struct.pack('<I', mip_count)        # dwMipMapCount
    hdr += b'\x00' * 44                       # dwReserved1[11]
    # Pixel format (32 bytes)
    hdr += struct.pack('<II', 32, DDPF_FOURCC) # size, flags
    hdr += b'DXT1'                             # dwFourCC
    hdr += struct.pack('<IIIII', 0,0,0,0,0)   # unused
    hdr += struct.pack('<I', caps)             # dwCaps
    hdr += struct.pack('<IIII', 0,0,0,0)      # remaining caps + reserved
    assert len(hdr) == 128
    return hdr


def _encode_dxt1_quality(img: np.ndarray) -> bytes:
    """DXT1 encoder with per-block min/max color endpoints for better quality.

    For each 4×4 block, finds the two most distant colors (min/max in each
    channel) and uses them as DXT1 endpoints c0 > c1 (opaque mode).
    Each pixel is then assigned the nearest of the 4 interpolated colors.
    """
    h, w = img.shape[:2]
    ph = (h + 3) & ~3
    pw = (w + 3) & ~3
    padded = np.zeros((ph, pw, 3), dtype=np.uint8)
    padded[:h, :w] = img

    out = bytearray()
    for by in range(0, ph, 4):
        for bx in range(0, pw, 4):
            block = padded[by:by+4, bx:bx+4].reshape(16, 3).astype(np.int32)

            # Find min/max per channel
            cmin = block.min(axis=0)
            cmax = block.max(axis=0)

            c0_rgb = cmax.astype(np.uint8)
            c1_rgb = cmin.astype(np.uint8)

            c0 = _rgb_to_565(c0_rgb)
            c1 = _rgb_to_565(c1_rgb)

            # Ensure c0 > c1 for opaque DXT1 (4-color mode)
            if c0 < c1:
                c0, c1 = c1, c0
                c0_rgb, c1_rgb = c1_rgb, c0_rgb
            elif c0 == c1:
                if c0 == 0:
                    c0 = 1
                else:
                    c1 = c0 - 1

            # The 4 palette entries in opaque mode:
            #   code 0 → c0
            #   code 1 → c1
            #   code 2 → 2/3*c0 + 1/3*c1
            #   code 3 → 1/3*c0 + 2/3*c1
            palette = np.array([
                _565_to_rgb(c0),
                _565_to_rgb(c1),
                ((2 * _565_to_rgb(c0).astype(np.int32) + _565_to_rgb(c1).astype(np.int32)) // 3).astype(np.uint8),
                ((_565_to_rgb(c0).astype(np.int32) + 2 * _565_to_rgb(c1).astype(np.int32)) // 3).astype(np.uint8),
            ], dtype=np.int32)  # (4,3)

            # Assign each pixel to nearest palette entry
            diffs = block[:, None, :] - palette[None, :, :]  # (16,4,3)
            dist2 = (diffs * diffs).sum(axis=2)               # (16,4)
            codes = dist2.argmin(axis=1)                       # (16,)

            # Pack 4 rows of 4×2-bit codes into 4 bytes
            packed = 0
            for i, code in enumerate(codes):
                packed |= (int(code) & 3) << (i * 2)

            out += struct.pack('<HHI', c0, c1, packed)
    return bytes(out)


def _rgb_to_565(rgb):
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _565_to_rgb(c565):
    r = (c565 >> 11) & 0x1F
    g = (c565 >> 5)  & 0x3F
    b =  c565        & 0x1F
    # Expand to 8 bits
    return np.array([(r << 3) | (r >> 2),
                     (g << 2) | (g >> 4),
                     (b << 3) | (b >> 2)], dtype=np.uint8)


def _encode_bc5_flat_block() -> bytes:
    """Return one 16-byte BC5 block encoding a flat normal (X=128, Y=128).

    BC5 stores two independent BC4 channels (R and G = X and Y normals).
    Each BC4 channel: 2 endpoint bytes + 6 bytes of 3-bit indices.
    For a flat block all pixels = 128: both endpoints = 128, all indices = 0.
    """
    # BC4 channel: ep0=128, ep1=128, 6 index bytes all zero
    flat_channel = struct.pack('BB', 128, 128) + b'\x00' * 6  # 8 bytes
    return flat_channel + flat_channel  # R channel + G channel = 16 bytes


def _make_flat_bc5_dds(size: int) -> bytes:
    """Build a BC5 DDS with full mipmap chain, all blocks encoding flat normal."""
    DDSD_CAPS        = 0x1
    DDSD_HEIGHT      = 0x2
    DDSD_WIDTH       = 0x4
    DDSD_PIXELFORMAT = 0x1000
    DDSD_LINEARSIZE  = 0x80000
    DDSD_MIPMAPCOUNT = 0x20000
    DDPF_FOURCC      = 0x4
    DDSCAPS_TEXTURE  = 0x1000
    DDSCAPS_MIPMAP   = 0x400000
    DDSCAPS_COMPLEX  = 0x8

    # Count mip levels
    mip_count = 0
    s = size
    while s >= 1:
        mip_count += 1
        if s == 1:
            break
        s //= 2

    # Top mip linear size: BC5 = 16 bytes/block, 1 block per 4x4 pixels
    top_blocks = max(1, size // 4) * max(1, size // 4)
    top_linear_size = top_blocks * 16

    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE | DDSD_MIPMAPCOUNT
    caps  = DDSCAPS_TEXTURE | DDSCAPS_MIPMAP | DDSCAPS_COMPLEX

    hdr  = b'DDS '
    hdr += struct.pack('<I', 124)
    hdr += struct.pack('<I', flags)
    hdr += struct.pack('<I', size)          # height
    hdr += struct.pack('<I', size)          # width
    hdr += struct.pack('<I', top_linear_size)
    hdr += struct.pack('<I', 0)             # depth
    hdr += struct.pack('<I', mip_count)
    hdr += b'\x00' * 44
    # Pixel format: ATI2 / BC5 FourCC
    hdr += struct.pack('<II', 32, DDPF_FOURCC)
    hdr += b'ATI2'                          # BC5 FourCC (same as ATI2N)
    hdr += struct.pack('<IIIII', 0,0,0,0,0)
    hdr += struct.pack('<I', caps)
    hdr += struct.pack('<IIII', 0,0,0,0)
    assert len(hdr) == 128

    flat_block = _encode_bc5_flat_block()
    pixel_data = bytearray()
    s = size
    while s >= 1:
        n_blocks = max(1, s // 4) * max(1, s // 4)
        pixel_data += flat_block * n_blocks
        if s == 1:
            break
        s //= 2

    return bytes(hdr) + bytes(pixel_data)


def _write_flat_normal_dds(path: Path, size: int = TEX_SIZE):
    """Write a flat normal map in BC5/ATI2 format matching vanilla Skyrim terrain LOD.

    Vanilla uses BC5/ATI2 (two-channel, stores X and Y; Z derived in shader).
    Flat normal = (128, 128) in [0,255] for both X and Y channels.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_make_flat_bc5_dds(size))


def _make_bc5_dds_header(size: int, mip_count: int) -> bytes:
    top_blocks = max(1, size // 4) * max(1, size // 4)
    top_linear_size = top_blocks * 16
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | 0x20000
    caps  = 0x1000 | 0x400000 | 0x8
    hdr  = b'DDS '
    hdr += struct.pack('<I', 124)
    hdr += struct.pack('<I', flags)
    hdr += struct.pack('<I', size)
    hdr += struct.pack('<I', size)
    hdr += struct.pack('<I', top_linear_size)
    hdr += struct.pack('<I', 0)
    hdr += struct.pack('<I', mip_count)
    hdr += b'\x00' * 44
    hdr += struct.pack('<II', 32, 0x4)
    hdr += b'ATI2'
    hdr += struct.pack('<IIIII', 0, 0, 0, 0, 0)
    hdr += struct.pack('<I', caps)
    hdr += struct.pack('<IIII', 0, 0, 0, 0)
    assert len(hdr) == 128
    return hdr


def _encode_bc4_block(vals16: np.ndarray) -> bytes:
    """Encode one 4x4 block of a single channel (16 uint8 values) as BC4 (8 bytes)."""
    v = vals16.astype(np.int32)
    r0 = int(v.max())
    r1 = int(v.min())
    if r0 == r1:
        # all equal -> endpoints equal, indices all 0
        return struct.pack('BB', r0, r1) + b'\x00' * 6
    # 8-value interpolation mode (r0 > r1)
    palette = [r0, r1] + [((7 - i) * r0 + i * r1) // 7 for i in range(1, 7)]
    palette = np.array(palette, dtype=np.int32)
    idx = np.abs(v[:, None] - palette[None, :]).argmin(axis=1)
    bits = 0
    for i, code in enumerate(idx):
        bits |= (int(code) & 7) << (3 * i)
    idx_bytes = bits.to_bytes(6, 'little')
    return struct.pack('BB', r0, r1) + idx_bytes


def _write_normal_dds(normal_rgb: np.ndarray, path: Path):
    """Write a real BC5/ATI2 normal map from an RGB normal image.

    BC5 stores two channels: R (=normal X) and G (=normal Y).  Skyrim's landscape
    LOD shader reconstructs Z.  Full mip chain, matching vanilla format.
    """
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(normal_rgb, 'RGB')
    size = img.size[0]

    mip_data = bytearray()
    mip_count = 0
    s = size
    cur = img
    while s >= 1:
        arr = np.asarray(cur.resize((s, s), Image.LANCZOS) if cur.size[0] != s else cur,
                         dtype=np.uint8)
        R = arr[:, :, 0]
        G = arr[:, :, 1]
        # pad to multiple of 4
        ph = (s + 3) & ~3
        pw = (s + 3) & ~3
        Rp = np.zeros((ph, pw), np.uint8); Rp[:s, :s] = R
        Gp = np.zeros((ph, pw), np.uint8); Gp[:s, :s] = G
        for by in range(0, ph, 4):
            for bx in range(0, pw, 4):
                mip_data += _encode_bc4_block(Rp[by:by+4, bx:bx+4].reshape(16))
                mip_data += _encode_bc4_block(Gp[by:by+4, bx:bx+4].reshape(16))
        mip_count += 1
        if s == 1:
            break
        s //= 2

    hdr = _make_bc5_dds_header(size, mip_count)
    path.write_bytes(hdr + bytes(mip_data))


# ---------------------------------------------------------------------------
# NIF writing via pyffi
# ---------------------------------------------------------------------------

def _build_water_node(water_quads, level: int):
    """Build the vanilla-style LOD water node for a tile.

    Vanilla .btr structure (verified against Skyrim.esm terrain meshes):
      root "chunk" child[1] = BSMultiBoundNode named "WATER" (scale 1) holding
      one shape with an independent flat quad per water cell:
        * LOD4:  BSSegmentedTriShape with EXACTLY 16 segments — a fixed 4x4
          grid over the tile (1 cell per segment at LOD4), column-major
          (segment index = sx*4 + sy).  Segments let the engine hide the quad
          for cells that are loaded at full detail.  Per-segment binary layout
          (nif.xml BSGeometrySegmentData, PyFFI's BSSegment fields are
          misaligned over the same 9 bytes):
            flags(byte)=0 | start_index(uint, tri-POINTS, 0 when empty)
            | num_primitives(uint)
          Through PyFFI's fields: internal_index = start_index << 8, and
          num_primitives=2 lands exactly on the bsseg_water bit (2 << 8).
        * LOD8/16/32: plain NiTriShape (no segments — these tiles never
          overlap the loaded-cell area).
      The shape has NO shader property, no UVs, no normals: the engine
      attaches the worldspace LOD water shader itself (WRLD NAM3).  That is
      also why NAM3 must point at a valid WATR record — a null one CTDs.
      Quad verts are local 0..4096 like the land (x scale=level), Z = water
      height / level.  Quads are unshared (4 verts each) so per-cell heights
      can differ.
    """
    cell_local = CELL_SIZE / level
    scale = float(level)

    quad_map = {(cx, cy): wh for cx, cy, wh in water_quads}
    span = max(1, level // 4)   # cells per segment side (4x4 segment grid)

    ordered = []                # quads in segment order, column-major
    seg_num_prims = [0] * 16
    seg_start = [0] * 16
    for sx in range(4):
        for sy in range(4):
            seg = sx * 4 + sy
            n_before = len(ordered)
            for cx in range(sx * span, (sx + 1) * span):
                for cy in range(sy * span, (sy + 1) * span):
                    wh = quad_map.get((cx, cy))
                    if wh is not None:
                        ordered.append((cx, cy, wh))
            count = len(ordered) - n_before
            seg_num_prims[seg] = count * 2
            # start_index in triangle points; vanilla stores 0 for empty segments
            seg_start[seg] = n_before * 6 if count else 0

    verts = []
    tris = []
    for cx, cy, wh in ordered:
        x0 = cx * cell_local
        y0 = cy * cell_local
        z = wh / scale
        b = len(verts)
        verts += [(x0, y0, z), (x0 + cell_local, y0, z),
                  (x0, y0 + cell_local, z), (x0 + cell_local, y0 + cell_local, z)]
        tris += [(b, b + 1, b + 2), (b + 1, b + 3, b + 2)]

    # ---- geometry data ----
    shapedata = NifFormat.NiTriShapeData()
    shapedata.has_vertices = True
    shapedata.has_normals = False
    shapedata.num_uv_sets = 0
    shapedata.has_uv = False
    shapedata.num_vertices = len(verts)
    shapedata.vertices.update_size()
    for i, (x, y, z) in enumerate(verts):
        shapedata.vertices[i].x = x
        shapedata.vertices[i].y = y
        shapedata.vertices[i].z = z
    shapedata.num_triangles = len(tris)
    shapedata.num_triangle_points = len(tris) * 3
    shapedata.has_triangles = True
    shapedata.triangles.update_size()
    for i, (a, b, c) in enumerate(tris):
        shapedata.triangles[i].v_1 = a
        shapedata.triangles[i].v_2 = b
        shapedata.triangles[i].v_3 = c

    # Bounding sphere in LOCAL coords (vanilla: bbox centre, corner radius)
    va = np.array(verts, dtype=np.float64)
    lo = va.min(axis=0)
    hi = va.max(axis=0)
    ctr = (lo + hi) / 2.0
    shapedata.center.x, shapedata.center.y, shapedata.center.z = ctr
    shapedata.radius = float(np.linalg.norm((hi - lo) / 2.0))

    if level == 4:
        shape = NifFormat.BSSegmentedTriShape()
        shape.num_segments = 16
        shape.segment.update_size()
        for i in range(16):
            seg = shape.segment[i]
            # True layout: flags byte (0) | start uint | num_prims uint.
            # PyFFI's misaligned view: internal_index covers flags+start[0:3],
            # its 'flags' bitstruct covers start[3]+num_prims[0:3].
            seg.internal_index = (seg_start[i] << 8) & 0xFFFFFFFF
            seg.flags.bsseg_water = 1 if seg_num_prims[i] else 0
            seg.unknown_byte_1 = 0
    else:
        shape = NifFormat.NiTriShape()
    shape.name = b''
    shape.flags = 14
    shape.scale = scale
    shape.data = shapedata

    # ---- WATER BSMultiBoundNode ----
    whs = [wh for _, _, wh in ordered]
    aabb = NifFormat.BSMultiBoundAABB()
    # XY: bbox of the quads in WORLD units relative to the tile origin.
    aabb.position.x = float(ctr[0] * scale)
    aabb.position.y = float(ctr[1] * scale)
    aabb.extent.x = float((hi[0] - lo[0]) / 2.0 * scale)
    aabb.extent.y = float((hi[1] - lo[1]) / 2.0 * scale)
    # Z: vanilla spans [min height, max(max height, 0)].
    z_lo = min(whs)
    z_hi = max(max(whs), 0.0)
    aabb.position.z = (z_lo + z_hi) / 2.0
    aabb.extent.z = (z_hi - z_lo) / 2.0

    multi_bound = NifFormat.BSMultiBound()
    multi_bound.data = aabb

    wnode = NifFormat.BSMultiBoundNode()
    wnode.name = b'WATER'
    wnode.flags = 14
    wnode.multi_bound = multi_bound
    wnode.num_children = 1
    wnode.children.update_size()
    wnode.children[0] = shape
    return wnode


def _build_terrain_nif(heights: np.ndarray, tile_x: int, tile_y: int,
                       level: int, edid: str, output_dir: Path,
                       water_quads=None) -> bytes:
    """Build a .btr NIF for a terrain tile and return bytes.

    Vertex layout matches vanilla Skyrim terrain LOD:
      - BSMultiBoundNode root named "chunk" (required for Skyrim LOD culling)
      - NiTriShape child named "Land" with scale=level
      - All levels: 33×33 = 1089 verts at local step=128 (matches vanilla ~1056 vert count)
      - heights input is the full-res (level*32+1)² grid
      - Z = world_height / scale  (vertex_z × scale = world_Z in game units)
      - No normals; 1 UV set (all zero) — LOD landscape shader uses world-space texturing
      - Bounding sphere and AABB position use world-space Z
    """
    if not _PYFFI:
        raise RuntimeError("pyffi not available")

    nif_data = NifFormat.Data()
    nif_data.version        = 0x14020007   # 20.2.0.7
    nif_data.user_version   = 12           # Skyrim
    nif_data.user_version_2 = 83           # Skyrim LE
    nif_data.header.endian_type = 1        # little-endian

    # ------------------------------------------------------------------ #
    # Geometry
    # ------------------------------------------------------------------ #
    # All levels: subsample to 33×33 (1089 verts).
    #   Vanilla Skyrim LE LOD4 uses ~1056 verts (decimated), so 33×33 is
    #   comparable and avoids rendering issues from oversized meshes.
    #   LOD8+ full-res (257²=66049) also overflows uint16.
    src_tv = level * 32 + 1
    assert heights.shape == (src_tv, src_tv), \
        f"Expected heights shape ({src_tv},{src_tv}), got {heights.shape}"

    # Subsample to 33×33: stride=level samples indices 0,level,2*level,...,32*level
    # Local tile spans CELL_SIZE = 4096 units; step = 4096/32 = 128
    tv   = 33
    step = CELL_SIZE / (tv - 1)    # 4096/32 = 128 local units/step
    h33  = heights[::level, ::level]   # stride=level → 33×33

    N = tv * tv

    # Triangles: wind so the front face points UP (+Z).  The terrain is a top
    # surface; with X=col, Y=row and Z up, i0->i1->i2 is CCW seen from +Z (front
    # face up).  The previous i0->i2->i1 order was CW = back-facing, so the land
    # rendered only from below / looked transparent from above.
    tris = []
    for row in range(tv - 1):
        for col in range(tv - 1):
            i0 = row * tv + col
            i1 = i0 + 1
            i2 = i0 + tv
            i3 = i2 + 1
            tris.append((i0, i1, i2))
            tris.append((i1, i3, i2))

    world_scale = float(level)

    # ---- NiTriShapeData ----
    shapedata = NifFormat.NiTriShapeData()
    shapedata.has_vertices = True
    shapedata.has_normals  = False   # unused in vanilla terrain LOD
    shapedata.num_uv_sets  = 1       # vanilla BTR has num_uv_sets=1
    shapedata.has_uv       = True    # must be True for PyFFI to allocate UV array
    shapedata.num_vertices = N
    shapedata.vertices.update_size()

    # Use full-res grid for bounding box (accurate Z range), h33 for geometry
    z_min = float(heights.min())
    z_max = float(heights.max())

    for row in range(tv):
        for col in range(tv):
            i = row * tv + col
            shapedata.vertices[i].x = col * step
            shapedata.vertices[i].y = row * step
            # Z stored pre-divided by scale so vertex_z × scale = world_Z
            shapedata.vertices[i].z = float(h33[row, col]) / world_scale

    # UV set — the tile texture maps across the whole tile.  Vanilla ground
    # truth (tamriel.4.0.32.btr): u = x/4096, v = 1 - y/4096 (v=0 at the NORTH
    # edge, matching the DDS row 0 = north).  All-zero UVs made every triangle
    # sample a single texel, so each tile rendered as one flat colour — the
    # in-game map became a hard-edged per-tile checkerboard.
    shapedata.uv_sets.update_size()
    for row in range(tv):
        for col in range(tv):
            i = row * tv + col
            shapedata.uv_sets[0][i].u = col * step / CELL_SIZE
            shapedata.uv_sets[0][i].v = 1.0 - (row * step / CELL_SIZE)

    shapedata.num_triangles       = len(tris)
    shapedata.num_triangle_points = len(tris) * 3
    shapedata.has_triangles       = True
    shapedata.triangles.update_size()
    for i, (a, b, c) in enumerate(tris):
        shapedata.triangles[i].v_1 = a
        shapedata.triangles[i].v_2 = b
        shapedata.triangles[i].v_3 = c

    # Bounding sphere — center and radius in WORLD space (same as AABB).
    # XY world center = CELL_SIZE/2 × level (tile spans 0..CELL_SIZE in local,
    # scaled by level gives 0..CELL_SIZE×level in world).
    z_ctr         = (z_min + z_max) / 2.0
    xy_world_half = CELL_SIZE / 2.0 * world_scale   # e.g. 2048 * 4 = 8192 for L4
    z_world_half  = (z_max - z_min) / 2.0 + 500.0   # extra safety margin
    shapedata.center.x = xy_world_half
    shapedata.center.y = xy_world_half
    shapedata.center.z = z_ctr              # world-space Z centre
    shapedata.radius   = math.sqrt(xy_world_half**2 + xy_world_half**2 + z_world_half**2)

    # ---- Texture set ----
    tex_base = f'textures\\terrain\\{edid}\\{edid}.{level}.{tile_x}.{tile_y}'
    texset = NifFormat.BSShaderTextureSet()
    texset.num_textures = 9
    texset.textures.update_size()
    texset.textures[0] = f'Data\\{tex_base}.dds'.encode()
    texset.textures[1] = f'Data\\{tex_base}_n.dds'.encode()

    # ---- Shader property (landscape LOD) ----
    shader = NifFormat.BSLightingShaderProperty()
    shader.skyrim_shader_type = 18  # kLODLandscapeNoise
    shader.texture_set = texset
    sf1 = shader.shader_flags_1
    sf1.slsf_1_model_space_normals = 1
    sf1.slsf_1_own_emit            = 1
    sf1.slsf_1_z_buffer_test       = 1
    sf2 = shader.shader_flags_2
    sf2.slsf_2_lod_landscape  = 1
    sf2.slsf_2_z_buffer_write = 1
    # uv_scale must be (1,1) — pyffi defaults to (0,0) which breaks the LOD shader
    shader.uv_scale.u = 1.0
    shader.uv_scale.v = 1.0

    # ---- NiTriShape ----
    shape = NifFormat.NiTriShape()
    shape.name  = b'land'
    shape.flags = 14
    shape.scale = float(level)
    shape.data  = shapedata
    shape.bs_properties[0] = shader

    # ---- BSMultiBoundNode root ----
    world_half = CELL_SIZE * level / 2.0
    z_extent   = (z_max - z_min) / 2.0 + 500.0

    aabb = NifFormat.BSMultiBoundAABB()
    aabb.position.x = world_half
    aabb.position.y = world_half
    aabb.position.z = z_ctr
    aabb.extent.x   = world_half
    aabb.extent.y   = world_half
    aabb.extent.z   = z_extent

    multi_bound = NifFormat.BSMultiBound()
    multi_bound.data = aabb

    root = NifFormat.BSMultiBoundNode()
    root.name         = b'chunk'
    root.flags        = 14
    root.multi_bound  = multi_bound

    # Water: child[1] BSMultiBoundNode "WATER" (vanilla structure).  The engine
    # textures it with the worldspace LOD water shader (WRLD NAM3).
    if water_quads:
        water_node = _build_water_node(water_quads, level)
        root.num_children = 2
        root.children.update_size()
        root.children[0] = shape
        root.children[1] = water_node
    else:
        root.num_children = 1
        root.children.update_size()
        root.children[0] = shape

    nif_data.roots = [root]

    buf = io.BytesIO()
    nif_data.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Diffuse tile compositing + heightmap normal maps
# ---------------------------------------------------------------------------

# Per-cell pixel resolution when compositing the diffuse atlas.  A level-N tile
# is N cells per side, so the atlas is N*CELL_DIFFUSE_PX per side; clamped to the
# per-level TEX_SIZE on write.
CELL_DIFFUSE_PX = 64


_EMPTY_LAYERS = {'base': {}, 'alpha': {}}


def _composite_tile_diffuse(lands, tile_x, tile_y, level, ltex_map, tex_root,
                            tile_heights, cell_water, default_wh):
    """Composite a level-N tile diffuse from its cells' real landscape textures.

    tile_heights is the FILLED tile height grid from _assemble_tile (row 0 =
    south), used to bake the underwater murk.  Cells with no LAND record get
    the engine default texture + murk instead of a flat fill colour.

    Returns (atlas RGB ndarray, side_px) with image row 0 = north (+Y), so it
    matches the DDS orientation vanilla terrain LOD uses.
    """
    from .terrain_lod_textures import composite_cell
    side = level * CELL_DIFFUSE_PX
    atlas = np.empty((side, side, 3), dtype=np.uint8)
    for cy in range(level):
        for cx in range(level):
            key = (tile_x + cx, tile_y + cy)
            land = lands.get(key)
            layers = land['layers'] if land is not None else _EMPTY_LAYERS
            colors = land.get('colors') if land is not None else None
            # 33x33 height patch for this cell from the filled tile grid
            h33 = tile_heights[cy*32:cy*32+33, cx*32:cx*32+33]
            wh = _cell_water_height(cell_water, key, default_wh)
            img = composite_cell(layers, colors,
                                 ltex_map, tex_root, tile_x + cx, tile_y + cy,
                                 cell_px=CELL_DIFFUSE_PX, tex_size=128,
                                 heights=h33, water_height=wh)
            col0 = cx * CELL_DIFFUSE_PX
            # north (+Y, higher cy) at the TOP of the image
            row0 = (level - 1 - cy) * CELL_DIFFUSE_PX
            atlas[row0:row0+CELL_DIFFUSE_PX, col0:col0+CELL_DIFFUSE_PX] = img
    return atlas, side


def _heightmap_normal_rgb(heights: np.ndarray, out_px: int) -> np.ndarray:
    """Derive a tangent-space normal map (RGB uint8) from a height grid.

    Skyrim terrain-LOD normal maps encode the surface normal so distant terrain
    is lit; a flat normal leaves the LOD looking unlit.  heights is in game
    units; we resize to out_px and take the gradient.
    """
    from PIL import Image
    # heights row 0 = SOUTH (LAND convention); the diffuse tile is written with
    # image row 0 = NORTH, and the normal map shares its UVs — flip to match.
    hh = np.flipud(np.nan_to_num(heights.astype(np.float32)))
    im = Image.fromarray(hh).resize((out_px, out_px), Image.BILINEAR)
    hh = np.asarray(im, dtype=np.float32)
    # world-space spacing between output samples (game units)
    span = CELL_SIZE * (heights.shape[0] - 1) / 32.0  # tile world span
    dpx = span / out_px
    grow, gx = np.gradient(hh, dpx)
    # image rows run north→south, so ∂h/∂y_world = -∂h/∂row
    gy = -grow
    nz = np.ones_like(gx)
    nx, ny, nzz = -gx, -gy, nz
    norm = np.sqrt(nx*nx + ny*ny + nzz*nzz) + 1e-6
    nx, ny, nzz = nx/norm, ny/norm, nzz/norm
    rgb = np.stack([(nx*0.5+0.5), (ny*0.5+0.5), (nzz*0.5+0.5)], axis=-1)
    return np.clip(rgb*255, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Per-tile worker — pool initializer + task function
# ---------------------------------------------------------------------------

# Per-process global set by _worker_init; avoids pickling lands on every task.
_worker_lands      = None
_worker_mesh_dir   = None
_worker_tex_dir    = None
_worker_ltex_map   = None
_worker_tex_root   = None
_worker_cell_water = None
_worker_default_wh = 0.0


def _worker_init(lands, mesh_dir_s, tex_dir_s, ltex_map, tex_root_s,
                 cell_water, default_wh):
    """Called once per worker process to stash shared read-only data."""
    global _worker_lands, _worker_mesh_dir, _worker_tex_dir
    global _worker_ltex_map, _worker_tex_root
    global _worker_cell_water, _worker_default_wh
    _worker_lands      = lands
    _worker_mesh_dir   = Path(mesh_dir_s)
    _worker_tex_dir    = Path(tex_dir_s)
    _worker_ltex_map   = ltex_map
    _worker_tex_root   = Path(tex_root_s)
    _worker_cell_water = cell_water
    _worker_default_wh = default_wh


def _process_tile(args):
    """Worker task for one tile.  lands/dirs come from the process global.

    args: (tile_x, tile_y, level, worldspace_edid)
    Returns (tag, ok, error_msg).
    """
    tile_x, tile_y, level, worldspace_edid = args
    tag = f'{worldspace_edid}.{level}.{tile_x}.{tile_y}'

    try:
        heights, colors = _assemble_tile(_worker_lands, tile_x, tile_y, level)

        water_quads = _tile_water_quads(_worker_lands, _worker_cell_water,
                                        tile_x, tile_y, level, _worker_default_wh)

        output_dir = _worker_mesh_dir.parent.parent.parent
        nif_bytes  = _build_terrain_nif(heights, tile_x, tile_y, level,
                                        worldspace_edid, output_dir,
                                        water_quads=water_quads)
        (_worker_mesh_dir / f'{tag}.btr').write_bytes(nif_bytes)

        tex_size = TEX_SIZE_BY_LEVEL.get(level, TEX_SIZE)

        # Diffuse: composite real landscape textures per LAND alpha layers.
        atlas, _side = _composite_tile_diffuse(
            _worker_lands, tile_x, tile_y, level,
            _worker_ltex_map, _worker_tex_root,
            heights, _worker_cell_water, _worker_default_wh)
        _write_dds_dxt1(atlas, _worker_tex_dir / f'{tag}.dds', size=tex_size)

        # Normal map: derive from the tile heightmap so distant terrain is lit.
        # Baked at half the diffuse resolution (BC5 is 2x DXT1/texel).
        normal_size = max(64, tex_size // NORMAL_SIZE_DIVISOR)
        normal_rgb = _heightmap_normal_rgb(heights, normal_size)
        _write_normal_dds(normal_rgb, _worker_tex_dir / f'{tag}_n.dds')

        return tag, True, None
    except Exception as e:
        import traceback
        return tag, False, f"{e}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def generate_terrain_lod(esm_path: Path, output_dir: Path,
                         worldspace_edid: str = 'TES4Tamriel') -> bool:
    """Generate terrain LOD (.btr + .dds) for all cells in the worldspace.

    Tile generation is parallelised across (cpu_count - 2) processes.

    Args:
        esm_path:        Path to the converted ESM.
        output_dir:      Per-plugin output directory (output/Oblivion.esm/).
        worldspace_edid: EditorID of the worldspace.

    Returns True on success.
    """
    try:
        __import__('PIL')
    except ImportError:
        print("  ERROR: Pillow not installed — pip install Pillow")
        return False

    if not _PYFFI:
        print("  ERROR: pyffi not available")
        return False

    print(f"\n[TerrainLOD] Parsing LAND records from {esm_path.name}...")
    lands, cell_water, default_wh = _parse_land_records(esm_path, worldspace_edid)
    if not lands:
        print("  No LAND records found.")
        return False
    n_water = sum(1 for hw, _ in cell_water.values() if hw)
    print(f"  Found {len(lands)} LAND records; {n_water} water cells "
          f"(default water height {default_wh}).")

    # Determine cell bounds
    all_x = [k[0] for k in lands]
    all_y = [k[1] for k in lands]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    print(f"  Cell range: X=[{min_x},{max_x}] Y=[{min_y},{max_y}]")

    mesh_dir = output_dir / 'meshes' / 'terrain' / worldspace_edid
    tex_dir  = output_dir / 'textures' / 'terrain' / worldspace_edid
    mesh_dir.mkdir(parents=True, exist_ok=True)
    tex_dir.mkdir(parents=True, exist_ok=True)

    # Resolve LTEX FormID -> landscape diffuse/normal .dds for the compositor.
    from .terrain_lod_textures import build_ltex_texture_map
    ltex_map = build_ltex_texture_map(esm_path)
    tex_root = output_dir / 'textures'
    print(f"  Resolved {len(ltex_map)} LTEX landscape textures.")

    # Number of worker processes: cpu_count - 2, minimum 1
    n_workers = max(1, (os.cpu_count() or 2) - 2)
    print(f"  Using {n_workers} worker process(es).")

    total_tiles = 0
    for level in LOD_LEVELS:
        # Align SW corner to tile grid
        tx_start = (min_x // level) * level
        ty_start = (min_y // level) * level
        tx_end   = ((max_x + level - 1) // level) * level
        ty_end   = ((max_y + level - 1) // level) * level

        # Build work items for tiles that have at least one LAND cell.
        # Only tile coords are passed per-task; lands is sent once via initializer.
        work = []
        for ty in range(ty_start, ty_end, level):
            for tx in range(tx_start, tx_end, level):
                if any((tx + cx, ty + cy) in lands
                       for cy in range(level) for cx in range(level)):
                    work.append((tx, ty, level, worldspace_edid))

        if not work:
            print(f"  LOD {level}: 0 tiles")
            continue

        tile_count = 0
        warn_count = 0
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_worker_init,
            initargs=(lands, str(mesh_dir), str(tex_dir), ltex_map, str(tex_root),
                      cell_water, default_wh),
        ) as pool:
            for tag, ok, err in pool.map(_process_tile, work):
                if ok:
                    tile_count += 1
                else:
                    warn_count += 1
                    print(f"  WARNING: {tag}: {err}")

        msg = f"  LOD {level}: {tile_count} tiles"
        if warn_count:
            msg += f" ({warn_count} failed)"
        print(msg)
        total_tiles += tile_count

    print(f"[TerrainLOD] Done — {total_tiles} tiles generated.")
    return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Generate terrain LOD for a converted TES5 plugin')
    parser.add_argument('esm', help='Path to converted ESM/ESP')
    parser.add_argument('output_dir', help='Plugin output directory')
    parser.add_argument('--worldspace', default='TES4Tamriel')
    args = parser.parse_args()
    generate_terrain_lod(Path(args.esm), Path(args.output_dir), args.worldspace)
