"""A texture a plugin's meshes reference ships even from a pruned folder.

See: docs/commentary/asset_convert_texture.md#pruned-dir-references
"""
import inspect
import json
from pathlib import Path

import convert
from asset_convert.sources import bsa_pack
from asset_convert.texture import texture_prune
from output_layout import asset_root, plugin_out_root, record_dir


def _put(path, data=b'DDS '):
    """Write `data` at `path`, creating its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _fake_bsarch(monkeypatch, staged):
    """Replace BSArch: record each staged tree's files instead of packing."""
    def run(_exe, stage_root, bsa_path, _compress, results):
        """Record what was staged."""
        staged[bsa_path.name] = sorted(
            p.relative_to(stage_root).as_posix()
            for p in Path(stage_root).rglob('*') if p.is_file())
        results['packed'].append(str(bsa_path))
        return True
    monkeypatch.setattr(bsa_pack, '_run_bsarch', run)


def _nested_mod(tmp_path, manifest=True, meshes=True):
    """A two-plugin mod (record dir nested below the asset dir) over a master.

    Returns (export root, output root, BSArch path).
    """
    exp = tmp_path / 'export'
    exp.mkdir()
    (exp / 'sources.json').write_text(json.dumps({
        'version': 1,
        'sources': {n: {'kind': 'archive', 'plugin': n, 'group_id': 'g1',
                        'group_label': 'My Mod',
                        'group_plugins': ['A.esp', 'B.esp']}
                    for n in ('A.esp', 'B.esp')},
    }), encoding='utf-8')
    rec = record_dir(exp, 'A.esp')
    _put(rec / '_HEADER.txt', b'Master[0]=Base.esm\n')
    if manifest:
        _put(asset_root(exp, 'A.esp') / texture_prune.MANIFEST_NAME,
             b'tes4/clutter/cup.dds\ntes4/menus/book/page.dds\n'
             b'tes4/menus/faders/black.dds\n')
    out = tmp_path / 'output'
    mod = plugin_out_root(out, 'A.esp', str(exp))
    for rel in ('textures/tes4/clutter/cup.dds', 'textures/tes4/menus/book/page.dds',
                'textures/tes4/menus/unused.dds'):
        _put(mod / rel)
    if meshes:
        _put(mod / 'meshes' / 'tes4' / 'cup.nif', b'NIF')
    _put(out / 'Base.esm' / 'textures' / 'tes4' / 'Menus' / 'Faders' / 'Black.dds')
    exe = tmp_path / 'BSArch.exe'
    exe.write_bytes(b'')
    return exp, out, exe


def _pack(exp, out, exe, manifest_dir):
    """Pack A.esp the way `phase_pack` wires it, with `manifest_dir` given."""
    return bsa_pack.pack_bsas('A.esp', output_dir=str(out), bsarch_path=str(exe),
                              export_dir=str(record_dir(exp, 'A.esp')),
                              export_root=str(exp), manifest_dir=manifest_dir)


def test_keep_escapes_the_folder_rule_only():
    """A kept menus key ships; a kept non-texture never does."""
    assert texture_prune.is_excluded('tes4/menus/a.dds')
    assert not texture_prune.is_excluded('tes4/menus/a.dds', {'tes4/menus/a.dds'})
    assert texture_prune.is_excluded('tes4/menus/a.txt', {'tes4/menus/a.txt'})


def test_pruned_refs_are_the_manifest_keys_in_excluded_folders():
    """Only pruned-folder texture keys are exempt candidates."""
    got = texture_prune.pruned_refs({'tes4/menus/a.dds', 'tes4/clutter/b.dds',
                                     'tes4/faces/c.dds', 'tes4/menus/d.txt'})
    assert got == {'tes4/menus/a.dds', 'tes4/faces/c.dds'}


