"""Which converted meshes a PLACED FIXTURE record names.

Morrowind's `NC`/`NCC`/`NCO` root extra means "actors do not collide with
this", not "this has no physics": OpenMW still builds the shape and only
downgrades it to `CollisionType_VisualOnly`
(`references/openmw/apps/openmw/mwphysics/physicssystem.cpp:423`). Skyrim has
no such collision class, so honoring the flag means shipping no body at all.
That is right for scenery and wrong for anything carriable, because Morrowind
picks an item up through its REFERENCE while Skyrim picks it up through its
collision -- a bodyless MISC/APPA could never be looted.

So the flag is honored only for the record types that are placed fixtures.
The record type is the authored answer to "is this scenery?"; a mesh no
fixture record names keeps its body whatever extras it carries.
See: docs/commentary/asset_convert_nif.md#morrowind-collision
"""

import os
from pathlib import Path

from asset_convert.character.wearable_plan import iter_records, norm_model_path

#: Records for objects the world places and the player can never carry.
FIXTURE_TYPES = ('STAT.txt', 'ACTI.txt', 'LIGH.txt', 'CONT.txt', 'DOOR.txt')

#: Fixture sub-map key inside the wearable plan; norm_model_path never emits it.
FIXTURE_KEY = '*fixture_models*'


def build_fixture_models(export_dir) -> set:
    """Every mesh-relative NIF path a placed-fixture record names."""
    out = set()
    export_dir = Path(export_dir)
    for name in FIXTURE_TYPES:
        for rec in iter_records(export_dir / name):
            model = rec.get('Model.MODL', '').strip()
            if model:
                out.add(norm_model_path(model))
    return out


def is_fixture_model(plan: dict, src_path, meshes_root) -> bool:
    """Whether a placed-fixture record names this source NIF."""
    if not plan:
        return False
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except (ValueError, TypeError):
        return False
    return norm_model_path(rel) in plan.get(FIXTURE_KEY, ())


_LATCH = [False]


def latch_fixture_model(plan: dict, src_path, meshes_root) -> None:
    """Record whether the NIF about to convert is scenery, for the collision pass."""
    _LATCH[0] = is_fixture_model(plan, src_path, meshes_root)


def mesh_is_fixture() -> bool:
    """Whether the NIF being converted is named by a placed-fixture record."""
    return _LATCH[0]
