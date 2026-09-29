"""Audit SPT leaf-texture resolution: which trees end up with NO leaf texture.

The SPT stores the ARTIST'S .tga path, which is often not the .dds that
shipped. `spt_converter._match_tex_stem` handles the known renamings, and the
TREE record's ICON is tried first; when everything fails the NIF is written
with no leaf texture at all and the foliage renders untextured in-game.

Mirrors `convert_spt_directory` exactly -- same manifest, same texture index,
and the same MASTER tree-texture merge (a dependent plugin's trees use art its
master ships, so auditing only this plugin's textures reports false misses).

Usage:
    python tools/lod/spt_leaf_tex_audit.py                   # every export/ plugin
    python tools/lod/spt_leaf_tex_audit.py -f TamRes.esm     # one plugin
    python tools/lod/spt_leaf_tex_audit.py --show-ok         # list resolved too
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from asset_convert.speedtree.spt_converter import (
    match_tex_stem, tex_index, load_tree_manifest)
from asset_convert.speedtree.spt_parser import parse_spt, SptParseError
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from output_layout import paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from output_layout import assets_for


def master_names(export_dir: Path):
    """Master plugin names from the export header, in order."""
    out = []
    h = export_dir / '_HEADER.txt'
    if h.is_file():
        for line in open(h, encoding='utf-8', errors='replace'):
            if line.startswith('Master['):
                name = line.partition('=')[2].strip()
                if name:
                    out.append(name)
    return out


def build_tex_index(export_dir: Path, export_root: Path) -> dict:
    """Texture index for this plugin, then its masters' nearest first (as the converter)."""
    idx = tex_index(assets_for(export_dir) / 'textures' / 'trees')
    for m in reversed(master_names(export_dir)):
        mtex = paths(m, export_root=export_root).assets / 'textures' / 'trees'
        if mtex.is_dir():
            for stem, sub in tex_index(mtex).items():
                idx.setdefault(stem, sub)
    return idx


def audit(export_dir: Path, export_root: Path, show_ok=False):
    trees = assets_for(export_dir) / 'trees'
    if not trees.is_dir():
        return 0, 0, []
    idx = build_tex_index(export_dir, export_root)
    man = load_tree_manifest(export_dir)
    ok = bad = 0
    broken = []
    for spt in sorted(trees.rglob('*.spt')):
        try:
            t = parse_spt(spt)
        except SptParseError as e:
            broken.append((spt.name, 'PARSE FAIL', str(e)[:70]))
            bad += 1
            continue
        icons = [e[1] for e in man.get(spt.stem.lower(), [])]
        cands = icons + [t.composite_map] + [m.texture for m in t.leaf_maps]
        hit = next((s for s in (match_tex_stem(c, idx) for c in cands if c)
                    if s is not None), None)
        if hit is None:
            wanted = sorted({Path(str(m.texture).replace(chr(92), '/')).stem.lower()
                             for m in t.leaf_maps})
            broken.append((spt.name, 'NO LEAF TEXTURE',
                           ','.join(wanted) if wanted else '(no leaf maps)'))
            bad += 1
        else:
            ok += 1
            if show_ok:
                print(f'    ok   {spt.name}: {hit}')
    return ok, bad, broken


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-f', '--files', nargs='+', metavar='PLUGIN',
                    help='plugin/mod names under export/ (default: all)')
    ap.add_argument('--export-root', default='export')
    ap.add_argument('--show-ok', action='store_true')
    args = ap.parse_args()

    root = Path(args.export_root)
    dirs = ([root / f for f in args.files] if args.files
            else sorted(d for d in root.iterdir() if (d / 'trees').is_dir()))

    tot_ok = tot_bad = 0
    for d in dirs:
        ok, bad, broken = audit(d, root, args.show_ok)
        if ok or bad:
            print(f'== {d.name}: {ok} resolved, {bad} MISSING', flush=True)
            for name, kind, detail in broken:
                print(f'    {kind}  {name}  wanted: {detail}', flush=True)
        tot_ok += ok
        tot_bad += bad
    print()
    print(f'TOTAL: {tot_ok} resolved, {tot_bad} with no leaf texture')
    return 1 if tot_bad else 0


if __name__ == '__main__':
    sys.exit(main())
