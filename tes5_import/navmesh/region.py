"""Walkable regions: flood-fill, pathgrid seeding, and floor synthesis.

The voxelizer finds every surface an NPC could physically stand on — which
includes plenty of surfaces the designers never intended them to use: tabletops,
the tops of crates, window ledges, roof beams, the parapet of a wall.

The PATHGRID is Bethesda's own annotation of where NPCs actually walk.  So it is
used here not as geometry (that was the old converter's mistake) but as an
ORACLE over the geometry:

  1. flood-fill the walkable spans into connected regions (3D-aware: two floors
     stacked in XY are different regions because their spans differ in Z);
  2. KEEP only regions a pathgrid node vouches for.  Tabletops, roofs and ledges
     hold no node and are silently dropped;
  3. where a node's column has several spans (multi-floor), the node's Z picks
     which one it means;
  4. where a node has NO walkable span at all — a real case: some rooms' floor
     shells are placed in a *neighbouring* cell, e.g. the Anvil Fighters Guild
     bedroom wing, so the floor is genuinely absent from this cell's geometry —
     synthesize a floor patch at the node's Z.  The pathgrid asserts an NPC
     walks there, so we trust it over the (incomplete) geometry.
"""

from collections import deque

from . import params


def _span_key(cx, cy, si):
    return (cx << 20) | (cy << 4) | si


def build_regions(hf):
    """Flood-fill walkable spans into connected regions.

    Returns (region_id, regions) where region_id maps (cx,cy,span_index) ->
    region index, and regions is a list of lists of those same keys.

    Two spans in neighbouring columns connect only if their tops are within
    MAX_CLIMB — this is what keeps floor 1 and floor 2 apart, and what lets a
    staircase connect step to step.

    A PROTECTED span links on the PATHGRID height it was stamped with, not on its
    voxel top.  Regions are what the contourer meshes, and it meshes each one as
    an INDEPENDENT polygon — two regions that merely abut share no vertices and
    touch at a corner at best, which is exactly how a staircase ended up joined to
    its floor by a single triangle corner.  A staircase must therefore land in the
    SAME region as the floor it rises from, and comparing stamped pathgrid heights
    achieves that: the pathgrid line up a staircase is continuous, so consecutive
    stamped samples differ by only a few units and the stair links tread to tread
    and on into the floor.

    Two STOREYS of a house are also both protected and stacked in the same
    columns, but their stamped heights differ by a whole storey, so they still do
    not link.  (A cruder "relax the gate whenever both spans are protected" rule
    got this badly wrong: it fused both floors into one region, and the contourer
    then triangulated giant vertical zig-zags between them.)
    """
    climb = params.MAX_CLIMB
    w, h = hf.w, hf.h
    spans = hf.spans

    # Index walkable spans per column ONCE (this is the hot loop of the whole
    # build on big exterior cells, so keep it flat and allocation-free).  A span's
    # LINK height is the pathgrid Z stamped on it, or its top when it has none.
    walk = {}
    for ci in range(w * h):
        col = spans[ci]
        ws = None
        for i, s in enumerate(col):
            if s[2]:
                if ws is None:
                    ws = []
                link_z = s[4] if (s[3] and s[4] is not None) else s[1]
                ws.append((i, link_z))
        if ws is not None:
            walk[ci] = ws

    region_of = {}
    regions = []

    for ci, ws in walk.items():
        cx = ci % w
        cy = ci // w
        for (si, slz) in ws:
            key = (cx, cy, si)
            if key in region_of:
                continue
            rid = len(regions)
            members = []
            q = deque([(cx, cy, si, slz)])
            region_of[key] = rid
            while q:
                x, y, i, lz = q.popleft()
                members.append((x, y, i))
                for (nx, ny) in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    nws = walk.get(ny * w + nx)
                    if not nws:
                        continue
                    for (ni, nlz) in nws:
                        nk = (nx, ny, ni)
                        if nk in region_of:
                            continue
                        if -climb <= nlz - lz <= climb:
                            region_of[nk] = rid
                            q.append((nx, ny, ni, nlz))
            regions.append(members)

    return region_of, regions


