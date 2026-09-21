"""Which converted meshes a DOOR record names, so they can be animated.

Morrowind stores no animation in a door mesh: the engine swings the whole
REFERENCE 90 degrees about Z over one second. Skyrim has no such mechanism --
its doors carry `Open`/`Close` sequences inside the NIF -- so a converted
Morrowind door is a static prop until the sequences are synthesised, and the
record type is the authored answer to "is this mesh a door?".
See: docs/commentary/asset_convert_nif.md#morrowind-door-animation
"""

import os
from pathlib import Path

from asset_convert.character.wearable_plan import iter_records, norm_model_path

#: Door sub-map key inside the wearable plan; norm_model_path never emits it.
DOOR_KEY = '*door_models*'


def build_door_models(export_dir) -> set:
    """Every mesh-relative NIF path a DOOR record names."""
    out = set()
    for rec in iter_records(Path(export_dir) / 'DOOR.txt'):
        model = rec.get('Model.MODL', '').strip()
        if model:
            out.add(norm_model_path(model))
    return out


def is_door_model(plan: dict, src_path, meshes_root) -> bool:
    """Whether a DOOR record names this source NIF."""
    if not plan:
        return False
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except (ValueError, TypeError):
        return False
    return norm_model_path(rel) in plan.get(DOOR_KEY, ())


_LATCH = [False]


def latch_door_model(plan: dict, src_path, meshes_root) -> None:
    """Record whether the NIF about to convert is a door, for the anim pass."""
    _LATCH[0] = is_door_model(plan, src_path, meshes_root)


def mesh_is_door() -> bool:
    """Whether the NIF being converted is named by a DOOR record."""
    return _LATCH[0]
