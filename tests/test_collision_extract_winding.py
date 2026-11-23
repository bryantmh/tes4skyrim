"""_primitive_tris winds every triangle to face the side Havok collides on.

A mesh triangle by its stored (authored) normal, a solid's triangles outward, a mirroring transform
undone. Measured on Nehrim's interiors: before, the topmost flat face of 1,629 of 3,926 collision meshes
mixed up and down faces (and a navmesh built two-sided walked inside every solid); after, 25.
See: docs/commentary/asset_convert_collision.md#rewritten-2026-08-20-round-3--the-winding-is-authored-stop-inferring-it
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.collision import collision_extract as ce


class _Vec(object):
    """A vertex, normal or scale triple."""

    def __init__(self, x, y, z):
        """Hold the three components."""
        self.x, self.y, self.z = x, y, z


class _Tri(object):
    """One hkTriangle: its three vertex indices and its stored normal."""

    def __init__(self, a, b, c, normal):
        """Hold the indices as pyffi names them, and the normal."""
        self.triangle = type('T', (), {'v_1': a, 'v_2': b, 'v_3': c})()
        self.normal = normal


class _PackedData(object):
    """A floor quad whose second triangle lost its strip parity: wound down, its stored normal still up."""

    def __init__(self):
        """Two triangles of a unit square, both stored as facing +Z."""
        self.vertices = [_Vec(0, 0, 0), _Vec(1, 0, 0), _Vec(1, 1, 0), _Vec(0, 1, 0)]
        up = _Vec(0, 0, 1)
        self.triangles = [_Tri(0, 1, 2, up), _Tri(0, 3, 2, up)]


class bhkPackedNiTriStripsShape(object):
    """Stand-in packed shape; `_primitive_tris` dispatches on the class name."""

    def __init__(self):
        """Hold the data and a unit scale."""
        self.data = _PackedData()
        self.scale = _Vec(1.0, 1.0, 1.0)


class bhkBoxShape(object):
    """Stand-in box: half extents 1 x 2 x 3 (havok units)."""

    def __init__(self):
        """Hold the dimensions."""
        self.dimensions = _Vec(1.0, 2.0, 3.0)


class _Matrix(object):
    """A transform shape's 4 x 4 matrix: a mirror in X, no translation."""

    def __init__(self):
        """X negated, Y and Z kept."""
        for i in range(1, 5):
            for j in range(1, 5):
                setattr(self, f'm_{i}{j}', 0.0)
        self.m_11, self.m_22, self.m_33, self.m_44 = -1.0, 1.0, 1.0, 1.0


class bhkConvexTransformShape(object):
    """Stand-in transform shape mirroring a box."""

    def __init__(self):
        """A box under a mirroring matrix."""
        self.shape = bhkBoxShape()
        self.transform = _Matrix()


def _facing_up(tri):
    """Whether a triangle's winding faces +Z."""
    return ce._facing(tri)[2] > 0


def _all_outward(tris):
    """Whether every triangle of a solid centred on the origin faces away from it."""
    for tri in tris:
        f = ce._facing(tri)
        centre = [sum(p[axis] for p in tri) / 3.0 for axis in range(3)]
        if sum(a * b for a, b in zip(f, centre)) <= 0:
            return False
    return True


def test_packed_triangles_face_their_stored_normal():
    """The triangle wound against its stored normal is rewound; the other is left as it is."""
    tris = ce._primitive_tris(bhkPackedNiTriStripsShape(), scale=1.0)
    assert len(tris) == 2
    assert all(_facing_up(t) for t in tris)
    assert tris[0] == ((0, 0, 0), (1, 0, 0), (1, 1, 0))


def test_box_faces_outward():
    """Every face of a box shape points away from its centre (Havok's inside is solid)."""
    tris = ce._primitive_tris(bhkBoxShape(), scale=1.0)
    assert len(tris) == 12
    assert _all_outward(tris)


def test_mirroring_transform_keeps_faces_outward():
    """A transform whose matrix mirrors turns windings around; they are turned back."""
    tris = ce._primitive_tris(bhkConvexTransformShape(), scale=1.0)
    assert len(tris) == 12
    assert _all_outward(tris)
