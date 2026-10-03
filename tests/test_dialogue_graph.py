"""Conversation loss, cross-master choices and runtime binding regressions."""

import struct

import pytest

from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph
from script_convert.pipeline import (
    _info_begin_fragment, _info_end_fragment, info_needs_fragment,
    build_script_context, convert_all_scripts, scan_say_topic_fids,
)
from tes5_import.base import text_reader
from tes5_import.base.tes5_reader import records
from tes5_import.base.writer import (
    PluginWriter, pack_group, pack_record, pack_string_subrecord, pack_tes4_header,
    pack_top_group,
)
from tes5_import.dialogue.runtime_graph import (
    bind_graph, generate_scripts, load_manifest, seal_manifest, sidecar_path, source_graph,
)
from tes5_import.overrides.master_index import MasterIndex


def _export(folder, masters=(), dials=(), infos=(), source=''):
    folder.mkdir(parents=True, exist_ok=True)
    header = ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters))
    (folder / '_HEADER.txt').write_text(header + f'Source={source}\n', encoding='utf-8')
    for sig, rows in [('DIAL', dials), ('INFO', infos)]:
        (folder / (sig + '.txt')).write_text(''.join(
            '---RECORD_BEGIN---\n' + ''.join(f'{k}={v}\n' for k, v in row.items())
            + '---RECORD_END---\n' for row in rows), encoding='utf-8')
    return folder


def _dial(fid, edid, kind=1):
    return {'FormID': f'{fid:08X}', 'EditorID': edid, 'DATA.Type': str(kind)}


def _info(fid, parent, *choices, speaker=0, **other):
    return {'FormID': f'{fid:08X}', 'ParentDIAL': f'{parent:08X}',
            'DATA.DialogType': '1', 'DATA.NextSpeaker': str(speaker),
            'ChoiceCount': str(len(choices)),
            **{f'Choice[{i}]': f'{f:08X}' for i, f in enumerate(choices)}, **other}


def _topic_blob(parent, *infos):
    body = b''.join(pack_record('INFO', fid, flags, pack_string_subrecord('NAM1', 'Response'))
                    for fid, flags in infos)
    return (pack_record('DIAL', parent, 0, pack_string_subrecord('EDID', 'Topic'))
            + pack_group(7, struct.pack('<I', parent), body))


@pytest.fixture(autouse=True)
def _restore_shared_state(monkeypatch):
    monkeypatch.setattr(text_reader, '_formid_index_offset', 0)
    monkeypatch.setattr(ScriptConverter, 'say_topics', set())
    monkeypatch.setattr(ScriptConverter, 'conversation_graph', {})


def test_indirect_branches_cycles_and_cross_master_overrides(tmp_path):
    root = tmp_path / 'export'
    _export(root / 'Base.esm', dials=[_dial(0x100, 'Reaction')],
            infos=[_info(0x101, 0x100)])
    child = _export(root / 'Child.esp', ('Base.esm',),
                    [_dial(0x01000200, 'Head'), _dial(0x01000300, 'Tail', 0)],
                    [_info(0x01000201, 0x01000200, 0x100, 0x01000300),
                     _info(0x101, 0x100, 0x01000200, speaker=2),
                     _info(0x01000301, 0x01000300)])
    graph = source_graph(child)
    assert set(graph['dials']) == {('base.esm', 0x100), ('child.esp', 0x200), ('child.esp', 0x300)}
    assert graph['infos']['base.esm', 0x101]['_choices'] == [('child.esp', 0x200)]
    assert graph['infos']['child.esp', 0x201]['_choices'] == [('base.esm', 0x100), ('child.esp', 0x300)]
    writer = PluginWriter(['Skyrim.esm', 'Base.esm'])
    # The head was split/reparented by the importer; source DIAL != final DIAL.
    writer.add_raw_group('DIAL', _topic_blob(0x02000900, (0x02000201, 0)))
    writer.add_raw_group('DIAL', _topic_blob(0x01000100, (0x01000101, 0)))
    writer.add_raw_group('DIAL', _topic_blob(0x02000300, (0x02000301, 0)))
    output = tmp_path / 'Child.esp'
    assert bind_graph(child, output, writer)
    writer.write(str(output))
    seal_manifest(child, output)
    manifest = load_manifest(child, tmp_path, required=True)
    rows = {row['formid']: row for row in manifest['infos']}
    topics = manifest['topics']
    assert topics[rows[0x02000201]['parent']] == 0x02000900
    assert [topics[i] for i in rows[0x02000201]['choices']] == [0x01000100, 0x02000300]
    assert rows[0x01000101]['next_speaker'] == 2
    assert writer.conversation_hidden_topics == {0x01000100, 0x02000900}


