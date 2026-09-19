"""Which converted creature project serves each CREA record.

Projects come from `creature_projects.json` (the creatures step), this
plugin's own plus every master's, keyed on the model folder's leaf name.
"""

import os

from ..base.artifact_schema import read_artifact
from ..base.text_reader import get_int, get_str
from ..overrides.nested import (export_master_names, export_root,
                                master_export_dir)


def _usable(proj) -> bool:
    """True when the project merged at least one body NIF."""
    return bool(proj.get('bodies') or proj.get('body_map'))


def load_projects(export_dir: str) -> tuple:
    """`(projects, owner_slot)`: this plugin's creature projects with its
    MASTERS' merged in underneath, and the master slot each INHERITED folder
    came from (own folders are absent from `owner_slot`).

    Own projects win on conflict, EXCEPT an own project that ships no body
    mesh, which never shadows a master's usable one.  The master's generated
    behavior project, skeleton and body NIFs are loaded by path at runtime, so
    pointing this plugin's RACE chain at them is correct.

    See: docs/commentary/tes5_import_mod_merge.md#master-export-resolution
    """
    own_path = os.path.join(export_dir, 'creature_projects.json')
    own = {}
    if os.path.exists(own_path):
        own = read_artifact(
            own_path, os.path.basename(os.path.normpath(export_dir)))

    names = export_master_names(export_dir)
    root = export_root(export_dir)
    merged, owner_slot, rescued = {}, {}, []
    for slot, name in enumerate(names):
        mpath = os.path.join(master_export_dir(root, name),
                             'creature_projects.json')
        if not os.path.exists(mpath):
            continue
        for folder, proj in read_artifact(mpath, name).items():
            if folder in merged:
                continue
            if folder in own and _usable(own[folder]):
                continue
            if folder in own and _usable(proj):
                rescued.append(folder)
            merged[folder] = proj
            owner_slot[folder] = slot
    if merged:
        print(f'  Creature projects: inherited {len(merged)} from master(s) '
              f'{", ".join(names)} (own: {len(own)})')
    if rescued:
        print(f'  Creature projects: {len(rescued)} bodyless own project(s) '
              f'superseded by the master\'s: {", ".join(sorted(rescued))}')
    for folder, proj in own.items():
        if folder in merged and not _usable(proj):
            continue
        merged[folder] = proj
    return merged, owner_slot


def folder_of(rec) -> str:
    """The creature's mesh folder token: "Creatures\\Dog\\Skeleton.NIF" -> "dog"."""
    model = (get_str(rec, 'Model.MODL') or '').replace('/', '\\')
    parts = [p for p in model.lower().split('\\') if p]
    return parts[-2] if len(parts) >= 2 else ''


def bodies_of(rec, proj):
    """The merged body NIF(s) for one CREA, or None when the project has none.

    The creature pipeline merged each CREA's NIFZ part set into ONE whole-animal
    NIF and ships the exact set->file mapping as ``body_map``, so dog / wolf /
    skeletal-hound (one folder) each point at the right mesh without re-deriving
    names here.  Falls back to the folder's first merged NIF.
    """
    nifz = [(get_str(rec, f'NIFZ[{i}]') or '').lower()
            for i in range(get_int(rec, 'NIFZCount', 0))]
    nifz = [p for p in nifz if p.endswith('.nif')]
    merged = (proj.get('body_map') or {}).get('|'.join(nifz))
    if merged:
        return [merged]
    if proj['bodies']:
        return [proj['bodies'][0]]
    return None


def folders_built_by_master(master_export: dict, projects: dict,
                            owner_slot: dict) -> set:
    """Inherited folders whose MOVT + IDLE set the owning master already wrote.

    Both bind by NAME (MOVT.MNAM, IDLE.DNAM), never by FormID, so a second
    copy in this plugin only competes with the master's.  The master wrote
    them exactly when one of ITS OWN CREA records uses the folder with a body.

    See: docs/commentary/tes5_import_mod_merge.md#inherited-creature-folders
    """
    built = set()
    for key, rec in (master_export or {}).items():
        if rec.get('Signature') != 'CREA':
            continue
        folder = folder_of(rec)
        if (owner_slot.get(folder) == int(key[:2], 16)
                and bodies_of(rec, projects[folder]) is not None):
            built.add(folder)
    return built
