"""Extract Havok COLLISION geometry from the CONVERTED Skyrim NIFs as a triangle soup.

Why collision and not render geometry
-------------------------------------
The navmesh builder needs to know what an NPC can stand on and what stops them.
That is *exactly* what the Havok collision mesh encodes — it is the geometry the
game engine itself uses for the same decision.  The render mesh is the wrong
source (it has no notion of solidity), and a 2D convex hull of it is worse still:
an architecture shell is a HOLLOW BOX, so its hull is a solid rectangle covering
the whole room.  A hull can never represent a wall.

We read the collision tree and emit, per mesh, a local-space triangle soup split
by surface normal:

    WALKABLE  |nz| >= cos(MAX_SLOPE)   floors, stair treads, bridge decks, ramps
    BLOCKING  everything steeper       walls, pillars, railings, crate sides

That single normal test cleanly separates floors from walls with no heuristic —
architecture/anvil/anvilfgfirstfloor.nif yields 266 walkable / 421 blocking
triangles, matching the hand-painted wall reference for that cell.

Why the CONVERTED (output/) meshes, not the source (export/) ones
-----------------------------------------------------------------
We read `output/.../meshes/tes4/**.nif` — the Skyrim NIFs our own pipeline
already produced — not the original Oblivion meshes.  This is strictly better:

  * Collision is moved to the ROOT node during conversion.  In the Oblivion
    source, the bhkCollisionObject hangs off a NON-root NiNode in ~17% of meshes,
    so extracting from source would require accumulating the whole NiNode
    transform chain (and getting it wrong would silently misplace those shapes).
  * The shape is a single bhkCompressedMeshShape (CMS) — one flat triangle soup.
    No strip decoding, no bhkListShape recursion, no box/capsule/convex
    triangulation, no bhkRigidBodyT quaternion baking.  All of that is already
    resolved and baked in by the converter.
  * `asset_convert/collision/cms.py::decode_cms` already decodes it, and the mesh keys
    (`tes4/...`) are exactly the keys the import pipeline uses.

Verified lossless: the converted CMS for anvilfgfirstfloor.nif contains 725
triangles — the same count as the source bhkNiTriStripsShape.

Units: decode_cms returns havok units.  Game units = havok × CMS_TO_GAME (70.0),
measured empirically as exactly 70.0000 on all three axes against the source
mesh (= 7 game-units-per-havok-unit ÷ the 0.1 _HAVOK_SCALE the converter applies).

What this module deliberately does NOT decide
---------------------------------------------
It never judges whether a mesh is "an obstacle".  That question is meaningless
per-mesh, because Oblivion collision meshes are ORIGIN-CENTERED: a ref's PosZ
sits at the object's MIDDLE, not its base.  Measured:

    lowerclasstable01.nif   collision z [-28.7 .. +28.7]   (a ~57u tall table)
    lowerclassbench01.nif   total collision height 30u     (BELOW the 34u step)

A local-space "short => steppable" rule would call a dining table walk-over-able.
Whether something obstructs depends on how far it rises above the floor beneath
it *in world space*, which only the voxelizer can know.  This module therefore
stores geometry only; the verdict happens in tes5_import/navmesh/voxel.py.

Cache: two-phase, mirroring mesh_bounds.
    scan_collision(mesh_dir, cache_path)   — once, after mesh conversion
    load_collision(cache_path)             — in each navmesh worker
"""

from asset_convert.game_paths import current_namespace
from output_layout import assets_for
import hashlib
import json
import math
import os
import struct
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

from core.worker_budget import worker_count

# --- Constants ----------------------------------------------------------------

# Walkable if the surface normal is within MAX_SLOPE of straight up.
MAX_SLOPE_DEG = 46.0
_COS_MAX_SLOPE = math.cos(math.radians(MAX_SLOPE_DEG))

#: Havok->game units. See: docs/commentary/asset_convert_collision.md#collision-extraction-scale
CMS_TO_GAME = 70.0
#: Havok->game units. See: docs/commentary/asset_convert_collision.md#collision-extraction-scale
PRIM_TO_GAME = 70.0

# OblivionLayer values survive conversion in bhkRigidBody.havok_col_filter.layer.
# Only real world collision supports/obstructs an NPC.
OL_STATIC = 1
OL_ANIM_STATIC = 2
OL_TERRAIN = 13
OL_GROUND = 17
OL_STAIRS = 19

# Ignored: OL_BIPED(8) actor ragdolls, OL_CLUTTER(4) loose physics props (pushed
# aside, not walked around), OL_TRANSPARENT(3), OL_TRIGGER(12),
# OL_NONCOLLIDABLE(15), weapons/projectiles/trees/props.  Ignoring these is what
# fixes the old converter's "too many objects are avoided".
_PATHING_LAYERS = frozenset({OL_STATIC, OL_ANIM_STATIC, OL_TERRAIN,
                             OL_GROUND, OL_STAIRS})

WALKABLE = 0
BLOCKING = 1


# ---------------------------------------------------------------------------
# Primitive shapes

