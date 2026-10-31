"""Navmesh for exterior cells with no pathgrid, grown in across their neighbours' seams.

Navmesh jobs come from pathgrids, so a land cell without one got no mesh and no actor could path
through it. Each wave takes the cells a built mesh reaches on a seam -- the border edges where an
edge link can form -- seeds them there, floods a synthetic pathgrid from the seeds over the cell's
walkable ground, and builds it like any authored pathgrid. Terrain under water floods the same
way and comes out water-flagged, as vanilla's bed-following water triangles do.
See: docs/commentary/tes5_import_navmesh.md#seeded-cells
"""

import math
import struct

import numpy as np

from ..base.text_reader import get_float, get_formid, get_hex_bytes, get_int
from . import native_loader, params
from .corridor_grow import walkable_sampler, wall_slab_sampler
from .edge_links import CELL_SIZE, border_edges, exterior_views, master_view
from .world import gather_cell_geometry, land_field, open_water_z, placements

#: Spacing of the flood lattice; the terrain grow widens each ribbon to the walls between nodes.
LATTICE_SPACING = 512.0

#: Step of the walk test along a lattice edge; MAX_CLIMB per step is the walkable slope.
WALK_STEP = 32.0

#: Border edges further apart than this along a seam start a new seed run.
RUN_GAP = 96.0

#: A long run gets a seed every this many units, so its whole width is reached.
RUN_SEED_SPACING = 512.0

#: How far inside its cell a seed sits, so it samples that cell's ground.
SEED_INSET = 4.0

#: Each side of a cell: (dx, dy, seam axis, whether the seam is the cell's upper edge).
_SIDES = ((1, 0, 0, True), (-1, 0, 0, False), (0, 1, 1, True), (0, -1, 1, False))

#: Most cells an all-water seeded cell lies from a cell with land above its water.
MAX_SEA_CELLS = 2

#: CELL DATA flag: the cell has water.
_HAS_WATER = 0x02

#: WRLD DATA flag Small world: its pathgrids already cover all of it that is played.
_SMALL_WORLD = 0x01

#: REGN record flag Border Region: the player never leaves its areas.
_BORDER_REGION = 0x40


# ----------------------------------------------------------------------------
# The authored playable area
# ----------------------------------------------------------------------------

