"""Release-gate checks that read LAND: G6 land layers, G7 terrain-LOD colour.

Ported from the LOD probe's adversary scripts (`quadloss.py`, `vclr.py`) so
the rebuild can run them; both compare against the TES4 SOURCE, never
against the converter's own output alone. Both run over every plugin and
worldspace the build has: no worldspace, plugin or FormID is named here.
See: docs/commentary/tools_release_gate.md#terrain-checks
"""

import io
import struct
import zlib
from pathlib import Path

import numpy as np

from tools.release.verify_build import (FAIL, PASS, REFUSE, baseline,
                                        not_applicable, ratio, result,
                                        worldspace_blocks)

#: Opacity below which a VTXT point does not count as painted.
PAINTED = 0.01

#: G7: MAE (0-255 colour levels) a worldspace may gain over the baseline run.
MAE_TOLERANCE = 1.0

#: Cells a tile must contribute to count; fewer is noise.
MIN_CELLS = 30

#: Mean (max - min channel) below which the terrain reads as grey.
MIN_SPREAD = 2.0

#: G7: |t| of the paired flip test that decides a worldspace's orientation.
ORIENT_T = 3.0

#: The TES4 texture under a quadrant no BTXT paints, Data-relative.
DEFAULT_LAND = 'textures\\landscape\\default.dds'

#: Where a TES4 LTEX's ICON is relative to, Data-relative.
LANDSCAPE_DIR = 'textures\\landscape\\'

#: Top-level TES4 groups the source walk enters; every other is skipped whole.
SOURCE_TOPS = (b'WRLD', b'LTEX')


# ---------------------------------------------------------------------------
# Shared LAND parse
# ---------------------------------------------------------------------------


def converted_lands(ctx, ws: str):
    """(lands, cell_water, default water height) of converted worldspace `ws`, as the LOD bake reads it.

    The first plugin in load order that knows `ws` defines it; every later
    converted plugin is laid on top. A cell whose last LAND has no VHGT has
    no terrain here, which is what `_decode_land` does in the bake.
    """
    key = ('lands', ws.lower())
    if key not in ctx.cache:
        lands, water, height = converted_land_records(ctx, ws)
        ctx.cache[key] = ({c: land for c, land in lands.items()
                           if 'heights' in land}, water, height)
    return ctx.cache[key]


