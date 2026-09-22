"""Phase 0e2-0f: the per-run indexes every actor and creature pass reads.

Door sounds, sound identity, FO3/FNV template flattening and the generated
creature races all have to be in place before any record is converted, and each
depends on the ones above it: a stub that has not taken its model from TPLT yet
earns no generated race, and a creature with no race gets one by name.

Extracted from import_plugin, which is over the size the code rules allow.

See: docs/commentary/tes5_import_falloutnv_actors.md
"""

from output_layout import assets_for

from .creature_races import build_creature_death_piles, build_creature_races
from ..record_types.actors_falloutnv import flatten_actor_templates
from ..record_types.common import reset_emitted_regions
from ..record_types.items import load_door_model_sounds
from ..record_types.sound import (load_soun_identity, reset_sound_descriptors,
                                 reset_soun_identity)
from ..record_types.projectile_falloutnv import index_gun_projectiles
from ..record_types.reference_falloutnv import index_convertible_records
from ..record_types.world_falloutnv import register_fallout_source
from ..record_types.world_morrowind import register_tes3_locks
from ..registry import IMPORT_DISPATCH, SKIP_TYPES


def _load_door_sounds(by_type: dict, export_dir: str) -> None:
    """Lift door open/close sounds the MESH authors onto SNAM/ANAM."""
    load_door_model_sounds(str(assets_for(export_dir) / 'meshes'), by_type)


def _load_sound_identity(by_type: dict, master_export: dict) -> None:
    """Reset the per-run sound state and index every SOUN in scope."""
    reset_sound_descriptors()
    reset_soun_identity()
    if master_export:
        load_soun_identity([r for r in master_export.values()
                            if r.get('Signature') == 'SOUN'])
    load_soun_identity(by_type.get('SOUN', []))


def build_actor_indexes(by_type: dict, writer, export_dir: str, ctx,
                        step_done) -> None:
    """Populate every actor and creature index, in dependency order."""
    master_export = ctx.master_export if ctx else None

    _load_door_sounds(by_type, export_dir)
    step_done('door mesh sounds')

    _load_sound_identity(by_type, master_export)
    reset_emitted_regions()
    register_fallout_source(by_type)
    n_locks = register_tes3_locks(by_type, export_dir, master_export)
    if n_locks:
        print(f'  TES3 door locks: {n_locks} exit-only lock(s) dropped')
    index_convertible_records(by_type, IMPORT_DISPATCH, SKIP_TYPES)
    n_ammo = index_gun_projectiles(by_type, master_export)
    if n_ammo:
        print(f'  FO3/FNV gun projectiles: {n_ammo} ammo type(s) indexed')

    n_tplt = flatten_actor_templates(by_type, master_export)
    if n_tplt:
        print(f'  FO3/FNV actor templates: {n_tplt} stub(s) took their '
              f'model, name or AI data from TPLT')

    build_creature_races(by_type, writer, export_dir, master_export)
    n_piles = build_creature_death_piles(writer)
    if n_piles:
        print(f'  Creature death piles: {n_piles} ACTI '
              f'(authored ectoplasm, replaces the vanilla ash pile)')
    step_done('creature races')
