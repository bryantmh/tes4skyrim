"""Grouping ribbons into STOREYS and separating sheets that stack.

A cell's ribbons are partitioned so each group is one walkable sheet: joined
where the pathgrid says an actor walks between them, split where they merely
overlap in plan at different heights.

See: docs/commentary/tes5_import_navmesh.md#plan-overlap-split-uses-bonded-pairs
"""

from shapely import intersects as _sh_intersects, points as _sh_points

from .union_geom import (
    SAME_SURFACE_Z, STOREY_GAP_Z, _height_on, _ribbon_polygon,
)


def _overlap_height_gap(sa, sb, inter):
    """Smallest height disagreement between two ribbons over shared ground.

    Sampled on the shared footprint, so a long ribbon crossing another at a
    single point is judged there rather than on its mean height.
    """
    pts = []
    try:
        c = inter.centroid
        pts.append((c.x, c.y))
    except Exception:
        pass
    try:
        rp = inter.representative_point()
        pts.append((rp.x, rp.y))
    except Exception:
        pass
    try:
        minx, miny, maxx, maxy = inter.bounds
        grid = [(minx + (maxx - minx) * fx, miny + (maxy - miny) * fy)
                for fx in (0.25, 0.5, 0.75) for fy in (0.25, 0.5, 0.75)]
        hits = _sh_intersects(inter, _sh_points(grid))
        pts.extend(g for g, hit in zip(grid, hits.tolist()) if hit)
    except Exception:
        pass
    if not pts:
        return float('inf')
    return min(abs(_height_on(sa, px, py) - _height_on(sb, px, py))
               for (px, py) in pts)

def _subs_same_floor(items, sub_a, sub_b):
    """True when two sub-sheets share ground at (nearly) the same height.

    Two sub-sheets that overlap in plan and agree in height there are ONE floor
    that the conflict split happened to separate; keeping them apart makes each
    mesh that ground on its own and the results stack.
    """
    for i in sub_a:
        si, pi = items[i]
        for j in sub_b:
            sj, pj = items[j]
            if not pi.intersects(pj):
                continue
            try:
                inter = pi.intersection(pj)
            except Exception:
                continue
            if inter.is_empty or inter.area < 1.0:
                continue
            cx, cy = inter.centroid.x, inter.centroid.y
            if abs(_height_on(si, cx, cy) -
                   _height_on(sj, cx, cy)) <= SAME_SURFACE_Z:
                return True
    return False

def _same_surface_region(group_a, group_b, shared):
    """The part of `shared` where both sheets describe the SAME surface height.

    Returned as a polygon to subtract from the later sheet, so that ground is
    meshed once.  Where the two sheets disagree in height they are genuinely
    stacked storeys and both keep their mesh, so that ground is NOT returned.

    Worked per ribbon-pair rather than over the whole region, because a sheet
    containing a staircase spans many heights and a single test would either
    surrender a whole floor or nothing.
    """
    from shapely import STRtree
    from shapely.ops import unary_union as _uu
    dup = []
    b_strips = list(group_b)
    b_polys = [_ribbon_polygon(sb) for sb in b_strips]
    if not b_strips:
        return None
    b_tree = STRtree(b_polys)
    for sa in group_a:
        pa = _ribbon_polygon(sa)
        if pa.is_empty or not pa.intersects(shared):
            continue
        for bi in sorted(b_tree.query(pa, predicate='intersects').tolist()):
            sb = b_strips[bi]
            pb = b_polys[bi]
            if pb.is_empty:
                continue
            try:
                piece = pa.intersection(pb).intersection(shared)
            except Exception:
                continue
            if piece.is_empty or piece.area < 1.0:
                continue
            if _overlap_height_gap(sa, sb, piece) <= SAME_SURFACE_Z:
                dup.append(piece)
    if not dup:
        return None
    try:
        return _uu(dup)
    except Exception:
        return None

def _bonded_pairs(items):
    """Index pairs meeting at a pathgrid node where their heights agree.

    The pathgrid asserts an actor walks from one onto the other there, so they
    describe ONE junction and must end up in the same sheet.
    """
    node_h = {}
    for k, (sk, _p) in enumerate(items):
        (ni, nj) = sk.get('edge', (-1, -1))
        if ni < 0:
            continue
        node_h.setdefault(ni, []).append((k, sk['na'][2]))
        if nj != ni:
            node_h.setdefault(nj, []).append((k, sk['nb'][2]))
    bonded = set()
    for entries in node_h.values():
        for x in range(len(entries)):
            for y in range(x + 1, len(entries)):
                ka, za = entries[x]
                kb, zb = entries[y]
                if abs(za - zb) <= SAME_SURFACE_Z:
                    bonded.add((min(ka, kb), max(ka, kb)))
    return bonded

