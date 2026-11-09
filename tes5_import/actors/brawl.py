"""The ability MorrowindRuntime gives a TES3 actor whose script stages a fistfight.

A TES3 punch tires its target; a Skyrim punch wounds it. A script that reads its own
GetFatigue on OnPCHitMe counts on the first (HroldarScript, TR's Anbarnag and
Savrethi), so the runtime gives each actor running one, and the player, TES3BrawlAbility.
Its effect gives the player its perk, which zeroes the damage of the player's bare
fists on a target holding the ability; the runtime moves the punch to Stamina.

See: docs/commentary/morrowind_runtime.md#brawls
"""

from ..base.conditions import build_ctda
from ..base.owned_records import (adopted_or_derived, hidden_perk, multiply_entry,
                                  owner_row, perk_effect, perk_spell)
from ..base.writer import pack_subrecord

#: The ability, its effect and its perk.
BRAWL_ABILITY = 'TES3BrawlAbility'
_EFFECT, _PERK = BRAWL_ABILITY + 'Effect', BRAWL_ABILITY + 'Perk'

#: PERK entry point 35 Mod Attack Damage, function 3 Multiply Value, 3 tabs (vanilla crNerfDamage05, on unarmed creatures).
_ATTACK_DAMAGE = bytes((35, 3, 3))

#: Mod Attack Damage's Perk Owner and Target tabs.
_TAB_OWNER, _TAB_TARGET = 0, 2

#: GetEquippedItemType(hand), 0 for hand to hand; vanilla DualSavagery tests both hands (0 left, 1 right).
_GET_EQUIPPED_ITEM_TYPE = 597

#: HasSpell(spell).
_HAS_SPELL = 264

#: This run's ability FormID, or empty when the source is not TES3.
_ability: list = []


def _player_punch_entry(ability: int) -> bytes:
    """Zeroes the damage of the owner's bare fists on a target that has `ability`.

    See: docs/commentary/morrowind_runtime.md#brawls
    """
    fists = b''.join(pack_subrecord('CTDA', build_ctda(_GET_EQUIPPED_ITEM_TYPE, param1=hand,
                                                       comp_value=0.0))
                     for hand in (0, 1))
    marked = pack_subrecord('CTDA', build_ctda(_HAS_SPELL, param1=ability))
    return multiply_entry(_ATTACK_DAMAGE, {_TAB_OWNER: fists, _TAB_TARGET: marked})


def create_brawl_ability(writer, master_index, wanted: bool) -> dict:
    """{EditorID: FormID} of the ability, a master's adopted; {} unless `wanted`."""
    _ability.clear()
    if not wanted:
        return {}
    spel, own = adopted_or_derived(writer, master_index, 'SPEL', BRAWL_ABILITY)
    if own:
        perk = writer.derive_formid('PERK', _PERK)
        mgef = writer.derive_formid('MGEF', _EFFECT)
        writer.add_record('PERK', hidden_perk(perk, _PERK, _player_punch_entry(spel)))
        writer.add_record('MGEF', perk_effect(mgef, _EFFECT, perk, ability=True))
        writer.add_record('SPEL', perk_spell(spel, BRAWL_ABILITY, mgef, ability=True))
    _ability.append(spel)
    return {BRAWL_ABILITY: spel}


def brawl_rows(plugin: str, masters: list) -> list:
    """`brawl=plugin|FormID` naming the ability, for MorrowindRuntime."""
    return [owner_row('brawl', fid, plugin, masters) for fid in _ability]
