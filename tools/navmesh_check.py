"""CheckNavMesh — port of the Creation Kit's own navmesh validation rules.

The CK ships a `NavMesh::CheckNavMesh` pass (Check NavMeshes / Finalize) that
enumerates exactly what Bethesda considers a malformed navmesh.  Its rule set
was recovered from CreationKit.exe's error strings at .text:0x02142410-0x02142f80
(the exe is unpacked — see docs/ck_navmesh_generation.md), and this module
reimplements every one of them against our WRITTEN NVNM records.

Why validate the written records rather than the generator's in-memory output:
these are structural invariants of the serialised format (edge symmetry, portal
targets, index ranges), and the packing step is where several of them can break.
`tools/navmesh_audit.py` remains the quality metric (coverage, islands, slope);
this is the correctness gate.

Rules, with the CK message each mirrors:

  BAD_VERT_INDEX     "Triangle %d, vert %d has bad Vertex index"
  BAD_TRI_INDEX      "Triangle %d, edge %d has bad Triangle index"
  DEGENERATE         "Triangle %d is degenerate, Vertices A and B both use
                      vertex index %d"          (all three pairs checked)
  DUP_EDGE_TARGET    "Triangle %d Edges A and B both point to the same triangle"
  ASYMMETRIC_EDGE    "Navmesh %08x, Tri %d, Edge %d has mismatched connection"
                     (A names B as a neighbour but B does not name A)
  DOWNFACING         "Triangle %d, has a downfacing normal" (CK flips it)
  OPPOSITE_NORMALS   "Triangle %d and %d have opposite normals but are linked"
  BAD_PORTAL_MESH    "edge %d has bad Portal (no matching navmesh)"
  BAD_PORTAL_TRI     "Navmesh %08x does not have a triangle index %d"
  PORTAL_WORLDSPACE  "has a Portal to a Navmesh (%08x) in a different Worldspace"
  VERT_COUNT         "Navmesh has more vertices than should be possible"
  TRI_COUNT          "NAVMESH: Triangle count is out of bounds"
  DOOR_OFF_MESH      "Finalize NavMesh: Teleport marker for door %s (%08x) in
                      cell %s (%08x) is not sitting on a navmesh"
  NAVI_MISSING       "NavmeshInfo refers to form that does not exist"
                     / "refers to a form that is not a navmesh"
  TRI_COUNT_WARN     uNavmeshTriangleCountWarnThreshold (3500 exterior /
                     5000 interior) — the CK's own audit warning.

Usage:
    python tools/navmesh_check.py output/Oblivion.esm/Oblivion.esm
    python tools/navmesh_check.py <esm> --verbose            # per-defect detail
    python tools/navmesh_check.py <esm> --rule DEGENERATE --verbose
    python tools/navmesh_check.py <esm> --csv report.csv
"""

import argparse
import csv
import math
import struct
import sys
import zlib
from collections import Counter

# NVNM constants — mirror tes5_import/pgrd_to_navm.py.
_PATHING_CELL_CRC = 0xA5E9A03C
_TRI_FLAG_WATER = 0x0200
_TRI_FLAG_DOOR = 0x0400
_TRI_FLAG_FOUND = 0x0800
# Per-edge "this edge is an EDGE LINK" bits, one per edge slot.  When set, that
# edge's S16 field is an INDEX INTO THE EDGE-LINK ARRAY (a portal to a triangle
# in ANOTHER navmesh), NOT a neighbouring triangle index in this mesh.
#
# Every rule that interprets an edge field must consult these bits first.
# Reading an edge-link index as a local triangle index reports essentially every
# cross-cell portal in the game as a defect: it scored vanilla Skyrim.esm at
# 188,785 ASYMMETRIC_EDGE + 3,030 DUP_EDGE_TARGET + 76 BAD_TRI_INDEX, all false.
_TRI_EDGE_LINK = (0x0001, 0x0002, 0x0004)

# CK NavMeshGeneration warn thresholds (recovered defaults).
TRI_WARN_EXTERIOR = 3500
TRI_WARN_INTERIOR = 5000

# The CK stores vertex/triangle indices as S16 in the NVNM triangle struct, so
# anything at or past 0x8000 cannot be addressed — this is what "more vertices
# than should be possible" actually means.
MAX_INDEX = 0x7FFF

# A triangle whose normal tips past this from straight up is "downfacing" in the
# CK's sense (it checks the sign, we allow a hair of slack for float noise).
_DOWNFACE_EPS = 1e-6