def _candidate_pairs(polys):
    """Overlapping index pairs, box-filtered in bulk by an R-tree.

    All-pairs testing cost 7.4M scalar `intersects` calls on Moranda.
    """
    if len(polys) < 2:
        return []
    from shapely import STRtree
    qa, qb = STRtree(polys).query(polys, predicate='intersects')
    return sorted((a, b) for a, b in zip(qa.tolist(), qb.tolist()) if a < b)

def _z_ranges(items):
    """(lo, hi) height span of each ribbon, following its profile when steep."""
    out = []
    for (s, _p) in items:
        prof = s.get('prof')
        if s.get('land') is not None:
            out.append((float('-inf'), float('inf')))
        elif prof and len(prof) >= 2:
            zs = [q[2] for q in prof]
            out.append((min(zs), max(zs)))
        else:
            za, zb = s['a'][2], s['b'][2]
            out.append((za, zb) if za <= zb else (zb, za))
    return out

def _partition_by_agreement(items, bonded, parent, find):
    """Union agreeing overlaps into `parent`; returns the conflicting pairs."""
    conflicts = set()
    zrange = _z_ranges(items)
    for (a, b) in _candidate_pairs([p for (_s, p) in items]):
        if (a, b) not in bonded:
            alo, ahi = zrange[a]
            blo, bhi = zrange[b]
            sep = blo - ahi if blo > ahi else alo - bhi
            if sep > STOREY_GAP_Z:
                conflicts.add((a, b))
                continue
        sa, pa = items[a]
        sb, pb = items[b]
        inter = pa.intersection(pb)
        if inter.is_empty or inter.area < 1.0:
            continue
        if _overlap_height_gap(sa, sb, inter) > STOREY_GAP_Z \
                and (a, b) not in bonded:
            conflicts.add((a, b))
        else:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    for (a, b) in bonded:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return conflicts

def _sub_sheets(items, members, cadj, badj):
    """Greedy conflict-free sub-sheets of one bucket.

    Each ribbon joins the sub-sheet it AGREES with best, not the first that
    merely does not conflict: first-fit scatters one floor across several
    sub-sheets which then stack.  A BONDED sub-sheet always outranks height.
    """
    subs, sub_sets, sub_h = [], [], []
    for i in sorted(members,
                    key=lambda k: -0.5 * (items[k][0]['na'][2] +
                                          items[k][0]['nb'][2])):
        zi = 0.5 * (items[i][0]['na'][2] + items[i][0]['nb'][2])
        ci = cadj.get(i) or ()
        bi = badj.get(i) or ()
        best = None
        for si, _sub in enumerate(subs):
            if ci and not sub_sets[si].isdisjoint(ci):
                continue
            d = abs(sub_h[si] - zi)
            rank = 0 if (bi and not sub_sets[si].isdisjoint(bi)) else 1
            if best is None or (rank, d) < (best[0], best[1]):
                best = (rank, d, si)
        if best is None:
            subs.append([i])
            sub_sets.append({i})
            sub_h.append(zi)
        else:
            si = best[2]
            subs[si].append(i)
            sub_sets[si].add(i)
            sub_h[si] = (sub_h[si] * (len(subs[si]) - 1) + zi) / len(subs[si])
    return subs

def _merge_same_floor(items, subs, cadj):
    """Merge back any two sub-sheets overlapping in plan at the SAME height.

    The sub-split only has to separate ribbons that genuinely conflict;
    anything else on one floor must stay together or the triangles stack.
    """
    merged = []
    for sub in subs:
        sub_conf = set()
        for i in sub:
            sub_conf |= cadj.get(i) or set()
        target = None
        for mi, msub in enumerate(merged):
            if sub_conf and not sub_conf.isdisjoint(msub):
                continue
            if _subs_same_floor(items, sub, msub):
                target = mi
                break
        if target is None:
            merged.append(list(sub))
        else:
            merged[target].extend(sub)
    return merged

