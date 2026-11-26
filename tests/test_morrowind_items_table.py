"""The items table MorrowindRuntime resolves AddItem and GetItemCount ids through.

See: docs/commentary/morrowind_runtime.md#soul-gems
"""

import os

from tes5_import.base.equivalents import (SKYRIM_LOCKPICK, SKYRIM_SKELETON_KEY,
                                          TES4_ITEM_FORMID_TO_SKYRIM,
                                          morrowind_lockpick_substitutions,
                                          set_run_item_substitutions)
from tes5_import.base.text_reader import get_formid
from tes5_import.dialogue.morrowind_sidecar import (item_lines, table_dirs,
                                                    write_morrowind_sidecar)
from tes5_import.dialogue.morrowind_sidecar_source import gather


def _items(tmp_path, exports):
    """The staged items_formid.txt rows for an export holding these `{file: [records]}`."""
    folder = tmp_path / 'export' / 'Plugin.esm'
    folder.mkdir(parents=True)
    (folder / '_HEADER.txt').write_text('Source=TES3\n', encoding='utf-8')
    for name, records in exports.items():
        (folder / name).write_text(''.join(
            '---RECORD_BEGIN---\n' + ''.join(f'{k}={v}\n' for k, v in rec.items())
            + '---RECORD_END---\n' for rec in records), encoding='utf-8')
    out = tmp_path / 'output' / 'Plugin.esm'
    out.mkdir(parents=True)
    write_morrowind_sidecar(str(folder), str(out / 'Plugin.esm'), 'Plugin.esm')
    path = os.path.join(str(out), 'SKSE', 'Plugins', 'MorrowindRuntime', 'Plugin', 'items_formid.txt')
    with open(path, encoding='utf-8') as handle:
        return handle.read().splitlines()


def test_an_empty_soul_gem_record_is_an_item(tmp_path):
    """A TES4 SLGM, empty or filled, resolves by id like any other item."""
    rows = _items(tmp_path, {
        'SLGM.txt': [{'Signature': 'SLGM', 'FormID': '00000801', 'EditorID': 'Misc_SoulGem_Greater',
                      'SOUL': '0'},
                     {'Signature': 'SLGM', 'FormID': '00000802', 'EditorID': 'Misc_SoulGem_Greater_Filled1',
                      'SOUL': '4'}],
        'MISC.txt': [{'Signature': 'MISC', 'FormID': '00000803', 'EditorID': 'Misc_Quill'}]})
    assert 'Misc_SoulGem_Greater=Plugin.esm|00000801' in rows
    assert 'Misc_SoulGem_Greater_Filled1=Plugin.esm|00000802' in rows
    assert 'Misc_Quill=Plugin.esm|00000803' in rows


def _tes3_file(path, records) -> str:
    """A TES3 file on disk holding `(signature, id)` records; its path."""
    blob = b'TES3' + bytes(12)
    for sig, record_id in records:
        name = record_id.encode('cp1252') + b'\x00'
        body = b'NAME' + len(name).to_bytes(4, 'little') + name
        blob += sig.encode('ascii') + len(body).to_bytes(4, 'little') + bytes(8) + body
    path.write_bytes(blob)
    return str(path)


def test_a_tes3_lockpick_names_skyrims_lockpick(tmp_path):
    """A TES3 lockpick resolves to Skyrim's Lockpick, the skeleton key to its Skeleton Key.

    See: docs/commentary/tes5_import_pipeline.md#morrowind-lockpicks
    """
    source = _tes3_file(tmp_path / 'Plugin.esm', [('LOCK', 'pick_apprentice_01'),
                                                 ('LOCK', 'Skeleton_Key'),
                                                 ('PROB', 'probe_apprentice_01')])
    gathered = gather([('Plugin.esm', source)])
    assert gathered['lockpicks'] == {'pick_apprentice_01', 'skeleton_key'}
    folder = tmp_path / 'export' / 'Plugin.esm'
    folder.mkdir(parents=True)
    (folder / '_HEADER.txt').write_text('Source=TES3\n', encoding='utf-8')
    (folder / 'MISC.txt').write_text(''.join(
        f'---RECORD_BEGIN---\nSignature=MISC\nFormID={fid}\nEditorID={edid}\n---RECORD_END---\n'
        for fid, edid in (('00000801', 'pick_apprentice_01'),
                          ('00000802', 'probe_apprentice_01'),
                          ('00000803', 'Skeleton_Key'))), encoding='utf-8')
    rows = item_lines(table_dirs(str(folder), 'Plugin.esm'), str(tmp_path / 'export'), gathered)
    assert 'pick_apprentice_01=Skyrim.esm|0000000A' in rows
    assert 'probe_apprentice_01=Plugin.esm|00000802' in rows
    assert 'Skeleton_Key=Skyrim.esm|0003A070' in rows


def test_a_reference_to_a_morrowind_lockpick_lands_on_skyrims():
    """A master's or the plugin's lockpick is substituted in references, never as its own id."""
    picks = morrowind_lockpick_substitutions([
        ('00000801', {'MorrowindTool': 'LOCK'}), ('00000802', {'MorrowindTool': 'PROB'}),
        ('00000803', {}), ('00000804', {'MorrowindTool': 'LOCK', 'EditorID': 'skeleton_key'})])
    assert picks == {0x801: SKYRIM_LOCKPICK, 0x804: SKYRIM_SKELETON_KEY}
    try:
        set_run_item_substitutions(picks)
        assert get_formid({'Item': '00000801'}, 'Item') == SKYRIM_LOCKPICK
        assert get_formid({'FormID': '00000801'}, 'FormID') == 0x801
        assert get_formid({'Item': '00000802'}, 'Item') == 0x802
    finally:
        set_run_item_substitutions({})
    assert 0x801 not in TES4_ITEM_FORMID_TO_SKYRIM
