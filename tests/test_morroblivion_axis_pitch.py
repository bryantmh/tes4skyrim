"""Morroblivion's hand corrections reach only a non-owner's placements.

See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
"""
import math

import numpy as np
import pytest

from tes4_export.morroblivion import (MorroblivionModels, fix_placements,
                                      archive_path)
from tes4_export.morroblivion_axis import (
    AXIS_PITCH_DEG, AXIS_PITCH_UNSURE, ROTX_ADD_DEG, SUBSTITUTION_BLACKLIST,
    Z_RESEAT, compose_pitch, is_unsure, pitch_for_model, rotx_add_for_model,
    z_reseat_for_base)
from tes5_import.navmesh.world import rot_matrix

ROCK = 'Morro\\i\\InUlavaUrockU14.nif'
FIRE = 'Fire\\FireOpenMediumSmoke.nif'
BASE = '012C0134'
MODELS = {'2c0134': ROCK}
THREE_QUARTERS = math.radians(270)


def _rot(lines):
    """(RotX, RotY, RotZ) read back from export lines."""
    return tuple(float(next(l for l in lines if l.startswith(k))[5:])
                 for k in ('RotX=', 'RotY=', 'RotZ='))


def test_tables_resolve_in_any_spelling():
    """Export paths vary in separator and case; the tables are normalized."""
    assert rotx_add_for_model(ROCK) == pytest.approx(math.pi)
    assert rotx_add_for_model('MORRO/I/INULAVAUROCKU14.NIF') == pytest.approx(math.pi)
    assert pitch_for_model(FIRE) == pytest.approx(THREE_QUARTERS)
    assert pitch_for_model('') == 0.0 and rotx_add_for_model('') == 0.0


def test_rock_rotx_is_added_to_and_yaw_kept():
    """Morroblivion flipped its rocks by adding to RotX alone; so do we."""
    lines = ['NAME=' + BASE, 'RotX=0.25', 'RotY=0.0', 'RotZ=1.2']
    assert fix_placements({'REFR': [('1', lines)]}, MODELS, {}) == 1
    assert _rot(lines) == pytest.approx((0.25 + math.pi, 0.0, 1.2))


def test_compose_equals_a_sum_without_yaw_or_roll():
    """With RotY = RotZ = 0 the model-side pitch is just a RotX add."""
    rx, ry, rz = compose_pitch(0.3, 0.0, 0.0, THREE_QUARTERS)
    assert np.allclose(rot_matrix(rx, ry, rz), rot_matrix(0.3 + THREE_QUARTERS, 0, 0))


@pytest.mark.parametrize('angles', [(0.0, 0.0, 1.2), (0.4, -0.7, 2.9),
                                    (1.1, math.pi / 2, 0.5), (0.2, -math.pi / 2, 3.0)])
def test_compose_round_trips_the_matrix(angles):
    """The returned angles rebuild ref-rotation x model-pitch exactly, gimbal included."""
    want = rot_matrix(*angles) @ rot_matrix(THREE_QUARTERS, 0.0, 0.0)
    assert np.allclose(rot_matrix(*compose_pitch(*angles, THREE_QUARTERS)), want)


def test_yawed_fire_stands_upright():
    """The reported TR ref (RotZ 1.2) keeps the fire's +Y on world up.

    See: docs/audits/morroblivion_mesh_axis_rotation.md#pitch-must-compose
    """
    lines = ['NAME=' + BASE, 'RotX=0.0', 'RotY=-0.0', 'RotZ=1.2000000476837158']
    assert fix_placements({'REFR': [('1', lines)]}, {'2c0134': FIRE}, {}) == 1
    up = rot_matrix(*_rot(lines)) @ np.array([0.0, 1.0, 0.0])
    assert up[2] == pytest.approx(1.0)


def test_ref_without_all_three_angles_is_not_pitched():
    """A composed pitch needs the whole rotation; a partial one is left alone."""
    lines = ['NAME=' + BASE, 'RotX=0.0']
    assert fix_placements({'REFR': [('1', lines)]}, {'2c0134': FIRE}, {}) == 0
    assert lines == ['NAME=' + BASE, 'RotX=0.0']


