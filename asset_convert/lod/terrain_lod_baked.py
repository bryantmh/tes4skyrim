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


def mesh_triangles(path) -> tuple:
    """(world-space vertices, triangles) of the LOD mesh at `path`, any frame."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data()
    with open(path, 'rb') as fh:
        data.read(fh)
    return _world_triangles(data)


def raster_lod_mesh(path, tile_x: int, tile_y: int) -> np.ndarray:
    """`raster_world` of the LOD mesh at `path`, whatever frame it was authored in."""
    return raster_world(*mesh_triangles(path), tile_x, tile_y)


# ---------------------------------------------------------------------------
# Per-cell heights
# ---------------------------------------------------------------------------


def cell_block(grid: np.ndarray, cx: int, cy: int) -> np.ndarray:
    """The 33x33 heights of cell (cx, cy) of a tile raster, row 0 = south."""
    step = CELL_VERTS - 1
    return grid[cy * step:cy * step + CELL_VERTS, cx * step:cx * step + CELL_VERTS]


#: Heights closer than this (units) are equal; under LAND's 8-unit step, over float noise. See #baked-lod-fill.
FLAT_EPSILON = 1.0

#: A block is no-LAND fill when over this share of its vertices sit at the fill height (flat 1.0, a ramp ~0.9).
FILL_SHARE = 0.5


def flat_areas(verts, tris) -> dict:
    """{height: plan area} of the triangles whose three vertices share one height."""
    if not len(tris):
        return {}
    p = np.asarray(verts, dtype=np.float64)[np.asarray(tris)]
    z = p[:, :, 2]
    flat = (z.max(1) - z.min(1)) < FLAT_EPSILON
    a, b, c = p[flat, 0, :2], p[flat, 1, :2], p[flat, 2, :2]
    area = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
                        - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    out = {}
    for h, ar in zip(np.round(z[flat, 0]).tolist(), area.tolist()):
        out[h] = out.get(h, 0.0) + ar
    return out


def fill_height(meshes) -> float:
    """The worldspace's no-LAND fill height, or None: the height the most flat
    mesh area sits at, over every source mesh (`meshes`: [(verts, tris)]).

    The editor bakes cells with no LAND as a flat plane at the worldspace's
    default land height; that plane dominates the flat area of the source
    game's own meshes (sea floor, past-the-map ring), so no height is assumed.
    See: docs/commentary/asset_convert_terrain.md#baked-lod-fill
    """
    total = {}
    for verts, tris in meshes:
        for h, area in flat_areas(verts, tris).items():
            total[h] = total.get(h, 0.0) + area
    return max(total, key=total.get) if total else None


def _is_fill(block: np.ndarray, fill) -> bool:
    """True when most of `block` sits at the fill height `fill`."""
    if fill is None:
        return False
    return float((np.abs(block - fill) < FLAT_EPSILON).mean()) > FILL_SHARE


def _pick(covering, fill):
    """The last (label, block) in `covering` that is not fill, else the last."""
    relief = [c for c in covering if not _is_fill(c[1], fill)]
    return (relief or covering)[-1]


def tile_cells(tile, grids, fill=None) -> tuple:
    """({(x, y): 33x33 heights}, {plugin label: cells}) for one tile.

    `grids` are [(plugin label, raster)] in load order; each cell takes the
    LAST source covering all its vertices whose block is not the no-LAND fill
    at height `fill` (`fill_height`), else the last covering source.
    See: docs/commentary/asset_convert_terrain.md#baked-lod-fill
    """
    cells, used = {}, {}
    for cy in range(TILE_CELLS):
        for cx in range(TILE_CELLS):
            covering = [(label, cell_block(g, cx, cy)) for label, g in grids
                        if not np.isnan(cell_block(g, cx, cy)).any()]
            if not covering:
                continue
            label, block = _pick(covering, fill)
            cells[(tile[0] + cx, tile[1] + cy)] = block.copy()
            used[label] = used.get(label, 0) + 1
    return cells, used


def baked_heights(meshes: dict, log=print) -> dict:
    """{(x, y): 33x33 heights} over every tile in `meshes` (from `lod_meshes`).

    Every mesh is read first so the fill height comes from all of them; logs
    it and, per tile, which plugin supplied how many cells.
    See: docs/commentary/asset_convert_terrain.md#baked-lod-sources
    """
    loaded = {tile: [(label, mesh_triangles(p)) for label, p in meshes[tile]]
              for tile in sorted(meshes)}
    fill = fill_height([m for srcs in loaded.values() for _l, m in srcs])
    log('  Baked LOD no-LAND fill height: %s' % fill)
    out = {}
    for tile, srcs in loaded.items():
        grids = [(label, raster_world(*m, *tile)) for label, m in srcs]
        cells, used = tile_cells(tile, grids, fill)
        out.update(cells)
        log('  Baked LOD %d.%d: %s' % (tile[0], tile[1], ', '.join(
            '%s %d cells' % kv for kv in used.items()) or 'no coverage'))
    return out


# ---------------------------------------------------------------------------
# Synthetic horizon cells
# ---------------------------------------------------------------------------


def coons(dw, de, ds, dn) -> np.ndarray:
    """The 33x33 field (row 0 south) meeting four edge corrections exactly.

    Transfinite (Coons) blend: each edge's correction fades linearly across
    the cell, minus the bilinear of the corners so none counts twice. Corners
    are read from `ds` / `dn`; `dw` / `de` must agree with them there.
    See: docs/commentary/asset_convert_terrain.md#synthetic-seam-stitch
    """
    t = np.linspace(0.0, 1.0, CELL_VERTS)
    u, v = t[None, :], t[:, None]
    corners = ((1 - u) * (1 - v) * ds[0] + u * (1 - v) * ds[-1]
               + (1 - u) * v * dn[0] + u * v * dn[-1])
    return ((1 - u) * np.asarray(dw)[:, None] + u * np.asarray(de)[:, None]
            + (1 - v) * np.asarray(ds)[None, :] + v * np.asarray(dn)[None, :]
            - corners)


def _ramp(a, b) -> np.ndarray:
    """CELL_VERTS values from `a` to `b`, linearly."""
    return np.linspace(a, b, CELL_VERTS)


#: side: (neighbour offset, own edge, neighbour's edge, own corners (row, col) at its two ends).
_EDGES = {'west': ((-1, 0), np.s_[:, 0], np.s_[:, -1], ((0, 0), (-1, 0))),
          'east': ((1, 0), np.s_[:, -1], np.s_[:, 0], ((0, -1), (-1, -1))),
          'south': ((0, -1), np.s_[0, :], np.s_[-1, :], ((0, 0), (0, -1))),
          'north': ((0, 1), np.s_[-1, :], np.s_[0, :], ((-1, 0), (-1, -1)))}


def fit_edges(h, targets: dict) -> np.ndarray:
    """`h` (33x33, row 0 south) bent to meet `targets` {side: 33 heights} exactly.

    A side with no target keeps its shape, shifted linearly between whatever
    its two end corners become; the corrections blend across the cell by
    `coons`, and the targets are then written verbatim on the edges.
    See: docs/commentary/asset_convert_terrain.md#synthetic-seam-stitch
    """
    raw = np.asarray(h, dtype=np.float64)
    corner = {}
    for side, (_d, own, _o, ends) in _EDGES.items():
        if side in targets:
            delta = np.asarray(targets[side], np.float64) - raw[own]
            corner.setdefault(ends[0], delta[0])
            corner.setdefault(ends[1], delta[-1])
    deltas = {}
    for side, (_d, own, _o, ends) in _EDGES.items():
        deltas[side] = (np.asarray(targets[side], np.float64) - raw[own]
                        if side in targets else
                        _ramp(corner.get(ends[0], 0.0), corner.get(ends[1], 0.0)))
    out = raw + coons(deltas['west'], deltas['east'], deltas['south'],
                      deltas['north'])
    for side, edge in targets.items():
        out[_EDGES[side][1]] = edge
    return out.astype(np.float32)


def feather_edges(h, west=None, east=None, south=None, north=None) -> np.ndarray:
    """`fit_edges` for keyword edges: `h` bent to meet each given LAND edge exactly."""
    given = {'west': west, 'east': east, 'south': south, 'north': north}
    return fit_edges(h, {k: v for k, v in given.items() if v is not None})


#: (cell offset from a vertex, that cell's (row, col) at the vertex) for the four cells meeting there.
_AROUND = (((-1, -1), (-1, -1)), ((0, -1), (-1, 0)), ((-1, 0), (0, -1)), ((0, 0), (0, 0)))


def vertex_height(lands, keys, heights, vertex) -> float:
    """The height every cell meeting at cell corner `vertex` must share.

    Real LAND there wins (their mean); else the synthetic cells' raw mean.
    See: docs/commentary/asset_convert_terrain.md#synthetic-seam-stitch
    """
    real, syn = [], []
    for (dx, dy), at in _AROUND:
        nb = (vertex[0] + dx, vertex[1] + dy)
        if nb in keys:
            syn.append(float(heights[nb][at]))
        elif nb in lands:
            real.append(float(lands[nb]['heights'][at]))
    return float(np.mean(real or syn))


def _vertex_of(key, at) -> tuple:
    """The vertex (cell-corner coordinates) at (row, col) corner `at` of cell `key`."""
    return (key[0] + (at[1] == -1), key[1] + (at[0] == -1))


def seam_targets(lands, keys, heights, key) -> dict:
    """{side: 33 heights} synthetic cell `key` must meet.

    A LAND side: that LAND's edge. A synthetic side: the mean of both raw
    edges. A free side: its own raw edge. Every synthetic or free target is
    then shifted linearly so its ends sit on `vertex_height`, so neighbours
    that share an edge or only a corner compute one value there.
    See: docs/commentary/asset_convert_terrain.md#synthetic-seam-stitch
    """
    raw = heights[key]
    out = {}
    for side, ((dx, dy), own, other, ends) in _EDGES.items():
        nb = (key[0] + dx, key[1] + dy)
        if nb not in keys and nb in lands:
            out[side] = np.asarray(lands[nb]['heights'][other], np.float64)
            continue
        base = np.asarray(raw[own], np.float64)
        if nb in keys:
            base = (base + np.asarray(heights[nb][other], np.float64)) / 2.0
        a, b = (vertex_height(lands, keys, heights, _vertex_of(key, e))
                for e in ends)
        out[side] = base + _ramp(a - base[0], b - base[-1])
    return out


def synthesize(world: dict, heights: dict) -> set:
    """Add a cell for every baked-height cell with no LAND; return their keys.

    `world` holds 'lands', 'cell_water', 'default_wh' and 'deleted' (cells an
    overlay deleted, which stay empty). A synthetic cell is heights only
    (white VCLR, no layers), bent to meet its LAND neighbours exactly and its
    synthetic neighbours on their mean edge (`seam_targets`); it takes
    worldspace-default water when it dips below it and the worldspace has any.
    See: docs/commentary/asset_convert_terrain.md#no-terrain-without-an-authored-source
    """
    lands, water = world['lands'], world['cell_water']
    wet = any(flag for flag, _h in water.values())
    keys = {k for k in heights
            if k not in lands and k not in world.get('deleted', ())}
    fitted = {key: fit_edges(heights[key], seam_targets(lands, keys, heights, key))
              for key in sorted(keys)}
    for key in sorted(keys):
        lands[key] = {'heights': fitted[key],
                      'colors': np.full((CELL_VERTS, CELL_VERTS, 3), 255, np.uint8),
                      'layers': {'base': {}, 'alpha': {}}}
        if wet and float(lands[key]['heights'].min()) < world['default_wh']:
            water.setdefault(key, (True, None))
    return keys


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


# ---------------------------------------------------------------------------
# Matching the filler to the painted composite
# ---------------------------------------------------------------------------

#: Gaussian spread (cells) and window radius of the per-cell offset field.
OFFSET_SIGMA = 1.5
OFFSET_RADIUS = 4

#: Weight of the worldspace-wide offset in every cell's estimate.
OFFSET_PRIOR_WEIGHT = 0.05

#: Largest per-channel offset applied, so one bad fit cannot repaint a region.
OFFSET_CLAMP = 60.0

#: Box width (px) smoothing an edge's colour profile before it is feathered in.
FEATHER_SMOOTH = 5


def cell_offset(ours: np.ndarray, crop: np.ndarray, quads, blocks) -> np.ndarray:
    """Mean per-channel (composite - bake) over a cell's painted `quads`."""
    return np.mean([ours[blocks[q]].reshape(-1, 3).mean(0)
                    - crop[blocks[q]].reshape(-1, 3).mean(0) for q in quads], 0)


