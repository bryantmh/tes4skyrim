"""Geometry for one cell of one export, and the hand-corrections over it.

The mesh editor's half of cellview: our generated triangles, the collision they
were built against, the read-only pathgrid, and any saved correction.  No fit
and no Bruma -- an arbitrary cell has no counterpart to align to, which is what
lets this serve every plugin while the corpus serves seven cells.

See: docs/commentary/tes5_import_navmesh.md#hand-corrected-navmesh-corpus
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.base.navmesh_pins import (
    cell_key, pins_for, save as save_pins, welds_for,
)
from tes5_import.base.text_reader import parse_export_file
from tes5_import.navmesh import corridor
from tools.cellview import plugins, progress, seams
from tools.navmesh.draw import tri_class
from tools.navmesh.meshedit import (
    free_cell_name, is_stale, load_fix, make_entry, replay, save_fix,
)
from tools.navmesh.navm_patch import patch as navm_patch
from tools.navmesh.index import master_export_dirs_of
from tools.navmesh.transplant import index_for

#: Baked geometry per cell, so switching back is instant.
CACHE = {}


#: Plugins whose index this process has already ensured exists.
_READY = set()


def index_of(plugin):
    """The NavIndex for `plugin`, building its export index on first use."""
    if plugin not in _READY:
        plugins.ensure_index(plugin)
        _READY.add(plugin)
    return index_for(plugins.export_dir(plugin))


#: Worldspace EditorID -> FormID, per export; WRLD.txt is tiny (84 recs, 0.01s).
_WORLDS = {}


def worlds(plugin):
    """`{lowercased worldspace EditorID: FormID}`, MASTERS included.

    A child plugin need not declare the worldspace it builds in: measured,
    TR_Mainland.esm has no WRLD record of its own and every one of its
    exteriors sits in a master's `wrldmorrowind`.  Reading only this plugin's
    WRLD.txt therefore resolves nothing at all.

    See: docs/commentary/tes5_import_navmesh.md#cellview-exterior-coordinates
    """
    export = plugins.export_dir(plugin)
    if export not in _WORLDS:
        out = {}
        for d in master_export_dirs_of(export) + [export]:
            for rec in _wrld_records(d):
                if rec.get('EditorID'):
                    out[rec['EditorID'].lower()] = (rec.get('FormID') or
                                                    '').upper()
        _WORLDS[export] = out
    return _WORLDS[export]


def _wrld_records(export):
    """One export's WRLD records, read straight from its WRLD.txt.

    See: docs/commentary/tes5_import_navmesh.md#cellview-open-is-cached
    """
    path = os.path.join(export, 'WRLD.txt')
    return parse_export_file(path) if os.path.isfile(path) else []


def parse_coords(text):
    """`(worldspace, x, y)` from "wrldmorrowind -17 -51", else None.

    Exterior cells have no EditorID, so a grid reference is the only way to
    name one.  Commas are accepted because the CK prints coordinates that way.
    """
    parts = text.replace(',', ' ').split()
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None


#: (plugin, worldspace FormID) -> {(x, y): CELL record}; see grid_of.
_GRIDS = {}


def grid_of(plugin, wfid):
    """`{(x, y): record}` for one worldspace, built once per export.

    Scanning every cell per lookup measured 8.5s on Morrowind_ob.esm (35k
    cells); the map makes a miss instant, which matters because a miss is
    what a mistyped coordinate produces.
    """
    key = (plugin, wfid)
    if key not in _GRIDS:
        out = {}
        for rec in index_of(plugin).cells:
            if (rec.get('ParentWRLD') or '').upper() != wfid:
                continue
            g = _grid(rec)
            if g is not None:
                out[g] = rec
        _GRIDS[key] = out
    return _GRIDS[key]


def find_by_coords(plugin, world, x, y):
    """The exterior cell record at `(x, y)` of `world`, or an error string.

    See: docs/commentary/tes5_import_navmesh.md#cellview-exterior-coordinates
    """
    wfid = worlds(plugin).get(world.lower())
    if wfid is None:
        known = sorted(worlds(plugin))
        return None, 'no worldspace %r in %s -- have: %s' % (
            world, plugin, ', '.join(known) or 'none')
    grid = grid_of(plugin, wfid)
    rec = grid.get((x, y))
    if rec is not None:
        return rec, ''
    if not grid:
        return None, '%s has no exterior cells' % world
    xs = [gx for (gx, _gy) in grid]
    ys = [gy for (_gx, gy) in grid]
    return None, ('no cell at %d %d in %s -- its grid spans X %d..%d, '
                  'Y %d..%d' % (x, y, world, min(xs), max(xs),
                                min(ys), max(ys)))


def _grid(rec):
    """A cell's `(X, Y)` grid position, or None when it is an interior."""
    try:
        return int(rec['XCLC.X']), int(rec['XCLC.Y'])
    except (KeyError, TypeError, ValueError):
        return None


