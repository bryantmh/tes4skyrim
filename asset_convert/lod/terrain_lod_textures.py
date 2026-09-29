"""Terrain LOD diffuse texture compositing for TES4->TES5 worldspaces.

The old terrain LOD wrote the per-tile diffuse .dds straight from the LAND
VCLR vertex colors upscaled to 1024 — a blurry color grid, not the actual
ground.  Vanilla Skyrim terrain LOD diffuse is a *render* of the real landscape
textures, alpha-blended exactly as the near landscape shader blends them, then
modulated by the vertex-color shading and baked to a single texture per tile.

This module reproduces that:

  LAND record  --BTXT/ATXT--> LTEX FormID
  LTEX (output ESM) --TNAM--> TXST --TX00--> tes4\\landscape\\<name>.dds

Each LAND cell has 4 quadrants (BL, BR, TL, TR), each a 17x17 vertex grid
(quadrants share their center row/column).  A quadrant has one BASE layer
(BTXT, opaque) plus up to N ALPHA layers (ATXT+VTXT), where VTXT gives a
per-vertex opacity at position index (row*17+col within the quadrant).

We render each cell to an RGB image by:
  * tiling every referenced landscape .dds across the cell in world UV
    (Skyrim landscape UV repeats the diffuse once every 2 cells; Oblivion
    ground textures were authored to tile the same way),
  * starting from the base layer, then compositing each alpha layer using its
    bilinearly-upsampled opacity grid,
  * multiplying by the vertex-color luminance (VCLR) for baked terrain shading.

The result is downsampled per LOD level into the tile diffuse atlas.
"""

from asset_convert.game_paths import current_namespace
import struct
from collections import Counter
from pathlib import Path

import numpy as np

from asset_convert.case_paths import resolve, split_rel
from output_layout import plugin_out_root
from tes5_import.base.tes5_reader import masters, records

# 4096 game units per cell; landscape diffuse repeats every 2 cells in Skyrim.
# Oblivion authored the same, so one .dds spans a 2x2 cell region at UV [0,1].
TILE_REPEAT_CELLS = 2.0
QUAD_VERTS = 17            # vertices per quadrant side (33 per cell = 2*17-1)

# Per-cell composite resolution.  Each cell contributes this many pixels to the
# tile atlas; keep modest so a 32x32-cell tile stays a sane texture size.
CELL_PX = 64

# Baked underwater murk.  Vanilla terrain LOD diffuse bakes submerged terrain
# toward a flat murky color (the LOD water sheet drawn above it is nearly
# opaque-looking only up close).  Blend by depth below the cell water height.
MURK_COLOR = np.array([54.0, 66.0, 62.0], dtype=np.float32)
MURK_FULL_DEPTH = 512.0    # game units below water at which murk saturates
MURK_MAX = 0.9             # never fully hide the ground texture

#: Fraction of the VCLR light map applied (0=off, 1=full x2 range).
VCLR_SHADE_STRENGTH = 0.4


# ---------------------------------------------------------------------------
# Output-ESM parsing: LTEX FormID -> diffuse/normal texture path
# ---------------------------------------------------------------------------


def default_land_texture() -> str:
    """Unpainted-quadrant diffuse, under the ACTIVE game namespace."""
    return current_namespace() + '\\landscape\\default.dds'

# Keyed on (path, mtime_ns, size) like lod_gen._PARSED_ESM_CACHE. The result is
# a pure function of the file and carries NO worldspace scoping, yet the terrain
# stage rebuilt it once per worldspace (plus once per overlay, per worldspace) —
# 18 full reads of a 613 MB ESM for Oblivion.esm alone. Bounded by the number of
# plugins in one run, so it does not grow with worldspace count.
_LTEX_MAP_CACHE: dict = {}


