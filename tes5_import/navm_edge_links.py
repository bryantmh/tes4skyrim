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
from collections import defaultdict

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
        self.verts = [struct.unpack_from('<fff', blob, p + i * 12)
                      for i in range(nv)]
        p += nv * 12
        nt = struct.unpack_from('<I', blob, p)[0]
        p += 4
        self.tris = [list(struct.unpack_from(TRI_STRUCT, blob, p + i * TRI_SIZE))
                     for i in range(nt)]
        p += nt * TRI_SIZE
        ne = struct.unpack_from('<I', blob, p)[0]
        p += 4
        self.links = [list(struct.unpack_from('<IIh', blob, p + i * EDGE_LINK_SIZE))
                      for i in range(ne)]
        p += ne * EDGE_LINK_SIZE
        self.head = blob[:head_end]
        self.tail = blob[p:]                     # doors, cover, grid, bbox

    def pack(self) -> bytes:
        out = bytearray(self.head)
        out += struct.pack('<I', len(self.verts))
        for x, y, z in self.verts:
            out += struct.pack('<fff', x, y, z)
        out += struct.pack('<I', len(self.tris))
        for t in self.tris:
            out += struct.pack(TRI_STRUCT, *t)
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
        tri = self.tris[tri_index]
        tri[3 + edge_slot] = link_index          # edge field becomes a link index
        tri[6] |= (1 << edge_slot)               # 'Edge N Link' flag
        self.dirty = True


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


def _border_edges(view: NavMeshView, axis: int, coord: float):
    """[(tri_index, edge_slot, midpoint, z)] for border edges lying on a seam.

    A border edge is one whose neighbour field is -1 (nothing local adjoins it).
    `axis` 0 = the seam is a constant-X plane, 1 = constant-Y.
    """
    out = []
    verts = view.verts
    for ti, t in enumerate(view.tris):
        if t[6] & 0x0008:                       # Deleted
            continue
        vids = (t[0], t[1], t[2])
        for slot in range(3):
            if t[3 + slot] != -1:
                continue                        # has a local neighbour
            if t[6] & (1 << slot):
                continue                        # already linked
            a = verts[vids[slot]]
            b = verts[vids[(slot + 1) % 3]]
            if abs(a[axis] - coord) > SEAM_BAND or abs(b[axis] - coord) > SEAM_BAND:
                continue
            mid_other = ((a[1 - axis] + b[1 - axis]) * 0.5)
            mid_z = (a[2] + b[2]) * 0.5
            out.append((ti, slot, mid_other, mid_z))
    return out


def _seam_sort_key(e):
    return (e[2], e[3], e[0], e[1])


def _match_seam(edges_a, edges_b):
    """Greedy nearest-neighbour pairing of border edges across one seam.

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
            if d_along > SEAM_TOLERANCE:
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


def build_edge_links(navm_cache: dict, verbose: bool = True) -> int:
    """Add reciprocal Portal links between adjacent exterior cell navmeshes.

    navm_cache: {key: (navm_bytes, meta)} from _precompute_navmeshes.  Entries
    are rewritten in place with fresh bytes when links are added.

    Returns the number of links created.
    """
    from .pgrd_to_navm import _pack_navm_record
    from .writer import pack_subrecord, pack_string_subrecord

    # Decode every exterior mesh once, indexed by (worldspace, grid).
    views = {}
    holders = {}
    for key, value in navm_cache.items():
        if not value:
            continue
        navm_bytes, meta = value
        if not navm_bytes or not meta or not meta.get('is_exterior'):
            continue
        blob, prefix, suffix = _extract_nvnm(navm_bytes)
        if blob is None:
            continue
        try:
            view = NavMeshView(meta['fid'], blob)
        except (struct.error, IndexError):
            continue
        if not view.exterior:
            continue
        cell = (meta.get('wrld_fid'), meta['grid_x'], meta['grid_y'])
        views[cell] = view
        holders[cell] = (key, prefix, suffix, meta)

    # The set of navmeshes that will actually be written to the ESM: exactly the
    # ones with truthy bytes (the CELL/WRLD builders write iff navm_bytes is
    # truthy, same predicate that populated `views`).  A link may only ever point
    # at one of these — a link to a burned/degenerate fid would be a dangling
    # Edge Link the engine derefs into a null navmesh.  `views` already excludes
    # None-byte meshes, so this is the definitive live set.
    live_fids = {v.fid for v in views.values()}

    made = 0
    # Sorted iteration + only the +X/+Y neighbours means each seam is visited
    # exactly once, in a stable order.
    for cell in sorted(views):
        wrld, gx, gy = cell
        view_a = views[cell]
        for dx, dy, axis, _ in _NEIGHBOURS:
            other = (wrld, gx + dx, gy + dy)
            view_b = views.get(other)
            if view_b is None:
                continue
            # The shared plane: cell A's upper edge on that axis.
            coord = ((gx + 1) * CELL_SIZE) if axis == 0 else ((gy + 1) * CELL_SIZE)
            edges_a = _border_edges(view_a, axis, coord)
            edges_b = _border_edges(view_b, axis, coord)
            if not edges_a or not edges_b:
                continue
            for ea, eb in _match_seam(edges_a, edges_b):
                view_a.add_link(ea[0], ea[1], view_b.fid, eb[0])
                view_b.add_link(eb[0], eb[1], view_a.fid, ea[0])
                made += 2

    # Final safety: drop any link whose target is not in the live set and
    # renumber each mesh's remaining links (the triangle edge fields store link
    # INDICES, so removing an entry shifts every later index).  In practice this
    # closes the small residue of links to meshes that end up unwritten.
    for view in views.values():
        if any(lk[1] not in live_fids for lk in view.links):
            _prune_links(view, live_fids)

    # Re-pack only the meshes that changed.
    rewritten = 0
    for cell, view in views.items():
        key, prefix, suffix, meta = holders[cell]
        # NVMI Edge Links = the distinct neighbour meshes this mesh's NVNM edge
        # links reach, self excluded — the vanilla NVMI rule (15,115/15,462
        # exact matches; the 347 outliers differ only by a self-link, which
        # NVMI omits).  Stored on the meta so navi_builder can mirror it.
        meta['edge_link_fids'] = sorted(
            {lk[1] for lk in view.links if lk[1] != view.fid})
        if not view.dirty:
            continue
        new_nvnm = pack_subrecord('NVNM', view.pack())
        subs = prefix + new_nvnm + suffix
        navm_cache[key] = (_pack_navm_record(meta['fid'], subs), meta)
        rewritten += 1

    if verbose:
        total_ext = len(views)
        linked = sum(1 for v in views.values() if v.links)
        pct = (100.0 * linked / total_ext) if total_ext else 0.0
        print(f"  Navmesh edge links: {made} portals stitched across "
              f"{rewritten} cells ({linked}/{total_ext} exterior "
              f"navmeshes linked, {pct:.0f}%)")
    return made


def _extract_nvnm(navm_bytes: bytes):
    """Split a packed NAVM record into (nvnm_blob, subs_before, subs_after).

    The record is uncompressed at this stage (compression happens in
    _pack_navm_record), so walk its subrecords directly.
    """
    import zlib
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
    while p < len(body) - 5:
        sig = body[p:p + 4]
        ln = struct.unpack_from('<H', body, p + 4)[0]
        chunk = body[p:p + 6 + ln]
        if sig == b'NVNM' and blob is None:
            blob = body[p + 6:p + 6 + ln]
        elif blob is None:
            before += chunk
        else:
            after += chunk
        p += 6 + ln
    return blob, before, after