def window(key, radius):
    """(neighbour key, squared distance) over the square window around `key`."""
    x, y = key
    return [((x + dx, y + dy), dx * dx + dy * dy)
            for dx in range(-radius, radius + 1)
            for dy in range(-radius, radius + 1)]


def smooth_offsets(fitted: dict, targets) -> dict:
    """{'prior': rgb, 'cells': {key: rgb}}: Gaussian-weighted `fitted` offsets per target.

    Each estimate is pulled toward the worldspace-wide mean with a small
    weight, so a cell far from any painted cell takes that mean and the
    field stays smooth where fitted cells run out. Clamped.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-filler-match
    """
    prior = (np.mean(list(fitted.values()), 0) if fitted else np.zeros(3))
    prior = np.clip(prior, -OFFSET_CLAMP, OFFSET_CLAMP)
    cells = {}
    for key in targets:
        acc, weight = OFFSET_PRIOR_WEIGHT * prior, OFFSET_PRIOR_WEIGHT
        for nb, r2 in window(key, OFFSET_RADIUS):
            if nb in fitted:
                w = np.exp(-r2 / (2.0 * OFFSET_SIGMA ** 2))
                acc, weight = acc + w * fitted[nb], weight + w
        cells[key] = np.clip(acc / weight, -OFFSET_CLAMP, OFFSET_CLAMP)
    return {'prior': prior, 'cells': cells}


