"""A child plugin's generated records and copies land on its master's.

See: docs/commentary/tes5_import_override.md#generated-records-reuse-the-masters
"""

import struct
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.actors.creature_races import _shared_creatures
from tes5_import.base.writer import PluginWriter, pack_record, pack_string_subrecord
from tes5_import.overrides.adoption import MasterAdoption, generated_formid
from tes5_import.overrides.builder import apply_changes, split_subrecords
from tes5_import.overrides.nested import OverrideContext
from tes5_import.record_types.common import register_music_types
from tes5_import.record_types.magic_variants import copy_editor_ids
from tes5_import.record_types.music import master_music_types

MASTER_MSG = 0x01000900
MASTER_HAIR_COPY = 0x01000A00
MASTER_MGEF_COPY = 0x01000B00


def _record(sig: str, fid: int, edid: str, full: str) -> bytes:
    """A packed record carrying only EDID and FULL."""
    return pack_record(sig, fid, 0, pack_string_subrecord('EDID', edid)
                       + pack_string_subrecord('FULL', full))


class _Index:
    """A master index over a {FormID: packed record} dict."""

    def __init__(self, records: dict):
        """Index `records` by FormID and by (signature, EditorID)."""
        self.records = records
        self.edids = {(r[:4], split_subrecords(r)[0][1].rstrip(b'\0').decode()): fid
                      for fid, r in records.items()}

    def find_by_edid(self, sig: bytes, edid: str) -> int:
        """The FormID of the record with this signature and EditorID, else 0."""
        return self.edids.get((sig, edid), 0)

    def record(self, fid: int) -> bytes:
        """The packed record at `fid`, else b''."""
        return self.records.get(fid, b'')


def _writer(records: dict) -> PluginWriter:
    """A child writer whose adoption reads `records` as the master."""
    writer = PluginWriter(masters=['Skyrim.esm', 'Nehrim.esm'], is_esm=False)
    writer.adoption = MasterAdoption(_Index(records))
    return writer


def test_generated_record_takes_the_masters_formid():
    """A generated record the master defines by EditorID reuses its FormID."""
    writer = _writer({MASTER_MSG: _record('MESG', MASTER_MSG, 'TES4Msg_A', 'Hallo')})
    assert generated_formid(writer, 'MESG', 'TES4Msg_A', 'SCRIPT_MESG', 'TES4Msg_A') == MASTER_MSG
    assert generated_formid(writer, 'MESG', 'TES4Msg_B', 'SCRIPT_MESG', 'TES4Msg_B') >> 24 == 2


def test_finalize_ships_the_masters_record_with_our_text_only():
    """An adopted record keeps the master's data; only its text is ours, else it drops."""
    race = 0x01000C00
    master_race = pack_record('RACE', race, 0, pack_string_subrecord('EDID', 'TES4WolfRace')
                              + pack_string_subrecord('FULL', 'Wolf')
                              + struct.pack('<4sHI', b'DATA', 4, 7))
    writer = _writer({MASTER_MSG: _record('MESG', MASTER_MSG, 'TES4Msg_A', 'Hallo'),
                      race: master_race})
    for sig, edid in (('MESG', 'TES4Msg_A'), ('RACE', 'TES4WolfRace')):
        writer.adoption.find(sig, edid)
    writer.add_record('MESG', _record('MESG', MASTER_MSG, 'TES4Msg_A', 'Hello'))
    writer.add_record('RACE', pack_record('RACE', race, 0, pack_string_subrecord(
        'EDID', 'TES4WolfRace') + pack_string_subrecord('FULL', 'Wolf')
        + struct.pack('<4sHI', b'DATA', 4, 9)))
    assert writer.adoption.finalize(writer) == (1, 0)
    assert list(writer.top_records()) == [_record('MESG', MASTER_MSG, 'TES4Msg_A', 'Hello')]


def test_translated_record_keeps_the_masters_other_fields():
    """A renamed adopted record carries the master's DATA, not the child's regeneration."""
    race = 0x01000C00

    def packed(full: str, data: int) -> bytes:
        """A RACE with this FULL and DATA."""
        return pack_record('RACE', race, 0, pack_string_subrecord('EDID', 'TES4WolfRace')
                           + pack_string_subrecord('FULL', full)
                           + struct.pack('<4sHI', b'DATA', 4, data))
    writer = _writer({race: packed('Wolf', 7)})
    writer.adoption.find('RACE', 'TES4WolfRace')
    writer.add_record('RACE', packed('Grey Wolf', 9))
    writer.adoption.finalize(writer)
    assert list(writer.top_records()) == [packed('Grey Wolf', 7)]


