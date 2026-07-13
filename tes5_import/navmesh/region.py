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


def keep_pathgrid_heights(hf, nodes, edges):
    """Keep only walkable spans the PATHGRID vouches for AT THE RIGHT HEIGHT.

    ``keep_regions`` works at the region level, but a staircase legitimately
    climb-connects a room's floor to the ceiling of the room beneath it (the
    stair treads wrap over that lower room), so the flood-fill merges floor +
    stairs + ceiling into ONE region.  Keeping the whole region then paints
    navmesh on the ceiling where there is no room ("triangles on the wrong
    floor").

    The pathgrid is authored ground truth for where NPCs stand, so it is the
    only thing that can tell the floor from the ceiling here: keep a span iff a
    densified pathgrid sample lies within XY reach and within the floor Z
    tolerance of that span's top.  This also RESCUES floors the region seeding
    dropped (a second storey whose region no node happened to seed) — any span a
    pathgrid sample sits on is kept regardless of its region — which is why
    two-storey houses now get both floors.

    Runs after ``keep_regions`` (so tabletops/roofs are already gone) and only
    ever removes spans, so it can never add a wall back.
    """
    import numpy as np

    samples = _pathgrid_samples(nodes, edges)
    if not samples:
        return 0
    sarr = np.asarray(samples, dtype=np.float64)

    # Generous XY reach so the floor BETWEEN sparse nodes survives, but tight
    # enough in Z that the ceiling (a full storey up) is rejected.
    xy_r = params.SEED_SNAP * 2.5
    xy_r2 = xy_r * xy_r
    ztol = params.SEED_Z_TOLERANCE
    reach_c = int(round(xy_r / hf.cs)) + 1

    # Bucket samples into grid columns once, then for each walkable span test
    # only samples in the surrounding cell window (keeps this O(spans * local)).
    dropped = 0
    for cy in range(hf.h):
        wy = hf.min_y + cy * hf.cs
        for cx in range(hf.w):
            col = hf.spans[cy * hf.w + cx]
            has_walk = False
            for s in col:
                if s[2]:
                    has_walk = True
                    break
            if not has_walk:
                continue
            wx = hf.min_x + cx * hf.cs
            d2 = (sarr[:, 0] - wx) ** 2 + (sarr[:, 1] - wy) ** 2
            near = d2 <= xy_r2
            if not near.any():
                # No pathgrid anywhere near this column: it is not vouched.
                for s in col:
                    if s[2] and not s[3]:
                        s[2] = False
                        dropped += 1
                continue
            near_z = sarr[near, 2]
            for s in col:
                if not s[2] or s[3]:
                    continue
                if np.min(np.abs(near_z - s[1])) > ztol:
                    s[2] = False
                    dropped += 1
    return dropped
