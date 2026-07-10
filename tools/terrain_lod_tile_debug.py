"""Regenerate specific terrain LOD tiles in-process (single-threaded) and dump
what was produced — for iterating on tile bugs without a full worldspace run.

Usage:
    python -m tools.terrain_lod_tile_debug --tiles 4,0,0 4,-4,-4 32,-32,-32 \
        [--esm output/Oblivion.esm/Oblivion.esm] [--out output/Oblivion.esm]
        [--png-dir temp] [--worldspace TES4Tamriel]

Each --tiles entry is level,tile_x,tile_y.  Writes the .btr/.dds into the real
output tree and, with --png-dir, also saves the diffuse as PNG for inspection.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert import terrain_lod as TL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--esm', default='output/Oblivion.esm/Oblivion.esm')
    ap.add_argument('--out', default='output/Oblivion.esm')
    ap.add_argument('--worldspace', default='TES4Tamriel')
    ap.add_argument('--png-dir', default=None)
    ap.add_argument('--tiles', nargs='+', required=True,
                    metavar='LEVEL,TX,TY')
    args = ap.parse_args()

    esm = Path(args.esm)
    out_dir = Path(args.out)
    ws = args.worldspace

    lands, cell_water, default_wh = TL._parse_land_records(esm, ws)
    print(f"{len(lands)} LAND cells; "
          f"{sum(1 for hw, _ in cell_water.values() if hw)} water cells; "
          f"default water height {default_wh}")

    from asset_convert.terrain_lod_textures import build_ltex_texture_map
    ltex_map = build_ltex_texture_map(esm)
    mesh_dir = out_dir / 'meshes' / 'terrain' / ws
    tex_dir = out_dir / 'textures' / 'terrain' / ws
    mesh_dir.mkdir(parents=True, exist_ok=True)
    tex_dir.mkdir(parents=True, exist_ok=True)

    TL._worker_init(lands, str(mesh_dir), str(tex_dir), ltex_map,
                    str(out_dir / 'textures'), cell_water, default_wh)

    for spec in args.tiles:
        level, tx, ty = (int(v) for v in spec.split(','))
        tag, ok, err = TL._process_tile((tx, ty, level, ws))
        if not ok:
            print(f"FAIL {tag}: {err}")
            continue
        quads = TL._tile_water_quads(lands, cell_water, tx, ty, level, default_wh)
        print(f"OK {tag}: {len(quads)} water quads"
              + (f" heights {sorted(set(round(q[2],1) for q in quads))[:6]}"
                 if quads else ""))
        if args.png_dir:
            from PIL import Image
            png_dir = Path(args.png_dir)
            png_dir.mkdir(parents=True, exist_ok=True)
            im = Image.open(tex_dir / f'{tag}.dds').convert('RGB')
            im.save(png_dir / f'{tag}.png')
            print(f"   diffuse PNG -> {png_dir / (tag + '.png')}")


if __name__ == '__main__':
    main()