# ---------------------------------------------------------------------------
# Record walking (shared shape with tools/navmesh_dump.py)
# ---------------------------------------------------------------------------

def _iter_records(data, start, end, path=()):
    """Yield (sig, formid, body, grup_path) recursing into GRUPs."""
    off = start
    while off + 24 <= end:
        sig = data[off:off + 4]
        size = struct.unpack_from('<I', data, off + 4)[0]
        if sig == b'GRUP':
            label = struct.unpack_from('<I', data, off + 8)[0]
            gtype = struct.unpack_from('<i', data, off + 12)[0]
            grp_end = off + size
            yield from _iter_records(data, off + 24, min(grp_end, end),
                                     path + ((gtype, label),))
            off = grp_end
            continue
        flags = struct.unpack_from('<I', data, off + 8)[0]
        formid = struct.unpack_from('<I', data, off + 12)[0]
        body = data[off + 24:off + 24 + size]
        if flags & 0x00040000:                      # Compressed
            try:
                body = zlib.decompress(body[4:])
            except zlib.error:
                body = b''
        yield sig.decode('latin1'), formid, body, path
        off += 24 + size


def _iter_subrecords(body):
    """Yield (sig, data) honouring the XXXX oversized-subrecord protocol."""
    off = 0
    override = None
    while off + 6 <= len(body):
        sig = body[off:off + 4].decode('latin1')
        size = struct.unpack_from('<H', body, off + 4)[0]
        off += 6
        if sig == 'XXXX':
            override = struct.unpack_from('<I', body, off)[0]
            off += size
            continue
        real = override if override is not None else size
        override = None
        yield sig, body[off:off + real]
        off += real


# ---------------------------------------------------------------------------
# NVNM parsing
# ---------------------------------------------------------------------------

class NavMesh:
    __slots__ = ('formid', 'version', 'worldspace', 'cell', 'grid',
                 'verts', 'tris', 'edge_links', 'door_tris', 'cover_tris',
                 'bbox', 'truncated')

    @property
    def is_exterior(self):
        return self.worldspace != 0


def parse_nvnm(fid, d):
    """Parse an NVNM blob into a NavMesh.  Returns None if unparseable.

    Layout is documented in tes5_import/pgrd_to_navm.py.  Note the EDGE LINK
    stride of 10 bytes (Type U32 + Navmesh FormID U32 + Triangle S16) — a
    12-byte stride silently misparses every navmesh that HAS edge links, which
    is most vanilla exteriors.
    """
    nm = NavMesh()
    nm.formid = fid
    nm.truncated = False
    # Defaults first: __slots__ makes an unset attribute raise, and a blob may
    # end at any point below.
    nm.version = 0
    nm.worldspace = 0
    nm.cell = 0
    nm.grid = None
    nm.verts = []
    nm.tris = []
    nm.edge_links = []
    nm.door_tris = []
    nm.cover_tris = 0
    nm.bbox = None
    try:
        p = 0
        nm.version = struct.unpack_from('<I', d, p)[0]; p += 4
        p += 4                                       # PathingCell CRC
        nm.worldspace = struct.unpack_from('<I', d, p)[0]; p += 4
        if nm.worldspace == 0:
            nm.cell = struct.unpack_from('<I', d, p)[0]; p += 4
            nm.grid = None
        else:
            gy, gx = struct.unpack_from('<hh', d, p); p += 4
            nm.cell = 0
            nm.grid = (gx, gy)

        nv = struct.unpack_from('<I', d, p)[0]; p += 4
        nm.verts = list(struct.unpack_from('<%df' % (nv * 3), d, p))
        p += nv * 12

        nt = struct.unpack_from('<I', d, p)[0]; p += 4
        tris = []
        for _ in range(nt):
            tris.append(struct.unpack_from('<6h2H', d, p))
            p += 16
        nm.tris = tris

        ne = struct.unpack_from('<I', d, p)[0]; p += 4
        links = []
        for _ in range(ne):
            ltype, lmesh = struct.unpack_from('<II', d, p)
            ltri = struct.unpack_from('<h', d, p + 8)[0]
            links.append((ltype, lmesh, ltri))
            p += 10
        nm.edge_links = links

        nd = struct.unpack_from('<I', d, p)[0]; p += 4
        doors = []
        for _ in range(nd):
            dtri = struct.unpack_from('<h', d, p)[0]
            dfid = struct.unpack_from('<I', d, p + 6)[0]
            doors.append((dtri, dfid))
            p += 10
        nm.door_tris = doors

        nc = struct.unpack_from('<I', d, p)[0]; p += 4 + nc * 2
        nm.cover_tris = nc

        p += 4 + 8                              # grid divisor, max X/Y distance
        nm.bbox = struct.unpack_from('<6f', d, p)   # minX minY minZ maxX maxY maxZ
    except struct.error:
        nm.truncated = True
    return nm


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def _normal(v, a, b, c):
    ax, ay, az = v[a * 3], v[a * 3 + 1], v[a * 3 + 2]
    bx, by, bz = v[b * 3], v[b * 3 + 1], v[b * 3 + 2]
    cx, cy, cz = v[c * 3], v[c * 3 + 1], v[c * 3 + 2]
    ux, uy, uz = bx - ax, by - ay, bz - az
    wx, wy, wz = cx - ax, cy - ay, cz - az
    return (uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx)


