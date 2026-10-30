"""The ability MorrowindRuntime gives a TES3 actor whose script stages a fistfight.

A TES3 punch tires its target; a Skyrim punch wounds it. A script that reads its own
GetFatigue on OnPCHitMe counts on the first (HroldarScript, TR's Anbarnag and
Savrethi), so the runtime gives each actor running one TES3BrawlAbility, whose perk
zeroes the health the player's bare fists take, and moves the punch to Stamina.

See: docs/commentary/morrowind_runtime.md#brawls
"""

from ..base.conditions import FUNC_GET_IS_ID, build_ctda
from ..base.owned_records import (adopted_or_derived, hidden_perk, multiply_entry,
                                  owner_row, perk_effect, perk_spell)
from ..base.writer import pack_subrecord

#: The ability, its effect and its perk.
BRAWL_ABILITY = 'TES3BrawlAbility'
_EFFECT, _PERK = BRAWL_ABILITY + 'Effect', BRAWL_ABILITY + 'Perk'

#: PERK entry point 36 Mod Incoming Damage, function 3 Multiply Value, 3 tabs (vanilla DeftMovement).
_INCOMING_DAMAGE = bytes((36, 3, 3))

#: Mod Incoming Damage's Attacker and Attacker Weapon tabs.
_TAB_ATTACKER, _TAB_WEAPON = 1, 2

#: Skyrim.esm's Player NPC_ and its Unarmed WEAP, the "weapon" of a bare fist.
_PLAYER, _UNARMED = 0x00000007, 0x000001F4

#: This run's ability FormID, or empty when the source is not TES3.
_ability: list = []


def _player_punch_entry() -> bytes:
    """Zeroes the damage of a hit the player lands with bare fists."""
    return multiply_entry(_INCOMING_DAMAGE, {
        _TAB_ATTACKER: pack_subrecord('CTDA', build_ctda(FUNC_GET_IS_ID, param1=_PLAYER)),
        _TAB_WEAPON: pack_subrecord('CTDA', build_ctda(FUNC_GET_IS_ID, param1=_UNARMED))})


def create_brawl_ability(writer, master_index, wanted: bool) -> dict:
    """{EditorID: FormID} of the ability, a master's adopted; {} unless `wanted`."""
    _ability.clear()
    if not wanted:
        return {}
    spel, own = adopted_or_derived(writer, master_index, 'SPEL', BRAWL_ABILITY)
    if own:
        perk = writer.derive_formid('PERK', _PERK)
        mgef = writer.derive_formid('MGEF', _EFFECT)
        writer.add_record('PERK', hidden_perk(perk, _PERK, _player_punch_entry()))
        writer.add_record('MGEF', perk_effect(mgef, _EFFECT, perk, ability=True))
        writer.add_record('SPEL', perk_spell(spel, BRAWL_ABILITY, mgef, ability=True))
    _ability.append(spel)
    return {BRAWL_ABILITY: spel}


def brawl_rows(plugin: str, masters: list) -> list:
    """`brawl=plugin|FormID` naming the ability, for MorrowindRuntime."""
    return [owner_row('brawl', fid, plugin, masters) for fid in _ability]
