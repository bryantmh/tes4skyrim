"""Tests for LAND VHGT decoding and NAVM/NAVI binary packing.

The NVNM/NVMI/NAVI layouts are validated against real Skyrim.esm records
(tools/navmesh/dump.py).  Those layouts are byte-exact and must not drift.

The geometry-behaviour tests that used to live here ("floors become navmesh,
walls do not", "an NPC walks OVER a rug but AROUND a table") drove the
voxelize/region/spanmesh generator through synthetic collision soups.  That
generator was replaced by the pathgrid CORRIDOR model (tes5_import/navmesh/
corridor.py) and has now been deleted, so those tests and their fixtures went
with it — they asserted rules the corridor model does not have: it derives the
mesh from the pathgrid rather than discovering walkable surface from collision.
Corridor geometry is verified against real cells instead, by the tools in
tools/ (navmesh_check.py, navmesh_reach.py, navmesh_slope_check.py).
"""

import struct
import zlib

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

from tes5_import import pgrd_to_navm as p2n  # noqa: E402
from tes5_import.navi_builder import build_navi_record  # noqa: E402


class FakeWriter:
    def __init__(self, start=0x01000800):
        self._next = start

    def alloc_formid(self):
        fid = self._next
        self._next += 1
        return fid


    def derive_formid(self, site, key):
        if not hasattr(self, '_derived'):
            self._derived = {}
        k = (site, repr(key))
        if k not in self._derived:
            self._derived[k] = self.alloc_formid()
        return self._derived[k]
# ---------------------------------------------------------------------------
# LAND VHGT decoding
# ---------------------------------------------------------------------------

def test_vhgt_offset_is_scaled_like_the_deltas():
    """The VHGT offset float is in delta units and scales by 8, like the deltas.

    Regression: the old decoder did `offset / 8` in and `* 8` out, which cancels
    for the deltas but ANNIHILATES the offset — putting exterior terrain
    thousands of units below the objects standing on it (Tamriel 47,6 decoded to
    z=829..3213 while its own REFRs sat at z=18288..19776).
    """
    from tes5_import.navmesh.world import decode_vhgt
    offset = 2397.0
    data = struct.pack('<f', offset) + bytes(33 * 33)   # all-zero gradients
    grid = decode_vhgt(data.hex())
    assert grid is not None
    assert grid.min() == pytest.approx(offset * 8.0)
    assert grid.max() == pytest.approx(offset * 8.0)


def test_vhgt_constant_slope_accumulates_linearly():
    from tes5_import.navmesh.world import decode_vhgt
    deltas = bytes([1]) * (33 * 33)          # +1 per step on both axes
    data = struct.pack('<f', 0.0) + deltas
    grid = decode_vhgt(data.hex())
    assert grid[0][0] == pytest.approx(8.0)
    assert grid[0][32] == pytest.approx(33 * 8.0)
    assert grid[32][0] == pytest.approx(33 * 8.0)


# ---------------------------------------------------------------------------
# NVNM / NAVI binary layout (validated against real Skyrim.esm records)
# ---------------------------------------------------------------------------

def _decode_nvnm(nvnm):
    p = 0
    ver = struct.unpack_from('<I', nvnm, p)[0]
    p += 4
    crc = struct.unpack_from('<I', nvnm, p)[0]
    p += 4
    wrld = struct.unpack_from('<I', nvnm, p)[0]
    p += 4
    if wrld == 0:
        parent = struct.unpack_from('<I', nvnm, p)[0]
        grid = None
    else:
        gy, gx = struct.unpack_from('<hh', nvnm, p)
        parent, grid = None, (gx, gy)
    p += 4
    nv = struct.unpack_from('<I', nvnm, p)[0]
    p += 4
    verts = []
    for _ in range(nv):
        verts.append(struct.unpack_from('<fff', nvnm, p))
        p += 12
    nt = struct.unpack_from('<I', nvnm, p)[0]
    p += 4
    tris, adj, flags = [], [], []
    for _ in range(nt):
        t = struct.unpack_from('<6h2H', nvnm, p)
        tris.append(t[0:3])
        adj.append(t[3:6])
        flags.append(t[6])
        p += 16
    return {'ver': ver, 'crc': crc, 'wrld': wrld, 'parent': parent,
            'grid': grid, 'verts': verts, 'tris': tris, 'adj': adj,
            'flags': flags}


