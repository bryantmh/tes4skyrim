"""Audit navmesh quality across MANY cells at once.

Single-cell rendering only ever proves a bug is fixed in the cell you looked at.
This sweeps a whole batch and reports the defects that actually matter, so a fix
can be judged on the population rather than on one favourite room:

  COVER    % of pathgrid edge length with no navmesh under it.  The pathgrid is
           the only authored ground truth we have, so an uncovered pathgrid line
           is always a generation failure.  This is the headline number.
  ISLANDS  connected components (an NPC cannot cross between them).
  STEEP    triangles too steep to walk that the pathgrid does NOT vouch for.
  FLOOR    pathgrid nodes whose nearest navmesh vertex is far off in Z — the
           "triangles on the ceiling of the room below" bug.

    python tools/navmesh_audit.py --interiors 40
    python tools/navmesh_audit.py --cells AnvilFightersGuild,anvilcastlegreathall
"""

import argparse
import math
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import build, params  # noqa: E402
from tes5_import.pgrd_to_navm import _collect_doors  # noqa: E402
from tes5_import.text_reader import (  # noqa: E402
    parse_export_directory, group_records_by_type, get_float, get_int, get_str,
)

_TYPES = {'CELL', 'REFR', 'PGRD', 'LAND', 'STAT', 'CONT', 'FURN', 'ACTI',
          'TREE', 'DOOR', 'WRLD'}
_BASES = ('STAT', 'CONT', 'FURN', 'ACTI', 'TREE', 'DOOR')


def _model_key(model):
    k = 'tes4/' + model.lower().replace('\\', '/').lstrip('/')
    return k if k.endswith('.nif') else k + '.nif'


def _pgrd_nodes(pgrd):
    nodes, edges = [], []
    n = get_int(pgrd, 'DATA.PointCount', 0)
    for i in range(n):
        if pgrd.get('Point[%d].X' % i) is None:
            break
        nodes.append((get_float(pgrd, 'Point[%d].X' % i),
                      get_float(pgrd, 'Point[%d].Y' % i),
                      get_float(pgrd, 'Point[%d].Z' % i)))
    seen = set()
    for i in range(len(nodes)):
        for j in range(get_int(pgrd, 'Point[%d].Connections' % i, 0)):
            t = pgrd.get('Point[%d].Edge[%d]' % (i, j))
            if t is None:
                break
            try:
                t = int(t)
            except ValueError:
                continue
            if 0 <= t < len(nodes) and t != i:
                k = (min(i, t), max(i, t))
                if k not in seen:
                    seen.add(k)
                    edges.append(k)
    return nodes, edges


def _tri_z(px, py, va, vb, vc):
    x0, y0 = va[0], va[1]
    x1, y1 = vb[0], vb[1]
    x2, y2 = vc[0], vc[1]
    d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(d) < 1e-9:
        return None
    l0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / d
    l1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / d
    l2 = 1.0 - l0 - l1
    if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
        return None
    return l0 * va[2] + l1 * vb[2] + l2 * vc[2]


