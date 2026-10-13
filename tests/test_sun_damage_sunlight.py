"""Sun Damage works in sunlight only: its effect items carry Skyrim's own sunlight test.

See: docs/commentary/tes5_import_magic.md#sun-damage-sunlight
"""

import struct
import zlib

import pytest

from tes5_import import pipeline
from tes5_import.base.text_reader import set_formid_index_offset
from tes5_import.record_types import equipment, magic

#: The three conditions Skyrim.esm's `crVampireSunDamage` carries on each effect item, in the game's order.
GAME_ORDER = [
    bytes.fromhex('a0000000000098414a00000038000000000000000000000000000000ffffffff'),   # GameHour <= 19
    bytes.fromhex('600000000000a0404a00000038000000000000000000000000000000ffffffff'),   # GameHour >= 5
    bytes.fromhex('00000000000000002c01000000000000000000000000000000000000ffffffff'),   # IsInInterior == 0
]
GAME_CONDITIONS = set(GAME_ORDER)
#: Update.esm's `VampireSunDamage01` puts this in front: GetInWorldspace(its list of sunless worlds, 01000969) == 0.
GAME_WORLD_TEST = bytes.fromhex('00000000000000003601000069090001000000000000000000000000ffffffff')
#: TES4 effect codes that share Sun Damage's archetype and actor value, and one of every other kind used here.
OTHER_CODES = ('DGHE', 'FIDG', 'FRDG', 'SHDG', 'REHE', 'DRHE', 'VAMP', 'WKFI')


class VariantWriter:
    """The writer calls the converters make here: a FormID, and a record kept."""

    def __init__(self):
        """Start with nothing written."""
        self.records = []

    def derive_formid(self, site, key):
        """A FormID of this plugin that is a function of (site, key) alone."""
        return zlib.crc32(f'{site}|{key!r}'.encode()) & 0x00FFFFFF

    def add_record(self, sig, data):
        """Keep each written record."""
        self.records.append((sig, data))


def subrecords(record: bytes) -> list:
    """[(signature, payload)] of a packed record's subrecords."""
    out, pos = [], 24
    while pos + 6 <= len(record):
        size = struct.unpack_from('<H', record, pos + 4)[0]
        out.append((record[pos:pos + 4].decode(), record[pos + 6:pos + 6 + size]))
        pos += 6 + size
    return out


@pytest.fixture(autouse=True)
def _no_list_left_over():
    """Every test starts and ends with no sunless list noted."""
    del magic._sunless_lists[:]
    yield
    del magic._sunless_lists[:]


def _register(*codes: str) -> None:
    """Index one exported MGEF per code, as phase 1 does before any item is converted."""
    magic.register_mgef_formids([
        {'EditorID': code, 'FormID': f'{0x1200 + n:08X}', 'FULL': code, 'DATA.School': '5',
         'DATA.Flags': str(magic.T4_SELF | magic.T4_DETRIMENTAL)}
        for n, code in enumerate(codes)])


def _item(effects: list, **fields) -> dict:
    """An exported item whose effect slots are `effects`: (code, magnitude) or (code, magnitude, Morrowind index)."""
    rec = {'FormID': '00000A11', 'EditorID': 'TestItem', 'RecordFlags': '0', 'EffectCount': str(len(effects)), **fields}
    for i, entry in enumerate(effects):
        rec.update({f'Effect[{i}].EFID': entry[0], f'Effect[{i}].Magnitude': str(entry[1]),
                    f'Effect[{i}].Area': '0', f'Effect[{i}].Duration': '0', f'Effect[{i}].Type': 'Self'})
        if len(entry) > 2:
            rec[f'Effect[{i}].MorrowindIndex'] = str(entry[2])
    return rec


def _slots(record: bytes) -> list:
    """[(magnitude, [conditions])] per effect slot, in the record's own order."""
    out = []
    for sig, data in subrecords(record):
        if sig == 'EFIT':
            out.append((struct.unpack_from('<f', data)[0], []))
        elif sig == 'CTDA':
            assert out, 'a condition precedes the first effect item'
            out[-1][1].append(data)
    return out


def _ability(effects: list, spell_type: int = 4) -> bytes:
    """`effects` converted as one spell of TES4 type `spell_type`."""
    return equipment.convert_SPEL(_item(effects, **{'SPIT.Type': str(spell_type), 'SPIT.Flags': '0'}),
                                  writer=VariantWriter())


