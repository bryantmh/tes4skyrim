"""LOD's view of a plugin: global FormID identity, and the four LOD tables.

The record/GRUP binary layer lives in `tes5_import/base/tes5_reader.py`; this adds
what only the LOD stages need.

Every FormID here is NORMALIZED: the index byte is rewritten to a global one
that names the same file in every plugin.  Raw ids cannot be compared across
plugins -- the index byte is per file -- so without this an overlay merge
treats numeric coincidences as overrides.
See: docs/commentary/tes5_import_override.md#cross-plugin-formid-identity
"""

import math
import struct
from pathlib import Path

from tes5_import.base.tes5_reader import masters, walk, zstr

#: Has Distant LOD -- SSELodGen bakes LOD for this object.
FLAG_DISTANT_LOD = 0x00008000

#: Placement values beyond this are junk, not geometry.
_PLACEMENT_LIMIT = 1e9

#: Base-object signatures that can carry a LOD model.
_LOD_BASE_SIGS = (b'STAT', b'ACTI', b'MSTT', b'TREE')

#: Signatures `parse_esm` collects; everything else is skipped unread.
_WANTED = (b'WRLD', b'CELL', b'REFR') + _LOD_BASE_SIGS

_MASTERS_CACHE: dict = {}

#: Every plugin name seen while normalizing, in first-seen order; append-only.
_GLOBAL_FILES: list = []
_GLOBAL_FILE_IDX: dict = {}

_PARSED_ESM_CACHE: dict = {}


def plugin_masters(esm_path: Path) -> list:
    """The plugin's MAST list, lowercased, in declaration order.

    Slot i of the returned list is what index byte i means inside this file;
    the file itself owns index `len(masters)`.
    """
    key = str(esm_path).lower()
    hit = _MASTERS_CACHE.get(key)
    if hit is None:
        try:
            hit = masters(Path(esm_path).read_bytes())
        except OSError:
            hit = []
        _MASTERS_CACHE[key] = hit
    return hit


def global_file_index(name: str) -> int:
    """Stable small integer for a plugin name, assigned on first sight.

    Process-wide and append-only: an id normalized for one worldspace must
    mean the same thing in the next.
    """
    name = name.lower()
    idx = _GLOBAL_FILE_IDX.get(name)
    if idx is None:
        idx = len(_GLOBAL_FILES)
        _GLOBAL_FILES.append(name)
        _GLOBAL_FILE_IDX[name] = idx
    return idx


def formid_remap_table(esm_path: Path):
    """256-entry table mapping this file's index bytes to GLOBAL index bytes.

    Slot i of the plugin's master list names the file its index byte i refers
    to; anything past the list is the plugin itself.  A local id is only ever
    24 bits, so the high byte is free to re-stamp, and
    `(global_byte << 24) | local_id` is comparable across the load order.
    """
    ms = plugin_masters(esm_path)
    own = Path(esm_path).name.lower()
    return tuple(
        global_file_index(ms[i] if i < len(ms) else own) << 24
        for i in range(256)
    )


def finite(v: float, default: float = 0.0) -> float:
    """Clamp a placement float; NaN and out-of-range both become `default`.

    See: docs/commentary/asset_convert_terrain.md#lodgen-poisoned-floats
    """
    if not math.isfinite(v) or abs(v) > _PLACEMENT_LIMIT:
        return default
    return v


def _worldspace_bounds(mnam: bytes):
    """`(sw_x, sw_y, ne_x, ne_y)` cell corners from a WRLD MNAM."""
    if not mnam or len(mnam) < 16:
        return 0, 0, 0, 0
    nw_x, nw_y, se_x, se_y = struct.unpack_from('<4h', mnam, 8)
    return (min(nw_x, se_x), min(nw_y, se_y),
            max(nw_x, se_x), max(nw_y, se_y))


def _read_worldspace(rec) -> dict:
    """The WRLD fields the LOD stages read."""
    sw_x, sw_y, ne_x, ne_y = _worldspace_bounds(rec.sub(b'MNAM'))
    return {'edid': rec.string(b'EDID'),
            'sw_x': sw_x, 'sw_y': sw_y, 'ne_x': ne_x, 'ne_y': ne_y}


def _read_cell(rec, parent_wrld: int) -> dict:
    """The CELL grid position, or None coordinates for an interior."""
    grid_x = grid_y = None
    xclc = rec.sub(b'XCLC')
    if xclc and len(xclc) >= 8:
        grid_x, grid_y = struct.unpack_from('<2i', xclc)
    return {'parent_wrld': parent_wrld, 'grid_x': grid_x, 'grid_y': grid_y}


