"""A nested mod's bases resolve from the EXPORT ROOT, not from its parent.

An imported mod that ships several plugins nests their records as
`export/<Mod>/<plugin>/`. `dirname(record_dir)` is then the MOD folder, so
every master looked up from it read as missing and the consumer quietly
answered "no base": Frostcrag Reborn's three masters vanished from
`export_dirs`, and with them the wearable plan's inherited ARMO/CLOT, the
grass profile's base GRAS models and the voice pipeline's master RACEs.
See: docs/commentary/tes5_import_mod_merge.md#export-root-resolution
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    return _write(d / '_HEADER.txt',
                  ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)))


def _record(d, sig, **fields):
    body = ''.join(f'{k.replace("__", ".")}={v}\n' for k, v in fields.items())
    _write(d / f'{sig}.txt',
           f'---RECORD_BEGIN---\nSignature={sig}\n{body}---RECORD_END---\n')


def _export(tmp_path):
    """export/ with Base.esm + Patch.esp (plain), `MOD` (Mod.esp + Extra.esp
    nested), and `PACK` (Res.esm + Res2.esp nested). Returns (root, mod rec)."""
    exp = tmp_path / 'export'

    def group(label, gid, plugins):
        return {n: {'kind': 'archive', 'plugin': n, 'group_id': gid,
                    'group_label': label, 'group_plugins': plugins}
                for n in plugins}

    _write(exp / 'sources.json', json.dumps({'version': 1, 'sources': {
        **group(MOD, 'g-mod', ['Mod.esp', 'Extra.esp']),
        **group(PACK, 'g-pack', ['Res.esm', 'Res2.esp'])}}))
    base = exp / 'Base.esm'
    _header(base, [])
    _record(base, 'ARMO', FormID='00001234', BMDT__BipedFlags='4',
            Male__BipedModel__MODL='armor\\iron\\cuirass.nif')
    _record(base, 'GRAS', FormID='00002222', Model__MODL='plants\\grass01.nif')
    _record(base, 'RACE', FormID='00000907', EditorID='Imperial')
    _write(base / 'textures' / 'rock' / 'stone.dds', 'DDS')
    _header(exp / 'Patch.esp', ['Base.esm'])
    res = exp / PACK / 'Res.esm'
    _header(res, [])
    _write(exp / PACK / 'meshes' / 'res' / 'thing.nif', 'NIF')
    _write(exp / PACK / 'textures' / 'res' / 'thing.dds', 'DDS')
    rec = exp / MOD / 'Mod.esp'
    _header(rec, ['Base.esm', 'Patch.esp', 'Res.esm'])
    (exp / MOD / 'meshes').mkdir(parents=True)
    return exp, rec


# ---------------------------------------------------------------------------
#  The resolver
# ---------------------------------------------------------------------------

def test_export_root_of_every_shape(tmp_path):
    """A nested record dir, its mod folder, a plain plugin, the root itself
    and a trailing separator all resolve to the root."""
    from output_layout import export_root_of
    exp, rec = _export(tmp_path)
    for d in (rec, exp / MOD, exp / 'Base.esm', exp, str(rec) + '/'):
        assert export_root_of(d) == exp, d


def test_without_a_marker_the_parent_is_the_root(tmp_path):
    """Pre-registry layout; and a `sources.json` above the deepest shape
    (three levels up) is never taken for the root."""
    from output_layout import export_root_of
    _write(tmp_path / 'sources.json', '{}')
    d = tmp_path / 'a' / 'b' / 'c' / 'Mod.esp'
    d.mkdir(parents=True)
    assert export_root_of(d) == d.parent


def test_every_export_root_helper_agrees(tmp_path):
    """Four modules once each answered this separately."""
    from asset_convert.game_paths import _export_root
    from asset_convert.lod.terrain_lod import _master_record_dir
    from tes5_import.dialogue.morrowind_sidecar import export_root as mw_root
    from tes5_import.overrides.nested import export_root
    exp, rec = _export(tmp_path)
    assert Path(export_root(str(rec))) == exp
    assert Path(mw_root(str(rec))) == exp
    assert _export_root(rec) == exp
    assert Path(_master_record_dir(rec, 'Base.esm')) == exp / 'Base.esm'


# ---------------------------------------------------------------------------
#  base_plugins: the source
# ---------------------------------------------------------------------------

def test_export_dirs_of_a_nested_plugin_finds_its_masters(tmp_path):
    """All three masters, nearest first, the nested one by its record dir."""
    exp, rec = _export(tmp_path)
    assert base_plugins.names_for(rec) == ['Res.esm', 'Patch.esp', 'Base.esm']
    assert [Path(d) for d in base_plugins.export_dirs(rec)] == [
        exp / PACK / 'Res.esm', exp / 'Patch.esp', exp / 'Base.esm']


def test_asset_dirs_read_a_nested_masters_assets_from_its_mod_folder(tmp_path):
    """A nested master's meshes/textures come from its mod folder."""
    exp, rec = _export(tmp_path)
    assert [Path(d) for d in base_plugins.asset_dirs(rec)] == [
        exp / PACK, exp / 'Patch.esp', exp / 'Base.esm']
    assert [Path(d) for d in base_plugins.subdirs(rec, 'textures')] == [
        exp / PACK / 'textures', exp / 'Base.esm' / 'textures']