def check_navmesh(nm, by_formid=None, local_mask=None):
    """Run every CK rule against one navmesh.  Returns [(rule, detail), ...].

    local_mask: set of FormID high bytes this file owns.  Portals to FormIDs
    outside it belong to a master and are not judged (see the portal rules).
    """
    out = []
    if nm.truncated:
        out.append(('TRUNCATED', 'NVNM blob ended mid-structure'))
        return out

    nverts = len(nm.verts) // 3
    ntris = len(nm.tris)

    # "Navmesh has more vertices than should be possible" / triangle count OOB.
    if nverts > MAX_INDEX:
        out.append(('VERT_COUNT', '%d vertices (S16 index max %d)'
                    % (nverts, MAX_INDEX)))
    if ntris > MAX_INDEX:
        out.append(('TRI_COUNT', '%d triangles (S16 index max %d)'
                    % (ntris, MAX_INDEX)))

    for ti, t in enumerate(nm.tris):
        v0, v1, v2, e01, e12, e20, flags, _cover = t
        vs = (v0, v1, v2)
        es = (e01, e12, e20)

        # "Triangle %d, vert %d has bad Vertex index" — CK removes the triangle.
        bad_vert = False
        for vi, v in enumerate(vs):
            if v < 0 or v >= nverts:
                out.append(('BAD_VERT_INDEX',
                            'tri %d vert %d -> %d (nverts %d)'
                            % (ti, vi, v, nverts)))
                bad_vert = True
        # An edge slot flagged as an EDGE LINK indexes the edge-link array, so
        # the local-triangle rules below do not apply to it.
        is_link = tuple(bool(flags & bit) for bit in _TRI_EDGE_LINK)

        # "Triangle %d, edge %d has bad Triangle index" — CK clears the link.
        for ei, e in enumerate(es):
            if is_link[ei]:
                if e < 0 or e >= len(nm.edge_links):
                    out.append(('BAD_EDGE_LINK_INDEX',
                                'tri %d edge %d -> edge-link %d (have %d)'
                                % (ti, ei, e, len(nm.edge_links))))
                continue
            if e < -1 or e >= ntris:
                out.append(('BAD_TRI_INDEX',
                            'tri %d edge %d -> %d (ntris %d)'
                            % (ti, ei, e, ntris)))

        # "Triangle %d is degenerate, Vertices A and B both use vertex index %d"
        for (ia, ib) in ((0, 1), (0, 2), (1, 2)):
            if vs[ia] == vs[ib]:
                out.append(('DEGENERATE',
                            'tri %d verts %d and %d both use index %d'
                            % (ti, ia, ib, vs[ia])))

        # "Triangle %d Edges A and B both point to the same triangle %d"
        # (only meaningful between two LOCAL edges — two edge-link slots may
        # legitimately index unrelated entries that happen to collide.)
        for (ia, ib) in ((0, 1), (0, 2), (1, 2)):
            if is_link[ia] or is_link[ib]:
                continue
            if es[ia] != -1 and es[ia] == es[ib]:
                out.append(('DUP_EDGE_TARGET',
                            'tri %d edges %d and %d both point to tri %d'
                            % (ti, ia, ib, es[ia])))

        # "Tri %d, Edge %d has mismatched connection to Tri %d" — A names B, but
        # B must name A back.  This is the invariant the engine's edge walk
        # depends on; an asymmetric link is a one-way portal.
        for ei, e in enumerate(es):
            if is_link[ei] or e == -1 or e < 0 or e >= ntris:
                continue
            other = nm.tris[e]
            # The neighbour must name us back through one of ITS local edges;
            # a slot of its own that is an edge link cannot be the return path.
            o_link = tuple(bool(other[6] & bit) for bit in _TRI_EDGE_LINK)
            back = [other[3 + k] for k in range(3) if not o_link[k]]
            if ti not in back:
                out.append(('ASYMMETRIC_EDGE',
                            'tri %d edge %d -> tri %d, which does not link back'
                            % (ti, ei, e)))

        if bad_vert:
            continue

        # "Triangle %d, has a downfacing normal, flipping the triangle"
        nx, ny, nz = _normal(nm.verts, v0, v1, v2)
        if nz < -_DOWNFACE_EPS:
            out.append(('DOWNFACING', 'tri %d normal z=%.4f' % (ti, nz)))

        # "Triangle %d and %d have opposite normals but are linked"
        for ei, e in enumerate(es):
            if is_link[ei] or e <= ti or e < 0 or e >= ntris:
                continue                      # each pair once, and skip invalid
            o = nm.tris[e]
            if max(o[0], o[1], o[2]) >= nverts or min(o[0], o[1], o[2]) < 0:
                continue
            ox, oy, oz = _normal(nm.verts, o[0], o[1], o[2])
            la = math.sqrt(nx * nx + ny * ny + nz * nz)
            lb = math.sqrt(ox * ox + oy * oy + oz * oz)
            if la < 1e-9 or lb < 1e-9:
                continue
            if (nx * ox + ny * oy + nz * oz) / (la * lb) < -0.9:
                out.append(('OPPOSITE_NORMALS',
                            'tri %d and tri %d are linked but face opposite'
                            % (ti, e)))

    # Portal rules — an edge link naming another navmesh.
    #
    # Only judge portals whose target lives in THIS file.  A plugin's navmeshes
    # legitimately portal into its masters' (Dawnguard.esm has 3,206 such links
    # into Skyrim.esm), and we cannot resolve those from a single-file scan —
    # reporting them would bury the real defects.  `local_mask` is the set of
    # master-index bytes this file owns.
    if by_formid is not None:
        for (_ltype, lmesh, ltri) in nm.edge_links:
            if lmesh == 0:
                continue
            target = by_formid.get(lmesh)
            if target is None:
                if local_mask is not None and (lmesh >> 24) not in local_mask:
                    continue                   # lives in a master; not ours
                out.append(('BAD_PORTAL_MESH',
                            'edge link -> navmesh %08X which does not exist'
                            % lmesh))
                continue
            if ltri < 0 or ltri >= len(target.tris):
                out.append(('BAD_PORTAL_TRI',
                            'navmesh %08X has no triangle index %d'
                            % (lmesh, ltri)))
            if target.worldspace != nm.worldspace:
                out.append(('PORTAL_WORLDSPACE',
                            'portal to navmesh %08X in worldspace %08X (ours %08X)'
                            % (lmesh, target.worldspace, nm.worldspace)))

    return out


