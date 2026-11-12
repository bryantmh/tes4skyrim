"""The import's own navmesh phase (navmesh.phase.build_navmeshes) for cellview: every cell of a plugin.

cellview never builds a navmesh itself: it shows each cell from this build, so it cannot differ from
the plugin.  The build runs in its own process (it rewrites module state cellview's index relies on)
and is saved beside the navmesh geometry cache, rebuilt only when its key changes: the generator's
code tag, the plugin's export, or the pins that reach a cell's edge (only those change a neighbour).
Every other pin is applied on top of its own cell, as the build does.

    python tools/cellview/world.py <plugin> <out.pkl> <key>

See: docs/commentary/tes5_import_navmesh.md#seeded-cells
"""

import hashlib
import os
import pickle
import subprocess
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.navmesh_options import set_edge_pins_only, set_keep_flood
from core.subprocess_flags import POPEN_FLAGS
from tes5_import.base.navmesh_pins import PARTS, build_pins, hand_edits_for, load
from tes5_import.base.text_reader import get_formid
from tes5_import.navmesh import pool
from tes5_import.navmesh.edge_links import NavMeshView, extract_nvnm
from tes5_import.navmesh.phase import build_navmeshes
from tes5_import.overrides.nested import open_masters
from tools.cellview import plugins
from tools.navmesh.job_trace import load_records

#: The FormID index offset the build runs at; it numbers records, it never moves geometry.
OFFSET = 1

#: Folder holding the converted masters a child plugin's build reads.
OUTPUT_ROOT = 'output'

#: Saved-build key -> {cell export FormID: [(verts, tris, door points, ledges)]}, for this session.
_BUILT = {}

#: Layout of a saved build; a change rebuilds every saved one.
SAVE_FORMAT = 3


class _Ids:
    """The writer calls the navmesh phase makes; the ids only label results here."""

    def derive_formid(self, _sig, key):
        """A stable id per key."""
        return 0x01000000 | (zlib.crc32(repr(key).encode()) & 0xFFFFFF)


def _marks(view):
    """(door points, ledges) of one built mesh: each Door Triangle's centroid, and each ledge link as
    (upper tri, lower tri, drop)."""
    centre = view.verts[view.tris[:, :3]].mean(axis=1)
    doors = [tuple(float(c) for c in centre[ti]) for ti, _ref in view.door_triangles()]
    pairs = {tuple(sorted(p)) for p in view.own_links()}
    ledges = []
    for a, b in sorted(pairs):
        hi, lo = (a, b) if centre[a][2] >= centre[b][2] else (b, a)
        ledges.append((hi, lo, float(centre[hi][2] - centre[lo][2])))
    return doors, ledges


def _by_cell(cache, by_type):
    """{cell export FormID: [(verts, tris, door points, ledges, flood)]} from the phase's {key: (navm_bytes,
    meta)}; flood is the generated graph (points, edges) the mesh was built over, or None."""
    names = {get_formid(c, 'FormID'): (c.get('FormID') or '').upper() for c in by_type.get('CELL', [])}
    out = {}
    for navm_bytes, meta in cache.values():
        blob = extract_nvnm(navm_bytes)[0] if navm_bytes and meta else None
        name = names.get(meta.get('cell_fid')) if blob is not None else None
        if name:
            view = NavMeshView(meta['fid'], blob)
            out.setdefault(name, []).append((view.verts, view.tris[:, :3].copy()) + _marks(view)
                                            + (meta.get('flood'),))
    return out


def _build(plugin):
    """{cell export FormID: [(verts, tris)]}: the plugin's navmesh phase, here, with its edge pins."""
    export = plugins.export_dir(plugin)
    t0 = time.time()
    set_edge_pins_only(True)
    set_keep_flood(True)
    by_type, _masters = load_records(export, OFFSET)
    ctx = open_masters(export, [], OUTPUT_ROOT)[2]
    cache = build_navmeshes(by_type, _Ids(), ctx, export, plugin)[0]
    out = _by_cell(cache, by_type)
    print('cellview: built %d cells of %s in %.0fs' % (len(out), plugin, time.time() - t0), flush=True)
    return out


