"""Gather a cell's collision geometry into world-space triangle arrays.

Produces the input the voxelizer rasterizes:

    walkable  (N,3,3) float32   floors, stair treads, bridge decks, ramps, terrain
    blocking  (M,3,3) float32   walls, pillars, railings, crate sides

Each placed REFR contributes its base mesh's cached collision soup
(asset_convert.collision.collision_extract), transformed by the ref's full rotation,
scale and position.  The old converter applied only RotZ, which silently
mis-oriented every tilted/ramped static; we apply the full X*Y*Z rotation.

Exterior cells additionally contribute the LAND height field as walkable terrain
triangles, so open ground is navigable even where nothing is placed.
"""

import math

import numpy as np

from ..base.text_reader import get_float, get_str
from ..record_types.world import shifted_position
from . import params

_CELL_SIZE = 4096.0
_LAND_VERTS = 33
_LAND_SPACING = _CELL_SIZE / (_LAND_VERTS - 1)   # 128.0
_VHGT_UNIT = 8.0

#: Placement bound in game units. See: docs/commentary/tes5_import_navmesh.md#wild-placements-are-dropped
MAX_PLACEMENT = 1e7


def _finite_placement(pos, scale):
    """True if a REFR's placement can contribute usable collision geometry."""
    if not np.all(np.isfinite(pos)) or not math.isfinite(scale):
        return False
    return bool(np.all(np.abs(pos) <= MAX_PLACEMENT))


def rot_matrix(rx, ry, rz):
    """REFR placement rotation matrix (local mesh coords -> cell coords).

    The transpose of the Rz@Ry@Rx product, never the product itself.
    See: docs/commentary/tes5_import_navmesh.md#refr-placement-is-the-transpose
    """
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # (Rz @ Ry @ Rx).T
    m = np.array([
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy,     cy * sx,                cy * cx],
    ], dtype=np.float64)
    return m.T


def place(flat, rot, scale, pos):
    """Transform a flat [9N] game-unit soup into an (N,3,3) world array.

    `flat` may be a python list or (from the cache) a float32 numpy array; use
    len() rather than truthiness so an ndarray doesn't raise.
    """
    if flat is None or len(flat) == 0:
        return None
    tris = np.asarray(flat, dtype=np.float64).reshape(-1, 3, 3)
    tris = tris * scale
    # (N,3,3) @ (3,3)^T  -> rotate every vertex
    tris = tris @ rot.T
    tris += pos
    return tris


def decode_vhgt(vhgt_hex):
    """Decode LAND VHGT into a 33x33 grid of absolute Z (game units).

    BOTH the offset and the accumulated deltas scale by _VHGT_UNIT.
    See: docs/commentary/tes5_import_navmesh.md#vhgt-offset-scales-too
    """
    try:
        data = bytes.fromhex(vhgt_hex)
    except ValueError:
        return None
    if len(data) < 4 + _LAND_VERTS * _LAND_VERTS:
        return None
    offset = float(np.frombuffer(data, dtype='<f4', count=1)[0])
    deltas = np.frombuffer(data, dtype=np.int8, offset=4,
                           count=_LAND_VERTS * _LAND_VERTS
                           ).reshape(_LAND_VERTS, _LAND_VERTS).astype(np.float64)
    row_starts = np.cumsum(deltas[:, 0])
    grid = np.cumsum(deltas, axis=1)
    grid = grid - deltas[:, [0]] + row_starts[:, None]
    return (grid + offset) * _VHGT_UNIT


def _land_tris(land_rec, origin_x, origin_y):
    """LAND height field as walkable terrain triangles."""
    vhgt = get_str(land_rec, 'VHGT')
    if not vhgt:
        return None
    grid = decode_vhgt(vhgt)
    if grid is None:
        return None

    n = _LAND_VERTS
    xs = origin_x + np.arange(n) * _LAND_SPACING
    ys = origin_y + np.arange(n) * _LAND_SPACING
    gx, gy = np.meshgrid(xs, ys)                   # grid[row=y][col=x]
    pts = np.stack([gx, gy, grid], axis=-1)        # (n, n, 3)

    v00 = pts[:-1, :-1]
    v10 = pts[:-1, 1:]
    v01 = pts[1:, :-1]
    v11 = pts[1:, 1:]
    t1 = np.stack([v00, v10, v11], axis=-2).reshape(-1, 3, 3)
    t2 = np.stack([v00, v11, v01], axis=-2).reshape(-1, 3, 3)
    return np.concatenate([t1, t2], axis=0)


