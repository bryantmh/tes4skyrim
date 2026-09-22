#!/usr/bin/env python
"""Write one hand-corrected cell navmesh straight into a built output ESM.

A correction in `tests/navmesh_fixed/` is only a scoring target: nothing ships
it, so testing an edit meant a full `--import-only`.  This replaces that cell's
NVNM geometry in `output/<plugin>/` in place, keeping the record's FormID, EDID
and ONAM so NAVI, XNDP and the neighbours' edge links stay valid.

    python tools/navmesh/navm_patch.py --plugin Oblivion.esm --cell ICMarket
    python tools/navmesh/navm_patch.py --plugin Nehrim.esm --cell "wrldnehrim 3 -5"

A cell the importer split into several NAVM records is refused.

See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from output_layout import plugin_esm
from tes5_import.base.tes5_reader import GRP_HDR, REC_HDR, masters, walk
from tes5_import.base.writer import pack_subrecord
from tes5_import.navmesh.edge_links import (
    CELL_SIZE, NavMeshView, border_edges, extract_nvnm, match_seam,
)
from tes5_import.navmesh.from_pgrd import (
    compute_adjacency, pack_navm_record, pack_nvnm,
)
from tes5_import.navmesh.split import Nvnm

#: Triangle flag the water pass sets; carried over from the replaced mesh.
TRI_FLAG_WATER = 0x0200

#: Exterior neighbours sharing a seam, as (dx, dy, axis); axis 0 = constant X.
SEAM_NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 1), (0, -1, 1))


def export_master_count(export):
    """How many TES4 masters `export`'s plugin declares."""
    path = os.path.join(export, '_HEADER.txt')
    if not os.path.isfile(path):
        return 0
    with open(path, encoding='utf-8') as fh:
        return sum(1 for line in fh if line.startswith('Master['))


def index_offset(raw, export):
    """The load-order shift the import applied, read back off the two files.

    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    return len(masters(raw)) - export_master_count(export)


def remap(fid, offset):
    """A TES4 FormID in the output plugin's index space."""
    return (((fid >> 24) + offset) << 24) | (fid & 0x00FFFFFF) if fid else fid


def grid_of(nv):
    """An exterior NVNM's `(x, y)`; its header packs Y first, both signed."""
    gy, gx = struct.unpack('<hh', struct.pack('<I', nv.cell))
    return gx, gy


class Navm:
    """One NAVM record located in the file, with its decoded geometry."""

    __slots__ = ('offset', 'end', 'fid', 'cell', 'wrld', 'grid',
                 'nv', 'prefix', 'suffix')

    def __init__(self, rec, raw, cell, wrld):
        """Decode the record at `rec`, splitting its NVNM from its other subs."""
        blob, self.prefix, self.suffix = extract_nvnm(raw[rec.offset:rec.end])
        self.offset, self.end, self.fid = rec.offset, rec.end, rec.form_id
        self.cell, self.wrld = cell, wrld
        self.nv = Nvnm(blob)
        self.grid = grid_of(self.nv) if wrld else None

    def blob(self):
        """This record's NVNM re-emitted from its decode, geometry unchanged."""
        adj = [tuple(t[3:6]) for t in self.nv.tris]
        return pack_nvnm(self.nv.verts, [tuple(t[:3]) for t in self.nv.tris],
                         adj, [t[6] for t in self.nv.tris], self.nv.wrld,
                         self.nv.cell, *(self.grid or (0, 0)), bool(self.wrld),
                         door_tris=self.nv.doors, navm_fid=self.fid)

    def repack(self, view):
        """This record's bytes carrying `view`'s blob."""
        subs = self.prefix + pack_subrecord('NVNM', view.pack()) + self.suffix
        return pack_navm_record(self.fid, subs)

    def rebuilt(self, verts, tris, tri_flags, doors, ledges):
        """A view over `verts`/`tris` in this record's frame."""
        adj = compute_adjacency(tris)
        gx, gy = self.grid or (0, 0)
        blob = pack_nvnm(verts, tris, adj, tri_flags, self.nv.wrld,
                         self.nv.cell, gx, gy, bool(self.wrld),
                         door_tris=doors, ledges=ledges, navm_fid=self.fid)
        return NavMeshView(self.fid, blob)