def _read_base_object(rec) -> dict:
    """A LOD-capable base object: model, the three LOD meshes, and bounds."""
    lods = [zstr(d) for t, d in rec.subs() if t == b'MNAM'][:3] + [''] * 3
    obnd = rec.sub(b'OBND')
    return {
        'edid': rec.string(b'EDID'),
        'sig': rec.sig.decode('latin-1'),
        'flags': rec.flags,
        'model': rec.string(b'MODL'),
        'obnd': (struct.unpack_from('<6h', obnd)
                 if obnd and len(obnd) >= 12 else None),
        'lod4': lods[0], 'lod8': lods[1], 'lod16': lods[2],
    }


def _read_xesp(xesp: bytes, g):
    """`(parent FormID normalized, flags)` of an XESP enable parent, else None.

    Flags bit 0 = Set Enable State to Opposite of Parent.
    See: docs/commentary/asset_convert_terrain.md#object-lod-selection
    """
    if not xesp or len(xesp) < 8:
        return None
    parent, flags = struct.unpack_from('<II', xesp)
    return g(parent), flags


def _read_ref(rec, fid: int, pw: int, pc: int, g) -> dict:
    """One placed reference, with every float clamped by `finite`.

    NAME points at the placed base object, which very often lives in a MASTER
    -- the most collision-prone field in the merge, and the one deciding which
    mesh a distant object draws. `xesp` is its enable parent (`_read_xesp`).
    """
    subs = rec.sub_map()
    name = subs.get(b'NAME')
    data = subs.get(b'DATA')
    xscl = subs.get(b'XSCL')
    x, y, z, rx, ry, rz = (
        tuple(finite(v) for v in struct.unpack_from('<6f', data))
        if data and len(data) >= 24 else (0.0,) * 6)
    return {
        'form_id': fid, 'flags': rec.flags,
        'base_fid': (g(struct.unpack_from('<I', name)[0])
                     if name and len(name) >= 4 else 0),
        'parent_wrld': pw, 'parent_cell': pc,
        'x': x, 'y': y, 'z': z, 'rx': rx, 'ry': ry, 'rz': rz,
        'scale': (finite(struct.unpack_from('<f', xscl)[0], 1.0)
                  if xscl and len(xscl) >= 4 else 1.0),
        'xesp': _read_xesp(subs.get(b'XESP'), g),
    }


def parse_esm(esm_path: Path):
    """Scan a plugin into `(worldspaces, cells, stats, refs)`.

    worldspaces: {fid: {edid, sw_x, sw_y, ne_x, ne_y}}
    cells:       {fid: {parent_wrld, grid_x, grid_y}}
    stats:       {fid: {edid, sig, flags, model, obnd, lod4, lod8, lod16}}
    refs:        [{form_id, flags, base_fid, parent_wrld, parent_cell,
                   x, y, z, rx, ry, rz, scale, xesp}]

    Every FormID is normalized into the global index space.
    """
    esm_path = Path(esm_path)
    gmap = formid_remap_table(esm_path)
    worldspaces: dict = {}
    cells: dict = {}
    stats: dict = {}
    refs: list = []

    def g(fid: int) -> int:
        """Normalize one FormID into the global space."""
        return gmap[fid >> 24] | (fid & 0x00FFFFFF)

    for rec, stack in walk(esm_path.read_bytes(), *_WANTED):
        fid = g(rec.form_id)
        pw = stack.worldspace
        pw = 0 if pw is None else g(pw)
        if rec.sig == b'WRLD':
            worldspaces[fid] = _read_worldspace(rec)
        elif rec.sig == b'CELL':
            cells[fid] = _read_cell(rec, pw)
        elif rec.sig == b'REFR':
            pc = stack.cell
            refs.append(_read_ref(rec, fid, pw, 0 if pc is None else g(pc), g))
        else:
            stats[fid] = _read_base_object(rec)
    return worldspaces, cells, stats, refs


def parse_esm_cached(esm_path: Path):
    """`parse_esm` memoised on (path, mtime, size).

    The returned structures are treated as READ-ONLY by callers.
    See: docs/commentary/asset_convert_terrain.md#parsed-esm-cache
    """
    esm_path = Path(esm_path)
    try:
        st = esm_path.stat()
        key = (str(esm_path).lower(), st.st_mtime_ns, st.st_size)
    except OSError:
        return parse_esm(esm_path)
    hit = _PARSED_ESM_CACHE.get(key)
    if hit is None:
        hit = parse_esm(esm_path)
        for k in [k for k in _PARSED_ESM_CACHE if k[0] == key[0]]:
            del _PARSED_ESM_CACHE[k]
        _PARSED_ESM_CACHE[key] = hit
    return hit
