import math
import sys
from collections import deque
from pathlib import Path

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()

from pyffi.formats.nif import NifFormat

from asset_convert.collision.cms_builder import build_cms_collision
from asset_convert.collision.collision_falloutnv import fo3_layer, is_fallout_source
from asset_convert.collision.collision_hulls import decompose_clutter_hull
from asset_convert.collision.collision_material import (
    OB_TO_SK_MATERIAL,
    convert_materials,
    get_havok_material,
    set_havok_material,
)
from asset_convert.collision.collision_winding import (
    INVERTED_FLOOR_FLIPS,
    repair_inverted_floors,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HAVOK_SCALE = 0.1
#: Skyrim: one Havok unit is this many game units.
GAME_UNITS_PER_HAVOK = 69.9904
# Oblivion->game unit scale, used to divide authored ragdoll MASS on creature
# blend bodies.  Must stay equal to hkx_ragdoll._OB_MASS_DIV: skeleton.nif and
# skeleton.hkx describe the SAME bodies and vanilla ships identical masses in
# both (dog total 74.00 either side).  See _convert_blend_collision.
_OB_MASS_DIV = 7.0
#: Vanilla's velocity ceiling for every engine-driven keyframed door and gate.
_KEYFRAMED_VELOCITY_CAP = 1000002.0
NIF_FLAGS = 14  # Standard Skyrim NiAVObject flags (SelectiveUpdate bits 1-3)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Triangle extraction from NiTriStripsData
# ---------------------------------------------------------------------------

def _triangulate_strips(strips_data):
    """Convert NiTriStripsData strip indices to a list of (a, b, c) triangles.

    Triangles indexing past the vertex array are dropped: some third-party
    meshes carry strip points beyond num_vertices, which would otherwise
    raise IndexError in every consumer.
    """
    triangles = []
    nv = len(strips_data.vertices)
    for strip in strips_data.points:
        pts = list(strip)
        flip = False
        for i in range(2, len(pts)):
            a, b, c = pts[i-2], pts[i-1], pts[i]
            if a != b and b != c and c != a and max(a, b, c) < nv:
                if not flip:
                    triangles.append((a, b, c))
                else:
                    triangles.append((a, c, b))
            flip = not flip
    return triangles


def _find_normal(verts, a, b, c):
    """Return normalized face normal for triangle (a,b,c) in a vertex list."""
    va, vb, vc = verts[a], verts[b], verts[c]
    ux, uy, uz = vb[0]-va[0], vb[1]-va[1], vb[2]-va[2]
    vx, vy, vz = vc[0]-va[0], vc[1]-va[1], vc[2]-va[2]
    nx = uy*vz - uz*vy
    ny = uz*vx - ux*vz
    nz = ux*vy - uy*vx
    mag = math.sqrt(nx*nx + ny*ny + nz*nz)
    if mag > 0:
        nx /= mag; ny /= mag; nz /= mag
    return nx, ny, nz


# ---------------------------------------------------------------------------

def _set_packed_sub_shape(packed, num_vertices, sk_material, layer=1):
    """Write the single covering sub-shape onto a bhkPackedNiTriStripsShape.

    The sub-shape list MOVED between the two formats (nif.xml):

        bhkPackedNiTriStripsShape.Num Sub Shapes   until="20.0.0.5"  (Oblivion)
        hkPackedNiTriStripsData.Num Sub Shapes     since="20.2.0.7"  (Skyrim)

    So at Skyrim's 20.2.0.7 the count written on the *shape* is not even
    serialised, and the engine reads the one on the *data* block.  Writing
    only the Oblivion-side field left every fallback shape claiming zero
    sub-shapes while carrying real geometry; Skyrim's reader sizes its
    sub-part allocation from that count, then memcpys the vertex/triangle
    payload into the undersized buffer — an access violation inside
    VCRUNTIME140 on load (crash-2026-07-27/28, inucaveuplant00.nif).

    Both fields are set so the shape is correct at either version.
    """
    packed.num_sub_shapes = 1
    packed.sub_shapes.update_size()
    packed.sub_shapes[0].layer = layer
    packed.sub_shapes[0].num_vertices = num_vertices
    set_havok_material(packed.sub_shapes[0].material, sk_material)

    data = packed.data
    if data is not None and hasattr(data, 'num_sub_shapes'):
        data.num_sub_shapes = 1
        data.sub_shapes.update_size()
        data.sub_shapes[0].layer = layer
        data.sub_shapes[0].num_vertices = num_vertices
        set_havok_material(data.sub_shapes[0].material, sk_material)


def _ni_strips_to_packed(bhk_strips):
    """Convert bhkNiTriStripsShape → bhkPackedNiTriStripsShape.

    Combines ALL NiTriStripsData blocks (Oblivion often has multiple per shape)
    and scales vertices by 1/7 (Oblivion stores them at 7× Havok unit scale).
    Returns a bhkPackedNiTriStripsShape, or None on failure.
    """
    try:
        strips_list = list(bhk_strips.strips_data)
        if not strips_list:
            return None

        # Combine vertices and triangles from ALL NiTriStripsData blocks.
        # Oblivion bhkNiTriStripsShape can have multiple data blocks (each is a
        # separate collision piece), but bhkPackedNiTriStripsShape stores them
        # merged with a single sub-shape covering all vertices.
        all_verts = []
        all_triangles = []
        for sd in strips_list:
            offset = len(all_verts)
            block_verts = [(v.x / 7.0, v.y / 7.0, v.z / 7.0) for v in sd.vertices]
            all_verts.extend(block_verts)
            block_tris = _triangulate_strips(sd)
            all_triangles.extend(
                (a + offset, b + offset, c + offset) for a, b, c in block_tris
            )

        if not all_triangles:
            return None

        hkdata = NifFormat.hkPackedNiTriStripsData()
        hkdata.num_vertices = len(all_verts)
        hkdata.vertices.update_size()
        for i, (x, y, z) in enumerate(all_verts):
            hkdata.vertices[i].x = x
            hkdata.vertices[i].y = y
            hkdata.vertices[i].z = z

        hkdata.num_triangles = len(all_triangles)
        hkdata.triangles.update_size()
        for i, (a, b, c) in enumerate(all_triangles):
            hkdata.triangles[i].triangle.v_1 = a
            hkdata.triangles[i].triangle.v_2 = b
            hkdata.triangles[i].triangle.v_3 = c
            hkdata.triangles[i].welding_info = 0
            nx, ny, nz = _find_normal(all_verts, a, b, c)
            hkdata.triangles[i].normal.x = nx
            hkdata.triangles[i].normal.y = ny
            hkdata.triangles[i].normal.z = nz

        packed = NifFormat.bhkPackedNiTriStripsShape()
        packed.data = hkdata
        _set_packed_sub_shape(packed, len(all_verts),
                              get_havok_material(bhk_strips.material))
        packed.scale.x = 1.0
        packed.scale.y = 1.0
        packed.scale.z = 1.0
        packed.unknown_float_1 = 0.1
        packed.unknown_float_3 = 0.1
        return packed
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Mesh collision rebuild (strips/packed → vanilla-style MOPP + CMS)
# ---------------------------------------------------------------------------

def shape_tri_groups(shape):
    """Triangle-count of each independent geometry group inside `shape`.

    A mesh shape can hold several self-contained pieces — one NiTriStripsData
    block per piece, or one packed sub-shape per piece — that happen to touch
    in space without being one surface.  _shape_tri_soup concatenates them in
    this order, so these counts partition its output.

    repair_inverted_floors needs the partition: it discovers surfaces by
    welding coincident vertices, and welding ACROSS a group boundary fuses
    two independent pieces into one component.  A single orientation is then
    forced on both, which is how the sign step inverted half of vanilla's
    SI bridges (dementiabridge01: 2 sub-shapes, 130/258 triangles flipped).
    """
    if isinstance(shape, NifFormat.bhkNiTriStripsShape):
        return [len(list(_triangulate_strips(sd)))
                for sd in shape.strips_data if sd is not None]

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        data = getattr(shape, 'data', None)
        if data is None:
            return []
        subs = getattr(shape, 'sub_shapes', None) or []
        if len(subs) < 2:
            return []
        # Sub-shapes partition the VERTEX array; a triangle belongs to the
        # sub-shape owning its vertices.  Walk the triangles in order and cut
        # whenever the owning sub-shape changes.
        bounds, run = [], 0
        for s in subs:
            run += s.num_vertices
            bounds.append(run)

        def owner(vi):
            for gi, hi in enumerate(bounds):
                if vi < hi:
                    return gi
            return len(bounds) - 1

        groups, cur, prev = [], 0, None
        for t in data.triangles:
            a, b, c = t.triangle.v_1, t.triangle.v_2, t.triangle.v_3
            if a == b or b == c or a == c:
                continue
            g = owner(a)
            if prev is not None and g != prev:
                groups.append(cur)
                cur = 0
            prev = g
            cur += 1
        if cur:
            groups.append(cur)
        return groups

    return []


def _shape_tri_soup(shape):
    """Extract (triangles_hu, sk_material) from a mesh collision shape.

    bhkNiTriStripsShape data is at game-unit scale (÷7 → Oblivion havok,
    ×_HAVOK_SCALE → Skyrim havok).  hkPackedNiTriStripsData is at 1/7
    game-unit scale already (×_HAVOK_SCALE only).  Returns None for
    non-mesh shapes (caller uses the primitive conversion path).
    """
    if isinstance(shape, NifFormat.bhkNiTriStripsShape):
        scale = _HAVOK_SCALE / 7.0
        tris = []
        for sd in shape.strips_data:
            if sd is None:
                continue
            verts = [(v.x * scale, v.y * scale, v.z * scale)
                     for v in sd.vertices]
            tris.extend((verts[a], verts[b], verts[c])
                        for a, b, c in _triangulate_strips(sd))
        if not tris:
            return None
        material = get_havok_material(shape.material)
        if 0 <= material <= 31:
            material = OB_TO_SK_MATERIAL.get(material, 3741512247)
        return tris, material

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        data = getattr(shape, 'data', None)
        if data is None or data.num_triangles == 0:
            return None
        verts = [(v.x * _HAVOK_SCALE, v.y * _HAVOK_SCALE, v.z * _HAVOK_SCALE)
                 for v in data.vertices]
        tris = []
        for t in data.triangles:
            a, b, c = t.triangle.v_1, t.triangle.v_2, t.triangle.v_3
            if a == b or b == c or a == c:
                continue
            tris.append((verts[a], verts[b], verts[c]))
        if not tris:
            return None
        material = 3741512247  # stone default
        if shape.num_sub_shapes > 0:
            material = get_havok_material(shape.sub_shapes[0].material)
            if 0 <= material <= 31:
                material = OB_TO_SK_MATERIAL.get(material, 3741512247)
        return tris, material

    return None


# Minimum length of an averaged per-vertex normal for it to describe THIS
# triangle.  bhkNiTriStripsShape stores normals per VERTEX, and a vertex on a
# smoothed edge carries the average of every face meeting there -- averaging
# three of those yields a short vector pointing at none of them
# (mageguilddesk01's desk edge: |n| = 0.31, and trusting it rewound 9 correct
# faces).  Near unit length is the proof that all three vertices agreed, i.e.
# the face is flat-shaded and its normal really is the face normal.
_AUTHORED_NORMAL_MIN_LEN = 0.95


def _shape_tri_normals(shape):
    """Authored per-triangle normals for `shape`, parallel to _shape_tri_soup.

    A collision triangle records the direction it is supposed to face
    INDEPENDENTLY of the winding that actually produces that facing:

        hkPackedNiTriStripsData.triangles[i].normal   -- one per triangle
        NiTriStripsData.normals[]                     -- one per vertex

    When an exporter flattens triangle strips and drops the alternating
    parity, the winding is reversed but this stored normal is carried through
    untouched -- so the two disagree, and the disagreement is the AUTHORED
    record of exactly which triangles were damaged.  No adjacency walk, no
    thresholds, no oracle mesh: the file says which faces are wrong.

    Entries are None where nothing can be trusted (no normals block, a
    zero-length normal, or a per-vertex average that did not survive the
    length test), and the caller leaves those triangles alone.  The order and
    the degenerate-triangle filtering MUST match _shape_tri_soup exactly or
    normals bind to the wrong faces; the two are edited together.

    Returns None when the shape carries no usable normals at all.
    """
    if isinstance(shape, NifFormat.bhkNiTriStripsShape):
        out = []
        any_real = False
        for sd in shape.strips_data:
            if sd is None:
                continue
            tri_iter = list(_triangulate_strips(sd))
            if not getattr(sd, 'has_normals', False) or not sd.normals:
                out.extend([None] * len(tri_iter))
                continue
            norms = [(n.x, n.y, n.z) for n in sd.normals]
            for a, b, c in tri_iter:
                try:
                    na, nb, nc = norms[a], norms[b], norms[c]
                except IndexError:
                    out.append(None)
                    continue
                avg = ((na[0] + nb[0] + nc[0]) / 3.0,
                       (na[1] + nb[1] + nc[1]) / 3.0,
                       (na[2] + nb[2] + nc[2]) / 3.0)
                if (math.sqrt(avg[0]**2 + avg[1]**2 + avg[2]**2)
                        < _AUTHORED_NORMAL_MIN_LEN):
                    out.append(None)      # smoothed edge -- not this face
                    continue
                out.append(avg)
                any_real = True
        return out if any_real else None

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        data = getattr(shape, 'data', None)
        if data is None or data.num_triangles == 0:
            return None
        out = []
        any_real = False
        for t in data.triangles:
            a, b, c = t.triangle.v_1, t.triangle.v_2, t.triangle.v_3
            if a == b or b == c or a == c:
                continue              # dropped by _shape_tri_soup too
            n = (t.normal.x, t.normal.y, t.normal.z)
            if n[0] == 0.0 and n[1] == 0.0 and n[2] == 0.0:
                out.append(None)
                continue
            out.append(n)
            any_real = True
        return out if any_real else None

    return None


# Minimum vertex count for the bulk path.  MEASURED, not assumed: there is no
# crossover -- numpy wins at every size tried, including n=4 (16 us vs 135 us,
# 8x), because the scalar loop pays three Python calls and several Vector3
# allocations PER VERTEX while the array round trip is paid once.  Kept at 2
# only so a degenerate 0/1-vertex shape takes the trivial path.
_VECTOR_XFORM_MIN = 2

_NUMPY = None


def _numpy():
    """numpy module, or None.  Imported lazily and cached (incl. failure).

    TESCONV_NO_FAST_VERT_XFORM=1 forces the scalar path, for A/B measurement.
    """
    global _NUMPY
    if _NUMPY is None:
        import os
        if os.environ.get('TESCONV_NO_FAST_VERT_XFORM'):
            _NUMPY = False
            return None
        try:
            import numpy
        except ImportError:
            _NUMPY = False
        else:
            _NUMPY = numpy
    return _NUMPY or None


def _transform_verts(vertices, m, scale):
    """[(x, y, z), ...] for `vertices` through Matrix44 `m`, times `scale`.

    Reproduces PyFFI's ``v * m`` exactly, in bulk.  That operator is
    ``v * m.get_matrix_33() + m.get_translation()`` -- a row-vector affine
    transform -- but PyFFI evaluates it one vertex at a time through
    ``Vector3.__mul__``, which recurses and allocates several Vector3 objects
    per vertex.  Measured on a 20-mesh sample it was 3.42 s of 18.27 s
    (18.7%) in 24,887 calls, ALL of them from _visual_tri_soup: the single
    largest item left in mesh conversion after Patch 12.

    The result is BIT-EXACT with that loop -- verified 52,159 of 52,159
    vertices across 168 real shapes, worst diff 0.  That is the contract, not
    a nicety: this soup is the oracle for repair_inverted_floors' nearest-face
    search, which compares against a trust radius, so a 1e-7 drift can flip a
    DIFFERENT triangle and change the collision we ship.

    Falls back to the scalar loop if numpy is unavailable or the vertex list
    is too short to be worth the array round trip.
    """
    n = len(vertices)
    if n >= _VECTOR_XFORM_MIN:
        np = _numpy()
        if np is not None:
            flat = np.fromiter(
                (c for vert in vertices for c in (vert.x, vert.y, vert.z)),
                dtype=np.float64, count=n * 3)
            vx = flat[0::3]
            vy = flat[1::3]
            vz = flat[2::3]
            # float64, NOT float32: PyFFI's Float holds a plain Python float,
            # so ``v * m`` is evaluated in double precision -- only the on-disk
            # representation is 32-bit.  Computing this in float32 left just
            # 0.47% of 52,159 sample vertices bit-exact (worst 9.5e-07).
            #
            # Row-vector convention, matching Vector3.__mul__(Matrix33):
            #   x' = v.x*m_11 + v.y*m_21 + v.z*m_31   (+ m_41 from translation)
            #
            # Written as explicit per-component mul/add in PyFFI's own
            # evaluation order rather than a matmul, so every intermediate
            # rounds exactly where the scalar loop rounds it.
            out_v = []
            for (c1, c2, c3, t) in ((m.m_11, m.m_21, m.m_31, m.m_41),
                                    (m.m_12, m.m_22, m.m_32, m.m_42),
                                    (m.m_13, m.m_23, m.m_33, m.m_43)):
                acc = vx * c1
                acc += vy * c2
                acc += vz * c3
                acc += t
                acc *= scale
                out_v.append(acc.tolist())
            return list(zip(*out_v))
    verts = []
    for vert in vertices:
        w = vert * m
        verts.append((w.x * scale, w.y * scale, w.z * scale))
    return verts


def _visual_tri_soup(root, max_tris=20000):
    """Render-mesh triangles under `root`, in Havok units to match collision.

    Used as the orientation oracle by repair_inverted_floors: the artist's
    visual winding is correct by construction, so a collision face pointing
    opposite a co-located visual face is reversed.  Returns [] when the node
    has no render geometry (collision-only markers), which makes the repair
    fall back to Signal A alone.

    The scale MUST match _shape_tri_soup's output or the oracle silently does
    nothing: render vertices are in game units, while the triangle soup is in
    Skyrim havok units built from data already at 1/7 game-unit scale — hence
    _HAVOK_SCALE / 7, not _HAVOK_SCALE.  (Getting this wrong put the visual
    geometry 7× too large, so no face ever fell inside the trust radius and
    every mesh came through unrepaired.)

    Capped at max_tris because the oracle is a nearest-face search: highly
    tessellated meshes would dominate conversion time for no extra accuracy.
    """
    scale = _HAVOK_SCALE / 7.0
    if root is None:
        return []
    out = []
    try:
        for blk in root.tree():
            if not isinstance(blk, NifFormat.NiTriBasedGeom):
                continue
            data = blk.data
            if data is None:
                continue
            try:
                m = blk.get_transform(root)
                verts = _transform_verts(data.vertices, m, scale)
                for (a, b, c) in data.get_triangles():
                    if a != b and b != c and a != c:
                        out.append((verts[a], verts[b], verts[c]))
            except Exception:
                continue
            if len(out) > max_tris:
                return []
    except Exception:
        return []
    return out


def _bake_body_transform_into_tris(rb, tris):
    """Fold a bhkRigidBodyT transform into the triangle soup (Skyrim hu).

    Vanilla Skyrim never pairs a transformed rigid body with MOPP/mesh
    collision (0 of 6341 vanilla CMS meshes contain bhkRigidBodyT): the
    engine's CMS/MOPP query path intermittently produces invalid shape keys
    (HK_INVALID_SHAPE_KEY → runaway hit scan → CTD) when one is present —
    every Collision Sentinel CULPRIT was a rotated-root mesh whose wrap
    pass produced bhkRigidBodyT + CMS.  So the body transform is applied to
    the vertices here and the body is demoted to a plain identity
    bhkRigidBody, exactly like vanilla static collision.

    rb.translation must already be in Skyrim havok units (the caller scales
    it before shape conversion).  Returns the transformed triangle list.
    """
    if not isinstance(rb, NifFormat.bhkRigidBodyT):
        return tris
    q = rb.rotation
    R = _m3_from_quat_xyzw(q.x, q.y, q.z, q.w)  # column-vector convention
    t = (rb.translation.x, rb.translation.y, rb.translation.z)

    def xf(v):
        return (
            R[0][0] * v[0] + R[0][1] * v[1] + R[0][2] * v[2] + t[0],
            R[1][0] * v[0] + R[1][1] * v[1] + R[1][2] * v[2] + t[1],
            R[2][0] * v[0] + R[2][1] * v[1] + R[2][2] * v[2] + t[2],
        )

    tris = [(xf(a), xf(b), xf(c)) for a, b, c in tris]
    rb.__class__ = NifFormat.bhkRigidBody
    rb.rotation.x = rb.rotation.y = rb.rotation.z = 0.0
    rb.rotation.w = 1.0
    rb.translation.x = rb.translation.y = rb.translation.z = 0.0
    return tris


# Smallest AABB extent (havok units) a mesh collision hull may have.
#
# Havok's MOPP builder access-violates on hulls a few hundredths of a havok
# unit across; cms_builder now recovers those by building the MOPP in a
# scaled-up frame (exact, see the degenerate-scale retry there), so a small
# hull is NOT by itself a reason to discard collision -- vanilla Oblivion
# clutter genuinely ships them (paintbrush01 = 0.034 hu) and it must stay
# grabbable.
#
# This threshold therefore only catches hulls that are sub-viable in the
# engine regardless of MOPP: 0.01 hu is 0.07 game units, i.e. sub-millimetre,
# far below the smallest vanilla Skyrim hull (0.179 hu) and small enough that
# nothing can ever collide with it.  Morroblivion's inucaveuplant00 (0.0098 hu
# against a 73.5-game-unit visual mesh, ~1000x too small) is the case in
# point: its collision is meaningless, so it is dropped rather than shipped.
_MIN_HULL_EXTENT = 0.01

# Mesh collisions dropped as degenerate (list so process workers can mutate).
_DEGENERATE_HULLS_DROPPED = [0]


def _rebuild_mesh_collision(rb, target_node):
    """Rebuild strips/packed mesh collision as vanilla MOPP+CMS (in place).

    Handles rb.shape being bhkNiTriStripsShape, bhkPackedNiTriStripsShape,
    or a stale Oblivion bhkMoppBvTreeShape wrapping either.  Bakes any
    bhkRigidBodyT transform into the geometry (body becomes plain identity
    bhkRigidBody).

    Returns True when handled, False → caller uses the primitive-shape
    conversion path, or 'drop' → caller removes the collision object
    entirely.  'drop' covers a sub-viable hull (see _MIN_HULL_EXTENT) and a
    MOPP build that failed outright: the old fallback shipped a
    bhkPackedNiTriStripsShape in that case, which Skyrim cannot load at all.
    """
    shape = rb.shape
    inner = shape.shape if isinstance(shape, NifFormat.bhkMoppBvTreeShape) else shape
    soup = _shape_tri_soup(inner)
    if soup is None:
        return False
    tris, sk_material = soup
    groups = shape_tri_groups(inner)
    authored_normals = _shape_tri_normals(inner)
    if authored_normals is not None and len(authored_normals) != len(tris):
        authored_normals = None
    keep = [all(math.isfinite(c) for v in t for c in v) for t in tris]
    if groups and sum(groups) == len(tris) and not all(keep):
        adj, base = [], 0
        for g in groups:
            adj.append(sum(1 for i in range(base, base + g) if keep[i]))
            base += g
        groups = adj
    if authored_normals is not None and not all(keep):
        authored_normals = [n for n, k in zip(authored_normals, keep) if k]
    tris = [t for t, k in zip(tris, keep) if k]
    if not tris:
        return False

    # Degenerate hull: too small for Havok to build a MOPP over, and far too
    # small to collide with anything.  Drop it instead of shipping a shape
    # that crashes the MOPP builder and lands on the packed fallback.
    lo = [min(v[i] for t in tris for v in t) for i in range(3)]
    hi = [max(v[i] for t in tris for v in t) for i in range(3)]
    if max(hi[i] - lo[i] for i in range(3)) < _MIN_HULL_EXTENT:
        _DEGENERATE_HULLS_DROPPED[0] += 1
        return 'drop'

    # The authored normals live in the SHAPE's frame; _bake_body_transform
    # rotates the triangles into the node's.  Rotate the normals by the same
    # matrix first or every comparison is made across two different frames --
    # a 180-degree body would then read every face as reversed.  (The rotation
    # comes from a quaternion, so it never mirrors and never itself changes
    # which way a triangle winds.)
    if authored_normals and isinstance(rb, NifFormat.bhkRigidBodyT):
        q = rb.rotation
        R = _m3_from_quat_xyzw(q.x, q.y, q.z, q.w)
        authored_normals = [
            None if n is None else
            (R[0][0]*n[0] + R[0][1]*n[1] + R[0][2]*n[2],
             R[1][0]*n[0] + R[1][1]*n[1] + R[1][2]*n[2],
             R[2][0]*n[0] + R[2][1]*n[1] + R[2][2]*n[2])
            for n in authored_normals
        ]
    tris = _bake_body_transform_into_tris(rb, tris)
    tris, n_flipped = repair_inverted_floors(
        tris, _visual_tri_soup(target_node), groups, authored_normals)
    if n_flipped:
        INVERTED_FLOOR_FLIPS[0] += n_flipped
    mopp = build_cms_collision(tris, sk_material, NifFormat)
    if mopp is not None:
        mopp.shape.target = target_node
        rb.shape = mopp
        return True
    # MOPP failed.  Shipping _packed_from_tris here was a LOAD CRASH: Skyrim
    # does not support bhkPackedNiTriStripsShape (0 of 17,216 vanilla meshes
    # carry it or hkPackedNiTriStripsData), so the engine mis-sizes its
    # sub-part allocation and memcpys the payload with a garbage 32-bit
    # length -- measured live at 2.03 GB out of a 2.25 GB tbbmalloc block,
    # with 49.2 GB committed and the resulting 0x0000000100000001 fill
    # crashing whatever allocated next.  An unsupported shape is never a
    # safer outcome than no collision, so the collision is dropped instead.
    #
    # In practice MOPP only fails on geometry that is not a surface at all:
    # romanhanginglamp01.nif's collision is 8 vertices with X=Y=0 -- a bare
    # line segment on the Z axis, zero area, which quantises to two distinct
    # points and yields no chunk.  Dropping it costs nothing real.
    _DEGENERATE_HULLS_DROPPED[0] += 1
    return 'drop'


# ---------------------------------------------------------------------------
# Rigid body conversion
# ---------------------------------------------------------------------------

# Oblivion layer → Skyrim layer for the values whose meaning diverges.
# Layers 0-18 are identical in both enums (STATIC..PORTAL) and pass through.
# 19+ diverge: Oblivion 19-31 are stairs/pick layers that moved, 32 is
# OL_OTHER, and 33-56 are per-bone ragdoll layers (OL_HEAD..OL_WING) that
# Oblivion authors also used on constrained world props (cellchain01 anchor
# = OL_L_FOOT).  Skyrim reads those raw values as pick/zone layers with NO
# physical collision (42 = PATHPICK), so touching the object does nothing.
# Vanilla Skyrim constrained props (trapmace01 links) use 10 = PROPS.
_OB_TO_SKY_LAYER: dict[int, int] = {
    19: 31,   # OL_STAIRS          → SKYL_STAIRHELPER
    20: 30,   # OL_CHAR_CONTROLLER → SKYL_CHARCONTROLLER
    21: 34,   # OL_AVOID_BOX       → SKYL_AVOIDBOX
    22: 1,    # OL_UNKNOWN1        → STATIC (no equivalent)
    23: 1,    # OL_UNKNOWN2        → STATIC (no equivalent)
    24: 39,   # OL_CAMERA_PICK     → SKYL_CAMERAPICK
    25: 40,   # OL_ITEM_PICK       → SKYL_ITEMPICK
    26: 41,   # OL_LINE_OF_SIGHT   → SKYL_LINEOFSIGHT
    27: 42,   # OL_PATH_PICK       → SKYL_PATHPICK
    28: 43,   # OL_CUSTOM_PICK_1   → SKYL_CUSTOMPICK1
    29: 44,   # OL_CUSTOM_PICK_2   → SKYL_CUSTOMPICK2
    30: 45,   # OL_SPELL_EXPLOSION → SKYL_SPELLEXPLOSION
    31: 46,   # OL_DROPPING_PICK   → SKYL_DROPPINGPICK
    32: 4,    # OL_OTHER           → SKYL_CLUTTER
}
for _l in range(33, 58):   # OL_HEAD..OL_NULL (ragdoll bone layers)
    _OB_TO_SKY_LAYER[_l] = 10  # SKYL_PROPS (vanilla constrained-prop layer)


def _remap_world_filter(rb):
    """Convert the Oblivion collision filter of a world-object body (in-place).

    Remaps diverging layer values (see _OB_TO_SKY_LAYER) and zeroes the
    flags/part byte and group: bits 0-4 are biped part numbers (meaningless
    off the BIPED layers) and bit 7 is Skyrim's "Linked Group" flag —
    Oblivion chains ship 0x80|part here, vanilla Skyrim constrained props
    ship 0 and rely on the engine's per-reference group assignment.
    Creature-skeleton blend bodies do NOT go through this (their layer is
    forced to 8 BIPED in _convert_blend_collision and part numbers matter).
    """
    for hf in (getattr(rb, 'havok_col_filter', None),
               getattr(rb, 'havok_col_filter_copy', None)):
        if hf is None:
            continue
        hf.layer = (fo3_layer(hf.layer) if is_fallout_source()
                    else _OB_TO_SKY_LAYER.get(int(hf.layer), int(hf.layer)))
        hf.flags_and_part_number = 0
        hf.unknown_short = 0   # Group


def _convert_rigid_body(rb):
    """Set Skyrim-compatible rigid body flags (in-place).

    Field mapping (PyFFI ↔ newer nif.xml):
      unknown_int_1  → bhkWorldObjCInfo.Unused01     (4 bytes binary padding)
      unknown_int_2  → BroadPhaseType(1B) + Unused02 (3B padding)
      unknown_3_ints → bhkWorldObjCInfoProperty       (Data,Size,CapacityFlags)
      unknown_byte   → bhkEntityCInfo.Unused01         (padding byte)
      unknown_2_shorts → bhkRBCInfo2010.Unused01       (padding)
      havok_col_filter_copy → bhkRBCInfo2010.HavokFilter (copy of entity filter)
      unknown_6_shorts[0:2] → bhkRBCInfo2010.Unused02  (padding)
      unknown_6_shorts[2:4] → bhkRBCInfo2010.UnknownInt1 (MUST be 0!)
      unknown_6_shorts[4:6] → bhkRBCInfo2010.CollisionResponse+ProcessContactDelay
    """
    # Zero padding fields that carried Oblivion-specific data.
    # unknown_int_1 = WorldObjCInfo.Unused01 — padding, zero is safest
    rb.unknown_int_1 = 0
    # unknown_int_2 byte 0 = BroadPhaseType, bytes 1-3 = Unused02 padding.
    # BroadPhaseType 1 = BROAD_PHASE_ENTITY (standard for all shapes).
    rb.unknown_int_2 = 1  # BroadPhaseType=1, padding=0
    # unknown_3_ints = WorldObjCInfoProperty (Data=0, Size=0, CapacityFlags=0x80000000)
    rb.unknown_3_ints[0] = 0
    rb.unknown_3_ints[1] = 0
    rb.unknown_3_ints[2] = -2147483648  # 0x80000000

    # unknown_byte: bhkEntityCInfo.Unused01 — the external NIFConverter
    # sets this to 116, vanilla Skyrim NIFs also show 116.
    rb.unknown_byte = 116
    # Gravity/time factors must be 1.0 or Havok ignores the body
    rb.unknown_time_factor_or_gravity_factor_1 = 1.0
    rb.unknown_time_factor_or_gravity_factor_2 = 1.0
    rb.unknown_int_6 = 196608
    rb.unknown_int_7 = 0
    rb.unknown_int_8 = 0
    rb.unknown_int_81 = 0
    rb.unknown_int_91 = 0
    # unknown_2_shorts: static Skyrim values (per external NIFConverter)
    rb.unknown_2_shorts[0] = 29541
    rb.unknown_2_shorts[1] = 23659
    # unknown_6_shorts: Skyrim-specific values required for correct physics.
    # Elements [2] and [3] map to bhkRBCInfo2010.UnknownInt1 — a 32-bit field
    # that Oblivion values corrupt into invalid pointer 0xFFFF1301.
    # Must be zero.  Elements [0:2] and [4:6] are padding/duplicates.
    rb.unknown_6_shorts[0] = 20704
    rb.unknown_6_shorts[1] = 9444
    rb.unknown_6_shorts[2] = 0       # MUST be 0 — Skyrim interprets as pointer
    rb.unknown_6_shorts[3] = 0       # MUST be 0 — Skyrim interprets as pointer
    rb.unknown_6_shorts[4] = 60417
    rb.unknown_6_shorts[5] = 65535


# ---------------------------------------------------------------------------
# Recursive shape conversion
# ---------------------------------------------------------------------------

def _expand_multisphere(ms):
    """Expand bhkMultiSphereShape into per-sphere bhkConvexTransformShape-
    wrapped bhkSphereShapes.

    hkpMultiSphereShape is deprecated in Skyrim's Havok generation: 0 of
    17,216 vanilla meshes ship the block, and files that do (Oblivion's
    alchemy apparatus clutter) crash SSE at cell load with no crash log.
    Vanilla expresses the same thing as ConvexTransform+Sphere children in a
    list shape (e.g. clutter\\kitchen\\woodenladle01.nif).

    Sphere data arrives in Oblivion Havok units — the ×0.1 rescale happens
    here.  Returns a single wrapper for 1 sphere, a bhkListShape for several,
    or None for an empty multisphere.
    """
    mat = get_havok_material(ms.material)
    wrappers = []
    for s in ms.spheres:
        sph = NifFormat.bhkSphereShape()
        set_havok_material(sph.material, mat)
        sph.radius = s.radius * _HAVOK_SCALE

        cts = NifFormat.bhkConvexTransformShape()
        set_havok_material(cts.material, mat)
        cts.unknown_float_1 = sph.radius
        for i in range(8):
            cts.unknown_8_bytes[i] = 0
        t = cts.transform
        # Identity rotation, translation in the 4th column, 4th row all
        # zeros (incl. m_44) — matches vanilla bhkConvexTransformShape.
        t.m_11 = 1.0; t.m_12 = 0.0; t.m_13 = 0.0; t.m_14 = s.center.x * _HAVOK_SCALE
        t.m_21 = 0.0; t.m_22 = 1.0; t.m_23 = 0.0; t.m_24 = s.center.y * _HAVOK_SCALE
        t.m_31 = 0.0; t.m_32 = 0.0; t.m_33 = 1.0; t.m_34 = s.center.z * _HAVOK_SCALE
        t.m_41 = 0.0; t.m_42 = 0.0; t.m_43 = 0.0; t.m_44 = 0.0
        cts.shape = sph
        wrappers.append(cts)

    if not wrappers:
        return None
    if len(wrappers) == 1:
        return wrappers[0]
    ls = NifFormat.bhkListShape()
    set_havok_material(ls.material, mat)
    ls.num_sub_shapes = len(wrappers)
    ls.sub_shapes.update_size()
    for i, w in enumerate(wrappers):
        ls.sub_shapes[i] = w
    ls.num_unknown_ints = len(wrappers)
    ls.unknown_ints.update_size()
    for i in range(len(wrappers)):
        ls.unknown_ints[i] = 0
    return ls


def _convert_shape(shape, root_node):
    """Recursively convert an Oblivion Havok shape to Skyrim format.

    Scales all geometry/dimensions by _HAVOK_SCALE (0.1).  Top-level mesh
    collision (strips/packed/MOPP) is rebuilt in _rebuild_mesh_collision
    before this runs; the mesh branches here only serve nested occurrences
    (e.g. a strips shape inside a bhkListShape) and produce a bare packed
    shape without MOPP.
    Returns the (possibly replaced) shape.
    """
    if shape is None:
        return None

    if isinstance(shape, NifFormat.bhkBoxShape):
        shape.dimensions.x *= _HAVOK_SCALE
        shape.dimensions.y *= _HAVOK_SCALE
        shape.dimensions.z *= _HAVOK_SCALE
        shape.radius *= _HAVOK_SCALE
        shape.minimum_size = min(shape.dimensions.x, shape.dimensions.y, shape.dimensions.z)
        return shape

    if isinstance(shape, NifFormat.bhkSphereShape):
        shape.radius *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkCapsuleShape):
        shape.radius   *= _HAVOK_SCALE
        shape.radius_1 *= _HAVOK_SCALE
        shape.radius_2 *= _HAVOK_SCALE
        shape.first_point.x  *= _HAVOK_SCALE
        shape.first_point.y  *= _HAVOK_SCALE
        shape.first_point.z  *= _HAVOK_SCALE
        shape.second_point.x *= _HAVOK_SCALE
        shape.second_point.y *= _HAVOK_SCALE
        shape.second_point.z *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkMultiSphereShape):
        return _expand_multisphere(shape)

    # bhkConvexSweepShape: early-Oblivion (10.0.1.0) wrapper for a swept
    # convex shape (handscythe01, oar01).  Skyrim never ships it — unwrap to
    # the inner shape, which then converts normally.
    if shape.__class__.__name__ == 'bhkConvexSweepShape':
        return _convert_shape(shape.shape, root_node)

    if isinstance(shape, (NifFormat.bhkConvexTransformShape,
                           NifFormat.bhkTransformShape)):
        shape.transform.m_14 *= _HAVOK_SCALE
        shape.transform.m_24 *= _HAVOK_SCALE
        shape.transform.m_34 *= _HAVOK_SCALE
        shape.shape = _convert_shape(shape.shape, root_node)
        return shape

    if isinstance(shape, NifFormat.bhkConvexVerticesShape):
        for i in range(len(shape.vertices)):
            shape.vertices[i].x *= _HAVOK_SCALE
            shape.vertices[i].y *= _HAVOK_SCALE
            shape.vertices[i].z *= _HAVOK_SCALE
        for i in range(len(shape.normals)):
            shape.normals[i].w *= _HAVOK_SCALE
        shape.radius *= _HAVOK_SCALE
        return shape

    if isinstance(shape, NifFormat.bhkListShape):
        # Convert children; flatten any nested bhkListShape produced by child
        # conversion (e.g. multisphere expansion) — a list shape carries no
        # transform of its own so flattening is semantics-preserving, and
        # vanilla never nests list shapes.
        children = []
        for i in range(len(shape.sub_shapes)):
            c = _convert_shape(shape.sub_shapes[i], root_node)
            if isinstance(c, NifFormat.bhkListShape):
                children.extend(list(c.sub_shapes))
            elif c is not None:
                children.append(c)
        if len(children) != shape.num_sub_shapes:
            shape.num_sub_shapes = len(children)
            shape.sub_shapes.update_size()
            shape.num_unknown_ints = len(children)
            shape.unknown_ints.update_size()
            for i in range(len(children)):
                shape.unknown_ints[i] = 0
        for i, c in enumerate(children):
            shape.sub_shapes[i] = c
        return shape

    if isinstance(shape, NifFormat.bhkNiTriStripsShape):
        # Nested strips (inside a list shape) → triangle soup, then rebuilt as
        # MOPP+CMS by the bhkPackedNiTriStripsShape branch below.
        #
        # Returning the packed shape DIRECTLY here was a load crash: Skyrim
        # never ships bhkPackedNiTriStripsShape/hkPackedNiTriStripsData -- 0 of
        # 17,216 vanilla meshes contain either -- so the engine mis-sizes its
        # sub-part allocation from a shape it does not really support and then
        # memcpys the payload with a garbage 32-bit length.  Measured live:
        # a 2.03 GB memcpy out of a 2.25 GB tbbmalloc block, a 36 GB block in
        # the same allocator list, 49.2 GB committed, and the resulting
        # 0x0000000100000001 fill surfacing as an access violation in whatever
        # allocated next (renderer, audio, tbbmalloc's own getTLS).
        # Reproduced from the mesh the crash dump named:
        # anequina/architecture/huts/domehut01.nif, which was
        # the ONLY mesh of 1,837 in its plugin still carrying the shape.
        #
        # The rebuild succeeds for these meshes -- it was simply never
        # attempted, because this branch returned before reaching it.
        packed = _ni_strips_to_packed(shape)
        if packed is None:
            return shape
        return _convert_shape(packed, root_node)

    if isinstance(shape, NifFormat.bhkMoppBvTreeShape):
        # Never keep the outer bhkMoppBvTreeShape with stale Oblivion MOPP
        # data: Skyrim can't load Oblivion MOPP and will silently drop the
        # collision, while the incompatible blob causes undefined behaviour.
        return _convert_shape(shape.shape, root_node)

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        # Reached only as a bhkListShape child (the standalone case is rebuilt
        # as MOPP+CMS by _rebuild_mesh_collision).  Rebuild it the same way so
        # a list child gets real Skyrim collision; if the MOPP bridge rejects
        # the geometry, at least migrate the sub-shape count to the field
        # Skyrim actually reads (see _set_packed_sub_shape) — leaving the
        # Oblivion-side count made the engine allocate zero sub-parts and then
        # memcpy the payload over unmapped memory (crash on load).
        soup = _shape_tri_soup(shape)
        if soup is not None:
            tris, sk_material = soup
            tris = [t for t in tris
                    if all(math.isfinite(c) for v in t for c in v)]
            if tris:
                lo = [min(v[i] for t in tris for v in t) for i in range(3)]
                hi = [max(v[i] for t in tris for v in t) for i in range(3)]
                if max(hi[i] - lo[i] for i in range(3)) < _MIN_HULL_EXTENT:
                    _DEGENERATE_HULLS_DROPPED[0] += 1
                    return None
                mopp = build_cms_collision(tris, sk_material, NifFormat)
                if mopp is not None:
                    mopp.shape.target = root_node
                    return mopp
        # Could not rebuild.  Both of the old fallbacks here -- returning
        # _packed_from_tris, or repairing the sub-shape count and returning
        # `shape` -- kept a bhkPackedNiTriStripsShape in the output, and
        # Skyrim does not support that shape at all (0 of 17,216 vanilla
        # meshes carry it or hkPackedNiTriStripsData).  The engine then
        # mis-sizes its sub-part allocation and memcpys the payload with a
        # garbage 32-bit length: measured live at 2.03 GB out of a 2.25 GB
        # tbbmalloc block, 49.2 GB committed, and the resulting
        # 0x0000000100000001 fill crashing whatever allocated next.
        #
        # No collision is strictly better than a shape that crashes on load,
        # so the child is dropped.
        _DEGENERATE_HULLS_DROPPED[0] += 1
        return None

    # Unknown shape — return as-is
    return shape


