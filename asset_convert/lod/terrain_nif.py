"""Building the .btr terrain-LOD tile mesh (land shape, water node, root).

Split out of `terrain_lod.py`, which owns the LAND parsing, tile assembly and
run orchestration; this module owns only the NIF that one tile becomes, and the
landmass extent that decides which of its cells draw at all.

See: docs/commentary/asset_convert_terrain.md#terrain-lod-sselodgen--data-chain
"""

import io
import math

import numpy as np

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()

try:
    from pyffi.formats.nif import NifFormat
    PYFFI_AVAILABLE = True
except ImportError:
    PYFFI_AVAILABLE = False

#: Skyrim world units per exterior cell, on both the X and Y axes.
CELL_SIZE = 4096.0

#: Vertices per tile side in the emitted mesh (32 quad intervals).
TILE_VERTS = 33

#: Slack added to every bounding-box Z half-extent, in world units.
Z_MARGIN = 500.0


# ---------------------------------------------------------------------------
# Landmass extent
# ---------------------------------------------------------------------------

def tile_solid_mask(lands, tile_x: int, tile_y: int, level: int):
    """Which of a tile's cells own a LAND record, indexed [cy][cx] from the SW.

    See: docs/commentary/asset_convert_terrain.md#lod-invents-terrain-over-cells-with-no-land
    """
    return [[(tile_x + cx, tile_y + cy) in lands for cx in range(level)]
            for cy in range(level)]


# ---------------------------------------------------------------------------
# LOD water (vanilla-style)
# ---------------------------------------------------------------------------

def _set_local_bounding_sphere(shapedata, verts):
    """Set the shape's bounding sphere in LOCAL coords, vanilla-style.

    Vanilla uses the bbox center with the corner distance as the radius.
    Returns the (lo, hi, center) bbox arrays for callers that also need them.
    """
    va = np.array(verts, dtype=np.float64)
    lo = va.min(axis=0)
    hi = va.max(axis=0)
    ctr = (lo + hi) / 2.0
    shapedata.center.x, shapedata.center.y, shapedata.center.z = ctr
    shapedata.radius = float(np.linalg.norm((hi - lo) / 2.0))
    return lo, hi, ctr


def _segment_quads(quad_map, sx: int, sy: int, span: int):
    """The water quads inside one segment of the 4x4 grid, in cell order."""
    out = []
    for cx in range(sx * span, (sx + 1) * span):
        for cy in range(sy * span, (sy + 1) * span):
            wh = quad_map.get((cx, cy))
            if wh is not None:
                out.append((cx, cy, wh))
    return out


