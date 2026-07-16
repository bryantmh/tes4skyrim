"""Shared cell-loading helpers for navmesh debugging tools.

Loads a cell's records, collision cache, pathgrid and world-space geometry, so
the render/probe tools all agree on how a cell is assembled.

    python tools/navmesh_probe.py --cell AnvilFightersGuild
"""

import argparse
import os
import pickle
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import voxel, world  # noqa: E402
from tes5_import.text_reader import (  # noqa: E402
    parse_export_directory, group_records_by_type, get_float, get_int, get_str,
)

_TYPES = {'CELL', 'REFR', 'PGRD', 'LAND', 'STAT', 'CONT', 'FURN', 'ACTI',
          'TREE', 'DOOR', 'WRLD'}
_BLOCKING_BASES = ('STAT', 'CONT', 'FURN', 'ACTI', 'TREE', 'DOOR')


def model_key(model):
    k = 'tes4/' + model.lower().replace('\\', '/').lstrip('/')
    if not k.endswith('.nif'):
        k += '.nif'
    return k


def load_by_type(export_dir, reindex=False):
    """Parsed export records grouped by type, CACHED to disk.

    Parsing the export is ~78s single-threaded (1.1M records) and every tool here
    needs it, so a re-render used to cost more in parsing than in rendering.  The
    parsed slices are pickled next to the export and reused.
    """
    cache = os.path.join(export_dir, 'navmesh_index.pkl')
    if os.path.exists(cache) and not reindex:
        with open(cache, 'rb') as fh:
            return pickle.load(fh)

    t0 = time.time()
    recs = parse_export_directory(export_dir, type_filter=_TYPES)
    by_type = group_records_by_type(recs)
    with open(cache, 'wb') as fh:
        pickle.dump(by_type, fh, pickle.HIGHEST_PROTOCOL)
    print('indexed export in %.0fs -> %s' % (time.time() - t0, cache))
    return by_type


def load_cell(export_dir, cell_arg, load_collision=True):
    """Return a dict with cell/refrs/pgrd/land/nodes/edges/base_model."""
    if load_collision:
        ce.load_collision(os.path.join(export_dir, 'collision_cache.bin'),
                          quiet=True)

    by_type = load_by_type(export_dir)

    cell = None
    # "grid:X:Y" (or "grid:X,Y") selects an exterior cell by grid coordinate
    # (Tamriel first).  Colons survive the comma-splitting of --cell lists.
    if cell_arg.lower().startswith('grid:'):
        try:
            gx, gy = (int(v) for v in
                      cell_arg[5:].replace(',', ':').split(':'))
        except ValueError:
            raise SystemExit('bad grid spec %r (want grid:X:Y)' % cell_arg)
        matches = [c for c in by_type.get('CELL', [])
                   if c.get('ParentWRLD') and c.get('ParentWRLD') != '00000000'
                   and get_int(c, 'XCLC.X', 10**9) == gx
                   and get_int(c, 'XCLC.Y', 10**9) == gy]
        matches.sort(key=lambda c: c.get('ParentWRLD') != '0000003C')
        cell = matches[0] if matches else None
    else:
        for c in by_type.get('CELL', []):
            if c.get('FormID', '').upper() == cell_arg.upper():
                cell = c
                break
            if (c.get('EditorID') or '').lower() == cell_arg.lower():
                cell = c
                break
    if cell is None:
        raise SystemExit('cell %s not found' % cell_arg)

    fid = cell['FormID']
    refrs = [r for r in by_type.get('REFR', [])
             if r.get('ParentCELL', '').upper() == fid.upper()]
    pgrd = next((p for p in by_type.get('PGRD', [])
                 if p.get('ParentCELL', '').upper() == fid.upper()), None)
    land = next((l for l in by_type.get('LAND', [])
                 if l.get('ParentCELL', '').upper() == fid.upper()), None)

    base_model = {}
    for t in _BLOCKING_BASES:
        for rec in by_type.get(t, []):
            f = rec.get('FormID')
            m = get_str(rec, 'Model.MODL') or get_str(rec, 'MODL')
            if f and m:
                base_model[int(f, 16) & 0xFFFFFF] = model_key(m)

    nodes, edges = [], []
    if pgrd is not None:
        n = get_int(pgrd, 'DATA.PointCount', 0)
        for i in range(n):
            if pgrd.get('Point[%d].X' % i) is None:
                break
            nodes.append((get_float(pgrd, 'Point[%d].X' % i),
                          get_float(pgrd, 'Point[%d].Y' % i),
                          get_float(pgrd, 'Point[%d].Z' % i)))
        seen = set()
        for i in range(len(nodes)):
            deg = get_int(pgrd, 'Point[%d].Connections' % i, 0)
            for j in range(deg):
                tgt = pgrd.get('Point[%d].Edge[%d]' % (i, j))
                if tgt is None:
                    break
                try:
                    t = int(tgt)
                except ValueError:
                    continue
                if 0 <= t < len(nodes) and t != i:
                    key = (min(i, t), max(i, t))
                    if key not in seen:
                        seen.add(key)
                        edges.append(key)

    door_fids = {int(d['FormID'], 16) & 0xFFFFFF
                 for d in by_type.get('DOOR', []) if d.get('FormID')}

    # Doors as build_navmesh wants them: (x, y, z, rot_z, is_teleport).
    from tes5_import.pgrd_to_navm import _collect_doors
    doors = [(x, y, z, r, tp)
             for (x, y, z, r, _f, tp) in _collect_doors(refrs, door_fids)]

    is_ext = bool(cell.get('ParentWRLD') and
                  cell.get('ParentWRLD') != '00000000')
    grid_x = get_int(cell, 'XCLC.X', 0) if is_ext else 0
    grid_y = get_int(cell, 'XCLC.Y', 0) if is_ext else 0

    return {
        'by_type': by_type, 'cell': cell, 'cell_fid': fid, 'refrs': refrs,
        'pgrd': pgrd, 'land': land, 'nodes': nodes, 'edges': edges,
        'base_model': base_model, 'door_fids': door_fids, 'doors': doors,
        'is_exterior': is_ext, 'grid_x': grid_x, 'grid_y': grid_y,
    }


