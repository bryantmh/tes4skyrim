"""
Split Morroblivion pairs: pairing, re-listed holders, script swaps and child scripts.

The mechanism and its measurements are recorded in
docs/commentary/tes4_export_morrowind.md#split-pairs.
"""

import struct

import numpy as np

from asset_convert.character.ground_halves import _layout, fit_errors
from tes4_export.morroblivion_pair_scripts import (_END_FRAGMENT, Twin, _child_script,
                                                   _functions)
from tes4_export.morroblivion_pairs import _right_ids, _with_twins
from tes4_export.morrowind_ids import IdIndex, interior_key
from tes5_import.overrides.vmad_swap import SCRIPT_SWAP_KEY, swap_vmad_script


def test_right_twin_is_the_left_id_with_one_side_word_swapped():
    """`left`, a lone `l` and a trailing `l` each name the right twin."""
    assert 'steel_gauntlet_right' in _right_ids('steel_gauntlet_left')
    assert 'indoril right gauntlet' in _right_ids('indoril left gauntlet')
    assert 'darkbrotherhood gauntlet_r' in _right_ids('DarkBrotherhood gauntlet_L')
    assert 'bm_ice_gauntletr' in _right_ids('BM_Ice_gauntletL')


def _turn_about_z(degrees: float) -> np.ndarray:
    """A rotation about +Z."""
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def test_a_ground_piece_names_its_hand_by_whether_it_fits_the_left_unmirrored():
    """A turned copy of the left fits by rotation; a mirrored copy only by mirroring."""
    left = np.random.default_rng(1).normal(size=(40, 3)) * [5.0, 2.0, 1.0]
    same, mirror = fit_errors(left @ _turn_about_z(30).T + 3.0, left)
    assert same < 1e-9 < mirror
    same, mirror = fit_errors((left * [-1.0, 1.0, 1.0]) @ _turn_about_z(30).T, left)
    assert mirror < 1e-9 < same


def test_a_rebuilt_ground_half_lies_flat_on_the_ground_unmirrored():
    """Laid on its principal axes: thinnest axis up, resting on z=0, centered, never mirrored."""
    standing = np.random.default_rng(2).normal(size=(200, 3)) * [1.0, 3.0, 8.0] + [50.0, 0.0, 99.0]
    rotation, shift = _layout(standing, turn=True)
    moved = standing @ rotation.T + shift
    extent = moved.max(axis=0) - moved.min(axis=0)
    assert np.isclose(np.linalg.det(rotation), 1.0)
    assert extent[2] == extent.min()
    assert np.isclose(moved[:, 2].min(), 0.0)
    assert np.allclose((moved.max(axis=0) + moved.min(axis=0))[:2], 0.0)


def test_holder_entries_naming_the_pair_item_gain_a_left_twin():
    """Each pair-item entry is doubled with the same count; the count key follows."""
    rec = {'Signature': 'NPC_', 'FormID': '01000001', 'EditorID': 'npc',
           'ItemCount': '2', 'Item[0].FormID': '01120050', 'Item[0].Count': '1',
           'Item[1].FormID': '0000000F', 'Item[1].Count': '13'}
    lines = _with_twins(rec, 'Item', {'01120050': '02ADFD6F'})
    assert 'ItemCount=3' in lines
    assert lines[-2:] == ['Item[2].FormID=02ADFD6F', 'Item[2].Count=1']
    assert _with_twins(rec, 'Item', {'01999999': '02ADFD6F'}) == []


def test_script_swap_renames_every_length_prefixed_occurrence():
    """The script entry and a fragment's file name both move to the child."""
    def wstring(name):
        return struct.pack('<H', len(name)) + name.encode('ascii')
    vmad = (b'\x05\x00\x02\x00\x01\x00' + wstring('TES4_TIF__0132117A') + b'\x00\x00\x00'
            + b'\x02\x01\x00' + wstring('TES4_TIF__0132117A'))
    out = swap_vmad_script(vmad, {SCRIPT_SWAP_KEY: 'tes4_tif__0132117a>TES4_TIF__0132117A_Pairs'})
    assert out.count(wstring('TES4_TIF__0132117A_Pairs')) == 2
    assert wstring('TES4_TIF__0132117A') not in out


def test_interior_cell_resolves_by_escape_without_the_zero_and_by_display_name():
    """Morroblivion escapes a cell without the base objects' leading 0."""
    index = IdIndex()
    index.add(interior_key('BalmoraVSCaiusSCosadesASHouse'), '01000010', 'CELL')
    index.add(interior_key('Ald-ruhn, Council Club'), '01000011', 'CELL')
    assert index.lookup_interior("Balmora, Caius Cosades' House") == '01000010'
    assert index.lookup_interior('Ald-ruhn, Council Club') == '01000011'


def test_child_script_follows_the_parent_s_change_to_the_pair_item():
    """A result script's player.additem becomes a Fragment_0 wrapping the parent."""
    names = {'0bmuiceugauntletr': ('Morrowind_ob.esm', 0x12018C, 0x97044F)}
    rec = {'ResultScript': 'player.additem 0BMUIceUgauntletR 1'}
    functions = _functions('INFO', rec, names)
    assert functions == {_END_FRAGMENT: {Twin('Game.GetPlayer()', 'Morrowind_ob.esm',
                                              0x12018C, 0x97044F)}}
    text = _child_script('TES4_TIF__010196EC', 'TES4_TIF__010196EC_Pairs', functions, 'patch.esp')
    assert text.startswith('ScriptName TES4_TIF__010196EC_Pairs extends TES4_TIF__010196EC')
    assert '  Parent.Fragment_0(akSpeakerRef)' in text
    assert 'AddItem(Game.GetFormFromFile(0x97044F, "patch.esp"), change0)' in text
