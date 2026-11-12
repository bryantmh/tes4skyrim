"""A dependent plugin's FlameNode sockets read its masters' STATs and flame NIFs.

See: docs/commentary/asset_convert_nif.md#flame-stats-from-the-masters
"""
import json
import os
import time

import pytest

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from asset_convert.nif import nif_flames
from asset_convert.sources.base_plugins import record_chain_for_assets
from output_layout import record_dirs_for_assets


def _stat(rec_dir, sockets):
    """Write a STAT.txt holding one FlameNode<N> STAT per {N: flame nif} entry."""
    os.makedirs(rec_dir, exist_ok=True)
    text = ''.join(
        f'---RECORD_BEGIN---\nFormID=000000{20 + n:02X}\nEditorID=FlameNode{n}\n'
        f'Model.MODL=Fire\\\\{flame}\n---RECORD_END---\n'
        for n, flame in sockets.items())
    with open(os.path.join(rec_dir, 'STAT.txt'), 'w', encoding='latin1') as fh:
        fh.write(text)


def _header(rec_dir, masters):
    """Write a _HEADER.txt naming `masters` in order."""
    os.makedirs(rec_dir, exist_ok=True)
    with open(os.path.join(rec_dir, '_HEADER.txt'), 'w', encoding='utf-8') as fh:
        fh.write(''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)))


def _fire(asset_dir, name):
    """Write a placeholder flame NIF at <asset_dir>/meshes/fire/<name>; its path."""
    path = os.path.join(asset_dir, 'meshes', 'fire', name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'flame:' + path.encode('utf-8'))
    return path


def _host(asset_dir):
    """The path of a host mesh in `asset_dir` (it need not exist)."""
    return os.path.join(asset_dir, 'meshes', 'lights', 'lamp01.nif')


@pytest.fixture
def export(tmp_path):
    """An export root holding a master `Base.esm` with socket 0 and its flame NIF."""
    root = tmp_path / 'export'
    _stat(str(root / 'Base.esm'), {0: 'FireCandleFlame.NIF'})
    _header(str(root / 'Base.esm'), [])
    _fire(str(root / 'Base.esm'), 'firecandleflame.nif')
    return root


def _nested(root, mod, plugins, exported):
    """Register `plugins` as one mod in `root`'s sources.json; export `exported`."""
    sources = {p: {'plugin': p, 'group_id': mod, 'group_label': mod,
                   'group_plugins': list(plugins)} for p in plugins}
    with open(root / 'sources.json', 'w', encoding='utf-8') as fh:
        json.dump({'version': 2, 'sources': sources}, fh)
    for p in exported:
        _header(str(root / mod / p), ['Base.esm'])
    return root / mod


class _FakeData:
    """Stands in for NifFormat.Data: `write` echoes the bytes `read` saw."""

    def __init__(self):
        """Nothing read yet."""
        self.blob = b''

    def inspect(self, fh):
        """No header check."""

    def read(self, fh):
        """Keep the file's bytes."""
        self.blob = fh.read()

    def write(self, fh):
        """Write back the bytes that were read."""
        fh.write(self.blob)


class _FakeNifFormat:
    """The one attribute `_load_converted_flame` uses."""

    Data = _FakeData


@pytest.fixture
def converted(monkeypatch):
    """The src_path each convert_nif call saw, with NIF parsing stubbed out."""
    seen = []
    monkeypatch.setattr(nif_flames, 'NifFormat', _FakeNifFormat)

    def convert(fdata, **kwargs):
        """Record the flame source path; no stats."""
        seen.append(kwargs.get('src_path'))
        return {}
    return seen, convert


def test_flat_dependent_reads_the_masters_flame_stat(export):
    """A flat plugin with no FlameNode STAT of its own reads its master's."""
    _header(str(export / 'Child.esp'), ['Base.esm'])
    table = nif_flames.flame_socket_map(_host(str(export / 'Child.esp')))
    assert table == {0: 'firecandleflame.nif'}


def test_own_flame_stat_wins_a_socket(export):
    """The plugin's own FlameNode STAT beats its master's for the same socket."""
    _header(str(export / 'Child.esp'), ['Base.esm'])
    _stat(str(export / 'Child.esp'), {0: 'FireTorchSmall.nif'})
    table = nif_flames.flame_socket_map(_host(str(export / 'Child.esp')))
    assert table == {0: 'firetorchsmall.nif'}


def test_own_and_master_sockets_are_merged(export):
    """Sockets the plugin does not define still come from the master."""
    _header(str(export / 'Child.esp'), ['Base.esm'])
    _stat(str(export / 'Child.esp'), {2: 'FireTorchLarge.nif'})
    table = nif_flames.flame_socket_map(_host(str(export / 'Child.esp')))
    assert table == {0: 'firecandleflame.nif', 2: 'firetorchlarge.nif'}