# ---------------------------------------------------------------------------

def _node_is_animated(node, actual_root):
    """True if this node's transform is driven by animation in this NIF.

    Sources checked (walking from the file root):
      - NiControllerSequence controlled blocks (node-name entries),
      - NiMultiTargetTransformController extra targets,
      - a NiTransformController/NiKeyframeController attached to the node.
    Used to decide whether an Oblivion MO_SYS_KEYFRAMED body stays keyframed
    in Skyrim (gate leaves, animated lids) or is an unyielding anchor/held
    trap part instead (see the motion-system comment in _convert_collision).
    """
    root = actual_root if actual_root is not None else node

    def _name_of(b):
        nm = getattr(b, 'name', b'')
        return nm.decode('latin-1') if isinstance(nm, (bytes, bytearray)) else str(nm)

    names = set()

    def _walk(n):
        if not isinstance(n, NifFormat.NiAVObject):
            return
        ctrl = getattr(n, 'controller', None)
        while ctrl is not None:
            cls = ctrl.__class__.__name__
            if cls == 'NiControllerManager':
                for seq in getattr(ctrl, 'controller_sequences', []) or []:
                    if seq is None:
                        continue
                    for cb in getattr(seq, 'controlled_blocks', []) or []:
                        try:
                            nm = cb.get_node_name()
                        except Exception:
                            nm = None
                        if nm:
                            names.add(nm.decode('latin-1')
                                      if isinstance(nm, (bytes, bytearray)) else str(nm))
            elif cls == 'NiMultiTargetTransformController':
                for t in getattr(ctrl, 'extra_targets', []) or []:
                    if t is not None:
                        names.add(_name_of(t))
            elif 'TransformController' in cls or 'KeyframeController' in cls:
                names.add(_name_of(n))
            ctrl = getattr(ctrl, 'next_controller', None)
        for c in getattr(n, 'children', []) or []:
            if c is not None:
                _walk(c)

    _walk(root)
    return _name_of(node) in names


