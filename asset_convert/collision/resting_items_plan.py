"""Which placed fixtures the plugin authors items resting INSIDE.

A Morrowind RootCollisionNode is often one closed box over a whole model: a
bookshelf, a table, a wine rack. Morrowind never simulates items, so the books
inside that box sit undisturbed; Skyrim simulates them and ejects them. The
box's shape cannot say whether its volume is open -- a ramp over steps is the
same kind of box -- but the plugin's placements can: an item whose origin lies
inside a placed fixture's box proves the author treated that space as open.

The index is too large to ride in the per-task plan, so the parent writes it
beside the record dump and each worker loads it once, on first use.
See: docs/commentary/asset_convert_collision.md#morrowind-stand-in-boxes
"""

import pickle
from collections import defaultdict
from pathlib import Path

import numpy as np

from asset_convert.character.wearable_plan import iter_records
from asset_convert.collision.clutter_plan import CLUTTER_TYPES, WEARABLE_TYPES
from asset_convert.nif.fixture_plan import fixture_model_ids, latched_fixture
from tes5_import.navmesh.world import rot_matrix

#: Resting-items sub-map key inside the wearable plan: the index file's path.
RESTING_KEY = '*resting_items*'

#: Index file name, written into the plugin's record dump.
_INDEX_NAME = 'resting_items.pkl'

#: REFR export fields of a placement: position, rotation (radians), scale.
_PLACE_FIELDS = ('PosX', 'PosY', 'PosZ', 'RotX', 'RotY', 'RotZ', 'XSCL.Scale')

_LOADED: dict = {}


def _placement(rec) -> list:
    """Position, rotation and scale of one REFR record; scale defaults to 1."""
    out = [float(rec.get(key) or 0.0) for key in _PLACE_FIELDS]
    out[6] = out[6] or 1.0
    return out


def build_index(export_dir) -> dict:
    """Fixture placements that share a cell with an item, and those items.

    `cells` maps a cell to an (N,3) array of item origins; `models` maps a
    mesh-relative NIF path to (cells, (K,7) array of placements).
    """
    export_dir = Path(export_dir)
    fixtures = fixture_model_ids(export_dir)
    items = {rec.get('FormID') for name in CLUTTER_TYPES + WEARABLE_TYPES
             for rec in iter_records(export_dir / name)}
    points, placed = defaultdict(list), defaultdict(list)
    for rec in iter_records(export_dir / 'REFR.txt'):
        base, cell = rec.get('NAME'), rec.get('ParentCELL')
        if base in items:
            points[cell].append(_placement(rec)[:3])
        elif base in fixtures:
            placed[fixtures[base]].append((cell, _placement(rec)))
    cells = {cell: np.array(p, dtype=np.float64) for cell, p in points.items()}
    models = {}
    for model, refs in placed.items():
        refs = [(cell, p) for cell, p in refs if cell in cells]
        if refs:
            models[model] = ([cell for cell, _ in refs],
                             np.array([p for _, p in refs], dtype=np.float64))
    return {'cells': cells, 'models': models}


def write_index(export_dir) -> tuple:
    """Build and write the index into `export_dir`; (path, fixture models).

    Writes nothing and returns (None, 0) when no fixture shares a cell with
    an item.
    """
    index = build_index(export_dir)
    if not index['models']:
        return None, 0
    path = Path(export_dir) / _INDEX_NAME
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