def test_queued_copy_yields_to_a_record_already_written():
    """A renamed copy ships only where nothing else wrote that FormID."""
    written = _record('MGEF', MASTER_MGEF_COPY, 'TES4REHEFFSelf', 'Restore Life')
    writer = _writer({MASTER_MGEF_COPY: _record('MGEF', MASTER_MGEF_COPY, 'TES4REHEFFSelf',
                                                'Lebensenergie wiederherstellen')})
    writer.adoption.find('MGEF', 'TES4REHEFFSelf')
    writer.add_record('MGEF', written)
    writer.adoption.queue_copy(_record('MGEF', MASTER_MGEF_COPY, 'TES4REHEFFSelf', 'Other'))
    writer.adoption.queue_copy(_record('HDPT', MASTER_HAIR_COPY, 'TES4HairAElf', 'Long Hair'))
    assert writer.adoption.finalize(writer) == (1, 1)
    assert written in list(writer.top_records())


def _context(records: dict, companions: dict) -> OverrideContext:
    """An OverrideContext over fake master records, manifest and export."""
    ctx = object.__new__(OverrideContext)
    ctx.master_index = _Index(records)
    ctx.master_manifest = SimpleNamespace(companions=lambda fid: companions.get(fid, []))
    ctx.master_export = {}
    ctx.stats = Counter()
    return ctx


def test_rename_reaches_the_masters_effect_and_hair_copies():
    """A translated FULL renames every master copy still carrying the old one."""
    ctx = _context({
        MASTER_MGEF_COPY: _record('MGEF', MASTER_MGEF_COPY, 'TES4REHEFFSelf',
                                  'Lebensenergie wiederherstellen'),
        MASTER_HAIR_COPY: _record('HDPT', MASTER_HAIR_COPY, 'TES4HairAElf', 'Lange Haare'),
        0x01000D00: _record('MGEF', 0x01000D00, 'TES4REHEFFTargetActor', 'Anders'),
    }, {'00002091': [MASTER_HAIR_COPY]})
    ctx.master_export = {
        '0000188C': {'Signature': 'MGEF', 'FormID': '0000188C', 'EditorID': 'REHE',
                     'FULL': 'Lebensenergie wiederherstellen'},
        '00002091': {'Signature': 'HAIR', 'FormID': '00002091', 'EditorID': 'A',
                     'FULL': 'Lange Haare'}}
    mgef = ctx.renamed_copies({'Signature': 'MGEF', 'FormID': '0000188C',
                               'EditorID': 'REHE', 'FULL': 'Restore Life'})
    hair = ctx.renamed_copies({'Signature': 'HAIR', 'FormID': '00002091',
                               'EditorID': 'A', 'FULL': 'Long Hair'})
    assert mgef == [_record('MGEF', MASTER_MGEF_COPY, 'TES4REHEFFSelf', 'Restore Life')]
    assert hair == [_record('HDPT', MASTER_HAIR_COPY, 'TES4HairAElf', 'Long Hair')]


def test_copy_editor_ids_name_delivery_and_ability_clones():
    """The clone names `delivery_variant` and `ability_variant` give REHE."""
    names = copy_editor_ids('REHE')
    assert {'TES4REHEFFSelf', 'TES4REHEConstantSelfAbility',
            'TES4REHEFFAimedArea'} <= set(names)
    assert 'REHE' not in names


def test_cell_music_change_rewrites_xcmo():
    """An authored XCMT change swaps the master's XCMO for the new music type."""
    register_music_types({1: 0x01000E01, 2: 0x01000E02})
    base = pack_record('CELL', 0x01000F00, 0, pack_string_subrecord('EDID', 'Keller')
                       + struct.pack('<4sHI', b'XCMO', 4, 0x01000E01))
    out, applied, unmapped = apply_changes(
        base, {'XCMT.MusicType': '2'},
        {'Signature': 'CELL', 'FormID': '00000F00', 'XCMT.MusicType': '2'},
        {'Signature': 'CELL', 'FormID': '00000F00', 'XCMT.MusicType': '1'})
    assert (b'XCMO', struct.pack('<I', 0x01000E02)) in split_subrecords(out)
    assert applied == {'XCMT.MusicType'} and not unmapped


def test_races_group_over_the_masters_creatures_in_master_order():
    """The child's override replaces the master's creature in place; its own follow."""
    master = {'00000001': {'Signature': 'CREA', 'FormID': '00000001', 'FULL': 'Wolf'},
              '00000002': {'Signature': 'CREA', 'FormID': '00000002', 'FULL': 'Bär'}}
    own = [{'Signature': 'CREA', 'FormID': '00000002', 'FULL': 'Bear'},
           {'Signature': 'CREA', 'FormID': '01000003', 'FULL': 'Troll'}]
    names = [r['FULL'] for r in _shared_creatures({'CREA': own}, master)]
    assert names == ['Wolf', 'Bear', 'Troll']


def test_child_music_falls_back_to_the_masters_categories():
    """A child with no music of its own resolves each enum to its master's MUSC."""
    writer = _writer({0x01001000: _record('MUSC', 0x01001000, 'MUSNehrimesmDungeon', ''),
                      0x01001001: _record('MUSC', 0x01001001, 'MUSNehrimesmExplore', '')})
    assert master_music_types(writer) == {0: 0x01001001, 2: 0x01001000}