def resolve_cell(plugin, cell):
    """`(CellCtx, '')` for a name, FormID or "<worldspace> X Y", else error.

    See: docs/commentary/tes5_import_navmesh.md#cellview-exterior-coordinates
    """
    coords = parse_coords(cell)
    if coords is not None:
        rec, why = find_by_coords(plugin, *coords)
        if rec is None:
            return None, why
        return index_of(plugin).cell(rec['FormID']), ''
    src = index_of(plugin).cell(cell)
    if src is None:
        return None, ('no cell %r in %s -- an exterior cell has no name, '
                      'use "<worldspace> X Y"' % (cell, plugin))
    return src, ''


def plugin_cells(plugin, prefix=''):
    """EditorIDs in `plugin` that have a pathgrid, optionally filtered.

    Filters by NAME first: matching a 2-character prefix against 25k EditorIDs
    is free, while asking which cells have a pathgrid is a full scan.
    """
    idx = index_of(plugin)
    want = prefix.lower()
    named = [rec for rec in idx.cells
             if (rec.get('EditorID') or '') and want in rec['EditorID'].lower()]
    if not named:
        return []
    with_pgrd = idx.pathgrid_fids()
    return sorted(rec['EditorID'] for rec in named
                  if rec.get('FormID') in with_pgrd)


def flatten(tris):
    """Triangles as a flat [x0,y0,z0,x1,y1,z1,...] list.

    Z ships too: the elevation view draws the same triangles projected onto
    X/Z or Y/Z, so dropping it would mean a second round trip per cell.
    """
    out = []
    for t in tris:
        for p in (t[0], t[1], t[2]):
            out += [round(float(p[0]), 1), round(float(p[1]), 1),
                    round(float(p[2]), 1)]
    return out


def our_doors(cell, verts, tris):
    """Indices of OUR triangles a door threshold stands on.

    Uses production's own `_tri_carries_door`, so what the page marks is what
    the writer would flag -- a second predicate here could disagree silently.
    """
    xy = [(x, y, z) for (x, y, z, _r, _f, _tp, _w) in cell.doors]
    if not xy:
        return []
    return [i for i, t in enumerate(tris)
            if corridor._tri_carries_door(verts, t, xy)]


def _tri_key(tri):
    """Rounded, winding-independent identity of one collision triangle."""
    return tuple(sorted(tuple(round(float(c), 2) for c in p) for p in tri))


def source_names(src, tris):
    """One model path per triangle of `tris`, looked up BY POSITION.

    `_resplit_placed` moves triangles between the walkable and blocking sets,
    so the per-REFR counts do not replay the gathered order; keying on the
    rounded corner triple survives any reordering.  An unclaimed triangle
    (LAND) answers '' and the renderer shows no name.
    """
    if tris is None or not len(tris):
        return []
    owner = {}
    for (model, w, b) in src.collision_sources():
        for part in (w, b):
            if part is None or not len(part):
                continue
            for tri in np.asarray(part).reshape(-1, 3, 3):
                owner.setdefault(_tri_key(tri), model)
    return [owner.get(_tri_key(t), '')
            for t in np.asarray(tris).reshape(-1, 3, 3)]


def cell_grid(src):
    """An exterior cell's `(gx, gy)`, or None for an interior."""
    return _grid(src.rec)


def neighbour_builder(plugin, wfid, tick=None):
    """`f(gx, gy) -> (verts, tris) | None` for seam matching.

    Each neighbour costs a full navmesh generation, so results are cached for
    the session: four cells share their neighbours as you walk a worldspace.
    """
    state = {'n': 0}

    def build(gx, gy):
        """That neighbour's geometry, generated on first request."""
        key = ('nb', plugin, wfid, gx, gy)
        if key not in CACHE:
            rec = grid_of(plugin, wfid).get((gx, gy))
            ctx = index_of(plugin).cell(rec['FormID']) if rec else None
            CACHE[key] = (ctx.build() if ctx is not None and ctx.has_pathgrid
                          else None)
        state['n'] += 1
        if tick is not None:
            tick(state['n'])
        return CACHE[key]
    return build