def _edge_pins(plugin, grid_of):
    """repr of every pinned cell whose pins reach its edge, in key order; `grid_of(key)` -> grid or None."""
    doc = load(plugin)
    keys = sorted({k for part in PARTS for k in doc.get(part, {})})
    rows = []
    for key in keys:
        held = build_pins(hand_edits_for(plugin, key), grid_of(key))
        if held:
            rows.append((key, held))
    return repr(rows)


def _key(plugin, export, grid_of):
    """(folder, key) of a saved build: the generator's code tag, the export's files, the edge pins."""
    geom = pool.navmesh_geom_cache(pool.collision_cache_chain(export)[-1])
    h = hashlib.sha1(repr((SAVE_FORMAT, geom[1] if geom else '')).encode())
    h.update(_edge_pins(plugin, grid_of).encode())
    for entry in sorted(os.scandir(export), key=lambda e: e.name):
        if entry.name.endswith('.txt'):
            st = entry.stat()
            h.update(repr((entry.name, st.st_size, st.st_mtime_ns)).encode())
    return (geom[0] if geom else export), h.hexdigest()


def _saved(path, key):
    """The build saved at `path` under `key`, or None."""
    try:
        with open(path, 'rb') as fh:
            got = pickle.load(fh)
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ImportError):
        return None
    return got['meshes'] if got.get('key') == key else None


def _save(path, key, meshes):
    """Write one build to `path` under `key`, atomically."""
    with open(path + '.tmp', 'wb') as fh:
        pickle.dump({'key': key, 'meshes': meshes}, fh, pickle.HIGHEST_PROTOCOL)
    os.replace(path + '.tmp', path)


#: Tries, and seconds between them, when Windows runs out of system resources (WinError 1450) mid-build.
RESOURCE_TRIES, RESOURCE_WAIT = 3, 20.0


def _run_build(cmd) -> None:
    """Run the build process; wait and retry while Windows reports WinError 1450, raise on any other failure."""
    for attempt in range(RESOURCE_TRIES):
        done = subprocess.run(cmd, capture_output=True, text=True, **POPEN_FLAGS)
        err = done.stderr or done.stdout
        if not done.returncode:
            return
        if 'WinError 1450' not in err or attempt == RESOURCE_TRIES - 1:
            raise RuntimeError('navmesh build failed:\n' + err[-3000:])
        print('cellview: build out of system resources (WinError 1450); retrying in %.0fs' % RESOURCE_WAIT,
              flush=True)
        time.sleep(RESOURCE_WAIT)


def plugin_meshes(plugin, grid_of):
    """{cell export FormID: [(verts, tris)]}: this session's build, else the saved one, else built in its
    own process.  One build per plugin, with the pins that reach a cell's edge: the page's pinned box only
    applies or skips each cell's other pins on top, so it never starts a build.  `grid_of(pin key)` gives
    an exterior cell's grid, None for an interior."""
    folder, key = _key(plugin, plugins.export_dir(plugin), grid_of)
    if key not in _BUILT:
        path = os.path.join(folder, 'cellview_%s.pkl' % plugin)
        meshes = _saved(path, key)
        if meshes is None:
            _run_build([sys.executable, os.path.abspath(__file__), plugin, path, key])
            meshes = _saved(path, key)
        _BUILT[key] = meshes or {}
    return _BUILT[key]


def cell_mesh(plugin, cell_fid, grid_of):
    """`(verts, tris, door points, ledges)` of every mesh the build made for one cell (export FormID text),
    joined; a ledge is (upper tri, lower tri, drop)."""
    verts, tris, doors, ledges = [], [], [], []
    for v, t, d, lk, _flood in plugin_meshes(plugin, grid_of).get(cell_fid.upper(), ()):
        base = len(tris)
        tris += [tuple(int(i) + len(verts) for i in row) for row in t]
        verts += [tuple(float(c) for c in p) for p in v]
        doors += d
        ledges += [(hi + base, lo + base, drop) for hi, lo, drop in lk]
    return verts, tris, doors, ledges


def cell_flood(plugin, cell_fid, grid_of):
    """`(points, edges)` of the graph the build generated for one cell (gap flood or seeding), or None."""
    for *_mesh, flood in plugin_meshes(plugin, grid_of).get(cell_fid.upper(), ()):
        if flood:
            return flood
    return None


def main():
    """Build one plugin and save it: the process plugin_meshes starts."""
    plugin, path, key = sys.argv[1:4]
    _save(path, key, _build(plugin))
    return 0


if __name__ == '__main__':
    sys.exit(main())