def test_a_mods_recorded_base_is_seen_from_its_nested_record_dir(tmp_path):
    """`--base` lands in the MOD's `_source/`, one level above the records."""
    exp, rec = _export(tmp_path)
    _write(exp / MOD / '_source' / base_plugins.FILE_NAME, 'Base.esm\nExtra.esm\n')
    assert base_plugins.names_for(rec) == [
        'Extra.esm', 'Res.esm', 'Patch.esp', 'Base.esm']


def test_texture_fallback_of_a_nested_mods_meshes(tmp_path):
    """The asset root finds its bases through `--base`; a NESTED base's
    textures sit in its mod folder, not beside its records."""
    from asset_convert.nif import shaders
    exp, rec = _export(tmp_path)
    _write(exp / MOD / '_source' / base_plugins.FILE_NAME, 'Base.esm\nRes.esm\n')
    roots = shaders.master_texture_roots(exp / MOD / 'meshes')
    assert [Path(r) for r in roots] == [exp / PACK / 'textures',
                                        exp / 'Base.esm' / 'textures']


# ---------------------------------------------------------------------------
#  Consumers that were blind
# ---------------------------------------------------------------------------

def test_wearable_plan_inherits_the_masters_armo(tmp_path):
    """The master's worn cuirass is in the nested mod's plan."""
    from asset_convert.character import wearable_plan as wp
    exp, rec = _export(tmp_path)
    plan = wp.build_plan(rec)
    key = wp.norm_model_path('armor\\iron\\cuirass.nif')
    assert plan.get(key) == wp.build_plan(exp / 'Base.esm')[key]
    assert plan[key] & wp.WORN


def test_grass_profile_sees_the_masters_gras(tmp_path):
    """The master's GRAS model is in the nested mod's grass set."""
    from asset_convert.nif.grass_profile import load_grass_model_paths
    _exp, rec = _export(tmp_path)
    assert 'plants\\grass01.nif' in load_grass_model_paths(rec)


def test_voice_races_read_the_masters_race(tmp_path):
    """Every master's record dir, in load order."""
    from asset_convert.audio.voice_races import master_race_dirs
    exp, rec = _export(tmp_path)
    assert master_race_dirs(rec) == [exp / 'Base.esm', exp / 'Patch.esp',
                                     exp / PACK / 'Res.esm']


def test_morroblivion_split_roots_include_a_nested_masters_meshes(tmp_path):
    """The pair splitter reads meshes from asset roots, its own included."""
    from tes4_export import morroblivion_pairs as mp
    exp, rec = _export(tmp_path)
    seen = {}

    def _fake(item, roots, out, log):
        seen['roots'] = [Path(r) for r in roots]
        return {}

    orig = mp.split_models
    mp.split_models = _fake
    try:
        mp.split_pairs([mp.Pair(None, None, {}, str(rec))], str(tmp_path / 'o'))
    finally:
        mp.split_models = orig
    assert seen['roots'] == [exp / MOD / 'meshes', exp / PACK / 'meshes',
                             exp / 'Patch.esp' / 'meshes',
                             exp / 'Base.esm' / 'meshes']


def test_cell_meshes_resolves_a_nested_plugins_master_base(tmp_path):
    """A base owned by master 0 resolves from a nested plugin."""
    from tools.esm.cell_meshes import build_master_aware_index
    exp, rec = _export(tmp_path)
    _record(exp / 'Base.esm', 'STAT', FormID='00000ABC', EditorID='Rock',
            Model__MODL='rocks\\rock01.nif')
    index = build_master_aware_index(str(rec), {'00000ABC'})
    assert '00000ABC' in index
