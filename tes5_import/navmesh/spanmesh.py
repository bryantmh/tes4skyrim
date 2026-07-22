"""Walkable spans -> triangles, meshed directly from the 3D span graph.

This replaces the contour/height-map mesher, which was structurally unable to get
this right.

Why the old approach could not work
-----------------------------------
A contour is a HEIGHT MAP: one Z per (cx,cy) column.  A walkable surface in a
building is not.  A staircase carries an NPC over the room below it, so the same
column holds two walkable surfaces; a two-storey house holds two more.  The old
mesher tried to slice the world into height-map "layers" and contour each one, and
every defect we chased came from the seams between those slices:

  * a staircase peeled into five layers, each contoured on its own, each an island
    joined to the next only at a triangle corner;
  * a layer boundary that fell between two floors let the triangulator bridge them,
    producing a wall of near-vertical triangles "connecting" storey 1 to storey 2;
  * a short pathgrid stub became its own layer and was culled for being small,
    leaving a pathgrid line with no navmesh under it.

Tuning the slicer traded these defects for one another indefinitely.

What this does instead
----------------------
Mesh the span graph directly.  A walkable SPAN — not a column — is the unit:

    node      = (cx, cy, span_index)          two floors in one column are two nodes
    adjacent  = neighbouring columns, span tops within MAX_CLIMB
    geometry  = one quad per span, on the column's four lattice corners

A corner's Z is shared by every span that meets there, so adjacent spans literally
share vertices and the mesh is connected BY CONSTRUCTION.  Nothing has to be
stitched, welded or repaired afterwards.  Two spans a storey apart are never
adjacent, so no triangle can ever bridge them: the cross-floor bug is not fixed
here so much as made unrepresentable.

The raw quad mesh is far too dense (one quad per voxel cell), so it is simplified
afterwards (collapse + flip + smooth, see the Simplification section) — but every
move is local and quality/fidelity-bounded and never changes which spans are
connected, so it cannot reintroduce any of the above.
"""

import math

import numpy as _np

from ._native_loader import load_native

# Decimation runs in the C++ extension (native/src/decimate.cpp), whose built
# .pyd is committed under native/dist/.  Rebuild with `python native/build.py`.
# This is deliberately NOT optional: a silent fall back to a Python
# implementation would make navmesh output depend on whether the .pyd happened
# to be present, which breaks the pipeline's determinism contract.
_native = load_native()
from . import params
from .voxel import span_z


def _corner_key(cx, cy, si):
    return (cx, cy, si)