def _water_segments(water_quads, level: int):
    """Order the water quads into vanilla's fixed 4x4 segment grid.

    Segments are column-major (index = sx*4 + sy) and let the engine hide the
    quad for cells loaded at full detail.  Returns (ordered quads, per-segment
    triangle count, per-segment start index in triangle POINTS) — vanilla
    stores a start of 0 for an empty segment.
    """
    quad_map = {(cx, cy): wh for cx, cy, wh in water_quads}
    span = max(1, level // 4)
    ordered = []
    seg_num_prims = [0] * 16
    seg_start = [0] * 16
    for sx in range(4):
        for sy in range(4):
            seg = sx * 4 + sy
            n_before = len(ordered)
            ordered.extend(_segment_quads(quad_map, sx, sy, span))
            count = len(ordered) - n_before
            seg_num_prims[seg] = count * 2
            seg_start[seg] = n_before * 6 if count else 0
    return ordered, seg_num_prims, seg_start


def _water_shapedata(ordered, level: int):
    """Flat unshared quads for the water shape, one per water cell.

    Quads are unshared (4 verts each) so per-cell heights can differ; verts are
    local 0..4096 like the land, Z = water height / level.  Returns
    (shapedata, lo, hi, center).
    """
    cell_local = CELL_SIZE / level
    scale = float(level)
    verts = []
    tris = []
    for cx, cy, wh in ordered:
        x0 = cx * cell_local
        y0 = cy * cell_local
        z = wh / scale
        b = len(verts)
        verts += [(x0, y0, z), (x0 + cell_local, y0, z),
                  (x0, y0 + cell_local, z),
                  (x0 + cell_local, y0 + cell_local, z)]
        tris += [(b, b + 1, b + 2), (b + 1, b + 3, b + 2)]

    shapedata = NifFormat.NiTriShapeData()
    shapedata.has_vertices = True
    shapedata.has_normals = False
    shapedata.num_uv_sets = 0
    shapedata.has_uv = False
    shapedata.num_vertices = len(verts)
    shapedata.vertices.update_size()
    for i, (x, y, z) in enumerate(verts):
        shapedata.vertices[i].x = x
        shapedata.vertices[i].y = y
        shapedata.vertices[i].z = z
    _set_triangles(shapedata, tris)
    lo, hi, ctr = _set_local_bounding_sphere(shapedata, verts)
    return shapedata, lo, hi, ctr


def _water_shape(shapedata, level: int, seg_num_prims, seg_start):
    """The water geometry node: segmented at LOD4, a plain NiTriShape above it.

    Only LOD4 tiles overlap the loaded-cell area, so only they need segments.
    PyFFI's BSSegment fields are misaligned over the true 9-byte layout
    (flags byte 0 | start_index uint | num_primitives uint): `internal_index`
    covers flags+start[0:3], and its `flags` bitstruct covers
    start[3]+num_primitives[0:3], where num_primitives=2 lands exactly on the
    bsseg_water bit.
    """
    if level == 4:
        shape = NifFormat.BSSegmentedTriShape()
        shape.num_segments = 16
        shape.segment.update_size()
        for i in range(16):
            seg = shape.segment[i]
            seg.internal_index = (seg_start[i] << 8) & 0xFFFFFFFF
            seg.flags.bsseg_water = 1 if seg_num_prims[i] else 0
            seg.unknown_byte_1 = 0
    else:
        shape = NifFormat.NiTriShape()
    shape.name = b''
    shape.flags = 14
    shape.scale = float(level)
    shape.data = shapedata
    return shape


def _build_water_node(water_quads, level: int):
    """Build the vanilla-style LOD water node for a tile.

    A BSMultiBoundNode named "WATER" holding one shape with an independent flat
    quad per water cell.  It carries NO shader property, UVs or normals: the
    engine attaches the worldspace LOD water shader from WRLD NAM3 itself.  The
    AABB spans the quads' XY bbox in world units, Z [min, max(max, 0)].
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-sselodgen--data-chain
    """
    ordered, seg_num_prims, seg_start = _water_segments(water_quads, level)
    shapedata, lo, hi, ctr = _water_shapedata(ordered, level)
    shape = _water_shape(shapedata, level, seg_num_prims, seg_start)
    scale = float(level)

    whs = [wh for _, _, wh in ordered]
    z_lo = min(whs)
    z_hi = max(max(whs), 0.0)
    aabb = NifFormat.BSMultiBoundAABB()
    aabb.position.x = float(ctr[0] * scale)
    aabb.position.y = float(ctr[1] * scale)
    aabb.position.z = (z_lo + z_hi) / 2.0
    aabb.extent.x = float((hi[0] - lo[0]) / 2.0 * scale)
    aabb.extent.y = float((hi[1] - lo[1]) / 2.0 * scale)
    aabb.extent.z = (z_hi - z_lo) / 2.0

    multi_bound = NifFormat.BSMultiBound()
    multi_bound.data = aabb

    wnode = NifFormat.BSMultiBoundNode()
    wnode.name = b'WATER'
    wnode.flags = 14
    wnode.multi_bound = multi_bound
    wnode.num_children = 1
    wnode.children.update_size()
    wnode.children[0] = shape
    return wnode


# ---------------------------------------------------------------------------
# Land shape
# ---------------------------------------------------------------------------

def _set_triangles(shapedata, tris):
    """Write `tris` into a NiTriShapeData, sizing the array to match."""
    shapedata.num_triangles = len(tris)
    shapedata.num_triangle_points = len(tris) * 3
    shapedata.has_triangles = True
    shapedata.triangles.update_size()
    for i, (a, b, c) in enumerate(tris):
        shapedata.triangles[i].v_1 = a
        shapedata.triangles[i].v_2 = b
        shapedata.triangles[i].v_3 = c


def _land_triangles(level: int, solid_mask):
    """Quad triangles for the land shape, skipping cells with no ground.

    Wound front-face UP (+Z): with X=col, Y=row, i0->i1->i2 is CCW seen from
    +Z; the reverse order is back-facing and renders only from below.
    `solid_mask[cy][cx]` False drops that cell's quads, so no ground is drawn
    past the coastline even though the heightmap still fills those verts.
    See: docs/commentary/asset_convert_terrain.md#lod-invents-terrain-over-cells-with-no-land
    """
    tv = TILE_VERTS
    quads_per_cell = (tv - 1) // level
    tris = []
    for row in range(tv - 1):
        for col in range(tv - 1):
            if solid_mask is not None and not solid_mask[
                    row // quads_per_cell][col // quads_per_cell]:
                continue
            i0 = row * tv + col
            i1 = i0 + 1
            i2 = i0 + tv
            i3 = i2 + 1
            tris.append((i0, i1, i2))
            tris.append((i1, i3, i2))
    return tris


def _set_land_bounds(shapedata, heights: np.ndarray, level: int):
    """Set the land shape's bounding sphere, centred in WORLD space."""
    z_min = float(heights.min())
    z_max = float(heights.max())
    xy_half = CELL_SIZE / 2.0 * level
    z_half = (z_max - z_min) / 2.0 + Z_MARGIN
    shapedata.center.x = xy_half
    shapedata.center.y = xy_half
    shapedata.center.z = (z_min + z_max) / 2.0
    shapedata.radius = math.sqrt(xy_half**2 + xy_half**2 + z_half**2)


def _land_shapedata(heights: np.ndarray, level: int, tris):
    """NiTriShapeData for the land: 33x33 verts, UVs, `tris`, bounding sphere.

    Subsamples to 33x33 verts at local step 128, matching vanilla's decimated
    LOD4 count; LOD8+ at full resolution overflows uint16.  Z is pre-divided by
    the scale, so vertex_z * scale is the world height.  UVs are vanilla's
    u = x/4096, v = 1 - y/4096, v=0 at the NORTH edge to match DDS row 0.
    """
    tv = TILE_VERTS
    step = CELL_SIZE / (tv - 1)
    h33 = heights[::level, ::level]
    world_scale = float(level)

    shapedata = NifFormat.NiTriShapeData()
    shapedata.has_vertices = True
    shapedata.has_normals = False
    shapedata.num_uv_sets = 1
    shapedata.has_uv = True
    shapedata.num_vertices = tv * tv
    shapedata.vertices.update_size()
    shapedata.uv_sets.update_size()
    for row in range(tv):
        for col in range(tv):
            i = row * tv + col
            shapedata.vertices[i].x = col * step
            shapedata.vertices[i].y = row * step
            shapedata.vertices[i].z = float(h33[row, col]) / world_scale
            shapedata.uv_sets[0][i].u = col * step / CELL_SIZE
            shapedata.uv_sets[0][i].v = 1.0 - (row * step / CELL_SIZE)

    _set_triangles(shapedata, tris)
    _set_land_bounds(shapedata, heights, level)
    return shapedata


def _land_shader(tile_x: int, tile_y: int, level: int, edid: str):
    """The kLODLandscapeNoise shader property and its diffuse/normal texture set.

    `uv_scale` must be (1,1); PyFFI defaults it to (0,0), which breaks the LOD
    landscape shader.
    """
    tex_base = f'textures\\terrain\\{edid}\\{edid}.{level}.{tile_x}.{tile_y}'
    texset = NifFormat.BSShaderTextureSet()
    texset.num_textures = 9
    texset.textures.update_size()
    texset.textures[0] = f'Data\\{tex_base}.dds'.encode()
    texset.textures[1] = f'Data\\{tex_base}_n.dds'.encode()

    shader = NifFormat.BSLightingShaderProperty()
    shader.skyrim_shader_type = 18
    shader.texture_set = texset
    sf1 = shader.shader_flags_1
    sf1.slsf_1_model_space_normals = 1
    sf1.slsf_1_own_emit = 1
    sf1.slsf_1_z_buffer_test = 1
    sf2 = shader.shader_flags_2
    sf2.slsf_2_lod_landscape = 1
    sf2.slsf_2_z_buffer_write = 1
    shader.uv_scale.u = 1.0
    shader.uv_scale.v = 1.0
    return shader


def _build_land_shape(heights: np.ndarray, tile_x: int, tile_y: int,
                      level: int, edid: str, solid_mask, allow_empty=False):
    """The land NiTriShape for a tile, or None when no cell in it draws."""
    src_tv = level * 32 + 1
    assert heights.shape == (src_tv, src_tv), \
        f"Expected heights shape ({src_tv},{src_tv}), got {heights.shape}"

    tris = _land_triangles(level, solid_mask)
    if not tris and not allow_empty:
        return None

    shape = NifFormat.NiTriShape()
    shape.name = b'land'
    shape.flags = 14
    shape.scale = float(level)
    shape.data = _land_shapedata(heights, level, tris)
    shape.bs_properties[0] = _land_shader(tile_x, tile_y, level, edid)
    return shape


# ---------------------------------------------------------------------------
# Tile root
# ---------------------------------------------------------------------------

def _skyrim_le_nif():
    """An empty NifFormat.Data tagged little-endian Skyrim LE (20.2.0.7)."""
    nif_data = NifFormat.Data()
    nif_data.version = 0x14020007
    nif_data.user_version = 12
    nif_data.user_version_2 = 83
    nif_data.header.endian_type = 1
    return nif_data


def _chunk_root(heights: np.ndarray, level: int, water_quads=None):
    """The BSMultiBoundNode "chunk" root, which Skyrim LOD culling requires."""
    z_min = float(heights.min())
    z_max = float(heights.max())
    if water_quads:
        z_min = min(z_min, min(wh for _, _, wh in water_quads))
        z_max = max(z_max, max(wh for _, _, wh in water_quads))
    world_half = CELL_SIZE * level / 2.0

    aabb = NifFormat.BSMultiBoundAABB()
    aabb.position.x = world_half
    aabb.position.y = world_half
    aabb.position.z = (z_min + z_max) / 2.0
    aabb.extent.x = world_half
    aabb.extent.y = world_half
    aabb.extent.z = (z_max - z_min) / 2.0 + Z_MARGIN

    multi_bound = NifFormat.BSMultiBound()
    multi_bound.data = aabb

    root = NifFormat.BSMultiBoundNode()
    root.name = b'chunk'
    root.flags = 14
    root.multi_bound = multi_bound
    return root


def build_terrain_nif(heights: np.ndarray, tile_x: int, tile_y: int,
                      level: int, edid: str,
                      water_quads=None, solid_mask=None):
    """A tile's .btr bytes, or None when the tile would draw nothing.

    `heights` is the full-res (level*32+1)^2 grid.  `solid_mask` marks which of
    the level*level cells carry drawable ground; a tile whose every cell lies
    past the coastline with no water yields None. Water-only tiles retain an
    empty land shape at child[0], keeping WATER at the engine's child[1]. Water
    present, becomes child[1] and the engine textures it from WRLD NAM3.
    """
    if not PYFFI_AVAILABLE:
        raise RuntimeError("pyffi not available")

    shape = _build_land_shape(heights, tile_x, tile_y, level, edid, solid_mask,
                              allow_empty=bool(water_quads))
    if shape is None:
        return None

    root = _chunk_root(heights, level, water_quads)
    children = [shape]
    if water_quads:
        children.append(_build_water_node(water_quads, level))
    root.num_children = len(children)
    root.children.update_size()
    for i, child in enumerate(children):
        root.children[i] = child

    nif_data = _skyrim_le_nif()
    nif_data.roots = [root]
    buf = io.BytesIO()
    nif_data.write(buf)
    return buf.getvalue()
