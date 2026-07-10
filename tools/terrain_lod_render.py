"""Render / validate terrain LOD for a converted worldspace.

Produces a top-down PNG over a cell region that overlays, side by side:
  1. HEIGHTMAP  — full-res LAND heights (hillshade) from the converted ESM
  2. DIFFUSE    — composited landscape-texture diffuse (the terrain LOD atlas)
  3. LOD MESH   — the generated .btr mesh heights (hillshade), same region,
                  so LOD topology can be compared against the source terrain.

This is the primary iteration tool for terrain LOD, mirroring
tools/navmesh_render.py and tools/spt_preview.py.

Usage:
  python tools/terrain_lod_render.py --esm output/oblivion.esm/oblivion.esm \
      --worldspace TES4Tamriel --cell -8 -8 --radius 8 --out temp/lod_check.png
  # or a whole worldspace overview (downsampled):
  python tools/terrain_lod_render.py --esm ... --overview --out temp/overview.png
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from asset_convert import pyffi_monkey_patch as _patch  # noqa: F401
from asset_convert import terrain_lod as TL
from asset_convert import terrain_lod_textures as TLT


def hillshade(h: np.ndarray, az=315.0, alt=45.0) -> np.ndarray:
    """Return a uint8 hillshade image from a height grid."""
    h = np.nan_to_num(h, nan=float(np.nanmin(h)) if np.isfinite(np.nanmin(h)) else 0.0)
    gy, gx = np.gradient(h)
    slope = np.pi / 2.0 - np.arctan(np.hypot(gx, gy) * 0.02)
    aspect = np.arctan2(-gx, gy)
    az_r = np.radians(360.0 - az + 90.0)
    alt_r = np.radians(alt)
    shaded = (np.sin(alt_r) * np.sin(slope) +
              np.cos(alt_r) * np.cos(slope) * np.cos(az_r - aspect))
    shaded = np.clip(shaded, 0, 1)
    return (shaded * 255).astype(np.uint8)


def _region_grid(lands, min_x, min_y, max_x, max_y, per_cell):
    """Assemble a full-res height grid over the cell region [min..max]."""
    nx = (max_x - min_x + 1)
    ny = (max_y - min_y + 1)
    W = nx * per_cell
    H = ny * per_cell
    out = np.full((H, W), np.nan, dtype=np.float32)
    for (cx, cy), land in lands.items():
        if not (min_x <= cx <= max_x and min_y <= cy <= max_y):
            continue
        h = land['heights']  # 33x33
        # subsample/resize to per_cell
        step = max(1, 32 // per_cell)
        hs = h[::step, ::step][:per_cell, :per_cell]
        # image row 0 = top = +Y(north): flip Y so higher cy is higher up
        col0 = (cx - min_x) * per_cell
        row0 = (max_y - cy) * per_cell
        out[row0:row0+hs.shape[0], col0:col0+hs.shape[1]] = np.flipud(hs)
    return out


def _region_diffuse(lands, cell_water, default_wh, ltex_map, tex_root,
                    min_x, min_y, max_x, max_y, per_cell):
    W = (max_x - min_x + 1) * per_cell
    H = (max_y - min_y + 1) * per_cell
    out = np.full((H, W, 3), 40, dtype=np.uint8)
    for (cx, cy), land in lands.items():
        if not (min_x <= cx <= max_x and min_y <= cy <= max_y):
            continue
        wh = TL._cell_water_height(cell_water, (cx, cy), default_wh)
        img = TLT.composite_cell(land['layers'], land.get('colors'), ltex_map,
                                 tex_root, cx, cy, cell_px=per_cell,
                                 heights=land['heights'], water_height=wh)
        col0 = (cx - min_x) * per_cell
        row0 = (max_y - cy) * per_cell
        out[row0:row0+per_cell, col0:col0+per_cell] = img
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--esm', required=True)
    ap.add_argument('--worldspace', default='TES4Tamriel')
    ap.add_argument('--cell', nargs=2, type=int, metavar=('X', 'Y'),
                    help='center cell')
    ap.add_argument('--radius', type=int, default=6, help='cells around center')
    ap.add_argument('--overview', action='store_true',
                    help='render the whole worldspace (downsampled)')
    ap.add_argument('--out', default='temp/lod_check.png')
    ap.add_argument('--btr-dir', default=None,
                    help='generated .btr dir to overlay LOD-mesh heights')
    args = ap.parse_args()

    esm = Path(args.esm)
    tex_root = esm.parent / 'textures'

    print(f"Parsing LAND from {esm} (worldspace {args.worldspace})...")
    lands, cell_water, default_wh = TL._parse_land_records(esm, args.worldspace)
    print(f"  {len(lands)} LAND cells; "
          f"{sum(1 for hw, _ in cell_water.values() if hw)} water cells")
    if not lands:
        print("No LAND records; abort")
        return

    ltex_map = TLT.build_ltex_texture_map(esm)
    print(f"  {len(ltex_map)} LTEX textures resolved")

    all_x = [k[0] for k in lands]
    all_y = [k[1] for k in lands]
    if args.overview or not args.cell:
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        per_cell = 4 if args.overview else 16
    else:
        cx, cy = args.cell
        r = args.radius
        min_x, max_x = cx - r, cx + r
        min_y, max_y = cy - r, cy + r
        per_cell = 32

    print(f"  Region X=[{min_x},{max_x}] Y=[{min_y},{max_y}] per_cell={per_cell}")

    hmap = _region_grid(lands, min_x, min_y, max_x, max_y, per_cell)
    shade = hillshade(hmap)
    diffuse = _region_diffuse(lands, cell_water, default_wh, ltex_map, tex_root,
                              min_x, min_y, max_x, max_y, per_cell)

    from PIL import Image
    shade_rgb = np.stack([shade]*3, axis=-1)

    panels = [("Heightmap (hillshade)", shade_rgb),
              ("LOD diffuse (composited)", diffuse)]

    W = shade_rgb.shape[1]
    H = shade_rgb.shape[0]
    gap = 12
    total_w = W * len(panels) + gap * (len(panels) - 1)
    canvas = np.full((H, total_w, 3), 20, dtype=np.uint8)
    for i, (_, panel) in enumerate(panels):
        x0 = i * (W + gap)
        canvas[:, x0:x0+W] = panel

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(canvas).save(out)
    print(f"Wrote {out} ({total_w}x{H})")
    for i, (label, _) in enumerate(panels):
        print(f"  panel {i}: {label}")


if __name__ == '__main__':
    main()