def build_mesh(hf, region_of=None, doors=None):
    """Walkable spans -> (verts3d, tris).  Connected by construction.

    doors: optional [(x, y, z, rot_z), ...] door thresholds.  Each gets an
    exact oriented quad stamped into the raw mesh (see _stamp_door_quads);
    its corner vertices are pinned through decimation, so the final mesh
    always carries two clean triangles precisely on every door threshold.
    """
    w, h, cs = hf.w, hf.h, hf.cs
    climb = params.MAX_CLIMB

    # Walkable spans per column, as (span_index, effective_z).  A protected
    # span meshes at the PATHGRID height, not its (possibly furniture-merged)
    # voxel top — see voxel.span_z.
    walk = {}
    for cy in range(h):
        for cx in range(w):
            col = hf.spans[cy * w + cx]
            ws = [(si, span_z(s)) for si, s in enumerate(col) if s[2]]
            if ws:
                walk[(cx, cy)] = ws
    if not walk:
        return [], []

    # --- corner vertices -----------------------------------------------------
    # A span occupies the lattice square [cx,cx+1] x [cy,cy+1].  Each of its four
    # corners is shared with the spans of the (up to) four columns touching that
    # corner — but ONLY those whose top is within a step, i.e. the ones an NPC can
    # actually walk between.  Spans on different floors touch the same corner in XY
    # and must NOT share a vertex, so each corner is split into groups of mutually
    # step-reachable spans, one vertex per group.
    #
    # corner_groups[(lx,ly)] = list of [z_sum, n, {span_key: True}]
    corner_groups = {}

    def corner_spans(lx, ly):
        """(span_key, top_z) of every walkable span touching lattice corner."""
        out = []
        for (dx, dy) in ((0, 0), (-1, 0), (0, -1), (-1, -1)):
            key = (lx + dx, ly + dy)
            for (si, z) in walk.get(key, ()):
                out.append(((key[0], key[1], si), z))
        return out

    vert_of = {}          # (lx, ly, group_index) -> vertex index
    span_corner = {}      # (span_key, corner_ix) -> vertex index
    verts = []

    for (cx, cy), ws in walk.items():
        for (si, _z) in ws:
            for ci, (lx, ly) in enumerate(((cx, cy), (cx + 1, cy),
                                           (cx + 1, cy + 1), (cx, cy + 1))):
                if (lx, ly) not in corner_groups:
                    # Build the corner's groups once: cluster the spans touching it
                    # by step-reachability in Z.
                    spans_here = sorted(corner_spans(lx, ly), key=lambda t: t[1])
                    groups = []
                    for (skey, z) in spans_here:
                        if groups and z - groups[-1][-1] <= climb:
                            groups[-1][0].append(skey)
                            groups[-1][1].append(z)
                            groups[-1][-1] = z
                        else:
                            groups.append([[skey], [z], z])
                    corner_groups[(lx, ly)] = groups
                    for gi, g in enumerate(groups):
                        zs = g[1]
                        verts.append((hf.min_x + lx * cs,
                                      hf.min_y + ly * cs,
                                      sum(zs) / len(zs)))
                        vert_of[(lx, ly, gi)] = len(verts) - 1

                skey = (cx, cy, si)
                gi = None
                for k, g in enumerate(corner_groups[(lx, ly)]):
                    if skey in g[0]:
                        gi = k
                        break
                if gi is None:
                    continue
                span_corner[(skey, ci)] = vert_of[(lx, ly, gi)]

    # --- quads ---------------------------------------------------------------
    # `seen` dedups coincident quads: two walkable spans of one column within
    # MAX_CLIMB of each other (a protected ribbon under a real floor) land in
    # the same corner groups and would emit identical triangles twice.
    tris = []
    seen = set()
    for (cx, cy), ws in walk.items():
        for (si, _z) in ws:
            skey = (cx, cy, si)
            c = [span_corner.get((skey, i)) for i in range(4)]
            if any(v is None for v in c):
                continue
            a, b, d, e = c            # SW, SE, NE, NW
            if len({a, b, d, e}) < 3:
                continue
            for t in (((a, b, d)) if (a != b and b != d and d != a) else None,
                      ((a, d, e)) if (a != d and d != e and e != a) else None):
                if t is None:
                    continue
                k = tuple(sorted(t))
                if k in seen:
                    continue
                seen.add(k)
                tris.append(t)

    verts, tris, pinned = _stamp_door_quads(verts, tris, doors)
    return _decimate(verts, tris, cs, pinned)


