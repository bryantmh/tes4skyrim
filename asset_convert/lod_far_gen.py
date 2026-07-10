"""Generate _far.nif LOD meshes by decimating full-resolution Skyrim NIFs.

Called from lod_gen.generate_lod() as a pre-pass to ensure all LOD-flagged
objects have a _far.nif before LODGenx64 is invoked.

Algorithm
---------
1. Read the converted Skyrim NIF (v20.2.0.7, BSStream 83).
2. Walk all solid (non-skinned) geometry **per shape in local space**.
3. Simplify each shape with quadric-error-metric (QEM) half-edge collapses:
   positions are welded for topology, edges are collapsed cheapest-first into
   a surviving original vertex (no position/UV interpolation), boundary edges
   carry constraint quadrics so open meshes keep their rims, and collapses
   that would flip a face normal are rejected.  This replaced grid-based
   vertex clustering, which snapped vertices to coarse cells and left big
   holes in objects.
4. Recompute smooth per-vertex normals; recompute tangent/bitangent vectors
   from UV differentials (standard tangent-space method).
5. Re-write the NIF in-place: keep every NiTriShape that survives decimation
   with its *original* BSLightingShaderProperty (and NiAlphaProperty) intact.
   Strip collision, controllers, skin, vertex colors, and extra data from
   all nodes.
6. Clear the VertexColors shader flag (SF2 bit 0x20) since vertex colors are
   removed.
7. Write to <model_base>_far.nif.

Each shape is decimated independently so it keeps its own texture correctly.
BSLightingShaderProperty is COPIED from the source (correct flags, no
recreation) — this fixes the missing ZBufferTest flag that caused objects to
not render in-game.
"""

import io
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import pyffi_monkey_patch  # noqa — must apply before NifFormat import
from pyffi.formats.nif import NifFormat

_SKYRIM_VER = 0x14020007
_NIF_FLAGS  = 14

# Global target across all shapes combined.  Auto-decimation needs more
# headroom than hand-made vanilla _far.nif (~5-10%) to keep silhouettes; the
# QEM error floor below stops early on shapes that would fall apart anyway.
_DECIMATE_RATIO    = 0.08
_MAX_TARGET_VERTS  = 800     # absolute cap: LOD geometry is baked per-instance
                             # into every .bto tile, so heavy meshes multiply
_MIN_TRIS          = 12      # min output tris to keep a shape
_MIN_SRC_TRIS      = 20      # min source tris to include a shape at all
_SF2_VERTEX_COLORS = 0x20    # SF2 bit to clear when removing vertex colors

# Tree models get a crossed-quad billboard _far.nif (vanilla-style flat tree
# LOD) instead of decimated geometry — decimating leaf cards shreds canopies
# and drops trunks, and the full geometry made .bto tiles enormous.
_TREE_MODEL_PREFIX = 'tes4\\speedtrees\\'
_BILLBOARD_TEX_DIR = 'tes4\\trees\\billboards'


# ---------------------------------------------------------------------------
# Decimation helpers
# ---------------------------------------------------------------------------


_BOUNDARY_WEIGHT = 8.0     # boundary-edge constraint quadric weight (× len²)
_PRUNE_MAX_AREA_FRAC = 0.05  # only islands below this share of total surface
                             # area may be pruned to meet the vertex budget
_WELD_EPS        = 1e-3    # position weld tolerance (game units)
_MAX_DEV_FRAC    = 0.03    # error floor: stop when a collapse would deviate
                           # more than this fraction of the model diagonal
_EDGE_LEN_REG    = 0.5     # edge-length regularization (× mean face area)

# Coarser variants for the far LOD rings.  The _far8/_far16 meshes are
# re-decimated FROM the _far.nif with a relaxed error floor — at level-8/16
# distances (2+ km) silhouette lumps are invisible but baked verts still
# cost disk/VRAM in every tile.
_TIER8  = dict(ratio=0.5,  cap=250, dev=0.08, suffix='_far8')
_TIER16 = dict(ratio=0.25, cap=120, dev=0.12, suffix='_far16')


