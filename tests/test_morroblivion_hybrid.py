"""Hybrid source mode: whitelist reading, world swaps, the assembler guard, the one texture map, land twins, and the companion plugin."""

import json
from types import SimpleNamespace

from asset_convert.character.morrowind_armor import _assembled
from asset_convert.nif import morroblivion_textures as mbt
from asset_convert.nif.grass_profile import load_grass_model_paths
from asset_convert.nif.tex_paths import full_res_twin
from asset_convert.sources import source_registry
from tes4_export import morroblivion_companion as companion
from tes4_export.morroblivion_ground import MorroblivionGround, match_ground, write_pairs
from tes4_export.morroblivion_weather import copy_weathers
from asset_convert import game_paths
from asset_convert.sources import hybrid_assets
from tes4_export import morroblivion_hybrid as hybrid_mod
from tes4_export.morroblivion_hybrid import (MANIFEST_NAME, _swap_world, load_whitelist, remove_copies, swap_ground,
                                             with_maps)


def _rows(tmp_path, rows):
    """A whitelist file holding `rows`."""
    path = tmp_path / 'wl.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    return path


def test_whitelist_keeps_denials_and_only_approved_worn(tmp_path):
    """A denied world pair maps to '' so furniture approval cannot override it; denied worn models are absent."""
    world, worn = load_whitelist(_rows(tmp_path, [
        {'kind': 'world', 'vanilla': 'f\\A.nif', 'morroblivion': 'morro/f/a.nif', 'decision': 'approve'},
        {'kind': 'world', 'vanilla': 'f\\B.nif', 'morroblivion': 'morro/f/b.nif', 'decision': 'deny'},
        {'kind': 'worn', 'vanilla': 'x', 'morroblivion': 'Armor\\Y\\m.nif', 'decision': 'approve'},
        {'kind': 'worn', 'vanilla': 'z', 'morroblivion': 'Armor\\Z\\m.nif', 'decision': 'deny'}]))
    assert world == {'f\\a.nif': 'morro\\f\\a.nif', 'f\\b.nif': ''}
    assert worn == {'armor\\y\\m.nif'}


def test_world_swap_rewrites_model_and_records_it():
    """An approved model replaces the line; an unapproved one is left alone."""
    hybrid = SimpleNamespace(named=set(), world_model=lambda p: 'morro\\f\\a.nif' if p == 'f\\a.nif' else '')
    ctx = SimpleNamespace(origin_shifts=None)
    lines = ['EditorID=a', 'Model.MODL=f\\\\a.nif']
    assert _swap_world(lines, hybrid, ctx) == 1
    assert lines[1] == 'Model.MODL=morro\\\\f\\\\a.nif' and hybrid.named == {'morro\\f\\a.nif'}
    other = ['Model.MODL=f\\\\b.nif']
    assert _swap_world(other, hybrid, ctx) == 0 and other == ['Model.MODL=f\\\\b.nif']


def test_assembler_writes_only_its_own_folder():
    """A swapped-in Morroblivion worn model is never overwritten by the vanilla assembly."""
    assert _assembled('armor\\\\morrowind\\\\m\\\\0x.nif') and _assembled('armor\\morrowind\\f\\0x.nif')
    assert not _assembled('VvardenfellArmormod\\netch\\helmet.nif') and not _assembled('')


def _export(folder, header, files):
    """Write a minimal export folder: _HEADER.txt plus {signature: [record dict]}."""
    folder.mkdir(parents=True)
    (folder / '_HEADER.txt').write_text(header, encoding='utf-8')
    for sig, recs in files.items():
        text = ''.join('---RECORD_BEGIN---\n' + ''.join(f'{k}={v}\n' for k, v in r.items())
                       + '---RECORD_END---\n\n' for r in recs)
        (folder / f'{sig}.txt').write_text(text, encoding='utf-8')


