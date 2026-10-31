"""Shape repair for a finished corridor mesh: collapse, flip, bisect.

`decimate` is the driver.  It never moves the outline, never touches a pinned
door vertex, and never lets a collapse worsen the shape of anything it touches;
what collapse cannot reach, edge flips and long-edge bisection do.

See: docs/commentary/tes5_import_navmesh.md#decimation
"""

import math

from . import native_loader, params

def _badness(verts, t):
    """Normalized shape badness; 1.0 = exactly at the contract boundary.

    max(edge_ratio / MAX_EDGE_RATIO, aspect / MAX_TRI_ASPECT): the ratio term
    catches needles (one short edge), the aspect term catches CAPS (all edges
    comparable, near-zero height) which the ratio cannot see.
    """
    p, q, r = verts[t[0]], verts[t[1]], verts[t[2]]
    e = [math.dist(p[:2], q[:2]), math.dist(q[:2], r[:2]),
         math.dist(r[:2], p[:2])]
    lo, hi = min(e), max(e)
    area = abs((q[0] - p[0]) * (r[1] - p[1]) -
               (q[1] - p[1]) * (r[0] - p[0])) * 0.5
    if lo <= 1e-9 or area <= 1e-9:
        return 1e9
    return max((hi / lo) / params.MAX_EDGE_RATIO,
               (hi * hi / (4.0 * area)) / params.MAX_TRI_ASPECT)


def _near_a_pin(v, buckets, cell, default_r):
    """True if vertex `v` lies inside any pin's radius."""
    gx, gy = int(v[0] // cell), int(v[1] // cell)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for p in buckets.get((gx + dx, gy + dy), ()):
                r = p[2] if len(p) > 2 else default_r
                if (v[0] - p[0]) ** 2 + (v[1] - p[1]) ** 2 <= r * r:
                    return True
    return False


def _pin_verts(verts, pinned_xy):
    """Vertex indices pinned by the (x, y[, radius]) door-pin list.

    Bucketed: the pin list carries every pathgrid node, and the naive
    O(verts x pins) scan was 2.5s of a single cave cell's build.
    """
    if not pinned_xy:
        return set()
    default_r = params.DECIMATE_PIN_RADIUS
    rmax = max((p[2] if len(p) > 2 else default_r) for p in pinned_xy)
    cell = max(float(rmax), 1.0)
    buckets = {}
    for p in pinned_xy:
        buckets.setdefault((int(p[0] // cell), int(p[1] // cell)),
                           []).append(p)
    return {vi for vi, v in enumerate(verts)
            if _near_a_pin(v, buckets, cell, default_r)}


CONCAVE_CUT_FRAC = 0.25


def _boundary_verts(tris):
    """(boundary vertex set, {vertex: [boundary neighbours]})."""
    cnt = {}
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            cnt[key] = cnt.get(key, 0) + 1
    bset, nbr = set(), {}
    for (a, b), c in cnt.items():
        if c == 1:
            bset.add(a)
            bset.add(b)
            nbr.setdefault(a, []).append(b)
            nbr.setdefault(b, []).append(a)
    return bset, nbr


def _outline_error(verts, vi, nbr):
    """How far the outline would move if boundary vertex vi were removed.

    A junction or fork on the outline answers 1e9, so it is always kept.
    """
    ns = nbr.get(vi, ())
    if len(ns) != 2:
        return 1e9
    p = verts[vi]
    a, b = verts[ns[0]], verts[ns[1]]
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    if d2 < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2))
    return math.hypot(p[0] - (a[0] + dx * t), p[1] - (a[1] + dy * t))


def _area2(verts, t):
    """Twice the signed plan area of a triangle."""
    p, q, r = verts[t[0]], verts[t[1]], verts[t[2]]
    return ((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]))


def _mesh_area(verts, tris):
    """Total plan area of a triangle list."""
    return sum(abs(_area2(verts, t)) for t in tris) * 0.5


def _short_edges(verts, tris, min_edge):
    """Every edge shorter than min_edge, shortest first, as (length, key)."""
    cands, seen = [], set()
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            d = math.dist(verts[a][:2], verts[b][:2])
            if d < min_edge:
                cands.append((d, key))
    cands.sort()
    return cands


