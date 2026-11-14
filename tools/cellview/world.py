"""The import's own navmesh phase (navmesh.phase.build_navmeshes) for cellview: every cell of a plugin,
or one interior alone (nothing outside an interior changes it).

cellview never builds a navmesh itself: it shows each cell from this build, so it cannot differ from
the plugin.  The build runs in its own process (it rewrites module state cellview's index relies on)
and is saved in `cellview_builds/` beside the navmesh geometry cache, rebuilt only when its key changes: the generator's
code tag, the plugin's export, or the pins that reach a cell's edge (only those change a neighbour).
Every other pin is applied on top of its own cell, as the build does.  Interiors are built by one
worker per plugin that loads the plugin once and stays up while its key holds.

    python tools/cellview/world.py <plugin> <out.pkl> <key>     # the whole plugin
    python tools/cellview/world.py --serve <plugin>             # the interior worker

See: docs/commentary/tes5_import_navmesh.md#seeded-cells
"""

import glob
import hashlib
import os
import pickle
import subprocess
import sys
import threading
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

#: (saved-build key, interior or '') -> {cell export FormID: [(verts, tris, door points, ledges)]}, this session.
_BUILT = {}

#: Layout of a saved build; a change rebuilds every saved one.
SAVE_FORMAT = 5

#: plugin -> (saved-build key, worker process) building that plugin's interiors.
_WORKERS = {}

#: One request at a time per worker; the page's threaded server can ask for two cells at once.
_WORKER_LOCK = threading.Lock()

#: The line a worker prints once a requested build is saved.
DONE = 'cellview-worker: done'

#: Worker output lines kept for the error when a build fails.
TAIL_LINES = 60


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


def _navms(cache):
    """Every (navm_bytes, meta) built, split interiors' pieces included."""
    for navm_bytes, meta in cache.values():
        yield navm_bytes, meta
        yield from (meta or {}).get('extra_navms', ())


def _by_cell(cache, by_type):
    """{cell export FormID: [(verts, tris, door points, ledges, flood)]} from the phase's {key: (navm_bytes,
    meta)}, split interiors' pieces included; flood is the generated graph (points, edges) the mesh was
    built over, or None."""
    names = {get_formid(c, 'FormID'): (c.get('FormID') or '').upper() for c in by_type.get('CELL', [])}
    out = {}
    for navm_bytes, meta in _navms(cache):
        blob = extract_nvnm(navm_bytes)[0] if navm_bytes and meta else None
        name = names.get(meta.get('cell_fid')) if blob is not None else None
        if name:
            view = NavMeshView(meta['fid'], blob)
            out.setdefault(name, []).append((view.verts, view.tris[:, :3].copy()) + _marks(view)
                                            + (meta.get('flood'),))
    return out


def _records(plugin):
    """(export dir, records by type, override context) of one plugin, loaded once for its builds."""
    export = plugins.export_dir(plugin)
    set_edge_pins_only(True)
    set_keep_flood(True)
    by_type, _masters = load_records(export, OFFSET)
    return export, by_type, open_masters(export, [], OUTPUT_ROOT)[2]


def _build(plugin, state, cell):
    """{cell export FormID: [(verts, tris)]}: the plugin's navmesh phase, here, with its edge pins; only
    the interior `cell` (export FormID text) when one is named."""
    export, by_type, ctx = state
    t0 = time.time()
    cells = {get_formid(c, 'FormID') for c in by_type.get('CELL', [])
             if (c.get('FormID') or '').upper() == cell} if cell else None
    cache = build_navmeshes(by_type, _Ids(), ctx, export, plugin, cells)[0]
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


def _folder(export, geom):
    """Where saved builds go: beside the navmesh geometry cache, never in it (everything there is a
    published cache entry); builds an older cellview left inside it are deleted."""
    if not geom:
        return export
    for old in glob.glob(os.path.join(geom[0], 'cellview_*.pkl')):
        os.remove(old)
    folder = os.path.join(os.path.dirname(geom[0]), 'cellview_builds')
    os.makedirs(folder, exist_ok=True)
    return folder


def _key(plugin, export, grid_of):
    """(folder, key) of a saved build: the generator's code tag, the export's files, the edge pins."""
    geom = pool.navmesh_geom_cache(pool.collision_cache_chain(export)[-1])
    h = hashlib.sha1(repr((SAVE_FORMAT, geom[1] if geom else '')).encode())
    h.update(_edge_pins(plugin, grid_of).encode())
    for entry in sorted(os.scandir(export), key=lambda e: e.name):
        if entry.name.endswith('.txt'):
            st = entry.stat()
            h.update(repr((entry.name, st.st_size, st.st_mtime_ns)).encode())
    return _folder(export, geom), h.hexdigest()


