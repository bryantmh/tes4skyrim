"""A death pile's bounds are read from its converted mesh (tes5_import/actors/creature_races.py).

See: docs/commentary/asset_convert_creature.md#pile-acti-record-fields
"""

import struct

import pytest

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()

NifFormat = pytest.importorskip('pyffi.formats.nif').NifFormat

from tes5_import.actors import creature_races

GHOST = {'dissolves_on_death': True, 'death_pile': 'ghostdeathpile.nif',
         'body_dir': 'Actors\\tes4\\oblivion\\ghost'}
PILE = ('meshes', 'actors', 'tes4', 'oblivion', 'ghost', 'ghostdeathpile.nif')
FALLBACK_BOX = (-24, -24, -4, 24, 24, 16)


def _write_pile(path, half=10):
    """A NIF at `path` holding one shape spanning (-half, -20, 0)..(half, 20, 5)."""
    geom = NifFormat.NiTriShapeData()
    geom.num_vertices = 2
    geom.has_vertices = True
    geom.vertices.update_size()
    for vertex, xyz in zip(geom.vertices, ((-half, -20, 0), (half, 20, 5))):
        vertex.x, vertex.y, vertex.z = xyz
    shape = NifFormat.NiTriShape()
    shape.data = geom
    root = NifFormat.NiNode()
    root.num_children = 1
    root.children.update_size()
    root.children[0] = shape
    data = NifFormat.Data()
    data.version, data.user_version, data.user_version_2 = 0x14000005, 11, 11
    data.header.endian_type = 1
    data.roots = [root]
    path.parent.mkdir(parents=True)
    with open(path, 'wb') as f:
        data.write(f)


@pytest.fixture
def output(tmp_path, monkeypatch):
    """The `output/` folder of an install standing at `tmp_path`, not yet created."""
    monkeypatch.setattr(creature_races, '__file__', str(
        tmp_path / 'tes5_import' / 'actors' / 'creature_races.py'))
    return tmp_path / 'output'


def _pile_record(monkeypatch):
    """The OBND of the one ACTI `build_creature_death_piles` writes for the ghost."""
    monkeypatch.setattr(creature_races, '_PROJECTS', {'ghost': GHOST})
    monkeypatch.setattr(creature_races, '_CREA_PILE_ACTI', {})
    monkeypatch.setattr(creature_races, 'generated_formid', lambda *args: 0x800)
    records = []
    writer = type('Writer', (), {
        'add_record': lambda self, sig, record: records.append(record)})()
    assert creature_races.build_creature_death_piles(writer) == 1
    obnd = records[0].index(b'OBND') + 6
    return struct.unpack('<6h', records[0][obnd:obnd + 12])


def test_a_pile_under_a_backslash_body_dir_is_measured(output):
    """`Actors\\tes4\\oblivion\\ghost` finds the lowercase folders the mesh stage wrote."""
    _write_pile(output.joinpath('Oblivion.esm', *PILE))
    assert creature_races._pile_mesh_bounds(GHOST, 'ghostdeathpile.nif') == (
        [-10, -20, 0], [10, 20, 5])


def test_a_pile_is_found_in_whichever_plugin_folder_holds_it(output):
    """Every folder under `output/` is searched, in sorted order."""
    (output / 'AutoConvertLOD' / 'meshes').mkdir(parents=True)
    _write_pile(output.joinpath('Oblivion.esm', *PILE))
    assert creature_races._pile_mesh_bounds(GHOST, 'ghostdeathpile.nif') == (
        [-10, -20, 0], [10, 20, 5])


def test_the_first_plugin_folder_holding_a_pile_answers(output):
    """Of two converted piles, the one in the first folder in sorted order is read."""
    _write_pile(output.joinpath('Knights.esp', *PILE))
    _write_pile(output.joinpath('Oblivion.esm', *PILE), half=30)
    assert creature_races._pile_mesh_bounds(GHOST, 'ghostdeathpile.nif') == (
        [-10, -20, 0], [10, 20, 5])


def test_a_project_naming_no_body_dir_is_looked_up_under_meshes(output):
    """With no body_dir the pile sits directly in the plugin's `meshes/`."""
    _write_pile(output / 'Oblivion.esm' / 'meshes' / 'ghostdeathpile.nif')
    assert creature_races._pile_mesh_bounds({}, 'ghostdeathpile.nif') == (
        [-10, -20, 0], [10, 20, 5])


def test_the_pile_record_carries_the_measured_bounds(output, monkeypatch):
    """The ACTI's OBND is the mesh's own box, not the fallback."""
    _write_pile(output.joinpath('Oblivion.esm', *PILE))
    assert _pile_record(monkeypatch) == (-10, -20, 0, 10, 20, 5)


def test_a_pile_that_was_not_converted_keeps_the_fallback_box(output, monkeypatch):
    """With no mesh under any plugin folder there are no bounds to read."""
    (output / 'Oblivion.esm' / 'meshes' / 'actors').mkdir(parents=True)
    assert creature_races._pile_mesh_bounds(GHOST, 'ghostdeathpile.nif') is None
    assert _pile_record(monkeypatch) == FALLBACK_BOX


def test_an_install_with_no_output_folder_has_no_bounds(output):
    """Before any mesh stage has run there is nothing to search."""
    assert not output.exists()
    assert creature_races._pile_mesh_bounds(GHOST, 'ghostdeathpile.nif') is None
