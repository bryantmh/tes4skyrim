"""Split multi-component interior navmeshes into one NAVM per component.

Why this exists
---------------
The engine joins two navmeshes through a teleport door only when the two
sides live in DIFFERENT NAVM records: the high-level pathing graph is built
over navmeshes, door links connect mesh to mesh, and a route WITHIN one mesh
is assumed to be pure triangle adjacency.  When both ends of a teleport-door
pair sit in the same NAVM, the pathfinder asks the local mesh solver for a
route, the components are disconnected, the solve fails, and the actor falls
back to straight-line movement — it walks into the closed door forever.

That is exactly Oblivion's CharacterGen assassins: their holding room
connects to the ambush balcony through the same-cell teleport pair
0004F7A2/0004F795 in ImperialDungeon01, and the whole cell was one NAVM with
9 disconnected components.  XNDP, NVNM Door Triangles and NVMI Door Links
were all present and correct — the door still never became a portal.

Vanilla census (Skyrim.esm): of the 7 same-cell teleport-door pairs, every
one with XNDP on both ends (5/5) has the two ends on two different NAVM
records; there is NO vanilla case of a teleport door pair inside a single
navmesh.  Interior cells routinely carry several NAVMs, one per walkable
area.

What it does
------------
Runs as a deterministic parent-side post-pass over the precomputed navmesh
cache (same pattern as navm_edge_links.build_edge_links), after every mesh
exists and before door XNDP collection / group serialisation:

  * decodes each INTERIOR NVNM, finds connected components over shared-edge
    adjacency (edge-link edges — ledges — do not merge components),
  * one component keeps the original FormID, the rest get fresh FormIDs
    allocated in sorted-cell order so output stays byte-reproducible,
  * triangles/vertices are renumbered per component; ledge Edge Links are
    rewritten to name the component NAVM that owns their target triangle —
    a drop from a balcony to the floor becomes a cross-mesh ledge link,
    which is vanilla's own shape (exterior cell seams do the same),
  * Door Triangles and the meta's door_refs / door_xndp move to the
    component that owns them, so each REFR's XNDP names the right mesh and
    each NVMI lists exactly its own doors,
  * sibling meshes reached via ledge links appear in each other's NVMI Edge
    Links (the NVMI contract: edge links == the distinct neighbour meshes
    named by the mesh's own NVNM edge link array).

Exteriors are left alone: they are one mesh per grid cell stitched by
build_edge_links, and same-cell teleport pairs are an interior idiom.
"""

import struct

from ..base.tes5_reader import REC_HDR, decompress, subrecords
from ..base.writer import pack_subrecord, pack_string_subrecord
from .lookup_grid import build_navmesh_grid
from .from_pgrd import (
    choose_divisor,
    pack_navm_record,
    PATHING_CELL_CRC,
    PATHING_DOOR_CRC,
    NVNM_VERSION,
)

_TRI_EDGE_LINK_BITS = (0x0001, 0x0002, 0x0004)


def _array(blob, p, fmt):
    """`(rows, next offset)` for the count-prefixed array of `fmt` rows at `p`."""
    n = struct.unpack_from('<I', blob, p)[0]
    size = struct.calcsize(fmt)
    p += 4
    return [struct.unpack_from(fmt, blob, p + i * size) for i in range(n)], p + n * size


class Nvnm:
    """Full decode of one of OUR interior NVNM blobs (see pack_nvnm).

    `tris` rows are (v0, v1, v2, e0, e1, e2, flags, cover), `links` rows
    (type, navmesh fid, triangle), `doors` rows (triangle, door ref fid).  The
    cover, bounds and bucket-grid tail is recomputed on re-pack, so not kept.
    """

    def __init__(self, blob):
        """Decode `blob` from past its version and location CRC."""
        self.wrld, self.cell = struct.unpack_from('<II', blob, 8)
        self.verts, p = _array(blob, 16, '<fff')
        tris, p = _array(blob, p, '<6h2H')
        self.tris = [list(t) for t in tris]
        self.links, p = _array(blob, p, '<IIh')
        doors, _end = _array(blob, p, '<hII')
        self.doors = [(ti, fid) for (ti, _crc, fid) in doors]


def components(tris):
    """`(tri -> component, count)` over shared edges; an edge-link slot joins nothing."""
    comp = [-1] * len(tris)
    n = 0
    for seed in range(len(tris)):
        if comp[seed] != -1:
            continue
        stack = [seed]
        comp[seed] = n
        while stack:
            t = stack.pop()
            _v0, _v1, _v2, e0, e1, e2, flags, _cover = tris[t]
            for slot, e in enumerate((e0, e1, e2)):
                if flags & _TRI_EDGE_LINK_BITS[slot]:
                    continue
                if e != -1 and comp[e] == -1:
                    comp[e] = n
                    stack.append(e)
        n += 1
    return comp, n


