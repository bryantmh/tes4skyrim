"""Probe cross-cell seam coverage for exterior PGRDs before a full rebuild.

Replays the import's own navmesh jobs for a set of exterior cells (by
worldspace grid coords, or by PGRD FormID), then reports for each:
  * verts / tris produced,
  * how many triangle border edges land ON each of the four cell seams
    (within navm_edge_links.SEAM_BAND of the boundary plane),
  * the InterCell (PGRI) links that were parsed vs dropped.

Then it runs build_edge_links over the built set and reports how many Portal
links were stitched — the number that was ~0 before the InterCell fix.

Usage:
    # by worldspace + grid range (Anvil worldspace 0001C31A around Pinarus):
    python tools/navmesh/seam_probe.py --wrld 0001C31A --gx -49 -46 --gy -9 -6

    # by explicit PGRD FormIDs:
    python tools/navmesh/seam_probe.py --pgrd 00012345 00012346
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.navmesh import from_pgrd as pgrd_to_navm
from tools.navmesh import job_trace
from tes5_import.navmesh.edge_links import build_edge_links, NavMeshView, extract_nvnm, SEAM_BAND

_CELL = 4096.0


def _seam_gaps(view):
    """{'x_lo':d, ...} distance from each cell plane to the nearest vertex.

    A seam with no border edges is either walled off or simply not reached:
    the gap separates the two.  A gap far larger than SEAM_BAND means the mesh
    stops short of the boundary, so no edge can ever land in the band.
    """
    if not view.exterior or len(view.verts) == 0:
        return {}
    gx, gy = view.grid
    xs = [v[0] for v in view.verts]
    ys = [v[1] for v in view.verts]
    return {'x_lo': min(xs) - gx * _CELL, 'x_hi': (gx + 1) * _CELL - max(xs),
            'y_lo': min(ys) - gy * _CELL, 'y_hi': (gy + 1) * _CELL - max(ys)}


def _seam_border_counts(view):
    """{'x_lo':n, 'x_hi':n, 'y_lo':n, 'y_hi':n} border edges on each seam."""
    if not view.exterior:
        return {}
    gx, gy = view.grid
    planes = {
        'x_lo': (0, gx * _CELL), 'x_hi': (0, (gx + 1) * _CELL),
        'y_lo': (1, gy * _CELL), 'y_hi': (1, (gy + 1) * _CELL),
    }
    counts = {k: 0 for k in planes}
    for t in view.tris:
        if t[6] & 0x0008:
            continue
        vids = (t[0], t[1], t[2])
        for slot in range(3):
            if t[3 + slot] != -1 or (t[6] & (1 << slot)):
                continue
            a = view.verts[vids[slot]]
            b = view.verts[vids[(slot + 1) % 3]]
            for name, (axis, coord) in planes.items():
                if (abs(a[axis] - coord) <= SEAM_BAND and
                        abs(b[axis] - coord) <= SEAM_BAND):
                    counts[name] += 1
    return counts


def _parse_args():
    """The probe's command line."""
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--wrld', help='worldspace FormID (hex) to filter PGRDs')
    ap.add_argument('--gx', type=int, nargs=2, help='grid X range (inclusive)')
    ap.add_argument('--gy', type=int, nargs=2, help='grid Y range (inclusive)')
    ap.add_argument('--pgrd', nargs='*', help='explicit PGRD FormIDs (hex)')
    return ap.parse_args()


def _in_range(rng, value):
    """True when `value` is inside the inclusive range, or no range was given."""
    return not rng or rng[0] <= value <= rng[1]


def _select(jobs, args):
    """[(job, gx, gy)] of the import's own exterior jobs matching the filters."""
    want = set(x.upper() for x in (args.pgrd or []))
    wrld = args.wrld.upper() if args.wrld else None
    out = []
    for job in jobs:
        rec, cell = job['pgrd_rec'], job['cell_rec']
        wf = rec.get('ParentWRLD')
        if (wf in (None, '00000000') or cell is None
                or (wrld and (wf or '').upper() != wrld)):
            continue
        gx, gy = int(cell.get('XCLC.X', 0)), int(cell.get('XCLC.Y', 0))
        if want:
            if (rec.get('FormID') or '').upper() not in want:
                continue
        elif not (_in_range(args.gx, gx) and _in_range(args.gy, gy)):
            continue
        out.append((job, gx, gy))
    return out


