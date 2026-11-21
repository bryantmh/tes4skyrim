"""Hybrid source mode: whitelist reading, world swaps, the assembler guard, and the furniture plugin's overrides."""

import json
from types import SimpleNamespace

from asset_convert.character.morrowind_armor import _assembled
from tes4_export import morroblivion_furniture as furniture
from tes4_export.morroblivion_hybrid import _swap_world, load_whitelist


def _rows(tmp_path, rows):
    """A whitelist file holding `rows`."""
    path = tmp_path / 'wl.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    return path


def test_whitelist_keeps_denials_and_only_approved_worn(tmp_path):
    """A denied world pair maps to '' so furniture approval cannot override it; denied worn models are absent."""
    world, worn = load_whitelist(_rows(tmp_path, [
        {'kind': 'world', 'vanilla': 'f\\A.nif', 'morroblivion': 'morro/f/a.nif', 'decision': 'approve'},
        {'kind': 'world', 'vanilla': 'f\\B.nif', 'morroblivion': 'morro/f/b.nif', 'decision': 'deny'},
        {'kind': 'worn', 'vanilla': 'x', 'morroblivion': 'Armor\\Y\\m.nif', 'decision': 'approve'},
        {'kind': 'worn', 'vanilla': 'z', 'morroblivion': 'Armor\\Z\\m.nif', 'decision': 'deny'}]))
    assert world == {'f\\a.nif': 'morro\\f\\a.nif', 'f\\b.nif': ''}
    assert worn == {'armor\\y\\m.nif'}


def test_world_swap_rewrites_model_and_records_it():
    """An approved model replaces the line; an unapproved one is left alone."""
    hybrid = SimpleNamespace(named=set(), world_model=lambda p: 'morro\\f\\a.nif' if p == 'f\\a.nif' else '')
    ctx = SimpleNamespace(origin_shifts=None)
    lines = ['EditorID=a', 'Model.MODL=f\\\\a.nif']
    assert _swap_world(lines, hybrid, ctx) == 1
    assert lines[1] == 'Model.MODL=morro\\\\f\\\\a.nif' and hybrid.named == {'morro\\f\\a.nif'}
    other = ['Model.MODL=f\\\\b.nif']
    assert _swap_world(other, hybrid, ctx) == 0 and other == ['Model.MODL=f\\\\b.nif']


def test_assembler_writes_only_its_own_folder():
    """A swapped-in Morroblivion worn model is never overwritten by the vanilla assembly."""
    assert _assembled('armor\\\\morrowind\\\\m\\\\0x.nif') and _assembled('armor\\morrowind\\f\\0x.nif')
    assert not _assembled('VvardenfellArmormod\\netch\\helmet.nif') and not _assembled('')


def _export(folder, header, files):
    """Write a minimal export folder: _HEADER.txt plus {signature: [record dict]}."""
    folder.mkdir(parents=True)
    (folder / '_HEADER.txt').write_text(header, encoding='utf-8')
    for sig, recs in files.items():
        text = ''.join('---RECORD_BEGIN---\n' + ''.join(f'{k}={v}\n' for k, v in r.items())
                       + '---RECORD_END---\n\n' for r in recs)
        (folder / f'{sig}.txt').write_text(text, encoding='utf-8')


def test_furniture_family_is_the_roots_hybrid_dependents(tmp_path):
    """A root's family: itself, then hybrid exports whose masters are all in it; others are left out."""
    def plugin(path, masters, hybrid=True):
        """One export folder with `masters`, marked hybrid or not."""
        path.mkdir(parents=True)
        (path / '_HEADER.txt').write_text(''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)),
                                          encoding='utf-8')
        if hybrid:
            (path / furniture.MANIFEST_NAME).write_text('', encoding='utf-8')
    plugin(tmp_path / 'Root.esm', [])
    plugin(tmp_path / 'Mod' / 'Child.esp', ['Root.esm'])
    plugin(tmp_path / 'Grand.esp', ['Root.esm', 'Child.esp'])
    plugin(tmp_path / 'Other.esp', ['Elsewhere.esm'])
    plugin(tmp_path / 'Plain.esp', ['Root.esm'], hybrid=False)
    names = [n for n, _p in furniture.family(str(tmp_path), 'Root.esm')]
    assert names == ['Root.esm', 'Child.esp', 'Grand.esp']
    assert furniture.furniture_name('Root.esm') == 'Root - Morroblivion Furniture.esp'
    assert furniture.is_furniture('Root - Morroblivion Furniture.esp')


def test_furniture_overrides_rekey_each_master(tmp_path):
    """Bloodmoon's own id (01 in its file) is 02 in [Morrowind, Tribunal, Bloodmoon]; deleted refs stay."""
    mw, bm = tmp_path / 'mw', tmp_path / 'bm'
    _export(mw, 'Source=TES3\n', {'REFR': [
        {'Signature': 'REFR', 'FormID': '00000010', 'NAME': '00000001', 'ParentCELL': '00000100', 'RecordFlags': '0'},
        {'Signature': 'REFR', 'FormID': '00000011', 'NAME': '00000001', 'ParentCELL': '00000100', 'RecordFlags': '32'}]})
    _export(bm, 'Master[0]=Morrowind.esm\nSource=TES3\n', {'REFR': [
        {'Signature': 'REFR', 'FormID': '01000020', 'NAME': '00000001', 'ParentCELL': '01000200', 'RecordFlags': '0'}]})
    masters = [('Morrowind.esm', str(mw)), ('Bloodmoon.esm', str(bm))]
    names = ['Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm']
    refs = dict(furniture.placements(masters, names, {'00000001': ('03ABCDEF', [])}))
    assert sorted(refs) == ['00000010', '02000020']
    assert 'NAME=03ABCDEF' in refs['02000020'] and 'ParentCELL=02000200' in refs['02000020']
