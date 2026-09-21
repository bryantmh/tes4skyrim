"""Transplant an Oblivion pathgrid onto a Beyond Skyrim: Bruma cell.

Bruma rebuilt Cyrodiil's architecture in Skyrim and navmeshed it BY HAND, so a
Bruma interior that replicates an Oblivion one is an answer key for our
generator.  Using it needs an input in the same frame: our generator is seeded
by a pathgrid, and the Oblivion cell's pathgrid sits in a different coordinate
frame from its Bruma counterpart (BrumaChapelHall: ours x -411..1366, Bruma's
authored navmesh x 16..1732).

`fit` finds the rigid placement -- translation plus one of the four 90 degree
rotations -- that best lands the Oblivion grid on the authored mesh.  That is a
STARTING POSITION, never the answer: Bruma moved statics, so nodes still land in
walls and have to be nudged by hand.  The corpus file records the fit plus those
per-node overrides, and is what the comparison harness reads.

Node positions are NEVER derived from the authored navmesh -- a grid shaped by
the answer would leak the answer into the input.

    python tools/navmesh/transplant.py fit BrumaChapelHall \
        --authored-cell CYRBrumaChapelHall --authored-esm <esm>
    python tools/navmesh/transplant.py move BrumaChapelHall 17 --to 620 -40
    python tools/navmesh/transplant.py render BrumaChapelHall

See: docs/commentary/tes5_import_navmesh.md#authored-navmesh-corpus
"""

import argparse
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.navmesh.authored import load_authored
from tools.navmesh.index import NavIndex, DEFAULT_EXPORT

#: One committed JSON per matched cell pair.
CORPUS = os.path.join('tests', 'navmesh_authored')

#: The only rotations considered: Bruma rebuilt rooms on axis, not at an angle.
_ROTS = (0, 90, 180, 270)

#: NavIndex by export path; see index_for.
_INDEX = {}

#: Serializes index_for; the cellview server builds indexes on N threads.
_INDEX_LOCK = threading.Lock()


def index_for(export):
    """The shared NavIndex for `export`, keyed on its normalized path.

    Locked: the cellview server is threaded, so an unguarded check-then-build
    lets two requests construct an index at once and interleave `arm`'s writes
    to the shared collision/namespace globals.

    See: docs/commentary/tes5_import_navmesh.md#editor-navindex-cache
    """
    key = os.path.normcase(os.path.normpath(export))
    with _INDEX_LOCK:
        if key not in _INDEX:
            _INDEX[key] = NavIndex(export)
        return _INDEX[key]


def corpus_path(cell):
    """Path of the corpus file for `cell`."""
    return os.path.join(CORPUS, '%s.json' % cell)


def load(cell):
    """The corpus entry for `cell`, or None if it has not been fitted yet."""
    p = corpus_path(cell)
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def save(cell, entry):
    """Write the corpus entry, creating the directory on first use."""
    if not os.path.isdir(CORPUS):
        os.makedirs(CORPUS)
    with open(corpus_path(cell), 'w', encoding='utf-8') as fh:
        json.dump(entry, fh, indent=2, sort_keys=True)
        fh.write('\n')
    print('wrote', corpus_path(cell))


def _rotate(x, y, deg):
    """Rotate (x, y) about the origin by one of the four right angles."""
    if deg == 90:
        return -y, x
    if deg == 180:
        return -x, -y
    if deg == 270:
        return y, -x
    return x, y


def apply_fit(nodes, fit_d):
    """Map source nodes through a fit dict into the authored cell's frame."""
    cx, cy = fit_d['src_center']
    dx, dy, dz = fit_d['offset']
    out = []
    for (x, y, z) in nodes:
        rx, ry = _rotate(x - cx, y - cy, fit_d['rot'])
        out.append((rx + dx, ry + dy, z + dz))
    return out