def _intercell_points(rec, gx, gy):
    """(collected intercell links, the PGRD's declared InterCellCount)."""
    pts = [(pgrd_to_navm.get_float(rec, f'Point[{i}].X'),
            pgrd_to_navm.get_float(rec, f'Point[{i}].Y'),
            pgrd_to_navm.get_float(rec, f'Point[{i}].Z'))
           for i in range(pgrd_to_navm.get_int(rec, 'DATA.PointCount', 0))
           if rec.get(f'Point[{i}].X') is not None]
    return (pgrd_to_navm._collect_intercell(rec, pts, gx * _CELL, gy * _CELL),
            pgrd_to_navm.get_int(rec, 'InterCellCount', 0))


def _build_meshes(selected, base_model_by_fid, door_fids):
    """Convert each selected job, printing its seam profile; returns the cache."""
    navm_cache = {}
    for (job, gx, gy) in selected:
        rec, fid = job['pgrd_rec'], job['navm_fid']
        ic, raw_ic = _intercell_points(rec, gx, gy)
        navm_bytes, meta = pgrd_to_navm.convert_PGRD(
            rec, navm_fid=fid, land_rec=job['land_rec'],
            cell_rec=job['cell_rec'], refr_recs=job['refr_recs'],
            base_model_by_fid=base_model_by_fid, door_fids=door_fids,
            extra_door_refrs=job.get('extra_door_refrs'))
        if not navm_bytes:
            print(f"  grid({gx},{gy}) PGRD {rec.get('FormID')}: NO MESH "
                  f"(intercell {len(ic)}/{raw_ic})")
            continue
        navm_cache[fid] = (navm_bytes, meta)
        blob, _, _ = extract_nvnm(navm_bytes)
        view = NavMeshView(fid, blob)
        gaps = _seam_gaps(view)
        print(f"  grid({gx},{gy}) fid={fid:#x}: {len(view.verts)}v "
              f"{len(view.tris)}t  seam_edges={_seam_border_counts(view)}  "
              f"intercell={len(ic)}/{raw_ic}", flush=True)
        print("      plane gaps: %s  (band=%.0f)"
              % (' '.join('%s=%.0f' % (k, gaps[k]) for k in sorted(gaps)),
                 SEAM_BAND))
    return navm_cache


def _report_connectivity(navm_cache):
    """Union-find over Portal links: one component == mutually reachable.

    The precondition for cross-cell pathing, so a split here is the mesh-level
    form of "the NPC will not walk from one cell into the next".
    """
    parent = {f: f for f in navm_cache}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    grid_of = {}
    link_pairs = set()
    for f, (nb, meta) in navm_cache.items():
        grid_of[f] = (meta.get('grid_x'), meta.get('grid_y'))
        blob, _, _ = extract_nvnm(nb)
        for _typ, other, _tri in NavMeshView(f, blob).links:
            if other in parent:
                ra, rb = find(f), find(other)
                if ra != rb:
                    parent[ra] = rb
                link_pairs.add((f, other))
    reciprocal_ok = all((b, a) in link_pairs for (a, b) in link_pairs)
    comps = {}
    for f in navm_cache:
        comps.setdefault(find(f), []).append(grid_of[f])
    print(f"reciprocal links: {'OK' if reciprocal_ok else 'MISMATCH'}")
    print(f"connected components ({len(comps)}):")
    for _root, grids in sorted(comps.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(grids)} cells: {sorted(grids)}")


def main():
    """Build the selected cells' meshes, stitch them, and report connectivity.

    Jobs come from `job_trace.load_export`, the import's own loader: a plugin's
    masters and its neighbours' overhanging refs decide the geometry.
    See: docs/commentary/tes5_import_navmesh.md#seam-probe-replays-real-jobs
    """
    args = _parse_args()
    im, _by_type, door_fids, base_model_by_fid, jobs = job_trace.load_export(
        args.export, 1)
    job_trace.init_worker_for(args.export, im, door_fids, base_model_by_fid, 1)
    selected = _select(jobs, args)
    print(f"selected {len(selected)} exterior PGRDs")
    navm_cache = _build_meshes(selected, base_model_by_fid, door_fids)
    made = build_edge_links(navm_cache, verbose=True)
    print(f"\nTOTAL portals stitched across the selected set: {made}")
    _report_connectivity(navm_cache)


if __name__ == '__main__':
    main()
