"""Morroblivion's hand corrections reach only a non-owner's placements.

See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
"""
import math

import pytest

from tes4_export.morroblivion import _fix_placements
from tes4_export.morroblivion_axis import (
    AXIS_PITCH_DEG, AXIS_PITCH_UNSURE, MESH_SWAPS, Z_RESEAT, is_unsure,
    pitch_for_model, z_reseat_for_base)

BROKEN = 'Morroblivion\\Lights\\Common\\candle_10.nif'
GOOD = 'Morroblivion\\Lights\\Dungeons\\tikitorch.nif'
BASE = '012C0134'
MODELS = {'2c0134': BROKEN}


def test_broken_mesh_has_a_right_angle_pitch():
    """The reported candle_10 case resolves to a quarter turn."""
    assert pitch_for_model(BROKEN) == pytest.approx(math.radians(270))


def test_mesh_with_no_measured_delta_is_untouched():
    """tikitorch reads delta 0 on 1,310 of 1,311 refs, so it must not move."""
    assert pitch_for_model(GOOD) == 0.0
    assert pitch_for_model('') == 0.0


def test_forward_slash_and_case_both_resolve():
    """Export paths vary in separator and case; the table is normalized."""
    assert pitch_for_model('MORROBLIVION/LIGHTS/COMMON/CANDLE_10.NIF') > 0.0


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
    """Morroblivion moved 0torchU256 but left 0lightUcomUtorchU01 alone."""
    assert z_reseat_for_base('0torchU256') == pytest.approx(-5.0)
    assert z_reseat_for_base('0lightUcomUtorchU01') == 0.0
    assert z_reseat_for_base('') == 0.0


def test_z_reseat_applies_to_posz():
    """A re-seated base's refs move in Z, and nothing else changes."""
    lines = ['NAME=012C0155', 'PosX=1.0', 'PosZ=100.0', 'RotX=0.0']
    out = {'REFR': [('1', lines)]}
    assert _fix_placements(out, {}, {'2c0155': '0torchU256'}) == 1
    posz = float(next(l for l in lines if l.startswith('PosZ='))[5:])
    assert posz == pytest.approx(95.0)
    assert 'PosX=1.0' in lines and 'RotX=0.0' in lines


def test_pitch_and_reseat_can_both_apply():
    """A base needing both corrections gets both, counted separately."""
    lines = ['NAME=' + BASE, 'PosZ=10.0', 'RotX=0.0']
    out = {'REFR': [('1', lines)]}
    assert _fix_placements(out, MODELS, {'2c0134': '0torchU256'}) == 2
    assert float(next(l for l in lines if l.startswith('PosZ='))[5:]) == \
        pytest.approx(5.0)
    assert float(next(l for l in lines if l.startswith('RotX='))[5:]) == \
        pytest.approx(math.radians(270))


def test_mesh_swaps_are_recorded_but_never_applied():
    """A swapped object's offset would relocate the error, not fix it."""
    assert MESH_SWAPS
    for edid, (dz, mw_mesh, mb_mesh, refs) in MESH_SWAPS.items():
        assert edid == edid.lower()
        assert z_reseat_for_base(edid) == 0.0, edid
        assert dz and mw_mesh and mb_mesh and refs > 0


def test_swap_and_reseat_tables_are_disjoint():
    """A base is either re-seated or mis-swapped, never both."""
    assert not set(MESH_SWAPS) & set(Z_RESEAT)


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


def test_reseat_keys_are_lowercase():
    """`z_reseat_for_base` lowercases its argument, so keys must match."""
    for key in Z_RESEAT:
        assert key == key.lower()
