"""Render a top-down image of a cell's navmesh conversion for iteration.

Draws, in world (X,Y) space seen from above:
  - LAND height field (exterior) as a grayscale floor colormap
  - placed static/object footprint AABBs (rotation-aware) in translucent red
  - the Oblivion pathgrid: nodes (yellow dots) + edges (thin yellow lines)
  - the generated Skyrim navmesh: triangle fill (green) + wireframe edges
  - door references (blue squares) and inter-cell exit points (cyan)

This is the primary tool for iterating on tes5_import/pgrd_to_navm.py: run it on a
cell, look at whether the mesh covers the floor, hugs the walls, avoids objects,
and links doors, then adjust the converter and re-render.

Usage:
    python tools/navmesh_render.py --export export/Oblivion.esm \
        --cell 00010360 [--out temp/nav_cell.png] [--size 1600]
    python tools/navmesh_render.py --export export/Oblivion.esm \
        --cell ICMarketDistrict --mesh-bounds export/mesh_bounds.json

If --cell is a worldspace exterior grid, pass --grid X Y instead of a cell FormID,
or just pass the exterior CELL FormID (its XCLC gives the grid).
"""

import argparse
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.text_reader import (  # noqa: E402
    parse_export_directory, group_records_by_type, get_int, get_float, get_str,
)
from tes5_import import pgrd_to_navm  # noqa: E402
from tes5_import import mesh_bounds as mb  # noqa: E402


class _FakeWriter:
    """Minimal writer exposing alloc_formid() for the converter."""
    def __init__(self):
        self._n = 0x800

    def alloc_formid(self):
        self._n += 1
        return self._n


# ---------------------------------------------------------------------------
# NVNM decode (read back what the converter produced)
# ---------------------------------------------------------------------------

def _decode_nvnm(navm_bytes):
    """Decompress+decode a NAVM record's NVNM into (verts, tris, flags, bbox)."""
    import struct
    import zlib
    # NAVM record: 24-byte header, compressed payload = U32 size + zlib
    size = struct.unpack_from('<I', navm_bytes, 4)[0]
    payload = navm_bytes[24:24 + size]
    subs = zlib.decompress(payload[4:])
    # walk subrecords to find NVNM (honour XXXX oversized protocol)
    off = 0
    nvnm = None
    override = None
    while off + 6 <= len(subs):
        sig = subs[off:off + 4]
        slen = struct.unpack_from('<H', subs, off + 4)[0]
        off += 6
        if sig == b'XXXX':
            override = struct.unpack_from('<I', subs, off)[0]
            off += slen
            continue
        real = override if override is not None else slen
        override = None
        if sig == b'NVNM':
            nvnm = subs[off:off + real]
            break
        off += real
    if nvnm is None:
        return [], [], [], None

    d = nvnm
    p = 8  # version + crc
    wrld = struct.unpack_from('<I', d, p)[0]
    p += 4
    p += 4 if wrld == 0 else 4  # parent union
    nv = struct.unpack_from('<I', d, p)[0]
    p += 4
    verts = []
    for _ in range(nv):
        verts.append(struct.unpack_from('<fff', d, p))
        p += 12
    nt = struct.unpack_from('<I', d, p)[0]
    p += 4
    tris, flags = [], []
    for _ in range(nt):
        t = struct.unpack_from('<6h2H', d, p)
        tris.append((t[0], t[1], t[2]))
        flags.append(t[6])
        p += 16
    return verts, tris, flags, None


# ---------------------------------------------------------------------------
# Cell resolution
# ---------------------------------------------------------------------------

def _resolve_cell(by_type, cell_arg):
    cells = by_type.get('CELL', [])
    for c in cells:
        if c.get('FormID', '').upper() == cell_arg.upper():
            return c
        if (c.get('EditorID') or '').lower() == cell_arg.lower():
            return c
    return None


def _cell_refrs(by_type, cell_fid):
    out = []
    for r in by_type.get('REFR', []):
        if r.get('ParentCELL', '').upper() == cell_fid.upper():
            out.append(r)
    return out


def _cell_pgrd(by_type, cell_fid):
    for p in by_type.get('PGRD', []):
        if p.get('ParentCELL', '').upper() == cell_fid.upper():
            return p
    return None


