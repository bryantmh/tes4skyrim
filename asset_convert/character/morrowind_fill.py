"""
Where a Morrowind worn piece's Skyrim skin fill is cut, and seating its armor over it.

The fill is cut exactly along the Morrowind reference body's part borders,
fitted onto the Skyrim body: skin stays where the actor's own uncovered part
would show (`SkinFill.shown`).

See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
See: docs/commentary/asset_convert_armor.md#exact-skin-cut
"""

from functools import lru_cache

import numpy as np
from scipy.spatial import cKDTree

from asset_convert.character.body_wrap import field_path
from asset_convert.character.mesh_cut import LabeledSurface
from asset_convert.character.wrap_mesh import closest_point_on_triangles, group_mean, weld_groups

#: Armor this close to the skin fill's surface is tested for sinking behind it.
_SEAT_REACH = 2.0

#: How far outside the skin fill a seated armor vertex ends.
_SEAT_MARGIN = 0.2


@lru_cache(maxsize=2)
def _reference(female: bool):
    """Morrowind's reference body fitted onto the Skyrim body, labeled by INDX slot, or None."""
    path = field_path(female, True)
    if not path.exists():
        return None
    with np.load(path, allow_pickle=False) as z:
        if 'mw_slot' not in z:
            return None
        return LabeledSurface.from_vertex_labels(z['dst0'], z['tris'], z['mw_slot'])


def skin_field(fill, points, female: bool) -> np.ndarray:
    """Per Skyrim body point: negative where the actor's own skin shows under the piece."""
    reference = _reference(female)
    if reference is None:
        return np.ones(len(points))
    return reference.field(points, fill.shown())


def _vertices(shape) -> np.ndarray:
    """A shape's stored vertices as (n, 3)."""
    return np.array([[v.x, v.y, v.z] for v in shape.data.vertices], dtype=np.float64)


def _surface(shapes) -> tuple:
    """(verts, tris) of `shapes` stacked into one triangle soup."""
    verts, tris, offset = [], [], 0
    for shape in shapes:
        verts.append(_vertices(shape))
        tris.append(np.array([[t.v_1, t.v_2, t.v_3] for t in shape.data.triangles],
                             dtype=np.int64).reshape(-1, 3) + offset)
        offset += len(verts[-1])
    return np.vstack(verts), np.vstack(tris)


def _seat_offsets(points, surface) -> np.ndarray:
    """Per point: the push along the nearest fill normal out to _SEAT_MARGIN, zero if not sunk."""
    verts, tris = surface
    normals = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]], verts[tris[:, 2]] - verts[tris[:, 0]])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    k = min(8, len(tris))
    cand = cKDTree(verts[tris].mean(axis=1)).query(points, k=k)[1].reshape(len(points), k)
    cp = closest_point_on_triangles(points[:, None, :], verts[tris[cand, 0]],
                                    verts[tris[cand, 1]], verts[tris[cand, 2]])
    dist = np.linalg.norm(cp - points[:, None, :], axis=2)
    best = np.argmin(dist, axis=1)
    rows = np.arange(len(points))
    normal = normals[cand[rows, best]]
    signed = np.einsum('pi,pi->p', points - cp[rows, best], normal)
    sunk = (dist[rows, best] < _SEAT_REACH) & (signed < 0.0)
    return normal * ((_SEAT_MARGIN - signed) * sunk)[:, None]


def seat(armor_shapes, fill_shapes) -> int:
    """Push armor vertices sunk behind the skin fill back out over it; how many moved.

    The fill is only skin the actor really shows, so armor under it at a
    seam is clipping; seam twins move alike.
    """
    if not armor_shapes or not fill_shapes:
        return 0
    points = np.vstack([_vertices(s) for s in armor_shapes])
    push = _seat_offsets(points, _surface(fill_shapes))
    wg = weld_groups(points)
    push = group_mean(push, wg, int(wg.max()) + 1)[wg]
    offset = 0
    for shape in armor_shapes:
        n = shape.data.num_vertices
        for v, (x, y, z) in zip(shape.data.vertices, points[offset:offset + n]
                                + push[offset:offset + n]):
            v.x, v.y, v.z = float(x), float(y), float(z)
        offset += n
    return int((np.linalg.norm(push, axis=1) > 0).sum())
