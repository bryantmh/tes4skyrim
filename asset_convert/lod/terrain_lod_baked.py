"""The source game's own distant-land LOD: baked tile textures and height meshes.

Oblivion ships, per worldspace (named by its DECIMAL FormID), 32-cell tiles:

  textures\\landscapelod\\generated\\<fid>.<x>.<y>.32.dds   diffuse, stored SOUTH-UP
  meshes\\landscape\\lod\\<fid>.<x>.<y>.32.nif              NiTriStrips heightfield

They are the only authored source for land past the LAND records (the horizon
mountains) and for quadrants no layer paints. Plugins ship their own copies in
any case (`meshes\\Landscape\\LOD\\...NIF`), some in tile space with the tile
origin as the node translation, some (UOP's 60.64.00) in world space with no
translation, so meshes are rasterised in WORLD space and discovery ignores case.

See: docs/commentary/asset_convert_terrain.md#baked-lod-sources
"""

import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from asset_convert import case_paths
from core.worldspace_names import converted_worldspace_edid
from output_layout import assets_for

#: Cells per side of one shipped LOD tile.
TILE_CELLS = 32

#: Game units per cell side.
CELL_UNITS = 4096

#: Height samples per cell side, shared with LAND (32 intervals).
CELL_VERTS = 33

#: Raster spacing in game units: one sample per LAND vertex.
RASTER_STEP = CELL_UNITS // (CELL_VERTS - 1)

#: Samples per tile side (the far edge included).
TILE_SAMPLES = TILE_CELLS * (CELL_VERTS - 1) + 1

_TILE_NAME = re.compile(r'^(\d+)\.(-?\d+)\.(-?\d+)\.32\.(nif|dds)$', re.I)

_MESH_DIR = 'meshes/landscape/lod'
_TEX_DIR = 'textures/landscapelod/generated'


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def worldspace_fids(record_dir, edid: str) -> set:
    """The raw FormIDs `record_dir`'s export (or a master's) gives worldspace `edid`."""
    from asset_convert.lod.terrain_lod import worldspace_edids
    want = edid.lower()
    return {fid for fid, name in worldspace_edids(Path(record_dir)).items()
            if want in (name.lower(), converted_worldspace_edid(name).lower())}


def _tiles_in(asset_dir, sub: str, ext: str, fids: set) -> dict:
    """{(tile x, tile y): path} of `fids`' shipped 32-cell tiles in `asset_dir/sub`."""
    out = {}
    for path in case_paths.list_prefix(asset_dir, sub, ''):
        got = _TILE_NAME.match(path.name)
        if got and got.group(4).lower() == ext and int(got.group(1)) in fids:
            out[(int(got.group(2)), int(got.group(3)))] = path
    return out


def _sources(record_dirs, edid: str) -> list:
    """[(asset dir, fids)] for each record dir, in load order."""
    return [(assets_for(Path(d)), worldspace_fids(d, edid))
            for d in (record_dirs or [])]


def baked_textures(record_dirs, edid: str) -> dict:
    """{(tile x, tile y): baked diffuse path}; the last plugin in load order wins."""
    out = {}
    for asset_dir, fids in _sources(record_dirs, edid):
        out.update(_tiles_in(asset_dir, _TEX_DIR, 'dds', fids))
    return out


def lod_meshes(record_dirs, edid: str) -> dict:
    """{(tile x, tile y): [(plugin label, mesh path), ...]} in load order."""
    out = {}
    for asset_dir, fids in _sources(record_dirs, edid):
        for key, path in sorted(_tiles_in(asset_dir, _MESH_DIR, 'nif',
                                          fids).items()):
            out.setdefault(key, []).append((Path(asset_dir).name, path))
    return out


# ---------------------------------------------------------------------------
# Rasterising a LOD mesh
# ---------------------------------------------------------------------------


def _local_matrix(node) -> np.ndarray:
    """`node`'s 4x4 transform (scale, rotation, translation), column vectors."""
    r = node.rotation
    rot = np.array([[r.m_11, r.m_21, r.m_31],
                    [r.m_12, r.m_22, r.m_32],
                    [r.m_13, r.m_23, r.m_33]], dtype=np.float64)
    out = np.eye(4)
    out[:3, :3] = rot * float(node.scale)
    t = node.translation
    out[:3, 3] = (t.x, t.y, t.z)
    return out


def _world_triangles(data) -> tuple:
    """(world-space vertices (n,3), triangles (m,3)) of every strip shape in `data`."""
    from pyffi.formats.nif import NifFormat
    verts, tris = [], []

    def walk(node, parent):
        """Collect `node`'s geometry under the accumulated `parent` transform."""
        mat = parent @ _local_matrix(node)
        if isinstance(node, NifFormat.NiTriBasedGeom) and node.data:
            v = np.array([[p.x, p.y, p.z, 1.0] for p in node.data.vertices])
            tris.append(np.array(node.data.get_triangles(), dtype=np.int64)
                        + sum(len(x) for x in verts))
            verts.append((mat @ v.T).T[:, :3])
        for child in getattr(node, 'children', None) or []:
            if child is not None:
                walk(child, mat)

    for root in data.roots:
        walk(root, np.eye(4))
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    return np.concatenate(verts), np.concatenate(tris)