def test_nvnm_header_constants():
    """Version and CRC constants must not drift from Skyrim.esm."""
    assert p2n._NVNM_VERSION == 12
    assert p2n._PATHING_CELL_CRC == 0xA5E9A03C
    assert p2n._PATHING_DOOR_CRC == 0xE48B73F3


def test_nvnm_roundtrip_and_adjacency_symmetry():
    verts = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0),
             (0.0, 100.0, 0.0)]
    tris = [(0, 1, 2), (0, 2, 3)]
    adj = p2n._compute_adjacency(tris)
    nvnm = p2n.pack_nvnm(verts, tris, adj, [0] * len(tris),
                          wrld_fid=0, cell_fid=0x00001234,
                          grid_x=0, grid_y=0, is_exterior=False)
    d = _decode_nvnm(nvnm)
    assert d['ver'] == 12
    assert d['crc'] == 0xA5E9A03C
    assert d['parent'] == 0x00001234
    assert len(d['verts']) == 4
    assert len(d['tris']) == 2
    for ti, a in enumerate(d['adj']):
        for tj in a:
            if tj >= 0:
                assert ti in d['adj'][tj], "adjacency is not symmetric"


def test_nvnm_exterior_writes_grid_y_then_x():
    verts = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0)]
    tris = [(0, 1, 2)]
    nvnm = p2n.pack_nvnm(verts, tris, p2n._compute_adjacency(tris), [0],
                          wrld_fid=0x0000003C, cell_fid=0,
                          grid_x=7, grid_y=-3, is_exterior=True)
    d = _decode_nvnm(nvnm)
    assert d['wrld'] == 0x0000003C
    assert d['grid'] == (7, -3)


def test_all_triangles_carry_found_flag():
    verts = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0)]
    tris = [(0, 1, 2)]
    nvnm = p2n.pack_nvnm(verts, tris, p2n._compute_adjacency(tris), [0],
                          wrld_fid=0, cell_fid=1, grid_x=0, grid_y=0,
                          is_exterior=False)
    d = _decode_nvnm(nvnm)
    assert d['flags'][0] & p2n._TRI_FLAG_FOUND


def test_water_flag_set_below_water_height():
    verts = [(0.0, 0.0, -50.0), (100.0, 0.0, -50.0), (100.0, 100.0, -50.0),
             (0.0, 0.0, 50.0), (100.0, 0.0, 50.0), (100.0, 100.0, 50.0)]
    tris = [(0, 1, 2), (3, 4, 5)]
    flags = p2n._compute_water_flags(verts, tris, water_z=0.0)
    assert flags[0] == p2n._TRI_FLAG_WATER
    assert flags[1] == 0


def test_duplicate_indexed_faces_are_removed_and_ledges_remapped():
    tris = [(0, 1, 2), (2, 1, 0), (2, 3, 4)]
    ledges = [(1, 2, 64.0), (0, 2, 64.0), (0, 1, 12.0)]

    got_tris, got_ledges = p2n._dedupe_indexed_triangles(tris, ledges)

    assert got_tris == [(0, 1, 2), (2, 3, 4)]
    assert got_ledges == [(0, 1, 64.0)]
    assert p2n._compute_adjacency(got_tris) == [(-1, -1, -1),
                                                (-1, -1, -1)]