# Oblivion collision layer 10 = OL_PROPS, the DYNAMIC clutter layer (barrels,
# cups -- everything Havok simulates and lets fall).  It is the authored
# indicator that separates a piece meant to break off and drop from one whose
# animation performs the whole motion: the artist picks the layer in the
# exporter, so this is a statement of intent, not a measurement.
#
# Census of every ms=6 + animated + mass>0 body across both plugins (44 meshes):
#   layer 10 OL_PROPS   -> falls: mwallplankbreakaway01, idcrumblewall01,
#                          cpbrick01-15, cpgenericbrick01-03, cplog01/02,
#                          artrapbridgecrumble, roperock01, rfpitbridgetrap
#   layer 2/3           -> self-actuating, MUST stay keyframed: prisoncellgate01,
#                          cgprisoncellgate01, icbastioncellgate01, argate01,
#                          rfwportcullis01(+door), arpitstairs01/03,
#                          arenclosedcircle01, dreamstairs01
#   layer 14 OL_TRAP    -> swinging traps, keyframed: ctrapcavein01,
#                          ctraplogs01, cprollingrock01, artrapchannelspikes01
# Gates swing and portcullises slide precisely because they are NOT on the
# props layer; nothing on layer 10 is expected to hold a pose against gravity.
_OL_PROPS = 10


