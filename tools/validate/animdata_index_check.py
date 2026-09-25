"""Animationdata clip-index audit for generated creature projects.

THE CONTRACT (skyrim-creature-system skill, animation_cache.md 1.3): line 2
of every animationdata clip block is an index into the project's character
hkx `hkbCharacterStringData.animationNames` — the DEDUPLICATED animation
file list.  Two clips playing one file share one index; an index >= the
file count never binds.  This is "the single most fragile link in the whole
system": a broken index plays the wrong animation, and an out-of-range one
leaves the clip generator dead (the 2026-08-08 creature-ragdoll root cause:
FullyRagdollPose, the death-state pose source, was out of range in every
creature project, so no corpse ever ragdolled no matter what the behavior
graph did).

The singlefile the engine parses is composed at load by CreatureRuntime.dll
from the plugin's fragment, so this tool composes it the same way
(animcache_validate.compose_for_plugins) and then resolves each clip
block's index against the character hkx on disk, reporting:
  - indices out of range            (clip never binds)
  - motion-block indices out of range / duplicated
  - (--verbose) every clip -> file resolution, for eyeballing

Usage:
  python tools/validate/animdata_index_check.py [--plugin Oblivion.esm]
      [--project tes4oblivion_dogproject.txt] [--verbose] [--base DIR]

Exits non-zero if any generated project has an out-of-range index.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.validate.animcache_validate import (Scanner, read_string_list,
                                parse_project_block, compose_for_plugins)


def parse_singlefile(path):
    """{project_name.txt (lower): (clips, motion_lines)} using the exact
    ck-cmd wrapper grammar from animcache_validate."""
    x = open(path, encoding='latin-1').read().lstrip('\x00')
    sc = Scanner(x.splitlines())
    names = read_string_list(sc)
    out = {}
    for name in names:
        block_lines = read_string_list(sc)
        pb = parse_project_block(block_lines, name)
        motion = []
        if pb['has_cache']:
            motion = read_string_list(sc)
        out[name.lower()] = (pb['clips'], motion)
    return out


def motion_indices(motion):
    i, out = 0, []
    while i < len(motion):
        if not motion[i].strip():
            i += 1
            continue
        idx = int(motion[i])
        nt = int(motion[i + 2])
        nr = int(motion[i + 3 + nt])
        out.append(idx)
        i += 4 + nt + nr + 1
    return out


def character_anims(char_hkx):
    raw = open(char_hkx, 'rb').read()
    anims = [m.group(0).decode('latin-1')
             for m in re.finditer(rb'[ -~]{4,}\.hkx', raw, re.I)]
    return [a for a in anims if a.lower().startswith('animations')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plugin', default='Oblivion.esm')
    ap.add_argument('--project', help='single project name (.txt) to check')
    ap.add_argument('--base', help='dir with the vanilla singlefiles')
    ap.add_argument('--out-root',
                    help='read fragments from this tree instead of output/')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    meshes = os.path.join(args.out_root or 'output', args.plugin, 'meshes')
    actors = os.path.join(meshes, 'actors', 'tes4')
    if not os.path.isdir(actors):
        sys.exit(f'missing {actors}')
    composed = compose_for_plugins([args.plugin], args.base,
                                   out_root=args.out_root)
    single = os.path.join(composed, 'animationdatasinglefile.txt')

    blocks = parse_singlefile(single)
    failures = 0
    checked = 0
    for proj_dir, m in project_manifests(actors):
        proj_txt = m['project_txt'].lower()
        if args.project and proj_txt != args.project.lower():
            continue
        if proj_txt not in blocks:
            print(f'{proj_txt}: NOT REGISTERED in the composed singlefile')
            failures += 1
            continue
        char_rel = next((p for p in m.get('project_files', [])
                         if p.lower().startswith('characters')), None)
        char = os.path.join(proj_dir, *char_rel.split('\\')) if char_rel \
            else ''
        if not char or not os.path.isfile(char):
            print(f'{proj_txt}: character hkx missing, skipped')
            continue
        anims = character_anims(char)
        bad = index_problems(blocks[proj_txt], anims, args.verbose)
        checked += 1
        if bad:
            failures += 1
            print(f'{proj_txt}: {len(bad)} PROBLEMS')
            for b in bad:
                print(f'    {b}')
        elif args.verbose or args.project:
            print(f'{proj_txt}: OK ({len(anims)} files)')
    print(f'checked {checked} projects, {failures} with problems')
    return 1 if failures else 0


def project_manifests(actors):
    """[(project dir, manifest)] under actors/tes4/<namespace>/<folder>/
    (hkx_behavior.project_layout); the manifest names the project files."""
    manifests = []
    for ns in sorted(os.listdir(actors)):
        ns_dir = os.path.join(actors, ns)
        if not os.path.isdir(ns_dir):
            continue
        for folder in sorted(os.listdir(ns_dir)):
            mp = os.path.join(ns_dir, folder, 'project_manifest.json')
            if os.path.isfile(mp):
                with open(mp, encoding='utf-8') as f:
                    manifests.append((os.path.join(ns_dir, folder),
                                      json.load(f)))
    return manifests


def index_problems(block, anims, verbose):
    """Every clip or motion index of one project block outside `anims`."""
    clips, motion = block
    bad = []
    for name, idx, _ntrig in clips:
        if not (0 <= idx < len(anims)):
            bad.append(f'clip {name!r} index {idx} out of range '
                       f'(files: {len(anims)})')
        elif verbose:
            print(f'  {name:40} -> [{idx:3}] {anims[idx]}')
    mi = motion_indices(motion)
    for idx in mi:
        if not (0 <= idx < len(anims)):
            bad.append(f'motion block index {idx} out of range')
    dupes = {x for x in mi if mi.count(x) > 1}
    if dupes:
        bad.append(f'duplicate motion indices: {sorted(dupes)}')
    return bad


if __name__ == '__main__':
    sys.exit(main())
