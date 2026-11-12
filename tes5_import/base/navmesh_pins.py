"""Hand navmesh corrections that survive the generator: frozen patches and cuts.

A correction in `tests/navmesh_fixed/` is a snapshot keyed by triangle INDEX,
so it decays the moment the generator moves and it is gitignored.  The pin
file is the durable half of the same intent, stored as WORLD POSITIONS: a
frozen patch (`frozen` + `voids`) keeps a human's triangles verbatim, and a
cut removes floor inside a polygon and height band.  Both apply after the
build (`apply_hand_edits`), so neither depends on how the generator works.

    from tes5_import.base.navmesh_pins import hand_edits_for
    edits = hand_edits_for('Nehrim.esm', 'SchattenrufMinePart05')

This module lives OUTSIDE `tes5_import/navmesh/` on purpose: that folder's
bytes are the navmesh cache tag.

See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
"""

import json
import os

from core.navmesh_options import navmesh_pins_dir, set_navmesh_plugin
from tes5_import.base.navmesh_frozen import (
    FROZEN_VERSION, SNAP_TOL, STOREY_BAND, apply_frozen, centroid,
    drop_unused_verts, plane_z,
)

#: Shipped, committable pin files, one per source plugin; a user folder reads over them.
PINS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'navmesh_pins')

#: Every section of a pin file, each `{cell key: [row, ...]}`.
PARTS = ('cuts', 'frozen', 'voids', 'links', 'nodes')

#: Floats per `frozen` / `voids` row: three corners of (x, y, z).
TRI_WIDTH = 9

#: Floats per `nodes` row: one (x, y, z) point the cell's gap flood also starts from.
NODE_WIDTH = 3

#: Floats per `links` row: the upper triangle's three corners, then the lower's.
LINK_WIDTH = 2 * TRI_WIDTH

#: Parsed pin files, keyed by plugin; a missing file caches as {}.
_CACHE = {}


def user_dir():
    """The user's pin folder, or '' when none is set or it IS the shipped one.

    See: docs/commentary/tes5_import_navmesh.md#user-pin-folder
    """
    got = navmesh_pins_dir()
    if not got or (os.path.normcase(os.path.abspath(got))
                   == os.path.normcase(os.path.abspath(PINS))):
        return ''
    return got


def save_dir():
    """The folder new pins are written to: the user's, else the shipped one."""
    return user_dir() or PINS


def pins_path(plugin, folder=None):
    """Path of one source plugin's pin file in `folder` (the shipped one by default)."""
    return os.path.join(folder or PINS, '%s.json' % plugin)


def _read(plugin):
    """Shipped pins with the user's folder read over them.

    A cell the user's file carries in a section replaces the shipped cell in
    that section -- an empty list included, which is how a user unpins a
    shipped patch without editing a file an update would overwrite.
    See: docs/commentary/tes5_import_navmesh.md#user-pin-folder
    """
    doc = _read_file(pins_path(plugin))
    user = user_dir()
    if user:
        for part, cells in _read_file(pins_path(plugin, user)).items():
            doc[part].update(cells)
    return doc


def _read_file(path):
    """One parsed pin file, or an empty document.

    A malformed or absent file answers empty: a pin is an optimisation of
    human intent, never a thing whose absence may abort a conversion.
    """
    out = {part: {} for part in PARTS}
    try:
        with open(path, encoding='utf-8') as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return out
    if not isinstance(got, dict):
        return out
    for part in PARTS:
        section = got.get(part)
        if isinstance(section, dict):
            out[part] = {k: v for k, v in section.items()
                         if isinstance(v, list)}
    return out


def load(plugin):
    """One plugin's whole pin document, read at most once."""
    if plugin not in _CACHE:
        _CACHE[plugin] = _read(plugin)
    return _CACHE[plugin]


def _section(plugin, part, key):
    """One cell's raw entry from `part`, matched case-insensitively."""
    if not plugin or not key:
        return []
    cells = load(plugin).get(part, {})
    got = cells.get(key)
    if got is None:
        want = key.lower()
        for name, val in cells.items():
            if name.lower() == want:
                return val
    return got or []


