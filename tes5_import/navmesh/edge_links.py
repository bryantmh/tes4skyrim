"""Stitch adjacent exterior cell navmeshes together with Edge Links.

Why this exists
---------------
A navmesh can be geometrically perfect and still useless.  Skyrim connects one
cell's navmesh to the next through **Edge Links**; without them every cell mesh
is an isolated island, so an actor paths fine inside its current cell and can
NEVER cross a cell boundary.  Every AI package whose destination is in another
cell then starts (the actor stands up, plays its en-route dialogue) and never
moves — which is exactly how converted quest travel/escort packages failed.

Measured against Skyrim.esm: **12,145 of 14,440 vanilla exterior navmeshes (84%)
carry edge links, 194,744 in total** (Portal 190,779 / LedgeUp 1,978 /
LedgeDown 1,987).  We emitted zero.

Binary contract (verified; Skyrim.esm parses 15,949/15,949 clean with it)
------------------------------------------------------------------------
* Edge Link = ``Type(U32) + Navmesh(FormID U32) + Triangle(S16)`` = **10 bytes**
  (not 12 — a 12-byte stride misparses every navmesh that has links).
* A triangle's flag bits 0/1/2 mean ``Edge 0-1 / 1-2 / 2-0 Link``.  When bit N is
  set, that triangle's edge-N field is an **index into the Edge Links array**
  instead of a local neighbour-triangle index (xEdit ``wbEdgeToStr``).
* Edge Link Type 0 = Portal (the cell-seam link), 1 Ledge Up, 2 Ledge Down,
  3 Enable/Disable Portal.
* Links are **reciprocal**: vanilla NAVM 0x00101F29 grid (7,7) links to its four
  orthogonal neighbours (6,7)x15 (8,7)x11 (7,6)x22 (7,8)x15 and each neighbour
  links back the identical count.

Approach
--------
Runs as a post-pass over the precomputed navmesh cache, after every cell mesh
exists (it needs neighbour NAVM FormIDs and final triangle indices) and before
the group builders serialise them.  Cells are processed in sorted order and each
mesh's links are appended in sorted order, so the output stays byte-reproducible
(see the determinism contract in CLAUDE.md).
"""

import struct
import zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from core.worker_budget import worker_count

# NVNM layout constants (see module docstring).
EDGE_LINK_SIZE = 10
DOOR_TRI_SIZE = 10
TRI_STRUCT = '<6h2H'
TRI_SIZE = 16

LINK_TYPE_PORTAL = 0

# A cell is 4096 units square; vertices are world coordinates in exteriors.
CELL_SIZE = 4096.0

# How close two border-edge endpoints must be, across the seam, to be treated as
# the same edge.  The two meshes are generated independently from their own
# collision/terrain samples, so their boundary vertices rarely land on exactly
# the same coordinate; this is the snap distance in game units.
SEAM_TOLERANCE = 48.0
# How far a vertex may sit from the seam plane and still count as "on" it.
SEAM_BAND = 24.0
# Vertical tolerance — two edges that meet in plan view but are a storey apart
# must not be linked.
SEAM_Z_TOLERANCE = 96.0


