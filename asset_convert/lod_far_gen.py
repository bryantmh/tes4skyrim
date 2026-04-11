"""Generate _far.nif LOD meshes by decimating full-resolution Skyrim NIFs.

Called from lod_gen.generate_lod() as a pre-pass to ensure all LOD-flagged
objects have a _far.nif before LODGenx64 is invoked.

Algorithm
---------
1. Read the converted Skyrim NIF (v20.2.0.7, BSStream 83).
2. Walk all solid (non-skinned) geometry **per shape in local space**.
3. Apply grid-based vertex-clustering decimation (O(nV + nT)), targeting
   ~8% of the original vertex count per shape.
4. Recompute smooth per-vertex normals; recompute tangent/bitangent vectors
   from UV differentials (standard tangent-space method).
5. Re-write the NIF in-place: keep every NiTriShape that survives decimation
   with its *original* BSLightingShaderProperty and textures intact.
   Strip collision, controllers, skin, vertex colors, and extra data from
   all nodes.
6. Clear the VertexColors shader flag (SF2 bit 0x20) since vertex colors are
   removed.
7. Write to <model_base>_far.nif.

Key difference from v1: geometry is NOT merged into a single world-space mesh.
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

# Global target: 5% of total source verts across all shapes combined.
# This matches empirically observed ratios in vanilla _far.nif files.
_DECIMATE_RATIO    = 0.07
_MIN_TRIS          = 12      # min output tris to keep a shape
_MIN_SRC_TRIS      = 20      # min source tris to include a shape at all
_SF2_VERTEX_COLORS = 0x20    # SF2 bit to clear when removing vertex colors


# ---------------------------------------------------------------------------
# Decimation helpers
# ---------------------------------------------------------------------------


def _cluster_decimate(verts: np.ndarray, tris: np.ndarray,
                      uvs: Optional[np.ndarray],
                      target_verts: int) -> Tuple:
    """Position-only vertex-clustering targeting target_verts output vertices.

    Each cluster uses the FIRST-SEEN vertex's UV (not an average).  Averaging
    UVs across texture-seam duplicates (e.g. one vertex at UV=(0,0.5) and its
    seam-mirror at UV=(1,0.5)) produces UV=(0.5,0.5) which samples from the
    wrong texture region — causing invisible or mis-mapped faces.  Taking the
    first-seen UV preserves at least one correct sample per cluster.

    Architecture geometry is sparse in 3-D (mostly flat surfaces), so the grid
    n is scaled 2× larger than the naïve cube-root to hit the target density.
    """
    if not len(verts) or not len(tris):
        return verts, tris, uvs

    n = max(4, int(math.ceil((max(target_verts, 8) * 2.0) ** (1.0 / 3.0))))
    lo = verts.min(axis=0)
    hi = verts.max(axis=0)
    span = hi - lo
    cell = np.where(span > 1e-6, span / n, 1.0)
    coords = np.floor((verts - lo) / cell).astype(np.int64)
    coords  = np.clip(coords, 0, n - 1)
    key = coords[:, 0] * n * n + coords[:, 1] * n + coords[:, 2]

    unique_keys, inv = np.unique(key, return_inverse=True)
    m = len(unique_keys)

    # Average position per cluster
    nv  = np.zeros((m, 3), np.float32)
    cnt = np.zeros(m, np.float32)
    np.add.at(nv, inv, verts)
    np.add.at(cnt, inv, 1)
    nv /= np.maximum(cnt[:, None], 1)

    # First-seen UV per cluster — avoids averaging across seam duplicates
    nuv = None
    if uvs is not None:
        nuv  = np.zeros((m, 2), np.float32)
        seen = np.zeros(m, dtype=bool)
        for old_i in range(len(verts)):
            cid = inv[old_i]
            if not seen[cid]:
                nuv[cid] = uvs[old_i]
                seen[cid] = True

    # Remap triangles, drop degenerate
    nt = inv[tris.ravel()].reshape(-1, 3)
    a, b, c = nt[:, 0], nt[:, 1], nt[:, 2]
    ok = (a != b) & (b != c) & (a != c)
    nt = nt[ok]

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


def _decimate_shape_inplace(shape, target_verts: int) -> bool:
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

    d_v, d_t, d_uv = _cluster_decimate(verts, tris, uvs, target_verts)
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


def _decimate_children(node, targets: Dict[int, int]) -> int:
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
            if _decimate_shape_inplace(child, target):
                keep.append(child)
                survivors += 1
        elif isinstance(child, NifFormat.NiNode):
            _strip_node(child)
            sub = _decimate_children(child, targets)
            if sub > 0:
                keep.append(child)
                survivors += sub
        # NiTriStrips should not appear in converted Skyrim NIFs; skip others

    node.num_children = len(keep)
    node.children.update_size()
    for i, c in enumerate(keep):
        node.children[i] = c

    return survivors


def _decimate_nif_inplace(nif_data, ratio: float) -> bool:
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

    total_target = max(200, int(total_source * ratio))

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
        survivors += _decimate_children(root, targets)
    return survivors > 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_far_nif(src_path: Path, dst_path: Path,
                     decimate_ratio: float = _DECIMATE_RATIO) -> bool:
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

    if not _decimate_nif_inplace(nif_data, decimate_ratio):
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


def generate_missing_far_nifs(stats: dict, output_meshes_dir: Path,
                               referenced_models: 'set | None' = None,
                               workers: int = None,
                               force_regen_generated: bool = False) -> int:
    """Generate _far.nif files for all LOD-flagged stats that lack one.

    Args:
        stats:                  {form_id: {flags, model, ...}} from lod_gen._parse_esm()
        output_meshes_dir:      e.g. output/Oblivion.esm/meshes/
        referenced_models:      If provided, only generate for models in this set.
        workers:                Process count; defaults to cpu_count - 1.
        force_regen_generated:  If True, regenerate files that were previously
                                auto-generated (have a .nif.generated marker).
                                Hand-crafted _far.nif files (no marker) are
                                never overwritten.

    Returns the number of _far.nif files successfully created.
    """
    from .lod_gen import _FLAG_DISTANT_LOD, _FLAG_WORLD_MAP, _far_nif_path, _mesh_exists
    import multiprocessing as mp

    if workers is None:
        workers = max(1, (os.cpu_count() or 4) - 1)

    tasks: List[Tuple[Path, Path]] = []
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

        if not src.exists():
            continue  # source doesn't exist yet

        tasks.append((src, dst))

    if not tasks:
        print(f'  LOD: all {len(seen)} unique models already have _far.nif')
        return 0

    print(f'  LOD: generating {len(tasks)} _far.nif files with {workers} workers...')
    success = failed = 0

    if workers <= 1:
        for src, dst in tasks:
            if generate_far_nif(src, dst):
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


def _far_nif_worker(args: Tuple[Path, Path]) -> bool:
    """Top-level worker for multiprocessing.Pool — must be picklable."""
    src, dst = args
    return generate_far_nif(src, dst)