def _stamp_door_quads(verts, tris, doors):
    """Rebuild the mesh under each door into an exact oriented threshold quad.

    The Door Triangle must be a well-shaped triangle sitting precisely on the
    doorway threshold — the old approach (link whatever decimated triangle
    happened to be nearest the door) produced door triangles of arbitrary
    shape and position.  Here, while the mesh is still at voxel resolution,
    every vertex inside the door's rect (oriented by the door's RotZ) is
    snapped onto the rect's four corners.  Triangles wholly inside the rect
    degenerate and vanish; triangles crossing the rect boundary stretch to
    its corners; and the rect itself is covered by exactly two clean
    triangles whose shared edge crosses the door line.  The corner vertices
    are PINNED through decimation, so the quad survives to the output intact.

    Runs on the raw voxel mesh, where vertices are dense (every cs units), so
    a rect over walkable floor always captures vertices; after decimation the
    triangles are bigger than the rect and there would be nothing to snap.

    Returns (verts, tris, pinned_vertex_ids).
    """
    if not doors:
        return verts, tris, frozenset()
    hw = params.DOOR_QUAD_HALF_WIDTH
    hd = params.DOOR_QUAD_HALF_DEPTH
    ztol = params.DOOR_QUAD_ZTOL

    verts = [list(v) for v in verts]
    pinned = set()
    vmap = {}
    quad_tris = []
    for (dx, dy, dz, rz) in doors:
        tx, ty = math.cos(rz), math.sin(rz)       # threshold (width) axis
        fx, fy = -ty, tx                          # facing (walk-through) axis
        members = ([], [], [], [])                # per corner
        inside = []
        for vi, v in enumerate(verts):
            if vi in pinned or vi in vmap:
                continue                          # already claimed by a door
            ox, oy = v[0] - dx, v[1] - dy
            a = ox * tx + oy * ty
            b = ox * fx + oy * fy
            if abs(a) <= hw and abs(b) <= hd and abs(v[2] - dz) <= ztol:
                ci = {(False, False): 0, (True, False): 1,
                      (True, True): 2, (False, True): 3}[(a >= 0, b >= 0)]
                members[ci].append(vi)
                inside.append(vi)
        if not inside:
            continue                              # no mesh at this door
        z_all = sum(verts[vi][2] for vi in inside) / len(inside)
        cids = []
        for ci, (sa, sb) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
            cxw = dx + sa * hw * tx + sb * hd * fx
            cyw = dy + sa * hw * ty + sb * hd * fy
            ms = members[ci]
            zm = (sum(verts[m][2] for m in ms) / len(ms)) if ms else z_all
            verts.append([cxw, cyw, zm])
            cids.append(len(verts) - 1)
        for ci in range(4):
            for m in members[ci]:
                vmap[m] = cids[ci]
        pinned.update(cids)
        # CCW like the span quads ((t, f) is a right-handed frame).
        quad_tris.append((cids[0], cids[1], cids[2]))
        quad_tris.append((cids[0], cids[2], cids[3]))

    if not vmap:
        return verts, tris, frozenset(pinned)

    out = []
    seen = set()
    for (a, b, c) in tris:
        a = vmap.get(a, a)
        b = vmap.get(b, b)
        c = vmap.get(c, c)
        if len({a, b, c}) < 3:
            continue
        k = (a, b, c) if a < b and a < c else \
            (b, c, a) if b < c else (c, a, b)
        km = tuple(sorted(k))
        if km in seen:
            continue
        seen.add(km)
        out.append((a, b, c))
    for t in quad_tris:
        km = tuple(sorted(t))
        if km not in seen:
            seen.add(km)
            out.append(t)
    return verts, out, frozenset(pinned)


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------
#
# The raw mesh has a quad per voxel cell — one to two orders of magnitude more
# triangles than Skyrim's NVNM wants (it indexes triangles with an int16), all
# grid-aligned, with sawtooth outlines wherever a wall runs diagonally.  It is
# simplified with the three classic constrained-remeshing moves, each of which
# provably preserves what the span graph guaranteed (connectivity, and no
# cross-floor triangles, whose z-spread no move here can increase past the
# existing spread of a one-ring):
#
#   COLLAPSE  merge a vertex into a neighbour.  Bounded by surface fidelity
#             (MAX_SIMPLIFY_ERR to the old planes), triangle shape (MAX_ASPECT),
#             edge length, orientation (no folds) and a link condition (no
#             non-manifold pinches).  Boundary vertices are NOT pinned: a
#             boundary vertex may slide away along its own boundary chain when
#             it deviates less than MAX_SIMPLIFY_ERR from the straight line
#             between its two outline neighbours — this is what turns the 16u
#             voxel sawtooth into clean diagonals (octagon, not saw blade).
#   FLIP      Lawson edge flip between two near-coplanar triangles when it
#             improves the worse of the two shapes.  Geometry does not move at
#             all, so this is free of fidelity risk; it is what dissolves the
#             high-valence fans a greedy collapse leaves behind.
#   SMOOTH    tangential relaxation: move an interior vertex of a locally flat
#             one-ring toward its neighbours' centroid, IN the surface plane.
#             This is what makes triangles come out near-equilateral like the
#             CK's, instead of echoing the voxel grid.
#
# A few rounds of collapse+flip+smooth converge quickly.

def _decimate(verts, tris, cs, pinned=frozenset()):
    """Collapse + flip + smooth, run in the C++ extension.

    Returns (verts, tris): a list of 3-tuples and a list of index triples.

    The simplification itself is documented in native/src/decimate.cpp, which
    is the only implementation — see the Simplification notes there for what
    each pass does and which quality bounds it enforces.
    """
    if not tris:
        return verts, tris

    # Edge budget scales with the voxel size: exteriors (CS 32) are smooth
    # terrain and take triangles twice the interior scale without losing shape.
    max_edge = params.TRI_TARGET_EDGE * (cs / params.CS)
    varr = _np.asarray(verts, dtype=_np.float64)
    tarr = _np.asarray(tris, dtype=_np.int32)
    parr = _np.zeros(len(verts), dtype=_np.bool_)
    for i in pinned:
        if 0 <= i < len(verts):
            parr[i] = True

    ov, ot = _native.decimate(
        varr, tarr, parr, float(cs), float(max_edge),
        float(params.MAX_SIMPLIFY_ERR), float(params.MAX_ASPECT),
        float(params.MAX_EDGE_RATIO), int(params.SIMPLIFY_PASSES))

    return ([tuple(p) for p in ov.tolist()],
            [tuple(t) for t in ot.tolist()])
