"""Split a convex hull into pieces so a concave prop stays walk-through.

Split out of collision.py.  One convex hull over a chair or a cart blocks
every gap the shape should leave open, so the hull is cut recursively along
its widest axis and shipped as a bhkListShape of convex pieces.

The cut is accepted only when it actually removes volume (_DECOMP_SPLIT_GAIN),
which keeps a genuinely convex shape as a single piece.
See: docs/commentary/asset_convert_collision.md#nif-dynamic-clutter-physics
"""

from asset_convert.collision.collision_material import (
    get_havok_material,
    set_havok_material,
)
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()

from pyffi.formats.nif import NifFormat

#: Skyrim: 1 Havok unit = 69.9904 game units.
_GAME_UNITS_PER_HAVOK = 69.9904


#: Binary split tree depth: 3 gives at most 8 pieces.
_DECOMP_MAX_DEPTH = 3
#: A cut is accepted only if the pieces lose >=10% of the parent's volume.
_DECOMP_SPLIT_GAIN = 0.90
_DECOMP_MIN_PIECE_VERTS = 8
_DECOMP_MAX_HULL_VERTS = 64


#: An axis shorter than this (game units) is too thin to be worth splitting.
_MIN_SPLIT_EXTENT_GU = 3.0

#: Quantisation grids in Havok units. See: docs/commentary/asset_convert_collision.md#concave-hull-decomposition
_QUANT_GRIDS = (0.004, 0.008, 0.015)

#: How far the visual AABB and the authored hull may disagree.
_AABB_CENTER_TOL = 0.35
_AABB_EXTENT_LO = 0.6
_AABB_EXTENT_HI = 1.67


def _scipy_available():
    """Whether scipy's ConvexHull can be imported; decomposition needs it."""
    try:
        from scipy.spatial import ConvexHull
    except ImportError:
        return False
    return ConvexHull is not None


def _quantised_hull(pts):
    """The coarsest hull under the vertex cap, as `(hull, hull_pts)`.

    Quantising keeps hull vertex counts in the vanilla range.
    """
    import numpy as np
    from scipy.spatial import ConvexHull
    hull = hull_pts = None
    for grid in _QUANT_GRIDS:
        q = np.unique(np.round(pts / grid) * grid, axis=0)
        if len(q) < 4:
            continue
        try:
            h = ConvexHull(q)
        except Exception:
            continue
        hull, hull_pts = h, q[h.vertices]
        if len(hull_pts) <= _DECOMP_MAX_HULL_VERTS:
            break
    if hull is None or len(hull_pts) < 4:
        return None, None
    return hull, hull_pts


def _covers_same_volume(pts_hk, hull_shape):
    """Whether the authored hull and the visual AABB describe one object.

    A mismatch means the collision covers something else, or sits in a
    different frame, and the original shape must be kept.
    """
    import numpy as np
    hull_pts = np.array([[v.x, v.y, v.z] for v in hull_shape.vertices])
    if len(hull_pts) < 4:
        return False
    for axis in range(3):
        v_lo, v_hi = pts_hk[:, axis].min(), pts_hk[:, axis].max()
        h_lo, h_hi = hull_pts[:, axis].min(), hull_pts[:, axis].max()
        v_ext, h_ext = v_hi - v_lo, h_hi - h_lo
        max_ext = max(v_ext, h_ext, 1e-4)
        if abs((v_lo + v_hi) - (h_lo + h_hi)) / 2 > _AABB_CENTER_TOL * max_ext:
            return False
        ratio = (v_ext + 1e-4) / (h_ext + 1e-4)
        if not _AABB_EXTENT_LO <= ratio <= _AABB_EXTENT_HI:
            return False
    return True


def _list_shape_over(piece_shapes, sk_material):
    """A bhkListShape wrapping the finished piece hulls."""
    ls = NifFormat.bhkListShape()
    set_havok_material(ls.material, sk_material)
    ls.num_sub_shapes = len(piece_shapes)
    ls.sub_shapes.update_size()
    for i, s in enumerate(piece_shapes):
        ls.sub_shapes[i] = s
    ls.num_unknown_ints = len(piece_shapes)
    ls.unknown_ints.update_size()
    for i in range(len(piece_shapes)):
        ls.unknown_ints[i] = 0
    return ls


def _hull_volume(pts):
    """The convex hull's volume, or None when scipy cannot build one."""
    from scipy.spatial import ConvexHull
    try:
        return ConvexHull(pts).volume
    except Exception:
        return None


def _recursive_hull_split(pts, depth):
    """Split a point cloud into pieces whose hulls waste less volume.

    Returns a list of point arrays, one entry when no cut pays for itself.
    Points near the cut are shared by BOTH halves, and each half reaches
    past the first vertex ring beyond it, so the piece hulls leave no gap.
    See: docs/commentary/asset_convert_collision.md#concave-hull-decomposition
    """
    vol = _hull_volume(pts)
    if vol is None or vol <= 0 or depth <= 0:
        return [pts]

    best = None
    for axis in range(3):
        lo, hi = pts[:, axis].min(), pts[:, axis].max()
        extent = hi - lo
        if extent * _GAME_UNITS_PER_HAVOK < _MIN_SPLIT_EXTENT_GU:
            continue
        eps = 0.02 * extent
        for frac in (0.3, 0.4, 0.5, 0.6, 0.7):
            cut = lo + frac * extent
            coords = pts[:, axis]
            above = coords[coords > cut]
            below = coords[coords < cut]
            reach_a = (above.min() if len(above) else cut) + eps
            reach_b = (below.max() if len(below) else cut) - eps
            a = pts[coords <= reach_a]
            b = pts[coords >= reach_b]
            if len(a) < _DECOMP_MIN_PIECE_VERTS or len(b) < _DECOMP_MIN_PIECE_VERTS:
                continue
            va = _hull_volume(a)
            vb = _hull_volume(b)
            if va is None or vb is None:
                continue
            if best is None or va + vb < best[0]:
                best = (va + vb, a, b)

    if best is None or best[0] > vol * _DECOMP_SPLIT_GAIN:
        return [pts]
    return (_recursive_hull_split(best[1], depth - 1)
            + _recursive_hull_split(best[2], depth - 1))


