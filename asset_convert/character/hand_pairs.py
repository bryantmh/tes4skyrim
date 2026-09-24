"""
A two-handed wearable's worn and ground models split into a left and a right half.

See: docs/commentary/asset_convert_armor.md#split-pair-gauntlets
"""

import os

from asset_convert.character.body_slots import write_hand_side
from asset_convert.character.ground_halves import write_ground_halves

#: Folder under meshes\ the split halves are written to, mirroring the source path.
SPLIT_DIR = 'armor' + chr(92) + 'split'

#: Export keys of the worn models a pair item names.
WORN_KEYS = ('Male.BipedModel.MODL', 'Female.BipedModel.MODL')

#: Export key of each ground model a pair item names -> the worn model its hands come from.
GROUND_KEYS = {'Male.WorldModel.MODL': WORN_KEYS[0], 'Female.WorldModel.MODL': WORN_KEYS[1]}

#: Ends a ground half's name, so the mesh converts as a ground model.
GROUND_SUFFIX = '_gnd'


def split_paths(model: str, suffix: str = '') -> tuple:
    """(left, right) meshes-relative paths of a model's halves, ending `suffix`."""
    stem = os.path.splitext(_rel(model))[0]
    return tuple(f'{SPLIT_DIR}{chr(92)}{stem}_{side}{suffix}.nif' for side in ('left', 'right'))


def split_models(models: dict, roots, out_meshes, log=print) -> dict:
    """{export key: (left path, right path)} for every worn and ground model in `models` ({export key: path}).

    Empty unless every worn model is found under `roots` and splits into two
    non-empty hands, written under `out_meshes`. A ground model that cannot
    be halved is left out, so both halves keep it whole.
    """
    out = _split_worn(models, roots, out_meshes, log)
    for key, worn_key in GROUND_KEYS.items() if out else ():
        model = models.get(key)
        ground = _find(model, roots)
        worn = _find(models.get(worn_key) or models.get(WORN_KEYS[0]), roots)
        halves = split_paths(model or '', GROUND_SUFFIX)
        if ground and worn and write_ground_halves(ground, worn, _targets(halves, out_meshes)):
            out[key] = halves
        elif model:
            log(f'  [pair] {model}: ground model does not split into two hands')
    return out


def _split_worn(models: dict, roots, out_meshes, log) -> dict:
    """{worn key: (left path, right path)}; empty unless every worn model splits into two hands."""
    out = {}
    for key in WORN_KEYS:
        model = models.get(key)
        if not model:
            continue
        source = _find(model, roots)
        halves = split_paths(model)
        if source is None or not all(write_hand_side(source, target, right)
                                     for target, right in zip(_targets(halves, out_meshes),
                                                              (False, True))):
            log(f'  [pair] {model}: worn model does not split into two hands')
            return {}
        out[key] = halves
    return out


def _targets(halves: tuple, out_meshes) -> list:
    """The files under `out_meshes` the meshes-relative `halves` are written to."""
    return [os.path.join(str(out_meshes), *p.split(chr(92))) for p in halves]


def _rel(model: str) -> str:
    """`model` as a lowercase meshes-relative path."""
    rel = model.replace('/', chr(92)).strip().lower().lstrip(chr(92))
    return rel[len('meshes' + chr(92)):] if rel.startswith('meshes' + chr(92)) else rel


def _find(model, roots):
    """The first file under `roots` holding the meshes-relative `model`, or None (also for no model)."""
    if not model:
        return None
    for root in roots:
        path = os.path.join(str(root), *_rel(model).split(chr(92)))
        if os.path.isfile(path):
            return path
    return None