def placed_nodes(cell, idx=None, entry=None):
    """Final `(nodes, edges)` for `cell`: the fit, with manual edits applied.

    Node indices are STABLE: `added` nodes append after the source grid, and a
    `dropped` node keeps its slot rather than renumbering the ones after it --
    every override, edge and cut is keyed by index, so compacting would silently
    reassign them.  Dropped nodes and their edges are filtered at the end.
    """
    entry = entry or load(cell)
    if entry is None:
        return [], []
    idx = idx or index_for(entry.get('export', DEFAULT_EXPORT))
    src = idx.cell(entry['source_cell'])
    nodes = apply_fit(src.nodes, entry['fit'])
    for k, v in entry.get('moved', {}).items():
        i = int(k)
        if 0 <= i < len(nodes):
            nodes[i] = tuple(v)
    nodes += [tuple(p) for p in entry.get('added', [])]
    keep = set(range(len(nodes))) - set(entry.get('dropped', []))
    cut = {tuple(sorted(e)) for e in entry.get('cut_edges', [])}
    edges = [(a, b) for (a, b) in src.edges
             if a in keep and b in keep and (min(a, b), max(a, b)) not in cut]
    have = {(min(a, b), max(a, b)) for (a, b) in edges}
    for (a, b) in entry.get('added_edges', []):
        k = (min(a, b), max(a, b))
        if a in keep and b in keep and k not in have:
            have.add(k)
            edges.append((a, b))
    return nodes, edges


