"""Build a playable mod: a Skyblivion mesh on a converted Oblivion creature.

    python -m skyb_retarget.build --skyb SKYBSkinLandDreugh.nif \
        --creature <Oblivion Meshes/Creatures/LandDreugh folder>

1. fit the Oblivion skeleton onto the Skyblivion mesh (fit.py) and write it,
   ragdoll included, into a staged copy of the creature folder;
2. retarget every .kf onto the fitted skeleton, feet planted (anim.py);
3. stage a one-creature TES4 plugin (tes4_plugin.py) and run the normal
   converter on it: --import-mod, --export-only, --creatures-only, --import-only;
4. write the Skyblivion mesh as the project's body NIF (body.py);
5. zip plugin + meshes + animation fragment + CreatureRuntime.dll.

See: skyb_retarget/README.md#what-it-does
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

from asset_convert.havok.clip_retarget import Skeleton
from core.subprocess_flags import POPEN_FLAGS
from output_layout import (DEFAULT_EXPORT, DEFAULT_OUTPUT, REPO_ROOT,
                           asset_root, plugin_out_root, tree_members,
                           write_mod_zip)
from skyb_retarget import dreugh_map
from skyb_retarget.anim import first_frame_worlds, match_deltas, rewrite_kf
from skyb_retarget.body import write_body
from skyb_retarget.esp_prune import prune
from skyb_retarget.fit import (fitted_worlds, ground_lift, landmark_positions,
                               write_fitted_skeleton)
from skyb_retarget.skin_data import read_skin
from skyb_retarget.tes4_plugin import LAND_DREUGH, write_plugin

#: The SKSE plugin that registers converted creature projects at runtime.
CREATURE_DLL = REPO_ROOT / 'tes_runtime' / 'dist' / 'CreatureRuntime.dll'

#: Converter stages run on the staged plugin, in order.
STAGES = ('--export-only', '--creatures-only', '--import-only')


def fit_rig(skyb: str, creature_dir: str) -> dict:
    """Landmarks, lift, fitted skeleton and the per-clip limb solvers."""
    mesh = read_skin(skyb)
    lift = ground_lift(mesh, dreugh_map.LANDMARKS)
    mesh.verts[:, 2] += lift
    positions = landmark_positions(mesh, dreugh_map.LANDMARKS)
    src = Skeleton.from_nif(os.path.join(creature_dir, 'skeleton.nif'))
    new = fitted_worlds(src, positions, dreugh_map.SWING)
    local = np.array([new[i] if p < 0 else new[i] @ np.linalg.inv(new[p])
                      for i, p in enumerate(src.parents)])
    dst = Skeleton(src.names, src.parents, local)
    clip, blend = dreugh_map.STANCE
    stance = (first_frame_worlds(os.path.join(creature_dir, clip + '.kf'), src), blend)
    return {'lift': lift, 'src': src, 'new_world': new, 'dst': dst,
            'limbs': (dreugh_map.LEGS, match_deltas(src, dst, dreugh_map.MATCH_BONES),
                      dreugh_map.REACH + (stance,)),
            'mapping': (dreugh_map.SKIN_MAP, dreugh_map.SPLIT_BODY)}


def _weighted_bones() -> set:
    """Oblivion bones the Skyblivion weights land on."""
    split = dreugh_map.SPLIT_BODY
    return set(dreugh_map.SKIN_MAP.values()) | {split[1], split[2]}


def stage_creature(creature_dir: str, out_dir: str, rig: dict) -> dict:
    """Copy the creature folder with a fitted skeleton.nif and retargeted .kf files."""
    shutil.rmtree(out_dir, ignore_errors=True)
    shutil.copytree(creature_dir, out_dir)
    report = write_fitted_skeleton(
        os.path.join(creature_dir, 'skeleton.nif'),
        os.path.join(out_dir, 'skeleton.nif'), rig['new_world'],
        dreugh_map.SWING, _weighted_bones())
    report['clips'] = {}
    for dirpath, _dirs, files in os.walk(creature_dir):
        for fn in sorted(f for f in files if f.lower().endswith('.kf')):
            src_kf = os.path.join(dirpath, fn)
            rel = os.path.relpath(src_kf, creature_dir)
            report['clips'][rel] = rewrite_kf(
                src_kf, os.path.join(out_dir, rel), rig['src'], rig['dst'],
                rig["limbs"])
            print(f'  retargeted {rel}: {report["clips"][rel]} tracks', flush=True)
    return report


def run_converter(mod_dir: str, plugin: str) -> None:
    """Import the staged mod, then run each converter stage on its plugin.

    The plugin's previously imported meshes are cleared first: a single-source
    import layers on top, and a stale creature folder would shadow the new one.
    """
    shutil.rmtree(asset_root(DEFAULT_EXPORT, plugin) / 'meshes', ignore_errors=True)
    env = dict(os.environ, WINEDEBUG='-all')
    convert = str(REPO_ROOT / 'convert.py')
    cmds = [[sys.executable, convert, '--import-mod', mod_dir]]
    cmds += [[sys.executable, convert, '-f', plugin, stage] for stage in STAGES]
    for cmd in cmds:
        print('>>', ' '.join(cmd[1:]), flush=True)
        subprocess.run(cmd, check=True, env=env, cwd=str(REPO_ROOT), **POPEN_FLAGS)


def install_body(plugin: str, skyb: str, rig: dict) -> str:
    """Write the Skyblivion body over every body NIF the project manifest lists."""
    out = plugin_out_root(DEFAULT_OUTPUT, plugin, DEFAULT_EXPORT)
    for dirpath, _dirs, files in os.walk(out / 'meshes'):
        if 'project_manifest.json' not in files:
            continue
        with open(os.path.join(dirpath, 'project_manifest.json'), encoding='utf-8') as f:
            manifest = json.load(f)
        skeleton = os.path.join(dirpath, 'character assets', 'skeleton.nif')
        for body in manifest['bodies']:
            write_body(skyb, skeleton, os.path.join(dirpath, body), rig['lift'],
                       rig['mapping'])
            print(f'  body: {os.path.join(dirpath, body)}')
    return str(out)


def _shipped(name: str) -> bool:
    """True for what the creature needs in game: meshes and its animation fragment."""
    name = name.replace('\\', '/')
    return (name.startswith(('meshes/', 'SKSE/Plugins/CreatureRuntime/'))
            and not name.endswith('project_manifest.json'))


def package(plugin_out: str, plugin: str, zip_path: str) -> tuple:
    """Zip the pruned plugin, meshes, animation fragment and CreatureRuntime.dll.

    Returns (file count, the pruned plugin's record counts).
    """
    with open(os.path.join(plugin_out, plugin), 'rb') as f:
        esp, counts = prune(f.read())
    members = [(name, path) for name, path in tree_members(plugin_out)
               if _shipped(name)]
    members += [(plugin, esp), ('SKSE/Plugins/CreatureRuntime.dll', CREATURE_DLL)]
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)), exist_ok=True)
    return write_mod_zip(zip_path, members), counts


def main(argv=None) -> int:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--skyb', required=True, help='Skyblivion body NIF')
    ap.add_argument('--creature', required=True, help='Oblivion creature folder')
    ap.add_argument('--work', default=str(REPO_ROOT / 'temp' / 'skyb_retarget'))
    ap.add_argument('--zip', default=None, help='output zip (default: <work>/<plugin>.zip)')
    args = ap.parse_args(argv)
    plugin = LAND_DREUGH['edid'] + '.esp'
    mod_dir = os.path.join(args.work, LAND_DREUGH['edid'])
    rig = fit_rig(args.skyb, args.creature)
    folder = os.path.basename(os.path.normpath(args.creature))
    report = stage_creature(args.creature, os.path.join(
        mod_dir, 'Meshes', 'Creatures', folder), rig)
    write_plugin(os.path.join(mod_dir, plugin))
    run_converter(mod_dir, plugin)
    out = install_body(plugin, args.skyb, rig)
    zip_path = args.zip or os.path.join(args.work, LAND_DREUGH['edid'] + '.zip')
    count, report['records'] = package(out, plugin, zip_path)
    report['lift'] = rig['lift']
    with open(os.path.join(args.work, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=1)
    print(f'Packaged {count} files -> {zip_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