def use_plugin(plugin, folder):
    """Read `plugin`'s pins this import, renaming a file keyed by its export `folder`.

    See: docs/commentary/tes5_import_navmesh.md#pins-key-on-the-plugin
    """
    set_navmesh_plugin(plugin)
    if not folder or folder == plugin:
        return
    for where in filter(None, (PINS, user_dir())):
        old, new = pins_path(folder, where), pins_path(plugin, where)
        if not os.path.isfile(old):
            continue
        print('    DEPRECATED: navmesh pins keyed by folder %r: %s'
              % (folder, 'ignored, %s wins' % new if os.path.isfile(new)
                 else 'renamed to %s' % new))
        if not os.path.isfile(new):
            os.replace(old, new)
    _CACHE.pop(plugin, None)


def cell_key(cell_rec, wrld_fid=0, grid=None):
    """The key a cell is stored under: its EditorID, else "wrld:FID X Y".

    An exterior CELL has no EditorID.  The generator knows its worldspace only
    as a FormID, so that is what names it -- threading the WRLD EditorID into
    every navmesh worker would be real plumbing for a file nobody reads by eye.

    See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
    """
    edid = (cell_rec or {}).get('EditorID') or ''
    if edid:
        return edid
    if grid is None or not wrld_fid:
        return ''
    return 'wrld:%06X %d %d' % (wrld_fid & 0x00FFFFFF, grid[0], grid[1])


def cuts_for(plugin, key):
    """`[(zmin, zmax, [(x, y), ...]), ...]` regions to strip from one cell's navmesh.

    A row is `[zmin, zmax, x1, y1, x2, y2, x3, y3, ...]`: a polygon of at
    least three corners in world XY plus the height band it applies to.
    See: docs/commentary/tes5_import_navmesh.md#cut-pins
    """
    out = []
    for row in _section(plugin, 'cuts', key):
        if len(row) >= 8 and len(row) % 2 == 0:
            vals = [float(c) for c in row]
            out.append((vals[0], vals[1], list(zip(vals[2::2], vals[3::2]))))
    return out


def _tri(row):
    """The first three (x, y, z) corners of a flat row."""
    return tuple(tuple(float(c) for c in row[k:k + 3]) for k in (0, 3, 6))


def tris_for(plugin, part, key):
    """`[((x,y,z), (x,y,z), (x,y,z)), ...]` of one cell's `frozen` or `voids` rows.

    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    return [_tri(r) for r in _section(plugin, part, key) if len(r) >= TRI_WIDTH]


def links_for(plugin, key):
    """`[(upper triangle, lower triangle), ...]` of one cell's pinned drop links.

    See: docs/commentary/tes5_import_navmesh.md#pinned-drop-links
    """
    return [(_tri(r), _tri(r[TRI_WIDTH:]))
            for r in _section(plugin, 'links', key) if len(r) >= LINK_WIDTH]


def nodes_for(plugin, key):
    """`[(x, y, z), ...]` pinned nodes one cell's gap flood also starts from."""
    return [tuple(float(c) for c in r[:NODE_WIDTH]) for r in _section(plugin, 'nodes', key)
            if len(r) >= NODE_WIDTH]


def hand_edits_for(plugin, key):
    """Every correction a human committed for one cell, by section name."""
    return {'cuts': cuts_for(plugin, key),
            'frozen': tris_for(plugin, 'frozen', key),
            'voids': tris_for(plugin, 'voids', key),
            'links': links_for(plugin, key),
            'nodes': nodes_for(plugin, key)}


#: A pin point closer than this to its cell's edge can change what the neighbour across it is built from.
EDGE_REACH = 64.0


def reaches_edge(edits, grid) -> bool:
    """True when an exterior cell's `edits` come within EDGE_REACH of its edge or cross it; a pin that
    does not can change only its own cell. An interior (`grid` None) has no edge."""
    if grid is None:
        return False
    x0, y0 = grid[0] * 4096.0 + EDGE_REACH, grid[1] * 4096.0 + EDGE_REACH
    x1, y1 = x0 + 4096.0 - 2 * EDGE_REACH, y0 + 4096.0 - 2 * EDGE_REACH
    pts = [p for _lo, _hi, poly in edits.get('cuts') or () for p in poly]
    pts += [c for part in ('frozen', 'voids') for t in edits.get(part) or () for c in t]
    pts += [c for pair in edits.get('links') or () for t in pair for c in t]
    return any(not (x0 < p[0] < x1 and y0 < p[1] < y1) for p in pts)


def build_pins(edits, grid):
    """The part of `edits` a build must hold rather than lay on top afterwards: all of them when they
    reach the cell's edge, else only the pinned flood nodes (their flood seeds the neighbours); None when
    neither."""
    if reaches_edge(edits, grid):
        return edits
    if edits.get('nodes'):
        return dict({part: [] for part in edits}, nodes=edits['nodes'])
    return None