@pytest.mark.parametrize('deleted', [False, True])
def test_missing_or_deleted_response_warns_without_stopping_conversion(tmp_path, deleted, capsys):
    folder = _export(tmp_path / 'Base.esm',
                     dials=[_dial(0x100, 'FearGeneral'), _dial(0x200, 'Surviving')],
                     infos=[_info(0x101, 0x100), _info(0x201, 0x200)])
    writer = PluginWriter(['Skyrim.esm'])
    writer.add_raw_group('DIAL', _topic_blob(0x01000200, (0x01000201, 0)))
    if deleted:
        writer.add_raw_group('DIAL', _topic_blob(0x01000100, (0x01000101, 0x20)))
    output = tmp_path / 'out' / 'Base.esm'
    assert bind_graph(folder, output, writer)
    writer.write(str(output))
    seal_manifest(folder, output)
    manifest = load_manifest(folder, tmp_path / 'out', required=True)
    assert manifest['missing_infos'] == ['base.esm:000101']
    assert [r['formid'] for r in manifest['infos']] == [0x01000201]
    actual = [r for r in records(output.read_bytes(), b'INFO') if not r.deleted]
    assert [r.form_id for r in actual] == [0x01000201]
    assert 'conversion continues' in capsys.readouterr().out


def test_inherited_response_resolves_through_master_index(tmp_path):
    root = tmp_path / 'export'
    _export(root / 'Base.esm', dials=[_dial(0x100, 'Reaction')], infos=[_info(0x101, 0x100)])
    child = _export(root / 'Child.esp', ('Base.esm',), [_dial(0x01000200, 'Head')],
                    [_info(0x01000201, 0x01000200, 0x100)])
    master_path = tmp_path / 'Base.esm'
    master_path.write_bytes(pack_tes4_header(['Skyrim.esm'])
                            + pack_top_group('DIAL', _topic_blob(0x01000100, (0x01000101, 0))))
    writer = PluginWriter(['Skyrim.esm', 'Base.esm'])
    writer.add_raw_group('DIAL', _topic_blob(0x02000200, (0x02000201, 0)))
    bind_graph(child, tmp_path / 'Child.esp', writer, MasterIndex(str(master_path)))
    assert len(load_manifest(child, tmp_path)['infos']) == 2


def test_empty_patch_scripts_do_not_require_inherited_bindings(tmp_path):
    root = tmp_path / 'export'
    _export(root / 'Base.esm', dials=[_dial(0x100, 'Reaction')],
            infos=[_info(0x101, 0x100)])
    child = _export(root / 'Empty.esp', ('Base.esm',))
    output = tmp_path / 'out' / 'scripts' / 'Source'

    stats = convert_all_scripts(str(child), str(output), workers=1)

    assert stats['scpt_total'] == stats['info_total'] == stats['qust_total'] == 0
    assert stats['scpt_err'] == stats['info_err'] == stats['qust_err'] == 0
    assert not list(output.glob('*DialogueGraph*.psc'))


