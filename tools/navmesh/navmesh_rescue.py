"""Where to put a walker back on the navmesh after it slid off: a point on the mesh a little further along its
route.

    python tools/navmesh/navmesh_rescue.py --plugin Arktwend_English.esm --from x,y,z --to x,y,z [--interior NAME]

The test harness asks this when a `walk` stalls: a player that slid down a slope off the mesh's edge has no
triangle under it and the engine's Travel never moves it again.
See: docs/commentary/headless_testing.md#quest-runs
"""
import argparse
import math
import os

from core.plugin_masters import export_root, master_export_dir
from output_layout import plugin_esm
from tools.navmesh.pathfind import World, plan

#: How far a walker may lie from the mesh it slid off, and the ring step of the search for it.
REACH, RING_STEP = 800.0, 16.0
#: How far along the route past the mesh point nearest the walker it is put back.
AHEAD = 256.0


def nearest_on_mesh(world, p, interior=None):
    """The nearest point to `p` (within REACH, either way in height too) whose lookup at the mesh's own height
    lands exactly on a triangle, else None: a walker that slid lies below the mesh it came from."""
    r = RING_STEP
    while r <= REACH:
        steps = max(8, int(2 * math.pi * r / RING_STEP))
        for k in range(steps):
            a = 2 * math.pi * k / steps
            x, y = p[0] + r * math.cos(a), p[1] + r * math.sin(a)
            q = (x, y, world.surface_z((x, y, p[2]), interior))
            got = world.find(q, interior)
            if got and got[2] and abs(q[2] - p[2]) <= REACH:
                return q
        r += RING_STEP
    return None


def along(points, distance):
    """The point `distance` along a polyline from its start (its end if shorter)."""
    for a, b in zip(points, points[1:]):
        seg = math.dist(a[:2], b[:2])
        if seg >= distance and seg > 0:
            t = distance / seg
            return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))
        distance -= seg
    return tuple(points[-1])


def rescue_point(world, p, target, interior=None):
    """Where to put a walker stalled at `p` heading for `target`: None when `p` is on the mesh (not a slide)."""
    here = world.find(p, interior)
    if here and here[2]:
        return None
    start = nearest_on_mesh(world, p, interior)
    if start is None:
        return None
    legs = plan(world, start, target, interior)[0]
    if not legs:
        return start
    q = along([tuple(v) for v in legs[0][0]], AHEAD)
    return q[0], q[1], world.surface_z(q, interior)


def plugin_esm_path(plugin):
    """The plugin's built ESM in output/."""
    return str(plugin_esm('output', plugin, export_root(master_export_dir('export', plugin))))


def plugin_world(plugin):
    """The navmeshes of a plugin's built ESM."""
    return World(plugin_esm_path(plugin))


def harness_reply(plugins, line, worlds):
    """The runner's `{"onmesh": [x,y,z], "to": [x,y,z], "cell": interior}` answered as 'x y z' or 'none', from
    the last plugin in `plugins` whose built ESM has navmesh there; `worlds` caches each plugin's World."""
    p, target, interior = line['onmesh'], line['to'], line.get('cell') or None
    for name in reversed(plugins):
        if name not in worlds:
            path = plugin_esm_path(name)
            worlds[name] = World(path) if os.path.isfile(path) else None
        world = worlds[name]
        if world is not None and world.meshes_at(p[0], p[1], interior):
            point = rescue_point(world, p, target, interior)
            return 'none' if point is None else '%.1f %.1f %.1f' % point
    return 'none'


def main():
    """Print the rescue point, or 'none'."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--from', dest='start', required=True)
    ap.add_argument('--to', required=True)
    ap.add_argument('--interior')
    args = ap.parse_args()
    point = rescue_point(plugin_world(args.plugin), [float(v) for v in args.start.split(',')],
                         [float(v) for v in args.to.split(',')], args.interior)
    print('none' if point is None else '%.0f %.0f %.0f' % point)


if __name__ == '__main__':
    main()
