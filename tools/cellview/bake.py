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

from tes5_import.base.navmesh_frozen import centroid, covered_by
from tes5_import.base.navmesh_pins import (
    PARTS, apply_hand_edits, cell_key, hand_edits_for, load as load_pins, reaches_edge, remove_patch,
    save as save_pins,
)
from tes5_import.navmesh import corridor
from tools.cellview import plugins, progress, seams, world
from tools.navmesh.draw import tri_class
from tools.navmesh.meshedit import (
    free_cell_name, is_stale, load_fix, make_entry, rebase_ops, replay,
    result_marks, save_fix,
)
from tools.navmesh.navm_patch import patch as navm_patch
from tools.navmesh.index import master_export_dirs_of, wrld_records
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
            for rec in wrld_records(d):
                if rec.get('EditorID'):
                    out[rec['EditorID'].lower()] = (rec.get('FormID') or
                                                    '').upper()
        _WORLDS[export] = out
    return _WORLDS[export]


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


def pinned_cells(plugin):
    """Sorted names of the cells `plugin`'s pin file holds any correction for."""
    doc = load_pins(plugin)
    return sorted({key for part in PARTS for key, rows in (doc.get(part) or {}).items() if rows},
                  key=str.lower)


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


def generate(plugin, src, lattice, ledges=None, edits=None, doors=None):
    """This cell's `(verts, tris)` with the committed `edits` (None: unpinned); `ledges` and `doors` receive
    its ledge links and Door Triangle indices.

    The cell comes from the import's own navmesh phase (world.cell_mesh) with its doors and ledges; that
    build holds the pins that reach a cell's edge, even unpinned, and any other pin is applied here, as
    `from_pgrd._cell_geometry` applies it.  `lattice` builds the cell alone with the lattice generator.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    raw, door_pts = [], None
    if lattice:
        verts, tris = src.build(ledges_out=raw, lattice=True) if src.has_pathgrid else ([], [])
    else:
        verts, tris, door_pts, raw = world.cell_mesh(plugin, src.fid, pin_grids(plugin))
    if lattice or not reaches_edge(edits or {}, cell_grid(src)):
        verts, tris, raw = apply_hand_edits([tuple(float(c) for c in v[:3]) for v in verts], tris, raw,
                                            edits or {})
    tris = [tuple(int(i) for i in t[:3]) for t in tris]
    if ledges is not None:
        ledges.extend(raw)
    if doors is not None:
        doors.extend(our_doors(src, verts, tris) if door_pts is None else tris_at(verts, tris, door_pts))
    return verts, tris


def tris_at(verts, tris, points):
    """Index of the triangle under each point (inside in plan, nearest in height), points on none skipped."""
    out = []
    for px, py, pz in points:
        best = None
        for i, t in enumerate(tris):
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (verts[k][:3] for k in t)
            d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(d) < 1e-9:
                continue
            l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
            l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
            if min(l1, l2, 1.0 - l1 - l2) < -1e-6:
                continue
            gap = abs(l1 * az + l2 * bz + (1.0 - l1 - l2) * cz - pz)
            if best is None or gap < best[0]:
                best = (gap, i)
        if best is not None and best[1] not in out:
            out.append(best[1])
    return out


#: Plugin -> {lowercased EditorID: exterior grid}, for naming a pin key's cell.
_NAMED_GRIDS = {}


def pin_grids(plugin):
    """`f(pin key) -> (gx, gy) | None`: an exterior cell's grid by its pin key, None for an interior."""
    if plugin not in _NAMED_GRIDS:
        _NAMED_GRIDS[plugin] = {(rec.get('EditorID') or '').lower(): _grid(rec)
                                for rec in index_of(plugin).cells if rec.get('EditorID')}
    names = _NAMED_GRIDS[plugin]

    def grid_of(key):
        """The grid a pin key names."""
        if key.startswith('wrld:'):
            parts = key.split()
            return int(parts[1]), int(parts[2])
        return names.get(key.lower())
    return grid_of


def neighbour_builder(plugin, wfid, tick=None, lattice=False):
    """`f(gx, gy) -> (verts, tris) | None` for seam matching.

    Each neighbour costs a full navmesh generation, so results are cached for
    the session: four cells share their neighbours as you walk a worldspace.
    """
    state = {'n': 0}

    def build(gx, gy):
        """That neighbour's geometry, generated on first request."""
        key = ('nb', plugin, wfid, gx, gy, lattice)
        if key not in CACHE:
            rec = grid_of(plugin, wfid).get((gx, gy))
            ctx = index_of(plugin).cell(rec['FormID']) if rec else None
            CACHE[key] = generate(plugin, ctx, lattice) if ctx is not None else None
        state['n'] += 1
        if tick is not None:
            tick(state['n'])
        return CACHE[key]
    return build