class NavMeshView:
    """Mutable decode of one NVNM blob: enough to add links and re-pack."""

    __slots__ = ('fid', 'head', 'verts', 'tris', 'links', 'tail',
                 'exterior', 'grid', 'dirty')

    def __init__(self, fid, blob):
        """Decode one NVNM blob into mutable numpy arrays.

        See: docs/commentary/tes5_import_navmesh.md#navmeshview-decode-is-vectorised
        """
        self.fid = fid
        self.dirty = False
        p = 8                                    # version + crc
        wrld = struct.unpack_from('<I', blob, p)[0]
        p += 4
        self.exterior = wrld != 0
        if self.exterior:
            gy, gx = struct.unpack_from('<hh', blob, p)
            self.grid = (gx, gy)
            p += 4
        else:
            self.grid = None
            p += 4                               # interior: cell FormID
        head_end = p
        nv = struct.unpack_from('<I', blob, p)[0]
        p += 4
        self.verts = np.frombuffer(blob, dtype='<f4', count=nv * 3,
                                   offset=p).reshape(nv, 3).astype(np.float64)
        p += nv * 12
        nt = struct.unpack_from('<I', blob, p)[0]
        p += 4
        self.tris = np.frombuffer(blob, dtype='<i2', count=nt * 8,
                                  offset=p).reshape(nt, 8).astype(np.int32)
        if nt:
            self.tris[:, 6:8] &= 0xFFFF
        p += nt * TRI_SIZE
        ne = struct.unpack_from('<I', blob, p)[0]
        p += 4
        self.links = [list(struct.unpack_from('<IIh', blob, p + i * EDGE_LINK_SIZE))
                      for i in range(ne)]
        p += ne * EDGE_LINK_SIZE
        self.head = blob[:head_end]
        self.tail = blob[p:]                     # doors, cover, grid, bbox

    def pack(self) -> bytes:
        """Re-emit the NVNM blob.

        Vectorised for the same reason as the decode above.  Byte-for-byte
        identical to the per-element struct.pack loop it replaces: verts are
        float32 triples, triangles 8 little-endian shorts (the two unsigned
        trailing fields wrap identically under an int16 view), links 10 bytes
        each — the one field group that stays a Python loop, since it is a
        mixed 4/4/2 layout and there are only a handful per mesh.
        """
        out = bytearray(self.head)
        out += struct.pack('<I', len(self.verts))
        out += self.verts.astype('<f4', copy=False).tobytes()
        out += struct.pack('<I', len(self.tris))
        out += self.tris.astype('<i2', copy=False).tobytes()
        out += struct.pack('<I', len(self.links))
        for typ, nav, tri in self.links:
            out += struct.pack('<IIh', typ, nav, tri)
        out += self.tail
        return bytes(out)

    def add_link(self, tri_index: int, edge_slot: int,
                 other_fid: int, other_tri: int) -> None:
        """Flag one triangle edge as external and point it at another mesh."""
        link_index = len(self.links)
        self.links.append([LINK_TYPE_PORTAL, other_fid, other_tri])
        tri = self.tris[tri_index]               # numpy row view — mutates in place
        tri[3 + edge_slot] = link_index          # edge field becomes a link index
        tri[6] |= (1 << edge_slot)               # 'Edge N Link' flag
        self.dirty = True

    def door_triangles(self) -> list:
        """[(triangle index, door reference FormID)] of the mesh's Door Triangles."""
        count = struct.unpack_from('<I', self.tail, 0)[0]
        rows = (struct.unpack_from('<hII', self.tail, 4 + i * DOOR_TRI_SIZE) for i in range(count))
        return [(int(ti), ref) for ti, _crc, ref in rows]

    def own_links(self) -> list:
        """[(triangle, linked triangle)] of every non-portal (ledge) link into this same mesh."""
        out = []
        for ti, t in enumerate(self.tris):
            for slot in range(3):
                if t[6] & (1 << slot):
                    typ, fid, other = self.links[t[3 + slot]]
                    if typ != LINK_TYPE_PORTAL and fid == self.fid:
                        out.append((ti, int(other)))
        return out


def _prune_links(view: NavMeshView, live_fids: set) -> None:
    """Remove links whose target isn't live and renumber the survivors.

    A triangle edge that is flagged as a link stores the link's INDEX in the
    Edge Links array, so dropping an entry requires remapping every edge that
    referenced a later index.  An edge pointing at a removed link is reverted to
    a plain border edge (field -1, flag bit cleared).
    """
    old_index_map = {}
    new_links = []
    for i, lk in enumerate(view.links):
        if lk[1] in live_fids:
            old_index_map[i] = len(new_links)
            new_links.append(lk)
    view.links = new_links
    for t in view.tris:
        for slot in range(3):
            if t[6] & (1 << slot):
                old = t[3 + slot]
                if old in old_index_map:
                    t[3 + slot] = old_index_map[old]
                else:                       # its link was pruned
                    t[3 + slot] = -1
                    t[6] &= ~(1 << slot)
    view.dirty = True


