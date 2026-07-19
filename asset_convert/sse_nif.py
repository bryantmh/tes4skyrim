"""SSE-format NIF loading: BSTriShape graphs -> LE NiTriShape graphs.

The SSE "Skyrim - Meshes*.bsa" archives are the only guaranteed source of
vanilla Skyrim meshes on an SSE-only install, and they ship SSE-optimized
NIFs (BSTriShape / SSE NiSkinPartition).  pyffi_monkey_patch Patch 8 makes
pyffi READ those blocks; this module rebuilds them into ordinary LE blocks
(NiTriShape + NiTriShapeData + NiSkinData weights) so the rest of the
pipeline — skin splicing, partition regeneration, LE writing — works
unchanged.  Output is always written LE (User Version 2 = 83), which the SSE
engine loads natively.

Usage:
    from asset_convert.sse_nif import read_nif
    data = read_nif(path_or_bytes)      # any of LE / SSE / Oblivion format
    # SSE graphs come back as LE NiTriShape graphs, uv2 already set to 83.
"""

import io

import numpy as np

from . import pyffi_monkey_patch as _patch  # noqa: F401  (must precede pyffi)
from pyffi.formats.nif import NifFormat

_LE_UV2 = 83
_SSE_UV2 = 100


def read_nif(source):
    """Read a NIF from bytes or a path; convert SSE geometry to LE in place."""
    data = NifFormat.Data()
    if isinstance(source, (bytes, bytearray)):
        data.read(io.BytesIO(source))
    else:
        with open(source, 'rb') as f:
            data.read(f)
    sse_to_le(data)
    return data


def sse_to_le(data):
    """Rebuild every BSTriShape in the graph as NiTriShape (+data, weights).

    Returns the number of shapes converted.  Marks the Data as LE (uv2=83)
    so a subsequent write produces an LE NIF.
    """
    shapes = [b for b in getattr(data, 'blocks', [])
              if isinstance(b, NifFormat.BSTriShape)]
    replaced = {}
    for s in shapes:
        new = _convert_shape(s)
        if new is not None:
            replaced[id(s)] = new
    if replaced:
        _swap_children(data, replaced)
        data.roots = [replaced.get(id(r), r) for r in data.roots]
        data.blocks = [replaced.get(id(b), b) for b in data.blocks]
    if getattr(data, 'user_version_2', 0) == _SSE_UV2:
        data.user_version_2 = _LE_UV2
    return len(replaced)


# ---------------------------------------------------------------------------
# per-shape conversion
# ---------------------------------------------------------------------------

def _geometry_arrays(s):
    """Collect (verts, normals, uvs, colors, tangents, bitangents, tris)
    for a BSTriShape from either its inline buffer or its skin partition."""
    if s.sse_verts is not None:
        return (s.sse_verts, s.sse_normals, s.sse_uvs, s.sse_colors,
                s.sse_tangents, s.sse_bitangents, s.sse_triangles)
    skin = s.skin
    sp = getattr(skin, 'skin_partition', None) if skin is not None else None
    if sp is None or getattr(sp, 'sse_verts', None) is None:
        return None
    tris = [p['triangles_copy'] for p in sp.sse_partitions
            if p['triangles_copy'] is not None and len(p['triangles_copy'])]
    tris = np.vstack(tris) if tris else np.zeros((0, 3), dtype='<u2')
    return (sp.sse_verts, sp.sse_normals, sp.sse_uvs, sp.sse_colors,
            sp.sse_tangents, sp.sse_bitangents, tris)


