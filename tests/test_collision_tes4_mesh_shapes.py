"""_primitive_tris on an unconverted TES4 NIF's triangle-mesh shapes, at TES4 havok scale.

See: docs/commentary/asset_convert_collision.md#collision-extraction-scale
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.collision import collision_extract as ce


class _Vec(object):
    """A vertex or scale triple."""

    def __init__(self, x, y, z):
        """Hold the three components."""
        self.x, self.y, self.z = x, y, z


class _Tri(object):
    """One hkTriangle: its three vertex indices."""

    def __init__(self, a, b, c):
        """Hold the indices as pyffi names them."""
        self.triangle = type('T', (), {'v_1': a, 'v_2': b, 'v_3': c})()


class _PackedData(object):
    """hkPackedNiTriStripsData: one triangle in havok units, plus one index past the end."""

    def __init__(self):
        """A unit right triangle and a triangle naming a missing vertex."""
        self.vertices = [_Vec(0, 0, 0), _Vec(1, 0, 0), _Vec(0, 1, 0)]
        self.triangles = [_Tri(0, 1, 2), _Tri(0, 1, 9)]


class bhkPackedNiTriStripsShape(object):
    """Stand-in packed shape; `_primitive_tris` dispatches on the class name."""

    def __init__(self):
        """Hold the data and a unit scale."""
        self.data = _PackedData()
        self.scale = _Vec(1.0, 1.0, 1.0)


class _StripsData(object):
    """NiTriStripsData: vertices already in TES4 game units."""

    def __init__(self):
        """One triangle seven units on a side."""
        self.vertices = [_Vec(0, 0, 0), _Vec(7, 0, 0), _Vec(0, 7, 0)]

    def get_triangles(self):
        """The strip's triangles as pyffi yields them."""
        return [(0, 1, 2)]


class bhkNiTriStripsShape(object):
    """Stand-in strips shape holding one NiTriStripsData."""

    def __init__(self):
        """Hold the strips data list."""
        self.strips_data = [_StripsData()]


def test_packed_strips_scale_by_havok_units():
    """A havok-unit triangle comes out TES4_HAVOK_TO_GAME times larger; a bad index is dropped."""
    tris = ce._primitive_tris(bhkPackedNiTriStripsShape(), scale=ce.TES4_HAVOK_TO_GAME)
    assert tris == [((0.0, 0.0, 0.0), (7.0, 0.0, 0.0), (0.0, 7.0, 0.0))]


def test_ni_tri_strips_keep_game_units():
    """NiTriStripsData is already in TES4 game units: unchanged at TES4 scale."""
    tris = ce._primitive_tris(bhkNiTriStripsShape(), scale=ce.TES4_HAVOK_TO_GAME)
    assert tris == [((0.0, 0.0, 0.0), (7.0, 0.0, 0.0), (0.0, 7.0, 0.0))]