class _Collapse:
    """One decimation round's mutable mesh state.

    See: docs/commentary/tes5_import_navmesh.md#decimate-keeps-a-vertex-incidence-map
    """

    def __init__(self, verts, tris, boundary, bnbr, pin, on_seam,
                 ground_ok=None):
        """Index the triangles by vertex and start with nothing collapsed."""
        self.verts = verts
        self.tris = [tuple(t) for t in tris]
        self.boundary = boundary
        self.bnbr = bnbr
        self.pin = pin
        self.on_seam = on_seam
        self.ground_ok = ground_ok
        self.alive = [True] * len(self.tris)
        self.vmap = {}
        for ti, t in enumerate(self.tris):
            for i in t:
                self.vmap.setdefault(i, set()).add(ti)
        self.gone = set()
        self.remap = {}
        self.changed = False

    def resolve(self, i):
        """Follow the remap chain to the vertex that absorbed i."""
        while i in self.remap:
            i = self.remap[i]
        return i

    def outline_error(self, vi):
        """This vertex's deviation from its two boundary neighbours' chord."""
        return _outline_error(self.verts, vi, self.bnbr)

    def is_convex(self, vi):
        """Does boundary vertex vi jut OUTWARD, away from the interior?

        See: docs/commentary/tes5_import_navmesh.md#sawtooth-and-concave-allowances
        """
        ns = self.bnbr.get(vi, ())
        if len(ns) != 2:
            return False
        a0, a1 = self.verts[ns[0]], self.verts[ns[1]]
        p = self.verts[vi]
        dx, dy = a1[0] - a0[0], a1[1] - a0[1]
        side_v = dx * (p[1] - a0[1]) - dy * (p[0] - a0[0])
        cx = cy = 0.0
        cnt = 0
        for ti in sorted(self.vmap.get(vi, ())):
            if not self.alive[ti]:
                continue
            t = self.tris[ti]
            cx += sum(self.verts[i][0] for i in t) / 3.0
            cy += sum(self.verts[i][1] for i in t) / 3.0
            cnt += 1
        if not cnt:
            return False
        side_c = (dx * (cy / cnt - a0[1]) - dy * (cx / cnt - a0[0]))
        if abs(side_v) < 1e-9 or abs(side_c) < 1e-9:
            return False
        return (side_v > 0) != (side_c > 0)

    def pins_are_redundant(self, a, b):
        """May two PINNED vertices still fuse?  Only if both add no position.

        See: docs/commentary/tes5_import_navmesh.md#a-pin-protects-a-position
        """
        if not (a in self.boundary and b in self.boundary):
            return False
        if b not in self.bnbr.get(a, ()):
            return False
        return min(self.outline_error(a),
                   self.outline_error(b)) <= params.DECIMATE_OUTLINE_TOL

    def _outline_move_ok(self, vi, budget_left):
        """May the outline give up this boundary vertex?

        See: docs/commentary/tes5_import_navmesh.md#sawtooth-and-concave-allowances
        """
        err = self.outline_error(vi)
        tol = params.DECIMATE_OUTLINE_TOL
        if err <= tol:
            return True
        if not budget_left or self.on_seam(vi):
            return False
        if self.is_convex(vi):
            return err <= params.DECIMATE_SAWTOOTH_DEV
        if err <= max(tol, CONCAVE_CUT_FRAC * params.RIBBON_HALF_WIDTH):
            return True
        return self.notch_is_open_ground(vi, err)

    def notch_is_open_ground(self, vi, err):
        """May a deeper concave notch go?  Only with collision saying so.

        See: docs/commentary/tes5_import_navmesh.md#concave-notches-straightened-against-collision
        """
        if self.ground_ok is None or err > params.DECIMATE_SAWTOOTH_DEV:
            return False
        a, b = self.bnbr[vi]
        return self.ground_ok(self.verts[a], self.verts[vi], self.verts[b])

    def boundary_pair(self, a, b, budget_left):
        """(keep, drop) for two OUTLINE vertices, or None if neither may move.

        See: docs/commentary/tes5_import_navmesh.md#the-outline-may-not-move
        """
        if b not in self.bnbr.get(a, ()):
            return None
        ok_a = self._outline_move_ok(a, budget_left)
        ok_b = self._outline_move_ok(b, budget_left)
        if not (ok_a or ok_b):
            return None
        pin_a, pin_b = a in self.pin, b in self.pin
        if pin_a and pin_b:
            return ((a, b) if self.outline_error(b) <= self.outline_error(a)
                    else (b, a))
        if pin_a or pin_b:
            if (pin_a and not ok_b) or (pin_b and not ok_a):
                return None
            return (a, b) if pin_a else (b, a)
        if ok_a and (not ok_b or self.outline_error(a)
                     <= self.outline_error(b)):
            return b, a
        return a, b

    def link_condition_ok(self, a, b):
        """Would collapsing a-b pinch the surface into a bowtie?

        See: docs/commentary/tes5_import_navmesh.md#link-condition-guards-the-bowtie
        """
        la = {v for ti in self.vmap.get(a, ()) if self.alive[ti]
              for v in self.tris[ti]} - {a, b}
        lb = {v for ti in self.vmap.get(b, ()) if self.alive[ti]
              for v in self.tris[ti]} - {a, b}
        opp = {v for ti in self.vmap.get(a, ())
               if self.alive[ti] and b in self.tris[ti]
               for v in self.tris[ti]} - {a, b}
        if not ((la & lb) - opp):
            return True
        shared = sum(1 for ti in self.vmap.get(a, ())
                     if self.alive[ti] and b in self.tris[ti])
        return shared == 1

    def incident(self, keep, drop):
        """Triangle indices touching either vertex, ascending (not set order).

        See: docs/commentary/tes5_import_navmesh.md#native-port-ab
        """
        return sorted(self.vmap.get(keep, set()) | self.vmap.get(drop, set()))

    def affected(self, keep, drop):
        """(indices, triangles) that survive the collapse and must be rewound."""
        inc = self.incident(keep, drop)
        idx = [ti for ti in inc if self.alive[ti]
               and not (keep in self.tris[ti] and drop in self.tris[ti])]
        return idx, [self.tris[ti] for ti in idx]

    def shape_ok(self, before, new_tris, old_signs):
        """True if no affected triangle flips, degenerates or loses shape.

        `before` is the worst badness measured BEFORE the vertex moved.
        See: docs/commentary/tes5_import_navmesh.md#shape-bound-is-not-strict-improvement
        """
        for t, s0 in zip(new_tris, old_signs):
            if len(set(t)) < 3:
                return False
            ar = _area2(self.verts, t)
            if abs(ar) < 1e-6 or ((ar > 0) != s0):
                return False
        after = max([_badness(self.verts, t) for t in new_tris], default=1.0)
        return after <= max(before, 1.0) + 1e-9

    def cut_area(self, keep, drop, affected, new_tris, target, old):
        """Plan area the outline gives up by this boundary collapse.

        See: docs/commentary/tes5_import_navmesh.md#boundary-cuts-are-charged-to-a-budget
        """
        killed = [self.tris[ti] for ti in self.incident(keep, drop)
                  if self.alive[ti]
                  and keep in self.tris[ti] and drop in self.tris[ti]]
        self.verts[keep] = old
        before_area = _mesh_area(self.verts, affected + killed)
        self.verts[keep] = target
        return max(0.0, before_area - _mesh_area(self.verts, new_tris))

    def commit(self, keep, drop):
        """Rewrite every triangle around `drop` to use `keep` instead."""
        self.remap[drop] = keep
        self.gone.add(drop)
        for ti in list(self.vmap.get(drop, ())):
            if not self.alive[ti]:
                continue
            t = self.tris[ti]
            nt = tuple(keep if i == drop else i for i in t)
            for i in set(t):
                self.vmap.get(i, set()).discard(ti)
            if len(set(nt)) < 3:
                self.alive[ti] = False
                continue
            self.tris[ti] = nt
            for i in set(nt):
                self.vmap.setdefault(i, set()).add(ti)
        self.changed = True

    def survivors(self):
        """The triangles still alive after this round's collapses."""
        return [t for ti, t in enumerate(self.tris) if self.alive[ti]]