def _qem_decimate(verts: np.ndarray, tris: np.ndarray,
                  uvs: Optional[np.ndarray],
                  target_verts: int,
                  max_dev_frac: float = _MAX_DEV_FRAC) -> Tuple:
    """Quadric-error-metric half-edge-collapse simplification.

    Positions are welded (UV-seam duplicates share one topology node) so
    collapses can cross seams; each output corner keeps ITS original vertex's
    UV, so texture charts never bleed into each other.  A collapse u→v moves
    u to v's exact original position (no interpolation), is charged the
    combined quadric error at v, is rejected if it would flip an adjacent
    face's normal, and boundary edges carry perpendicular constraint quadrics
    so open rims shrink last.  This preserves silhouettes and never punches
    holes the way grid vertex-clustering did.

    Returns (new_verts, new_tris, new_uvs).
    """
    nV, nT = len(verts), len(tris)
    if nV == 0 or nT == 0:
        return verts, tris, uvs

    # ---- weld positions for topology -------------------------------------
    keys = np.round(verts / _WELD_EPS).astype(np.int64)
    _, first_idx, wid = np.unique(keys, axis=0, return_index=True,
                                  return_inverse=True)
    W = len(first_idx)
    P = verts[first_idx].astype(np.float64)          # position per weld node

    F0 = wid[tris]                                    # faces in weld space
    ok = (F0[:, 0] != F0[:, 1]) & (F0[:, 1] != F0[:, 2]) & (F0[:, 0] != F0[:, 2])
    F0 = F0[ok]
    C0 = tris[ok]                                     # original corner ids (UVs)
    if not len(F0):
        return verts, tris, uvs

    # ---- initial quadrics (area-weighted face planes) ---------------------
    v0, v1, v2 = P[F0[:, 0]], P[F0[:, 1]], P[F0[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)                   # |fn| = 2×area
    area2 = np.linalg.norm(fn, axis=1)
    nrm = fn / np.maximum(area2, 1e-12)[:, None]
    d = -np.einsum('ij,ij->i', nrm, v0)
    plane = np.concatenate([nrm, d[:, None]], axis=1)             # (nF,4)
    fq = plane[:, :, None] * plane[:, None, :] * area2[:, None, None]
    Q = np.zeros((W, 4, 4), np.float64)
    for k in range(3):
        np.add.at(Q, F0[:, k], fq)

    # ---- boundary constraint quadrics -------------------------------------
    edges = np.concatenate([F0[:, [0, 1]], F0[:, [1, 2]], F0[:, [2, 0]]])
    edges_s = np.sort(edges, axis=1)
    uniq_e, e_cnt = np.unique(edges_s, axis=0, return_counts=True)
    boundary = uniq_e[e_cnt == 1]
    if len(boundary):
        # Constraint plane through each boundary edge: any plane containing the
        # edge penalizes moving its endpoints off the edge line, which is what
        # keeps open rims (window frames, wall tops, leaf-card edges) intact.
        # Use the plane spanned by the edge and the world axis least aligned
        # with it.
        ea, eb = boundary[:, 0], boundary[:, 1]
        edge_v = P[eb] - P[ea]
        ax = np.zeros_like(edge_v)
        ax[np.arange(len(edge_v)), np.argmin(np.abs(edge_v), axis=1)] = 1.0
        cn = np.cross(edge_v, ax)
        cl = np.linalg.norm(cn, axis=1)
        good = cl > 1e-12
        cn[good] /= cl[good][:, None]
        cd = -np.einsum('ij,ij->i', cn, P[ea])
        cplane = np.concatenate([cn, cd[:, None]], axis=1)
        w = _BOUNDARY_WEIGHT * np.einsum('ij,ij->i', edge_v, edge_v)
        cq = cplane[:, :, None] * cplane[:, None, :] * w[:, None, None]
        np.add.at(Q, ea, cq)
        np.add.at(Q, eb, cq)

    # ---- mutable topology --------------------------------------------------
    import heapq
    faces = [[int(a), int(b), int(c)] for a, b, c in F0]
    corners = [[int(a), int(b), int(c)] for a, b, c in C0]
    face_alive = [True] * len(faces)
    vert_faces = [set() for _ in range(W)]
    for fi, f in enumerate(faces):
        for v in f:
            vert_faces[v].add(fi)

    version = [0] * W
    alive = sum(1 for s in vert_faces if s)

    # Per-vertex accumulated face area (for the error floor) + regularization.
    A = np.zeros(W, np.float64)
    for k in range(3):
        np.add.at(A, F0[:, k], area2 / 6.0)   # area2 = 2×area, /3 per corner
    mean_face_area = float(area2.mean()) / 2.0
    diag = float(np.linalg.norm(P.max(axis=0) - P.min(axis=0)))
    max_dev2 = (diag * max_dev_frac) ** 2

    hom = np.ones(4)

    def cost_of(u, v):
        hom[:3] = P[v]
        q = Q[u] + Q[v]
        c = float(hom @ q @ hom)
        # edge-length regularization: discourage long-distance collapses that
        # stretch faces into "sails" even when the quadric error is small.
        # Scaled like "one average face displaced by the collapse distance".
        dv = P[u] - P[v]
        c += _EDGE_LEN_REG * mean_face_area * float(dv @ dv)
        return c

    def neighbors(u):
        out = set()
        for fi in vert_faces[u]:
            out.update(faces[fi])
        out.discard(u)
        return out

    heap = []
    for e in uniq_e:
        a, b = int(e[0]), int(e[1])
        heapq.heappush(heap, (cost_of(a, b), a, b, version[a], version[b]))
        heapq.heappush(heap, (cost_of(b, a), b, a, version[b], version[a]))

    target = max(int(target_verts), 4)

    while alive > target and heap:
        cost, u, v, vu, vv = heapq.heappop(heap)
        if version[u] != vu or version[v] != vv:
            continue
        if not vert_faces[u] or not vert_faces[v]:
            continue
        if not math.isfinite(cost):
            continue
        # error floor: if even the cheapest remaining collapse would deviate
        # more than _MAX_DEV_FRAC of the diagonal, stop — a heavier LOD beats
        # a shredded one.  (cost ≈ local_area × deviation².)
        if cost > (A[u] + A[v]) * max_dev2 + 1e-12:
            continue
        # still adjacent?
        shared = [fi for fi in vert_faces[u] if v in faces[fi]]
        if not shared:
            continue

        # normal-flip guard: faces of u that survive (don't contain v)
        flip = False
        pu, pv = P[u], P[v]
        for fi in vert_faces[u]:
            f = faces[fi]
            if v in f:
                continue
            i = f.index(u)
            a, b = f[(i + 1) % 3], f[(i + 2) % 3]
            n_before = np.cross(P[a] - pu, P[b] - pu)
            n_after = np.cross(P[a] - pv, P[b] - pv)
            if np.dot(n_before, n_after) <= 0:
                flip = True
                break
        if flip:
            continue

        # ---- perform collapse u -> v ----
        for fi in list(vert_faces[u]):
            f = faces[fi]
            if v in f:
                # face degenerates: remove from all its vertices
                face_alive[fi] = False
                for w_ in f:
                    vert_faces[w_].discard(fi)
            else:
                i = f.index(u)
                f[i] = v
                vert_faces[v].add(fi)
        vert_faces[u].clear()
        Q[v] += Q[u]
        A[v] += A[u]
        version[u] += 1
        version[v] += 1
        alive -= 1
        if not vert_faces[v]:
            alive -= 1
            continue

        for nb in neighbors(v):
            heapq.heappush(heap, (cost_of(nb, v), nb, v, version[nb], version[v]))
            heapq.heappush(heap, (cost_of(v, nb), v, nb, version[v], version[nb]))

    # ---- component pruning --------------------------------------------------
    # When collapses stall above target (the error floor protects shapes made
    # of many small disconnected islands — foliage cards, clutter piles), drop
    # whole components smallest-area-first until the budget is met.  Only
    # components below _PRUNE_MAX_AREA_FRAC of the shape's total area may be
    # dropped: pruning anything bigger visibly removes chunks of the object
    # (this is what used to delete tree trunks whose canopy out-measured them).
    if alive > target:
        parent = list(range(W))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        comp_area: dict = {}
        comp_faces: dict = {}
        for fi, f in enumerate(faces):
            if not face_alive[fi]:
                continue
            ra, rb, rc = find(f[0]), find(f[1]), find(f[2])
            for r2 in (rb, rc):
                if find(r2) != find(ra):
                    parent[find(r2)] = find(ra)
        for fi, f in enumerate(faces):
            if not face_alive[fi]:
                continue
            r = find(f[0])
            e0, e1, e2 = P[f[0]], P[f[1]], P[f[2]]
            comp_area[r] = comp_area.get(r, 0.0) + \
                float(np.linalg.norm(np.cross(e1 - e0, e2 - e0))) / 2.0
            comp_faces.setdefault(r, []).append(fi)

        if len(comp_faces) > 1:
            total_area = sum(comp_area.values())
            by_area = sorted(comp_area, key=comp_area.get)
            for r in by_area[:-1]:                    # never drop the largest
                if alive <= target:
                    break
                if comp_area[r] > total_area * _PRUNE_MAX_AREA_FRAC:
                    break                             # rest are bigger still
                dropped_verts = set()
                for fi in comp_faces[r]:
                    face_alive[fi] = False
                    for w_ in faces[fi]:
                        if vert_faces[w_]:
                            vert_faces[w_].discard(fi)
                            if not vert_faces[w_]:
                                dropped_verts.add(w_)
                alive -= len(dropped_verts)

    # ---- rebuild output arrays --------------------------------------------
    # Output vertex = (surviving weld node, corner's ORIGINAL UV): faces keep
    # their own texture chart, seams stay intact.
    out_map = {}
    out_v: list = []
    out_uv: list = []
    out_t = []
    for fi, f in enumerate(faces):
        if not face_alive[fi]:
            continue
        idx3 = []
        for k in range(3):
            wnode = f[k]
            o = corners[fi][k]
            if uvs is not None:
                key = (wnode, round(float(uvs[o][0]) * 4096),
                       round(float(uvs[o][1]) * 4096))
            else:
                key = wnode
            j = out_map.get(key)
            if j is None:
                j = len(out_v)
                out_map[key] = j
                out_v.append(P[wnode])
                if uvs is not None:
                    out_uv.append(uvs[o])
            idx3.append(j)
        if idx3[0] != idx3[1] and idx3[1] != idx3[2] and idx3[0] != idx3[2]:
            out_t.append(idx3)

    if not out_t:
        return (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32),
                np.zeros((0, 2), np.float32) if uvs is not None else None)

    nv = np.asarray(out_v, dtype=np.float32)
    nt = np.asarray(out_t, dtype=np.int32)
    nuv = np.asarray(out_uv, dtype=np.float32) if uvs is not None else None
    return nv, nt, nuv


