"""`--only` scoping: which steps honor it, and what a scoped creature run keeps.

See: docs/reference/pipeline.md#scoping-a-stage
"""

import json
from pathlib import Path

from asset_convert.havok.animation_data import fragment_path
from asset_convert.havok.creature_pipeline import _kept_appends
from convert_cli import (apply_config_overrides, build_parser, selected_steps,
                         unscoped_steps)


def _steps(*argv):
    """(args, selected steps) for a command line."""
    args = build_parser().parse_args(list(argv))
    return args, selected_steps(args)


def test_only_scopes_the_creature_stage():
    """`--creatures-only --only rat` is a scoped run with nothing refused."""
    args, steps = _steps('--creatures-only', '--only', 'rat')
    assert steps == ['creatures'] and args.only == ['rat']
    assert unscoped_steps(args, steps) == []


def test_only_is_refused_where_it_cannot_narrow():
    """A stage that ignores `--only` is named, so the run refuses instead of rebuilding all."""
    args, steps = _steps('--meshes-only', '--creatures-only', '--only', 'rat')
    assert unscoped_steps(args, steps) == ['meshes']
    args, steps = _steps('--meshes-only')
    assert unscoped_steps(args, steps) == []


def test_morrowind_source_flag_overrides_the_config_for_one_run():
    """`--morrowind-source` wins over the saved mode; without it the saved mode stands."""
    config = {'morrowindSource': 'morroblivion'}
    apply_config_overrides(build_parser().parse_args(
        ['--morrowind-source', 'vanilla']), config)
    assert config['morrowindSource'] == 'vanilla'
    config = {'morrowindSource': 'morroblivion'}
    apply_config_overrides(build_parser().parse_args([]), config)
    assert config['morrowindSource'] == 'morroblivion'


def test_a_scoped_run_keeps_the_fragments_gun_appends(tmp_path):
    """The appends a full run registered survive a run scoped to some creatures."""
    plugin_out = tmp_path / 'FalloutNV.esm'
    path = Path(fragment_path(str(plugin_out), plugin_out.name))
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'version': 3, 'source': 'FalloutNV.esm',
                                'animdata': [1], 'animsetdata': [2],
                                'animdata_appends': ['gun']}), encoding='utf-8')
    assert _kept_appends(str(plugin_out)) == {'animdata_appends': ['gun']}
    assert _kept_appends(str(tmp_path / 'Missing.esm')) == {}


def test_plugin_mesh_option_reaches_conversion_but_asset_only_mods_keep_their_payload(monkeypatch):
    import convert
    from asset_convert import asset_pipeline

    scopes = []
    monkeypatch.setattr(convert, '_use_plugin_namespace', lambda *_: None)
    monkeypatch.setattr(convert, 'is_asset_only', lambda name, root: name == 'Assets')
    monkeypatch.setenv(convert.WINDING_FIX_ENV_VAR, '0')
    monkeypatch.setattr(asset_pipeline, 'convert_meshes',
                        lambda **kw: scopes.append((kw['source_file'],
                                                    kw['plugin_assets_only'])) or {})
    for name in ('Patch.esp', 'Assets'):
        assert convert.phase_assets(name, {}, textures_only=True,
                                     plugin_assets_only=True) is True
    assert scopes == [('Patch.esp', True), ('Assets', False)]


def test_shared_texture_phase_does_not_run_mesh_or_book_conversion(monkeypatch):
    import convert
    from asset_convert import asset_pipeline

    monkeypatch.setattr(convert, '_use_plugin_namespace', lambda *_: None)
    def unexpected(**_kwargs):
        raise AssertionError('Shared texture pass must not convert meshes')
    monkeypatch.setattr(asset_pipeline, 'convert_meshes', unexpected)
    calls = []
    monkeypatch.setattr(asset_pipeline, 'convert_shared_textures',
                        lambda name, plugins, **_kw: calls.append((name, plugins))
                        or {'textures_copied': 7})
    assert convert.phase_assets('A.esm', {}, shared_texture_plugins=['A.esm', 'B.esp'])
    assert calls == [('A.esm', ['A.esm', 'B.esp'])]


def test_imported_single_plugin_uses_setting_for_meshes_and_creatures(tmp_path, monkeypatch):
    import convert
    from asset_convert import asset_pipeline
    from asset_convert.havok import creature_pipeline
    monkeypatch.setattr(convert, 'SCRIPT_DIR', tmp_path)
    monkeypatch.setattr(convert, '_use_plugin_namespace', lambda *_: None)
    monkeypatch.setattr(convert, 'is_asset_only', lambda *_: False)
    monkeypatch.setattr(convert.source_registry, 'get', lambda *_: {'group_id': 'g'})
    monkeypatch.setattr(convert.source_registry, 'group_members',
                        lambda *_: ['A.esm', 'B.esp'])
    monkeypatch.setattr(convert, 'record_dir', lambda *_: tmp_path)
    scopes, textures, creatures = [], [], []
    monkeypatch.setattr(asset_pipeline, 'convert_meshes',
                        lambda **kw: scopes.append(kw) or {})
    monkeypatch.setattr(asset_pipeline, 'convert_shared_textures',
                        lambda *args, **kw: textures.append((args, kw)))
    monkeypatch.setattr(creature_pipeline, 'convert_creatures',
                        lambda *args, **kw: creatures.append(kw) or
                        {'projects': [], 'errors': {}})
    for enabled in (True, False):
        config = {'importedModOptimizations': enabled}
        assert convert.phase_assets('B.esp', config, textures_only=True,
                                     plugin_assets_only=True)
        assert convert.phase_creatures('B.esp', '', config, plugin_assets_only=True)
        assert scopes[-1]['plugin_assets_only'] is enabled
        assert scopes[-1]['defer_textures'] is enabled
        assert bool(scopes[-1]['mesh_reuse_token']) is enabled
        assert creatures[-1]['plugin_assets_only'] is enabled
    assert len(textures) == 1 and textures[0][0] == ('A.esm', ['A.esm', 'B.esp'])
    assert textures[0][1]['reuse'] is True
    monkeypatch.setattr(convert, 'is_asset_only', lambda *_: True)
    assert convert.phase_creatures('Assets', {}, {'importedModOptimizations': True})
    assert creatures[-1]['plugin_assets_only'] is False