def audit(verts, tris, nodes, edges):
    """Return (cover_pct_uncovered, broken, steep, wrongfloor, islands, tiny,
    sliver_pct, micro)."""
    if not tris:
        return 100.0, 0, 0, len(nodes), 0, 0, 0.0, 0
    V = np.asarray(verts)

    # --- triangle quality: slivers (bad aspect) and micro-triangles ---------
    # aspect = longest_edge^2 / (4*area): equilateral 0.58, degenerate -> inf.
    A = V[[t[0] for t in tris]]
    B = V[[t[1] for t in tris]]
    C = V[[t[2] for t in tris]]
    e0 = np.linalg.norm(B - A, axis=1)
    e1 = np.linalg.norm(C - B, axis=1)
    e2 = np.linalg.norm(A - C, axis=1)
    longest = np.maximum(e0, np.maximum(e1, e2))
    area = 0.5 * np.linalg.norm(np.cross(B - A, C - A), axis=1)
    ok = area > 1e-9
    aspect = np.full(len(tris), 1e9)
    aspect[ok] = longest[ok] ** 2 / (4.0 * area[ok])
    sliver_pct = 100.0 * float((aspect > 6.0).sum()) / max(1, len(tris))
    micro = int((area < 64.0).sum())

    # --- pathgrid coverage (the headline metric) ---
    #
    # A sample is covered when navmesh exists at its XY anywhere in the EDGE's
    # Z RANGE (each end padded by a step).  The old test compared against the
    # interpolated chord Z, but the generator follows the walked SURFACE, not
    # the chord — on a long cave edge the chord cuts through open air two
    # storeys above the floor the ribbon (correctly) sits on, and the old
    # metric read that as 27% "uncovered" in a perfectly meshed cell.
    tri_pts = [(V[a], V[b], V[c]) for (a, b, c) in tris]
    total = bad = 0
    zpad = params.MAX_CLIMB * 1.5
    for (i, j) in edges:
        a, b = nodes[i], nodes[j]
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(2, int(seg / 32))
        zlo = min(a[2], b[2]) - zpad
        zhi = max(a[2], b[2]) + zpad
        for k in range(steps + 1):
            t = k / steps
            px = a[0] + (b[0] - a[0]) * t
            py = a[1] + (b[1] - a[1]) * t
            total += 1
            ok = False
            for (va, vb, vc) in tri_pts:
                z = _tri_z(px, py, va, vb, vc)
                if z is not None and zlo <= z <= zhi:
                    ok = True
                    break
            if not ok:
                bad += 1
    cover = 100.0 * bad / max(1, total)

    # --- BROKEN: pathgrid edges whose two ends land on DIFFERENT components ---
    #
    # A raw component count is a bad metric and was misleading: a cave with six
    # chambers that this cell's pathgrid never links is legitimately six
    # components, and counting those as failures buries the real bug.  What is
    # always wrong is a pathgrid edge — an authored "an NPC walks from A to B" —
    # whose ends are on pieces of navmesh an NPC cannot actually cross between.
    #
    # A node is anchored to the triangle COVERING it in XY at the nearest height,
    # not to the nearest vertex: triangles are now up to TRI_TARGET_EDGE across,
    # so the nearest vertex to a node standing mid-triangle can easily belong to
    # some other piece (a ledge above), which mis-reported the edge as broken.
    parent = list(range(len(verts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b, c) in tris:
        parent[find(a)] = find(b)
        parent[find(b)] = find(c)
    comp = [find(i) for i in range(len(verts))]

    def comp_at(n):
        best = None            # (|dz|, vertex_index)
        for ti, (va, vb, vc) in enumerate(tri_pts):
            z = _tri_z(n[0], n[1], va, vb, vc)
            if z is None:
                continue
            dz = abs(z - n[2])
            if best is None or dz < best[0]:
                best = (dz, tris[ti][0])
        if best is not None:
            return comp[best[1]]
        d = (V[:, 0] - n[0]) ** 2 + (V[:, 1] - n[1]) ** 2 + (V[:, 2] - n[2]) ** 2
        return comp[int(np.argmin(d))]

    ncomp = {i: comp_at(n) for i, n in enumerate(nodes)}
    broken = sum(1 for (i, j) in edges if ncomp[i] != ncomp[j])

    # --- island census: total components, and TINY ones (< MIN_ISLAND_TRIS).
    # A tiny disconnected group is unusable by an NPC and should never survive
    # pruning; any nonzero count here is generator junk.
    comp_tris = {}
    for (a, _b, _c) in tris:
        comp_tris[comp[a]] = comp_tris.get(comp[a], 0) + 1
    islands = len(comp_tris)
    tiny = sum(1 for n in comp_tris.values() if n < params.MIN_ISLAND_TRIS)

    # --- steep WALL-sized tris (single steps are legitimate) ---
    #
    # A stair riser meshed at CS=16 is steeper than the walkable slope yet is a
    # single step the pathgrid walks; the generator keeps those on purpose.
    # What must never exist is a steep triangle TALLER than any step could be —
    # the cross-floor wall artifact.  Mirror the generator's own invariant.
    cos_lim = math.cos(math.radians(50))
    max_step_span = params.MAX_CLIMB * 2.5
    steep = 0
    for (a, b, c) in tris:
        n = np.cross(V[b] - V[a], V[c] - V[a])
        ln = np.linalg.norm(n)
        if ln < 1e-9 or abs(n[2]) / ln >= cos_lim:
            continue
        zs = (V[a][2], V[b][2], V[c][2])
        if max(zs) - min(zs) > max_step_span:
            steep += 1

    # --- wrong floor: navmesh near a node exists ONLY at the wrong height ---
    wrong = 0
    for (nx, ny, nz) in nodes:
        covered_ok = False
        for (va, vb, vc) in tri_pts:
            z = _tri_z(nx, ny, va, vb, vc)
            if z is not None and abs(z - nz) <= params.MAX_CLIMB * 2:
                covered_ok = True
                break
        if covered_ok:
            continue
        d = (V[:, 0] - nx) ** 2 + (V[:, 1] - ny) ** 2
        near = d < (params.SEED_SNAP * 1.5) ** 2
        if near.any() and np.min(np.abs(V[near, 2] - nz)) > params.MAX_CLIMB * 2:
            wrong += 1

    return cover, broken, steep, wrong, islands, tiny, sliver_pct, micro


_W = {}


def _init_worker(export_dir, base_model):
    """Load the collision cache ONCE per worker, not once per cell."""
    ce.load_collision(os.path.join(export_dir, 'collision_cache.bin'), quiet=True)
    _W['base_model'] = base_model


def _run_cell(job):
    name, refrs, nodes, edges, land, grid_x, grid_y, doors = job
    t0 = time.time()
    verts, tris = build.build_navmesh(
        refrs, _W['base_model'], ce.get_collision, nodes, edges,
        land_rec=land, origin_x=grid_x * 4096.0, origin_y=grid_y * 4096.0,
        doors=doors)
    dt = time.time() - t0
    (cov, broken, steep, wrong, islands, tiny, sliv,
     micro) = audit(verts, tris, nodes, edges)
    return (name, len(tris), cov, broken, steep, wrong, islands, tiny,
            sliv, micro, dt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cells', help='comma-separated EditorIDs/FormIDs')
    ap.add_argument('--interiors', type=int, default=0,
                    help='audit the first N interior cells that have a pathgrid')
    ap.add_argument('--exteriors', type=int, default=0,
                    help='audit the first N exterior cells that have a pathgrid')
    ap.add_argument('--workers', type=int,
                    default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--reindex', action='store_true',
                    help='rebuild the cached export index')
    a = ap.parse_args()

    # Parsing the export is ~78s single-threaded (1.1M records) and dwarfs the
    # actual navmesh work, so the slices we need are cached to disk and reused.
    cache = os.path.join(a.export, 'audit_index3.pkl')
    if os.path.exists(cache) and not a.reindex:
        with open(cache, 'rb') as fh:
            (base_model, refr_by_cell, pgrd_by_cell, land_by_cell,
             door_fids, cells) = pickle.load(fh)
    else:
        t0 = time.time()
        recs = parse_export_directory(a.export, type_filter=_TYPES)
        by_type = group_records_by_type(recs)

        base_model = {}
        for t in _BASES:
            for rec in by_type.get(t, []):
                f = rec.get('FormID')
                m = get_str(rec, 'Model.MODL') or get_str(rec, 'MODL')
                if f and m:
                    base_model[int(f, 16) & 0xFFFFFF] = _model_key(m)

        refr_by_cell = {}
        for r in by_type.get('REFR', []):
            refr_by_cell.setdefault((r.get('ParentCELL') or '').upper(), []).append(r)
        pgrd_by_cell = {(p.get('ParentCELL') or '').upper(): p
                        for p in by_type.get('PGRD', [])}
        land_by_cell = {(ld.get('ParentCELL') or '').upper(): ld
                        for ld in by_type.get('LAND', [])}
        door_fids = {int(d['FormID'], 16) & 0xFFFFFF
                     for d in by_type.get('DOOR', []) if d.get('FormID')}
        cells = by_type.get('CELL', [])

        with open(cache, 'wb') as fh:
            pickle.dump((base_model, refr_by_cell, pgrd_by_cell, land_by_cell,
                         door_fids, cells), fh, pickle.HIGHEST_PROTOCOL)
        print('indexed export in %.0fs -> %s' % (time.time() - t0, cache))

    def _is_exterior(c):
        return bool(c.get('ParentWRLD') and c.get('ParentWRLD') != '00000000')

    if a.cells:
        want = {c.strip().lower() for c in a.cells.split(',')}
        sel = [c for c in cells
               if (c.get('EditorID') or '').lower() in want
               or (c.get('FormID') or '').lower() in want]
    else:
        sel = []
        if a.interiors or not a.exteriors:
            ints = [c for c in cells if not _is_exterior(c)
                    and (c.get('FormID') or '').upper() in pgrd_by_cell]
            sel += ints[:a.interiors or 30]
        if a.exteriors:
            exts = [c for c in cells if _is_exterior(c)
                    and (c.get('FormID') or '').upper() in pgrd_by_cell]
            sel += exts[:a.exteriors]

    jobs = []
    for c in sel:
        fid = c['FormID'].upper()
        pgrd = pgrd_by_cell.get(fid)
        if pgrd is None:
            continue
        nodes, edges = _pgrd_nodes(pgrd)
        if not nodes:
            continue
        land = land_by_cell.get(fid) if _is_exterior(c) else None
        gx = get_int(c, 'XCLC.X', 0) if _is_exterior(c) else 0
        gy = get_int(c, 'XCLC.Y', 0) if _is_exterior(c) else 0
        name = c.get('EditorID') or ''
        if _is_exterior(c) and not name:
            name = 'ext_%d_%d' % (gx, gy)
        refrs = refr_by_cell.get(fid, [])
        doors = [(x, y, z, r, tp)
                 for (x, y, z, r, _f, tp) in _collect_doors(refrs, door_fids)]
        jobs.append(((name or fid)[:34],
                     refrs, nodes, edges, land, gx, gy, doors))

    print('%-34s %6s %7s %7s %6s %6s %5s %5s %6s %6s %6s' %
          ('CELL', 'TRIS', 'UNCOV%', 'BROKEN', 'STEEP', 'FLOOR', 'ISL',
           'TINY', 'SLIV%', 'MICRO', 'SEC'))

    # One cell per worker.  Auditing is embarrassingly parallel and each cell is
    # seconds of CPU, so a serial sweep over a few dozen cells wastes minutes.
    results = []
    with ProcessPoolExecutor(max_workers=a.workers,
                             initializer=_init_worker,
                             initargs=(a.export, base_model)) as ex:
        futs = {ex.submit(_run_cell, j): j[0] for j in jobs}
        for f in as_completed(futs):
            results.append(f.result())

    order = {j[0]: i for i, j in enumerate(jobs)}
    results.sort(key=lambda r: order[r[0]])

    tot_cov = []
    tot_broken = tot_steep = tot_floor = tot_tiny = tot_isl = tot_micro = 0
    tot_dt = 0.0
    tot_sliv = []
    nbad_cells = 0
    for (name, ntris, cov, broken, steep, wrong, islands, tiny, sliv, micro,
         dt) in results:
        print('%-34s %6d %7.1f %7d %6d %6d %5d %5d %6.1f %6d %6.2f'
              % (name, ntris, cov, broken, steep, wrong, islands, tiny,
                 sliv, micro, dt))
        tot_cov.append(cov)
        tot_broken += broken
        nbad_cells += (broken > 0)
        tot_steep += steep
        tot_floor += wrong
        tot_isl += islands
        tot_tiny += tiny
        tot_sliv.append(sliv)
        tot_micro += micro
        tot_dt += dt

    if tot_cov:
        worst_c = sorted(zip(tot_cov, (r[0] for r in results)), reverse=True)[:5]
        worst_b = sorted(((r[3], r[0]) for r in results), reverse=True)[:5]
        print('\n%d cells | mean uncovered %.1f%% | %d broken pgrd edges in '
              '%d cells | %d steep | %d wrong-floor | %d islands (%d tiny) | '
              'mean sliver %.1f%% | %d micro | %.1f cpu-s'
              % (len(tot_cov), sum(tot_cov) / len(tot_cov),
                 tot_broken, nbad_cells, tot_steep, tot_floor,
                 tot_isl, tot_tiny, sum(tot_sliv) / len(tot_sliv), tot_micro,
                 tot_dt))
        print('worst coverage: %s'
              % ', '.join('%s %.0f%%' % (n, c) for (c, n) in worst_c))
        print('worst broken:   %s'
              % ', '.join('%s %d' % (n, b) for (b, n) in worst_b))


if __name__ == '__main__':
    main()
