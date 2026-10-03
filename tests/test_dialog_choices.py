"""Startup regression: invalid TCLT must not trigger Skyrim's text diagnostic."""

import struct
import zlib
from types import SimpleNamespace

import pytest

from tes5_import.base.tes5_reader import FLAG_COMPRESSED, records
from tes5_import.base.writer import (
    PluginWriter,
    pack_group,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_tes4_header,
    pack_top_group,
)
from tes5_import.dialogue.choices import prune_invalid_choices
from tes5_import.overrides.master_index import ChainedMasterIndex, MasterIndex


def _topic(fid, infos=b'', flags=0):
    return (pack_record('DIAL', fid, flags, pack_string_subrecord('EDID', 'Topic'))
            + pack_group(7, struct.pack('<I', fid), infos))


def _info(fid, choices, text='Полная реплика без обрезки. ' * 20):
    # The pruner must preserve arbitrary existing bytes, independently of
    # the converter's chosen output encoding.
    body = pack_subrecord('NAM1', text.encode('utf-8') + b'\0')
    for choice in choices:
        body += pack_subrecord('TCLT', struct.pack('<I', choice))
    return pack_record('INFO', fid, 0, body)


def _infos(writer):
    return [rec for blob in writer._top_groups['DIAL']
            for rec in records(blob, b'INFO', span=(0, len(blob)))]


def _choices(rec):
    return [struct.unpack('<I', value)[0]
            for tag, value in rec.subs() if tag == b'TCLT']


def _master(tmp_path):
    path = tmp_path / 'Converted.esm'
    data = pack_tes4_header(['Skyrim.esm'])
    data += pack_top_group('DIAL', _topic(0x01000800)
                           + _topic(0x01000801, flags=0x20))
    data += pack_top_group('QUST', pack_record('QUST', 0x01000802, 0, b''))
    path.write_bytes(data)
    return MasterIndex(str(path))


def test_hidden_npc_continuation_is_removed_from_player_choices_only():
    writer = PluginWriter(['Skyrim.esm'])
    writer.add_raw_group('DIAL', _topic(0x01000100, _info(
        0x01000101, [0x01000200, 0x01000300])))
    writer.add_raw_group('DIAL', _topic(0x01000200, _info(0x01000201, [])))
    writer.add_raw_group('DIAL', _topic(0x01000300))
    assert prune_invalid_choices(writer, hidden_targets={0x01000200}) == 1
    emitted = _infos(writer)
    assert _choices(emitted[0]) == [0x01000300]
    assert emitted[1].form_id == 0x01000201  # The response itself survives.


def test_missing_choices_removed_forward_topic_and_utf8_kept(tmp_path):
    writer = PluginWriter(['Skyrim.esm', 'Converted.esm'])
    info = _info(0x02000800, [0x02000810, 0x02000820, 0])
    original_text = next(records(info, span=(0, len(info)))).sub(b'NAM1')
    assert len(original_text) > 260
    writer.add_raw_group('DIAL', _topic(0x02000801, info))
    writer.add_raw_group('DIAL', _topic(0x02000810))

    assert prune_invalid_choices(writer) == 2
    result = _infos(writer)[0]
    assert _choices(result) == [0x02000810]
    assert result.sub(b'NAM1') == original_text
    assert result.sub(b'NAM1').rstrip(b'\0').decode('utf-8').startswith('Полная')
    assert prune_invalid_choices(writer) == 0

    path = tmp_path / 'Result.esp'
    writer.write(str(path))
    reread = list(records(path.read_bytes()))
    assert [rec.sig for rec in reread] == [b'DIAL', b'INFO', b'DIAL']
    assert reread[1].sub(b'NAM1') == original_text


def test_master_resolution_uses_child_slots_and_preserves_unindexed_master(tmp_path):
    # The converted master's own index 1 is slot 2 in this child's load order.
    master = _master(tmp_path)
    chain = ChainedMasterIndex([master], base_slot=2,
                              child_masters=['Skyrim.esm', 'Other.esm', 'Converted.esm'])
    writer = PluginWriter(['Skyrim.esm', 'Other.esm', 'Converted.esm'])
    writer.add_raw_group('DIAL', _topic(0x03000800, _info(
        0x03000801, [0x02000800, 0x02000801, 0x02000802, 0x02000803,
                     0x00012345, 0x01012345])))

    assert prune_invalid_choices(writer, chain) == 3
    assert _choices(_infos(writer)[0]) == [0x02000800, 0x00012345, 0x01012345]
    assert master.covers_slot(1) and not master.covers_slot(0)
    assert chain.covers_slot(2) and not chain.covers_slot(1)