def test_the_last_master_wins_a_socket(export):
    """Of two masters defining one socket, the later in the header wins."""
    _stat(str(export / 'Patch.esp'), {0: 'FireOpenSmall.nif'})
    _header(str(export / 'Patch.esp'), ['Base.esm'])
    _header(str(export / 'Child.esp'), ['Base.esm', 'Patch.esp'])
    table = nif_flames.flame_socket_map(_host(str(export / 'Child.esp')))
    assert table == {0: 'fireopensmall.nif'}


def test_a_master_reads_only_its_own_stat(export):
    """A masterless plugin's map is its own STAT, as before."""
    table = nif_flames.flame_socket_map(_host(str(export / 'Base.esm')))
    assert table == {0: 'firecandleflame.nif'}


def test_a_master_that_was_not_exported_is_skipped(export):
    """A header master with no export folder adds nothing and raises nothing."""
    _header(str(export / 'Child.esp'), ['Missing.esm'])
    assert nif_flames.flame_socket_map(_host(str(export / 'Child.esp'))) == {}


def test_a_flamenode_record_without_a_model_is_skipped(export):
    """A STAT that names FlameNode but carries no model adds no socket."""
    _header(str(export / 'Child.esp'), ['Base.esm'])
    with open(export / 'Child.esp' / 'STAT.txt', 'w', encoding='latin1') as fh:
        fh.write('---RECORD_BEGIN---\nEditorID=FlameNodeHolder\n---RECORD_END---\n')
    table = nif_flames.flame_socket_map(_host(str(export / 'Child.esp')))
    assert table == {0: 'firecandleflame.nif'}


def test_a_path_outside_meshes_maps_nothing(export):
    """A source path with no meshes/ folder has no flame map."""
    assert nif_flames.flame_socket_map(str(export / 'Child.esp' / 'a.nif')) == {}


def test_nested_mod_reads_its_plugins_and_their_masters(export):
    """The folder above a nested mod's meshes/ reads its plugin's master's STAT."""
    mod = _nested(export, 'Some Mod', ['A.esp', 'B.esp'], ['A.esp'])
    assert nif_flames.flame_socket_map(_host(str(mod))) == {
        0: 'firecandleflame.nif'}


def test_nested_mod_reads_its_plugins_own_stat(export):
    """A nested plugin's own FlameNode STAT is read from its record dir."""
    mod = _nested(export, 'Some Mod', ['A.esp', 'B.esp'], ['A.esp'])
    _stat(str(mod / 'A.esp'), {0: 'FireOpenLarge.nif'})
    assert nif_flames.flame_socket_map(_host(str(mod))) == {
        0: 'fireopenlarge.nif'}


def test_record_dirs_for_assets_of_a_nested_mod(export):
    """A registered mod folder answers its exported plugins' record dirs only."""
    mod = _nested(export, 'Some Mod', ['A.esp', 'B.esp'], ['A.esp'])
    got = [os.path.normcase(str(p)) for p in record_dirs_for_assets(mod)]
    assert got == [os.path.normcase(str(mod / 'A.esp'))]


def test_record_dirs_for_assets_matches_folders_by_the_platform_case_rule(
        export, monkeypatch):
    """Where the platform folds case, a differently cased mod folder still matches."""
    mod = _nested(export, 'Some Mod', ['A.esp', 'B.esp'], ['A.esp'])
    monkeypatch.setattr(os.path, 'normcase', str.lower)
    got = record_dirs_for_assets(export / 'SOME MOD')
    assert [str(p) for p in got] == [str(mod / 'A.esp')]


def test_record_dirs_for_assets_without_the_registry_module(export, monkeypatch):
    """With no registry module a mod folder answers itself, as asset_root does."""
    import output_layout
    mod = _nested(export, 'Some Mod', ['A.esp', 'B.esp'], ['A.esp'])
    monkeypatch.setattr(output_layout, '_registry', lambda: None)
    assert [str(p) for p in record_dirs_for_assets(mod)] == [str(mod)]


def test_record_dirs_for_assets_of_a_flat_folder(export):
    """A flat plugin's folder answers itself, registry or not."""
    _nested(export, 'Some Mod', ['A.esp', 'B.esp'], ['A.esp'])
    got = record_dirs_for_assets(export / 'Base.esm')
    assert [str(p) for p in got] == [str(export / 'Base.esm')]


def test_record_chain_is_own_then_masters(export):
    """The chain is the own record dir, then the masters, last master first."""
    _header(str(export / 'Patch.esp'), ['Base.esm'])
    _header(str(export / 'Child.esp'), ['Base.esm', 'Patch.esp'])
    chain = record_chain_for_assets(str(export / 'Child.esp'))
    assert chain == [str(export / n) for n in ('Child.esp', 'Patch.esp', 'Base.esm')]


def test_recorded_base_of_an_asset_only_mod(export):
    """An asset-only mod reads the STAT of the base recorded at import."""
    src = export / 'Meshes Only' / '_source'
    src.mkdir(parents=True)
    (src / '.base_plugins').write_text('Base.esm\n', encoding='utf-8')
    table = nif_flames.flame_socket_map(_host(str(export / 'Meshes Only')))
    assert table == {0: 'firecandleflame.nif'}


