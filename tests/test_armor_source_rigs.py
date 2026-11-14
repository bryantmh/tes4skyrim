"""Armor fits against its own source rig: head gear's head origin and Morrowind's extra skin bones.

See: docs/commentary/asset_convert_armor.md#head-gear-fit
"""

import numpy as np
import pytest

from asset_convert.character import head_fit
from asset_convert.character.body_wrap import head_origin
from asset_convert.character.skin_retarget import (SKEL_MORROWIND, SKEL_OBLIVION, SKEL_SKYRIM_FEMALE,
                                                   SKEL_SKYRIM_MALE, load_skeleton)
from asset_convert.character.skyrim_overrides_falloutnv import bone_map_for
from asset_convert.havok.extract_skeleton_bones import with_beast_tail


def _at(x, y, z) -> np.ndarray:
    """A 4x4 row-vector transform translated to (x, y, z)."""
    m = np.eye(4)
    m[3, :3] = (x, y, z)
    return m


def test_head_origin_reads_the_source_rig_head():
    """The rig's Bip01 Head translation; the default when the rig has no head."""
    assert head_origin({'Bip01 Head': _at(1, 2, 118)}, None).tolist() == [1, 2, 118]
    assert head_origin({}, 'fallback') == 'fallback'


def test_oblivion_rig_head_is_the_fit_origin():
    """Oblivion's own head is the origin head_fit was built about, so Oblivion head gear is unchanged."""
    if not head_fit.fit_available(False) or not load_skeleton(SKEL_OBLIVION):
        pytest.skip('head fit or Oblivion skeleton table not generated')
    np.testing.assert_allclose(head_origin(load_skeleton(SKEL_OBLIVION), None), head_fit._get(False).o_ob,
                               atol=1e-3)


def test_morrowind_rig_head_sits_higher_than_oblivions():
    """Morrowind's head is several units above Oblivion's: fitted about Oblivion's, helmets floated."""
    mw, ob = load_skeleton(SKEL_MORROWIND[False]), load_skeleton(SKEL_OBLIVION)
    if not mw or not ob:
        pytest.skip('skeleton tables not generated')
    assert head_origin(mw, None)[2] - head_origin(ob, None)[2] > 5.0


def test_beast_tail_keeps_its_offset_from_the_pelvis():
    """Tail bones move with the human pelvis; other beast bones are not copied."""
    pelvis = 'NPC Pelvis [Pelv]'
    beast = {pelvis: _at(0, 0, 60), 'TailBone01': _at(0, -5, 58), 'NPC Head [Head]': _at(0, 0, 120)}
    human = {pelvis: _at(0, 1, 64), 'NPC Head [Head]': _at(0, 0, 125)}
    out = with_beast_tail(human, beast)
    np.testing.assert_allclose(np.array(out['TailBone01'])[3, :3], [0, -4, 62])
    assert out['NPC Head [Head]'] is human['NPC Head [Head]']


@pytest.mark.parametrize('table', [SKEL_SKYRIM_MALE, SKEL_SKYRIM_FEMALE])
def test_skyrim_tables_carry_the_beast_tail(table):
    """Both generated Skyrim tables hold the tail bones a beast-race Morrowind robe binds to."""
    bones = load_skeleton(table)
    if not bones:
        pytest.skip('Skyrim skeleton table not generated')
    assert {'TailBone01', 'TailBone02', 'TailBone03', 'TailBone04'} <= set(bones)


def test_morrowind_skin_bones_map_onto_skyrim():
    """Morrowind's attach and tail bones map to Skyrim's weapon, shield and tail bones."""
    names = bone_map_for(load_skeleton(SKEL_OBLIVION))
    assert names['Weapon Bone'] == 'WEAPON'
    assert names['Shield Bone'] == 'SHIELD'
    assert names['Bip01 Tail'] == 'TailBone01'