_BOX_FACES = ((0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
              (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
              (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0))


def _aabb_tris(x0, y0, z0, x1, y1, z1):
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    return [(c[i], c[j], c[k]) for (i, j, k) in _BOX_FACES]


def _convex_hull_tris(pts):
    """Triangulate a convex point cloud via its 3D hull (AABB fallback)."""
    if len(pts) < 4:
        return []
    try:
        import numpy as np
        from scipy.spatial import ConvexHull  # type: ignore
        hull = ConvexHull(np.array(pts, dtype=np.float64))
        return [(pts[a], pts[b], pts[c]) for (a, b, c) in hull.simplices]
    except Exception:
        # Coplanar/degenerate sets raise — an AABB is a safe over-approximation.
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        return _aabb_tris(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _primitive_tris(shape, depth=0):
    """Game-unit triangles for a primitive (or list/transform of primitives)."""
    if shape is None or depth > 6:
        return []
    n = type(shape).__name__
    s = PRIM_TO_GAME

    if n == 'bhkListShape':
        out = []
        for sub in shape.sub_shapes:
            out.extend(_primitive_tris(sub, depth + 1))
        return out

    if n in ('bhkConvexTransformShape', 'bhkTransformShape'):
        inner = _primitive_tris(getattr(shape, 'shape', None), depth + 1)
        try:
            t = shape.transform
            m = ((t.m_11, t.m_12, t.m_13),
                 (t.m_21, t.m_22, t.m_23),
                 (t.m_31, t.m_32, t.m_33))
            tr = (t.m_14 * s, t.m_24 * s, t.m_34 * s)

            def xf(p):
                return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + tr[0],
                        m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + tr[1],
                        m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + tr[2])
            return [tuple(xf(p) for p in tri) for tri in inner]
        except AttributeError:
            return inner

    if n == 'bhkConvexVerticesShape':
        return _convex_hull_tris([(v.x * s, v.y * s, v.z * s)
                                  for v in shape.vertices])

    if n == 'bhkBoxShape':
        d = shape.dimensions
        hx, hy, hz = d.x * s, d.y * s, d.z * s
        return _aabb_tris(-hx, -hy, -hz, hx, hy, hz)

    if n == 'bhkCapsuleShape':
        p1, p2 = shape.first_point, shape.second_point
        r = shape.radius * s
        a = (p1.x * s, p1.y * s, p1.z * s)
        b = (p2.x * s, p2.y * s, p2.z * s)
        return _aabb_tris(min(a[0], b[0]) - r, min(a[1], b[1]) - r,
                          min(a[2], b[2]) - r,
                          max(a[0], b[0]) + r, max(a[1], b[1]) + r,
                          max(a[2], b[2]) + r)

    if n == 'bhkSphereShape':
        r = shape.radius * s
        return _aabb_tris(-r, -r, -r, r, r, r)

    return []


def _classify(a, b, c):
    """WALKABLE if the triangle normal is within MAX_SLOPE of up, else BLOCKING."""
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln < 1e-12:
        return None                     # degenerate
    return WALKABLE if abs(nz) / ln >= _COS_MAX_SLOPE else BLOCKING


# ---------------------------------------------------------------------------
# Per-NIF extraction
# ---------------------------------------------------------------------------

def extract_nif_collision(nif_path: str) -> Optional[dict]:
    """Return {'w': [9N floats], 'b': [9M floats]} in GAME units, or None.

    Convenience wrapper that parses *nif_path* and delegates.  Prefer
    `collision_from_data` when the caller already holds a parsed NIF — reading
    the file is ~174 ms of the ~205 ms this costs, so a second parse is by far
    the most expensive thing about calling this twice (see scan_mesh_data).
    """
    return collision_from_data(read_nif_data(nif_path))


def read_nif_data(nif_path: str):
    """Parse a NIF into a pyffi Data object.

    Split out so one parse can feed several analyses (bounds + collision).
    """
    import time as _t
    if not hasattr(_t, 'clock'):
        _t.clock = _t.perf_counter  # pyffi 2.2.3 still calls the removed time.clock
    from pyffi.formats.nif import NifFormat

    data = NifFormat.Data()
    with open(nif_path, 'rb') as fh:
        data.read(fh)
    return data


def collision_from_data(data) -> Optional[dict]:
    """Collision soup from an ALREADY-PARSED NIF (see extract_nif_collision).

    A body is placed at its OWNING NODE's world transform: collision usually
    sits on the root, but where it hangs off a moved child the engine still
    honours that chain.  Non-pathing layers (clutter, biped ragdolls,
    triggers) are dropped wholesale.
    See: docs/commentary/asset_convert_collision.md#body-placement
    """
    walk: List[float] = []
    block_: List[float] = []
    placement = _body_placements(data)

    for body in data.blocks:
        if type(body).__name__ not in ('bhkRigidBody', 'bhkRigidBodyT'):
            continue
        tris = _body_tris(body)
        place = placement.get(id(body))
        if place is not None:
            tris = [tuple(place(pt) for pt in tri) for tri in tris]

        for (a, b, c) in tris:
            cls = _classify(a, b, c)
            if cls is None:
                continue
            dst = walk if cls == WALKABLE else block_
            dst.extend((a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]))

    if not walk and not block_:
        return None
    return {'w': walk, 'b': block_}


def bounds_from_data(data):
    """AABB over every NiTriShapeData vertex, as an OBND 6-tuple, or None.

    Lives here rather than in tes5_import.base.mesh_bounds so that one parsed NIF can
    produce BOTH the bounds and the collision soup — see scan_mesh_data.
    """
    import math

    xs: list = []
    ys: list = []
    zs: list = []
    for block in data.blocks:
        if type(block).__name__ == 'NiTriShapeData':
            if block.has_vertices:
                for i in range(block.num_vertices):
                    v = block.vertices[i]
                    xs.append(v.x)
                    ys.append(v.y)
                    zs.append(v.z)
    if not xs:
        return None
    return (
        int(math.floor(min(xs))), int(math.floor(min(ys))),
        int(math.floor(min(zs))),
        int(math.ceil(max(xs))), int(math.ceil(max(ys))),
        int(math.ceil(max(zs))),
    )


# Schema version of mesh_bounds_cache.json, stored under _BOUNDS_SCHEMA_KEY.
#
# Bump this whenever an entry gains a field or a field changes meaning.  Any
# cache written before versioning, or at a lower version, is REGENERATED rather
# than trusted: entries are plain lists, so a missing trailing field is
# indistinguishable from a computed zero and the reader cannot tell a stale
# cache from a complete one.
#
# That exact hole cost a shipped bug.  `physics_flags_from_data` gained the
# HELD bit (bit 1) on 2026-08-05, but the scan only ran when the cache file was
# ABSENT, so Nehrim kept its 2026-08-02 cache forever: 0 of its 11,946 meshes
# carried the bit, `needs_havok_release` answered False for every one, and no
# converted `playgroup` ever emitted TES4Polyfill.ReleaseBreakaway.
# mwallplankbreakaway01's planks hung in mid-air.  Oblivion's cache happened to
# be rebuilt an hour after that commit, so the same meshes worked there — which
# is why this looked like a Nehrim-only mesh bug rather than a stale cache.
BOUNDS_SCHEMA_VERSION = 3
_BOUNDS_SCHEMA_KEY = '__schema__'

#: Bumped when a door axis entry gains a field or a field changes meaning.
DOOR_AXIS_SCHEMA_VERSION = 1


def door_axis_cache_is_current(axis_cache: str) -> bool:
    """True if the door axis cache exists AND was written at this schema.

    Entries are plain lists, so one written before a field existed loads
    cleanly and silently contributes no center and no floor drop.
    See: docs/commentary/tes5_import_pipeline.md#phase-0-stale-bounds-cache
    """
    try:
        with open(axis_cache, encoding='utf-8') as fh:
            raw = json.load(fh)
        return int(raw[_BOUNDS_SCHEMA_KEY][0]) >= DOOR_AXIS_SCHEMA_VERSION
    except (OSError, json.JSONDecodeError, ValueError, KeyError,
            TypeError, IndexError):
        return False


def bounds_cache_is_current(bounds_cache: str) -> bool:
    """True if *bounds_cache* exists AND was written at the current schema.

    Callers gate the rescan on this instead of os.path.exists, so a cache
    predating a field addition is refreshed instead of silently serving
    incomplete entries.
    """
    try:
        with open(bounds_cache, encoding='utf-8') as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    entry = raw.get(_BOUNDS_SCHEMA_KEY)
    try:
        return int(entry[0]) >= BOUNDS_SCHEMA_VERSION
    except (TypeError, ValueError, IndexError):
        return False


def physics_flags_from_data(data) -> int:
    """Physics facts the IMPORTER needs about a converted mesh, as bit flags.

    bit 0 — constrained dynamic island: the mesh contains at least one
    simulated (mass > 0) rigid body AND at least one bhk constraint block.
    Skyrim only simulates such content on MSTT/ACTI references — a STAT
    reference leaves the whole compound frozen (Oblivion's PrisonCellChains01
    is a STAT; every vanilla swinging sign is MSTT, the bone-alarm is ACTI).
    The constraint is checked file-wide, not per-body, because chain links
    routinely carry mass with ncons=0 and hang off an anchor's constraint
    (vanilla trapbonealarmhavok01 stores its bhkBallSocketConstraintChain on
    the static peg, not on the swinging bones).

    bit 1 — HELD body: the mesh ships at least one KEYFRAMED body that kept a
    non-zero mass.  `_convert_collision` writes that combination for exactly
    one thing: a piece Oblivion holds rigid until a script releases it —
    breakaway planks/bricks and constrained trap islands (see the breakaway
    and held-trap cases there).  Every other keyframed body is forced to
    mass 0, so this bit is precisely "this mesh needs
    SetMotionType(Motion_Dynamic) to ever move".

    The script converter reads it to decide whether a converted `playgroup`
    should emit the release.  Keying that off the ANIMATION NAME cannot work:
    'forward' is 491 of the 850 playgroup calls in Oblivion and is
    overwhelmingly gates, doors and portcullises, which must keep following
    their clip exactly — but it is also the tripwire's break group.  The mesh
    knows which it is; the group name does not.

    Shipped as the 7th element of a bounds-cache entry.  A cache written before
    this field existed has 6-element entries that are indistinguishable from
    "computed, and the flags happened to be 0", so absence CANNOT be read as
    zero — that silently disabled the havok release for every mesh in any
    plugin whose cache predated the field (see BOUNDS_SCHEMA_VERSION).
    """
    has_dynamic = False
    has_constraint = False
    flags = 0
    for block in data.blocks:
        cls = type(block).__name__
        if cls in ('bhkRigidBody', 'bhkRigidBodyT'):
            if getattr(block, 'mass', 0) > 0:
                has_dynamic = True
                # MO_SYS_KEYFRAMED (4) with mass retained == held-until-scripted.
                if getattr(block, 'motion_system', 0) == 4:
                    flags |= 2
            if getattr(block, 'num_constraints', 0) > 0:
                has_constraint = True
        elif cls.startswith('bhk') and 'Constraint' in cls:
            has_constraint = True
    if has_dynamic and has_constraint:
        flags |= 1
    return flags


def _tri_shape_points(shape):
    """Vertices of a bhk triangle-mesh collision shape, in shape space.

    bhkPackedNiTriStripsShape keeps them in a shared hkPackedNiTriStripsData;
    bhkNiTriStripsShape keeps a list of NiTriStripsData.  Both are scaled by the
    shape's own `scale` where present.
    """
    pts = []
    data = getattr(shape, 'data', None)
    if data is not None and hasattr(data, 'vertices'):
        for v in data.vertices:
            pts.append((v.x, v.y, v.z))
    for sd in (getattr(shape, 'strips_data', None) or ()):
        if getattr(sd, 'has_vertices', True):
            for v in sd.vertices:
                pts.append((v.x, v.y, v.z))
    if not pts:
        return pts
    # bhkPackedNiTriStripsShape stores vertices already in havok units; the
    # per-shape scale (when it is a vector) applies on top.
    sc = getattr(shape, 'scale', None)
    if sc is not None and hasattr(sc, 'x'):
        sx, sy, sz = sc.x or 1.0, sc.y or 1.0, sc.z or 1.0
        if (sx, sy, sz) != (1.0, 1.0, 1.0):
            pts = [(p[0] * sx, p[1] * sy, p[2] * sz) for p in pts]
    return pts


def _unwrap_shapes(shape, depth=0):
    """Yield the leaf collision shapes inside wrappers and containers.

    bhkMoppBvTreeShape / bhkConvexTransformShape wrap ONE shape; bhkListShape
    holds SEVERAL (the prison cell gates ship as a list of bars).  A door whose
    shape is a container yielded nothing before, which made it look like a
    trapdoor and dropped the door entirely.
    """
    if shape is None or depth > 6:
        return
    name = type(shape).__name__
    if name in ('bhkMoppBvTreeShape', 'bhkConvexTransformShape',
                'bhkTransformShape'):
        yield from _unwrap_shapes(getattr(shape, 'shape', None), depth + 1)
        return
    if name == 'bhkListShape':
        for sub in (getattr(shape, 'sub_shapes', None) or ()):
            yield from _unwrap_shapes(sub, depth + 1)
        return
    yield shape


def _pal_name(sp, off):
    """Resolve a NiStringPalette offset to a node name, or None."""
    if sp is None or off is None or off < 0:
        return None
    try:
        pal = sp.palette.palette
    except AttributeError:
        return None
    if isinstance(pal, str):
        pal = pal.encode('latin1')
    end = pal.find(b'\x00', off)
    return pal[off:end if end >= 0 else None].decode('ascii', 'replace')


def _body_tris(body):
    """Game-unit triangles for one rigid body, or [] if it does not path.

    Non-pathing layers (clutter, biped ragdolls, triggers) are dropped
    wholesale, and bhkMoppBvTreeShape nesting is unwrapped to the real shape.
    """
    from asset_convert.collision.cms import decode_cms

    try:
        layer = int(body.havok_col_filter.layer)
    except AttributeError:
        layer = OL_STATIC
    if layer not in _PATHING_LAYERS:
        return []

    shape = getattr(body, 'shape', None)
    for _ in range(4):
        if shape is None or type(shape).__name__ != 'bhkMoppBvTreeShape':
            break
        shape = getattr(shape, 'shape', None)
    if shape is None:
        return []

    if type(shape).__name__ != 'bhkCompressedMeshShape':
        return _primitive_tris(shape)
    cms_data = getattr(shape, 'data', None)
    if cms_data is None:
        return []
    try:
        return [tuple(tuple(v * CMS_TO_GAME for v in p) for p in tri)
                for (_key, tri) in decode_cms(cms_data)]
    except Exception:
        return []


def _body_placements(data):
    """{id(rigid body): f(point) -> world point} for bodies on a MOVED node.

    An identity chain returns no entry, so the common root-mounted case pays
    nothing.  Scale is uniform in a NIF node, and the shape's own transform is
    already baked in by `_primitive_tris`/`decode_cms`.
    See: docs/commentary/asset_convert_collision.md#body-placement
    """
    out = {}
    ident = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    def walk(node, R, T, sc, seen):
        """Accumulate this node's transform and record any collision body."""
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        m = getattr(node, 'rotation', None)
        lr = ([[m.m_11, m.m_12, m.m_13], [m.m_21, m.m_22, m.m_23],
               [m.m_31, m.m_32, m.m_33]] if m is not None else ident)
        tr = getattr(node, 'translation', None)
        lt = [tr.x, tr.y, tr.z] if tr is not None else [0.0, 0.0, 0.0]
        ls = getattr(node, 'scale', 1.0) or 1.0
        Tw = [sum(R[i][k] * lt[k] * sc for k in range(3)) + T[i]
              for i in range(3)]
        Rw = _mat_mul(R, lr)
        sw = sc * ls
        co = getattr(node, 'collision_object', None)
        body = getattr(co, 'body', None) if co is not None else None
        if body is not None and not _is_identity(Rw, Tw, sw):
            out[id(body)] = _placer(Rw, Tw, sw)
        for ch in (getattr(node, 'children', None) or []):
            walk(ch, Rw, Tw, sw, seen)

    for root in data.roots:
        try:
            walk(root, ident, [0.0, 0.0, 0.0], 1.0, set())
        except Exception:
            continue
    return out


def _is_identity(R, T, s):
    """True when this chain would move nothing (the root-mounted case)."""
    if abs(s - 1.0) > 1e-6 or any(abs(v) > 1e-4 for v in T):
        return False
    return all(abs(R[i][j] - (1.0 if i == j else 0.0)) <= 1e-6
               for i in range(3) for j in range(3))


def _placer(R, T, s):
    """f(point) applying this node chain's rotation, scale and translation."""
    def place(p):
        """One point through the chain."""
        q = (p[0] * s, p[1] * s, p[2] * s)
        return tuple(sum(R[i][k] * q[k] for k in range(3)) + T[i]
                     for i in range(3))
    return place


def _mat_mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _quat_mat(w, x, y, z):
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def _euler_xyz_mat(ax, ay, az):
    import math as _m
    cx, sx = _m.cos(ax), _m.sin(ax)
    cy, sy = _m.cos(ay), _m.sin(ay)
    cz, sz = _m.cos(az), _m.sin(az)
    Rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    Ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    Rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _mat_mul(_mat_mul(Rx, Ry), Rz)


def _close_pose_overrides(data):
    """{node name: (R|None, T|None, keyed)} from the 'Close' sequence.

    Each controlled node's local transform in the fully-CLOSED pose: the FINAL
    key values of its interpolator's data (XYZ rotation groups or quaternion
    keys, plus translation keys), else the interpolator's direct transform
    when finite.  `keyed` is True only when real key data exists — that is
    what distinguishes the moving door leaf from a statically-retargeted
    frame piece.
    """
    seq = None
    for b in data.blocks:
        if type(b).__name__ != 'NiControllerSequence':
            continue
        nm = b.name
        if isinstance(nm, bytes):
            nm = nm.decode('ascii', 'replace')
        if nm.lower() == 'close':
            seq = b
            break
    if seq is None:
        return {}
    out = {}
    for cb in (getattr(seq, 'controlled_blocks', None) or []):
        nn = _pal_name(getattr(cb, 'string_palette', None),
                       getattr(cb, 'node_name_offset', None))
        itp = getattr(cb, 'interpolator', None)
        if not nn or itp is None:
            continue
        R = T = None
        keyed = False
        dt = getattr(itp, 'translation', None)
        if dt is not None and abs(dt.x) < 1e30:
            T = [dt.x, dt.y, dt.z]
        dr = getattr(itp, 'rotation', None)
        if dr is not None and abs(getattr(dr, 'w', 3e38)) < 1e30:
            R = _quat_mat(dr.w, dr.x, dr.y, dr.z)
        d = getattr(itp, 'data', None)
        if d is not None:
            if int(getattr(d, 'rotation_type', 0) or 0) == 4:
                angs = []
                got = False
                for g in d.xyz_rotations:
                    ks = getattr(g, 'keys', None) or []
                    got = got or bool(len(ks))
                    angs.append(float(ks[-1].value) if len(ks) else 0.0)
                if got:
                    # An all-zero final rotation is still a CLOSED pose (the
                    # leaf swings back to identity) — key data existing is
                    # what marks the node as the moving door.
                    R = _euler_xyz_mat(*angs)
                    keyed = True
            else:
                qk = getattr(d, 'quaternion_keys', None) or []
                if len(qk):
                    q = qk[-1].value
                    R = _quat_mat(q.w, q.x, q.y, q.z)
                    keyed = True
            tks = getattr(d.translations, 'keys', None) or []
            if len(tks):
                v = tks[-1].value
                T = [v.x, v.y, v.z]
                keyed = True
        if R is not None or T is not None:
            out[nn] = (R, T, keyed)
    return out


def door_closed_geometry(data):
    """Doorway as ('X'|'Y', width, center_x, center_y, z_min) — WORLD units.

    Reads the ORIGINAL Oblivion NIF's render graph at the CLOSED pose: the
    'Close' controller sequence's final key values override each animated
    node's local transform, then the union bbox of the shapes under the KEYED
    nodes — the door leaf/leaves themselves — gives the doorway span, its
    center relative to the REFR pivot, and the slab's base height.

    This is the AUTHORED closed door, which no static measurement recovers:
    idgate01's two leaves are STORED mid-open (bbox nowhere near the doorway)
    and swing 90 degrees on 'Close'; the converted collision (previous source
    for this cache) additionally baked the leaf transforms wrong, which is
    what rotated and offset the CharacterGen pen gate's Door Triangle.

    Frames, arches and static fence sections carry no keys, so they never
    widen the doorway (idgate01's fixed side grates span 269u; its keyed
    leaves close to 133u).  A model with no Close sequence measures all its
    shapes at rest pose — plain static doors are stored closed.

    Returns None when the closed union is thin in Z (a trapdoor/hatch: swings
    about a horizontal axis, no vertical threshold line exists).
    """
    ovr = _close_pose_overrides(data)
    shapes = []                              # (keyed, lo3, hi3)

    ident = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    def walk(node, R, T, s, keyed):
        name = getattr(node, 'name', b'')
        if isinstance(name, bytes):
            name = name.decode('ascii', 'replace')
        o = ovr.get(name)
        my_keyed = keyed or bool(o and o[2])
        if o and o[0] is not None:
            lr = o[0]
        else:
            m = getattr(node, 'rotation', None)
            lr = ([[m.m_11, m.m_12, m.m_13],
                   [m.m_21, m.m_22, m.m_23],
                   [m.m_31, m.m_32, m.m_33]] if m is not None else ident)
        if o and o[1] is not None:
            lt = o[1]
        else:
            tr = getattr(node, 'translation', None)
            lt = [tr.x, tr.y, tr.z] if tr is not None else [0.0, 0.0, 0.0]
        ls = getattr(node, 'scale', 1.0) or 1.0
        Tw = [R[i][0] * lt[0] * s + R[i][1] * lt[1] * s
              + R[i][2] * lt[2] * s + T[i] for i in range(3)]
        Rw = _mat_mul(R, lr)
        sw = s * ls
        ln = type(node).__name__
        if ln in ('NiTriShape', 'NiTriStrips') \
                and getattr(node, 'data', None) is not None:
            vs = node.data.vertices
            if len(vs):
                lo = [1e30] * 3
                hi = [-1e30] * 3
                for v in vs:
                    q = (v.x * sw, v.y * sw, v.z * sw)
                    for i in range(3):
                        p = (Rw[i][0] * q[0] + Rw[i][1] * q[1]
                             + Rw[i][2] * q[2] + Tw[i])
                        if p < lo[i]:
                            lo[i] = p
                        if p > hi[i]:
                            hi[i] = p
                shapes.append((my_keyed, lo, hi))
        for ch in (getattr(node, 'children', None) or []):
            if ch is not None:
                walk(ch, Rw, Tw, sw, my_keyed)

    for r in data.roots:
        try:
            walk(r, ident, [0.0, 0.0, 0.0], 1.0, False)
        except Exception:
            continue
    if not shapes:
        return None
    pick = [sh for sh in shapes if sh[0]] or shapes
    lo = [min(sh[1][i] for sh in pick) for i in range(3)]
    hi = [max(sh[2][i] for sh in pick) for i in range(3)]
    ex, ey, ez = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    if max(ex, ey) <= 1e-9:
        return None
    # Thin in Z => swings about a HORIZONTAL axis (trapdoor, hatch, grate,
    # display case, manhole cover).  No vertical-axis threshold line exists,
    # so the caller must skip it rather than lay a door quad in a made-up
    # direction.
    if ez < max(ex, ey) * 0.5:
        return None
    axis = 'Y' if ey > ex else 'X'
    return (axis, max(ex, ey),
            0.5 * (lo[0] + hi[0]), 0.5 * (lo[1] + hi[1]), lo[2])


def _worker(args: tuple):
    """Collision only (kept for `python -m asset_convert.collision.collision_extract`)."""
    nif_path, rel_key = args
    try:
        return rel_key, extract_nif_collision(nif_path)
    except Exception:
        return rel_key, None


def _worker_both(args: tuple):
    """(rel_key, bounds, collision) from a SINGLE parse of one NIF.

    The whole point of the merged scan: bounds and collision each cost ~15-30 ms
    of analysis on top of a ~174 ms parse, so parsing once and running both
    nearly halves the combined phase.

    NOTE: the door axis/center cache is NOT produced here — the scan reads
    CONVERTED meshes, and door geometry must come from the ORIGINAL NIF's
    Close-sequence pose (see door_closed_geometry).  scan_door_axes builds it
    from the export meshes.
    """
    nif_path, rel_key = args
    try:
        data = read_nif_data(nif_path)
    except Exception:
        return rel_key, None, None
    try:
        bounds = bounds_from_data(data)
    except Exception:
        bounds = None
    if bounds is not None:
        try:
            phys = physics_flags_from_data(data)
        except Exception:
            phys = 0
        if phys:
            bounds = bounds + (phys,)
    try:
        col = collision_from_data(data)
    except Exception:
        col = None
    return rel_key, bounds, col


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

#: Bumped when extraction or mesh conversion changes walkable/blocking output.
COLLISION_SCHEMA_VERSION = 5

#: Cache format id; its trailing digits carry COLLISION_SCHEMA_VERSION.
_MAGIC = b'TESCOL08'
_COLLISION: Dict[str, dict] = {}
# path_key -> short collision digest, memoised by collision_digest().  Cleared
# with _COLLISION so a reload cannot serve digests for the previous cache.
_DIGESTS: Dict[str, str] = {}


def collision_cache_is_current(collision_cache: str) -> bool:
    """True if the cache exists, carries the current magic AND decodes.

    Gates the rescan instead of `os.path.exists`: a cache built before a change
    to what counts as walkable, or one whose entry table cannot be read, is
    regenerated rather than trusted.
    See: docs/commentary/tes5_import_pipeline.md#phase-0-stale-bounds-cache
    """
    try:
        with open(collision_cache, 'rb') as fh:
            return _entry_table_is_intact(zlib.decompress(fh.read()))
    except (OSError, zlib.error):
        return False


def _entry_table_is_intact(data: bytes) -> bool:
    """True if *data* has the current magic and entries consuming it exactly.

    Reads only the lengths, so no float data is decoded.  An EMPTY table is
    never current: it parses perfectly, so a run that scanned the wrong mesh
    dir pins itself as fresh and the rescan never fires again.
    See: docs/commentary/tes5_import_pipeline.md#phase-0-stale-bounds-cache
    """
    if data[:8] != _MAGIC:
        return False
    try:
        (count,) = struct.unpack_from('<I', data, 8)
        if not count:
            return False
        off = 12
        for _ in range(count):
            (klen,) = struct.unpack_from('<H', data, off)
            off += 2 + klen
            nw, nb = struct.unpack_from('<II', data, off)
            off += 8 + (nw + nb) * 9 * 4
    except struct.error:
        return False
    return off == len(data)


def _serialize(results: Dict[str, dict]) -> bytes:
    """Pack every mesh's triangle soups into the compressed cache blob."""
    buf = bytearray()
    buf += _MAGIC
    buf += struct.pack('<I', len(results))
    for key, ent in results.items():
        kb = key.encode('utf-8')
        w, b = ent['w'], ent['b']
        buf += struct.pack('<H', len(kb))
        buf += kb
        buf += struct.pack('<II', len(w) // 9, len(b) // 9)
        if w:
            buf += struct.pack('<%df' % len(w), *w)
        if b:
            buf += struct.pack('<%df' % len(b), *b)
    return zlib.compress(bytes(buf), 6)


def _deserialize(raw: bytes) -> Dict[str, dict]:
    """Decode the cache into per-mesh float32 numpy arrays.

    Storing the triangle soups as Python float lists cost ~530 MB in memory for
    Oblivion.esm; at 15+ navmesh worker processes that is ~16 GB of cache copies
    alone and OOM-kills the pool.  float32 numpy arrays are ~8x smaller (~66 MB)
    and are exactly what world.gather_cell_geometry consumes, so this also skips
    a per-cell list->array conversion.  We copy out of the decompressed buffer
    (not frombuffer) so the multi-hundred-MB `data` bytes can be freed here.
    """
    import numpy as np
    data = zlib.decompress(raw)
    if data[:8] != _MAGIC:
        raise ValueError('bad collision cache magic')
    off = 8
    (count,) = struct.unpack_from('<I', data, off)
    off += 4
    out: Dict[str, dict] = {}
    for _ in range(count):
        (klen,) = struct.unpack_from('<H', data, off)
        off += 2
        key = data[off:off + klen].decode('utf-8')
        off += klen
        nw, nb = struct.unpack_from('<II', data, off)
        off += 8
        w = np.frombuffer(data, dtype='<f4', count=nw * 9,
                          offset=off).astype(np.float32).copy()
        off += nw * 9 * 4
        b = np.frombuffer(data, dtype='<f4', count=nb * 9,
                          offset=off).astype(np.float32).copy()
        off += nb * 9 * 4
        out[key] = {'w': w, 'b': b}
    return out


def _list_nifs(mesh_dir_norm: str):
    """[(abs_path, rel_key)] for every .nif under *mesh_dir_norm*.

    rel_key is lowercase with forward slashes, relative to the mesh root — the
    same key the import pipeline builds via _navm_model_key().
    """
    out = []
    for root, _dirs, files in os.walk(mesh_dir_norm):
        for fname in files:
            if fname.lower().endswith('.nif'):
                abs_path = os.path.join(root, fname)
                rel = os.path.relpath(abs_path, mesh_dir_norm)
                out.append((abs_path, rel.lower().replace('\\', '/')))
    return out


def scan_mesh_data(mesh_dir: str, collision_cache: str, bounds_cache: str,
                   workers: int = None, seed_bounds=None, seed_collision=None):
    """Scan the CONVERTED mesh dir ONCE, writing both caches.

    Bounds and collision share one NIF parse, which dominates both analyses.
    `seed_bounds`/`seed_collision` carry entries the producing stage already
    computed from the graph it was writing; only meshes missing from them are
    parsed here.

    The two caches stay SEPARATE files in their existing formats, so every
    consumer (load_collision / mesh_bounds.load_mesh_bounds) is unchanged.

    Returns (n_collision, n_bounds).
    See: docs/commentary/tes5_import_pipeline.md#producer-emitted-mesh-entries
    """
    mesh_dir_norm = os.path.normpath(mesh_dir)
    if not os.path.isdir(mesh_dir_norm):
        print(f"  Mesh scan: mesh dir not found ({mesh_dir}), skipping")
        return 0, 0

    nif_files = _list_nifs(mesh_dir_norm)
    if not nif_files:
        print(f"  Mesh scan: no .nif files found in {mesh_dir}")
        return 0, 0

    col_results: Dict[str, dict] = dict(seed_collision or {})
    bnd_results: Dict[str, tuple] = dict(seed_bounds or {})
    known = set(bnd_results) | set(col_results)
    todo = [job for job in nif_files if job[1] not in known]

    n = len(todo)
    if workers is None:
        workers = worker_count()
    if known:
        print(f"  Mesh scan: {len(nif_files) - n} of {len(nif_files)} NIFs "
              f"came from the mesh stage")
    if n:
        print(f"  Scanning {n} NIFs for bounds + collision ({workers} "
              f"workers)...")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        done = 0
        for rel_key, bounds, col in ex.map(_worker_both, todo,
                                           chunksize=16):
            if col is not None:
                col_results[rel_key] = col
            if bounds is not None:
                bnd_results[rel_key] = bounds
            done += 1
            if done % 1000 == 0:
                print(f"    {done}/{n} processed...")

    _write_mesh_caches(col_results, bnd_results, collision_cache,
                       bounds_cache, len(nif_files))
    return len(col_results), len(bnd_results)


def _write_mesh_caches(col_results, bnd_results, collision_cache,
                       bounds_cache, total) -> None:
    """Report the tallies and write both caches in their shipped formats.

    The bounds payload is stamped so a later run can tell a complete cache
    from one written before an entry field existed; the key is not a mesh
    path, so readers that iterate entries must skip it.
    See: docs/commentary/tes5_import_pipeline.md#phase-0-stale-bounds-cache
    """
    tw = sum(len(e['w']) // 9 for e in col_results.values())
    tb = sum(len(e['b']) // 9 for e in col_results.values())
    print(f"  Collision: {len(col_results)} / {total} NIFs "
          f"({tw} walkable, {tb} blocking tris)")
    print(f"  Mesh bounds: {len(bnd_results)} / {total} NIFs computed")

    os.makedirs(os.path.dirname(os.path.abspath(collision_cache)),
                exist_ok=True)
    with open(collision_cache, 'wb') as fh:
        fh.write(_serialize(col_results))

    os.makedirs(os.path.dirname(os.path.abspath(bounds_cache)), exist_ok=True)
    payload = {k: list(v) for k, v in bnd_results.items()}
    payload[_BOUNDS_SCHEMA_KEY] = [BOUNDS_SCHEMA_VERSION]
    with open(bounds_cache, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh)


def _door_model_paths(door_txt: str) -> set:
    """Normalized model paths of every DOOR base in an export's DOOR.txt."""
    models = set()
    try:
        with open(door_txt, encoding='utf-8', errors='replace') as fh:
            txt = fh.read()
    except OSError:
        return models
    for line in txt.splitlines():
        if not line.startswith('Model.MODL='):
            continue
        path = line.split('=', 1)[1].strip().lower().replace('\\', '/')
        while '//' in path:
            path = path.replace('//', '/')
        if path:
            models.add(path)
    return models


def _closed_door_entry(args):
    """(cache key, door_closed_geometry result or None) for one ORIGINAL NIF."""
    path, key = args
    try:
        return key, door_closed_geometry(read_nif_data(path))
    except Exception:
        return key, None


def _door_axis_jobs(export_plugin_dir: str) -> list:
    """(original NIF path, '<ns>/<model>') for every DOOR base that exists.

    DOOR.txt is a RECORD file while `meshes` is an ASSET tree; they share a
    folder only in a flat export, so the mesh root resolves via assets_for.
    See: docs/commentary/tes5_import_pipeline.md#phase-0-stale-bounds-cache
    """
    door_txt = os.path.join(export_plugin_dir, 'DOOR.txt')
    root = str(assets_for(export_plugin_dir) / 'meshes')
    if not os.path.exists(door_txt) or not os.path.isdir(root):
        return []
    jobs = []
    for mdl in sorted(_door_model_paths(door_txt)):
        path = os.path.join(root, *mdl.split('/'))
        if os.path.exists(path):
            jobs.append((path, current_namespace() + '/' + mdl))
    return jobs


def scan_door_axes(export_plugin_dir: str, dest: str,
                   workers: int = None) -> int:
    """Write door_panel_axis_cache.json; return how many models it classified.

    Reads the ORIGINAL (export) door meshes at their CLOSED pose, which is the
    only source that recovers a door stored mid-open, so this cannot share
    `scan_mesh_data`'s parse of the CONVERTED tree.  The navmesh sizes, orients
    and positions every Door Triangle from this file, so a missing one silently
    falls back to a constant half-width and moves the geometry.

    See: docs/commentary/tes5_import_navmesh.md#door-base-line-is-local-y
    """
    jobs = _door_axis_jobs(export_plugin_dir)
    if not jobs:
        return 0
    out = {}
    with ProcessPoolExecutor(max_workers=workers or worker_count()) as ex:
        for key, res in ex.map(_closed_door_entry, jobs, chunksize=8):
            if res is not None:
                axis, width, cx, cy, zmin = res
                out[key] = [axis, round(width, 2), round(cx, 2),
                            round(cy, 2), round(zmin, 2)]
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    n = len(out)
    out[_BOUNDS_SCHEMA_KEY] = [DOOR_AXIS_SCHEMA_VERSION]
    with open(dest, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=0, sort_keys=True)
    print(f"  Door axes: {n} / {len(jobs)} door meshes classified")
    return n


def scan_collision(mesh_dir: str, cache_path: str, workers: int = None) -> int:
    """Collision-only scan (CLI entry point; prefer scan_mesh_data)."""
    mesh_dir_norm = os.path.normpath(mesh_dir)
    if not os.path.isdir(mesh_dir_norm):
        print(f"  Collision: mesh dir not found ({mesh_dir}), skipping")
        return 0

    nif_files = _list_nifs(mesh_dir_norm)
    if not nif_files:
        print(f"  Collision: no .nif files found in {mesh_dir}")
        return 0

    n = len(nif_files)
    if workers is None:
        workers = worker_count()
    print(f"  Scanning {n} NIFs for collision ({workers} workers)...")

    results: Dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        done = 0
        for rel_key, ent in ex.map(_worker, nif_files, chunksize=16):
            if ent is not None:
                results[rel_key] = ent
            done += 1
            if done % 1000 == 0:
                print(f"    {done}/{n} processed...")

    tw = sum(len(e['w']) // 9 for e in results.values())
    tb = sum(len(e['b']) // 9 for e in results.values())
    print(f"  Collision: {len(results)} / {n} NIFs "
          f"({tw} walkable, {tb} blocking tris)")

    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, 'wb') as fh:
        fh.write(_serialize(results))
    return len(results)


def load_collision(cache_path, quiet: bool = False) -> int:
    """Load one or more collision caches into this process's module cache.

    `cache_path` is a path or an iterable of them, MASTERS FIRST: a plugin's
    own mesh wins over the master's entry for the same path key.  A child
    plugin caches only the meshes it ships, so loading its cache alone leaves
    every master-owned static with no collision and nothing carves.  The
    digest memo is dropped, belonging to the cache just replaced.

    See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
    """
    global _COLLISION, _DIGESTS
    paths = [cache_path] if isinstance(cache_path, str) else list(cache_path)
    merged, loaded = {}, 0
    for path in paths:
        if not os.path.exists(path):
            if not quiet:
                print(f"  Collision: cache not found ({path})")
            continue
        try:
            with open(path, 'rb') as fh:
                merged.update(_deserialize(fh.read()))
            loaded += 1
        except (OSError, ValueError, zlib.error, struct.error) as exc:
            if not quiet:
                print(f"  Collision: could not load cache ({exc})")
    if not loaded:
        return 0
    _COLLISION, _DIGESTS = merged, {}
    if not quiet:
        print(f"  Collision: loaded {len(_COLLISION)} entries "
              f"from {loaded} cache(s)")
    return len(_COLLISION)


def get_collision(path_key: str) -> Optional[dict]:
    """Return {'w': [...9N floats], 'b': [...]} in game units for a key, or None."""
    return _COLLISION.get(path_key)


def collision_digest(path_key: str) -> str:
    """Short stable digest of ONE mesh's collision soup ('' if absent).

    The navmesh geometry cache keys each cell on the collision of the meshes
    THAT CELL places (pgrd_to_navm._geom_hash).  Hashing the whole
    collision_cache.bin instead would be far cheaper here, but it makes every
    entry share one fate: replacing a single mesh would invalidate all ~8,200
    Oblivion entries and force a full regeneration.  Per-mesh digests keep the
    blast radius to the cells that actually place the changed mesh.

    Digests are computed once on load and memoised, so a cell pays a dict
    lookup per distinct model, not a re-hash.
    """
    if path_key in _DIGESTS:
        return _DIGESTS[path_key]
    ent = _COLLISION.get(path_key)
    if ent is None:
        digest = ''
    else:
        h = hashlib.sha1()
        # float32 bytes, the same representation the cache FILE stores, so the
        # digest is stable across machines and across a rebuild that produced
        # identical geometry.  load_collision yields numpy arrays, but the
        # scanners (scan_mesh_data / scan_collision) build plain float lists
        # before serialising, and either shape can reach here -- coerce so the
        # two produce the SAME digest rather than crashing on .tobytes().
        import numpy as np
        for part in ('w', 'b'):
            arr = ent[part]
            if not isinstance(arr, np.ndarray):
                arr = np.asarray(arr, dtype=np.float32)
            h.update(np.ascontiguousarray(arr, dtype=np.float32).tobytes())
            h.update(b'|')
        digest = h.hexdigest()[:16]
    _DIGESTS[path_key] = digest
    return digest


def collision_content_hash() -> str:
    """Digest over EVERY loaded mesh's collision, independent of file layout.

    Used to certify a published navmesh cache: two machines that converted the
    same meshes get the same value here even though their collision_cache.bin
    files differ in mtime (and may differ in key order or zlib output).  Never
    ship collision_cache.bin itself — it is keyed-by-name Bethesda collision
    geometry.  This hash proves a local cache matches without carrying any of
    it.
    """
    h = hashlib.sha1()
    for key in sorted(_COLLISION):
        h.update(key.encode('utf-8'))
        h.update(collision_digest(key).encode('ascii'))
    return h.hexdigest()


def collision_loaded() -> int:
    return len(_COLLISION)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Extract collision soups from converted NIFs')
    ap.add_argument('mesh_dir', nargs='?', help='converted mesh root')
    ap.add_argument('-o', '--out', default='collision_cache.bin')
    ap.add_argument('-j', '--workers', type=int, default=None)
    ap.add_argument('--probe', help='extract one NIF and print a summary')
    a = ap.parse_args()
    if a.probe:
        ent = extract_nif_collision(a.probe)
        if not ent:
            print('no collision')
        else:
            print(f"walkable {len(ent['w']) // 9}  blocking {len(ent['b']) // 9}")
    elif a.mesh_dir:
        scan_collision(a.mesh_dir, a.out, a.workers)
    else:
        ap.error('mesh_dir or --probe required')
