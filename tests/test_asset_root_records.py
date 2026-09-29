"""Record readers handed an ASSET folder read its plugins' record dirs.

A nested mod keeps its assets in `export/<Mod>/` and each plugin's records one
level deeper, `export/<Mod>/<plugin>/`. Readers that took the folder above
`meshes/` or `sound/` and opened `STAT.txt`, `RACE.txt` or `_HEADER.txt` there
found nothing: the flame map came back empty, the sound stage saw no races and
a mod folder without `--base` named no masters. And a mod's FlameNode STATs
(and the flame NIFs) usually live in its MASTER, so even a flat plugin that
read only its own STAT.txt burned nothing.
See: docs/commentary/tes5_import_mod_merge.md#export-root-resolution
"""

import json
import os

import output_layout
from asset_convert.audio.audio_falloutnv import load_voice_type_edids
from asset_convert.audio.voice_races import load_race_voices
from asset_convert.sources import base_plugins


# ---------------------------------------------------------------------------
#  Fixture: one plain base game, one nested mod, one nested resource master
# ---------------------------------------------------------------------------

MOD = 'My Mod 1.0'
PACK = 'Res Pack'


def _write(p, text):
    """Write `text` to `p`, making its folders; the path."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')
    return p


def _header(d, masters):
    """`d/_HEADER.txt` naming `masters` in load order."""
    _write(d / '_HEADER.txt',
           ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)))


def _records(d, sig, rows):
    """`d/<sig>.txt` holding one record per dict in `rows`."""
    body = ''.join(
        '---RECORD_BEGIN---\nSignature=%s\n%s---RECORD_END---\n'
        % (sig, ''.join(f'{k}={v}\n' for k, v in row.items()))
        for row in rows)
    _write(d / f'{sig}.txt', body)


def _flames():
    """`nif_flames`, imported once PyFFI is patched for this Python."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from asset_convert.nif import nif_flames
    return nif_flames


def _flame(index, nif):
    """One FlameNode<index> STAT row naming Fire\\<nif>."""
    return {'EditorID': f'FlameNode{index}', 'Model.MODL': f'Fire\\\\{nif}'}


def _export(tmp_path):
    """export/ with Base.esm (plain), `MOD` (Mod.esp + Extra.esp nested,
    Extra.esp never exported) and `PACK` (Res.esm + Res2.esp nested).
    Mod.esp's masters are Base.esm then Res.esm: Res.esm is nearest."""
    exp = tmp_path / 'export'

    def group(label, gid, plugins):
        return {n: {'kind': 'archive', 'plugin': n, 'group_id': gid,
                    'group_label': label, 'group_plugins': plugins}
                for n in plugins}

    _write(exp / 'sources.json', json.dumps({'version': 1, 'sources': {
        **group(MOD, 'g-mod', ['Mod.esp', 'Extra.esp']),
        **group(PACK, 'g-pack', ['Res.esm', 'Res2.esp'])}}))
    _header(exp / 'Base.esm', [])
    _header(exp / PACK / 'Res.esm', [])
    rec = exp / MOD / 'Mod.esp'
    _header(rec, ['Base.esm', 'Res.esm'])
    (exp / MOD / 'meshes').mkdir(parents=True)
    return exp, rec


# ---------------------------------------------------------------------------
#  The resolver
# ---------------------------------------------------------------------------

def test_a_mod_folder_answers_its_exported_plugins(tmp_path):
    """The mod folder names Mod.esp's record dir (Extra.esp has none yet);
    a record dir, a flat plugin and an unregistered folder answer themselves."""
    record_dirs_for_assets = output_layout.record_dirs_for_assets
    exp, rec = _export(tmp_path)
    assert record_dirs_for_assets(exp / MOD) == [rec]
    assert record_dirs_for_assets(str(exp / MOD) + os.sep) == [rec]
    assert record_dirs_for_assets(rec) == [rec]
    assert record_dirs_for_assets(exp / 'Base.esm') == [exp / 'Base.esm']
    loose = tmp_path / 'loose' / 'Plug.esp'
    loose.mkdir(parents=True)
    assert record_dirs_for_assets(loose) == [loose]


def test_names_for_a_mod_folder_reads_its_plugins_headers(tmp_path):
    """No `--base` recorded: the header of the plugin inside still counts."""
    exp, _rec = _export(tmp_path)
    assert base_plugins.names_for(exp / MOD) == ['Res.esm', 'Base.esm']
    _write(exp / 'Base.esm' / 'textures' / 'rock.dds', 'DDS')
    assert base_plugins.subdirs(exp / MOD, 'textures') == (
        str(exp / 'Base.esm' / 'textures'),)


