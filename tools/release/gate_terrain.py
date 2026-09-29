"""Release-gate checks that read LAND: G6 land layers, G7 terrain-LOD colour.

Ported from the LOD probe's adversary scripts (`quadloss.py`, `vclr.py`) so
the rebuild can run them; both compare against the TES4 SOURCE, never
against the converter's own output alone. Both run over every plugin and
worldspace the build has: no worldspace, plugin or FormID is named here.
See: docs/commentary/tools_release_gate.md#terrain-checks
"""

import struct
import zlib
from pathlib import Path

import numpy as np

from tools.release.verify_build import (FAIL, PASS, REFUSE, baseline,
                                        not_applicable, ratio, result)

#: Opacity below which a VTXT point does not count as painted.
PAINTED = 0.01

#: G7: MAE (0-255 colour levels) a worldspace may gain over the baseline run.
MAE_TOLERANCE = 1.0

#: Cells a tile must contribute to count; fewer is noise.
MIN_CELLS = 30

#: Mean (max - min channel) below which the terrain reads as grey.
MIN_SPREAD = 2.0


# ---------------------------------------------------------------------------
# Shared LAND parse
# ---------------------------------------------------------------------------


def converted_lands(ctx, ws: str):
    """(lands, cell_water, default water height) of converted worldspace `ws`, memoised.

    The first plugin in load order that knows `ws` defines it; every later
    converted plugin is laid on top, as the LOD bake does.
    """
    key = ('lands', ws.lower())
    if key not in ctx.cache:
        from asset_convert.lod.terrain_lod import parse_land_records
        esms = land_stack(ctx, ws)
        ctx.cache[key] = (parse_land_records(esms[0], ws, esms[1:])
                          if esms else ({}, {}, 0.0))
    return ctx.cache[key]


def land_stack(ctx, ws: str) -> list:
    """Converted plugin files for `ws`: its defining plugin, then every later one."""
    from asset_convert.lod.terrain_lod_baked import worldspace_fids
    from output_layout import plugin_esm, record_dir
    out = []
    for plugin in ctx.plugins:
        esm = plugin_esm(ctx.output, plugin, ctx.export)
        if esm.is_file() and (out or worldspace_fids(
                record_dir(ctx.export, plugin), ws)):
            out.append(esm)
    return out


def source_esms(ctx) -> dict:
    """{plugin: TES4 source file or None}: `--source PLUGIN=PATH`, else the registry's."""
    from source_paths import resolve_plugin_path
    out = {}
    for plugin in ctx.plugins:
        given = ctx.sources.get(plugin)
        path = Path(given or resolve_plugin_path(plugin, None, str(ctx.export)))
        out[plugin] = path if path.is_absolute() and path.is_file() else None
    return out


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


def _body(raw: bytes, pos: int, size: int, flags: int) -> bytes:
    """A TES4 record body, decompressed when flagged."""
    body = raw[pos + 20:pos + 20 + size]
    return zlib.decompress(body[4:]) if flags & 0x40000 else body


def source_land_quads(raw: bytes) -> dict:
    """{(worldspace EDID, (x, y)): ATXT-only quadrants} for every LAND in a TES4 file.

    A flat walk: each WRLD record names its FormID's EDID, a type-1 GRUP
    names the worldspace it holds, each CELL's XCLC names the grid cell the
    following LAND belongs to. A LAND with no such quadrant maps to an empty
    set, so a later plugin's LAND can replace an earlier one's.
    """
    out, names, pos, cell, inside, ends = {}, {}, 0, None, None, []
    while pos < len(raw):
        while ends and pos >= ends[-1][0]:
            inside = ends.pop()[1]
        sig = raw[pos:pos + 4]
        size, label, kind = struct.unpack_from('<IIi', raw, pos + 4)
        if sig == b'GRUP':
            ends.append((pos + size, inside))
            inside = names.get(label) if kind == 1 else inside
            pos += 20
            continue
        rec_pos, pos = pos, pos + 20 + size
        if sig in (b'WRLD', b'CELL', b'LAND'):
            body = _body(raw, rec_pos, size, label)
            cell = _source_record(sig, body, kind & 0xFFFFFFFF, names, cell,
                                  inside, out)
    return out


def _source_record(sig, body, fid, names, cell, inside, out):
    """Fold one WRLD/CELL/LAND body into the walk's state; return the current cell."""
    if sig == b'WRLD':
        edid = dict(_subs(body)).get(b'EDID', b'')
        names[fid] = edid.rstrip(b'\0').decode('latin-1')
    elif sig == b'CELL':
        xclc = dict(_subs(body)).get(b'XCLC')
        return struct.unpack('<ii', xclc[:8]) if xclc else None
    elif cell is not None and inside:
        base, alpha = _land_quads(body)
        out[(inside, cell)] = alpha - base
    return cell


def merged_source_quads(ctx):
    """({converted worldspace: {cell: quadrants}}, plugins without a source) in load order."""
    from core.worldspace_names import converted_worldspace_edid
    merged, missing = {}, []
    for plugin, src in source_esms(ctx).items():
        if src is None:
            missing.append(plugin)
            continue
        for (edid, cell), quads in source_land_quads(src.read_bytes()).items():
            merged.setdefault(converted_worldspace_edid(edid), {})[cell] = quads
    return merged, missing


