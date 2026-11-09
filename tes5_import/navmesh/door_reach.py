"""Navmesh for an interior with no pathgrid, flooded from its doors.

Such a cell would otherwise get no navmesh at all, so no actor could walk to its doors and a
follower could not follow through them. Each teleport door and each landing point in the cell
seeds a flood over the walkable floor, and the cell keeps everything its doors flood. A cell
with an authored pathgrid is left to it.
See: docs/commentary/tes5_import_navmesh.md#door-reach
"""

import math
from collections import defaultdict

import numpy as np

from ..base.text_reader import get_formid
from . import native_loader
from .edge_links import CELL_SIZE
from .spawn_nodes import spawn_point
from .world import land_field, open_water_z, placements

#: Spacing of the door flood's lattice: fine enough to stand a node between a stair's rails.
DOOR_REACH_SPACING = 64.0

#: Spacing of the join anchors laid along a graph's edges for a door the mesh never reached.
EDGE_ANCHOR_STEP = 128.0

#: Spacing of the graph a flood is thinned to (pathgrid-like, so the ribbon grow stays cheap).
DOOR_REACH_THIN_SPACING = 256.0

#: Most lattice nodes one flood holds before it gives up (about 3,500 units square at 64).
DOOR_REACH_MAX_NODES = 3000.0

#: How far in plan a seed with no floor under it looks for one (a door's frame stands in a wall).
DOOR_SEED_SEARCH = 192.0

#: How far below a seed its floor may lie (a landing marker floats above its floor).
DOOR_SEED_DROP = 256.0

def landings_by_cell(refrs) -> dict:
    """{cell FormID: [(x, y, z)]}: where each teleport door's traveller lands, by the landing's cell."""
    by_fid = {get_formid(r, 'FormID'): r for r in refrs}
    out = defaultdict(list)
    for r in refrs:
        partner = by_fid.get(get_formid(r, 'XTEL.Door'))
        point = spawn_point({k[5:]: r.get(k) for k in ('XTEL.PosX', 'XTEL.PosY', 'XTEL.PosZ')})
        if partner is not None and point is not None:
            out[get_formid(partner, 'ParentCELL')].append(point)
    return out


def door_seeds(cell_refrs, landings) -> list:
    """Sorted seed points of one interior: its teleport doors and the landings in it, one per
    DOOR_REACH_SPACING (a door and its landing usually stand side by side)."""
    doors = (spawn_point(r) for r in cell_refrs if r.get('XTEL.Door'))
    out = []
    for p in sorted({p for p in doors if p is not None} | set(landings)):
        if all(math.dist(p, q) > DOOR_REACH_SPACING for q in out):
            out.append(p)
    return out


def join_missing_doors(job, graph, missing, base_model_by_fid, door_fids, get_collision):
    """`graph` plus a walkable path from each door point in `missing` to the graph, or None when none joins.

    A door the cell's mesh never reached gets no Door Triangle, and the engine routes nothing through it.
    See: docs/commentary/tes5_import_navmesh.md#door-reach
    """
    points, edges = list(graph[0]), list(graph[1])
    cell = job['cell_rec']
    exterior = bool(cell.get('XCLC.X') not in (None, ''))
    ox = float(cell.get('XCLC.X') or 0) * CELL_SIZE if exterior else 0.0
    oy = float(cell.get('XCLC.Y') or 0) * CELL_SIZE if exterior else 0.0
    land = land_field(job['land_rec'], ox, oy) if exterior else None
    bases = set(door_fids.keys()) if isinstance(door_fids, dict) else set(door_fids or ())
    anchors, on_edge = edge_anchors(points, edges)
    new, links, joins = native_loader.native_core().door_reach(
        *placements(job['refr_recs'], base_model_by_fid, get_collision, bases),
        None if land is None else land.grid, ox, oy, open_water_z(cell, exterior),
        np.asarray(missing, dtype=np.float64).reshape(-1, 3),
        np.asarray(anchors, dtype=np.float64).reshape(-1, 3))
    if not joins:
        return None
    base = len(points)
    points += [tuple(float(c) for c in p) for p in new]
    added = [(base + a, base + b) for a, b in links if a != b]
    for p, a in joins:
        if a < base:
            added.append((base + p, a))
            continue
        points.append(tuple(anchors[a]))
        mid = len(points) - 1
        added += [(base + p, mid)] + [(mid, end) for end in on_edge[a - base]]
    seen = set(edges)
    edges += [e for e in dict.fromkeys((min(a, b), max(a, b)) for a, b in added) if e not in seen]
    return points, edges


def edge_anchors(points, edges):
    """`(anchors, ends)`: the graph's nodes, then a point every EDGE_ANCHOR_STEP along each longer edge;
    `ends[i]` is the edge an extra anchor `len(points) + i` lies on."""
    anchors, ends = list(points), []
    for a, b in edges:
        n = int(math.dist(points[a], points[b]) // EDGE_ANCHOR_STEP)
        for k in range(1, n):
            f = k / n
            anchors.append(tuple(points[a][i] + (points[b][i] - points[a][i]) * f for i in range(3)))
            ends.append((a, b))
    return anchors, ends


def flood_graph(job, base_model_by_fid, door_fids, get_collision) -> tuple:
    """(points, edges): everything the job's door seeds flood, as a pathgrid-like graph."""
    bases = set(door_fids.keys()) if isinstance(door_fids, dict) else set(door_fids or ())
    new, links, _joins = native_loader.native_core().door_reach(
        *placements(job['refr_recs'], base_model_by_fid, get_collision, bases),
        None, 0.0, 0.0, open_water_z(job['cell_rec'], False),
        np.asarray(job['door_seeds'], dtype=np.float64).reshape(-1, 3),
        np.zeros((0, 3), dtype=np.float64))
    edges = dict.fromkeys((min(a, b), max(a, b)) for a, b in links if a != b)
    return [tuple(p) for p in new], list(edges)