def converted_land_records(ctx, ws: str):
    """`parse_land_records` of converted `ws` keeping every LAND, VHGT or not, memoised.

    A height-less LAND carries only its layers and replaces the cell like
    any later LAND; `converted_lands` filters it back out.
    """
    key = ('all lands', ws.lower())
    if key not in ctx.cache:
        from asset_convert.lod.terrain_lod import (decode_any_land,
                                                   parse_land_records)
        esms = land_stack(ctx, ws)
        ctx.cache[key] = (parse_land_records(esms[0], ws, esms[1:],
                                             decode=decode_any_land)
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

    A LAND with no such quadrant maps to an empty set, so a later plugin's
    LAND can replace an earlier one's.
    """
    out = {}
    for sig, _fid, edid, cell, body in source_records(raw):
        if sig == b'LAND':
            base, alpha = _land_quads(body)
            out[(edid, cell)] = alpha - base
    return out


def source_records(raw: bytes, keep_world=None):
    """Yield (sig, FormID, worldspace EDID, cell, body) per LTEX and worldspace LAND of a TES4 file.

    A flat walk: each WRLD record names its FormID's EDID, a type-1 GRUP
    names the worldspace it holds, each CELL's XCLC names the grid cell the
    following LAND belongs to. Top groups other than SOURCE_TOPS, and a
    worldspace whose EDID `keep_world` rejects, are skipped whole.
    """
    names, pos, cell, inside, ends = {}, 0, None, None, []
    while pos < len(raw):
        while ends and pos >= ends[-1][0]:
            inside = ends.pop()[1]
        sig = raw[pos:pos + 4]
        size, label, kind = struct.unpack_from('<IIi', raw, pos + 4)
        if sig == b'GRUP':
            if _skip_group(raw[pos + 8:pos + 12], kind, names.get(label),
                           keep_world):
                pos += size
                continue
            ends.append((pos + size, inside))
            inside = names.get(label) if kind == 1 else inside
            pos += 20
            continue
        rec_pos, pos = pos, pos + 20 + size
        if sig not in (b'WRLD', b'CELL', b'LAND', b'LTEX'):
            continue
        body = _body(raw, rec_pos, size, label)
        fid = kind & 0xFFFFFFFF
        cell = _source_record(sig, body, fid, names, cell)
        if sig == b'LTEX' or (sig == b'LAND' and inside and cell is not None):
            yield sig, fid, inside, cell, body


def _skip_group(label: bytes, kind: int, world, keep_world) -> bool:
    """True for a top group not needed, or a worldspace `keep_world` rejects."""
    if kind == 0:
        return label not in SOURCE_TOPS
    return kind == 1 and keep_world is not None and not keep_world(world)


def _source_record(sig, body, fid, names, cell):
    """Fold one WRLD/CELL body into the walk's state; return the current cell."""
    if sig == b'WRLD':
        edid = dict(_subs(body)).get(b'EDID', b'')
        names[fid] = edid.rstrip(b'\0').decode('latin-1')
    elif sig == b'CELL':
        xclc = dict(_subs(body)).get(b'XCLC')
        return struct.unpack('<ii', xclc[:8]) if xclc else None
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
    on both sides; a converted LAND counts with or without VHGT. The
    converter used to drop alpha on a quadrant with no BTXT, painting it
    the default texture; denominator = such quadrants.
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
        lands = converted_land_records(ctx, ws)[0]
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


def worldspace_tint(ctx, ws: str) -> tuple:
    """(VCLR tint mode, where it came from): the bake's `VCLR tint:` line for `ws`, else the config's.

    The line belongs to the worldspace whose `LODGen input` block holds it;
    a block with no such line, or two different ones, falls back.
    """
    from asset_convert.lod.terrain_lod_textures import (VCLR_TINT_MODES,
                                                        configured_vclr_tint)
    lines = worldspace_blocks(ctx.sections).get(ws, ())
    modes = {line.split('VCLR tint:', 1)[1].strip().lower()
             for line in lines if 'VCLR tint:' in line}
    if len(modes) == 1 and modes <= set(VCLR_TINT_MODES):
        return modes.pop(), 'log'
    return configured_vclr_tint(), f'config (log gave {sorted(modes)})'


def peak_image(lands, mask, tx: int, ty: int, px: int) -> np.ndarray:
    """(32px, 32px, 1) per-pixel VCLR peak over the tile's masked cells, row 0 = north; 1 elsewhere.

    The 'hue' tint divided our colours by exactly this; multiplying it back
    gives the vanilla multiply Oblivion's baked tile was made with.
    """
    from asset_convert.lod.terrain_lod_textures import vclr_shade
    out = np.ones((32 * px, 32 * px, 1), np.float32)
    for row, col in zip(*np.nonzero(mask)):
        land = lands[(tx + col, ty + 31 - row)]
        out[row * px:(row + 1) * px, col * px:(col + 1) * px] = vclr_shade(
            land['colors'], px)[1]
    return out


def worldspace_cells(ctx, ws: str):
    """(cells, [ours, reference, reference unflipped] (3, n, 3), tiles) of `ws`; None without baked LOD.

    Cells are those `tile_mask` keeps in tiles with at least MIN_CELLS of
    them. Oblivion's image is stored south-up, so the reference is it
    flipped. Under the 'hue' tint ours is put back to multiply first.
    """
    pairs = tile_pairs(ctx, ws)
    if pairs is None:
        return None
    land = converted_lands(ctx, ws)
    hue = worldspace_tint(ctx, ws)[0] == 'hue'
    cells, rows = [], []
    for tx, ty, ours, theirs in pairs:
        mask = tile_mask(*land, tx, ty)
        if mask.sum() < MIN_CELLS:
            continue
        img = _load(ours)
        if hue:
            img = img * peak_image(land[0], mask, tx, ty, img.shape[0] // 32)
        b = _load(theirs)
        rows.append(np.stack([cell_means(img), cell_means(np.flipud(b)),
                              cell_means(b)])[:, mask])
        cells += [(tx + int(c), ty + 31 - int(r))
                  for r, c in zip(*np.nonzero(mask))]
    stack = np.concatenate(rows, 1) if rows else np.zeros((3, 0, 3))
    return cells, stack, len(rows)


def orientation(ours, ref, unflipped) -> tuple:
    """('ok' | 'flipped' | 'untestable', t): the paired test over cells.

    d_i = |ours - unflipped| - |ours - ref| per cell (channel mean); t is
    mean(d) / its standard error, positive when the flipped reference fits.
    Beyond +-ORIENT_T it decides; in between the reference carries too little
    orientation signal to say.
    """
    d = np.abs(ours - unflipped).mean(-1) - np.abs(ours - ref).mean(-1)
    sd = d.std(ddof=1) if len(d) > 1 else 0.0
    t = float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 0 else 0.0
    if abs(t) <= ORIENT_T:
        return 'untestable', t
    return ('ok' if t > 0 else 'flipped'), t


def colour_bound(ctx, ws: str):
    """The highest MAE `ws` may score: `--mae-bound`, else the baseline's + MAE_TOLERANCE, else None."""
    if ctx.mae_bound is not None:
        return ctx.mae_bound
    base = baseline(ctx, 'G7', 'worldspaces', {}).get(ws, {}).get('mae')
    return None if base is None else base + MAE_TOLERANCE


def judge_worldspace(ctx, ws, got, pred) -> dict:
    """One worldspace's G7 row: MAE against the reference, or its prediction when the reference misses that.

    `pred` is {cell: predicted colour} from the source (`source_predictions`).
    When the reference misses the prediction by more than the bound, the
    reference does not describe its own terrain; ours is judged against the
    prediction under the same bound, and the reference decides no
    orientation (ours far from it, d_i is only the reference's own
    asymmetry).
    """
    cells, (ours, ref, unflipped), tiles = got
    tint, tint_from = worldspace_tint(ctx, ws)
    verdict, t = orientation(ours, ref, unflipped)
    bound = colour_bound(ctx, ws)
    row = {'cells': len(cells), 'tiles': tiles, 'tint': tint,
           'tint_from': tint_from, 'orientation': verdict, 't': t,
           'mae_ref': _mae(ours, ref), 'control': _mae(ours, unflipped),
           'spread': float((ours.max(-1) - ours.min(-1)).mean()),
           'pred_cells': 0, 'judged_against': 'reference'}
    row['mae'] = row['mae_ref']
    have = [i for i, c in enumerate(cells) if c in pred]
    if have:
        p = np.array([pred[cells[i]] for i in have])
        row.update(pred_cells=len(have), ref_vs_pred=_mae(ref[have], p))
        if bound is not None and row['ref_vs_pred'] > bound:
            row.update(judged_against='prediction', mae=_mae(ours[have], p),
                       orientation='untestable')
    row['fails'] = _colour_fails(row, bound)
    return row


def _mae(a, b) -> float:
    """Mean absolute difference over cells and channels."""
    return float(np.abs(a - b).mean()) if len(a) else 0.0


def _colour_fails(row, bound) -> list:
    """Why one worldspace's colour fails; [] when it passes."""
    why = []
    if row['orientation'] == 'flipped':
        why.append(f"unflipped reference fits better (t {row['t']:.1f})")
    if row['spread'] < MIN_SPREAD:
        why.append('grey')
    if bound is not None and row['mae'] > bound:
        why.append(f'MAE above {bound:.1f}')
    return why


def check_terrain_colour(ctx) -> dict:
    """G7: per worldspace with baked source LOD: orientation, not grey, MAE within bound.

    Per-cell mean colour of our level-32 terrain diffuse vs the source's
    baked tile, over cells with LAND that are fully painted and dry. The
    MAE bound is `--mae-bound`, else the baseline run's MAE plus
    MAE_TOLERANCE; with neither, only the intrinsic conditions apply. No
    worldspace whose orientation can be decided REFUSES.
    See: docs/commentary/tools_release_gate.md#terrain-checks
    """
    got = {ws: worldspace_cells(ctx, ws) for ws in ctx.worldspaces}
    got = {ws: g for ws, g in got.items() if g is not None}
    if not got:
        return not_applicable('G7', 'no worldspace has baked source LOD')
    judged = {ws: g for ws, g in got.items() if g[0]}
    if not judged:
        return result('G7', REFUSE, '0 cells', 'no comparable tile',
                      worldspaces={ws: {'cells': 0} for ws in got})
    preds, pred_note = source_predictions(
        ctx, {ws: set(g[0]) for ws, g in judged.items()})
    rows = {ws: judge_worldspace(ctx, ws, g, preds.get(ws, {}))
            for ws, g in judged.items()}
    return _colour_result(rows, pred_note)


def _colour_result(rows, pred_note) -> dict:
    """G7's result over the judged worldspace rows."""
    cells = sum(r['cells'] for r in rows.values())
    testable = [ws for ws, r in rows.items() if r['orientation'] != 'untestable']
    status = (FAIL if any(r['fails'] for r in rows.values())
              else PASS if testable else REFUSE)
    detail = '; '.join(_colour_line(ws, r) for ws, r in rows.items())
    if not testable:
        detail = 'no worldspace can decide orientation; ' + detail
    return result('G7', status,
                  f'{cells} cells in {sum(r["tiles"] for r in rows.values())}'
                  f' tiles, {len(rows)} worldspaces', detail,
                  mae=sum(r['mae'] * r['cells'] for r in rows.values()) / cells,
                  prediction=pred_note, worldspaces=rows)


def _colour_line(ws, r) -> str:
    """One worldspace's part of the G7 detail."""
    line = (f"{ws} MAE {r['mae']:.1f} t {r['t']:+.1f} spread {r['spread']:.1f}"
            f" tint {r['tint']}")
    if r['judged_against'] == 'prediction':
        line += (f" (reference inconsistent with its terrain: it misses the "
                 f"source prediction by {r['ref_vs_pred']:.1f}; vs reference "
                 f"{r['mae_ref']:.1f})")
    if r['orientation'] == 'untestable':
        line += ' INFO orientation untestable'
    return line + (f" FAIL {r['fails']}" if r['fails'] else '')


# ---------------------------------------------------------------------------
# G7 reference self-check: a prediction from the TES4 source alone
# ---------------------------------------------------------------------------


def source_predictions(ctx, wanted: dict) -> tuple:
    """({ws: {cell: predicted colour}}, note) for `wanted` {converted ws: cells}, from the source alone.

    Each cell's TES4 SOURCE LAND (the last plugin in load order holding it)
    paints each quadrant with its SOURCE textures' mean colours, base under
    each ATXT at its mean opacity, times the mean VCLR / 255 (the vanilla
    multiply Oblivion bakes with); no layer, or id 0, is DEFAULT_LAND. No
    converter output is read. A cell whose texture is found nowhere gets no
    prediction.
    """
    srcs = source_esms(ctx)
    if None in srcs.values():
        return {}, f'no TES4 source for {[p for p, s in srcs.items() if not s]}'
    lands, icons = source_lands(ctx, wanted)
    means = source_texture_means(srcs, set(icons.values()) | {DEFAULT_LAND})
    out, missing = {}, 0
    for (ws, cell), (layers, vclr) in lands.items():
        p = _predict(layers, vclr, lambda f: means.get(
            icons.get(f) if f and f[1] else DEFAULT_LAND))
        missing += p is None
        if p is not None:
            out.setdefault(ws, {})[cell] = p
    return out, (f'{len(lands) - missing} cells predicted, {missing} with a '
                 f'texture found nowhere; {len(means)} source textures')


def source_lands(ctx, wanted: dict) -> tuple:
    """({(ws, cell): (layers, mean VCLR)}, {LTEX key: Data-relative texture}) over the TES4 sources.

    Plugins are read in load order, a later LAND or LTEX replacing an
    earlier one; ids are keyed (owning file, object id) so they compare
    across files.
    """
    from core.plugin_masters import get_masters_from_binary
    from core.worldspace_names import converted_worldspace_edid
    lands, icons = {}, {}
    for plugin, src in source_esms(ctx).items():
        own = _owner(plugin, get_masters_from_binary(str(src)))
        for sig, fid, edid, cell, body in source_records(
                src.read_bytes(),
                lambda w: converted_worldspace_edid(w or '') in wanted):
            if sig == b'LTEX':
                icon = dict(_subs(body)).get(b'ICON', b'').rstrip(b'\0')
                icons[own(fid)] = (LANDSCAPE_DIR
                                   + icon.decode('latin-1').lower())
            elif cell in wanted.get(converted_worldspace_edid(edid), ()):
                lands[(converted_worldspace_edid(edid), cell)] = (
                    _source_land(body, own))
    return lands, icons


def _owner(plugin: str, masters) -> callable:
    """fid -> (owning file, object id), by the TES4 load-order byte against `masters`."""
    files = [m.lower() for m in masters] + [plugin.lower()]
    return lambda fid: (files[min(fid >> 24, len(files) - 1)], fid & 0xFFFFFF)


def _source_land(body: bytes, own) -> tuple:
    """(layers, mean VCLR RGB) of one TES4 LAND; no VCLR is white."""
    from asset_convert.lod.terrain_lod_textures import decode_land_layers
    vclr = dict(_subs(body)).get(b'VCLR', b'')
    colours = (np.frombuffer(vclr[:33 * 33 * 3], np.uint8).reshape(-1, 3)
               if len(vclr) >= 33 * 33 * 3 else np.full((1, 3), 255, np.uint8))
    return decode_land_layers(body, own), colours.mean(0)


def _predict(layers, vclr, colour):
    """Predicted cell colour: the 4 quadrants' blends averaged, x VCLR / 255; None if a texture is missing."""
    quads = []
    for q in range(4):
        c = colour(layers['base'].get(q))
        for fid, grid in layers['alpha'].get(q, []):
            top = colour(fid)
            if c is None or top is None:
                return None
            op = float(grid.sum()) / grid.size
            c = c * (1 - op) + top * op
        if c is None:
            return None
        quads.append(c)
    return np.mean(quads, 0) * vclr / 255.0


def source_texture_means(srcs: dict, paths) -> dict:
    """{Data-relative path: mean RGB} as the source game finds it: loose, then BSAs, last plugin first.

    A texture found nowhere, or that will not decode, is left out.
    """
    from asset_convert import case_paths
    from asset_convert.sources.bsa_extract import get_bsa_files, read_bsa_files
    order = [s for s in reversed(list(srcs.values())) if s]
    dirs = list(dict.fromkeys(s.parent for s in order))
    blobs = {}
    for rel in paths:
        hit = case_paths.resolve(dirs, rel, site='gate_terrain_source')
        if hit is not None:
            blobs[rel] = hit.read_bytes()
    for bsa in (b for s in order for b in get_bsa_files(s.parent, s.name)):
        want = [rel for rel in paths if rel not in blobs]
        if not want:
            break
        blobs.update(read_bsa_files(bsa, want))
    means = {rel: _mean_rgb(data) for rel, data in blobs.items()}
    return {rel: m for rel, m in means.items() if m is not None}


def _mean_rgb(data: bytes):
    """Mean RGB of an image file's bytes, or None when it will not decode."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data)).convert('RGB')
    except Exception:
        return None
    return np.asarray(img, np.float32).reshape(-1, 3).mean(0)