def check_land_layers(ctx) -> dict:
    """G6: every source quadrant painted only by ATXT keeps an alpha layer.

    Over every worldspace of every converted plugin, merged in load order
    on both sides. The converter used to drop alpha on a quadrant with no
    BTXT, painting it the default texture; denominator = such quadrants.
    """
    merged, missing = merged_source_quads(ctx)
    if missing:
        return result('G6', REFUSE, '0 quadrants',
                      f'no TES4 source found for {missing}')
    kept = total = 0
    per = {}
    for ws, cells in merged.items():
        quads = [(c, q) for c, qs in cells.items() for q in qs]
        if not quads:
            continue
        lands = converted_lands(ctx, ws)[0]
        n = sum(1 for c, q in quads
                if lands.get(c) and lands[c]['layers']['alpha'].get(q))
        per[ws] = [n, len(quads)]
        kept, total = kept + n, total + len(quads)
    if not total:
        return not_applicable('G6', 'no source quadrant is painted by ATXT '
                              'alone')
    return ratio('G6', kept, total, 'ATXT-only quadrants',
                 ', '.join(f'{w} {k}/{t}' for w, (k, t) in per.items()),
                 worldspaces=per)


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


def tile_pairs(ctx, ws: str) -> list:
    """(tx, ty, our LOD32 diffuse, the source's baked tile) present on both sides.

    The baked tiles come from every converted plugin's export, the last in
    load order winning (`terrain_lod_baked.baked_textures`).
    """
    from asset_convert.lod.terrain_lod_baked import baked_textures
    from output_layout import record_dir
    baked = baked_textures([record_dir(ctx.export, p) for p in ctx.plugins], ws)
    if not baked:
        return None
    out = []
    ours = ctx.lod / 'textures' / 'terrain' / ws
    for p in sorted(ours.glob(f'{ws}.32.*.dds')):
        parts = p.name[len(ws) + 1:].split('.')
        if len(parts) != 4 or p.name.endswith('_n.dds'):
            continue
        tx, ty = int(parts[1]), int(parts[2])
        if (tx, ty) in baked:
            out.append((tx, ty, p, baked[(tx, ty)]))
    return out


def _tile_errors(land, tx, ty, ours, theirs):
    """(cells, abs error sum, flipped-control error sum, spread sum) for one tile.

    Oblivion's image is stored south-up, so it is flipped to compare; the
    unflipped image is the control, which must score worse.
    """
    mask = tile_mask(*land, tx, ty)
    n = int(mask.sum())
    if n < MIN_CELLS:
        return 0, 0.0, 0.0, 0.0
    a = cell_means(_load(ours))
    b = _load(theirs)
    good = np.abs(a - cell_means(np.flipud(b)))[mask].mean(-1).sum()
    control = np.abs(a - cell_means(b))[mask].mean(-1).sum()
    spread = (a.max(-1) - a.min(-1))[mask].sum()
    return n, float(good), float(control), float(spread)


def worldspace_colour(ctx, ws: str):
    """{'cells', 'tiles', 'mae', 'control', 'spread'} for `ws`, or None without baked LOD."""
    pairs = tile_pairs(ctx, ws)
    if pairs is None:
        return None
    sums = np.zeros(4)
    tiles = 0
    for tx, ty, ours, theirs in pairs:
        got = _tile_errors(converted_lands(ctx, ws), tx, ty, ours, theirs)
        tiles += got[0] > 0
        sums += got
    cells = int(sums[0])
    row = {'cells': cells, 'tiles': tiles}
    if cells:
        row.update(mae=sums[1] / cells, control=sums[2] / cells,
                   spread=sums[3] / cells)
    return row


def colour_bound(ctx, ws: str):
    """The highest MAE `ws` may score: `--mae-bound`, else the baseline's + MAE_TOLERANCE, else None."""
    if ctx.mae_bound is not None:
        return ctx.mae_bound
    base = baseline(ctx, 'G7', 'worldspaces', {}).get(ws, {}).get('mae')
    return None if base is None else base + MAE_TOLERANCE


def _colour_fails(row, bound) -> list:
    """Why one worldspace's colour fails; [] when it passes."""
    why = []
    if row['control'] <= row['mae']:
        why.append('flipped control not worse')
    if row['spread'] < MIN_SPREAD:
        why.append('grey')
    if bound is not None and row['mae'] > bound:
        why.append(f'MAE above {bound:.1f}')
    return why


def check_terrain_colour(ctx) -> dict:
    """G7: per worldspace with baked source LOD: flipped control worse, not grey, MAE within bound.

    Per-cell mean colour of our level-32 terrain diffuse vs the source's
    baked tile, over cells with LAND that are fully painted and dry. The
    MAE bound is `--mae-bound`, else the baseline run's MAE plus
    MAE_TOLERANCE; with neither, only the intrinsic conditions apply.
    See: docs/commentary/tools_release_gate.md#terrain-checks
    """
    rows = {ws: worldspace_colour(ctx, ws) for ws in ctx.worldspaces}
    rows = {ws: r for ws, r in rows.items() if r is not None}
    if not rows:
        return not_applicable('G7', 'no worldspace has baked source LOD')
    judged = {ws: r for ws, r in rows.items() if r['cells']}
    cells = sum(r['cells'] for r in judged.values())
    if not cells:
        return result('G7', REFUSE, '0 cells', 'no comparable tile',
                      worldspaces=rows)
    fails = {ws: _colour_fails(r, colour_bound(ctx, ws))
             for ws, r in judged.items()}
    mae = sum(r['mae'] * r['cells'] for r in judged.values()) / cells
    return result('G7', FAIL if any(fails.values()) else PASS,
                  f'{cells} cells in {sum(r["tiles"] for r in judged.values())}'
                  f' tiles, {len(judged)} worldspaces',
                  '; '.join(f"{ws} MAE {r['mae']:.1f} control "
                            f"{r['control']:.1f} spread {r['spread']:.1f}"
                            + (f" FAIL {fails[ws]}" if fails[ws] else '')
                            for ws, r in judged.items()),
                  mae=mae, worldspaces=rows)