def _build_piece_convex_shape(pts, radius, sk_material):
    """A bhkConvexVerticesShape over one piece's points (Havok units).

    Face planes are pushed out by the convex radius, matching vanilla.
    See: docs/commentary/asset_convert_collision.md#concave-hull-decomposition
    """
    import numpy as np

    hull, hull_pts = _quantised_hull(pts)
    if hull is None:
        return None
    eqs = np.unique(np.round(hull.equations, 5), axis=0)

    shape = NifFormat.bhkConvexVerticesShape()
    set_havok_material(shape.material, sk_material)
    shape.radius = radius
    shape.num_vertices = len(hull_pts)
    shape.vertices.update_size()
    for i, (x, y, z) in enumerate(hull_pts):
        shape.vertices[i].x = float(x)
        shape.vertices[i].y = float(y)
        shape.vertices[i].z = float(z)
        shape.vertices[i].w = 0.0
    shape.num_normals = len(eqs)
    shape.normals.update_size()
    for i, eq in enumerate(eqs):
        shape.normals[i].x = float(eq[0])
        shape.normals[i].y = float(eq[1])
        shape.normals[i].z = float(eq[2])
        shape.normals[i].w = float(eq[3]) - radius
    return shape


def _collect_visual_vertices(node):
    """Gather all visual mesh vertices under *node* in node-frame game units."""
    import numpy as np
    out = []

    def walk(n, M):
        """Accumulate `n`'s vertices under the parent transform `M`."""
        if n is None:
            return
        L = np.eye(4)
        if hasattr(n, 'translation') and hasattr(n.translation, 'x'):
            r = n.rotation
            L[0, :3] = [r.m_11, r.m_12, r.m_13]
            L[1, :3] = [r.m_21, r.m_22, r.m_23]
            L[2, :3] = [r.m_31, r.m_32, r.m_33]
            L[:3, :3] *= n.scale
            L[3, :3] = [n.translation.x, n.translation.y, n.translation.z]
        M2 = L @ M
        if isinstance(n, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            d = n.data
            if d is not None and getattr(d, 'num_vertices', 0) > 0:
                verts = np.array([[v.x, v.y, v.z] for v in d.vertices])
                out.append(verts @ M2[:3, :3] + M2[3, :3])
        elif isinstance(n, NifFormat.NiNode):
            for c in n.children:
                walk(c, M2)

    if isinstance(node, NifFormat.NiNode):
        for c in node.children:
            walk(c, np.eye(4))
    if not out:
        return None
    return np.vstack(out)


#: Convex radius (Havok units) for a synthesized clutter hull, vanilla's clutter value.
_SYNTH_HULL_RADIUS = 0.01


def build_clutter_hull(tris_hk, sk_material):
    """A convex shape over triangles ALREADY in Havok units, or None.

    For a source that authors no Havok shape at all: Morrowind geometry, whose
    dynamic bodies cannot use the concave MOPP the static path builds.  Splits
    into a bhkListShape when the hull is meaningfully concave, exactly as
    decompose_clutter_hull does for Oblivion clutter.
    See: docs/commentary/asset_convert_collision.md#morrowind-dynamic-clutter
    """
    if not _scipy_available() or not tris_hk:
        return None
    import numpy as np

    pts = np.array([v for tri in tris_hk for v in tri], dtype=float)
    if len(pts) < 4:
        return None
    pieces = (_recursive_hull_split(pts, _DECOMP_MAX_DEPTH)
              if len(pts) >= 24 else [pts])
    shapes = [s for s in (_build_piece_convex_shape(p, _SYNTH_HULL_RADIUS,
                                                    sk_material)
                          for p in pieces) if s is not None]
    if not shapes:
        shapes = [s for s in [_build_piece_convex_shape(
            pts, _SYNTH_HULL_RADIUS, sk_material)] if s is not None]
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else _list_shape_over(shapes,
                                                               sk_material)


def decompose_clutter_hull(node, hull_shape):
    """A bhkListShape of tighter per-piece hulls, or None to keep the hull.

    Only called for dynamic (mass>0) plain bhkRigidBody clutter, where the
    shape frame equals the node frame.
    See: docs/commentary/asset_convert_collision.md#concave-hull-decomposition
    """
    if not _scipy_available():
        return None

    pts = _collect_visual_vertices(node)
    if pts is None or len(pts) < 24 or len(pts) > 60000:
        return None
    pts_hk = pts / _GAME_UNITS_PER_HAVOK
    if not _covers_same_volume(pts_hk, hull_shape):
        return None

    single_vol = _hull_volume(pts_hk)
    if single_vol is None or single_vol <= 0:
        return None

    pieces = _recursive_hull_split(pts_hk, _DECOMP_MAX_DEPTH)
    if len(pieces) < 2:
        return None

    radius = max(hull_shape.radius, 0.005)
    sk_material = get_havok_material(hull_shape.material)
    piece_shapes = []
    for piece in pieces:
        s = _build_piece_convex_shape(piece, radius, sk_material)
        if s is None:
            return None
        piece_shapes.append(s)

    return _list_shape_over(piece_shapes, sk_material)