def test_local_overrides_win_and_copied_master_info_is_checked(tmp_path):
    master = _master(tmp_path)
    writer = PluginWriter(['Skyrim.esm', 'Converted.esm'])
    # A copied INFO under an inherited topic needs the same cleanup as own INFOs.
    writer.add_raw_group('DIAL', _topic(0x01000900, _info(
        0x01000901, [0x01000800, 0x01000801, 0x01000803, 0x01000804])))
    writer.add_record('QUST', pack_record('QUST', 0x01000800, 0, b''))
    writer.add_raw_group('DIAL', _topic(0x01000801))  # undeletes the master topic
    writer.add_raw_group('DIAL', _topic(0x01000803, flags=0x20))
    writer.add_raw_group('DIAL', _topic(0x01000804))  # new topic at a master's id

    assert prune_invalid_choices(writer, master) == 2
    assert _choices(_infos(writer)[0]) == [0x01000801, 0x01000804]


@pytest.mark.parametrize('compressed', [False, True])
def test_nested_sizes_headers_and_other_subrecords_preserved(compressed):
    writer = PluginWriter(['Skyrim.esm'])
    info = _info(0x01000800, [0x01000900, 0x01000901])
    # Unknown oversized subrecords and repeated response text are preserved.
    info_body = info[24:] + pack_subrecord('ZZZZ', b'x' * 65536)
    info_body += pack_string_subrecord('NAM1', 'Ещё одна реплика')
    if compressed:
        info_body = struct.pack('<I', len(info_body)) + zlib.compress(info_body)
    header = bytearray(info[:24])
    struct.pack_into('<I', header, 4, len(info_body))
    struct.pack_into('<I', header, 8, FLAG_COMPRESSED if compressed else 0)
    struct.pack_into('<IHH', header, 16, 0xAABBCCDD, 44, 0x1234)
    info = bytes(header) + info_body
    original = next(records(info, span=(0, len(info))))
    blob = pack_top_group('DIAL', _topic(0x01000801, info)
                          + _topic(0x01000900))
    writer.add_raw_group('DIAL', blob)

    assert prune_invalid_choices(writer) == 1
    patched = writer._top_groups['DIAL'][0]
    assert struct.unpack_from('<I', patched, 4)[0] == len(patched)
    result = _infos(writer)[0]
    assert _choices(result) == [0x01000900]
    assert [(tag, value) for tag, value in result.subs() if tag != b'TCLT'] == [
        (tag, value) for tag, value in original.subs() if tag != b'TCLT']
    assert patched[result.offset + 8:result.offset + 24] == info[8:24]
    assert [r.sig for r in records(patched, span=(0, len(patched)))] == [
        b'DIAL', b'INFO', b'DIAL']


def test_valid_dialogue_is_byte_identical():
    writer = PluginWriter(['Skyrim.esm'])
    blob = _topic(0x01000800, _info(0x01000801, [0x01000800]))
    writer.add_raw_group('DIAL', blob)
    assert prune_invalid_choices(writer) == 0
    assert writer._top_groups['DIAL'][0] is blob


def test_finalize_checks_choices_after_adoption_before_write(monkeypatch, tmp_path):
    from tes5_import import pipeline_finalize as finalize

    writer = PluginWriter(['Skyrim.esm'])
    writer.add_raw_group('DIAL', _topic(0x01000800, _info(
        0x01000801, [0x01000802, 0x01000803])))
    monkeypatch.setattr(finalize, '_build_dialogue', lambda *_: set())
    monkeypatch.setattr(finalize, 'morrowind_arrest_topic', lambda *_: set())
    monkeypatch.setattr(finalize, '_patch_late_bindings', lambda *_: None)
    monkeypatch.setattr(finalize, '_write_lip_text', lambda *_: None)
    monkeypatch.setattr(finalize, '_finalize_adoption', lambda w: w.add_raw_group(
        'DIAL', _topic(0x01000802)))
    st = SimpleNamespace(
        writer=writer, ctx=SimpleNamespace(master_index=None, report=lambda: None),
        by_type={}, sge_quest_fids=set(), t2=0, converted=0, errors=0,
        output_path=str(tmp_path / 'Final.esp'))

    finalize.run_finalize_phases(st, '', lambda _: None, False, writer.masters)
    result = next(records((tmp_path / 'Final.esp').read_bytes(), b'INFO'))
    assert _choices(result) == [0x01000802]
