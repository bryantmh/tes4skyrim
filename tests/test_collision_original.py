"""latch_original: which converting mesh has a Morrowind original to look up."""

import time

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from asset_convert.collision import collision_original


def test_mesh_under_root_latches_its_relative_path():
    """A mesh inside the meshes root latches its path relative to it, any case."""
    collision_original.latch_original('H:/mod/Oblivion.esm/meshes/characters/hair/a.nif',
                                      'h:/mod/oblivion.esm/meshes')
    assert collision_original._CURRENT[0] == 'characters\\hair\\a.nif'


def test_staged_mesh_on_another_drive_latches_none():
    """A staged temp mesh on another drive (the hair bake) latches None, no ValueError."""
    collision_original.latch_original('C:/Temp/tes4hair_1/hair.nif', 'H:/mod/meshes')
    assert collision_original._CURRENT[0] is None