def _conflict_adjacency(members, conflicts, bonded):
    """(cadj, badj) neighbour maps restricted to this bucket's members."""
    cadj, badj = {}, {}
    for (a, b) in conflicts:
        if a in members and b in members:
            cadj.setdefault(a, set()).add(b)
            cadj.setdefault(b, set()).add(a)
    for (a, b) in bonded:
        if a in members and b in members:
            badj.setdefault(a, set()).add(b)
            badj.setdefault(b, set()).add(a)
    return cadj, badj


def _split_one_group(group, out):
    """Split one connectivity group into sheets, appending to `out`."""
    items = []
    for s in group:
        poly = _ribbon_polygon(s)
        if poly.is_valid and not poly.is_empty:
            items.append((s, poly))
    if not items:
        return
    n = len(items)
    parent = list(range(n))

    def find(x):
        """Root of x, path-compressed."""
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    bonded = _bonded_pairs(items)
    conflicts = _partition_by_agreement(items, bonded, parent, find)
    buckets = {}
    for i in range(n):
        buckets.setdefault(find(i), []).append(i)
    for members in buckets.values():
        cadj, badj = _conflict_adjacency(members, conflicts, bonded)
        if not cadj:
            out.append([items[i][0] for i in members])
            continue
        subs = _sub_sheets(items, members, cadj, badj)
        for sub in _merge_same_floor(items, subs, cadj):
            out.append([items[i][0] for i in sub])


def _split_plan_overlaps(groups):
    """Split each connectivity group where it OVERLAPS ITSELF in plan.

    See: docs/commentary/tes5_import_navmesh.md#plan-overlap-split-uses-bonded-pairs
    """
    out = []
    for group in groups:
        _split_one_group(group, out)
    return out

def _node_partition(strips):
    """Union-find roots joining ribbons that share a node at the same height.

    Grouping cannot be a Z threshold: a STAIRCASE has no single height and is
    exactly the thing that legitimately spans two floors.  So ribbons are
    joined by CONNECTIVITY -- a stair joins the floor at its foot and the one
    at its head, and two floors merely overlapping in plan never merge.
    """
    parent = list(range(len(strips)))

    def find(x):
        """Root of x, path-compressed."""
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    at_node = {}
    for si, s in enumerate(strips):
        (i, j) = s.get('edge', (-1, -1))
        if i < 0:
            continue
        at_node.setdefault(i, []).append((si, s['na'][2]))
        if j != i:
            at_node.setdefault(j, []).append((si, s['nb'][2]))
    for entries in at_node.values():
        for a in range(len(entries)):
            for b in range(a + 1, len(entries)):
                if abs(entries[a][1] - entries[b][1]) > SAME_SURFACE_Z:
                    continue
                rx, ry = find(entries[a][0]), find(entries[b][0])
                if rx != ry:
                    parent[rx] = ry
    return find


def _best_group_for_door(out, dp, z):
    """Index of the group this door footprint joins, or None.

    Judged in plan AND height: a door has no pathgrid edge, so it joins the
    group whose ribbons its footprint actually touches at an agreeing height.
    """
    best = None
    for gi, g in enumerate(out):
        hz = None
        for x in g:
            if not _ribbon_polygon(x).intersects(dp):
                continue
            d = min(abs(x['na'][2] - z), abs(x['nb'][2] - z))
            if hz is None or d < hz:
                hz = d
        if hz is None or hz > STOREY_GAP_Z:
            continue
        if best is None or hz < best[0]:
            best = (hz, gi)
    return None if best is None else best[1]


def _storey_groups(strips):
    """Group the ribbons into STOREYS, so each is unioned on its own.

    Two ribbons join the same storey when they share a pathgrid NODE and
    their heights AT THAT NODE agree within SAME_SURFACE_Z.  A door
    footprint has no pathgrid edge, so it joins the overlapping group whose
    height agrees, or becomes its own.  Returns a list of strip lists.

    See: docs/commentary/tes5_import_navmesh.md#storey-grouping-by-connectivity
    """
    find = _node_partition(strips)
    groups = {}
    for si, s in enumerate(strips):
        if s.get('edge', (-1, -1))[0] >= 0:
            groups.setdefault(find(si), []).append(s)
    out = [g for g in groups.values() if g]
    for s in [x for x in strips if x.get('edge', (-1, -1))[0] < 0]:
        gi = _best_group_for_door(out, _ribbon_polygon(s), s['a'][2])
        if gi is None:
            out.append([s])
        else:
            out[gi].append(s)
    return out or [list(strips)]
