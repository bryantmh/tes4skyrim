"""Terrain LOD diffuse texture compositing for TES4->TES5 worldspaces.

The old terrain LOD wrote the per-tile diffuse .dds straight from the LAND
VCLR vertex colours upscaled to 1024 — a blurry colour grid, not the actual
ground.  Vanilla Skyrim terrain LOD diffuse is a *render* of the real landscape
textures, alpha-blended exactly as the near landscape shader blends them, then
modulated by the vertex-colour shading and baked to a single texture per tile.

This module reproduces that:

  LAND record  --BTXT/ATXT--> LTEX FormID
  LTEX (output ESM) --TNAM--> TXST --TX00--> tes4\\landscape\\<name>.dds

Each LAND cell has 4 quadrants (BL, BR, TL, TR), each a 17x17 vertex grid
(quadrants share their centre row/column).  A quadrant has one BASE layer
(BTXT, opaque) plus up to N ALPHA layers (ATXT+VTXT), where VTXT gives a
per-vertex opacity at position index (row*17+col within the quadrant).

We render each cell to an RGB image by:
  * tiling every referenced landscape .dds across the cell in world UV
    (Skyrim landscape UV repeats the diffuse once every 2 cells; Oblivion
    ground textures were authored to tile the same way),
  * starting from the base layer, then compositing each alpha layer using its
    bilinearly-upsampled opacity grid,
  * multiplying by the vertex-colour luminance (VCLR) for baked terrain shading.

The result is downsampled per LOD level into the tile diffuse atlas.
"""

import struct
from pathlib import Path

import numpy as np

# 4096 game units per cell; landscape diffuse repeats every 2 cells in Skyrim.
# Oblivion authored the same, so one .dds spans a 2x2 cell region at UV [0,1].
TILE_REPEAT_CELLS = 2.0
QUAD_VERTS = 17            # vertices per quadrant side (33 per cell = 2*17-1)

# Per-cell composite resolution.  Each cell contributes this many pixels to the
# tile atlas; keep modest so a 32x32-cell tile stays a sane texture size.
CELL_PX = 64

# Engine default for quadrants with no BTXT base layer.  Oblivion renders
# unpainted land with Landscape\Default.dds; ~23% of Tamriel quadrants
# (mostly sea floor) have no base layer, and the old grey-128 fallback painted
# them as huge flat grey areas in the distant LOD.
DEFAULT_LAND_TEXTURE = 'tes4\\landscape\\default.dds'

# Baked underwater murk.  Vanilla terrain LOD diffuse bakes submerged terrain
# toward a flat murky colour (the LOD water sheet drawn above it is nearly
# opaque-looking only up close).  Blend by depth below the cell water height.
MURK_COLOR = np.array([54.0, 66.0, 62.0], dtype=np.float32)
MURK_FULL_DEPTH = 512.0    # game units below water at which murk saturates
MURK_MAX = 0.9             # never fully hide the ground texture


# ---------------------------------------------------------------------------
# Output-ESM parsing: LTEX FormID -> diffuse/normal texture path
# ---------------------------------------------------------------------------

def _sub(body: bytes, tag: bytes):
    p = 0
    n = len(body)
    while p + 6 <= n:
        s = body[p:p+4]
        sz = struct.unpack_from('<H', body, p+4)[0]
        if s == tag:
            return body[p+6:p+6+sz]
        p += 6 + sz
    return None


def _zstr(b) -> str:
    if b is None:
        return ''
    return bytes(b).rstrip(b'\x00').decode('latin-1', errors='replace')


def _iter_records(raw: bytes):
    """Yield (sig, fid, body) for every record, descending into groups.

    Handles compressed records transparently.
    """
    import zlib
    n = len(raw)
    hdr_size = struct.unpack_from('<I', raw, 4)[0]
    stack = [(24 + hdr_size, n)]
    while stack:
        p, end = stack.pop()
        while p < end and p + 24 <= n:
            sig = raw[p:p+4]
            if sig == b'GRUP':
                g_size = struct.unpack_from('<I', raw, p+4)[0]
                stack.append((p+24, p+g_size))
                p += g_size
                continue
            data_size = struct.unpack_from('<I', raw, p+4)[0]
            flags = struct.unpack_from('<I', raw, p+8)[0]
            fid = struct.unpack_from('<I', raw, p+12)[0]
            body = raw[p+24:p+24+data_size]
            if flags & 0x00040000 and len(body) >= 4:
                try:
                    body = zlib.decompress(body[4:])
                except Exception:
                    pass
            yield sig.decode('latin-1', 'replace'), fid, body
            p += 24 + data_size