def test_navm_record_is_compressed():
    verts = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0)]
    tris = [(0, 1, 2)]
    nvnm = p2n.pack_nvnm(verts, tris, p2n._compute_adjacency(tris), [0],
                          wrld_fid=0, cell_fid=1, grid_x=0, grid_y=0,
                          is_exterior=False)
    from tes5_import.writer import pack_subrecord
    rec = p2n.pack_navm_record(0x01000801, pack_subrecord('NVNM', nvnm))
    sig, size, flags, formid = struct.unpack_from('<4sIII', rec, 0)
    assert sig == b'NAVM'
    assert flags & 0x00040000, "NAVM must be written compressed"
    assert formid == 0x01000801
    subs = zlib.decompress(rec[24:24 + size][4:])
    assert subs[:4] == b'NVNM'


def test_navi_record_layout():
    metas = [{
        'fid': 0x01000801, 'wrld_fid': 0, 'cell_fid': 0x00001234,
        'grid_x': 0, 'grid_y': 0, 'is_exterior': False,
        'center': (1.0, 2.0, 3.0), 'base_objects': [],
    }]
    rec = build_navi_record(0x01000900, metas)
    assert rec[:4] == b'NAVI'
    # NAVI carries no EDID; the first subrecord is NVER.
    assert rec[24:28] == b'NVER'


# ---------------------------------------------------------------------------
# Edge Links — cross-cell navmesh stitching
# ---------------------------------------------------------------------------
# Without Edge Links every cell navmesh is an ISLAND: an actor paths fine inside
# its own cell and can never cross a cell boundary, so any AI package with an
# out-of-cell destination starts (the actor stands up) and never moves.  Vanilla
# links 12,145 of 14,440 exterior navmeshes (84%), 194,744 links in total.
#
# Binary contract (verified against Skyrim.esm, which parses 15,949/15,949 clean):
#   Edge Link = Type(U32) + Navmesh(FormID U32) + Triangle(S16) = 10 bytes
#   Triangle flag bits 0/1/2 = 'Edge 0-1 / 1-2 / 2-0 Link'; when set that edge
#   field is an INDEX into the Edge Links array, not a neighbour triangle.
#   Type 0 = Portal (the cell-seam link).

_CELL = 4096.0


def _edge_link_cell(gx, gy, fid, hug_west):
    """A 2-triangle quad hugging one vertical edge of an exterior cell."""
    from tes5_import.writer import pack_subrecord
    x0, x1 = gx * _CELL, (gx + 1) * _CELL
    y0, y1 = gy * _CELL, gy * _CELL + 512
    if hug_west:
        verts = [(x0, y0, 0.0), (x0 + 512, y0, 0.0),
                 (x0 + 512, y1, 0.0), (x0, y1, 0.0)]
    else:
        verts = [(x1 - 512, y0, 0.0), (x1, y0, 0.0),
                 (x1, y1, 0.0), (x1 - 512, y1, 0.0)]
    nvnm = p2n.pack_nvnm(verts, [(0, 1, 2), (0, 2, 3)],
                          [(-1, -1, 1), (0, -1, -1)], [0, 0],
                          0x0100003C, 0, gx, gy, True)
    rec = p2n.pack_navm_record(fid, pack_subrecord('NVNM', nvnm))
    meta = {'fid': fid, 'wrld_fid': 0x0100003C, 'grid_x': gx, 'grid_y': gy,
            'is_exterior': True}
    return rec, meta


def _two_cell_cache():
    return {
        ('a', 1): _edge_link_cell(0, 0, 0x1000, hug_west=False),
        ('b', 2): _edge_link_cell(1, 0, 0x2000, hug_west=True),
    }


def _decode_view(navm_bytes, fid):
    from tes5_import import navm_edge_links as el
    blob, _pre, _post = el._extract_nvnm(navm_bytes)
    return el.NavMeshView(fid, blob)


