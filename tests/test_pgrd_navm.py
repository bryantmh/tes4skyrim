"""Tests for PGRD (PathGrid) → NAVM (NavMesh) conversion and NAVI building.

Validates the NVNM binary layout against the structure dumped from a real
Skyrim.esm navmesh (see tools/navmesh_dump.py) and the NVMI/NAVI layout against
a real Skyrim.esm NAVI record.
"""

import struct
import zlib

import pytest

pytest.importorskip("scipy")
pytest.importorskip("numpy")

from tes5_import import pgrd_to_navm as p2n
from tes5_import.navi_builder import build_navi_record


class FakeWriter:
    """Minimal writer supplying sequential FormIDs."""

    def __init__(self, start=0x01000800):
        self._next = start

    def alloc_formid(self):
        fid = self._next
        self._next += 1
        return fid


# ---------------------------------------------------------------------------
# Test fixtures — synthetic pathgrids
# ---------------------------------------------------------------------------

def _grid_pgrd(cell_fid='00001234', wrld_fid=None, n=5, spacing=200.0):
    """Build an n×n grid pathgrid, each node connected to its neighbours."""
    rec = {'Signature': 'PGRD', 'FormID': '000104D1', 'EditorID': 'TestGrid'}
    if wrld_fid:
        rec['ParentWRLD'] = wrld_fid
    rec['ParentCELL'] = cell_fid

    points = []
    for iy in range(n):
        for ix in range(n):
            points.append((ix * spacing, iy * spacing, 100.0))
    rec['DATA.PointCount'] = str(len(points))

    def idx(ix, iy):
        return iy * n + ix

    edges = {i: [] for i in range(len(points))}
    for iy in range(n):
        for ix in range(n):
            i = idx(ix, iy)
            if ix + 1 < n:
                j = idx(ix + 1, iy)
                edges[i].append(j)
                edges[j].append(i)
            if iy + 1 < n:
                j = idx(ix, iy + 1)
                edges[i].append(j)
                edges[j].append(i)

    for i, (x, y, z) in enumerate(points):
        rec[f'Point[{i}].X'] = str(x)
        rec[f'Point[{i}].Y'] = str(y)
        rec[f'Point[{i}].Z'] = str(z)
        rec[f'Point[{i}].Connections'] = str(len(edges[i]))
        for j, tgt in enumerate(edges[i]):
            rec[f'Point[{i}].Edge[{j}]'] = str(tgt)
    return rec


# ---------------------------------------------------------------------------
# NVNM parsing helper (mirrors the on-disk layout)
# ---------------------------------------------------------------------------

def _parse_navm(navm_bytes):
    """Parse a compressed NAVM record -> (formid, subs dict, nvnm dict)."""
    sig, size, flags, formid = struct.unpack_from('<4sIII', navm_bytes, 0)
    assert sig == b'NAVM'
    assert flags & 0x00040000, "NAVM must set the Compressed flag"
    payload = navm_bytes[24:24 + size]
    uncompressed_size = struct.unpack_from('<I', payload, 0)[0]
    body = zlib.decompress(payload[4:])
    assert len(body) == uncompressed_size

    # Parse subrecords.
    subs = {}
    off = 0
    while off + 6 <= len(body):
        ssig = body[off:off + 4].decode('latin1')
        slen = struct.unpack_from('<H', body, off + 4)[0]
        off += 6
        subs[ssig] = body[off:off + slen]
        off += slen

    nvnm = _parse_nvnm(subs['NVNM'])
    return formid, subs, nvnm


