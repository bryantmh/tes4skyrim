"""A child plugin's generated records and copies land on its master's.

See: docs/commentary/tes5_import_override.md#generated-records-reuse-the-masters
"""

import json
import struct
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.actors.creature_races import _shared_creatures
from tes5_import.base.writer import PluginWriter, pack_record, pack_string_subrecord
from tes5_import.overrides.adoption import MasterAdoption, generated_formid
from tes5_import.overrides.master_index import MasterIndex
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


def test_finalize_keeps_ours_when_the_master_record_is_missing(capsys):
    """An adopted id with no master bytes ships ours instead of crashing.

    The master index can answer an EDID with an id whose bytes are then
    unavailable (truncated output, an unroutable slot). finalize used to die
    with struct.error on the empty buffer and kill the whole import.
    """
    ours = _record('MESG', MASTER_MSG, 'TES4Msg_A', 'Hello')
    writer = _writer({MASTER_MSG: _record('MESG', MASTER_MSG, 'TES4Msg_A', 'Hallo')})
    writer.adoption.find('MESG', 'TES4Msg_A')
    writer.add_record('MESG', ours)
    writer.adoption.master_index.records.pop(MASTER_MSG)
    assert writer.adoption.finalize(writer) == (1, 0)
    assert list(writer.top_records()) == [ours]
    assert 'has no master record' in capsys.readouterr().out


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


def test_child_music_uses_the_converted_masters_install_name(tmp_path):
    """A renamed localized master still supplies the child's music categories."""
    folder = tmp_path / 'Nehrim.esm'
    folder.mkdir()
    (folder / 'music_tracks.json').write_text(
        json.dumps({'plugin': 'Nehrim.esm (Deutsch)'}), encoding='utf-8')
    writer = _writer({
        0x01001000: _record('MUSC', 0x01001000, 'MUSNehrimesmDeutschDungeon', ''),
        0x01001001: _record('MUSC', 0x01001001, 'MUSNehrimesmDeutschExplore', ''),
        0x01001002: _record('MUSC', 0x01001002, 'MUSNehrimesmDeutschPublic', ''),
    })
    assert master_music_types(writer, output_root=tmp_path) == {
        0: 0x01001001, 1: 0x01001002, 2: 0x01001000}


@pytest.mark.parametrize('installed', ['localized', 'canonical', 'missing', 'other'])
def test_child_uses_installed_converted_master_variant(tmp_path, monkeypatch, installed):
    """Master records, companion IDs and music all come from the installed variant."""
    from asset_convert.sources import skyrim_assets
    from tes5_import.overrides.master_index import load_master_index
    from tes5_import.overrides.manifest import load_master_manifests, write_manifest

    output = tmp_path / 'output'
    game = tmp_path / 'Data'
    game.mkdir()
    files = {}
    for kind, name, fid in [('canonical', 'Nehrim.esm', 0x01001000),
                            ('localized', 'Nehrim.esm (Deutsch)', 0x01002000)]:
        folder = output / name
        folder.mkdir(parents=True)
        master = PluginWriter(masters=['Skyrim.esm'])
        edid = 'MUS' + ''.join(c for c in name if c.isalnum()) + 'Explore'
        master.add_record('MUSC', _record('MUSC', fid, edid, ''))
        path = folder / 'Nehrim.esm'
        master.write(str(path))
        write_manifest(str(path), 'Nehrim.esm', {'00000080': {'fid': fid}})
        (folder / 'music_tracks.json').write_text(
            json.dumps({'plugin': name}), encoding='utf-8')
        files[kind] = path
    if installed in files:
        (game / 'Nehrim.esm').write_bytes(files[installed].read_bytes())
    elif installed == 'other':
        # Same size is insufficient: an unrelated install must not select a variant.
        raw = bytearray(files['localized'].read_bytes())
        raw[-2] ^= 1
        (game / 'Nehrim.esm').write_bytes(raw)
    monkeypatch.setattr(skyrim_assets, 'find_skyrim_data', lambda: str(game))
    masters = ['Skyrim.esm', 'Nehrim.esm']
    index = load_master_index(masters, 1, str(output))
    manifest = load_master_manifests(masters, 1, str(output))
    writer = PluginWriter(masters=masters)
    writer.adoption = MasterAdoption(index)
    expected = 0x01002000 if installed == 'localized' else 0x01001000
    assert manifest.output_formid('00000080') == expected
    assert master_music_types(writer, output_root=output) == {0: expected}


def test_child_npc_adopts_the_localized_master_voice(tmp_path, monkeypatch):
    """The child uses the existing Russian VTYP and publishes its audio folder."""
    from tes5_import.base.adopted_records import _adopt_race_voices
    from tes5_import.base.equivalents import VOICE_TYPE_MAP, VTYP_EDID_BY_FID
    from tes5_import.dialogue.converter import build_npc_to_vtyp_map

    english_fid, russian_fid = 0x01001000, 0x01001001
    russian_edid = 'TES4MaleВысокийэльф'
    master = PluginWriter(masters=['Skyrim.esm'])
    for fid, edid in [(english_fid, 'TES4MaleHighElf'),
                      (russian_fid, russian_edid)]:
        master.add_record('VTYP', _record('VTYP', fid, edid, ''))
    path = tmp_path / 'Oblivion.esm'
    master.write(str(path))
    (tmp_path / 'RACE.txt').write_text(
        '---RECORD_BEGIN---\nFormID=00000001\nEditorID=HighElf\n'
        'FULL=Высокий эльф\n---RECORD_END---\n', encoding='utf-8')
    monkeypatch.setitem(VOICE_TYPE_MAP, ('HighElf', 'Male'), english_fid)
    monkeypatch.setitem(VTYP_EDID_BY_FID, russian_fid, '')
    assert _adopt_race_voices(MasterIndex(str(path)), [tmp_path]) == 1
    by_type = {
        'RACE': [{'FormID': '00000001', 'EditorID': 'HighElf'}],
        'NPC_': [{'FormID': '01000080', 'RNAM.Race': '00000001', 'ACBS.Flags': '0'}],
    }
    assert build_npc_to_vtyp_map(by_type, 1)[0x02000080] == russian_fid
    assert VTYP_EDID_BY_FID[russian_fid] == russian_edid