def _node_is_breakaway(node, actual_root, rb):
    """True if this animated node is a piece that breaks off and falls.

    Oblivion authors breakaway props (mwallplankbreakaway01's planks,
    IDCrumbleWall01's bricks) as ms=6 bodies with real mass on OL_PROPS: the
    sequence only creaks them off their mounting and Havok does the rest.
    Converting them to keyframed/mass-0 pins them in the half-broken pose
    forever.  Gates and portcullises are also ms=6 with mass, but they sit on
    the anim-static/clutter layers and must keep following their clip exactly.
    """
    if not _node_is_animated(node, actual_root):
        return False
    for attr in ('havok_col_filter', 'havok_col_filter_copy'):
        hf = getattr(rb, attr, None)
        if hf is not None and int(getattr(hf, 'layer', -1)) == _OL_PROPS:
            return True
    return False


def _node_is_held_trap(node, actual_root, rb):
    """True if this body is part of a trap island Oblivion holds until scripted.

    Oblivion authors a whole swinging trap (ctrapswingmacelong01's chain links
    + mace head, ctraplogs01's logs) as ms=6 KEYFRAMED bodies with real mass
    and `Unyielding = 1`, wired together by constraints.  ITS engine keeps the
    island rigid until the trap script runs `playgroup` -- the script header
    says so outright: "On activation havok will turn on and logs will roll".

    Shipping them dynamic (the old "mass>0 and owns a constraint" rule) made
    every trap swing freely on cell load.  Shipping them mass-0 keyframed
    welds them solid forever.  They are breakaway pieces in the exact sense
    the breakaway path already models: HELD, keeping their mass, released to
    Motion_Dynamic by the converted trap script.

    Membership is island-wide, not per-body: a chain link routinely carries
    mass with num_constraints == 0 and hangs off a neighbour's constraint
    (the same reason collision_extract checks constraints file-wide).
    """
    if rb.num_constraints > 0:
        return True
    root = actual_root if actual_root is not None else node
    for blk in root.tree():
        co = getattr(blk, 'collision_object', None)
        body = getattr(co, 'body', None) if co is not None else None
        if body is not None and getattr(body, 'num_constraints', 0) > 0:
            return True
    return False


