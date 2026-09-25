"""The runtime sidecars an import writes for one plugin: whatever the run did
not rewrite is deleted once it has written everything.

A plugin's sidecars are its `SKSE/Plugins/MorrowindRuntime/<plugin>/` folder
and its `<plugin>.<kind>.json` files under `TESRuntime/` and
`FalloutRuntime/`. They are swept after the write rather than cleared first,
so an import that fails partway deletes nothing.

See: docs/commentary/tes5_import_pipeline.md#stale-runtime-sidecars
"""

import os
import time

from .dialogue.morrowind_sidecar import sidecar_dir
from .record_types.bodypart_falloutnv import SIDECAR_DIR as FALLOUT_DIR
from .record_types.crime import SIDECAR_DIR as TES_DIR

#: Output ESM path -> when its import began; a sidecar older than that was not rewritten.
_RUNS = {}

#: Seconds of slack for the filesystem's timestamp granularity.
_SLACK = 2.0


def begin_sidecar_run(output_path: str) -> None:
    """Note that an import of `output_path` starts now."""
    _RUNS[os.path.abspath(output_path)] = time.time()


def owned_sidecars(output_path: str) -> list:
    """Every runtime sidecar file belonging to the plugin at `output_path`."""
    plugin = os.path.basename(output_path)
    stem = os.path.splitext(plugin)[0]
    root = os.path.dirname(output_path)
    files = [os.path.join(folder, name)
             for folder, _dirs, names in os.walk(sidecar_dir(output_path, plugin))
             for name in names]
    for runtime in (TES_DIR, FALLOUT_DIR):
        folder = os.path.join(root, runtime)
        names = os.listdir(folder) if os.path.isdir(folder) else []
        files += [os.path.join(folder, name) for name in names
                  if name.startswith(stem + '.') and name.endswith('.json')
                  and '.' not in name[len(stem) + 1:-len('.json')]]
    return files


def sweep_stale_sidecars(output_path: str) -> int:
    """Delete the plugin's sidecars this run did not write, and the Morrowind
    folders that leaves empty. Nothing without `begin_sidecar_run`. Returns
    files deleted."""
    start = _RUNS.pop(os.path.abspath(output_path), None)
    if start is None:
        return 0
    stale = [path for path in owned_sidecars(output_path)
             if os.path.getmtime(path) < start - _SLACK]
    for path in stale:
        os.remove(path)
    folder = sidecar_dir(output_path, os.path.basename(output_path))
    for path in (folder, os.path.dirname(folder)):
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    return len(stale)
