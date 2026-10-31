"""The native navmesh kernels reproduce the Python reference bit for bit.

See: docs/commentary/tes5_import_navmesh.md#native-port-ab
"""

import math
import random

import numpy as np
import pytest

from tes5_import.navmesh import native_loader, corridor, corridor_grow
from tes5_import.navmesh.from_pgrd import packed_nvnm, record_graph
from tes5_import.navmesh.seeding import pathgrid_order, seeded_graph
from tes5_import.navmesh.native_loader import load_native

_native = load_native('_navgrow_native')


def _magnitudes(rng, n):
    """Random doubles spread across many magnitudes, signs included."""
    return [rng.uniform(-1.0, 1.0) * 10.0 ** rng.randint(-8, 8)
            for _ in range(n)]


@pytest.fixture
def python_path():
    """Run the test body on the Python reference, restoring the switch after."""
    was = native_loader.NATIVE
    native_loader.NATIVE = False
    yield
    native_loader.NATIVE = was


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


def test_simplify_matches_python(python_path):
    """Native Douglas-Peucker keeps exactly the points the Python one does."""
    rng = random.Random(17)
    cases = []
    for _ in range(3000):
        n = rng.randint(3, 40)
        x0, y0 = rng.uniform(-4000, 4000), rng.uniform(-4000, 4000)
        pts = [(x0 + 8.0 * k + rng.uniform(-3, 3), y0 + rng.uniform(-6, 6))
               for k in range(n)]
        if rng.random() < 0.1:
            pts[-1] = pts[0]
        cases.append((pts, rng.choice((0.5, 2.0, 4.0))))
    want = [corridor._simplify(p, tol) for p, tol in cases]
    native_loader.NATIVE = True
    assert [corridor._simplify(p, tol) for p, tol in cases] == want


def _weld_soup(rng, n=600):
    """A random welded soup: shared vertices, some slivers and degenerate triangles."""
    verts = [[rng.uniform(0, 600), rng.uniform(0, 600), rng.choice((0.0, 30.0, 200.0))]
             for _ in range(n // 2)]
    verts += [[verts[0][0] + 1e-7, verts[0][1], 0.0], [verts[1][0], verts[1][1], 0.0]]
    tris = []
    for _ in range(n):
        a = rng.randrange(len(verts))
        near = sorted(range(len(verts)), key=lambda k: (verts[k][0] - verts[a][0]) ** 2
                      + (verts[k][1] - verts[a][1]) ** 2)[1:8]
        b, c = rng.sample(near, 2)
        tris.append((a, b, c))
    tris.append((len(verts) - 2, 0, 1))
    return verts, tris


def test_bad_welds_match_python():
    """bad_welds reverts exactly the welds the shapely overlap probe does."""
    from tes5_import.navmesh import union_mesh
    rng = random.Random(13)
    for _ in range(6):
        out, welded = _weld_soup(rng)
        provisional = {k: [k] for k in rng.sample(range(len(out)), 60)}
        vtris = {}
        for ti, t in enumerate(welded):
            for k in t:
                if k in provisional:
                    vtris.setdefault(k, []).append(ti)
        probe = union_mesh._overlap_probe(out, welded, provisional)
        want = [rep for rep in sorted(provisional)
                if any(probe(ti) for ti in vtris.get(rep, ()))]
        assert union_mesh._native_bad_welds(out, welded, provisional, vtris) == want
        assert want


def test_stacked_pairs_match_python():
    """stacked_pairs finds _stacked_pairs' pairs, areas and triangle areas exactly."""
    from tes5_import.navmesh import union_mesh
    rng = random.Random(15)
    for _ in range(4):
        verts, tris = _weld_soup(rng)
        n = len(verts)
        for (a, b, c) in list(tris[:150]):
            dz = rng.choice((5.0, 39.9, 41.0, 300.0))
            verts += [[verts[k][0] + rng.uniform(-3, 3), verts[k][1] + rng.uniform(-3, 3),
                       verts[k][2] + dz] for k in (a, b, c)]
            tris.append((n, n + 1, n + 2))
            n += 3
        polys, geoms, gmap = union_mesh._plan_polys(verts, tris, 4.0)
        want = union_mesh._stacked_pairs(verts, tris, polys, geoms, gmap)
        got, area = native_loader.native_geos().stacked_pairs(
            np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int32), 4.0)
        assert sorted(got) == sorted(want) and want
        assert area == {ti: polys[ti].area for (_na, a, b) in want for ti in (a, b)}


def test_notch_overlaps_match_python():
    """notch_overlaps answers _overlaps_existing for every candidate fill."""
    from tes5_import.navmesh import corridor_union
    rng = random.Random(16)
    seen = set()
    for _ in range(4):
        verts, tris = _weld_soup(rng)
        cands = [tuple(rng.sample(range(len(verts)), 3)) for _ in range(300)]
        cands += [(0, len(verts) - 2, 1)]
        index = corridor_union._plan_index(verts, tris)
        want = [corridor_union._overlaps_existing(verts, tris, c, index) for c in cands]
        got = native_loader.native_geos().notch_overlaps(
            np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int32),
            np.asarray(cands, dtype=np.int32), corridor_union.STOREY_GAP_Z)
        assert got == want
        seen.update(want)
    assert seen == {True, False}