def _convert_blend_collision(node, coll_obj):
    """Convert a bhkBlendCollisionObject on a creature-skeleton bone.

    Vanilla Skyrim creature skeletons KEEP blend collision objects (dog:
    flags=137, plain bhkRigidBody with a NON-zero bone-relative translation
    in Havok units, capsule shapes, motion_system=4 KEYFRAMED,
    quality_type=1 FIXED, layer=8 BIPED).  Shape/material/2010-format fixups
    are shared with the standard path.  Inertia gets the full ×0.01
    (mass·length², lengths scale ×0.1) here, same as the standard dynamic
    path in _convert_collision.
    """
    coll_obj.flags = 137
    rb = getattr(coll_obj, 'body', None)
    if rb is None:
        return
    # Blend bodies USE their translation (bone-relative placement) — scale,
    # never zero, even for plain bhkRigidBody.
    rb.translation.x *= _HAVOK_SCALE
    rb.translation.y *= _HAVOK_SCALE
    rb.translation.z *= _HAVOK_SCALE
    rb.center.x *= _HAVOK_SCALE
    rb.center.y *= _HAVOK_SCALE
    rb.center.z *= _HAVOK_SCALE
    _convert_rigid_body(rb)
    # bhkEntityCInfo byte: prop bodies use 116/10, but the vanilla creature
    # blend-body census is 0 (COM root occasionally 3)
    rb.unknown_byte = 0
    # MASS: divide by the Oblivion->game unit scale, exactly as
    # hkx_ragdoll._OB_MASS_DIV does for the skeleton.hkx ragdoll bodies.
    #
    # **These are the SAME physical bodies described twice** — vanilla ships
    # identical masses in skeleton.nif and skeleton.hkx (dog: total 74.00 in
    # both, per-body 2-6).  The 2026-08-08 "dead creatures weigh a million
    # pounds, I can only move limbs a little" report survived fixing the hkx
    # side alone because the ENGINE WEIGHS THE NIF BLEND BODIES: they still
    # shipped Oblivion's raw values (dog total 262, per-body 25-50) while the
    # hkx said 37.4, so the two representations disagreed and the heavy one won.
    #
    # Whenever mass handling changes, it MUST change in both places or they
    # desync.  Verify with: vanilla dog nif == 74.0 total, and our output nif
    # total == our output hkx total.
    rb.mass = float(rb.mass) / _OB_MASS_DIV
    for attr in ('m_11', 'm_12', 'm_13', 'm_21', 'm_22', 'm_23',
                 'm_31', 'm_32', 'm_33'):
        setattr(rb.inertia, attr,
                getattr(rb.inertia, attr) * _HAVOK_SCALE * _HAVOK_SCALE
                / _OB_MASS_DIV)
    rb.motion_system = 4        # MO_SYS_KEYFRAMED (bone follows animation)
    rb.quality_type = 1         # MO_QUAL_FIXED
    rb.deactivator_type = 1
    # Dynamics contract (vanilla census: wolf/dog/bear/deer/sabrecat/skeever,
    # 136/136 blend bodies identical).  Skyrim APPLIES these on blend bodies
    # — same lesson as the chains/signs saga — and Oblivion ships garbage:
    # damping 5.0/5.0 froze every corpse solid mid-air (no gravity reaction,
    # immovable by havok grab), maxLinearVelocity up to 10000, restitution
    # 0.3, solverDeactivation 1, and junk in translation.w.
    rb.linear_damping = 0.0996
    rb.angular_damping = 0.0498
    rb.max_linear_velocity = 104.4
    rb.max_angular_velocity = 31.57
    rb.solver_deactivation = 2
    rb.friction = 0.3
    rb.restitution = 0.8
    rb.translation.w = 0.0
    rb.havok_col_filter.layer = 8   # SKYL_BIPED
    # Oblivion's filter byte (0x40 | part) means nothing to Skyrim — the
    # vanilla creature-skeleton census is plain small part numbers with NO
    # flag bits (wolf: all 0 across 22 bodies; bear: 0-2). Carrying the
    # Oblivion byte through left every blend body flagged.
    rb.havok_col_filter.flags_and_part_number = 0
    if hasattr(rb, 'havok_col_filter_copy'):
        rb.havok_col_filter_copy.layer = 8
        rb.havok_col_filter_copy.flags_and_part_number = 0
    rb.shape = _convert_shape(rb.shape, node)
    convert_materials(rb.shape)