def border_edges(view: NavMeshView, axis: int, coord: float):
    """[(tri_index, edge_slot, midpoint, z, half length)] for border edges lying on a seam.

    A border edge is one whose neighbour field is -1 (nothing local adjoins it).
    It must run ALONG the seam: a stub crossing the band is not one.
    `axis` 0 = the seam is a constant-X plane, 1 = constant-Y.
    See: docs/commentary/tes5_import_navmesh.md#seam-edges-run-along-the-seam
    """
    if len(view.tris) == 0:
        return []
    # Vectorised: this was 28% of the edge-link pass, because every seam
    # rescans both of its meshes' full triangle lists in Python (24k calls over
    # 6.5k meshes).  The numpy form evaluates the same predicate over all
    # triangles x 3 slots at once.  Emission order is preserved (triangle
    # index, then slot) so the greedy seam matching downstream is unchanged.
    tris = view.tris
    verts = view.verts
    flags = tris[:, 6]
    alive = (flags & 0x0008) == 0
    vids = tris[:, 0:3]

    rows = []
    for slot in range(3):
        sel = alive & (tris[:, 3 + slot] == -1) & ((flags & (1 << slot)) == 0)
        if not sel.any():
            continue
        idx = np.nonzero(sel)[0]
        a = verts[vids[idx, slot]]
        b = verts[vids[idx, (slot + 1) % 3]]
        # NOT(> band) rather than (<= band): the original skipped on `>`, so a
        # NaN coordinate (Nehrim ships uninitialised placement floats) was
        # KEPT.  `<=` would silently drop it.  Same predicate, same result.
        with np.errstate(invalid='ignore'):
            on_seam = ~((np.abs(a[:, axis] - coord) > SEAM_BAND) |
                        (np.abs(b[:, axis] - coord) > SEAM_BAND) |
                        (np.abs(a[:, axis] - b[:, axis]) >=
                         np.abs(a[:, 1 - axis] - b[:, 1 - axis])))
        if not on_seam.any():
            continue
        keep = np.nonzero(on_seam)[0]
        ti = idx[keep]
        mid_other = (a[keep, 1 - axis] + b[keep, 1 - axis]) * 0.5
        mid_z = (a[keep, 2] + b[keep, 2]) * 0.5
        half = np.abs(a[keep, 1 - axis] - b[keep, 1 - axis]) * 0.5
        rows.append((ti, np.full(ti.shape, slot), mid_other, mid_z, half))

    if not rows:
        return []
    ti, sl, mo, mz, hl = (np.concatenate([r[k] for r in rows]) for k in range(5))
    order = np.lexsort((sl, ti))
    return [(int(ti[i]), int(sl[i]), float(mo[i]), float(mz[i]), float(hl[i]))
            for i in order]


def _seam_sort_key(e):
    return (e[2], e[3], e[0], e[1])


def _shared(ea, eb):
    """Length of seam two border edges both cover (negative when apart)."""
    return min(ea[2] + ea[4], eb[2] + eb[4]) - max(ea[2] - ea[4], eb[2] - eb[4])


