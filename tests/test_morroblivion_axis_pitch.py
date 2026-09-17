"""The Morroblivion wrong-axis pitch is added only to a non-owner's refs.

See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
"""
import math

import pytest

from tes4_export.morroblivion import _pitch_placements
from tes4_export.morroblivion_axis import (
    AXIS_PITCH_DEG, AXIS_PITCH_UNSURE, is_unsure, pitch_for_model)

BROKEN = 'Morroblivion\\Lights\\Common\\candle_10.nif'
GOOD = 'Morroblivion\\Lights\\Dungeons\\tikitorch.nif'
BASE = '012C0134'


def test_broken_mesh_has_a_right_angle_pitch():
    """The reported candle_10 case resolves to a quarter turn."""
    assert pitch_for_model(BROKEN) == pytest.approx(math.radians(270))


def test_correctly_authored_mesh_has_no_pitch():
    """A Z-axis mesh placed upright must not be rotated."""
    assert pitch_for_model(GOOD) == 0.0
    assert pitch_for_model('') == 0.0


def test_forward_slash_and_case_both_resolve():
    """Export paths vary in separator and case; the table is normalized."""
    assert pitch_for_model('MORROBLIVION/LIGHTS/COMMON/CANDLE_10.NIF') > 0.0


def test_master_owned_base_resolves_through_the_models_index():
    """The broken bases belong to Morroblivion, not to the placing plugin."""
    models = {'2c0134': BROKEN}
    lines = ['NAME=' + BASE, 'PosX=1.0', 'RotX=0.0', 'RotY=0.5', 'RotZ=0.0']
    out = {'REFR': [('00000001', lines)]}
    assert _pitch_placements(out, models) == 1
    rotx = float(next(l for l in lines if l.startswith('RotX='))[5:])
    assert rotx == pytest.approx(math.radians(270))
    assert 'RotY=0.5' in lines and 'RotZ=0.0' in lines


def test_existing_rotx_is_added_to_not_replaced():
    """A ref that already carries a rotation keeps it, plus the pitch."""
    models = {'2c0134': BROKEN}
    lines = ['NAME=' + BASE, 'RotX=0.25']
    assert _pitch_placements({'REFR': [('1', lines)]}, models) == 1
    rotx = float(next(l for l in lines if l.startswith('RotX='))[5:])
    assert rotx == pytest.approx(0.25 + math.radians(270))


def test_ref_to_a_good_mesh_is_untouched():
    """Only refs whose base names a broken mesh move."""
    models = {'2c0134': GOOD}
    lines = ['NAME=' + BASE, 'RotX=0.25']
    assert _pitch_placements({'REFR': [('1', lines)]}, models) == 0
    assert 'RotX=0.25' in lines


def test_unknown_base_is_a_no_op():
    """A ref whose base is in no master index keeps its rotation."""
    lines = ['NAME=00FFFFFF', 'RotX=0.0']
    assert _pitch_placements({'REFR': [('1', lines)]}, {'2c0134': BROKEN}) == 0
    assert 'RotX=0.0' in lines


def test_actor_placements_are_corrected_too():
    """ACHR and ACRE carry the same rotation keys as REFR."""
    models = {'2c0134': BROKEN}
    achr = ['NAME=' + BASE, 'RotX=0.0']
    acre = ['NAME=' + BASE, 'RotX=0.0']
    out = {'ACHR': [('1', achr)], 'ACRE': [('2', acre)]}
    assert _pitch_placements(out, models) == 2


def test_unsure_meshes_are_never_applied():
    """The two tables stay disjoint, or a split-pitch mesh gets tilted."""
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