def _component_edges(nv, t, tri_local, comp_fid_of_tri, root_fid, out_links):
    """Triangle `t`'s three edge fields renumbered into its component.

    A link naming a triangle of THIS cell mesh is re-aimed at the component
    NAVM that now owns that triangle; a link into another mesh is untouched.
    Every link is appended to `out_links` and its slot names its index there.
    """
    edges = list(t[3:6])
    for slot in range(3):
        if t[6] & _TRI_EDGE_LINK_BITS[slot]:
            typ, nav, target = nv.links[edges[slot]]
            if nav == root_fid:
                nav, target = comp_fid_of_tri[target], tri_local[target]
            edges[slot] = len(out_links)
            out_links.append((typ, nav, target))
        elif edges[slot] != -1:
            edges[slot] = tri_local[edges[slot]]
    return edges


def _renumber(nv, comp_tris, tri_local, comp_fid_of_tri, root_fid):
    """`(verts, tris, links)` of one component, with local vertex and triangle ids."""
    vmap, verts, out_tris, out_links = {}, [], [], []
    for ti in comp_tris:
        t = nv.tris[ti]
        for v in t[:3]:
            if v not in vmap:
                vmap[v] = len(verts)
                verts.append(nv.verts[v])
        edges = _component_edges(nv, t, tri_local, comp_fid_of_tri, root_fid,
                                 out_links)
        out_tris.append(tuple(vmap[v] for v in t[:3]) + tuple(edges)
                        + (t[6], t[7]))
    return verts, out_tris, out_links


def _pack_body(nv, verts, tris, links, doors):
    """The NVNM head: header, vertices, triangles, links, doors and no cover."""
    buf = bytearray(struct.pack('<IIII', NVNM_VERSION, PATHING_CELL_CRC,
                                nv.wrld, nv.cell))
    buf += struct.pack('<I', len(verts))
    for x, y, z in verts:
        buf += struct.pack('<fff', x, y, z)
    buf += struct.pack('<I', len(tris))
    for t in tris:
        buf += struct.pack('<6h2H', *t)
    buf += struct.pack('<I', len(links))
    for (typ, nav, ti) in links:
        buf += struct.pack('<IIh', typ, nav, ti)
    buf += struct.pack('<I', len(doors))
    for (ti, fid) in doors:
        buf += struct.pack('<hII', ti, PATHING_DOOR_CRC, fid)
    buf += struct.pack('<I', 0)
    return buf


def _pack_bounds(verts, tris):
    """The NVNM tail: divisor, bucket size, bounding box and bucket grid."""
    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    span_x = hi[0] - lo[0] if hi[0] > lo[0] else 1.0
    span_y = hi[1] - lo[1] if hi[1] > lo[1] else 1.0
    divisor = choose_divisor(span_x, span_y)
    buf = bytearray(struct.pack('<Iff', divisor, span_x / divisor,
                                span_y / divisor))
    buf += struct.pack('<ffffff', *lo, *hi)
    for cell_tris in build_navmesh_grid(verts, [t[:3] for t in tris],
                                        lo[0], lo[1], hi[0], hi[1], divisor):
        buf += struct.pack('<I', len(cell_tris))
        for ti in cell_tris:
            buf += struct.pack('<h', ti)
    return buf


def pack_component_nvnm(nv, comp_tris, tri_local, comp_fid_of_tri, root_fid):
    """`(blob, center, link_fids, door_refs, {door: local tri})` for one component.

    The blob mirrors `pack_nvnm`'s layout.
    """
    verts, tris, links = _renumber(nv, comp_tris, tri_local, comp_fid_of_tri,
                                   root_fid)
    members = set(comp_tris)
    doors = sorted((tri_local[ti], fid) for (ti, fid) in nv.doors
                   if ti in members)
    blob = bytes(_pack_body(nv, verts, tris, links, doors)
                 + _pack_bounds(verts, tris))
    center = tuple(sum(v[k] for v in verts) / len(verts) for k in range(3))
    return (blob, center, sorted({nav for (_t, nav, _ti) in links}),
            sorted({fid for (_t, fid) in doors}),
            {fid: ti for (ti, fid) in doors})


def split_disconnected_interiors(navm_cache: dict, writer) -> int:
    """Split multi-component interior NAVMs that need it; returns the count.

    Mutates navm_cache in place: the root entry keeps its (bytes, meta) shape
    and gains meta['extra_navms'] = [(bytes, meta)...] for the siblings, which
    the group builders emit alongside it.  Exteriors are skipped.

    See: docs/commentary/tes5_import_navmesh.md#split-only-the-meshes-that-need-it
    """
    split_count = 0
    for key in sorted(navm_cache):
        navm_bytes, meta = navm_cache[key]
        if not navm_bytes or not meta or meta.get('is_exterior'):
            continue
        nv = _decode_record(navm_bytes)
        if nv is None or not nv.tris:
            continue
        comp, ncomp = components(nv.tris)
        if ncomp <= 1:
            continue
        if not _doors_in_several_components(nv, comp):
            continue
        navm_cache[key] = _split_one_mesh(nv, comp, ncomp, key, meta, writer)
        split_count += 1
    return split_count


