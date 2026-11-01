"""Check a BUILT navmesh the way Skyrim's pathfinder reads it.

Reads each cell's NVNM exactly as written, plus the export's pathgrid and
collision, and reports what makes an actor fail to path or walk into a wall:

  GRID_MISS   triangle left out of a lookup-grid cell it overlaps, so the
              engine cannot find it for a point in that cell
  MISLOCATE   a point on triangle T that the engine's lookup puts on another
              triangle; `split` = onto another component (no path exists)
  PG_SPLIT    pathgrid edge whose two ends resolve into different components
  PG_OFF      pathgrid node the lookup finds no containing triangle for
  INSIDE      triangle standing inside solid collision
  NO_FLOOR    triangle with no collision surface near its height
  HEADROOM    triangle with collision overhead inside the actor's height
  SHORT       open edge with open floor past it: the mesh stops short of a wall

    python tools/navmesh/engine_check.py --plugin Nehrim.esm SchattenrufMinePart01
    python tools/navmesh/engine_check.py --plugin Nehrim.esm --prefix SchattenrufMinePart
    python tools/navmesh/engine_check.py --plugin Nehrim.esm --prefix X --dump temp/x.txt

See: docs/reference/navmesh_engine_contracts.md#what-the-checker-tests
"""

import argparse
import math
import os
import struct
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.plugin_masters import export_root, master_export_dir
from output_layout import plugin_esm
from tes5_import.base.tes5_reader import walk
from tes5_import.navmesh.edge_links import DOOR_TRI_SIZE, NavMeshView
from tes5_import.navmesh.from_pgrd import compute_adjacency, pack_nvnm
from tes5_import.navmesh.lookup_grid import build_navmesh_grid
from tools.navmesh.authored import cell_editor_ids
from tools.navmesh.index import NavIndex

#: A containing triangle this close in height is an exact hit (CK 0x2830170, constant 40).
EXACT_DZ = 40.0
#: Triangle flag the engine's point lookup skips ("Overlapping").
FLAG_OVERLAPPING = 0x0020
#: Per-edge "this edge is an Edge Link" triangle flag bits.
EDGE_LINK_BITS = (0x0001, 0x0002, 0x0004)
#: Height weights for an off-mesh point above / below a triangle center (CK 0x2844330).
ABOVE_WEIGHT, BELOW_WEIGHT = 5.0, 10.0
#: Body column above the surface that must be clear (CK AgentMaxClimb 32).
STEP_UP = 32.0
#: Overhead collision below this is an obstacle the actor walks into (a table top is ~75).
HEADROOM = 100.0
#: Height of the horizontal wall probes above the surface.
WAIST = 48.0
#: Floor search window around the navmesh surface.
FLOOR_ABOVE, FLOOR_BELOW = 32.0, 48.0
#: Length of the horizontal wall probes.
RAY_LEN = 64.0
#: Horizontal probe directions for the inside-solid vote.
DIRS = np.array([(math.cos(a), math.sin(a), 0.0)
                 for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)])
#: Of the probes that hit, this many backfaces means the point is inside a solid.
INSIDE_VOTES = 5
#: Distances past an open edge probed for continuing floor.
SHORT_PROBES = (32.0, 64.0)
#: Minimum floor normal Z (CK MaxSlope 45 degrees).
FLOOR_NZ = 0.7071
#: Collision bucket size for the ray index.
BUCKET = 64.0
#: Samples per triangle that must fail before the triangle is reported.
TRI_VOTES = 2


# ---------------------------------------------------------------------------
# Written navmesh
# ---------------------------------------------------------------------------

def parse_lookup(tail):
    """`(divisor, cell width, cell height, bbox, [tri index arrays])` from an NVNM tail."""
    p = 4 + struct.unpack_from('<I', tail, 0)[0] * DOOR_TRI_SIZE
    p += 4 + struct.unpack_from('<I', tail, p)[0] * 2
    div, cw, ch = struct.unpack_from('<Iff', tail, p)
    p += 12
    bbox = struct.unpack_from('<6f', tail, p)
    p += 24
    buckets = []
    for _ in range(div * div):
        n = struct.unpack_from('<I', tail, p)[0]
        buckets.append(np.frombuffer(tail, '<i2', n, p + 4).astype(np.int64))
        p += 4 + 2 * n
    return div, cw, ch, bbox, buckets