def test_companion_lives_in_its_owners_folders(tmp_path):
    """The companion's records nest in its owner's export folder, which also names its output folder."""
    _export(tmp_path / 'Morrowind.esm', 'Source=TES3\n', {})
    name = source_registry.companion_name('Morrowind.esm')
    assert name == 'Morrowind - Morroblivion Hybrid.esp'
    assert source_registry.companion_owner(str(tmp_path), name) == 'Morrowind.esm'
    assert source_registry.record_dir(str(tmp_path), name) == tmp_path / 'Morrowind.esm' / name
    assert source_registry.asset_root_name(str(tmp_path), name) == 'Morrowind.esm'
    assert source_registry.companion_owner(str(tmp_path), 'Morrowind.esm') == ''
    assert source_registry.is_companion(name) and not source_registry.is_companion('Morrowind.esm')


def test_companion_reuses_master_furniture_and_restates_edits(tmp_path):
    """A master's base keeps its companion's FURN, re-keyed; an own base gets a new one; edits of overridden refs stay."""
    mw, comp, bm = tmp_path / 'mw', tmp_path / 'mwc', tmp_path / 'bm'
    _export(mw, 'Source=TES3\n', {'STAT': [{'FormID': '00000001', 'EditorID': 'bed', 'Model.MODL': 'f/bed.nif'}]})
    _export(comp, 'Master[0]=Morrowind.esm\n', {
        'FURN': [{'FormID': '01ABCDEF', 'EditorID': 'bed'}],
        'REFR': [{'FormID': '00000010', 'NAME': '01ABCDEF', 'RecordFlags': '0'}]})
    _export(bm, 'Master[0]=Morrowind.esm\nMaster[1]=Tribunal.esm\n', {
        'STAT': [{'FormID': '02000002', 'EditorID': 'stool', 'Model.MODL': 'f/stool.nif'}],
        'REFR': [{'FormID': '00000010', 'NAME': '00000001', 'RecordFlags': '32'},
                 {'FormID': '02000020', 'NAME': '00000001', 'RecordFlags': '0'},
                 {'FormID': '02000021', 'NAME': '02000002', 'RecordFlags': '0'},
                 {'FormID': '02000022', 'NAME': '02000002', 'RecordFlags': '32'}]})
    (bm / 'meshes' / 'f').mkdir(parents=True)
    (bm / 'meshes' / 'f' / 'stool.nif').write_bytes(b'..BSFurnitureMarker..')
    layout = {'names': ['Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm', 'Morrowind - Morroblivion Hybrid.esp'],
              'masters': [('Morrowind.esm', str(mw))], 'own': ('Bloodmoon.esm', str(bm)),
              'companions': [('Morrowind - Morroblivion Hybrid.esp', str(comp))], 'borrowed': []}
    bases, furn = companion.furniture(layout, {'f\\stool.nif': '5'})
    assert bases['00000001'] == '03ABCDEF' and bases['02000002'].startswith('04')
    assert [f for f, _l in furn] == [bases['02000002']] and 'MNAM.Flags=5' in furn[0][1]
    refs = dict(companion.placements(layout, bases))
    assert sorted(refs) == ['00000010', '02000020', '02000021']
    assert 'RecordFlags=32' in refs['00000010'] and 'NAME=03ABCDEF' in refs['02000020']


def _morroblivion_ground(tmp_path):
    """Morroblivion: one texture as a bare and a grassy record, a NoGrass texture, and land painting both."""
    _export(tmp_path / 'Morrowind_ob.esm', 'Master[0]=Oblivion.esm\n', {
        'LTEX': [{'FormID': '01000001', 'EditorID': 'bare', 'ICON': 'morro/TxUsandU01.dds'},
                 {'FormID': '01000002', 'EditorID': 'grassy', 'ICON': 'morro/TxUsandU01.dds',
                  'GrassCount': '1', 'Grass[0]': '01000009'},
                 {'FormID': '01000003', 'EditorID': 'mwNoGrass', 'ICON': 'morro/TxUdirt.dds'}],
        'GRAS': [{'FormID': '01000009', 'EditorID': 'mwG1', 'Model.MODL': 'Morroblivion/Grass/G1.nif'}],
        'WRLD': [{'FormID': '01000100', 'EditorID': 'WrldMorrowind'}],
        'CELL': [{'FormID': '01000200', 'XCLC.X': '0', 'XCLC.Y': '0'}],
        'LAND': [{'FormID': '01000300', 'ParentWRLD': '01000100', 'ParentCELL': '01000200', 'LayerCount': '2',
                  'Layer[0].Type': 'BASE', 'Layer[0].BTXT.Texture': '01000002', 'Layer[0].BTXT.Quadrant': '0',
                  'Layer[1].Type': 'ALPHA', 'Layer[1].ATXT.Texture': '01000003', 'Layer[1].ATXT.Quadrant': '0',
                  'Layer[1].ATXT.Layer': '0', 'Layer[1].VTXTCount': '1', 'Layer[1].VT[0].Pos': '0',
                  'Layer[1].VT[0].Opacity': '1.0'}]})
    return MorroblivionGround(str(tmp_path), ['Morrowind_ob.esm'])