def apply_hand_edits(verts, tris, ledges, edits):
    """(verts, tris, ledges) after the post-build corrections: cuts, then frozen patches.

    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    edits = edits or {}
    verts, tris, ledges = apply_cuts(verts, tris, ledges, edits.get('cuts'))
    return apply_frozen(verts, tris, ledges, edits.get('frozen') or [],
                        edits.get('voids') or [], edits.get('links') or [])


def _inside(x, y, poly):
    """True when (x, y) lies inside the polygon (even-odd rule)."""
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _is_cut(verts, tri, cuts):
    """True when the triangle's centroid falls inside any cut region and band."""
    cx, cy, cz = (sum(verts[i][k] for i in tri[:3]) / 3.0 for k in range(3))
    return any(zmin <= cz <= zmax and _inside(cx, cy, poly)
               for zmin, zmax, poly in cuts)


def apply_cuts(verts, tris, ledges, cuts):
    """(verts, tris, ledges) with every cut triangle removed and indices compacted.

    Ledge links naming a removed triangle are dropped with it.
    See: docs/commentary/tes5_import_navmesh.md#cut-pins
    """
    if not cuts or not tris:
        return verts, tris, ledges
    keep = [i for i, t in enumerate(tris) if not _is_cut(verts, t, cuts)]
    if len(keep) == len(tris):
        return verts, tris, ledges
    tri_map = {old: new for new, old in enumerate(keep)}
    new_ledges = [(tri_map[u], tri_map[l]) + tuple(rest)
                  for (u, l, *rest) in ledges or ()
                  if u in tri_map and l in tri_map]
    new_verts, new_tris = drop_unused_verts(verts, [tris[i] for i in keep])
    return new_verts, new_tris, new_ledges


def digest(plugin, key):
    """A stable string for `geom_hash`, so pinning one cell restages only it.

    Empty when the cell has no cuts or patches, which keeps every unpinned
    cell's hash exactly what it was before pins existed.
    """
    parts = ['C%.2f,%.2f:' % (zmin, zmax)
              + ';'.join('%.2f,%.2f' % p for p in poly)
              for (zmin, zmax, poly) in cuts_for(plugin, key)]
    for part in ('frozen', 'voids'):
        parts += [part[0].upper() + ';'.join('%.2f,%.2f,%.2f' % p for p in t)
                  for t in tris_for(plugin, part, key)]
    parts += ['L' + ';'.join('%.2f,%.2f,%.2f' % p for p in up + down)
              for up, down in links_for(plugin, key)]
    parts += ['N%.2f,%.2f,%.2f' % p for p in nodes_for(plugin, key)]
    if any(tris_for(plugin, part, key) for part in ('frozen', 'voids')):
        parts.append('V%d' % FROZEN_VERSION)
    return '|'.join(parts)


def _rounded(rows, width):
    """`rows` as plain lists of `width` floats at 0.01u, dropping short ones."""
    return [[round(float(c), 2) for c in r[:width]]
            for r in rows or () if len(r) >= width]


def _flat(points):
    """A sequence of (x, y, z) points as one flat row."""
    return [float(c) for p in points for c in p[:3]]


def _write(plugin, doc, folder):
    """Write one plugin's pin document into `folder` and drop its cached read."""
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = pins_path(plugin, folder)
    body = {'plugin': plugin}
    body.update({part: doc[part] for part in PARTS})
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(body, fh, indent=1, sort_keys=True)
        fh.write('\n')
    _CACHE.pop(plugin, None)
    return path


def save(plugin, key, frozen=None, voids=None, links=None, nodes=None):
    """Replace one cell's frozen patch, pinned links and pinned flood nodes in the save folder's file.

    A section passed as None is left alone; an empty one clears the cell from
    it (kept as an empty override where a shipped pin exists).  `frozen` and
    `voids` are triangles of three points; `links` are (upper, lower) pairs
    of them; `nodes` are points.  Values round to 0.01u so float noise never
    churns the diff.  Returns `(path, {section: rows now in effect})`.
    """
    folder = save_dir()
    doc = _read_file(pins_path(plugin, folder))
    shipped = _read_file(pins_path(plugin)) if folder != PINS else None
    flat_links = None if links is None else [tuple(u) + tuple(d) for u, d in links]
    flat_nodes = None if nodes is None else [(p,) for p in nodes]
    for part, rows, width in (('frozen', frozen, TRI_WIDTH),
                              ('voids', voids, TRI_WIDTH),
                              ('links', flat_links, LINK_WIDTH),
                              ('nodes', flat_nodes, NODE_WIDTH)):
        if rows is None:
            continue
        got = _rounded([_flat(t) for t in rows], width)
        if got or (shipped is not None and key in shipped[part]):
            doc[part][key] = got
        else:
            doc[part].pop(key, None)
    path = _write(plugin, doc, folder)
    return path, {part: len(_section(plugin, part, key)) for part in PARTS}