class LandField:
    """A cell's LAND heights, interpolated on the same diagonal `_land_tris` cuts.

    See: docs/commentary/tes5_import_navmesh.md#terrain-standing-ribbons-follow-the-land
    """

    __slots__ = ('grid', 'ox', 'oy')

    def __init__(self, grid, origin_x, origin_y):
        """`grid` is decode_vhgt's 33x33 array, row = y."""
        self.grid = np.ascontiguousarray(grid, dtype=np.float64)
        self.ox, self.oy = float(origin_x), float(origin_y)

    def covers(self, x, y):
        """True when (x, y) lies inside this cell's LAND square."""
        span = (_LAND_VERTS - 1) * _LAND_SPACING
        return (self.ox <= x <= self.ox + span
                and self.oy <= y <= self.oy + span)

    def z(self, x, y):
        """Terrain height at (x, y); points outside the cell clamp to its rim."""
        last = _LAND_VERTS - 1
        fx = min(float(last), max(0.0, (x - self.ox) / _LAND_SPACING))
        fy = min(float(last), max(0.0, (y - self.oy) / _LAND_SPACING))
        ix, iy = min(last - 1, int(fx)), min(last - 1, int(fy))
        tx, ty = fx - ix, fy - iy
        g = self.grid
        z00, z10 = g[iy, ix], g[iy, ix + 1]
        z01, z11 = g[iy + 1, ix], g[iy + 1, ix + 1]
        if tx >= ty:
            return float(z00 + (z10 - z00) * tx + (z11 - z10) * ty)
        return float(z00 + (z11 - z01) * tx + (z01 - z00) * ty)


def land_field(land_rec, origin_x, origin_y):
    """The cell's LandField, or None when it has no readable LAND."""
    vhgt = get_str(land_rec, 'VHGT') if land_rec is not None else None
    grid = decode_vhgt(vhgt) if vhgt else None
    return None if grid is None else LandField(grid, origin_x, origin_y)


def _split_by_slope(tris):
    """Split an (N,3,3) array into (walkable, blocking) by face normal."""
    if tris is None or len(tris) == 0:
        return None, None
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    nrm = np.cross(e1, e2)
    ln = np.linalg.norm(nrm, axis=1)
    ok = ln > 1e-9
    cos_up = np.zeros(len(tris))
    cos_up[ok] = np.abs(nrm[ok, 2]) / ln[ok]
    walk = ok & (cos_up >= math.cos(math.radians(params.MAX_SLOPE_DEG)))
    return tris[walk], tris[ok & ~walk]


def _placed_soup(refr, base_model_by_fid, get_collision):
    """Return (walkable, blocking) placed arrays for one REFR, or (None, None).

    Either element is None when that class is absent or the ref is unusable.
    Placement uses `shifted_position`, the SAME position the ESM writes, so
    furniture collision does not float above where the engine puts the object.

    See: docs/commentary/tes5_import_navmesh.md#wild-placements-are-dropped
    See: docs/commentary/asset_convert_nif.md#furniture-shift-third-consumer
    """
    name = refr.get('NAME')
    if not name:
        return None, None
    try:
        base_low = int(name, 16) & 0x00FFFFFF
    except ValueError:
        return None, None
    key = base_model_by_fid.get(base_low)
    soup = get_collision(key) if key else None
    if not soup:
        return None, None
    scale = get_float(refr, 'XSCL.Scale', 1.0) or 1.0
    pos = np.array(shifted_position(refr, scale), dtype=np.float64)
    if not _finite_placement(pos, scale):
        return None, None
    rot = rot_matrix(get_float(refr, 'RotX'),
                      get_float(refr, 'RotY'),
                      get_float(refr, 'RotZ'))
    if not np.all(np.isfinite(rot)):
        return None, None
    return (place(soup.get('w'), rot, scale, pos),
            place(soup.get('b'), rot, scale, pos))


