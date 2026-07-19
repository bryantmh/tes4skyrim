"""Tests for asset_convert.inv_marker — inventory display orientation.

Convention (derived empirically from vanilla meshes, see
tools/inv_marker_survey.py): the game composes the stored ushort
milliradian angles as M = Rx(-rx/1000) @ Ry(-ry/1000) @ Rz(-rz/1000) and
views the rotated model along +Y with +Z as screen-up.
"""

import time

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

import numpy as np
import pytest
from pyffi.formats.nif import NifFormat

from asset_convert.inv_marker import compute_inv_rotation, rotation_for_view


def _angles_close(stored, expected, tol=80):
    """Compare marker ushort milliradians modulo the full turn (6283)."""
    for s, e in zip(stored, expected):
        d = abs((s - e) % 6283)
        if min(d, 6283 - d) > tol:
            return False
    return True


def _game_matrix(rx, ry, rz):
    """The rotation Skyrim's inventory view applies to the model."""
    def rot(axis, a):
        c, s = np.cos(a), np.sin(a)
        if axis == 'X':
            return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
        if axis == 'Y':
            return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    return (rot('X', -rx / 1000.0) @ rot('Y', -ry / 1000.0)
            @ rot('Z', -rz / 1000.0))


def _make_plate_nif(normal):
    """BSFadeNode root with a unit quad whose face normal is ``normal``.

    The quad is built in the XY plane facing +Z, then swung so its normal
    matches; vertex normals are set accordingly (single-sided plate).
    """
    n = np.asarray(normal, dtype=float)
    n /= np.linalg.norm(n)
    # basis: two edges spanning the plate, wide along the first
    helper = np.array([0.0, 0.0, 1.0])
    if abs(n @ helper) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    e1 = np.cross(helper, n)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    verts = [(-2 * e1 - e2), (2 * e1 - e2), (2 * e1 + e2), (-2 * e1 + e2)]

    root = NifFormat.BSFadeNode()
    root.name = b'test'
    shape = NifFormat.NiTriShape()
    shape.name = b'plate'
    gd = NifFormat.NiTriShapeData()
    gd.num_vertices = 4
    gd.has_vertices = True
    gd.vertices.update_size()
    for i, v in enumerate(verts):
        gd.vertices[i].x, gd.vertices[i].y, gd.vertices[i].z = v
    gd.has_normals = True
    gd.normals.update_size()
    for i in range(4):
        gd.normals[i].x, gd.normals[i].y, gd.normals[i].z = n
    gd.num_triangles = 2
    gd.num_triangle_points = 6
    gd.has_triangles = True
    gd.triangles.update_size()
    for i, (a, b, c) in enumerate([(0, 1, 2), (0, 2, 3)]):
        gd.triangles[i].v_1 = a
        gd.triangles[i].v_2 = b
        gd.triangles[i].v_3 = c
    shape.data = gd
    root.num_children = 1
    root.children.update_size()
    root.children[0] = shape
    return root


def _face_toward_camera(root, rot):
    """Fraction of the mesh's max single-axis visible area that the game's
    camera (+Y) sees under marker rotation ``rot``."""
    from asset_convert.inv_marker import _gather_area_normals, _visible_area
    tri_n, tri_a, _c = _gather_area_normals(root)
    m = _game_matrix(*rot)
    rn = tri_n @ m.T
    axes = [np.array(a, dtype=float) for a in
            [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
             (0, 0, 1), (0, 0, -1)]]
    best = max(_visible_area(rn, tri_a, d) for d in axes)
    return _visible_area(rn, tri_a, np.array([0.0, 1.0, 0.0])) / best


class TestRotationForView:
    def test_face_up_flat_item_matches_vanilla_gnd(self):
        # cuirassgnd.nif convention: face-up ground item -> (1570, 0, 0)
        assert _angles_close(rotation_for_view([0, 0, 1]), (1570, 0, 0))

    def test_face_down_flat_item_matches_vanilla_shield(self):
        # ironshield.nif convention: -Z face -> (4712, 0, 0)
        assert _angles_close(rotation_for_view([0, 0, -1]), (4712, 0, 0))

    def test_camera_facing_side_is_identity(self):
        assert _angles_close(rotation_for_view([0, 1, 0]), (0, 0, 0))

    def test_away_facing_side_is_half_turn(self):
        assert _angles_close(rotation_for_view([0, -1, 0]), (0, 0, 3142))

    def test_side_facing_normals_stay_upright(self):
        # n along +/-X: model up (+Z) must stay screen-up (pure Z spin)
        for nx in (1, -1):
            rx, ry, rz = rotation_for_view([nx, 0, 0])
            m = _game_matrix(rx, ry, rz)
            assert np.allclose(m @ np.array([nx, 0.0, 0.0]),
                               [0, 1, 0], atol=1e-3)
            assert np.allclose(m @ np.array([0.0, 0.0, 1.0]),
                               [0, 0, 1], atol=1e-3)

    def test_arbitrary_normal_lands_on_camera(self):
        rng = np.random.default_rng(42)
        for _ in range(20):
            n = rng.normal(size=3)
            n /= np.linalg.norm(n)
            rx, ry, rz = rotation_for_view(n)
            assert np.allclose(_game_matrix(rx, ry, rz) @ n,
                               [0, 1, 0], atol=5e-3)


class TestComputeInvRotation:
    @pytest.mark.parametrize('normal', [
        (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0),
        (1, 0, 0), (0.6, -0.4, 0.7),
    ])
    def test_plate_best_side_faces_camera(self, normal):
        root = _make_plate_nif(normal)
        rot = compute_inv_rotation(root)
        assert rot is not None
        # the plate's face (its only real side) must end up at the camera
        assert _face_toward_camera(root, rot) > 0.99

    def test_face_up_plate_matches_vanilla_gnd_convention(self):
        rot = compute_inv_rotation(_make_plate_nif((0, 0, 1)))
        m_expected = _game_matrix(1570, 0, 0)
        m_actual = _game_matrix(*rot)
        # same view up to spin about the camera axis is NOT accepted here:
        # flat face-up items must follow the cuirassgnd screen-roll exactly
        assert np.allclose(m_actual @ [0, 0, 1], m_expected @ [0, 0, 1],
                           atol=5e-3)

    def test_empty_root_returns_none(self):
        root = NifFormat.BSFadeNode()
        root.name = b'empty'
        assert compute_inv_rotation(root) is None

    def test_hidden_geometry_ignored(self):
        root = _make_plate_nif((0, 0, 1))
        # add a much larger hidden plate facing -Z; it must not flip the pick
        big = _make_plate_nif((0, 0, -1))
        hidden_shape = big.children[0]
        for i in range(hidden_shape.data.num_vertices):
            v = hidden_shape.data.vertices[i]
            v.x, v.y, v.z = v.x * 10, v.y * 10, v.z * 10
        hidden_shape.flags |= 1  # APP_CULLED
        root.num_children = 2
        root.children.update_size()
        root.children[1] = hidden_shape
        rot = compute_inv_rotation(root)
        assert _angles_close(rot, (1570, 0, 0))
