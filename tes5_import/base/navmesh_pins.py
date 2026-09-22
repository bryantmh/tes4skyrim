"""Hand-pinned navmesh floor: triangles the generator must never cut away.

A correction in `tests/navmesh_fixed/` is a snapshot keyed by triangle INDEX,
so it decays the moment the generator moves and it is gitignored.  A pin is the
durable half of the same intent: the WORLD POSITIONS a human declared walkable,
which survive any retriangulation and are small enough to commit and review.

    from tes5_import.base.navmesh_pins import pins_for
    pts = pins_for('Oblivion.esm', 'ImperialDungeon02')

Pins ride `corridor_clean.finalize`'s existing `pin_xy` mechanism.  They protect
existing floor; they do not make the generator REACH ground it never grew.

This module lives OUTSIDE `tes5_import/navmesh/` on purpose: that folder's
bytes are the navmesh cache tag.

See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
"""

import json
import os

#: Committable pin files, one per source plugin.
PINS = 'navmesh_pins'

#: Parsed pin files, keyed by plugin; a missing file caches as {}.
_CACHE = {}

#: How near a generated vertex must be to a weld endpoint to BE that endpoint.
WELD_TOLERANCE = 8.0


def pins_path(plugin):
    """Path of the pin file for one source plugin."""
    return os.path.join(PINS, '%s.json' % plugin)


def _read(plugin):
    """The parsed pin document on disk, or an empty one.

    A malformed or absent file answers empty: a pin is an optimisation of
    human intent, never a thing whose absence may abort a conversion.
    """
    out = {'cells': {}, 'welds': {}}
    try:
        with open(pins_path(plugin), encoding='utf-8') as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return out
    if not isinstance(got, dict):
        return out
    for part in ('cells', 'welds'):
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


def plugin_of(geom_cache_dir):
    """Plugin owning a `<export>/<plugin>/navmesh_geom_cache` dir, any spelling.

    See: docs/commentary/tes5_import_navmesh.md#mixed-separators-lost-the-pins
    """
    flat = str(geom_cache_dir or '').replace('\\', '/').rstrip('/')
    return flat.rsplit('/', 2)[-2] if '/' in flat else ''


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


def pins_for(plugin, key):
    """`[(x, y, z), ...]` of floor pinned walkable in one cell, or `[]`."""
    return [tuple(float(c) for c in p[:3])
            for p in _section(plugin, 'cells', key) if len(p) >= 3]


def welds_for(plugin, key):
    """`[((x,y,z) from, (x,y,z) to), ...]` cracks to close in one cell.

    A crack closes only when two triangles SHARE a vertex index, so a weld
    names the two PLACES whose vertices must become one -- a position pair
    survives regeneration where the editor's index pair cannot.

    See: docs/commentary/tes5_import_navmesh.md#pinned-navmesh-floor
    """
    out = []
    for w in _section(plugin, 'welds', key):
        if len(w) >= 6:
            out.append((tuple(float(c) for c in w[:3]),
                        tuple(float(c) for c in w[3:6])))
    return out


def digest(plugin, key):
    """A stable string for `geom_hash`, so pinning one cell restages only it.

    Empty when the cell has neither pins nor welds, which keeps every
    unpinned cell's hash exactly what it was before pins existed.
    """
    parts = ['%.2f,%.2f,%.2f' % p for p in pins_for(plugin, key)]
    parts += ['W%.2f,%.2f,%.2f>%.2f,%.2f,%.2f' % (a + b)
              for (a, b) in welds_for(plugin, key)]
    return '|'.join(parts)


def _rounded(rows, width):
    """`rows` as plain lists of `width` floats at 0.01u, dropping short ones."""
    return [[round(float(c), 2) for c in r[:width]]
            for r in rows or () if len(r) >= width]


def save(plugin, key, points, welds=()):
    """Replace one cell's pins and welds, creating the file on first use.

    Returns `(path, pin count, weld count)`.  Values are rounded to 0.01u: a
    pin is a place, and float noise would churn a committed file's diff.
    """
    if not os.path.isdir(PINS):
        os.makedirs(PINS)
    doc = _read(plugin)
    for (part, rows, width) in (('cells', points, 3),
                                ('welds', _flat_welds(welds), 6)):
        got = _rounded(rows, width)
        if got:
            doc[part][key] = got
        else:
            doc[part].pop(key, None)
    path = pins_path(plugin)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({'plugin': plugin, 'cells': doc['cells'],
                   'welds': doc['welds']}, fh, indent=1, sort_keys=True)
        fh.write('\n')
    _CACHE.pop(plugin, None)
    return path, len(doc['cells'].get(key, ())), len(doc['welds'].get(key, ()))


def _flat_welds(welds):
    """`[(from, to)]` pairs as flat 6-value rows."""
    return [list(a[:3]) + list(b[:3]) for (a, b) in welds or ()]