def _normals(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Smooth per-vertex normals averaged from face normals."""
    n_out = np.zeros_like(verts)
    v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    d = np.linalg.norm(fn, axis=1, keepdims=True)
    d[d < 1e-10] = 1.0
    fn /= d
    np.add.at(n_out, tris[:, 0], fn)
    np.add.at(n_out, tris[:, 1], fn)
    np.add.at(n_out, tris[:, 2], fn)
    d2 = np.linalg.norm(n_out, axis=1, keepdims=True)
    d2[d2 < 1e-10] = 1.0
    return (n_out / d2).astype(np.float32)


def _compute_tangents(verts: np.ndarray, tris: np.ndarray,
                      uvs: np.ndarray, normals: np.ndarray
                      ) -> Tuple[np.ndarray, np.ndarray]:
    """Per-vertex tangents and bitangents via UV differentials (Gram-Schmidt)."""
    tan1 = np.zeros_like(verts)
    tan2 = np.zeros_like(verts)

    v0 = verts[tris[:, 0]];  v1 = verts[tris[:, 1]];  v2 = verts[tris[:, 2]]
    uv0 = uvs[tris[:, 0]];   uv1 = uvs[tris[:, 1]];   uv2 = uvs[tris[:, 2]]

    dv1 = v1 - v0;    dv2 = v2 - v0
    duv1 = uv1 - uv0; duv2 = uv2 - uv0

    denom = duv1[:, 0] * duv2[:, 1] - duv2[:, 0] * duv1[:, 1]
    with np.errstate(divide='ignore', invalid='ignore'):
        r = np.where(np.abs(denom) > 1e-10, 1.0 / denom, 0.0)

    t_face = r[:, None] * (duv2[:, 1:2] * dv1 - duv1[:, 1:2] * dv2)
    b_face = r[:, None] * (duv1[:, 0:1] * dv2 - duv2[:, 0:1] * dv1)

    np.add.at(tan1, tris[:, 0], t_face); np.add.at(tan1, tris[:, 1], t_face); np.add.at(tan1, tris[:, 2], t_face)
    np.add.at(tan2, tris[:, 0], b_face); np.add.at(tan2, tris[:, 1], b_face); np.add.at(tan2, tris[:, 2], b_face)

    nT = np.einsum('ij,ij->i', normals, tan1)[:, None]
    t_ortho = tan1 - nT * normals
    d_t = np.linalg.norm(t_ortho, axis=1, keepdims=True)
    d_t[d_t < 1e-10] = 1.0
    tangents = (t_ortho / d_t).astype(np.float32)
    bitangents = np.cross(normals, tangents).astype(np.float32)
    return tangents, bitangents


# ---------------------------------------------------------------------------
# NIF in-place modification (per-shape)
# ---------------------------------------------------------------------------

def _strip_node(node) -> None:
    """Remove collision, controller, and extra data from a NIF node."""
    if hasattr(node, 'collision_object'):
        node.collision_object = None
    if hasattr(node, 'controller'):
        node.controller = None
    if hasattr(node, 'num_extra_data_list'):
        node.num_extra_data_list = 0
        if hasattr(node, 'extra_data_list'):
            node.extra_data_list.update_size()


def _decimate_shape_inplace(shape, target_verts: int,
                            max_dev_frac: float = _MAX_DEV_FRAC) -> bool:
    """Decimate one NiTriShape in local space to approximately target_verts vertices.

    Keeps the original BSLightingShaderProperty (correct flags + textures).
    Clears vertex colors and the VertexColors SF2 bit.
    Recomputes normals and tangents from the decimated geometry.
    Returns True on success, False if shape should be removed.
    """
    shape.skin_instance = None
    if hasattr(shape, 'controller'):
        shape.controller = None
    if hasattr(shape, 'num_extra_data_list'):
        shape.num_extra_data_list = 0
        if hasattr(shape, 'extra_data_list'):
            shape.extra_data_list.update_size()

    d = getattr(shape, 'data', None)
    if d is None or not isinstance(d, NifFormat.NiTriShapeData):
        return False
    if d.num_vertices < 3 or d.num_triangles < _MIN_SRC_TRIS:
        return False

    verts = np.array([(v.x, v.y, v.z) for v in d.vertices], dtype=np.float32)
    tris  = np.array([(t.v_1, t.v_2, t.v_3) for t in d.triangles], dtype=np.int32)

    # Use actual array length, not the count field (more reliable across versions)
    uvs = None
    try:
        uv_sets = d.uv_sets
        if len(uv_sets) > 0 and len(uv_sets[0]) == d.num_vertices:
            uvs = np.array([(uv.u, uv.v) for uv in uv_sets[0]], dtype=np.float32)
    except Exception:
        pass

    d_v, d_t, d_uv = _qem_decimate(verts, tris, uvs, target_verts,
                                   max_dev_frac)
    if len(d_t) < _MIN_TRIS:
        return False

    used  = np.unique(d_t)
    v_map = np.full(len(d_v), -1, dtype=np.int32)
    v_map[used] = np.arange(len(used), dtype=np.int32)
    f_v  = d_v[used]
    f_t  = v_map[d_t]
    f_uv = d_uv[used] if d_uv is not None else None
    f_n  = _normals(f_v, f_t)

    nv = len(f_v)
    nt = len(f_t)

    # --- Write geometry ---
    d.num_vertices = nv
    d.has_vertices = True
    d.vertices.update_size()
    for i, (x, y, z) in enumerate(f_v):
        d.vertices[i].x = float(x)
        d.vertices[i].y = float(y)
        d.vertices[i].z = float(z)

    d.has_normals = True
    d.normals.update_size()
    for i, (nx, ny, nz) in enumerate(f_n):
        d.normals[i].x = float(nx)
        d.normals[i].y = float(ny)
        d.normals[i].z = float(nz)

    # UVs (_ListWrap has no update_size; resize via list primitives)
    if f_uv is not None:
        try:
            inner_uv = d.uv_sets[0]
            elem_type = inner_uv._elementType
            list.clear(inner_uv)
            list.extend(inner_uv, [elem_type() for _ in range(nv)])
            for i, (u, v) in enumerate(f_uv):
                d.uv_sets[0][i].u = float(u)
                d.uv_sets[0][i].v = float(v)
        except Exception:
            f_uv = None  # fall back: no UVs

    # Vertex colors — remove
    d.has_vertex_colors = False
    if hasattr(d, 'vertex_colors'):
        d.vertex_colors.update_size()

    # Tangents + bitangents
    has_tang = bool(getattr(d, 'extra_vectors_flags', 0) & 0x10)
    if has_tang:
        if f_uv is not None:
            try:
                f_tang, f_bita = _compute_tangents(f_v, f_t, f_uv, f_n)
                d.tangents.update_size()
                for i, (tx, ty, tz) in enumerate(f_tang):
                    d.tangents[i].x = float(tx)
                    d.tangents[i].y = float(ty)
                    d.tangents[i].z = float(tz)
                d.bitangents.update_size()
                for i, (bx, by, bz) in enumerate(f_bita):
                    d.bitangents[i].x = float(bx)
                    d.bitangents[i].y = float(by)
                    d.bitangents[i].z = float(bz)
            except Exception:
                d.extra_vectors_flags = getattr(d, 'extra_vectors_flags', 0) & ~0x10
                if hasattr(d, 'tangents'):   d.tangents.update_size()
                if hasattr(d, 'bitangents'): d.bitangents.update_size()
        else:
            # No UVs — resize to new vert count with zero vectors
            if hasattr(d, 'tangents'):   d.tangents.update_size()
            if hasattr(d, 'bitangents'): d.bitangents.update_size()

    d.num_triangles       = nt
    d.num_triangle_points = nt * 3
    d.has_triangles       = True
    d.triangles.update_size()
    for i, (a, b, c) in enumerate(f_t):
        d.triangles[i].v_1 = int(a)
        d.triangles[i].v_2 = int(b)
        d.triangles[i].v_3 = int(c)

    d.consistency_flags = 0x4000  # CT_STATIC
    d.unknown_int_2     = 0

    # Remove VertexColors bit from SF2 since vertex colors are stripped
    for prop in getattr(shape, 'bs_properties', []):
        if prop is None:
            continue
        sf2 = getattr(prop, 'shader_flags_2', None)
        if sf2 is None:
            continue
        # SkyrimShaderPropertyFlags2 has no integer setter; use the named bit
        try:
            sf2.slsf_2_vertex_colors = 0
        except Exception:
            pass

    return True


def _collect_shapes(node, out: list) -> None:
    """Recursively collect all NiTriShapes in the NIF tree."""
    if node is None:
        return
    for child in getattr(node, 'children', []):
        if child is None:
            continue
        if isinstance(child, NifFormat.NiTriShape):
            out.append(child)
        elif isinstance(child, NifFormat.NiNode):
            _collect_shapes(child, out)


def _decimate_children(node, targets: Dict[int, int],
                       max_dev_frac: float = _MAX_DEV_FRAC) -> int:
    """Walk a NiNode's children, decimate shapes, drop non-geometry blocks.

    targets maps id(shape) → target_verts for each NiTriShape.
    Returns count of surviving shapes.
    """
    keep: list = []
    survivors = 0

    for child in getattr(node, 'children', []):
        if child is None:
            continue
        if isinstance(child, NifFormat.NiTriShape):
            target = targets.get(id(child), 50)
            if _decimate_shape_inplace(child, target, max_dev_frac):
                keep.append(child)
                survivors += 1
        elif isinstance(child, NifFormat.NiNode):
            _strip_node(child)
            sub = _decimate_children(child, targets, max_dev_frac)
            if sub > 0:
                keep.append(child)
                survivors += sub
        # NiTriStrips should not appear in converted Skyrim NIFs; skip others

    node.num_children = len(keep)
    node.children.update_size()
    for i, c in enumerate(keep):
        node.children[i] = c

    return survivors


def _decimate_nif_inplace(nif_data, ratio: float,
                          cap: int = _MAX_TARGET_VERTS,
                          max_dev_frac: float = _MAX_DEV_FRAC) -> bool:
    """Decimate all geometry in the NIF in-place using a global proportional budget.

    Phase 1: count total source verts across all valid shapes.
    Phase 2: allocate per-shape targets (proportional to shape's share of total).
    Phase 3: strip collision/controllers, then decimate each shape.

    Returns True if at least one shape survived.
    """
    # Phase 1: collect + count
    all_shapes: list = []
    for root in nif_data.roots:
        _collect_shapes(root, all_shapes)

    total_source = 0
    valid: List[tuple] = []
    for shape in all_shapes:
        d = getattr(shape, 'data', None)
        if (d is not None
                and isinstance(d, NifFormat.NiTriShapeData)
                and d.num_vertices >= 3
                and d.num_triangles >= _MIN_SRC_TRIS
                and getattr(shape, 'skin_instance', None) is None):
            total_source += d.num_vertices
            valid.append((shape, d.num_vertices))

    if total_source == 0:
        return False

    total_target = min(max(150, int(total_source * ratio)), cap)

    # Phase 2: per-shape target proportional to vertex share
    targets: Dict[int, int] = {}
    for shape, nv in valid:
        share = nv / total_source
        targets[id(shape)] = max(40, int(total_target * share))

    # Phase 3: strip + decimate
    survivors = 0
    for root in nif_data.roots:
        if root is None:
            continue
        _strip_node(root)
        root.flags = _NIF_FLAGS
        survivors += _decimate_children(root, targets, max_dev_frac)
    return survivors > 0


# ---------------------------------------------------------------------------
# Tree billboard LOD (vanilla-style flat crossed quads)
# ---------------------------------------------------------------------------

def _write_billboard_flat_normal(path: Path, size: int = 128) -> None:
    """Write a flat-normal uncompressed DDS so billboard LOD is lit evenly."""
    import struct as _struct
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = b'DDS ' + _struct.pack('<I', 124)
    hdr += _struct.pack('<I', 0x1 | 0x2 | 0x4 | 0x1000 | 0x8)
    hdr += _struct.pack('<II', size, size)
    hdr += _struct.pack('<I', size * 4)
    hdr += _struct.pack('<II', 0, 0)
    hdr += b'\x00' * 44
    hdr += _struct.pack('<II', 32, 0x41)              # RGB | ALPHAPIXELS
    hdr += _struct.pack('<I', 0)
    hdr += _struct.pack('<I', 32)
    hdr += _struct.pack('<IIII', 0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000)
    hdr += _struct.pack('<I', 0x1000)
    hdr += _struct.pack('<IIII', 0, 0, 0, 0)
    # BGRA (255,128,128,255) = flat +Z normal
    px = bytes((255, 128, 128, 255)) * (size * size)
    path.write_bytes(hdr + px)


def _billboard_geometry(width: float, z_bottom: float, z_top: float):
    """Crossed-quad card verts/normals/uvs/tris (two quads at 90°)."""
    hw = width / 2.0
    verts = np.array([
        (-hw, 0.0, z_bottom), (hw, 0.0, z_bottom),
        (hw, 0.0, z_top),     (-hw, 0.0, z_top),
        (0.0, -hw, z_bottom), (0.0, hw, z_bottom),
        (0.0, hw, z_top),     (0.0, -hw, z_top),
    ], dtype=np.float32)
    normals = np.array([(0, 1, 0)] * 4 + [(1, 0, 0)] * 4, dtype=np.float32)
    # DDS v=0 is the top of the rendered tree
    uvs = np.array([(0, 1), (1, 1), (1, 0), (0, 0)] * 2, dtype=np.float32)
    tris = np.array([(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)],
                    dtype=np.int32)
    return verts, normals, uvs, tris


def generate_tree_billboard_far(dst_path: Path, obnd, model_rel: str,
                                tex_root: Path) -> bool:
    """Write a crossed-quad billboard _far.nif for a TREE model.

    Uses Oblivion's own shipped billboard render
    (textures\\tes4\\trees\\billboards\\<model stem>.dds — a full-tree render
    including the trunk).  Card size comes from OBND, which the importer
    derived from the billboard dimensions, so proportions match.  Returns
    False if the billboard texture doesn't exist (caller falls back to
    geometry decimation).
    """
    stem = os.path.splitext(os.path.basename(
        model_rel.replace('\\', '/')))[0].lower()
    diffuse_rel = f'{_BILLBOARD_TEX_DIR}\\{stem}.dds'
    if not (tex_root / diffuse_rel).exists():
        return False
    normal_rel = f'{_BILLBOARD_TEX_DIR}\\{stem}_n.dds'
    if not (tex_root / normal_rel).exists():
        try:
            _write_billboard_flat_normal(tex_root / normal_rel)
        except Exception:
            return False

    width = height = z_min = 0.0
    if obnd:
        x1, y1, z1, x2, y2, z2 = obnd
        width  = float(max(x2 - x1, y2 - y1))
        height = float(z2 - z1)
        z_min  = float(z1)
    if width <= 0:
        width = 256.0
    if height <= 0:
        height, z_min = 384.0, 0.0
    # Sink the card slightly so it doesn't float on slopes (LODGen's own
    # flat-billboard code uses the same 5-unit sink).
    verts, normals, uvs, tris = _billboard_geometry(
        width, z_min - 5.0, z_min + height)

    tsd = NifFormat.NiTriShapeData()
    tsd.num_vertices = len(verts)
    tsd.has_vertices = True
    tsd.vertices.update_size()
    tsd.has_normals = True
    tsd.normals.update_size()
    tsd.num_uv_sets = 1
    tsd.uv_sets.update_size()
    for i in range(len(verts)):
        v = tsd.vertices[i]
        v.x, v.y, v.z = map(float, verts[i])
        n = tsd.normals[i]
        n.x, n.y, n.z = map(float, normals[i])
        uv = tsd.uv_sets[0][i]
        uv.u, uv.v = map(float, uvs[i])
    tsd.num_triangles = len(tris)
    tsd.num_triangle_points = len(tris) * 3
    tsd.has_triangles = True
    tsd.triangles.update_size()
    for i, (a, b, c) in enumerate(tris):
        t = tsd.triangles[i]
        t.v_1, t.v_2, t.v_3 = int(a), int(b), int(c)
    ctr = (verts.min(axis=0) + verts.max(axis=0)) / 2.0
    tsd.center.x, tsd.center.y, tsd.center.z = map(float, ctr)
    tsd.radius = float(np.linalg.norm(verts - ctr, axis=1).max())
    tsd.consistency_flags = 0x4000  # CT_STATIC

    texset = NifFormat.BSShaderTextureSet()
    texset.num_textures = 9
    texset.textures.update_size()
    texset.textures[0] = f'textures\\{diffuse_rel}'.encode()
    texset.textures[1] = f'textures\\{normal_rel}'.encode()

    shader = NifFormat.BSLightingShaderProperty()
    shader.texture_set = texset
    shader.uv_scale.u = 1.0
    shader.uv_scale.v = 1.0
    shader.glossiness = 1.0
    shader.specular_strength = 0.0
    shader.alpha = 1.0
    shader.emissive_multiple = 1.0
    shader.texture_clamp_mode = 3
    shader.shader_flags_1.slsf_1_z_buffer_test = 1
    shader.shader_flags_1.slsf_1_specular = 0
    shader.shader_flags_2.slsf_2_z_buffer_write = 1
    shader.shader_flags_2.slsf_2_double_sided = 1

    alpha = NifFormat.NiAlphaProperty()
    alpha.flags = 4844        # alpha testing, GREATER (LODGen's own value)
    alpha.threshold = 128

    shape = NifFormat.NiTriShape()
    shape.name = b'TreeBillboard'
    shape.flags = _NIF_FLAGS
    shape.data = tsd
    shape.bs_properties.update_size()
    shape.bs_properties[0] = shader
    shape.bs_properties[1] = alpha
    try:
        shape.update_tangent_space(as_extra=False)
    except Exception:
        pass

    root = NifFormat.BSFadeNode()
    root.name = (stem + '_far').encode('latin1')
    root.flags = _NIF_FLAGS
    root.num_children = 1
    root.children.update_size()
    root.children[0] = shape

    data = NifFormat.Data()
    data.version = _SKYRIM_VER
    data.user_version = 12
    data.user_version_2 = 83
    data.header.endian_type = 1
    data.roots = [root]
    buf = io.BytesIO()
    try:
        data.write(buf)
    except Exception:
        return False

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_bytes(buf.getvalue())
    marker = dst_path.with_suffix('.nif.generated')
    marker.write_text('generated by lod_far_gen (tree billboard)\n',
                      encoding='utf-8')
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_far_nif(src_path: Path, dst_path: Path,
                     decimate_ratio: float = _DECIMATE_RATIO,
                     cap: int = _MAX_TARGET_VERTS,
                     max_dev_frac: float = _MAX_DEV_FRAC) -> bool:
    """Generate dst_path (_far.nif) by decimating each shape in src_path.

    Only processes NIFs already in Skyrim format (v20.2.0.7).
    Each shape retains its original BSLightingShaderProperty (correct flags
    and textures).  Returns True on success, False on skip/failure.

    A marker file <dst_path>.generated is written alongside the NIF so the
    pipeline knows this file was auto-generated (and may be overwritten on
    subsequent runs) rather than being a hand-crafted LOD mesh.
    """
    if not src_path.exists():
        return False

    nif_data = NifFormat.Data()
    try:
        with open(src_path, 'rb') as fh:
            nif_data.inspect(fh)
            if nif_data.version != _SKYRIM_VER:
                return False
            nif_data.read(fh)
    except Exception:
        return False

    if not _decimate_nif_inplace(nif_data, decimate_ratio, cap, max_dev_frac):
        return False

    # Rename root to <stem>_far
    for root in nif_data.roots:
        if root is not None:
            stem = src_path.stem
            root.name = (stem + '_far').encode('latin1') if not stem.endswith('_far') else stem.encode('latin1')

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    try:
        nif_data.write(buf)
    except Exception:
        return False

    with open(dst_path, 'wb') as fh:
        fh.write(buf.getvalue())

    # Write marker so regen passes know this is auto-generated
    marker = dst_path.with_suffix('.nif.generated')
    marker.write_text('generated by lod_far_gen\n', encoding='utf-8')
    return True


def _is_generated(far_path: Path) -> bool:
    """Return True if far_path was written by generate_far_nif (has marker)."""
    return far_path.with_suffix('.nif.generated').exists()


def is_tree_model(stat: dict) -> bool:
    """True if this stat should get billboard tree LOD."""
    if stat.get('sig') == 'TREE':
        return True
    rel = stat.get('model', '').lower().replace('/', '\\').lstrip('\\')
    if rel.startswith('meshes\\'):
        rel = rel[len('meshes\\'):]
    return rel.startswith(_TREE_MODEL_PREFIX)


def generate_missing_far_nifs(stats: dict, output_meshes_dir: Path,
                               referenced_models: 'set | None' = None,
                               workers: int = None,
                               force_regen_generated: bool = False,
                               tex_root: 'Path | None' = None) -> int:
    """Generate _far.nif files for all LOD-flagged stats that lack one.

    TREE-type stats get a crossed-quad billboard card (Oblivion's shipped
    billboard render); everything else is QEM-decimated from the full mesh.

    Args:
        stats:                  {form_id: {flags, model, ...}} from lod_gen._parse_esm()
        output_meshes_dir:      e.g. output/Oblivion.esm/meshes/
        referenced_models:      If provided, only generate for models in this set.
        workers:                Process count; defaults to cpu_count - 1.
        force_regen_generated:  If True, regenerate files that were previously
                                auto-generated (have a .nif.generated marker).
                                Hand-crafted _far.nif files (no marker) are
                                never overwritten.
        tex_root:               textures/ root (for billboard lookup); defaults
                                to <output_meshes_dir>/../textures.

    Returns the number of _far.nif files successfully created.
    """
    from .lod_gen import (_FLAG_DISTANT_LOD, _FLAG_WORLD_MAP, _far_nif_path,
                          _mesh_exists, _LOD8_MIN_SIZE, _obnd_max_dim)
    import multiprocessing as mp

    if workers is None:
        workers = max(1, (os.cpu_count() or 4) - 1)
    if tex_root is None:
        tex_root = output_meshes_dir.parent / 'textures'

    tasks: List[tuple] = []
    seen: set = set()

    for stat in stats.values():
        flags = stat.get('flags', 0)
        if not (flags & (_FLAG_DISTANT_LOD | _FLAG_WORLD_MAP)):
            continue
        model = stat.get('model', '')
        if not model or model in seen:
            continue
        seen.add(model)

        if referenced_models is not None and model not in referenced_models:
            continue

        # Resolve to filesystem paths
        rel = model.lower().replace('/', '\\').lstrip('\\')
        if rel.startswith('meshes\\'):
            rel = rel[len('meshes\\'):]
        src = output_meshes_dir / rel

        far_rel = _far_nif_path(rel.replace('\\', '/')).replace('/', '\\')
        dst = output_meshes_dir / far_rel

        far_exists = dst.exists()
        if far_exists:
            if not force_regen_generated:
                continue  # skip — we have a _far.nif and aren't forcing regen
            if not _is_generated(dst):
                continue  # skip — hand-crafted, never overwrite

        tree = is_tree_model(stat)
        if not src.exists() and not tree:
            continue  # source doesn't exist yet

        # Which far-ring tiers does this object need?  (Trees reuse their
        # billboard at every level, so they never need tier meshes.)
        need8  = (not tree) and _obnd_max_dim(stat) >= _LOD8_MIN_SIZE
        need16 = (not tree) and bool(flags & _FLAG_WORLD_MAP)

        tasks.append((src, dst, tree, stat.get('obnd'), rel, tex_root,
                      need8, need16))

    if not tasks:
        print(f'  LOD: all {len(seen)} unique models already have _far.nif')
        return 0

    print(f'  LOD: generating {len(tasks)} _far.nif files with {workers} workers...')
    success = failed = 0

    if workers <= 1:
        for task in tasks:
            if _far_nif_worker(task):
                success += 1
            else:
                failed += 1
    else:
        # Use multiprocessing.Pool for true CPU parallelism (PyFFI is GIL-bound)
        with mp.Pool(processes=workers) as pool:
            for ok in pool.imap_unordered(_far_nif_worker, tasks, chunksize=8):
                if ok:
                    success += 1
                else:
                    failed += 1

    print(f'  LOD: generated {success} _far.nif files ({failed} failed/skipped)')
    return success


def _tier_path(far_path: Path, suffix: str) -> Path:
    """foo_far.nif → foo<suffix>.nif (e.g. foo_far8.nif)."""
    stem = far_path.stem
    if stem.endswith('_far'):
        stem = stem[:-len('_far')]
    return far_path.with_name(stem + suffix + '.nif')


def _far_nif_worker(args: tuple) -> bool:
    """Top-level worker for multiprocessing.Pool — must be picklable."""
    src, dst, tree, obnd, model_rel, tex_root, need8, need16 = args
    if tree:
        if generate_tree_billboard_far(dst, obnd, model_rel, tex_root):
            return True
        # no billboard texture for this tree — fall back to decimation
    if not dst.exists() or _is_generated(dst):
        if not src.exists():
            return False
        if not generate_far_nif(src, dst):
            return False
    # Far-ring tiers are decimated FROM the _far.nif (also works for the
    # hand-crafted vanilla _far meshes, which are already low-poly).
    if need8:
        p8 = _tier_path(dst, _TIER8['suffix'])
        if not p8.exists() or _is_generated(p8):
            generate_far_nif(dst, p8, _TIER8['ratio'], _TIER8['cap'],
                             _TIER8['dev'])
    if need16:
        p16 = _tier_path(dst, _TIER16['suffix'])
        if not p16.exists() or _is_generated(p16):
            generate_far_nif(dst, p16, _TIER16['ratio'], _TIER16['cap'],
                             _TIER16['dev'])
    return True
