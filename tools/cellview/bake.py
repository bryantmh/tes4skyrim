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

from tes5_import.navmesh import corridor
from tools.cellview import plugins
from tools.navmesh.draw import tri_class
from tools.navmesh.meshedit import (
    free_cell_name, is_stale, load_fix, make_entry, save_fix,
)
from tools.navmesh.transplant import index_for

#: Baked geometry per cell, so switching back is instant.
CACHE = {}


def index_of(plugin):
    """The NavIndex for `plugin`, building its export index on first use."""
    plugins.ensure_index(plugin)
    return index_for(plugins.export_dir(plugin))


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


def mesh_bake(plugin, cell):
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
    src = index_of(plugin).cell(cell)
    if src is None:
        return {'error': 'no cell %r in %s' % (cell, plugin)}
    ledges = []
    verts, tris = (src.build(ledges_out=ledges) if src.has_pathgrid
                   else ([], []))
    walk, block = src.collision()
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
    }
    CACHE[ck] = out
    return out


def mesh_save(plugin, cell, payload):
    """Commit a correction's ops, re-baking `result` from the live mesh.

    `mode` "new" writes a numbered sibling rather than replacing the existing
    correction, so re-editing a cell never destroys the previous reading.
    """
    src = index_of(plugin).cell(cell)
    if src is None:
        return {'error': 'no cell %r in %s' % (cell, plugin)}
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
