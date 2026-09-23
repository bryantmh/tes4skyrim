"""Stairs materials on the treads and risers of stepped mesh collision.

Skyrim smooths a character over a step only when the triangles it meets have
a material whose MATT record carries the Stair Material flag; stepped
collision without it stutters.  TES4 authored stepped collision with plain
materials, because its character controller climbed steps unmarked.
See: docs/commentary/asset_convert_collision.md#stairs-material
"""
import math
from collections import defaultdict

from asset_convert.collision.collision_material import OB_TO_SK_MATERIAL

#: Skyrim plain material -> its stairs variant, from TES4's paired enums (i and i + 15).
STAIRS_OF = {OB_TO_SK_MATERIAL[i]: OB_TO_SK_MATERIAL[i + 15] for i in range(15)}

#: A tread tilts less than this many degrees from level.
_TREAD_TILT_DEG = 10.0
#: Two treads this close in height (game units) are one tread (a nosing lip, a rug).
_MIN_RISE = 4.0
#: Steepest single step (game units) a flight may climb; Telvanni steps rise up to 44.
_MAX_RISE = 48.0
#: Horizontal gap (game units) a riser or nosing may leave between two treads, beyond the rise itself.
_GAP = 8.0
#: Farthest apart (game units) two treads of one step can sit horizontally.
_REACH = _GAP + _MAX_RISE
#: Share of the smaller triangle two levels may overlap and still be a step (a nosing), not stacked.
_OVERLAP_SHARE = 0.5
#: Inner treads a flight needs; a bed's mattress-pillow-headboard has one.
_MIN_INNER_TREADS = 2
#: Grid cell (game units) for pairing nearby treads.
_CELL = 64.0


def _tilt(t):
    """(degrees the triangle's plane tilts from level, True if it faces up);
    None when it is degenerate."""
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0.0:
        return None
    return math.degrees(math.acos(min(1.0, abs(nz) / length))), nz > 0.0