def test_the_conditions_are_the_games_own_three():
    """Byte for byte what the game's creature sun spell carries, and nothing more."""
    conditions = [data for sig, data in subrecords(b'\0' * 24 + magic.SUNLIGHT_CONDITIONS)]
    assert conditions == GAME_ORDER
    assert {sig for sig, _ in subrecords(b'\0' * 24 + magic.SUNLIGHT_CONDITIONS)} == {'CTDA'}


def test_no_condition_is_chained_by_or():
    """Three tests that must all hold: an OR bit would let one of them pass alone."""
    for sig, data in subrecords(b'\0' * 24 + magic.SUNLIGHT_CONDITIONS):
        assert not data[0] & 0x01


def test_only_sun_damage_asks_for_conditions():
    """The rule names one effect; every other code gets nothing."""
    assert magic.effect_item_conditions('SUDG') == magic.SUNLIGHT_CONDITIONS
    assert magic.effect_item_conditions('SUDG', '') == magic.SUNLIGHT_CONDITIONS
    for code in OTHER_CODES + ('', 'SEFF', 'sudg'):
        assert magic.effect_item_conditions(code) == b''


def test_a_morrowind_sun_damage_slot_is_known_by_its_index():
    """Morrowind's effect 135 is Sun Damage under another name; no other index is."""
    assert magic.effect_item_conditions('sEffectSunDamage', '135') == magic.SUNLIGHT_CONDITIONS
    for index in ('', '13', '1350', '35', '99', '-1'):
        assert magic.effect_item_conditions('sEffectOther', index) == b''


def test_an_abilitys_sun_damage_slot_carries_the_test_and_its_neighbours_do_not():
    """The vampire ability's shape: the conditions follow the Sun Damage EFIT and stop at the next effect."""
    _register('VAMP', 'SUDG', 'WKFI')
    slots = _slots(_ability([('VAMP', 100), ('SUDG', 8), ('WKFI', 50)]))
    assert [mag for mag, _ in slots] == [100.0, 8.0, 50.0]
    assert slots[0][1] == [] and slots[2][1] == []
    assert len(slots[1][1]) == 3 and set(slots[1][1]) == GAME_CONDITIONS


def test_the_sun_damage_slot_keeps_its_magnitude_area_and_duration():
    """The rule adds conditions; the authored numbers of the slot are untouched."""
    _register('SUDG')
    rec = _item([('SUDG', 8)], **{'SPIT.Type': '4', 'SPIT.Flags': '0'})
    rec.update({'Effect[0].Area': '15', 'Effect[0].Duration': '30'})
    efit, = [data for sig, data in subrecords(equipment.convert_SPEL(rec, writer=VariantWriter())) if sig == 'EFIT']
    assert struct.unpack('<fII', efit) == (8.0, 15, 30)


@pytest.mark.parametrize('spell_type', [0, 1, 2, 3, 4, 5])
def test_every_spell_type_carries_it(spell_type):
    """The test rides the effect item, whatever kind of spell owns it."""
    _register('SUDG')
    (mag, conditions), = _slots(_ability([('SUDG', 4)], spell_type))
    assert mag == 4.0 and set(conditions) == GAME_CONDITIONS and len(conditions) == 3


@pytest.mark.parametrize('ench_type', [0, 1, 2, 3])
def test_every_enchantment_type_carries_it(ench_type):
    """Every TES4 enchantment type carries the test on its Sun Damage slot."""
    _register('SUDG')
    rec = _item([('SUDG', 30)], **{'ENIT.Type': str(ench_type), 'ENIT.Charge': '100', 'ENIT.Cost': '10', 'ENIT.Flags': '0'})
    (mag, conditions), = _slots(equipment.convert_ENCH(rec, writer=VariantWriter()))
    assert mag == 30.0 and set(conditions) == GAME_CONDITIONS and len(conditions) == 3