def seams_for(plugin, cell, job=''):
    """The seam report for one exterior cell, built on demand.

    Separate from `mesh_bake` because each of the four neighbours costs a full
    navmesh generation: measured, 44s for the cell alone against 178s with its
    seams.  The cell draws first; the seams arrive after.
    """
    ck = ('seams', plugin, cell)
    if ck in CACHE:
        return CACHE[ck]
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    grid = cell_grid(src)
    wfid = (src.rec.get('ParentWRLD') or '').upper()
    if grid is None or not wfid or not src.has_pathgrid:
        return {'grid': list(grid) if grid else None, 'seams': []}
    verts, tris = src.build()
    total = len(seams.NEIGHBOURS)

    def tick(done):
        """Publish progress as each neighbour finishes."""
        progress.step(job, 0, float(done) / total)

    out = seams.seam_report(verts, tris, grid,
                            neighbour_builder(plugin, wfid, tick))
    out['neighbours'] = neighbour_meshes(plugin, wfid, grid)
    CACHE[ck] = out
    return out


def neighbour_meshes(plugin, wfid, grid):
    """Flat triangles of each adjacent cell's mesh, for context.

    They are generated for seam matching anyway, so drawing them costs only
    the transfer -- and a seam is far easier to judge against the mesh it is
    meant to meet.
    """
    out = []
    for (side, dx, dy, _axis) in seams.NEIGHBOURS:
        got = CACHE.get(('nb', plugin, wfid, grid[0] + dx, grid[1] + dy))
        if not got:
            continue
        nverts, ntris = got
        out.append({'side': side, 'grid': [grid[0] + dx, grid[1] + dy],
                    'tris': flatten([(nverts[a], nverts[b], nverts[c])
                                     for (a, b, c) in ntris])})
    return out


def cell_corrections(plugin, src, cell):
    """`(pins, welds, key)` committed for this cell, or empty lists.

    See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
    """
    grid = cell_grid(src)
    wrld = int(src.rec.get('ParentWRLD') or '0', 16)
    key = cell_key(src.rec, wrld, grid)
    if not key:
        return [], [], ''
    return pins_for(plugin, key), welds_for(plugin, key), key


def mesh_bake(plugin, cell, job='', pinned=True):
    """Our mesh, its collision and any saved correction, in the CELL's frame.

    Refuses a plugin whose collision cache is missing: the cell would open
    with a pathgrid and no walls at all, which reads as a generation bug.
    `pinned` false rebuilds WITHOUT the committed corrections, which is how
    the page shows what they actually changed.

    See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
    """
    why = plugins.preconditions(plugin)
    if why:
        return {'error': why}
    ck = ('mesh', plugin, cell, bool(pinned))
    if ck in CACHE:
        return CACHE[ck]
    progress.step(job, 0)
    index_of(plugin)
    progress.step(job, 1)
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    progress.step(job, 2)
    ledges = []
    pins, welds, pin_key = cell_corrections(plugin, src, cell)
    verts, tris = (src.build(ledges_out=ledges,
                             pins=pins if pinned else None,
                             welds=welds if pinned else None)
                   if src.has_pathgrid else ([], []))
    walk, block = src.collision()
    progress.step(job, 3)
    fix = load_fix(plugin, cell)
    out = {
        'plugin': plugin,
        'cell': cell,
        'verts': [[round(float(c), 2) for c in p] for p in verts],
        'tris': [[int(i) for i in t] for t in tris],
        'tri_class': [tri_class(verts, t) for t in tris],
        'doors': our_doors(src, verts, tris),
        'ledges': [[int(a), int(b), round(float(d), 1)]
                   for (a, b, d) in ledges],
        'walkable': flatten(walk),
        'blocking': flatten(block),
        'walk_src': source_names(src, walk),
        'block_src': source_names(src, block),
        'nodes': [[round(float(c), 1) for c in p] for p in src.nodes],
        'edges': [list(e) for e in src.edges],
        'ops': (fix or {}).get('ops', []),
        'stale': is_stale(fix, verts, tris) if fix else False,
        'saved': (fix or {}).get('result'),
        'grid': cell_grid(src),
        'pinned': bool(pinned),
        'pin_key': pin_key,
        'pin_tris': len(pins) // 3,
        'pin_welds': len(welds),
    }
    CACHE[ck] = out
    return out