@pytest.mark.parametrize('own_dialogue', [False, True])
def test_imported_patch_still_generates_bound_dialogue_scripts(tmp_path, own_dialogue):
    root = tmp_path / 'export'
    _export(root / 'Base.esm', dials=[_dial(0x100, 'Reaction')],
            infos=[_info(0x101, 0x100)])
    dials = [_dial(0x01000200, 'Head')] if own_dialogue else []
    infos = [_info(0x01000201, 0x01000200, 0x100)] if own_dialogue else []
    child = _export(root / 'Child.esp', ('Base.esm',), dials, infos)
    output = tmp_path / 'out'
    output.mkdir()
    master = output / 'Base.esm'
    master.write_bytes(pack_tes4_header(['Skyrim.esm'])
                       + pack_top_group('DIAL', _topic_blob(0x01000100, (0x01000101, 0))))
    writer = PluginWriter(['Skyrim.esm', 'Base.esm'])
    if own_dialogue:
        writer.add_raw_group('DIAL', _topic_blob(0x02000200, (0x02000201, 0)))
    plugin = output / 'Child.esp'
    bind_graph(child, plugin, writer, MasterIndex(str(master)))
    writer.write(str(plugin))
    seal_manifest(child, plugin)
    manifest = load_manifest(child, output, required=True)
    script_output = output / 'scripts' / 'Source'

    context = build_script_context(str(child), str(script_output))

    assert context['initargs'][-1]['infos'] == manifest['infos']
    assert len(context['info_work']) == int(own_dialogue)
    assert (script_output / (manifest['script'] + '.psc')).is_file()
    assert (script_output / (manifest['script'] + 'Page0.psc')).is_file()


def test_patch_with_own_dialogue_still_requires_import_bindings(tmp_path):
    root = tmp_path / 'export'
    _export(root / 'Base.esm', dials=[_dial(0x100, 'Reaction')],
            infos=[_info(0x101, 0x100)])
    child = _export(root / 'Child.esp', ('Base.esm',),
                    [_dial(0x01000200, 'Head')], [_info(0x01000201, 0x01000200)])
    with pytest.raises(ValueError, match='bindings are missing'):
        build_script_context(str(child), str(tmp_path / 'out' / 'scripts' / 'Source'))


def test_shared_output_sidecars_are_distinct_and_stale_exports_rejected(tmp_path):
    for name in ('One.esp', 'Two.esp'):
        folder = _export(tmp_path / 'export' / name, dials=[_dial(0x100, 'Head')],
                         infos=[_info(0x101, 0x100)])
        writer = PluginWriter(['Skyrim.esm'])
        writer.add_raw_group('DIAL', _topic_blob(0x01000100, (0x01000101, 0)))
        bind_graph(folder, tmp_path / name, writer)
    one = tmp_path / 'export' / 'One.esp'
    two = tmp_path / 'export' / 'Two.esp'
    assert load_manifest(one, tmp_path)['plugin'] == 'One.esp'
    assert load_manifest(two, tmp_path)['plugin'] == 'Two.esp'
    with (one / 'INFO.txt').open('a', encoding='utf-8') as stream:
        stream.write('# Export changed\n')
    with pytest.raises(ValueError, match='exports changed'):
        load_manifest(one, tmp_path)
    assert load_manifest(two, tmp_path)
    with pytest.raises(ValueError, match='bindings are missing'):
        load_manifest(two, tmp_path / 'missing', required=True)


def test_morrowind_uses_its_existing_dialogue_pipeline(tmp_path):
    folder = _export(tmp_path / 'Morrowind.esm', dials=[_dial(0x100, 'Voice')],
                     infos=[_info(0x101, 0x100)], source='TES3')
    assert source_graph(folder) == {'dials': {}, 'infos': {}}
    assert bind_graph(folder, tmp_path / 'out.esm', PluginWriter(['Skyrim.esm'])) is None


def test_incomplete_or_replaced_plugin_import_rejected(tmp_path):
    folder = _export(tmp_path / 'export' / 'Base.esm', dials=[_dial(0x100, 'Head')],
                     infos=[_info(0x101, 0x100)])
    writer = PluginWriter(['Skyrim.esm'])
    writer.add_raw_group('DIAL', _topic_blob(0x01000100, (0x01000101, 0)))
    output = tmp_path / 'Base.esm'
    bind_graph(folder, output, writer)
    with pytest.raises(ValueError, match='completed plugin import'):
        load_manifest(folder, tmp_path, required=True)
    writer.write(str(output))
    seal_manifest(folder, output)
    assert load_manifest(folder, tmp_path, required=True)
    output.write_bytes(output.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='completed plugin import'):
        load_manifest(folder, tmp_path, required=True)