def _seg_dist(p, a, b):
    """XY distance from point p to segment ab."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    dd = dx * dx + dy * dy
    s = 0.0 if dd == 0.0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / dd))
    ex, ey = a[0] + s * dx - p[0], a[1] + s * dy - p[1]
    return math.sqrt(ex * ex + ey * ey)


def _signed_area(poly):
    """Signed XY area of a polygon (positive when counter-clockwise)."""
    return sum(poly[k - 1][0] * poly[k][1] - poly[k][0] * poly[k - 1][1]
               for k in range(len(poly))) / 2.0


def _overlap_area(p, q):
    """XY area shared by triangles p and q (Sutherland-Hodgman clip of p by q)."""
    if _signed_area(q) < 0.0:
        q = q[::-1]
    poly = list(p)
    for k in range(3):
        (ax, ay), (bx, by) = q[k], q[(k + 1) % 3]
        side = [(bx - ax) * (v[1] - ay) - (by - ay) * (v[0] - ax) for v in poly]
        clipped = []
        for n, v in enumerate(poly):
            w, sv, sw = poly[n - 1], side[n], side[n - 1]
            if (sv >= 0.0) != (sw >= 0.0):
                s = sw / (sw - sv)
                clipped.append((w[0] + s * (v[0] - w[0]), w[1] + s * (v[1] - w[1])))
            if sv >= 0.0:
                clipped.append(v)
        poly = clipped
        if len(poly) < 3:
            return 0.0
    return abs(_signed_area(poly))


def _contact(p, q):
    """(gap, shared) of XY triangles p and q: their boundary distance (0 when
    they overlap) and the area they share."""
    shared = _overlap_area(p, q)
    if shared > 0.0:
        return 0.0, shared
    gap = min(_seg_dist(pt, b[k], b[(k + 1) % 3])
              for a, b in ((p, q), (q, p)) for pt in a for k in range(3))
    return gap, 0.0


def _find(parent, i):
    """Union-find root of `i`, halving the path as it goes."""
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _xy(t, scale):
    """Triangle `t` projected to XY, in game units."""
    return [(v[0] * scale, v[1] * scale) for v in t]


def _bbox(p):
    """XY box (x0, y0, x1, y1) of the points `p`."""
    return min(x for x, _ in p), min(y for _, y in p), max(x for x, _ in p), max(y for _, y in p)


def _cells(box, pad):
    """Grid cells covering `box` grown by `pad` on every side."""
    x0, y0, x1, y1 = box
    return [(gx, gy)
            for gx in range(int((x0 - pad) // _CELL), int((x1 + pad) // _CELL) + 1)
            for gy in range(int((y0 - pad) // _CELL), int((y1 + pad) // _CELL) + 1)]


def _boxes_near(ba, bb):
    """True if XY boxes (x0, y0, x1, y1) come within _REACH of each other."""
    return not (ba[0] - _REACH > bb[2] or bb[0] - _REACH > ba[2]
                or ba[1] - _REACH > bb[3] or bb[1] - _REACH > ba[3])


def _near_pairs(bbox):
    """Index pairs whose XY boxes come within _REACH, found through a grid."""
    grid = defaultdict(list)
    for i, box in bbox.items():
        for cell in _cells(box, _REACH):
            grid[cell].append(i)
    seen = set()
    for members in grid.values():
        for n, a in enumerate(members):
            for b in members[n + 1:]:
                if (a, b) not in seen and _boxes_near(bbox[a], bbox[b]):
                    seen.add((a, b))
                    yield a, b


def _treads_and_steps(flat, tris, scale):
    """Group flat triangles into treads and list the steps between them.

    Returns (parent, tread_area, steps): a union-find over triangle indices
    whose roots are treads, each tread's XY area in game units², and the
    (lower, upper) tread pairs one rise apart whose horizontal gap is at most
    _GAP plus the rise (a riser at 45° or steeper), sharing no more than
    _OVERLAP_SHARE of the smaller tread's area (stacked shelves share it all).
    """
    xy = {i: _xy(tris[i], scale) for i in flat}
    z = {i: sum(v[2] for v in tris[i]) * scale / 3.0 for i in flat}
    area = {i: abs(_signed_area(p)) for i, p in xy.items()}
    parent = {i: i for i in flat}
    rises = []
    for a, b in _near_pairs({i: _bbox(p) for i, p in xy.items()}):
        dz = abs(z[a] - z[b])
        if dz > _MAX_RISE:
            continue
        gap, shared = _contact(xy[a], xy[b])
        if dz >= _MIN_RISE:
            rises.append(((a, b) if z[a] < z[b] else (b, a), gap - dz, shared))
        elif gap <= _GAP:
            parent[_find(parent, a)] = _find(parent, b)
    tread_area = defaultdict(float)
    for i in flat:
        tread_area[_find(parent, i)] += area[i]
    touching, shared_area = set(), defaultdict(float)
    for (lo, hi), gap, shared in rises:
        pair = (_find(parent, lo), _find(parent, hi))
        shared_area[pair] += shared
        if gap <= _GAP and pair[0] != pair[1]:
            touching.add(pair)
    steps = [p for p in touching
             if shared_area[p] <= _OVERLAP_SHARE * min(tread_area[p[0]], tread_area[p[1]])]
    return parent, tread_area, steps


def _flight_treads(tread_area, steps):
    """Tread roots of every flight with at least _MIN_INNER_TREADS inner treads
    (a step both below and above), keeping only treads no larger than twice
    the flight's median inner tread (a floor or wide landing is not a tread)."""
    up, down, flight = defaultdict(set), defaultdict(set), {}
    for a, b in steps:
        up[a].add(b)
        down[b].add(a)
        flight.setdefault(a, a)
        flight.setdefault(b, b)
        flight[_find(flight, a)] = _find(flight, b)
    inner = defaultdict(list)
    for t in flight:
        if up[t] and down[t]:
            inner[_find(flight, t)].append(tread_area[t])
    limit = {f: 2.0 * sorted(a)[len(a) // 2] for f, a in inner.items() if len(a) >= _MIN_INNER_TREADS}
    return {t for t in flight if tread_area[t] <= limit.get(_find(flight, t), -1.0)}


def _risers(tris, flat, parent, treads, steps, scale):
    """Indices of non-level triangles spanning one step of a flight, including
    the step from an end tread onto the floor or landing beside it: their
    height lies within the step's rise (± _MIN_RISE), they come within _GAP
    of one of its treads and within _GAP plus the rise of the other (a
    chamfered step leaves its riser a rise away from the lower tread)."""
    steps = [(a, b) for a, b in steps if a in treads or b in treads]
    ends = {r for step in steps for r in step}
    members = defaultdict(list)
    for i in flat:
        if (root := _find(parent, i)) in ends:
            members[root].append(i)
    tread_z = {r: sum(v[2] for i in m for v in tris[i]) * scale / (3 * len(m)) for r, m in members.items()}
    bands = [(tread_z[a], tread_z[b], a, b) for a, b in steps]
    xy = {i: _xy(tris[i], scale) for m in members.values() for i in m}
    grid = defaultdict(list)
    for i, p in xy.items():
        for cell in _cells(_bbox(p), _REACH):
            grid[cell].append(i)
    out = []
    for j, t in enumerate(tris):
        tilt = _tilt(t)
        if tilt is None or tilt[0] < _TREAD_TILT_DEG:
            continue
        lo, hi = min(v[2] for v in t) * scale, max(v[2] for v in t) * scale
        spans = [(za, zb, a, b) for za, zb, a, b in bands if za - _MIN_RISE <= lo and hi <= zb + _MIN_RISE]
        if spans and _spans_a_step(_xy(t, scale), spans, grid, xy, parent):
            out.append(j)
    return out


def _spans_a_step(p, spans, grid, xy, parent):
    """True if XY triangle `p` comes within _GAP of one tread of a (za, zb, lower,
    upper) step in `spans` and within _GAP plus that step's rise of the other."""
    gap = defaultdict(lambda: math.inf)
    for i in {i for cell in _cells(_bbox(p), _REACH) for i in grid.get(cell, ())}:
        root = _find(parent, i)
        gap[root] = min(gap[root], _contact(p, xy[i])[0])
    return any(min(gap[a], gap[b]) <= _GAP and max(gap[a], gap[b]) <= _GAP + zb - za
               for za, zb, a, b in spans)


def stairs_materials(tris, materials, scale):
    """`materials` with every tread and riser of a stepped flight switched to its stairs variant.

    tris: triangles in the final shape frame, wound so walkable faces point
    up (+Z); materials: one Skyrim material CRC per triangle; scale: game
    units per triangle unit.  A triangle whose material has no stairs
    variant, or already is one, keeps it.
    """
    flat = [i for i, t in enumerate(tris)
            if (tilt := _tilt(t)) is not None and tilt[1] and tilt[0] < _TREAD_TILT_DEG]
    if len(flat) < 3:
        return list(materials)
    parent, tread_area, steps = _treads_and_steps(flat, tris, scale)
    treads = _flight_treads(tread_area, steps)
    tread_tris = [i for i in flat if _find(parent, i) in treads]
    out = list(materials)
    for i in tread_tris + _risers(tris, flat, parent, treads, steps, scale):
        out[i] = STAIRS_OF.get(out[i], out[i])
    return out
