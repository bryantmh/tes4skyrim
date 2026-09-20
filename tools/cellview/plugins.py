"""Which exports cellview can open, and what they still need.

A plugin used to appear only if its ~2 GB `audit_index3.pkl` already existed.
This lists every export with a `CELL.txt` instead, builds the index on demand,
and refuses a cell whose collision cache is missing rather than drawing a
pathgrid floating in an empty room.

See: docs/commentary/tes5_import_navmesh.md#cellview-index-on-demand
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.collision.collision_extract import collision_cache_is_current
from asset_convert.sources import source_registry
from output_layout import assets_for, record_dir
from tools.navmesh.audit import cell_index, index_path

#: Where every export's record dump lives.
EXPORT_ROOT = 'export'


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
    """Every plugin name to consider: registered ones plus loose folders.

    A registered mod's plugins are nested, so listing `export/` alone shows
    the mod FOLDER and never the ESM inside it.
    """
    out = set(os.listdir(EXPORT_ROOT) if os.path.isdir(EXPORT_ROOT) else [])
    try:
        out.update(source_registry.plugins(EXPORT_ROOT))
    except (OSError, ValueError):
        pass
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
                'draw) -- run: python convert.py -f %s --meshes-only'
                % (plugin, plugin))
    return ''


def ensure_index(plugin):
    """Build `plugin`'s index if absent (~78s once); returns its path.

    See: docs/commentary/tes5_import_navmesh.md#cellview-cell-index
    """
    export = export_dir(plugin)
    cell_index(export).close()
    return index_path(export)
