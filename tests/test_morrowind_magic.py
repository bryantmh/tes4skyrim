"""Morrowind and TES4 magic: bound items, sounds, borrowed art, flags, delivery and master effects."""

import struct
from types import SimpleNamespace

from tes4_export.record_types.morrowind_magic import (_emit_bound_item, _emit_sounds,
                                                      _engine_flags, _spell_flags,
                                                      game_settings)
from tes4_export.tes3_reader import Tes3Record
from tes4_export.tes4_reader import Subrecord
from tes5_import.base.text_reader import get_formid
from tes5_import.generated.vanilla_mgef_data import VANILLA_MGEF_DATA
from tes5_import.record_types import magic_art
from tes5_import.record_types.equipment import attack_spell, convert_ENCH
from tes5_import.record_types.magic import (MGEF_DATA_SIZE, O_CASTING_ART,
                                            O_CASTING_LIGHT, O_HIT_SHADER, _fill_art,
                                            code_to_fid, register_mgef_formids)
from tes5_import.record_types.magic_art_morrowind import FIRE
from tes5_import.record_types.magic_morrowind import mw_tes4_flags
from tes5_import.record_types.magic_variants import owner_delivery

#: TES5 SPIT and ENIT byte offsets of the delivery (xEdit wbSPIT / wbENIT).
_SPIT_DELIVERY = 20
_ENIT_DELIVERY = 16


class _Writer:
    """Just enough of the plugin writer to derive FormIDs and collect records."""

    def __init__(self):
        """No records yet."""
        self.records = []

    def derive_formid(self, site, key) -> int:
        """A fresh id per call, like a hash that never collides."""
        return 0x05000100 + len(self.records)

    def add_record(self, sig, data) -> None:
        """Keep the packed record."""
        self.records.append((sig, data))


def _sub(record: bytes, sig: bytes) -> bytes:
    """The first subrecord ``sig`` of a packed record."""
    at = record.index(sig, 24)
    size = struct.unpack_from('<H', record, at + 4)[0]
    return record[at + 6:at + 6 + size]


def _ctx(resolved: dict, settings: dict = None):
    """The little of MorrowindContext the MGEF exporter asks for."""
    return SimpleNamespace(game_settings=settings or {},
                           resolve=lambda rid, sig='': resolved.get((rid.lower(), sig), ''))


def _field(data: bytes, offset: int) -> int:
    """One u32 FormID field of an MGEF DATA."""
    return struct.unpack_from('<I', data, offset)[0]


def test_bound_effect_names_the_item_its_gmst_names():
    """Archetype 17 with no item crashes on cast; TES3 names the item in a GMST.

    See: docs/commentary/tes4_export_morrowind.md#bound-items
    """
    gmst = Tes3Record(type='GMST', flags=0, record_id='sMagicBoundBattleAxeID',
                      subrecords=[Subrecord(type='STRV', data=b'bound_battle_axe')])
    ctx = _ctx({('bound_battle_axe', 'WEAP'): '01100167'}, game_settings([gmst]))
    lines = []
    _emit_bound_item(lines, 123, ctx)
    assert lines == ['DATA.AssocItem=01100167']


def test_unset_sound_is_the_school_default():
    """An effect with no CSND plays "<school> cast", as the engine does; a set one is kept."""
    rec = Tes3Record(type='MGEF', flags=0,
                     subrecords=[Subrecord(type='HSND', data=b'frost hit\x00')])
    ctx = _ctx({('destruction cast', 'SOUN'): '00000A01', ('frost hit', 'SOUN'): '00000A02'})
    lines = []
    _emit_sounds(lines, rec, 2, ctx)
    assert 'DATA.CastingSound=00000A01' in lines
    assert 'DATA.HitSound=00000A02' in lines


