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

from tes5_import.base.text_reader import parse_export_file
from tes5_import.navmesh import corridor
from tools.cellview import plugins, progress, seams
from tools.navmesh.draw import tri_class
from tools.navmesh.meshedit import (
    free_cell_name, is_stale, load_fix, make_entry, save_fix,
)
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
    """EditorIDs in `plugin` that have a pathgrid, optionally filtered."""
    idx = index_of(plugin)
    want = prefix.lower()
    out = []
    for rec in idx.cells:
        eid = rec.get('EditorID') or ''
        if eid and want in eid.lower() and idx.pgrd_by_cell.get(rec['FormID']):
            out.append(eid)
    return sorted(out)


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


def mesh_bake(plugin, cell, job=''):
    """Our mesh, its collision and any saved correction, in the CELL's frame.

    Refuses a plugin whose collision cache is missing: the cell would open
    with a pathgrid and no walls at all, which reads as a generation bug.
    """
    why = plugins.preconditions(plugin)
    if why:
        return {'error': why}
    ck = ('mesh', plugin, cell)
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
    verts, tris = (src.build(ledges_out=ledges) if src.has_pathgrid
                   else ([], []))
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
    CACHE.pop(('mesh', plugin, cell), None)
    return {'saved': path, 'ops': len(ops), 'cell': target,
            'tris': len(entry['result']['tris'])}
