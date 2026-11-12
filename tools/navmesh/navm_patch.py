#!/usr/bin/env python
"""Write one hand-corrected cell navmesh straight into a built output ESM.

A correction in `tests/navmesh_fixed/` is only a scoring target: nothing ships
it, so testing an edit meant a full `--import-only`.  This replaces that cell's
NVNM geometry in `output/<plugin>/` in place, keeping the record's FormID, EDID
and ONAM so NAVI, XNDP and the neighbours' edge links stay valid.

    python tools/navmesh/navm_patch.py --plugin Oblivion.esm --cell ICMarket
    python tools/navmesh/navm_patch.py --plugin Nehrim.esm --cell "wrldnehrim 3 -5"

A cell the importer split into several NAVM records is re-split onto those
same records; an edit that changes how the pieces connect is refused.

See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
"""

import argparse
import os
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from scipy.spatial import cKDTree

from core.plugin_masters import masters_from_export_header
from output_layout import plugin_esm
from tes5_import.base.tes5_reader import (
    FLAG_COMPRESSED, GRP_HDR, REC_HDR, SUB_HDR, masters, subrecords, walk,
)
from tes5_import.base.writer import pack_subrecord
from tes5_import.navmesh.edge_links import (
    CELL_SIZE, NavMeshView, border_edges, extract_nvnm, match_seam,
)
from tes5_import.navmesh.from_pgrd import (
    TRI_FLAG_WATER, compute_adjacency, compute_water_flags, pack_navm_record, pack_nvnm,
)
from tes5_import.navmesh.split import Nvnm, components, pack_component_nvnm

#: Exterior neighbours sharing a seam, as (dx, dy, axis); axis 0 = constant X.
SEAM_NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 1), (0, -1, 1))


def export_master_count(export):
    """How many TES4 masters `export`'s plugin declares."""
    return len(masters_from_export_header(export))


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
                 'nv', 'blob', 'prefix', 'suffix')

    def __init__(self, rec, raw, cell, wrld):
        """Decode the record at `rec`, splitting its NVNM (kept as stored, edge links and all) from its other subs."""
        self.blob, self.prefix, self.suffix = extract_nvnm(raw[rec.offset:rec.end])
        self.offset, self.end, self.fid = rec.offset, rec.end, rec.form_id
        self.cell, self.wrld = cell, wrld
        self.nv = Nvnm(self.blob)
        self.grid = grid_of(self.nv) if wrld else None

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
    return compute_water_flags(verts, tris, max(wet))


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
        theirs = NavMeshView(nb.fid, nb.blob)
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
    """`(edits, {door: (NAVM, triangle)})` one corrected mesh implies."""
    flags = carry_water_flags(target.nv, verts, tris)
    doors = carry_doors(target.nv, verts, tris)
    ours = target.rebuilt(verts, tris, flags, doors, ledges)
    changed = restitch(target, ours, neighbours) if target.grid else {}
    out = [(target.offset, target.end, target.repack(ours))]
    out += [(nb.offset, nb.end, nb.repack(view))
            for (nb, view) in changed.values()]
    return out, {fid: (target.fid, ti) for (ti, fid) in doors}


def whole_mesh(found, verts, tris, ledges):
    """The corrected mesh of a split cell as ONE decode, framed as its first record.

    Water flags and doors are carried from every piece it replaces.
    """
    root = found[0]
    flags = [max(f) for f in zip(*(carry_water_flags(n.nv, verts, tris)
                                   for n in found))]
    doors = [d for n in found for d in carry_doors(n.nv, verts, tris)]
    return Nvnm(pack_nvnm(verts, tris, compute_adjacency(tris), flags,
                          root.nv.wrld, root.nv.cell, 0, 0, False,
                          door_tris=doors, ledges=ledges, navm_fid=root.fid))


def piece_of(found, verts, tris):
    """For each corrected triangle, the index in `found` of the record nearest it."""
    owner, cents = [], []
    for r, n in enumerate(found):
        for t in n.nv.tris:
            owner.append(r)
            cents.append(centroid(n.nv.verts, t))
    near = cKDTree(cents).query([centroid(verts, t) for t in tris])[1]
    return [owner[int(i)] for i in near]


def records_of(comp, ncomp, piece, nrec):
    """`[record index per component]` by majority vote, or None unless one-to-one."""
    votes = [Counter() for _ in range(ncomp)]
    for ti, c in enumerate(comp):
        votes[c][piece[ti]] += 1
    out = [v.most_common(1)[0][0] for v in votes]
    return out if sorted(out) == list(range(nrec)) else None


def same_neighbours(rec, door_refs, link_fids):
    """True when a re-cut piece keeps its record's doors and sibling links."""
    old_links = {lk[1] for lk in rec.nv.links} - {rec.fid}
    return (door_refs == sorted({fid for (_t, fid) in rec.nv.doors})
            and set(link_fids) - {rec.fid} == old_links)


