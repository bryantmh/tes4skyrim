"""Which placed fixtures the plugin authors items resting INSIDE.

A Morrowind RootCollisionNode is often one closed box over a whole model: a
bookshelf, a table, a wine rack. Morrowind never simulates items, so the books
inside that box sit undisturbed; Skyrim simulates them and ejects them. The
box's shape cannot say whether its volume is open -- a ramp over steps is the
same kind of box -- but the placements can: an item whose origin lies inside a
placed fixture's box proves the author treated that space as open. The
placements come from the plugin AND the plugins that master it, since a patch
can define a shelf that only the plugins it patches place.

The index is too large to ride in the per-task plan, so the parent writes it
beside the record dump and each worker loads it once, on first use.
See: docs/commentary/asset_convert_collision.md#morrowind-stand-in-boxes
"""

import pickle
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from asset_convert.collision.clutter_plan import CLUTTER_TYPES, WEARABLE_TYPES
from asset_convert.nif.fixture_plan import fixture_model_ids, latched_fixture
from core.plugin_masters import masters_from_export_header
from output_layout import record_dir
from tes4_export.morrowind_patch import PATCH_NAME, PATCH_SOURCES
from tes5_import.navmesh.world import rot_matrix

#: Resting-items sub-map key inside the wearable plan: the index file's path.
RESTING_KEY = '*resting_items*'

#: Index file name, written into the plugin's record dump.
_INDEX_NAME = 'resting_items.pkl'

#: REFR export fields of a placement: position, rotation (radians), scale.
_PLACE_FIELDS = ('PosX', 'PosY', 'PosZ', 'RotX', 'RotY', 'RotZ', 'XSCL.Scale')

#: The placement lines of a REFR whose base the index wants.
_REF_LINE = re.compile(r'^(%s)=(.*?)\s*$' % '|'.join(
    re.escape(f) for f in _PLACE_FIELDS), re.M)

#: A record's FormID line.
_FORMID_LINE = re.compile(r'^FormID=([0-9A-Fa-f]{8})\s*$', re.M)

_LOADED: dict = {}


def _placement(rec) -> list:
    """Position, rotation and scale of one REFR record; scale defaults to 1."""
    out = [float(rec.get(key) or 0.0) for key in _PLACE_FIELDS]
    out[6] = out[6] or 1.0
    return out


def _field(chunk: str, name: str) -> str:
    """The raw FormID a record's `name` line holds, upper-cased; '' if absent."""
    at = chunk.find('\n%s=' % name) + len(name) + 2
    return chunk[at:at + 8].upper() if at > len(name) + 1 else ''


def _refs(path: Path):
    """(raw base FormID, raw cell FormID, record text) of each REFR."""
    if path.is_file():
        body = path.read_text(encoding='utf-8', errors='replace')
        for chunk in body.split('---RECORD_BEGIN---')[1:]:
            yield _field(chunk, 'NAME'), _field(chunk, 'ParentCELL'), chunk


def _dependents(export_root, plugin: str) -> list:
    """Record dumps under `export_root` whose header names `plugin` a master.

    The generated Morroblivion patch is a patch OF its source ESMs, so only
    their dumps are read for it.
    """
    root, want = Path(export_root), plugin.lower()
    if want == PATCH_NAME.lower():
        return [d for d in (Path(record_dir(root, s)) for s in PATCH_SOURCES)
                if (d / '_HEADER.txt').is_file()]
    headers = [*root.glob('*/_HEADER.txt'), *root.glob('*/*/_HEADER.txt')]
    return sorted(h.parent for h in headers
                  if want in (m.lower() for m in
                              masters_from_export_header(h.parent)))


def _owners(rec_dir, names: dict) -> list:
    """The plugin each raw FormID index byte of a dump names, own last."""
    owners = [m.lower() for m in masters_from_export_header(rec_dir)]
    owners.append(names.get(Path(rec_dir).resolve(), Path(rec_dir).name.lower()))
    return owners


def _global(owners: list, fid: str):
    """A dump's raw FormID as (owning plugin, object id); '' as None."""
    if not fid:
        return None
    return owners[min(int(fid[:2], 16), len(owners) - 1)], fid[2:].upper()


def _local(owners: list, keys) -> dict:
    """{raw FormID in a dump: global key} for each of `keys` the dump can name."""
    slot = {owner: '%02X' % i for i, owner in enumerate(owners)}
    return {slot[owner] + oid: (owner, oid) for owner, oid in keys if owner in slot}


def _dump_names(export_root, own, plugin: str, dumps: list) -> dict:
    """{resolved dump dir: plugin name} for `own` and every master of `dumps`."""
    names = {Path(own).resolve(): plugin.lower()}
    for d in dumps:
        for m in masters_from_export_header(d):
            names.setdefault(Path(record_dir(export_root, m)).resolve(), m.lower())
    return names


