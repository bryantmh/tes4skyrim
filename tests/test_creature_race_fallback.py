"""The creature keyword fallback names real Skyrim.esm races.

The pinned FormIDs were read from Skyrim.esm's RACE records (EditorID by
FormID). Where docs/reference/creature_race_equivalence.md or
vanilla_creature_swap.py names the same race, it gives the same id.
"""
import re

import pytest

from tes5_import.actors.vanilla_creature_swap import BY_EDITORID, BY_FOLDER
from tes5_import.base import equivalents
from tes5_import.base.equivalents import (CREA_RACE_PATTERNS,
                                          resolve_creature_race)

WOLF_RACE = 0x0001320A
CHAURUS_RACE = 0x000131EB
MAMMOTH_RACE = 0x000131FF
_LABEL = re.compile(r'Skyrim\.esm 0x([0-9A-Fa-f]{8}) (\w+)')
#: Skyrim.esm RACE EditorID -> FormID, for every race a row hands out.
SKYRIM_RACES = {
    'AtronachFlameRace': 0x000131F5, 'AtronachFrostRace': 0x000131F6,
    'AtronachStormRace': 0x000131F7, 'BearBrownRace': 0x000131E7,
    'ChaurusRace': 0x000131EB, 'DraugrRace': 0x00000D53,
    'DremoraRace': 0x000131F0, 'ElkRace': 0x000131ED,
    'FalmerRace': 0x000131F4, 'FrostbiteSpiderRace': 0x000131F8,
    'GiantRace': 0x000131F9, 'GoatRace': 0x000131FA,
    'HorseRace': 0x000131FD, 'IceWraithRace': 0x000131FE,
    'MudcrabRace': 0x000BA545, 'SabreCatRace': 0x00013200,
    'SkeeverRace': 0x00013201, 'SkeletonRace': 0x000B7998,
    'SprigganRace': 0x00013204, 'TrollRace': 0x00013205,
    'WispRace': 0x00013208, 'WolfRace': 0x0001320A,
}
_ROWS = [pytest.param(fid, note, id=keyword)
         for keyword, fid, note, _alt in CREA_RACE_PATTERNS]


def _swap_table_ids() -> dict:
    """Race name -> FormIDs, from every row of the vanilla-swap tables."""
    swaps = [*BY_FOLDER.values(), *(s for _key, s in BY_EDITORID if s)]
    ids: dict = {}
    for swap in swaps:
        ids.setdefault(swap.skyrim_name, set()).add(swap.race)
    return ids


@pytest.mark.parametrize('edid, name', [
    ('CreatureWolf', 'Wolf'), ('CreatureWolfTimber', 'Timber Wolf'),
    ('CreatureDog', 'Dog')])
def test_wolf_and_dog_fall_back_to_wolf_race(edid, name):
    """0x000131EB is ChaurusRace; a wolf or dog is written with WolfRace."""
    assert resolve_creature_race(edid, name)[0] == WOLF_RACE


@pytest.mark.parametrize('edid, name', [
    ('SECreatureScalonMania1', 'Scalon'), ('SECreatureBaliwog2', 'Baliwog'),
    ('CreatureLandDreugh', 'Land Dreugh')])
def test_chaurus_stand_ins_fall_back_to_chaurus_race(edid, name):
    """0x000131F3 is DwarvenSpiderRace, not ChaurusRace."""
    assert resolve_creature_race(edid, name)[0] == CHAURUS_RACE


def test_every_row_is_labelled():
    """Each row says which Skyrim.esm race it hands out, id and name."""
    assert _ROWS
    assert all(_LABEL.match(note) for _kw, _fid, note, _alt in CREA_RACE_PATTERNS)


@pytest.mark.parametrize('fid, note', _ROWS)
def test_a_label_names_the_formid_its_row_returns(fid, note):
    """A note's FormID must be the one returned, or reports mislead."""
    assert int(_LABEL.match(note).group(1), 16) == fid


@pytest.mark.parametrize('fid, note', _ROWS)
def test_a_row_hands_out_the_race_its_label_names(fid, note):
    """A row's FormID is the Skyrim.esm id of the race its label names."""
    assert SKYRIM_RACES.get(_LABEL.match(note).group(2)) == fid


def test_the_swap_table_gives_each_race_one_formid():
    """The table the rows are checked against does not contradict itself."""
    ids = _swap_table_ids()
    assert 'WolfRace' in ids
    assert all(len(fids) == 1 for fids in ids.values())


@pytest.mark.parametrize('fid, note', _ROWS)
def test_a_race_the_swap_table_names_has_its_formid(fid, note):
    """Two tables that name one Skyrim race must give it one FormID."""
    known = _swap_table_ids().get(_LABEL.match(note).group(2))
    assert known is None or known == {fid}


def test_the_cross_check_reaches_the_wolf_rows():
    """The rows this file was written for are among those cross-checked."""
    checked = {_LABEL.match(note).group(2)
               for _kw, _fid, note, _alt in CREA_RACE_PATTERNS
               if _LABEL.match(note).group(2) in _swap_table_ids()}
    assert {'WolfRace', 'SkeeverRace', 'TrollRace'} <= checked


def test_the_stand_ins_no_row_uses_are_real_races_too():
    """Mammoth, hare and fox are in no row yet; their ids are still right."""
    ids = _swap_table_ids()
    assert equivalents._SKY_MAMMOTH == MAMMOTH_RACE
    assert {equivalents._SKY_RABBIT} == ids['HareRace']
    assert {equivalents._SKY_FOX} == ids['FoxRace']
