"""Same-named plugins from different Data folders convert side by side.

See: docs/commentary/asset_convert_mod_ingest.md#same-named-plugins
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.sources import bsa_extract, source_registry
from convert_cli import build_parser, selected_steps
from output_layout import asset_root, plugin_out_root, record_dir


def _install(tmp_path, name, files):
    """A fake game Data folder holding empty `files`."""
    data = tmp_path / name / 'Data'
    data.mkdir(parents=True)
    for f in files:
        (data / f).write_bytes(b'x')
    return data


def _two_copies(tmp_path):
    """An export root with New Vegas registered BEFORE Fallout 3, both holding Fallout3.esm."""
    export = tmp_path / 'export'
    export.mkdir()
    nv = _install(tmp_path, 'Fallout New Vegas', ['Fallout3.esm', 'FalloutNV.esm'])
    fo3 = _install(tmp_path, 'Fallout 3 goty', ['Fallout3.esm'])
    source_registry.add_directory(export, str(nv))
    source_registry.add_directory(export, str(fo3))
    return export, nv, fo3


def test_real_fallout3_install_finds_its_fallout_archives(tmp_path):
    """Fallout 3 names its base archives after the game, not the plugin."""
    data = _install(tmp_path, 'Fallout 3 goty',
                    ['Fallout3.esm', 'Fallout - Meshes.bsa', 'Fallout - Textures.bsa'])
    names = [b.name for b in bsa_extract.get_bsa_files(data, 'Fallout3.esm')]
    assert names == ['Fallout - Meshes.bsa', 'Fallout - Textures.bsa']


def test_selected_folder_wins_over_registration_order(tmp_path):
    """The selected Data folder decides which copy converts."""
    export, nv, fo3 = _two_copies(tmp_path)
    source_registry.select_directory(str(fo3))
    assert source_registry.directory_for(export, 'Fallout3.esm') == str(fo3)
    source_registry.select_directory(str(nv))
    assert source_registry.directory_for(export, 'Fallout3.esm') == str(nv)


def test_home_wins_when_the_selected_folder_lacks_the_plugin(tmp_path):
    """A master outside the selected folder resolves to its home, not the first registered."""
    export, _nv, fo3 = _two_copies(tmp_path)
    source_registry.select_directory(str(fo3))
    source_registry.claim_home(export, 'Fallout3.esm')
    source_registry.select_directory(str(tmp_path / 'elsewhere'))
    assert source_registry.directory_for(export, 'Fallout3.esm') == str(fo3)


def test_second_copy_gets_its_own_export_and_output_folders(tmp_path):
    """The home keeps the plain folders; the other copy gets `<plugin> (<install>)`."""
    export, nv, fo3 = _two_copies(tmp_path)
    out = tmp_path / 'output'
    source_registry.select_directory(str(fo3))
    source_registry.claim_home(export, 'Fallout3.esm')
    assert record_dir(export, 'Fallout3.esm') == export / 'Fallout3.esm'
    assert plugin_out_root(out, 'Fallout3.esm', export) == out / 'Fallout3.esm'

    source_registry.select_directory(str(nv))
    variant = 'Fallout3.esm (Fallout New Vegas)'
    assert record_dir(export, 'Fallout3.esm') == export / variant
    assert asset_root(export, 'Fallout3.esm') == export / variant
    assert plugin_out_root(out, 'Fallout3.esm', export) == out / variant
    assert Path(variant).stem == 'Fallout3'


def test_a_pinned_home_never_moves(tmp_path):
    """A later run from another folder cannot take over the plain folders."""
    export, nv, fo3 = _two_copies(tmp_path)
    source_registry.select_directory(str(fo3))
    source_registry.claim_home(export, 'Fallout3.esm')
    source_registry.select_directory(str(nv))
    source_registry.claim_home(export, 'Fallout3.esm')
    assert source_registry.home_directory(export, 'Fallout3.esm') == str(fo3)


def test_dots_in_the_install_name_keep_the_plugin_stem(tmp_path):
    """The variant folder's stem stays the plugin's, so its asset namespace does too."""
    export = tmp_path / 'export'
    export.mkdir()
    a = _install(tmp_path, 'Game', ['X.esm'])
    b = _install(tmp_path, 'Game v1.2', ['X.esm'])
    source_registry.select_directory(str(a))
    source_registry.claim_home(export, 'X.esm')
    source_registry.select_directory(str(b))
    folder = source_registry.asset_root_name(export, 'X.esm')
    assert folder == 'X.esm (Game v1 2)'
    assert Path(folder).stem == 'X'


def test_copies_lists_every_folder_home_first(tmp_path):
    """Each copy is listed with the folder it converts into."""
    export, nv, fo3 = _two_copies(tmp_path)
    source_registry.select_directory(str(fo3))
    source_registry.claim_home(export, 'Fallout3.esm')
    assert source_registry.copies(export, 'Fallout3.esm') == [
        (str(fo3), 'Fallout3.esm'),
        (str(nv), 'Fallout3.esm (Fallout New Vegas)')]
    assert source_registry.copies(export, 'FalloutNV.esm') == [(str(nv), 'FalloutNV.esm')]


def test_same_named_archive_from_another_folder_re_extracts(tmp_path, monkeypatch):
    """A cached archive is only reused when it came from the same path."""
    monkeypatch.setattr(bsa_extract, '_open_archive',
                        lambda _path: iter([('meshes\\a.nif', b'x')]))
    first = _install(tmp_path, 'A', ['Fallout - Meshes.bsa']) / 'Fallout - Meshes.bsa'
    second = _install(tmp_path, 'B', ['Fallout - Meshes.bsa']) / 'Fallout - Meshes.bsa'
    out = tmp_path / 'export'
    assert not bsa_extract.extract_bsa(first, out, source_name='P.esm')['skipped_cached']
    assert bsa_extract.extract_bsa(first, out, source_name='P.esm')['skipped_cached']
    assert not bsa_extract.extract_bsa(second, out, source_name='P.esm')['skipped_cached']


def test_mixed_sources_are_named(tmp_path):
    """Archives from another folder, or from an unrecorded one, are reported."""
    base = tmp_path / 'export' / 'Fallout3.esm'
    base.mkdir(parents=True)
    (base / bsa_extract.MANIFEST_NAME).write_text(json.dumps({'extracted_bsas': {
        'Fallout3 - Meshes.bsa': {'size': 1, 'file_count': 1},
        'Fallout - Sound.bsa': {'path': 'C:/NV/Data/Fallout - Sound.bsa', 'size': 1},
        'Fallout - Meshes.bsa': {'path': 'C:/FO3/Data/Fallout - Meshes.bsa', 'size': 1},
    }}), encoding='utf-8')
    got = bsa_extract.extracted_from(base, [Path('C:/FO3/Data/Fallout - Meshes.bsa')])
    assert got == [str(Path('C:/NV/Data')),
                   'Fallout3 - Meshes.bsa (folder not recorded)']


def test_selected_steps():
    """The default run leaves out zip packing; an --*-only flag runs just that step."""
    assert 'pack_zip' not in selected_steps(build_parser().parse_args([]))
    assert selected_steps(build_parser().parse_args(['--meshes-only'])) == ['meshes']
    args = build_parser().parse_args(['--data-dir', 'D:/X/Data', '-f', 'A.esm'])
    assert args.data_dir == 'D:/X/Data'