def _convert_shape(s):
    arrays = _geometry_arrays(s)
    if arrays is None:
        return None
    verts, normals, uvs, colors, tangents, bitangents, tris = arrays
    nv = len(verts)
    nt = len(tris)

    ts_data = NifFormat.NiTriShapeData()
    ts_data.consistency_flags = 0x4000
    ts_data.num_vertices = nv
    ts_data.has_vertices = True
    ts_data.vertices.update_size()
    for i in range(nv):
        ts_data.vertices[i].x = float(verts[i, 0])
        ts_data.vertices[i].y = float(verts[i, 1])
        ts_data.vertices[i].z = float(verts[i, 2])
    if normals is not None:
        ts_data.has_normals = True
        ts_data.normals.update_size()
        for i in range(nv):
            ts_data.normals[i].x = float(normals[i, 0])
            ts_data.normals[i].y = float(normals[i, 1])
            ts_data.normals[i].z = float(normals[i, 2])
    if tangents is not None and bitangents is not None:
        ts_data.extra_vectors_flags = 16
        ts_data.tangents.update_size()
        ts_data.bitangents.update_size()
        for i in range(nv):
            ts_data.tangents[i].x = float(tangents[i, 0])
            ts_data.tangents[i].y = float(tangents[i, 1])
            ts_data.tangents[i].z = float(tangents[i, 2])
            ts_data.bitangents[i].x = float(bitangents[i, 0])
            ts_data.bitangents[i].y = float(bitangents[i, 1])
            ts_data.bitangents[i].z = float(bitangents[i, 2])
    if uvs is not None:
        ts_data.num_uv_sets = 1
        ts_data.uv_sets.update_size()
        for i in range(nv):
            ts_data.uv_sets[0][i].u = float(uvs[i, 0])
            ts_data.uv_sets[0][i].v = float(uvs[i, 1])
    if colors is not None:
        ts_data.has_vertex_colors = True
        ts_data.vertex_colors.update_size()
        for i in range(nv):
            c = ts_data.vertex_colors[i]
            c.r = colors[i, 0] / 255.0
            c.g = colors[i, 1] / 255.0
            c.b = colors[i, 2] / 255.0
            c.a = colors[i, 3] / 255.0
    ts_data.num_triangles = nt
    ts_data.num_triangle_points = nt * 3
    ts_data.has_triangles = True
    ts_data.triangles.update_size()
    for i in range(nt):
        ts_data.triangles[i].v_1 = int(tris[i, 0])
        ts_data.triangles[i].v_2 = int(tris[i, 1])
        ts_data.triangles[i].v_3 = int(tris[i, 2])
    ts_data.center.x = float(s.center.x)
    ts_data.center.y = float(s.center.y)
    ts_data.center.z = float(s.center.z)
    ts_data.radius = float(s.radius)

    new = NifFormat.NiTriShape()
    new.name = bytes(s.name)
    new.flags = int(s.flags)
    new.translation.x = s.translation.x
    new.translation.y = s.translation.y
    new.translation.z = s.translation.z
    r, nr = s.rotation, new.rotation
    nr.m_11, nr.m_12, nr.m_13 = r.m_11, r.m_12, r.m_13
    nr.m_21, nr.m_22, nr.m_23 = r.m_21, r.m_22, r.m_23
    nr.m_31, nr.m_32, nr.m_33 = r.m_31, r.m_32, r.m_33
    new.scale = s.scale
    new.data = ts_data
    new.num_extra_data_list = s.num_extra_data_list
    new.extra_data_list.update_size()
    for i in range(s.num_extra_data_list):
        new.extra_data_list[i] = s.extra_data_list[i]
    new.controller = s.controller
    new.collision_object = s.collision_object
    if s.shader_property is not None:
        new.bs_properties[0] = s.shader_property
    if s.alpha_property is not None:
        new.bs_properties[1] = s.alpha_property

    skin = s.skin
    if skin is not None:
        new.skin_instance = skin
        _ensure_skin_weights(skin)
        # Rebuild the SSE partition as a faithful LE NiSkinPartition: the
        # per-partition bones / vertex maps / weights / local triangles map
        # 1:1, which preserves the semantic body-part partition structure
        # (e.g. the vanilla body's 32/34/38 split) that a from-scratch
        # regeneration would destroy.
        skin.skin_partition = _le_partition_from_sse(skin.skin_partition)
    return new