def _parse_nvnm(d):
    p = 0
    ver, crc, wrld = struct.unpack_from('<III', d, p); p += 12
    out = {'version': ver, 'crc': crc, 'worldspace': wrld}
    if wrld == 0:
        out['parent_cell'] = struct.unpack_from('<I', d, p)[0]; p += 4
    else:
        gy, gx = struct.unpack_from('<hh', d, p); p += 4
        out['grid_y'], out['grid_x'] = gy, gx

    nv = struct.unpack_from('<I', d, p)[0]; p += 4
    verts = []
    for _ in range(nv):
        verts.append(struct.unpack_from('<fff', d, p)); p += 12
    out['vertices'] = verts

    nt = struct.unpack_from('<I', d, p)[0]; p += 4
    tris = []
    for _ in range(nt):
        t = struct.unpack_from('<6h2H', d, p); p += 16
        tris.append(t)
    out['triangles'] = tris

    ne = struct.unpack_from('<I', d, p)[0]; p += 4 + ne * 12
    nd = struct.unpack_from('<I', d, p)[0]; p += 4 + nd * 10
    nc = struct.unpack_from('<I', d, p)[0]; p += 4 + nc * 2
    out['edge_links'], out['door_tris'], out['cover_tris'] = ne, nd, nc

    divisor = struct.unpack_from('<I', d, p)[0]; p += 4
    out['divisor'] = divisor
    out['max_x_dist'], out['max_y_dist'] = struct.unpack_from('<ff', d, p); p += 8
    bbox = struct.unpack_from('<6f', d, p); p += 24
    out['bbox'] = bbox

    total = 0
    for _ in range(divisor * divisor):
        cnt = struct.unpack_from('<I', d, p)[0]; p += 4 + cnt * 2
        total += cnt
    out['grid_total'] = total
    out['consumed'] = p
    out['len'] = len(d)
    return out


# ---------------------------------------------------------------------------
# NVNM structure tests
# ---------------------------------------------------------------------------

def test_interior_navm_roundtrip():
    rec = _grid_pgrd(cell_fid='00001234', n=5)
    navm, meta = p2n.convert_PGRD(rec, writer=FakeWriter())
    assert navm is not None
    formid, subs, nvnm = _parse_navm(navm)

    assert nvnm['version'] == 12
    assert nvnm['crc'] == 0xA5E9A03C
    assert nvnm['worldspace'] == 0
    # Interior parent cell is the remapped FormID (offset 0 in tests -> unchanged).
    assert nvnm['parent_cell'] == meta['cell_fid']
    # NVNM must be fully consumed (no trailing/misaligned bytes).
    assert nvnm['consumed'] == nvnm['len']
    assert len(nvnm['vertices']) >= 3
    assert len(nvnm['triangles']) >= 1
    assert nvnm['edge_links'] == 0
    assert nvnm['door_tris'] == 0
    assert nvnm['cover_tris'] == 0


def test_exterior_navm_grid_coords():
    rec = _grid_pgrd(cell_fid='00005678', wrld_fid='0000003C', n=5)
    cell_rec = {'XCLC.X': '-30', 'XCLC.Y': '-1'}
    navm, meta = p2n.convert_PGRD(rec, writer=FakeWriter(), cell_rec=cell_rec)
    assert navm is not None
    _, _, nvnm = _parse_navm(navm)

    assert nvnm['worldspace'] == meta['wrld_fid']
    assert nvnm['worldspace'] != 0
    # Verified field order vs Skyrim.esm: Grid Y then Grid X.
    assert nvnm['grid_y'] == -1
    assert nvnm['grid_x'] == -30
    assert nvnm['consumed'] == nvnm['len']


def test_triangle_indices_in_range():
    rec = _grid_pgrd(n=6)
    navm, _ = p2n.convert_PGRD(rec, writer=FakeWriter())
    _, _, nvnm = _parse_navm(navm)
    nverts = len(nvnm['vertices'])
    for (v0, v1, v2, e01, e12, e20, flags, cover) in nvnm['triangles']:
        for v in (v0, v1, v2):
            assert 0 <= v < nverts
        for e in (e01, e12, e20):
            assert e == -1 or 0 <= e < len(nvnm['triangles'])
        assert cover == 0


def test_adjacency_is_symmetric():
    """If tri A lists tri B as an edge neighbour, B must list A back."""
    rec = _grid_pgrd(n=6)
    navm, _ = p2n.convert_PGRD(rec, writer=FakeWriter())
    _, _, nvnm = _parse_navm(navm)
    tris = nvnm['triangles']
    for ti, t in enumerate(tris):
        for e in t[3:6]:
            if e != -1:
                assert ti in tris[e][3:6], f"tri {ti}->{e} not mirrored"


def test_grid_indexes_all_triangles():
    rec = _grid_pgrd(n=5)
    navm, _ = p2n.convert_PGRD(rec, writer=FakeWriter())
    _, _, nvnm = _parse_navm(navm)
    # Every triangle lands in exactly one grid bucket (by centroid).
    assert nvnm['grid_total'] == len(nvnm['triangles'])


