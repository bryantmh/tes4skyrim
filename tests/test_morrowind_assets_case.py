"""Morrowind loose files are found in the case the install ships them.

`morrowind_assets` lowercases a path to its archive form, then joined it onto
the Data folder, so a loose `Meshes/R/Guar.NIF` (vanilla ships mixed case) was
missed on a case-sensitive filesystem and a replacer silently lost to the BSA.

See: docs/commentary/asset_convert_paths.md#case-resolver
"""

import pytest

from asset_convert.sources import morrowind_assets as ma


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A Data folder with mixed-case loose files and no archives."""
    folder = tmp_path / 'Data Files'
    for rel in ('Meshes/R/Guar.NIF', 'Fonts/Magic_Cards.fnt'):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(b'x')
    monkeypatch.setattr(ma.source_registry, 'directory_for', lambda *_a: str(folder))
    monkeypatch.setattr(ma, '_archive_index', lambda _d: {})
    return folder


@pytest.mark.usefixtures('case_twins')
def test_loose_vanilla_mesh_in_any_case(data, tmp_path):
    assert ma.find_vanilla_mesh(tmp_path, 'r\\guar.nif') == data / 'Meshes' / 'R' / 'Guar.NIF'


@pytest.mark.usefixtures('case_twins')
def test_loose_file_without_an_archive_in_any_case(data, tmp_path):
    assert ma.find_archived_file(tmp_path, 'fonts\\magic_cards.fnt') == (
        data / 'Fonts' / 'Magic_Cards.fnt')


@pytest.mark.usefixtures('case_twins')
def test_mesh_roots_in_any_case(data, tmp_path):
    """A mod mesh root is searched case-blind before the vanilla install."""
    root = tmp_path / 'mod'
    (root / 'R').mkdir(parents=True)
    (root / 'R' / 'Cliff.NIF').write_bytes(b'x')
    assert ma.resolve_mesh([root], 'R\\Cliff.nif', tmp_path) == root / 'R' / 'Cliff.NIF'