def _convert_collision(node, actual_root=None, keep_blend=False):
    """Convert all collision on a NiNode from Oblivion to Skyrim Havok format.

    Modifies node.collision_object in-place.
    actual_root: the NIF's top-level root node, used as target for
    bhkCompressedMeshShape so Skyrim reads the correct world transform.
    keep_blend: creature skeletons — convert bhkBlendCollisionObject
    (ragdoll bone collision, fully supported by Skyrim) instead of
    stripping it.
    """
    if not hasattr(node, 'collision_object') or node.collision_object is None:
        return

    # bhkBlendCollisionObject is stripped on world objects, but on creature
    # skeletons (keep_blend) it is the vanilla ragdoll-bone type.
    cls_name = node.collision_object.__class__.__name__
    if cls_name == 'bhkBlendCollisionObject':
        if keep_blend:
            _convert_blend_collision(node, node.collision_object)
        else:
            node.collision_object = None
        return
    if cls_name == 'bhkSPCollisionObject':
        # Trigger-volume phantom (tripwire triggers, gas/fire damage zones).
        # Skyrim fully supports bhkSPCollisionObject + bhkSimpleShapePhantom —
        # vanilla ships 31 of them under meshes/traps alone (traptripwire01,
        # pressure plates, bear trap...), always with collision-object
        # flags=129 and layer 12 (TRIGGER, same enum value as Oblivion).
        # Convert the inner shape (×0.1 scale + material remap) and keep it.
        body = getattr(node.collision_object, 'body', None)
        if isinstance(body, NifFormat.bhkSimpleShapePhantom):
            node.collision_object.flags = 129
            _remap_world_filter(body)
            body.shape = _convert_shape(body.shape, node)
            convert_materials(body.shape)
        else:
            node.collision_object = None
        return
    if cls_name == 'bhkNPCollisionObject':
        node.collision_object = None
        return

    coll_obj = node.collision_object
    # Default: standard Skyrim collision flags.  Animated collision (keyframed)
    # has flags overridden below after rigid body analysis.
    coll_obj.flags = 129

    rb = coll_obj.body if hasattr(coll_obj, 'body') else None
    if rb is None:
        return

    if isinstance(rb, NifFormat.bhkSimpleShapePhantom):
        _remap_world_filter(rb)
        rb.shape = _convert_shape(rb.shape, node)
        convert_materials(rb.shape)
        return

    # Scale rigid body translation.
    # bhkRigidBodyT uses translation/rotation for the Havok body offset; scale
    # the translation and keep the rotation.
    # bhkRigidBody (non-T): OBLIVION ignores both fields, so its files carry
    # arbitrary leftover values there.  SKYRIM APPLIES BOTH EVEN ON NON-T
    # BODIES — proven by vanilla trapmace01.nif Base01: node rotated +0.5°
    # about X, body rotation = the exact inverse quaternion (-0.0044,0,0,1)
    # so its root-space MOPP stays aligned; every other vanilla non-T body is
    # exactly identity/zero, unlike the genuinely-garbage padding fields.
    # Leftover Oblivion rotations (up to ~115° on chain links) rotated every
    # constraint frame and collision shape out from under the solver: chains/
    # swinging traps acted welded solid, and ordinary clutter collision sat
    # askew from the visual mesh ("havok interactions feel weird").
    if isinstance(rb, NifFormat.bhkRigidBodyT):
        rb.translation.x *= _HAVOK_SCALE
        rb.translation.y *= _HAVOK_SCALE
        rb.translation.z *= _HAVOK_SCALE
    else:
        rb.translation.x = 0.0
        rb.translation.y = 0.0
        rb.translation.z = 0.0
        rb.translation.w = 0.0
        rb.rotation.x = 0.0
        rb.rotation.y = 0.0
        rb.rotation.z = 0.0
        rb.rotation.w = 1.0
    rb.center.x *= _HAVOK_SCALE
    rb.center.y *= _HAVOK_SCALE
    rb.center.z *= _HAVOK_SCALE

    _convert_rigid_body(rb)
    _remap_world_filter(rb)

    # Penetration depth is a LENGTH (max allowed overlap): Oblivion ships
    # 0.15 in its Havok units; vanilla Skyrim bodies carry ~0.005-0.012.
    # Unscaled it lets contacts sink an entire chain-link deep.
    rb.penetration_depth *= _HAVOK_SCALE

    # Oblivion MO_SYS_KEYFRAMED (6) semantics are context-dependent.  Three
    # cases, discriminated per body (vanilla Skyrim census):
    #  1. Node driven by animation (gate leaves targeted by Open/Close
    #     sequences, animated display-case lids) → Skyrim KEYFRAMED, like
    #     vanilla farmhouseanimdoor01.  Keyframed is ONLY valid for animated
    #     nodes: a keyframed body with anim flags (137/142) on a non-animated
    #     object flips the engine into the baked/anim-static path and the
    #     whole compound acts welded solid.
    #  2. mass>0 AND owns a constraint (mace-trap chain links: Oblivion holds
    #     whole traps keyframed until the trap script enables havok) →
    #     DYNAMIC, like vanilla trapmace01's links (ms=3, quality 4).
    #  3. Everything else (constrained-island anchors: cellchain01 root,
    #     cellChainMiddle, mass=100 "Unyielding"; unyielding props) →
    #     STATIC with mass 0.  Vanilla chain/noose/trap anchors are ALWAYS
    #     static mass-0 bodies (NooseRopePiece01 root, trapmace Base01),
    #     never keyframed.
    #  4. BREAKAWAY pieces (mwallplankbreakaway01's 8 planks, IDCrumbleWall01's
    #     bricks): ms=6 bodies with real mass whose clip only creaks them off
    #     their mounting -- 15.19 deg and ZERO translation keys for the planks
    #     -- because the visible break is HAVOK taking over and letting the
    #     pieces detach and FALL.  Forcing those onto the plain keyframed path
    #     (which also zeroes the mass) pinned them forever: the clip played, the
    #     planks tilted, and then hung in the half-broken pose as a solid wall.
    #     Gates/portcullises are also ms=6 with mass but sit on the anim-static
    #     layers, so the authored OL_PROPS layer separates the two.
    #
    #     A breakaway piece still ships KEYFRAMED, exactly like Oblivion's
    #     `Unyielding = 1` (all 8 planks; the root is Unyielding 0 / mass 0):
    #     the body is HELD, following the clip, and only becomes dynamic when
    #     the animation ends.  Shipping it dynamic instead made the planks drop
    #     the instant the cell loaded, before the clip ever played.  What the
    #     breakaway flag changes is that the piece KEEPS ITS MASS, so the
    #     script-side release (ObjectReference.SetMotionType(Motion_Dynamic))
    #     hands Havok a body that can actually fall -- a mass-0 body would just
    #     hang there.  See script_convert PlayGroup handling.
    #  5. HELD TRAP islands (ctrapswingmacelong01's chain + mace,
    #     ctraplogs01's logs, cprollingrock01): ms=6 bodies with real mass
    #     that belong to a CONSTRAINED island.  Case 2 shipped these DYNAMIC,
    #     which is what made every swinging trap swing freely the moment the
    #     cell loaded, before anything tripped it.  Oblivion's own trap script
    #     states the contract in its header comment -- "On activation havok
    #     will turn on and logs will roll" (CTrapLogs01SCRIPT) -- and authors
    #     the whole island `Unyielding = 1`: the trap is HELD rigid until the
    #     trap script fires, exactly like a breakaway piece.
    #
    #     So a constrained trap island is a breakaway: ship it KEYFRAMED (held,
    #     not simulating) but KEEP the authored mass, and let the script-side
    #     SetMotionType(Motion_Dynamic) release start the swing.  Skyrim's own
    #     trapmace01 ships its links dynamic because a Skyrim trap has no
    #     script-held phase; ours must reproduce Oblivion's held phase instead.
    breakaway_body = (rb.motion_system == 6 and rb.mass > 0
                      and (_node_is_breakaway(node, actual_root, rb)
                           or _node_is_held_trap(node, actual_root, rb)))
    keyframed_body = (rb.motion_system == 6
                      and (_node_is_animated(node, actual_root)
                           or breakaway_body))
    if rb.motion_system == 6 and not keyframed_body:
        rb.mass = 0.0               # case 3: falls into the static branch

    # Oblivion MO_SYS_FIXED (7) is the explicit "static element of the scene"
    # motion type (nif.xml: landscape/architecture).  The static-vs-dynamic
    # branch below dispatches on mass alone, which silently misreads any fixed
    # body whose mass field is non-zero as clutter: Skyrim then simulates a
    # 1000 kg mesh-collision prop, and it tips onto its side, sinks, or spins
    # off through the air on cell load.
    #
    # Base Oblivion hid the bug — a 300-NIF census found 198 ms=7 bodies with
    # mass EXACTLY 0 (0 non-zero), so mass alone happened to classify all of
    # them right.  Third-party content does not follow that convention: the
    # same census over Morroblivion found 186 ms=7 bodies of which 157 carry
    # a junk mass (1000.0 is its idiom for "static"), i.e. the majority of its
    # statics were being converted into dynamic clutter.
    #
    # A fixed body that owns a constraint is left alone: it is a real
    # trap/chain part whose island the constraint branches handle.
    if rb.motion_system == 7 and rb.num_constraints == 0:
        rb.mass = 0.0               # falls into the static branch
    if keyframed_body:
        # Skyrim animated doors/activators: the collision body follows the
        # NiNode animation exactly (keyframed).  Values sourced from vanilla
        # Skyrim farmhouseanimdoor01.nif.
        coll_obj.flags      = 137  # 0x89 = ACTIVE | D_ANIMATED | bit 7
        rb.motion_system    = 4    # MO_SYS_KEYFRAMED
        rb.deactivator_type = 1
        # quality_type / solver_deactivation are NOT changed by the runtime
        # release: `SetMotionType(Motion_Dynamic)` swaps only the motion type,
        # so whatever ships in the NIF is what the body simulates with AFTER a
        # breakaway/held trap is let go.
        #
        #  * A plain animated body (door, gate, portcullis) is never released
        #    and its position is fully deterministic -> MO_QUAL_FIXED with
        #    solver deactivation OFF, sourced from vanilla
        #    farmhouseanimdoor01.nif.
        #  * A BREAKAWAY / HELD-TRAP body becomes a real moving object the
        #    instant the script releases it, and it does so while carrying
        #    mass inside a live constraint island.  MO_QUAL_FIXED there tells
        #    Havok the body is static, so the released chain is a ring of
        #    mass-bearing constrained bodies all claiming to be static with
        #    deactivation disabled -- the solver has no consistent state to
        #    converge on and the simulation step stops completing (the
        #    Natural Caverns / CGTrigTripwire01 hang: the game keeps running
        #    but never renders another frame).  Vanilla's own chain trap is
        #    the reference for the released state: trapmace01.nif ships every
        #    Link01..11 at quality_type=4 (MO_QUAL_MOVING) with
        #    solver_deactivation=2 (LOW), and its Mace01 head likewise.
        #    Vilverin's ctrapswingmaceshort01 never hit this because it has
        #    NO chain links and so no constraint island at all.
        if breakaway_body:
            rb.quality_type        = 4  # MO_QUAL_MOVING (post-release)
            rb.solver_deactivation = 2  # SOLVER_DEACTIVATION_LOW
        else:
            rb.quality_type        = 1  # MO_QUAL_FIXED (deterministic)
            rb.solver_deactivation = 1  # OFF
        rb.unknown_byte     = 10   # Skyrim broadphase type for animated
        # Set bit 7 (0x80) on the animated NiNode — tells Skyrim to
        # synchronise the node's transform updates with physics.
        if hasattr(node, 'flags'):
            node.flags = NIF_FLAGS | 0x80  # 0x008E = 142
        # Layer MUST be 2 SKYL_ANIMSTATIC.  Census: every vanilla keyframed
        # body is layer 2 (farmhouseanimdoor01, rtirongate01, orcdoor01,
        # riftenkeepdoor01 ×2, mrkmarketstalldoor01, rifrmsmbasewallgrate01,
        # rifrmsmsecretcabinetdoor01 ×2, farmbtrapdoor01,
        # sldjailwallcollapse01 — 11/11, zero exceptions), and our one
        # in-game-confirmed working animated object (prisonSecretWall01,
        # source-authored OL_ANIM_STATIC) also ships layer 2.  Oblivion
        # authored crumble-wall bricks / breakaway planks on OL_PROPS (10),
        # which _remap_world_filter passes through unchanged — those were the
        # meshes whose sequences played without any visible motion.
        # A breakaway piece keeps SKYL_PROPS (10): once the clip ends the script
        # releases it to Motion_Dynamic and it has to collide and settle as
        # ordinary debris, which layer 2 ANIMSTATIC does not do.  Vanilla
        # breakableboard01 ships its falling Piece01/Piece02 on layer 10 for
        # exactly this reason (its fixed Anchors sit on layer 0).
        if not breakaway_body:
            for _hf in (getattr(rb, 'havok_col_filter', None),
                        getattr(rb, 'havok_col_filter_copy', None)):
                if _hf is not None:
                    _hf.layer = 2  # SKYL_ANIMSTATIC
        rb.friction         = 0.50
        rb.restitution      = 0.40
        rb.linear_damping   = 0.0996
        rb.angular_damping  = 0.0498
        rb.max_linear_velocity  = _KEYFRAMED_VELOCITY_CAP
        rb.max_angular_velocity = _KEYFRAMED_VELOCITY_CAP
        # Scripts can switch keyframed trap bodies to dynamic at runtime
        # (swinging traps activate that way), so inertia must be in Skyrim
        # units even though keyframed motion ignores it.  ×0.01, same as the
        # dynamic branch.
        _s2 = _HAVOK_SCALE * _HAVOK_SCALE
        for _attr in ('m_11', 'm_12', 'm_13', 'm_21', 'm_22', 'm_23',
                      'm_31', 'm_32', 'm_33'):
            setattr(rb.inertia, _attr, getattr(rb.inertia, _attr) * _s2)
    elif rb.mass == 0:
        # Static object — vanilla Skyrim static NIFs (farmhouse01.nif etc.)
        # use quality_type=0 (MO_QUAL_INVALID = auto-detect), not 1.
        # The working pre-refactor code also used 0.
        rb.motion_system    = 5  # SYS_BOX_STABILIZED
        rb.deactivator_type = 1
        rb.quality_type     = 0  # MO_QUAL_INVALID (auto-detect, vanilla standard)
        rb.solver_deactivation = 1
        rb.friction         = 0.50
        rb.restitution      = 0.40
        rb.linear_damping   = 0.0996
        rb.angular_damping  = 0.0498
        rb.max_linear_velocity  = 104.4
        rb.max_angular_velocity = 31.57
    else:
        # Dynamic/clutter objects.
        #
        # Mass: keep Oblivion mass as-is.  Oblivion masses (0.1–35) are in the
        # same SI-kilogram range as Skyrim clutter (0.2–100) — no scaling needed.
        # Skyrim designers set masses independently; there is no consistent
        # object-to-object multiplier between the two games.
        #
        # Inertia tensor: inertia ∝ mass × length², and lengths scale by
        # _HAVOK_SCALE (0.1) going from Oblivion Havok units (game/7) to Skyrim
        # Havok units (game/70) — so inertia scales by _HAVOK_SCALE² = 0.01.
        # Verified against vanilla: silverjug01 (mass 0.8, r≈0.19 hk, h≈0.6 hk)
        # stores I_x=0.031 = m(3r²+h²)/12 exactly (SI physics in Havok metres).
        # Scaling by only 0.1 leaves inertia ~10× too large → objects resist
        # rotation, feel sluggish/heavy when grabbed or knocked.
        _INERTIA_SCALE = _HAVOK_SCALE ** 2  # 0.01
        rb.inertia.m_11 *= _INERTIA_SCALE
        rb.inertia.m_12 *= _INERTIA_SCALE
        rb.inertia.m_13 *= _INERTIA_SCALE
        rb.inertia.m_21 *= _INERTIA_SCALE
        rb.inertia.m_22 *= _INERTIA_SCALE
        rb.inertia.m_23 *= _INERTIA_SCALE
        rb.inertia.m_31 *= _INERTIA_SCALE
        rb.inertia.m_32 *= _INERTIA_SCALE
        rb.inertia.m_33 *= _INERTIA_SCALE
        # motion_system: preserve SPHERE (2) for round objects; map all others
        # (Oblivion used BOX=4) to SPHERE_INERTIA (3), which Skyrim uses for
        # asymmetric clutter (keys, bottles, boxes, etc.).
        if rb.motion_system == 2:
            pass  # keep SPHERE
        else:
            rb.motion_system = 3  # MO_SYS_SPHERE_INERTIA
        rb.quality_type    = 4  # MO_QUAL_MOVING
        rb.deactivator_type = 1
        rb.solver_deactivation = 2
        rb.rolling_friction_multiplier = 0
        rb.linear_damping  = 0.0996
        rb.angular_damping = 0.0498
        rb.friction        = 0.50
        rb.restitution     = 0.40
        rb.max_linear_velocity  = 104.4
        rb.max_angular_velocity = 31.57

    # Mesh collision (strips/packed, possibly under a stale Oblivion MOPP) is
    # rebuilt from scratch as vanilla-style MOPP + bhkCompressedMeshShape with
    # any bhkRigidBodyT transform baked into the geometry (plain identity
    # body, like all 6341 vanilla CMS meshes).  The CMS target is the root
    # BSFadeNode — static collision must live on the root.
    target_node = actual_root if actual_root is not None else node
    rebuilt = _rebuild_mesh_collision(rb, target_node)
    if rebuilt == 'drop':
        # Sub-viable hull (see _MIN_HULL_EXTENT) — ship no collision at all
        # rather than a shape that crashes Skyrim's loader.
        node.collision_object = None
        return
    if not rebuilt:
        rb.shape = _convert_shape(rb.shape, target_node)
    convert_materials(rb.shape)

    # Dynamic clutter with a single full-object convex hull: rebuild concave
    # objects (goblets, pitchers, ewers…) as a compound of tighter hulls so
    # the activation raycast and contacts match the visible mesh.
    # bhkRigidBodyT excluded — its shape frame is offset from the node frame.
    if (rb.mass > 0 and rb.__class__ is NifFormat.bhkRigidBody
            and isinstance(rb.shape, NifFormat.bhkConvexVerticesShape)):
        decomposed = decompose_clutter_hull(node, rb.shape)
        if decomposed is not None:
            rb.shape = decomposed

    # Keyframed bodies carry mass 0 — vanilla census 11/11, zero exceptions
    # (Havok keyframed motion has infinite effective mass; the field is
    # convention, but it is the one remaining field where broken converted
    # animated objects diverged from both vanilla and the in-game-confirmed
    # prisonSecretWall01).  This write MUST stay at the very end of the
    # function: an earlier attempt assigned it inside the keyframed branch,
    # which flipped the mass-keyed decompose gate above and silently rebuilt
    # the collision compound (see nif_conversion_notes.md, 2026-08-01).  Down
    # here nothing dispatches on mass any more, so the only bytes that change
    # are the mass field itself.
    # ...EXCEPT a breakaway piece, which is keyframed only until its clip ends.
    # It must keep the authored mass so the script-side
    # SetMotionType(Motion_Dynamic) release drops a body Havok can simulate.
    if keyframed_body and not breakaway_body:
        rb.mass = 0.0

