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

from .writer import pack_subrecord, pack_string_subrecord
from .pgrd_to_navm import (
    _build_navmesh_grid,
    _choose_divisor,
    _pack_navm_record,
    _PATHING_CELL_CRC,
    _PATHING_DOOR_CRC,
    _NVNM_VERSION,
)

_TRI_EDGE_LINK_BITS = (0x0001, 0x0002, 0x0004)


class _Nvnm:
    """Full decode of one of OUR interior NVNM blobs (see _pack_nvnm)."""

    def __init__(self, blob):
        p = 8                                     # version + location CRC
        self.wrld = struct.unpack_from('<I', blob, p)[0]
        p += 4
        self.cell = struct.unpack_from('<I', blob, p)[0]
        p += 4
        nv = struct.unpack_from('<I', blob, p)[0]
        p += 4
        self.verts = [struct.unpack_from('<fff', blob, p + i * 12)
                      for i in range(nv)]
        p += nv * 12
        nt = struct.unpack_from('<I', blob, p)[0]
        p += 4
        # (v0, v1, v2, e0, e1, e2, flags, cover)
        self.tris = [list(struct.unpack_from('<6h2H', blob, p + i * 16))
                     for i in range(nt)]
        p += nt * 16
        nl = struct.unpack_from('<I', blob, p)[0]
        p += 4
        # (type, navmesh fid, triangle)
        self.links = [struct.unpack_from('<IIh', blob, p + i * 10)
                      for i in range(nl)]
        p += nl * 10
        nd = struct.unpack_from('<I', blob, p)[0]
        p += 4
        # (triangle, door ref fid)
        self.doors = []
        for i in range(nd):
            ti, _crc, fid = struct.unpack_from('<hII', blob, p + i * 10)
            self.doors.append((ti, fid))
        # Cover triangles / bounding box / bucket grid tail: recomputed on
        # re-pack, nothing to keep.


def _components(tris):
    """Connected components over shared (non-link) edges; returns tri->comp."""
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
                    continue                     # edge-link slot, not a neighbour
                if e != -1 and comp[e] == -1:
                    comp[e] = n
                    stack.append(e)
        n += 1
    return comp, n


def _pack_component_nvnm(nv, comp_tris, tri_local, comp_fid_of_tri,
                         root_fid):
    """Serialise one component as an NVNM blob (mirrors _pack_nvnm's layout)."""
    vmap = {}
    verts = []
    out_tris = []
    out_links = []
    for ti in comp_tris:
        v0, v1, v2, e0, e1, e2, flags, cover = nv.tris[ti]
        nvtx = []
        for v in (v0, v1, v2):
            if v not in vmap:
                vmap[v] = len(verts)
                verts.append(nv.verts[v])
            nvtx.append(vmap[v])
        edges = [e0, e1, e2]
        for slot in range(3):
            if flags & _TRI_EDGE_LINK_BITS[slot]:
                typ, nav, target = nv.links[edges[slot]]
                if nav == root_fid:
                    # A ledge names a triangle of THIS cell mesh: re-aim it at
                    # the component NAVM that now owns the target triangle.
                    nav, target = comp_fid_of_tri[target], tri_local[target]
                # else: link into another mesh — indices there are untouched.
                edges[slot] = len(out_links)
                out_links.append((typ, nav, target))
            elif edges[slot] != -1:
                edges[slot] = tri_local[edges[slot]]
        out_tris.append((nvtx[0], nvtx[1], nvtx[2],
                         edges[0], edges[1], edges[2], flags, cover))

    members = set(comp_tris)
    doors = sorted(((tri_local[ti], fid) for (ti, fid) in nv.doors
                    if ti in members),
                   key=lambda d: (d[0], d[1]))

    buf = bytearray()
    buf += struct.pack('<I', _NVNM_VERSION)
    buf += struct.pack('<I', _PATHING_CELL_CRC)
    buf += struct.pack('<I', nv.wrld)
    buf += struct.pack('<I', nv.cell)
    buf += struct.pack('<I', len(verts))
    for x, y, z in verts:
        buf += struct.pack('<fff', x, y, z)
    buf += struct.pack('<I', len(out_tris))
    for t in out_tris:
        buf += struct.pack('<6h2H', *t)
    buf += struct.pack('<I', len(out_links))
    for (typ, nav, ti) in out_links:
        buf += struct.pack('<IIh', typ, nav, ti)
    buf += struct.pack('<I', len(doors))
    for (ti, fid) in doors:
        buf += struct.pack('<hII', ti, _PATHING_DOOR_CRC, fid)
    buf += struct.pack('<I', 0)                   # cover triangles

    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)
    span_x = max_x - min_x if max_x > min_x else 1.0
    span_y = max_y - min_y if max_y > min_y else 1.0
    divisor = _choose_divisor(span_x, span_y)
    buf += struct.pack('<I', divisor)
    buf += struct.pack('<f', span_x / divisor)
    buf += struct.pack('<f', span_y / divisor)
    buf += struct.pack('<ffffff', min_x, min_y, min_z, max_x, max_y, max_z)
    grid = _build_navmesh_grid(verts, [t[:3] for t in out_tris],
                               min_x, min_y, max_x, max_y, divisor)
    for cell_tris in grid:
        buf += struct.pack('<I', len(cell_tris))
        for ti in cell_tris:
            buf += struct.pack('<h', ti)

    verts_center = (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))
    link_fids = sorted({nav for (_t, nav, _ti) in out_links})
    door_refs = sorted({fid for (_t, fid) in doors})
    door_local = {fid: ti for (ti, fid) in doors}
    return bytes(buf), verts_center, link_fids, door_refs, door_local