def build_ltex_texture_map(esm_path: Path) -> dict:
    """Return {LTEX FormID(int) -> {'diffuse': path, 'normal': path}}.

    Paths are relative to the Data folder (e.g. 'tes4\\landscape\\foo.dds').
    Resolved via LTEX.TNAM -> TXST.TX00/TX01.
    """
    raw = Path(esm_path).read_bytes()
    txst = {}   # TXST FormID -> (tx00, tx01)
    ltex_tnam = {}  # LTEX FormID -> TXST FormID
    for sig, fid, body in _iter_records(raw):
        if sig == 'TXST':
            tx00 = _zstr(_sub(body, b'TX00'))
            tx01 = _zstr(_sub(body, b'TX01'))
            txst[fid] = (tx00, tx01)
        elif sig == 'LTEX':
            tnam = _sub(body, b'TNAM')
            if tnam and len(tnam) >= 4:
                ltex_tnam[fid] = struct.unpack_from('<I', tnam)[0]

    out = {}
    for lfid, tfid in ltex_tnam.items():
        tx00, tx01 = txst.get(tfid, ('', ''))
        out[lfid] = {'diffuse': tx00, 'normal': tx01}
    return out


# ---------------------------------------------------------------------------
# LAND layer decode (from output-ESM binary body)
# ---------------------------------------------------------------------------

