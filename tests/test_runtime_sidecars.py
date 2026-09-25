"""The import sweeps the runtime sidecars it did not rewrite.

See: docs/commentary/tes5_import_pipeline.md#stale-runtime-sidecars
"""

import os
import time

from tes5_import.runtime_sidecars import (begin_sidecar_run,
                                          sweep_stale_sidecars)


def _file(path, age=0.0):
    """Write `path`, its mtime `age` seconds in the past."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('x')
    when = time.time() - age
    os.utime(path, (when, when))
    return path


def test_only_the_files_the_run_left_untouched_go(tmp_path):
    """Old files go, rewritten ones stay, other plugins' files are never touched."""
    out = tmp_path / 'Plugin.esm'
    esm = str(out / 'Plugin.esm')
    plugins = out / 'SKSE' / 'Plugins'
    old = [_file(str(plugins / 'TESRuntime' / 'Plugin.crime.json'), 600),
           _file(str(plugins / 'FalloutRuntime' / 'Plugin.guns.json'), 600),
           _file(str(plugins / 'MorrowindRuntime' / 'Plugin' / 'SOUN.txt'), 600)]
    others = [_file(str(plugins / 'TESRuntime' / 'Other.crime.json'), 600),
              _file(str(plugins / 'TESRuntime' / 'Plugin.x.crime.json'), 600),
              _file(str(plugins / 'FalloutRuntime' / 'FalloutRuntime.ini'), 600)]
    begin_sidecar_run(esm)
    kept = _file(str(plugins / 'TESRuntime' / 'Plugin.apparatus.json'))
    assert sweep_stale_sidecars(esm) == len(old)
    assert not any(os.path.exists(path) for path in old)
    assert all(os.path.exists(path) for path in others + [kept])
    assert not os.path.exists(plugins / 'MorrowindRuntime')


def test_nothing_is_swept_without_a_run(tmp_path):
    """A caller that never began a run deletes nothing."""
    esm = str(tmp_path / 'Plugin.esm' / 'Plugin.esm')
    stale = _file(os.path.join(os.path.dirname(esm), 'SKSE', 'Plugins',
                               'TESRuntime', 'Plugin.crime.json'), 600)
    assert sweep_stale_sidecars(esm) == 0
    assert os.path.exists(stale)