def offset_image(offsets: dict, key, px: int) -> np.ndarray:
    """The cell's offset as a (px,px,3) field, north-up, bilinear between its corners.

    A corner is the mean of the four cells meeting there, so neighbouring
    filler cells meet without a step.
    """
    x, y = key
    get = offsets['cells'].get
    prior = offsets['prior']
    corner = np.array([[np.mean([get((x - 1 + i + a, y - 1 + j + b), prior)
                                 for a in (0, 1) for b in (0, 1)], 0)
                        for i in (0, 1)] for j in (1, 0)])
    t = (np.arange(px) + 0.5) / px
    top = corner[0, 0][None] * (1 - t)[:, None] + corner[0, 1][None] * t[:, None]
    bot = corner[1, 0][None] * (1 - t)[:, None] + corner[1, 1][None] * t[:, None]
    return top[None] * (1 - t)[:, None, None] + bot[None] * t[:, None, None]


def _smooth(profile: np.ndarray) -> np.ndarray:
    """`profile` (n,3) box-filtered along n with edge padding."""
    k = FEATHER_SMOOTH
    pad = np.pad(profile, ((k // 2, k // 2), (0, 0)), mode='edge')
    ker = np.ones(k) / k
    return np.stack([np.convolve(pad[:, c], ker, mode='valid')
                     for c in range(3)], -1)


def _feather_side(img, quad, side, nb, h) -> None:
    """Fade the colour step between filler block `quad` and painted `nb` across `quad`."""
    fade = np.linspace(1.0, 0.0, h)
    if side in ('w', 'e'):
        own = quad[:, :2] if side == 'w' else quad[:, -2:]
        other = nb[:, -2:] if side == 'w' else nb[:, :2]
        delta = _smooth(other.mean(1) - own.mean(1))
        ramp = fade if side == 'w' else fade[::-1]
        img += delta[:, None, :] * ramp[None, :, None]
        return
    own = quad[:2] if side == 'n' else quad[-2:]
    other = nb[-2:] if side == 'n' else nb[:2]
    delta = _smooth(other.mean(0) - own.mean(0))
    ramp = fade if side == 'n' else fade[::-1]
    img += delta[None, :, :] * ramp[:, None, None]


_SIDES = (('w', 0, -1), ('e', 0, 1), ('n', -1, 0), ('s', 1, 0))


def feather_filler(canvas: np.ndarray, state: np.ndarray, h: int) -> np.ndarray:
    """`canvas` (north-up, quad blocks of h px) with each filler block feathered.

    `state` holds one entry per block: 1 painted, 0 filler, -1 unknown. A
    filler block is bent, side by side, to meet each painted neighbour's
    edge colour, the correction fading to 0 across the block; painted
    pixels never change.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-filler-match
    """
    out = canvas.astype(np.float32)
    rows, cols = state.shape
    for r, c in zip(*np.nonzero(state == 0)):
        block = out[r * h:(r + 1) * h, c * h:(c + 1) * h]
        for side, dr, dc in _SIDES:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and state[nr, nc] == 1:
                nb = out[nr * h:(nr + 1) * h, nc * h:(nc + 1) * h]
                _feather_side(block, block.copy(), side, nb, h)
    return np.clip(out, 0, 255)