def test_adjacent_cells_get_reciprocal_portal_links():
    """Two cells meeting at a seam must link to each other, both ways."""
    from tes5_import.navm_edge_links import build_edge_links, LINK_TYPE_PORTAL
    cache = _two_cell_cache()
    made = build_edge_links(cache, verbose=False)
    assert made == 2, 'one link per side of the seam'

    a = _decode_view(cache[('a', 1)][0], 0x1000)
    b = _decode_view(cache[('b', 2)][0], 0x2000)
    assert len(a.links) == 1 and len(b.links) == 1
    # Reciprocal: each names the OTHER mesh.
    assert a.links[0][0] == LINK_TYPE_PORTAL
    assert a.links[0][1] == 0x2000
    assert b.links[0][1] == 0x1000
    # ...and each points at a real triangle in the other mesh.
    assert 0 <= a.links[0][2] < len(b.tris)
    assert 0 <= b.links[0][2] < len(a.tris)


def test_linked_edge_field_is_an_index_not_a_neighbour():
    """The flagged edge field must become an index into Edge Links."""
    from tes5_import.navm_edge_links import build_edge_links
    cache = _two_cell_cache()
    build_edge_links(cache, verbose=False)
    a = _decode_view(cache[('a', 1)][0], 0x1000)
    flagged = [(i, t) for i, t in enumerate(a.tris) if t[6] & 0x0007]
    assert flagged, 'some triangle edge must be flagged as an external link'
    for _i, t in flagged:
        for slot in range(3):
            if t[6] & (1 << slot):
                idx = t[3 + slot]
                assert 0 <= idx < len(a.links), \
                    'flagged edge must index the Edge Links array'


def test_non_adjacent_cells_are_not_linked():
    """Cells that share no seam must not be stitched."""
    from tes5_import.navm_edge_links import build_edge_links
    cache = {
        ('a', 1): _edge_link_cell(0, 0, 0x1000, hug_west=False),
        ('far', 2): _edge_link_cell(5, 5, 0x3000, hug_west=True),
    }
    assert build_edge_links(cache, verbose=False) == 0


def test_edge_linked_navm_round_trips_to_exact_length():
    """A re-packed NAVM must still consume exactly its blob (no drift)."""
    from tes5_import.navm_edge_links import build_edge_links
    cache = _two_cell_cache()
    build_edge_links(cache, verbose=False)
    for key, (rec, meta) in cache.items():
        size, flags = struct.unpack_from('<II', rec, 4)
        body = rec[24:24 + size]
        if flags & 0x00040000:
            body = zlib.decompress(body[4:])
        assert body[:4] == b'NVNM'


def test_edge_links_are_deterministic():
    """Same input must give byte-identical output (the ESM is reproducible)."""
    from tes5_import.navm_edge_links import build_edge_links
    c1, c2 = _two_cell_cache(), _two_cell_cache()
    build_edge_links(c1, verbose=False)
    build_edge_links(c2, verbose=False)
    for key in c1:
        assert c1[key][0] == c2[key][0]


# ---------------------------------------------------------------------------
# NAVI connectivity mirror — the engine's navmesh info map
# ---------------------------------------------------------------------------
# The runtime plans cross-navmesh paths on NAVI's NVMI entries, not on the NVNM
# blobs: an NVMI with empty Edge/Door Link arrays declares its navmesh an
# unreachable island even when the NVNM carries portal links and door
# triangles.  Contract verified against all 15,462 Skyrim.esm NVMI entries:
#   Edge Links == distinct NVNM edge-link neighbours, self excluded;
#   Door Links == the mesh's own NVNM door-triangle refs, CRC "PathingDoor".
# And the NAVI record itself must OVERRIDE Skyrim.esm's singleton 0x00012FB4
# (as all four DLC ESMs do) — under a fresh FormID the engine never consults
# it and no navmesh is registered at all.

def _parse_nvmi_entries(navi_rec):
    entries = {}
    off = 24
    while off + 6 <= len(navi_rec):
        sig = navi_rec[off:off + 4]
        sz = struct.unpack_from('<H', navi_rec, off + 4)[0]
        sd = navi_rec[off + 6:off + 6 + sz]
        off += 6 + sz
        if sig != b'NVMI':
            continue
        p = 0
        fid = struct.unpack_from('<I', sd, p)[0]; p += 24
        ne = struct.unpack_from('<I', sd, p)[0]; p += 4
        edges = list(struct.unpack_from(f'<{ne}I', sd, p)); p += 4 * ne
        npf = struct.unpack_from('<I', sd, p)[0]; p += 4 + 4 * npf
        nd = struct.unpack_from('<I', sd, p)[0]; p += 4
        doors = []
        for _ in range(nd):
            crc, dref = struct.unpack_from('<II', sd, p); p += 8
            assert crc == 0xE48B73F3, 'door link CRC must be PathingDoor'
            doors.append(dref)
        entries[fid] = (edges, doors)
    return entries


