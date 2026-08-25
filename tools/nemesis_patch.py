#!/usr/bin/env python3
"""Keep converted creature projects alive in a load order that runs Nemesis.

Nemesis regenerates ``meshes\\animationdatasinglefile.txt`` /
``animationsetdatasinglefile.txt`` into its output mod, and that copy wins the
conflict. It builds them from ITS OWN baseline, so every project this converter
generates falls out. In game the behaviour graph still loads (the actor is
visible, its idle plays) but no clip has metadata, so creatures slide at their
MOVT speed with no locomotion animation and never attack.

Subcommands:

  baseline --nemesis <nemesis-mod-dir> --out <mod-root>  [the recommended route]
      Write ``meshes/nemesis_animationdatasinglefile.txt`` and its setdata twin
      into OUR mod = Nemesis's originals + our projects. ``--nemesis`` takes the
      Nemesis MOD folder; ``meshes`` is appended in code. Nemesis reads that pair
      as its vanilla baseline (it walks ``<Data>/meshes`` for the ``nemesis_``
      prefix), so every creature -- Skyrim's and ours -- survives every
      regeneration. The Nemesis install is only READ, never modified.

      Load order: our mod AFTER "Nemesis Unlimited Behavior Engine" so our
      baseline wins, and BEFORE "Nemesis Output" so the pair the GAME reads is
      the freshly generated one. Reversed, creatures still work but every other
      animation mod's entries are lost.

  find <mods-root>
      List every copy of both pairs under a mod manager's mods folder, with
      project counts and how many are ours. Which copy wins is a property of
      the mod manager, so it is reported rather than guessed.

  inject --base <dir>
      After-the-fact merge into whichever GAME-facing pair currently wins.
      Must be re-run after every Nemesis regeneration; `baseline` need not be.

Usage:
  python tools/nemesis_patch.py find "D:/Skyrim SE Mod/mods"
  python tools/nemesis_patch.py baseline --out output/Oblivion.esm \
      --nemesis "D:/mods/Nemesis Unlimited Behavior Engine"
  python tools/nemesis_patch.py inject --base "D:/.../Nemesis Output/meshes"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import nemesis  # noqa: E402


def _manifests(output_root):
    ms = nemesis.load_all_manifests(output_root)
    if not ms:
        print(f'no generated creature projects under {output_root!r} -- '
              f'run the creatures step first')
    return ms


def cmd_detect(args):
    from asset_convert.skyrim_assets import find_skyrim_data
    cands = nemesis.autodetect(find_skyrim_data())
    if not cands:
        print('no Nemesis installation found')
        return 1
    print(f'{len(cands)} Nemesis baseline(s) found, best first:')
    for i, (path, source, total, ours) in enumerate(cands):
        tag = 'pristine' if ours == 0 else f'already holds {ours} of ours'
        print(f'  {"->" if i == 0 else "  "} {path}')
        print(f'       {total} projects, {tag} -- {source}')
    return 0


def cmd_find(args):
    caches = nemesis.find_caches(args.root, args.depth)
    bases = nemesis.find_nemesis_baseline(args.root, args.depth)
    if not caches and not bases:
        print(f'nothing found under {args.root}')
        return 1
    if bases:
        print('NEMESIS BASELINE (nemesis_*singlefile.txt) -- what Nemesis reads')
        print(f'{"projects":>9} {"ours":>6}  dir')
        for d, n, gen in bases:
            print(f'{n:>9} {gen:>6}  {d}')
        print()
    if caches:
        print('GAME-FACING (animationdatasinglefile.txt) -- what Skyrim reads')
        print(f'{"projects":>9} {"ours":>6}  path')
        for p, n, gen in caches:
            print(f'{n:>9} {gen:>6}  {p}')
    return 0


def cmd_baseline(args):
    ms = _manifests(args.output_root)
    if not ms:
        return 1
    src = args.nemesis
    if not src:
        from asset_convert.skyrim_assets import find_skyrim_data
        if args.search:
            found = nemesis.find_nemesis_baseline(args.search, args.depth)
            cands = [(d, f'under {args.search}', n, gen)
                     for d, n, gen in found]
        else:
            cands = nemesis.autodetect(find_skyrim_data())
        if not cands:
            print('no Nemesis installation found; pass --nemesis <the Nemesis '
                  'Unlimited Behavior Engine mod folder>')
            return 1
        for i, (path, source, total, ours) in enumerate(cands):
            tag = 'pristine' if ours == 0 else f'already holds {ours} of ours'
            print(f'  {"->" if i == 0 else "  "} {path}')
            print(f'       {total} projects, {tag} -- {source}')
        src = cands[0][0]
        print(f'using {src}')
    out = os.path.join(args.out, 'meshes')
    print(f'{len(ms)} generated projects')
    nemesis.write_baseline_override(ms, src, out)
    return 0


def cmd_inject(args):
    ms = _manifests(args.output_root)
    if not ms:
        return 1
    print(f'{len(ms)} generated projects')
    nemesis.inject_into_cache(ms, args.base, args.out)
    print('done -- re-run this after every Nemesis regeneration')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('detect',
                   help='auto-detect Nemesis installs on this machine'
                   ).set_defaults(fn=cmd_detect)

    s = sub.add_parser('find', help='locate every cache/baseline copy')
    s.add_argument('root')
    s.add_argument('--depth', type=int, default=6)
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser('baseline', help="override Nemesis's own baseline")
    s.add_argument('--nemesis', default=None,
                   help='the Nemesis Unlimited Behavior Engine MOD folder '
                        '(its meshes subfolder is resolved automatically)')
    s.add_argument('--search', default=None,
                   help='folder to auto-detect the baseline under')
    s.add_argument('--out', default='output/Oblivion.esm',
                   help='our mod data root (the folder holding meshes/)')
    s.add_argument('--output-root', default='output')
    s.add_argument('--depth', type=int, default=6)
    s.set_defaults(fn=cmd_baseline)

    s = sub.add_parser('inject', help='merge into the winning game-facing pair')
    s.add_argument('--base', required=True,
                   help="folder holding the pair (Nemesis output 'meshes')")
    s.add_argument('--out', default=None,
                   help='write elsewhere (default: in place)')
    s.add_argument('--output-root', default='output')
    s.set_defaults(fn=cmd_inject)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())
