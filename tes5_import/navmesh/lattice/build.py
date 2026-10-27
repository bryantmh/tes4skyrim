"""Lattice navmesh for one cell: collision columns grown out from the pathgrid.

Same inputs and output as `corridor.build_corridors`, so either can stand
behind `build_navmesh`.  The floor is found in the collision, the pathgrid
says which floor is walked, the mesh is a shared-corner lattice over every
floor within walking reach of it, and a collapse pass coarsens it.
"""

import math

import numpy as np

from .. import params, world
from ..clean_validate import find_ledge_links
from .columns import Grid
from .graph import build_graph
from .merge import merge_flat
from .mesh import lattice, surface_sampler
from .simplify import locate, outline_loops, simplify

#: Walked distance from the pathgrid that interior floor is kept within (units).
REACH = 1024.0

#: A door threshold stands on a quad within this height of it (the writer's own window).
DOOR_DZ = 128.0

#: Exterior cell side (units); the grid is anchored on the cell so seams line up.
_CELL = 4096.0

#: Spacing (units) of the border vertices every exterior cell keeps, so both sides of a seam pair up.
SEAM_STEP = 128.0

#: ACTI base FormIDs of the plugin being converted; set once per process by set_activators.
_ACTIVATORS = [frozenset()]


def set_activators(fids) -> None:
    """Record the plugin's ACTI base FormIDs for every later build_lattice call."""
    _ACTIVATORS[0] = frozenset(fids)


def _domain(walkable, nodes, land_rec, origin_x, origin_y):
    """(grid, reach) covering the floor the pathgrid could walk to."""
    if land_rec is not None:
        grid = Grid.over((origin_x, origin_y), (origin_x + _CELL, origin_y + _CELL),
                         params.CS_EXTERIOR)
        return grid, params.PGRD_XY_REACH_EXTERIOR
    pn = np.asarray(nodes, float)[:, :2]
    lo, hi = pn.min(0) - REACH, pn.max(0) + REACH
    if len(walkable):
        w = np.asarray(walkable, float).reshape(-1, 3)[:, :2]
        lo = np.maximum(lo, np.minimum(w.min(0), pn.min(0)) - params.CS)
        hi = np.minimum(hi, np.maximum(w.max(0), pn.max(0)) + params.CS)
    lo = np.floor(lo / params.CS) * params.CS
    return Grid.over(lo, hi, params.CS), REACH


def _border_lines(verts, border):
    """{vertex: set of cell edges (0 west, 1 south, 2 east, 3 north) it lies on}."""
    out = {}
    for k, p in enumerate(verts):
        on = {e for e, (axis, c) in enumerate(((0, border[0]), (1, border[1]),
                                              (0, border[2]), (1, border[3])))
              if abs(p[axis] - c) < 0.5}
        if on:
            out[k] = on
    return out


def seam_roles(verts, tris, border):
    """(pinned, seam) for an exterior cell's border vertices; nothing for an interior.

    seam maps every border vertex to its cell edges.  Pinned are the cell
    corners, points on SEAM_STEP multiples (both neighbours keep the same
    ones) and the ends of each run of floor along an edge; the rest may only
    slide along their edge.
    """
    if border is None:
        return set(), {}
    seam = _border_lines(verts, border)
    nbrs = {}
    for loop in outline_loops(tris)[0]:
        for k, v in enumerate(loop):
            nbrs.setdefault(v, []).extend((loop[k - 1], loop[(k + 1) % len(loop)]))
    pinned = set()
    for v, on in seam.items():
        along = verts[v][1] if min(on) % 2 == 0 else verts[v][0]
        if len(on) > 1 or abs(math.remainder(along, SEAM_STEP)) < 0.5 or v not in nbrs \
                or any(not on <= seam.get(u, set()) for u in nbrs[v]):
            pinned.add(v)
    return pinned, seam


def _on_mesh(x, y, cands, p, tris):
    """The first of triangles `cands` holding plan point (x, y), or None."""
    if not cands:
        return None
    which, _z = locate(np.array([[x, y, 0.0]]), p[np.array([tris[t] for t in cands])])
    return cands[int(which[0])] if which[0] >= 0 else None