def test_a_potion_and_an_ingredient_carry_it():
    """Both go through the same writer; an ingredient's filler slots stay bare."""
    _register('SUDG')
    fields = {'FULL': 'Test', 'Model.MODL': 'x.nif', 'DATA.Weight': '0.1', 'DATA.Value': '5', 'ENIT.Value': '5', 'ENIT.Flags': '0'}
    (mag, conditions), = _slots(equipment.convert_ALCH(_item([('SUDG', 3)], **fields), writer=VariantWriter()))
    assert mag == 3.0 and set(conditions) == GAME_CONDITIONS
    slots = _slots(equipment.convert_INGR(_item([('SUDG', 3)], **fields), writer=VariantWriter()))
    assert len(slots) == 4 and set(slots[0][1]) == GAME_CONDITIONS
    assert all(conditions == [] for _, conditions in slots[1:])


def test_two_sun_damage_slots_are_each_tested():
    """Each slot is tested on its own: an item condition covers one effect item."""
    _register('SUDG', 'FIDG')
    slots = _slots(_ability([('SUDG', 1), ('FIDG', 5), ('SUDG', 8)]))
    assert [len(conditions) for _, conditions in slots] == [3, 0, 3]


@pytest.mark.parametrize('code', OTHER_CODES)
def test_no_other_effect_gains_a_condition(code):
    """Damage that ticks anywhere in Oblivion still does."""
    _register(code)
    (mag, conditions), = _slots(_ability([(code, 8)]))
    assert mag == 8.0 and conditions == []


def test_a_filler_slot_has_no_condition():
    """An item none of whose effects converts gets a zero-magnitude filler, bare."""
    _register()
    slots = _slots(_ability([('ZZZZ', 8)], spell_type=0))
    assert slots and all(conditions == [] for _, conditions in slots)


def test_a_morrowind_item_is_tested_through_its_index():
    """A Morrowind slot is known by its effect index, not its EditorID."""
    _register('sEffectSunDamage', 'sEffectFireDamage')
    slots = _slots(_ability([('sEffectSunDamage', 2, 135), ('sEffectFireDamage', 2, 14)], spell_type=0))
    assert [len(conditions) for _, conditions in slots] == [3, 0]


@pytest.mark.parametrize('field,value', [('Duration', '50'), ('Area', '32392920'), ('Magnitude', '60'),
                                         ('Type', 'Touch'), ('Type', 'Target')])
def test_the_test_does_not_depend_on_the_slots_numbers_or_range(field, value):
    """Sun Damage with a duration, an area, a large magnitude or another range is tested all the same."""
    _register('SUDG')
    rec = _item([('SUDG', 8)], **{'SPIT.Type': '0', 'SPIT.Flags': '0'})
    rec[f'Effect[0].{field}'] = value
    (_, conditions), = _slots(equipment.convert_SPEL(rec, writer=VariantWriter()))
    assert len(conditions) == 3 and set(conditions) == GAME_CONDITIONS


def test_no_other_known_code_asks_for_conditions():
    """Every code of the effect table but Sun Damage, and every Morrowind index but 135, gets nothing."""
    others = sorted(set(magic.EFFECT_ARCHETYPES) - {'SUDG'})
    assert len(others) > 100 and 'CUDI' in others
    assert [code for code in others if magic.effect_item_conditions(code)] == []
    assert [i for i in range(-1, 200) if magic.effect_item_conditions('', str(i))] == [135]


def test_a_dropped_slot_in_front_does_not_shift_the_conditions():
    """The conditions follow the Sun Damage slot, not its source number."""
    _register('SUDG', 'FIDG')
    slots = _slots(_ability([('ZZZZ', 9), ('SUDG', 8), ('FIDG', 5)]))
    assert [(mag, len(conditions)) for mag, conditions in slots] == [(8.0, 3), (5.0, 0)]


def test_a_sigil_stone_and_an_enchanted_book_carry_it():
    """Both become scrolls through the same writer."""
    _register('SUDG')
    stone = _item([('SUDG', 6)], FULL='Stone', **{'DATA.Value': '5', 'DATA.Weight': '0.1'})
    blob = equipment.convert_SGST(stone, writer=VariantWriter())
    assert blob[:4] == b'SCRL' and [len(c) for _, c in _slots(blob)] == [3]
    ench = _item([('SUDG', 6)], Signature='ENCH', **{'ENIT.Cost': '10'})
    ench['FormID'] = '000402B1'
    equipment.set_ench_index([ench])
    try:
        blob = equipment.convert_BOOK({'FormID': '0018022C', 'EditorID': 'TestScroll', 'FULL': 'Scroll', 'ENAM': '000402B1',
                                       'DATA.Flags': '1', 'DATA.Value': '9', 'DATA.Weight': '0.2'}, writer=VariantWriter())
    finally:
        equipment.set_ench_index([])
    assert blob[:4] == b'SCRL' and [len(c) for _, c in _slots(blob)] == [3]