def _item_keys(dumps: list, names: dict) -> set:
    """Global keys of every item record `dumps` or their masters define."""
    keys = set()
    for d in sorted({Path(d).resolve() for d in dumps} | set(names)):
        owners = _owners(d, names)
        for name in CLUTTER_TYPES + WEARABLE_TYPES:
            if (d / name).is_file():
                body = (d / name).read_text(encoding='utf-8', errors='replace')
                keys.update(_global(owners, fid) for fid in _FORMID_LINE.findall(body))
    return keys


def _placements(dumps: list, names: dict, wanted, cells=None):
    """(base global key, cell global key, placement) of each wanted REFR.

    `wanted` holds global base keys; `cells`, when given, the only cells kept.
    """
    for d in dumps:
        owners = _owners(d, names)
        local = _local(owners, wanted)
        for base, cell, chunk in _refs(d / 'REFR.txt'):
            if base in local and (cells is None or _global(owners, cell) in cells):
                yield (local[base], _global(owners, cell),
                       _placement(dict(_REF_LINE.findall(chunk))))


def build_index(export_root, plugin: str) -> dict:
    """Fixture placements that share a cell with an item, and those items.

    Placements are read from `plugin` and every exported plugin mastering it.
    `cells` maps a cell's (plugin, id) to an (N,3) array of item origins;
    `models` maps a mesh-relative NIF path to (cells, (K,7) placements).
    """
    own = Path(record_dir(export_root, plugin))
    dumps = [own, *_dependents(export_root, plugin)]
    names = _dump_names(export_root, own, plugin, dumps)
    owners = _owners(own, names)
    fixtures = {_global(owners, fid): model
                for fid, model in fixture_model_ids(own).items() if fid}
    points, placed = defaultdict(list), defaultdict(list)
    for _base, cell, place in _placements(dumps, names, _item_keys(dumps, names)):
        points[cell].append(place[:3])
    for base, cell, place in _placements(dumps, names, fixtures, points):
        placed[fixtures[base]].append((cell, place))
    models = {model: ([cell for cell, _ in refs],
                      np.array([p for _, p in refs], dtype=np.float64))
              for model, refs in placed.items()}
    cells = {cell: np.array(p, dtype=np.float64) for cell, p in points.items()}
    return {'cells': cells, 'models': models}


def write_index(export_root, plugin: str) -> tuple:
    """Build and write `plugin`'s index beside its records; (path, fixture models).

    Writes nothing and returns (None, 0) when no fixture shares a cell with
    an item.
    """
    index = build_index(export_root, plugin)
    if not index['models']:
        return None, 0
    path = Path(record_dir(export_root, plugin)) / _INDEX_NAME
    with open(path, 'wb') as fh:
        pickle.dump(index, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return str(path), len(index['models'])


def _load(path):
    """The index at `path`, read once per process; None when there is none."""
    if not path:
        return None
    if path not in _LOADED:
        try:
            with open(path, 'rb') as fh:
                _LOADED[path] = pickle.load(fh)
        except OSError:
            _LOADED[path] = None
    return _LOADED[path]


def _outward_planes(tris):
    """(normals, offsets) of a convex hull's faces, normals pointing out."""
    t = np.asarray(tris, dtype=np.float64)
    normals = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    keep = np.linalg.norm(normals, axis=1) > 0
    normals, first = normals[keep], t[keep, 0]
    offsets = np.einsum('ij,ij->i', normals, first)
    inward = normals @ t.reshape(-1, 3).mean(axis=0) - offsets > 0
    normals[inward] *= -1
    offsets[inward] *= -1
    return normals, offsets


def items_rest_inside(tris) -> bool:
    """Whether an authored item's origin lies inside this fixture's hull.

    `tris` is a convex hull in the mesh's root frame, in game units; the
    fixture is the one `fixture_plan` latched for the NIF being converted.
    See: docs/commentary/asset_convert_collision.md#morrowind-stand-in-boxes
    """
    latched = latched_fixture()
    index = _load(latched[1].get(RESTING_KEY)) if latched else None
    entry = index['models'].get(latched[0]) if index else None
    if entry is None:
        return False
    normals, offsets = _outward_planes(tris)
    for cell, (px, py, pz, rx, ry, rz, scale) in zip(*entry):
        pts = index['cells'][cell] - (px, py, pz)
        local = pts @ rot_matrix(rx, ry, rz) / scale
        if np.any(np.all(local @ normals.T < offsets, axis=1)):
            return True
    return False