def seams_for(plugin, cell, job='', lattice=False):
    """The seam report for one exterior cell, built on demand.

    Separate from `mesh_bake` because each of the four neighbours costs a full
    navmesh generation: measured, 44s for the cell alone against 178s with its
    seams.  The cell draws first; the seams arrive after.
    """
    ck = ('seams', plugin, cell, lattice)
    if ck in CACHE:
        return CACHE[ck]
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    grid = cell_grid(src)
    wfid = (src.rec.get('ParentWRLD') or '').upper()
    if grid is None or not wfid or not src.has_pathgrid:
        return {'grid': list(grid) if grid else None, 'seams': []}
    verts, tris = generate(plugin, src, lattice)
    total = len(seams.NEIGHBOURS)

    def tick(done):
        """Publish progress as each neighbour finishes."""
        progress.step(job, 0, float(done) / total)

    out = seams.seam_report(verts, tris, grid,
                            neighbour_builder(plugin, wfid, tick, lattice))
    out['neighbours'] = neighbour_meshes(plugin, wfid, grid, lattice)
    CACHE[ck] = out
    return out


def neighbour_meshes(plugin, wfid, grid, lattice=False):
    """Flat triangles of each adjacent cell's mesh, for context.

    They are generated for seam matching anyway, so drawing them costs only
    the transfer -- and a seam is far easier to judge against the mesh it is
    meant to meet.
    """
    out = []
    for (side, dx, dy, _axis) in seams.NEIGHBOURS:
        got = CACHE.get(('nb', plugin, wfid, grid[0] + dx, grid[1] + dy, lattice))
        if not got:
            continue
        nverts, ntris = got
        out.append({'side': side, 'grid': [grid[0] + dx, grid[1] + dy],
                    'tris': flatten([(nverts[a], nverts[b], nverts[c])
                                     for (a, b, c) in ntris])})
    return out


def cell_corrections(plugin, src):
    """`(edits by section, key)` committed for this cell; `({}, '')` when it has no key.

    See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
    """
    grid = cell_grid(src)
    wrld = int(src.rec.get('ParentWRLD') or '0', 16)
    key = cell_key(src.rec, wrld, grid)
    if not key:
        return {}, ''
    return hand_edits_for(plugin, key), key


def live_mesh(plugin, src, lattice, pinned, ledges=None, doors=None):
    """`(verts, tris)` as the page shows it, which the page's ops index."""
    edits = cell_corrections(plugin, src)[0] if pinned else None
    return generate(plugin, src, lattice, ledges, edits, doors)


def edit_basis(plugin, cell, src, payload, lattice=False, pinned=True):
    """`(verts, tris, doors, links, ops)` the page's edits apply to, or an error string.

    On "Current mesh" that is the live mesh and the session's ops.  On "Saved
    result" it is the correction's stored base with its own ops followed by
    the session's, rebased: the saved edit is replayed, never re-guessed
    against a generator that may have moved.

    See: docs/commentary/tes5_import_navmesh.md#editing-a-saved-result
    """
    ops = payload.get('ops') or []
    if payload.get('source') != 'saved':
        ledges, doors = [], []
        verts, tris = live_mesh(plugin, src, lattice, pinned, ledges, doors)
        return verts, tris, doors, [(int(a), int(b)) for (a, b, _d) in ledges], ops
    fix = load_fix(plugin, cell)
    if not fix:
        return 'no saved correction for %s' % cell
    verts = [tuple(p) for p in fix['base']['verts']]
    tris = [tuple(t) for t in fix['base']['tris']]
    doors, links = result_marks(verts, tris, fix['ops'], fix['result'])
    return verts, tris, doors, links, rebase_ops(verts, tris, fix['ops'], ops)


