"""Release-gate checks that read LAND: G6 land layers, G7 terrain-LOD colour.

Ported from the LOD probe's adversary scripts (`quadloss.py`, `vclr.py`) so
the rebuild can run them; both compare against the TES4 SOURCE, never
against the converter's own output alone.
See: docs/commentary/tools_release_gate.md#terrain-checks
"""

import json
import struct
import zlib
from pathlib import Path

import numpy as np

from tools.release.verify_build import FAIL, PASS, REFUSE, ratio, result

#: Oblivion's Tamriel WRLD FormID, and the converted worldspace it becomes.
SOURCE_WRLD = 0x0000003C
CONVERTED_WRLD = 'TES4Tamriel'

#: Opacity below which a VTXT point does not count as painted.
PAINTED = 0.01

#: Cell-weighted MAE bound for terrain-LOD colour (Stream 3's bound).
MAE_BOUND = 18.0

#: Cells a tile must contribute to count; fewer is noise.
MIN_CELLS = 30

#: Mean (max - min channel) below which the terrain reads as grey.
MIN_SPREAD = 2.0


# ---------------------------------------------------------------------------
# Shared LAND parse
# ---------------------------------------------------------------------------


def converted_lands(ctx):
    """(lands, cell_water, default water height) of the converted Tamriel, memoised."""
    if 'lands' not in ctx.cache:
        from asset_convert.lod.terrain_lod import parse_land_records
        esm = ctx.output / 'Oblivion.esm' / 'Oblivion.esm'
        ctx.cache['lands'] = parse_land_records(esm, CONVERTED_WRLD)
    return ctx.cache['lands']


