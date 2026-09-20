"""Cross-cell edge links for an exterior cell, computed for display.

An exterior navmesh that does not link to its neighbours is an island.  In a
single-cell view a missing link looks exactly like a correct one, so the editor
draws the seam: which border edges pair with the cell next door, and which are
left dangling.

Reuses production's own `border_edges` / `match_seam`, so what the page draws
is what the writer would emit.

See: docs/commentary/tes5_import_navmesh.md#cellview-edge-links
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.navmesh.edge_links import CELL_SIZE, border_edges, match_seam

#: (dx, dy, axis) per seam; axis 0 = the seam is a constant-X plane.
NEIGHBOURS = (('east', 1, 0, 0), ('west', -1, 0, 0),
              ('north', 0, 1, 1), ('south', 0, -1, 1))


def nvnm_tris(tris):
    """Generator triangles as NVNM rows: 3 corners, 3 neighbours, 2 flag words.

    The generator emits corners only; `border_edges` needs the neighbour
    columns to tell a border edge from an interior one.
    """
    tris = np.asarray(tris, dtype=np.int32).reshape(-1, 3)
    out = np.zeros((len(tris), 8), dtype=np.int32)
    out[:, 0:3] = tris
    out[:, 3:6] = -1
    owner = {}
    for i, t in enumerate(tris):
        for slot in range(3):
            a, b = int(t[slot]), int(t[(slot + 1) % 3])
            key = (min(a, b), max(a, b))
            if key in owner:
                j, jslot = owner.pop(key)
                out[i, 3 + slot] = j
                out[j, 3 + jslot] = i
            else:
                owner[key] = (i, slot)
    return out


class CellView(object):
    """The slice of `NavMeshView` that `border_edges` reads.

    Cellview holds raw `(verts, tris)` from the generator, never a packed NVNM
    blob, so the production class cannot be built here.
    """

    __slots__ = ('verts', 'tris', 'exterior', 'grid')

    def __init__(self, verts, tris, grid):
        """Wrap raw generator geometry as something `border_edges` accepts."""
        self.verts = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
        self.tris = nvnm_tris(tris)
        self.exterior = True
        self.grid = grid


def seam_coord(grid, dx, dy, axis):
    """The world coordinate of the plane between `grid` and that neighbour."""
    gx, gy = grid
    if axis == 0:
        return (gx + (1 if dx > 0 else 0)) * CELL_SIZE
    return (gy + (1 if dy > 0 else 0)) * CELL_SIZE


def edge_points(view, edge):
    """One border edge's two endpoints, flattened as world XYZ."""
    row = view.tris[edge[0]]
    a = view.verts[row[edge[1]]]
    b = view.verts[row[(edge[1] + 1) % 3]]
    return [float(a[0]), float(a[1]), float(a[2]),
            float(b[0]), float(b[1]), float(b[2])]


def _one_seam(view, grid, spec, neighbour_of):
    """Matched and dangling border edges on a single seam, or None."""
    side, dx, dy, axis = spec
    coord = seam_coord(grid, dx, dy, axis)
    mine = border_edges(view, axis, coord)
    if not mine:
        return None
    ngrid = (grid[0] + dx, grid[1] + dy)
    other = neighbour_of(ngrid[0], ngrid[1])
    theirs = []
    if other is not None:
        theirs = border_edges(CellView(other[0], other[1], ngrid), axis, coord)
    paired = {(a[0], a[1]) for (a, _b) in (match_seam(mine, theirs)
                                           if theirs else [])}
    entry = {'side': side, 'grid': list(ngrid), 'neighbour': other is not None,
             'matched': [], 'dangling': []}
    for e in mine:
        key = 'matched' if (e[0], e[1]) in paired else 'dangling'
        entry[key] += edge_points(view, e)
    return entry


def seam_report(verts, tris, grid, neighbour_of):
    """Per-seam matched/dangling border edges for one exterior cell.

    `neighbour_of(gx, gy)` returns that cell's `(verts, tris)` or None, so the
    caller decides how neighbours are built and what that costs.
    """
    if grid is None or tris is None or not len(tris):
        return {'grid': list(grid) if grid else None, 'seams': []}
    view = CellView(verts, tris, grid)
    seams = [_one_seam(view, grid, spec, neighbour_of) for spec in NEIGHBOURS]
    return {'grid': list(grid), 'seams': [s for s in seams if s]}
