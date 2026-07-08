"""Creature conversion orchestrator: Oblivion creature folders → complete
Skyrim LE actor projects.

Per creature folder (``<export>/meshes/creatures/<name>/``), emits under
``<out_meshes>/actors/tes4/<name>/``:

  tes4<name>project.hkx / characters / behaviors / character assets/skeleton.hkx
  animations/*.hkx                      (spline-compressed, from the .kf files)
  character assets/skeleton.nif         (converted, ragdoll bhk kept on bones)
  <body part>.nif                       (converted, plain NiSkinInstance)
  project_manifest.json                 (contract for animation_data + import)

Then registers every generated project in the two merged singlefiles
(meshes/animationdatasinglefile.txt + animationsetdatasinglefile.txt — the
engine only loads projects listed there) and writes
``<export>/creature_projects.json`` for the record-side import (RACE/ARMA/
ARMO generation reads project paths, attack events and body-part lists
from it).

NPCs are NOT processed here: humanoid NPC_ records keep the Skyrim race
override system. This pipeline is for everything CREA.
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

_WORKERS = max(1, (os.cpu_count() or 4) - 3)

# Not real creatures: 'boxtest' is a Bethesda test asset; 'endgame' is the
# KFM-driven Mehrunes Dagon avatar cinematic (morph-controller NIFs PyFFI
# cannot parse — needs its own conversion path if ever wanted). The playable
# Dagon creature is the separate 'mehrunesdagon' folder, which converts.
_EXCLUDE = {'boxtest', 'endgame'}


def _convert_creature(creature_dir: str, name: str, out_meshes_dir: str,
                      fps: float = 30.0) -> dict:
    """Full conversion of one creature folder. Returns its manifest
    (with added 'skeleton_nif'/'bodies' keys) or raises."""
    from asset_convert.hkx_behavior import generate_creature_project
    from asset_convert.nif_converter import convert_nif

    manifest = generate_creature_project(creature_dir, name, out_meshes_dir,
                                         fps=fps)
    proj_dir = os.path.join(out_meshes_dir, 'actors', 'tes4', name.lower())

    bodies, nif_failures = [], []
    for fn in sorted(os.listdir(creature_dir)):
        if not fn.lower().endswith('.nif'):
            continue
        is_skeleton = fn.lower().startswith('skeleton')
        if is_skeleton:
            dst = os.path.join(proj_dir, 'character assets', fn.lower())
        else:
            dst = os.path.join(proj_dir, fn.lower())
        res = convert_nif(os.path.join(creature_dir, fn), dst, creature=True)
        if res.get('error'):
            nif_failures.append((fn, res['error']))
            continue
        if not is_skeleton:
            bodies.append(fn.lower())

    manifest['skeleton_nif'] = \
        f'actors\\tes4\\{name.lower()}\\character assets\\skeleton.nif'
    manifest['bodies'] = bodies
    manifest['nif_failures'] = nif_failures

    # keep the on-disk manifest in sync (includes the mesh keys)
    with open(os.path.join(proj_dir, 'project_manifest.json'), 'w',
              encoding='utf-8') as f:
        json.dump(manifest, f)
    return manifest


def convert_creatures(export_dir: str, out_meshes_dir: str,
                      skyrim_data_path: str = None,
                      names: list = None, workers: int = None,
                      log=print) -> dict:
    """Convert every creature folder under <export_dir>/meshes/creatures.

    Writes the actor projects + converted meshes, merges the animation
    singlefiles (vanilla base from the user's Skyrim install, cached in
    <export_dir>/animdata_base), and saves <export_dir>/creature_projects.json.

    Returns {'projects': {name: manifest}, 'errors': {name: str}}.
    """
    from asset_convert.animation_data import write_singlefiles

    creatures_root = os.path.join(export_dir, 'meshes', 'creatures')
    if not os.path.isdir(creatures_root):
        log(f'  No creatures folder at {creatures_root}')
        return {'projects': {}, 'errors': {}}

    dirs = []
    for name in sorted(os.listdir(creatures_root)):
        cdir = os.path.join(creatures_root, name)
        if not os.path.isdir(cdir):
            continue
        if names and name.lower() not in {n.lower() for n in names}:
            continue
        if name.lower() in _EXCLUDE and not names:
            log(f'  [skip] {name}: excluded (test/cinematic asset)')
            continue
        if not os.path.exists(os.path.join(cdir, 'skeleton.nif')):
            log(f'  [skip] {name}: no skeleton.nif')
            continue
        if not any(f.lower().endswith('.kf') for f in os.listdir(cdir)):
            log(f'  [skip] {name}: no animations')
            continue
        dirs.append((cdir, name))

    log(f'  Converting {len(dirs)} creatures '
        f'({workers or _WORKERS} workers)...')
    projects, errors = {}, {}
    with ThreadPoolExecutor(max_workers=workers or _WORKERS) as pool:
        futs = {pool.submit(_convert_creature, cdir, name, out_meshes_dir):
                name for cdir, name in dirs}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                manifest = fut.result()
            except Exception as e:
                errors[name] = f'{type(e).__name__}: {e}'
                log(f'  [FAIL] {name}: {errors[name]}')
                continue
            projects[name] = manifest
            n_fail = len(manifest['failures']) + len(manifest['nif_failures'])
            log(f'  [ok] {name}: {len(manifest["clips"])} clips, '
                f'{len(manifest["bodies"])} body nifs'
                + (f', {n_fail} failures' if n_fail else ''))

    # Registration: merged singlefiles (vanilla base + all our projects).
    if projects:
        cache_dir = os.path.join(export_dir, 'animdata_base')
        manifests = [projects[n] for n in sorted(projects)]
        counts = write_singlefiles(manifests, out_meshes_dir,
                                   skyrim_data_path, cache_dir)
        log(f'  Registered {len(manifests)} projects '
            f'(animationdatasinglefile: {counts["animationdatasinglefile.txt"]}'
            f' total projects)')

    # Contract for tes5_import (RACE/ARMA/ARMO generation).
    summary = {name: {
        'project_hkx': m['project_hkx'],
        'skeleton_nif': m['skeleton_nif'],
        'bodies': m['bodies'],
        'attacks': m['attacks'],
        'clips': [c['name'] for c in m['clips']],
        'bones': m['bones'],
    } for name, m in projects.items()}
    with open(os.path.join(export_dir, 'creature_projects.json'), 'w',
              encoding='utf-8') as f:
        json.dump(summary, f, indent=1)

    return {'projects': projects, 'errors': errors}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description='Convert Oblivion creatures to Skyrim actor projects')
    ap.add_argument('export_dir', help='export/<plugin> directory')
    ap.add_argument('out_meshes_dir', help='output meshes/ directory')
    ap.add_argument('--skyrim-data', help='Skyrim Data folder (for the '
                    'vanilla animation singlefile merge base)')
    ap.add_argument('--names', nargs='+', help='only these creature folders')
    ap.add_argument('--workers', type=int)
    args = ap.parse_args()

    out = convert_creatures(args.export_dir, args.out_meshes_dir,
                            skyrim_data_path=args.skyrim_data,
                            names=args.names, workers=args.workers)
    print(f"{len(out['projects'])} projects, {len(out['errors'])} errors")
    for name, err in out['errors'].items():
        print(f'  {name}: {err}')
