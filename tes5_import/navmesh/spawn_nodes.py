"""Morrowind actor spawn points as lone pathgrid nodes.

Morrowind lets an actor stand where its pathgrid never reaches; Skyrim moves
an actor that is off navmesh onto the nearest navmesh. Each spawn the
pathgrid does not already cover becomes a node of its own, and the corridor
generator grows a disc around it.
"""

import math

from . import params, world
from .corridor_union import levels_at
from .union_geom import SAME_SURFACE_Z
from ..record_types.actor_common import T4C_FLIES, T4C_SWIMS, T4C_WALKS

#: Placed-actor record types whose spawn points become nodes.
ACTOR_SIGS = ('ACHR', 'ACRE')


def is_airborne(crea: dict) -> bool:
    """True for a creature that flies or swims and never walks: it stands on no floor."""
    flags = int(crea.get('ACBS.Flags') or 0)
    return not flags & T4C_WALKS and bool(flags & (T4C_FLIES | T4C_SWIMS))


def spawn_point(rec: dict):
    """A placement's authored (x, y, z), or None when it is not a usable position."""
    try:
        point = tuple(float(rec.get(key)) for key in ('PosX', 'PosY', 'PosZ'))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) and abs(v) <= world.MAX_PLACEMENT for v in point):
        return None
    return point


def _segment_gap(spawn, a, b):
    """(plan distance, height gap) from `spawn` to segment a-b."""
    ax, ay, az = a
    dx, dy = b[0] - ax, b[1] - ay
    run = dx * dx + dy * dy
    t = 0.0 if run < 1e-9 else max(0.0, min(1.0, ((spawn[0] - ax) * dx
                                                   + (spawn[1] - ay) * dy) / run))
    plan = math.hypot(spawn[0] - (ax + dx * t), spawn[1] - (ay + dy * t))
    return plan, abs(spawn[2] - (az + (b[2] - az) * t))


def _on_pathgrid(points, edges, spawn) -> bool:
    """True when a pathgrid ribbon already passes under `spawn`."""
    for i, j in edges:
        plan, height = _segment_gap(spawn, points[i], points[j])
        if plan <= params.RIBBON_HALF_WIDTH and height <= params.SEED_Z_TOLERANCE:
            return True
    return False


def add_spawn_nodes(points: list, edges: list, spawns) -> frozenset:
    """Append each spawn no ribbon covers to `points`; the new nodes' indices."""
    first = len(points)
    for spawn in spawns or ():
        if not _on_pathgrid(points, edges, spawn):
            points.append(spawn)
    return frozenset(range(first, len(points)))


def floor_below(sample, x, y, z):
    """The highest walkable height at or a step above z: where a placed actor falls to."""
    under = [h for h in sample.layers(x, y) if h <= z + params.MAX_CLIMB]
    return under[-1] if under else None


def body_clear(x, y, z, wall_hit) -> bool:
    """No object in an actor-width body column from step height to actor height."""
    return not wall_hit(x, y, 1.0, 0.0, 0.0, 1.0,
                        z + params.RIBBON_GROW_SLAB_Z_BOTTOM,
                        z + params.AGENT_HEIGHT,
                        depth=params.RIBBON_GROW_SLAB_HALF_WIDTH)


def _is_spawn_disc(strip, lone) -> bool:
    """True for the disc strip of a spawn node."""
    return strip['edge'][0] == strip['edge'][1] and strip['edge'][0] in lone


def drop_covered_discs(strips: list, lone) -> tuple:
    """(strips, kept spawn nodes): a disc goes when the pathgrid's ribbons already cover its node.

    Covered means a ribbon surface within SAME_SURFACE_Z of the node.
    """
    ribbons = [s for s in strips if not _is_spawn_disc(s, lone)]
    kept = frozenset(
        s['edge'][0] for s in strips if _is_spawn_disc(s, lone)
        and not any(abs(z - s['na'][2]) <= SAME_SURFACE_Z
                    for z in levels_at(ribbons, s['na'][0], s['na'][1])))
    return ([s for s in strips
             if not _is_spawn_disc(s, lone) or s['edge'][0] in kept], kept)


def standing_spawns(lone, nodes, node_z, sample, wall_hit) -> frozenset:
    """Drop each spawn node in `lone` onto its floor in `node_z`; the ones an actor stands at.

    A spawn with no floor below, or inside furniture or under a table top, is refused.
    """
    if sample is None:
        return frozenset()
    standing = set()
    for i in lone:
        floor = floor_below(sample, nodes[i][0], nodes[i][1], nodes[i][2])
        if floor is not None and body_clear(nodes[i][0], nodes[i][1], floor, wall_hit):
            node_z[i] = floor
            standing.add(i)
    return frozenset(standing)