def mesh_save(plugin, cell, payload):
    """Commit a correction's ops, re-baking `result` from the live mesh.

    `mode` "new" writes a numbered sibling rather than replacing the existing
    correction, so re-editing a cell never destroys the previous reading.
    """
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    ledges = []
    verts, tris = (src.build(ledges_out=ledges) if src.has_pathgrid
                   else ([], []))
    ops = payload.get('ops') or []
    target = (free_cell_name(plugin, cell)
              if payload.get('mode') == 'new' else cell)
    entry = make_entry(plugin, target, verts, tris, ops,
                       our_doors(src, verts, tris),
                       [(int(a), int(b)) for (a, b, _d) in ledges])
    path = save_fix(plugin, target, entry)
    for variant in (True, False):
        CACHE.pop(('mesh', plugin, cell, variant), None)
    return {'saved': path, 'ops': len(ops), 'cell': target,
            'tris': len(entry['result']['tris'])}


def mesh_to_esm(plugin, cell, payload):
    """Apply the page's ops to the live mesh and patch it into the built ESM.

    Replays against a FRESH build rather than the saved correction's result, so
    the button ships exactly the mesh on screen -- including edits not yet
    saved, and without a correction's stored base standing in for what the
    generator produces now.

    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    ledges = []
    verts, tris = (src.build(ledges_out=ledges) if src.has_pathgrid
                   else ([], []))
    if not tris:
        return {'error': '%s has no generated navmesh to patch' % cell}
    rv, rt, rd, rl = replay(verts, tris, payload.get('ops') or [],
                            our_doors(src, verts, tris),
                            [(int(a), int(b)) for (a, b, _d) in ledges])
    result = {'verts': rv, 'tris': rt, 'doors': rd, 'links': rl}
    return navm_patch(plugin, int(src.fid, 16), cell, result,
                      export=plugins.export_dir(plugin))


def touched_verts(ops, nbase):
    """Indices of every vertex the ops moved, welded or created.

    A vertex added by build mode has no index until replay appends it, so the
    appended positions are counted forward from the base vertex count.
    """
    out = set()
    added = nbase
    for op in ops or ():
        out.update(_op_verts(op, added))
        if op.get('op') == 'add_vert':
            added += 1
    return out


def _op_verts(op, added):
    """Vertices one op touches; `added` is the index an `add_vert` takes."""
    kind = op.get('op')
    if kind == 'add_vert':
        return (added,)
    if kind == 'move_vert':
        return (int(op['v']),)
    if kind == 'snap_vert':
        return (int(op['v']), int(op['to_v']))
    if kind == 'add_tri':
        return tuple(int(k) for k in op['verts'])
    return ()


def changed_triangles(rv, rt, ops, nbase):
    """Edited result triangles, as corner positions; no ops pins the lot."""
    hot = touched_verts(ops, nbase)
    keep = [t for t in rt if not hot or any(v in hot for v in t)]
    return [tuple(rv[v]) for t in keep for v in t]


def weld_pairs(verts, ops):
    """Each snap_vert as a (from, to) position pair the GENERATOR will have.

    BOTH endpoints come from the pre-replay verts.  Reading either one after
    replay records where the human dragged it, which no fresh build reproduces
    -- measured, a target read post-replay landed 59u from the nearest
    generated vertex and the weld silently never applied.

    See: docs/commentary/tes5_import_navmesh.md#weld-pins
    """
    out = []
    for op in ops or ():
        if op.get('op') != 'snap_vert':
            continue
        i, j = int(op['v']), int(op['to_v'])
        if 0 <= i < len(verts) and 0 <= j < len(verts):
            out.append((tuple(verts[i]), tuple(verts[j])))
    return out


def mesh_pin(plugin, cell, payload):
    """Pin the edited triangles of this cell to the committable pin file.

    See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
    """
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    ledges = []
    verts, tris = (src.build(ledges_out=ledges) if src.has_pathgrid
                   else ([], []))
    if not tris:
        return {'error': '%s has no generated navmesh to pin' % cell}
    ops = payload.get('ops') or []
    rv, rt, _rd, _rl = replay(verts, tris, ops,
                              our_doors(src, verts, tris),
                              [(int(a), int(b)) for (a, b, _d) in ledges])
    pts = changed_triangles(rv, rt, ops, len(verts))
    welds = weld_pairs(verts, ops)
    key = cell_key(src.rec, int(src.rec.get('ParentWRLD') or '0', 16),
                   cell_grid(src))
    if not key:
        return {'error': 'cannot name %s for the pin file' % cell}
    path, n, nw = save_pins(plugin, key, pts, welds)
    for variant in (True, False):
        CACHE.pop(('mesh', plugin, cell, variant), None)
    return {'pinned': path, 'key': key, 'points': n, 'welds': nw,
            'tris': n // 3, 'ops': len(ops)}
