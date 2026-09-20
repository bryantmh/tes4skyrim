"""The Bruma transplant corpus: fitting our pathgrid onto an authored answer key.

Cellview's secondary feature.  Bruma rebuilt Cyrodiil interiors in Skyrim and
navmeshed them BY HAND, so a Bruma cell mirroring an Oblivion one is an answer
key -- but only for the seven fitted cells, which is why this is a panel rather
than the main view.

See: docs/commentary/tes5_import_navmesh.md#authored-navmesh-corpus
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.cellview.bake import CACHE, flatten, our_doors
from tools.navmesh.authored import load_authored, load_authored_full
from tools.navmesh.bruma_collision import cell_collision, default_esm
from tools.navmesh.draw import tri_class
from tools.navmesh.transplant import (
    CORPUS, DEFAULT_EXPORT, apply_fit, index_for, load, occupancy,
    placed_nodes, save,
)


def _generated(entry):
    """`(flat tris, shape verdicts, door tri indices)` for OUR fitted mesh.

    The verdict is `draw.tri_class`, so the page colours our mesh exactly as
    `render.py` does; the editor and the preview must not disagree.

    See: docs/commentary/tes5_import_navmesh.md#renderer-colour-contract
    """
    idx = index_for(entry.get('export', DEFAULT_EXPORT))
    src = idx.cell(entry['source_cell'])
    if src is None or not src.has_pathgrid:
        return [], [], []
    verts, tris = src.build()
    cls = [tri_class(verts, t) for t in tris]
    doors = our_doors(src, verts, tris)
    fit = apply_fit([tuple(p) for p in verts], entry['fit'])
    flat = flatten([(fit[a], fit[b], fit[c]) for (a, b, c) in tris])
    return flat, cls, doors


def _authored_full(entry):
    """`(flat tris, door-flagged tri indices)` for the authored answer key."""
    averts, atris, adoors = load_authored_full(entry['authored_esm'],
                                               entry['authored_cell'])
    return [(averts[a], averts[b], averts[c]) for (a, b, c) in atris], adoors


def cells():
    """Every cell EditorID in the corpus."""
    if not os.path.isdir(CORPUS):
        return []
    return sorted(f[:-5] for f in os.listdir(CORPUS) if f.endswith('.json'))


def _clip_z(tris, zlo, zhi):
    """Collision triangles overlapping the [zlo, zhi] slab."""
    return [t for t in tris
            if min(p[2] for p in t) <= zhi and max(p[2] for p in t) >= zlo]


def _fit_tris(tris, fit):
    """Apply a corpus fit (rotation + offset) to whole triangles."""
    return [tuple(apply_fit([tuple(p) for p in t], fit)) for t in tris]


def _oblivion_collision(entry, zlo, zhi):
    """The SOURCE cell's collision, mapped through the fit into Bruma's frame.

    Showing both sides in one frame makes "which statics moved" a question you
    answer by toggling, rather than from memory.
    """
    idx = index_for(entry.get('export', DEFAULT_EXPORT))
    src = idx.cell(entry['source_cell'])
    if src is None:
        return [], []
    walk, block = src.collision()
    return (_clip_z(_fit_tris(walk, entry['fit']), zlo, zhi),
            _clip_z(_fit_tris(block, entry['fit']), zlo, zhi))


def bake(cell):
    """All geometry the page needs for `cell`, as a JSON-ready dict."""
    if cell in CACHE:
        return CACHE[cell]
    entry = load(cell)
    if entry is None:
        return {'error': 'no corpus entry for %r' % cell}
    nodes, edges = placed_nodes(cell, entry=entry)
    tris, adoors = _authored_full(entry)
    zs = [p[2] for t in tris for p in t] or [0.0]
    lo, hi = min(zs) - 64.0, max(zs) + 160.0
    walk, block = cell_collision(default_esm(), entry['authored_cell'])
    owalk, oblock = _oblivion_collision(entry, lo, hi)
    gen, gen_class, gen_doors = _generated(entry)
    out = {
        'cell': cell,
        'source_cell': entry['source_cell'],
        'authored_cell': entry['authored_cell'],
        'authored': flatten(tris),
        'authored_doors': adoors,
        'generated': gen,
        'generated_class': gen_class,
        'generated_doors': gen_doors,
        'walkable': flatten(_clip_z(walk, lo, hi)),
        'blocking': flatten(_clip_z(block, lo, hi)),
        'ob_walkable': flatten(owalk),
        'ob_blocking': flatten(oblock),
        'nodes': [[round(p[0], 1), round(p[1], 1), round(p[2], 1)]
                  for p in nodes],
        'edges': [list(e) for e in edges],
        'moved': sorted(int(k) for k in entry.get('moved', {})),
        'dropped': sorted(entry.get('dropped', [])),
    }
    CACHE[cell] = out
    return out


def _n_source(entry):
    """How many nodes come from the source grid, before any `added` ones."""
    nodes, _e = placed_nodes(entry['cell'], entry=entry)
    return len(nodes) - len(entry.get('added', []))


def apply_edits(cell, payload):
    """Write every edit kind into the corpus; returns a summary.

    A moved node that is one of the `added` ones is rewritten in place rather
    than pushed into `moved`, which is keyed against the source grid.
    """
    entry = load(cell)
    if entry is None:
        return {'ok': False, 'error': 'no corpus entry for %s' % cell}
    nodes, _e = placed_nodes(cell, entry=entry)
    base = _n_source(entry)
    added = [list(p) for p in entry.get('added', [])]
    added += [[float(p[0]), float(p[1]), float(p[2])]
              for p in (payload.get('added') or [])]
    moved = entry.setdefault('moved', {})
    for k, xyz in (payload.get('moved') or {}).items():
        i = int(k)
        old = nodes[i] if i < len(nodes) else (0.0, 0.0, 0.0)
        p = [float(xyz[0]), float(xyz[1]),
             float(xyz[2]) if len(xyz) > 2 else float(old[2])]
        if i >= base:
            added[i - base] = p
        else:
            moved[str(i)] = p
    entry['added'] = added
    if 'dropped' in payload:
        entry['dropped'] = sorted({int(i) for i in payload['dropped']})
    for key in ('cut_edges', 'added_edges'):
        if key in payload:
            entry[key] = sorted({(min(int(a), int(b)), max(int(a), int(b)))
                                 for a, b in payload[key]})
            entry[key] = [list(e) for e in entry[key]]
    save(cell, entry)
    _refresh_nodes(cell, entry)
    return {'ok': True, 'moved': len(moved), 'added': len(added),
            'dropped': len(entry.get('dropped', []))}


def _refresh_nodes(cell, entry):
    """Update the cached bake's pathgrid in place after a save.

    Dropping the whole entry would re-parse every NIF in the cell on the next
    request -- seconds of work to answer an edit that cannot have changed one
    triangle of collision.
    """
    cached = CACHE.get(cell)
    if cached is None:
        return
    nodes, edges = placed_nodes(cell, entry=entry)
    cached['nodes'] = [[round(p[0], 1), round(p[1], 1), round(p[2], 1)]
                       for p in nodes]
    cached['edges'] = [list(e) for e in edges]
    cached['moved'] = sorted(int(k) for k in entry.get('moved', {}))
    cached['dropped'] = sorted(entry.get('dropped', []))


def _edge_crosses(nodes, a, b, occ, step=32.0):
    """True if edge a-b leaves the authored mesh anywhere along its length."""
    pa, pb = nodes[a], nodes[b]
    d = max(abs(pb[0] - pa[0]), abs(pb[1] - pa[1]))
    n = max(1, int(d / step))
    for i in range(n + 1):
        f = i / float(n)
        x, y = pa[0] + (pb[0] - pa[0]) * f, pa[1] + (pb[1] - pa[1]) * f
        if (int(x // 64.0), int(y // 64.0)) not in occ:
            return True
    return False


def _occ_for(entry):
    """Cached occupancy grid of a cell's authored mesh.

    Rasterising it costs more than the scoring that follows, and it never
    changes while a cell is open -- the answer key is fixed.
    """
    ck = ('occ', entry['authored_cell'])
    if ck not in CACHE:
        averts, atris = load_authored(entry['authored_esm'],
                                      entry['authored_cell'])
        CACHE[ck] = occupancy(averts, atris)
    return CACHE[ck]


def score(cell, edits=None):
    """Live node/edge quality, scoring UNSAVED edits when `edits` is given.

    The page sends its pending state so the readout tracks a drag without a
    round trip through the corpus file.
    """
    entry = load(cell)
    if entry is None:
        return {'error': 'no corpus entry for %r' % cell}
    nodes, edges = placed_nodes(cell, entry=entry)
    dropped = set(entry.get('dropped', []))
    if edits:
        nodes, edges, dropped = _apply_pending(nodes, edges, edits)
    occ = _occ_for(entry)
    off = [i for i, p in enumerate(nodes)
           if i not in dropped
           and (int(p[0] // 64.0), int(p[1] // 64.0)) not in occ]
    bad = [[a, b] for (a, b) in edges
           if a not in dropped and b not in dropped
           and _edge_crosses(nodes, a, b, occ)]
    return {'off_nodes': off, 'bad_edges': bad,
            'n_nodes': len(nodes), 'n_edges': len(edges)}


def _apply_pending(nodes, edges, edits):
    """Overlay the page's unsaved edits on the stored `(nodes, edges)`."""
    nodes = [list(p) for p in nodes]
    for p in (edits.get('added') or []):
        nodes.append([float(p[0]), float(p[1]), float(p[2])])
    for k, xyz in (edits.get('moved') or {}).items():
        i = int(k)
        if 0 <= i < len(nodes):
            nodes[i] = [float(xyz[0]), float(xyz[1]),
                        float(xyz[2]) if len(xyz) > 2 else nodes[i][2]]
    dropped = {int(i) for i in (edits.get('dropped') or [])}
    cut = {(min(int(a), int(b)), max(int(a), int(b)))
           for a, b in (edits.get('cut_edges') or [])}
    out = [(a, b) for (a, b) in edges if (min(a, b), max(a, b)) not in cut]
    have = {(min(a, b), max(a, b)) for (a, b) in out}
    for a, b in (edits.get('added_edges') or []):
        a, b = int(a), int(b)
        k = (min(a, b), max(a, b))
        if k not in have and a < len(nodes) and b < len(nodes):
            have.add(k)
            out.append((a, b))
    return nodes, out, dropped
