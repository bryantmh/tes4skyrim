"""The items table MorrowindRuntime resolves AddItem and GetItemCount ids through.

See: docs/commentary/morrowind_runtime.md#soul-gems
"""

import os

from tes5_import.dialogue.morrowind_sidecar import write_morrowind_sidecar


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