def _fill_triangle(grid, p) -> None:
    """Write the heights of triangle `p` (3x3, grid units) at the samples it covers."""
    lo = np.ceil(p[:, :2].min(0)).astype(int).clip(0, grid.shape[0] - 1)
    hi = np.floor(p[:, :2].max(0)).astype(int).clip(0, grid.shape[0] - 1)
    if (hi < lo).any():
        return
    xs, ys = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1))
    den = ((p[1, 1] - p[2, 1]) * (p[0, 0] - p[2, 0])
           + (p[2, 0] - p[1, 0]) * (p[0, 1] - p[2, 1]))
    if abs(den) < 1e-12:
        return
    l1 = ((p[1, 1] - p[2, 1]) * (xs - p[2, 0]) + (p[2, 0] - p[1, 0]) * (ys - p[2, 1])) / den
    l2 = ((p[2, 1] - p[0, 1]) * (xs - p[2, 0]) + (p[0, 0] - p[2, 0]) * (ys - p[2, 1])) / den
    l3 = 1.0 - l1 - l2
    inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
    grid[ys[inside], xs[inside]] = (l1 * p[0, 2] + l2 * p[1, 2] + l3 * p[2, 2])[inside]


def raster_world(verts, tris, tile_x: int, tile_y: int) -> np.ndarray:
    """Heights of world-space triangles over tile (tile_x, tile_y), NaN where uncovered.

    (TILE_SAMPLES, TILE_SAMPLES) float32, row 0 = SOUTH, one sample per LAND
    vertex; triangles are clipped to the tile.
    """
    grid = np.full((TILE_SAMPLES, TILE_SAMPLES), np.nan, dtype=np.float32)
    if not len(tris):
        return grid
    local = np.asarray(verts, dtype=np.float64).copy()
    local[:, 0] = (local[:, 0] - tile_x * CELL_UNITS) / RASTER_STEP
    local[:, 1] = (local[:, 1] - tile_y * CELL_UNITS) / RASTER_STEP
    for tri in tris:
        _fill_triangle(grid, local[tri])
    return grid


def raster_lod_mesh(path, tile_x: int, tile_y: int) -> np.ndarray:
    """`raster_world` of the LOD mesh at `path`, whatever frame it was authored in."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data()
    with open(path, 'rb') as fh:
        data.read(fh)
    return raster_world(*_world_triangles(data), tile_x, tile_y)


# ---------------------------------------------------------------------------
# Per-cell heights
# ---------------------------------------------------------------------------


def cell_block(grid: np.ndarray, cx: int, cy: int) -> np.ndarray:
    """The 33x33 heights of cell (cx, cy) of a tile raster, row 0 = south."""
    step = CELL_VERTS - 1
    return grid[cy * step:cy * step + CELL_VERTS, cx * step:cx * step + CELL_VERTS]


def tile_cells(tile, grids) -> tuple:
    """({(x, y): 33x33 heights}, {plugin label: cells}) for one tile.

    `grids` are [(plugin label, raster)] in load order; each cell takes the
    LAST source covering all its vertices.
    """
    cells, used = {}, {}
    for cy in range(TILE_CELLS):
        for cx in range(TILE_CELLS):
            hit = next(((label, cell_block(g, cx, cy)) for label, g in reversed(grids)
                        if not np.isnan(cell_block(g, cx, cy)).any()), None)
            if hit is None:
                continue
            cells[(tile[0] + cx, tile[1] + cy)] = hit[1].copy()
            used[hit[0]] = used.get(hit[0], 0) + 1
    return cells, used


def baked_heights(meshes: dict, log=print) -> dict:
    """{(x, y): 33x33 heights} over every tile in `meshes` (from `lod_meshes`).

    Logs, per tile, which plugin supplied how many cells.
    See: docs/commentary/asset_convert_terrain.md#baked-lod-sources
    """
    out = {}
    for tile in sorted(meshes):
        grids = [(label, raster_lod_mesh(p, *tile)) for label, p in meshes[tile]]
        cells, used = tile_cells(tile, grids)
        out.update(cells)
        log('  Baked LOD %d.%d: %s' % (tile[0], tile[1], ', '.join(
            '%s %d cells' % kv for kv in used.items()) or 'no coverage'))
    return out


# ---------------------------------------------------------------------------
# Baked diffuse
# ---------------------------------------------------------------------------


def cell_crop(tile_rgb: np.ndarray, cx: int, cy: int, px: int) -> np.ndarray:
    """Cell (cx, cy) of a SOUTH-UP baked tile image, north-up, resized once to px.

    Flipping makes image row 0 north; the cell's block is then rows
    (31-cy)*n.., columns cx*n.. with n the tile's pixels per cell.
    """
    from PIL import Image
    north_up = np.flipud(tile_rgb)
    n = north_up.shape[0] // TILE_CELLS
    r0 = (TILE_CELLS - 1 - cy) * n
    block = np.ascontiguousarray(north_up[r0:r0 + n, cx * n:(cx + 1) * n])
    img = Image.fromarray(block, 'RGB').resize((px, px), Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


@lru_cache(maxsize=4)
def load_tile_rgb(path: str) -> np.ndarray:
    """A baked tile as a south-up RGB array; the last few stay decoded per process."""
    from PIL import Image
    with Image.open(path) as img:
        return np.asarray(img.convert('RGB'), dtype=np.uint8)

