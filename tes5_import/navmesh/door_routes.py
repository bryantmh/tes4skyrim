"""Route a door the corridor never reaches to the pathgrid over the cell's collision.

`corridor_doors` bridges a door to a corridor edge within DOOR_BRIDGE_RADIUS on
its own storey. A door the bridge cannot reach -- farther off, or up a flight
of steps -- gets a route instead: a best-first ROUTE_STEP grid walk from just
in front of the door to the nearest pathgrid node an actor can walk to,
pulled tight into straight walkable legs that join the pathgrid as edges.
"""

import heapq
import math

from . import params
from .corridor_doors import DOOR_TRI_MIN_DEPTH
from .spawn_nodes import body_clear, floor_below

#: Grid spacing of a route search; under the body column's 40u width so no wall slips between.
ROUTE_STEP = 16.0

#: Farthest a route strays from its door, in plan.
ROUTE_REACH = 1536.0

#: The eight grid neighbours a route search steps to.
_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


def _station_floor(sample, wall_hit, x, y, z):
    """The floor an actor at height z steps onto at (x, y), or None for a drop, a wall or no floor."""
    floor = floor_below(sample, x, y, z)
    if floor is None or z - floor > params.MAX_CLIMB or not body_clear(x, y, floor, wall_hit):
        return None
    return floor


def _straight_walk(start, end, sample, wall_hit) -> bool:
    """True when an actor walks straight from `start` to `end`, a station every RIBBON_GROW_STEP."""
    run = math.hypot(end[0] - start[0], end[1] - start[1])
    stations = max(1, math.ceil(run / params.RIBBON_GROW_STEP))
    z = start[2]
    for k in range(1, stations + 1):
        z = _station_floor(sample, wall_hit, start[0] + (end[0] - start[0]) * k / stations,
                           start[1] + (end[1] - start[1]) * k / stations, z)
        if z is None:
            return False
    return True


def _target_at(targets, x, y, z):
    """The target node a station at (x, y, z) stands on, or None."""
    for i, (tx, ty, tz) in targets:
        if math.hypot(x - tx, y - ty) <= params.RIBBON_HALF_WIDTH and abs(z - tz) <= params.MAX_CLIMB:
            return i
    return None


def _route(start, targets, sample, wall_hit):
    """(stations from `start`, reached target node) by a best-first ROUTE_STEP grid walk, or None."""
    floors, came = {(0, 0): start[2]}, {}
    heap = [(0.0, (0, 0))]
    while heap:
        _h, (i, j) = heapq.heappop(heap)
        hit = _target_at(targets, start[0] + i * ROUTE_STEP, start[1] + j * ROUTE_STEP, floors[(i, j)])
        if hit is not None:
            path = [(i, j)]
            while path[-1] in came:
                path.append(came[path[-1]])
            return [(start[0] + a * ROUTE_STEP, start[1] + b * ROUTE_STEP, floors[(a, b)])
                    for a, b in reversed(path)], hit
        for di, dj in _NEIGHBOURS:
            key = (i + di, j + dj)
            if key in floors or key in came or math.hypot(*key) * ROUTE_STEP > ROUTE_REACH:
                continue
            came[key] = (i, j)
            x, y = start[0] + key[0] * ROUTE_STEP, start[1] + key[1] * ROUTE_STEP
            floor = _station_floor(sample, wall_hit, x, y, floors[(i, j)])
            if floor is not None:
                floors[key] = floor
                heapq.heappush(heap, (min(math.hypot(x - t[0], y - t[1]) for _t, t in targets), key))
    return None


def _pull_tight(path, sample, wall_hit) -> list:
    """The stations of `path` that straight walks between them still join, both ends kept."""
    out, cur = [path[0]], 0
    while cur < len(path) - 1:
        cur = next(j for j in range(len(path) - 1, cur, -1)
                   if j == cur + 1 or _straight_walk(path[cur], path[j], sample, wall_hit))
        out.append(path[cur])
    return out


def _door_route(door, targets, sample, wall_hit):
    """The first route found from a point DOOR_TRI_MIN_DEPTH in front of either face of `door`."""
    dx, dy, dz, rz = door[:4]
    fx, fy = math.cos(rz), -math.sin(rz)
    for side in (1.0, -1.0):
        x, y = dx + fx * side * DOOR_TRI_MIN_DEPTH, dy + fy * side * DOOR_TRI_MIN_DEPTH
        floor = floor_below(sample, x, y, dz)
        if floor is not None and body_clear(x, y, floor, wall_hit):
            found = _route((x, y, floor), targets, sample, wall_hit)
            if found:
                return found
    return None


def route_doors(doors, nodes, node_z, edges, sample, wall_hit):
    """(nodes, node_z, edges) with a route from each of `doors` to the pathgrid, or None if none routed.

    Each route, pulled tight, joins as new nodes plus the edges chaining them
    to the pathgrid node it reached.
    """
    targets = [(i, (nodes[i][0], nodes[i][1], node_z[i]))
               for i in sorted({i for edge in edges for i in edge})]
    if sample is None or not targets:
        return None
    nodes, node_z, edges, routed = list(nodes), list(node_z), list(edges), False
    for door in doors:
        found = _door_route(door, targets, sample, wall_hit)
        if not found:
            continue
        chain = []
        for x, y, z in _pull_tight(found[0], sample, wall_hit)[:-1]:
            chain.append(len(nodes))
            nodes.append((x, y, z))
            node_z.append(z)
        chain.append(found[1])
        edges += [(min(a, b), max(a, b)) for a, b in zip(chain, chain[1:])]
        routed = True
    return (nodes, node_z, edges) if routed else None
