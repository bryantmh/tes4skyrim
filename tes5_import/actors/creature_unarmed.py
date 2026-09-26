"""Per-creature unarmed damage on a shared generated race.

A generated creature race is shared by every CREA with the same mesh folder and
body set, but TES4 authors DATA.AttackDamage per creature (the eight SE02
Gatekeepers share one skeleton and hit for 10..58).  Vanilla solves the same
split for dragons, vampires and werewolves: the race carries the base, and each
stronger NPC gets an ability of AbFortifyUnarmedDamage for the rest
(crDragonUnarmedDamage02..05, +25..+175).  This module does the same.
"""

import struct

from ..base.text_reader import get_formid, get_int
from ..base.writer import (pack_formid_subrecord, pack_obnd, pack_record,
                           pack_string_subrecord, pack_subrecord)
from ..overrides.adoption import generated_formid

#: Skyrim.esm AbFortifyUnarmedDamage: PeakValueModifier on UnarmedDamage, the effect crDragonUnarmedDamage05 uses.
_MGEF_FORTIFY_UNARMED = 0x000424E2
#: Skyrim.esm EitherHand EQUP, the ETYP every vanilla crXUnarmedDamage ability carries.
_ETYP_EITHER_HAND = 0x00013F44
#: SPIT spell type Ability; cast type 0 Constant and delivery 0 Self go with it.
_SPELL_TYPE_ABILITY = 4

#: {TES4 CREA FormID (low 24 bits): output ability SPEL FormID}, reset per plugin.
_CREA_ABILITY = {}


def authored_damage(rec: dict) -> int:
    """The creature's TES4 DATA.AttackDamage, floored at 1."""
    return max(1, get_int(rec, 'DATA.AttackDamage', 5))


def race_unarmed_damage(recs: list) -> float:
    """The race's own unarmed damage: the weakest creature sharing it."""
    return float(min(authored_damage(r) for r in recs))


def reset() -> None:
    """Forget every creature's ability; runs once per plugin, before races are built."""
    _CREA_ABILITY.clear()


def _ability_subs(edid: str, magnitude: float) -> bytes:
    """A constant self Ability with one AbFortifyUnarmedDamage effect."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_obnd()
    subs += pack_formid_subrecord('ETYP', _ETYP_EITHER_HAND)
    subs += pack_subrecord('SPIT', struct.pack(
        '<IIIfII12x', 0, 0, _SPELL_TYPE_ABILITY, 0.0, 0, 0))
    subs += pack_formid_subrecord('EFID', _MGEF_FORTIFY_UNARMED)
    subs += pack_subrecord('EFIT', struct.pack('<fII', magnitude, 0, 0))
    return subs


def build_unarmed_abilities(writer, race_key, recs: list, edid_base: str) -> int:
    """Write one ability per authored damage above the race's base; returns how many.

    The FormID is keyed on the race key and the authored damage, so creatures
    of one race that hit equally hard share an ability.
    """
    base = race_unarmed_damage(recs)
    made = {}
    for rec in recs:
        damage = authored_damage(rec)
        if damage <= base:
            continue
        if damage not in made:
            edid = f'{edid_base}UnarmedDamage{damage}'
            fid = generated_formid(writer, 'SPEL', edid, 'CREA_UNARMED',
                                   (race_key, damage))
            writer.add_record('SPEL', pack_record('SPEL', fid, 0, _ability_subs(
                edid, damage - base)))
            made[damage] = fid
        _CREA_ABILITY[get_formid(rec, 'FormID') & 0x00FFFFFF] = made[damage]
    return len(made)


def creature_unarmed_ability(fid_low24: int) -> int:
    """The creature's unarmed-damage ability, or 0 when the race base fits."""
    return _CREA_ABILITY.get(fid_low24, 0)
