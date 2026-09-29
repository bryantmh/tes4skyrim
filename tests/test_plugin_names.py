"""Plugin names in any case resolve to the spelling on disk.

See: docs/commentary/asset_convert_paths.md#plugin-names
"""

import argparse

import pytest

import output_layout
import source_paths
from asset_convert.sources.source_registry import canonical_plugin_name
from core import plugin_masters
from script_convert.cross_ref import _export_dirs_with_masters
from tools.release import create_lod


def _export(tmp_path, *plugins):
    """An export root holding one record folder per plugin, masters from `plugins` pairs."""
    root = tmp_path / 'export'
    for plugin, masters in plugins:
        d = root / plugin
        d.mkdir(parents=True)
        (d / '_HEADER.txt').write_text(
            ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)))
    return root


def test_lowercase_master_name_finds_its_record_folder(tmp_path):
    """`oblivion.esm` from a header resolves to the existing `Oblivion.esm/`."""
    root = _export(tmp_path, ('Oblivion.esm', []))
    assert output_layout.master_record_dir(root, 'oblivion.esm') == root / 'Oblivion.esm'
    assert output_layout.paths('oblivion.esm', root, tmp_path / 'out').esm == (
        tmp_path / 'out' / 'Oblivion.esm' / 'Oblivion.esm')


def test_exact_spelling_wins_and_twins_raise(tmp_path):
    """An exact folder is kept; two folders differing only by case are refused."""
    root = _export(tmp_path, ('Knights.esp', []), ('knights.esp', []))
    assert canonical_plugin_name(root, 'knights.esp') == 'knights.esp'
    with pytest.raises(ValueError):
        canonical_plugin_name(root, 'KNIGHTS.ESP')


def test_script_master_walk_follows_a_lowercase_master(tmp_path):
    """The cross-reference scan reads a master named in lowercase."""
    root = _export(tmp_path, ('Oblivion.esm', []), ('Mod.esp', ['oblivion.esm']))
    assert _export_dirs_with_masters(str(root / 'Mod.esp')) == [
        str(root / 'Oblivion.esm'), str(root / 'Mod.esp')]


def test_plugin_binary_is_found_in_any_case(tmp_path):
    """A Data folder's `Oblivion.esm` answers a request for `oblivion.esm`."""
    data = tmp_path / 'Data'
    data.mkdir()
    (data / 'Oblivion.esm').write_bytes(b'TES4')
    got = source_paths.resolve_plugin_path('oblivion.esm', str(data), str(tmp_path / 'export'))
    assert got == str(data / 'Oblivion.esm')


def test_lowercase_header_master_still_sorts_first(monkeypatch):
    """A binary naming `oblivion.esm` loads after `Oblivion.esm`."""
    heads = {'Mod.esp': ['oblivion.esm'], 'Oblivion.esm': []}
    monkeypatch.setattr(plugin_masters, 'get_masters_from_binary', lambda src: heads[src])
    monkeypatch.setattr(plugin_masters.os.path, 'isfile', lambda src: True)
    assert plugin_masters.topological_order(['Mod.esp', 'Oblivion.esm'], lambda n: n) == [
        'Oblivion.esm', 'Mod.esp']


def test_lod_plugin_selection_ignores_case():
    """`--plugins oblivion.esm` selects the converted `Oblivion.esm`."""
    args = argparse.Namespace(plugins=['oblivion.esm', 'Gone.esp'])
    assert create_lod._select_plugins(args, ['Oblivion.esm'], None, None) == ['Oblivion.esm']