def _le_partition_from_sse(sp):
    if sp is None or not getattr(sp, 'sse_partitions', None):
        return None
    new_sp = NifFormat.NiSkinPartition()
    parts = sp.sse_partitions
    new_sp.num_skin_partition_blocks = len(parts)
    new_sp.skin_partition_blocks.update_size()
    for pi, p in enumerate(parts):
        blk = new_sp.skin_partition_blocks[pi]
        vmap = p['vertex_map']
        weights = p['weights']
        bidx = p['bone_indices']
        # SSE stores GLOBAL shape-vertex indices in both triangle arrays
        # (verified: in-struct Triangles == Triangles Copy on vanilla bodies);
        # the LE convention is partition-LOCAL indices into the vertex map.
        tris = p['triangles'] if p['triangles'] is not None else p['triangles_copy']
        if tris is not None and vmap is not None and len(vmap):
            inv = np.full(int(vmap.max()) + 1, -1, dtype=np.int64)
            inv[vmap.astype(np.int64)] = np.arange(len(vmap))
            local = inv[tris.astype(np.int64)]
            keep = (local >= 0).all(axis=1)
            tris = local[keep].astype('<u2')
        if weights is None and sp.sse_bone_weights is not None and vmap is not None:
            weights = sp.sse_bone_weights[vmap]
            bidx = sp.sse_bone_indices[vmap]
        nv = len(vmap) if vmap is not None else 0
        nt = len(tris) if tris is not None else 0
        wpv = p['num_weights_per_vertex']
        blk.num_vertices = nv
        blk.num_triangles = nt
        blk.num_bones = len(p['bones'])
        blk.num_strips = 0
        blk.num_weights_per_vertex = wpv
        blk.bones.update_size()
        for i, b in enumerate(p['bones']):
            blk.bones[i] = int(b)
        blk.has_vertex_map = vmap is not None
        if vmap is not None:
            blk.vertex_map.update_size()
            for i in range(nv):
                blk.vertex_map[i] = int(vmap[i])
        blk.has_vertex_weights = weights is not None
        if weights is not None:
            blk.vertex_weights.update_size()
            for i in range(nv):
                for k in range(wpv):
                    blk.vertex_weights[i][k] = float(weights[i, k])
        blk.has_faces = tris is not None
        if tris is not None:
            blk.triangles.update_size()
            for i in range(nt):
                blk.triangles[i].v_1 = int(tris[i, 0])
                blk.triangles[i].v_2 = int(tris[i, 1])
                blk.triangles[i].v_3 = int(tris[i, 2])
        blk.has_bone_indices = bidx is not None
        if bidx is not None:
            blk.bone_indices.update_size()
            for i in range(nv):
                for k in range(wpv):
                    blk.bone_indices[i][k] = int(bidx[i, k])
    return new_sp


def _ensure_skin_weights(skin):
    """Guarantee per-bone vertex weights on NiSkinData.

    Vanilla SSE NiSkinData usually still carries LE-style weights; when it
    does not (has_vertex_weights == 0) rebuild them from the SSE partition's
    weight/bone-index/vertex-map arrays (bone indices are partition-local)."""
    sd = getattr(skin, 'data', None)
    sp = getattr(skin, 'skin_partition', None)
    if sd is None or getattr(sd, 'has_vertex_weights', 1):
        return
    if sp is None or not getattr(sp, 'sse_partitions', None):
        return
    per_bone: dict = {}
    for p in sp.sse_partitions:
        vmap = p['vertex_map']
        weights = p['weights']
        bidx = p['bone_indices']
        if weights is None and sp.sse_bone_weights is not None and vmap is not None:
            weights = sp.sse_bone_weights[vmap]
            bidx = sp.sse_bone_indices[vmap]
        if vmap is None or weights is None or bidx is None:
            continue
        for vi_local in range(len(vmap)):
            gvi = int(vmap[vi_local])
            for k in range(weights.shape[1]):
                w = float(weights[vi_local, k])
                if w <= 0.0:
                    continue
                gbi = int(p['bones'][int(bidx[vi_local, k])])
                per_bone.setdefault(gbi, {})[gvi] = w
    sd.has_vertex_weights = 1
    for gbi, vw in per_bone.items():
        if gbi >= sd.num_bones:
            continue
        be = sd.bone_list[gbi]
        items = sorted(vw.items())
        be.num_vertices = len(items)
        be.vertex_weights.update_size()
        for i, (gvi, w) in enumerate(items):
            be.vertex_weights[i].index = gvi
            be.vertex_weights[i].weight = w


def _swap_children(data, replaced):
    """Point every child/ref in the graph at the replacement shapes."""
    for block in data.blocks:
        children = getattr(block, 'children', None)
        if children is None:
            continue
        for i in range(getattr(block, 'num_children', 0)):
            c = children[i]
            if c is not None and id(c) in replaced:
                children[i] = replaced[id(c)]