def _sand_ctx(tmp_path):
    """Our Sand texture, and an export context minting one id."""
    out = {'LTEX': [('00000005', ['EditorID=Sand', 'ICON=textures\\\\Tx_sand_01.dds'])]}
    return out, SimpleNamespace(export_root=str(tmp_path), derive=lambda key: '00ABCDEF', master_names=[])


def test_vanilla_land_takes_morroblivions_paint_and_its_pairs_grass(tmp_path):
    """Sand pairs with the record painted on its vertices; the land takes Morroblivion's layers; grass follows the pair."""
    ground = _morroblivion_ground(tmp_path)
    out, ctx = _sand_ctx(tmp_path)
    out['CELL'] = [('00000300', ['XCLC.X=0', 'XCLC.Y=0'])]
    out['LAND'] = [('00000400', ['DATA.Flags=29', 'LayerCount=1', 'Layer[0].Type=BASE',
                                 'Layer[0].BTXT.Texture=00000005', 'Layer[0].BTXT.Quadrant=0', 'ParentCELL=00000300'])]
    pairs = match_ground(out, ctx, ground, True)
    assert pairs == {'00000005': 'Morrowind_ob.esm|01000002', '00ABCDEF': 'Morrowind_ob.esm|01000003'}
    land = out['LAND'][0][1]
    assert 'Layer[0].BTXT.Texture=00000005' in land and 'Layer[1].ATXT.Texture=00ABCDEF' in land
    assert land[-1] == 'ParentCELL=00000300' and 'EditorID=mwNoGrass' in out['LTEX'][1][1]
    hybrid = SimpleNamespace(vanilla=True, named=set())
    assert swap_ground(out, hybrid, ground, pairs)[0] == 2
    assert out['LTEX'][0][1][1] == 'ICON=morro/TxUsandU01.dds' and hybrid.named == {'morroblivion\\grass\\g1.nif'}
    own = tmp_path / 'Morrowind.esm'
    _export(own, 'Source=TES3\n', {'LTEX': [{'FormID': '00000005', 'EditorID': 'Sand', 'ICON': 'morro/TxUsandU01.dds'},
                                            {'FormID': '00ABCDEF', 'EditorID': 'mwNoGrass', 'ICON': 'morro/TxUdirt.dds'}]})
    write_pairs(own, out, pairs)
    layout = {'names': ['Morrowind.esm'], 'own': ('Morrowind.esm', str(own)), 'companions': []}
    grasses, lands = companion.grass(layout, ground)
    assert len(grasses) == 1 and grasses[0][0].startswith('01') and 'EditorID=mwG1' in grasses[0][1]
    assert lands == [('00000005', lands[0][1])] and lands[0][1][-2:] == ['GrassCount=1', f'Grass[0]={grasses[0][0]}']


def test_an_unpainted_texture_on_a_doubled_stem_has_no_pair(tmp_path):
    """With no painted land and two Morroblivion records on its texture, nothing authored picks one."""
    ground = _morroblivion_ground(tmp_path)
    out, ctx = _sand_ctx(tmp_path)
    assert match_ground(out, ctx, ground, False) == {}


