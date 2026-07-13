"""Tests for collision-driven navmesh generation and NAVM/NAVI packing.

The navmesh is built by VOXELIZING the Havok collision meshes of everything
placed in a cell (see tes5_import/navmesh/), so these tests feed synthetic
collision soups — a floor slab, walls, a table, a rug — through the real
pipeline and assert the behaviours we actually care about:

  * floors become navmesh, walls do not
  * an NPC walks OVER a rug but AROUND a table (the world-space step-height
    rule, which is the whole reason obstruction is not decided per-mesh)
  * stairs connect; two stacked floors do not

Plus the NVNM/NVMI binary layout, validated against real Skyrim.esm records
(tools/navmesh_dump.py).  That layout is byte-exact and must not drift.
"""

import struct
import zlib

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

from tes5_import import pgrd_to_navm as p2n  # noqa: E402
from tes5_import.navi_builder import build_navi_record  # noqa: E402
from tes5_import.navmesh import build as nmbuild  # noqa: E402
from tes5_import.navmesh import params, region, spanmesh, voxel  # noqa: E402


class FakeWriter:
    def __init__(self, start=0x01000800):
        self._next = start

    def alloc_formid(self):
        fid = self._next
        self._next += 1
        return fid


# ---------------------------------------------------------------------------
# Synthetic collision helpers
# ---------------------------------------------------------------------------

def _quad(x0, y0, x1, y1, z):
    """Two triangles forming a horizontal slab at height z (flat 9N list)."""
    return [x0, y0, z, x1, y0, z, x1, y1, z,
            x0, y0, z, x1, y1, z, x0, y1, z]


def _wall(x0, y0, x1, y1, z0, z1):
    """Two triangles forming a vertical wall quad."""
    return [x0, y0, z0, x1, y1, z0, x1, y1, z1,
            x0, y0, z0, x1, y1, z1, x0, y0, z1]


def _box(cx, cy, z0, z1, half):
    """A solid box: walkable top + four blocking sides."""
    w = _quad(cx - half, cy - half, cx + half, cy + half, z1)
    b = []
    b += _wall(cx - half, cy - half, cx + half, cy - half, z0, z1)
    b += _wall(cx + half, cy - half, cx + half, cy + half, z0, z1)
    b += _wall(cx + half, cy + half, cx - half, cy + half, z0, z1)
    b += _wall(cx - half, cy + half, cx - half, cy - half, z0, z1)
    return w, b


def _refr(fid, base, x, y, z, rot_z=0.0, scale=1.0):
    return {'Signature': 'REFR', 'FormID': fid, 'NAME': base,
            'PosX': str(x), 'PosY': str(y), 'PosZ': str(z),
            'RotX': '0.0', 'RotY': '0.0', 'RotZ': str(rot_z),
            'XSCL.Scale': str(scale)}


def _room_collision(r=500.0, h=200.0):
    """A 2r x 2r room: floor slab + 4 perimeter walls, centred on the origin."""
    block = []
    block += _wall(-r, -r, r, -r, 0.0, h)
    block += _wall(r, -r, r, r, 0.0, h)
    block += _wall(r, r, -r, r, 0.0, h)
    block += _wall(-r, r, -r, -r, 0.0, h)
    return {'w': _quad(-r, -r, r, r, 0.0), 'b': block}


def _nodes_grid(n=3, spacing=250.0, z=0.0):
    """n x n pathgrid nodes centred on the origin, 4-connected."""
    nodes = []
    for iy in range(n):
        for ix in range(n):
            nodes.append(((ix - (n - 1) / 2) * spacing,
                          (iy - (n - 1) / 2) * spacing, z))
    edges = []
    for iy in range(n):
        for ix in range(n):
            i = iy * n + ix
            if ix + 1 < n:
                edges.append((i, iy * n + ix + 1))
            if iy + 1 < n:
                edges.append((i, (iy + 1) * n + ix))
    return nodes, edges


def _build_cell(soups, refrs, nodes, edges, base_model):
    """Run the real builder with an injected collision accessor."""
    return nmbuild.build_navmesh(refrs, base_model, soups.get, nodes, edges)


def _centroids(verts, tris):
    for t in tris:
        yield (sum(verts[i][0] for i in t) / 3.0,
               sum(verts[i][1] for i in t) / 3.0,
               sum(verts[i][2] for i in t) / 3.0)


