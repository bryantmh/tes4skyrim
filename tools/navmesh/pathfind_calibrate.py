"""Score the offline pathfinder against walks the game really made (tools/navmesh/walk_outcomes.py output).

    python tools/navmesh/pathfind_calibrate.py --plugin Arktwend_English.esm walks.jsonl [--radius 22] [--max 40]

Only walks logged after the plugin's ESM was last written count. For each, the planned path is stepped with
the body test; the prediction (reached / stalled where) is set beside what the game did.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.plugin_masters import export_root, master_export_dir
from output_layout import plugin_esm
from tools.navmesh.pathfind import World, plan
from tools.navmesh.pathfind_body import Collision, walk_body

#: A predicted stall this close to the game's stall counts as the same stall.
SAME_SPOT = 200.0
#: Walks whose starts and targets round to the same grid count once.
DEDUPE = 64.0


def load_walks(path, stem, newer_than):
    """The distinct reached/stalled walks of plugin `stem` logged after `newer_than`."""
    out, seen = [], set()
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            w = json.loads(line)
            if not w['plugins'] or w['plugins'][-1].lower() != stem or w['start'] is None:
                continue
            if w['outcome'] not in ('reached', 'stalled') or os.path.getmtime(w['log']) < newer_than:
                continue
            key = tuple(round(v / DEDUPE) for v in start_of(w) + w['target']) + (w['outcome'],)
            if key not in seen:
                seen.add(key)
                out.append(w)
    return out


def start_of(w):
    """The walk's first logged position (a door hop leaves `start` stale)."""
    return w['samples'][0] if w['samples'] else w['start']


def predict(world, collision, w, radius):
    """`(outcome, point, reason)` the offline pathfinder gives for one walk."""
    interior = w['place'] if w['place'].lower() in world.cell_names else None
    route, _meshes, why = plan(world, start_of(w), w['target'], interior, radius)
    if route is None:
        return 'stalled', np.array(start_of(w)), why
    for points, place in route:
        stop, reason = walk_body(collision, points, radius, lambda p, i=place: world.surface_z(p, i), place)
        if stop is not None:
            return 'stalled', stop, reason
    return 'reached', None, 'ok'


def score(world, collision, walks, radius, limit):
    """Print each walk's prediction beside the game's outcome, then the agreement counts."""
    counts, t0 = {}, time.time()
    for w in walks[:limit]:
        got, at, why = predict(world, collision, w, radius)
        same = got == w['outcome']
        if same and got == 'stalled' and w['end'] is not None:
            same = float(np.linalg.norm(np.array(at[:2]) - np.array(w['end'][:2]))) <= SAME_SPOT
        counts[(w['outcome'], got, same)] = counts.get((w['outcome'], got, same), 0) + 1
        mark = 'OK ' if same else 'BAD'
        print('%s game %-7s tool %-7s %s  %s -> %s  %s' % (
            mark, w['outcome'], got, why, fmt(start_of(w)), fmt(w['target']),
            'tool stop %s game end %s' % (fmt(at), fmt(w['end'])) if got == 'stalled' or w['outcome'] == 'stalled'
            else ''), flush=True)
    print('%.0f s' % (time.time() - t0))
    for (game, tool, same), n in sorted(counts.items()):
        print('game %-7s tool %-7s %s: %d' % (game, tool, 'agree' if same else 'DISAGREE', n))


def fmt(p):
    """A point as 'x y z', or '-'."""
    return '-' if p is None else ' '.join('%.0f' % v for v in p)


def main():
    """CLI: score the pathfinder for one plugin's logged walks."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('walks')
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--radius', type=float, default=22.0, help='body radius (human BSBound half-width 22)')
    ap.add_argument('--max', type=int, default=40)
    ap.add_argument('--stalled-first', action='store_true')
    a = ap.parse_args()
    export = master_export_dir('export', a.plugin)
    esm = str(plugin_esm('output', a.plugin, export_root(export)))
    walks = load_walks(a.walks, os.path.splitext(a.plugin)[0].lower(), os.path.getmtime(esm))
    if a.stalled_first:
        walks.sort(key=lambda w: w['outcome'] != 'stalled')
    print('%d walks after %s was written' % (len(walks), esm), flush=True)
    score(World(esm), Collision(export), walks, a.radius, a.max)
    return 0


if __name__ == '__main__':
    sys.exit(main())