def test_max_distance_matches_span_over_divisor():
    rec = _grid_pgrd(n=5)
    navm, _ = p2n.convert_PGRD(rec, writer=FakeWriter())
    _, _, nvnm = _parse_navm(navm)
    min_x, min_y, min_z, max_x, max_y, max_z = nvnm['bbox']
    span_x = max_x - min_x
    span_y = max_y - min_y
    assert nvnm['max_x_dist'] == pytest.approx(span_x / nvnm['divisor'], rel=1e-4)
    assert nvnm['max_y_dist'] == pytest.approx(span_y / nvnm['divisor'], rel=1e-4)


def test_water_flag_set_below_water_height():
    rec = _grid_pgrd(n=5)
    # Nodes at z=100; water at z=500 → everything underwater → all Water-flagged.
    cell_rec = {'DATA.Flags': str(0x02), 'XCLW.WaterHeight': '500.0'}
    navm, _ = p2n.convert_PGRD(rec, writer=FakeWriter(), cell_rec=cell_rec)
    _, _, nvnm = _parse_navm(navm)
    assert nvnm['triangles'], "expected triangles"
    assert all(t[6] & 0x0200 for t in nvnm['triangles'])


def test_too_few_points_returns_none():
    rec = {'Signature': 'PGRD', 'DATA.PointCount': '1',
           'Point[0].X': '0', 'Point[0].Y': '0', 'Point[0].Z': '0',
           'Point[0].Connections': '0'}
    navm, meta = p2n.convert_PGRD(rec, writer=FakeWriter())
    assert navm is None and meta is None


def test_no_writer_returns_none():
    rec = _grid_pgrd(n=5)
    assert p2n.convert_PGRD(rec, writer=None) == (None, None)


# ---------------------------------------------------------------------------
# Exclusion (static-footprint carving) tests
# ---------------------------------------------------------------------------

def test_static_footprint_carves_triangles(monkeypatch):
    # A big STAT centred in the grid should remove central triangles.
    rec = _grid_pgrd(n=7, spacing=200.0)  # covers 0..1200 in x,y
    base_low = 0x00ABCDEF
    refr = {'Signature': 'REFR', 'NAME': format(base_low, '08X'),
            'PosX': '600', 'PosY': '600', 'PosZ': '100'}
    base_model = {base_low: 'tes4/stat/bigrock.nif'}

    # Fake a 400×400×200 mesh AABB centred at origin.
    monkeypatch.setattr(p2n, 'get_mesh_obnd',
                        lambda k: (-200, -200, -100, 200, 200, 100),
                        raising=False)
    # get_mesh_obnd is imported lazily inside _build_exclusion_zones; patch the
    # source module instead.
    import tes5_import.mesh_bounds as mb
    monkeypatch.setattr(mb, 'get_mesh_obnd',
                        lambda k: (-200, -200, -100, 200, 200, 100))

    navm_no, _ = p2n.convert_PGRD(_grid_pgrd(n=7, spacing=200.0),
                                  writer=FakeWriter())
    navm_yes, _ = p2n.convert_PGRD(rec, writer=FakeWriter(),
                                   refr_recs=[refr], base_model_by_fid=base_model)
    _, _, nvnm_no = _parse_navm(navm_no)
    _, _, nvnm_yes = _parse_navm(navm_yes)
    assert len(nvnm_yes['triangles']) < len(nvnm_no['triangles'])


def test_tiny_object_does_not_carve(monkeypatch):
    rec = _grid_pgrd(n=7, spacing=200.0)
    base_low = 0x00ABCDEF
    refr = {'Signature': 'REFR', 'NAME': format(base_low, '08X'),
            'PosX': '600', 'PosY': '600', 'PosZ': '100'}
    base_model = {base_low: 'tes4/clutter/cup.nif'}
    import tes5_import.mesh_bounds as mb
    # 20×20 half-extents → below MIN_EXCLUSION_HALF_EXTENT → ignored.
    monkeypatch.setattr(mb, 'get_mesh_obnd',
                        lambda k: (-20, -20, -10, 20, 20, 10))

    navm_no, _ = p2n.convert_PGRD(_grid_pgrd(n=7, spacing=200.0),
                                  writer=FakeWriter())
    navm_yes, _ = p2n.convert_PGRD(rec, writer=FakeWriter(),
                                   refr_recs=[refr], base_model_by_fid=base_model)
    _, _, nvnm_no = _parse_navm(navm_no)
    _, _, nvnm_yes = _parse_navm(navm_yes)
    assert len(nvnm_yes['triangles']) == len(nvnm_no['triangles'])


