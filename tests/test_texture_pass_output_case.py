"""Texture passes write into the lowercase textures folder the packer reads.

Shader paths start with `Textures\\`. Joined onto the output root as spelled,
on a case-sensitive filesystem they opened a second `Textures/` folder beside
`textures/`, which `bsa_pack` does not pack, so the flipbook atlases (and
parallax height maps) reached no archive.

See: docs/commentary/asset_convert_shader.md#flipbook-to-atlas
"""
import os

from asset_convert.nif import flipbook, nif_converter
from asset_convert.texture import parallax

#: What regen prints for the test's map, in this platform's path separator.
MISSING_MAP = 'MISSING SOURCE for ' + os.path.join('tes4', 'stone_p.dds')


def _touch(_src, out):
    """Stand-in for an atlas or height-map writer: an empty file at `out`."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, 'wb').close()


def _dst(tmp_path):
    """A mesh destination inside an output tree rooted at tmp_path."""
    return str(tmp_path / 'meshes' / 'tes4' / 'fire' / 'firetorchlarge.nif')


def test_flip_atlas_lands_in_lowercase_textures(tmp_path, monkeypatch):
    """A `Textures\\` atlas path is written under `textures/`, the only top folder."""
    monkeypatch.setattr(flipbook, 'build_flip_atlas', _touch)
    stats = {'_flipbook_atlases': {'x': {
        'atlas_rel': 'Textures\\tes4\\fire\\firetorchlarge_flip.dds',
        'files': []}}}
    nif_converter._build_flip_atlases(stats, _dst(tmp_path))
    assert (tmp_path / 'textures' / 'tes4' / 'fire' / 'firetorchlarge_flip.dds').is_file()
    assert sorted(os.listdir(tmp_path)) == ['textures']


def test_height_map_lands_in_lowercase_textures(tmp_path, monkeypatch):
    """A `Textures\\` height-map path is written under `textures/`, the only top folder."""
    monkeypatch.setattr(parallax, 'build_height_map', _touch)
    stats = {'_parallax_maps': {'x': {
        'height_rel': 'Textures\\tes4\\dungeons\\stone_p.dds', 'src': ''}}}
    nif_converter._build_height_maps(stats, _dst(tmp_path))
    assert (tmp_path / 'textures' / 'tes4' / 'dungeons' / 'stone_p.dds').is_file()
    assert sorted(os.listdir(tmp_path)) == ['textures']


def test_existing_lowercase_root_is_kept():
    """A path already under `textures\\` keeps its folders as spelled."""
    out = nif_converter._texture_out_path(os.sep.join(['', 'o', '']), 'textures\\a\\b.dds')
    assert out == os.sep.join(['', 'o', 'textures', 'a', 'b.dds'])


def test_only_the_top_folder_is_lowercased():
    """Below the `textures` folder the path keeps its case."""
    out = nif_converter._texture_out_path(os.sep.join(['', 'o', '']), 'TEXTURES\\Tes4\\Fire\\A_flip.dds')
    assert out == os.sep.join(['', 'o', 'textures', 'Tes4', 'Fire', 'A_flip.dds'])


def test_a_path_outside_textures_is_left_alone():
    """A path whose first folder is not `textures` is joined unchanged."""
    out = nif_converter._texture_out_path(os.sep.join(['', 'o', '']), 'Meshes\\a.dds')
    assert out == os.sep.join(['', 'o', 'Meshes', 'a.dds'])


def test_a_forward_slash_path_is_split_too():
    """A shader path written with `/` gets the same lowercase top folder."""
    out = nif_converter._texture_out_path(os.sep.join(['', 'o', '']), 'Textures/tes4/a_flip.dds')
    assert out == os.sep.join(['', 'o', 'textures', 'tes4', 'a_flip.dds'])


def test_the_parallax_audit_finds_a_map_in_lowercase_textures(tmp_path):
    """`parallax_check verify` reads a `Textures\\` slot-3 path from `textures/`."""
    from tools.audit import parallax_check
    _touch('', str(tmp_path / 'textures' / 'tes4' / 'rocks' / 'stone_p.dds'))
    got = parallax_check._height_map_file(tmp_path, 'Textures\\tes4\\rocks\\stone_p.dds')
    assert got == tmp_path / 'textures' / 'tes4' / 'rocks' / 'stone_p.dds'


def test_the_parallax_audit_still_reads_an_older_capitalised_tree(tmp_path):
    """A tree an older build wrote under `Textures/` is still found."""
    from tools.audit import parallax_check
    _touch('', str(tmp_path / 'Textures' / 'tes4' / 'stone_p.dds'))
    got = parallax_check._height_map_file(tmp_path, 'Textures\\tes4\\stone_p.dds')
    assert got == tmp_path / 'Textures' / 'tes4' / 'stone_p.dds'


def test_the_parallax_audit_reports_a_missing_map(tmp_path):
    """A map in no spelling comes back as joined, so the check calls it missing."""
    from tools.audit import parallax_check
    got = parallax_check._height_map_file(tmp_path, 'Textures\\tes4\\stone_p.dds')
    assert parallax_check._check_height_dds(got) == 'missing'


def test_the_parallax_regen_looks_in_lowercase_textures(tmp_path, capsys, monkeypatch):
    """`parallax_check regen` lists the maps under the `textures/` the passes write."""
    from tools.audit import parallax_check
    monkeypatch.setattr(parallax_check, 'Pool', _SerialPool)
    _touch('', str(tmp_path / 'Test.esm' / 'textures' / 'tes4' / 'stone_p.dds'))
    rc = parallax_check.regen('Test.esm', str(tmp_path), 1.0, None, None, None, workers=1)
    assert rc == 2
    assert MISSING_MAP in capsys.readouterr().out


def test_the_parallax_regen_names_the_lowercase_folder_when_none_match(tmp_path, capsys):
    """With no map matching `only`, regen names the `textures/` folder it searched."""
    from tools.audit import parallax_check
    _touch('', str(tmp_path / 'Test.esm' / 'textures' / 'tes4' / 'stone_p.dds'))
    rc = parallax_check.regen('Test.esm', str(tmp_path), 1.0, None, None, 'no-such-map')
    assert rc == 1
    assert capsys.readouterr().out.startswith(
        'no height maps under ' + str(tmp_path / 'Test.esm' / 'textures'))


class _SerialPool:
    """`multiprocessing.Pool` stand-in that maps in this process."""

    def __init__(self, *_args, **_kwargs):
        """Takes the worker count and ignores it."""

    def __enter__(self):
        """The pool itself."""
        return self

    def __exit__(self, *_exc):
        """Nothing to shut down."""
        return False

    def imap_unordered(self, fn, items, _chunksize=1):
        """`fn` over `items`, in order."""
        return map(fn, items)


def test_the_parallax_regen_also_reads_an_older_capitalised_folder(tmp_path, capsys, monkeypatch):
    """With `textures/` and an older `Textures/` both present, regen sees the old map."""
    from tools.audit import parallax_check
    monkeypatch.setattr(parallax_check, 'Pool', _SerialPool)
    _touch('', str(tmp_path / 'Test.esm' / 'textures' / 'tes4' / 'stone.dds'))
    _touch('', str(tmp_path / 'Test.esm' / 'Textures' / 'tes4' / 'stone_p.dds'))
    rc = parallax_check.regen('Test.esm', str(tmp_path), 1.0, None, None, None, workers=1)
    assert rc == 2
    assert MISSING_MAP in capsys.readouterr().out
