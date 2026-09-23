"""
Which Skyrim body sections a Morrowind wearable hides, and where its skin still shows.

Morrowind draws the actor's skin part in every body-part slot no equipped item
fills; Skyrim hides whole body partitions per ARMA slot and expects the armor
to carry any skin left showing inside them. The record's `MorrowindPart[i].Slot`
list is the authored coverage: the ARMA claims the partitions those parts sit
in, and the worn mesh carries Skyrim skin only where Morrowind would draw the
actor's own skin part -- a part the record does not list, inside a hidden
partition.

See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
"""

from typing import NamedTuple

import numpy as np
from scipy.spatial import cKDTree

#: INDX body-part slot -> the Skyrim body partitions its skin overlaps, measured on malebody_0.
PART_PARTITIONS = {2: (32,), 3: (32,), 4: (44,), 5: (44,), 6: (33,), 7: (33,),
                   8: (34,), 9: (34,), 11: (32, 34), 12: (32, 34), 13: (32,),
                   14: (32,), 15: (37,), 16: (37,), 17: (38,), 18: (38,),
                   19: (38,), 20: (38,), 21: (44,), 22: (44,), 23: (32,),
                   24: (32,)}

#: Every Skyrim body partition a part can overlap.
BODY_PARTITIONS = frozenset(p for parts in PART_PARTITIONS.values() for p in parts)

#: Biped slot of BOD2 bit 0.
_FIRST_SLOT = 30

#: female -> (KD-tree of the reference body fitted onto Skyrim's, its part label per vertex).
_LABELS: dict = {}

#: Armor this close to the skin fill's surface is tested for sinking behind it.
_SEAT_REACH = 2.0

#: How far outside the skin fill a seated armor vertex ends.
_SEAT_MARGIN = 0.2


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


def _seat_offsets(points, surface, closest_point_on_triangles) -> np.ndarray:
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


def part_slots(rec: dict) -> list:
    """The INDX slots a Morrowind wearable record fills; empty for any other record."""
    count = int(rec.get('MorrowindPartCount', '0') or 0)
    return [int(rec[f'MorrowindPart[{i}].Slot']) for i in range(count)]


def covered_partitions(slots) -> frozenset:
    """The Skyrim body partitions the parts in `slots` overlap."""
    return frozenset(p for slot in slots for p in PART_PARTITIONS.get(slot, ()))


def coverage_bits(slots) -> int:
    """BOD2 bits of the body partitions the parts in `slots` overlap."""
    bits = 0
    for partition in covered_partitions(slots):
        bits |= 1 << (partition - _FIRST_SLOT)
    return bits


def _part_labels(female: bool):
    """(KD-tree over the fitted reference body, part label per vertex), or (None, None).

    body_wrap is imported here because it imports wearable_plan, which
    imports this module.
    """
    if female not in _LABELS:
        from asset_convert.character.body_wrap import field_path
        path = field_path(female, True)
        _LABELS[female] = (None, None)
        if path.exists():
            with np.load(path, allow_pickle=False) as z:
                if 'mw_slot' in z:
                    _LABELS[female] = (cKDTree(z['dst0'].astype(np.float64)),
                                       z['mw_slot'].astype(np.int64))
    return _LABELS[female]


class SkinFill(NamedTuple):
    """A Morrowind worn piece's skin fill: the partitions its ARMA hides, the parts it covers."""

    partitions: frozenset
    covered: frozenset

    def keep(self, points, female: bool) -> np.ndarray:
        """Per Skyrim body point: whether the actor's own skin shows there under this piece.

        The point's part is the nearest vertex of the Morrowind reference body
        fitted onto the Skyrim body. Skin shows where that part is not covered
        and belongs to a hidden partition, so a chest-only cuirass never fills
        the thighs even on a body whose partition 32 still holds them.
        """
        tree, labels = _part_labels(female)
        if tree is None:
            return np.zeros(len(points), dtype=bool)
        shown = [slot for slot, parts in PART_PARTITIONS.items()
                 if slot not in self.covered and self.partitions.intersection(parts)]
        return np.isin(labels[tree.query(points)[1]], shown)

    def seat(self, armor_shapes, fill_shapes) -> int:
        """Push armor vertices sunk behind the skin fill back out over it; how many moved.

        The fill is only skin the actor really shows, so armor under it at a
        seam is clipping; seam twins move alike. wrap_mesh is imported here
        because it imports skin_retarget, which imports wearable_plan, which
        imports this module.
        See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
        """
        from asset_convert.character.wrap_mesh import (closest_point_on_triangles,
                                                       group_mean, weld_groups)
        if not armor_shapes or not fill_shapes:
            return 0
        points = np.vstack([_vertices(s) for s in armor_shapes])
        push = _seat_offsets(points, _surface(fill_shapes), closest_point_on_triangles)
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