def _ltex_tables(esm_path: Path) -> tuple:
    """One plugin's ({LTEX -> TNAM}, {TXST -> (TX00, TX01)}), load-order keyed.

    Every id is re-stamped through `formid_remap_table`, so a LAND layer, an
    LTEX and the TXST it names compare across files. Memoised per file.
    """
    from asset_convert.lod.esm_scan import formid_remap_table
    esm_path = Path(esm_path)
    try:
        st = esm_path.stat()
        key = (str(esm_path).lower(), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    hit = None if key is None else _LTEX_MAP_CACHE.get(key)
    if hit is not None:
        return hit

    gmap = formid_remap_table(esm_path)

    def g(fid):
        """This file's id re-stamped with its global index byte."""
        return gmap[fid >> 24] | (fid & 0x00FFFFFF)

    txst = {}
    ltex_tnam = {}
    for rec in records(esm_path.read_bytes(), b'TXST', b'LTEX'):
        if rec.sig == b'TXST':
            txst[g(rec.form_id)] = (rec.string(b'TX00'), rec.string(b'TX01'))
            continue
        tnam = rec.sub(b'TNAM')
        if tnam and len(tnam) >= 4:
            ltex_tnam[g(rec.form_id)] = g(struct.unpack_from('<I', tnam)[0])
    if key is not None:
        _LTEX_MAP_CACHE[key] = (ltex_tnam, txst)
    return ltex_tnam, txst


def _with_masters(esm_paths) -> list:
    """The listed plugins, each preceded by its converted masters, in order.

    A converted master sits at `output/<plugin>/<plugin>`; one that was never
    converted (Skyrim.esm) is skipped. Morroblivion's terrain names Oblivion's
    own land textures, which only Oblivion.esm's records resolve.
    """
    out = []
    for path in esm_paths:
        path = Path(path)
        out_root = path.parent.parent
        export_dir = out_root.parent / 'export'
        for name in masters(path.read_bytes()):
            master = plugin_out_root(
                out_root, name, export_dir if export_dir.is_dir() else None
            ) / name
            if master.is_file() and master not in out:
                out.append(master)
        if path not in out:
            out.append(path)
    return out


def build_ltex_texture_map(esm_paths) -> dict:
    """Return {LTEX FormID(int) -> {'diffuse': path, 'normal': path}}.

    `esm_paths` is one plugin or the owner followed by its overlays in load
    order; each file's converted masters are read ahead of it. Keys are
    load-order-normalized ids, and TNAM resolves against the TXST records of
    EVERY file read: an override plugin re-emits an LTEX
    whose texture set lives in the master, so resolving per file left the
    override with an empty diffuse that composited grey.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-texture-lookup
    """
    if isinstance(esm_paths, (str, Path)):
        esm_paths = [esm_paths]
    ltex_tnam = {}
    txst = {}
    for path in _with_masters(esm_paths):
        own_ltex, own_txst = _ltex_tables(path)
        ltex_tnam.update(own_ltex)
        txst.update(own_txst)
    out = {}
    for lfid, tfid in ltex_tnam.items():
        tx00, tx01 = txst.get(tfid, ('', ''))
        out[lfid] = {'diffuse': tx00, 'normal': tx01}
    return out


# ---------------------------------------------------------------------------
# LAND layer decode (from output-ESM binary body)
# ---------------------------------------------------------------------------

def _fill_vtxt(grid: np.ndarray, val: bytes) -> None:
    """Write one VTXT run of (position:u16, unused:u16, opacity:f32) into `grid`.

    A structured dtype reads the whole array at once: the per-entry unpack it
    replaces was the hottest thing in the LAND parse, 14.7M calls across
    Tamriel's 14,686 records. Later entries win, as sequential assignment did.
    """
    cnt = len(val) // 8
    if not cnt:
        return
    rec = np.frombuffer(val, count=cnt, dtype=np.dtype(
        [('pos', '<u2'), ('u', '<u2'), ('op', '<f4')]))
    pos = rec['pos']
    keep = pos < QUAD_VERTS * QUAD_VERTS
    ops = rec['op']
    if not keep.all():
        pos = pos[keep]
        ops = ops[keep]
    grid[pos // QUAD_VERTS, pos % QUAD_VERTS] = ops


def decode_land_layers(body: bytes, remap=None) -> dict:
    """Decode BTXT/ATXT/VTXT into per-quadrant layer data.

    `remap` re-stamps each LTEX id into load-order space, matching the keys
    `build_ltex_texture_map` writes; None keeps the file's raw ids.

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
            base[quad] = remap(tex) if remap else tex
            pending_atxt = None
        elif tag == b'ATXT' and len(val) >= 8:
            tex, quad, _unused, layer = struct.unpack_from('<IBBH', val)
            tex = remap(tex) if remap else tex
            pending_atxt = (tex, quad)
            # opacity grid defaults to 0
            grid = np.zeros((QUAD_VERTS, QUAD_VERTS), dtype=np.float32)
            alpha.setdefault(quad, []).append((layer, tex, grid))
        elif tag == b'VTXT' and pending_atxt is not None:
            _fill_vtxt(alpha[pending_atxt[1]][-1][2], val)
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

#: (lowercase texture path, outcome) -> lookups in this process, cache hits included.
_TEX_USES = Counter()

#: Case-paths site name for landscape texture lookups.
_TEX_SITE = 'terrain_lod_texture'


def _texture_rel(rel_path: str) -> str:
    """`rel_path` relative to a textures/ root, backslash form."""
    rp = (rel_path or '').replace('/', '\\').lstrip('\\')
    if rp.lower().startswith('textures\\'):
        rp = rp[len('textures\\'):]
    return rp


def _find_texture(rp: str, roots) -> tuple:
    """(path, 'exact'|'resolved') for `rp`, roots searched in order; (None, 'missing')."""
    hit = resolve(roots, rp, site=_TEX_SITE)
    if hit is None:
        return None, 'missing'
    parts = split_rel(rp)
    exact = any(hit == Path(r).joinpath(*parts) for r in roots)
    return hit, 'exact' if exact else 'resolved'


def _decode_rgb(fpath, size: int):
    """`fpath` as a (size,size,3) uint8 array, or None when it cannot be decoded."""
    try:
        from PIL import Image
        im = Image.open(fpath).convert('RGB').resize((size, size), Image.LANCZOS)
        return np.asarray(im, dtype=np.uint8)
    except Exception:
        return None


def _load_uncached(rel_path: str, roots, size: int) -> tuple:
    """(RGB tile, outcome, lowercase path); a miss or bad file is neutral grey."""
    rp = _texture_rel(rel_path)
    if not rp:
        return np.full((size, size, 3), 128, dtype=np.uint8), None, ''
    fpath, outcome = _find_texture(rp, roots)
    img = _decode_rgb(fpath, size) if fpath is not None else None
    if img is None:
        outcome = 'decode_error' if fpath is not None else outcome
        img = np.full((size, size, 3), 128, dtype=np.uint8)
    return img, outcome, rp.lower()


def load_texture_rgb(rel_path: str, tex_root, size: int = 64):
    """Load a landscape .dds as an (size,size,3) uint8 RGB tile, cached.

    `tex_root` is a directory OR a sequence searched in order (an override's
    own output, then its masters'). Lookup is case-blind, exact first, and
    every call -- cache hits included -- is counted for `texture_stats`. A
    texture that cannot be found or decoded is neutral grey, never silently:
    the count says so.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-texture-counts
    """
    roots = ([tex_root] if isinstance(tex_root, (str, Path))
             else list(tex_root))
    key = (rel_path.lower(), size, tuple(str(r) for r in roots))
    hit = _TEX_CACHE.get(key)
    if hit is None:
        hit = _TEX_CACHE[key] = _load_uncached(rel_path, roots, size)
    img, outcome, name = hit
    if name:
        _TEX_USES[(name, outcome)] += 1
    return img


def texture_stats() -> Counter:
    """This process's {(path, outcome): uses}, then reset (a worker returns these)."""
    out = Counter(_TEX_USES)
    _TEX_USES.clear()
    return out


def texture_report(stats) -> tuple:
    """(log lines, ok) for merged `texture_stats`; ok is False when nothing was found.

    See: docs/commentary/asset_convert_terrain.md#terrain-lod-texture-counts
    """
    paths = {o: sorted({p for (p, oc) in stats if oc == o})
             for o in ('exact', 'resolved', 'missing', 'decode_error')}
    uses = sum(stats.values())
    wanted = sum(len(v) for v in paths.values())
    lines = ['  Terrain-LOD textures: %d requested (%d lookups) -- %d exact, '
             '%d case-resolved, %d missing, %d unreadable'
             % (wanted, uses, len(paths['exact']), len(paths['resolved']),
                len(paths['missing']), len(paths['decode_error']))]
    for kind in ('missing', 'decode_error'):
        lines += ['    %s: %s (%d lookups)' % (kind, p, sum(
            n for (q, oc), n in stats.items() if q == p and oc == kind))
            for p in paths[kind]]
    found = len(paths['exact']) + len(paths['resolved'])
    return lines, not (wanted and not found)


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


def _apply_vclr_shading(out: np.ndarray, colors: np.ndarray,
                        cell_px: int) -> np.ndarray:
    """Modulate `out` by the cell's VCLR luminance (baked AO / lighting).

    `colors` is 33x33 with row 0 = south, flipped here to image orientation.
    VCLR is a light map centered on ~0.5 = neutral (x2 = unshaded); the full
    x2 range produced hard cell seams (per-cell VCLR discontinuities) and
    crushed shadows, so the shading is blended only partway toward neutral.
    """
    from PIL import Image
    shade = Image.fromarray(np.flipud(colors).copy(), 'RGB').resize(
        (cell_px, cell_px), Image.BILINEAR)
    shade = np.asarray(shade, dtype=np.float32) / 255.0
    lum = shade.mean(axis=2, keepdims=True) * 2.0
    mult = 1.0 + (lum - 1.0) * VCLR_SHADE_STRENGTH
    return np.clip(out * mult, 0, 255)


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
        btile = load_texture_rgb(diff or default_land_texture(),
                                 tex_root, tex_size)
        quad_img = _sample_tiled(btile, q_us, q_vs).astype(np.float32)

        # alpha layers, in ATXT layer order
        for (lfid, grid17) in alpha.get(quad, []):
            diff = ltex_map.get(lfid, {}).get('diffuse', '')
            if not diff:
                continue
            atile = load_texture_rgb(diff, tex_root, tex_size)
            atex = _sample_tiled(atile, q_us, q_vs).astype(np.float32)
            op = _upsample_opacity(grid17, quad_px)[:, :, None]
            quad_img = quad_img * (1.0 - op) + atex * op

        out[rs, cs] = quad_img

    if colors is not None:
        out = _apply_vclr_shading(out, colors, cell_px)

    # Bake the underwater murk: blend submerged pixels toward a flat murky
    # color by depth, like vanilla LOD diffuse (the LOD water sheet alone is
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