def check_counts(nm):
    """The CK's own audit warning thresholds (not errors)."""
    n = len(nm.tris)
    lim = TRI_WARN_EXTERIOR if nm.is_exterior else TRI_WARN_INTERIOR
    if n > lim:
        return [('TRI_COUNT_WARN', '%d triangles (CK warns above %d for %s)'
                 % (n, lim, 'exterior' if nm.is_exterior else 'interior'))]
    return []


# ---------------------------------------------------------------------------
# Cross-record rules
# ---------------------------------------------------------------------------

def _parse_navi_meshes(body):
    """FormIDs registered by the NAVI's NVMI entries.

    One NVMI subrecord per navmesh; the navmesh FormID is the FIRST field
    (offset 0) — see tes5_import/navi_builder.py for the full layout.  We only
    need that field, so the rest of the entry is not parsed.
    """
    fids = []
    for sig, data in _iter_subrecords(body):
        if sig == 'NVMI' and len(data) >= 4:
            fids.append(struct.unpack_from('<I', data, 0)[0])
    return fids


def check_navi(navi_bodies, by_formid, local_mask=None):
    """NavmeshInfo refers to a form that does not exist / is not a navmesh."""
    out = []
    seen = set()
    for body in navi_bodies:
        for fid in _parse_navi_meshes(body):
            if fid in seen:
                continue
            seen.add(fid)
            if fid not in by_formid:
                # A NAVI override legitimately registers a master's navmeshes.
                if local_mask is not None and (fid >> 24) not in local_mask:
                    continue
                out.append(('NAVI_MISSING',
                            'NavmeshInfo refers to %08X, which is not a navmesh '
                            'in this file' % fid))
    # A navmesh absent from the info map is invisible to the engine's
    # pathfinding (see project_navi_singleton) — but this is a WARNING, not an
    # error, because vanilla tolerates it for isolated meshes: Skyrim.esm ships
    # 15,462 NVMI entries for 15,966 navmeshes, and all 505 unregistered ones
    # are tiny (10-16 triangle) interior meshes with ZERO edge links.  A mesh
    # that connects to something and is still unregistered is the real bug, so
    # only those are reported.
    for fid, nm in by_formid.items():
        if fid in seen:
            continue
        if not nm.edge_links and not nm.door_tris:
            continue
        out.append(('NAVI_UNREGISTERED_WARN',
                    'navmesh %08X has %d edge links / %d door triangles but is '
                    'not listed in any NAVI NVMI'
                    % (fid, len(nm.edge_links), len(nm.door_tris))))
    return out