def _fringed_grid(rng, n=14):
    """A jittered grid with thin fringe triangles on its outline, some Z as numpy."""
    verts = [[x * 24.0 + rng.uniform(-4, 4), y * 24.0 + rng.uniform(-4, 4),
              rng.uniform(0, 30)] for y in range(n) for x in range(n)]
    tris = []
    for y in range(n - 1):
        for x in range(n - 1):
            a = y * n + x
            tris += [(a, a + 1, a + n + 1), (a, a + n + 1, a + n)]
    for x in range(n - 1):
        for (a, b, dy) in ((x, x + 1, -1), ((n - 1) * n + x + 1, (n - 1) * n + x, 1)):
            if rng.random() < 0.7:
                mx = (verts[a][0] + verts[b][0]) / 2 + rng.uniform(-8, 8)
                my = (verts[a][1] + verts[b][1]) / 2 + dy * rng.uniform(0.2, 3)
                verts.append([mx, my, np.float64(rng.uniform(0, 30))])
                tris.append((b, a, len(verts) - 1) if dy < 0 else (a, b, len(verts) - 1))
    for v in rng.sample(verts, len(verts) // 3):
        v[2] = np.float64(v[2])
    return verts, tris


def test_cull_slivers_match_python(python_path):
    """Native cull_boundary_slivers keeps exactly the triangles the Python one does."""
    from tes5_import.navmesh import corridor_clean
    rng = random.Random(18)
    culled = kept = 0
    for _ in range(8):
        verts, tris = _fringed_grid(rng)
        line = [(rng.uniform(0, 300), y, rng.uniform(0, 30), 1.0, 0.0)
                for y in np.arange(-4.0, 320.0, 7.0)]
        pins = [(rng.uniform(0, 300), rng.uniform(0, 300), 10.0) for _ in range(3)]
        args = dict(pinned_xy=pins, pin_xy=line + [(5.0, 5.0)],
                    seam_bounds=rng.choice((None, (0.0, -100.0, 400.0, 400.0))),
                    budget_frac=rng.choice((0.002, 0.05)))
        want = corridor_clean.cull_boundary_slivers(verts, tris, **args)
        native_loader.NATIVE = True
        assert corridor_clean.cull_boundary_slivers(verts, tris, **args) == want
        native_loader.NATIVE = False
        culled += len(tris) - len(want)
        kept += len(want)
    assert culled and kept


def test_candidate_pairs_touching_cases(python_path):
    """Shared edges, shared corners, hairline gaps and containment match shapely."""
    from shapely.geometry import Polygon
    from tes5_import.navmesh import union_sheets

    def sq(x, y, s):
        """An axis-aligned square of side s at (x, y)."""
        return Polygon([(x, y), (x + s, y), (x + s, y + s), (x, y + s)])

    polys = [sq(0, 0, 10), sq(10, 0, 10), sq(20, 10, 5), sq(25 + 1e-7, 0, 5),
             sq(2, 2, 3), sq(40, 40, 10), sq(50 + 1e-3, 40, 3), sq(1000.5, 0, 1)]
    want = union_sheets._candidate_pairs(polys)
    native_loader.NATIVE = True
    assert union_sheets._candidate_pairs(polys) == want
    assert (0, 1) in want and (1, 2) in want and (0, 4) in want and (5, 6) not in want


def _star_strip(rng, land=None):
    """A ribbon with a random non-convex (star) outline, one of three storeys, maybe sloped.

    With `land`, some ribbons follow that terrain instead.
    """
    cx, cy = rng.uniform(0, 400), rng.uniform(0, 400)
    n = rng.randint(5, 12)
    ring = []
    for k in range(n):
        ang = 2 * math.pi * (k + rng.uniform(0, 0.8)) / n
        r = rng.uniform(4, 60)
        ring.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    z = rng.choice((0.0, 0.0, 100.0, 260.0))
    dz = rng.choice((0.0, 0.0, 50.0, 140.0))
    s = {'a': (cx - 30, cy, z), 'b': (cx + 30, cy, z + dz), 'half': 30.0,
         'poly': ring}
    if land is not None and rng.random() < 0.4:
        s['land'] = land
    return s


def test_pair_gaps_verdicts_match_python(python_path):
    """Native candidate pairs and pair_gaps verdicts are exactly the Python ones."""
    from tes5_import.navmesh import union_sheets, world
    from tes5_import.navmesh.union_geom import _ribbon_polygon
    rng = random.Random(14)
    hills = np.array([[60.0 * math.sin(i / 3.0) * math.cos(j / 5.0) + rng.uniform(0, 30)
                       for i in range(33)] for j in range(33)])
    land = world.LandField(hills, -40.0, -40.0)
    strips = [_star_strip(rng, land if k % 2 else None) for k in range(400)]
    items = [(s, _ribbon_polygon(s)) for s in strips]
    polys = [p for (_s, p) in items]
    pairs = union_sheets._candidate_pairs(polys)
    want = union_sheets._pair_gaps(items, pairs)
    native_loader.NATIVE = True
    assert union_sheets._candidate_pairs(polys) == pairs
    got = union_sheets._pair_gaps(items, pairs)

    def verdict(g):
        """What _partition_by_agreement reads from a gap."""
        return None if g is None else g > union_sheets.STOREY_GAP_Z

    assert [verdict(g) for g in got] == [verdict(g) for g in want]
    assert {verdict(g) for g in want} == {None, True, False}


def _soup(rng, n=400):
    """A random triangle soup of n triangles over a few storeys."""
    out = []
    for _ in range(n):
        cx, cy = rng.uniform(0, 2000), rng.uniform(0, 2000)
        z = rng.choice((0.0, 140.0, 300.0)) + rng.uniform(-20, 20)
        out.append([(cx + rng.uniform(-150, 150), cy + rng.uniform(-150, 150),
                     z + rng.uniform(-30, 30)) for _ in range(3)])
    return np.asarray(out, dtype=np.float64)


def _queries(rng, n=3000):
    """Random (x, y, near_z) probes over the soup's extent."""
    return [(rng.uniform(-100, 2100), rng.uniform(-100, 2100),
             rng.uniform(-50, 350)) for _ in range(n)]


def _sampled(walk, sample, wall, qs):
    """Every sampler's answer at every probe, with result types."""
    out = []
    for (x, y, z) in qs:
        w, s = walk(x, y, z), sample(x, y, z)
        out.append((w, type(w).__name__, s, type(s).__name__,
                    tuple(sample.layers(x, y)),
                    wall(x, y, 0.6, 0.8, -0.8, 0.6, z - 20, z + 100),
                    wall(x, y, 1.0, 0.0, 0.0, 1.0, z, z + 64, 30.0, 50.0)))
    return out


def test_samplers_match_python(python_path):
    """TriIndex answers every sampler query exactly as the Python closures do."""
    rng = random.Random(11)
    soup = _soup(rng)
    qs = _queries(rng)

    def build():
        """The three samplers on whichever path the switch selects."""
        return (corridor_grow.walkable_sampler(soup),
                corridor._surface_sampler(soup),
                corridor_grow.wall_slab_sampler(soup))

    want = _sampled(*build(), qs)
    native_loader.NATIVE = True
    assert _sampled(*build(), qs) == want


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


def test_seed_flood_matches_python(python_path):
    """The native gather and seed flood rebuild seeding.seeded_graph's graph exactly."""
    for seed in range(3):
        job, models, doors, get_collision = _flood_job(random.Random(seed))
        native_loader.NATIVE = False
        want = seeded_graph(job, models, doors, get_collision)
        native_loader.NATIVE = True
        assert seeded_graph(job, models, doors, get_collision) == want
        assert want and len(want[0]) > 1


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


def test_packed_nvnm_matches_python(python_path):
    """The native NVNM packer gives the Python reference's bytes, door triangles and center."""
    for seed in range(6):
        geometry, doors = _packing_case(random.Random(seed))
        water = None if seed % 2 else 10.0
        frame = (0x3C, 0x3D, seed - 3, 2 - seed, seed % 3 != 0)
        native_loader.NATIVE = False
        want = packed_nvnm(geometry, doors, water, frame, 0xABC)
        native_loader.NATIVE = True
        assert packed_nvnm(geometry, doors, water, frame, 0xABC) == want
        assert want[1] and b'\x02\x00\x00\x00' in want[0]