def match_seam(edges_a, edges_b):
    """Greedy nearest-neighbour pairing of border edges across one seam.

    Two edges pair when their midpoints lie within SEAM_TOLERANCE or they share at
    least that much of the seam, as edges split differently by the two meshes do.
    Deterministic: both sides are sorted once up front and each edge on side B
    is consumed at most once, so the pairing depends only on geometry — never on
    dict or iteration order (the ESM must stay byte-reproducible).
    """
    a_sorted = sorted(edges_a, key=_seam_sort_key)
    b_sorted = sorted(edges_b, key=_seam_sort_key)
    pairs = []
    used_b = set()
    for ea in a_sorted:
        best = None
        best_d = None
        for j, eb in enumerate(b_sorted):
            if j in used_b:
                continue
            d_along = abs(ea[2] - eb[2])
            if d_along > SEAM_TOLERANCE and _shared(ea, eb) < SEAM_TOLERANCE:
                continue
            if abs(ea[3] - eb[3]) > SEAM_Z_TOLERANCE:
                continue
            if best_d is None or d_along < best_d:
                best_d = d_along
                best = j
        if best is not None:
            used_b.add(best)
            pairs.append((ea, b_sorted[best]))
    return pairs


# Neighbour offsets: only the four orthogonal cells share a seam.
#   (dx, dy, axis, a_is_low)  — axis 0 = seam at constant X, 1 = constant Y.
_NEIGHBOURS = (
    (1, 0, 0, True),    # east  : seam at this cell's max X
    (0, 1, 1, True),    # north : seam at this cell's max Y
)


def _drop_stale_links(view, ours) -> None:
    """Drop a master's links into cells THIS plugin rewrote.

    See: docs/commentary/tes5_import_navmesh.md#cross-plugin-edge-links
    """
    if any(lk[1] in ours for lk in view.links):
        _prune_links(view, {lk[1] for lk in view.links
                            if lk[1] not in ours})
        view.dirty = True


def master_view(master_index, fid):
    """(view, subs before NVNM, subs after) of a master's NAVM, or None when unreadable."""
    blob, prefix, suffix = extract_nvnm(master_index.record(fid))
    if blob is None:
        return None
    try:
        return NavMeshView(fid, blob), prefix, suffix
    except (struct.error, IndexError):
        return None


def _master_neighbour_views(views, master_index, master_navms):
    """Decode the MASTER meshes bordering ours; return {cell: view}.

    A child plugin navmeshes only the cells it edits, so at the edge of its
    region every neighbour is master-owned and absent from `views`: the seam
    is skipped, and the pruning pass then deletes the links the master
    already had.  Loading the neighbours read-only lets both happen
    correctly, and any that gain a link are re-emitted as overrides.

    See: docs/commentary/tes5_import_navmesh.md#cross-plugin-edge-links
    """
    if master_index is None or not master_navms:
        return {}
    ours = {v.fid for v in views.values()}
    wanted = set()
    for (wrld, gx, gy, _fid) in views:
        for dx, dy, _axis, _ in _NEIGHBOURS:
            wanted.add((wrld, gx + dx, gy + dy))
        for dx, dy in ((-1, 0), (0, -1)):
            wanted.add((wrld, gx + dx, gy + dy))
    out = {}
    missing = no_record = 0
    for cell in sorted(wanted - {vkey[:3] for vkey in views}):
        fid = master_navms.get(cell)
        if not fid:
            missing += 1
            continue
        entry = master_view(master_index, fid)
        if entry is None:
            no_record += 1
        elif entry[0].exterior:
            _drop_stale_links(entry[0], ours)
            out[cell] = entry
    if missing or no_record:
        print(f"    Master neighbours: {len(out)} loaded, {missing} cell(s) "
              f"with no master navmesh, {no_record} unreadable")
    return out


def _prune_dead_links(views, live_fids, master_navms, masters) -> None:
    """Drop dead links; a master's reach into untouched terrain stays live.

    See: docs/commentary/tes5_import_navmesh.md#cross-plugin-edge-links
    """
    far = set((master_navms or {}).values())
    for cell, view in views.items():
        live = (live_fids | far) if cell in masters else live_fids
        if any(lk[1] not in live for lk in view.links):
            _prune_links(view, live)