def check_door_markers(teleport_doors, meshes, local_mask=None):
    """CK: "Teleport marker for door ... is not sitting on a navmesh".

    teleport_doors: {door_fid: cell_fid} every REFR carrying an XTEL.
    meshes:         [NavMesh, ...] every navmesh in the file.

    Expressed as "every teleport door must own a DOOR TRIANGLE", which is what
    the CK's finalize step actually produces when the marker does sit on a mesh.
    Verified against Skyrim.esm: the NVNM Door Triangle arrays name exactly
    1,703 door REFRs, and all 1,703 are teleport doors — the two sets coincide
    completely, so a teleport door with no door triangle is precisely the CK's
    complaint.

    This replaced a geometric "is the marker inside a triangle, within Z
    tolerance" test.  That test cannot be made reliable from record data alone:
    on vanilla it flagged 1,178 of 1,722 markers, with a median vertical gap of
    338u and a p90 of 5,089u — the marker is routinely nowhere near the mesh
    that serves it, because the engine drops the arriving actor onto the mesh
    rather than requiring the marker to be embedded in it.  The door-triangle
    formulation needs no tolerance and is self-validating against vanilla.
    """
    out = []
    linked = set()
    for nm in meshes:
        for (_ti, dfid) in nm.door_tris:
            linked.add(dfid)
    for dfid, cell_fid in sorted(teleport_doors.items()):
        if dfid in linked:
            continue
        # Only judge doors this file owns; a master's door is linked by the
        # master's own navmesh, which we cannot see.
        if local_mask is not None and (dfid >> 24) not in local_mask:
            continue
        out.append(('DOOR_OFF_MESH',
                    'teleport door %08X (cell %08X) has no Door Triangle on any '
                    'navmesh — its teleport marker is not sitting on a navmesh'
                    % (dfid, cell_fid)))
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _local_mask(data, hdr_size):
    """FormID high bytes this file owns: its own index, plus 0x00 for a master.

    A file's own records carry the index equal to its MAST count (the slot it
    occupies at load), so anything below that belongs to a master and cannot be
    judged from a single-file scan.  Skyrim.esm itself has no masters and owns
    0x00.
    """
    n_mast = 0
    for sig, sdata in _iter_subrecords(data[24:24 + hdr_size]):
        if sig == 'MAST':
            n_mast += 1
    # 0xFF is the runtime/injected marker; treat it as ours so nothing is
    # silently skipped in a save-game-like file.
    return {n_mast, 0xFF} if n_mast else {0x00, 0xFF}