def seed_regions(hf, region_of, regions, nodes):
    """Return the set of region ids vouched for by at least one pathgrid node.

    A node selects the span in (or near) its column whose top is closest to the
    node's Z — that is how a multi-floor column is disambiguated: the designer
    put the node at the height of the floor they meant.
    """
    snap = int(round(params.SEED_SNAP / hf.cs))
    ztol = params.SEED_Z_TOLERANCE
    seeded = set()

    for (nx, ny, nz) in nodes:
        cx0 = int((nx - hf.min_x) / hf.cs)
        cy0 = int((ny - hf.min_y) / hf.cs)

        best = None          # (|dz|, rid)
        for dy in range(-snap, snap + 1):
            for dx in range(-snap, snap + 1):
                cx, cy = cx0 + dx, cy0 + dy
                if cx < 0 or cy < 0 or cx >= hf.w or cy >= hf.h:
                    continue
                col = hf.spans[cy * hf.w + cx]
                for si, s in enumerate(col):
                    if not s[2]:
                        continue
                    rid = region_of.get((cx, cy, si))
                    if rid is None:
                        continue
                    dz = abs(s[1] - nz)
                    if dz > ztol:
                        continue
                    if best is None or dz < best[0]:
                        best = (dz, rid)
        if best is not None:
            seeded.add(best[1])

    return seeded


def keep_regions(hf, region_of, seeded):
    """Clear the walkable flag on every span outside a seeded region.

    This is what discards tabletops, crate tops, roofs and ledges: they are
    physically standable but hold no pathgrid node, so no designer ever intended
    an NPC to path across them.

    Protected spans (stamped from the pathgrid) are never dropped — they ARE the
    designer's assertion, so they cannot be culled for failing to look like one.
    """
    dropped = 0
    for cy in range(hf.h):
        for cx in range(hf.w):
            col = hf.spans[cy * hf.w + cx]
            for si, s in enumerate(col):
                if not s[2] or s[3]:
                    continue
                if region_of.get((cx, cy, si)) not in seeded:
                    s[2] = False
                    dropped += 1
    return dropped


def _pathgrid_samples(nodes, edges):
    """Densify the pathgrid into (x, y, z) samples along every edge.

    A bare node set is too sparse to gate spans by height (nodes sit ~128-256u
    apart while spans are every cell), and a straight stair edge climbs steeply
    between its two nodes, so we interpolate points along each edge at ~cell
    spacing.  The result is a dense height oracle that tracks the floor AND the
    slope of every staircase.
    """
    samples = [(float(n[0]), float(n[1]), float(n[2])) for n in nodes]
    for (i, j) in edges or ():
        if i >= len(nodes) or j >= len(nodes):
            continue
        a, b = nodes[i], nodes[j]
        seg = max(abs(a[0] - b[0]), abs(a[1] - b[1]))
        steps = max(1, int(seg / params.CS))
        for k in range(1, steps):
            t = k / steps
            samples.append((a[0] + (b[0] - a[0]) * t,
                            a[1] + (b[1] - a[1]) * t,
                            a[2] + (b[2] - a[2]) * t))
    return samples


