"""Which converted meshes Skyrim should simulate as dynamic Havok clutter.

Morrowind NIFs carry no Havok data at all, so `nif_converter_morrowind` builds
a static body over the RootCollisionNode geometry.  That leaves every cup,
ingredient and weapon welded to the world.

The record type is the authored answer to "is this a loose object?", and
DATA.Weight the authored mass.
See: docs/commentary/asset_convert_collision.md#morrowind-dynamic-clutter
"""

import os
from pathlib import Path

from asset_convert.character.wearable_plan import iter_records, norm_model_path

#: Item records whose one Model.MODL is both the carried and the dropped mesh.
CLUTTER_TYPES = ('MISC.txt', 'INGR.txt', 'ALCH.txt', 'APPA.txt',
                 'WEAP.txt', 'BOOK.txt', 'KEYM.txt', 'AMMO.txt', 'SLGM.txt')

#: Wearables, whose WORLD model is the dropped item; the biped mesh is skinned.
WEARABLE_TYPES = ('ARMO.txt', 'CLOT.txt')

#: The dropped-item model fields on a wearable record.
_WORLD_MODEL_KEYS = ('Male.WorldModel.MODL', 'Female.WorldModel.MODL')

#: Clutter sub-map key inside the wearable plan; norm_model_path never emits it.
CLUTTER_KEY = '*clutter_mass*'

#: Mass (kg) for an item whose record ships no usable weight.
_DEFAULT_MASS = 1.0

#: Floor for authored weights; Havok reads a mass-0 dynamic body as immovable.
_MIN_MASS = 0.1

#: Vanilla's heaviest simulated clutter body (248-body census of meshes/clutter).
_MAX_MASS = 100.0


def _record_mass(rec) -> float:
    """The authored DATA.Weight of one item record, clamped to vanilla's range.

    See: docs/commentary/asset_convert_collision.md#morrowind-dynamic-clutter
    """
    try:
        mass = float(rec.get('DATA.Weight', '') or 0.0)
    except ValueError:
        mass = 0.0
    return min(max(mass, _MIN_MASS) if mass > 0 else _DEFAULT_MASS, _MAX_MASS)


def build_clutter_masses(export_dir) -> dict:
    """Map mesh-relative NIF path -> authored mass (kg) for every item model.

    A mesh named by several records keeps the LARGEST weight: one model is
    reused across value tiers and the heaviest is the safest to simulate.
    """
    out: dict = {}
    export_dir = Path(export_dir)
    for name in CLUTTER_TYPES + WEARABLE_TYPES:
        keys = (_WORLD_MODEL_KEYS if name in WEARABLE_TYPES
                else ('Model.MODL',))
        for rec in iter_records(export_dir / name):
            mass = _record_mass(rec)
            for field in keys:
                model = rec.get(field, '').strip()
                if model:
                    key = norm_model_path(model)
                    out[key] = max(out.get(key, 0.0), mass)
    return out


def clutter_mass_for(plan: dict, src_path, meshes_root):
    """Authored mass for a source NIF, or None when no item record names it."""
    if not plan:
        return None
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except (ValueError, TypeError):
        return None
    return plan.get(CLUTTER_KEY, {}).get(norm_model_path(rel))


_LATCH = [None]


def latch_clutter_mass(plan: dict, src_path, meshes_root) -> None:
    """Record the mass of the NIF about to convert, for the collision pass."""
    _LATCH[0] = clutter_mass_for(plan, src_path, meshes_root)


def mesh_clutter_mass():
    """Authored mass of the NIF being converted, or None when it is not clutter."""
    return _LATCH[0]