def occupancy(averts, atris, cell_size=64.0):
    """Grid cells the authored mesh covers, RASTERISED from its triangles.

    Marking only cells that contain a vertex undercounts badly: authored
    triangles are large (p50 area 5,683u2), so a node in open floor falls in no
    vertex's cell and scores as a miss.  Walking each triangle's bbox and
    testing containment is what makes the score mean "on the mesh".
    """
    occ = set()
    for (ia, ib, ic) in atris:
        a, b, c = averts[ia], averts[ib], averts[ic]
        x0 = int(min(a[0], b[0], c[0]) // cell_size)
        x1 = int(max(a[0], b[0], c[0]) // cell_size)
        y0 = int(min(a[1], b[1], c[1]) // cell_size)
        y1 = int(max(a[1], b[1], c[1]) // cell_size)
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                if _in_tri((gx + 0.5) * cell_size, (gy + 0.5) * cell_size,
                           a, b, c):
                    occ.add((gx, gy))
    return occ


def _in_tri(px, py, a, b, c):
    """Is (px, py) inside triangle abc, by consistent edge sign?"""
    d1 = (px - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (py - b[1])
    d2 = (px - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (py - c[1])
    d3 = (px - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (py - a[1])
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def _score(nodes, occ, cell_size=64.0):
    """Fraction of nodes landing on the authored mesh, higher better."""
    if not occ or not nodes:
        return 0.0
    hit = sum(1 for p in nodes
              if (int(p[0] // cell_size), int(p[1] // cell_size)) in occ)
    return hit / float(len(nodes))


def _climb(base, src_nodes, occ, step):
    """Hill-climb the translation of one rotation; returns (score, fit)."""
    cur = _score(apply_fit(src_nodes, base), occ)
    moved = True
    while moved:
        moved = False
        for (ddx, ddy) in ((step, 0), (-step, 0), (0, step), (0, -step)):
            cand = dict(base)
            cand['offset'] = [base['offset'][0] + ddx,
                              base['offset'][1] + ddy, base['offset'][2]]
            s = _score(apply_fit(src_nodes, cand), occ)
            if s > cur:
                base, cur, moved = cand, s, True
    return cur, base


def fit(src_nodes, averts, atris, step=32.0):
    """Best rigid (rotation, translation) landing `src_nodes` on the mesh.

    Tries each right-angle rotation from aligned bbox centers, then hill-climbs
    the translation.  Returns the fit dict the corpus stores.
    """
    occ = occupancy(averts, atris)
    cx = (min(p[0] for p in src_nodes) + max(p[0] for p in src_nodes)) / 2.0
    cy = (min(p[1] for p in src_nodes) + max(p[1] for p in src_nodes)) / 2.0
    ax = (min(p[0] for p in averts) + max(p[0] for p in averts)) / 2.0
    ay = (min(p[1] for p in averts) + max(p[1] for p in averts)) / 2.0
    az = sum(p[2] for p in averts) / float(len(averts))
    sz = sum(p[2] for p in src_nodes) / float(len(src_nodes))
    best = None
    for rot in _ROTS:
        cur, cand = _climb({'rot': rot, 'src_center': [cx, cy],
                            'offset': [ax, ay, az - sz]},
                           src_nodes, occ, step)
        if best is None or cur > best[0]:
            best = (cur, cand)
    best[1]['score'] = round(best[0], 4)
    return best[1]


def _cmd_fit(a):
    """Fit the Oblivion grid onto the authored mesh and write the corpus file."""
    idx = NavIndex(a.export)
    src = idx.cell(a.source_cell or a.cell)
    if src is None or not src.has_pathgrid:
        print('no source pathgrid for', a.source_cell or a.cell)
        return 1
    averts, atris = load_authored(a.authored_esm, a.authored_cell)
    if not atris:
        print('no authored navmesh for', a.authored_cell)
        return 1
    f = fit(src.nodes, averts, atris)
    entry = load(a.cell) or {}
    entry.update({'cell': a.cell, 'source_cell': a.source_cell or a.cell,
                  'authored_cell': a.authored_cell,
                  'authored_esm': a.authored_esm, 'export': a.export,
                  'fit': f})
    entry.setdefault('moved', {})
    entry.setdefault('dropped', [])
    save(a.cell, entry)
    print('rot %d deg, offset (%.0f, %.0f, %.0f), %d/%d nodes on mesh (%.0f%%)'
          % (f['rot'], f['offset'][0], f['offset'][1], f['offset'][2],
             round(f['score'] * len(src.nodes)), len(src.nodes),
             f['score'] * 100))
    return 0


def _cmd_move(a):
    """Record a manual position for one node, or drop it."""
    entry = load(a.cell)
    if entry is None:
        print('no corpus entry for %s -- run `fit` first' % a.cell)
        return 1
    if a.drop:
        entry['dropped'] = sorted(set(entry.get('dropped', []) + [a.node]))
    else:
        nodes, _e = placed_nodes(a.cell, entry=entry)
        z = a.to[2] if len(a.to) > 2 else nodes[a.node][2]
        entry.setdefault('moved', {})[str(a.node)] = [a.to[0], a.to[1], z]
    save(a.cell, entry)
    return 0


def _cmd_render(a):
    """Render the transplanted grid over the authored mesh, for review."""
    from tools.navmesh.render import render
    entry = load(a.cell)
    if entry is None:
        print('no corpus entry for %s -- run `fit` first' % a.cell)
        return 1
    nodes, edges = placed_nodes(a.cell)
    averts, atris = load_authored(entry['authored_esm'], entry['authored_cell'])
    coll = None
    if a.collision:
        from tools.navmesh.bruma_collision import cell_collision, default_esm
        coll = cell_collision(default_esm(), entry['authored_cell'],
                              quiet=False)
    out = a.out or 'temp/%s_transplant.png' % a.cell
    d = os.path.dirname(out)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    render([], [], nodes, edges, [], out, width=a.width,
           title='%s transplant' % a.cell, authored=(averts, atris),
           node_ids=True, collision=coll)
    return 0


def _cmd_check(a):
    """List the nodes that do NOT land on the authored mesh, with coordinates.

    This is the adjustment worklist: each line names a node to move, drop, or
    justify keeping (a node over a gap Bruma genuinely walled off).
    """
    entry = load(a.cell)
    if entry is None:
        print('no corpus entry for %s -- run `fit` first' % a.cell)
        return 1
    nodes, edges = placed_nodes(a.cell, entry=entry)
    averts, atris = load_authored(entry['authored_esm'], entry['authored_cell'])
    occ = occupancy(averts, atris)
    off = [(i, p) for i, p in enumerate(nodes)
           if (int(p[0] // 64.0), int(p[1] // 64.0)) not in occ
           and i not in entry.get('dropped', [])]
    for i, p in off:
        print('  node %3d at (%8.1f, %8.1f, %8.1f)%s'
              % (i, p[0], p[1], p[2],
                 '  [moved]' if str(i) in entry.get('moved', {}) else ''))
    cross = _edges_off_mesh(nodes, edges, occ)
    print('%s: %d/%d nodes off the authored mesh, %d/%d edges crossing a void'
          % (a.cell, len(off), len(nodes), cross, len(edges)))
    return 0


def _edges_off_mesh(nodes, edges, occ, step=32.0):
    """How many edges run over ground the authored mesh does not cover.

    A grid whose NODES all land on the mesh can still be wrong: if Bruma
    partitioned a room differently, an inherited edge cuts straight through the
    new wall.  Counting those is what separates a clean transplant from a
    merely plausible one.
    """
    bad = 0
    for (a, b) in edges:
        pa, pb = nodes[a], nodes[b]
        d = max(abs(pb[0] - pa[0]), abs(pb[1] - pa[1]))
        n = max(1, int(d / step))
        for i in range(n + 1):
            f = i / float(n)
            x = pa[0] + (pb[0] - pa[0]) * f
            y = pa[1] + (pb[1] - pa[1]) * f
            if (int(x // 64.0), int(y // 64.0)) not in occ:
                bad += 1
                break
    return bad


def _cmd_status(_a):
    """Report every corpus entry: fit score and how much was hand-adjusted."""
    if not os.path.isdir(CORPUS):
        print('no corpus yet at', CORPUS)
        return 0
    for fn in sorted(os.listdir(CORPUS)):
        if not fn.endswith('.json'):
            continue
        e = load(fn[:-5])
        print('%-28s rot %3d  fit %.0f%%  %d moved  %d dropped'
              % (e['cell'], e['fit']['rot'], e['fit'].get('score', 0) * 100,
                 len(e.get('moved', {})), len(e.get('dropped', []))))
    return 0


def _add_args(ap):
    """Register the subcommands."""
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fit', help='rigid-fit the Oblivion grid onto Bruma')
    f.add_argument('cell')
    f.add_argument('--source-cell', help='Oblivion cell (default: same name)')
    f.add_argument('--authored-cell', required=True)
    f.add_argument('--authored-esm', required=True)
    f.add_argument('--export', default=DEFAULT_EXPORT)
    f.set_defaults(fn=_cmd_fit)
    m = sub.add_parser('move', help='hand-place one node')
    m.add_argument('cell')
    m.add_argument('node', type=int)
    m.add_argument('--to', nargs='*', type=float, default=[],
                   metavar='COORD', help='X Y [Z] in the authored frame')
    m.add_argument('--drop', action='store_true', help='remove this node')
    m.set_defaults(fn=_cmd_move)
    r = sub.add_parser('render', help='review render of the transplanted grid')
    r.add_argument('cell')
    r.add_argument('-o', '--out')
    r.add_argument('--width', type=int, default=1400)
    r.add_argument('--collision', action='store_true',
                   help="draw Bruma's real walls under the grid")
    r.set_defaults(fn=_cmd_render)
    k = sub.add_parser('check', help='list nodes off the authored mesh')
    k.add_argument('cell')
    k.set_defaults(fn=_cmd_check)
    s = sub.add_parser('status', help='list the corpus')
    s.set_defaults(fn=_cmd_status)


def main():
    """CLI: fit, adjust, review and list the transplanted pathgrid corpus."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_args(ap)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
