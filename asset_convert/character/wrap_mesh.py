"""
Mesh helpers shared by the body-wrap runtime (body_wrap) and its field builder
(body_wrap_build): closest points, seam welding, graph smoothing, and reading
skinned geometry out of a NIF in skeleton space.

See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
"""

import numpy as np
from scipy.spatial import cKDTree

from asset_convert.character.skin_retarget import m44_to_np
from asset_convert.nif import sse_nif
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

#: Coincident-vertex weld tolerance (UV seam twins).
WELD_TOL = 1e-3


def closest_point_on_triangles(p, a, b, c):
    """Vectorised closest point on triangle (Ericson); every argument is (..., 3)."""
    ab, ac = b - a, c - a
    ap, bp, cp = p - a, p - b, p - c
    d1, d2 = np.einsum('...i,...i->...', ab, ap), np.einsum('...i,...i->...', ac, ap)
    d3, d4 = np.einsum('...i,...i->...', ab, bp), np.einsum('...i,...i->...', ac, bp)
    d5, d6 = np.einsum('...i,...i->...', ab, cp), np.einsum('...i,...i->...', ac, cp)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    denom = va + vb + vc
    denom = np.where(np.abs(denom) < 1e-12, 1.0, denom)
    res = a + (vb / denom)[..., None] * ab + (vc / denom)[..., None] * ac
    res = _edge(res, (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0),
                b, c - b, d4 - d3, (d4 - d3) + (d5 - d6))
    res = _edge(res, (vb <= 0) & (d2 >= 0) & (d6 <= 0), a, ac, d2, d2 - d6)
    res = _edge(res, (vc <= 0) & (d1 >= 0) & (d3 <= 0), a, ab, d1, d1 - d3)
    res = np.where(((d6 >= 0) & (d5 <= d6))[..., None], c, res)
    res = np.where(((d3 >= 0) & (d4 <= d3))[..., None], b, res)
    return np.where(((d1 <= 0) & (d2 <= 0))[..., None], a, res)


def _edge(res, mask, start, direction, num, div):
    """`res` with the masked points moved onto an edge at parameter num / div."""
    t = (num / np.where(np.abs(div) < 1e-12, 1.0, div))[..., None]
    return np.where(mask[..., None], start + t * direction, res)


def weld_groups(verts: np.ndarray, tol: float = WELD_TOL) -> np.ndarray:
    """Group id per vertex, coincident vertices (UV-seam twins) sharing one.

    Distance-based (KD pairs + union-find), so twins either side of a rounding
    boundary still weld.
    """
    n = len(verts)
    parent = np.arange(n)

    def find(i):
        """The union-find root of vertex `i`, halving the path on the way."""
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in cKDTree(verts).query_pairs(tol, output_type='ndarray'):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri
    roots = np.fromiter((find(i) for i in range(n)), dtype=np.int64, count=n)
    return np.unique(roots, return_inverse=True)[1]


def group_mean(values: np.ndarray, group: np.ndarray, n_groups: int):
    """Mean of `values` (N, D) per weld group -> (G, D)."""
    sums = np.zeros((n_groups, values.shape[1]), dtype=np.float64)
    np.add.at(sums, group, values)
    counts = np.bincount(group, minlength=n_groups).astype(np.float64)
    return sums / np.maximum(counts, 1.0)[:, None]


def vertex_normals(verts, tris, group, n_groups):
    """Area-weighted vertex normals accumulated over weld groups."""
    fn = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]],
                  verts[tris[:, 2]] - verts[tris[:, 0]])
    acc = np.zeros((n_groups, 3), dtype=np.float64)
    for k in range(3):
        np.add.at(acc, group[tris[:, k]], fn)
    acc /= np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-12)
    return acc[group]


def build_adjacency(tris, group, n_groups):
    """Weld-group adjacency as (nbr_idx, nbr_ptr) CSR arrays."""
    e = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [0, 2]]])
    ge = group[e]
    ge = ge[ge[:, 0] != ge[:, 1]]
    ge = np.unique(np.vstack([ge, ge[:, ::-1]]), axis=0)
    nbr_ptr = np.zeros(n_groups + 1, dtype=np.int64)
    nbr_ptr[1:] = np.cumsum(np.bincount(ge[:, 0], minlength=n_groups))
    return ge[:, 1], nbr_ptr


def smooth_group_field(field_g, nbr_idx, nbr_ptr, iters, lam=0.5):
    """Jacobi smoothing of a per-group vector field over the mesh graph."""
    counts = np.maximum(np.diff(nbr_ptr), 1).astype(np.float64)
    src_of_edge = np.repeat(np.arange(len(counts)), np.diff(nbr_ptr))
    for _ in range(iters):
        nbr_sum = np.zeros_like(field_g)
        np.add.at(nbr_sum, src_of_edge, field_g[nbr_idx])
        field_g = (1.0 - lam) * field_g + lam * (nbr_sum / counts[:, None])
    return field_g


def read_nif(source):
    """A NIF from a path or bytes, SSE geometry converted to LE blocks."""
    return sse_nif.read_nif(source)


def block_name(block) -> str:
    """A block's name as text."""
    return bytes(block.name).rstrip(b'\x00').decode('latin-1', errors='replace')


def _skeleton_root(root):
    """The first skin's skeleton root under `root`, else `root` itself."""
    for block in root.tree():
        skin = getattr(block, 'skin_instance', None)
        if skin is not None and skin.skeleton_root is not None:
            return skin.skeleton_root
    return root


def iter_skinned_geoms(data):
    """(block, skeleton root) for every skinned NiTriShape/Strips with vertices."""
    for root in data.roots:
        if root is None:
            continue
        skel_root = _skeleton_root(root)
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None or skin.data is None:
                continue
            if block.data is not None and block.data.num_vertices:
                yield block, skel_root


def geom_world(block, skel_root):
    """(the shape's vertices in skeleton space, its 4x4 transform there)."""
    try:
        G = m44_to_np(block.get_transform(skel_root))
    except (ValueError, RuntimeError):
        G = np.eye(4)
    verts = np.array([[v.x, v.y, v.z] for v in block.data.vertices], dtype=np.float64)
    if not np.allclose(G, np.eye(4), atol=1e-6):
        verts = verts @ G[:3, :3] + G[3, :3]
    return verts, G


def geom_triangles(block) -> np.ndarray:
    """The shape's triangles (M, 3); strips are unrolled."""
    d = block.data
    if hasattr(d, 'triangles') and d.num_triangles:
        return np.array([[t.v_1, t.v_2, t.v_3] for t in d.triangles], dtype=np.int64)
    if hasattr(d, 'get_triangles'):
        return np.array(d.get_triangles(), dtype=np.int64)
    return np.zeros((0, 3), dtype=np.int64)


def geom_bone_weights(block, alias: dict = None) -> dict:
    """{bone name: (indices, weights)} from NiSkinData, names passed through `alias`."""
    skin = block.skin_instance
    sd = skin.data
    out: dict = {}
    alias = alias or {}
    for bi in range(min(skin.num_bones, sd.num_bones)):
        bone = skin.bones[bi]
        if bone is None:
            continue
        name = alias.get(block_name(bone), block_name(bone))
        be = sd.bone_list[bi]
        idx = np.fromiter((vw.index for vw in be.vertex_weights),
                          dtype=np.int64, count=be.num_vertices)
        w = np.fromiter((vw.weight for vw in be.vertex_weights),
                        dtype=np.float64, count=be.num_vertices)
        if name in out:
            idx = np.concatenate([out[name][0], idx])
            w = np.concatenate([out[name][1], w])
        out[name] = (idx, w)
    return out