def _saved(path, key):
    """The build saved at `path` under `key`, or None; a stale build's meshes are never read."""
    try:
        with open(path, 'rb') as fh:
            return pickle.load(fh) if pickle.load(fh) == key else None
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ImportError):
        return None


def _save(path, key, meshes):
    """Write one build to `path`, its key first, atomically."""
    with open(path + '.tmp', 'wb') as fh:
        pickle.dump(key, fh, pickle.HIGHEST_PROTOCOL)
        pickle.dump(meshes, fh, pickle.HIGHEST_PROTOCOL)
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


def _worker(plugin, key):
    """The running interior worker for `plugin` at `key`; a stale or dead one is replaced."""
    got = _WORKERS.get(plugin)
    if got and got[0] == key and got[1].poll() is None:
        return got[1]
    if got:
        got[1].kill()
    proc = subprocess.Popen([sys.executable, '-u', os.path.abspath(__file__), '--serve', plugin],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors='replace', **POPEN_FLAGS)
    _WORKERS[plugin] = (key, proc)
    return proc


def _ask(proc, cell, path, key):
    """Have a worker build interior `cell` into `path` under `key`; raise with its output if it dies."""
    proc.stdin.write('%s\t%s\t%s\n' % (cell, path, key))
    proc.stdin.flush()
    tail = []
    for line in proc.stdout:
        if line.startswith(DONE):
            return
        tail = (tail + [line])[-TAIL_LINES:]
    raise RuntimeError('navmesh build failed:\n' + ''.join(tail))


def _make(plugin, path, key, cell):
    """Build into `path`: an interior in the plugin's worker, the whole plugin in a process of its own."""
    if not cell:
        _run_build([sys.executable, os.path.abspath(__file__), plugin, path, key])
        return
    with _WORKER_LOCK:
        _ask(_worker(plugin, key), cell, path, key)


def _serve(plugin):
    """Load `plugin` once, then build each interior asked for on stdin as `cell<TAB>path<TAB>key`."""
    state = _records(plugin)
    for line in sys.stdin:
        cell, path, key = line.rstrip('\n').split('\t')
        _save(path, key, _build(plugin, state, cell))
        print(DONE, flush=True)


def _load(folder, plugin, key, cell):
    """One build (`cell` '' for the whole plugin): this session's, else the saved one, else built in its
    own process."""
    if (key, cell) not in _BUILT:
        path = os.path.join(folder, 'cellview_%s%s.pkl' % (plugin, cell and '_' + cell))
        meshes = _saved(path, key)
        if meshes is None:
            _make(plugin, path, key, cell)
            meshes = _saved(path, key)
        _BUILT[(key, cell)] = meshes or {}
    return _BUILT[(key, cell)]


def plugin_meshes(plugin, grid_of, interior=''):
    """{cell export FormID: [(verts, tris)]}: the plugin's build, with the pins that reach a cell's edge:
    the page's pinned box only applies or skips each cell's other pins on top, so it never starts a build.
    `interior` (export FormID text) builds that cell alone unless the whole build is current, since no
    other cell changes an interior.  `grid_of(pin key)` gives an exterior cell's grid, None for an interior."""
    folder, key = _key(plugin, plugins.export_dir(plugin), grid_of)
    if interior and (key, '') not in _BUILT:
        whole = _saved(os.path.join(folder, 'cellview_%s.pkl' % plugin), key)
        if whole is None:
            return _load(folder, plugin, key, interior.upper())
        _BUILT[(key, '')] = whole
    return _load(folder, plugin, key, '')


def cell_mesh(plugin, cell_fid, grid_of, interior):
    """`(verts, tris, door points, ledges)` of every mesh the build made for one cell (export FormID text),
    joined; a ledge is (upper tri, lower tri, drop)."""
    verts, tris, doors, ledges = [], [], [], []
    meshes = plugin_meshes(plugin, grid_of, cell_fid if interior else '')
    for v, t, d, lk, _flood in meshes.get(cell_fid.upper(), ()):
        base = len(tris)
        tris += [tuple(int(i) + len(verts) for i in row) for row in t]
        verts += [tuple(float(c) for c in p) for p in v]
        doors += d
        ledges += [(hi + base, lo + base, drop) for hi, lo, drop in lk]
    return verts, tris, doors, ledges


def cell_flood(plugin, cell_fid, grid_of, interior):
    """`(points, edges)` of the graph the build generated for one cell (gap flood or seeding), or None."""
    for *_mesh, flood in plugin_meshes(plugin, grid_of, cell_fid if interior else '').get(cell_fid.upper(), ()):
        if flood:
            return flood
    return None


def main():
    """Run the interior worker (`--serve`), or build a whole plugin and save it."""
    if sys.argv[1] == '--serve':
        _serve(sys.argv[2])
        return 0
    plugin, path, key = sys.argv[1:4]
    _save(path, key, _build(plugin, _records(plugin), ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
