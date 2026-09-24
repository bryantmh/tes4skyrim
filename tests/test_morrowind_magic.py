"""Morrowind magic effects: the item a bound effect conjures, its sounds and its borrowed art."""

import struct
from types import SimpleNamespace

from tes4_export.record_types.morrowind_magic import (_emit_bound_item, _emit_sounds,
                                                      game_settings)
from tes4_export.tes3_reader import Tes3Record
from tes4_export.tes4_reader import Subrecord
from tes5_import.generated.vanilla_mgef_data import VANILLA_MGEF_DATA
from tes5_import.record_types import magic_art
from tes5_import.record_types.magic import (MGEF_DATA_SIZE, O_CASTING_ART,
                                            O_CASTING_LIGHT, O_HIT_SHADER, _fill_art)
from tes5_import.record_types.magic_art_morrowind import FIRE


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