def test_hybrid_plugin_reads_the_one_map_less_its_kept_textures(tmp_path, monkeypatch):
    """A hybrid export needs no patch: it uses the one map, minus the keys it keeps."""
    own = tmp_path / 'Arktwend.esm'
    _export(own, 'Source=TES3\n', {})
    mbt.write_map(mbt.map_dir(tmp_path), {'tx_a': 'textures\\morro\\txua.dds', 'tx_b': 'textures\\morro\\txub.dds'})
    (own / mbt.HYBRID_MANIFEST).write_text('', encoding='utf-8')
    (own / mbt.KEEP_NAME).write_text('tx_b\n', encoding='utf-8')
    monkeypatch.delenv(mbt.MAP_ENV, raising=False)
    monkeypatch.delenv(mbt.KEEP_ENV, raising=False)
    assert mbt.activate(own) == mbt.map_dir(tmp_path)
    assert mbt.substitute(b'textures\\tx_a.dds') and mbt.substitute(b'textures\\tx_b.dds') is None
    (own / mbt.HYBRID_MANIFEST).unlink()
    assert mbt.map_folder(own) == ''




def test_a_twin_ships_with_its_normal_and_glow_maps():
    """A swapped diffuse without its _n map renders flat: every twin carries both maps."""
    assert with_maps('morro/x/txua.dds') == {'morro/x/txua.dds', 'morro/x/txua_n.dds', 'morro/x/txua_g.dds'}


def test_the_owners_grass_pass_places_its_companions_grass(tmp_path):
    """The companion's grass meshes ship in its owner's tree, so the owner's grass pass must place them."""
    _export(tmp_path / 'Morrowind.esm', 'Source=TES3\n', {})
    _export(tmp_path / 'Morrowind.esm' / 'Morrowind - Morroblivion Hybrid.esp', 'Master[0]=Morrowind.esm\n',
            {'GRAS': [{'FormID': '01000009', 'Model.MODL': 'Morroblivion/Grass/G1.nif'}]})
    assert load_grass_model_paths(str(tmp_path / 'Morrowind.esm')) == {'morroblivion/grass/g1.nif'}


def test_an_older_builds_copies_go_only_when_they_match_their_source(tmp_path):
    """An old copy list names files a build copied into the export: identical ones and split halves go, the rest stay.

    See: docs/commentary/tes4_export_morrowind.md#hybrid-shared-assets
    """
    folder, source = tmp_path / 'P.esp', tmp_path / 'Morrowind_ob.esm'
    for rel, own, src in (('meshes/a.nif', b'n', b'n'), ('textures/t.dds', b'mine', b'theirs'),
                          ('meshes/armor/split/g_left.nif', b'h', None)):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(own)
        if src is not None:
            (source / rel).parent.mkdir(parents=True, exist_ok=True)
            (source / rel).write_bytes(src)
    (folder / MANIFEST_NAME).write_text('meshes/a.nif\ntextures/t.dds\nmeshes/armor/split/g_left.nif\n',
                                        encoding='utf-8')
    assert remove_copies(str(tmp_path), 'P.esp', [source]) == (2, 1)
    assert (folder / 'textures' / 't.dds').read_bytes() == b'mine'
    assert not (folder / 'meshes' / 'a.nif').exists() and not (folder / MANIFEST_NAME).exists()


def test_a_borrowed_path_ships_under_the_shared_namespace(tmp_path, monkeypatch):
    """Records and meshes name a borrowed file in the shared mod; the plugin's own files keep its namespace."""
    own = tmp_path / 'Morrowind.esm'
    _export(own, 'Source=TES3\n', {})
    hybrid_assets.write_borrowed(own, {'meshes/morro/chair.nif': 'Morrowind_ob.esm',
                                       'textures/morro/txuchair.dds': 'Morrowind_ob.esm'}, [])
    monkeypatch.setenv(game_paths.BORROWED_ENV, '')
    game_paths.set_namespace('morrowind')
    game_paths.set_borrowed(own)
    try:
        assert game_paths.asset_namespace('Morro\\Chair.NIF') == hybrid_assets.NAMESPACE
        assert game_paths.asset_namespace('morro\\TxUChair.tga') == hybrid_assets.NAMESPACE
        assert game_paths.asset_namespace('f\\chair.nif') == 'morrowind'
        cache = game_paths.alias_borrowed({'morroblivion/morro/chair.nif': 1, 'morrowind/f/x.nif': 2,
                                           'morrowind/morro/chair.nif': 3})
        assert cache['morroblivion/morro/chair.nif'] == 1 and cache['morrowind/morro/chair.nif'] == 3
    finally:
        game_paths.set_borrowed('')
        game_paths.set_namespace(game_paths.DEFAULT_NAMESPACE)