def build_edge_links(navm_cache: dict, verbose: bool = True,
                     master_index=None, master_navms: dict = None,
                     relinked_masters: list = None) -> int:
    """Add reciprocal Portal links between adjacent exterior cell navmeshes.

    navm_cache: {key: (navm_bytes, meta)} from _precompute_navmeshes, rewritten
    in place when links are added.  `master_navms` maps {(wrld, gx, gy):
    navm_fid} for the MASTERS' cells; a master mesh that gains a link ships as
    an override, so both halves of the seam exist.  `live_fids` is the set a
    link may point at; outside it would dereference a null.

    Returns the number of links created.
    See: docs/commentary/tes5_import_navmesh.md#cross-plugin-edge-links
    """
    views, holders = exterior_views(navm_cache)
    if relinked_masters is None:
        relinked_masters = []
    masters = {}
    for cell, entry in _master_neighbour_views(views, master_index, master_navms).items():
        masters[cell + (entry[0].fid,)] = entry
        views[cell + (entry[0].fid,)] = entry[0]
    live_fids = {v.fid for v in views.values()}
    made = _stitch_seams(views)
    _prune_dead_links(views, live_fids, master_navms, masters)

    rewritten = _repack(views, holders, masters, navm_cache,
                        relinked_masters)

    if verbose:
        total_ext = len(views)
        linked = sum(1 for v in views.values() if v.links)
        pct = (100.0 * linked / total_ext) if total_ext else 0.0
        extra = f", {len(masters)} master meshes" if masters else ""
        print(f"  Navmesh edge links: {made} portals stitched across "
              f"{rewritten} cells ({linked}/{total_ext} exterior "
              f"navmeshes linked, {pct:.0f}%{extra})")
    return made


def exterior_views(navm_cache: dict) -> tuple:
    """({(wrld, gx, gy, fid): view}, {same key: (cache key, prefix, suffix, meta)}) per exterior mesh.

    Keyed by mesh, not cell: FO3/FNV author up to 8 navmeshes in one cell.
    See: docs/commentary/tes5_import_navmesh.md#every-mesh-in-a-cell
    """
    views, holders = {}, {}
    for key, value in navm_cache.items():
        navm_bytes, meta = value or (None, None)
        if not navm_bytes or not meta or not meta.get('is_exterior'):
            continue
        blob, prefix, suffix = extract_nvnm(navm_bytes)
        if blob is None:
            continue
        try:
            view = NavMeshView(meta['fid'], blob)
        except (struct.error, IndexError):
            continue
        if view.exterior:
            vkey = (meta.get('wrld_fid'), meta['grid_x'], meta['grid_y'], meta['fid'])
            views[vkey] = view
            holders[vkey] = (key, prefix, suffix, meta)
    return views, holders


def _seam_edges(cell_views: list, axis: int, coord: float) -> list:
    """Border edges on one seam from every mesh in a cell, each tagged with its mesh."""
    return [e + (view,) for view in cell_views for e in border_edges(view, axis, coord)]


def _stitch_seams(views: dict) -> int:
    """Link matching border edges across each cell seam; return the links made.

    Sorted cells and only the +X/+Y neighbours visit each seam once, in a stable
    order. The seam plane is cell A's upper edge on that axis, and every mesh on
    either side competes for it.
    """
    cells = {}
    for vkey in sorted(views):
        cells.setdefault(vkey[:3], []).append(views[vkey])
    made = 0
    for (wrld, gx, gy) in sorted(cells):
        for dx, dy, axis, _ in _NEIGHBOURS:
            other = cells.get((wrld, gx + dx, gy + dy))
            if other is None:
                continue
            coord = ((gx + 1) * CELL_SIZE) if axis == 0 else ((gy + 1) * CELL_SIZE)
            edges_a = _seam_edges(cells[(wrld, gx, gy)], axis, coord)
            edges_b = _seam_edges(other, axis, coord)
            for ea, eb in match_seam(edges_a, edges_b):
                ea[5].add_link(ea[0], ea[1], eb[5].fid, eb[0])
                eb[5].add_link(eb[0], eb[1], ea[5].fid, ea[0])
                made += 2
    return made


