"""`--only` scoping: which steps honor it, and what a scoped creature run keeps.

See: docs/reference/pipeline.md#scoping-a-stage
"""

import json
from pathlib import Path

from asset_convert.havok.animation_data import fragment_path
from asset_convert.havok.creature_pipeline import _kept_appends
from convert_cli import build_parser, selected_steps, unscoped_steps


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