def scan(path, want_doors=True):
    """Parse an ESM/ESP -> (meshes, navi_bodies, teleport_doors, local_mask)."""
    with open(path, 'rb') as fh:
        data = fh.read()
    hdr_size = struct.unpack_from('<I', data, 4)[0]
    start = 24 + hdr_size
    mask = _local_mask(data, hdr_size)

    meshes = []
    navi_bodies = []
    teleport_doors = {}            # door REFR fid -> parent cell fid
    # A NAVM lives in a CELL's temporary child group (type 9); the enclosing
    # type-6 group's label is the CELL FormID, so the parent cell is readable
    # from the GRUP path without a second pass.
    for sig, fid, body, gpath in _iter_records(data, start, len(data)):
        if sig == 'NAVM':
            for ssig, sdata in _iter_subrecords(body):
                if ssig == 'NVNM':
                    nm = parse_nvnm(fid, sdata)
                    cell = nm.cell
                    if not cell:
                        for gtype, label in reversed(gpath):
                            if gtype == 6:
                                cell = label
                                break
                    meshes.append((nm, cell))
                    break
        elif sig == 'NAVI':
            navi_bodies.append(body)
        elif sig == 'REFR' and want_doors:
            if any(ssig == 'XTEL' for ssig, _ in _iter_subrecords(body)):
                cell = 0
                for gtype, label in reversed(gpath):
                    if gtype == 6:
                        cell = label
                        break
                teleport_doors[fid] = cell
    return meshes, navi_bodies, teleport_doors, mask


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('esm')
    ap.add_argument('--verbose', action='store_true',
                    help='print every defect, not just the summary')
    ap.add_argument('--rule', help='only report this rule')
    ap.add_argument('--limit', type=int, default=20,
                    help='max detail lines per rule in --verbose (0 = all)')
    ap.add_argument('--csv', help='write per-defect rows to this CSV')
    ap.add_argument('--no-doors', action='store_true',
                    help='skip the door-marker rule (the slowest check)')
    ap.add_argument('--all-portals', action='store_true',
                    help='judge portals into masters too (noisy for plugins)')
    a = ap.parse_args()

    meshes, navi_bodies, teleport_doors, mask = scan(a.esm,
                                                     want_doors=not a.no_doors)
    if not meshes:
        print('no NAVM records found in %s' % a.esm)
        return 0
    if a.all_portals:
        mask = None

    by_formid = {nm.formid: nm for nm, _cell in meshes}
    findings = []                      # (rule, navmesh_fid, cell_fid, detail)
    for nm, cell in meshes:
        for rule, detail in check_navmesh(nm, by_formid, local_mask=mask):
            findings.append((rule, nm.formid, cell, detail))
        for rule, detail in check_counts(nm):
            findings.append((rule, nm.formid, cell, detail))

    for rule, detail in check_navi(navi_bodies, by_formid, local_mask=mask):
        findings.append((rule, 0, 0, detail))

    if not a.no_doors:
        for rule, detail in check_door_markers(
                teleport_doors, [nm for nm, _c in meshes], local_mask=mask):
            findings.append((rule, 0, 0, detail))

    if a.rule:
        findings = [f for f in findings if f[0] == a.rule.upper()]

    tri_total = sum(len(nm.tris) for nm, _ in meshes)
    vert_total = sum(len(nm.verts) // 3 for nm, _ in meshes)
    n_ext = sum(1 for nm, _ in meshes if nm.is_exterior)
    print('%s' % a.esm)
    print('  navmeshes %d (%d exterior, %d interior), %d triangles, %d vertices'
          % (len(meshes), n_ext, len(meshes) - n_ext, tri_total, vert_total))
    print('  edge links %d, door triangles %d'
          % (sum(len(nm.edge_links) for nm, _ in meshes),
             sum(len(nm.door_tris) for nm, _ in meshes)))

    counts = Counter(f[0] for f in findings)
    if not counts:
        print('\n  CheckNavMesh: PASS — no defects')
    else:
        print('\n  CheckNavMesh defects:')
        width = max(len(r) for r in counts)
        for rule, n in counts.most_common():
            affected = len({f[1] for f in findings if f[0] == rule and f[1]})
            print('    %-*s %7d  (%d navmeshes)' % (width, rule, n, affected))

    if a.verbose:
        for rule in counts:
            rows = [f for f in findings if f[0] == rule]
            print('\n  --- %s (%d) ---' % (rule, len(rows)))
            shown = rows if a.limit == 0 else rows[:a.limit]
            for (_r, fid, cell, detail) in shown:
                where = ''
                if fid:
                    where = 'navmesh %08X ' % fid
                    if cell:
                        where += '(cell %08X) ' % cell
                print('    %s%s' % (where, detail))
            if len(rows) > len(shown):
                print('    ... +%d more' % (len(rows) - len(shown)))

    if a.csv:
        with open(a.csv, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['RULE', 'NAVMESH', 'CELL', 'DETAIL'])
            for (rule, fid, cell, detail) in findings:
                w.writerow([rule, '%08X' % fid if fid else '',
                            '%08X' % cell if cell else '', detail])
        print('\n  wrote %s (%d rows)' % (a.csv, len(findings)))

    # Warnings are not failures; structural defects are.
    hard = sum(n for r, n in counts.items() if not r.endswith('_WARN'))
    return 1 if hard else 0


if __name__ == '__main__':
    sys.exit(main())