def convert_all_collisions(node, actual_root=None, keep_blend=False):
    """Recursively convert collision objects on every node in the entire tree.

    Skyrim requires ALL bhkCollisionObject instances in a NIF to use Skyrim
    Havok format.  Our main conversion only processes the root node's collision,
    but objects like animated display cases have additional collision objects on
    child NiNodes (e.g. the moving lid).  These child collisions also contain
    Oblivion-format unknown_6_shorts values that cause a crash when Skyrim reads
    them as pointers.  This function walks the full tree to convert every one.

    actual_root: the NIF's top-level root node (BSFadeNode).  Passed through
    to _convert_collision → _rebuild_mesh_collision so that
    bhkCompressedMeshShape.target always points to the root, not an inner wrapper.
    keep_blend: creature skeleton mode — see _convert_collision.
    """
    if node is None:
        return
    # Collision is NOT limited to NiNode: Oblivion hangs bhkCollisionObject off
    # GEOMETRY too (obmkmeadhallmaindoor.nif puts a bhkPackedNiTriStripsShape
    # on the NiTriShape 'Scene Root:5').  Bailing on anything that was not an
    # NiNode left those objects entirely unconverted, so an Oblivion-only
    # packed shape reached Skyrim's loader -- the 2 GB memcpy / heap-corruption
    # crash.  Convert whatever this node owns, then keep walking.
    if actual_root is None:
        actual_root = node
    if getattr(node, 'collision_object', None) is not None:
        _convert_collision(node, actual_root, keep_blend=keep_blend)
    if hasattr(node, 'children'):
        for child in node.children:
            convert_all_collisions(child, actual_root, keep_blend=keep_blend)


# ---------------------------------------------------------------------------
# Collision hoisting (child → root)
# ---------------------------------------------------------------------------

def _offset_collision_shape_verts(co, ox, oy, oz):
    """Add (ox, oy, oz) game-unit offset to all collision shape vertices.

    Bakes a child NiNode's world-space translation into the shape so the
    collision stays in place after the node is hoisted to the root (which sits
    at the origin).  Traverses bhkCollisionObject -> body -> shape, unwrapping
    a bhkMoppBvTreeShape to reach the inner shape.  A bhkNiTriStripsShape takes
    the offset as-is; a bhkPackedNiTriStripsShape stores verts at 1/7 game
    units, so the offset is divided by 7 first.

    See: docs/commentary/asset_convert_collision.md#packed-shape-vertex-scale
    """
    rb = getattr(co, 'body', None)
    if rb is None:
        return
    shape = getattr(rb, 'shape', None)
    if shape is not None and isinstance(shape, NifFormat.bhkMoppBvTreeShape):
        shape = shape.shape
    if shape is None:
        return

    if isinstance(shape, NifFormat.bhkNiTriStripsShape):
        for sd in shape.strips_data:
            if sd is None:
                continue
            for v in sd.vertices:
                v.x += ox
                v.y += oy
                v.z += oz
        return

    if isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        data = getattr(shape, 'data', None)
        if data is None:
            return
        sx = ox / _OB_GAME_UNITS_PER_HAVOK
        sy = oy / _OB_GAME_UNITS_PER_HAVOK
        sz = oz / _OB_GAME_UNITS_PER_HAVOK
        for v in data.vertices:
            v.x += sx
            v.y += sy
            v.z += sz


_OB_GAME_UNITS_PER_HAVOK = 7.0  # Oblivion: 1 Havok unit = 7 game units


def _m3_from_quat_xyzw(x, y, z, w):
    """Unit quaternion (x,y,z,w) → 3x3 column-vector rotation matrix.

    Same formula as NifSkope's Matrix::fromQuat, which is how the engine
    interprets bhkRigidBodyT.rotation.
    """
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z),     2 * (x * z + w * y)],
        [2 * (x * y + w * z),     1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y),     2 * (y * z + w * x),     1 - 2 * (x * x + y * y)],
    ]


def _quat_xyzw_from_m3(m):
    """3x3 column-vector rotation matrix → unit quaternion (x,y,z,w).

    Shoemake branches handle 180° rotations (trace = -1, w = 0), which are
    common on Oblivion architecture roots.
    """
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    return x, y, z, w


def _is_identity(rotation):
    """Return True if a PyFFI Matrix33 is the identity matrix."""
    return (abs(rotation.m_11 - 1.0) < 1e-4 and abs(rotation.m_22 - 1.0) < 1e-4 and
            abs(rotation.m_33 - 1.0) < 1e-4 and abs(rotation.m_12) < 1e-4 and
            abs(rotation.m_13) < 1e-4 and abs(rotation.m_21) < 1e-4 and
            abs(rotation.m_23) < 1e-4 and abs(rotation.m_31) < 1e-4 and
            abs(rotation.m_32) < 1e-4)


