"""Read hand-authored navmeshes out of a shipped ESM, as the answer key.

Bethesda and Beyond Skyrim: Bruma both ship navmesh an artist drew by hand.
Bruma re-creates Cyrodiil architecture, so several of its interiors are near
replicas of Oblivion cells we also convert -- which makes their NVNM the only
ground truth we have for what a GOOD answer looks like, as opposed to merely a
non-broken one.

    from tools.navmesh.authored import load_authored
    verts, tris = load_authored(esm, 'BrumaChapelHall')

`references/Skyrim.esm/NAVM.txt` is NOT a usable source: the dump elides long
hex as `... (18230 bytes total)`.  Read the real ESM.

    python tools/navmesh/authored.py <esm>                  # list cells
    python tools/navmesh/authored.py <esm> --cell BrumaChapelHall

See: docs/commentary/tes5_import_navmesh.md#renderer-colour-contract
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.base.tes5_reader import subrecords, walk
from tools.navmesh.check import parse_nvnm


def cell_editor_ids(path):
    """`{cell FormID: EditorID}` for every CELL in the file that names one."""
    with open(path, 'rb') as fh:
        data = fh.read()
    start = 24 + struct.unpack_from('<I', data, 4)[0]
    out = {}
    for rec, _stack in walk(data, span=(start, len(data))):
        if rec.sig != b'CELL':
            continue
        for tag, sdata in subrecords(rec.body):
            if tag == b'EDID':
                out[rec.form_id] = sdata.rstrip(b'\0').decode('latin1')
                break
    return out


def meshes_by_cell(path):
    """`{cell FormID: [NavMesh]}` for every NAVM in the file."""
    with open(path, 'rb') as fh:
        data = fh.read()
    start = 24 + struct.unpack_from('<I', data, 4)[0]
    out = {}
    for rec, stack in walk(data, span=(start, len(data))):
        if rec.sig != b'NAVM':
            continue
        for tag, sdata in subrecords(rec.body):
            if tag == b'NVNM':
                nm = parse_nvnm(rec.form_id, sdata)
                out.setdefault(nm.cell or stack.cell or 0, []).append(nm)
                break
    return out


#: Bit 10 of an NVNM triangle's Flags: the triangle a door stands on.
TRI_FLAG_DOOR = 0x0400

#: Index of Flags in the `'<6h2H'` triangle; index 7 is COVER flags, not these.
TRI_FLAGS_FIELD = 6


def _as_arrays(meshes):
    """Concatenate NavMesh records into flat `(verts, tris)` render arrays.

    NVNM stores vertices as a flat float run and triangles as `6h2H`: the
    first three shorts are vertex indices, the next three neighbours, and the
    last ushort the flags.
    """
    verts, tris = [], []
    for nm in meshes:
        base = len(verts)
        v = nm.verts
        verts += [(v[i], v[i + 1], v[i + 2]) for i in range(0, len(v), 3)]
        tris += [(t[0] + base, t[1] + base, t[2] + base) for t in nm.tris]
    return verts, tris


def door_triangles(meshes):
    """Indices (into the concatenated tri list) of door-flagged triangles.

    Cross-check any change here against each mesh's `door_tris`, the NVNM
    Doors array, which names the same triangles independently.

    See: docs/commentary/tes5_import_navmesh.md#nvnm-flags-vs-cover-flags
    """
    out, n = [], 0
    for nm in meshes:
        for t in nm.tris:
            if t[TRI_FLAGS_FIELD] & TRI_FLAG_DOOR:
                out.append(n)
            n += 1
    return out


def load_authored_full(path, cell_name):
    """`(verts, tris, door_tri_indices)` for one authored cell."""
    names = cell_editor_ids(path)
    want = str(cell_name).lower()
    fids = [f for f, e in names.items() if e.lower() == want]
    if not fids:
        return [], [], []
    by_cell = meshes_by_cell(path)
    meshes = [m for f in fids for m in by_cell.get(f, [])]
    verts, tris = _as_arrays(meshes)
    return verts, tris, door_triangles(meshes)


def load_authored(path, cell_name):
    """`(verts, tris)` for the authored navmesh of `cell_name`, else `([], [])`.

    Every navmesh in the cell is merged: a large interior is often split across
    several NAVM records, and the plan of the room is the union of them.
    """
    names = cell_editor_ids(path)
    want = str(cell_name).lower()
    fids = [f for f, e in names.items() if e.lower() == want]
    if not fids:
        return [], []
    by_cell = meshes_by_cell(path)
    meshes = [m for f in fids for m in by_cell.get(f, [])]
    return _as_arrays(meshes)


def navm_arrays(path, cell=None):
    """`{NAVM FormID: (verts, tris)}` of the file's navmeshes, only those whose cell's low 24 bits are `cell`
    when given (exterior NAVMs never match)."""
    out = {}
    for meshes in meshes_by_cell(path).values():
        for nm in meshes:
            if nm.tris and (cell is None or (not nm.is_exterior and nm.cell & 0xFFFFFF == cell)):
                out[nm.formid] = _as_arrays([nm])
    return out


def _report(path, cell):
    """Print the authored triangle/vertex counts for one cell or all of them."""
    names = cell_editor_ids(path)
    by_cell = meshes_by_cell(path)
    rows = []
    for fid, meshes in by_cell.items():
        eid = names.get(fid, '%08X' % fid)
        if cell and eid.lower() != cell.lower():
            continue
        nt = sum(len(m.tris) for m in meshes)
        rows.append((nt, eid, len(meshes), sum(len(m.verts) // 3
                                               for m in meshes)))
    for nt, eid, nm, nv in sorted(rows, reverse=True):
        print('%-40s %3d navm %6d verts %6d tris' % (eid, nm, nv, nt))
    print('%d cells with authored navmesh' % len(rows))


def main():
    """CLI: list the authored navmesh inventory of an ESM."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('esm')
    ap.add_argument('--cell', help='only this cell EditorID')
    a = ap.parse_args()
    _report(a.esm, a.cell)
    return 0


if __name__ == '__main__':
    sys.exit(main())