def test_record_chain_is_own_then_masters_nearest_first(tmp_path):
    exp, rec = _export(tmp_path)
    assert base_plugins.record_chain_for_assets(exp / MOD) == [
        str(rec), str(exp / PACK / 'Res.esm'), str(exp / 'Base.esm')]


# ---------------------------------------------------------------------------
#  The flame map
# ---------------------------------------------------------------------------

def test_nested_flame_map_reads_a_sibling_masters_stat(tmp_path):
    """The mod folder has no STAT.txt; FlameNode STATs sit in the nested
    master Res.esm and in Base.esm. The nearer master wins a shared socket."""
    nif_flames = _flames()
    exp, _rec = _export(tmp_path)
    _records(exp / PACK / 'Res.esm', 'STAT', [_flame(0, 'FireB.nif')])
    _records(exp / 'Base.esm', 'STAT',
             [_flame(0, 'FireA.nif'), _flame(1, 'FireTorch.nif')])
    src = exp / MOD / 'meshes' / 'lamp.nif'
    assert nif_flames.flame_socket_map(str(src)) == {
        0: 'fireb.nif', 1: 'firetorch.nif'}
    assert nif_flames._flame_nif_for_socket(str(src), 0) == 'fireb.nif'


def test_flat_plugin_flame_stat_only_in_its_master(tmp_path):
    """A flat plugin with no FlameNode STAT of its own takes its master's,
    and its own STAT still wins the socket both define."""
    nif_flames = _flames()
    exp = tmp_path / 'export'
    _write(exp / 'sources.json', json.dumps({'version': 1, 'sources': {}}))
    _header(exp / 'Base.esm', [])
    _records(exp / 'Base.esm', 'STAT',
             [_flame(0, 'FireA.nif'), _flame(2, 'FireOpen.nif')])
    plug = exp / 'Plug.esp'
    _header(plug, ['Base.esm'])
    _records(plug, 'STAT', [_flame(0, 'MyFire.nif'),
                            {'EditorID': 'Rock01', 'Model.MODL': 'r.nif'}])
    src = plug / 'meshes' / 'dungeons' / 'sconce.nif'
    assert nif_flames.flame_socket_map(str(src)) == {
        0: 'myfire.nif', 2: 'fireopen.nif'}


def test_flame_nif_comes_from_the_master_when_the_mod_has_none(tmp_path):
    """The flame NIF ships with the master; the mod's own copy wins."""
    nif_flames = _flames()
    exp, _rec = _export(tmp_path)
    base_fire = _write(exp / 'Base.esm' / 'meshes' / 'Fire' / 'FireA.NIF', 'x')
    assert nif_flames._flame_source(str(exp / MOD), 'firea.nif') == str(base_fire)
    own = _write(exp / MOD / 'meshes' / 'fire' / 'firea.nif', 'y')
    assert nif_flames._flame_source(str(exp / MOD), 'firea.nif') == str(own)
    assert nif_flames._flame_source(str(exp / MOD), 'nothere.nif') is None


# ---------------------------------------------------------------------------
#  The voice readers
# ---------------------------------------------------------------------------

def test_nested_mod_races_are_read_from_its_record_dir(tmp_path):
    """The sound stage passes the mod folder: its plugin's RACE records and
    that plugin's masters' both resolve, the plugin's own winning."""
    exp, rec = _export(tmp_path)
    _records(rec, 'RACE', [{'EditorID': 'WoodFolk', 'FULL': 'Wood Folk'},
                           {'EditorID': 'Imperial', 'FULL': 'Imperial Two'}])
    _records(exp / 'Base.esm', 'RACE',
             [{'EditorID': 'Imperial', 'FULL': 'Imperial'},
              {'EditorID': 'Nord', 'FULL': 'Nord'}])
    rv = load_race_voices(exp / MOD)
    assert rv.folder_key('Wood Folk') == 'WoodFolk'
    assert rv.folder_key('nord') == 'Nord'
    assert rv.by_race_edid['Imperial'] == 'ImperialTwo'
    assert load_race_voices(exp / MOD).keys == load_race_voices(rec).keys


def test_nested_mod_voice_types_are_read_from_its_record_dir(tmp_path):
    """VTYP.txt is read from the plugin's record dir, not the mod folder."""
    exp, rec = _export(tmp_path)
    _write(rec / 'VTYP.txt', 'EditorID=MaleRobot\n')
    assert load_voice_type_edids(exp / MOD) == {'malerobot': 'MaleRobot'}
