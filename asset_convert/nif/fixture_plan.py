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


def fixture_model_ids(export_dir) -> dict:
    """Map placed-fixture record FormID -> mesh-relative NIF path."""
    out = {}
    export_dir = Path(export_dir)
    for name in FIXTURE_TYPES:
        for rec in iter_records(export_dir / name):
            model = rec.get('Model.MODL', '').strip()
            if model:
                out[rec.get('FormID', '')] = norm_model_path(model)
    return out


def build_fixture_models(export_dir) -> set:
    """Every mesh-relative NIF path a placed-fixture record names."""
    return set(fixture_model_ids(export_dir).values())


def _fixture_key(plan: dict, src_path, meshes_root):
    """The source NIF's mesh-relative path when a fixture names it, else None."""
    if not plan:
        return None
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except (ValueError, TypeError):
        return None
    key = norm_model_path(rel)
    return key if key in plan.get(FIXTURE_KEY, ()) else None


_LATCH = [None]


def latch_fixture_model(plan: dict, src_path, meshes_root) -> None:
    """Record the (key, plan) of the NIF about to convert when it is scenery."""
    key = _fixture_key(plan, src_path, meshes_root)
    _LATCH[0] = (key, plan) if key else None


def mesh_is_fixture() -> bool:
    """Whether the NIF being converted is named by a placed-fixture record."""
    return _LATCH[0] is not None


def latched_fixture():
    """(mesh-relative path, plan) of the fixture being converted, or None."""
    return _LATCH[0]
