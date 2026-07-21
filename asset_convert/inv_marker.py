"""BSInvMarker rotation computation — orient the inventory 3D view so the
side of the mesh with the most visible surface faces the camera.

Skyrim's inventory viewer applies the marker rotation to the model and looks
at it along +Y (screen-right = X, screen-up = Z).  The stored ushort angles
are milliradians and the engine composes them as

    M_game = Rx(-rx/1000) @ Ry(-ry/1000) @ Rz(-rz/1000)

(column-vector convention).  This was derived empirically with
tools/inv_marker_survey.py: across vanilla Skyrim meshes that convention
turns each mesh's largest-visible-area side toward +Y with mean alignment
0.97+ (and reproduces vanilla markers exactly on the canonical cases:
ironshield.nif (4712,0,0), cuirassgnd (1570,0,0), iron weapons Rx=4712).

Orientation choice:
- View normal n: of the six principal-axis directions (area-weighted PCA of
  the triangle soup), the one with the greatest front-facing projected area.
  This is "the side that shows the most of the mesh".
- Screen roll: keep the model's own up-axis (+Z) pointing to screen-up, so
  upright items (bottles, statuettes) don't display sideways or upside down.
  When n is (anti)parallel to +Z — items modeled lying flat like books,
  plates and armor ground models — vanilla lays "screen-up" along model -Y
  for face-up items (cuirassgnd) and +Y for face-down ones; we do the same.
"""

from __future__ import annotations

import numpy as np

TWO_PI = 2.0 * np.pi

# Geometry names that are invisible in the inventory view and must not sway
# the orientation choice (weapon blood decals, editor helpers).
_IGNORED_NAME_PREFIXES = (b'blood', b'editormarker')


def _gather_area_normals(root):
    """Collect (tri_normals Nx3, tri_areas N, tri_centroids Nx3) of all
    visible render geometry in root space.

    Uses PyFFI's row-vector transform convention throughout — the same math
    the convention survey validated end-to-end against vanilla markers.
    """
    normals, areas, centroids = [], [], []
    for block in root.tree():
        if type(block).__name__ not in ('NiTriShape', 'NiTriStrips'):
            continue
        if getattr(block, 'flags', 0) & 1:          # APP_CULLED / hidden
            continue
        name = bytes(getattr(block, 'name', b'') or b'').rstrip(b'\x00').lower()
        if name.startswith(_IGNORED_NAME_PREFIXES):
            continue
        gd = block.data
        # Trust the array length, not num_vertices: some Oblivion meshes carry a
        # stale count with has_vertices unset, so vertices is empty while
        # num_vertices is nonzero (leyawiinhouselower01_far.nif) — that built a
        # (0,) array and blew up the matmul below.
        if gd is None or len(gd.vertices) == 0:
            continue
        try:
            tf = block.get_transform(root)
        except ValueError:
            tf = block.get_transform()
        m = np.array([[tf.m_11, tf.m_12, tf.m_13],
                      [tf.m_21, tf.m_22, tf.m_23],
                      [tf.m_31, tf.m_32, tf.m_33]])
        t = np.array([tf.m_41, tf.m_42, tf.m_43])
        verts = np.array([[v.x, v.y, v.z] for v in gd.vertices]) @ m + t
        tris = np.array([[a, b, c] for a, b, c in gd.get_triangles()],
                        dtype=np.int64)
        if len(tris) == 0:
            continue
        e1 = verts[tris[:, 1]] - verts[tris[:, 0]]
        e2 = verts[tris[:, 2]] - verts[tris[:, 0]]
        n = np.cross(e1, e2)
        ln = np.linalg.norm(n, axis=1)
        keep = ln > 1e-12
        if not keep.any():
            continue
        tris, n, ln = tris[keep], n[keep], ln[keep]
        tri_n = n / ln[:, None]
        # Align winding-derived normals with authored vertex normals: winding
        # conventions vary between meshes, vertex normals are ground truth
        # for which side of a face is "outside".
        if gd.has_normals and len(gd.normals) == len(gd.vertices):
            vn = np.array([[v.x, v.y, v.z] for v in gd.normals]) @ m
            tvn = vn[tris[:, 0]] + vn[tris[:, 1]] + vn[tris[:, 2]]
            flip = np.einsum('ij,ij->i', tri_n, tvn) < 0
            tri_n[flip] = -tri_n[flip]
        normals.append(tri_n)
        areas.append(ln * 0.5)
        centroids.append((verts[tris[:, 0]] + verts[tris[:, 1]]
                          + verts[tris[:, 2]]) / 3.0)
    if not normals:
        return None
    return (np.concatenate(normals), np.concatenate(areas),
            np.concatenate(centroids))