def test_nvmi_mirrors_edge_links_after_stitching():
    """Stitched meshes must advertise their neighbours in their NVMI entry."""
    from tes5_import.navm_edge_links import build_edge_links
    cache = _two_cell_cache()
    build_edge_links(cache, verbose=False)
    metas = [meta for (_rec, meta) in cache.values()]
    for m in metas:
        m.setdefault('cell_fid', 0)
        m.setdefault('center', (0.0, 0.0, 0.0))
    rec = build_navi_record(0x00012FB4, metas)
    entries = _parse_nvmi_entries(rec)
    assert entries[0x1000][0] == [0x2000]
    assert entries[0x2000][0] == [0x1000]


def test_nvmi_mirrors_door_links():
    """A mesh's door-triangle refs must appear as NVMI Door Links."""
    meta = {
        'fid': 0x01000801, 'wrld_fid': 0, 'cell_fid': 0x00001234,
        'grid_x': 0, 'grid_y': 0, 'is_exterior': False,
        'center': (1.0, 2.0, 3.0), 'base_objects': [],
        'door_refs': [0x01047A11, 0x01047A15],
    }
    rec = build_navi_record(0x00012FB4, [meta])
    entries = _parse_nvmi_entries(rec)
    assert entries[0x01000801][1] == [0x01047A11, 0x01047A15]


def test_navi_is_override_of_vanilla_singleton():
    from tes5_import.navi_builder import NAVI_SINGLETON_FID
    assert NAVI_SINGLETON_FID == 0x00012FB4


class TestNativeGrowGuards:
    """The native corridor-grow extension must never abort the process.

    A C++ exception escaping the extension (its allocations sit inside
    Py_BEGIN_ALLOW_THREADS) reaches std::terminate() and kills the interpreter
    by abort(). In a process-pool worker that surfaced in the parent only as an
    opaque BrokenProcessPool with no traceback -- and under pythonw.exe the
    worker's stderr goes nowhere either, so nothing explained the failure.

    Nehrim triggers it for real: 17 REFRs across 10 base objects carry an
    uninitialised PosY of 8.936455989415117e+17, which stretched a cell's
    triangle soup to 8.9e17 units and blew the dense bucket grid up to 5.4e14
    buckets (a 4-billion-GB request).
    """

    def _stations(self, np):
        # One march station: cx,cy,cz,dirx,diry,tanx,tany,lo,edge_index
        return np.array([[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, -1.0]],
                        dtype=np.float64)

    def _tri(self, np, y):
        return np.array([[[0.0, y, 0.0], [10.0, y, 0.0], [0.0, y, 10.0]]],
                        dtype=np.float64)

    def test_absurd_extent_raises_instead_of_aborting(self):
        """A garbage coordinate must raise ValueError, not abort the process."""
        np = pytest.importorskip("numpy")
        from tes5_import.navmesh import corridor_grow as cg

        blocking = np.concatenate([self._tri(np, 0.0),
                                   self._tri(np, 8.936455989415117e+17)])
        with pytest.raises(ValueError):
            cg._native.grow_strips(
                blocking, None,
                np.zeros((0, 2), dtype=np.float64),
                np.zeros((0, 2), dtype=np.int32),
                np.zeros(0, dtype=np.float64),
                self._stations(np), cg._native_params())

    def test_non_finite_coordinate_raises(self):
        """NaN/Inf propagate through the extent maths, so reject them up front."""
        np = pytest.importorskip("numpy")
        from tes5_import.navmesh import corridor_grow as cg

        for bad in (float('nan'), float('inf')):
            blocking = np.concatenate([self._tri(np, 0.0), self._tri(np, bad)])
            with pytest.raises(ValueError):
                cg._native.grow_strips(
                    blocking, None,
                    np.zeros((0, 2), dtype=np.float64),
                    np.zeros((0, 2), dtype=np.int32),
                    np.zeros(0, dtype=np.float64),
                    self._stations(np), cg._native_params())

    def test_normal_soup_still_grows(self):
        """The guards must not reject legitimate cell-sized geometry."""
        np = pytest.importorskip("numpy")
        from tes5_import.navmesh import corridor_grow as cg

        # A 4096-unit exterior cell is 33x33 buckets -- far under the ceiling.
        blocking = np.concatenate([self._tri(np, 0.0), self._tri(np, 4096.0)])
        out = cg._native.grow_strips(
            blocking, None,
            np.zeros((0, 2), dtype=np.float64),
            np.zeros((0, 2), dtype=np.int32),
            np.zeros(0, dtype=np.float64),
            self._stations(np), cg._native_params())
        assert len(out) == 1
        assert out[0] >= 0.0


