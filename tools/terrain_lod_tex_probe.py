"""Probe terrain LOD texture inputs: LTEX->TXST resolution, DDS presence/loadability,
and per-cell compositor output stats.

Usage:
    python -m tools.terrain_lod_tex_probe [--esm output/Oblivion.esm/Oblivion.esm]
        [--tex-root output/Oblivion.esm/textures] [--cell X Y] [--worldspace TES4Tamriel]

Reports:
  * how many LTEX diffuse paths resolve to an existing, PIL-loadable file
  * for a sample cell (or --cell): its BTXT/ATXT layers and what each resolves to
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.terrain_lod_textures import (
    build_ltex_texture_map, _load_texture_rgb)


def probe_ltex(esm: Path, tex_root: Path):
    ltex = build_ltex_texture_map(esm)
    print(f"LTEX entries: {len(ltex)}")
    ok = missing = loadfail = nopath = 0
    samples = []
    for fid, d in sorted(ltex.items()):
        p = d['diffuse']
        if not p:
            nopath += 1
            if len(samples) < 10:
                samples.append(("NOPATH", f"{fid:08X}", ""))
            continue
        rp = p.replace('/', '\\').lstrip('\\')
        if rp.lower().startswith('textures\\'):
            rp = rp[len('textures\\'):]
        f = tex_root / rp
        if not f.exists():
            missing += 1
            if len(samples) < 10:
                samples.append(("MISSING", f"{fid:08X}", p))
            continue
        try:
            from PIL import Image
            im = Image.open(f).convert('RGB')
            arr = np.asarray(im)
            ok += 1
            if len(samples) < 10:
                samples.append(("OK", f"{fid:08X}", p, im.size,
                                "std=" + str(arr.reshape(-1, 3).std(axis=0).round(1).tolist())))
        except Exception as e:
            loadfail += 1
            if len(samples) < 10:
                samples.append(("LOADFAIL", f"{fid:08X}", p, str(e)[:100]))
    print(f"diffuse: ok={ok} missing={missing} loadfail={loadfail} nopath={nopath}")
    for s in samples:
        print("  ", *s)
    return ltex


def probe_cell(esm: Path, tex_root: Path, ltex, cx: int, cy: int, worldspace: str):
    from asset_convert.terrain_lod import _parse_land_records
    lands, cell_water, default_wh = _parse_land_records(esm, worldspace)
    print(f"LAND cells parsed: {len(lands)}; water cells: "
          f"{sum(1 for hw, _ in cell_water.values() if hw)}; "
          f"default water height: {default_wh}")
    land = lands.get((cx, cy))
    if land is None:
        near = sorted(lands.keys(), key=lambda k: abs(k[0]-cx)+abs(k[1]-cy))[:5]
        print(f"cell ({cx},{cy}) has no LAND; nearest: {near}")
        return
    layers = land['layers']
    print(f"cell ({cx},{cy}): base={{q: fid}} =",
          {q: f"{f:08X}" for q, f in layers['base'].items()})
    for q, lst in layers['alpha'].items():
        for i, (fid, grid) in enumerate(lst):
            d = ltex.get(fid, {}).get('diffuse', '<UNRESOLVED>')
            print(f"  quad {q} alpha[{i}] fid={fid:08X} opacity[min={grid.min():.2f} "
                  f"max={grid.max():.2f} mean={grid.mean():.2f}] -> {d}")
    for q, fid in layers['base'].items():
        d = ltex.get(fid, {}).get('diffuse', '<UNRESOLVED>')
        tile = _load_texture_rgb(d, tex_root, 64)
        print(f"  quad {q} base fid={fid:08X} -> {d}  "
              f"loaded std={tile.reshape(-1,3).std(axis=0).round(1).tolist()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--esm', default='output/Oblivion.esm/Oblivion.esm')
    ap.add_argument('--tex-root', default='output/Oblivion.esm/textures')
    ap.add_argument('--cell', nargs=2, type=int, default=None)
    ap.add_argument('--worldspace', default='TES4Tamriel')
    args = ap.parse_args()

    esm = Path(args.esm)
    tex_root = Path(args.tex_root)
    print("ESM:", esm, "exists:", esm.exists())
    ltex = probe_ltex(esm, tex_root)
    if args.cell:
        probe_cell(esm, tex_root, ltex, args.cell[0], args.cell[1], args.worldspace)


if __name__ == '__main__':
    main()