def _collapse_target(st, a, b, budget_left):
    """(keep, drop, new position) for this candidate edge, or None.

    See: docs/commentary/tes5_import_navmesh.md#the-outline-may-not-move
    """
    a_b, b_b = a in st.boundary, b in st.boundary
    pin_a, pin_b = a in st.pin, b in st.pin
    if pin_a and pin_b and not st.pins_are_redundant(a, b):
        return None
    if a_b and b_b:
        pair = st.boundary_pair(a, b, budget_left)
        if pair is None:
            return None
        keep, drop = pair
    elif a_b:
        keep, drop = a, b
    elif b_b:
        keep, drop = b, a
    elif pin_a or pin_b:
        keep, drop = (a, b) if pin_a else (b, a)
    else:
        return a, b, [(st.verts[a][0] + st.verts[b][0]) * 0.5,
                      (st.verts[a][1] + st.verts[b][1]) * 0.5,
                      (st.verts[a][2] + st.verts[b][2]) * 0.5]
    return keep, drop, st.verts[keep][:]


def _try_collapse(st, a, b, area_lost, budget):
    """Attempt one edge collapse; returns the area it cost, or None if refused.

    A refusal restores the moved vertex, so the mesh is left exactly as found.
    See: docs/commentary/tes5_import_navmesh.md#boundary-cuts-are-charged-to-a-budget
    """
    got = _collapse_target(st, a, b, area_lost < budget)
    if got is None:
        return None
    keep, drop, target = got
    if not st.link_condition_ok(a, b):
        return None
    _idx, affected = st.affected(keep, drop)
    old = st.verts[keep][:]
    old_signs = [_area2(st.verts, t) > 0 for t in affected]
    before = max([_badness(st.verts, t) for t in affected], default=1.0)
    st.verts[keep] = target
    new_tris = [tuple(keep if i == drop else i for i in t) for t in affected]
    loss = 0.0
    ok = st.shape_ok(before, new_tris, old_signs)
    if ok and a in st.boundary and b in st.boundary:
        loss = st.cut_area(keep, drop, affected, new_tris, target, old)
        if loss > 1.0 and area_lost + loss > budget:
            ok = False
    if not ok:
        st.verts[keep] = old
        return None
    st.commit(keep, drop)
    return loss


