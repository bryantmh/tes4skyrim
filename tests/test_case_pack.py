"""Packing every case spelling of a folder, the collision gate, and the zip refusal.

The packer read only `textures/` and `meshes/`, so a `Textures/` beside them
never reached the BSA; and `_run_steps` zipped whatever archives were on disk
even when this run's BSA pack had failed.
See: docs/commentary/asset_convert_paths.md#pack-gate
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import convert
from asset_convert.sources import bsa_pack


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _put(path, data=b'DDS '):
    """Write `data` at `path`, creating its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _plugin(tmp_path, files):
    """output/Oblivion.esm holding the plugin, a stale BSA and `files`."""
    root = tmp_path / 'output' / 'Oblivion.esm'
    for rel in files:
        _put(root / rel)
    _put(root / 'Oblivion.esm', b'TES4')
    _put(root / 'Oblivion.bsa', b'BSA')
    (tmp_path / 'output' / 'Finished Mods').mkdir(parents=True, exist_ok=True)
    return root


def _fake_bsarch(monkeypatch, staged, ok=True):
    """Replace BSArch: record each staged tree's files; succeed or fail."""
    def run(_exe, stage_root, bsa_path, _compress, results):
        """Record what was staged instead of packing it."""
        staged[bsa_path.name] = sorted(
            p.relative_to(stage_root).as_posix()
            for p in Path(stage_root).rglob('*') if p.is_file())
        if not ok:
            results['errors'].append(bsa_path.name + ': failed')
            return False
        results['packed'].append(str(bsa_path))
        return True
    monkeypatch.setattr(bsa_pack, '_run_bsarch', run)


def _run(tmp_path, monkeypatch, steps):
    """Drive convert._run_steps over Oblivion.esm without the machine-wide lock."""
    exe = tmp_path / 'BSArch.exe'
    exe.write_bytes(b'')
    monkeypatch.setattr(convert, 'hold_heavy_lock', lambda *_a: None)
    monkeypatch.setattr(convert, 'is_asset_only', lambda *_a: False)
    args = SimpleNamespace(only=None, mesh_subdirs=None, config=None,
                           textures_only=False, parallax=False, skip_hair=False,
                           collision_winding_fix=False, no_engine_branches=False,
                           patch_plugins=None)
    run = SimpleNamespace(args=args, config={'bsarchPath': str(exe)},
                          tes4_data='', tes5_data='', export_dir='',
                          output_dir=str(tmp_path / 'output'))
    return convert._run_steps(steps, ['Oblivion.esm'], run)


_ZIP = ('output', 'Finished Mods', 'Oblivion.esm.zip')


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def test_every_case_spelling_of_textures_is_collected(tmp_path):
    """4 of 4 files from Textures/ and textures/, under the lowercase top."""
    root = _plugin(tmp_path, ['Textures/tes4/a.dds', 'Textures/tes4/b.dds',
                              'textures/tes4/c.dds', 'textures/tes4/d.dds'])
    got = bsa_pack._collect_files(root, ['textures'])
    assert [e[1].as_posix() for e in got] == [
        'textures/tes4/a.dds', 'textures/tes4/b.dds',
        'textures/tes4/c.dds', 'textures/tes4/d.dds']


def test_pack_stages_all_spellings_of_textures_and_a_misc_dir(tmp_path, monkeypatch):
    """The archives hold every file, and a misc dir spelled twice packs once."""
    _plugin(tmp_path, ['Textures/tes4/a.dds', 'textures/tes4/b.dds',
                       'Sound/fx/a.wav', 'sound/fx/b.wav', 'meshes/x.nif'])
    staged = {}
    _fake_bsarch(monkeypatch, staged)
    exe = tmp_path / 'BSArch.exe'
    exe.write_bytes(b'')
    r = bsa_pack.pack_bsas('Oblivion.esm', output_dir=str(tmp_path / 'output'),
                           bsarch_path=str(exe))
    assert r['errors'] == []
    assert staged['Oblivion - Textures.bsa'] == ['textures/tes4/a.dds',
                                                 'textures/tes4/b.dds']
    assert staged['Oblivion.bsa'] == ['meshes/x.nif', 'sound/fx/a.wav',
                                      'sound/fx/b.wav']


# ---------------------------------------------------------------------------
# The gate and the zip
# ---------------------------------------------------------------------------


def test_file_collision_blocks_the_pack_and_the_zip(tmp_path, monkeypatch, capsys):
    """Two files on one archive path: nothing packed, no zip written."""
    _plugin(tmp_path, ['Textures/tes4/Stone.dds', 'textures/tes4/stone.dds'])
    staged = {}
    _fake_bsarch(monkeypatch, staged)
    step_ok, success = _run(tmp_path, monkeypatch, ['pack_bsa', 'pack_zip'])
    assert staged == {} and not success
    assert step_ok['pack'] == {'Oblivion.esm': False}
    assert not tmp_path.joinpath(*_ZIP).exists()
    out = capsys.readouterr().out
    assert 'differ only by case' in out and 'no zip is written' in out


def test_folder_twins_alone_only_warn(tmp_path, monkeypatch, capsys):
    """Twin folders with distinct files pack, and the zip is written."""
    _plugin(tmp_path, ['textures/tes4/Dementia/a.dds', 'textures/tes4/dementia/b.dds'])
    staged = {}
    _fake_bsarch(monkeypatch, staged)
    step_ok, success = _run(tmp_path, monkeypatch, ['pack_bsa', 'pack_zip'])
    assert success and len(staged['Oblivion - Textures.bsa']) == 2
    assert tmp_path.joinpath(*_ZIP).is_file()
    assert 'WARN' in capsys.readouterr().out


def test_a_failed_pack_is_never_zipped(tmp_path, monkeypatch):
    """BSArch failing in this run leaves no zip of the stale archive on disk."""
    _plugin(tmp_path, ['textures/tes4/a.dds'])
    _fake_bsarch(monkeypatch, {}, ok=False)
    step_ok, _ = _run(tmp_path, monkeypatch, ['pack_bsa', 'pack_zip'])
    assert step_ok['pack_zip'] == {'Oblivion.esm': False}
    assert not tmp_path.joinpath(*_ZIP).exists()


def test_zip_only_run_still_zips(tmp_path, monkeypatch):
    """With no pack step in this run the zip is written as before."""
    _plugin(tmp_path, ['textures/tes4/a.dds'])
    step_ok, success = _run(tmp_path, monkeypatch, ['pack_zip'])
    assert success and tmp_path.joinpath(*_ZIP).is_file()