class TestPlacementSanity:
    """Garbage REFR placements must be dropped before they reach the native code."""

    def test_absurd_and_non_finite_placements_rejected(self):
        np = pytest.importorskip("numpy")
        from tes5_import.navmesh.world import (_finite_placement,
                                              _MAX_PLACEMENT)

        ok = np.array([100.0, -200.0, 30.0])
        assert _finite_placement(ok, 1.0)
        # Nehrim's real garbage value.
        assert not _finite_placement(
            np.array([1.68e-36, 8.936455989415117e+17, 0.0]), 1.0)
        assert not _finite_placement(
            np.array([0.0, float('nan'), 0.0]), 1.0)
        assert not _finite_placement(
            np.array([0.0, float('inf'), 0.0]), 1.0)
        assert not _finite_placement(ok, float('nan'))
        assert not _finite_placement(
            np.array([_MAX_PLACEMENT * 2, 0.0, 0.0]), 1.0)


class TestStoreyGroupsDoorGrouping:
    """group_polys must stay index-aligned with the group list it mirrors."""

    def test_unmatched_door_strips_do_not_desync_group_polys(self):
        """A door that matches no group appends to `out`; group_polys must grow too.

        Without that, the NEXT door's group_polys[gi] ran off the end and raised
        IndexError -- which run_job swallowed, so the cell silently shipped with
        no navmesh at all (measured on Nehrim cells 012217C1 and 01193F44).
        """
        pytest.importorskip("numpy")
        pytest.importorskip("shapely")
        from tes5_import.navmesh import corridor_union as cu

        def ribbon(x, y, z, edge):
            half = 20.0
            return {
                'edge': edge,
                'a': (x, y, z), 'b': (x + 100.0, y, z),
                'na': (x, y, z), 'nb': (x + 100.0, y, z),
                'poly': [(x, y - half), (x + 100.0, y - half),
                         (x + 100.0, y + half), (x, y + half)],
            }

        # The failure needs a specific ORDER. Door 1 matches nothing (far from
        # the ribbon in plan), so it self-appends as a new group. Door 2 then
        # OVERLAPS door 1 in plan and height, so the `enumerate(out)` scan
        # reaches that newly-appended index and reads group_polys[gi] -- which,
        # unless group_polys grew in step, is off the end of the list.
        strips = [
            ribbon(0.0, 0.0, 0.0, (0, 1)),              # real ribbon
            ribbon(50000.0, 50000.0, 9000.0, (-1, -1)),  # door 1: no match
            ribbon(50010.0, 50000.0, 9000.0, (-1, -1)),  # door 2: overlaps it
        ]
        groups = cu._storey_groups(strips)
        placed = sum(len(g) for g in groups)
        assert placed == len(strips), 'every strip must land in exactly one group'