def _cell_land(by_type, cell_fid):
    for l in by_type.get('LAND', []):
        if l.get('ParentCELL', '').upper() == cell_fid.upper():
            return l
    return None


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(export_dir, cell_arg, out_path, img_size, mesh_bounds_path):
    types = {'CELL', 'REFR', 'PGRD', 'LAND', 'STAT', 'CONT', 'FURN', 'ACTI',
             'TREE', 'DOOR'}
    recs = parse_export_directory(export_dir, type_filter=types)
    by_type = group_records_by_type(recs)

    # Resolve the bounds cache: try the given path, then the pipeline location
    # (export/<plugin>/mesh_bounds_cache.json).
    bounds_candidates = [mesh_bounds_path,
                         os.path.join(export_dir, 'mesh_bounds_cache.json')]
    for cand in bounds_candidates:
        if cand and os.path.exists(cand):
            mb.load_mesh_bounds(cand)
            break
    # 2D silhouette footprints (ad-hoc and pipeline names).
    fp_candidates = [
        os.path.join(os.path.dirname(mesh_bounds_path or '.'), 'mesh_footprints.json'),
        os.path.join(export_dir, 'mesh_footprints_cache.json'),
    ]
    for cand in fp_candidates:
        if os.path.exists(cand):
            from tes5_import import mesh_footprints as mf
            mf.load_mesh_footprints(cand)
            break

    cell = _resolve_cell(by_type, cell_arg)
    if cell is None:
        print(f"Cell {cell_arg} not found")
        return
    cell_fid = cell['FormID']
    print(f"Cell {cell_fid} ({cell.get('EditorID', '?')})")

    pgrd = _cell_pgrd(by_type, cell_fid)
    if pgrd is None:
        print("  No PGRD for this cell")
        return
    land = _cell_land(by_type, cell_fid)
    refrs = _cell_refrs(by_type, cell_fid)

    # Build the base-model index the real pipeline uses (blocking types only).
    blocking = {'STAT', 'CONT', 'FURN', 'ACTI', 'TREE'}
    base_model_by_fid = {}
    for t in blocking:
        for rec in by_type.get(t, []):
            fid = rec.get('FormID')
            model = get_str(rec, 'Model.MODL') or get_str(rec, 'MODL')
            if fid and model:
                key = 'tes4/' + model.lower().replace('\\', '/').lstrip('/')
                if not key.endswith('.nif'):
                    key += '.nif'
                base_model_by_fid[int(fid, 16) & 0xFFFFFF] = key

    # Read pathgrid nodes/edges for drawing.
    n = get_int(pgrd, 'DATA.PointCount', 0)
    nodes = []
    for i in range(n):
        if pgrd.get(f'Point[{i}].X') is None:
            break
        nodes.append((get_float(pgrd, f'Point[{i}].X'),
                      get_float(pgrd, f'Point[{i}].Y'),
                      get_float(pgrd, f'Point[{i}].Z')))
    pg_edges = []
    for i in range(len(nodes)):
        deg = get_int(pgrd, f'Point[{i}].Connections', 0)
        for j in range(deg):
            tgt = pgrd.get(f'Point[{i}].Edge[{j}]')
            if tgt is None:
                break
            try:
                t = int(tgt)
            except ValueError:
                continue
            if 0 <= t < len(nodes):
                pg_edges.append((i, t))

    door_fids = {int(d['FormID'], 16) & 0xFFFFFF
                 for d in by_type.get('DOOR', []) if d.get('FormID')}

    # Run the converter.
    navm_bytes, meta = pgrd_to_navm.convert_PGRD(
        pgrd, writer=_FakeWriter(), land_rec=land, cell_rec=cell,
        refr_recs=refrs, base_model_by_fid=base_model_by_fid,
        door_fids=door_fids)
    verts, tris, tflags = [], [], []
    if navm_bytes:
        verts, tris, tflags, _ = _decode_nvnm(navm_bytes)
        print(f"  navmesh: {len(verts)} verts, {len(tris)} tris")
    else:
        print("  converter returned no navmesh")

    # ---- Determine world bounds of everything drawn ----
    xs, ys = [], []
    for x, y, _z in nodes:
        xs.append(x); ys.append(y)
    for v in verts:
        xs.append(v[0]); ys.append(v[1])
    is_ext = meta['is_exterior'] if meta else (get_str(pgrd, 'ParentWRLD') not in ('', '00000000'))
    if is_ext and meta:
        gx, gy = meta['grid_x'], meta['grid_y']
        xs += [gx * 4096, (gx + 1) * 4096]
        ys += [gy * 4096, (gy + 1) * 4096]
    if not xs:
        print("  nothing to draw")
        return
    pad = 256.0
    min_x, max_x = min(xs) - pad, max(xs) + pad
    min_y, max_y = min(ys) - pad, max(ys) + pad
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    scale = img_size / max(span_x, span_y)
    W = int(span_x * scale)
    H = int(span_y * scale)

    def to_px(wx, wy):
        # world +Y up -> image y down
        px = (wx - min_x) * scale
        py = H - (wy - min_y) * scale
        return px, py

    img = Image.new('RGB', (W, H), (24, 24, 28))
    draw = ImageDraw.Draw(img, 'RGBA')

    # ---- LAND heightmap (exterior) ----
    if is_ext and land is not None:
        vhgt = get_str(land, 'VHGT')
        if vhgt:
            grid = pgrd_to_navm._decode_vhgt(vhgt)
            if grid:
                origin_x = meta['grid_x'] * 4096 if meta else 0
                origin_y = meta['grid_y'] * 4096 if meta else 0
                zvals = [z for row in grid for z in row]
                zmin, zmax = min(zvals), max(zvals)
                zspan = max(zmax - zmin, 1.0)
                spacing = 128.0
                for r in range(32):
                    for c in range(32):
                        wx0 = origin_x + c * spacing
                        wy0 = origin_y + r * spacing
                        z = grid[r][c]
                        g = int(40 + 150 * (z - zmin) / zspan)
                        p0 = to_px(wx0, wy0)
                        p1 = to_px(wx0 + spacing, wy0 + spacing)
                        draw.rectangle([min(p0[0], p1[0]), min(p0[1], p1[1]),
                                        max(p0[0], p1[0]), max(p0[1], p1[1])],
                                       fill=(g, g, g))

    # ---- Object footprints (shapely polygons) ----
    all_excl = pgrd_to_navm._build_exclusion_zones(refrs, base_model_by_fid)
    nodes_xyz = [(x, y, z) for (x, y, z) in nodes]
    kept_excl, _floors = pgrd_to_navm._classify_footprints(all_excl, nodes_xyz)
    kept_ids = {id(p) for p in kept_excl}
    for poly in all_excl:
        ring = [to_px(x, y) for (x, y) in poly.exterior.coords]
        if len(ring) < 3:
            continue
        if id(poly) in kept_ids:      # real obstacle -> carved
            draw.polygon(ring, outline=(230, 70, 70, 230), fill=(210, 45, 45, 90))
        else:                          # floor/shell -> NOT carved (faint gray)
            draw.polygon(ring, outline=(110, 110, 120, 90))

    # ---- Generated navmesh triangles ----
    for ti, (a, b, c) in enumerate(tris):
        pa = to_px(verts[a][0], verts[a][1])
        pb = to_px(verts[b][0], verts[b][1])
        pc = to_px(verts[c][0], verts[c][1])
        fl = tflags[ti] if ti < len(tflags) else 0
        if fl & 0x0200:      # water
            fill = (40, 90, 200, 110)
        elif fl & 0x0400:    # door
            fill = (200, 160, 40, 130)
        else:
            fill = (40, 180, 90, 90)
        draw.polygon([pa, pb, pc], fill=fill, outline=(20, 120, 60, 200))

    # ---- Pathgrid edges + nodes ----
    for (i, j) in pg_edges:
        pa = to_px(nodes[i][0], nodes[i][1])
        pb = to_px(nodes[j][0], nodes[j][1])
        draw.line([pa, pb], fill=(230, 220, 60, 200), width=1)
    for (x, y, _z) in nodes:
        px, py = to_px(x, y)
        draw.ellipse([px - 3, py - 3, px + 3, py + 3],
                     fill=(255, 240, 80, 255))

    # ---- Doors: teleport (magenta) vs interior-only (cyan) ----
    import math as _m
    for r in refrs:
        is_tp = bool(r.get('XTEL.Door'))
        is_door = is_tp or (r.get('NAME') and _is_door(by_type, r))
        if not is_door:
            continue
        dx, dy = get_float(r, 'PosX'), get_float(r, 'PosY')
        px, py = to_px(dx, dy)
        col = (255, 80, 220, 255) if is_tp else (80, 230, 230, 255)
        draw.rectangle([px - 6, py - 6, px + 6, py + 6], outline=col, width=2)
        # Draw the door facing/threshold line so alignment is visible.
        rot = get_float(r, 'RotZ')
        ux, uy = _m.cos(rot), _m.sin(rot)      # opening axis
        a = to_px(dx - 45 * ux, dy - 45 * uy)
        b = to_px(dx + 45 * ux, dy + 45 * uy)
        draw.line([a, b], fill=col, width=2)

    ic = get_int(pgrd, 'InterCellCount', 0)
    for i in range(ic):
        if pgrd.get(f'InterCell[{i}].X') is None:
            break
        px, py = to_px(get_float(pgrd, f'InterCell[{i}].X'),
                       get_float(pgrd, f'InterCell[{i}].Y'))
        draw.ellipse([px - 4, py - 4, px + 4, py + 4],
                     outline=(60, 230, 230, 255), width=2)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    print(f"  wrote {out_path} ({W}x{H})")


_DOOR_FIDS = None


def _is_door(by_type, refr):
    global _DOOR_FIDS
    if _DOOR_FIDS is None:
        _DOOR_FIDS = {d.get('FormID', '').upper() for d in by_type.get('DOOR', [])}
    name = refr.get('NAME', '')
    return name and name.upper() in _DOOR_FIDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True,
                    help='CELL FormID (8 hex) or EditorID')
    ap.add_argument('--out', default=None)
    ap.add_argument('--size', type=int, default=1600)
    ap.add_argument('--mesh-bounds', default='export/mesh_bounds.json')
    args = ap.parse_args()

    out = args.out or f'temp/nav_{args.cell}.png'
    render(args.export, args.cell, out, args.size, args.mesh_bounds)


if __name__ == '__main__':
    main()
