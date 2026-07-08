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
                      part_sets: list = None, fps: float = 30.0) -> dict:
    """Full conversion of one creature folder. Returns its manifest
    (with added 'skeleton_nif'/'bodies' keys) or raises.

    part_sets: the distinct NIFZ part groupings that CREA records in this
    folder actually use (e.g. dog's [('dogbody.nif','doghead.nif',
    'dogeyes01.nif'), ('wolfbody.nif',...), ...]).  Each set is merged into
    ONE skinned NIF named after its body part (vanilla one-file layout).  If
    None, every .nif in the folder is treated as one set."""
    from asset_convert.hkx_behavior import generate_creature_project
    from asset_convert.hkx_xml import convert_hkx_to_amd64
    from asset_convert.nif_converter import convert_nif, merge_creature_body

    manifest = generate_creature_project(creature_dir, name, out_meshes_dir,
                                         fps=fps)
    proj_dir = os.path.join(out_meshes_dir, 'actors', 'tes4', name.lower())

    # SSE only loads 64-bit havok files: a 32-bit project makes the engine
    # silently fail the behavior-graph load → invisible actor (collision
    # capsule still works).  Generation/validation above is 32-bit WIN32
    # (hkxcmd can't read AMD64 back), so convert everything in place LAST.
    for dirpath, _dirs, files in os.walk(proj_dir):
        for fn in files:
            if fn.lower().endswith('.hkx'):
                convert_hkx_to_amd64(os.path.join(dirpath, fn))

    # Convert every non-skeleton part NIF once (keyed lowercase filename).
    converted = {}       # lower filename -> converted dst path
    nif_failures = []
    for fn in sorted(os.listdir(creature_dir)):
        if not fn.lower().endswith('.nif'):
            continue
        is_skeleton = fn.lower().startswith('skeleton')
        if is_skeleton:
            dst = os.path.join(proj_dir, 'character assets', fn.lower())
            convert_nif(os.path.join(creature_dir, fn), dst, creature=True)
            continue
        dst = os.path.join(proj_dir, fn.lower())
        res = convert_nif(os.path.join(creature_dir, fn), dst, creature=True)
        if res.get('error'):
            nif_failures.append((fn, res['error']))
            continue
        converted[fn.lower()] = dst

    # A single Oblivion creature folder holds several DISTINCT creatures (dog,
    # wolf, skeletal-hound) each with its own NIFZ part set.  Merge EACH set
    # into one skinned NIF (whole animal under one root), the vanilla layout —
    # the engine renders only the single BODY-slot ARMA, so separate head/eyes
    # NIFs never show.  The merged file is named after the set's body part.
    if not part_sets:
        part_sets = [tuple(converted.keys())] if converted else []
    bodies = []          # merged NIF filenames (one per distinct part set)
    used = set()
    for pset in part_sets:
        paths = [converted[p] for p in pset if p in converted]
        if not paths:
            continue
        # name the merged file after the part with the most bones (body)
        stem = os.path.splitext(os.path.basename(pset[0]))[0]
        merged_name = f'{stem}.nif'
        merged_dst = os.path.join(proj_dir, merged_name)
        try:
            merge_creature_body(paths, merged_dst)
            bodies.append(merged_name)
            used.update(os.path.abspath(p) for p in paths)
        except Exception as e:
            nif_failures.append((merged_name, f'{type(e).__name__}: {e}'))
            bodies.append(os.path.basename(paths[0]))
            used.update(os.path.abspath(p) for p in paths[1:])

    # Drop the now-redundant individual part NIFs (kept only if a merge reused
    # the file in place or a part belonged to no set).
    merged_abs = {os.path.abspath(os.path.join(proj_dir, b)) for b in bodies}
    for p in used:
        if p not in merged_abs and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    manifest['skeleton_nif'] = \
        f'actors\\tes4\\{name.lower()}\\character assets\\skeleton.nif'
    manifest['bodies'] = bodies
    manifest['nif_failures'] = nif_failures

    # keep the on-disk manifest in sync (includes the mesh keys)
    with open(os.path.join(proj_dir, 'project_manifest.json'), 'w',
              encoding='utf-8') as f:
        json.dump(manifest, f)
    return manifest


def _part_sets_by_folder(export_dir: str) -> dict:
    """folder(lower) -> list of distinct NIFZ part sets (each a tuple of
    lowercase .nif filenames), read from the CREA export.

    A single creature folder holds several distinct creatures (dog/wolf/
    skeletal-hound) each listing its own body parts in NIFZ.  Each distinct
    set is merged into its own whole-animal NIF, so the record side can point
    each CREA at the right merged mesh."""
    from tes5_import.text_reader import parse_export_file

    crea_path = os.path.join(export_dir, 'CREA.txt')
    if not os.path.exists(crea_path):
        return {}
    out = {}
    for rec in parse_export_file(crea_path):
        model = (rec.get('Model.MODL') or '').replace('/', '\\')
        parts = [p for p in model.lower().split('\\') if p]
        folder = parts[-2] if len(parts) >= 2 else ''
        if not folder:
            continue
        n = int(rec.get('NIFZCount', 0) or 0)
        pset = tuple((rec.get(f'NIFZ[{i}]') or '').lower()
                     for i in range(n))
        pset = tuple(p for p in pset if p.endswith('.nif'))
        if pset:
            out.setdefault(folder, [])
            if pset not in out[folder]:
                out[folder].append(pset)
    return out


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

    # Distinct NIFZ part sets per folder (dog/wolf/skeletal-hound share a
    # folder but each merges into its own whole-animal NIF).
    part_sets = _part_sets_by_folder(export_dir)

    log(f'  Converting {len(dirs)} creatures '
        f'({workers or _WORKERS} workers)...')
    projects, errors = {}, {}
    with ThreadPoolExecutor(max_workers=workers or _WORKERS) as pool:
        futs = {pool.submit(_convert_creature, cdir, name, out_meshes_dir,
                            part_sets.get(name.lower())):
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

    # Registration: merged singlefiles (vanilla base + ALL projects on disk).
    # A subset run (--names) must not drop the other creatures' registrations,
    # so pick up every previously generated project_manifest.json too.
    all_manifests = dict(projects)
    actors_root = os.path.join(out_meshes_dir, 'actors', 'tes4')
    if os.path.isdir(actors_root):
        for d in sorted(os.listdir(actors_root)):
            if d in all_manifests:
                continue
            mp = os.path.join(actors_root, d, 'project_manifest.json')
            if os.path.exists(mp):
                with open(mp, encoding='utf-8') as f:
                    all_manifests[d] = json.load(f)

    if all_manifests:
        cache_dir = os.path.join(export_dir, 'animdata_base')
        manifests = [all_manifests[n] for n in sorted(all_manifests)]
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
    } for name, m in all_manifests.items()}
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
