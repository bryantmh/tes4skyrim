"""Navmesh invariant measurements, shared by the sweep/render/compare tools.

ONE definition of each invariant, so a render and a sweep can never disagree
about whether a cell is broken.  The invariants, and why each one exists:

  MISS     pathgrid sample with no navmesh under it.  The pathgrid is the
           authored ground truth; an uncovered walked line is always a failure.
  CRACK    a walked line crosses a BOUNDARY edge at similar z.  Plan coverage
           looks continuous but the two sides share no edge, so the engine
           cannot path across.  This is the in-game "hole in the stairs" that
           coverage numbers are blind to.
  CHOKE    the continuous covered cross-width along a walked line drops below
           half a doorway (48u).  Connectivity is NOT the metric: a passage
           an NPC cannot fit through is unwalkable even though the graph says
           it is connected.
  OVL      two triangles overlapping in plan at the same height (same-surface
           overlap) — the engine picks one arbitrarily.
  VERT     near-vertical triangles: walls, not floor.
  DMISS    door passage squares with no mesh: an NPC cannot use that door.
  BADNESS  shape contract, max(ratio/MAX_EDGE_RATIO, aspect/MAX_TRI_ASPECT).
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.navmesh import clean_validate, params

GRID = 128.0
CHOKE_MIN_WIDTH = 48.0      # half a doorway
COVER_Z = 80.0
CRACK_Z = 60.0


class Surface(object):
    """Plan-bucketed triangle soup with height queries."""

    def __init__(self, verts, tris):
        self.v = verts
        self.t = tris
        self.grid = {}
        for ti, tri in enumerate(tris):
            xs = [verts[k][0] for k in tri]
            ys = [verts[k][1] for k in tri]
            for gx in range(int(min(xs) // GRID), int(max(xs) // GRID) + 1):
                for gy in range(int(min(ys) // GRID), int(max(ys) // GRID) + 1):
                    self.grid.setdefault((gx, gy), []).append(ti)

    def _zat(self, tri, x, y):
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (self.v[k][:3] for k in tri)
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-9:
            return None
        l0 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d
        l1 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d
        l2 = 1 - l0 - l1
        if l0 < -0.02 or l1 < -0.02 or l2 < -0.02:
            return None
        return l0 * az + l1 * bz + l2 * cz

    def covered(self, x, y, z, tol=COVER_Z):
        for ti in self.grid.get((int(x // GRID), int(y // GRID)), ()):
            zz = self._zat(self.t[ti], x, y)
            if zz is not None and abs(zz - z) <= tol:
                return True
        return False

    def height_near(self, x, y, z, tol=CRACK_Z):
        """Height of the surface nearest z at (x, y), or None."""
        best = None
        for ti in self.grid.get((int(x // GRID), int(y // GRID)), ()):
            zz = self._zat(self.t[ti], x, y)
            if zz is not None and abs(zz - z) <= tol:
                if best is None or abs(zz - z) < abs(best - z):
                    best = zz
        return best


def boundary_edges(tris):
    """Edges with exactly one owner, plus the full owner-count map."""
    counts = {}
    for tri in tris:
        for k in range(3):
            a, b = tri[k], tri[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    return [e for e, n in counts.items() if n == 1], counts


def _seg_cross(x0, y0, x1, y1, x2, y2, x3, y3):
    d1 = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
    d2 = (x1 - x0) * (y3 - y0) - (y1 - y0) * (x3 - x0)
    d3 = (x3 - x2) * (y0 - y2) - (y3 - y2) * (x0 - x2)
    d4 = (x3 - x2) * (y1 - y2) - (y3 - y2) * (x1 - x2)
    return d1 * d2 < 0 and d3 * d4 < 0


def crossed_boundary_edges(verts, tris, cell, step=16.0):
    """Boundary edges that a walked pathgrid line crosses at similar z.

    Each one is a crack: the walked line runs off the edge of the mesh and
    (usually) straight onto another triangle that shares no vertex with it.
    """
    bedges, _counts = boundary_edges(tris)
    bgrid = {}
    for (a, b) in bedges:
        xs = [verts[a][0], verts[b][0]]
        ys = [verts[a][1], verts[b][1]]
        for gx in range(int(min(xs) // GRID), int(max(xs) // GRID) + 1):
            for gy in range(int(min(ys) // GRID), int(max(ys) // GRID) + 1):
                bgrid.setdefault((gx, gy), []).append((a, b))
    hit = set()
    for (a, b) in cell.edges:
        pa, pb = cell.nodes[a], cell.nodes[b]
        n = max(2, int(math.dist(pa[:2], pb[:2]) / step) + 1)
        for i in range(n):
            f0, f1 = i / n, (i + 1) / n
            x0 = pa[0] + (pb[0] - pa[0]) * f0
            y0 = pa[1] + (pb[1] - pa[1]) * f0
            x1 = pa[0] + (pb[0] - pa[0]) * f1
            y1 = pa[1] + (pb[1] - pa[1]) * f1
            z0 = pa[2] + (pb[2] - pa[2]) * f0
            for (ea, eb) in bgrid.get((int(x0 // GRID), int(y0 // GRID)), ()):
                if abs(0.5 * (verts[ea][2] + verts[eb][2]) - z0) > CRACK_Z:
                    continue
                if _seg_cross(x0, y0, x1, y1, verts[ea][0], verts[ea][1],
                              verts[eb][0], verts[eb][1]):
                    hit.add((ea, eb))
    return sorted(hit)


NOTCH_MAX_MOUTH = 160.0
NOTCH_MIN_DEPTH_RATIO = 1.2


def open_notches(verts, tris, cell=None, near=256.0):
    """V-shaped bites cut into the surface: apex inward, mouth open.

    THE METRIC THAT WAS MISSING.  `crossed_boundary_edges` only sees a crack
    when a walked line CROSSES it, and `count_missing` only samples the walked
    centerline — so a notch beside the centerline reads as perfectly healthy on
    both while being plainly visible (and unwalkable) in a render.  That blind
    spot is how "0 holes, min width 88u" got reported for a staircase that had
    two V-cracks bitten out of it.

    A notch is a boundary vertex with exactly two boundary edges whose far ends
    are closer to each other than the sides are long (a slim V, not a room
    corner), with no edge already bridging the mouth.  Returns
    [(apex_i, p_i, q_i, depth, mouth), ...], deepest first.
    """
    bedges, counts = boundary_edges(tris)
    at = {}
    for (a, b) in bedges:
        at.setdefault(a, []).append(b)
        at.setdefault(b, []).append(a)
    out = []
    for apex, ends in at.items():
        if len(ends) != 2:
            continue
        p, q = ends
        va, vp, vq = verts[apex], verts[p], verts[q]
        mouth = math.dist(vp[:2], vq[:2])
        if mouth < 1e-6 or mouth > NOTCH_MAX_MOUTH:
            continue
        side = min(math.dist(va[:2], vp[:2]), math.dist(va[:2], vq[:2]))
        if side < 1e-6 or side < mouth * NOTCH_MIN_DEPTH_RATIO:
            continue        # wide and shallow: an ordinary convex corner
        key = (min(p, q), max(p, q))
        if counts.get(key, 0) > 0:
            continue        # mouth already bridged
        if cell is not None and near:
            # Only notches on ground an actor uses are defects.
            best = min((math.dist((va[0], va[1]), (n[0], n[1]))
                        for n in cell.nodes), default=1e9)
            if best > near:
                continue
        out.append((apex, p, q, side, mouth))
    out.sort(key=lambda r: -r[3])
    return out


def count_missing(surf, cell, step=16.0):
    return sum(1 for (x, y, z) in cell.walked_samples(step)
               if not surf.covered(x, y, z))


def walked_gaps(surf, cell, step=16.0):
    """CONTIGUOUS runs of uncovered samples on a walked line, worst first.

    A bare `miss` count is what let a hole straight through Pinarus's upstairs
    doorway ship as "miss=1" — one uncovered sample reads as fringe noise, and
    three in a row across a threshold is an impassable wall.  What matters is
    how LONG the unbroken gap is and where it sits, so this returns the runs
    themselves: [(length_units, (x, y, z) midpoint, n_samples), ...].
    """
    out = []
    for (a, b) in cell.edges:
        pa, pb = cell.nodes[a], cell.nodes[b]
        n = max(2, int(math.dist(pa[:2], pb[:2]) / step) + 1)
        run = []
        for i in range(n + 1):
            f = i / n
            p = (pa[0] + (pb[0] - pa[0]) * f,
                 pa[1] + (pb[1] - pa[1]) * f,
                 pa[2] + (pb[2] - pa[2]) * f)
            if surf.covered(*p):
                if run:
                    out.append(run)
                    run = []
            else:
                run.append(p)
        if run:
            out.append(run)
    gaps = []
    for run in out:
        length = math.dist(run[0][:2], run[-1][:2]) + step
        mid = run[len(run) // 2]
        gaps.append((length, mid, len(run)))
    gaps.sort(key=lambda g: -g[0])
    return gaps


def door_gaps(surf, cell, min_len=0.0):
    """Walked-line gaps that sit IN a doorway — always a blocking defect.

    A door is the one place a gap cannot be routed around, so these are
    reported separately from ordinary coverage misses and never averaged into
    a single number.
    """
    out = []
    for (length, mid, count) in walked_gaps(surf, cell):
        if length < min_len:
            continue
        for (dx, dy, dz, _rz, fid, _tp, w) in cell.doors:
            half = max(0.5 * (w or 96.0), 48.0)
            if (math.dist((mid[0], mid[1]), (dx, dy)) <= half
                    and abs(mid[2] - dz) <= 128.0):
                out.append((length, mid, count, fid))
                break
    return out


def count_cracked_edges(verts, tris, cell, step=16.0):
    """Number of pathgrid EDGES whose walk crosses a boundary."""
    bedges, _counts = boundary_edges(tris)
    bgrid = {}
    for (a, b) in bedges:
        xs = [verts[a][0], verts[b][0]]
        ys = [verts[a][1], verts[b][1]]
        for gx in range(int(min(xs) // GRID), int(max(xs) // GRID) + 1):
            for gy in range(int(min(ys) // GRID), int(max(ys) // GRID) + 1):
                bgrid.setdefault((gx, gy), []).append((a, b))
    bad = 0
    for (a, b) in cell.edges:
        pa, pb = cell.nodes[a], cell.nodes[b]
        n = max(2, int(math.dist(pa[:2], pb[:2]) / step) + 1)
        for i in range(n):
            f0, f1 = i / n, (i + 1) / n
            x0 = pa[0] + (pb[0] - pa[0]) * f0
            y0 = pa[1] + (pb[1] - pa[1]) * f0
            x1 = pa[0] + (pb[0] - pa[0]) * f1
            y1 = pa[1] + (pb[1] - pa[1]) * f1
            z0 = pa[2] + (pb[2] - pa[2]) * f0
            crossed = False
            for (ea, eb) in bgrid.get((int(x0 // GRID), int(y0 // GRID)), ()):
                if abs(0.5 * (verts[ea][2] + verts[eb][2]) - z0) > CRACK_Z:
                    continue
                if _seg_cross(x0, y0, x1, y1, verts[ea][0], verts[ea][1],
                              verts[eb][0], verts[eb][1]):
                    crossed = True
                    break
            if crossed:
                bad += 1
                break
    return bad


def corridor_width(surf, x, y, z, wx, wy, step=4.0, reach=16):
    """Continuous covered cross-width at (x, y, z) along (wx, wy).

    Walks outward in both directions, following the surface in z (a staircase
    is continuous even though every step changes height), and stops at the
    first gap.  This is the number the chokepoint rule is written against.
    """
    width = step
    for sign in (1.0, -1.0):
        zc = z
        for s in range(1, reach + 1):
            zz = surf.height_near(x + wx * sign * s * step,
                                  y + wy * sign * s * step, zc)
            if zz is None:
                break
            zc = zz
            width += step
    return width


def count_chokepoints(surf, cell, min_width=CHOKE_MIN_WIDTH):
    """Pathgrid edges pinched below min_width somewhere along their walk."""
    bad = 0
    for (a, b) in cell.edges:
        pa, pb = cell.nodes[a], cell.nodes[b]
        run = math.dist(pa[:2], pb[:2])
        if run < 24:
            continue
        ux, uy = (pb[0] - pa[0]) / run, (pb[1] - pa[1]) / run
        wx, wy = -uy, ux
        n = max(2, int(run / 24.0))
        wmin = 1e9
        for i in range(1, n):
            f = i / n
            x = pa[0] + (pb[0] - pa[0]) * f
            y = pa[1] + (pb[1] - pa[1]) * f
            z = pa[2] + (pb[2] - pa[2]) * f
            if surf.height_near(x, y, z) is None:
                continue        # a coverage miss, counted separately
            wmin = min(wmin, corridor_width(surf, x, y, z, wx, wy))
        if wmin < min_width:
            bad += 1
    return bad


def count_overlaps(verts, tris, dz=40.0):
    """Same-surface plan overlaps (the engine picks one arbitrarily)."""
    from shapely.geometry import Polygon
    from shapely import STRtree
    polys = []
    for tri in tris:
        pts = [verts[k] for k in tri]
        try:
            pgn = Polygon([(p[0], p[1]) for p in pts])
        except Exception:
            polys.append(None)
            continue
        polys.append(pgn if pgn.is_valid and pgn.area > 4.0 else None)
    geoms = [(ti, p) for ti, p in enumerate(polys) if p is not None]
    if not geoms:
        return 0, []
    tree = STRtree([p for (_ti, p) in geoms])
    gmap = [ti for (ti, _p) in geoms]
    surf = Surface(verts, tris)
    pairs = []
    for gi in range(len(geoms)):
        ti = gmap[gi]
        for gj in tree.query(geoms[gi][1]).tolist():
            tj = gmap[gj]
            if tj <= ti or set(tris[ti]) & set(tris[tj]):
                continue
            try:
                inter = geoms[gi][1].intersection(polys[tj])
                if inter.area <= 4.0:
                    continue
            except Exception:
                continue
            cx, cy = inter.centroid.x, inter.centroid.y
            zi = surf._zat(tris[ti], cx, cy)
            zj = surf._zat(tris[tj], cx, cy)
            if zi is None or zj is None or abs(zi - zj) > dz:
                continue
            pairs.append((ti, tj, inter.area, cx, cy))
    return len(pairs), pairs


def count_vertical(verts, tris):
    n = 0
    for tri in tris:
        pts = [verts[k] for k in tri]
        ax, ay, az = (pts[1][i] - pts[0][i] for i in range(3))
        bx, by, bz = (pts[2][i] - pts[0][i] for i in range(3))
        nx, ny, nz = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
        area3 = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
        plan = 0.5 * abs(ax * by - ay * bx)
        if area3 > 30.0 and plan / area3 < 0.45:
            n += 1
    return n


def count_door_misses(surf, cell):
    """Door passage squares with no mesh — an NPC cannot use that door."""
    dmiss = 0
    for (dx, dy, dz, rz, _f, tp, w) in cell.doors:
        fx, fy = math.cos(rz), -math.sin(rz)
        tx, ty = math.sin(rz), math.cos(rz)
        half = min(0.5 * w if w else 45.0, 110.0)
        for side in (-1, 1):
            rows = []
            for off in (24, 48):
                for lat in (-0.55 * half, 0.0, 0.55 * half):
                    x = dx + fx * off * side + tx * lat
                    y = dy + fy * off * side + ty * lat
                    rows.append(surf.covered(x, y, dz))
            bad = rows.count(False)
            # A one-sided teleport door legitimately has no mesh on its far
            # side (the far side is another cell), so only count it when the
            # near side is also empty.
            if bad and not (tp and bad == len(rows)):
                dmiss += bad
    return dmiss


def shape_stats(verts, tris):
    """(p50, p90, pct over contract, pct under MIN_TRI_AREA)."""
    bad = []
    small = 0
    for tri in tris:
        bad.append(clean_validate.badness(verts, tri))
        p, q, r = (verts[k] for k in tri)
        area = abs((q[0] - p[0]) * (r[1] - p[1])
                   - (q[1] - p[1]) * (r[0] - p[0])) * 0.5
        if area < params.MIN_TRI_AREA:
            small += 1
    bad.sort()

    def pct(p):
        return bad[min(len(bad) - 1, int(p * len(bad)))]

    return (pct(0.5), pct(0.9),
            100.0 * sum(1 for b in bad if b > 1.0) / len(bad),
            100.0 * small / len(tris))


def measure(verts, tris, cell, want_overlaps=True):
    """Every invariant for one cell, as a dict."""
    surf = Surface(verts, tris)
    gaps = walked_gaps(surf, cell)
    comps = sorted((len(c) for c in clean_validate.components(tris)),
                   reverse=True)
    p50, p90, over, small = shape_stats(verts, tris)
    ovl = count_overlaps(verts, tris)[0] if want_overlaps else -1
    return {
        'tris': len(tris),
        'comps': comps,
        'miss': count_missing(surf, cell),
        'gap': (gaps[0][0] if gaps else 0.0),      # worst contiguous run
        'doorgap': len(door_gaps(surf, cell)),     # gaps inside a doorway
        'crack': count_cracked_edges(verts, tris, cell),
        'notch': len(open_notches(verts, tris, cell)),
        'choke': count_chokepoints(surf, cell),
        'ovl': ovl,
        'vert': count_vertical(verts, tris),
        'dmiss': count_door_misses(surf, cell),
        'p50': p50, 'p90': p90, 'over': over, 'small': small,
    }


def format_row(name, m, seconds=None):
    # DOORGAP and GAP come FIRST: they are the "an NPC cannot get through"
    # numbers.  A bare miss count hid a 3-sample hole straight through
    # Pinarus's upstairs doorway as "miss=1", indistinguishable from fringe
    # noise, and it shipped.  Never rank a cell on `miss` alone again.
    return ('%-28s tris=%5d comps=%-14s DOORGAP=%-2d gap=%-4.0f miss=%-3d '
            'crack=%-3d notch=%-3d choke=%-3d ovl=%-3d vert=%-2d dmiss=%-3d '
            'bad p50=%.2f p90=%.2f >1:%2d%% small=%2d%%%s'
            % (name, m['tris'], m['comps'][:4], m.get('doorgap', -1),
               m.get('gap', -1), m['miss'], m['crack'],
               m.get('notch', -1), m['choke'], m['ovl'], m['vert'],
               m['dmiss'], m['p50'], m['p90'], round(m['over']),
               round(m['small']),
               ('  %.0fs' % seconds) if seconds is not None else ''))
