"""Authored asset paths: one normaliser for every texture/mesh/sound string.

See: docs/commentary/asset_convert_shader.md#authored-rel
"""

import pytest

from asset_convert.nif.tex_paths import authored_rel, rewrite_tex_path


#: Real-shaped clean paths; each must come back byte-identical, no repairs.
CLEAN = [
    r'armor\iron\cuirass.dds',
    r'Architecture\Anvil\AnvilHouse01.dds',
    r'LowRes\xullc\Rockbeach05.dds',
    r'tes4\armor\iron\cuirass.dds',
    r'effects\fxwhite.dds',
    r'mod\textures\x.dds',
    r'a..b\c.dds',
    r'weird name with spaces.dds',
    '',
]


@pytest.mark.parametrize('rel', CLEAN)
def test_clean_path_is_unchanged(rel):
    """A clean relative path is returned exactly as written, with no repairs."""
    assert authored_rel(rel) == (rel, ())


@pytest.mark.parametrize('raw, want', [
    (rb'\textures\effects\minigateflame.dds',
     r'Textures\tes4\effects\minigateflame.dds'),
    (rb'f:\gogames\oblivion\data\textures\castlesky\alternatelavax.dds',
     r'Textures\tes4\castlesky\alternatelavax.dds'),
    (b'F:/GOGames/Oblivion/Data/Textures/castlesky/alternatelavax.dds',
     r'Textures\tes4\castlesky\alternatelavax.dds'),
    (rb'e:\modding\my mod 3.0\textures\mymod\clutter\screen01.dds',
     r'Textures\tes4\mymod\clutter\screen01.dds'),
    (rb'\\server\share\Data\Textures\dungeon\wall.dds',
     r'Textures\tes4\dungeon\wall.dds'),
    (rb'..\..\textures\clutter\cup.dds', r'Textures\tes4\clutter\cup.dds'),
    (rb'textures\architecture\castle\kvatch\KvatchDunWall01..dds',
     r'Textures\tes4\architecture\castle\kvatch\KvatchDunWall01.dds'),
])
def test_authoring_slips_are_repaired(raw, want):
    """Drive, rooted, UNC, `..` prefixes and `..ext` all land on the shipped key."""
    assert rewrite_tex_path(raw) == want


def test_inner_textures_folder_of_a_relative_path_is_kept():
    """No authoring prefix: only the leading `textures\\` goes, the inner one stays."""
    assert (rewrite_tex_path(rb'textures\mod\textures\x.dds')
            == r'Textures\tes4\mod\textures\x.dds')


def test_cut_uses_the_last_anchor():
    """An absolute prefix is cut after the LAST anchor folder."""
    rel, fixes = authored_rel(r'c:\textures\old\textures\new\x.dds')
    assert rel == r'new\x.dds'
    assert fixes == ('drive', 'authoring_prefix')


def test_drive_without_anchor_keeps_the_remainder():
    """A drive path naming no anchor folder loses only the drive."""
    assert authored_rel(r'c:\temp\faceconv.dds') == (r'temp\faceconv.dds',
                                                     ('drive',))


def test_repairs_are_named():
    """Every repair is reported by name, so a caller can count it."""
    _rel, fixes = authored_rel(r'\\srv\x\\data\textures\a..dds')
    assert fixes == ('rooted', 'separator', 'authoring_prefix', 'double_dot')


def test_anchor_is_a_parameter():
    """Mesh paths cut at `meshes`, sound paths at `sound`."""
    assert authored_rel(r'd:\oblivion\data\meshes\trees\oak.spt',
                        'meshes')[0] == r'trees\oak.spt'
    assert authored_rel(r'\trees\oak.spt', 'meshes')[0] == r'trees\oak.spt'
    assert authored_rel(r'data\sound\fx\hit.wav', 'sound')[0] == r'fx\hit.wav'