def _touches(a, b):
    """True when two patch triangles share a corner (to 0.5u in plan, same storey)."""
    return any((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= SNAP_TOL ** 2
               and abs(p[2] - q[2]) <= STOREY_BAND for p in a for q in b)


def _holds(tri, point):
    """True when `point` is inside `tri` in plan at its storey, or on one of its corners."""
    x, y, z = point[:3]
    if any((p[0] - x) ** 2 + (p[1] - y) ** 2 <= 4.0 and abs(p[2] - z) <= STOREY_BAND
           for p in tri):
        return True
    return (_inside(x, y, [p[:2] for p in tri])
            and abs(plane_z(tri, x, y) - z) <= STOREY_BAND)


def _corner_buckets(rows):
    """`{1u plan bucket: {row index}}` over every corner of every row."""
    out = {}
    for i, t in enumerate(rows):
        for p in t:
            out.setdefault((int(p[0] // 1.0), int(p[1] // 1.0)), set()).add(i)
    return out


def _rows_near(buckets, p):
    """Row indices with a corner in the 3x3 buckets around point `p`."""
    bx, by = int(p[0] // 1.0), int(p[1] // 1.0)
    return {j for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            for j in buckets.get((bx + dx, by + dy), ())}


def patch_at(rows, point):
    """Indices of `rows` in the patch holding `point`: every triangle joined to it by corners."""
    buckets = _corner_buckets(rows)
    todo = [i for i, t in enumerate(rows) if _holds(t, point)]
    seen = set(todo)
    while todo:
        i = todo.pop()
        near = set().union(*(_rows_near(buckets, p) for p in rows[i]))
        for j in near - seen:
            if _touches(rows[i], rows[j]):
                seen.add(j)
                todo.append(j)
    return seen


def remove_patch(plugin, key, point=None):
    """Unpin the frozen patch holding `point`, or every patch in the cell when None.

    A pinned link with either triangle on a removed patch goes with it.
    Returns `(path, frozen rows removed, void rows removed)`.
    See: docs/commentary/tes5_import_navmesh.md#frozen-navmesh-patches
    """
    frozen = tris_for(plugin, 'frozen', key)
    voids = tris_for(plugin, 'voids', key)
    nf = len(frozen)
    gone = (set(range(nf + len(voids))) if point is None
            else patch_at(frozen + voids, point))
    keep_f = [t for i, t in enumerate(frozen) if i not in gone]
    keep_v = [t for i, t in enumerate(voids) if i + nf not in gone]
    lost = [t for i, t in enumerate(frozen) if i in gone]
    keep_l = [pair for pair in links_for(plugin, key)
              if not any(_holds(t, centroid(end)) for t in lost for end in pair)]
    path, _n = save(plugin, key, frozen=keep_f, voids=keep_v, links=keep_l)
    return path, nf - len(keep_f), len(voids) - len(keep_v)


#: A click within this of a pinned node (in plan) removes that node.
NODE_PICK = 64.0


def add_node(plugin, key, point):
    """Pin one flood node at `point` (x, y, z); returns `(path, nodes now pinned)`."""
    nodes = nodes_for(plugin, key) + [tuple(float(c) for c in point[:3])]
    path, _n = save(plugin, key, nodes=nodes)
    return path, len(nodes)


def remove_node(plugin, key, point=None):
    """Unpin the flood node nearest `point` within NODE_PICK in plan, or every node when None.

    Returns `(path, nodes removed)`.
    """
    nodes = nodes_for(plugin, key)
    drop = set(range(len(nodes)))
    if point is not None:
        near = sorted(((p[0] - point[0]) ** 2 + (p[1] - point[1]) ** 2, i) for i, p in enumerate(nodes))
        drop = {near[0][1]} if near and near[0][0] <= NODE_PICK ** 2 else set()
    keep = [p for i, p in enumerate(nodes) if i not in drop]
    path, _n = save(plugin, key, nodes=keep)
    return path, len(nodes) - len(keep)