def test_morrowind_effect_borrows_vanilla_art(tmp_path):
    """Fire Damage takes the Firebolt's art, not the generic destruction VFX it names.

    See: docs/commentary/tes5_import_magic.md#morrowind-borrowed-art
    """
    magic_art.begin(None, tmp_path, [])
    rec = {'EditorID': 'MW014FireDamage', 'MorrowindEffectIndex': '14',
           'MorrowindArt.Cast': 'VFX_DestructCast', 'MorrowindArt.Hit': 'VFX_DestructHit'}
    data = bytearray(MGEF_DATA_SIZE)
    _fill_art(data, rec)
    src = bytes.fromhex(VANILLA_MGEF_DATA[FIRE][1])
    for offset in (O_CASTING_ART, O_CASTING_LIGHT, O_HIT_SHADER):
        assert _field(data, offset) == _field(src, offset) != 0


def test_engine_flags_make_damage_hostile():
    """Harmful comes from the engine's table; the ranges stay the spells' own.

    See: docs/commentary/tes4_export_morrowind.md#engine-flags
    """
    assert mw_tes4_flags(_engine_flags(14)) & 0x5 == 0x5
    assert not _engine_flags(14) & 0x1C0


def test_spell_flags_keep_the_authored_cost():
    """Stored cost always; PCStart is a starting spell; Always Succeeds drops.

    See: docs/commentary/tes4_export_morrowind.md#spell-flags
    """
    assert [_spell_flags(f) for f in (0x0, 0x1, 0x2, 0x4)] == [0x1, 0x1, 0x5, 0x1]


def test_touch_is_target_actor_and_aimed_wins():
    """Contact casts only from a weapon, so a Touch spell is Target Actor.

    See: docs/commentary/tes5_import_magic.md#owner-casting-type
    """
    touch = {'EffectCount': '1', 'Effect[0].Type': 'Touch'}
    mixed = {'EffectCount': '2', 'Effect[0].Type': 'Touch', 'Effect[1].Type': 'Target'}
    assert (owner_delivery(touch), owner_delivery(mixed)) == (3, 2)


def test_weapon_enchantment_is_contact_and_staff_target_actor():
    """A weapon's strike is its enchantment's only delivery; a staff aims like a spell."""
    register_mgef_formids([])
    for ench_type, delivery in (('2', 1), ('1', 3)):
        rec = {'FormID': '00001000', 'ENIT.Type': ench_type, 'EffectCount': '1',
               'Effect[0].EFID': 'FIDG', 'Effect[0].Type': 'Touch'}
        enit = _sub(convert_ENCH(rec), b'ENIT')
        assert struct.unpack_from('<I', enit, _ENIT_DELIVERY)[0] == delivery


def test_master_effect_found_by_editor_id():
    """A master's effect is its converted record, not the id its export numbered.

    See: docs/commentary/tes5_import_magic.md#master-effects
    """
    index = SimpleNamespace(find_by_edid=lambda sig, edid: 0x02D54380 if edid == 'MW014' else 0)
    master = {'EditorID': 'MW014', 'FormID': '02000001'}
    own = {'EditorID': 'MW999', 'FormID': '02000002'}
    register_mgef_formids([master, own], index, [own])
    assert code_to_fid['MW014'] == 0x02D54380
    assert code_to_fid['MW999'] == get_formid(own, 'FormID')


def test_creature_attack_spell_is_a_contact_copy():
    """A creature's touch attack names a Contact copy of the spell.

    See: docs/commentary/tes5_import_magic.md#creature-attack-spells
    """
    register_mgef_formids([])
    writer = _Writer()
    rec = {'Signature': 'SPEL', 'FormID': '00001000', 'EditorID': 'Frostbite',
           'SPIT.Type': '0', 'EffectCount': '1', 'Effect[0].EFID': 'FRDG',
           'Effect[0].Type': 'Touch'}
    copy = attack_spell(0x1000, rec, writer)
    assert attack_spell(0x1000, rec, writer) == copy != 0x1000
    spells = [data for sig, data in writer.records if sig == 'SPEL']
    assert len(spells) == 1
    assert struct.unpack_from('<I', spells[0], 12)[0] == copy
    assert struct.unpack_from('<I', _sub(spells[0], b'SPIT'), _SPIT_DELIVERY)[0] == 1
    assert attack_spell(0x1000, rec, None) == 0x1000
