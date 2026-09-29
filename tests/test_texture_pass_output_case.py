"""Texture passes write into the lowercase textures folder the packer reads.

Shader paths start with `Textures\\`. Joined onto the output root as-is, on a
case-sensitive filesystem they opened a second `Textures/` folder next to
`textures/`, and the 20 Oblivion flipbook atlases never reached the BSA while
271 mesh references pointed at them.
"""
import os

from asset_convert.nif import flipbook, nif_converter
from asset_convert.texture import parallax


def _touch(_src, out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, 'wb').close()


def _dst(tmp_path):
    return str(tmp_path / 'meshes' / 'tes4' / 'fire' / 'firetorchlarge.nif')


def test_flip_atlas_lands_in_lowercase_textures(tmp_path, monkeypatch):
    monkeypatch.setattr(flipbook, 'build_flip_atlas', _touch)
    stats = {'_flipbook_atlases': {'x': {
        'atlas_rel': 'Textures\\tes4\\fire\\firetorchlarge_flip.dds',
        'files': []}}}
    nif_converter._build_flip_atlases(stats, _dst(tmp_path))
    assert (tmp_path / 'textures' / 'tes4' / 'fire' / 'firetorchlarge_flip.dds').is_file()
    assert sorted(os.listdir(tmp_path)) == ['textures']


def test_height_map_lands_in_lowercase_textures(tmp_path, monkeypatch):
    monkeypatch.setattr(parallax, 'build_height_map', _touch)
    stats = {'_parallax_maps': {'x': {
        'height_rel': 'Textures\\tes4\\dungeons\\stone_p.dds', 'src': ''}}}
    nif_converter._build_height_maps(stats, _dst(tmp_path))
    assert (tmp_path / 'textures' / 'tes4' / 'dungeons' / 'stone_p.dds').is_file()
    assert sorted(os.listdir(tmp_path)) == ['textures']


def test_existing_lowercase_root_is_kept():
    out = nif_converter._texture_out_path(os.sep.join(['', 'o', '']), 'textures\\a\\b.dds')
    assert out == os.sep.join(['', 'o', 'textures', 'a', 'b.dds'])