def keep_pathgrid_heights(hf, nodes, edges, barriers=None, reach=None):
    """Keep only walkable spans within WALKING reach of the pathgrid.

    ``keep_regions`` works at the region level, but a staircase legitimately
    climb-connects a room's floor to surfaces at storey height (the treads wrap
    over the room below), so the flood-fill merges them into ONE region and
    region-level vouching cannot tell the floor from the ceiling.

    The gate here is GEODESIC: multi-source Dijkstra over the span graph
    (neighbouring columns, tops within MAX_CLIMB — the same adjacency the mesher
    uses), seeded at every densified pathgrid sample on the spans within
    SEED_Z_TOLERANCE of the sample's own Z.  A span survives iff the flood
    reaches it within PGRD_XY_REACH of walked distance.

    Why geodesic and not straight-line XY (the previous version):

      * a straight-line reach big enough to fill a whole room from the sparse
        line of nodes down its middle also reaches THROUGH the walls, and
        painted navmesh on the street outside a house's interior shell;
      * walking distance wraps around furniture (floor behind a table is kept)
        but does not pass through walls (no walkable adjacency), and it can
        only reach surfaces an NPC could genuinely step to from the pathgrid —
        a ceiling the stair merely passes near in Z is never step-reachable,
        so the wrong-floor defect stays unrepresentable.

    Runs after ``keep_regions`` (so tabletops/roofs are already gone) and only
    ever removes spans, so it can never add a wall back.
    """
    import heapq

    samples = _pathgrid_samples(nodes, edges)
    if not samples:
        return 0

    w, h, cs = hf.w, hf.h, hf.cs
    climb = params.MAX_CLIMB
    ztol = params.SEED_Z_TOLERANCE

    # Walkable span heights per column for adjacency — the EFFECTIVE height
    # (pathgrid height for protected spans), so the flood walks the stamped
    # ribbon at the height the mesher will actually use (voxel.span_z).
    from .voxel import span_z
    walk = {}
    for ci in range(w * h):
        ws = [(si, span_z(s)) for si, s in enumerate(hf.spans[ci]) if s[2]]
        if ws:
            walk[ci] = ws

    # Costs in half-cells so the 3-4 chamfer stays integer: straight=2, diag=3.
    limit = int((reach or params.PGRD_XY_REACH) / cs * 2.0)
    dist = {}
    heap = []
    for (sx, sy, sz) in samples:
        cx = int((sx - hf.min_x) / cs)
        cy = int((sy - hf.min_y) / cs)
        if cx < 0 or cy < 0 or cx >= w or cy >= h:
            continue
        for (si, top) in walk.get(cy * w + cx, ()):
            if abs(top - sz) <= ztol:
                key = (cx, cy, si)
                if dist.get(key, 1 << 30) > 0:
                    dist[key] = 0
                    heapq.heappush(heap, (0, cx, cy, si, top))

    # Barrier columns (teleport-door thresholds) may be REACHED — the mesh must
    # still cover the doorstep so the Door Triangle exists — but never expanded
    # FROM, so the flood cannot pour through the doorway into the fake exterior.
    barriers = barriers or ()

    steps = ((-1, 0, 2), (1, 0, 2), (0, -1, 2), (0, 1, 2),
             (-1, -1, 3), (1, -1, 3), (-1, 1, 3), (1, 1, 3))
    while heap:
        d, cx, cy, si, top = heapq.heappop(heap)
        if d > dist.get((cx, cy, si), 1 << 30):
            continue
        if cy * w + cx in barriers:
            continue
        for (dx, dy, c) in steps:
            nd = d + c
            if nd > limit:
                continue
            nx, ny = cx + dx, cy + dy
            if nx < 0 or ny < 0 or nx >= w or ny >= h:
                continue
            for (ni, ntop) in walk.get(ny * w + nx, ()):
                if abs(ntop - top) > climb:
                    continue
                nk = (nx, ny, ni)
                if nd < dist.get(nk, 1 << 30):
                    dist[nk] = nd
                    heapq.heappush(heap, (nd, nx, ny, ni, ntop))

    dropped = 0
    for ci, ws in walk.items():
        cx = ci % w
        cy = ci // w
        col = hf.spans[ci]
        for (si, _top) in ws:
            s = col[si]
            if s[3]:
                continue                       # protected: pathgrid asserts it
            if (cx, cy, si) not in dist:
                s[2] = False
                dropped += 1
    return dropped