def test_nested_mod_ships_its_referenced_menus_textures(tmp_path, monkeypatch,
                                                        capsys):
    """Own referenced page ships, master's fader is carried, the unused one is cut."""
    exp, out, exe = _nested_mod(tmp_path)
    assert asset_root(exp, 'A.esp') != record_dir(exp, 'A.esp')
    staged = {}
    _fake_bsarch(monkeypatch, staged)
    r = _pack(exp, out, exe, str(asset_root(exp, 'A.esp')))
    assert r['errors'] == []
    assert staged['A - Textures.bsa'] == [
        'textures/tes4/clutter/cup.dds', 'textures/tes4/menus/book/page.dds',
        'textures/tes4/menus/faders/black.dds']
    assert ('pruned-dir exempted: 2  (own 1, carried from masters 1, '
            'found nowhere 0)') in capsys.readouterr().out


def test_missing_manifest_refuses_the_pack(tmp_path, monkeypatch, capsys):
    """Pointing at the record dir (no manifest there) packs nothing and says so."""
    exp, out, exe = _nested_mod(tmp_path)
    staged = {}
    _fake_bsarch(monkeypatch, staged)
    r = _pack(exp, out, exe, str(record_dir(exp, 'A.esp')))
    assert staged == {}
    assert any('refusing' in e for e in r['errors'])
    assert 'ERROR texture manifest' in capsys.readouterr().out


def test_meshless_plugin_packs_without_a_manifest(tmp_path, monkeypatch):
    """No converted meshes means no manifest is owed; menus stay pruned."""
    exp, out, exe = _nested_mod(tmp_path, manifest=False, meshes=False)
    staged = {}
    _fake_bsarch(monkeypatch, staged)
    r = _pack(exp, out, exe, str(asset_root(exp, 'A.esp')))
    assert r['errors'] == []
    assert staged['A - Textures.bsa'] == ['textures/tes4/clutter/cup.dds']


def test_phase_pack_passes_the_asset_dir_as_the_manifest_dir():
    """The manifest sits beside the shared assets, not in the record dir."""
    src = inspect.getsource(convert.phase_pack)
    assert 'manifest_dir=str(asset_root(export_root, file_name))' in src


def test_mesh_stage_without_source_meshes_writes_an_empty_manifest(
        tmp_path, monkeypatch):
    """No source meshes/ still leaves a manifest, so tree-only output packs."""
    from asset_convert import asset_pipeline
    exp, out, exe = _nested_mod(tmp_path, manifest=False)
    for name in ('assemble_armor', '_copy_and_fix_textures',
                 '_report_case_paths'):
        monkeypatch.setattr(asset_pipeline, name, lambda *a, **k: None)
    monkeypatch.setattr(asset_pipeline.landscape_normals, 'ensure_ltex_normals',
                        lambda *a, **k: (0, 0))
    assert not (asset_root(exp, 'A.esp') / 'meshes').exists()
    asset_pipeline.convert_meshes('A.esp', extract_dir=str(exp),
                                  output_dir=str(out))
    manifest = asset_root(exp, 'A.esp') / texture_prune.MANIFEST_NAME
    assert manifest.is_file() and manifest.read_text(encoding='utf-8') == ''
    staged = {}
    _fake_bsarch(monkeypatch, staged)
    r = _pack(exp, out, exe, str(asset_root(exp, 'A.esp')))
    assert r['errors'] == []
    assert 'A.bsa' in staged


def test_a_later_master_wins_the_carry(tmp_path):
    """Two masters ship the key: the later one in header order is carried."""
    exp, out, _exe = _nested_mod(tmp_path)
    _put(record_dir(exp, 'A.esp') / '_HEADER.txt',
         b'Master[0]=Base.esm\nMaster[1]=Patch.esp\n')
    _put(out / 'Patch.esp' / 'textures' / 'tes4' / 'menus' / 'faders' / 'black.dds',
         b'DDS patched')
    entries, nowhere = bsa_pack._carry_from_masters(
        {'tes4/menus/faders/black.dds'}, [], str(record_dir(exp, 'A.esp')),
        str(out), str(exp))
    assert nowhere == []
    assert [e[0].read_bytes() for e in entries] == [b'DDS patched']