def _decimate_round(verts, tris, pin, on_seam, min_edge, budget, area_lost,
                    ground_ok=None):
    """One collapse+flip round; returns (tris, area lost, progressed) or None.

    `area_lost` is the running total across ALL rounds, charged against the
    one fixed `budget`.  None means there was no short edge left to consider,
    which ends the loop before the flip and split passes.
    """
    boundary, bnbr = _boundary_verts(tris)
    cands = _short_edges(verts, tris, min_edge)
    if not cands:
        return None
    st = _Collapse(verts, tris, boundary, bnbr, pin, on_seam, ground_ok)
    for _d, (ea, eb) in cands:
        a, b = st.resolve(ea), st.resolve(eb)
        if a == b or a in st.gone or b in st.gone:
            continue
        loss = _try_collapse(st, a, b, area_lost, budget)
        if loss is not None:
            area_lost += loss
    tris = st.survivors()
    flipped = _flip_pass(verts, tris, pin)
    if flipped is not None:
        tris = flipped
    return tris, area_lost, st.changed or flipped is not None


def decimate(verts, tris, pinned_xy=None, seam_bounds=None, rounds=None,
             allow_split=True, ground_ok=None, surface=None):
    """Collapse sliver-producing SHORT edges into near-equilateral triangles.

    pinned_xy: [(x, y), ...] positions that must survive (door thresholds).
    seam_bounds: (minx, miny, maxx, maxy) exterior cell rectangle, whose
    boundary vertices only ever collapse under the strict collinear rule.
    ground_ok: f(a, v, b) -> True when a concave notch may be cut straight.
    surface: f(x, y, z) -> the walkable height a needle split sits on.
    See: docs/commentary/tes5_import_navmesh.md#decimation
    """
    if native_loader.NATIVE:
        return native_loader.native_core().decimate(
            verts, tris, pinned_xy, seam_bounds,
            params.DECIMATE_ROUNDS if rounds is None else rounds,
            allow_split, ground_ok, surface)
    tris = [tuple(int(i) for i in t) for t in tris]
    verts = [list(map(float, v)) for v in verts]
    min_edge = params.DECIMATE_MIN_EDGE
    if not tris or min_edge <= 0.0:
        return verts, tris

    pin = _pin_verts(verts, pinned_xy)

    def on_seam(vi):
        """True if this vertex sits on the exterior cell seam rectangle."""
        if seam_bounds is None:
            return False
        x, y = verts[vi][0], verts[vi][1]
        minx, miny, maxx, maxy = seam_bounds
        return (abs(x - minx) <= 0.5 or abs(x - maxx) <= 0.5
                or abs(y - miny) <= 0.5 or abs(y - maxy) <= 0.5)

    budget = params.DECIMATE_MAX_AREA_LOSS * _mesh_area(verts, tris)
    area_lost = 0.0
    for _ in range(rounds if rounds is not None else params.DECIMATE_ROUNDS):
        got = _decimate_round(verts, tris, pin, on_seam, min_edge,
                              budget, area_lost, ground_ok)
        if got is None:
            break
        tris, area_lost, progressed = got
        split = (_split_needles(verts, tris, pin, on_seam, surface)
                 if allow_split else None)
        if split is not None:
            tris = split
        if not progressed and split is None:
            break
    return verts, tris