def find_navms(raw, cell_fid):
    """Every NAVM under `cell_fid`'s children GRUP, in file order."""
    return [Navm(rec, raw, stack.cell, stack.worldspace)
            for rec, stack in walk(raw, b'NAVM', bodies=())
            if stack.cell == cell_fid]


def exterior_navms(raw, wrld_fid):
    """`{(x, y): Navm}` for one worldspace, so seams can be re-stitched."""
    out = {}
    for rec, stack in walk(raw, b'NAVM', bodies=()):
        if stack.worldspace != wrld_fid:
            continue
        got = Navm(rec, raw, stack.cell, stack.worldspace)
        if got.grid is not None:
            out[got.grid] = got
    return out


def carry_water_flags(old, verts, tris):
    """Water flags for the new triangles, taken from the mesh being replaced.

    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    wet = [max(old.verts[v][2] for v in t[:3]) for t in old.tris
           if t[6] & TRI_FLAG_WATER]
    if not wet:
        return [0] * len(tris)
    water_z = max(wet)
    out = []
    for (a, b, c) in tris:
        cz = (verts[a][2] + verts[b][2] + verts[c][2]) / 3.0
        out.append(TRI_FLAG_WATER if cz < water_z else 0)
    return out


def centroid(verts, tri):
    """One triangle's centroid, as `(x, y, z)`."""
    return tuple(sum(verts[v][k] for v in tri[:3]) / 3.0 for k in range(3))


def carry_doors(old, verts, tris):
    """`[(triangle, door ref FormID)]` re-aimed at the corrected triangles.

    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    if not old.doors or not tris:
        return []
    cents = [centroid(verts, t) for t in tris]
    out = []
    for (ti, fid) in old.doors:
        if not (0 <= ti < len(old.tris)):
            continue
        ox, oy, oz = centroid(old.verts, old.tris[ti])
        best = min(range(len(cents)),
                   key=lambda i: ((cents[i][0] - ox) ** 2 +
                                  (cents[i][1] - oy) ** 2 +
                                  (cents[i][2] - oz) ** 2))
        out.append((best, fid))
    return out


def drop_links_to(view, fid):
    """Remove every Edge Link naming `fid`, renumbering what survives."""
    keep = {}
    links = []
    for i, lk in enumerate(view.links):
        if lk[1] != fid:
            keep[i] = len(links)
            links.append(lk)
    view.links = links
    for t in view.tris:
        for slot in range(3):
            if not t[6] & (1 << slot):
                continue
            moved = keep.get(int(t[3 + slot]))
            if moved is None:
                t[3 + slot] = -1
                t[6] &= ~(1 << slot)
            else:
                t[3 + slot] = moved


def seam_plane(gx, gy, dx, dy, axis):
    """The world coordinate of the plane between a cell and that neighbour."""
    low = (gx if dx >= 0 else gx + dx) if axis == 0 else (
        gy if dy >= 0 else gy + dy)
    return (low + 1) * CELL_SIZE


def restitch(target, ours, neighbours):
    """Re-link a rewritten exterior mesh to its neighbours, both sides.

    Mutates `ours` and each neighbour view; returns the neighbour views that
    changed as `{grid: (Navm, view)}`.

    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    gx, gy = target.grid
    out = {}
    for (dx, dy, axis) in SEAM_NEIGHBOURS:
        nb = neighbours.get((gx + dx, gy + dy))
        if nb is None:
            continue
        theirs = NavMeshView(nb.fid, nb.blob())
        drop_links_to(theirs, target.fid)
        coord = seam_plane(gx, gy, dx, dy, axis)
        for (ea, eb) in match_seam(border_edges(ours, axis, coord),
                                   border_edges(theirs, axis, coord)):
            ours.add_link(ea[0], ea[1], nb.fid, eb[0])
            theirs.add_link(eb[0], eb[1], target.fid, ea[0])
        out[(gx + dx, gy + dy)] = (nb, theirs)
    return out