def same_triangle_as(tris_pts):
    """`f(pts) -> bool`: is this triangle one of `tris_pts` (centroids within 1u)?

    A frozen corner may snap up to half a unit onto a generated vertex, so
    identity is a near centroid, not equal corners.
    """
    want = {}
    for pts in tris_pts:
        c = centroid(pts)
        want.setdefault((int(c[0] // 1.0), int(c[1] // 1.0)), []).append(c)

    def match(pts):
        """True when `pts` has a stored centroid within 1u."""
        c = centroid(pts)
        bx, by = int(c[0] // 1.0), int(c[1] // 1.0)
        near = [w for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for w in want.get((bx + dx, by + dy), ())]
        return any(sum((a - b) ** 2 for a, b in zip(c, w)) <= 1.0 for w in near)
    return match


def frozen_indices(verts, tris, frozen):
    """Indices of `tris` that are a frozen patch triangle or a stitched piece of one."""
    inside = covered_by(frozen)
    return [i for i, t in enumerate(tris) if inside(centroid([verts[k] for k in t]))]


def flood_json(plugin, src, lattice):
    """`{'nodes', 'edges'}` of the graph the import generated for this cell (gap flood or seeding), else None."""
    got = None if lattice else world.cell_flood(plugin, src.fid, pin_grids(plugin))
    if not got:
        return None
    points, edges = got
    return {'nodes': [[round(float(c), 1) for c in p[:3]] for p in points],
            'edges': [[int(a), int(b)] for a, b in edges]}


def mesh_bake(plugin, cell, job='', pinned=True, lattice=False):
    """Our mesh, its collision and any saved correction, in the CELL's frame.

    Refuses a plugin whose collision cache is missing: the cell would open
    with a pathgrid and no walls at all, which reads as a generation bug.
    `pinned` false rebuilds WITHOUT the committed corrections, which is how
    the page shows what they actually changed.  `lattice` builds with the
    prototype lattice generator.

    See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
    """
    why = plugins.preconditions(plugin)
    if why:
        return {'error': why}
    ck = ('mesh', plugin, cell, bool(pinned), bool(lattice))
    if ck in CACHE:
        return CACHE[ck]
    progress.step(job, 0)
    index_of(plugin)
    progress.step(job, 1)
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    progress.step(job, 2)
    ledges, doors = [], []
    edits, pin_key = cell_corrections(plugin, src)
    verts, tris = generate(plugin, src, lattice, ledges, edits if pinned else None, doors)
    walk, block = src.collision()
    progress.step(job, 3)
    fix = load_fix(plugin, cell)
    out = {
        'plugin': plugin,
        'cell': cell,
        'verts': [[round(float(c), 2) for c in p] for p in verts],
        'tris': [[int(i) for i in t] for t in tris],
        'tri_class': [tri_class(verts, t) for t in tris],
        'doors': doors,
        'ledges': [[int(a), int(b), round(float(d), 1)]
                   for (a, b, d) in ledges],
        'walkable': flatten(walk),
        'blocking': flatten(block),
        'walk_src': source_names(src, walk),
        'block_src': source_names(src, block),
        'nodes': [[round(float(c), 1) for c in p] for p in src.nodes],
        'edges': [list(e) for e in src.edges],
        'flood': flood_json(plugin, src, lattice),
        'edge_pins_kept': not pinned and not lattice and reaches_edge(edits, cell_grid(src)),
        'ops': (fix or {}).get('ops', []),
        'stale': is_stale(fix, verts, tris) if fix else False,
        'saved': (fix or {}).get('result'),
        'grid': cell_grid(src),
        'pinned': bool(pinned),
        'lattice': bool(lattice),
        'pin_key': pin_key,
        'pin_cuts': len(edits.get('cuts', ())),
        'pin_frozen': len(edits.get('frozen', ())),
        'pin_voids': len(edits.get('voids', ())),
        'frozen_tris': (frozen_indices(verts, tris, edits.get('frozen', ()))
                        if pinned else []),
    }
    CACHE[ck] = out
    return out


def mesh_save(plugin, cell, payload, lattice=False, pinned=True):
    """Commit a correction's ops, re-baking `result` from the live mesh.

    `mode` "new" writes a numbered sibling rather than replacing the existing
    correction, so re-editing a cell never destroys the previous reading.
    """
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    basis = edit_basis(plugin, cell, src, payload, lattice, pinned)
    if isinstance(basis, str):
        return {'error': basis}
    verts, tris, doors, links, ops = basis
    target = (free_cell_name(plugin, cell)
              if payload.get('mode') == 'new' else cell)
    entry = make_entry(plugin, target, verts, tris, ops, doors, links)
    path = save_fix(plugin, target, entry)
    _forget(plugin, cell)
    return {'saved': path, 'ops': len(ops), 'cell': target,
            'tris': len(entry['result']['tris'])}


def _forget(plugin, cell):
    """Drop every cached bake of this cell, pinned or not, from either generator."""
    for pinned in (True, False):
        for lattice in (True, False):
            CACHE.pop(('mesh', plugin, cell, pinned, lattice), None)


def mesh_to_esm(plugin, cell, payload, lattice=False, pinned=True):
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
    basis = edit_basis(plugin, cell, src, payload, lattice, pinned)
    if isinstance(basis, str):
        return {'error': basis}
    verts, tris, doors, links, ops = basis
    if not tris:
        return {'error': '%s has no generated navmesh to patch' % cell}
    rv, rt, rd, rl = replay(verts, tris, ops, doors, links)
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


def _points(verts, t):
    """Triangle `t` as three corner positions."""
    return tuple(tuple(float(c) for c in verts[v][:3]) for v in t[:3])


def frozen_patch(verts, tris, ops):
    """`(frozen, voids)` world triangles for one session's edits over the mesh on screen.

    Frozen: every result triangle using a vertex the ops moved, welded or
    made, plus both triangles of every link the ops added.  Voids: every
    on-screen triangle deleted or using such a vertex, plus the linked ones --
    the ground the human took over.  No ops freezes nothing.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    rv, rt, _d, rl = replay(verts, tris, ops)
    hot = touched_verts(ops, len(verts))
    linked = {i for pair in rl for i in pair}
    dead = {int(op['tri']) for op in ops or () if op.get('op') == 'del_tri'}
    frozen = [_points(rv, t) for i, t in enumerate(rt)
              if i in linked or any(v in hot for v in t)]
    voids = [_points(verts, t) for i, t in enumerate(tris)
             if i in dead or any(v in hot for v in t)]
    voids += [_points(rv, rt[i]) for i in sorted(linked)]
    return frozen, voids


def pinned_links(basis, frozen):
    """`[(upper, lower)]` world triangles of every on-screen link inside the frozen patch.

    `basis` is `edit_basis`'s `(verts, tris, doors, links, ops)`.  The result
    REPLACES the cell's pinned links, so a link the page no longer shows is
    unpinned; a generator ledge on unfrozen floor is left to the generator.
    See: docs/commentary/tes5_import_navmesh.md#pinned-drop-links
    """
    verts, tris, doors, links, ops = basis
    rv, rt, _d, rl = replay(verts, tris, ops, doors, links)
    held = set(frozen_indices(rv, rt, frozen))
    return [(_points(rv, rt[u]), _points(rv, rt[d])) for u, d in rl
            if u in held and d in held]


def merge_patches(old_frozen, old_voids, frozen, voids):
    """`(frozen, voids)`: new edits over committed patches, voided frozen dropped."""
    replaced = same_triangle_as(voids)
    keep = [t for t in old_frozen if not replaced(t)]
    return _unique(keep + list(frozen)), _unique(list(old_voids) + list(voids))


def _unique(tris):
    """`tris` with repeats (corners equal at 0.01u) removed, order kept."""
    seen, out = set(), []
    for t in tris:
        k = tuple(sorted(tuple(round(c, 2) for c in p) for p in t))
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def mesh_pin(plugin, cell, payload, lattice=False, pinned=True):
    """Freeze this cell's edited triangles as a patch in the committable pin file.

    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    edits, key = cell_corrections(plugin, src)
    if not key:
        return {'error': 'cannot name %s for the pin file' % cell}
    basis = edit_basis(plugin, cell, src, payload, lattice, pinned)
    if isinstance(basis, str):
        return {'error': basis}
    verts, tris, _doors, _links, ops = basis
    if not tris:
        return {'error': '%s has no generated navmesh to pin' % cell}
    if not ops:
        return {'error': 'no edits to pin in %s' % cell}
    frozen, voids = merge_patches(edits.get('frozen', ()), edits.get('voids', ()),
                                  *frozen_patch(verts, tris, ops))
    path, n = save_pins(plugin, key, frozen=frozen, voids=voids,
                        links=pinned_links(basis, frozen))
    _forget(plugin, cell)
    return {'pinned': path, 'key': key, 'tris': n['frozen'],
            'voids': n['voids'], 'links': n['links'], 'ops': len(ops)}


def mesh_unpin(plugin, cell, payload):
    """Remove the frozen patch at `payload['point']`, or every patch when it names none.

    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    src, why = resolve_cell(plugin, cell)
    if src is None:
        return {'error': why}
    key = cell_corrections(plugin, src)[1]
    if not key:
        return {'error': 'cannot name %s for the pin file' % cell}
    point = payload.get('point')
    path, nf, nv = remove_patch(plugin, key,
                                tuple(float(c) for c in point) if point else None)
    _forget(plugin, cell)
    return {'pinned': path, 'key': key, 'frozen': nf, 'voids': nv}
