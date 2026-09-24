"""Spell merchants sell tomes: which spells, at what price, carried by whom.

See: docs/commentary/tes5_import_actors.md#spell-tomes
"""

import struct

import numpy as np

from tes5_import.record_types import spell_tomes
from tes5_import.record_types.spell_tomes_morrowind import (autocalc_spells,
                                                            spell_cost)

#: Fire Damage: Destruction, base cost 5, OpenMW's fixed flags for index 14.
_FIRE = (2, np.float32(5.0), 0x1f0)

#: ENAM (effect, skill, attribute, range, area, duration, min, max): 10 fire on target for 1s.
_FIREBOLT = (14, -1, -1, 2, 0, 1, 10, 10)


def _tables(**gmsts) -> dict:
    """Tables holding the one fire effect and Morrowind's default GMSTs."""
    base = {'feffectcostmult': 0.5, 'fnpcbasemagickamult': 2.0,
            'fautospellchance': 80.0, 'iautospelltimescancast': 3,
            'iautospellattskillmin': 70, 'iautospellalterationmax': 5,
            'iautospellconjurationmax': 2, 'iautospelldestructionmax': 5,
            'iautospellillusionmax': 5, 'iautospellmysticismmax': 5,
            'iautospellrestorationmax': 5}
    base.update(gmsts)
    return {'effects': {14: _FIRE}, 'gmsts': base, 'spells': {}}


def test_autocalc_cost_is_openmws_game_spell_formula():
    """0.5 x (10+10) x 0.1 x 5 x 1s x fEffectCostMult 0.5 x 1.5 on target = 3.75 -> 4.

    An authored cost is used as is; the Construction Set stored this same
    formula's result for all 1,423 autocalc spells of Morrowind and TR.
    """
    tables = _tables()
    auto = {'type': 0, 'cost': 99, 'flags': 0x1, 'effects': [_FIREBOLT]}
    assert spell_cost(auto, tables) == 4
    assert spell_cost({**auto, 'flags': 0}, tables) == 99


def _stats(destruction: int, intelligence: int) -> dict:
    """27 skills and 8 attributes, all 50 but Destruction and Intelligence."""
    skills, attributes = [50] * 27, [50] * 8
    skills[10] = destruction
    attributes[1] = intelligence
    return {'skills': skills, 'attributes': attributes}


def test_autocalc_npc_knows_only_what_it_can_cast():
    """A spell is picked only if magicka covers three casts and the chance clears 80."""
    tables = _tables()
    cheap = {'type': 0, 'cost': 0, 'flags': 0x1, 'effects': [_FIREBOLT]}
    tables['spells'] = {'firebolt': cheap,
                        'power': {**cheap, 'type': 5},
                        'unflagged': {**cheap, 'flags': 0}}
    costs = {key: spell_cost(spell, tables) for key, spell in tables['spells'].items()}
    assert autocalc_spells(_stats(60, 50), set(), tables, costs) == ['firebolt']
    assert autocalc_spells(_stats(60, 50), {'firebolt'}, tables, costs) == [], (
        'a race power is never an autocalc pick')
    assert autocalc_spells(_stats(20, 50), set(), tables, costs) == [], (
        'chance 2x20 - 4 + 10 + 5 = 51 is under fAutoSpellChance')
    assert autocalc_spells(_stats(60, 5), set(), tables, costs) == [], (
        'magicka 2 x 5 = 10 cannot cast a 4-point spell three times')


class _Writer:
    """Just enough of PluginWriter: derived ids and the records added."""

    def __init__(self):
        """No records yet."""
        self.records = []

    def derive_formid(self, site: str, key) -> int:
        """A stable fake id per key."""
        return 0x01000800 + len(self.records)

    def add_record(self, sig: str, data: bytes) -> None:
        """Keep what the converter wrote."""
        self.records.append((sig, data))


def _subrecords(record: bytes) -> dict:
    """{tag: payload} of one packed TES5 record."""
    body, subs, at = record[24:], {}, 0
    while at < len(body):
        tag, size = body[at:at + 4].decode(), struct.unpack_from('<H', body, at + 4)[0]
        subs[tag] = body[at + 6:at + 6 + size]
        at += 6 + size
    return subs


def test_oblivion_merchant_sells_its_spells_at_cost_times_gold_mult(tmp_path):
    """Ordinary spells only, at SPIT.Cost x fSpellmakingGoldMult, carried by the merchant."""
    by_type = {
        'NPC_': [{'FormID': '00001000', 'AIDT.Services': str(1 << 11),
                  'SpellCount': '2', 'Spell[0]': '00002000', 'Spell[1]': '00002001'}],
        'SPEL': [{'FormID': '00002000', 'EditorID': 'Flare', 'FULL': 'Flare',
                  'SPIT.Type': '0', 'SPIT.Cost': '11'},
                 {'FormID': '00002001', 'EditorID': 'Shield', 'SPIT.Type': '4',
                  'SPIT.Cost': '5'}],
        'GMST': [{'FormID': '00003000', 'EditorID': 'fSpellmakingGoldMult',
                  'DATA.Value': '3.0'}],
    }
    writer = _Writer()
    spell_tomes.create_spell_tomes(by_type, writer, None, str(tmp_path), 'Fixture.esm')

    assert len(writer.records) == 1, 'the ability is never sold'
    subs = _subrecords(writer.records[0][1])
    flags, _kind, _pad, teaches, value, weight = struct.unpack('<BBHIIf', subs['DATA'])
    assert flags & 0x04 and teaches == 0x2000
    assert value == 33, 'cost 11 x fSpellmakingGoldMult 3'
    assert subs['FULL'].rstrip(b'\x00') == b'Spell Tome: Flare'
    assert spell_tomes.tome_items(0x1000) == [(0x01000800, 1)]