def split_disconnected_interiors(navm_cache: dict, writer) -> int:
    """Split every multi-component interior NAVM in the cache; returns the
    number of meshes split.  Mutates navm_cache in place: the root entry keeps
    its (bytes, meta) shape and gains meta['extra_navms'] = [(bytes, meta)...]
    for the sibling components, which the group builders emit alongside it.
    """
    split_count = 0
    for key in sorted(navm_cache):
        navm_bytes, meta = navm_cache[key]
        if not navm_bytes or not meta or meta.get('is_exterior'):
            continue
        nv = _decode_record(navm_bytes)
        if nv is None or not nv.tris:
            continue
        comp, ncomp = _components(nv.tris)
        if ncomp <= 1:
            continue

        # Components ordered by smallest member triangle (deterministic since
        # _components seeds in index order: component id == first-tri order).
        comp_tris = [[] for _ in range(ncomp)]
        for ti, c in enumerate(comp):
            comp_tris[c].append(ti)
        root_fid = meta['fid']
        # Component ORDER is derived (it falls out of triangle connectivity, so
        # any change upstream of triangulation renumbers it). Key each sibling
        # on the authored data it contains instead: the sorted door REFRs its
        # triangles touch, which are TES4 ids and survive re-triangulation.
        # Components with no door fall back to the ordinal — still stable for a
        # fixed input, and those meshes carry no door state to lose.
        fids = [root_fid]
        for c in range(1, ncomp):
            tset = set(comp_tris[c])
            doors = sorted({fid for (ti, fid) in nv.doors if ti in tset})
            ckey = (key, tuple(doors)) if doors else (key, 'comp%d' % c)
            fids.append(writer.derive_formid('NAVM_SPLIT', ckey))
        comp_fid_of_tri = [fids[c] for c in comp]
        tri_local = {}
        for tris in comp_tris:
            for local, ti in enumerate(tris):
                tri_local[ti] = local

        edid, onam = nv.edid, nv.onam
        parts = []
        for c in range(ncomp):
            blob, center, link_fids, door_refs, door_local = \
                _pack_component_nvnm(nv, comp_tris[c], tri_local,
                                     comp_fid_of_tri, root_fid)
            subs = b''
            if edid:
                suffix = '' if c == 0 else f'_{c + 1:02d}'
                subs += pack_string_subrecord('EDID', edid + suffix)
            subs += pack_subrecord('NVNM', blob)
            if onam:
                subs += pack_subrecord('ONAM', onam)
            rec = _pack_navm_record(fids[c], subs)
            parts.append((rec, center, link_fids, door_refs, door_local))

        # door ref -> (component NAVM fid, local triangle), for XNDP.
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
            xmeta = {
                'fid': fids[c],
                'wrld_fid': meta['wrld_fid'],
                'cell_fid': meta['cell_fid'],
                'grid_x': meta['grid_x'],
                'grid_y': meta['grid_y'],
                'is_exterior': False,
                'center': center,
                'door_refs': door_refs,
                'edge_link_fids': [f for f in link_fids if f != fids[c]],
            }
            extras.append((rec, xmeta))
        meta['extra_navms'] = extras
        navm_cache[key] = (root_rec, meta)
        split_count += 1
    return split_count


def _decode_record(navm_bytes) -> _Nvnm:
    """Decode a packed (compressed) NAVM record into an _Nvnm, or None."""
    import zlib
    flags = struct.unpack_from('<I', navm_bytes, 8)[0]
    body = navm_bytes[24:]
    if flags & 0x00040000:
        body = zlib.decompress(body[4:])
    edid = None
    onam = None
    nvnm = None
    p = 0
    size_override = None
    while p + 6 <= len(body):
        sig = body[p:p + 4]
        size = struct.unpack_from('<H', body, p + 4)[0]
        if sig == b'XXXX':
            # Oversized-subrecord protocol: the real size of the NEXT
            # subrecord, whose own size field is 0 (see pack_subrecord).
            size_override = struct.unpack_from('<I', body, p + 6)[0]
            p += 6 + size
            continue
        if size_override is not None:
            size = size_override
            size_override = None
        payload = body[p + 6:p + 6 + size]
        if sig == b'EDID':
            edid = payload.rstrip(b'\0').decode('latin1')
        elif sig == b'NVNM':
            nvnm = payload
        elif sig == b'ONAM':
            onam = payload
        p += 6 + size
    if nvnm is None:
        return None
    nv = _Nvnm(nvnm)
    nv.edid = edid
    nv.onam = onam
    return nv
