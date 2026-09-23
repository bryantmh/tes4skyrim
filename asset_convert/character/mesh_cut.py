"""
Cutting a triangle mesh exactly along a border drawn by a labeled reference body.

A reference body (Oblivion's or Morrowind's) fitted onto the Skyrim body
carries a label per vertex: the body part it belongs to. A Skyrim point's
field value is its distance to the reference triangles inside a label set,
less its distance to the rest, so the zero line is the border between them.
A Skyrim triangle straddling it is split at the edge crossings, so the two
sides meet on that line instead of along a jagged triangle border.

See: docs/commentary/asset_convert_armor.md#exact-skin-cut
"""

from typing import NamedTuple

import numpy as np
from scipy.spatial import cKDTree

from asset_convert.character.wrap_mesh import closest_point_on_triangles

#: Reference triangles examined per point when measuring distance to a label set.
_NEAREST_K = 16


class Cut(NamedTuple):
    """A mesh cut along a field's zero line.

    Crossing vertex `i` lies on edge (a[i], b[i]) at fraction t[i]; it is
    numbered after the source vertices. `tris` covers the whole mesh, `side`
    is True where a triangle lies on the negative side, `src` is the source
    triangle each came from.
    """

    a: np.ndarray
    b: np.ndarray
    t: np.ndarray
    tris: np.ndarray
    side: np.ndarray
    src: np.ndarray

    def lerp(self, values) -> np.ndarray:
        """Per-vertex `values` (n, ...) extended by the crossing vertices."""
        v = np.asarray(values, dtype=np.float64)
        t = self.t.reshape((-1,) + (1,) * (v.ndim - 1))
        return np.concatenate([v, v[self.a] * (1.0 - t) + v[self.b] * t])


def cut(tris, field) -> Cut:
    """Split every triangle of `tris` whose corners straddle the zero of `field`.

    A lone corner keeps its own triangle; the other two get the quad, as two
    triangles in the source winding. A crossing is shared by both triangles
    on its edge.
    """
    tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    f = np.asarray(field, dtype=np.float64)
    neg = f[tris] < 0.0
    count = neg.sum(axis=1)
    whole = (count == 0) | (count == 3)
    mixed = np.flatnonzero(~whole)
    lone_neg = count[mixed] == 1
    lone = np.where(lone_neg, np.argmax(neg[mixed], axis=1), np.argmin(neg[mixed], axis=1))
    rot = np.take_along_axis(tris[mixed], (lone[:, None] + np.arange(3)) % 3, axis=1)
    edges, inv = np.unique(np.vstack([np.sort(rot[:, [0, 1]], axis=1),
                                      np.sort(rot[:, [0, 2]], axis=1)]),
                           axis=0, return_inverse=True)
    inv = inv.ravel() + len(f)
    p1, p2 = inv[:len(mixed)], inv[len(mixed):]
    fa, fb = f[edges[:, 0]], f[edges[:, 1]]
    out = np.vstack([tris[whole], np.column_stack([rot[:, 0], p1, p2]),
                     np.column_stack([p1, rot[:, 1], rot[:, 2]]),
                     np.column_stack([p1, rot[:, 2], p2])])
    side = np.concatenate([count[whole] == 3, lone_neg, ~lone_neg, ~lone_neg])
    src = np.concatenate([np.flatnonzero(whole), mixed, mixed, mixed])
    return Cut(edges[:, 0], edges[:, 1], fa / (fa - fb), out, side, src)


def _distance(points, verts, tris) -> np.ndarray:
    """Per point, the distance to the nearest of `tris`."""
    k = min(_NEAREST_K, len(tris))
    cand = cKDTree(verts[tris].mean(axis=1)).query(points, k=k)[1].reshape(len(points), k)
    cp = closest_point_on_triangles(points[:, None, :], verts[tris[cand, 0]],
                                    verts[tris[cand, 1]], verts[tris[cand, 2]])
    return np.linalg.norm(cp - points[:, None, :], axis=2).min(axis=1)


class LabeledSurface(NamedTuple):
    """A reference body fitted onto the Skyrim body: verts, tris, a label per triangle."""

    verts: np.ndarray
    tris: np.ndarray
    labels: np.ndarray

    @classmethod
    def from_vertex_labels(cls, verts, tris, vertex_labels):
        """A surface whose triangles take their first corner's label."""
        tris = np.asarray(tris, dtype=np.int64)
        return cls(np.asarray(verts, dtype=np.float64), tris,
                   np.asarray(vertex_labels)[tris[:, 0]])

    def subset(self, labels):
        """Only the triangles whose label is in `labels`."""
        keep = np.isin(self.labels, list(labels))
        return LabeledSurface(self.verts, self.tris[keep], self.labels[keep])

    def field(self, points, inside) -> np.ndarray:
        """Per point: distance to the triangles labeled `inside`, less distance to the rest."""
        points = np.asarray(points, dtype=np.float64)
        mask = np.isin(self.labels, list(inside))
        if not mask.any() or mask.all():
            return np.full(len(points), -1.0 if mask.any() else 1.0)
        return (_distance(points, self.verts, self.tris[mask])
                - _distance(points, self.verts, self.tris[~mask]))