def _condition(func, value, p1, p2=0, flag=0):
    return struct.pack('<B3sfH2sIII', flag, b'\0' * 3, value, func, b'\0' * 2,
                       p1, p2, 0).hex()


def test_counter_chain_without_tclt_repeats_until_last_value(tmp_path):
    rows = []
    for i in range(3):
        rows.append(_info(0x101 + i, 0x100, **{
            'Condition[0].Raw': _condition(72, 1.0, 0x500, flag=2),
            'Condition[1].Raw': _condition(79, float(i), 0x600, 1),
        }))
    folder = _export(tmp_path / 'Base.esm', dials=[_dial(0x100, 'Council')], infos=rows)
    graph = source_graph(folder)
    assert graph['infos']['base.esm', 0x101]['_choices'] == [('base.esm', 0x100)]
    assert graph['infos']['base.esm', 0x102]['_choices'] == [('base.esm', 0x100)]
    assert graph['infos']['base.esm', 0x103]['_choices'] == []


def test_startconversation_routes_to_graph_and_preserves_topic_binding(monkeypatch):
    monkeypatch.setattr(ScriptConverter, 'conversation_graph',
                        {'script': 'TES4_DialogueGraphTest', 'topic_edids': ['sermon']})
    monkeypatch.setattr(ScriptConverter, 'force_greet_slots', {})
    converter = ScriptConverter(CrossRefGraph())
    converter._property_refs['ProphetRef'] = 'Actor'
    output = '\n'.join(converter.convert_fragment('ProphetRef.StartConversation AudienceRef Sermon', 'Quest'))
    assert 'TES4_DialogueGraphTest.Play(ProphetRef,' in output
    assert 'Sermon)' in output
    assert converter.sc.property_refs['Sermon'] == 'Topic'
    assert 'Utility.Wait' not in output


def test_indirect_and_master_only_infos_have_end_hooks():
    records = {'DIAL': [_dial(0x100, 'Head'), _dial(0x200, 'Tail', 0)],
               'INFO': [_info(0x101, 0x100, 0x200), _info(0x301, 0x300)]}
    ScriptConverter.say_topics = scan_say_topic_fids(records)
    assert '00000200' in ScriptConverter.say_topics
    assert info_needs_fragment(_info(0x201, 0x200))
    assert info_needs_fragment(_info(0x301, 0x300))
    end = '\n'.join(_info_end_fragment(['  QuestRef.SetStage(25)'], '', [], None, 0))
    assert end.index('SetStage(25)') < end.index('NotifyEnd(')
    assert 'NotifyBegin(' in '\n'.join(_info_begin_fragment([], '', 0))


def test_large_routing_graph_is_paged_and_rebases_runtime_owner():
    manifest = {'script': 'TES4_DialogueGraphTest', 'plugin': 'Test.esp', 'quest': 0x02000ABC,
                'topics': [0x01000100, 0x02000200],
                'infos': [{'formid': 0x01001000 + i, 'parent': 0,
                           'choices': [1], 'next_speaker': 2} for i in range(65)]}
    scripts = generate_scripts(manifest)
    assert set(scripts) == {'TES4_DialogueGraphTest', *(f'TES4_DialogueGraphTestPage{i}' for i in range(3))}
    root = scripts['TES4_DialogueGraphTest']
    assert 'Form Property Owner1 Auto' in root
    assert 'FormSlot(Owner1.GetFormID())' in root
    assert 'localID += 16777216' in root  # Signed Papyrus IDs at load slots >= 128.
    assert 'GetFormFromFile(2748, "Test.esp")' in root
    assert 'TES4_DialogueGraphTestPage2.Select(1, localID)' in root

