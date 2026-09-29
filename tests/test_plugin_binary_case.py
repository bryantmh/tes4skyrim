"""A plugin found case-blind in a Data folder is opened by its spelling on disk.

`source_registry.directory_for` matches the plugin name in any case, so a
caller that then joined the ASKED spelling onto the folder built a path that
does not exist on a case-sensitive filesystem: `source_binary` answered a
missing file where it used to answer None. Those checks need a case-sensitive
tmp filesystem, so they take the `case_twins` guard.

See: docs/commentary/asset_convert_paths.md#plugin-names
"""

import os

import pytest

from asset_convert.character import morrowind_body
from asset_convert.sources import source_registry
from tes4_export.morrowind_patch import source_paths
from tes5_import.dialogue.morrowind_sidecar_source import source_binary


@pytest.fixture
def data(tmp_path):
    """A registered Data folder holding `Morrowind.esm` (capitalised on disk)."""
    folder = tmp_path / 'Data'
    folder.mkdir()
    (folder / 'Morrowind.esm').write_bytes(b'TES3' + bytes(20))
    export = tmp_path / 'export'
    export.mkdir()
    source_registry.add_directory(str(export), str(folder))
    os.environ.pop(source_registry.SELECTED_DIR_ENV, None)
    return export, folder


@pytest.mark.usefixtures('case_twins')
@pytest.mark.parametrize('asked', ['Morrowind.esm', 'morrowind.esm'])
def test_source_binary_names_the_file_on_disk(data, asked):
    """Either spelling answers the one existing file."""
    export, folder = data

    got = source_binary(str(export), asked)

    assert got == str(folder / 'Morrowind.esm') and os.path.isfile(got)


def test_source_binary_is_none_when_no_folder_holds_it(data):
    """An absent plugin is still None, never a made-up path."""
    assert source_binary(str(data[0]), 'Tribunal.esm') is None


@pytest.mark.usefixtures('case_twins')
def test_patch_source_paths_ignore_case(data):
    """The patch's source check finds `morrowind.esm` as the capitalised file."""
    found, missing = source_paths(str(data[1]), ['morrowind.esm', 'Absent.esm'])

    assert found == [str(data[1] / 'Morrowind.esm')] and missing == ['Absent.esm']


@pytest.mark.usefixtures('case_twins')
def test_body_master_records_open_the_file_on_disk(data, monkeypatch):
    """The body builder reads Morrowind.esm through the case-blind path."""
    folder = data[1]
    (folder / 'Morrowind.esm').rename(folder / 'morrowind.esm')
    monkeypatch.setattr(morrowind_body.source_registry, 'directory_for',
                        lambda _e, _p: str(folder))
    opened = []
    monkeypatch.setattr(morrowind_body, 'read_file',
                        lambda p: opened.append(p) or (None, ['rec']))

    assert morrowind_body._master_records() == ['rec']
    assert opened == [str(folder / 'morrowind.esm')]