# ---------------------------------------------------------------------------
# NAVI builder tests (validated against Skyrim.esm NAVI 0x00012FB4)
# ---------------------------------------------------------------------------

def _parse_navi(navi_bytes):
    sig, size, flags, formid = struct.unpack_from('<4sIII', navi_bytes, 0)
    assert sig == b'NAVI'
    body = navi_bytes[24:24 + size]
    subs = []
    off = 0
    while off + 6 <= len(body):
        ssig = body[off:off + 4].decode('latin1')
        slen = struct.unpack_from('<H', body, off + 4)[0]
        off += 6
        subs.append((ssig, body[off:off + slen]))
        off += slen
    return formid, subs


def _parse_nvmi(d):
    p = 0
    fid = struct.unpack_from('<I', d, p)[0]; p += 4
    cat = struct.unpack_from('<I', d, p)[0]; p += 4
    center = struct.unpack_from('<fff', d, p); p += 12
    p += 4  # preferred merges flag
    ne = struct.unpack_from('<I', d, p)[0]; p += 4 + ne * 4
    npe = struct.unpack_from('<I', d, p)[0]; p += 4 + npe * 4
    ndl = struct.unpack_from('<I', d, p)[0]; p += 4 + ndl * 8
    is_island = d[p]; p += 1
    crc = struct.unpack_from('<I', d, p)[0]; p += 4
    ws = struct.unpack_from('<I', d, p)[0]; p += 4
    out = {'fid': fid, 'category': cat, 'center': center, 'is_island': is_island,
           'crc': crc, 'worldspace': ws}
    if ws == 0:
        out['parent_cell'] = struct.unpack_from('<I', d, p)[0]; p += 4
    else:
        gy, gx = struct.unpack_from('<hh', d, p); p += 4
        out['grid_y'], out['grid_x'] = gy, gx
    out['consumed'] = p
    out['len'] = len(d)
    return out


def test_navi_layout_interior_and_exterior():
    metas = [
        {'fid': 0x01000900, 'wrld_fid': 0, 'cell_fid': 0x0001A2B3,
         'grid_x': 0, 'grid_y': 0, 'is_exterior': False,
         'center': (10.0, 20.0, 30.0), 'base_objects': []},
        {'fid': 0x01000901, 'wrld_fid': 0x0000003C, 'cell_fid': 0,
         'grid_x': -30, 'grid_y': -1, 'is_exterior': True,
         'center': (-100.0, 200.0, 5.0), 'base_objects': []},
    ]
    navi = build_navi_record(0x01000FFF, metas)
    formid, subs = _parse_navi(navi)
    assert formid == 0x01000FFF

    sigs = [s for s, _ in subs]
    # Real Skyrim.esm order: NVER first, one NVMI per navmesh, NVPP last, no EDID.
    assert sigs[0] == 'NVER'
    assert sigs.count('NVMI') == 2
    assert sigs[-1] == 'NVPP'
    assert 'EDID' not in sigs
    assert struct.unpack('<I', dict(subs)['NVER'])[0] == 12

    nvmis = [_parse_nvmi(d) for s, d in subs if s == 'NVMI']
    for n in nvmis:
        assert n['consumed'] == n['len']  # each NVMI fully consumed (57 bytes vanilla)
        assert n['crc'] == 0xA5E9A03C
        assert n['category'] == 0
        assert n['is_island'] == 0

    interior, exterior = nvmis
    assert interior['worldspace'] == 0
    assert interior['parent_cell'] == 0x0001A2B3
    assert exterior['worldspace'] == 0x0000003C
    assert exterior['grid_y'] == -1
    assert exterior['grid_x'] == -30


def test_navi_empty_metas_returns_empty():
    assert build_navi_record(0x01000FFF, []) == b''


def test_navm_meta_center_is_geometry_centroid():
    rec = _grid_pgrd(n=5)
    _, meta = p2n.convert_PGRD(rec, writer=FakeWriter())
    cx, cy, cz = meta['center']
    # Grid spans 0..800 in x/y; centroid should be near the middle.
    assert 100 < cx < 700
    assert 100 < cy < 700