def _world(fid: str, climate: str = '') -> dict:
    """An exported worldspace naming `climate`."""
    return {'FormID': fid, 'EditorID': f'World{fid}', 'CNAM.Climate': climate}


def _climate(fid: str, *weathers: str) -> dict:
    """An exported climate listing `weathers`."""
    rec = {'FormID': fid, 'WeatherCount': str(len(weathers))}
    rec.update({f'Weather[{i}].FormID': w for i, w in enumerate(weathers)})
    return rec


def _weather(fid: str, sun: int) -> dict:
    """An exported weather of Sun Damage `sun`."""
    return {'FormID': fid, 'DATA.SunDamage': str(sun)}


class _Masters:
    """A master index that knows form lists by EditorID."""

    def __init__(self, lists: dict):
        """`lists` maps an EditorID to the FormIDs the chain of masters holds under it."""
        self.lists = lists

    def find_all_by_edid(self, signature: bytes, edid: str) -> list:
        """Every master's form list of that EditorID."""
        return list(self.lists.get(edid, [])) if signature == b'FLST' else []

    def find_by_edid(self, signature: bytes, edid: str) -> int:
        """The single-record lookup the effect index makes: nothing is adopted here."""
        return 0


class _Context:
    """What the pipeline reads off a dependent plugin's context."""

    def __init__(self, masters):
        """`masters` answers the EditorID lookups."""
        self.master_index, self.master_export = masters, None


#: The exported effects the pipeline's own prescan indexes.
EFFECTS = [{'EditorID': code, 'FormID': f'{0x1200 + n:08X}', 'FULL': code, 'DATA.School': '5',
            'DATA.Flags': str(magic.T4_SELF | magic.T4_DETRIMENTAL)} for n, code in enumerate(('SUDG', 'FIDG'))]


#: Two dark worlds, one sunny, and the records that cannot be judged.
WEATHERS = [_weather('00000A01', 0), _weather('00000A02', 0), _weather('00000A03', 255), _weather('00000A04', 25)]
CLIMATES = [_climate('00000B01', '00000A01', '00000A02'), _climate('00000B02', '00000A01', '00000A04'),
            _climate('00000B03'), _climate('00000B04', '00000A01', '00000AFF'), _climate('00000B05', '00000A03')]
WORLDS = [_world('00000C02', '00000B01'), _world('00000C01', '00000b01'), _world('00000C03', '00000B02'),
          _world('00000C04'), _world('00000C05', '00000B03'), _world('00000C06', '00000B04'),
          _world('00000C07', '00000BFF'), _world('00000C08', '00000B05')]


def _settle(masters=None, worlds=WORLDS, writer=True):
    """Register Sun Damage, then settle the lists as the pipeline does; returns (count, the writer)."""
    _register('SUDG', 'FIDG')
    w = VariantWriter() if writer else None
    count = magic.settle_sunless_worlds({'WRLD': worlds, 'CLMT': CLIMATES, 'WTHR': WEATHERS}, w, masters)
    return count, w


def _world_test(fid: int) -> bytes:
    """The game's own worldspace condition with `fid` as the list."""
    return GAME_WORLD_TEST[:12] + struct.pack('<I', fid) + GAME_WORLD_TEST[16:]


def test_a_world_is_sunless_only_when_every_listed_weather_has_no_sun_damage():
    """One weather with any Sun Damage, no climate, no weather, or a record of another file: not listed."""
    found = magic.sunless_worldspaces(WORLDS, CLIMATES, WEATHERS)
    assert [w['FormID'] for w in found] == ['00000C02', '00000C01']
    assert magic.sunless_worldspaces(WORLDS, CLIMATES, []) == []
    assert magic.sunless_worldspaces(WORLDS, [], WEATHERS) == []


def test_the_plugins_list_names_its_sunless_worlds_in_id_order():
    """One FLST with the fixed EditorID, an LNAM per world, ascending."""
    count, writer = _settle()
    (sig, record), = writer.records
    subs = subrecords(record)
    assert count == 2 and sig == 'FLST' and record[:4] == b'FLST'
    assert subs == [('EDID', b'TES4SunDamageExceptionWorldSpaces\0'),
                    ('LNAM', struct.pack('<I', 0xC01)), ('LNAM', struct.pack('<I', 0xC02))]


