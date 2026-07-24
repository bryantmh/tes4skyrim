"""Probe cross-cell seam coverage for exterior PGRDs before a full rebuild.

Builds the corridor navmesh for a set of exterior cells (by worldspace grid
coords, or by PGRD FormID) straight from the export, then reports for each:
  * verts / tris produced,
  * how many triangle border edges land ON each of the four cell seams
    (within navm_edge_links.SEAM_BAND of the boundary plane),
  * the InterCell (PGRI) links that were parsed vs dropped.

Then it runs build_edge_links over the built set and reports how many Portal
links were stitched — the number that was ~0 before the InterCell fix.

Usage:
    # by worldspace + grid range (Anvil worldspace 0001C31A around Pinarus):
    python tools/navmesh_seam_probe.py --wrld 0001C31A --gx -49 -46 --gy -9 -6

    # by explicit PGRD FormIDs:
    python tools/navmesh_seam_probe.py --pgrd 00012345 00012346
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.text_reader import (parse_export_directory,
                                      group_records_by_type,
                                      set_formid_index_offset)
from tes5_import import pgrd_to_navm
from tes5_import.navm_edge_links import build_edge_links, NavMeshView, _extract_nvnm, SEAM_BAND

_CELL = 4096.0


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--wrld', help='worldspace FormID (hex) to filter PGRDs')
    ap.add_argument('--gx', type=int, nargs=2, help='grid X range (inclusive)')
    ap.add_argument('--gy', type=int, nargs=2, help='grid Y range (inclusive)')
    ap.add_argument('--pgrd', nargs='*', help='explicit PGRD FormIDs (hex)')
    args = ap.parse_args()

    # Match the real pipeline: exterior meshes are built with formid offset 1
    # (one prepended master, Skyrim.esm).  The probe only needs geometry, so the
    # exact offset does not affect seam edges, but set it so get_formid works.
    set_formid_index_offset(1)
    want_types = {'PGRD', 'CELL', 'LAND', 'REFR'}
    records = parse_export_directory(args.export, type_filter=want_types)
    recs = group_records_by_type(records)
    pgrds = recs.get('PGRD', [])
    cells = {c.get('FormID'): c for c in recs.get('CELL', [])}
    lands = {l.get('ParentCELL'): l for l in recs.get('LAND', [])}
    refr_by_cell = {}
    for r in recs.get('REFR', []):
        refr_by_cell.setdefault(r.get('ParentCELL'), []).append(r)

    want = set(x.upper() for x in (args.pgrd or []))
    wrld = args.wrld.upper() if args.wrld else None

    selected = []
    for rec in pgrds:
        wf = rec.get('ParentWRLD')
        if wf in (None, '00000000'):
            continue
        if wrld and (wf or '').upper() != wrld:
            continue
        cf = rec.get('ParentCELL')
        cell = cells.get(cf)
        gx = int(cell.get('XCLC.X', 0)) if cell else None
        gy = int(cell.get('XCLC.Y', 0)) if cell else None
        if want:
            if (rec.get('FormID') or '').upper() not in want:
                continue
        else:
            if args.gx and not (args.gx[0] <= gx <= args.gx[1]):
                continue
            if args.gy and not (args.gy[0] <= gy <= args.gy[1]):
                continue
        selected.append((rec, cell, cf, gx, gy))

    print(f"selected {len(selected)} exterior PGRDs")
    navm_cache = {}
    fid = 0x01900000
    for (rec, cell, cf, gx, gy) in selected:
        fid += 1
        land = lands.get(cf)
        refrs = refr_by_cell.get(cf, [])
        ic = pgrd_to_navm._collect_intercell(
            rec,
            [(pgrd_to_navm.get_float(rec, f'Point[{i}].X'),
              pgrd_to_navm.get_float(rec, f'Point[{i}].Y'),
              pgrd_to_navm.get_float(rec, f'Point[{i}].Z'))
             for i in range(pgrd_to_navm.get_int(rec, 'DATA.PointCount', 0))
             if rec.get(f'Point[{i}].X') is not None],
            gx * _CELL, gy * _CELL)
        raw_ic = pgrd_to_navm.get_int(rec, 'InterCellCount', 0)
        navm_bytes, meta = pgrd_to_navm.convert_PGRD(
            rec, navm_fid=fid, land_rec=land, cell_rec=cell, refr_recs=refrs)
        if not navm_bytes:
            print(f"  grid({gx},{gy}) PGRD {rec.get('FormID')}: NO MESH "
                  f"(intercell {len(ic)}/{raw_ic})")
            continue
        navm_cache[fid] = (navm_bytes, meta)
        blob, _, _ = _extract_nvnm(navm_bytes)
        view = NavMeshView(fid, blob)
        sb = _seam_border_counts(view)
        print(f"  grid({gx},{gy}) fid={fid:#x}: {len(view.verts)}v "
              f"{len(view.tris)}t  seam_edges={sb}  "
              f"intercell={len(ic)}/{raw_ic}")

    made = build_edge_links(navm_cache, verbose=True)
    print(f"\nTOTAL portals stitched across the selected set: {made}")

    # Connectivity across the stitched set: union-find over mesh FormIDs joined
    # by any Portal link.  One component == every selected cell is mutually
    # reachable at the mesh level (the precondition for cross-cell pathing).
    parent = {f: f for f in navm_cache}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    grid_of = {}
    reciprocal_ok = True
    link_pairs = set()
    for f, (nb, meta) in navm_cache.items():
        grid_of[f] = (meta.get('grid_x'), meta.get('grid_y'))
        blob, _, _ = _extract_nvnm(nb)
        view = NavMeshView(f, blob)
        for typ, other, _tri in view.links:
            if other in parent:
                ra, rb = find(f), find(other)
                if ra != rb:
                    parent[ra] = rb
                link_pairs.add((f, other))
    for (a, b) in link_pairs:
        if (b, a) not in link_pairs:
            reciprocal_ok = False
    comps = {}
    for f in navm_cache:
        comps.setdefault(find(f), []).append(grid_of[f])
    print(f"reciprocal links: {'OK' if reciprocal_ok else 'MISMATCH'}")
    print(f"connected components ({len(comps)}):")
    for root, grids in sorted(comps.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(grids)} cells: {sorted(grids)}")


if __name__ == '__main__':
    main()