def source_esm(ctx):
    """The TES4 Oblivion.esm: `--source-esm`, else export/sources.json's home."""
    if ctx.source_esm:
        return Path(ctx.source_esm)
    try:
        reg = json.loads((ctx.export / 'sources.json').read_text('utf-8'))
        return Path(reg['homes']['oblivion.esm']) / 'Oblivion.esm'
    except (OSError, ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# G6: ATXT-only quadrants keep their alpha layers
# ---------------------------------------------------------------------------


def _subs(body: bytes):
    """Yield (type, data) subrecords of a TES4 record body."""
    i = 0
    while i + 6 <= len(body):
        n = struct.unpack_from('<H', body, i + 4)[0]
        yield body[i:i + 4], body[i + 6:i + 6 + n]
        i += 6 + n


def _painted(vtxt: bytes) -> bool:
    """True when any VTXT point's opacity exceeds PAINTED."""
    return any(struct.unpack_from('<f', vtxt, i + 4)[0] > PAINTED
               for i in range(0, len(vtxt) - 7, 8))


def _land_quads(body: bytes):
    """(BTXT quadrants, quadrants with a painted ATXT) of one TES4 LAND."""
    base, alpha, last = set(), set(), None
    for sig, data in _subs(body):
        if sig == b'BTXT':
            base.add(data[4])
        elif sig == b'ATXT':
            last = data[4]
        elif sig == b'VTXT':
            alpha |= {last} if last is not None and _painted(data) else set()
            last = None
    return base, alpha


def source_atxt_only(raw: bytes, wrld: int = SOURCE_WRLD) -> set:
    """{((x, y), quadrant)} painted by ATXT with no BTXT, in TES4 worldspace `wrld`.

    A flat walk: a type-1 GRUP names its worldspace, each CELL's XCLC names
    the grid cell the following LAND belongs to.
    """
    out, pos, cell, inside = set(), 0, None, None
    ends = []
    while pos < len(raw):
        while ends and pos >= ends[-1][0]:
            inside = ends.pop()[1]
        sig = raw[pos:pos + 4]
        size, flags = struct.unpack_from('<II', raw, pos + 4)
        if sig == b'GRUP':
            ends.append((pos + size, inside))
            if struct.unpack_from('<i', raw, pos + 12)[0] == 1:
                inside = struct.unpack_from('<I', raw, pos + 8)[0]
            pos += 20
            continue
        body = raw[pos + 20:pos + 20 + size]
        pos += 20 + size
        if inside != wrld or sig not in (b'CELL', b'LAND'):
            continue
        if flags & 0x40000:
            body = zlib.decompress(body[4:])
        if sig == b'CELL':
            xclc = dict(_subs(body)).get(b'XCLC')
            cell = struct.unpack('<ii', xclc[:8]) if xclc else None
        elif cell is not None:
            base, alpha = _land_quads(body)
            out |= {(cell, q) for q in alpha - base}
    return out


def check_land_layers(ctx) -> dict:
    """G6: every source quadrant painted only by ATXT keeps an alpha layer.

    The converter used to drop alpha on a quadrant with no BTXT, painting it
    the default texture; denominator = such quadrants in the source.
    """
    src = source_esm(ctx)
    if src is None or not src.is_file():
        return result('G6', REFUSE, '0 quadrants', f'no source ESM ({src})')
    quads = source_atxt_only(src.read_bytes())
    lands = converted_lands(ctx)[0]
    kept = sum(1 for cell, q in quads
               if lands.get(cell) and lands[cell]['layers']['alpha'].get(q))
    return ratio('G6', kept, len(quads), 'ATXT-only quadrants',
                 f'{CONVERTED_WRLD} vs {src.name}')


# ---------------------------------------------------------------------------
# G7: terrain-LOD colour against Oblivion's own baked LOD
# ---------------------------------------------------------------------------


def cell_means(img: np.ndarray) -> np.ndarray:
    """(32, 32, 3) per-cell mean colour of a 32-cell tile image."""
    n = img.shape[0] // 32
    return img[:n * 32, :n * 32].reshape(32, n, 32, n, 3).mean((1, 3))


def _load(path: Path) -> np.ndarray:
    """An RGB image as float32."""
    from PIL import Image
    return np.asarray(Image.open(path).convert('RGB'), dtype=np.float32)


def tile_mask(lands, cell_water, default_wh, tx: int, ty: int) -> np.ndarray:
    """(32, 32) bool, row 0 = north: cells with LAND, all quadrants painted, dry."""
    mask = np.zeros((32, 32), bool)
    for cy in range(32):
        for cx in range(32):
            land = lands.get((tx + cx, ty + cy))
            if land is None:
                continue
            lay = land['layers']
            painted = all(q in lay['base'] or lay['alpha'].get(q)
                          for q in range(4))
            wh = water_height(cell_water, (tx + cx, ty + cy), default_wh)
            mask[31 - cy, cx] = painted and (wh is None
                                             or land['heights'].min() > wh)
    return mask


def water_height(cell_water, key, default_wh):
    """A cell's water height, or None when it has no water (as terrain LOD reads it)."""
    has, height = cell_water.get(key, (False, None))
    if not has:
        return None
    return height if height is not None else default_wh


def tile_pairs(ctx) -> list:
    """(tx, ty, our LOD32 diffuse, Oblivion's baked tile) present on both sides."""
    ours = ctx.lod / 'textures' / 'terrain' / CONVERTED_WRLD
    theirs = ctx.export / 'Oblivion.esm' / 'textures' / 'landscapelod' / \
        'generated'
    out = []
    for p in sorted(ours.glob(f'{CONVERTED_WRLD}.32.*.dds')):
        parts = p.name.split('.')
        if len(parts) != 5 or p.name.endswith('_n.dds'):
            continue
        tx, ty = int(parts[2]), int(parts[3])
        src = theirs / f'{SOURCE_WRLD}.{tx:02d}.{ty:02d}.32.dds'
        if src.is_file():
            out.append((tx, ty, p, src))
    return out


def _tile_errors(ctx, tx, ty, ours, theirs):
    """(cells, abs error sum, flipped-control error sum, spread sum) for one tile.

    Oblivion's image is stored south-up, so it is flipped to compare; the
    unflipped image is the control, which must score worse.
    """
    lands, water, dwh = converted_lands(ctx)
    mask = tile_mask(lands, water, dwh, tx, ty)
    n = int(mask.sum())
    if n < MIN_CELLS:
        return 0, 0.0, 0.0, 0.0
    a = cell_means(_load(ours))
    b = _load(theirs)
    good = np.abs(a - cell_means(np.flipud(b)))[mask].mean(-1).sum()
    control = np.abs(a - cell_means(b))[mask].mean(-1).sum()
    spread = (a.max(-1) - a.min(-1))[mask].sum()
    return n, float(good), float(control), float(spread)


def check_terrain_colour(ctx) -> dict:
    """G7: cell-weighted MAE <= MAE_BOUND, flipped control worse, not grey.

    Per-cell mean colour of our level-32 terrain diffuse vs Oblivion's baked
    tile, over cells with LAND that are fully painted and dry.
    """
    cells = err = ctrl = spread = 0.0
    tiles = 0
    for tx, ty, ours, theirs in tile_pairs(ctx):
        n, e, c, s = _tile_errors(ctx, tx, ty, ours, theirs)
        tiles += n > 0
        cells, err, ctrl, spread = cells + n, err + e, ctrl + c, spread + s
    if not cells:
        return result('G7', REFUSE, '0 cells', 'no comparable tile')
    mae, cmae, spr = err / cells, ctrl / cells, spread / cells
    ok = mae <= MAE_BOUND and cmae > mae and spr >= MIN_SPREAD
    return result('G7', PASS if ok else FAIL,
                  f'{int(cells)} cells in {tiles} tiles',
                  f'MAE {mae:.1f} (bound {MAE_BOUND}), flipped control '
                  f'{cmae:.1f}, channel spread {spr:.1f}',
                  mae=mae, control=cmae, spread=spr)
