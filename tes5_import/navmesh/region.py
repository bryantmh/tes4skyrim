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
    """
    climb = params.MAX_CLIMB
    w, h = hf.w, hf.h
    spans = hf.spans

    # Index walkable spans per column ONCE (this is the hot loop of the whole
    # build on big exterior cells, so keep it flat and allocation-free).
    walk = {}
    for ci in range(w * h):
        col = spans[ci]
        ws = None
        for i, s in enumerate(col):
            if s[2]:
                if ws is None:
                    ws = []
                ws.append((i, s[1]))          # only the TOP matters for linking
        if ws is not None:
            walk[ci] = ws

    region_of = {}
    regions = []

    for ci, ws in walk.items():
        cx = ci % w
        cy = ci // w
        for (si, stop) in ws:
            key = (cx, cy, si)
            if key in region_of:
                continue
            rid = len(regions)
            members = []
            q = deque([(cx, cy, si, stop)])
            region_of[key] = rid
            while q:
                x, y, i, top = q.popleft()
                members.append((x, y, i))
                for (nx, ny) in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    nws = walk.get(ny * w + nx)
                    if not nws:
                        continue
                    for (ni, ntop) in nws:
                        nk = (nx, ny, ni)
                        if nk in region_of:
                            continue
                        # Neighbouring spans connect only within a step height:
                        # this is what keeps floor 1 and floor 2 apart while
                        # letting a staircase link step to step.
                        if -climb <= ntop - top <= climb:
                            region_of[nk] = rid
                            q.append((nx, ny, ni, ntop))
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


def unseeded_nodes(hf, region_of, nodes, seeded):
    """Nodes that no kept region covers — the floor under them is missing."""
    snap = int(round(params.SEED_SNAP / hf.cs))
    ztol = params.SEED_Z_TOLERANCE
    out = []
    for node in nodes:
        nx, ny, nz = node
        cx0 = int((nx - hf.min_x) / hf.cs)
        cy0 = int((ny - hf.min_y) / hf.cs)
        found = False
        for dy in range(-snap, snap + 1):
            if found:
                break
            for dx in range(-snap, snap + 1):
                cx, cy = cx0 + dx, cy0 + dy
                if cx < 0 or cy < 0 or cx >= hf.w or cy >= hf.h:
                    continue
                for si, s in enumerate(hf.spans[cy * hf.w + cx]):
                    if (s[2] and abs(s[1] - nz) <= ztol
                            and region_of.get((cx, cy, si)) in seeded):
                        found = True
                        break
                if found:
                    break
        if not found:
            out.append(node)
    return out


def synthesize_floor(hf, nodes, edges, missing):
    """Stamp walkable spans under pathgrid nodes/edges that have no floor.

    Some rooms' floor shells are placed in a neighbouring cell (verified: the
    Anvil Fighters Guild bedroom wing is fully furnished — beds, chests, rugs —
    but its floor/wall shell lives in another cell's REFR list, so this cell has
    NO collision under 14 of its pathgrid nodes).  The pathgrid asserts an NPC
    walks there, and the pathgrid is authored ground truth, so we trust it and
    lay down a floor at the node's height.

    Only the affected nodes get a patch, and only where the column has no
    walkable span already — this never overrides real geometry.  The patch is
    also swept along any PGRD edge between two patched nodes, so a synthesized
    room comes out connected rather than as a scatter of discs.
    """
    if not missing:
        return 0

    idx = {}
    for i, n in enumerate(nodes):
        idx[(round(n[0], 1), round(n[1], 1), round(n[2], 1))] = i
    missing_ids = set()
    for n in missing:
        i = idx.get((round(n[0], 1), round(n[1], 1), round(n[2], 1)))
        if i is not None:
            missing_ids.add(i)

    radius = max(params.SEED_SNAP, params.AGENT_RADIUS * 2.0)
    stamped = 0

    def stamp(wx, wy, wz):
        nonlocal stamped
        r = int(round(radius / hf.cs))
        cx0 = int((wx - hf.min_x) / hf.cs)
        cy0 = int((wy - hf.min_y) / hf.cs)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy > r * r:
                    continue
                cx, cy = cx0 + dx, cy0 + dy
                if cx < 0 or cy < 0 or cx >= hf.w or cy >= hf.h:
                    continue
                col = hf.spans[cy * hf.w + cx]
                # Never overwrite real geometry at this height: if anything
                # (walkable or blocking) already occupies the node's Z band,
                # leave it alone — that is a wall or an existing floor.
                blocked = any(s[0] - params.MAX_CLIMB <= wz <= s[1] + params.MAX_CLIMB
                              for s in col)
                if blocked:
                    continue
                hf.add_span(cx, cy, wz - hf.ch, wz, True)
                stamped += 1

    for n in missing:
        stamp(n[0], n[1], n[2])

    # Sweep along edges joining two patched nodes so the room is contiguous.
    for (i, j) in edges or ():
        if i not in missing_ids and j not in missing_ids:
            continue
        a, b = nodes[i], nodes[j]
        dist = max(abs(a[0] - b[0]), abs(a[1] - b[1]))
        steps = int(dist / (hf.cs * 0.5)) + 1
        for k in range(1, steps):
            t = k / steps
            stamp(a[0] + (b[0] - a[0]) * t,
                  a[1] + (b[1] - a[1]) * t,
                  a[2] + (b[2] - a[2]) * t)

    return stamped


def keep_regions(hf, region_of, seeded):
    """Clear the walkable flag on every span outside a seeded region.

    This is what discards tabletops, crate tops, roofs and ledges: they are
    physically standable but hold no pathgrid node, so no designer ever intended
    an NPC to path across them.
    """
    dropped = 0
    for cy in range(hf.h):
        for cx in range(hf.w):
            col = hf.spans[cy * hf.w + cx]
            for si, s in enumerate(col):
                if not s[2]:
                    continue
                if region_of.get((cx, cy, si)) not in seeded:
                    s[2] = False
                    dropped += 1
    return dropped