def split_edits(found, verts, tris, ledges):
    """`(edits, {door: (NAVM, triangle)})` re-cutting a split cell, or an error string.

    Each component of the corrected mesh replaces the record it lies over, so
    FormIDs, EDIDs, ONAMs and NAVI stay valid.
    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    nv = whole_mesh(found, verts, tris, ledges)
    comp, ncomp = components(nv.tris)
    rec_of = records_of(comp, ncomp, piece_of(found, verts, tris), len(found))
    if rec_of is None:
        return ('the edit left %d separate pieces where the build has %d NAVM '
                'records' % (ncomp, len(found)))
    fid_of_tri = [found[rec_of[c]].fid for c in comp]
    members = [[] for _ in range(ncomp)]
    for ti, c in enumerate(comp):
        members[c].append(ti)
    tri_local = {ti: k for m in members for k, ti in enumerate(m)}
    edits, placed = [], {}
    for c, m in enumerate(members):
        rec = found[rec_of[c]]
        blob, _center, links, refs, door_local = pack_component_nvnm(
            nv, m, tri_local, fid_of_tri, found[0].fid)
        if not same_neighbours(rec, refs, links):
            return ('the edit moved a door or drop-down link between NAVM %08X '
                    'and its siblings' % rec.fid)
        placed.update((fid, (rec.fid, ti)) for fid, ti in door_local.items())
        subs = rec.prefix + pack_subrecord('NVNM', blob) + rec.suffix
        edits.append((rec.offset, rec.end, pack_navm_record(rec.fid, subs)))
    return edits, placed


def xndp_at(raw, rec):
    """Offset of an uncompressed record's XNDP payload in `raw`, or None."""
    if rec.flags & FLAG_COMPRESSED:
        return None
    pos = rec.offset + REC_HDR
    while pos + SUB_HDR <= rec.end:
        tag, size = struct.unpack_from('<4sH', raw, pos)
        if tag == b'XNDP':
            return pos + SUB_HDR
        pos += SUB_HDR + size
    return None


def xndp_edits(raw, placed):
    """`(offset, end, bytes)` re-aiming each door REFR's XNDP at `placed[door]`.

    XNDP names the triangle a door stands on, so renumbering the mesh stales it.
    See: docs/commentary/tes5_import_navmesh.md#patching-a-navmesh-into-a-built-esm
    """
    out = []
    for rec, _stack in walk(raw, b'REFR', bodies=()):
        at = xndp_at(raw, rec) if rec.form_id in placed else None
        if at is not None:
            out.append((at, at + 6, struct.pack('<Ih', *placed[rec.form_id])))
    return out


def reached_meshes(raw, fids):
    """`{NAVM FormID: set of other NAVMs its NVNM edge links reach}` for the records in `fids`."""
    out = {}
    for rec, _stack in walk(raw, b'NAVM', bodies=()):
        if rec.form_id in fids:
            out[rec.form_id] = {lk[1] for lk in Nvnm(extract_nvnm(raw[rec.offset:rec.end])[0]).links} - {rec.form_id}
    return out


def nvmi_relinked(data, reach):
    """One NVMI subrecord body with its Edge Links set to `reach[navmesh]`, unchanged if not listed.

    Layout (tes5_import/navmesh/navi.py): FormID, flags, centroid, preferred %, Edge Link count + FormIDs.
    """
    fid = struct.unpack_from('<I', data, 0)[0]
    if fid not in reach:
        return data
    n = struct.unpack_from('<I', data, 24)[0]
    links = sorted(reach[fid])
    return data[:24] + struct.pack('<I%dI' % len(links), len(links), *links) + data[28 + 4 * n:]


def navi_edits(raw, reach):
    """`(offset, end, bytes)` rewriting the NAVI's NVMI Edge Links of the meshes in `reach` (vanilla rule:
    Edge Links = the distinct meshes the NVNM edge links reach). The record is written uncompressed.
    """
    out = []
    for rec, _stack in walk(raw, b'NAVI'):
        body = b''.join(pack_subrecord(tag.decode('latin1'), nvmi_relinked(data, reach) if tag == b'NVMI' else data)
                        for tag, data in subrecords(rec.body))
        head = bytearray(raw[rec.offset:rec.offset + REC_HDR])
        struct.pack_into('<II', head, 4, len(body), rec.flags & ~FLAG_COMPRESSED)
        out.append((rec.offset, rec.end, bytes(head) + body))
    return out


def patch(plugin, cell_fid, cell_name, result, export=None,
          out_root='output'):
    """Write one correction's `result` mesh into `plugin`'s built ESM.

    `export` is the plugin's record folder; the ESM is found through the export root's source registry.
    Returns `{esm, navm, tris, verts, restitched}`, or `{error}`.
    """
    esm = str(plugin_esm(out_root, plugin, 'export'))
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
    verts = [tuple(float(c) for c in p) for p in result['verts']]
    tris = [tuple(int(i) for i in t[:3]) for t in result['tris']]
    ledges = [(int(a), int(b), 0.0) for (a, b) in result.get('links') or ()]
    got = cell_edits(raw, found, verts, tris, ledges)
    if isinstance(got, str):
        return {'error': '%s: %s -- only python convert.py -f %s --import-only '
                         'can re-split it' % (cell_name, got, plugin)}
    edits, placed = got
    doors = xndp_edits(raw, placed)
    patched = splice(raw, edits + doors)
    rewritten = {struct.unpack_from('<I', raw, start + 12)[0] for start, _end, _blob in edits}
    patched = splice(patched, navi_edits(patched, reached_meshes(patched, rewritten)))
    with open(esm, 'wb') as fh:
        fh.write(patched)
    return {'esm': esm, 'navm': ', '.join('%08X' % n.fid for n in found),
            'tris': len(tris), 'verts': len(verts),
            'restitched': len(edits) - len(found), 'doors': len(doors)}


def cell_edits(raw, found, verts, tris, ledges):
    """`(edits, {door: (NAVM, triangle)})` for one cell's NAVM records, or an error string."""
    if len(found) > 1:
        return split_edits(found, verts, tris, ledges)
    target = found[0]
    neighbours = exterior_navms(raw, target.wrld) if target.grid else {}
    return edits_for(target, verts, tris, ledges, neighbours)


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