def cell_geometry(ctx, pad=200.0):
    """(walkable, blocking, bounds) world-space triangles for a loaded cell."""
    ox = ctx['grid_x'] * 4096.0
    oy = ctx['grid_y'] * 4096.0
    walk, block = world.gather_cell_geometry(
        ctx['refrs'], ctx['base_model'], ce.get_collision,
        land_rec=ctx['land'] if ctx['is_exterior'] else None,
        origin_x=ox, origin_y=oy)

    parts = [a for a in (walk, block) if len(a)]
    if not parts:
        return walk, block, None
    allt = np.concatenate(parts, axis=0)
    bounds = (float(allt[:, :, 0].min()) - pad,
              float(allt[:, :, 1].min()) - pad,
              float(allt[:, :, 2].min()) - pad,
              float(allt[:, :, 0].max()) + pad,
              float(allt[:, :, 1].max()) + pad,
              float(allt[:, :, 2].max()) + pad)
    return walk, block, bounds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True)
    a = ap.parse_args()

    ctx = load_cell(a.export, a.cell)
    walk, block, bounds = cell_geometry(ctx)
    print('cell %s (%s)  exterior=%s'
          % (ctx['cell_fid'], ctx['cell'].get('EditorID'), ctx['is_exterior']))
    print('refrs=%d  pathgrid nodes=%d edges=%d'
          % (len(ctx['refrs']), len(ctx['nodes']), len(ctx['edges'])))
    print('collision: walkable=%d blocking=%d' % (len(walk), len(block)))
    if bounds is None:
        print('no geometry')
        return

    hf = voxel.build_heightfield(walk, block, bounds)
    voxel.apply_filters(hf)
    cols = voxel.walkable_columns(hf)
    print('grid %dx%d cs=%.0f  walkable columns=%d'
          % (hf.w, hf.h, hf.cs, len(cols)))

    # How well does the pathgrid land on the voxelized floor?
    hit = miss = 0
    zerr = []
    for (x, y, z) in ctx['nodes']:
        cx = int((x - hf.min_x) / hf.cs)
        cy = int((y - hf.min_y) / hf.cs)
        ws = cols.get((cx, cy))
        if ws:
            hit += 1
            best = min(ws, key=lambda s: abs(s[1] - z))
            zerr.append(best[1] - z)
        else:
            miss += 1
    print('pathgrid nodes on a walkable column: %d   missed: %d' % (hit, miss))
    if zerr:
        zerr.sort()
        print('span-top minus node-z: median %+.1f  min %+.1f  max %+.1f'
              % (zerr[len(zerr) // 2], zerr[0], zerr[-1]))


if __name__ == '__main__':
    main()
