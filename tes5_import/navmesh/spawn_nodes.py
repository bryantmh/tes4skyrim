"""Morrowind actor spawn points as lone pathgrid nodes.

Morrowind lets an actor stand where its pathgrid never reaches; Skyrim moves
an actor that is off navmesh onto the nearest navmesh. Each spawn the
pathgrid does not already cover becomes a node of its own, and the corridor
generator grows a disc around it.
"""

import math

from . import params, world
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


def segment_gap(spawn, a, b):
    """(plan distance, height gap) from `spawn` to segment a-b."""
    ax, ay, az = a
    dx, dy = b[0] - ax, b[1] - ay
    run = dx * dx + dy * dy
    t = 0.0 if run < 1e-9 else max(0.0, min(1.0, ((spawn[0] - ax) * dx
                                                   + (spawn[1] - ay) * dy) / run))
    plan = math.hypot(spawn[0] - (ax + dx * t), spawn[1] - (ay + dy * t))
    return plan, abs(spawn[2] - (az + (b[2] - az) * t))


def on_pathgrid(points, edges, spawn) -> bool:
    """True when a pathgrid ribbon already passes under `spawn`."""
    for i, j in edges:
        plan, height = segment_gap(spawn, points[i], points[j])
        if plan <= params.RIBBON_HALF_WIDTH and height <= params.SEED_Z_TOLERANCE:
            return True
    return False


def add_spawn_nodes(points: list, edges: list, spawns) -> frozenset:
    """Append each spawn no ribbon covers to `points`; the new nodes' indices."""
    first = len(points)
    for spawn in spawns or ():
        if not on_pathgrid(points, edges, spawn):
            points.append(spawn)
    return frozenset(range(first, len(points)))

