"""FO3/FNV equipment: weapon animation types and biped slots.

Skyrim's only ranged animations are Bow and Crossbow, so every firearm becomes
a Crossbow: it aims flat and fires a projectile, where a bow is drawn and arced.
ETYP, BIDS, BAMT, INAM, NAM9 and NAM8 all key off the anim type, so that one
substitution carries the whole record.

FO3/FNV biped flags are a 20-bit field that shares only bits 0-2 with
Oblivion's 16-bit one, so the slot table is selected by source game.

See: docs/commentary/tes4_export_falloutnv.md#weapons-guns-become-crossbows
See: docs/commentary/tes4_export_falloutnv.md#fnv-biped-slots
"""

import json
import os

from asset_convert.havok.gun_vocabulary_falloutnv import (ANIM_TYPE_CLASS,
                                                          ATTACK_ACTIONS,
                                                          ATTACK_ANIMS,
                                                          GUN_CLASSES,
                                                          RELOAD_LETTERS)

from ..base.equivalents import WEAPON_ANIM_CROSSBOW
from .bodypart_falloutnv import SIDECAR_DIR, source_file
from .projectile_falloutnv import gun_ammo
from .sound import get_soun_identity, sndr_editor_id
from .common import get_float, get_formid, get_int
from .world_falloutnv import is_fallout_source

#: FO3/FNV firearm anim types: pistols 3-4, rifles 5-7, launcher 9, thrown 10-13.
_GUN_TYPES = frozenset({3, 4, 5, 6, 7, 9, 10, 11, 12, 13})


def is_gun(rec: dict) -> bool:
    """Whether the WEAP is a FO3/FNV firearm (by its authored anim type)."""
    return get_int(rec, 'DNAM.FalloutAnimType', -1) in _GUN_TYPES


def refine_anim_type(rec: dict, anim_type: int) -> int:
    """Crossbow for a FO3/FNV firearm, else the given type unchanged."""
    return WEAPON_ANIM_CROSSBOW if is_gun(rec) else anim_type


def gun_speed(rec: dict, speed: float) -> float:
    """A gun's speed: its shots per second when automatic (the graph scales
    the loop clip by its duration), else its AnimAttackMult.
    See: docs/commentary/asset_convert_falloutnv.md#automatic-fire-rate
    """
    if not is_gun(rec):
        return speed
    if get_int(rec, 'DNAM.Flags1', 0) & 0x02:
        rate = (get_float(rec, 'DNAM.ShotsPerSec', 0.0)
                or get_float(rec, 'DNAM.FireRate', 0.0))
        if rate > 0:
            return rate
    mult = get_float(rec, 'DNAM.AnimAttackMult', 0.0)
    return mult if mult > 0 else speed


#: TES5 AMMO DATA flag: the ammo is an arrow, never loadable by a crossbow.
_AMMO_NON_BOLT = 0x04


def ammo_flags(tes4_flags: int) -> int:
    """TES5 AMMO flags: a bolt for a FO3/FNV round, an arrow for TES4."""
    bolt = tes4_flags & 0x01
    return bolt if is_fallout_source() else bolt | _AMMO_NON_BOLT


def dry_fire_sound(rec: dict) -> str:
    """The SNDR EditorID of the gun's TNAM (Sound - Gun - No Ammo), '' if none.
    See: docs/commentary/tes_runtime_guns.md#dry-fire
    """
    soun = get_formid(rec, 'TNAM')
    if not soun:
        return ''
    edid, _ = get_soun_identity(soun)
    return sndr_editor_id(edid, soun)