def _repack(views, holders, masters, navm_cache, relinked) -> int:
    """Re-emit every mesh whose links changed; return how many.

    A master's mesh has no navm_cache entry and the cell builders only look
    up (cell, pgrd) keys, so its relinked bytes go to `relinked` instead,
    which the nested-override pass emits in the master's own GRUP.

    See: docs/commentary/tes5_import_navmesh.md#cross-plugin-edge-links
    """
    from .from_pgrd import pack_navm_record
    from ..base.writer import pack_subrecord
    rewritten = 0
    pending = []
    for cell, view in views.items():
        if cell in masters:
            if not view.dirty:
                continue
            _mview, prefix, suffix = masters[cell]
            subs = prefix + pack_subrecord('NVNM', view.pack()) + suffix
            meta = {'fid': view.fid, 'is_exterior': True,
                    'wrld_fid': cell[0], 'grid_x': cell[1],
                    'grid_y': cell[2], 'master_override': True,
                    'edge_link_fids': sorted(
                        {lk[1] for lk in view.links if lk[1] != view.fid})}
            relinked.append((pack_navm_record(view.fid, subs), meta))
            rewritten += 1
            continue
        key, prefix, suffix, meta = holders[cell]
        # NVMI Edge Links = the distinct neighbour meshes this mesh's NVNM edge
        # links reach, self excluded — the vanilla NVMI rule (15,115/15,462
        # exact matches; the 347 outliers differ only by a self-link, which
        # NVMI omits).  Stored on the meta so navi_builder can mirror it.
        meta['edge_link_fids'] = sorted(
            {lk[1] for lk in view.links if lk[1] != view.fid})
        if view.dirty:
            pending.append((key, meta, prefix + pack_subrecord('NVNM', view.pack()) + suffix))
    with ThreadPoolExecutor(max_workers=worker_count()) as ex:
        records = list(ex.map(lambda p: pack_navm_record(p[1]['fid'], p[2]), pending))
    for (key, meta, _subs), record in zip(pending, records):
        navm_cache[key] = (record, meta)
    return rewritten + len(pending)


def extract_nvnm(navm_bytes: bytes):
    """Split a packed NAVM record into (nvnm_blob, subs_before, subs_after).

    The record is uncompressed at this stage (compression happens in
    pack_navm_record), so walk its subrecords directly.

    A 4-byte XXXX subrecord carries the REAL size of the one after it,
    whose own length field reads 0.

    See: docs/commentary/tes5_import_navmesh.md#nvnm-xxxx-size-protocol
    """
    header_size = 24
    if len(navm_bytes) < header_size:
        return None, b'', b''
    size, flags = struct.unpack_from('<II', navm_bytes, 4)
    body = navm_bytes[header_size:header_size + size]
    if flags & 0x00040000:
        try:
            body = zlib.decompress(body[4:])
        except zlib.error:
            return None, b'', b''
    p = 0
    before = b''
    blob = None
    after = b''
    xxxx_chunk = b''
    override = None
    while p < len(body) - 5:
        sig = body[p:p + 4]
        ln = struct.unpack_from('<H', body, p + 4)[0]
        if sig == b'XXXX' and ln == 4:
            override = struct.unpack_from('<I', body, p + 6)[0]
            xxxx_chunk = body[p:p + 6 + ln]
            p += 6 + ln
            continue
        real = ln if override is None else override
        override = None
        chunk = body[p:p + 6 + real]
        if sig == b'NVNM' and blob is None:
            blob = body[p + 6:p + 6 + real]
        elif blob is None:
            before += xxxx_chunk + chunk
        else:
            after += xxxx_chunk + chunk
        xxxx_chunk = b''
        p += 6 + real
    return blob, before, after