def test_unknown_base_is_a_no_op():
    """A ref whose base is in no master index keeps its placement."""
    lines = ['NAME=00FFFFFF', 'PosZ=5.0', 'RotX=0.0']
    assert fix_placements({'REFR': [('1', lines)]}, MODELS, {}) == 0
    assert 'PosZ=5.0' in lines and 'RotX=0.0' in lines


def test_actor_placements_are_corrected_too():
    """ACHR and ACRE carry the same keys as REFR."""
    out = {'ACHR': [('1', ['NAME=' + BASE, 'RotX=0.0'])],
           'ACRE': [('2', ['NAME=' + BASE, 'RotX=0.0'])]}
    assert fix_placements(out, MODELS, {}) == 2


def test_z_reseat_keys_on_the_base_not_the_mesh():
    """Morroblivion moved 0barrelU01Udrinks but left other barrels alone."""
    assert z_reseat_for_base('0barrelU01Udrinks') == pytest.approx(3.0)
    assert z_reseat_for_base('0lightUcomUtorchU01') == 0.0
    assert z_reseat_for_base('') == 0.0


def test_z_reseat_applies_to_posz():
    """A re-seated base's refs move in Z, and nothing else changes."""
    lines = ['NAME=012C0155', 'PosX=1.0', 'PosZ=100.0', 'RotX=0.0']
    out = {'REFR': [('1', lines)]}
    assert fix_placements(out, {}, {'2c0155': '0barrelU01Udrinks'}) == 1
    posz = float(next(l for l in lines if l.startswith('PosZ='))[5:])
    assert posz == pytest.approx(103.0)
    assert 'PosX=1.0' in lines and 'RotX=0.0' in lines


def test_blacklist_is_normalized():
    """Every entry matches what `archive_path` produces, or it can never fire."""
    assert SUBSTITUTION_BLACKLIST
    for mesh in SUBSTITUTION_BLACKLIST:
        assert mesh == mesh.lower()
        assert chr(92) not in mesh
        assert not mesh.startswith(chr(47))


def test_no_correction_targets_a_blacklisted_mesh():
    """A blacklisted mesh never reaches the output, so its row can never fire."""
    tables = (set(AXIS_PITCH_DEG), set(ROTX_ADD_DEG), set(AXIS_PITCH_UNSURE))
    for i, table in enumerate(tables):
        assert not table & SUBSTITUTION_BLACKLIST
        for other in tables[i + 1:]:
            assert not table & other


LAMP = 'l/light_de_lamp_03.nif'


def _models(model):
    """A MorroblivionModels whose single owner resolves to `model`."""
    obj = MorroblivionModels.__new__(MorroblivionModels)
    obj.owners = {archive_path(LAMP): ['light_de_lamp_03']}
    obj.models = {'2c0200': model}
    return obj


class _Index:
    """Stands in for the master index, resolving one known record."""

    @staticmethod
    def lookup(_record_id):
        """The FormID the one known owner resolves to."""
        return '012C0200'


def test_blacklisted_replacement_keeps_the_vanilla_mesh():
    """A different object must never be substituted for the authored one."""
    swap = sorted(SUBSTITUTION_BLACKLIST)[0]
    assert _models(swap).replacement(LAMP, _Index()) == ''


def test_ordinary_replacement_still_substitutes():
    """The blacklist must not disturb a legitimate Morroblivion mesh."""
    keep = 'morroblivion/lights/dungeons/tikitorch.nif'
    assert _models(keep).replacement(LAMP, _Index()) == keep


def test_unsure_meshes_resolve_in_any_spelling():
    """The held table matches a Windows-style path as well as its own key."""
    for mesh in AXIS_PITCH_UNSURE:
        assert is_unsure(mesh.replace('/', '\\'))
        assert pitch_for_model(mesh) == 0.0 and rotx_add_for_model(mesh) == 0.0


def test_reseat_keys_are_lowercase():
    """`z_reseat_for_base` lowercases its argument, so keys must match."""
    for key in Z_RESEAT:
        assert key == key.lower()