def test_a_split_items_textures_are_borrowed_with_its_halves(tmp_path, monkeypatch):
    """A split half has no owner, so the textures of the model it is split from are borrowed for it."""
    owner = tmp_path / 'Morrowind_ob.esm'
    for rel in ('meshes/armor/g.nif', 'textures/armor/g.dds'):
        (owner / rel).parent.mkdir(parents=True, exist_ok=True)
        (owner / rel).write_bytes(b'x')
    _export(tmp_path / 'P.esm', 'Source=TES3\n', {})
    monkeypatch.setattr(hybrid_mod, '_nif_textures', lambda path: {'armor\\g.dds'})

    def owner_of(part, rel):
        """The owner's file at `part`/`rel`, if any."""
        found = hybrid_mod.find_under([owner / part], rel)
        return ('Morrowind_ob.esm', found) if found else None
    hybrid = SimpleNamespace(named={'armor\\split\\g_left_0.nif'}, halves=lambda: {'armor\\split\\g_left_0.nif'},
                             owner_of=owner_of, dest=tmp_path / 'P.esm', export_root=str(tmp_path), plugin='P.esm',
                             split_items=[{'models': {'Male.BipedModel.MODL': 'armor\\g.nif'}, 'owners': []}],
                             models=SimpleNamespace(owners={}))
    assert hybrid_mod.borrow_assets(hybrid, []) == (1, 1)
    assert hybrid_assets.read_borrowed(tmp_path / 'P.esm')['textures/armor/g.dds'] == 'Morrowind_ob.esm'


def test_hand_made_lods_are_borrowed_for_swapped_and_kept_meshes(tmp_path, monkeypatch):
    """A swapped mesh's LOD is borrowed beside it; a kept vanilla mesh takes its pair's LOD under its own name.

    See: docs/commentary/tes4_export_morrowind.md#hybrid-lod-meshes
    """
    owner = tmp_path / 'Morrowind_ob.esm'
    for rel in ('meshes/morro/ruin.nif', 'meshes/morro/ruin_far.nif', 'meshes/morro/canton_far.nif',
                'meshes/morro/rock_far.nif', 'textures/morro/stone.dds'):
        (owner / rel).parent.mkdir(parents=True, exist_ok=True)
        (owner / rel).write_bytes(b'x')
    plugin = tmp_path / 'Morrowind.esm'
    _export(plugin, 'Source=TES3\n', {})
    monkeypatch.setattr(hybrid_mod, '_nif_textures', lambda path: hybrid_mod.with_maps('morro\\stone.dds'))

    def owner_of(part, rel):
        """The owner's file at `part`/`rel`, if any."""
        found = hybrid_mod.find_under([owner / part], rel)
        return ('Morrowind_ob.esm', found) if found else None
    pairs = {'x\\ruin.nif': 'morro\\ruin.nif', 'x\\canton.nif': 'morro\\canton.nif', 'x\\rock.nif': 'morro\\rock.nif'}
    models = SimpleNamespace(owners=dict.fromkeys(pairs, ['id']), owns=lambda rel: rel != 'x\\rock.nif',
                             replacement=lambda rel, index: pairs[rel])
    hybrid = SimpleNamespace(named={'morro\\ruin.nif'}, halves=set, owner_of=owner_of, dest=plugin,
                             export_root=str(tmp_path), plugin='Morrowind.esm', split_items=[], models=models,
                             index=None, vanilla=True, find=lambda rel: hybrid_mod.find_under([owner / 'meshes'], rel),
                             world_model=lambda rel: 'morro\\ruin.nif' if rel == 'x\\ruin.nif' else '')
    assert hybrid_mod.borrow_assets(hybrid, []) == (3, 1)
    rows = hybrid_assets.read_borrowed(plugin)
    assert {'meshes/morro/ruin.nif', 'meshes/morro/ruin_far.nif', 'textures/morro/stone.dds'} == set(rows)
    assert hybrid_assets.read_lods(plugin) == {'x\\canton_far.nif': ('Morrowind_ob.esm', 'morro\\canton_far.nif')}


