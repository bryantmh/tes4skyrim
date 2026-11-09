"""Which exports cellview can open, and what they still need.

A plugin used to appear only if its ~2 GB `audit_index3.pkl` already existed.
This lists every export with a `CELL.txt` instead, builds the index on demand,
and refuses a cell whose collision cache is missing rather than drawing a
pathgrid floating in an empty room.

See: docs/commentary/tes5_import_navmesh.md#cellview-index-on-demand
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.collision.collision_extract import collision_cache_is_current
from asset_convert.sources import source_registry
from core.plugin_masters import masters_from_export_header
from core.subprocess_flags import POPEN_FLAGS
from output_layout import assets_for, record_dir
from tools.navmesh.audit import cell_index, index_path
from tools.navmesh.index import NavIndex

#: The repository root every relative path here resolves against.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Where every export's record dump lives (the server runs from ROOT).
EXPORT_ROOT = 'export'

#: The tool that scans converted meshes into an export's collision cache.
RESCAN = os.path.join(ROOT, 'tools', 'navmesh', 'rescan_mesh_caches.py')


def export_dir(plugin):
    """The export directory for `plugin`, mod-nested plugins included.

    See: docs/commentary/tes5_import_navmesh.md#cellview-master-owned-cells
    """
    return str(record_dir(EXPORT_ROOT, plugin))


def _has_records(export):
    """True when a directory is an export we could index."""
    return os.path.isfile(os.path.join(export, 'CELL.txt'))


def _has_collision(export):
    """True when the export's Havok cache is present AND current.

    See: docs/commentary/tes5_import_navmesh.md#cellview-index-on-demand
    """
    return collision_cache_is_current(
        os.path.join(str(assets_for(export)), 'collision_cache.bin'))


def _names():
    """Every plugin name to consider: registered ones plus loose plugin folders.

    A registered mod's folder is never offered: pins save under the name
    picked here, and they key on the plugin, not the folder.
    See: docs/commentary/tes5_import_navmesh.md#pins-key-on-the-plugin
    """
    out = set(os.listdir(EXPORT_ROOT) if os.path.isdir(EXPORT_ROOT) else [])
    try:
        mods = source_registry.plugins(EXPORT_ROOT)
    except (OSError, ValueError):
        mods = []
    out -= {source_registry.asset_root_name(EXPORT_ROOT, p) for p in mods}
    out.update(mods)
    return sorted(out, key=str.lower)


def candidates():
    """Every openable export as `{name, indexed, collision}`.

    `indexed` false still opens -- the index builds on first use; `collision`
    false does not, which is what `preconditions` reports.
    """
    out = []
    for name in _names():
        export = export_dir(name)
        if not _has_records(export):
            continue
        out.append({'name': name,
                    'indexed': os.path.isfile(index_path(export)),
                    'collision': _has_collision(export)})
    return out


def preconditions(plugin):
    """Why `plugin` cannot be opened, or '' when it can.

    Collision is REQUIRED: `load_collision` answers 0 for a missing or
    unreadable cache rather than raising, so without this the cell opens with
    no walls at all and reads as a generation bug.
    """
    export = export_dir(plugin)
    if not _has_records(export):
        return 'no export for %r -- run: python convert.py -f %s --export-only' % (
            plugin, plugin)
    if not _has_collision(export):
        return ('collision cache missing or stale for %s (walls would not '
                'draw) -- pick it in the plugin list to build it' % plugin)
    return ''


def build_collision(plugin):
    """Scan `plugin`'s and its masters' converted meshes into their collision caches.

    Runs `rescan_mesh_caches.py`, which takes the heavy-job lock and so queues
    behind a running build; a current cache is left alone.  Returns '' on
    success, else why the cache is still missing.
    """
    names = masters_from_export_header(export_dir(plugin)) + [plugin]
    run = subprocess.run([sys.executable, '-u', RESCAN] + names, cwd=ROOT,
                         capture_output=True, text=True, errors='replace',
                         **POPEN_FLAGS)
    NavIndex.disarm()
    if _has_collision(export_dir(plugin)):
        return ''
    tail = '\n'.join((run.stdout + run.stderr).strip().splitlines()[-8:])
    return ('could not build the collision cache for %s -- its meshes may not '
            'be converted yet (run the Meshes step for it first).\n\n%s'
            % (plugin, tail))


def ensure_index(plugin):
    """Build `plugin`'s index if absent (~78s once); returns its path.

    See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
    """
    export = export_dir(plugin)
    cell_index(export).close()
    return index_path(export)