def _border_polygons(regions) -> dict:
    """{worldspace fid: [[(x, y), ...], ...]} of every Border region's areas."""
    out = {}
    for rec in regions:
        if not get_int(rec, 'RecordFlags') & _BORDER_REGION:
            continue
        polys = out.setdefault(get_formid(rec, 'WNAM.Worldspace'), [])
        for i in range(get_int(rec, 'AreaCount')):
            raw = get_hex_bytes(rec, f'Area[{i}].PointsHex')
            polys.append(list(struct.iter_unpack('<ff', raw[:len(raw) // 8 * 8])))
    return out


def _inside(x, y, poly) -> bool:
    """Even-odd test: (x, y) lies inside the polygon."""
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _within(polys, gx, gy) -> bool:
    """True when a corner, edge midpoint or centre of cell (gx, gy) is in a polygon."""
    return any(_inside((gx + a) * CELL_SIZE, (gy + b) * CELL_SIZE, poly)
               for a in (0, 0.5, 1) for b in (0, 0.5, 1) for poly in polys)


def drop_unplayable(seedable: dict, worlds, regions) -> None:
    """Remove the squares outside the authored playable area from `seedable`.

    A Small world is skipped whole; where a worldspace authors Border regions, only cells
    touching their areas stay. `worlds` and `regions` are WRLD and REGN records, masters' included.
    See: docs/commentary/tes5_import_navmesh.md#seeded-cells
    """
    small = {get_formid(w, 'FormID') for w in worlds if get_int(w, 'DATA.Flags') & _SMALL_WORLD}
    borders = _border_polygons(regions)
    for square in list(seedable):
        polys = borders.get(square[0])
        if square[0] in small or (polys and not _within(polys, square[1], square[2])):
            del seedable[square]


# ----------------------------------------------------------------------------
# Seeds from built meshes
# ----------------------------------------------------------------------------

def _runs(edges):
    """Border edges (sorted along the seam) split into runs at gaps and height jumps."""
    runs, cur = [], []
    for e in edges:
        if cur and (e[2] - cur[-1][2] > RUN_GAP or abs(e[3] - cur[-1][3]) > params.MAX_CLIMB * 2):
            runs.append(cur)
            cur = []
        cur.append(e)
    if cur:
        runs.append(cur)
    return runs


def _run_seeds(run):
    """(along, z) seed points for one run: its middle, plus one per RUN_SEED_SPACING."""
    span = run[-1][2] - run[0][2]
    count = max(1, int(span // RUN_SEED_SPACING) + 1)
    out = []
    for k in range(count):
        target = run[0][2] + span * (k + 0.5) / count
        edge = min(run, key=lambda e: (abs(e[2] - target), e[2]))
        out.append((edge[2], edge[3]))
    return out


def _seam_points(view, side, gx, gy):
    """Seed points in the neighbour across one side of the view's cell."""
    _dx, _dy, axis, upper = side
    base = gx if axis == 0 else gy
    coord = (base + 1) * CELL_SIZE if upper else base * CELL_SIZE
    inset = SEED_INSET if upper else -SEED_INSET
    edges = sorted(border_edges(view, axis, coord), key=lambda e: (e[2], e[3]))
    out = []
    for run in _runs(edges):
        for along, z in _run_seeds(run):
            out.append((coord + inset, along, z) if axis == 0 else (along, coord + inset, z))
    return out


def seam_seeds(views: dict, targets) -> dict:
    """{(wrld, gx, gy): {neighbour square: seed points}} for every `targets` cell a view's seam reaches.

    `views` is {(wrld, gx, gy, fid): NavMeshView}.
    """
    out = {}
    for (wrld, gx, gy, fid) in sorted(views):
        for side in _SIDES:
            target = (wrld, gx + side[0], gy + side[1])
            points = _seam_points(views[(wrld, gx, gy, fid)], side, gx, gy) if target in targets else ()
            if points:
                out.setdefault(target, {}).setdefault((wrld, gx, gy), []).extend(points)
    return out


def seeded_record(cell_rec: dict) -> dict:
    """A seeded cell's synthetic pathgrid, keyed by the cell's FormID, without points."""
    fid = cell_rec.get('FormID')
    return {'Signature': 'PGRD', 'FormID': fid, 'ParentCELL': fid,
            'ParentWRLD': cell_rec.get('ParentWRLD')}


def master_views(master_index, master_navms, empty) -> dict:
    """{(wrld, gx, gy, fid): view} for the masters' meshes on a seam of an `empty` cell."""
    if master_index is None or not master_navms:
        return {}
    wanted = {(w, gx + s[0], gy + s[1]) for (w, gx, gy) in empty for s in _SIDES}
    out = {}
    for cell in sorted(wanted):
        fid = master_navms.get(cell)
        entry = master_view(master_index, fid) if fid else None
        if entry is not None and entry[0].exterior:
            out[cell + (fid,)] = entry[0]
    return out


def _is_dry(job) -> bool:
    """True when some of the cell's land stands above its water."""
    cell = job['cell_rec']
    land = land_field(job['land_rec'], 0.0, 0.0)
    if land is None or not get_int(cell, 'DATA.Flags') & _HAS_WATER:
        return True
    return float(land.grid.max()) > get_float(cell, 'XCLW.WaterHeight', 0.0)


def _sea_cells(square, job, sea, meshed) -> int:
    """0 for a cell with dry land, else one more than its nearest meshed neighbour's count."""
    if _is_dry(job):
        return 0
    wrld, gx, gy = square
    near = [sea.get((wrld, gx + s[0], gy + s[1]), 0) for s in _SIDES
            if (wrld, gx + s[0], gy + s[1]) in meshed]
    return 1 + min(near, default=0)


def _wave_jobs(offers, seedable, sides, sea, meshed) -> list:
    """This wave's (square, job)s: each cell a new neighbour offers seeds, if land or near it.

    `sides` holds {square: {neighbour: seed points}} each cell has built from; a job seeds from all of them.
    """
    jobs = []
    for square in sorted(offers):
        taken = sides.setdefault(square, {})
        new = {src: points for src, points in offers[square].items() if src not in taken}
        depth = _sea_cells(square, seedable[square], sea, meshed)
        if new and depth <= MAX_SEA_CELLS:
            taken.update(new)
            sea[square] = depth
            seeds = sorted({p for points in taken.values() for p in points})
            jobs.append((square, dict(seedable[square], seeds=seeds)))
    return jobs


def grow_into_empty(cache, seedable, sources, derive_fid, run) -> list:
    """Build seeded cells wave by wave into `cache`; return the pathgrids that built a mesh.

    `seedable` maps (wrld, gx, gy) to a job template. The first wave seeds from `sources`, each
    later one from the wave before; a cell rebuilds, from every seed it was offered, whenever a
    neighbour it has not built from offers seeds, so at most once per side. `run(jobs)` builds jobs.
    See: docs/commentary/tes5_import_navmesh.md#seeded-cells
    """
    built, sides, sea = {}, {}, {}
    meshed = {key[:3] for key in sources}
    while True:
        jobs = _wave_jobs(seam_seeds(sources, seedable), seedable, sides, sea, meshed)
        if not jobs:
            return list(built.values())
        for _square, job in jobs:
            job['navm_fid'] = derive_fid(job['key'])
        results = run([job for _square, job in jobs])
        cache.update(results)
        sources = exterior_views(results)[0]
        for square, job in jobs:
            if (results.get(job['key']) or (None,))[0]:
                built.setdefault(square, job['pgrd_rec'])
                meshed.add(square)
            else:
                built.pop(square, None)
                meshed.discard(square)
        print(f"    Seeded {len(jobs)} cells with no pathgrid; "
              f"{sum(1 for v in results.values() if v[0])} built a navmesh")


# ----------------------------------------------------------------------------
# The flood, in the worker
# ----------------------------------------------------------------------------

class Ground:
    """Walkable height and wall tests over one cell's collision and terrain."""

    def __init__(self, walkable, blocking, land):
        """Index the soups once; `land` is the cell's LandField or None."""
        self.height = walkable_sampler(walkable)
        self.wall = wall_slab_sampler(blocking)
        self.land = land

    def floor(self, x, y, near):
        """Walkable Z at (x, y) nearest `near`, or None."""
        return self.height(x, y, near)

    def blocked(self, x, y, z, ux, uy):
        """True when a wall stands in the actor's body at (x, y, z), facing (ux, uy)."""
        return self.wall(x, y, ux, uy, -uy, ux, z + params.RIBBON_GROW_SLAB_Z_BOTTOM,
                         z + params.AGENT_HEIGHT)

    def walk(self, a, b):
        """True when an actor can walk the straight line from point a to point b."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return True
        ux, uy = dx / dist, dy / dist
        z = a[2]
        steps = max(1, int(math.ceil(dist / WALK_STEP)))
        for k in range(1, steps + 1):
            x, y = a[0] + dx * k / steps, a[1] + dy * k / steps
            nz = self.floor(x, y, z)
            if nz is None or abs(nz - z) > params.MAX_CLIMB or self.blocked(x, y, nz, ux, uy):
                return False
            z = nz
        return abs(z - b[2]) <= params.MAX_CLIMB


def _lattice(ground, ox, oy):
    """{(i, j): (x, y, z)} lattice nodes standing on open walkable ground."""
    count = int(CELL_SIZE // LATTICE_SPACING)
    nodes = {}
    for j in range(count):
        for i in range(count):
            x = ox + (i + 0.5) * LATTICE_SPACING
            y = oy + (j + 0.5) * LATTICE_SPACING
            near = ground.land.z(x, y) if ground.land is not None else 0.0
            z = ground.floor(x, y, near)
            if z is not None and not ground.blocked(x, y, z, 1.0, 0.0):
                nodes[(i, j)] = (x, y, z)
    return nodes


def _lattice_edges(ground, nodes):
    """Walkable links between orthogonal lattice neighbours."""
    out = []
    for (i, j) in sorted(nodes):
        for nb in ((i + 1, j), (i, j + 1)):
            if nb in nodes and ground.walk(nodes[(i, j)], nodes[nb]):
                out.append(((i, j), nb))
    return out


def _seed_links(ground, seeds, nodes):
    """(seed points on the ground, [(seed index, lattice key)]) for seeds an actor can walk in from."""
    placed, links = [], []
    for sx, sy, sz in seeds:
        z = ground.floor(sx, sy, sz)
        if z is None:
            continue
        seed = (sx, sy, z)
        near = sorted(nodes, key=lambda k: (math.hypot(nodes[k][0] - sx, nodes[k][1] - sy), k))
        reach = [k for k in near[:4] if math.hypot(nodes[k][0] - sx, nodes[k][1] - sy)
                 <= LATTICE_SPACING * 1.5 and ground.walk(seed, nodes[k])]
        if reach:
            links += [(len(placed), k) for k in reach]
            placed.append(seed)
    return placed, links


def _flooded(seed_links, lattice_edges):
    """The lattice keys reachable from any seed."""
    adj = {}
    for a, b in lattice_edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    reached = {k for _s, k in seed_links}
    todo = sorted(reached)
    while todo:
        for nb in adj.get(todo.pop(), ()):
            if nb not in reached:
                reached.add(nb)
                todo.append(nb)
    return reached


def seed_graph(seeds, walkable, blocking, land, ox, oy):
    """(points, edges) of the synthetic pathgrid flooded from `seeds`; ([], []) when none walks in."""
    ground = Ground(walkable, blocking, land)
    nodes = _lattice(ground, ox, oy)
    placed, links = _seed_links(ground, seeds, nodes)
    lattice_edges = _lattice_edges(ground, nodes)
    reached = _flooded(links, lattice_edges)
    keys = sorted(reached)
    index = {k: len(placed) + n for n, k in enumerate(keys)}
    points = placed + [nodes[k] for k in keys]
    edges = [(s, index[k]) for s, k in links]
    edges += [(index[a], index[b]) for a, b in lattice_edges if a in reached and b in reached]
    return (points, edges) if edges else ([], [])


def pathgrid_order(points, edges):
    """(points, edges) as a pathgrid record reads back: float points, edges by node then neighbour."""
    nbrs = [[] for _ in points]
    for a, b in edges:
        nbrs[a].append(b)
        nbrs[b].append(a)
    ordered, seen = [], set()
    for i in range(len(points)):
        for t in sorted(nbrs[i]):
            key = (min(i, t), max(i, t))
            if t != i and key not in seen:
                seen.add(key)
                ordered.append(key)
    return [tuple(float(c) for c in p) for p in points], ordered


def seeded_graph(job, base_model_by_fid, door_fids, get_collision):
    """The job's flooded (points, edges) in pathgrid order, or None when nothing walks in.

    Door panels are left out of the blocking soup, as the corridor build leaves them out.
    """
    cell = job['cell_rec']
    ox = float(cell.get('XCLC.X') or 0) * CELL_SIZE
    oy = float(cell.get('XCLC.Y') or 0) * CELL_SIZE
    bases = set(door_fids.keys()) if isinstance(door_fids, dict) else set(door_fids or ())
    land = land_field(job['land_rec'], ox, oy)
    if native_loader.NATIVE:
        points, edges = native_loader.native_core().seed_graph(
            *placements(job['refr_recs'], base_model_by_fid, get_collision, bases),
            None if land is None else land.grid, ox, oy, open_water_z(cell, True),
            np.asarray(job['seeds'], dtype=np.float64).reshape(-1, 3))
    else:
        walkable, blocking = gather_cell_geometry(job['refr_recs'], base_model_by_fid,
                                                  get_collision, job['land_rec'], ox, oy,
                                                  skip_bases=bases,
                                                  water_z=open_water_z(cell, True))
        points, edges = seed_graph(job['seeds'], np.asarray(walkable), np.asarray(blocking),
                                   land, ox, oy)
    return pathgrid_order(points, edges) if edges else None
