"""Death-pile ACTI bounds read from the shipped pile mesh (tes5_import/actors/creature_races.py).

See: docs/commentary/asset_convert_creature.md#pile-acti-record-fields
"""

import struct
import time

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from pyffi.formats.nif import NifFormat

from tes5_import.actors import creature_races


def _write_pile(path):
    """A NIF at `path` holding one shape spanning (-10, -20, 0)..(10, 20, 5)."""
    gd = NifFormat.NiTriShapeData()
    gd.num_vertices = 2
    gd.has_vertices = True
    gd.vertices.update_size()
    for v, xyz in zip(gd.vertices, ((-10, -20, 0), (10, 20, 5))):
        v.x, v.y, v.z = xyz
    shape = NifFormat.NiTriShape()
    shape.data = gd
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


def test_pile_under_a_backslash_body_dir_is_measured(tmp_path, monkeypatch):
    """body_dir `tes4\\creatures\\Ghost` finds output/<plugin>/meshes/tes4/creatures/ghost/."""
    _write_pile(tmp_path / 'Oblivion.esm' / 'meshes' / 'tes4' / 'creatures' / 'ghost'
                / 'ghostdeathpile.nif')
    proj = {'body_dir': 'tes4\\creatures\\Ghost'}
    assert creature_races._pile_mesh_bounds(proj, 'ghostdeathpile.nif',
                                            tmp_path) == ([-10, -20, 0], [10, 20, 5])


def test_the_pile_acti_reads_the_runs_output_root(tmp_path, monkeypatch):
    """The OBND comes from the configured output root, not the install's output/."""
    _write_pile(tmp_path / 'out' / 'Oblivion.esm' / 'meshes' / 'ghost'
                / 'ghostdeathpile.nif')
    monkeypatch.setattr(creature_races, '_PROJECTS', {'ghost': {
        'dissolves_on_death': True, 'death_pile': 'ghostdeathpile.nif',
        'body_dir': 'ghost'}})
    monkeypatch.setattr(creature_races, 'generated_formid', lambda *a: 0x800)
    records = []
    writer = type('W', (), {'add_record': lambda self, sig, rec: records.append(rec)})()

    assert creature_races.build_creature_death_piles(writer, tmp_path / 'out') == 1

    obnd = records[0][records[0].index(b'OBND') + 6:][:12]
    assert struct.unpack('<6h', obnd) == (-10, -20, 0, 10, 20, 5)
