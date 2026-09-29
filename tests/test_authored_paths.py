"""Authored asset paths: one normaliser for every texture/mesh/sound string.

See: docs/commentary/asset_convert_shader.md#authored-rel
"""

import pytest
from PIL import Image

from asset_convert.lod import terrain_lod_textures, tree_billboard
from asset_convert.nif import nif_batch, tex_paths
from asset_convert.nif.tex_paths import authored_rel, rewrite_tex_path
from asset_convert.ui import book_inam
from tes5_import.record_types.common import landscape_texture_path, prefix_path


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
    (rb'd:\games\oblivion\data\textures\castlesky\alternatelavax.dds',
     r'Textures\tes4\castlesky\alternatelavax.dds'),
    (b'D:/Games/Oblivion/Data/Textures/castlesky/alternatelavax.dds',
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


# ---------------------------------------------------------------------------
# The record side and the sibling readers share the normaliser
# ---------------------------------------------------------------------------

#: Texture strings for the record/asset parity check, clean and slipped alike.
PARITY = [
    'armor\\iron\\cuirass.dds',
    'textures\\armor\\iron\\cuirass.dds',
    'Data/Textures/dwarven/rock01.dds',
    '\\textures\\effects\\minigateflame.dds',
    'd:\\games\\oblivion\\data\\textures\\castlesky\\alternatelavax.dds',
    'textures\\mod\\textures\\x.dds',
    'textures\\kvatch\\KvatchDunWall01..dds',
]


@pytest.mark.parametrize('raw', PARITY)
def test_record_path_agrees_with_the_asset_copy(raw):
    """prefix_path and rewrite_tex_path name the same file below the namespace."""
    assert ('Textures\\' + prefix_path(raw)
            == rewrite_tex_path(raw.encode()))


@pytest.mark.parametrize('raw, want', [
    ('\\trees\\oak.spt', 'tes4\\trees\\oak.spt'),
    ('d:\\oblivion\\data\\meshes\\clutter\\cup.nif', 'tes4\\clutter\\cup.nif'),
    ('Clutter\\Cup.NIF', 'tes4\\Clutter\\Cup.NIF'),
    ('meshes\\clutter\\cup.nif', 'tes4\\clutter\\cup.nif'),
    ('d:\\oblivion\\data\\sound\\fx\\hit.wav', 'tes4\\fx\\hit.wav'),
    ('fx\\ui\\click.wav', 'tes4\\fx\\ui\\click.wav'),
])
def test_record_path_anchor_follows_the_extension(raw, want):
    """A model cuts at `meshes`, a sound at `sound`, a clean path is untouched."""
    assert prefix_path(raw) == want


@pytest.mark.parametrize('icon, want', [
    ('TerrainHDRock01.dds', 'tes4\\landscape\\TerrainHDRock01.dds'),
    ('textures\\tx_ash_01.dds', 'tes4\\tx_ash_01.dds'),
    ('Landscape\\Road.dds', 'tes4\\Landscape\\Road.dds'),
    ('f:\\game\\data\\textures\\landscape\\grass.dds',
     'tes4\\landscape\\grass.dds'),
])
def test_landscape_icon_uses_the_normaliser(icon, want):
    """A bare ICON gains `landscape\\`; one naming its folder does not."""
    assert landscape_texture_path(icon) == want


def _png(path):
    """A 2x2 image at `path` (PIL reads it whatever the extension says)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (2, 2), (10, 20, 30)).save(path, format='PNG')


def test_billboard_texture_loads_through_a_drive_path(tmp_path):
    """tree_billboard finds a diffuse authored with the author's drive path."""
    _png(tmp_path / 'trees' / 'bark.dds')
    got = tree_billboard._load_texture('f:\\game\\data\\textures\\trees\\bark.dds',
                                       [tmp_path])
    assert got is not None


def test_book_texture_resolves_through_a_drive_path(tmp_path):
    """book_inam finds a cover authored with the author's drive path."""
    _png(tmp_path / 'textures' / 'clutter' / 'books' / 'cover.dds')
    got = book_inam._find_source_texture(
        [tmp_path], 'e:\\my mod\\textures\\clutter\\books\\cover.dds')
    assert got is not None


def test_terrain_texture_resolves_through_a_drive_path(tmp_path):
    """terrain LOD finds a landscape texture authored with a drive path."""
    _png(tmp_path / 'landscape' / 'grass.dds')
    _img, outcome, key = terrain_lod_textures._load_uncached(
        'f:\\game\\data\\textures\\landscape\\grass.dds', [tmp_path], 4)
    assert (outcome, key) == ('exact', 'landscape\\grass.dds')


# ---------------------------------------------------------------------------
# Repairs are counted and reported by the mesh batch
# ---------------------------------------------------------------------------


def test_batch_reports_the_repairs_its_workers_made(monkeypatch, tmp_path,
                                                    capsys):
    """A worker's repairs reach the run totals and the end-of-run report."""
    def fake_convert(*_a, **_k):
        """Rewrite one drive path and one clean path, as a mesh would."""
        rewrite_tex_path(rb'f:\game\data\textures\a.dds')
        rewrite_tex_path(rb'textures\b.dds')
        return {'converted': True, 'strips_fixed': False,
                'properties_converted': False, 'root_converted': False,
                'root_rotation_baked': False}

    monkeypatch.setattr(nif_batch, 'convert_nif', fake_convert)
    tex_paths.snapshot_repairs()
    nif = str(tmp_path / 'm.nif')
    status, _path, r = nif_batch._batch_worker(
        (nif, nif, True, None, str(tmp_path), None, False, False, ()))
    assert status == 'ok'
    assert r['tex_repairs'] == {'drive': 1, 'authoring_prefix': 1}
    stats = nif_batch._empty_batch_stats(1)
    nif_batch._merge_result(stats, [], tmp_path, nif, r)
    nif_batch._merge_result(stats, [], tmp_path, nif, r)
    nif_batch._report_warnings(stats)
    assert ('Authored texture paths repaired: authoring_prefix 2, drive 2'
            in capsys.readouterr().out)