def pathgrid_points(grid, col, z, seed, span_tris, verts, tris):
    """(x, y, z, triangle) at the center of every kept pathgrid-seeded quad."""
    p = np.asarray(verts, float)
    out = []
    for s in np.flatnonzero(seed).tolist():
        c = int(col[s])
        x = grid.x0 + (c % grid.nx + 0.5) * grid.cs
        y = grid.y0 + (c // grid.nx + 0.5) * grid.cs
        ti = _on_mesh(x, y, span_tris.get(s), p, tris)
        if ti is not None:
            out.append((x, y, float(z[s]), ti))
    return out


def door_points(doors, grid, col, z, span_tris, verts, tris):
    """(x, y, z, triangle) for every door threshold standing on a lattice quad."""
    by_col = {}
    for s in span_tris:
        by_col.setdefault(int(col[s]), []).append(s)
    p = np.asarray(verts, float)
    out = []
    for (x, y, dz, _r, _tp, _w) in doors:
        for s in sorted(by_col.get(grid.column_at(x, y), ()), key=lambda s: abs(z[s] - dz)):
            ti = _on_mesh(x, y, span_tris[s], p, tris) if abs(z[s] - dz) < DOOR_DZ else None
            if ti is not None:
                out.append((x, y, float(z[s]), ti))
                break
    return out


def _walks_through(nodes, edges, lo, hi):
    """True when a pathgrid edge passes through box lo..hi at an actor's body height."""
    for (i, j) in edges:
        a, b = np.asarray(nodes[i], float), np.asarray(nodes[j], float)
        n = max(2, int(np.hypot(*(b - a)[:2]) // params.CS) + 1)
        p = a + (b - a) * np.linspace(0.0, 1.0, n)[:, None]
        inside = ((p[:, 0] >= lo[0]) & (p[:, 0] <= hi[0]) & (p[:, 1] >= lo[1]) & (p[:, 1] <= hi[1])
                  & (p[:, 2] + params.MAX_CLIMB <= hi[2]) & (p[:, 2] + params.AGENT_HEIGHT >= lo[2]))
        if inside.any():
            return True
    return False


def open_activators(refr_recs, base_model_by_fid, get_collision, nodes, edges, activators):
    """Placed activators a pathgrid edge walks straight through: moving parts, left open.

    A secret wall or gate is placed closed, but the pathgrid records the way
    through it; its collision would wall the protected corridor shut.
    """
    out = []
    for refr in refr_recs or ():
        if world.base_fid(refr) not in activators:
            continue
        w, b = world.gather_cell_geometry([refr], base_model_by_fid, get_collision)
        pts = np.concatenate([w.reshape(-1, 3), b.reshape(-1, 3)])
        if len(pts) and _walks_through(nodes, edges, pts.min(0), pts.max(0)):
            out.append(id(refr))
    return out


def build_lattice(refr_recs, base_model_by_fid, get_collision, nodes, edges,
                  land_rec=None, origin_x=0.0, origin_y=0.0, doors=None,
                  door_bases=None, activators=None, water_z=None):
    """(verts, tris, ledges) for one cell, or three empty lists.

    doors: [(x, y, z, rot_z, is_teleport, width), ...].  water_z: or None.
    activators: ACTI base FormIDs (see open_activators); None uses set_activators'.
    ledges: [(upper_tri, lower_tri, drop), ...] drop-down links.
    """
    if not nodes or not edges:
        return [], [], []
    acti = _ACTIVATORS[0] if activators is None else activators
    moving = set(open_activators(refr_recs, base_model_by_fid, get_collision, nodes, edges,
                                 acti))
    refr_recs = [r for r in refr_recs or () if id(r) not in moving]
    walkable, blocking = world.gather_cell_geometry(
        refr_recs, base_model_by_fid, get_collision, land_rec=land_rec,
        origin_x=origin_x, origin_y=origin_y, skip_bases=door_bases, water_z=water_z)
    grid, reach = _domain(walkable, nodes, land_rec, origin_x, origin_y)
    col, z, kept, seed, links = build_graph(
        grid, walkable, blocking, nodes, edges, list(doors or ()), reach)
    verts, tris, owner, corner = lattice(grid, col, z, kept, links)
    if not tris:
        return [], [], []
    border = ((origin_x, origin_y, origin_x + _CELL, origin_y + _CELL)
              if land_rec is not None else None)
    pinned, seam = seam_roles(verts, tris, border)
    surface = surface_sampler(grid, col, verts, tris, owner)
    tris, span_tris, probes = merge_flat(grid, col, kept, links, verts, corner, pinned)
    must = (pathgrid_points(grid, col, z, seed, span_tris, verts, tris)
            + door_points(list(doors or ()), grid, col, z, span_tris, verts, tris))
    verts, tris = simplify(verts, tris, pinned, must, surface, probes, seam)
    ledges = find_ledge_links([list(v) for v in verts], tris)
    return verts, tris, [(int(a), int(b), float(d)) for (a, b, d) in ledges]
