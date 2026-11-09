"""The native navmesh kernels: Python float semantics, packing, and golden outputs.

A golden test hashes a native build of a fixed synthetic cell; a change to its
digest is a change to navmesh output and must be intended.
See: docs/commentary/tes5_import_navmesh.md#cell-baseline
"""

import hashlib
import math
import random

import numpy as np

from tes5_import.navmesh import corridor
from tes5_import.navmesh.from_pgrd import (build_door_links, compute_adjacency,
                                           compute_water_flags, pack_nvnm, packed_nvnm,
                                           record_graph)
from tes5_import.navmesh.seeding import pathgrid_order, seeded_graph
from tes5_import.navmesh.native_loader import load_native

_native = load_native('_navgrow_native')


def _magnitudes(rng, n):
    """Random doubles spread across many magnitudes, signs included."""
    return [rng.uniform(-1.0, 1.0) * 10.0 ** rng.randint(-8, 8)
            for _ in range(n)]


def test_hypot_matches_math_hypot():
    """pymath hypot is CPython's vector_norm, not the C library hypot."""
    rng = random.Random(7)
    vals = _magnitudes(rng, 200000)
    got = _native.pymath('hypot', vals)
    want = [math.hypot(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
    assert got == want


def test_square_matches_pow():
    """pymath square is `x ** 2`, which is not always x * x."""
    rng = random.Random(8)
    vals = _magnitudes(rng, 100000)
    assert _native.pymath('square', vals) == [x ** 2 for x in vals]


def test_floordiv_matches_python():
    """pymath floordiv is Python's float `//`."""
    rng = random.Random(9)
    vals = []
    for _ in range(50000):
        vals += [rng.uniform(-5000.0, 5000.0), rng.choice((128.0, 0.1, 24.0, 7.3))]
    want = [vals[i] // vals[i + 1] for i in range(0, len(vals), 2)]
    assert _native.pymath('floordiv', vals) == want


def test_sum_matches_builtin_sum():
    """pymath sum is builtin sum() over floats, compensation included."""
    rng = random.Random(10)
    for _ in range(5000):
        vals = _magnitudes(rng, rng.randint(1, 12))
        assert _native.pymath('sum', vals) == [sum(vals)], vals
    assert _native.pymath('sum', [1e100, 1.0, -1e100]) == [sum([1e100, 1.0, -1e100])]


def _box(x0, y0, z0, x1, y1, z1):
    """A closed box as a flat soup: two floor/roof triangles per face."""
    c = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    faces = ((0, 2, 6, 4), (1, 5, 7, 3), (0, 4, 5, 1), (2, 3, 7, 6), (0, 1, 3, 2), (4, 6, 7, 5))
    return [v for a, b, k, d in faces for t in ((a, b, k), (a, k, d)) for i in t for v in c[i]]


def _flood_job(rng):
    """A seeded-cell job over tilted, scaled slabs, walls, a door panel, LAND and water."""
    collision = {'slab': {'w': np.asarray(_box(-600, -600, -8, 600, 600, 0), np.float32),
                          'b': np.zeros(0, np.float32)},
                 'wall': {'w': [], 'b': _box(-20, -300, 0, 20, 300, 200)},
                 'door': {'w': np.zeros(0, np.float32), 'b': np.asarray(_box(-5, -60, 0, 5, 60, 180),
                                                                          np.float32)}}
    models = {0x10: 'slab', 0x11: 'wall', 0x12: 'door'}
    refs = [{'NAME': '%08X' % rng.choice(sorted(models)), 'PosX': rng.uniform(4096, 8192),
             'PosY': rng.uniform(0, 4096), 'PosZ': rng.uniform(-40, 120),
             'RotX': rng.uniform(-0.3, 0.3), 'RotY': rng.uniform(-0.3, 0.3),
             'RotZ': rng.uniform(-3.2, 3.2), 'XSCL.Scale': rng.uniform(0.6, 1.8)} for _ in range(70)]
    deltas = bytes((rng.randint(-3, 3) & 0xFF) for _ in range(33 * 33))
    land = {'VHGT': (np.float32(-2.0).tobytes() + deltas + b'\0\0\0').hex()}
    cell = {'FormID': '00000AAA', 'XCLC.X': '1', 'XCLC.Y': '0', 'DATA.Flags': '2',
            'XCLW.WaterHeight': '30.0'}
    seeds = sorted({(4100.0, rng.uniform(100, 3900), 0.0) for _ in range(5)})
    job = {'cell_rec': cell, 'land_rec': land, 'refr_recs': refs, 'seeds': seeds,
           'pgrd_rec': {'Signature': 'PGRD', 'FormID': '00000AAA'}}
    return job, models, {0x12}, collision.get


def _as_pathgrid_record(points, edges):
    """The flooded graph written out in the exported PGRD text layout."""
    nbrs = [[] for _ in points]
    for a, b in edges:
        nbrs[a].append(b)
        nbrs[b].append(a)
    rec = {'DATA.PointCount': str(len(points))}
    for i, (x, y, z) in enumerate(points):
        rec.update({'Point[%d].X' % i: repr(float(x)), 'Point[%d].Y' % i: repr(float(y)),
                    'Point[%d].Z' % i: repr(float(z)), 'Point[%d].Connections' % i: str(len(nbrs[i]))})
        rec.update(('Point[%d].Edge[%d]' % (i, n), str(t)) for n, t in enumerate(sorted(nbrs[i])))
    return rec


def test_seeded_graph_reads_as_its_pathgrid_record():
    """A seeded graph handed over directly equals the same graph written to a PGRD and read back.

    Node 0 always has an edge, as in seed_graph (a seed is placed only once it links).
    """
    rng = random.Random(7)
    for _ in range(20):
        n = rng.randint(2, 30)
        points = [tuple(np.float64(rng.uniform(-5e3, 5e3)) for _ in range(3)) for _ in range(n)]
        edges = [(0, rng.randrange(1, n))]
        edges += [(rng.randrange(n), rng.randrange(n)) for _ in range(rng.randint(0, 3 * n))]
        assert pathgrid_order(points, edges) == record_graph(_as_pathgrid_record(points, edges))


def _jittered_mesh(rng, n=12):
    """A jittered n x n grid of float32-valued verts, two triangles per square."""
    verts = [tuple(float(np.float32(c)) for c in (x * 97.0 + rng.uniform(-30, 30),
                                                  y * 97.0 + rng.uniform(-30, 30),
                                                  rng.uniform(-40, 60)))
             for y in range(n) for x in range(n)]
    tris = []
    for a in (y * n + x for y in range(n - 1) for x in range(n - 1)):
        tris += [(a, a + 1, a + n + 1), (a, a + n + 1, a + n)]
    return verts, tris


def _packing_case(rng):
    """(geometry, doors) for packed_nvnm: contained, lifted, outside and id-less doors, and ledges."""
    verts, tris = _jittered_mesh(rng)
    doors = []
    for i in range(8):
        x, y, z = verts[rng.randrange(len(verts))]
        lift = 400.0 if i % 3 == 0 else 0.0
        doors.append((x + rng.uniform(-5, 5), y + rng.uniform(-5, 5), z + lift,
                      rng.uniform(-3.2, 3.2), 0x1000 + i, i % 2 == 0, 80.0))
    doors += [(-90.0, -60.0, 0.0, 0.4, 0x2000, True, 0.0), (500.0, 500.0, 0.0, 0.0, 0, False, 0.0)]
    ledges = [(rng.randrange(len(tris) + 2) - 1, rng.randrange(len(tris)), rng.uniform(60, 200))
              for _ in range(40)]
    return (verts, tris, ledges), doors


def _python_packed(geometry, doors, water_z, frame, navm_fid):
    """packed_nvnm's result from the Python packer the Fallout NV path and navm_patch use."""
    verts, tris, ledges = geometry
    cell_fid, wrld_fid, grid_x, grid_y, is_exterior = frame
    flags = compute_water_flags(verts, tris, water_z) if water_z is not None else [0] * len(tris)
    door_tris = build_door_links(verts, tris, doors)
    nvnm = pack_nvnm(verts, tris, compute_adjacency(tris), flags, wrld_fid, cell_fid,
                     grid_x, grid_y, is_exterior, door_tris=door_tris, ledges=ledges,
                     navm_fid=navm_fid)
    return nvnm, door_tris, tuple(sum(v[k] for v in verts) / len(verts) for k in range(3))


def test_packed_nvnm_matches_python_packer():
    """The native NVNM packer gives the Python packer's bytes, door triangles and center."""
    for seed in range(6):
        geometry, doors = _packing_case(random.Random(seed))
        water = None if seed % 2 else 10.0
        frame = (0x3C, 0x3D, seed - 3, 2 - seed, seed % 3 != 0)
        want = _python_packed(geometry, doors, water, frame, 0xABC)
        assert packed_nvnm(geometry, doors, water, frame, 0xABC) == want
        assert want[1] and b'\x02\x00\x00\x00' in want[0]


def _plain(obj):
    """A nested build result as nested tuples of Python floats, numpy scalars included."""
    return tuple(_plain(x) for x in obj) if isinstance(obj, (list, tuple)) else float(obj)


def _digest(obj):
    """A stable hex digest of a nested build result."""
    return hashlib.sha1(repr(_plain(obj)).encode()).hexdigest()


def _corridor_build(seed):
    """build_corridors over the flood fixture's cell, its pathgrid a jittered ring of nodes."""
    rng = random.Random(seed)
    job, models, doors, get_collision = _flood_job(rng)
    nodes = [(4096.0 + 2048.0 + 900.0 * math.cos(k * 0.7), 2048.0 + 900.0 * math.sin(k * 0.7),
              rng.uniform(-10.0, 40.0)) for k in range(9)]
    edges = [(k, (k + 1) % len(nodes)) for k in range(len(nodes))] + [(0, 4)]
    door = (nodes[2][0] + 60.0, nodes[2][1], nodes[2][2], 0.3, True, 90.0)
    return corridor.build_corridors(job['refr_recs'], models, get_collision, nodes, edges,
                                    land_rec=job['land_rec'], origin_x=4096.0, origin_y=0.0,
                                    doors=[door], door_bases=doors, water_z=30.0)


#: sha1 of seeded_graph over _flood_job(Random(seed)), per seed.
FLOOD_DIGESTS = {0: 'a60c651941e8bd35a7d62f5557a8b21e308f0d59',
                 1: '8b47bc2699c6a73261d68ca4636759d725f06f0a',
                 2: '0902119ef5669252977f39a75ab5df8ebaf7fb64'}

#: sha1 of _corridor_build(seed), per seed.
CORRIDOR_DIGESTS = {0: 'c74420db136fe88d0bf61ed2ac0b5be421935d46',
                    1: '531276d471ddd56fc43a6911e6bd2bea6326c736',
                    2: 'c33cf7b047e0825ed6f1c6698d228925f3f2ae7c'}


def test_seed_flood_is_golden():
    """The native gather and seed flood rebuild the recorded graphs exactly."""
    for seed, want in FLOOD_DIGESTS.items():
        job, models, doors, get_collision = _flood_job(random.Random(seed))
        graph = seeded_graph(job, models, doors, get_collision)
        assert graph and len(graph[0]) > 1
        assert _digest(graph) == want, seed


def test_corridor_cell_is_golden():
    """The native corridor build of a fixed synthetic cell is the recorded mesh exactly."""
    for seed, want in CORRIDOR_DIGESTS.items():
        verts, tris, _ledges = got = _corridor_build(seed)
        assert verts and tris
        assert _digest(got) == want, seed
