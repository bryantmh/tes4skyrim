"""Pathgrid edges across a removed obstacle, so the corridor build meshes the floor it leaves.

A pathgrid is authored around the cell as it first loads, so a blocker a script
later removes (`base/obstacles.py`) leaves no edge across the floor it stood
on, and the corridor generator meshes only along edges. Obstacle footprints
that touch in plan form one cluster. The pathgrid components that come within
the cluster's own half-diagonal of it are joined at their nearest nodes, where
an actor can walk the straight line between them over the cell's collision
without the obstacle.

See: docs/commentary/tes5_import_navmesh.md#a-removed-obstacle-is-bridged
"""

import itertools
import math

import numpy as np

from . import params
from .seeding import Ground
from .world import base_fid, gather_cell_geometry, land_field


def _footprint(refr, key, get_collision):
    """(xmin, ymin, zmin, xmax, ymax, zmax) of one placed obstacle's collision, or None."""
    soups = [s for s in gather_cell_geometry([refr], {base_fid(refr): key}, get_collision) if len(s)]
    if not soups:
        return None
    pts = np.concatenate(soups).reshape(-1, 3)
    return tuple(float(v) for v in (*pts.min(axis=0), *pts.max(axis=0)))


def _overlap(a, b) -> bool:
    """True when two boxes overlap in plan."""
    return a[0] <= b[3] and b[0] <= a[3] and a[1] <= b[4] and b[1] <= a[4]


def _union(a, b):
    """The box holding both."""
    return tuple(min(a[i], b[i]) for i in range(3)) + tuple(max(a[i], b[i]) for i in range(3, 6))


def _clusters(boxes) -> list:
    """The boxes merged wherever they overlap in plan."""
    out = []
    for box in boxes:
        touching = [c for c in out if _overlap(c, box)]
        while touching:
            for c in touching:
                out.remove(c)
                box = _union(box, c)
            touching = [c for c in out if _overlap(c, box)]
        out.append(box)
    return out


def _labels(count, edges) -> list:
    """Each node's component label: the lowest node index it connects to."""
    adj = [[] for _ in range(count)]
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    label = [-1] * count
    for start in range(count):
        if label[start] >= 0:
            continue
        label[start] = start
        todo = [start]
        while todo:
            for n in adj[todo.pop()]:
                if label[n] < 0:
                    label[n] = start
                    todo.append(n)
    return label


def _gap(box, x, y) -> float:
    """Plan distance from (x, y) to the box."""
    return math.hypot(max(box[0] - x, 0.0, x - box[3]), max(box[1] - y, 0.0, y - box[4]))


def _nearest_per_component(points, label, box) -> list:
    """Each component's node nearest the box, among nodes within its half-diagonal and height."""
    reach = 0.5 * math.hypot(box[3] - box[0], box[4] - box[1])
    nearest = {}
    for i, (x, y, z) in enumerate(points):
        gap = _gap(box, x, y)
        if gap <= reach and box[2] - params.AGENT_HEIGHT <= z <= box[5]:
            if label[i] not in nearest or gap < nearest[label[i]][0]:
                nearest[label[i]] = (gap, i)
    return sorted(i for _gap_, i in nearest.values())


def _bridge_cluster(points, edges, box, ground) -> int:
    """Join the components reaching `box` where an actor can walk between them; edges added."""
    label = _labels(len(points), edges)
    nodes = _nearest_per_component(points, label, box)
    pairs = sorted(itertools.combinations(nodes, 2),
                   key=lambda p: (math.dist(points[p[0]], points[p[1]]), p))
    added = 0
    for a, b in pairs:
        if label[a] != label[b] and ground.walk(points[a], points[b]):
            edges.append((a, b))
            added += 1
            joined = label[b]
            label = [label[a] if lab == joined else lab for lab in label]
    return added


def cell_ground(refr_recs, base_model_by_fid, get_collision, land_rec, origin_x, origin_y,
                door_bases, water_z):
    """The cell's walk tests over the collision the corridor build carves."""
    walkable, blocking = gather_cell_geometry(refr_recs, base_model_by_fid, get_collision, land_rec,
                                              origin_x, origin_y, skip_bases=door_bases,
                                              water_z=water_z)
    land = land_field(land_rec, origin_x, origin_y) if land_rec else None
    return Ground(np.asarray(walkable), np.asarray(blocking), land)


def bridge_obstacles(points, edges, obstacles, ground_of, get_collision) -> int:
    """Append walkable edges across each obstacle cluster to `edges`; how many.

    `obstacles` are (placement, model key) pairs; `ground_of()` builds the cell's
    `Ground`, called only when an obstacle has collision.
    """
    boxes = [b for b in (_footprint(r, k, get_collision) for r, k in obstacles or ()) if b]
    if not boxes or not points:
        return 0
    ground = ground_of()
    return sum(_bridge_cluster(points, edges, box, ground) for box in _clusters(boxes))