def _visible_area(tri_n, tri_a, d):
    """Front-facing projected area seen by a camera in direction d (unit
    vector pointing from the object toward the camera)."""
    return float(np.sum(tri_a * np.clip(tri_n @ d, 0.0, None)))


def _principal_axes(tri_a, tri_c):
    """Area-weighted principal axes of the triangle centroids, most→least
    variance.  Rows of the returned 3x3 are unit axes."""
    w = tri_a / tri_a.sum()
    mean = w @ tri_c
    d = tri_c - mean
    cov = (d * w[:, None]).T @ d
    evals, evecs = np.linalg.eigh(cov)      # ascending
    return evecs.T[::-1]                     # rows, descending variance


def _euler_from_view_matrix(m):
    """Decompose view rotation M = Rx(a) @ Ry(b) @ Rz(c) (column-vector)."""
    sb = float(np.clip(m[0, 2], -1.0, 1.0))
    b = np.arcsin(sb)
    if abs(sb) > 0.999999:                   # gimbal: a and c degenerate
        return float(np.arctan2(m[1, 0], m[1, 1])), b, 0.0
    c = float(np.arctan2(-m[0, 1], m[0, 0]))
    a = float(np.arctan2(-m[1, 2], m[2, 2]))
    return a, b, c


def _to_marker_units(angle):
    """Model-rotation angle → stored ushort milliradians (negated, mod 2pi)."""
    return int(round(((-angle) % TWO_PI) * 1000.0)) % 6284


def rotation_for_view(n, up_hint=None):
    """Marker (rx, ry, rz) ushort milliradians for a view rotation that turns
    model-space unit direction ``n`` toward the inventory camera (+Y) with
    ``up_hint`` (model space) as close to screen-up (+Z) as possible."""
    n = np.asarray(n, dtype=float)
    n = n / np.linalg.norm(n)
    if up_hint is None:
        up_hint = np.array([0.0, 0.0, 1.0])
    u = up_hint - (up_hint @ n) * n
    lu = np.linalg.norm(u)
    if lu < 0.25:
        # n is (anti)parallel to model +Z: item modeled lying flat.  Vanilla
        # convention (cuirassgnd 1570,0,0): face-up items put model -Y at
        # screen-up; face-down items +Y.
        y = np.array([0.0, -1.0, 0.0]) if n[2] > 0 else np.array([0.0, 1.0, 0.0])
        u = y - (y @ n) * n
        lu = np.linalg.norm(u)
    u /= lu
    # M rows (r, n, u) map model r->screen X, n->camera +Y, u->screen up +Z.
    m = np.array([np.cross(n, u), n, u])
    a, b, c = _euler_from_view_matrix(m)
    return _to_marker_units(a), _to_marker_units(b), _to_marker_units(c)


def compute_inv_rotation(root):
    """Compute (rx, ry, rz) ushort milliradians for the finished (converted)
    NIF tree under ``root`` so its most-visible side faces the inventory
    camera.  Returns None when there is no usable render geometry."""
    geo = _gather_area_normals(root)
    if geo is None:
        return None
    tri_n, tri_a, tri_c = geo
    if tri_a.sum() < 1e-9:
        return None
    axes = _principal_axes(tri_a, tri_c)
    best_n, best_area = None, -1.0
    for axis in axes:
        for s in (1.0, -1.0):
            d = axis * s
            va = _visible_area(tri_n, tri_a, d)
            if va > best_area:
                best_area, best_n = va, d
    if best_n is None:
        return None
    return rotation_for_view(best_n)
