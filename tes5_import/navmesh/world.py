"""Gather a cell's collision geometry into world-space triangle arrays.

Produces the input the voxelizer rasterizes:

    walkable  (N,3,3) float32   floors, stair treads, bridge decks, ramps, terrain
    blocking  (M,3,3) float32   walls, pillars, railings, crate sides

Each placed REFR contributes its base mesh's cached collision soup
(asset_convert.collision_extract), transformed by the ref's full rotation,
scale and position.  The old converter applied only RotZ, which silently
mis-oriented every tilted/ramped static; we apply the full X*Y*Z rotation.

Exterior cells additionally contribute the LAND height field as walkable terrain
triangles, so open ground is navigable even where nothing is placed.
"""

import math

import numpy as np

from ..text_reader import get_float, get_str
from . import params

_CELL_SIZE = 4096.0
_LAND_VERTS = 33
_LAND_SPACING = _CELL_SIZE / (_LAND_VERTS - 1)   # 128.0
_VHGT_UNIT = 8.0


def _rot_matrix(rx, ry, rz):
    """REFR placement rotation matrix (local mesh coords -> cell coords).

    Oblivion/Skyrim store a REFR's rotation and the engine applies its INVERSE
    when placing the mesh, so the placement matrix is the TRANSPOSE of the naive
    Rz@Ry@Rx product.  Verified on Anvil Fighters Guild: the floor shell has
    RotZ = -90 deg, and only the transpose lands its footprint (x -852..584,
    y -822..431) under the cell's pathgrid (x -769..511, y -742..357) — 52/52
    nodes inside, vs 34/52 with the non-transposed matrix (a ~180 deg error that
    put the room mesh backwards relative to the furniture).
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


def _place(flat, rot, scale, pos):
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

    Layout: float offset, then a 33x33 grid of SIGNED int8 gradients.  The first
    column of each row is a delta from the previous row's first column; within a
    row, each column is a delta from the previous column.

    BOTH the offset and the accumulated deltas are in VHGT units and BOTH scale
    by _VHGT_UNIT (=8) to reach game units.

    The old converter did `offset / 8` going in and `* 8` coming out, which
    cancels for the deltas but silently ANNIHILATES the offset's contribution —
    so every exterior cell's terrain came out at the wrong absolute height.  For
    Tamriel (47,6) that put the terrain at z=829..3213 while the cell's own
    pathgrid and REFRs sat at z=18288..19776, a ~16,700u error.  Verified: with
    the offset scaled correctly the terrain lands at 17608..19992, i.e. under
    the objects standing on it.
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


def gather_cell_geometry(refr_recs, base_model_by_fid, get_collision,
                         land_rec=None, origin_x=0.0, origin_y=0.0,
                         split_land=False):
    """Return (walkable, blocking) float64 (N,3,3) world-space triangle arrays.

    get_collision is the cache accessor (injected so workers can bind their own
    module-global cache without this module importing asset_convert).

    split_land=True returns (walkable, blocking, land_walkable) instead, keeping
    the LAND terrain separate so the caller can send it down the vectorized
    grid-rasterizer path (it is a regular grid of large triangles, and the
    generic scalar rasterizer spends most of an exterior cell's build time on it).
    """
    walk_parts = []
    block_parts = []

    for refr in refr_recs or []:
        name = refr.get('NAME')
        if not name:
            continue
        try:
            base_low = int(name, 16) & 0x00FFFFFF
        except ValueError:
            continue
        key = base_model_by_fid.get(base_low)
        if not key:
            continue
        soup = get_collision(key)
        if not soup:
            continue

        scale = get_float(refr, 'XSCL.Scale', 1.0) or 1.0
        rot = _rot_matrix(get_float(refr, 'RotX'),
                          get_float(refr, 'RotY'),
                          get_float(refr, 'RotZ'))
        pos = np.array([get_float(refr, 'PosX'),
                        get_float(refr, 'PosY'),
                        get_float(refr, 'PosZ')], dtype=np.float64)

        w = _place(soup.get('w'), rot, scale, pos)
        if w is not None and len(w):
            walk_parts.append(w)
        b = _place(soup.get('b'), rot, scale, pos)
        if b is not None and len(b):
            block_parts.append(b)

    # Exterior terrain.  Rotating a static can turn a floor triangle into a wall
    # (and vice versa), so re-derive the split from the placed normals rather
    # than trusting the cache's local-space classification.
    if walk_parts:
        placed = np.concatenate(walk_parts, axis=0)
        rw, rb = _split_by_slope(placed)
        walk_parts = [rw] if rw is not None and len(rw) else []
        if rb is not None and len(rb):
            block_parts.append(rb)

    land_walk = np.zeros((0, 3, 3))
    if land_rec is not None:
        lt = _land_tris(land_rec, origin_x, origin_y)
        if lt is not None and len(lt):
            lw, lb = _split_by_slope(lt)
            if lb is not None and len(lb):
                block_parts.append(lb)
            if lw is not None and len(lw):
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
