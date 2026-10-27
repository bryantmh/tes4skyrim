"""A plugin stages its own placements of the scripted objects its MWScript masters define."""

from tes5_import.dialogue.morrowind_placements import (folder_tables,
                                                       forget_folder_tables,
                                                       rebased_formid, script_named_ids)
from tes5_import.pipeline_records import move_persistent_refs
from tes5_import.record_types.world_morrowind import REFR_PERSISTENT, persist_script_named_refs

#: The patch's own banner base and its script, as the patch's export spells them.
_PATCH_BANNER, _PATCH_SCRIPT = '01000ABC', '01000DEF'

#: A Morroblivion base: its script is Oblivion script, run as Papyrus.
_OB_BANNER = '00000111'


def _records(signature, *records):
    """Export text holding `records`, each a dict of fields."""
    out = []
    for rec in records:
        out.append('---RECORD_BEGIN---')
        out.append(f'Signature={signature}')
        out.extend(f'{key}={value}' for key, value in rec.items())
        out.append('---RECORD_END---')
    return '\n'.join(out) + '\n'


def _plugin(root, name, masters, **files):
    """One plugin's export folder: a header naming `masters`, plus `files`."""
    folder = root / name
    folder.mkdir(parents=True)
    header = ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters))
    (folder / '_HEADER.txt').write_text(header, encoding='utf-8')
    for file_name, text in files.items():
        (folder / file_name.replace('_txt', '.txt')).write_text(text, encoding='utf-8')
    return folder


def _export(tmp_path):
    """Morrowind_ob, Other and the patch as masters of TR, which places a banner from each."""
    root = tmp_path / 'export'
    _plugin(root, 'Morrowind_ob.esm', [],
            ACTI_txt=_records('ACTI', {'FormID': _OB_BANNER, 'EditorID': 'obBanner',
                                       'SCRI': '00000222'}))
    _plugin(root, 'Other.esm', ['Morrowind_ob.esm'])
    _plugin(root, 'Patch.esp', ['Morrowind_ob.esm'],
            ACTI_txt=_records('ACTI', {'FormID': _PATCH_BANNER, 'EditorID': 'banner',
                                       'SCRI': _PATCH_SCRIPT}))
    refs = _records('REFR', {'FormID': '03000001', 'NAME': '02000ABC', 'ParentCELL': '00000001'},
                    {'FormID': '03000002', 'NAME': _OB_BANNER, 'ParentCELL': '00000001'})
    return root, _plugin(root, 'TR.esm', ['Morrowind_ob.esm', 'Other.esm', 'Patch.esp'],
                         REFR_txt=refs)


def test_a_masters_formid_is_respelled_by_the_dependents_master_list(tmp_path):
    """The patch's own 01 index becomes 02, the patch's slot in TR's header."""
    root, _tr = _export(tmp_path)
    header =['Morrowind_ob.esm', 'Other.esm', 'Patch.esp']
    assert rebased_formid(_PATCH_BANNER, str(root / 'Patch.esp'), 'Patch.esp', header) == '02000ABC'
    assert rebased_formid(_PATCH_BANNER, str(root / 'Patch.esp'), 'Patch.esp', ['Other.esm']) is None


def test_a_placed_master_object_is_staged_but_a_morroblivion_one_is_not(tmp_path):
    """TR's placement of the patch's banner runs its MWScript; Morroblivion's runs as Papyrus."""
    _root, tr = _export(tmp_path)
    forget_folder_tables()
    staged = {rec['FormID'] for rec in folder_tables(str(tr))['instances']}
    forget_folder_tables()
    assert staged == {'03000001'}


def _named_export(tmp_path):
    """A TES3 plugin whose script names `chest_a` and only comments on `chest_b`; each is placed twice."""
    folder = _plugin(tmp_path / 'export', 'Mw.esm', [],
                     CONT_txt=_records('CONT', {'FormID': '00000A01', 'EditorID': 'chest_a'},
                                       {'FormID': '00000B01', 'EditorID': 'chest_b'}),
                     SCPT_txt=_records('SCPT', {'SCTX': 'Begin s\\r\\n"Chest_A"->Lock 50 ; chest_b\\r\\nEnd'}))
    (folder / '_HEADER.txt').write_text('Source=TES3\n', encoding='utf-8')
    refs = [{'FormID': f'0000000{i}', 'NAME': base, 'ParentCELL': '00000C01', 'RecordFlags': '0'}
            for i, base in enumerate(('00000A01', '00000A01', '00000B01'), 1)]
    (folder / 'REFR.txt').write_text(_records('REFR', *refs), encoding='utf-8')
    return folder, refs


def test_a_script_named_id_ships_its_first_placement_persistent(tmp_path):
    """Only the first placement of an id a script names outside a comment becomes persistent."""
    folder, refs = _named_export(tmp_path)
    assert {'chest_a', 'lock', 'begin'} <= set(script_named_ids(str(folder)))
    assert 'chest_b' not in script_named_ids(str(folder))
    forget_folder_tables()
    changed = persist_script_named_refs({'REFR': refs}, str(folder))
    forget_folder_tables()
    assert changed == 1
    assert [rec['RecordFlags'] for rec in refs] == [str(REFR_PERSISTENT), '0', '0']


def test_exterior_persistent_refs_move_into_the_persistent_cell():
    """A grid cell's persistent ref joins the world's persistent cell; its temporary one stays."""
    grid, pcell = {'FormID': '00000C01'}, 0x0D01
    kept, moved = {'RecordFlags': '0'}, {'RecordFlags': str(REFR_PERSISTENT)}
    ctx = {'refr_by_cell': {0x0C01: [kept, moved]}, 'achr_by_cell': {}}
    move_persistent_refs([grid], pcell, ctx)
    assert ctx['refr_by_cell'] == {0x0C01: [kept], pcell: [moved]}