class Nav(object):
    """One written navmesh: its view, lookup grid and per-triangle geometry."""

    def __init__(self, fid, blob):
        """Decode `blob` and precompute corners, centers and plane helpers."""
        self.fid = fid
        self.view = NavMeshView(fid, blob)
        self.t = self.view.tris
        self.corners = self.view.verts[self.t[:, :3]]
        self.centers = self.corners.mean(axis=1)
        self.div, self.cw, self.ch, self.bbox, self.buckets = parse_lookup(self.view.tail)

    def bucket_at(self, x, y):
        """The lookup-grid triangle list under (x, y), or None outside the grid."""
        col = int((x - self.bbox[0]) // self.cw) if self.cw > 0 else 0
        row = int((y - self.bbox[1]) // self.ch) if self.ch > 0 else 0
        if not (-1 <= col <= self.div and -1 <= row <= self.div):
            return None
        col, row = min(max(col, 0), self.div - 1), min(max(row, 0), self.div - 1)
        return self.buckets[row * self.div + col]

    def containing(self, cand, x, y, z):
        """`(tris, dz)`: the candidates whose plan contains (x, y), and z minus their plane."""
        c = self.corners[cand]
        w = barycentric(c[:, 0, :2], c[:, 1, :2], c[:, 2, :2], x, y)
        keep = (w >= -1e-6).all(axis=1)
        pz = (w[keep] * c[keep][:, :, 2]).sum(axis=1)
        return cand[keep], z - pz

    def nearest_center(self, cand, x, y, z):
        """The candidate whose center is nearest in 3D (the engine's fallback)."""
        d = self.centers[cand] - (x, y, z)
        return int(cand[np.argmin((d * d).sum(axis=1))])


def barycentric(a, b, c, x, y):
    """(N, 3) barycentric weights of (x, y) in each plan triangle a-b-c."""
    v0, v1 = b - a, c - a
    p = np.array([x, y]) - a
    den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
    den = np.where(np.abs(den) < 1e-12, 1e-12, den)
    wb = (p[:, 0] * v1[:, 1] - v1[:, 0] * p[:, 1]) / den
    wc = (v0[:, 0] * p[:, 1] - p[:, 0] * v0[:, 1]) / den
    return np.stack([1.0 - wb - wc, wb, wc], axis=1)


def locate_in(nav, x, y, z):
    """`(tri, exact)` from one navmesh's lookup, as the engine does it.

    The grid list under the point is scanned in order; the running closest
    containing triangle wins, stopping once it is within EXACT_DZ. With no
    containing triangle the nearest center in that list wins; an empty list
    falls back to the nearest center of the whole mesh.
    """
    cand = nav.bucket_at(x, y)
    if cand is None or not len(cand):
        return nav.nearest_center(np.arange(len(nav.t)), x, y, z), False
    cand = cand[(nav.t[cand, 6] & FLAG_OVERLAPPING) == 0]
    inside, dz = nav.containing(cand, x, y, z)
    best, best_dz = None, float('inf')
    for ti, d in zip(inside, np.abs(dz)):
        if d < best_dz:
            best, best_dz = int(ti), d
            if d < EXACT_DZ:
                return best, True
    if best is not None:
        return best, False
    return (nav.nearest_center(cand, x, y, z), False) if len(cand) else (None, False)


def locate(navs, x, y, z):
    """`(mesh index, tri, exact)` over every navmesh of the cell."""
    best = None
    for mi, nav in enumerate(navs):
        ti, exact = locate_in(nav, x, y, z)
        if ti is None:
            continue
        if exact:
            return mi, ti, True
        dx, dy, dz = np.array((x, y, z)) - nav.centers[ti]
        score = dx * dx + dy * dy + (ABOVE_WEIGHT if dz >= 0 else BELOW_WEIGHT) * dz * dz
        if best is None or score < best[0]:
            best = (score, mi, ti)
    return (best[1], best[2], False) if best else (None, None, False)


# ---------------------------------------------------------------------------
# Connectivity over neighbour slots (engine GetMatchingTri)
# ---------------------------------------------------------------------------

def partner(navs, by_fid, mi, ti, slot):
    """`(mesh, tri)` across edge `slot` of `ti`, following links within the cell, else None."""
    view = navs[mi].view
    tri = view.tris[ti]
    e = int(tri[3 + slot])
    if not tri[6] & EDGE_LINK_BITS[slot]:
        return (mi, e) if 0 <= e < len(view.tris) else None
    if not 0 <= e < len(view.links):
        return None
    _typ, fid, other = view.links[e]
    return (by_fid[fid], int(other)) if fid in by_fid else None


def components(navs):
    """`[array of component ids per triangle]`, one per navmesh, over the whole cell."""
    base = np.cumsum([0] + [len(n.t) for n in navs])
    parent = list(range(int(base[-1])))

    def find(a):
        """Root of `a`, halving the path."""
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    by_fid = {n.fid: i for i, n in enumerate(navs)}
    for mi, nav in enumerate(navs):
        for ti in range(len(nav.t)):
            for slot in range(3):
                got = partner(navs, by_fid, mi, ti, slot)
                if got is not None and 0 <= got[1] < len(navs[got[0]].t):
                    parent[find(base[mi] + ti)] = find(base[got[0]] + got[1])
    return [np.array([find(base[mi] + ti) for ti in range(len(n.t))])
            for mi, n in enumerate(navs)]


def open_edges(navs):
    """`[(mesh, tri, slot)]` for every edge with no neighbour and no link."""
    out = []
    for mi, nav in enumerate(navs):
        for ti, tri in enumerate(nav.t):
            for slot in range(3):
                if not tri[6] & EDGE_LINK_BITS[slot] and tri[3 + slot] < 0:
                    out.append((mi, ti, slot))
    return out


# ---------------------------------------------------------------------------
# Lookup-grid coverage
# ---------------------------------------------------------------------------

def grid_misses(nav):
    """Triangle indices missing from at least one lookup cell they overlap.

    See: docs/reference/navmesh_engine_contracts.md#the-lookup-grid
    """
    want = build_navmesh_grid(nav.view.verts, nav.t[:, :3], nav.bbox[0], nav.bbox[1],
                              nav.bbox[3], nav.bbox[4], nav.div)
    out = set()
    for have, need in zip(nav.buckets, want):
        out |= set(need) - set(have.tolist())
    return sorted(out)


# ---------------------------------------------------------------------------
# Collision ray index
# ---------------------------------------------------------------------------

class Solid(object):
    """Placed collision triangles with an XY bucket index and ray queries."""

    def __init__(self, soups):
        """Index every non-empty (N, 3, 3) triangle soup in `soups`."""
        parts = [np.asarray(s, dtype=np.float64) for s in soups if s is not None and len(s)]
        self.tri = np.concatenate(parts) if parts else np.zeros((0, 3, 3))
        n = np.cross(self.tri[:, 1] - self.tri[:, 0], self.tri[:, 2] - self.tri[:, 0])
        self.n = n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
        grid = defaultdict(list)
        lo = np.floor(self.tri[:, :, :2].min(axis=1) / BUCKET).astype(int)
        hi = np.floor(self.tri[:, :, :2].max(axis=1) / BUCKET).astype(int)
        for i in range(len(self.tri)):
            for bx in range(lo[i, 0], hi[i, 0] + 1):
                for by in range(lo[i, 1], hi[i, 1] + 1):
                    grid[(bx, by)].append(i)
        self.grid = {k: np.array(v) for k, v in grid.items()}

    def near(self, x0, y0, x1, y1):
        """Indices of triangles in the buckets covering a plan rectangle."""
        got = [self.grid.get((bx, by)) for bx in range(int(x0 // BUCKET), int(x1 // BUCKET) + 1)
               for by in range(int(y0 // BUCKET), int(y1 // BUCKET) + 1)]
        got = [g for g in got if g is not None]
        return np.unique(np.concatenate(got)) if got else np.zeros(0, dtype=int)

    def cast(self, origin, dirs, length):
        """Per direction, `(distance, front-facing)` of the first hit within `length`, or None."""
        o = np.asarray(origin, dtype=np.float64)
        ends = o + dirs * length
        cand = self.near(min(o[0], ends[:, 0].min()), min(o[1], ends[:, 1].min()),
                         max(o[0], ends[:, 0].max()), max(o[1], ends[:, 1].max()))
        if not len(cand):
            return [None] * len(dirs)
        t, hit = ray_triangles(o, dirs, self.tri[cand])
        out = []
        for k in range(len(dirs)):
            ok = hit[k] & (t[k] <= length)
            if not ok.any():
                out.append(None)
                continue
            j = int(np.argmin(np.where(ok, t[k], np.inf)))
            out.append((float(t[k, j]), float(np.dot(dirs[k], self.n[cand[j]])) < 0))
        return out


def ray_triangles(o, dirs, tri):
    """Moller-Trumbore for D rays from one origin against N triangles: `(t, hit)` (D, N)."""
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    p = np.cross(dirs[:, None, :], e2[None, :, :])
    det = (e1[None] * p).sum(axis=2)
    ok = np.abs(det) > 1e-9
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = o - tri[:, 0]
    u = (s[None] * p).sum(axis=2) * inv
    q = np.cross(s, e1)
    v = (dirs[:, None, :] * q[None]).sum(axis=2) * inv
    t = (e2 * q).sum(axis=1)[None] * inv
    return t, ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)


# ---------------------------------------------------------------------------
# Standing tests
# ---------------------------------------------------------------------------

DOWN = np.array([[0.0, 0.0, -1.0]])
UP = np.array([[0.0, 0.0, 1.0]])


def floor_at(solid, x, y, z):
    """Normal z of the first surface under (x, y) near height z, else None.

    Either face counts: single meshes are wound inconsistently, so a rock's
    top can face down.
    See: docs/reference/navmesh_engine_contracts.md#what-the-checker-tests
    """
    hit = solid.cast((x, y, z + FLOOR_ABOVE), DOWN, FLOOR_ABOVE + FLOOR_BELOW)[0]
    if hit is None:
        return None
    return hit_normal_z(solid, x, y, z + FLOOR_ABOVE - hit[0])


def hit_normal_z(solid, x, y, z):
    """Normal z of the collision triangle under (x, y) nearest height z."""
    cand = solid.near(x, y, x, y)
    if not len(cand):
        return 0.0
    c = solid.tri[cand]
    w = barycentric(c[:, 0, :2], c[:, 1, :2], c[:, 2, :2], x, y)
    keep = (w >= -1e-6).all(axis=1)
    if not keep.any():
        return 0.0
    pz = (w[keep] * c[keep][:, :, 2]).sum(axis=1)
    return float(abs(solid.n[cand[keep][np.argmin(np.abs(pz - z))], 2]))


def standing_problems(solid, x, y, z):
    """The standing rules one navmesh surface point breaks, as a set."""
    out = set()
    if floor_at(solid, x, y, z) is None:
        out.add('NO_FLOOR')
    if solid.cast((x, y, z + STEP_UP), UP, HEADROOM - STEP_UP)[0] is not None:
        out.add('HEADROOM')
    hits = [h for h in solid.cast((x, y, z + WAIST), DIRS, RAY_LEN) if h is not None]
    if sum(1 for h in hits if not h[1]) >= INSIDE_VOTES:
        out.add('INSIDE')
    return out


def samples(nav, ti):
    """Four surface points of a triangle: its center and halfway to each corner."""
    c = nav.corners[ti]
    m = c.mean(axis=0)
    return [m] + [(m + c[k]) / 2.0 for k in range(3)]


def open_floor_past(solid, a, b, inward):
    """The largest SHORT_PROBES distance past edge a-b that is open, walkable floor, else 0.

    Open means no wall on the way and no collision overhead within HEADROOM,
    so floor running on under a bed or a table does not count.
    """
    mid = (a + b) / 2.0
    d = np.array([b[1] - a[1], a[0] - b[0], 0.0])
    d /= max(np.linalg.norm(d), 1e-9)
    if np.dot(d[:2], inward[:2] - mid[:2]) > 0:
        d = -d
    wall = solid.cast(mid + (0, 0, STEP_UP + 8.0), d[None], max(SHORT_PROBES))[0]
    reach = wall[0] if wall is not None else float('inf')
    best = 0.0
    for dist in SHORT_PROBES:
        p = mid + d * dist
        floor = floor_at(solid, p[0], p[1], mid[2])
        clear = solid.cast((p[0], p[1], mid[2] + STEP_UP), UP, HEADROOM - STEP_UP)[0] is None
        if dist < reach and floor is not None and floor >= FLOOR_NZ and clear:
            best = dist
    return best


# ---------------------------------------------------------------------------
# Cell report
# ---------------------------------------------------------------------------

def check_locate(navs, comps):
    """`(samples, mislocated, split, rows)` for points on every triangle."""
    n = mis = split = 0
    rows = []
    for mi, nav in enumerate(navs):
        for ti in range(len(nav.t)):
            for p in samples(nav, ti):
                n += 1
                gm, gt, _exact = locate(navs, *p)
                if (gm, gt) == (mi, ti):
                    continue
                mis += 1
                other = gt is None or comps[gm][gt] != comps[mi][ti]
                split += other
                rows.append(('MISLOCATE_SPLIT' if other else 'MISLOCATE', p, nav.fid, ti))
    return n, mis, split, rows


def check_pathgrid(navs, comps, nodes, edges):
    """`(off, split, rows)`: pathgrid nodes off the mesh and edges across components."""
    where = [locate(navs, *p) for p in nodes]
    rows = [('PG_OFF', nodes[i], 0, i) for i, w in enumerate(where) if not w[2]]
    split = 0
    for a, b in edges:
        (ma, ta, _), (mb, tb, _) = where[a], where[b]
        if ta is None or tb is None or comps[ma][ta] != comps[mb][tb]:
            split += 1
            rows.append(('PG_SPLIT', nodes[a], a, b))
    return len(rows) - split, split, rows


def check_standing(navs, solid):
    """`({rule: triangle count}, rows)` for INSIDE / NO_FLOOR / HEADROOM."""
    counts = defaultdict(int)
    rows = []
    for nav in navs:
        for ti in range(len(nav.t)):
            votes = defaultdict(int)
            for p in samples(nav, ti):
                for rule in standing_problems(solid, *p):
                    votes[rule] += 1
            for rule, v in votes.items():
                if v >= TRI_VOTES:
                    counts[rule] += 1
                    rows.append((rule, nav.centers[ti], nav.fid, ti))
    return counts, rows


def check_short(navs, solid):
    """`({probe distance: (edges, length)}, rows)` for open edges with floor past them."""
    out = defaultdict(lambda: [0, 0.0])
    rows = []
    for mi, ti, slot in open_edges(navs):
        c = navs[mi].corners[ti]
        a, b = c[slot], c[(slot + 1) % 3]
        dist = open_floor_past(solid, a, b, c.mean(axis=0))
        if dist:
            out[dist][0] += 1
            out[dist][1] += float(np.linalg.norm((b - a)[:2]))
            rows.append(('SHORT%d' % dist, (a + b) / 2.0, navs[mi].fid, ti))
    return dict(out), rows


def calibrate(solid, nodes):
    """`{rule: count}` the standing tests report on pathgrid nodes, which are known standable."""
    out = defaultdict(int)
    for p in nodes:
        for rule in standing_problems(solid, *p):
            out[rule] += 1
    return out


def check_cell(name, navs, ctx):
    """Print one cell's report; return its dump rows."""
    comps = components(navs)
    ntri = sum(len(n.t) for n in navs)
    ncomp = len(set(np.concatenate(comps).tolist())) if ntri else 0
    gmiss = sum(len(grid_misses(n)) for n in navs)
    n, mis, split, rows = check_locate(navs, comps)
    off, pg_split, pg_rows = check_pathgrid(navs, comps, ctx.nodes, ctx.edges)
    solid = Solid(ctx.collision()[:2])
    stand, st_rows = check_standing(navs, solid)
    short, sh_rows = check_short(navs, solid)
    print('%s: %d navm, %d tris, %d components, %d collision tris'
          % (name, len(navs), ntri, ncomp, len(solid.tri)))
    print('  GRID_MISS  %d tris absent from a lookup cell they overlap' % gmiss)
    print('  MISLOCATE  %d of %d samples (%d onto another component)' % (mis, n, split))
    print('  PATHGRID   %d nodes, %d edges: %d off-mesh, %d split'
          % (len(ctx.nodes), len(ctx.edges), off, pg_split))
    cal = calibrate(solid, ctx.nodes)
    print('  STANDING   inside=%d no_floor=%d headroom=%d tris'
          % (stand.get('INSIDE', 0), stand.get('NO_FLOOR', 0), stand.get('HEADROOM', 0)))
    print('  CALIBRATE  pathgrid nodes: inside=%d no_floor=%d headroom=%d of %d'
          % (cal['INSIDE'], cal['NO_FLOOR'], cal['HEADROOM'], len(ctx.nodes)))
    for dist in sorted(short):
        print('  SHORT%-4d  %d open edges, %.0fu, with open floor %du past them'
              % (dist, short[dist][0], short[dist][1], dist))
    return [(name,) + r for r in rows + pg_rows + st_rows + sh_rows]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def built_nav(ctx, lattice):
    """`[Nav]` for a cell generated now, packed by the importer's own NVNM writer."""
    ledges = []
    verts, tris = ctx.build(ledges_out=ledges, lattice=lattice)
    if not tris:
        return []
    blob = pack_nvnm(verts, tris, compute_adjacency(tris), [0] * len(tris),
                     0, int(ctx.fid, 16), 0, 0, False, ledges=ledges)
    return [Nav(0, blob)]


def load_navs(esm, names):
    """`{EditorID: [Nav]}` for the named cells of a built ESM."""
    want = {n.lower(): n for n in names}
    fids = {f: want[e.lower()] for f, e in cell_editor_ids(esm).items()
            if e.lower() in want}
    with open(esm, 'rb') as fh:
        data = fh.read()
    out = {n: [] for n in names}
    for rec, stack in walk(data, b'NAVM'):
        name = fids.get(stack.cell)
        blob = rec.sub_map().get(b'NVNM') if name else None
        if blob:
            out[name].append(Nav(rec.form_id, blob))
    return out


def write_dump(path, rows):
    """One line per finding: rule, cell, x y z, navmesh, triangle or node."""
    with open(path, 'w', encoding='utf-8') as fh:
        for cell, rule, p, fid, idx in rows:
            fh.write('%-16s %-28s %8.0f %8.0f %7.0f  %08X %d\n'
                     % (rule, cell, p[0], p[1], p[2], fid, idx))


def main():
    """CLI: check named cells (or an EditorID prefix) of a built plugin."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cells', nargs='*')
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--export', help="export dir (default: the plugin's record folder under export/)")
    ap.add_argument('--prefix', help='every cell whose EditorID starts with this')
    ap.add_argument('--dump', help='write every finding with coordinates here')
    ap.add_argument('--build', choices=('corridor', 'lattice'),
                    help='check a mesh this generator makes now, not the built ESM')
    a = ap.parse_args()
    export = a.export or master_export_dir('export', a.plugin)
    esm = str(plugin_esm('output', a.plugin, export_root(export)))
    names = list(a.cells)
    if a.prefix:
        names += sorted(e for e in cell_editor_ids(esm).values()
                        if e.lower().startswith(a.prefix.lower()))
    idx = NavIndex(export)
    rows = []
    if a.build:
        cells = {n: built_nav(idx.cell(n), a.build == 'lattice') if idx.cell(n) else []
                 for n in names}
    else:
        cells = load_navs(esm, names)
    for name, navs in cells.items():
        ctx = idx.cell(name)
        if not navs or ctx is None:
            print('%s: %s' % (name, 'no navmesh in %s' % esm if ctx else 'not in export'))
            continue
        rows += check_cell(name, navs, ctx)
    if a.dump:
        write_dump(a.dump, rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