def test_a_sun_damage_slot_skips_the_listed_worlds_first_as_the_game_does():
    """The game's own four conditions in its own order; the first names the plugin's list."""
    _, writer = _settle()
    fid = struct.unpack_from('<I', writer.records[0][1], 12)[0]
    slots = _slots(_ability([('FIDG', 5), ('SUDG', 8)]))
    assert slots[0][1] == []
    assert slots[1][1] == [_world_test(fid)] + GAME_ORDER


def test_a_plugin_without_a_sunless_world_writes_no_list():
    """Nothing to skip: no record, and the slot carries the three daylight tests."""
    count, writer = _settle(worlds=WORLDS[2:])
    assert count == 0 and writer.records == []
    assert _slots(_ability([('SUDG', 8)]))[0][1] == GAME_ORDER


def test_a_masters_list_is_named_and_not_written_again():
    """A dependent with no dark world of its own adopts the master's list."""
    count, writer = _settle(_Masters({'TES4SunDamageExceptionWorldSpaces': [0x01000ABC]}), worlds=[])
    assert count == 0 and writer.records == []
    assert _slots(_ability([('SUDG', 8)]))[0][1] == [_world_test(0x01000ABC)] + GAME_ORDER


def test_a_dependents_own_list_takes_the_next_free_name():
    """Every list down the chain is named, the masters' first; no EditorID is used twice."""
    masters = _Masters({'TES4SunDamageExceptionWorldSpaces': [0x01000ABC],
                        'TES4SunDamageExceptionWorldSpaces1': [0x02000DEF]})
    count, writer = _settle(masters)
    (sig, record), = writer.records
    own = struct.unpack_from('<I', record, 12)[0]
    assert count == 2 and subrecords(record)[0] == ('EDID', b'TES4SunDamageExceptionWorldSpaces2\0')
    assert _slots(_ability([('SUDG', 8)]))[0][1] == [_world_test(0x01000ABC), _world_test(0x02000DEF),
                                                     _world_test(own)] + GAME_ORDER


def test_the_lists_do_not_leak_into_the_next_plugin():
    """Indexing the next plugin's effects forgets the last plugin's lists."""
    _settle()
    _register('SUDG')
    assert _slots(_ability([('SUDG', 8)]))[0][1] == GAME_ORDER


def test_settling_twice_names_each_list_once():
    """Settling starts from nothing, whatever the last call left."""
    _settle()
    magic.settle_sunless_worlds({'WRLD': WORLDS, 'CLMT': CLIMATES, 'WTHR': WEATHERS}, VariantWriter(), None)
    assert len(_slots(_ability([('SUDG', 8)]))[0][1]) == 4


def test_without_a_writer_no_list_is_noted():
    """A dry run writes nothing and so must name nothing."""
    count, writer = _settle(writer=False)
    assert count == 0 and writer is None
    assert _slots(_ability([('SUDG', 8)]))[0][1] == GAME_ORDER


def test_a_gap_in_the_names_hides_no_list():
    """A master rebuilt without its list leaves a hole; the lists behind it are still named."""
    masters = _Masters({'TES4SunDamageExceptionWorldSpaces': [0x01000ABC],
                        'TES4SunDamageExceptionWorldSpaces2': [0x03000DEF]})
    _, writer = _settle(masters)
    own = struct.unpack_from('<I', writer.records[0][1], 12)[0]
    assert subrecords(writer.records[0][1])[0] == ('EDID', b'TES4SunDamageExceptionWorldSpaces3\0')
    assert _slots(_ability([('SUDG', 8)]))[0][1] == [_world_test(0x01000ABC), _world_test(0x03000DEF),
                                                     _world_test(own)] + GAME_ORDER


def test_two_masters_holding_the_same_name_are_both_named():
    """Independent masters each hold the unnumbered list; the plugin on both takes the next name."""
    _, writer = _settle(_Masters({magic.SUNLESS_WORLDS_EDID: [0x01000ABC, 0x02000DEF]}))
    own = struct.unpack_from('<I', writer.records[0][1], 12)[0]
    assert subrecords(writer.records[0][1])[0] == ('EDID', b'TES4SunDamageExceptionWorldSpaces1\0')
    assert _slots(_ability([('SUDG', 8)]))[0][1] == [_world_test(0x01000ABC), _world_test(0x02000DEF),
                                                     _world_test(own)] + GAME_ORDER