def decode_land_layers(body: bytes) -> dict:
    """Decode BTXT/ATXT/VTXT into per-quadrant layer data.

    Returns:
      {
        'base':  {quad: ltex_fid},
        'alpha': {quad: [ (ltex_fid, opacity_grid 17x17 float32), ... ]},
      }
    quad is 0..3 (BL, BR, TL, TR).  Opacity grid indexed [row, col] within the
    17x17 quadrant vertex grid.
    """
    base = {}
    alpha = {}          # quad -> [(layer_idx, ltex_fid, grid), ...]

    p = 0
    n = len(body)
    pending_atxt = None  # (ltex_fid, quad) awaiting its VTXT
    while p + 6 <= n:
        tag = body[p:p+4]
        sz = struct.unpack_from('<H', body, p+4)[0]
        val = body[p+6:p+6+sz]
        if tag == b'BTXT' and len(val) >= 6:
            tex, quad = struct.unpack_from('<IB', val)
            base[quad] = tex
            pending_atxt = None
        elif tag == b'ATXT' and len(val) >= 8:
            tex, quad, _unused, layer = struct.unpack_from('<IBBH', val)
            pending_atxt = (tex, quad)
            # opacity grid defaults to 0
            grid = np.zeros((QUAD_VERTS, QUAD_VERTS), dtype=np.float32)
            alpha.setdefault(quad, []).append((layer, tex, grid))
        elif tag == b'VTXT' and pending_atxt is not None:
            tex, quad = pending_atxt
            grid = alpha[quad][-1][2]
            cnt = len(val) // 8
            for i in range(cnt):
                pos, _u, op = struct.unpack_from('<HHf', val, i*8)
                if pos < QUAD_VERTS * QUAD_VERTS:
                    grid[pos // QUAD_VERTS, pos % QUAD_VERTS] = op
            pending_atxt = None
        p += 6 + sz

    # Blend order is the ATXT layer index, not file order.
    alpha_sorted = {q: [(t, g) for _l, t, g in sorted(lst, key=lambda e: e[0])]
                    for q, lst in alpha.items()}
    return {'base': base, 'alpha': alpha_sorted}


# ---------------------------------------------------------------------------
# Texture loading (DDS -> RGB ndarray), cached
# ---------------------------------------------------------------------------

_TEX_CACHE = {}


def _load_texture_rgb(rel_path: str, tex_root: Path, size: int = 64):
    """Load a landscape .dds as an (size,size,3) uint8 RGB tile, cached.

    Returns a neutral grey tile if the texture can't be found/decoded.
    """
    key = (rel_path.lower(), size)
    if key in _TEX_CACHE:
        return _TEX_CACHE[key]

    img = None
    if rel_path:
        rp = rel_path.replace('/', '\\').lstrip('\\')
        if rp.lower().startswith('textures\\'):
            rp = rp[len('textures\\'):]
        fpath = tex_root / rp
        if fpath.exists():
            try:
                from PIL import Image
                im = Image.open(fpath).convert('RGB').resize((size, size), Image.LANCZOS)
                img = np.asarray(im, dtype=np.uint8)
            except Exception:
                img = None

    if img is None:
        img = np.full((size, size, 3), 128, dtype=np.uint8)

    _TEX_CACHE[key] = img
    return img


# ---------------------------------------------------------------------------
# Per-cell compositing
# ---------------------------------------------------------------------------

def _upsample_opacity(grid17: np.ndarray, out_px: int) -> np.ndarray:
    """Bilinearly upsample a 17x17 opacity grid to (out_px, out_px), flipping
    to image orientation (grid row 0 = SOUTH, image row 0 = NORTH)."""
    from PIL import Image
    im = Image.fromarray((np.clip(np.flipud(grid17), 0, 1) * 255).astype(np.uint8), 'L')
    im = im.resize((out_px, out_px), Image.BILINEAR)
    return np.asarray(im, dtype=np.float32) / 255.0


def _sample_tiled(rgb_tile: np.ndarray, us: np.ndarray, vs: np.ndarray) -> np.ndarray:
    """Sample rgb_tile (tiling/wrapping) at world UV columns `us`, rows `vs`."""
    ts = rgb_tile.shape[0]
    px = ((us % 1.0) * ts).astype(np.int32) % ts
    py = ((vs % 1.0) * ts).astype(np.int32) % ts
    return rgb_tile[np.ix_(py, px)]


def composite_cell(layers: dict, colors: np.ndarray, ltex_map: dict,
                   tex_root: Path, cell_gx: int, cell_gy: int,
                   cell_px: int = CELL_PX, tex_size: int = 128,
                   heights: np.ndarray = None,
                   water_height: float = None) -> np.ndarray:
    """Composite one LAND cell into an (cell_px, cell_px, 3) uint8 RGB image.

    Quadrant layout in the image (row 0 = top = +Y = north):
      TL(2) TR(3)
      BL(0) BR(1)
    layers: from decode_land_layers.  colors: (33,33,3) uint8 VCLR shading
    (row 0 = south).  heights: (33,33) float32 cell heights (row 0 = south),
    used with water_height to bake the underwater murk.
    """
    base = layers['base']
    alpha = layers['alpha']
    quad_px = cell_px // 2
    out = np.zeros((cell_px, cell_px, 3), dtype=np.float32)

    # World-UV sample grids for the whole cell image.  The cell spans
    # 1/TILE_REPEAT_CELLS of the texture; origins from world cell coords so
    # neighbouring cells line up seamlessly.  Image row 0 is the cell's NORTH
    # edge, so v must DECREASE as the row index grows — sampling with
    # ascending v mirrored every quadrant vertically and broke texture
    # continuity at each quadrant boundary (the horizontal banding bug).
    uv_cell = 1.0 / TILE_REPEAT_CELLS
    us = (cell_gx + (np.arange(cell_px) + 0.5) / cell_px) * uv_cell
    vs = (cell_gy + 1.0 - (np.arange(cell_px) + 0.5) / cell_px) * uv_cell

    # image (row,col) block for each quad: (row_slice, col_slice)
    # top row = TL,TR ; bottom row = BL,BR
    quad_blocks = {
        2: (slice(0, quad_px),          slice(0, quad_px)),           # TL
        3: (slice(0, quad_px),          slice(quad_px, cell_px)),     # TR
        0: (slice(quad_px, cell_px),    slice(0, quad_px)),           # BL
        1: (slice(quad_px, cell_px),    slice(quad_px, cell_px)),     # BR
    }

    for quad in range(4):
        rs, cs = quad_blocks[quad]
        q_us = us[cs]
        q_vs = vs[rs]

        # base layer; quadrants with no BTXT use the engine default texture
        base_fid = base.get(quad)
        diff = ltex_map.get(base_fid, {}).get('diffuse', '') if base_fid else ''
        btile = _load_texture_rgb(diff or DEFAULT_LAND_TEXTURE, tex_root, tex_size)
        quad_img = _sample_tiled(btile, q_us, q_vs).astype(np.float32)

        # alpha layers, in ATXT layer order
        for (lfid, grid17) in alpha.get(quad, []):
            diff = ltex_map.get(lfid, {}).get('diffuse', '')
            if not diff:
                continue
            atile = _load_texture_rgb(diff, tex_root, tex_size)
            atex = _sample_tiled(atile, q_us, q_vs).astype(np.float32)
            op = _upsample_opacity(grid17, quad_px)[:, :, None]
            quad_img = quad_img * (1.0 - op) + atex * op

        out[rs, cs] = quad_img

    # Modulate by VCLR luminance shading (baked AO / lighting).  colors is 33x33
    # with row 0 = south — flip to image orientation.  VCLR is a light map
    # centred ~0.5 = neutral (x2 = unshaded).  Applying the full x2 range
    # produced hard cell seams (per-cell VCLR discontinuities) and crushed
    # shadows, so blend the shading only partway toward neutral.
    if colors is not None:
        from PIL import Image
        shade = Image.fromarray(np.flipud(colors).copy(), 'RGB').resize(
            (cell_px, cell_px), Image.BILINEAR)
        shade = np.asarray(shade, dtype=np.float32) / 255.0
        lum = shade.mean(axis=2, keepdims=True) * 2.0          # 0..2, 1=neutral
        SHADE_STRENGTH = 0.4                                    # 0=off, 1=full
        mult = 1.0 + (lum - 1.0) * SHADE_STRENGTH
        out = np.clip(out * mult, 0, 255)

    # Bake the underwater murk: blend submerged pixels toward a flat murky
    # colour by depth, like vanilla LOD diffuse (the LOD water sheet alone is
    # too translucent to hide raw seafloor texture at distance).
    if water_height is not None and heights is not None:
        from PIL import Image
        himg = Image.fromarray(np.flipud(np.nan_to_num(
            heights.astype(np.float32)))).resize((cell_px, cell_px), Image.BILINEAR)
        depth = float(water_height) - np.asarray(himg, dtype=np.float32)
        a = np.clip(depth / MURK_FULL_DEPTH, 0.0, 1.0)[:, :, None] * MURK_MAX
        out = out * (1.0 - a) + MURK_COLOR[None, None, :] * a

    # Note: image row 0 is +Y (north); callers assemble tiles top-down.
    return np.clip(out, 0, 255).astype(np.uint8)
