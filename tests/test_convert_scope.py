"""`--only` scoping: which steps honor it, and what a scoped creature run keeps.

See: docs/reference/pipeline.md#scoping-a-stage
"""

import json
from pathlib import Path

import pytest

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
    assert steps == ['creatures', 'pack_bsa'] and args.only == ['rat']
    assert unscoped_steps(args, steps) == []


def test_only_is_refused_where_it_cannot_narrow():
    """A stage that ignores `--only` is named, so the run refuses instead of rebuilding all."""
    args, steps = _steps('--meshes-only', '--creatures-only', '--only', 'rat')
    assert unscoped_steps(args, steps) == ['meshes']
    args, steps = _steps('--meshes-only')
    assert unscoped_steps(args, steps) == []


def test_morrowind_source_flag_overrides_the_config_for_one_run():
    """A confirmed `--morrowind-source` wins over the saved mode; without it the saved mode stands."""
    config = {'morrowindSource': 'morroblivion'}
    apply_config_overrides(build_parser().parse_args(
        ['--morrowind-source', 'vanilla', '--confirm-mode-change']), config)
    assert config['morrowindSource'] == 'vanilla'
    config = {'morrowindSource': 'morroblivion'}
    apply_config_overrides(build_parser().parse_args([]), config)
    assert config['morrowindSource'] == 'morroblivion'


def test_another_morrowind_mode_stops_unless_confirmed(monkeypatch, capsys):
    """A mode other than the configured one asks first; no or no answer stops the run.

    See: docs/reference/pipeline.md#morrowind-mode-guard
    """
    args = build_parser().parse_args(['--morrowind-source', 'vanilla'])
    for reply in (EOFError(), 'n'):
        monkeypatch.setattr('builtins.input', lambda _prompt, r=reply: _reply(r))
        config = {'morrowindSource': 'hybrid'}
        with pytest.raises(SystemExit):
            apply_config_overrides(args, config)
        assert config['morrowindSource'] == 'hybrid'
    assert "configured Morrowind source is 'hybrid'" in capsys.readouterr().out
    monkeypatch.setattr('builtins.input', lambda _prompt: 'y')
    config = {'morrowindSource': 'hybrid'}
    apply_config_overrides(args, config)
    assert config['morrowindSource'] == 'vanilla'


def test_the_configured_morrowind_mode_never_asks(monkeypatch):
    """Naming the mode already configured (vanilla when unset) runs without a prompt."""
    monkeypatch.setattr('builtins.input', lambda _prompt: pytest.fail('asked'))
    for config, mode in (({'morrowindSource': 'hybrid'}, 'hybrid'), ({}, 'vanilla')):
        apply_config_overrides(build_parser().parse_args(['--morrowind-source', mode]), config)
        assert config['morrowindSource'] == mode


def _reply(reply):
    """`reply` as input() gives it: raised when it is an exception."""
    if isinstance(reply, BaseException):
        raise reply
    return reply


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