def _doors_in_several_components(nv, comp) -> bool:
    """True when door triangles lie in two or more disconnected components.

    See: docs/commentary/tes5_import_navmesh.md#split-only-the-meshes-that-need-it
    """
    return len({comp[ti] for (ti, _fid) in nv.doors}) > 1


def _component_fids(nv, comp_tris, ncomp, key, root_fid, writer) -> list:
    """Sibling NAVM FormIDs, index 0 being the root mesh's own id.

    Keyed on AUTHORED data -- the sorted door REFRs each component's triangles
    touch -- never on component order, which is derived and would move ids.

    See: docs/commentary/tes5_import_navmesh.md#split-only-the-meshes-that-need-it
    """
    fids = [root_fid]
    for c in range(1, ncomp):
        tset = set(comp_tris[c])
        doors = sorted({fid for (ti, fid) in nv.doors if ti in tset})
        ckey = (key, tuple(doors)) if doors else (key, 'comp%d' % c)
        fids.append(writer.derive_formid('NAVM_SPLIT', ckey))
    return fids


def _pack_parts(nv, comp_tris, ncomp, tri_local, comp_fid_of_tri, fids) -> list:
    """One (record, center, link_fids, door_refs, door_local) per component.

    Component 0 keeps the source EDID; the rest get a `_NN` suffix so the CK
    shows them as siblings of the mesh they were cut from.
    """
    root_fid = fids[0]
    edid, onam = nv.edid, nv.onam
    parts = []
    for c in range(ncomp):
        blob, center, link_fids, door_refs, door_local = \
            pack_component_nvnm(nv, comp_tris[c], tri_local,
                                 comp_fid_of_tri, root_fid)
        subs = b''
        if edid:
            suffix = '' if c == 0 else f'_{c + 1:02d}'
            subs += pack_string_subrecord('EDID', edid + suffix)
        subs += pack_subrecord('NVNM', blob)
        if onam:
            subs += pack_subrecord('ONAM', onam)
        parts.append((pack_navm_record(fids[c], subs), center, link_fids,
                      door_refs, door_local))
    return parts


def _split_one_mesh(nv, comp, ncomp, key, meta, writer) -> tuple:
    """Cut one decoded mesh into per-component NAVMs; returns (root_bytes, meta).

    The root keeps the original FormID and gains meta['extra_navms'], a list of
    (bytes, meta) for the siblings.  Door state moves to whichever component
    owns it, so each REFR's XNDP names the right mesh and each NVMI lists only
    its own doors.
    """
    comp_tris = [[] for _ in range(ncomp)]
    for ti, c in enumerate(comp):
        comp_tris[c].append(ti)
    root_fid = meta['fid']
    fids = _component_fids(nv, comp_tris, ncomp, key, root_fid, writer)
    comp_fid_of_tri = [fids[c] for c in comp]
    tri_local = {}
    for tris in comp_tris:
        for local, ti in enumerate(tris):
            tri_local[ti] = local

    parts = _pack_parts(nv, comp_tris, ncomp, tri_local, comp_fid_of_tri, fids)

    door_xndp = {}
    for c, (_rec, _center, _links, _refs, door_local) in enumerate(parts):
        for fid, ti in door_local.items():
            door_xndp[fid] = (fids[c], ti)

    root_rec, root_center, root_links, root_refs, _ = parts[0]
    meta = dict(meta)
    meta['center'] = root_center
    meta['door_refs'] = root_refs
    meta['door_xndp'] = door_xndp
    meta['edge_link_fids'] = [f for f in root_links if f != root_fid]
    extras = []
    for c in range(1, ncomp):
        rec, center, link_fids, door_refs, _door_local = parts[c]
        extras.append((rec, {
            'fid': fids[c],
            'wrld_fid': meta['wrld_fid'],
            'cell_fid': meta['cell_fid'],
            'grid_x': meta['grid_x'],
            'grid_y': meta['grid_y'],
            'is_exterior': False,
            'center': center,
            'door_refs': door_refs,
            'edge_link_fids': [f for f in link_fids if f != fids[c]],
        }))
    meta['extra_navms'] = extras
    return root_rec, meta


def _decode_record(navm_bytes) -> Nvnm:
    """Decode a packed (compressed) NAVM record into an Nvnm, or None."""
    flags = struct.unpack_from('<I', navm_bytes, 8)[0]
    subs = {}
    for tag, payload in subrecords(decompress(navm_bytes[REC_HDR:], flags)):
        subs.setdefault(tag, payload)
    nvnm, onam, edid = (subs.get(b'NVNM'), subs.get(b'ONAM'),
                        subs.get(b'EDID'))
    edid = None if edid is None else edid.rstrip(b'\0').decode('latin1')
    if nvnm is None:
        return None
    nv = Nvnm(nvnm)
    nv.edid = edid
    nv.onam = onam
    return nv