def _sort_refr_parts(refr_recs, base_model_by_fid, get_collision, skip_bases):
    """Return (walk_parts, block_parts, door_walk_parts) for a cell's REFRs.

    A door's faces go to door_walk_parts whatever their cached class.
    See: docs/commentary/tes5_import_navmesh.md#door-panels-are-never-blocking
    """
    walk_parts, block_parts, door_walk_parts = [], [], []
    for refr in refr_recs or []:
        name = refr.get('NAME')
        try:
            base_low = int(name, 16) & 0x00FFFFFF if name else None
        except ValueError:
            base_low = None
        w, b = _placed_soup(refr, base_model_by_fid, get_collision)
        if w is None and b is None:
            continue
        if base_low in skip_bases:
            door_walk_parts += [p for p in (w, b) if p is not None and len(p)]
            continue
        if w is not None and len(w):
            walk_parts.append(w)
        if b is not None and len(b):
            block_parts.append(b)
    return walk_parts, block_parts, door_walk_parts


def _resplit_placed(walk_parts, block_parts, door_walk_parts):
    """Reclassify placed REFR faces by their PLACED slope; return walk_parts.

    Steep door faces are discarded rather than demoted to blocking.
    See: docs/commentary/tes5_import_navmesh.md#placements-are-slope-resplit
    """
    if walk_parts:
        rw, rb = _split_by_slope(np.concatenate(walk_parts, axis=0))
        walk_parts = [rw] if rw is not None and len(rw) else []
        if rb is not None and len(rb):
            block_parts.append(rb)
    if door_walk_parts:
        rw, _rb = _split_by_slope(np.concatenate(door_walk_parts, axis=0))
        if rw is not None and len(rw):
            walk_parts.append(rw)
    return walk_parts


def _terrain_parts(land_rec, origin_x, origin_y, block_parts):
    """Return the LAND record's walkable terrain, appending its steep faces."""
    if land_rec is None:
        return None
    lt = _land_tris(land_rec, origin_x, origin_y)
    if lt is None or not len(lt):
        return None
    lw, lb = _split_by_slope(lt)
    if lb is not None and len(lb):
        block_parts.append(lb)
    return lw if lw is not None and len(lw) else None


def gather_cell_geometry(refr_recs, base_model_by_fid, get_collision,
                         land_rec=None, origin_x=0.0, origin_y=0.0,
                         split_land=False, skip_bases=None):
    """Return (walkable, blocking) float64 (N,3,3) world-space triangle arrays.

    `get_collision` is the cache accessor, injected so workers can bind their
    own module-global cache without this module importing asset_convert.
    `skip_bases` holds low-24 base FormIDs contributing no blocking collision.
    `split_land=True` returns (walkable, blocking, land_walkable) instead.
    See: docs/commentary/tes5_import_navmesh.md#land-split-for-the-grid-rasterizer
    """
    walk_parts, block_parts, door_walk_parts = _sort_refr_parts(
        refr_recs, base_model_by_fid, get_collision, skip_bases or ())
    walk_parts = _resplit_placed(walk_parts, block_parts, door_walk_parts)

    land_walk = np.zeros((0, 3, 3))
    lw = _terrain_parts(land_rec, origin_x, origin_y, block_parts)
    if lw is not None:
        if split_land:
            land_walk = lw
        else:
            walk_parts.append(lw)

    walkable = (np.concatenate(walk_parts, axis=0) if walk_parts
                else np.zeros((0, 3, 3)))
    blocking = (np.concatenate(block_parts, axis=0) if block_parts
                else np.zeros((0, 3, 3)))
    if split_land:
        return walkable, blocking, land_walk
    return walkable, blocking
