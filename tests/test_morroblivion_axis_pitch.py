"""Morroblivion's hand corrections reach only a non-owner's placements.

See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
"""
import math

import pytest

from tes4_export.morroblivion import (MorroblivionModels, _fix_placements,
                                      archive_path)
from tes4_export.morroblivion_axis import (
    AXIS_PITCH_DEG, AXIS_PITCH_UNSURE, SUBSTITUTION_BLACKLIST, Z_RESEAT,
    is_unsure, pitch_for_model, z_reseat_for_base)

BROKEN = 'Morroblivion\\Lights\\Common\\candle_05.nif'
GOOD = 'Morroblivion\\Lights\\Dungeons\\tikitorch.nif'
BASE = '012C0134'
MODELS = {'2c0134': BROKEN}


def test_broken_mesh_has_a_right_angle_pitch():
    """A mis-authored axis resolves to a quarter turn."""
    assert pitch_for_model(BROKEN) == pytest.approx(math.radians(270))


def test_mesh_with_no_measured_delta_is_untouched():
    """tikitorch reads delta 0 on 1,310 of 1,311 refs, so it must not move."""
    assert pitch_for_model(GOOD) == 0.0
    assert pitch_for_model('') == 0.0


def test_forward_slash_and_case_both_resolve():
    """Export paths vary in separator and case; the table is normalized."""
    assert pitch_for_model('MORROBLIVION/LIGHTS/COMMON/CANDLE_05.NIF') > 0.0


def test_master_owned_base_resolves_through_the_models_index():
    """The corrected bases belong to Morroblivion, not the placing plugin."""
    lines = ['NAME=' + BASE, 'PosX=1.0', 'PosZ=9.0', 'RotX=0.0', 'RotY=0.5']
    assert _fix_placements({'REFR': [('1', lines)]}, MODELS, {}) == 1
    rotx = float(next(l for l in lines if l.startswith('RotX='))[5:])
    assert rotx == pytest.approx(math.radians(270))
    assert 'PosZ=9.0' in lines and 'RotY=0.5' in lines


def test_existing_rotx_is_added_to_not_replaced():
    """A ref that already carries a rotation keeps it, plus the delta."""
    lines = ['NAME=' + BASE, 'RotX=0.25']
    assert _fix_placements({'REFR': [('1', lines)]}, MODELS, {}) == 1
    rotx = float(next(l for l in lines if l.startswith('RotX='))[5:])
    assert rotx == pytest.approx(0.25 + math.radians(270))


def test_ref_to_an_uncorrected_mesh_is_untouched():
    """Only refs whose base names a corrected mesh move."""
    lines = ['NAME=' + BASE, 'RotX=0.25', 'PosZ=5.0']
    assert _fix_placements({'REFR': [('1', lines)]}, {'2c0134': GOOD},
                           {'2c0134': '0torch'}) == 0
    assert 'RotX=0.25' in lines and 'PosZ=5.0' in lines


def test_unknown_base_is_a_no_op():
    """A ref whose base is in no master index keeps its placement."""
    lines = ['NAME=00FFFFFF', 'RotX=0.0']
    assert _fix_placements({'REFR': [('1', lines)]}, MODELS, {}) == 0
    assert 'RotX=0.0' in lines


def test_actor_placements_are_corrected_too():
    """ACHR and ACRE carry the same keys as REFR."""
    out = {'ACHR': [('1', ['NAME=' + BASE, 'RotX=0.0'])],
           'ACRE': [('2', ['NAME=' + BASE, 'RotX=0.0'])]}
    assert _fix_placements(out, MODELS, {}) == 2


def test_z_reseat_keys_on_the_base_not_the_mesh():
    """Morroblivion moved 0barrelU01Udrinks but left other barrels alone."""
    assert z_reseat_for_base('0barrelU01Udrinks') == pytest.approx(3.0)
    assert z_reseat_for_base('0lightUcomUtorchU01') == 0.0
    assert z_reseat_for_base('') == 0.0


def test_z_reseat_applies_to_posz():
    """A re-seated base's refs move in Z, and nothing else changes."""
    lines = ['NAME=012C0155', 'PosX=1.0', 'PosZ=100.0', 'RotX=0.0']
    out = {'REFR': [('1', lines)]}
    assert _fix_placements(out, {}, {'2c0155': '0barrelU01Udrinks'}) == 1
    posz = float(next(l for l in lines if l.startswith('PosZ='))[5:])
    assert posz == pytest.approx(103.0)
    assert 'PosX=1.0' in lines and 'RotX=0.0' in lines


def test_pitch_and_reseat_can_both_apply():
    """A base needing both corrections gets both, counted separately."""
    lines = ['NAME=' + BASE, 'PosZ=10.0', 'RotX=0.0']
    out = {'REFR': [('1', lines)]}
    assert _fix_placements(out, MODELS, {'2c0134': '0barrelU01Udrinks'}) == 2
    assert float(next(l for l in lines if l.startswith('PosZ='))[5:]) == \
        pytest.approx(13.0)
    assert float(next(l for l in lines if l.startswith('RotX='))[5:]) == \
        pytest.approx(math.radians(270))


def test_blacklist_is_normalized():
    """Every entry matches what `archive_path` produces, or it can never fire."""
    assert SUBSTITUTION_BLACKLIST
    for mesh in SUBSTITUTION_BLACKLIST:
        assert mesh == mesh.lower()
        assert chr(92) not in mesh
        assert not mesh.startswith(chr(47))


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
    keep = 'morroblivion/lights/common/candle_05.nif'
    assert _models(keep).replacement(LAMP, _Index()) == keep


def test_unsure_meshes_are_never_applied():
    """The two tables stay disjoint, or a split delta gets applied."""
    assert not set(AXIS_PITCH_DEG) & set(AXIS_PITCH_UNSURE)
    for mesh in AXIS_PITCH_UNSURE:
        win = mesh.replace('/', '\\')
        assert pitch_for_model(win) == 0.0
        assert is_unsure(win)


def test_every_applied_pitch_is_a_right_angle():
    """An axis swap is always a multiple of 90 degrees."""
    for mesh, deg in AXIS_PITCH_DEG.items():
        assert deg in (90, 180, 270), mesh
        assert not is_unsure(mesh.replace('/', '\\'))


def test_no_correction_targets_a_blacklisted_mesh():
    """A blacklisted mesh never reaches the output, so its row can never fire."""
    assert not set(AXIS_PITCH_DEG) & SUBSTITUTION_BLACKLIST
    assert not set(AXIS_PITCH_UNSURE) & SUBSTITUTION_BLACKLIST


def test_reseat_keys_are_lowercase():
    """`z_reseat_for_base` lowercases its argument, so keys must match."""
    for key in Z_RESEAT:
        assert key == key.lower()