def _components(verts, tris):
    """Number of connected components (an NPC cannot cross between them)."""
    parent = list(range(len(verts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b, c) in tris:
        parent[find(a)] = find(b)
        parent[find(b)] = find(c)
    return len({find(a) for (a, _b, _c) in tris})


def _covered(verts, tris, px, py):
    """Is (px, py) inside any navmesh triangle?

    Asserting on triangle CENTROIDS instead is a trap: it only happens to work
    while triangles are small.  Once the mesher emits a triangle larger than the
    feature under test — a rug, say — the feature is fully covered but no centroid
    lands on it, and the test fails on a mesh that is entirely correct.
    """
    for (ia, ib, ic) in tris:
        a, b, c = verts[ia], verts[ib], verts[ic]
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-9:
            continue
        l0 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / d
        l1 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / d
        l2 = 1.0 - l0 - l1
        if l0 >= -1e-6 and l1 >= -1e-6 and l2 >= -1e-6:
            return True
    return False


# ---------------------------------------------------------------------------
# Voxel / region behaviour — the core rules
# ---------------------------------------------------------------------------

def test_flat_floor_is_one_region():
    hf = voxel.Heightfield(0, 0, 0, 10, 10, 16.0, 8.0)
    for y in range(10):
        for x in range(10):
            hf.add_span(x, y, -8, 0, True)
    _ro, regions = region.build_regions(hf)
    assert len(regions) == 1


def test_two_stacked_floors_stay_separate_regions():
    """A second storey must never merge into the first."""
    hf = voxel.Heightfield(0, 0, 0, 10, 10, 16.0, 8.0)
    for y in range(10):
        for x in range(10):
            hf.add_span(x, y, -8, 0, True)
            hf.add_span(x, y, 192, 200, True)
    _ro, regions = region.build_regions(hf)
    assert len(regions) == 2


def test_staircase_connects_into_one_region():
    """Steps within MAX_CLIMB of each other must form one walkable region."""
    step = params.MAX_CLIMB - 4.0
    hf = voxel.Heightfield(0, 0, 0, 10, 3, 16.0, 8.0)
    for x in range(10):
        for y in range(3):
            hf.add_span(x, y, x * step - 8, x * step, True)
    _ro, regions = region.build_regions(hf)
    assert len(regions) == 1


def test_wall_span_does_not_swallow_the_floor():
    """A wall standing ON a floor must not merge into one giant blocking span.

    Regression: merging spans by mere adjacency fused wall and floor into a
    single 400u-tall BLOCKING span, erasing the floor beneath it (observed as a
    column reading -260..235 BLOCKING under a pathgrid node standing at -254).
    """
    hf = voxel.Heightfield(0, 0, 0, 4, 4, 16.0, 8.0)
    hf.add_span(1, 1, -8, 0, True)        # floor
    hf.add_span(1, 1, 0, 400, False)      # wall rising from it
    col = hf.spans[1 * 4 + 1]
    assert any(s[2] for s in col), "the floor span was destroyed by the wall"


# ---------------------------------------------------------------------------
# The step-over rule: rugs vs tables (decided in WORLD space, never per-mesh)
# ---------------------------------------------------------------------------

def test_rug_is_walked_over_not_carved_around():
    """A low flat object must leave the floor beneath it navigable."""
    soups = {'room.nif': _room_collision(),
             'rug.nif': {'w': _quad(-120, -120, 120, 120, 2.0), 'b': []}}
    base_model = {1: 'room.nif', 2: 'rug.nif'}
    refrs = [_refr('00000001', '00000001', 0, 0, 0),
             _refr('00000002', '00000002', 0, 0, 0)]
    nodes, edges = _nodes_grid()

    verts, tris = _build_cell(soups, refrs, nodes, edges, base_model)
    assert tris
    assert _covered(verts, tris, 0.0, 0.0), \
        "navmesh was carved around a rug instead of over it"


def test_table_blocks_the_floor_beneath_it():
    """A tall object must NOT leave its own footprint navigable."""
    tw, tb = _box(0, 0, 0.0, 120.0, 110.0)   # 220x220 table, 120u tall
    soups = {'room.nif': _room_collision(),
             'table.nif': {'w': tw, 'b': tb}}
    base_model = {1: 'room.nif', 2: 'table.nif'}
    refrs = [_refr('00000001', '00000001', 0, 0, 0),
             _refr('00000002', '00000002', 0, 0, 0)]
    # Nodes routed AROUND the table, as Bethesda would author them.
    nodes = [(-300, -300, 0), (300, -300, 0), (300, 300, 0), (-300, 300, 0)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0)]

    verts, tris = _build_cell(soups, refrs, nodes, edges, base_model)
    assert tris
    # Test COVERAGE, not centroids: a single big triangle laid straight over the
    # table has no centroid inside it and would sail through a centroid check.
    assert not _covered(verts, tris, 0.0, 0.0), \
        "navmesh runs through a 120u-tall table"


# ---------------------------------------------------------------------------
# Walls
# ---------------------------------------------------------------------------

def test_navmesh_stays_inside_the_walls():
    soups = {'room.nif': _room_collision()}
    base_model = {1: 'room.nif'}
    refrs = [_refr('00000001', '00000001', 0, 0, 0)]
    nodes, edges = _nodes_grid()

    verts, tris = _build_cell(soups, refrs, nodes, edges, base_model)
    assert tris
    for (x, y, _z) in verts:
        assert -520 <= x <= 520 and -520 <= y <= 520, \
            "navmesh escaped the room walls"


# ---------------------------------------------------------------------------
# Span mesher — the guarantees that replaced the contour/height-map mesher
# ---------------------------------------------------------------------------

def test_flat_floor_meshes_as_one_connected_piece():
    """A single walkable surface must come out as ONE connected component."""
    hf = voxel.Heightfield(0, 0, 0, 8, 8, 16.0, 8.0)
    for y in range(8):
        for x in range(8):
            hf.add_span(x, y, -8, 0, True)
    verts, tris = spanmesh.build_mesh(hf)
    assert tris
    assert _components(verts, tris) == 1


def test_two_storeys_are_never_joined_by_a_triangle():
    """No triangle may span two floors.

    This is THE bug the contour mesher could not avoid: a height map holds one Z
    per column, so two storeys sharing a column forced the triangulator to bridge
    them, producing a wall of near-vertical triangles "connecting" the floors.
    The span graph makes it unrepresentable — spans a storey apart are never
    adjacent, so no triangle can have a corner on each.
    """
    hf = voxel.Heightfield(0, 0, 0, 8, 8, 16.0, 8.0)
    for y in range(8):
        for x in range(8):
            hf.add_span(x, y, -8, 0, True)          # ground floor
            hf.add_span(x, y, 242, 250, True)       # storey above
    verts, tris = spanmesh.build_mesh(hf)
    assert tris
    for (a, b, c) in tris:
        zs = [verts[a][2], verts[b][2], verts[c][2]]
        assert max(zs) - min(zs) < params.MAX_CLIMB, \
            "a triangle bridged two storeys (z spread %.0f)" % (max(zs) - min(zs))


def test_a_staircase_stays_connected_to_its_floor():
    """A stair must mesh as one piece with the floor it rises from.

    Regression: the height-map mesher peeled a staircase into several layers,
    contoured each on its own, and left them joined only at a triangle corner —
    which an NPC cannot cross.
    """
    hf = voxel.Heightfield(0, 0, 0, 12, 4, 16.0, 8.0)
    for y in range(4):
        for x in range(4):                          # flat floor
            hf.add_span(x, y, -8, 0, True)
        for i, x in enumerate(range(4, 12)):        # stair, one riser per column
            z = (i + 1) * 20.0
            hf.add_span(x, y, z - 8, z, True)
    verts, tris = spanmesh.build_mesh(hf)
    assert tris
    assert _components(verts, tris) == 1, \
        "the staircase came out disconnected from its floor"


def test_a_ledge_taller_than_a_step_stays_separate():
    """Surfaces further apart than a step must NOT be joined."""
    hf = voxel.Heightfield(0, 0, 0, 8, 4, 16.0, 8.0)
    for y in range(4):
        for x in range(4):
            hf.add_span(x, y, -8, 0, True)          # floor
        for x in range(4, 8):
            hf.add_span(x, y, 192, 200, True)       # ledge 200u up
    verts, tris = spanmesh.build_mesh(hf)
    assert tris
    assert _components(verts, tris) == 2, \
        "an unclimbable ledge was joined to the floor"


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
    nvnm = p2n._pack_nvnm(verts, tris, adj, [0] * len(tris),
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
    nvnm = p2n._pack_nvnm(verts, tris, p2n._compute_adjacency(tris), [0],
                          wrld_fid=0x0000003C, cell_fid=0,
                          grid_x=7, grid_y=-3, is_exterior=True)
    d = _decode_nvnm(nvnm)
    assert d['wrld'] == 0x0000003C
    assert d['grid'] == (7, -3)


def test_all_triangles_carry_found_flag():
    verts = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0)]
    tris = [(0, 1, 2)]
    nvnm = p2n._pack_nvnm(verts, tris, p2n._compute_adjacency(tris), [0],
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


def test_navm_record_is_compressed():
    verts = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0)]
    tris = [(0, 1, 2)]
    nvnm = p2n._pack_nvnm(verts, tris, p2n._compute_adjacency(tris), [0],
                          wrld_fid=0, cell_fid=1, grid_x=0, grid_y=0,
                          is_exterior=False)
    from tes5_import.writer import pack_subrecord
    rec = p2n._pack_navm_record(0x01000801, pack_subrecord('NVNM', nvnm))
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