def test_the_list_names_each_world_by_the_id_it_is_written_under():
    """With a master ahead of the plugin its records move up one index, and so do the entries."""
    set_formid_index_offset(1)
    try:
        _, writer = _settle()
    finally:
        set_formid_index_offset(0)
    entries = [struct.unpack('<I', d)[0] for sig, d in subrecords(writer.records[0][1]) if sig == 'LNAM']
    assert entries == [0x01000C01, 0x01000C02]


def test_a_morrowind_sun_damage_slot_skips_the_listed_worlds_too():
    """The world test rides every Sun Damage slot, whichever way the slot is known."""
    _, writer = _settle()
    own = struct.unpack_from('<I', writer.records[0][1], 12)[0]
    conditions = [data for sig, data in subrecords(b'\0' * 24 + magic.effect_item_conditions('sEffectSunDamage', '135'))]
    assert conditions == [_world_test(own)] + GAME_ORDER


def test_the_pipeline_settles_the_lists_after_indexing_the_effects():
    """The prescan itself: a stale list is forgotten, the plugin's own is written and named."""
    magic._sunless_lists.append(0xDEADBEEF)
    writer = VariantWriter()
    pipeline._prescan_effect_families({'MGEF': EFFECTS, 'WRLD': WORLDS, 'CLMT': CLIMATES, 'WTHR': WEATHERS}, None, writer)
    own, = [struct.unpack_from('<I', data, 12)[0] for sig, data in writer.records if sig == 'FLST']
    assert _slots(_ability([('SUDG', 8)]))[0][1] == [_world_test(own)] + GAME_ORDER


def test_the_pipeline_hands_the_masters_to_the_settling():
    """A dependent's prescan names its master's list."""
    masters = _Context(_Masters({magic.SUNLESS_WORLDS_EDID: [0x01000ABC]}))
    pipeline._prescan_effect_families({'MGEF': EFFECTS}, masters, VariantWriter())
    assert _slots(_ability([('SUDG', 8)]))[0][1] == [_world_test(0x01000ABC)] + GAME_ORDER


def test_the_list_record_is_plain_and_keyed_by_the_unnumbered_name():
    """Header flags 0; the FormID comes from the fixed name whatever number the EditorID takes."""
    _, writer = _settle(_Masters({magic.SUNLESS_WORLDS_EDID: [0x01000ABC]}))
    (_, record), = writer.records
    assert struct.unpack_from('<I', record, 8)[0] == 0
    assert struct.unpack_from('<I', record, 12)[0] == VariantWriter().derive_formid('FLST', magic.SUNLESS_WORLDS_EDID)


def test_ids_match_in_any_case_and_a_weather_without_the_field_is_not_dark():
    """Climate, weather and listed-weather ids fold case; a weather with no Sun Damage field cannot be judged."""
    worlds = [_world('00000C01', '00000B01'), _world('00000C02', '00000B02'), _world('00000C03', '00000B03')]
    climates = [_climate('00000b01', '00000a01'), _climate('00000B02', '00000A02'), _climate('00000B03', '00000A03')]
    weathers = [_weather('00000A01', 0), _weather('00000a02', 0), {'FormID': '00000A03'}]
    assert [w['FormID'] for w in magic.sunless_worldspaces(worlds, climates, weathers)] == ['00000C01', '00000C02']


def test_a_slot_on_the_fallback_effect_carries_it_too():
    """With no effect indexed the slot still carries the sunlight test."""
    _register()
    (mag, conditions), = _slots(_ability([('SUDG', 8)]))
    assert mag == 8.0 and conditions == GAME_ORDER


def test_a_far_name_is_still_found_and_a_world_is_listed_once():
    """The last name looked up is found; a worldspace exported twice is one entry."""
    count, writer = _settle(_Masters({f'{magic.SUNLESS_WORLDS_EDID}255': [0x01000ABC]}), worlds=WORLDS[:1] * 2)
    subs = subrecords(writer.records[0][1])
    assert count == 1 and subs[0] == ('EDID', b'TES4SunDamageExceptionWorldSpaces256\0')
    assert [sig for sig, _ in subs] == ['EDID', 'LNAM']