def enclosing_groups(raw, offset):
    """Offsets of every GRUP header containing `offset`, outermost first.

    Descends only into the GRUP that spans `offset` and stops on reaching it,
    so the scan is one chain of headers rather than a walk of the whole file.
    """
    out = []
    pos = REC_HDR + struct.unpack_from('<I', raw, 4)[0]
    while pos < offset:
        size = struct.unpack_from('<I', raw, pos + 4)[0]
        if raw[pos:pos + 4] != b'GRUP':
            pos += REC_HDR + size
        elif pos < offset < pos + size:
            out.append(pos)
            pos += GRP_HDR
        else:
            pos += size
    return out


def splice(raw, edits):
    """`raw` with each `(offset, end, bytes)` replaced and GRUP sizes fixed.

    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    edits = sorted(edits)
    deltas = {}
    for (start, end, blob) in edits:
        delta = len(blob) - (end - start)
        for group in (enclosing_groups(raw, start) if delta else ()):
            deltas[group] = deltas.get(group, 0) + delta
    out = bytearray(raw)
    for group, delta in deltas.items():
        size = struct.unpack_from('<I', out, group + 4)[0]
        struct.pack_into('<I', out, group + 4, size + delta)
    for (start, end, blob) in reversed(edits):
        out[start:end] = blob
    return bytes(out)


def edits_for(target, verts, tris, ledges, neighbours):
    """Every `(offset, end, bytes)` one corrected mesh implies."""
    flags = carry_water_flags(target.nv, verts, tris)
    doors = carry_doors(target.nv, verts, tris)
    ours = target.rebuilt(verts, tris, flags, doors, ledges)
    changed = restitch(target, ours, neighbours) if target.grid else {}
    out = [(target.offset, target.end, target.repack(ours))]
    out += [(nb.offset, nb.end, nb.repack(view))
            for (nb, view) in changed.values()]
    return out


def patch(plugin, cell_fid, cell_name, result, export=None,
          out_root='output'):
    """Write one correction's `result` mesh into `plugin`'s built ESM.

    Returns `{esm, navm, tris, verts, restitched}`, or `{error}`.
    """
    esm = str(plugin_esm(out_root, plugin, export))
    if not os.path.isfile(esm):
        return {'error': 'no built ESM at %s -- run: python convert.py -f %s '
                         '--import-only' % (esm, plugin)}
    with open(esm, 'rb') as fh:
        raw = fh.read()
    offset = index_offset(raw, export or os.path.join('export', plugin))
    found = find_navms(raw, remap(cell_fid, offset))
    if not found:
        return {'error': '%s has no NAVM in %s -- the cell was not navmeshed '
                         'in this build' % (cell_name, os.path.basename(esm))}
    if len(found) > 1:
        return {'error': '%s was split into %d NAVM records; only python '
                         'convert.py -f %s --import-only can rebuild a split '
                         'cell' % (cell_name, len(found), plugin)}
    target = found[0]
    verts = [tuple(float(c) for c in p) for p in result['verts']]
    tris = [tuple(int(i) for i in t[:3]) for t in result['tris']]
    ledges = [(int(a), int(b), 0.0) for (a, b) in result.get('links') or ()]
    neighbours = (exterior_navms(raw, target.wrld) if target.grid else {})
    edits = edits_for(target, verts, tris, ledges, neighbours)
    with open(esm, 'wb') as fh:
        fh.write(splice(raw, edits))
    return {'esm': esm, 'navm': '%08X' % target.fid, 'tris': len(tris),
            'verts': len(verts), 'restitched': len(edits) - 1}


def main():
    """CLI: patch one cell's saved correction into the built ESM."""
    from tools.cellview.bake import resolve_cell
    from tools.cellview.plugins import export_dir
    from tools.navmesh.meshedit import load_fix
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--cell', required=True)
    ap.add_argument('--out-root', default='output')
    a = ap.parse_args()
    fix = load_fix(a.plugin, a.cell)
    if not fix:
        print('no correction saved for %s / %s' % (a.plugin, a.cell))
        return 1
    src, why = resolve_cell(a.plugin, a.cell)
    if src is None:
        print(why)
        return 1
    out = patch(a.plugin, int(src.fid, 16), a.cell, fix['result'],
                export=export_dir(a.plugin), out_root=a.out_root)
    if out.get('error'):
        print(out['error'])
        return 1
    print('patched NAVM %s in %s: %d tris, %d verts, %d neighbours restitched'
          % (out['navm'], out['esm'], out['tris'], out['verts'],
             out['restitched']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