def test_a_lowres_texture_falls_back_to_its_borrowed_twin(tmp_path, monkeypatch):
    """A `_far` mesh's missing `lowres` texture resolves to its full-res twin in the shared mod when borrowed.

    See: docs/commentary/asset_convert_shader.md#lowres-textures
    """
    own = tmp_path / 'Morrowind.esm'
    _export(own, 'Source=TES3\n', {})
    hybrid_assets.write_borrowed(own, {'textures/morro/stone.dds': 'Morrowind_ob.esm'}, [])
    monkeypatch.setenv(game_paths.BORROWED_ENV, '')
    game_paths.set_namespace('morrowind')
    game_paths.set_borrowed(own)
    try:
        assert full_res_twin('Textures\\morrowind\\lowres\\morro\\stone.dds') == 'Textures\\morroblivion\\morro\\stone.dds'
        assert full_res_twin('Textures\\morrowind\\lowres\\x\\own.dds') == 'Textures\\morrowind\\x\\own.dds'
    finally:
        game_paths.set_borrowed('')
        game_paths.set_namespace(game_paths.DEFAULT_NAMESPACE)


def test_a_hybrid_root_copies_the_weathers_with_morrowinds_own_sounds(tmp_path):
    """The sky is Morroblivion's, the sounds the ones Morrowind.ini names, and regions and world point at the copies.

    See: docs/commentary/tes4_export_morrowind.md#hybrid-weathers
    """
    _export(tmp_path / 'Morrowind_ob.esm', 'Master[0]=Oblivion.esm\n', {
        'WTHR': [{'FormID': '01000001', 'EditorID': 'mwClear', 'DNAM.UpperCloudLayer': 'Sky/Clear.dds'},
                 {'FormID': '01000002', 'EditorID': 'mwRain', 'SoundCount': '1', 'Sound[0].FormID': '00086579',
                  'Sound[0].Type': '1'}],
        'CLMT': [{'FormID': '01000003', 'EditorID': 'mwMorrowindClimate', 'WeatherCount': '2',
                  'Weather[0].FormID': '01000001', 'Weather[0].Chance': '60',
                  'Weather[1].FormID': '01000002', 'Weather[1].Chance': '40'}]})
    data = tmp_path / 'Game' / 'Data Files'
    data.mkdir(parents=True)
    (tmp_path / 'Game' / 'Morrowind.ini').write_text('[Weather Rain]\nRain Loop Sound ID=Rain\n'
                                                     'Ambient Loop Sound ID=None\n', encoding='cp1252')
    out = {'SOUN': [('00000A01', ['EditorID=Rain'])],
           'REGN': [('00000B01', ['RegionData[0].Weather[0].MorrowindType=4'])],
           'WRLD': [('00000C01', ['CNAM.Vanilla=00000812'])]}
    ctx = SimpleNamespace(master_names=[], export_root=str(tmp_path),
                          index=SimpleNamespace(lookup_editor_id=lambda _e: None),
                          derive=lambda key: {'morroblivionweather:mwclear': '00000D01',
                                              'morroblivionweather:mwrain': '00000D02'}.get(key.lower(), '00000D03'))
    skies = copy_weathers(out, ctx, str(data / 'Morrowind.esm'), ['Morrowind_ob.esm'])
    rain = dict(l.split('=', 1) for l in dict(out['WTHR'])['00000D02'])
    assert rain['SoundCount'] == '1' and rain['Sound[0].FormID'] == '00000A01' and rain['Sound[0].Type'] == '1'
    climate = out['CLMT'][0][1]
    assert 'Weather[0].FormID=00000D01' in climate and 'Weather[1].FormID=00000D02' in climate
    assert out['REGN'][0][1] == ['RegionData[0].Weather[0].FormID=00000D02']
    assert out['WRLD'][0][1] == ['CNAM.Climate=00000D03']
    assert skies == ['Sky/Clear.dds']