def _wrap_shape_in_node_transform(body, node):
    """Compose a node's local transform into a phantom body's shape.

    bhkSimpleShapePhantom (trigger volumes, trap damage zones) carries no
    translation/rotation fields of its own, so a hoisted phantom cannot use
    bake_node_transform_into_body.  Wrap its shape in a bhkTransformShape
    holding L instead — Skyrim reads bhkTransformShape inside a phantom
    normally, and _convert_shape already scales its m_14/m_24/m_34 column.

    An existing bhkTransformShape / bhkConvexTransformShape at the top is
    composed into rather than double-wrapped.  Translation stays in Oblivion
    havok units at this stage (the _HAVOK_SCALE pass runs later).
    """
    shape = getattr(body, 'shape', None)
    if shape is None:
        return False

    r = node.rotation
    R = [[r.m_11, r.m_21, r.m_31],
         [r.m_12, r.m_22, r.m_32],
         [r.m_13, r.m_23, r.m_33]]  # column-vector convention
    s = float(node.scale)
    T = (node.translation.x / _OB_GAME_UNITS_PER_HAVOK,
         node.translation.y / _OB_GAME_UNITS_PER_HAVOK,
         node.translation.z / _OB_GAME_UNITS_PER_HAVOK)

    if isinstance(shape, (NifFormat.bhkTransformShape,
                          NifFormat.bhkConvexTransformShape)):
        # Compose into the existing wrapper: M' = R·M, t' = R·(t·s) + T
        m = shape.transform
        M_old = [[m.m_11, m.m_21, m.m_31],
                 [m.m_12, m.m_22, m.m_32],
                 [m.m_13, m.m_23, m.m_33]]
        t_old = (m.m_14, m.m_24, m.m_34)
        target = shape
    else:
        M_old = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        t_old = (0.0, 0.0, 0.0)
        target = NifFormat.bhkTransformShape()
        target.shape = shape
        if hasattr(shape, 'material') and hasattr(target, 'material'):
            target.material = shape.material
        body.shape = target

    M_new = [[sum(R[i][k] * M_old[k][j] for k in range(3)) for j in range(3)]
             for i in range(3)]
    t_new = [sum(R[i][k] * t_old[k] * s for k in range(3)) + T[i]
             for i in range(3)]

    m = target.transform
    m.m_11, m.m_21, m.m_31 = M_new[0][0], M_new[1][0], M_new[2][0]
    m.m_12, m.m_22, m.m_32 = M_new[0][1], M_new[1][1], M_new[2][1]
    m.m_13, m.m_23, m.m_33 = M_new[0][2], M_new[1][2], M_new[2][2]
    m.m_14, m.m_24, m.m_34 = t_new[0], t_new[1], t_new[2]
    m.m_41 = m.m_42 = m.m_43 = 0.0
    m.m_44 = 1.0
    return True


def bake_node_transform_into_body(coll_obj, node, extra_z=0.0):
    """Compose a node's local transform L=(R,T,s) into its collision body.

    extra_z: additional model-space z translation carried by the wrapper but
    not present on the node itself (the furniture origin shift — the importer
    lowers the REFRs by the same amount, so the collision must rise with the
    geometry or it ends up sunk by the shift).

    Used when the root's transform is about to be zeroed (rotation-wrap pass):
    the engine places a root collision body at REFR ∘ bodyT, so the vanishing
    root transform must be absorbed into bodyT or the collision ends up
    rotated/offset relative to the mesh (stackhallentrance01: 90° off).
    Collision must stay on the ROOT node — attaching it to the inner wrapper
    aligns it too, but intermittently crashes hkpCollisionDispatcher.

    NIF matrices act on row vectors under PyFFI's m_ij naming; the engine
    (and NifSkope) reads the same file bytes as a column-vector matrix, so the
    column matrix is the m_ij-named transpose.  bhkRigidBody(T) translation is
    in Oblivion Havok units at this stage (game / 7); the Skyrim rescale
    (× _HAVOK_SCALE) happens later in _convert_collision.

    bhkRigidBody (non-T) carries no transform of its own, so it is promoted to
    bhkRigidBodyT (identical field layout — PyFFI class swap) to hold L.
    Returns True if the body was modified.
    """
    body = getattr(coll_obj, 'body', None)
    if body is None or not isinstance(body, NifFormat.bhkRigidBody):
        return False

    r = node.rotation
    R = [[r.m_11, r.m_21, r.m_31],
         [r.m_12, r.m_22, r.m_32],
         [r.m_13, r.m_23, r.m_33]]  # column-vector convention
    s = node.scale
    T = (node.translation.x / _OB_GAME_UNITS_PER_HAVOK,
         node.translation.y / _OB_GAME_UNITS_PER_HAVOK,
         (node.translation.z + extra_z) / _OB_GAME_UNITS_PER_HAVOK)

    if isinstance(body, NifFormat.bhkRigidBodyT):
        q = body.rotation
        M_old = _m3_from_quat_xyzw(q.x, q.y, q.z, q.w)
        t_old = (body.translation.x, body.translation.y, body.translation.z)
    else:
        body.__class__ = NifFormat.bhkRigidBodyT
        M_old = _m3_from_quat_xyzw(0.0, 0.0, 0.0, 1.0)
        t_old = (0.0, 0.0, 0.0)

    # bodyT' = L ∘ bodyT:  M' = R·M,  t' = R·(t·s) + T
    M_new = [[sum(R[i][k] * M_old[k][j] for k in range(3)) for j in range(3)]
             for i in range(3)]
    t_new = [sum(R[i][k] * t_old[k] * s for k in range(3)) + T[i]
             for i in range(3)]
    x, y, z, w = _quat_xyzw_from_m3(M_new)

    body.rotation.x = x
    body.rotation.y = y
    body.rotation.z = z
    body.rotation.w = w
    body.translation.x = t_new[0]
    body.translation.y = t_new[1]
    body.translation.z = t_new[2]
    return True


def hoist_collision(root):
    """Find a collision object on any descendant NiNode and move it to root.

    Skyrim requires bhkCollisionObject to be on the root BSFadeNode.
    Oblivion meshes sometimes put it on a child NiNode (e.g. 'CollisionXxx').
    We take the first one found, assign it to root, and null it on the child.

    The child's FULL local transform (R, T, s) must follow the collision to the
    root, which sits at the origin with an identity rotation.  Two mechanisms:

      * bhkRigidBody(T) → bake_node_transform_into_body composes L into the
        body transform (promoting to bhkRigidBodyT).  Shape-agnostic, so it
        works for convex hulls, list shapes and primitives, none of which have
        a vertex array _offset_collision_shape_verts could rewrite.  Mesh
        collision later folds that bodyT back into the triangles in
        _bake_body_transform_into_tris and demotes the body to plain identity,
        matching all 6341 vanilla CMS meshes (a bhkRigidBodyT paired with
        MOPP/CMS makes the engine emit HK_INVALID_SHAPE_KEY and CTD).

      * bhkSimpleShapePhantom (trigger volumes / trap damage zones) has no
        body transform field at all and cannot be promoted, so its transform
        is composed into the inner shape via a bhkTransformShape wrapper.

    Previously only the child's TRANSLATION was applied, and only for the two
    strips shape types — so a rotated collision node silently lost its
    rotation.  citadelballconystandardendleft02 is the case in point: a
    180-degree flip about X put its collision at Y +508..+1035 while the
    balcony it belongs to sits at Y -957..-494, i.e. no collision at all where
    the player walks.  Its sibling citadelballconystandardendleft has the same
    geometry with an identity collision node and always converted correctly —
    the pair is the A/B that isolates the node rotation as the discriminator.

    Returns True if a collision was hoisted.
    """
    def _find_and_clear(node):
        """Return (collision_object, child_node) or None."""
        if not isinstance(node, NifFormat.NiNode):
            return None
        for child in node.children:
            if child is None:
                continue
            if (hasattr(child, 'collision_object') and
                    child.collision_object is not None):
                co = child.collision_object
                child.collision_object = None
                return co, child
            result = _find_and_clear(child)
            if result is not None:
                return result
        return None

    found = _find_and_clear(root)
    if found is None:
        return False

    co, child = found
    root.collision_object = co
    co.target = root

    t = child.translation
    ox, oy, oz = t.x, t.y, t.z
    has_translation = (ox != 0.0 or oy != 0.0 or oz != 0.0)
    has_rotation = not _is_identity(child.rotation)
    has_scale = abs(float(child.scale) - 1.0) > 1e-4

    if not (has_translation or has_rotation or has_scale):
        return True

    body = getattr(co, 'body', None)

    if isinstance(body, NifFormat.bhkSimpleShapePhantom):
        # No body transform field — compose L into the shape instead.
        _wrap_shape_in_node_transform(body, child)
        return True

    if bake_node_transform_into_body(co, child):
        return True

    # No rigid body to carry the transform (unknown body type): fall back to
    # the translation-only vertex bake, which is still better than dropping
    # the offset entirely.  Rotation/scale cannot be represented here.
    if has_translation:
        _offset_collision_shape_verts(co, ox, oy, oz)
    return True


def _collect_psys_referenced_nodes(root):
    """Return the set of id()s of nodes referenced by particle-system modifiers
    (NiPSysGravityModifier.gravity_object, *Emitter.emitter_object).

    These are empty marker NiNodes (e.g. 'Gravity', 'SparkGravity') that the
    particle physics point at; removing them dangles the reference and breaks
    the simulation (invisible particles)."""
    refs = set()
    for block in getattr(root, 'tree', lambda: [])():
        tn = type(block).__name__
        if tn == 'NiPSysGravityModifier':
            go = getattr(block, 'gravity_object', None)
            if go is not None:
                refs.add(id(go))
        elif tn.endswith('Emitter') and 'Ctlr' not in tn:
            eo = getattr(block, 'emitter_object', None)
            if eo is not None:
                refs.add(id(eo))
    return refs


def remove_empty_collision_nodes(root):
    """Remove empty NiNode children that were collision containers.

    After hoisting collision to root, the original NiNode child (e.g.
    'Collision045') is left empty: no children, no collision_object.  Skyrim
    processes every child of BSFadeNode and an unexpected empty NiNode can
    trigger crashes.  This function compacts the children array in-place.

    Nodes referenced by particle-system modifiers (Gravity/emitter objects)
    are PRESERVED even when empty — dropping them dangles the reference and the
    particle system stops rendering.
    """
    if not hasattr(root, 'children') or not isinstance(root, NifFormat.NiNode):
        return
    protected = _collect_psys_referenced_nodes(root)
    keep = []
    for child in root.children:
        if child is None:
            continue
        # Remove bare NiNodes with no children and no collision — unless a
        # particle modifier references them.
        if (type(child).__name__ in ('NiNode',) and
                child.num_children == 0 and
                getattr(child, 'collision_object', None) is None and
                id(child) not in protected):
            continue
        keep.append(child)
    if len(keep) < root.num_children:
        root.num_children = len(keep)
        root.children.update_size()
        for i, c in enumerate(keep):
            root.children[i] = c