def test_flame_nif_comes_from_the_master(export, converted):
    """A dependent with no meshes/fire/ converts the master's flame NIF."""
    seen, convert = converted
    _header(str(export / 'Child.esp'), ['Base.esm'])
    master = str(export / 'Base.esm' / 'meshes' / 'fire' / 'firecandleflame.nif')
    got = nif_flames._load_converted_flame(
        _host(str(export / 'Child.esp')), 'firecandleflame.nif', convert)
    assert (got, seen) == (b'flame:' + master.encode('utf-8'), [master])


def test_flame_nif_lookup_prefers_the_own_copy(export):
    """The plugin's own meshes/fire/ copy is used before the master's."""
    _header(str(export / 'Child.esp'), ['Base.esm'])
    own = _fire(str(export / 'Child.esp'), 'firecandleflame.nif')
    asset = str(export / 'Child.esp')
    meshes = os.path.join(asset, 'meshes') + os.sep
    got = nif_flames._flame_source(asset, meshes, 'firecandleflame.nif')
    assert got == own


def test_own_flame_nif_is_found_beside_a_mixed_case_meshes_folder(export):
    """The own copy is looked up under the host's own spelling of meshes/."""
    _header(str(export / 'Child.esp'), ['Base.esm'])
    own = os.path.join(str(export / 'Child.esp'), 'Meshes', 'fire', 'firecandleflame.nif')
    os.makedirs(os.path.dirname(own))
    with open(own, 'wb') as fh:
        fh.write(b'own')
    asset = str(export / 'Child.esp')
    meshes = os.path.join(asset, 'Meshes') + os.sep
    assert nif_flames._flame_source(asset, meshes, 'firecandleflame.nif') == own


def test_own_flame_nif_in_packaged_case_beats_the_masters(export):
    """An own Fire/FireCandleFlame.NIF is found before the master's lower-case copy."""
    _header(str(export / 'Child.esp'), ['Base.esm'])
    own = os.path.join(str(export / 'Child.esp'), 'meshes', 'Fire', 'FireCandleFlame.NIF')
    os.makedirs(os.path.dirname(own))
    with open(own, 'wb') as fh:
        fh.write(b'own')
    asset = str(export / 'Child.esp')
    meshes = os.path.join(asset, 'meshes') + os.sep
    assert os.path.samefile(nif_flames._flame_source(asset, meshes, 'firecandleflame.nif'), own)


def test_masters_flame_nif_in_packaged_case_is_found(tmp_path):
    """A master's Meshes/Fire/FireCandleFlame.NIF is found for a lower-case name."""
    root = tmp_path / 'export'
    _stat(str(root / 'Base.esm'), {0: 'FireCandleFlame.NIF'})
    _header(str(root / 'Base.esm'), [])
    master = os.path.join(str(root / 'Base.esm'), 'Meshes', 'Fire', 'FireCandleFlame.NIF')
    os.makedirs(os.path.dirname(master))
    with open(master, 'wb') as fh:
        fh.write(b'master')
    _header(str(root / 'Child.esp'), ['Base.esm'])
    asset = str(root / 'Child.esp')
    meshes = os.path.join(asset, 'meshes') + os.sep
    assert os.path.samefile(nif_flames._flame_source(asset, meshes, 'firecandleflame.nif'), master)


def test_flame_nif_lookup_falls_back_to_the_master(export):
    """Without an own copy the lookup names the master's meshes/fire/ file."""
    _header(str(export / 'Child.esp'), ['Base.esm'])
    asset = str(export / 'Child.esp')
    meshes = os.path.join(asset, 'meshes') + os.sep
    got = nif_flames._flame_source(asset, meshes, 'firecandleflame.nif')
    assert got == str(export / 'Base.esm' / 'meshes' / 'fire' / 'firecandleflame.nif')


def test_flame_nif_missing_everywhere_is_none(export, converted):
    """A flame no plugin in the chain ships converts to nothing."""
    seen, convert = converted
    _header(str(export / 'Child.esp'), ['Base.esm'])
    got = nif_flames._load_converted_flame(
        _host(str(export / 'Child.esp')), 'fireopenlarge.nif', convert)
    assert (got, seen) == (None, [])


def test_nested_mod_flame_nif_comes_from_the_master(export, converted):
    """A nested mod's host converts the flame NIF its plugin's master ships."""
    seen, convert = converted
    mod = _nested(export, 'Some Mod', ['A.esp', 'B.esp'], ['A.esp'])
    master = str(export / 'Base.esm' / 'meshes' / 'fire' / 'firecandleflame.nif')
    nif_flames._load_converted_flame(_host(str(mod)), 'firecandleflame.nif', convert)
    assert seen == [master]


def test_own_flame_nif_is_converted_before_the_masters(export, converted):
    """A plugin shipping its own meshes/fire/ copy converts that one."""
    seen, convert = converted
    _header(str(export / 'Child.esp'), ['Base.esm'])
    own = _fire(str(export / 'Child.esp'), 'firecandleflame.nif')
    nif_flames._load_converted_flame(
        _host(str(export / 'Child.esp')), 'firecandleflame.nif', convert)
    assert seen == [own]
