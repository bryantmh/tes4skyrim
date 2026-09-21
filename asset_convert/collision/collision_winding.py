"""Repair inverted collision winding: the "I fall through the floor" fix.

Split out of collision.py.  A collision triangle whose winding points the
wrong way is one-sided the wrong side, so the player falls through it.  The
repair works purely on triangle index tuples and vertex lists -- it never
touches a NIF block -- and uses the RENDER mesh as its orientation oracle.

See: docs/commentary/asset_convert_collision.md#inverted-collision-winding-i-fall
See: docs/commentary/asset_convert_collision.md#morroblivion-collision-is-copied-render
"""

import math
from itertools import permutations

from core.collision_options import winding_fix_enabled
from asset_convert.collision.collision_falloutnv import is_fallout_source

#: An authored normal must oppose the face normal by this much to count.
AUTHORED_NORMAL_DOT = -0.3

#: Triangles rewound so far; a list so process workers can mutate it.
INVERTED_FLOOR_FLIPS = [0]

#: Vertex-set quantum for the twin match, in Skyrim havok units (~0.02 game).
_TWIN_QUANTUM = 0.02 / 69.9904

#: A render face must align with the collision face by at least this much.
_PARALLEL = 0.70


def face_normal(tri):
    """Normalized face normal for a triangle given as three xyz tuples."""
    (v0, v1, v2) = tri
    ux, uy, uz = v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2]
    vx, vy, vz = v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2]
    nx = uy*vz - uz*vy
    ny = uz*vx - ux*vz
    nz = ux*vy - uy*vx
    mag = math.sqrt(nx*nx + ny*ny + nz*nz)
    if mag > 0:
        nx /= mag; ny /= mag; nz /= mag
    return nx, ny, nz


def _vertex_key(tri):
    """Order-independent quantized key for a triangle's three corners."""
    return tuple(sorted(tuple(round(c / _TWIN_QUANTUM) for c in v)
                        for v in tri))


def _render_faces(visual_tris):
    """[(tri, normal)] for each render face with a usable normal."""
    out = []
    for t in visual_tris or ():
        n = face_normal(t)
        if n[0] or n[1] or n[2]:
            out.append((t, n))
    return out


def _twin_index(faces):
    """{vertex_key: [normal, ...]} over the render faces."""
    idx = {}
    for tri, n in faces:
        idx.setdefault(_vertex_key(tri), []).append(n)
    return idx


def _twin_says_inverted(tri, n, twins):
    """Whether the render face this collision face COPIES is opposed.

    None when no render face shares this exact vertex set.
    See: docs/commentary/asset_convert_collision.md#morroblivion-collision-is-copied-render
    """
    got = twins.get(_vertex_key(tri))
    if not got:
        return None
    best = max(got, key=lambda o: abs(n[0]*o[0] + n[1]*o[1] + n[2]*o[2]))
    dot = n[0]*best[0] + n[1]*best[1] + n[2]*best[2]
    return dot < 0 if abs(dot) > 0.5 else None


def _projected_overlap(ctri, cn, rtri):
    """Whether the two triangles overlap projected along cn's dominant axis."""
    ax = max(range(3), key=lambda i: abs(cn[i]))
    u, v = [i for i in range(3) if i != ax]
    a = [(p[u], p[v]) for p in ctri]
    b = [(p[u], p[v]) for p in rtri]
    for poly, other in ((a, b), (b, a)):
        for i in range(3):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % 3]
            nx, ny = -(y2 - y1), (x2 - x1)
            pa = [nx * (px - x1) + ny * (py - y1) for px, py in poly]
            pb = [nx * (px - x1) + ny * (py - y1) for px, py in other]
            if max(pb) < min(pa) - 1e-6 or min(pb) > max(pa) + 1e-6:
                return False
    return True


def _vertex_set_distance(a, b):
    """Least total corner-to-corner distance over the six pairings."""
    best = None
    for p in permutations(range(3)):
        d = 0.0
        for i in range(3):
            q = b[p[i]]
            d += math.sqrt((a[i][0]-q[0])**2 + (a[i][1]-q[1])**2
                           + (a[i][2]-q[2])**2)
        if best is None or d < best:
            best = d
    return best


def _nearest_says_inverted(tri, n, faces):
    """Whether the closest coincident render surface is opposed.

    Nearest by VERTEX SET, for collision that is a simplification of the
    render mesh rather than a copy of it.
    See: docs/commentary/asset_convert_collision.md#morroblivion-collision-is-copied-render
    """
    best = None
    for rtri, rn in faces:
        align = n[0]*rn[0] + n[1]*rn[1] + n[2]*rn[2]
        if abs(align) < _PARALLEL:
            continue
        if not _projected_overlap(tri, n, rtri):
            continue
        d = _vertex_set_distance(tri, rtri)
        if best is None or d < best[0]:
            best = (d, align)
    return None if best is None else best[1] < 0


def _authored_flips(tris, authored_normals):
    """Step 0: triangles whose winding contradicts their own stored normal.

    Ungated: it reads a fact the file states about itself, so it is safe on
    every plugin and inert wherever winding and normal already agree.
    See: docs/commentary/asset_convert_collision.md#rewritten-2026-08-20-round-3--the-winding-is-authored-stop-inferring-it
    """
    out = set()
    if not authored_normals or len(authored_normals) != len(tris):
        return out
    for i, (t, an) in enumerate(zip(tris, authored_normals)):
        if an is None:
            continue
        alen = math.sqrt(an[0]**2 + an[1]**2 + an[2]**2)
        if alen < 1e-6:
            continue
        n = face_normal(t)
        if (n[0]*an[0] + n[1]*an[1] + n[2]*an[2]) / alen < AUTHORED_NORMAL_DOT:
            out.add(i)
    return out


def _render_flips(tris, faces):
    """Indices the render mesh says are wound backwards."""
    twins = _twin_index(faces)
    flip = set()
    for i, t in enumerate(tris):
        n = face_normal(t)
        if not (n[0] or n[1] or n[2]):
            continue
        verdict = _twin_says_inverted(t, n, twins)
        if verdict is None:
            verdict = _nearest_says_inverted(t, n, faces)
        if verdict:
            flip.add(i)
    return flip


def _rewound(tris, flip):
    """`(tris, n)` with every index in `flip` reversed; the input when empty."""
    if not flip:
        return tris, 0
    out = [(t[0], t[2], t[1]) if i in flip else t
           for i, t in enumerate(tris)]
    return out, len(flip)


def repair_inverted_floors(tris, visual_tris=None, groups=None,
                           authored_normals=None):
    """Rewind collision triangles wound backwards; `(repaired_tris, n_flipped)`.

    Step 0 (the authored normal) is ungated; the render-mesh repair is gated
    per plugin except for FO3/FNV sources, and supersedes step 0 where it
    reaches a verdict.  `groups` is accepted for call compatibility only.
    See: docs/commentary/asset_convert_collision.md#morroblivion-collision-is-copied-render
    See: docs/commentary/asset_convert_falloutnv.md#two-sided-welding
    """
    if not tris:
        return tris, 0

    flip = _authored_flips(tris, authored_normals)
    if not (winding_fix_enabled() or is_fallout_source()):
        return _rewound(tris, flip)

    faces = _render_faces(visual_tris)
    if faces:
        flip = _render_flips(tris, faces)
    return _rewound(tris, flip)