def _edge_owners(tris):
    """edge -> the triangle indices using it."""
    edge_tris = {}
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            edge_tris.setdefault((a, b) if a < b else (b, a), []).append(ti)
    return edge_tris


def _apex_split_point(verts, a, b, apex, d, surface=None):
    """Where to cut edge a-b: the apex's projection, clamped off both ends.

    The Z is the chord's, then snapped onto walkable collision within a step:
    an edge spanning two levels has no ground under its chord.
    See: docs/commentary/tes5_import_navmesh.md#split-at-the-apex-projection
    """
    dx_, dy_ = verts[b][0] - verts[a][0], verts[b][1] - verts[a][1]
    tproj = (((apex[0] - verts[a][0]) * dx_
              + (apex[1] - verts[a][1]) * dy_) / (d * d))
    tlo = params.DECIMATE_MIN_EDGE / d
    tproj = max(tlo, min(1.0 - tlo, tproj))
    px = verts[a][0] + dx_ * tproj
    py = verts[a][1] + dy_ * tproj
    pz = verts[a][2] + (verts[b][2] - verts[a][2]) * tproj
    if surface is not None:
        near = verts[a][2] if tproj <= 0.5 else verts[b][2]
        got = surface(px, py, near)
        if got is not None and abs(got - pz) <= params.MAX_CLIMB:
            pz = got
    return (px, py, pz)


def _split_halves(verts, tris, owners, a, b, mid):
    """((owner, edge slot, opposite corner) list, worst badness after), or None.

    None means an owner does not actually carry the edge, so the split is off.
    """
    after = 0.0
    halves = []
    for o in owners:
        to = tris[o]
        for kk in range(3):
            if {to[kk], to[(kk + 1) % 3]} != {a, b}:
                continue
            cvi = to[(kk + 2) % 3]
            after = max(after,
                        _badness([verts[to[kk]], list(mid), verts[cvi]],
                                 (0, 1, 2)),
                        _badness([list(mid), verts[to[(kk + 1) % 3]],
                                  verts[cvi]], (0, 1, 2)))
            halves.append((o, kk, cvi))
            break
        else:
            return None
    return halves, after


def _splittable_edge(verts, tris, t, pin, on_seam, edge_tris, consumed):
    """(a, b, owners, apex, length) for this triangle's cut, or None."""
    min_len = 2.0 * params.DECIMATE_MIN_EDGE
    if _badness(verts, t) <= 1.0:
        return None
    d, k = max((math.dist(verts[t[i]][:2], verts[t[(i + 1) % 3]][:2]), i)
               for i in range(3))
    if d < min_len:
        return None
    a, b = t[k], t[(k + 1) % 3]
    if on_seam(a) and on_seam(b):
        return None
    owners = edge_tris.get((a, b) if a < b else (b, a), ())
    if len(owners) > 2 or any(o in consumed for o in owners):
        return None
    if any(all(v in pin for v in tris[o]) for o in owners):
        return None
    return a, b, owners, verts[t[(k + 2) % 3]], d


