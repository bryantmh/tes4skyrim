"""Stairs materials on stepped collision treads (asset_convert/collision/collision_stairs.py).

See: docs/commentary/asset_convert_collision.md#stairs-material
"""
from types import SimpleNamespace

from asset_convert.collision.collision import _packed_tri_owners
from asset_convert.collision.collision_stairs import STAIRS_OF, stairs_materials

STONE = 3741512247
STAIRS_STONE = 899511101


def _quad(x0, y0, x1, y1, z):
    """Two up-facing triangles covering the XY rectangle at height z."""
    return [((x0, y0, z), (x1, y0, z), (x1, y1, z)),
            ((x0, y0, z), (x1, y1, z), (x0, y1, z))]


def _run(tris):
    """stairs_materials over `tris`, all stone, in game units."""
    return stairs_materials(tris, [STONE] * len(tris), 1.0)


def _tri(a, b, c):
    """A packed-data triangle row indexing vertices a, b, c."""
    return SimpleNamespace(triangle=SimpleNamespace(v_1=a, v_2=b, v_3=c))


def test_stone_pairs_with_stairs_stone():
    """Stone's stairs variant is StairsStone, from TES4's enum pairing."""
    assert STAIRS_OF[STONE] == STAIRS_STONE


def test_every_tread_of_a_flight_is_stairs():
    """Six 16u steps: every tread becomes StairsStone."""
    tris = [t for i in range(6) for t in _quad(0, 20 * i, 100, 20 * i + 20, 16 * i)]
    assert _run(tris) == [STAIRS_STONE] * len(tris)


def _riser(y, z0, z1):
    """Two vertical triangles facing -Y across x 0..100 at y, from z0 up to z1."""
    return [((0, y, z0), (100, y, z0), (100, y, z1)), ((0, y, z0), (100, y, z1), (0, y, z1))]


def test_risers_of_a_flight_are_stairs_and_a_full_height_side_wall_is_not():
    """Risers between treads take the stairs material; a wall spanning the flight keeps its own."""
    treads = [t for i in range(6) for t in _quad(0, 20 * i, 100, 20 * i + 20, 16 * i)]
    risers = [t for i in range(1, 6) for t in _riser(20 * i, 16 * (i - 1), 16 * i)]
    wall = [((0, 0, 0), (0, 120, 0), (0, 120, 96)), ((0, 0, 0), (0, 120, 96), (0, 0, 96))]
    out = _run(treads + risers + wall)
    assert out[:len(treads) + len(risers)] == [STAIRS_STONE] * (len(treads) + len(risers))
    assert out[len(treads) + len(risers):] == [STONE] * len(wall)


def test_floor_at_the_foot_of_a_flight_stays_plain():
    """A room floor one step below the flight is not a tread."""
    floor = _quad(-400, -400, 400, 0, 0)
    flight = [t for i in range(1, 6) for t in _quad(0, 20 * (i - 1), 100, 20 * i, 16 * i)]
    out = _run(floor + flight)
    assert out[:len(floor)] == [STONE] * len(floor)
    assert out[len(floor):] == [STAIRS_STONE] * len(flight)


def test_stacked_shelves_are_not_a_flight():
    """Boards stacked straight above each other share their footprint: no steps."""
    tris = [t for i in range(6) for t in _quad(0, 0, 100, 30, 30 * i)]
    assert _run(tris) == [STONE] * len(tris)


def test_a_ramp_is_left_alone():
    """A sloped ramp has no treads, so it keeps its material."""
    tris = [((0, 0, 0), (100, 0, 0), (100, 200, 150)), ((0, 0, 0), (100, 200, 150), (0, 200, 150))]
    assert _run(tris) == [STONE] * len(tris)


def test_packed_triangles_belong_to_the_sub_shape_owning_their_vertices():
    """Owners follow the sub-shape vertex ranges; degenerate rows are skipped
    and a vertex past the last range belongs to the last sub-shape."""
    shape = SimpleNamespace(
        sub_shapes=[SimpleNamespace(num_vertices=3), SimpleNamespace(num_vertices=3)],
        data=SimpleNamespace(triangles=[_tri(0, 1, 2), _tri(3, 3, 4), _tri(3, 4, 5), _tri(9, 10, 11)]))
    assert _packed_tri_owners(shape) == [0, 1, 1]