def gun_profile(rec: dict, plugin: str = '') -> dict:
    """The graph variables FalloutRuntime sets for a gun WEAP, the ammo it
    loads (as (local id, file) pairs) and its dry-fire sound, or None.

    Indices follow gun_graph_falloutnv: class into GUN_CLASSES, reload into
    RELOAD_LETTERS, attack into ATTACK_ACTIONS (-1 = the class default).
    See: docs/commentary/tes_runtime_guns.md#ammo-restriction
    """
    cls = ANIM_TYPE_CLASS.get(get_int(rec, 'DNAM.FalloutAnimType', -1))
    if not cls:
        return None
    reload = get_int(rec, 'DNAM.ReloadAnim', 0)
    attack = ATTACK_ANIMS.get(get_int(rec, 'DNAM.AttackAnim', 255))
    return {'class': GUN_CLASSES.index(cls),
            'reload': reload if 0 <= reload < len(RELOAD_LETTERS) else 0,
            'attack': (ATTACK_ACTIONS.index(attack)
                       if attack in ATTACK_ACTIONS else -1),
            'clip_size': get_int(rec, 'DATA.ClipSize', 0),
            'auto': 1 if get_int(rec, 'DNAM.Flags1', 0) & 0x02 else 0,
            'dry_sound': dry_fire_sound(rec),
            'sight_fov': get_float(rec, 'DNAM.SightFOV', 0.0) or DEFAULT_SIGHT_FOV,
            'ammo': [{'id': f'{a & 0xFFFFFF:06X}', 'file': source_file(a, plugin)}
                     for a in gun_ammo(rec)]}


#: Iron-sight FOV (degrees) for a DNAM Sight FOV of 0. See: docs/commentary/tes_runtime_guns.md#zoom
DEFAULT_SIGHT_FOV = 65.0


def write_gun_sidecar(records: list, plugin_out_dir: str,
                      plugin_name: str) -> str:
    """Write every gun WEAP's profile for FalloutRuntime; the path ('' if none).

    Keyed by the plugin-local FormID with its owning file, resolved through
    the engine's load order at DataLoaded.
    """
    guns = {}
    for rec in records:
        prof = gun_profile(rec, plugin_name)
        if prof is None:
            continue
        fid = get_formid(rec, 'FormID')
        prof['file'] = source_file(fid, plugin_name)
        guns[f'{fid & 0xFFFFFF:06X}'] = prof
    if not guns:
        return ''
    doc = {'version': 1, 'source': os.path.basename(plugin_name), 'guns': guns}
    stem = os.path.splitext(os.path.basename(plugin_name))[0]
    out_dir = os.path.join(plugin_out_dir, SIDECAR_DIR)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'{stem}.guns.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1)
    return path


#: FO3/FNV biped bit -> Skyrim BOD2 bit; Weapon(5) has no slot and is dropped.
FNV_BIPED_SLOT_MAP = {
    0: 0,    # Head -> 30-Head
    1: 1,    # Hair -> 31-Hair
    2: 2,    # Upper Body -> 32-Body
    3: 3,    # Left Hand -> 33-Hands
    4: 29,   # Right Hand -> 59-Right hand
    6: 4,    # PipBoy -> 34-Forearms
    7: 16,   # Backpack -> 46-Unnamed
    8: 5,    # Necklace -> 35-Amulet
    9: 12,   # Headband -> 42-Circlet
    10: 1,   # Hat -> 31-Hair
    11: 12,  # Eye Glasses -> 42-Circlet
    12: 13,  # Nose Ring -> 43-Ears
    13: 13,  # Earrings -> 43-Ears
    14: 0,   # Mask -> 30-Head
    15: 5,   # Choker -> 35-Amulet
    16: 13,  # Mouth Object -> 43-Ears
    17: 17,  # Body AddOn 1 -> 47-Unnamed
    18: 18,  # Body AddOn 2 -> 48-Unnamed
    19: 30,  # Body AddOn 3 -> 60-Misc (49 is the lower body)
}


#: FO3/FNV Upper Body also claims 37-Feet; each hand is its own bit, so Hands claims no right hand.
_FNV_BODY_EXTRA = {2: [7], 3: []}


def biped_slot_tables(oblivion_map: dict, oblivion_extra: dict) -> tuple:
    """(slot map, conflict extras) for the source game.

    See: docs/commentary/tes4_export_falloutnv.md#fnv-biped-slots
    """
    if not is_fallout_source():
        return oblivion_map, oblivion_extra
    return FNV_BIPED_SLOT_MAP, {**oblivion_extra, **_FNV_BODY_EXTRA}