def _split_needles(verts, tris, pin, on_seam, surface=None):
    """Bisect the LONGEST edge of triangles violating MAX_EDGE_RATIO.

    Only edges >= 2 * DECIMATE_MIN_EDGE split, so the pass terminates.  An
    interior edge splits BOTH owners, so no hanging node appears; pinned
    door wedges and exterior-seam edges are never touched.  A split is
    abandoned unless the worst badness over the owners strictly DROPS.
    Returns the new triangle list, or None if nothing split.

    See: docs/commentary/tes5_import_navmesh.md#needle-split-must-improve-the-worst-shape
    """
    edge_tris = _edge_owners(tris)
    consumed, new_tris, dead = set(), [], set()
    for ti, t in enumerate(tris):
        if ti in consumed:
            continue
        got = _splittable_edge(verts, tris, t, pin, on_seam, edge_tris,
                               consumed)
        if got is None:
            continue
        a, b, owners, apex, d = got
        mid = _apex_split_point(verts, a, b, apex, d, surface)
        made = _split_halves(verts, tris, owners, a, b, mid)
        if made is None:
            continue
        halves, after = made
        if after >= max(_badness(verts, tris[o]) for o in owners) - 1e-9:
            continue
        m = len(verts)
        verts.append([mid[0], mid[1], mid[2]])
        for (o, kk, cvi) in halves:
            to = tris[o]
            new_tris.append((to[kk], m, cvi))
            new_tris.append((m, to[(kk + 1) % 3], cvi))
            dead.add(o)
            consumed.add(o)
    if not new_tris:
        return None
    out = [t for ti, t in enumerate(tris) if ti not in dead]
    out.extend(new_tris)
    return out


def _signed_area2(verts, a, b, c):
    """Twice the signed plan area of the triangle on these three vertices."""
    p, q, r = verts[a], verts[b], verts[c]
    return ((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]))


def _flip_opposites(t1, t2, a, b):
    """The two corners opposite shared edge (a, b), or None if degenerate."""
    c = next((v for v in t1 if v != a and v != b), None)
    d = next((v for v in t2 if v != a and v != b), None)
    if c is None or d is None or c == d:
        return None
    return c, d


def _flip_improves(verts, t1, t2, a, b, c, d, pin):
    """True if swapping diagonal (a,b) for (c,d) is legal and strictly better.

    See: docs/commentary/tes5_import_navmesh.md#flips-remesh-a-convex-quad
    """
    if all(v in pin for v in t1) or all(v in pin for v in t2):
        return False
    worst_old = max(_badness(verts, t1), _badness(verts, t2))
    worst_new = max(_badness(verts, (c, d, a)), _badness(verts, (c, d, b)))
    if worst_new >= worst_old - 1e-9:
        return False
    dz_old = abs(verts[a][2] - verts[b][2])
    return abs(verts[c][2] - verts[d][2]) <= dz_old + params.MAX_CLIMB


def _flip_round(verts, tris, pin):
    """One sweep of legal improving flips; True if any landed."""
    edge_tris = _edge_owners(tris)
    done, new_edges = set(), set()
    changed = False
    for key in sorted(edge_tris):
        owners = edge_tris[key]
        if len(owners) != 2:
            continue
        ti, tj = owners
        if ti in done or tj in done:
            continue
        t1, t2 = tris[ti], tris[tj]
        a, b = key
        got = _flip_opposites(t1, t2, a, b)
        if got is None:
            continue
        c, d = got
        ckey = (c, d) if c < d else (d, c)
        if ckey in edge_tris or ckey in new_edges:
            continue
        if not _flip_improves(verts, t1, t2, a, b, c, d, pin):
            continue
        s_a = _signed_area2(verts, c, d, a)
        s_b = _signed_area2(verts, c, d, b)
        if s_a * s_b >= 0 or abs(s_a) <= 1e-6 or abs(s_b) <= 1e-6:
            continue
        tris[ti] = (c, d, b) if s_b > 0 else (d, c, b)
        tris[tj] = (c, d, a) if s_a > 0 else (d, c, a)
        new_edges.add(ckey)
        done.add(ti)
        done.add(tj)
        changed = True
    return changed


def _flip_pass(verts, tris, pin, rounds=3):
    """Lawson-style diagonal flips that strictly improve edge ratio.

    Returns the new triangle list, or None if nothing flipped.
    See: docs/commentary/tes5_import_navmesh.md#flips-remesh-a-convex-quad
    """
    any_flip = False
    for _ in range(rounds):
        if not _flip_round(verts, tris, pin):
            break
        any_flip = True
    return tris if any_flip else None
